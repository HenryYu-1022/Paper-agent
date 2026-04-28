# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup
cp paper_to_markdown/settings.example.json paper_to_markdown/settings.json
pip install -r requirements.txt  # also requires: pip install marker-pdf, PyTorch

# Batch convert all PDFs
cd paper_to_markdown && python3 convert.py
python3 convert.py --path "/path/to/Paper.pdf"   # single PDF
python3 convert.py --force                         # force reconvert all
python3 convert.py --limit 5                       # test with first N files
python3 convert.py --cleanup                       # remove orphaned Markdown
python3 -m paper_to_markdown.postprocess_markdown  # dry-run duplicate/SI cleanup
python3 -m paper_to_markdown.postprocess_markdown --apply

# Zotero Markdown views (controller/all-in-one host only)
python3 -m paper_to_markdown.zotero_markdown --mode symlink --clean

# Run daemon manually
python3 -m paper_to_markdown.daemon --config paper_to_markdown/settings.json

# Monitor and convert PDFs needing work
python3 monitor.py
python3 monitor.py --background --watch --interval 60 --convert --apply
python3 monitor.py --no-convert  # report only

# Tests
python -m pytest tests/
python -m pytest tests/test_daemon.py::DaemonTests::test_ping_returns_json_serializable_response
```

## Architecture

The pipeline has three layers:

**Conversion engine** (`pipeline.py`) — the core. Calls the external `marker_single` CLI to convert a PDF to raw Markdown, then post-processes it into a bundle directory. Handles duplicate detection (sha256 and numbered duplicate aliases before conversion, near-duplicate Markdown via `SequenceMatcher` after conversion) and supporting-document grouping (PDFs with filenames like `Paper_si.pdf` are placed inside the primary paper's bundle). Runner mode does not read Zotero SQLite and does not create collection mirrors.

**State index** (`frontmatter_index.py`) — conversion state is stored exclusively in YAML frontmatter of the output `.md` files, not in a separate manifest. `FrontmatterIndex` (aliased as `ManifestStore` in `pipeline.py`) walks the output tree on startup to rebuild state. This design keeps state in sync across Google Drive–synced devices without a central `manifest.json`.

**Daemon** (`daemon.py`) — a JSON-line stdin/stdout protocol server. The Zotero plugin (`zotero-paper-agent/`) spawns this process and sends commands like `convert`, `archive_orphan`, `delete_orphan`, `rescan`, `ping`, `shutdown`. The daemon dispatches to the pipeline and replies with JSON.

**Supporting modules:**
- `common.py` — path helpers, config loading, frontmatter read/write, logger setup
- `zotero_collections.py` — controller-side read-only access to `zotero.sqlite` for PDF→collection mapping
- `zotero_markdown.py` — builds the controller-side Zotero Markdown view under `zotero_markdown/`
- `postprocess_markdown.py` — after Marker finishes, classifies suffix variants as duplicate main papers or SI and merges/deletes without touching watcher flow

## Output layout

```
output_root/
  markdown/         ← primary library; each paper gets a bundle dir Paper/Paper.md
  zotero_markdown/  ← controller-built Zotero collection view symlinks/copies
  raw/              ← intermediate Marker output (temporary, safe to delete)
  logs/
  archive/          ← orphaned bundles moved here by daemon
```

Conversion state for each PDF lives in the `---` YAML frontmatter block of its `.md` file. Key fields: `conversion_status`, `source_pdf`, `source_pdf_sha256`, `zotero_collections`, `document_role` (`main` / `supporting`), `mirror_paths`.

## Config

`paper_to_markdown/settings.json` (create from `.example.json`). Required keys: `input_root`, `output_root`, `marker_cli`, `hf_home`. Optional: `torch_device` (`cuda`/`mps`/`cpu`). In runner mode, omit Zotero fields. On the controller/all-in-one host, `zotero_db_path` can be used by `paper_to_markdown.zotero_markdown` to build `output_root/zotero_markdown` with `--mode symlink` or `--mode copy`.
