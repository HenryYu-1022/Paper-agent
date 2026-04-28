from __future__ import annotations

import argparse
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

try:
    from .common import load_config, markdown_root, parse_frontmatter, state_root, update_frontmatter_fields
    from .jsonl_utils import write_jsonl
    from .zotero_api import ZoteroApiClient, attachment_filename
except ImportError:
    from common import load_config, markdown_root, parse_frontmatter, state_root, update_frontmatter_fields
    from jsonl_utils import write_jsonl
    from zotero_api import ZoteroApiClient, attachment_filename


DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
CITEKEY_RE = re.compile(r"(?im)^\s*(?:citekey|citation_key|citation key)\s*:\s*(\S+)\s*$")
YEAR_RE = re.compile(r"\b(18|19|20|21)\d{2}\b")


def matches_path(config: dict[str, Any]) -> Path:
    return state_root(config) / "markdown_zotero_matches.jsonl"


def unmatched_path(config: dict[str, Any]) -> Path:
    return state_root(config) / "unmatched_markdowns.jsonl"


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_doi(value: Any) -> str:
    text = _clean_text(value)
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text, flags=re.IGNORECASE)
    text = text.strip().rstrip(".,;)")
    return text.lower()


def _first_doi(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        match = DOI_RE.search(text)
        if match:
            return normalize_doi(match.group(0))
    return ""


def _first_citekey(metadata: dict[str, Any], body: str) -> str:
    for key in ("citekey", "citation_key", "citationKey", "zotero_citekey"):
        if metadata.get(key):
            return _clean_text(metadata[key])
    match = CITEKEY_RE.search(body)
    return match.group(1).strip() if match else ""


def _first_year(metadata: dict[str, Any], body: str) -> int | None:
    if metadata.get("year"):
        try:
            return int(metadata["year"])
        except (TypeError, ValueError):
            pass
    for key in ("date", "published", "converted_at"):
        match = YEAR_RE.search(str(metadata.get(key) or ""))
        if match:
            return int(match.group(0))
    match = YEAR_RE.search(body[:2000])
    return int(match.group(0)) if match else None


def _first_title(metadata: dict[str, Any], body: str, md_path: Path) -> str:
    if metadata.get("title"):
        return _clean_text(metadata["title"])
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return md_path.stem


def _attachment_candidates(metadata: dict[str, Any]) -> set[str]:
    values = [
        metadata.get("source_filename"),
        metadata.get("source_pdf"),
        metadata.get("source_relpath"),
    ]
    candidates: set[str] = set()
    for value in values:
        if not value:
            continue
        filename = attachment_filename(str(value)) or Path(str(value)).name
        if filename:
            candidates.add(filename.lower())
    return candidates


def markdown_record(md_path: Path) -> dict[str, Any]:
    metadata, body = parse_frontmatter(md_path)
    return {
        "markdown_path": str(md_path),
        "metadata": metadata,
        "body": body,
        "zotero_item_key": _clean_text(metadata.get("zotero_item_key")),
        "citekey": _first_citekey(metadata, body),
        "doi": normalize_doi(metadata.get("doi")) or _first_doi(body),
        "title": _first_title(metadata, body, md_path),
        "year": _first_year(metadata, body),
        "attachment_filenames": sorted(_attachment_candidates(metadata)),
    }


def _item_doi(item: dict[str, Any]) -> str:
    return normalize_doi(item.get("doi"))


def _item_citekey(item: dict[str, Any]) -> str:
    return _clean_text(item.get("citekey"))


def _item_attachment_filenames(item: dict[str, Any]) -> set[str]:
    names = set(str(name).lower() for name in item.get("attachment_filenames") or [] if name)
    for raw_path in item.get("attachment_paths") or []:
        filename = attachment_filename(str(raw_path))
        if filename:
            names.add(filename.lower())
    return names


def _result(record: dict[str, Any], item: dict[str, Any], method: str, confidence: float) -> dict[str, Any]:
    return {
        "markdown_path": record["markdown_path"],
        "zotero_item_key": item.get("key"),
        "citekey": record.get("citekey") or item.get("citekey") or "",
        "doi": record.get("doi") or item.get("doi") or "",
        "title": item.get("title") or record.get("title") or "",
        "year": item.get("year") or record.get("year"),
        "journal": item.get("journal") or "",
        "collections": list(item.get("collections") or []),
        "collection_keys": list(item.get("collection_keys") or []),
        "tags": list(item.get("tags") or []),
        "zotero_match_method": method,
        "zotero_match_confidence": confidence,
    }


def match_markdown_to_item(record: dict[str, Any], items: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    if record["zotero_item_key"]:
        exact = [item for item in items if item.get("key") == record["zotero_item_key"]]
        if len(exact) == 1:
            return _result(record, exact[0], "frontmatter_zotero_item_key", 1.0), ""

    citekey = record.get("citekey")
    if citekey:
        exact = [item for item in items if _item_citekey(item).lower() == str(citekey).lower()]
        if len(exact) == 1:
            return _result(record, exact[0], "citekey", 0.99), ""
        if len(exact) > 1:
            return None, "ambiguous_citekey"

    doi = record.get("doi")
    if doi:
        exact = [item for item in items if _item_doi(item) == doi]
        if len(exact) == 1:
            return _result(record, exact[0], "doi", 0.98), ""
        if len(exact) > 1:
            return None, "ambiguous_doi"

    filenames = set(record.get("attachment_filenames") or [])
    if filenames:
        exact = [item for item in items if filenames & _item_attachment_filenames(item)]
        if len(exact) == 1:
            return _result(record, exact[0], "attachment_filename", 0.95), ""
        if len(exact) > 1:
            return None, "ambiguous_attachment_filename"

    title = _clean_text(record.get("title")).lower()
    year = record.get("year")
    if title and year:
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in items:
            if item.get("year") != year:
                continue
            ratio = SequenceMatcher(None, title, _clean_text(item.get("title")).lower()).ratio()
            if ratio >= 0.92:
                scored.append((ratio, item))
        if scored:
            scored.sort(key=lambda pair: pair[0], reverse=True)
            best_score = scored[0][0]
            best = [item for score, item in scored if score == best_score]
            if len(best) == 1:
                return _result(record, best[0], "title_year_fuzzy", round(best_score, 4)), ""
            return None, "ambiguous_title_year"

    return None, "no_match"


def scan_markdowns(config: dict[str, Any]) -> list[Path]:
    root = markdown_root(config)
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.md") if path.is_file())


def apply_match_to_markdown(match: dict[str, Any]) -> None:
    collections = list(match.get("collections") or [])
    update_frontmatter_fields(
        Path(match["markdown_path"]),
        {
            "zotero_item_key": match.get("zotero_item_key") or "",
            "citekey": match.get("citekey") or "",
            "doi": match.get("doi") or "",
            "title": match.get("title") or "",
            "year": match.get("year"),
            "journal": match.get("journal") or "",
            "collections": collections,
            "zotero_collections": collections,
            "collection_keys": list(match.get("collection_keys") or []),
            "tags": list(match.get("tags") or []),
            "zotero_match_method": match.get("zotero_match_method") or "",
            "zotero_match_confidence": match.get("zotero_match_confidence"),
        },
    )


def backfill_existing_markdowns(
    config: dict[str, Any],
    *,
    zotero_client: Any | None = None,
    dry_run: bool = True,
) -> dict[str, int]:
    client = zotero_client or ZoteroApiClient.from_config(config)
    items = client.list_items()
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    updated_markdown = 0

    for md_path in scan_markdowns(config):
        record = markdown_record(md_path)
        match, reason = match_markdown_to_item(record, items)
        if match is None:
            unmatched.append(
                {
                    "markdown_path": str(md_path),
                    "title": record.get("title") or "",
                    "year": record.get("year"),
                    "doi": record.get("doi") or "",
                    "citekey": record.get("citekey") or "",
                    "reason": reason,
                }
            )
            continue
        matched.append(match)
        if not dry_run:
            apply_match_to_markdown(match)
            updated_markdown += 1

    write_jsonl(matches_path(config), matched)
    write_jsonl(unmatched_path(config), unmatched)
    return {
        "scanned": len(matched) + len(unmatched),
        "matched": len(matched),
        "unmatched": len(unmatched),
        "updated_markdown": updated_markdown,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Zotero matches for existing Markdown files.")
    parser.add_argument("--config", default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    print(backfill_existing_markdowns(config, dry_run=not args.apply))


if __name__ == "__main__":
    main()
