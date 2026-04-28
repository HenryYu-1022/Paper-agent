# Zotero → Markdown Pipeline

**Convert your entire Zotero PDF library into a searchable Markdown corpus for AI agents.**

中文说明见 [README.zh-CN.md](README.zh-CN.md)。

```text
 ┌──────────────┐   PDF files      ┌──────────────┐     ┌──────────────┐
 │ PDF folder   │─────────────────▶│ Markdown +   │────▶│ AI agents    │
 │ (Google Drive│   Marker engine  │ frontmatter  │     │ search &     │
 │  or local)   │                  └──────────────┘     │ summarize    │
 └──────────────┘                         │             └──────────────┘
                                          ▼
                               controller builds
                               zotero_markdown/
```

**Key features:**
- Batch PDF→Markdown conversion via [Marker](https://github.com/datalab-to/marker)
- Runner mode converts only and never needs Zotero SQLite
- Controller/all-in-one mode can build `zotero_markdown/` collection views from `zotero.sqlite`
- Writes `zotero_collections` tags into YAML frontmatter
- Conversion status is read from Markdown frontmatter, so the library works across synced devices without `manifest.json`
- Loose marker images are auto-organized into per-bundle `figures/` subfolders
- Works with [zotero-attanger](https://github.com/HenryYu-1022/zotero-attanger) for multi-device PDF access via Google Drive

---

## Quick Start — Three Decisions

> **You only need to make three decisions.** Everything else has a safe default.

### Decision 1 — Single machine or two machines?

| Mode | When to use | `run_mode` value |
|---|---|---|
| **Single machine** | One computer runs everything | `all-in-one` (default, no need to set) |
| **Win runner + Mac controller** | Windows GPU does conversion, Mac handles cleanup | `runner` on Win, `controller` on Mac |

---

### Decision 2 — Where are your PDFs and where should Markdown go?

Set `input_root` (your PDF folder) and `output_root` (where Markdown will be written). For two-machine setups, both machines point to the same Google Drive folders — each machine uses its own local mount path.

---

### Decision 3 — Where is Marker? (runner / single machine only)

Set `marker_cli` to the command name (`marker_single`) or its absolute path.

---

### Minimum `settings.json`

**Single machine:**

```json
{
  "input_root":   "/path/to/your/PDF/folder",
  "output_root":  "/path/to/your/Markdown/output",
  "marker_cli":   "marker_single",
  "hf_home":      "/path/to/.cache/huggingface",
  "torch_device": "mps"
}
```

> `torch_device`: NVIDIA GPU → `"cuda"` · Apple Silicon → `"mps"` · No GPU → `"cpu"`

**Two machines — Windows runner `settings.json`:**

```json
{
  "run_mode":     "runner",
  "input_root":   "G:/Shared/PDFs",
  "output_root":  "G:/Shared/Markdown",
  "marker_cli":   "marker_single",
  "hf_home":      "C:/Users/you/.cache/huggingface",
  "torch_device": "cuda"
}
```

**Two machines — Mac controller `settings.json`:**

```json
{
  "run_mode":    "controller",
  "input_root":  "/Volumes/GoogleDrive/PDFs",
  "output_root": "/Volumes/GoogleDrive/Markdown"
}
```

---

### Run

**Windows runner / single machine — watch PDFs and convert locally:**

```bash
python3 monitor.py --background --watch --interval 60 --convert --apply
```

For a one-shot full scan, you can still use `cd paper_to_markdown && python3 convert.py`.

**Mac controller — scan for orphaned Markdown and delete them when their PDF is gone:**

```bash
python3 -m paper_to_markdown.verify --apply --watch
```

> **Note:** Neither script starts automatically. Run them manually, or register them with Windows Task Scheduler / macOS launchd for periodic execution.

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
  "torch_device":  "cuda"                            // cuda / mps / cpu
}
```

See [Configuration Reference](#configuration-reference) for all options.

### Step 2 — Batch convert all PDFs

```bash
cd paper_to_markdown
python3 convert.py
```

### Step 3 — (Optional) Build Zotero Markdown views

Run this on the controller/all-in-one host where Zotero is available:

```bash
python3 -m paper_to_markdown.zotero_markdown --mode symlink --clean
```

### Step 4 — Use with AI

Open the `output_root/markdown/` folder as a workspace in Codex, Claude Code, or any AI agent. Ask questions across your entire library.

---

## Output Structure

```text
output_root/
  markdown/           ← your Markdown library (open this in AI / Obsidian)
    Paper1/
      Paper1.md       ← converted paper with YAML frontmatter
  zotero_markdown/    ← optional controller-built Zotero collection view
    Collection1/
      Paper2/         ← symlink/copy pointing at a converted bundle
  logs/
    app.log
    failed_pdfs.txt
  archive/            ← optional orphan archives from monitor.py / verify.py
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
| Watch PDFs and convert in realtime (runner, hidden on Windows) | `python3 monitor.py --background --watch --interval 60 --convert --apply` |
| Convert a single PDF | `python3 convert.py --path "/path/to/Paper.pdf"` |
| Force reconvert everything | `python3 convert.py --force` |
| Test with first N files | `python3 convert.py --limit 5` |
| Clean up orphaned Markdown | `python3 convert.py --cleanup` |
| Postprocess duplicate/SI suffix PDFs (dry-run) | `python3 -m paper_to_markdown.postprocess_markdown` |
| Apply duplicate/SI postprocess cleanup | `python3 -m paper_to_markdown.postprocess_markdown --apply` |
| Organize loose images into per-bundle `figures/` (dry-run) | `python3 -m paper_to_markdown.organize_figures` |
| Apply figure organization | `python3 -m paper_to_markdown.organize_figures --apply` |
| Build Zotero Markdown views | `python3 -m paper_to_markdown.zotero_markdown --mode symlink --clean` |
| Monitor and convert PDFs needing work | `python3 monitor.py` |
| Check conversion progress only | `python3 monitor.py --no-convert` |
| Scan for orphaned Markdown (dry-run) | `python3 -m paper_to_markdown.verify` |
| Delete orphaned Markdown immediately | `python3 -m paper_to_markdown.verify --apply` |
| Watch and auto-delete orphans (controller) | `python3 -m paper_to_markdown.verify --apply --watch` |

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
| `zotero_db_path` | | — | Controller/all-in-one only; path to `zotero.sqlite` for Zotero Markdown views |
| `zotero_markdown_root` | | `output_root/zotero_markdown` | Controller/all-in-one only; output root for Zotero collection Markdown views |
| `zotero_sync_interval_seconds` | | `60` | Controller collection sync polling interval |
| `python_path` | | — | Absolute Python path for background startup |
| `log_level` | | `INFO` | Logging level |

---

## File Guide

| File | What it does |
|---|---|
| `paper_to_markdown/convert.py` | Manual batch conversion CLI (runner / all-in-one) |
| `paper_to_markdown/verify.py` | Controller-mode orphan scanner: deletes Markdown when its PDF is gone |
| `paper_to_markdown/postprocess_markdown.py` | After-conversion duplicate main cleanup and SI merge tool |
| `paper_to_markdown/organize_figures.py` | Moves loose images in each bundle into a `figures/` subfolder; runs as a CLI and inside the conversion + monitor loops |
| `paper_to_markdown/zotero_markdown.py` | Controller/all-in-one Zotero Markdown view builder |
| `paper_to_markdown/settings.json` | Your local config (create from `.example.json`) |
| `paper_to_markdown/pipeline.py` | Core conversion engine (imported, not run directly) |
| `paper_to_markdown/frontmatter_index.py` | In-memory conversion index built from Markdown frontmatter |
| `paper_to_markdown/common.py` | Shared utilities (imported) |
| `paper_to_markdown/zotero_collections.py` | Zotero DB reader (imported) |
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
