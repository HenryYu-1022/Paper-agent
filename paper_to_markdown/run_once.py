from __future__ import annotations

import argparse

from pathlib import Path

try:
    from .common import cleanup_marker_raw_root, load_config, manifest_path, setup_logger
    from .pipeline import ManifestStore, convert_all_pdfs, convert_one_pdf_with_retries, delete_pdf_artifacts
except ImportError:
    from common import cleanup_marker_raw_root, load_config, manifest_path, setup_logger
    from pipeline import ManifestStore, convert_all_pdfs, convert_one_pdf_with_retries, delete_pdf_artifacts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert paper PDFs to markdown.")
    parser.add_argument(
        "--config",
        default=None,
        help="Optional path to settings.json. Defaults to the local workflow settings.",
    )
    parser.add_argument(
        "--path",
        default=None,
        help="Convert only one specific PDF. Leave empty to scan the whole input_root.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore the manifest and reconvert matching PDFs.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N PDFs. Useful for testing.",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Remove markdown artifacts for PDFs that no longer exist in input_root.",
    )
    return parser


def cleanup_orphans(config_path: str | None = None) -> dict[str, int]:
    config = load_config(config_path)
    logger = setup_logger(config)
    manifest = ManifestStore(manifest_path(config))

    files = dict(manifest.data.get("files", {}))
    cleaned = 0
    remaining = 0

    for rel_key, entry in files.items():
        source_pdf = entry.get("source_pdf")
        if source_pdf and Path(source_pdf).exists():
            remaining += 1
            continue

        logger.info("Orphan detected (source PDF missing): %s", rel_key)
        result = delete_pdf_artifacts(rel_key, config, manifest, logger)
        if result.get("deleted"):
            cleaned += 1
        else:
            remaining += 1

    summary = {"cleaned": cleaned, "remaining": remaining}
    logger.info("Orphan cleanup finished: %s", summary)
    return summary


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.cleanup:
            summary = cleanup_orphans(config_path=args.config)
            print(summary)
            return

        if args.path:
            result = convert_one_pdf_with_retries(
                args.path, config_path=args.config, force_reconvert=args.force,
            )
            print(result if result else "Skipped")
            return

        summary = convert_all_pdfs(
            config_path=args.config,
            force_reconvert=args.force,
            limit=args.limit,
        )
        print(summary)
    finally:
        try:
            config = load_config(args.config)
            logger = setup_logger(config)
            cleanup_marker_raw_root(config, logger)
        except Exception:
            pass


if __name__ == "__main__":
    main()
