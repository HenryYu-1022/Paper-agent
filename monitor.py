from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from paper_to_markdown.common import (
    conversion_lock_path,
    conversion_status_path,
    find_all_pdfs,
    load_config,
    logs_root,
    markdown_root,
    relative_pdf_path,
    setup_logger,
    to_posix_path_str,
)
from paper_to_markdown.frontmatter_index import FrontmatterIndex
from paper_to_markdown.organize_figures import organize_library
from paper_to_markdown.pipeline import convert_one_pdf_with_retries
from paper_to_markdown.postprocess_markdown import postprocess_library
from paper_to_markdown.verify import _remove_orphan, _scan


def is_controller_mode(config: dict[str, Any]) -> bool:
    return str(config.get("run_mode", "")).strip() == "controller"

STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87
LOG_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def format_duration(seconds: float) -> str:
    total_seconds = max(int(round(seconds)), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


class EtaTracker:
    def __init__(self) -> None:
        self._last_timestamp: float | None = None
        self._last_processed: int | None = None
        self._seconds_per_item: float | None = None

    def estimate(self, remaining: int, processed: int) -> str | None:
        now = time.time()

        if remaining <= 0:
            return "0s"

        if self._last_timestamp is None or self._last_processed is None:
            self._last_timestamp = now
            self._last_processed = processed
            return None

        elapsed = now - self._last_timestamp
        completed = processed - self._last_processed

        self._last_timestamp = now
        self._last_processed = processed

        if completed < 0:
            self._seconds_per_item = None
            return None

        if elapsed > 0 and completed > 0:
            sample_seconds_per_item = elapsed / completed
            if self._seconds_per_item is None:
                self._seconds_per_item = sample_seconds_per_item
            else:
                self._seconds_per_item = (
                    self._seconds_per_item * 0.7 + sample_seconds_per_item * 0.3
                )

        if self._seconds_per_item is None:
            return None

        return format_duration(remaining * self._seconds_per_item)


def _windows_process_is_running(pid_int: int) -> bool | None:
    process_query_limited_information = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid_int)
    if not handle:
        error = kernel32.GetLastError()
        if error == ERROR_ACCESS_DENIED:
            return True
        if error == ERROR_INVALID_PARAMETER:
            return False
        return None

    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return None
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def process_is_running(pid: Any) -> bool | None:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return None
    if pid_int <= 0:
        return None
    if os.name == "nt":
        return _windows_process_is_running(pid_int)
    try:
        os.kill(pid_int, 0)
        return True
    except OSError:
        return False


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_current_lock(config: dict[str, Any]) -> dict[str, Any] | None:
    lock_path = conversion_lock_path(config)
    if not lock_path.exists():
        return None
    payload = _read_json_file(lock_path)
    if payload is None:
        return None

    running = process_is_running(payload.get("pid"))
    if running is False:
        payload["stale"] = True
    if running is True:
        payload["lock_only"] = True
        payload["status"] = "running"

    started_at_epoch = payload.get("started_at_epoch")
    try:
        elapsed_seconds = max(time.time() - float(started_at_epoch), 0)
    except (TypeError, ValueError):
        elapsed_seconds = 0
    payload["elapsed"] = format_duration(elapsed_seconds)
    payload["source_relpath"] = payload.get("source_relpath") or payload.get("owner")
    return payload


def load_current_conversion(config: dict[str, Any]) -> dict[str, Any] | None:
    status_path = conversion_status_path(config)
    if not status_path.exists():
        current_lock = load_current_lock(config)
        return current_lock if current_lock and not current_lock.get("stale") else None

    payload = _read_json_file(status_path)
    if payload is None or payload.get("status") != "running":
        current_lock = load_current_lock(config)
        return current_lock if current_lock and not current_lock.get("stale") else None

    started_at_epoch = payload.get("started_at_epoch")
    try:
        elapsed_seconds = max(time.time() - float(started_at_epoch), 0)
    except (TypeError, ValueError):
        elapsed_seconds = 0

    payload["elapsed"] = format_duration(elapsed_seconds)
    running = process_is_running(payload.get("pid"))
    if running is False:
        payload["stale"] = True
        current_lock = load_current_lock(config)
        if current_lock and not current_lock.get("stale"):
            return current_lock
    return payload


