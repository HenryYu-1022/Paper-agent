"""One-shot cleanup for broken macOS collection-mirror symlink artifacts.

When collection-mirror symlinks created on macOS sync through Google Drive onto
Windows, they appear as tiny UTF-8 text files whose content is the macOS target
path (e.g. ``/Users/<user>/Library/CloudStorage/...``).  These artifacts sit at
paths the conversion pipeline later tries to use as bundle directories, causing
NotADirectoryError failures.

This tool scans ``markdown_root`` and removes files matching the known artifact
signature only.  It refuses to delete anything else.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from .common import is_relative_to, load_config, markdown_root
except ImportError:
    from common import is_relative_to, load_config, markdown_root


ARTIFACT_SIZE_LIMIT = 4096
ARTIFACT_PREFIXES = ("/Users/", "/Volumes/", "/home/")


def looks_like_mirror_artifact(path: Path) -> str | None:
    """Return the artifact's inner text if *path* matches the broken-mirror signature.

    Returns None otherwise. A match requires: not a directory, not a .md file,
    size under ARTIFACT_SIZE_LIMIT, decodable as strict UTF-8, content is a
    single line (no newlines) starting with one of ARTIFACT_PREFIXES.
    """
    if path.is_dir():
        return None
    if path.suffix.lower() == ".md":
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > ARTIFACT_SIZE_LIMIT:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return None
    stripped = text.strip()
    if not stripped or "\n" in stripped or "\r" in stripped:
        return None
    if not stripped.startswith(ARTIFACT_PREFIXES):
        return None
    return stripped


def scan(md_root: Path) -> list[tuple[Path, str]]:
    matches: list[tuple[Path, str]] = []
    if not md_root.exists():
        return matches
    for candidate in md_root.rglob("*"):
        if not candidate.is_file():
            continue
        inner = looks_like_mirror_artifact(candidate)
        if inner is not None:
            matches.append((candidate, inner))
    return matches


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete broken macOS-symlink text-file artifacts from the markdown library.",
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to settings.json (default: paper_to_markdown/settings.json).",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually delete matching files. Without this flag, only a preview is printed.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    config = load_config(args.config)
    md_root = markdown_root(config)

    matches = scan(md_root)
    if not matches:
        print(f"Scanned {md_root}: no broken-mirror artifacts found.")
        return 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] Scanned {md_root}: {len(matches)} artifact(s) identified.")
    removed = 0
    for path, inner in matches:
        preview = inner if len(inner) <= 160 else inner[:157] + "..."
        print(f"  - {path}  ->  {preview}")
        if not args.apply:
            continue
        if not is_relative_to(path, md_root):
            print(f"    SKIP: outside markdown root")
            continue
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            print(f"    FAILED: {exc}")
    if args.apply:
        print(f"Removed {removed}/{len(matches)} artifact(s).")
    else:
        print("Re-run with --apply to delete these files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
