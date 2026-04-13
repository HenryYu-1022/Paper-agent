from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

try:
    from .common import (
        bundle_dir_for_pdf,
        cleanup_marker_raw_root,
        ensure_directories,
        find_all_pdfs,
        load_config,
        manifest_path,
        markdown_root,
        output_root,
        pdf_fingerprint,
        relative_pdf_path,
        setup_logger,
        supporting_markdown_name,
        supporting_source_info,
    )
    from .pipeline import ManifestStore, convert_one_pdf_with_retries, delete_pdf_artifacts
except ImportError:
    from common import (
        bundle_dir_for_pdf,
        cleanup_marker_raw_root,
        ensure_directories,
        find_all_pdfs,
        load_config,
        manifest_path,
        markdown_root,
        output_root,
        pdf_fingerprint,
        relative_pdf_path,
        setup_logger,
        supporting_markdown_name,
        supporting_source_info,
    )
    from pipeline import ManifestStore, convert_one_pdf_with_retries, delete_pdf_artifacts


def is_pdf_path(path: str) -> bool:
    return Path(path).suffix.lower() == ".pdf"


def wait_until_stable(path: Path, checks: int, interval_seconds: int) -> bool:
    if checks <= 0:
        return path.exists()

    previous = None
    stable_count = 0

    while stable_count < checks:
        if not path.exists():
            return False
        stat = path.stat()
        current = (stat.st_size, stat.st_mtime_ns)
        if current == previous:
            stable_count += 1
        else:
            stable_count = 0
            previous = current
        time.sleep(interval_seconds)
    return True


