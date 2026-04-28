from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ZOTERO_API_VERSION = "3"
DEFAULT_BASE_URL = "https://api.zotero.org"


class ZoteroApiError(RuntimeError):
    pass


def attachment_filename(raw_path: str | None) -> str | None:
    if not raw_path:
        return None
    filename = str(raw_path)
    for prefix in ("storage:", "attachments:"):
        if filename.startswith(prefix):
            filename = filename[len(prefix):]
            break
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not filename.lower().endswith(".pdf"):
        return None
    return filename


def extract_citekey(data: dict[str, Any]) -> str:
    for key in ("citekey", "citationKey", "citation_key"):
        if data.get(key):
            return str(data[key]).strip()
    extra = str(data.get("extra") or "")
    match = re.search(r"(?im)^\s*(?:citation\s+key|citekey)\s*:\s*(\S+)\s*$", extra)
    return match.group(1).strip() if match else ""


def extract_year(value: Any) -> int | None:
    match = re.search(r"\b(18|19|20|21)\d{2}\b", str(value or ""))
    return int(match.group(0)) if match else None


def normalize_api_item(raw: dict[str, Any]) -> dict[str, Any]:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    key = raw.get("key") or data.get("key")
    collections = data.get("collections") or []
    tags = data.get("tags") or []
    normalized_tags: list[str] = []
    for tag in tags:
        if isinstance(tag, dict) and tag.get("tag"):
            normalized_tags.append(str(tag["tag"]))
        elif isinstance(tag, str):
            normalized_tags.append(tag)

    path = data.get("path") or data.get("filename") or ""
    filename = attachment_filename(path)
    return {
        "key": key,
        "version": int(raw.get("version") or data.get("version") or 0),
        "title": str(data.get("title") or "").strip(),
        "year": extract_year(data.get("date") or data.get("year")),
        "journal": str(
            data.get("publicationTitle")
            or data.get("journalAbbreviation")
            or data.get("journal")
            or ""
        ).strip(),
        "doi": str(data.get("DOI") or data.get("doi") or "").strip(),
        "citekey": extract_citekey(data),
        "attachment_paths": [path] if path else [],
        "attachment_filenames": [filename] if filename else [],
        "collections": [],
        "collection_keys": [str(item) for item in collections],
        "tags": normalized_tags,
        "raw": raw,
    }


