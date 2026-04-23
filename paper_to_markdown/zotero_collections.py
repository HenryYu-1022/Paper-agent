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


def _extract_attachment_filename(raw_path: str) -> str | None:
    """Normalise a value from ``itemAttachments.path`` into a bare filename.

    Returns *None* if the path does not look like a PDF attachment.
    Supported input shapes:

    * ``storage:filename.pdf``       (managed storage)
    * ``attachments:filename.pdf``   (linked attachment, relative)
    * ``D:\\path\\to\\filename.pdf``   (linked attachment, absolute Windows)
    * ``/path/to/filename.pdf``      (linked attachment, absolute Unix)
    """
    if not raw_path:
        return None

    filename = raw_path
    for prefix in ("storage:", "attachments:"):
        if filename.startswith(prefix):
            filename = filename[len(prefix):]
            break

    if "\\" in filename or "/" in filename:
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]

    if not filename.lower().endswith(".pdf"):
        return None
    return filename


def _build_pdf_collection_map(
    conn: sqlite3.Connection,
    collection_tree: dict[int, str],
) -> dict[str, list[str]]:
    """Return ``{pdf_filename: [collection_path, …]}`` for all PDF attachments.

    The filename is extracted from the ``itemAttachments.path`` column after
    stripping the ``storage:`` prefix.  Only rows with a resolvable parent
    item that belongs to at least one collection are included.
    """

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
        filename = _extract_attachment_filename(raw_path)
        if filename is None:
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


def _build_pdf_metadata_map(
    conn: sqlite3.Connection,
) -> dict[str, dict[str, Any]]:
    """Return ``{pdf_filename: {item_key, attachment_key, annotation_count}}``.

    Excludes attachments whose own item or parent item lives in the trash
    (``deletedItems``).  When the same filename maps to several attachments
    the first one wins; later duplicates are ignored.
    """

    annotation_counts: dict[int, int] = {}
    annot_cursor = conn.execute(
        """
        SELECT ann.parentItemID, COUNT(*)
        FROM itemAnnotations ann
        WHERE ann.itemID NOT IN (SELECT itemID FROM deletedItems)
        GROUP BY ann.parentItemID
        """
    )
    for parent_id, count in annot_cursor:
        if parent_id is None:
            continue
        annotation_counts[parent_id] = count

    cursor = conn.execute(
        """
        SELECT
            ia.path,
            ia.itemID,
            ia.parentItemID,
            att_item.key,
            parent_item.key
        FROM itemAttachments ia
        JOIN items att_item ON att_item.itemID = ia.itemID
        LEFT JOIN items parent_item ON parent_item.itemID = ia.parentItemID
        WHERE ia.path IS NOT NULL
          AND ia.path <> ''
          AND ia.itemID NOT IN (SELECT itemID FROM deletedItems)
          AND (
            ia.parentItemID IS NULL
            OR ia.parentItemID NOT IN (SELECT itemID FROM deletedItems)
          )
        """
    )

    mapping: dict[str, dict[str, Any]] = {}
    for raw_path, attachment_id, _parent_id, attachment_key, parent_key in cursor:
        filename = _extract_attachment_filename(raw_path)
        if filename is None:
            continue
        if filename in mapping:
            continue
        mapping[filename] = {
            "item_key": parent_key,
            "attachment_key": attachment_key,
            "annotation_count": int(annotation_counts.get(attachment_id, 0)),
        }
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
        self._pdf_metadata: dict[str, dict[str, Any]] | None = None

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
            self._pdf_metadata = {}
            return

        try:
            conn = _connect_readonly(self.db_path)
        except sqlite3.Error as exc:
            logger.warning("Cannot open Zotero database: %s", exc)
            self._collection_tree = {}
            self._pdf_map = {}
            self._pdf_metadata = {}
            return

        try:
            self._collection_tree = _build_collection_tree(conn)
            self._pdf_map = _build_pdf_collection_map(conn, self._collection_tree)
            self._pdf_metadata = _build_pdf_metadata_map(conn)
            logger.info(
                "Zotero DB loaded: %d collections, %d PDF mappings, %d PDF metadata records",
                len(self._collection_tree),
                len(self._pdf_map),
                len(self._pdf_metadata),
            )
        except sqlite3.Error as exc:
            logger.warning("Error reading Zotero database: %s", exc)
            self._collection_tree = {}
            self._pdf_map = {}
            self._pdf_metadata = {}
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

    def get_metadata_for_pdf(self, filename: str) -> dict[str, Any]:
        """Return ``{item_key, attachment_key, annotation_count}`` for a PDF.

        Returns an empty dict if the filename is not found in the Zotero
        database (e.g. the PDF lives only on disk, not in any Zotero item).
        """
        self._ensure_loaded()
        assert self._pdf_metadata is not None
        return dict(self._pdf_metadata.get(filename, {}))

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