def current_conversion_index_state(
    current_conversion: dict[str, Any] | None,
    summary: dict[str, Any],
) -> str:
    if not current_conversion:
        return "none"
    if current_conversion.get("lock_only"):
        return "running (lock only)"

    rel_key = current_conversion.get("source_relpath")
    if rel_key in summary["pending_conversion"]:
        return "pending"
    if rel_key in summary["needs_conversion"]:
        return "failed/retry"
    return "already successful or outside input_root"


def current_conversion_is_active(current_conversion: dict[str, Any] | None) -> bool:
    return bool(current_conversion and not current_conversion.get("stale"))


def apply_pending_conversions(
    config: dict[str, Any],
    summary: dict[str, Any],
    config_path: str | None,
    current_conversion: dict[str, Any] | None,
    logger: Any,
) -> dict[str, Any]:
    if current_conversion_is_active(current_conversion):
        logger.info(
            "Conversion already running (pid=%s, source=%s); skipping --convert this cycle",
            current_conversion.get("pid"),
            current_conversion.get("source_relpath"),
        )
        return {"converted": [], "errors": [], "skipped_running": True}

    input_root = Path(config["input_root"])
    converted: list[str] = []
    errors: list[dict[str, str]] = []

    conversion_queue = summary.get("needs_conversion") or summary.get("pending_conversion", [])
    for rel_key in conversion_queue:
        pdf_path = input_root / rel_key
        if not pdf_path.exists():
            continue
        try:
            result = convert_one_pdf_with_retries(pdf_path, config_path=config_path)
            if result is not None:
                converted.append(rel_key)
        except Exception as exc:
            logger.error("Conversion failed for %s: %s", rel_key, exc)
            errors.append({"rel_key": rel_key, "error": str(exc)})

    return {"converted": converted, "errors": errors, "skipped_running": False}


def apply_controller_postprocess(
    config: dict[str, Any],
    apply: bool,
    logger: Any,
) -> dict[str, Any]:
    try:
        summary = postprocess_library(config, apply=apply)
    except Exception as exc:
        logger.error("Controller postprocess failed: %s", exc)
        return {"summary": {}, "error": str(exc), "applied": apply}
    logger.info(
        "Controller postprocess (apply=%s): %s",
        apply,
        summary,
    )

    figures_totals: dict[str, int] = {}
    try:
        md_root = markdown_root(config)
        if md_root.exists():
            figures_totals = organize_library(md_root, apply=apply, logger=logger)
    except Exception as exc:
        logger.error("organize_library failed: %s", exc)

    return {"summary": summary, "applied": apply, "figures": figures_totals}


def apply_orphan_cleanup(
    config: dict[str, Any],
    current_conversion: dict[str, Any] | None,
    logger: Any,
) -> dict[str, Any]:
    manifest = FrontmatterIndex(config)
    _, orphan_main, orphan_supporting = _scan(config, manifest)

    skip_keys: set[str] = set()
    if current_conversion:
        rel_key = current_conversion.get("source_relpath")
        if isinstance(rel_key, str) and rel_key:
            skip_keys.add(rel_key)

    removed: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []

    for rel_key in orphan_main:
        if rel_key in skip_keys:
            logger.info("Skipping orphan currently in conversion: %s", rel_key)
            skipped.append(rel_key)
            continue
        try:
            _remove_orphan(rel_key, config, manifest, logger)
            removed.append(rel_key)
            manifest = FrontmatterIndex(config)
        except Exception as exc:
            logger.error("Failed to remove orphan %s: %s", rel_key, exc)
            errors.append({"rel_key": rel_key, "error": str(exc)})

    for rel_key in orphan_supporting:
        if rel_key in skip_keys:
            skipped.append(rel_key)
            continue
        entry = manifest.get(rel_key)
        if entry is None:
            continue
        output_md = entry.get("output_markdown", "")
        if output_md and not Path(output_md).exists():
            manifest.remove_entry(rel_key)
            manifest = FrontmatterIndex(config)
            continue
        try:
            _remove_orphan(rel_key, config, manifest, logger)
            removed.append(rel_key)
            manifest = FrontmatterIndex(config)
        except Exception as exc:
            logger.error("Failed to remove orphan %s: %s", rel_key, exc)
            errors.append({"rel_key": rel_key, "error": str(exc)})

    return {"removed": removed, "skipped": skipped, "errors": errors}


