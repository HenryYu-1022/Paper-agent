# Paper-agent

中文说明见 [README.zh-CN.markdown](README.zh-CN.markdown)。

---

## Why This Project Exists

### The Pain Points

If you are a researcher or knowledge worker who uses **Zotero** to manage your literature, you have probably run into these frustrations:

1. **AI coding agents cannot read your papers.** Tools like [OpenAI Codex](https://openai.com/index/codex/) and [Claude Code](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview) are incredibly powerful at understanding, searching, and synthesizing text — but they operate on *text files*, not PDFs. Your entire Zotero library is locked inside a format that no AI agent can natively ingest.

2. **Zotero is a closed silo for LLM workflows.** Zotero is great for organizing references and reading PDFs, but it does not expose your library as a searchable text corpus. You cannot ask an LLM "find all papers in my library that discuss method X and summarize their approaches" — because the content simply is not available in a form the LLM can consume.

3. **Manual note-taking does not scale.** You can read papers one by one and write notes, but cross-referencing dozens or hundreds of papers for a literature review, a grant proposal, or a research question requires a level of information integration that is tedious and error-prone when done by hand.

4. **No bridge between your reference manager and your personal knowledge base.** Even if you maintain a Markdown note library (in Obsidian, Logseq, Notion, or plain files), there is no automatic pipeline to turn your Zotero PDFs into structured Markdown that lives alongside your own notes and can be queried together.

### The Core Idea

**Convert your entire Zotero PDF library into a Markdown corpus, then let AI agents read it.**

```text
   Zotero Library          Structured PDFs           Markdown Library          AI Agent Access
 ┌──────────────┐       ┌──────────────────┐       ┌─────────────────┐       ┌─────────────────┐
 │  Papers in   │       │  PDFs organized  │       │  .md files with │       │  Codex / Claude │
 │  collections │──────▶│  by collection   │──────▶│  YAML front-    │──────▶│  Code search,   │
 │  & folders   │       │  hierarchy       │       │  matter + text  │       │  summarize,     │
 └──────────────┘       └──────────────────┘       └─────────────────┘       │  integrate      │
                 zotero-attanger             paper-agent                     └─────────────────┘
                  (migrate &                 (PDF → Markdown                         │
                   export)                    conversion)                            ▼
                                                                            ┌─────────────────┐
                                                                            │  Sync to your   │
                                                                            │  personal note  │
                                                                            │  library        │
                                                                            └─────────────────┘
```

The workflow is:

1. **Organize & export** — Use [zotero-attanger](https://github.com/HenryYu-1022/zotero-attanger) (or any similar tool) to migrate and export your Zotero attachments into a clean folder hierarchy that mirrors your Zotero collections. This step also prepares the file structure for YAML metadata enrichment.

2. **Convert** — Point `paper-agent` at the exported PDF directory. It batch-converts every PDF to Markdown using [Marker](https://github.com/datalab-to/marker), preserves the folder structure, and stamps each file with YAML frontmatter (source path, conversion timestamp, device, document role, etc.).

3. **Query with AI** — Open the resulting Markdown library as a workspace in Codex or Claude Code. Now you can ask questions across your entire library: *"Which papers propose attention-based architectures for time-series forecasting?"*, *"Summarize the experimental setups used in all papers under my `Reinforcement Learning` collection"*, *"Compare the loss functions described in these three papers"*. The AI agent can read, grep, and cross-reference thousands of pages of full-text content in seconds.

4. **Sync to your notes** — Because the output is plain Markdown with structured frontmatter, you can drop the library (or symlink it) into Obsidian, Logseq, or any Markdown-based note system. Your AI-generated insights and your personal annotations live in the same ecosystem.

### Recommended: Using zotero-attanger for the Migration Step

If your PDFs currently live inside Zotero's opaque storage (the `storage/` directory with random 8-character folder names), you need a way to export them into a human-readable, collection-based folder tree before `paper-agent` can process them.

[**zotero-attanger**](https://github.com/HenryYu-1022/zotero-attanger) is purpose-built for this. It reads your Zotero database, recreates the collection hierarchy as real folders, and moves or copies attachments into the matching directories. It can also inject or update YAML metadata in the exported filenames or sidecar files, providing a clean starting point for the YAML frontmatter that `paper-agent` later writes into the converted Markdown.

> **Tip:** If your PDFs are already organized in normal folders (not inside Zotero's storage), you do not need `zotero-attanger` — just point `paper-agent` directly at your PDF root.

---

`paper-agent` uses [Marker](https://github.com/datalab-to/marker) to convert a directory tree of PDF papers into a Markdown library. It is designed for local folders, cloud-mounted folders, or exporter outputs such as `zotero-attanger` where PDFs are already arranged by collection or folder hierarchy.

## What It Does

- Converts PDFs to Markdown with Marker
- Watches an input folder and processes new or changed PDFs
- Keeps output organized with the same relative folder structure as the input tree
- Detects supporting PDFs like `Paper_1.pdf` and merges them into the main paper bundle
- Tracks conversion state in a manifest to skip unchanged files
- Cleans up Markdown artifacts when the source PDF is deleted
- Writes YAML frontmatter into generated Markdown files
- Supports both manual runs and background watcher startup on Windows and macOS

## Input Model

`paper-agent` does not care where the PDFs come from. `input_root` can be:

- A normal local folder
- A cloud sync folder such as Google Drive, iCloud Drive, Dropbox, or OneDrive
- A collection-based export directory produced by tools like `zotero-attanger`

The only requirement is that all PDFs you want to process live under one root directory.

## Directory Model

You configure two roots:

- `input_root`: where the PDFs live
- `output_root`: where Markdown, logs, manifest, and temporary Marker output are written

Keep them separate. Do not point `output_root` inside `input_root`.

## Prerequisites

1. Python 3.10+
2. PyTorch for your platform from [pytorch.org](https://pytorch.org)
3. Marker

```bash
pip install marker-pdf
# or, for extra document formats:
pip install marker-pdf[full]
```

4. Project dependencies

```bash
pip install -r requirements.txt
```

## Quick Start

### 1. Create Config

macOS / Linux:

```bash
cp paper_to_markdown/settings.example.json paper_to_markdown/settings.json
```

Windows PowerShell:

```powershell
Copy-Item paper_to_markdown\settings.example.json paper_to_markdown\settings.json
```

Example:

```jsonc
{
  "_comment_01": "macOS only needs python_path; pythonw_path is only used on Windows.",
  "input_root": "/Users/yourname/Documents/paper-library/input",
  "output_root": "/Users/yourname/Documents/paper-library/output",
  "python_path": "/opt/homebrew/bin/python3",
  "pythonw_path": "",
  "model_cache_dir": "/Users/yourname/.cache/marker/datalab_model_cache",
  "marker_cli": "/absolute/path/to/marker_single",
  "hf_home": "/Users/yourname/.cache/huggingface",
  "torch_device": "mps"
}
```

Notes:

- `marker_cli` can be an absolute path or just `marker_single`
- `python_path` should be absolute, especially for background startup
- macOS LaunchAgent reads `python_path` and does not read `pythonw_path`
- `pythonw_path` is only for Windows background scheduled tasks; on macOS you can leave it empty or omit it
- `torch_device` is usually `cuda` on NVIDIA Windows/Linux, `mps` on Apple Silicon, and `cpu` when no accelerator is available

### 2. Download Marker Models Once

```bash
marker_single /path/to/test.pdf --output_dir /tmp/test_out --force_ocr
```

### 3. Run a Batch Conversion

```bash
cd paper_to_markdown
python3 run_once.py
```

### 4. Start the Watcher

```bash
cd paper_to_markdown
python3 watch_folder_resilient.py
```

## Local Folder Workflow

If your PDFs are already stored locally, for example:

```text
/Users/you/Documents/PapersByTopic/
  AI/
    Paper1.pdf
    Paper1_1.pdf
  Chemistry/
    Paper2.pdf
```

set:

- `input_root=/Users/you/Documents/PapersByTopic`
- `output_root=/Users/you/Documents/paper-agent-output`

Then run:

```bash
cd paper_to_markdown
python3 run_once.py
```

This works the same way for any exporter that has already arranged PDFs into folders, including `zotero-attanger`.

## Usage

Scripts inside `paper_to_markdown/` should be run from that directory. Root-level utilities such as `backfill_supporting.py`, `monitor_conversion_progress.py`, `install_or_update_watch_task.ps1`, `remove_watch_task.ps1`, `install_or_update_launch_agent.sh`, and `remove_launch_agent.sh` should be run from the repository root.

### Manual Conversion

```bash
cd paper_to_markdown

# convert all PDFs under input_root
python3 run_once.py

# convert one PDF
python3 run_once.py --path "/path/to/input_root/subdir/Paper.pdf"

# reconvert everything
python3 run_once.py --force

# test with a small batch
python3 run_once.py --limit 5

# clean artifacts whose source PDFs no longer exist
python3 run_once.py --cleanup

# use a custom config file
python3 run_once.py --config /path/to/settings.json
```

### Watch Mode

```bash
cd paper_to_markdown
python3 watch_folder_resilient.py
```

The watcher recursively monitors `input_root` and handles:

- PDF created: queue for conversion after debounce and stability checks
- PDF modified: queue again
- PDF moved or renamed: queue the new path
- PDF deleted: remove Markdown bundle, raw output, and manifest entry

### Backfill Supporting PDFs

```bash
python3 backfill_supporting.py
python3 backfill_supporting.py --apply
python3 backfill_supporting.py --limit 10
```

### Monitor Progress

```bash
python3 monitor_conversion_progress.py
python3 monitor_conversion_progress.py --watch --interval 30
```

## Background Startup

### macOS LaunchAgent

```bash
zsh ./install_or_update_launch_agent.sh
zsh ./remove_launch_agent.sh
```

- Supervisor: `paper_agent_watch_supervisor.sh`
- Default label: `com.paper.agent.watch`
- Installed plist: `~/Library/LaunchAgents/com.paper.agent.watch.plist`

### Windows Scheduled Task

```powershell
powershell -ExecutionPolicy Bypass -File .\install_or_update_watch_task.ps1
powershell -ExecutionPolicy Bypass -File .\remove_watch_task.ps1
```

- Supervisor: `paper_agent_watch_supervisor.ps1`
- Default task name: `PaperAgentWatch`

## Output Structure

For an input tree like:

```text
input_root/
  AI/
    Paper1.pdf
    Paper1_1.pdf
  Chemistry/
    Paper2.pdf
```

the output is:

```text
output_root/
  markdown/
    AI/
      Paper1/
        Paper1.md
        supporting.md
        supporting_assets/
    Chemistry/
      Paper2/
        Paper2.md
  state/
    manifest.json
  marker_raw/
  logs/
    app.log
    failed_pdfs.txt
```

## Frontmatter

Each Markdown file gets YAML frontmatter similar to:

```yaml
---
source_pdf: /path/to/input_root/AI/Paper1.pdf
source_relpath: AI/Paper1.pdf
source_filename: Paper1.pdf
converter: marker_single
converted_at: '2026-04-09T10:30:45.123456+00:00'
torch_device: mps
force_ocr: true
document_role: main
---
```

Supporting PDFs also include `supporting_index` and primary-paper metadata.

## Zotero Collection Mirroring

If you configure `zotero_db_path` in `settings.json`, `paper-agent` reads your Zotero database (read-only) to discover which collections each paper belongs to. It then:

1. **Tags each Markdown file** with a `zotero_collections` list in the YAML frontmatter
2. **Creates symlink mirrors** so the paper appears in every collection folder, even if the PDF only exists in one physical location

For example, if a paper belongs to both `Research/NLP` and `Coursework/CS229` in Zotero but only exists in `Research/NLP/` on disk:

```text
output_root/
  markdown/
    Research/NLP/Paper/          ← real directory
      Paper.md
    Coursework/CS229/Paper/      ← symlink → Research/NLP/Paper/
```

The YAML frontmatter will include:

```yaml
zotero_collections:
  - Coursework/CS229
  - Research/NLP
```

### Sync Daemon

When you move papers between collections in Zotero, run the sync daemon to update the Markdown library:

```bash
# One-shot sync
cd paper_to_markdown
python3 sync_zotero_collections.py --once

# Continuous daemon (polls every 60 seconds by default)
python3 sync_zotero_collections.py

# Custom interval
python3 sync_zotero_collections.py --interval 30
```

The daemon detects changes in collection assignments and:
- Adds new symlink mirrors when a paper is added to a collection
- Removes symlink mirrors when a paper is removed from a collection
- Updates the `zotero_collections` field in the YAML frontmatter

Zotero can remain running while the sync daemon operates. The database is opened in immutable read-only mode.

## Supporting PDF Rules

A PDF is treated as supporting material when all of these are true:

1. The converted markdown contains `supportinginformation` near the start after whitespace and punctuation are normalized
2. A main PDF with a matching name can be found in the same directory

Numeric suffixes such as `_1` and `_2` are still supported for indexing, but they are no longer required for supporting detection.

Otherwise it is treated as a standalone paper.

## Automatic Retry on Failure

Marker occasionally crashes or fails due to transient issues such as GPU memory pressure or corrupted intermediate state. To handle this, `paper-agent` automatically retries failed conversions up to 3 times:

- **Batch mode (`run_once.py`)**: After the first pass, any PDFs that failed are retried up to 3 additional times. The final `failed_pdfs.txt` report only lists PDFs that still fail after all retries.
- **Single-file mode (`run_once.py --path`)**: The same 3-retry logic applies.
- **Watch mode (`watch_folder_resilient.py`)**: Each PDF that triggers a filesystem event is converted with up to 3 retry attempts before being marked as failed.

The retry count is controlled by `MAX_CONVERSION_RETRIES` in `pipeline.py` (default: 3).

## Configuration Reference

Config file: `paper_to_markdown/settings.json`

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `input_root` | Yes | -- | Root directory containing the source PDFs |
| `output_root` | Yes | -- | Root directory for markdown, state, logs, and raw output |
| `marker_cli` | Yes | -- | Marker command or absolute path, such as `marker_single` or `.venv/bin/marker_single` |
| `hf_home` | Yes | -- | Hugging Face cache directory |
| `python_path` | No | -- | Absolute Python path actually used by macOS LaunchAgent, and also available as a Windows fallback |
| `pythonw_path` | No | -- | Preferred hidden-background `pythonw.exe` path for Windows scheduled tasks only; macOS does not need or read this field |
| `marker_repo_root` | No | -- | Optional Marker working directory, only needed for special local setups |
| `model_cache_dir` | No | -- | Exported as `MODEL_CACHE_DIR` by the supervisor scripts |
| `torch_device` | No | `cuda` | `cuda`, `mps`, or `cpu` |
| `output_format` | No | `markdown` | Marker output format |
| `force_ocr` | No | `false` | Force OCR even for machine-readable PDFs |
| `disable_image_extraction` | No | `false` | Disable image extraction |
| `disable_multiprocessing` | No | `false` | Disable Marker multiprocessing |
| `paginate_output` | No | `false` | Add page markers to Markdown |
| `compute_sha256` | No | `false` | Add SHA256 to change detection |
| `log_level` | No | `INFO` | Logging level |
| `watch_debounce_seconds` | No | `8` | Delay before processing a changed file |
| `watch_stable_checks` | No | `3` | Number of stability checks before conversion |
| `watch_stable_interval_seconds` | No | `2` | Seconds between stability checks |
| `watch_rescan_interval_seconds` | No | `60` | Periodic full rescan interval; `0` disables it |
| `watch_initial_scan` | No | `true` | Queue existing unprocessed PDFs when watcher starts |
| `zotero_db_path` | No | -- | Path to `zotero.sqlite`; enables collection hierarchy mirroring when set |
| `collection_mirror_mode` | No | `symlink` | `symlink` (saves space) or `copy` (for Windows without admin) |
| `zotero_sync_interval_seconds` | No | `60` | Polling interval for the collection sync daemon |

## Repository Layout

```text
paper-agent/
  paper_to_markdown/
    __init__.py
    common.py
    pipeline.py
    run_once.py
    watch_folder_resilient.py
    zotero_collections.py
    sync_zotero_collections.py
    settings.json
    settings.example.json
  backfill_supporting.py
  monitor_conversion_progress.py
  paper_agent_watch_supervisor.ps1
  paper_agent_watch_supervisor.sh
  install_or_update_watch_task.ps1
  remove_watch_task.ps1
  install_or_update_launch_agent.sh
  remove_launch_agent.sh
  requirements.txt
  README.markdown
  README.zh-CN.markdown
```

## Notes

- `settings.json` is machine-specific and gitignored
- The `_comment_*` fields at the top of `settings.example.json` are human-readable notes and do not affect runtime behavior
- `paper-agent` works with local folders and does not require Google Drive
- If you use `zotero-attanger`, point `input_root` at its exported PDF tree
- macOS LaunchAgents do not inherit your interactive shell PATH, and the scripts only read `python_path`, so that field should be absolute
- Windows background tasks prefer `pythonw_path`; if it is missing, they fall back to `python_path`

## Acknowledgments

This project was built with significant assistance from AI coding agents — part of the very workflow it enables:

<table>
  <tr>
    <td align="center"><a href="https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview"><img src="https://img.shields.io/badge/Claude_Code-Anthropic-CC785C?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude Code"></a></td>
    <td>Architecture design, code implementation, documentation, and iterative debugging were pair-programmed with <strong>Claude Code</strong>.</td>
  </tr>
  <tr>
    <td align="center"><a href="https://openai.com/index/codex/"><img src="https://img.shields.io/badge/Codex-OpenAI-412991?style=for-the-badge&logo=openai&logoColor=white" alt="OpenAI Codex"></a></td>
    <td><strong>OpenAI Codex</strong> was used for code review, refactoring suggestions, and cross-referencing implementation patterns.</td>
  </tr>
</table>

> *"We built a tool to let AI agents read research papers — and used AI agents to build it."*

## License

MIT. See `LICENSE`.
