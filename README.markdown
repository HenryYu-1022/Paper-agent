# Paper-agent

中文说明见 [README.zh-CN.markdown](README.zh-CN.markdown)。

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

## Repository Layout

```text
paper-agent/
  paper_to_markdown/
    __init__.py
    common.py
    pipeline.py
    run_once.py
    watch_folder_resilient.py
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

## License

MIT. See `LICENSE`.