def recent_average_conversion_seconds(config: dict[str, Any], max_samples: int = 20) -> tuple[float, int] | None:
    app_log = logs_root(config) / "app.log"
    if not app_log.exists():
        return None

    starts: list[datetime] = []
    durations: list[float] = []
    try:
        lines = app_log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    for line in lines:
        if len(line) < 19:
            continue
        try:
            timestamp = datetime.strptime(line[:19], LOG_TIMESTAMP_FORMAT)
        except ValueError:
            continue
        if "Starting marker conversion:" in line:
            starts.append(timestamp)
        elif "Conversion completed:" in line and starts:
            started_at = starts.pop(0)
            duration = (timestamp - started_at).total_seconds()
            if duration > 0:
                durations.append(duration)

    if not durations:
        return None
    recent = durations[-max_samples:]
    return sum(recent) / len(recent), len(recent)


def historical_eta_text(
    config: dict[str, Any],
    pending_count: int,
    current_conversion: dict[str, Any] | None,
) -> str | None:
    if pending_count <= 0:
        return "0s"
    average = recent_average_conversion_seconds(config)
    if average is None:
        return None
    avg_seconds, sample_count = average
    remaining_seconds = avg_seconds * pending_count
    if current_conversion and not current_conversion.get("stale"):
        started_at_epoch = current_conversion.get("started_at_epoch")
        try:
            elapsed_seconds = max(time.time() - float(started_at_epoch), 0)
        except (TypeError, ValueError):
            elapsed_seconds = 0
        remaining_seconds = max(avg_seconds - elapsed_seconds, 0) + avg_seconds * max(pending_count - 1, 0)
    return (
        f"{format_duration(remaining_seconds)} "
        f"(avg {format_duration(avg_seconds)}/PDF, {sample_count} recent)"
    )


def _background_log_paths(config: dict[str, Any]) -> tuple[Path, Path]:
    log_dir = Path(config.get("output_root", ".")).expanduser() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "monitor_background.log", log_dir / "monitor_background.err.log"


def _background_python_path(config: dict[str, Any]) -> str:
    if os.name == "nt":
        pythonw_path = str(config.get("pythonw_path") or "").strip()
        if pythonw_path and Path(pythonw_path).exists():
            return pythonw_path
    python_path = str(config.get("python_path") or "").strip()
    if python_path and Path(python_path).exists():
        return python_path
    return sys.executable


def launch_background_monitor(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    stdout_path, stderr_path = _background_log_paths(config)
    command = [
        _background_python_path(config),
        str(Path(__file__).resolve()),
    ]
    if args.config:
        command.extend(["--config", args.config])
    if args.watch:
        command.append("--watch")
    command.extend(["--interval", str(args.interval)])
    command.extend(["--list-limit", str(max(args.list_limit, 0))])
    if args.apply:
        command.append("--apply")
    if not args.convert:
        command.append("--no-convert")

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)

    with stdout_path.open("ab") as stdout_file, stderr_path.open("ab") as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=str(Path(__file__).resolve().parent),
            stdout=stdout_file,
            stderr=stderr_file,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
    return int(process.pid)


