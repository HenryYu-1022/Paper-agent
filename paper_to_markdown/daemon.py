from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from .common import (
        ensure_directories,
        load_config,
        relative_pdf_path,
        setup_logger,
    )
    from .frontmatter_index import FrontmatterIndex
    from .pipeline import (
        archive_pdf_artifacts,
        convert_one_pdf_with_retries,
        delete_pdf_artifacts,
    )
except ImportError:
    from common import (
        ensure_directories,
        load_config,
        relative_pdf_path,
        setup_logger,
    )
    from frontmatter_index import FrontmatterIndex
    from pipeline import (
        archive_pdf_artifacts,
        convert_one_pdf_with_retries,
        delete_pdf_artifacts,
    )


DEFAULT_IDLE_TIMEOUT_SECONDS = 300


@dataclass
class DaemonContext:
    config: dict[str, Any] | None = None
    config_path: str | None = None
    idle_timeout_seconds: int | None = None
    should_stop: bool = False
    logger: Any = None
    index: FrontmatterIndex = field(init=False)

    def __post_init__(self) -> None:
        if self.config is None:
            self.config = load_config(self.config_path)
        ensure_directories(self.config)
        if self.idle_timeout_seconds is None:
            self.idle_timeout_seconds = int(
                self.config.get("daemon_idle_timeout_seconds", DEFAULT_IDLE_TIMEOUT_SECONDS)
            )
        self.logger = self.logger or setup_logger(self.config, logger_name="paper_to_markdown.daemon")
        self.index = FrontmatterIndex(self.config)

    def reload_index(self) -> None:
        self.index.reload()


def _response(request_id: Any, ok: bool, result: Any = None, error: str | None = None) -> dict[str, Any]:
    response: dict[str, Any] = {"id": request_id, "ok": ok}
    if ok:
        response["result"] = result if result is not None else {}
    else:
        response["error"] = error or "unknown error"
    return response


def _source_relpath_from_request(request: dict[str, Any], context: DaemonContext) -> str:
    rel_key = str(request.get("source_relpath") or request.get("rel_key") or "").strip()
    if rel_key:
        return rel_key.replace("\\", "/")

    path = str(request.get("path") or request.get("source_pdf") or "").strip()
    if not path:
        raise ValueError("Missing source_relpath or path")

    assert context.config is not None
    return str(relative_pdf_path(Path(path).resolve(), Path(context.config["input_root"]))).replace("\\", "/")


def _cleanup_orphans(context: DaemonContext, mode: str) -> dict[str, Any]:
    assert context.config is not None
    cleaned = 0
    skipped = 0
    results: list[dict[str, Any]] = []
    for rel_key, entry in list(context.index.data.get("files", {}).items()):
        source_pdf = str(entry.get("source_pdf") or "").strip()
        current_source_pdf = Path(context.config["input_root"]) / rel_key
        if (source_pdf and Path(source_pdf).exists()) or current_source_pdf.exists():
            skipped += 1
            continue
        if mode == "archive":
            result = archive_pdf_artifacts(rel_key, context.config, context.index, context.logger)
            if result.get("archived"):
                cleaned += 1
        elif mode == "delete":
            result = delete_pdf_artifacts(rel_key, context.config, context.index, context.logger)
            if result.get("deleted"):
                cleaned += 1
        else:
            raise ValueError("cleanup_orphans mode must be archive or delete")
        results.append(result)
        context.reload_index()
    return {"mode": mode, "cleaned": cleaned, "skipped": skipped, "results": results}


def handle_request(request: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    request_id = request.get("id")
    command = str(request.get("command") or request.get("cmd") or "").strip()
    if not command:
        return _response(request_id, False, error="Missing command")

    try:
        assert context.config is not None
        if command == "ping":
            return _response(request_id, True, {"status": "pong"})

        if command == "rescan":
            context.reload_index()
            return _response(
                request_id,
                True,
                {"entries": len(context.index.data.get("files", {}))},
            )

        if command == "convert":
            path = request.get("path") or request.get("source_pdf")
            if not path:
                raise ValueError("convert requires path")
            output = convert_one_pdf_with_retries(
                path,
                config_path=context.config_path,
                force_reconvert=bool(request.get("force", False)),
            )
            context.reload_index()
            return _response(
                request_id,
                True,
                {"output_markdown": str(output) if output else None},
            )

        if command in {"delete_orphan", "delete"}:
            rel_key = _source_relpath_from_request(request, context)
            result = delete_pdf_artifacts(rel_key, context.config, context.index, context.logger)
            context.reload_index()
            return _response(request_id, True, result)

        if command in {"archive_orphan", "archive"}:
            rel_key = _source_relpath_from_request(request, context)
            result = archive_pdf_artifacts(rel_key, context.config, context.index, context.logger)
            context.reload_index()
            return _response(request_id, True, result)

        if command == "cleanup_orphans":
            mode = str(request.get("mode") or "archive")
            return _response(request_id, True, _cleanup_orphans(context, mode))

        if command == "shutdown":
            context.should_stop = True
            return _response(request_id, True, {"status": "stopping"})

        return _response(request_id, False, error=f"Unknown command: {command}")
    except Exception as exc:
        if context.logger is not None:
            context.logger.exception("Daemon command failed: %s", command)
        return _response(request_id, False, error=str(exc))


def _stdin_reader(line_queue: "queue.Queue[str | None]") -> None:
    for line in sys.stdin:
        line_queue.put(line)
    line_queue.put(None)


def serve(context: DaemonContext) -> int:
    line_queue: "queue.Queue[str | None]" = queue.Queue()
    reader = threading.Thread(target=_stdin_reader, args=(line_queue,), daemon=True)
    reader.start()

    idle_timeout = max(int(context.idle_timeout_seconds or 0), 0)
    last_activity = time.monotonic()

    while not context.should_stop:
        timeout: float | None
        if idle_timeout:
            elapsed = time.monotonic() - last_activity
            timeout = max(idle_timeout - elapsed, 0.1)
        else:
            timeout = None

        try:
            line = line_queue.get(timeout=timeout)
        except queue.Empty:
            context.logger.info("Daemon idle timeout reached, shutting down")
            break

        if line is None:
            context.logger.info("Daemon stdin closed, shutting down")
            break

        line = line.strip()
        if not line:
            continue
        last_activity = time.monotonic()
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Request must be a JSON object")
            response = handle_request(request, context)
        except Exception as exc:
            response = _response(None, False, error=str(exc))

        print(json.dumps(response, ensure_ascii=False), flush=True)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run paper_to_markdown as a JSON-lines daemon.")
    parser.add_argument("--config", default=None, help="Optional path to settings.json.")
    parser.add_argument(
        "--idle-timeout",
        type=int,
        default=None,
        help="Seconds to wait without requests before exiting. 0 disables the timeout.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    context = DaemonContext(config_path=args.config, idle_timeout_seconds=args.idle_timeout)
    raise SystemExit(serve(context))


if __name__ == "__main__":
    main()
