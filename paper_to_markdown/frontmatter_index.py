from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import (
    build_frontmatter,
    markdown_root,
    parse_frontmatter,
    to_posix_path_str,
    utc_now_iso,
)


SOURCE_ALIAS_FIELD = "source_aliases"
INTERNAL_ENTRY_KEYS = {"_frontmatter_path", "_source_is_alias"}


def _config_from_manifest_path(path: Path) -> dict[str, Any]:
    # Compatibility shim for older call sites that still pass
    # output_root/state/manifest.json.
    output_root = path.parent.parent
    return {"output_root": str(output_root), "compute_sha256": True}


def _normalize_rel_key(value: str | Path | None) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def _clean_metadata_value(value: Any) -> Any:
    if isinstance(value, Path):
        return to_posix_path_str(value)
    if isinstance(value, dict):
        return {
            str(key): _clean_metadata_value(inner)
            for key, inner in value.items()
            if inner is not None and str(key) not in INTERNAL_ENTRY_KEYS
        }
    if isinstance(value, list):
        return [_clean_metadata_value(item) for item in value]
    return value


def _fingerprint_updates(fingerprint: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if "sha256" in fingerprint:
        updates["source_pdf_sha256"] = fingerprint["sha256"]
    if "size" in fingerprint:
        updates["source_size"] = fingerprint["size"]
    if "mtime_ns" in fingerprint:
        updates["source_mtime_ns"] = fingerprint["mtime_ns"]
    return updates


def _entry_fingerprint(metadata: dict[str, Any]) -> dict[str, Any]:
    fingerprint: dict[str, Any] = {}
    sha256 = metadata.get("source_pdf_sha256") or metadata.get("source_sha256")
    if sha256:
        fingerprint["sha256"] = sha256
    if metadata.get("source_size") is not None:
        fingerprint["size"] = metadata.get("source_size")
    if metadata.get("source_mtime_ns") is not None:
        fingerprint["mtime_ns"] = metadata.get("source_mtime_ns")
    return fingerprint


def _source_records(metadata: dict[str, Any]) -> list[tuple[str, dict[str, Any], bool]]:
    records: list[tuple[str, dict[str, Any], bool]] = []
    primary_rel = _normalize_rel_key(metadata.get("source_relpath"))
    if primary_rel:
        records.append((primary_rel, metadata, False))

    aliases = metadata.get(SOURCE_ALIAS_FIELD, [])
    if isinstance(aliases, list):
        for alias in aliases:
            if not isinstance(alias, dict):
                continue
            alias_rel = _normalize_rel_key(alias.get("source_relpath"))
            if alias_rel:
                records.append((alias_rel, alias, True))
    return records


def _markdown_relpath(md_path: Path, md_root: Path) -> str:
    try:
        return to_posix_path_str(md_path.resolve().relative_to(md_root.resolve()))
    except ValueError:
        return to_posix_path_str(md_path)


class FrontmatterIndex:
    """Manifest-compatible state adapter backed by Markdown frontmatter.

    The public shape intentionally mirrors the previous ``ManifestStore`` so
    older pipeline code can continue to ask for ``data["files"]`` while the
    persisted source of truth is now each Markdown file's YAML frontmatter.
    """

    def __init__(self, config: dict[str, Any] | str | Path, scan: bool = True) -> None:
        if not isinstance(config, dict):
            config = _config_from_manifest_path(Path(config))
        self.config = config
        self.path = Path(config.get("output_root", "")) / "state" / "manifest.json"
        self.data: dict[str, Any] = {"version": 2, "files": {}}
        if scan:
            self.reload()

    def reload(self) -> None:
        files: dict[str, dict[str, Any]] = {}
        md_root = markdown_root(self.config)
        if md_root.exists():
            for md_path in sorted(md_root.rglob("*.md")):
                if not md_path.is_file():
                    continue
                metadata, _body = parse_frontmatter(md_path)
                if not metadata:
                    continue
                for rel_key, source_metadata, is_alias in _source_records(metadata):
                    files[rel_key] = self._build_entry(
                        rel_key=rel_key,
                        md_path=md_path,
                        metadata=metadata,
                        source_metadata=source_metadata,
                        is_alias=is_alias,
                    )
        self.data = {"version": 2, "files": files}

    def _build_entry(
        self,
        rel_key: str,
        md_path: Path,
        metadata: dict[str, Any],
        source_metadata: dict[str, Any],
        is_alias: bool,
    ) -> dict[str, Any]:
        md_root = markdown_root(self.config)
        fingerprint = _entry_fingerprint(source_metadata)
        source_pdf = source_metadata.get("source_pdf") or metadata.get("source_pdf", "")
        source_filename = (
            source_metadata.get("source_filename")
            or metadata.get("source_filename")
            or Path(rel_key).name
        )
        entry: dict[str, Any] = {
            "status": metadata.get("conversion_status", metadata.get("status", "success")),
            "source_pdf": source_pdf,
            "source_relpath": rel_key,
            "source_filename": source_filename,
            "output_markdown": str(md_path),
            "markdown_bundle_dir": str(md_path.parent),
            "markdown_relpath": _markdown_relpath(md_path, md_root),
            "markdown_bundle_relpath": _markdown_relpath(md_path.parent, md_root),
            "mirror_paths": list(metadata.get("mirror_paths") or []),
            "document_role": metadata.get("document_role", "main"),
            "_frontmatter_path": str(md_path),
            "_source_is_alias": is_alias,
        }
        entry.update(fingerprint)
        for key in [
            "converted_at",
            "raw_output_dir",
            "primary_source_pdf",
            "primary_source_relpath",
            "primary_source_filename",
            "canonical_source_pdf",
            "canonical_source_relpath",
            "canonical_source_filename",
            "supporting_index",
            "zotero_item_key",
            "zotero_attachment_key",
            "annotations_count",
            "zotero_collections",
        ]:
            if metadata.get(key) is not None:
                entry[key] = metadata[key]
        return entry

    def get(self, rel_key: str) -> dict[str, Any] | None:
        return self.data.setdefault("files", {}).get(_normalize_rel_key(rel_key))

    def is_unchanged(self, rel_key: str, fingerprint: dict[str, Any]) -> bool:
        existing = self.get(rel_key)
        if not existing or existing.get("status") != "success":
            return False

        if fingerprint.get("sha256"):
            return existing.get("sha256") == fingerprint.get("sha256")

        comparable_keys = [key for key in ("size", "mtime_ns") if key in fingerprint]
        return bool(comparable_keys) and all(existing.get(key) == fingerprint[key] for key in comparable_keys)

    def mark_success(
        self,
        rel_key: str,
        fingerprint: dict[str, Any],
        source_pdf: Path,
        output_markdown: Path,
        raw_dir: Path,
        metadata: dict[str, Any],
    ) -> None:
        rel_key = _normalize_rel_key(rel_key)
        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        frontmatter, body = parse_frontmatter(output_markdown)
        frontmatter = dict(frontmatter)

        source_updates = {
            "source_pdf": to_posix_path_str(source_pdf),
            "source_relpath": rel_key,
            "source_filename": source_pdf.name,
            **_fingerprint_updates(fingerprint),
        }
        runtime_updates = {
            "conversion_status": "success",
            "raw_output_dir": to_posix_path_str(raw_dir),
            "updated_at": utc_now_iso(),
            "markdown_relpath": _markdown_relpath(output_markdown, markdown_root(self.config)),
            "markdown_bundle_relpath": _markdown_relpath(output_markdown.parent, markdown_root(self.config)),
            **metadata,
        }
        runtime_updates = {
            key: _clean_metadata_value(value)
            for key, value in runtime_updates.items()
            if value is not None and key not in INTERNAL_ENTRY_KEYS
        }

        primary_rel = _normalize_rel_key(frontmatter.get("source_relpath"))
        if primary_rel and primary_rel != rel_key:
            aliases = self._upsert_alias(
                frontmatter.get(SOURCE_ALIAS_FIELD, []),
                {**source_updates, **runtime_updates},
            )
            frontmatter[SOURCE_ALIAS_FIELD] = aliases
            frontmatter.update({key: value for key, value in runtime_updates.items() if key != SOURCE_ALIAS_FIELD})
        else:
            frontmatter.update(source_updates)
            frontmatter.update(runtime_updates)

        output_markdown.write_text(build_frontmatter(frontmatter) + body, encoding="utf-8")
        self.reload()

    def mark_failure(self, rel_key: str, source_pdf: Path, error: str) -> None:
        rel_key = _normalize_rel_key(rel_key)
        self.data.setdefault("files", {})[rel_key] = {
            "status": "failed",
            "source_pdf": to_posix_path_str(source_pdf),
            "source_relpath": rel_key,
            "source_filename": source_pdf.name,
            "error": error,
            "failed_at": utc_now_iso(),
        }

    def remove_entry(self, rel_key: str) -> bool:
        rel_key = _normalize_rel_key(rel_key)
        entry = self.get(rel_key)
        if not entry:
            return False

        md_path = Path(entry.get("output_markdown", ""))
        if md_path.exists():
            frontmatter, body = parse_frontmatter(md_path)
            if _normalize_rel_key(frontmatter.get("source_relpath")) != rel_key:
                aliases = [
                    alias
                    for alias in frontmatter.get(SOURCE_ALIAS_FIELD, [])
                    if isinstance(alias, dict)
                    and _normalize_rel_key(alias.get("source_relpath")) != rel_key
                ]
                frontmatter[SOURCE_ALIAS_FIELD] = aliases
                md_path.write_text(build_frontmatter(frontmatter) + body, encoding="utf-8")

        removed = self.data.get("files", {}).pop(rel_key, None) is not None
        return removed

    def save(self) -> None:
        grouped: dict[Path, list[tuple[str, dict[str, Any]]]] = {}
        for rel_key, entry in self.data.get("files", {}).items():
            md_path = Path(entry.get("output_markdown") or entry.get("_frontmatter_path") or "")
            if not md_path:
                continue
            grouped.setdefault(md_path, []).append((rel_key, entry))

        for md_path, entries in grouped.items():
            if not md_path.exists():
                continue
            frontmatter, body = parse_frontmatter(md_path)
            frontmatter = dict(frontmatter)
            primary_rel = _normalize_rel_key(frontmatter.get("source_relpath"))
            aliases = list(frontmatter.get(SOURCE_ALIAS_FIELD) or [])

            for rel_key, entry in entries:
                entry_updates = self._entry_to_metadata(entry)
                if primary_rel == rel_key or not primary_rel:
                    frontmatter.update(entry_updates)
                    primary_rel = rel_key
                else:
                    aliases = self._upsert_alias(aliases, entry_updates)

            if aliases:
                frontmatter[SOURCE_ALIAS_FIELD] = aliases
            elif SOURCE_ALIAS_FIELD in frontmatter:
                frontmatter.pop(SOURCE_ALIAS_FIELD)
            md_path.write_text(build_frontmatter(frontmatter) + body, encoding="utf-8")

        self.reload()

    def _entry_to_metadata(self, entry: dict[str, Any]) -> dict[str, Any]:
        updates: dict[str, Any] = {
            "conversion_status": entry.get("status", "success"),
            "source_pdf": entry.get("source_pdf", ""),
            "source_relpath": entry.get("source_relpath", ""),
            "source_filename": entry.get("source_filename", ""),
            "markdown_relpath": entry.get("markdown_relpath"),
            "markdown_bundle_relpath": entry.get("markdown_bundle_relpath"),
            "mirror_paths": entry.get("mirror_paths", []),
            "document_role": entry.get("document_role", "main"),
        }
        if entry.get("sha256"):
            updates["source_pdf_sha256"] = entry["sha256"]
        if entry.get("size") is not None:
            updates["source_size"] = entry["size"]
        if entry.get("mtime_ns") is not None:
            updates["source_mtime_ns"] = entry["mtime_ns"]
        for key, value in entry.items():
            if key in updates or key in INTERNAL_ENTRY_KEYS:
                continue
            if key in {"output_markdown", "markdown_bundle_dir", "sha256", "size", "mtime_ns"}:
                continue
            if value is not None:
                updates[key] = value
        return {
            key: _clean_metadata_value(value)
            for key, value in updates.items()
            if value is not None and key not in INTERNAL_ENTRY_KEYS
        }

    def _upsert_alias(self, aliases: Any, alias_update: dict[str, Any]) -> list[dict[str, Any]]:
        clean_aliases: list[dict[str, Any]] = []
        if isinstance(aliases, list):
            clean_aliases = [dict(alias) for alias in aliases if isinstance(alias, dict)]

        rel_key = _normalize_rel_key(alias_update.get("source_relpath"))
        alias_update = {
            key: _clean_metadata_value(value)
            for key, value in alias_update.items()
            if value is not None and key not in INTERNAL_ENTRY_KEYS
        }
        for index, alias in enumerate(clean_aliases):
            if _normalize_rel_key(alias.get("source_relpath")) == rel_key:
                clean_aliases[index] = {**alias, **alias_update}
                return clean_aliases
        clean_aliases.append(alias_update)
        return clean_aliases

    def to_json(self) -> str:
        return json.dumps(self.data, ensure_ascii=False, indent=2)