def load_index_summary(config: dict[str, Any]) -> dict[str, Any]:
    input_root = Path(config["input_root"])
    input_pdfs = find_all_pdfs(input_root)
    input_keys = {
        to_posix_path_str(relative_pdf_path(pdf_path, input_root))
        for pdf_path in input_pdfs
    }

    index = FrontmatterIndex(config)
    entries = index.data.get("files", {})
    success_keys = {
        rel_key
        for rel_key, entry in entries.items()
        if entry.get("status") == "success"
    }
    failed_keys = {
        rel_key
        for rel_key, entry in entries.items()
        if entry.get("status") == "failed"
    }

    matched_success = input_keys & success_keys
    matched_failed = input_keys & failed_keys
    needs_conversion = input_keys - success_keys
    pending_conversion = input_keys - success_keys - failed_keys
    stale_markdown_index = success_keys - input_keys

    return {
        "input_total": len(input_keys),
        "markdown_index_total": len(entries),
        "markdown_success_total": len(success_keys),
        "markdown_failed_total": len(failed_keys),
        "matched_success": len(matched_success),
        "matched_failed": len(matched_failed),
        "needs_conversion": sorted(needs_conversion),
        "pending_conversion": sorted(pending_conversion),
        "stale_markdown_index": sorted(stale_markdown_index),
    }


