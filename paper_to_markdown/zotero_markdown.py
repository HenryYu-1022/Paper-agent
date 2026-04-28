"""CLI entry point for building Zotero collection markdown views."""

from __future__ import annotations

import sys

try:
    from .materialize_collection_views import main, materialize_views, zotero_markdown_root
except ImportError:
    from materialize_collection_views import main, materialize_views, zotero_markdown_root


__all__ = ["main", "materialize_views", "zotero_markdown_root"]


if __name__ == "__main__":
    sys.exit(main())
