from __future__ import annotations

import argparse
from pathlib import Path

from paper_to_markdown.common import (
    bundle_dir_for_pdf,
    cleanup_marker_raw_root,
    find_all_pdfs,
    load_config,
    manifest_path,
    relative_pdf_path,
    setup_logger,
    supporting_markdown_name,
    supporting_source_info,
)
from paper_to_markdown.pipeline import ManifestStore, convert_one_pdf, output_markdown_matches_current_layout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill missing supporting markdown files into the main paper bundle."
    )
    parser.add_argument(
        "--config",
        default="paper_to_markdown/settings.json",
        help="Path to settings.json.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Convert missing supporting PDFs. Without this flag, only print what would be converted.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only inspect the first N missing supporting PDFs.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    config = load_config(args.config)
    if str(config.get("run_mode", "")).strip() == "controller":
        parser.exit(1, "controller mode must not run backfill.py; backfill invokes marker on the runner host.\n")
    logger = setup_logger(config, logger_name="paper_to_markdown.backfill")
    input_root = Path(config["input_root"])
    manifest = ManifestStore(manifest_path(config))

    try:
        missing: list[tuple[Path, Path]] = []
        for pdf_path in find_all_pdfs(input_root):
            supporting_info = supporting_source_info(pdf_path)
            if not supporting_info:
                continue

            rel_key = str(relative_pdf_path(pdf_path, input_root)).replace("\\", "/")
            entry = manifest.get(rel_key)
            if output_markdown_matches_current_layout(pdf_path, input_root, config, entry):
                continue

            primary_pdf, supporting_index = supporting_info
            target_md = bundle_dir_for_pdf(primary_pdf, input_root, config) / supporting_markdown_name(supporting_index)
            if not target_md.exists():
                missing.append((pdf_path, target_md))

        if args.limit is not None:
            missing = missing[: args.limit]

        print(f"missing_supporting={len(missing)}")
        for pdf_path, target_md in missing:
            print(f"{pdf_path} -> {target_md}")

        if not args.apply:
            print("applied=false")
            return

        converted = 0
        failed = 0
        for pdf_path, _target_md in missing:
            try:
                convert_one_pdf(pdf_path, config_path=args.config, force_reconvert=True)
                converted += 1
            except Exception as exc:
                failed += 1
                print(f"FAILED {pdf_path}: {exc}")

        print(f"converted={converted}")
        print(f"failed={failed}")
        print("applied=true")
    finally:
        cleanup_marker_raw_root(config, logger)


if __name__ == "__main__":
    main()