def build_report(
    config_path: str | None = None,
    *,
    list_limit: int = 20,
    eta_text: str | None = None,
) -> str:
    config = load_config(config_path)
    summary = load_index_summary(config)
    current_conversion = load_current_conversion(config)
    needs_conversion = summary["needs_conversion"]
    pending_conversion = summary["pending_conversion"]
    stale_markdown_index = summary["stale_markdown_index"]

    eta_display = eta_text
    if eta_display is None:
        if is_controller_mode(config):
            eta_display = "n/a (controller: marker runs on the runner host)"
        elif not pending_conversion:
            eta_display = "0s"
            if summary["matched_failed"]:
                eta_display += " (failed PDFs need retry)"
        else:
            eta_display = historical_eta_text(config, len(pending_conversion), current_conversion)
            if eta_display is None:
                eta_display = "waiting for progress sample"

    lines = [
        f"Input PDF index: {summary['input_total']}",
        f"Markdown index entries: {summary['markdown_index_total']}",
        f"Markdown success entries: {summary['markdown_success_total']}",
        f"Markdown failed entries: {summary['markdown_failed_total']}",
        f"Input PDFs matched to Markdown: {summary['matched_success']}",
        f"Input PDFs with failed Markdown status: {summary['matched_failed']}",
        f"Input PDFs needing conversion: {len(needs_conversion)}",
        f"Input PDFs pending conversion: {len(pending_conversion)}",
        f"Estimated time remaining: {eta_display}",
        f"Current PDF: {current_conversion.get('source_relpath') if current_conversion else 'none'}",
        f"Current PDF index state: {current_conversion_index_state(current_conversion, summary)}",
        f"Current PDF elapsed: {current_conversion.get('elapsed') if current_conversion else '0s'}",
        f"Markdown success entries without current input PDF: {len(stale_markdown_index)}",
    ]
    if current_conversion and current_conversion.get("stale"):
        lines[-3] += " (stale status file)"

    if needs_conversion:
        lines.append("")
        lines.append(f"First {min(list_limit, len(needs_conversion))} PDFs needing conversion:")
        lines.extend(f"- {path}" for path in needs_conversion[:list_limit])

    if stale_markdown_index:
        lines.append("")
        lines.append(
            f"First {min(list_limit, len(stale_markdown_index))} stale Markdown index entries:"
        )
        lines.extend(f"- {path}" for path in stale_markdown_index[:list_limit])

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare the PDF library index with the Markdown frontmatter index."
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Optional path to settings.json. Defaults to the workflow directory.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Refresh the index report continuously.",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help=(
            "Start monitor in a hidden background process. On Windows this prefers "
            "pythonw_path from settings.json and writes output under output_root/logs."
        ),
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Refresh interval in seconds for --watch mode.",
    )
    parser.add_argument(
        "--list-limit",
        type=int,
        default=20,
        help="Maximum number of unmatched paths to show per section.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Archive (or delete, depending on archive_before_delete) Markdown bundles whose "
            "source PDF no longer exists. Skips the PDF currently being converted."
        ),
    )
    convert_group = parser.add_mutually_exclusive_group()
    convert_group.add_argument(
        "--convert",
        dest="convert",
        action="store_true",
        default=True,
        help=(
            "Run conversion for PDFs needing conversion. This is the default. Defers to an "
            "already-running conversion (per conversion_status.json) to avoid double work."
        ),
    )
    convert_group.add_argument(
        "--no-convert",
        dest="convert",
        action="store_false",
        help="Only print the monitor report; do not run Marker conversions.",
    )
    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args()

    if args.background:
        pid = launch_background_monitor(args)
        print(f"Started hidden monitor process: pid={pid}")
        return

    if args.convert:
        os.environ["PAPER_TO_MARKDOWN_LOG_CONSOLE"] = "0"

    active = args.apply or args.convert
    active_logger = (
        setup_logger(
            load_config(args.config),
            logger_name="paper_to_markdown.monitor",
            console=False,
        )
        if active
        else None
    )

    if not args.watch:
        print(build_report(args.config, list_limit=max(args.list_limit, 0)))
        if active and active_logger is not None:
            config = load_config(args.config)
            current_conversion = load_current_conversion(config)
            controller = is_controller_mode(config)
            if not controller and current_conversion_is_active(current_conversion):
                active_logger.info(
                    "Monitor report only because a conversion is already active "
                    "(pid=%s, source=%s)",
                    current_conversion.get("pid"),
                    current_conversion.get("source_relpath"),
                )
                return
            if args.convert and controller:
                apply_controller_postprocess(config, args.apply, active_logger)
            elif args.convert:
                summary = load_index_summary(config)
                conv = apply_pending_conversions(
                    config, summary, args.config, current_conversion, active_logger
                )
                active_logger.info(
                    "Converted this cycle: %s (errors %s, skipped_running=%s)",
                    len(conv["converted"]),
                    len(conv["errors"]),
                    conv["skipped_running"],
                )
            if args.apply:
                cleanup = apply_orphan_cleanup(config, current_conversion, active_logger)
                active_logger.info(
                    "Removed orphans this cycle: %s (skipped %s, errors %s)",
                    len(cleanup["removed"]),
                    len(cleanup["skipped"]),
                    len(cleanup["errors"]),
                )
        return

    eta_tracker = EtaTracker()

    while True:
        config = load_config(args.config)
        summary = load_index_summary(config)
        current_conversion = load_current_conversion(config)
        eta_text = eta_tracker.estimate(
            remaining=len(summary["pending_conversion"]),
            processed=summary["matched_success"] + summary["matched_failed"],
        )

        print("=" * 60)
        print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        print(
            build_report(
                args.config,
                list_limit=max(args.list_limit, 0),
                eta_text=eta_text,
            )
        )
        if args.convert and active_logger is not None:
            controller = is_controller_mode(config)
            try:
                if controller:
                    apply_controller_postprocess(config, args.apply, active_logger)
                else:
                    if current_conversion_is_active(current_conversion):
                        active_logger.info(
                            "Monitor report only because a conversion is already active "
                            "(pid=%s, source=%s)",
                            current_conversion.get("pid"),
                            current_conversion.get("source_relpath"),
                        )
                        time.sleep(max(args.interval, 1))
                        continue
                    conv = apply_pending_conversions(
                        config, summary, args.config, current_conversion, active_logger
                    )
                    active_logger.info(
                        "Converted this cycle: %s (errors %s, skipped_running=%s)",
                        len(conv["converted"]),
                        len(conv["errors"]),
                        conv["skipped_running"],
                    )
            except Exception as exc:
                active_logger.error("Conversion cycle failed: %s", exc)
        if args.apply and active_logger is not None:
            try:
                # Re-read current_conversion: --convert may have produced a fresh status file.
                cleanup = apply_orphan_cleanup(
                    config, load_current_conversion(config), active_logger
                )
                active_logger.info(
                    "Removed orphans this cycle: %s (skipped %s, errors %s)",
                    len(cleanup["removed"]),
                    len(cleanup["skipped"]),
                    len(cleanup["errors"]),
                )
            except Exception as exc:
                active_logger.error("Orphan cleanup cycle failed: %s", exc)
        time.sleep(max(args.interval, 1))


if __name__ == "__main__":
    main()
