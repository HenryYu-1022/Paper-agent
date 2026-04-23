# Zotero → Markdown Pipeline

**Convert your entire Zotero PDF library into a searchable Markdown corpus for AI agents.**

中文说明见 [README.zh-CN.md](README.zh-CN.md)。

```text
 ┌──────────────┐
 │ zotero.sqlite│── collection hierarchy ──┐
 └──────────────┘                          ▼
                                    ┌──────────────┐     ┌──────────────┐
 ┌──────────────┐   PDF files      │ Markdown +   │     │ AI agents    │
 │ PDF folder   │─────────────────▶│ frontmatter  │────▶│ search &     │
 │ (Google Drive│   Marker engine  │ + symlink    │     │ summarize    │
 │  or local)   │                  │   mirrors    │     └──────────────┘
 └──────────────┘                  └──────────────┘
```

**Key features:**
- Batch PDF→Markdown conversion via [Marker](https://github.com/datalab-to/marker)
- Reads Zotero collection hierarchy from `zotero.sqlite` (read-only)
- Creates symlink mirrors so Markdown library matches Zotero folder structure
- Writes `zotero_collections` tags into YAML frontmatter
- Zotero plugin can rename PDFs, launch the local daemon, convert changed attachments, and open Markdown files
- Conversion status is read from Markdown frontmatter, so the library works across synced devices without `manifest.json`
- Works with [zotero-attanger](https://github.com/HenryYu-1022/zotero-attanger) for multi-device PDF access via Google Drive

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python ≥ 3.10 | [python.org](https://www.python.org/downloads/) |
| PyTorch | [pytorch.org](https://pytorch.org/get-started/locally/) — CUDA / MPS / CPU |
| Marker | `pip install marker-pdf` |
| Project deps | `pip install -r requirements.txt` |

> **Hardware:** GPU strongly recommended (NVIDIA CUDA or Apple MPS). CPU works but is very slow.

---

## Step-by-Step Setup

### Step 1 — Create config

```bash
cp paper_to_markdown/settings.example.json paper_to_markdown/settings.json
```

Edit `paper_to_markdown/settings.json` — the key fields:

```jsonc
{
  "input_root":    "/path/to/your/PDF/folder",       // where your PDFs are
  "output_root":   "/path/to/output/folder",         // where Markdown goes
  "marker_cli":    "marker_single",                  // or absolute path
  "hf_home":       "/path/to/.cache/huggingface",
  "torch_device":  "cuda",                           // cuda / mps / cpu
  "zotero_db_path": "/path/to/Zotero/zotero.sqlite"  // optional, enables collection mirroring
}
```

See [Configuration Reference](#configuration-reference) for all options.

### Step 2 — Batch convert all PDFs

```bash
cd paper_to_markdown
python3 convert.py
```

### Step 3 — (Optional) Use the Zotero plugin

Build and install the local Zotero plugin:

```bash
cd zotero-paper-agent
./scripts/build.sh
```

Install `zotero-paper-agent.xpi` in Zotero via Tools → Plugins/Add-ons → Install Add-on From File.
In the plugin preferences, set:

- `daemon.py`: this repo's `paper_to_markdown/daemon.py`
- `Python`: the Python executable with this project's requirements installed
- `PDF root`: same value as `input_root`
- `Output root`: same value as `output_root`
- `Marker`, `HF cache`, `Device`, and `Idle timeout`

The plugin listens for Zotero attachment add/modify/trash/delete events and talks to the Python daemon over stdin/stdout JSON lines.

### Step 4 — (Optional) Sync Zotero collections

If you still want to mirror collection changes from `zotero.sqlite` outside the plugin event flow:

```bash
cd paper_to_markdown

# One-shot sync
python3 sync_collections.py --once

# Or run as a daemon (polls every 60s)
python3 sync_collections.py
```

### Step 5 — Use with AI

Open the `output_root/markdown/` folder as a workspace in Codex, Claude Code, or any AI agent. Ask questions across your entire library.

---

## Output Structure

```text
output_root/
  markdown/           ← your Markdown library (open this in AI / Obsidian)
    Paper1/
      Paper1.md       ← converted paper with YAML frontmatter
    Collection1/
      Paper2/         ← symlink mirror from Zotero collection
  logs/
    app.log
    failed_pdfs.txt
  archive/            ← optional orphan archives from the plugin/daemon
```

Each `.md` file includes YAML frontmatter. This frontmatter is the conversion state:

```yaml
---
source_pdf: /path/to/Paper1.pdf
source_relpath: Collection/Paper1.pdf
source_filename: Paper1.pdf
source_pdf_sha256: ...
conversion_status: success
zotero_collections:    # only when zotero_db_path is configured
  - Research/NLP
  - Coursework/CS229
---
```

---

## Command Reference

| What you want to do | Command |
|---|---|
| Convert all PDFs once | `cd paper_to_markdown && python3 convert.py` |
| Convert a single PDF | `python3 convert.py --path "/path/to/Paper.pdf"` |
| Force reconvert everything | `python3 convert.py --force` |
| Test with first N files | `python3 convert.py --limit 5` |
| Clean up orphaned Markdown | `python3 convert.py --cleanup` |
| Run JSON-line daemon | `python3 -m paper_to_markdown.daemon --config paper_to_markdown/settings.json` |
| Build Zotero plugin | `cd zotero-paper-agent && ./scripts/build.sh` |
| Sync Zotero collections (once) | `cd paper_to_markdown && python3 sync_collections.py --once` |
| Sync Zotero collections (daemon) | `cd paper_to_markdown && python3 sync_collections.py` |
| Check conversion progress | `python3 monitor.py` |

---

## JSON-Line Daemon

The Zotero plugin manages the daemon automatically. For manual testing:

```bash
python3 -m paper_to_markdown.daemon --config paper_to_markdown/settings.json
```

Example request:

```json
{"id":"1","command":"convert","path":"/path/to/Paper.pdf"}
```

Supported commands include `ping`, `convert`, `archive_orphan`, `delete_orphan`, `cleanup_orphans`, `rescan`, and `shutdown`.

---

## Configuration Reference

Config file: `paper_to_markdown/settings.json`

| Key | Required | Default | Description |
|---|---|---|---|
| `input_root` | ✅ | — | PDF source directory |
| `output_root` | ✅ | — | Output directory for Markdown, logs, state |
| `marker_cli` | ✅ | — | Marker command or path (e.g. `marker_single`) |
| `hf_home` | ✅ | — | Hugging Face cache directory |
| `torch_device` | | `cuda` | `cuda` / `mps` / `cpu` |
| `force_ocr` | | `false` | Force OCR for scanned PDFs |
| `zotero_db_path` | | — | Path to `zotero.sqlite`; enables collection mirroring |
| `collection_mirror_mode` | | `symlink` | `symlink` or `copy` |
| `zotero_sync_interval_seconds` | | `60` | Collection sync polling interval |
| `daemon_idle_timeout_seconds` | | `300` | Exit the plugin daemon after this many idle seconds; `0` disables |
| `python_path` | | — | Absolute Python path for background startup |
| `log_level` | | `INFO` | Logging level |

---

## File Guide

| File | What it does |
|---|---|
| `paper_to_markdown/convert.py` | Manual batch conversion CLI |
| `paper_to_markdown/daemon.py` | JSON-line daemon used by the Zotero plugin |
| `paper_to_markdown/sync_collections.py` | Zotero collection sync daemon |
| `paper_to_markdown/settings.json` | Your local config (create from `.example.json`) |
| `paper_to_markdown/pipeline.py` | Core conversion engine (imported, not run directly) |
| `paper_to_markdown/frontmatter_index.py` | In-memory conversion index built from Markdown frontmatter |
| `paper_to_markdown/common.py` | Shared utilities (imported) |
| `paper_to_markdown/zotero_collections.py` | Zotero DB reader (imported) |
| `zotero-paper-agent/` | Zotero 7 plugin source and build script |
| `monitor.py` | Progress viewer |
| `backfill.py` | Backfill missing supporting PDFs |

---

## Acknowledgments

<table>
  <tr>
    <td align="center"><a href="https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview"><img src="https://img.shields.io/badge/Claude_Code-Anthropic-CC785C?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude Code"></a></td>
    <td>Architecture, implementation, and documentation pair-programmed with <strong>Claude Code</strong>.</td>
  </tr>
  <tr>
    <td align="center"><a href="https://openai.com/index/codex/"><img src="https://img.shields.io/badge/Codex-OpenAI-412991?style=for-the-badge&logo=openai&logoColor=white" alt="OpenAI Codex"></a></td>
    <td><strong>OpenAI Codex</strong> used for code review and cross-referencing.</td>
  </tr>
</table>

> *"We built a tool to let AI agents read research papers — and used AI agents to build it."*

## License

MIT. See `LICENSE`.