class PdfEventHandler(FileSystemEventHandler):
    def __init__(self, config_path: str | None = None) -> None:
        self.config_path = config_path
        self.config = load_config(config_path)
        ensure_directories(self.config)
        self.logger = setup_logger(self.config, logger_name="paper_to_markdown.watch")
        self.input_root = Path(self.config["input_root"])
        self.pending: dict[Path, float] = {}
        self.in_progress: set[Path] = set()
        self.lock = threading.Lock()
        self.running = True
        self.rescan_interval_seconds = int(self.config.get("watch_rescan_interval_seconds", 60))
        self.enable_initial_scan = bool(self.config.get("watch_initial_scan", True))
        self.next_rescan_at = time.time()

        self.worker = threading.Thread(target=self._process_loop, daemon=True)
        self.worker.start()

        if self.enable_initial_scan:
            scheduled = self._rescan_input_root(reason="startup")
            self.logger.info("Startup rescan queued PDFs: %s", scheduled)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory and is_pdf_path(event.src_path):
            self._schedule(Path(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and is_pdf_path(event.src_path):
            self._schedule(Path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory and is_pdf_path(event.dest_path):
            self._schedule(Path(event.dest_path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory and is_pdf_path(event.src_path):
            self._schedule_deletion(Path(event.src_path))

    def _schedule_deletion(self, pdf_path: Path) -> None:
        resolved = pdf_path.resolve()
        rel_key = str(resolved.relative_to(self.input_root.resolve())).replace("\\", "/")
        self.logger.info("PDF deleted, cleaning up artifacts: %s", rel_key)
        try:
            manifest = ManifestStore(manifest_path(self.config))
            result = delete_pdf_artifacts(rel_key, self.config, manifest, self.logger)
            if result.get("deleted"):
                self.logger.info("Cleanup completed for deleted PDF: %s", rel_key)
            else:
                self.logger.info("No artifacts to clean for: %s (%s)", rel_key, result.get("reason"))
        except Exception:
            self.logger.exception("Failed to clean up artifacts for deleted PDF: %s", rel_key)

    def _schedule(self, pdf_path: Path) -> bool:
        resolved = pdf_path.resolve()
        with self.lock:
            if resolved in self.pending or resolved in self.in_progress:
                return False
            self.pending[resolved] = time.time()
        self.logger.info("Queued PDF for processing: %s", pdf_path)
        return True

    def _needs_processing(self, pdf_path: Path, manifest: ManifestStore) -> bool:
        bundle_dir = bundle_dir_for_pdf(pdf_path, self.input_root, self.config)
        supporting_info = supporting_source_info(pdf_path)
        if supporting_info:
            primary_pdf, supporting_index = supporting_info
            primary_bundle_dir = bundle_dir_for_pdf(primary_pdf, self.input_root, self.config)
            target_md = primary_bundle_dir / supporting_markdown_name(supporting_index)
        else:
            target_md = bundle_dir / (pdf_path.stem + ".md")

        if target_md.exists():
            return False

        rel_key = str(relative_pdf_path(pdf_path, self.input_root)).replace("\\", "/")
        entry = manifest.get(rel_key)
        fingerprint = pdf_fingerprint(
            pdf_path,
            use_sha256=self.config.get("compute_sha256", False),
        )

        if not target_md.exists():
            if entry and entry.get("status") == "failed":
                old_fp = {k: v for k, v in entry.items() if k in fingerprint}
                if fingerprint == old_fp:
                    return False
            return True
            
        return False

    def _rescan_input_root(self, reason: str) -> int:
        manifest = ManifestStore(manifest_path(self.config))
        scheduled = 0

        for pdf_path in find_all_pdfs(self.input_root):
            if self._needs_processing(pdf_path, manifest) and self._schedule(pdf_path):
                scheduled += 1

        if scheduled > 0:
            self.logger.info("%s rescan found PDFs to process: %s", reason, scheduled)
        return scheduled

    def _process_loop(self) -> None:
        debounce_seconds = int(self.config.get("watch_debounce_seconds", 8))
        stable_checks = int(self.config.get("watch_stable_checks", 3))
        stable_interval = int(self.config.get("watch_stable_interval_seconds", 2))

        while self.running:
            ready: list[Path] = []
            now = time.time()

            if self.rescan_interval_seconds > 0 and now >= self.next_rescan_at:
                try:
                    self._rescan_input_root(reason="periodic")
                except Exception:
                    self.logger.exception("Periodic rescan failed")
                self.next_rescan_at = now + self.rescan_interval_seconds

            with self.lock:
                for path, last_event_ts in list(self.pending.items()):
                    if now - last_event_ts >= debounce_seconds:
                        ready.append(path)
                        del self.pending[path]

            for pdf_path in ready:
                with self.lock:
                    self.in_progress.add(pdf_path)

                self.logger.info("Waiting for file to stabilize: %s", pdf_path)
                if not wait_until_stable(pdf_path, stable_checks, stable_interval):
                    with self.lock:
                        self.in_progress.discard(pdf_path)
                    self.logger.warning("File is not stable or disappeared: %s", pdf_path)
                    continue

                try:
                    convert_one_pdf_with_retries(
                        pdf_path, config_path=self.config_path, force_reconvert=False,
                    )
                except Exception:
                    self.logger.exception("Watcher conversion failed after all retries: %s", pdf_path)
                finally:
                    with self.lock:
                        self.in_progress.discard(pdf_path)

            time.sleep(1)

    def stop(self) -> None:
        self.running = False
        self.worker.join(timeout=5)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Watch the input PDF folder and convert new PDFs to Markdown."
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Optional path to settings.json. Defaults to the workflow directory.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    config = load_config(args.config)
    ensure_directories(config)
    logger = setup_logger(config, logger_name="paper_to_markdown.watch.main")

    input_root = Path(config["input_root"])
    handler = PdfEventHandler(config_path=args.config)
    observer = Observer()
    observer.schedule(handler, str(input_root), recursive=True)
    observer.start()

    logger.info("Watching input directory: %s", input_root)
    logger.info("Output root: %s", output_root(config))
    logger.info("Markdown root: %s", markdown_root(config))

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received shutdown signal. Stopping watcher.")
    finally:
        handler.stop()
        observer.stop()
        observer.join()
        cleanup_marker_raw_root(config, logger)


if __name__ == "__main__":
    main()