class ZoteroApiClient:
    def __init__(
        self,
        *,
        library_type: str,
        library_id: str,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        if library_type not in {"user", "group"}:
            raise ValueError("zotero_library_type must be 'user' or 'group'")
        self.library_type = library_type
        self.library_id = str(library_id)
        self.api_key = api_key or os.environ.get("ZOTERO_API_KEY", "")
        if not self.api_key:
            raise ValueError("ZOTERO_API_KEY is required for Zotero Web API access")
        self.base_url = base_url.rstrip("/")
        self._collections_by_key: dict[str, dict[str, Any]] | None = None
        self._ssl_context = self._build_ssl_context()

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "ZoteroApiClient":
        return cls(
            library_type=str(config.get("zotero_library_type", "user")),
            library_id=str(config.get("zotero_library_id", "")),
            api_key=str(config.get("zotero_api_key") or "") or None,
            base_url=str(config.get("zotero_api_base_url", DEFAULT_BASE_URL)),
        )

    @property
    def library_path(self) -> str:
        prefix = "users" if self.library_type == "user" else "groups"
        return f"/{prefix}/{self.library_id}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Any | None = None,
        version: int | None = None,
    ) -> Any:
        body = None
        headers = {
            "Zotero-API-Version": ZOTERO_API_VERSION,
            "Zotero-API-Key": self.api_key,
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")
        if version is not None:
            headers["If-Unmodified-Since-Version"] = str(version)

        request = urllib.request.Request(
            self.base_url + self.library_path + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, context=self._ssl_context) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ZoteroApiError(f"Zotero API {method} {path} failed: {exc.code} {detail}") from exc
        if not raw:
            return None
        return json.loads(raw)

    def _get_paginated(self, path: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        start = 0
        limit = 100
        separator = "&" if "?" in path else "?"
        while True:
            page = self._request("GET", f"{path}{separator}limit={limit}&start={start}")
            if not page:
                break
            rows.extend(page)
            if len(page) < limit:
                break
            start += limit
        return rows

    def list_items(self) -> list[dict[str, Any]]:
        raw_items = self._get_paginated("/items?include=data")
        parents: dict[str, dict[str, Any]] = {}
        attachments: dict[str, list[dict[str, str]]] = {}

        for raw in raw_items:
            data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
            if data.get("itemType") == "attachment":
                parent = data.get("parentItem")
                path = data.get("path") or ""
                filename = attachment_filename(path)
                if parent and (path or filename):
                    attachments.setdefault(str(parent), []).append(
                        {"path": str(path), "filename": filename or ""}
                    )
                continue
            normalized = normalize_api_item(raw)
            if normalized.get("key"):
                parents[str(normalized["key"])] = normalized

        collection_paths = self.collection_paths_by_key()
        for key, item in parents.items():
            for attachment in attachments.get(key, []):
                if attachment["path"]:
                    item.setdefault("attachment_paths", []).append(attachment["path"])
                if attachment["filename"]:
                    item.setdefault("attachment_filenames", []).append(attachment["filename"])
            item["collections"] = [
                collection_paths.get(collection_key, collection_key)
                for collection_key in item.get("collection_keys", [])
            ]
        return list(parents.values())

    def get_item(self, item_key: str) -> dict[str, Any]:
        item = normalize_api_item(self._request("GET", f"/items/{item_key}?include=data"))
        collection_paths = self.collection_paths_by_key()
        item["collections"] = [
            collection_paths.get(collection_key, collection_key)
            for collection_key in item.get("collection_keys", [])
        ]
        return item

    def collection_paths_by_key(self) -> dict[str, str]:
        collections = self._load_collections()
        cache: dict[str, str] = {}

        def resolve(key: str) -> str:
            if key in cache:
                return cache[key]
            data = collections[key]
            parent = data.get("parentCollection")
            name = str(data.get("name") or "")
            if parent and parent in collections:
                cache[key] = f"{resolve(parent)}/{name}"
            else:
                cache[key] = name
            return cache[key]

        for key in collections:
            resolve(key)
        return cache

    def _load_collections(self) -> dict[str, dict[str, Any]]:
        if self._collections_by_key is not None:
            return self._collections_by_key
        rows = self._get_paginated("/collections?include=data")
        collections: dict[str, dict[str, Any]] = {}
        for row in rows:
            data = row.get("data") if isinstance(row.get("data"), dict) else row
            key = row.get("key") or data.get("key")
            if key:
                collections[str(key)] = {
                    "key": str(key),
                    "name": data.get("name") or data.get("collectionName") or "",
                    "parentCollection": data.get("parentCollection") or False,
                }
        self._collections_by_key = collections
        return collections

    def ensure_collection_path(self, path: str) -> str:
        parts = [part.strip() for part in path.split("/") if part.strip()]
        if not parts:
            raise ValueError("collection path cannot be empty")

        parent_key: str | bool = False
        current_path = ""
        for part in parts:
            current_path = f"{current_path}/{part}" if current_path else part
            by_path = {value: key for key, value in self.collection_paths_by_key().items()}
            existing_key = by_path.get(current_path)
            if existing_key:
                parent_key = existing_key
                continue

            payload = [{"name": part, "parentCollection": parent_key}]
            response = self._request("POST", "/collections", payload=payload)
            created_key = self._extract_created_collection_key(response)
            collections = self._load_collections()
            collections[created_key] = {
                "key": created_key,
                "name": part,
                "parentCollection": parent_key,
            }
            parent_key = created_key
        return str(parent_key)

    def _extract_created_collection_key(self, response: Any) -> str:
        if isinstance(response, dict):
            successful = response.get("successful") or response.get("success")
            if isinstance(successful, dict):
                for value in successful.values():
                    if isinstance(value, dict) and value.get("key"):
                        return str(value["key"])
            if response.get("key"):
                return str(response["key"])
        raise ZoteroApiError(f"Could not determine created collection key from response: {response!r}")

    def patch_item(self, item_key: str, payload: dict[str, Any], version: int) -> None:
        self._request("PATCH", f"/items/{item_key}", payload=payload, version=version)

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        try:
            import certifi
        except ImportError:
            return None
        return ssl.create_default_context(cafile=certifi.where())
