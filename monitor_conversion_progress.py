from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from paper_to_markdown.common import find_all_pdfs, load_config, logs_root, manifest_path


LOG_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| (?P<level>[^|]+) \| (?P<message>.*)$"
)
DISCOVERED_RE = re.compile(r"^PDFs discovered: (?P<count>\d+)$")
SKIPPED_RE = re.compile(r"^Skipping unchanged PDF: (?P<path>.+)$")
START_RE = re.compile(r"^Starting marker conversion: (?P<path>.+)$")
COMPLETED_RE = re.compile(r"^Conversion completed: (?P<source>.+?) -> (?P<output>.+)$")
FAILED_RE = re.compile(r"^Conversion failed: (?P<source>.+)$")
FINISHED_RE = re.compile(
    r"^Batch finished: converted=(?P<converted>\d+) skipped=(?P<skipped>\d+) failed=(?P<failed>\d+)$"
)


@dataclass
class BatchProgress:
    started_at: datetime
    discovered: int
    skipped: int = 0
    converted: int = 0
    failed: int = 0
    current_pdf: str | None = None
    current_started_at: datetime | None = None
    current_elapsed_seconds: float = 0.0
    durations_seconds: list[float] = field(default_factory=list)
    finished_at: datetime | None = None

    @property
    def completed_units(self) -> int:
        return self.skipped + self.converted + self.failed

    @property
    def in_progress(self) -> int:
        return 1 if self.current_pdf else 0

    @property
    def remaining(self) -> int:
        remaining = self.discovered - self.completed_units - self.in_progress
        return max(remaining, 0)

    @property
    def average_conversion_seconds(self) -> float | None:
        if not self.durations_seconds:
            return None
        return sum(self.durations_seconds) / len(self.durations_seconds)

    @property
    def eta_seconds(self) -> float | None:
        average = self.average_conversion_seconds
        if average is None:
            return None

        eta = average * self.remaining
        if self.current_pdf:
            eta += max(average - self.current_elapsed_seconds, 0.0)
        return max(eta, 0.0)


def parse_timestamp(raw: str) -> datetime:
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")


def load_manifest_summary(config: dict[str, Any]) -> dict[str, int]:
    input_root = Path(config["input_root"])
    manifest_file = manifest_path(config)
    total_pdfs = len(find_all_pdfs(input_root))

    if not manifest_file.exists():
        return {"total": total_pdfs, "success": 0, "failed": 0}

    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    success = 0
    failed = 0

    for entry in data.get("files", {}).values():
        source_pdf = entry.get("source_pdf")
        if not source_pdf:
            continue
        if not Path(source_pdf).exists():
            continue
        if entry.get("status") == "success":
            success += 1
        elif entry.get("status") == "failed":
            failed += 1

    return {"total": total_pdfs, "success": success, "failed": failed}


def parse_latest_batch(config: dict[str, Any]) -> BatchProgress | None:
    log_file = logs_root(config) / "app.log"
    if not log_file.exists():
        return None

    latest_batch: BatchProgress | None = None

    for raw_line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line_match = LOG_LINE_RE.match(raw_line)
        if not line_match:
            continue

        timestamp = parse_timestamp(line_match.group("timestamp"))
        message = line_match.group("message")

        discovered_match = DISCOVERED_RE.match(message)
        if discovered_match:
            latest_batch = BatchProgress(
                started_at=timestamp,
                discovered=int(discovered_match.group("count")),
            )
            continue

        if latest_batch is None:
            continue

        if SKIPPED_RE.match(message):
            latest_batch.skipped += 1
            continue

        start_match = START_RE.match(message)
        if start_match:
            latest_batch.current_pdf = start_match.group("path")
            latest_batch.current_started_at = timestamp
            latest_batch.current_elapsed_seconds = 0.0
            continue

        if COMPLETED_RE.match(message):
            latest_batch.converted += 1
            if latest_batch.current_started_at is not None:
                latest_batch.durations_seconds.append(
                    max((timestamp - latest_batch.current_started_at).total_seconds(), 0.0)
                )
            latest_batch.current_pdf = None
            latest_batch.current_started_at = None
            latest_batch.current_elapsed_seconds = 0.0
            continue

        if FAILED_RE.match(message):
            latest_batch.failed += 1
            if latest_batch.current_started_at is not None:
                latest_batch.durations_seconds.append(
                    max((timestamp - latest_batch.current_started_at).total_seconds(), 0.0)
                )
            latest_batch.current_pdf = None
            latest_batch.current_started_at = None
            latest_batch.current_elapsed_seconds = 0.0
            continue

        if FINISHED_RE.match(message):
            latest_batch.finished_at = timestamp

    if latest_batch and latest_batch.current_started_at is not None:
        latest_batch.current_elapsed_seconds = max(
            (datetime.now() - latest_batch.current_started_at).total_seconds(),
            0.0,
        )

    return latest_batch


