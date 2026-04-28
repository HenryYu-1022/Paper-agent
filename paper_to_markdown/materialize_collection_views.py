"""Materialize Zotero collection markdown views from converted bundles.

This script never runs Marker and never changes the primary Markdown library.
It only reads frontmatter + Zotero collection metadata and creates lightweight
views under ``output_root/zotero_markdown`` by default.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    from .common import is_relative_to, load_config, output_root, safe_rmtree, setup_logger, to_posix_path_str
    from .frontmatter_index import FrontmatterIndex
    from .zotero_collections import ZoteroCollectionMap
except ImportError:
    from common import is_relative_to, load_config, output_root, safe_rmtree, setup_logger, to_posix_path_str
    from frontmatter_index import FrontmatterIndex
    from zotero_collections import ZoteroCollectionMap


VIEW_MODES = {"copy", "symlink"}


def zotero_markdown_root(config: dict[str, Any]) -> Path:
    configured = str(config.get("zotero_markdown_root", "")).strip()
    if not configured:
        configured = str(config.get("collection_views_root", "")).strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return output_root(config) / "zotero_markdown"


def collection_views_root(config: dict[str, Any]) -> Path:
    """Backward-compatible alias for older imports."""
    return zotero_markdown_root(config)


def _sanitize_collection_path(collection_path: str) -> Path:
    parts = [
        _sanitize_path_part(part.strip())
        for part in collection_path.replace("\\", "/").split("/")
        if part.strip() and part.strip() not in {".", ".."}
    ]
    return Path(*parts) if parts else Path("_uncategorized")


def _sanitize_path_part(value: str) -> str:
    sanitized = "".join("_" if char in '<>:"/\\|?*' or ord(char) < 32 else char for char in value)
    sanitized = sanitized.rstrip(" .")
    return sanitized or "_"


def _remove_existing(target: Path, allowed_root: Path) -> None:
    if target.exists() or target.is_symlink():
        if not is_relative_to(target, allowed_root):
            raise ValueError(f"Refusing to replace path outside collection views root: {target}")
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            safe_rmtree(target, allowed_root)


def _copy_bundle(source: Path, target: Path, views_root: Path) -> None:
    _remove_existing(target, views_root)
    shutil.copytree(source, target)


def _symlink_bundle(source: Path, target: Path, views_root: Path) -> None:
    _remove_existing(target, views_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source, target_is_directory=True)


def _collections_for_entry(
    entry: dict[str, Any],
    zotero_map: ZoteroCollectionMap | None,
) -> list[str]:
    filename = str(entry.get("source_filename") or Path(str(entry.get("source_relpath", ""))).name)
    collections: list[str] = []
    if zotero_map is not None:
        collections = zotero_map.get_collections_for_pdf(filename)
    if not collections:
        raw = entry.get("zotero_collections") or []
        if isinstance(raw, list):
            collections = [str(item) for item in raw if str(item).strip()]
    return sorted(dict.fromkeys(collections))


def materialize_views(config: dict[str, Any], mode: str, clean: bool = False) -> dict[str, int]:
    if mode not in VIEW_MODES:
        raise ValueError(f"Invalid view mode: {mode}")

    views_root = zotero_markdown_root(config)
    logger = setup_logger(config, logger_name="paper_to_markdown.zotero_markdown")
    if clean and views_root.exists():
        safe_rmtree(views_root, output_root(config))
    views_root.mkdir(parents=True, exist_ok=True)

    zotero_map = None
    if config.get("zotero_db_path"):
        zotero_map = ZoteroCollectionMap(config["zotero_db_path"])

    index = FrontmatterIndex(config)
    created = 0
    skipped = 0
    seen_targets: set[Path] = set()
    for entry in index.data.get("files", {}).values():
        if entry.get("status") != "success":
            skipped += 1
            continue
        if entry.get("document_role", "main") != "main":
            skipped += 1
            continue

        source_bundle = Path(str(entry.get("markdown_bundle_dir", "")))
        if not source_bundle.is_dir():
            skipped += 1
            continue

        collections = _collections_for_entry(entry, zotero_map)
        if not collections:
            skipped += 1
            continue

        bundle_name = _sanitize_path_part(source_bundle.name)
        for collection_path in collections:
            target = views_root / _sanitize_collection_path(collection_path) / bundle_name
            target_key = target.resolve(strict=False)
            if target_key in seen_targets:
                skipped += 1
                continue
            seen_targets.add(target_key)
            if mode == "copy":
                _copy_bundle(source_bundle, target, views_root)
            else:
                _symlink_bundle(source_bundle, target, views_root)
            created += 1
            logger.info("Created collection %s view: %s -> %s", mode, target, source_bundle)

    return {"created": created, "skipped": skipped, "views_root": to_posix_path_str(views_root)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create Zotero collection markdown views from existing bundles without running Marker.",
    )
    parser.add_argument("--config", default=None, help="Path to settings.json.")
    parser.add_argument(
        "--mode",
        choices=sorted(VIEW_MODES),
        default="symlink",
        help="How to materialize collection views. Run this on the controller host for symlink mode.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete the Zotero markdown view root before rebuilding it.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config)
    summary = materialize_views(config, mode=args.mode, clean=args.clean)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
