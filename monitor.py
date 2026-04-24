from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from paper_to_markdown.common import find_all_pdfs, load_config, relative_pdf_path, to_posix_path_str
from paper_to_markdown.frontmatter_index import FrontmatterIndex


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
    stale_markdown_index = success_keys - input_keys

    return {
        "input_total": len(input_keys),
        "markdown_index_total": len(entries),
        "markdown_success_total": len(success_keys),
        "markdown_failed_total": len(failed_keys),
        "matched_success": len(matched_success),
        "matched_failed": len(matched_failed),
        "needs_conversion": sorted(needs_conversion),
        "stale_markdown_index": sorted(stale_markdown_index),
    }


def build_report(config_path: str | None = None, *, list_limit: int = 20) -> str:
    config = load_config(config_path)
    summary = load_index_summary(config)
    needs_conversion = summary["needs_conversion"]
    stale_markdown_index = summary["stale_markdown_index"]

    lines = [
        f"Input PDF index: {summary['input_total']}",
        f"Markdown index entries: {summary['markdown_index_total']}",
        f"Markdown success entries: {summary['markdown_success_total']}",
        f"Markdown failed entries: {summary['markdown_failed_total']}",
        f"Input PDFs matched to Markdown: {summary['matched_success']}",
        f"Input PDFs with failed Markdown status: {summary['matched_failed']}",
        f"Input PDFs needing conversion: {len(needs_conversion)}",
        f"Markdown success entries without current input PDF: {len(stale_markdown_index)}",
    ]

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
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if not args.watch:
        print(build_report(args.config, list_limit=max(args.list_limit, 0)))
        return

    while True:
        print("=" * 60)
        print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        print(build_report(args.config, list_limit=max(args.list_limit, 0)))
        time.sleep(max(args.interval, 1))


if __name__ == "__main__":
    main()