def load_historical_average_seconds(config: dict[str, Any]) -> float | None:
    log_file = logs_root(config) / "app.log"
    if not log_file.exists():
        return None

    durations: list[float] = []
    current_started_at: datetime | None = None

    for raw_line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line_match = LOG_LINE_RE.match(raw_line)
        if not line_match:
            continue

        timestamp = parse_timestamp(line_match.group("timestamp"))
        message = line_match.group("message")

        if START_RE.match(message):
            current_started_at = timestamp
            continue

        if current_started_at is None:
            continue

        if COMPLETED_RE.match(message) or FAILED_RE.match(message):
            durations.append(max((timestamp - current_started_at).total_seconds(), 0.0))
            current_started_at = None

    if not durations:
        return None
    return sum(durations) / len(durations)


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    rounded = int(round(seconds))
    if rounded < 60:
        return f"{rounded}s"
    return str(timedelta(seconds=rounded))


def build_report(config_path: str | None = None) -> str:
    config = load_config(config_path)
    manifest_summary = load_manifest_summary(config)
    latest_batch = parse_latest_batch(config)

    lines = [
        f"Input PDFs: {manifest_summary['total']}",
        f"Manifest success: {manifest_summary['success']}",
        f"Manifest failed: {manifest_summary['failed']}",
    ]

    if latest_batch is None:
        lines.append("Active batch: none found in app.log")
        return "\n".join(lines)

    lines.extend(
        [
            f"Batch started: {latest_batch.started_at:%Y-%m-%d %H:%M:%S}",
            f"Batch discovered: {latest_batch.discovered}",
            f"Batch skipped: {latest_batch.skipped}",
            f"Batch converted: {latest_batch.converted}",
            f"Batch failed: {latest_batch.failed}",
            f"Batch in progress: {latest_batch.in_progress}",
            f"Batch remaining: {latest_batch.remaining}",
        ]
    )

    if latest_batch.current_pdf:
        lines.append(f"Current PDF: {latest_batch.current_pdf}")
        lines.append(f"Current elapsed: {format_duration(latest_batch.current_elapsed_seconds)}")

    average = latest_batch.average_conversion_seconds
    average_label = "Average conversion time"
    if average is None:
        average = load_historical_average_seconds(config)
        if average is not None:
            average_label = "Average conversion time (historical)"
    lines.append(f"{average_label}: {format_duration(average)}")

    eta_seconds = latest_batch.eta_seconds
    if eta_seconds is None and average is not None:
        eta_seconds = average * latest_batch.remaining
        if latest_batch.current_pdf:
            eta_seconds += max(average - latest_batch.current_elapsed_seconds, 0.0)
    lines.append(f"ETA: {format_duration(eta_seconds)}")
    if eta_seconds is not None:
        eta_finish = datetime.now() + timedelta(seconds=eta_seconds)
        lines.append(f"Estimated finish: {eta_finish:%Y-%m-%d %H:%M:%S}")

    lines.append(f"Batch finished: {'yes' if latest_batch.finished_at else 'no'}")
    if latest_batch.finished_at:
        lines.append(f"Finished at: {latest_batch.finished_at:%Y-%m-%d %H:%M:%S}")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monitor paper PDF conversion progress without changing conversion logic."
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Optional path to settings.json. Defaults to the workflow directory.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Refresh the report continuously.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Refresh interval in seconds for --watch mode.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if not args.watch:
        print(build_report(args.config))
        return

    while True:
        print("=" * 60)
        print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        print(build_report(args.config))
        time.sleep(max(args.interval, 1))


if __name__ == "__main__":
    main()
