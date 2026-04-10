"""Read-only access to a Zotero SQLite database for collection hierarchy lookup.

This module opens ``zotero.sqlite`` with ``immutable=1`` so that it never
conflicts with a running Zotero instance.  All public helpers return empty
results (and log a warning) when the database is unavailable or locked.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger("paper_to_markdown")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open the Zotero database in immutable read-only mode.

    ``immutable=1`` tells SQLite to treat the file as a static snapshot so
    we never interfere with Zotero's own WAL writes.
    """
    uri = f"file:{db_path}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True, timeout=5)


def _build_collection_tree(conn: sqlite3.Connection) -> dict[int, str]:
    """Return ``{collectionID: "Parent/Child/GrandChild"}`` for every collection."""

    cursor = conn.execute(
        "SELECT collectionID, collectionName, parentCollectionID FROM collections"
    )
    rows = cursor.fetchall()

    # id -> (name, parentID)
    info: dict[int, tuple[str, int | None]] = {}
    for cid, cname, pid in rows:
        info[cid] = (cname, pid)

    cache: dict[int, str] = {}

    def _resolve(cid: int) -> str:
        if cid in cache:
            return cache[cid]
        name, pid = info[cid]
        if pid is None or pid not in info:
            cache[cid] = name
        else:
            cache[cid] = f"{_resolve(pid)}/{name}"
        return cache[cid]

    for cid in info:
        _resolve(cid)

    return cache


def _build_pdf_collection_map(
    conn: sqlite3.Connection,
    collection_tree: dict[int, str],
) -> dict[str, list[str]]:
    """Return ``{pdf_filename: [collection_path, …]}`` for all PDF attachments.

    The filename is extracted from the ``itemAttachments.path`` column after
    stripping the ``storage:`` prefix.  Only rows with a resolvable parent
    item that belongs to at least one collection are included.
    """

    # Zotero stores attachment paths as "storage:filename.pdf"
    cursor = conn.execute(
        """
        SELECT ia.path, ci.collectionID
        FROM itemAttachments ia
        JOIN collectionItems ci ON ci.itemID = ia.parentItemID
        WHERE ia.path IS NOT NULL
          AND ia.path <> ''
        """
    )

    mapping: dict[str, list[str]] = {}
    for raw_path, cid in cursor:
        # Strip the "storage:" prefix if present
        filename = raw_path
        if filename.startswith("storage:"):
            filename = filename[len("storage:"):]

        # Only keep PDF attachments
        if not filename.lower().endswith(".pdf"):
            continue

        col_path = collection_tree.get(cid)
        if col_path is None:
            continue

        mapping.setdefault(filename, [])
        if col_path not in mapping[filename]:
            mapping[filename].append(col_path)

    # Sort each collection list for deterministic output
    for filename in mapping:
        mapping[filename].sort()

    return mapping


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class ZoteroCollectionMap:
    """Lazy, cacheable reader of the Zotero collection hierarchy.

    Instances are lightweight.  Call :meth:`load` (or any lookup method) to
    actually hit the database.  Results are cached; call :meth:`reload` to
    refresh from disk.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self._collection_tree: dict[int, str] | None = None
        self._pdf_map: dict[str, list[str]] | None = None

    # -- loading / caching ---------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._pdf_map is not None:
            return
        self.reload()

    def reload(self) -> None:
        """(Re-)read the Zotero database and rebuild internal caches."""
        if not self.db_path.exists():
            logger.warning("Zotero database not found: %s", self.db_path)
            self._collection_tree = {}
            self._pdf_map = {}
            return

        try:
            conn = _connect_readonly(self.db_path)
        except sqlite3.Error as exc:
            logger.warning("Cannot open Zotero database: %s", exc)
            self._collection_tree = {}
            self._pdf_map = {}
            return

        try:
            self._collection_tree = _build_collection_tree(conn)
            self._pdf_map = _build_pdf_collection_map(conn, self._collection_tree)
            logger.info(
                "Zotero DB loaded: %d collections, %d PDF mappings",
                len(self._collection_tree),
                len(self._pdf_map),
            )
        except sqlite3.Error as exc:
            logger.warning("Error reading Zotero database: %s", exc)
            self._collection_tree = {}
            self._pdf_map = {}
        finally:
            conn.close()

    # -- lookup --------------------------------------------------------------

    def get_collections_for_pdf(self, filename: str) -> list[str]:
        """Return all collection paths for a given PDF filename.

        Returns an empty list if the filename is not found or the database
        is unavailable.
        """
        self._ensure_loaded()
        assert self._pdf_map is not None
        return list(self._pdf_map.get(filename, []))

    def get_all_pdf_collections(self) -> dict[str, list[str]]:
        """Return the full ``{filename: [collection_paths]}`` mapping."""
        self._ensure_loaded()
        assert self._pdf_map is not None
        return dict(self._pdf_map)

    @property
    def collection_tree(self) -> dict[int, str]:
        """Return ``{collectionID: full_path}``."""
        self._ensure_loaded()
        assert self._collection_tree is not None
        return dict(self._collection_tree)

    @property
    def is_available(self) -> bool:
        """Return *True* if the database was loaded successfully."""
        self._ensure_loaded()
        return bool(self._collection_tree is not None and self._pdf_map is not None)
