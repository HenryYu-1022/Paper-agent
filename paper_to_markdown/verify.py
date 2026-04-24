"""Controller-mode verification script.

Scans the Markdown library for bundles whose source PDF no longer exists in
input_root and immediately removes them (or reports them in dry-run mode).

Direction is one-way only: PDF deleted → Markdown deleted.
Markdown changes never affect the PDF library.

Usage:
    python verify.py                  # dry-run: report orphans, no changes
    python verify.py --apply          # delete/archive orphan bundles now
    python verify.py --watch          # loop with --apply every --interval seconds
    python verify.py --report-json    # write JSON report to stdout
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

try:
    from .common import load_config, setup_logger
    from .pipeline import ManifestStore, archive_pdf_artifacts, delete_pdf_artifacts
except ImportError:
    from common import load_config, setup_logger
    from pipeline import ManifestStore, archive_pdf_artifacts, delete_pdf_artifacts


def _scan(
    config: dict[str, Any],
    manifest: ManifestStore,
) -> tuple[list[str], list[str], list[str]]:
    """Return (ok_keys, orphan_main_keys, orphan_supporting_keys).

    Main entries are checked against input_root/<rel_key>.
    Supporting entries follow the same check against their own source PDF.
    Main orphans are returned before supporting ones so callers can delete
    primary bundles first (which removes supporting files inside them too).
    """
    input_root = Path(config["input_root"])
    ok: list[str] = []
    orphan_main: list[str] = []
    orphan_supporting: list[str] = []

    for rel_key, entry in manifest.data.get("files", {}).items():
        if entry.get("status") != "success":
            continue
        pdf_path = input_root / rel_key
        role = entry.get("document_role", "main")
        if pdf_path.exists():
            ok.append(rel_key)
        elif role == "supporting":
            orphan_supporting.append(rel_key)
        else:
            orphan_main.append(rel_key)

    return sorted(ok), sorted(orphan_main), sorted(orphan_supporting)


def _remove_orphan(
    rel_key: str,
    config: dict[str, Any],
    manifest: ManifestStore,
    logger: Any,
) -> dict[str, Any]:
    if config.get("archive_before_delete", True):
        result = archive_pdf_artifacts(rel_key, config, manifest, logger)
        logger.info("Archived orphan %s → %s", rel_key, result.get("archive_root", ""))
    else:
        result = delete_pdf_artifacts(rel_key, config, manifest, logger)
        logger.info("Deleted orphan %s", rel_key)
    return result


def run_verify(
    config: dict[str, Any],
    apply: bool = False,
    report_json: bool = False,
) -> dict[str, Any]:
    logger = setup_logger(config)
    manifest = ManifestStore(config)
    ok_keys, orphan_main, orphan_supporting = _scan(config, manifest)

    report: dict[str, Any] = {
        "ok": len(ok_keys),
        "orphan": len(orphan_main) + len(orphan_supporting),
        "removed": [],
        "errors": [],
        "dry_run": not apply,
    }

    if not apply:
        for rel_key in orphan_main + orphan_supporting:
            logger.info("Orphan (dry-run, no changes): %s", rel_key)
            report["removed"].append({"rel_key": rel_key, "dry_run": True})
    else:
        # Delete main bundles first — this physically removes supporting files inside them.
        for rel_key in orphan_main:
            try:
                result = _remove_orphan(rel_key, config, manifest, logger)
                report["removed"].append({"rel_key": rel_key, "result": result})
                manifest = ManifestStore(config)  # reload after each deletion
            except Exception as exc:
                logger.error("Failed to remove orphan %s: %s", rel_key, exc)
                report["errors"].append({"rel_key": rel_key, "error": str(exc)})

        # Supporting entries: their files may already be gone if the primary bundle was removed.
        for rel_key in orphan_supporting:
            entry = manifest.get(rel_key)
            if entry is None:
                # Already cleaned up when the primary bundle was deleted above.
                continue
            output_md = entry.get("output_markdown", "")
            if output_md and not Path(output_md).exists():
                manifest.remove_entry(rel_key)
                logger.info("Cleared stale supporting entry (file already gone): %s", rel_key)
                manifest = ManifestStore(config)
                continue
            try:
                result = _remove_orphan(rel_key, config, manifest, logger)
                report["removed"].append({"rel_key": rel_key, "result": result})
                manifest = ManifestStore(config)
            except Exception as exc:
                logger.error("Failed to remove orphan %s: %s", rel_key, exc)
                report["errors"].append({"rel_key": rel_key, "error": str(exc)})

    if report_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        action = "would remove" if not apply else "removed"
        logger.info(
            "Verify finished: ok=%s orphan=%s %s=%s errors=%s",
            report["ok"],
            report["orphan"],
            action,
            len(report["removed"]),
            len(report["errors"]),
        )

    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Controller mode: delete Markdown bundles whose source PDF is gone."
    )
    parser.add_argument("--config", default=None, help="Path to settings.json.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually remove orphan bundles. Without this flag the script is read-only.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Run in a loop, applying removals every --interval seconds.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Seconds between scans in watch mode (default: 60).",
    )
    parser.add_argument(
        "--report-json",
        action="store_true",
        help="Print a JSON summary to stdout instead of plain log output.",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    if config.get("run_mode") == "runner":
        parser.exit(1, "runner mode must not run verify.py; use convert.py instead.\n")

    if args.watch:
        logger = setup_logger(config)
        logger.info(
            "Verify watch mode started (interval=%ss, apply=%s)", args.interval, args.apply
        )
        while True:
            run_verify(config, apply=args.apply, report_json=args.report_json)
            time.sleep(args.interval)
    else:
        run_verify(config, apply=args.apply, report_json=args.report_json)


if __name__ == "__main__":
    main()
