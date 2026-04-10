"""Sync Zotero collection hierarchy into the paper-agent Markdown library.

This script periodically reads the Zotero SQLite database, compares each
converted PDF's collection assignments against the last-known state, and
updates YAML frontmatter and symlink mirrors when changes are detected.

Usage::

    # One-shot sync
    python3 sync_zotero_collections.py --once

    # Continuous daemon (default interval: 60s)
    python3 sync_zotero_collections.py

    # Custom interval
    python3 sync_zotero_collections.py --interval 30

    # Custom config file
    python3 sync_zotero_collections.py --config /path/to/settings.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import Any

try:
    from .common import (
        collection_state_path,
        load_config,
        manifest_path,
        markdown_root,
        is_relative_to,
        pdf_bundle_relpath,
        relative_pdf_path,
        safe_rmtree,
        setup_logger,
        to_posix_path_str,
        update_frontmatter_fields,
    )
    from .zotero_collections import ZoteroCollectionMap
    from .pipeline import ManifestStore
except ImportError:
    from common import (
        collection_state_path,
        load_config,
        manifest_path,
        markdown_root,
        is_relative_to,
        pdf_bundle_relpath,
        relative_pdf_path,
        safe_rmtree,
        setup_logger,
        to_posix_path_str,
        update_frontmatter_fields,
    )
    from zotero_collections import ZoteroCollectionMap
    from pipeline import ManifestStore


# ---------------------------------------------------------------------------
# Collection state persistence
# ---------------------------------------------------------------------------

def _load_collection_state(path: Path) -> dict[str, list[str]]:
    """Load the last-known ``{pdf_filename: [collection_paths]}`` state."""
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_collection_state(path: Path, state: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Mirror management helpers
# ---------------------------------------------------------------------------

def _create_single_mirror(
    bundle_dir: Path,
    mirror_dir: Path,
    mirror_mode: str,
    logger,
) -> bool:
    """Create one symlink (or copy) from *mirror_dir* → *bundle_dir*.

    Returns True on success.
    """
    if mirror_dir.exists() or mirror_dir.is_symlink():
        return False

    mirror_dir.parent.mkdir(parents=True, exist_ok=True)

    if mirror_mode == "symlink":
        try:
            mirror_dir.symlink_to(bundle_dir)
            logger.info("Created mirror symlink: %s -> %s", mirror_dir, bundle_dir)
            return True
        except OSError as exc:
            logger.warning(
                "Failed to create symlink %s: %s; falling back to copy",
                mirror_dir, exc,
            )
            shutil.copytree(bundle_dir, mirror_dir, dirs_exist_ok=True)
            return True
    else:
        shutil.copytree(bundle_dir, mirror_dir, dirs_exist_ok=True)
        logger.info("Created mirror copy: %s", mirror_dir)
        return True


def _remove_single_mirror(
    mirror_dir: Path,
    md_root: Path,
    logger,
) -> bool:
    """Remove one mirror and clean up empty parent directories."""
    if not mirror_dir.exists() and not mirror_dir.is_symlink():
        return False

    if not is_relative_to(mirror_dir, md_root):
        logger.warning("Mirror path outside markdown root, skipping: %s", mirror_dir)
        return False

    if mirror_dir.is_symlink():
        mirror_dir.unlink()
        logger.info("Removed mirror symlink: %s", mirror_dir)
    elif mirror_dir.is_dir():
        safe_rmtree(mirror_dir, md_root)
        logger.info("Removed mirror directory: %s", mirror_dir)

    # Clean empty parents up to markdown_root
    parent = mirror_dir.parent
    while parent != md_root and parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
        logger.info("Removed empty directory: %s", parent)
        parent = parent.parent
    return True


# ---------------------------------------------------------------------------
# Core sync logic
# ---------------------------------------------------------------------------

def sync_once(config: dict[str, Any], logger) -> dict[str, int]:
    """Run one sync cycle.  Returns counts of changes made."""

    zotero_db_path = config.get("zotero_db_path")
    if not zotero_db_path:
        logger.info("zotero_db_path not configured, skipping sync")
        return {"added": 0, "removed": 0, "updated": 0}

    # 1. Read current Zotero state
    zotero_map = ZoteroCollectionMap(zotero_db_path)
    zotero_map.reload()
    current_zotero = zotero_map.get_all_pdf_collections()

    # 2. Load previous state
    state_path = collection_state_path(config)
    previous_state = _load_collection_state(state_path)

    # 3. Load manifest to find converted PDFs
    manifest = ManifestStore(manifest_path(config))
    md_root = markdown_root(config)
    input_root = Path(config["input_root"])
    mirror_mode = config.get("collection_mirror_mode", "symlink")

    added = 0
    removed = 0
    updated_frontmatter = 0

    # 4. Iterate over all manifest entries
    for rel_key, entry in list(manifest.data.get("files", {}).items()):
        if entry.get("status") != "success":
            continue

        source_pdf = Path(entry.get("source_pdf", ""))
        pdf_filename = source_pdf.name
        if not pdf_filename:
            continue

        bundle_dir_str = entry.get("markdown_bundle_dir")
        if not bundle_dir_str:
            continue
        bundle_dir = Path(bundle_dir_str)
        if not bundle_dir.exists():
            continue

        # Get current Zotero collections for this PDF
        new_collections = set(current_zotero.get(pdf_filename, []))
        old_collections = set(previous_state.get(pdf_filename, []))

        if new_collections == old_collections:
            continue

        # Determine the physical collection path (where the real bundle lives)
        try:
            rel_pdf = relative_pdf_path(source_pdf, input_root)
            physical_relpath = pdf_bundle_relpath(rel_pdf)
            physical_collection = str(physical_relpath.parent).replace("\\", "/")
        except ValueError:
            physical_collection = ""

        # Collections that need new mirrors
        to_add = new_collections - old_collections
        # Collections that need mirror removal
        to_remove = old_collections - new_collections

        existing_mirrors = list(entry.get("mirror_paths", []))

        for col_path in to_add:
            if col_path == physical_collection:
                continue
            mirror_dir = md_root / col_path / source_pdf.stem
            if _create_single_mirror(bundle_dir, mirror_dir, mirror_mode, logger):
                mirror_str = to_posix_path_str(mirror_dir)
                if mirror_str not in existing_mirrors:
                    existing_mirrors.append(mirror_str)
                added += 1

        for col_path in to_remove:
            if col_path == physical_collection:
                continue
            mirror_dir = md_root / col_path / source_pdf.stem
            if _remove_single_mirror(mirror_dir, md_root, logger):
                mirror_str = to_posix_path_str(mirror_dir)
                if mirror_str in existing_mirrors:
                    existing_mirrors.remove(mirror_str)
                removed += 1

        # Update manifest with new mirror_paths
        entry["mirror_paths"] = existing_mirrors
        manifest.save()

        # Update YAML frontmatter in the main markdown file
        output_md = entry.get("output_markdown")
        if output_md:
            md_path = Path(output_md)
            if md_path.exists() and not md_path.is_symlink():
                sorted_collections = sorted(new_collections) if new_collections else []
                update_frontmatter_fields(md_path, {
                    "zotero_collections": sorted_collections,
                })
                updated_frontmatter += 1
                logger.info(
                    "Updated frontmatter for %s: %s",
                    pdf_filename, sorted_collections,
                )

    # 5. Save new state
    _save_collection_state(state_path, current_zotero)

    logger.info(
        "Sync complete: mirrors added=%d removed=%d, frontmatter updated=%d",
        added, removed, updated_frontmatter,
    )
    return {"added": added, "removed": removed, "updated": updated_frontmatter}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync Zotero collection hierarchy into the Markdown library.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to settings.json (default: paper_to_markdown/settings.json)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single sync cycle and exit",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Seconds between sync cycles (default: from config or 60)",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    logger = setup_logger(config, logger_name="sync_zotero_collections")

    if not config.get("zotero_db_path"):
        logger.error(
            "zotero_db_path is not configured in settings.json. "
            "Set it to the path of your zotero.sqlite to enable collection sync."
        )
        sys.exit(1)

    interval = args.interval or config.get("zotero_sync_interval_seconds", 60)

    # Handle graceful shutdown
    stop = False

    def _handle_signal(signum, frame):
        nonlocal stop
        stop = True
        logger.info("Received signal %s, shutting down after current cycle…", signum)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.once:
        sync_once(config, logger)
        return

    logger.info(
        "Starting Zotero collection sync daemon (interval=%ds, db=%s)",
        interval, config["zotero_db_path"],
    )

    while not stop:
        try:
            sync_once(config, logger)
        except Exception:
            logger.exception("Error during sync cycle")
        for _ in range(interval):
            if stop:
                break
            time.sleep(1)

    logger.info("Sync daemon stopped.")


if __name__ == "__main__":
    main()
