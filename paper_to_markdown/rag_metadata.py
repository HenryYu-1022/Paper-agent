from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

try:
    from .common import load_config, parse_frontmatter
    from .jsonl_utils import read_jsonl, write_jsonl
except ImportError:
    from common import load_config, parse_frontmatter
    from jsonl_utils import read_jsonl, write_jsonl


RAG_METADATA_FIELDS = [
    "zotero_item_key",
    "citekey",
    "doi",
    "title",
    "year",
    "journal",
    "collections",
    "collection_keys",
    "tags",
]


def _markdown_path_from_chunk(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return str(row.get("source_markdown_path") or metadata.get("source_markdown_path") or "")


def sync_rag_metadata(config: dict[str, Any]) -> dict[str, int]:
    updated_chunks = 0
    files_seen = 0
    frontmatter_cache: dict[str, dict[str, Any]] = {}
    for raw_path in config.get("rag_chunks_jsonl_paths") or []:
        path = Path(raw_path)
        rows = read_jsonl(path)
        files_seen += 1
        changed = False
        for row in rows:
            md_path_text = _markdown_path_from_chunk(row)
            if not md_path_text:
                continue
            if md_path_text not in frontmatter_cache:
                metadata, _body = parse_frontmatter(Path(md_path_text))
                frontmatter_cache[md_path_text] = metadata
            source_metadata = frontmatter_cache[md_path_text]
            row_metadata = row.setdefault("metadata", {})
            before = dict(row_metadata)
            for field in RAG_METADATA_FIELDS:
                if source_metadata.get(field) is not None:
                    row_metadata[field] = source_metadata[field]
            row_metadata["source_markdown_path"] = md_path_text
            if "section_heading" in row:
                row_metadata.setdefault("section_heading", row["section_heading"])
            if row_metadata != before:
                updated_chunks += 1
                changed = True
        if changed:
            write_jsonl(path, rows)
    return {"files": files_seen, "updated_chunks": updated_chunks}


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Zotero metadata into JSONL RAG chunks.")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    config = load_config(args.config)
    print(sync_rag_metadata(config))


if __name__ == "__main__":
    main()
