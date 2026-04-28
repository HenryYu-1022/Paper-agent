"""Move loose image files in a Markdown bundle into a ``figures/`` subfolder.

Marker writes images alongside the ``.md`` file. This module relocates them
into ``<bundle>/figures/`` and rewrites image links inside the Markdown so
the bundle directory stays tidy. Idempotent: re-running on an already
organized bundle is a no-op.

Run as a CLI for a one-shot pass over the existing library:

    python -m paper_to_markdown.organize_figures              # dry run
    python -m paper_to_markdown.organize_figures --apply      # actually move

Or call ``organize_bundle`` from the conversion pipeline so newly produced
bundles get organized automatically.
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, unquote

from .common import (
    is_supporting_artifact_name,
    load_config,
    markdown_root,
    setup_logger,
)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".svg"}
FIGURES_DIRNAME = "figures"

# ![alt](path) and ![alt](path "title"); also bare <img src="path">
_MD_IMAGE_LINK = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)([^)]*\))")
_HTML_IMG_SRC = re.compile(r'(<img[^>]*\bsrc=)(["\'])([^"\']+)(\2)', re.IGNORECASE)


def _is_local_relative(target: str) -> bool:
    if not target:
        return False
    if target.startswith(("http://", "https://", "data:", "/", "#")):
        return False
    if "://" in target:
        return False
    return True


def _rewrite_markdown_links(text: str, image_names: set[str], figures_dir: str) -> str:
    """Rewrite image links whose target is one of ``image_names`` (top-level).

    Matches both raw and percent-encoded forms.
    """
    encoded_to_name = {quote(name): name for name in image_names}

    def map_target(target: str) -> str | None:
        if not _is_local_relative(target):
            return None
        # Strip optional "./" prefix
        clean = target[2:] if target.startswith("./") else target
        # Already inside figures dir? skip
        if clean.startswith(f"{figures_dir}/"):
            return None
        decoded = unquote(clean)
        if decoded in image_names:
            new = f"{figures_dir}/{quote(decoded)}" if clean != decoded else f"{figures_dir}/{decoded}"
            return new
        if clean in encoded_to_name:
            return f"{figures_dir}/{clean}"
        return None

    def md_sub(match: re.Match[str]) -> str:
        prefix, target, suffix = match.group(1), match.group(2), match.group(3)
        new_target = map_target(target)
        if new_target is None:
            return match.group(0)
        return f"{prefix}{new_target}{suffix}"

    def html_sub(match: re.Match[str]) -> str:
        prefix, quote_char, target, _ = match.group(1), match.group(2), match.group(3), match.group(4)
        new_target = map_target(target)
        if new_target is None:
            return match.group(0)
        return f"{prefix}{quote_char}{new_target}{quote_char}"

    text = _MD_IMAGE_LINK.sub(md_sub, text)
    text = _HTML_IMG_SRC.sub(html_sub, text)
    return text


def _collect_top_level_images(bundle_dir: Path) -> list[Path]:
    images: list[Path] = []
    for entry in bundle_dir.iterdir():
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        images.append(entry)
    return images


def _iter_main_markdown(bundle_dir: Path) -> Iterable[Path]:
    """Markdown files at the top of the bundle that are not supporting artifacts."""
    for path in bundle_dir.glob("*.md"):
        if is_supporting_artifact_name(path.name):
            continue
        yield path


def organize_bundle(
    bundle_dir: Path,
    *,
    figures_dirname: str = FIGURES_DIRNAME,
    apply: bool = True,
    logger: logging.Logger | None = None,
) -> dict[str, int]:
    """Move loose images in ``bundle_dir`` into ``figures/`` and rewrite links.

    Returns a small stats dict. ``apply=False`` performs a dry run.
    """
    log = logger or logging.getLogger(__name__)
    stats = {"moved": 0, "rewrote_md": 0, "scanned_md": 0}

    if not bundle_dir.is_dir():
        return stats

    images = _collect_top_level_images(bundle_dir)
    if not images:
        return stats

    figures_dir = bundle_dir / figures_dirname
    image_names = {img.name for img in images}

    if apply:
        figures_dir.mkdir(exist_ok=True)
        for img in images:
            destination = figures_dir / img.name
            if destination.exists():
                # Name collision: keep the one already in figures, drop the loose copy.
                log.warning("Figure already exists, removing loose duplicate: %s", img)
                img.unlink()
                continue
            shutil.move(str(img), str(destination))
            stats["moved"] += 1
    else:
        stats["moved"] = len(images)

    for md_path in _iter_main_markdown(bundle_dir):
        stats["scanned_md"] += 1
        try:
            original = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warning("Could not read %s: %s", md_path, exc)
            continue
        updated = _rewrite_markdown_links(original, image_names, figures_dirname)
        if updated != original:
            stats["rewrote_md"] += 1
            if apply:
                md_path.write_text(updated, encoding="utf-8")

    if stats["moved"] or stats["rewrote_md"]:
        log.info(
            "Organized bundle %s: moved=%d rewrote_md=%d (apply=%s)",
            bundle_dir,
            stats["moved"],
            stats["rewrote_md"],
            apply,
        )
    return stats


def organize_library(
    md_root: Path,
    *,
    figures_dirname: str = FIGURES_DIRNAME,
    apply: bool = True,
    logger: logging.Logger | None = None,
) -> dict[str, int]:
    """Walk every bundle under ``md_root`` and organize loose images."""
    log = logger or logging.getLogger(__name__)
    totals = {"bundles": 0, "bundles_changed": 0, "moved": 0, "rewrote_md": 0}

    for md_path in md_root.rglob("*.md"):
        if is_supporting_artifact_name(md_path.name):
            continue
        bundle_dir = md_path.parent
        # Only treat directories that look like bundles (contain a top-level md).
        if bundle_dir == md_root:
            continue
        if bundle_dir.is_symlink():
            # Skip mirror symlinks; we'll process the real bundle directly.
            continue
        totals["bundles"] += 1
        result = organize_bundle(
            bundle_dir,
            figures_dirname=figures_dirname,
            apply=apply,
            logger=log,
        )
        if result["moved"] or result["rewrote_md"]:
            totals["bundles_changed"] += 1
            totals["moved"] += result["moved"]
            totals["rewrote_md"] += result["rewrote_md"]

    log.info(
        "Library organize finished (apply=%s): bundles=%d changed=%d moved=%d rewrote_md=%d",
        apply,
        totals["bundles"],
        totals["bundles_changed"],
        totals["moved"],
        totals["rewrote_md"],
    )
    return totals


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Move loose images in each Markdown bundle into a 'figures/' subdir "
            "and rewrite Markdown links."
        )
    )
    parser.add_argument(
        "--config",
        default="paper_to_markdown/settings.json",
        help="Path to settings.json (used to locate markdown_root).",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Override markdown root (defaults to <output_root>/markdown from settings).",
    )
    parser.add_argument(
        "--figures-dir",
        default=FIGURES_DIRNAME,
        help=f"Subfolder name to hold figures (default: {FIGURES_DIRNAME}).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually move files. Without this flag the run is a dry-run.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    config = load_config(args.config)
    logger = setup_logger(config, logger_name="paper_to_markdown.organize_figures", console=True)

    root = Path(args.root) if args.root else markdown_root(config)
    if not root.exists():
        logger.error("Markdown root does not exist: %s", root)
        return

    totals = organize_library(
        root,
        figures_dirname=args.figures_dir,
        apply=args.apply,
        logger=logger,
    )
    mode = "APPLIED" if args.apply else "DRY RUN"
    print(
        f"[{mode}] root={root}\n"
        f"  bundles scanned : {totals['bundles']}\n"
        f"  bundles changed : {totals['bundles_changed']}\n"
        f"  images moved    : {totals['moved']}\n"
        f"  markdown rewrote: {totals['rewrote_md']}"
    )


if __name__ == "__main__":
    main()
