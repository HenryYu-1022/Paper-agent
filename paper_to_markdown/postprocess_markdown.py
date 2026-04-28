"""Post-process converted Markdown bundles after Marker has finished.

This script is intentionally separate from the watcher/conversion path.  It
looks at already-converted Markdown to decide whether suffix variants are
duplicate main papers or supporting information, then cleans or merges them.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    from .common import (
        build_frontmatter,
        is_relative_to,
        load_config,
        markdown_root,
        parse_frontmatter,
        raw_root,
        relative_pdf_path,
        safe_rmtree,
        setup_logger,
        supporting_assets_dir_name,
        supporting_markdown_name,
        to_posix_path_str,
    )
    from .frontmatter_index import FrontmatterIndex
    from .pipeline import (
        ManifestStore,
        _markdowns_are_near_duplicates,
        delete_pdf_artifacts,
        safe_unlink,
    )
except ImportError:
    from common import (
        is_relative_to,
        load_config,
        markdown_root,
        parse_frontmatter,
        raw_root,
        relative_pdf_path,
        safe_rmtree,
        setup_logger,
        supporting_assets_dir_name,
        supporting_markdown_name,
        to_posix_path_str,
        build_frontmatter,
    )
    from frontmatter_index import FrontmatterIndex
    from pipeline import (
        ManifestStore,
        _markdowns_are_near_duplicates,
        delete_pdf_artifacts,
        safe_unlink,
    )


NUMBER_SUFFIX_RE = re.compile(r"^(?P<base>.+?)(?:[\s_-]+|\s*\()(?P<num>[1-9]\d*)\)?$")
SUPPORTING_SUFFIX_RE = re.compile(
    r"^(?P<base>.+?)(?:[\s_\-()]+)"
    r"(?P<label>"
    r"si|"
    r"supporting(?:\s+information|\s+info)?|"
    r"supplement(?:ary|al)?(?:\s+information|\s+info)?|"
    r"supplementary\s+material|"
    r"supplemental\s+material"
    r")$",
    re.IGNORECASE,
)
SUPPORTING_MARKDOWN_FILE_RE = re.compile(r"^supporting(?:_(?P<index>\d+))?\.md$")

_IMAGE_ONLY_LINE_RE = re.compile(r"^!\[.*?\]\(.*?\)\s*$")
_DIVIDER_LINE_RE = re.compile(r"^[-=*_]{3,}\s*$")
_MD_SYNTAX_STRIP_RE = re.compile(r"[#*_`\[\]()~]+")
_WHITESPACE_RUN_RE = re.compile(r"\s+")

_SI_HEADING_RE = re.compile(
    r"^(supporting|supplementary|supplemental|support)\s+"
    r"(information|info|materials?|data|methods?|notes?|figures?|tables?|discussions?|text|results?)\b",
    re.IGNORECASE,
)
_ELECTRONIC_SI_RE = re.compile(
    r"^electronic\s+(supplementary|supporting)\s+(materials?|information|info)\b",
    re.IGNORECASE,
)
_FILENAME_SI_RE = re.compile(
    r"^file\s*name\s*[:\-]\s*(supplementary|supporting)\s+(information|info|materials?)\b",
    re.IGNORECASE,
)
_SI_SHORT_LABEL_RE = re.compile(
    r"^(si|s\.\s*i\.)(\s+(for|to|of)\b|\s*[:\-]|\s*$)",
    re.IGNORECASE,
)

MEANINGFUL_LEAD_LINES = 3
DUPLICATE_LEAD_NORMALIZED_CHARS = 500
DUPLICATE_LEAD_COMPARE_CHARS = 200


def _normalize_key(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", value.lower())


def _first_meaningful_lines(body: str, n: int = MEANINGFUL_LEAD_LINES) -> list[str]:
    collected: list[str] = []
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if _IMAGE_ONLY_LINE_RE.match(stripped):
            continue
        if _DIVIDER_LINE_RE.match(stripped):
            continue
        if stripped.startswith("|"):
            continue
        collected.append(stripped)
        if len(collected) >= n:
            break
    return collected


def _plain_text(line: str) -> str:
    plain = _MD_SYNTAX_STRIP_RE.sub(" ", line)
    return _WHITESPACE_RUN_RE.sub(" ", plain).strip()


def _strip_full_text_prefix(body: str) -> str:
    body = body.lstrip()
    if body[:12].lower() == "## full text":
        body = body[12:].lstrip(" \t\r\n#")
    return body


def _normalized_body_lead(md_path: Path, limit: int = DUPLICATE_LEAD_NORMALIZED_CHARS) -> str:
    _meta, body = parse_frontmatter(md_path)
    body = _strip_full_text_prefix(body)
    return re.sub(r"[^0-9a-z]+", "", body.lower())[:limit]


def _strip_suffix(stem: str) -> tuple[str, str]:
    support_match = SUPPORTING_SUFFIX_RE.fullmatch(stem)
    if support_match:
        return support_match.group("base").strip(), "supporting_suffix"

    number_match = NUMBER_SUFFIX_RE.fullmatch(stem)
    if number_match:
        return number_match.group("base").strip(), "number_suffix"

    return stem.strip(), "none"


def _group_key(rel_key: str) -> tuple[str, str]:
    rel_path = Path(rel_key)
    base, _suffix_kind = _strip_suffix(rel_path.stem)
    return to_posix_path_str(rel_path.parent), _normalize_key(base)


def _entry_sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, int, str]:
    rel_key, entry = item
    stem = Path(str(entry.get("source_filename") or rel_key)).stem
    base, suffix_kind = _strip_suffix(stem)
    return (
        0 if suffix_kind == "none" else 1,
        len(base),
        stem.lower(),
    )


def _entry_markdown(entry: dict[str, Any]) -> Path | None:
    md_path = Path(str(entry.get("output_markdown", "")))
    if md_path.exists() and md_path.is_file():
        return md_path
    return None


def _entry_pdf(entry: dict[str, Any], rel_key: str, input_root: Path) -> Path:
    source_pdf = str(entry.get("source_pdf", "")).strip()
    if source_pdf:
        return Path(source_pdf)
    return input_root / rel_key


def looks_like_supporting_by_content(markdown_path: Path) -> bool:
    """Classify a markdown file as SI only if one of its first few meaningful
    lines (skipping images, dividers, blanks, and table rows) is itself an SI
    title heading.  We intentionally avoid scanning deep into the body because
    main papers routinely mention "Supporting Information" as a callout near
    the abstract, which was producing false positives with the previous
    substring-based heuristic.
    """
    metadata, body = parse_frontmatter(markdown_path)
    if metadata.get("document_role") == "supporting":
        return True

    body = _strip_full_text_prefix(body)
    for line in _first_meaningful_lines(body):
        plain = _plain_text(line).lower()
        if not plain:
            continue
        if (
            _SI_HEADING_RE.match(plain)
            or _ELECTRONIC_SI_RE.match(plain)
            or _FILENAME_SI_RE.match(plain)
            or _SI_SHORT_LABEL_RE.match(plain)
        ):
            return True
    return False


def _next_supporting_index(bundle_dir: Path) -> int:
    index = 1
    while True:
        if not (bundle_dir / supporting_markdown_name(index)).exists() and not (
            bundle_dir / supporting_assets_dir_name(index)
        ).exists():
            return index
        index += 1


def _rewrite_asset_links(markdown_body: str, source_md: Path, source_bundle: Path, asset_dir_name: str) -> str:
    rewritten = markdown_body
    replacements: dict[str, str] = {}
    for asset_path in source_bundle.rglob("*"):
        if not asset_path.is_file() or asset_path == source_md or asset_path.suffix.lower() == ".md":
            continue
        old_relative = to_posix_path_str(Path(os.path.relpath(asset_path, start=source_md.parent)))
        new_relative = to_posix_path_str(Path(asset_dir_name) / asset_path.relative_to(source_bundle))
        replacements[old_relative] = new_relative
        replacements[f"./{old_relative}"] = new_relative

    for old, new in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        rewritten = rewritten.replace(f"]({old})", f"]({new})")
        rewritten = rewritten.replace(f'"{old}"', f'"{new}"')
        rewritten = rewritten.replace(f"'{old}'", f"'{new}'")
    return rewritten


def _move_supporting_assets(source_md: Path, source_bundle: Path, target_assets: Path) -> list[Path]:
    moved: list[Path] = []
    for asset_path in sorted(source_bundle.rglob("*")):
        if not asset_path.is_file() or asset_path == source_md or asset_path.suffix.lower() == ".md":
            continue
        destination = target_assets / asset_path.relative_to(source_bundle)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(asset_path), str(destination))
        moved.append(destination)
    return moved


def _markdown_relpath(config: dict[str, Any], path: Path) -> str:
    try:
        return to_posix_path_str(path.resolve().relative_to(markdown_root(config).resolve()))
    except ValueError:
        return to_posix_path_str(path)


def _merge_supporting_entry(
    *,
    rel_key: str,
    entry: dict[str, Any],
    canonical_rel_key: str,
    canonical_entry: dict[str, Any],
    config: dict[str, Any],
    input_root: Path,
    apply: bool,
) -> dict[str, Any]:
    source_md = _entry_markdown(entry)
    canonical_md = _entry_markdown(canonical_entry)
    if source_md is None or canonical_md is None:
        return {"moved": False, "reason": "missing_markdown"}

    source_bundle = Path(str(entry.get("markdown_bundle_dir") or source_md.parent))
    canonical_bundle = Path(str(canonical_entry.get("markdown_bundle_dir") or canonical_md.parent))
    if not canonical_bundle.exists():
        return {"moved": False, "reason": "missing_canonical_bundle"}

    supporting_index = _next_supporting_index(canonical_bundle)
    target_md = canonical_bundle / supporting_markdown_name(supporting_index)
    target_assets = canonical_bundle / supporting_assets_dir_name(supporting_index)

    if not apply:
        return {
            "moved": True,
            "source_markdown": str(source_md),
            "target_markdown": str(target_md),
        }

    target_md.parent.mkdir(parents=True, exist_ok=True)
    metadata, body = parse_frontmatter(source_md)
    rewritten_body = _rewrite_asset_links(body, source_md, source_bundle, target_assets.name)

    source_pdf = _entry_pdf(entry, rel_key, input_root)
    canonical_pdf = _entry_pdf(canonical_entry, canonical_rel_key, input_root)
    metadata.update(
        {
            "conversion_status": "success",
            "document_role": "supporting",
            "supporting_index": supporting_index,
            "primary_source_pdf": to_posix_path_str(canonical_pdf),
            "primary_source_relpath": canonical_rel_key,
            "primary_source_filename": canonical_pdf.name,
            "markdown_bundle_dir": str(canonical_bundle),
            "markdown_relpath": _markdown_relpath(config, target_md),
            "markdown_bundle_relpath": _markdown_relpath(config, canonical_bundle),
        }
    )
    target_md.write_text(build_frontmatter(metadata) + rewritten_body, encoding="utf-8")
    moved_assets = _move_supporting_assets(source_md, source_bundle, target_assets)

    safe_unlink(source_md, markdown_root(config))
    if source_bundle.exists() and source_bundle != canonical_bundle:
        safe_rmtree(source_bundle, markdown_root(config))

    raw_dir = str(entry.get("raw_output_dir", "")).strip()
    if raw_dir and Path(raw_dir).exists():
        safe_rmtree(Path(raw_dir), raw_root(config))

    return {
        "moved": True,
        "source_markdown": str(source_md),
        "target_markdown": str(target_md),
        "assets_moved": len(moved_assets),
    }


def _delete_duplicate_main(
    *,
    rel_key: str,
    entry: dict[str, Any],
    canonical_entry: dict[str, Any],
    config: dict[str, Any],
    input_root: Path,
    manifest: ManifestStore,
    logger,
    apply: bool,
) -> dict[str, Any]:
    source_md = _entry_markdown(entry)
    canonical_md = _entry_markdown(canonical_entry)
    source_pdf = _entry_pdf(entry, rel_key, input_root)

    if not apply:
        return {
            "deleted": True,
            "source_pdf": str(source_pdf),
            "source_markdown": str(source_md) if source_md else "",
        }

    deleted_markdown = False
    if source_md is not None and canonical_md is not None and source_md.resolve() == canonical_md.resolve():
        manifest.remove_entry(rel_key)
    else:
        result = delete_pdf_artifacts(rel_key, config, manifest, logger)
        deleted_markdown = bool(result.get("deleted"))

    deleted_pdf = False
    if source_pdf.exists():
        if not is_relative_to(source_pdf, input_root):
            raise ValueError(f"Refusing to delete duplicate PDF outside input_root: {source_pdf}")
        source_pdf.unlink()
        deleted_pdf = True

    return {
        "deleted": True,
        "deleted_markdown": deleted_markdown,
        "deleted_pdf": deleted_pdf,
        "source_pdf": str(source_pdf),
        "source_markdown": str(source_md) if source_md else "",
    }


def postprocess_library(
    config: dict[str, Any],
    *,
    apply: bool = False,
) -> dict[str, int]:
    logger = setup_logger(config, logger_name="paper_to_markdown.postprocess")
    input_root = Path(config["input_root"])
    manifest = ManifestStore(config)
    normalized_cache: dict[Path, str] = {}

    groups: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = {}
    for rel_key, entry in manifest.data.get("files", {}).items():
        if entry.get("status") != "success":
            continue
        if entry.get("document_role", "main") != "main":
            continue
        if _entry_markdown(entry) is None:
            continue
        groups.setdefault(_group_key(rel_key), []).append((rel_key, entry))

    summary = {
        "groups_seen": 0,
        "duplicate_main": 0,
        "supporting_moved": 0,
        "pdf_deleted": 0,
        "markdown_deleted": 0,
        "skipped": 0,
    }

    for group_entries in groups.values():
        if len(group_entries) < 2:
            continue
        summary["groups_seen"] += 1
        sorted_entries = sorted(group_entries, key=_entry_sort_key)
        canonical_pair: tuple[str, dict[str, Any]] | None = None
        for rel_key, entry in sorted_entries:
            md_path = _entry_markdown(entry)
            if md_path is not None and not looks_like_supporting_by_content(md_path):
                canonical_pair = (rel_key, entry)
                break
        if canonical_pair is None:
            summary["skipped"] += len(sorted_entries)
            continue

        canonical_rel_key, canonical_entry = canonical_pair
        canonical_md = _entry_markdown(canonical_entry)
        if canonical_md is None:
            summary["skipped"] += len(sorted_entries)
            continue

        for rel_key, entry in sorted_entries:
            if rel_key == canonical_rel_key:
                continue
            md_path = _entry_markdown(entry)
            if md_path is None:
                summary["skipped"] += 1
                continue

            if _markdowns_are_near_duplicates(canonical_md, md_path, normalized_cache):
                result = _delete_duplicate_main(
                    rel_key=rel_key,
                    entry=entry,
                    canonical_entry=canonical_entry,
                    config=config,
                    input_root=input_root,
                    manifest=manifest,
                    logger=logger,
                    apply=apply,
                )
                summary["duplicate_main"] += 1
                if result.get("deleted_pdf"):
                    summary["pdf_deleted"] += 1
                if result.get("deleted_markdown"):
                    summary["markdown_deleted"] += 1
                logger.info("Duplicate main %s: %s", "deleted" if apply else "would delete", result)
                continue

            if looks_like_supporting_by_content(md_path):
                result = _merge_supporting_entry(
                    rel_key=rel_key,
                    entry=entry,
                    canonical_rel_key=canonical_rel_key,
                    canonical_entry=canonical_entry,
                    config=config,
                    input_root=input_root,
                    apply=apply,
                )
                if result.get("moved"):
                    summary["supporting_moved"] += 1
                    logger.info("Supporting %s: %s", "moved" if apply else "would move", result)
                else:
                    summary["skipped"] += 1
                    logger.info("Supporting skipped: %s", result)
                continue

            summary["skipped"] += 1

    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify converted suffix PDFs as duplicate main papers or supporting information.",
    )
    parser.add_argument("--config", default=None, help="Path to settings.json.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply file changes. Without this flag the script only reports what it would do.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    summary = postprocess_library(config, apply=args.apply)
    print(summary)
    if not args.apply:
        print("Dry-run only. Re-run with --apply to delete duplicate main PDFs/Markdown and merge SI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
