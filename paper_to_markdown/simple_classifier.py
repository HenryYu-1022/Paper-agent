from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:
    from .common import parse_frontmatter
except ImportError:
    from common import parse_frontmatter


def classify(markdown_path: Path) -> dict[str, Any]:
    metadata, _body = parse_frontmatter(markdown_path)
    collections = [
        str(item)
        for item in metadata.get("collections") or metadata.get("zotero_collections") or []
        if str(item).strip()
    ]
    tags = [
        str(item)
        for item in metadata.get("tags") or []
        if str(item).strip()
    ]
    if not collections:
        collections = ["AI Classified/Needs Review"]
    return {
        "recommended_collections": collections,
        "recommended_tags": tags,
        "confidence": 0.8 if collections else 0.0,
    }


def main() -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    markdown_path = Path(payload["markdown_path"])
    print(json.dumps(classify(markdown_path), ensure_ascii=False))


if __name__ == "__main__":
    main()
