# Zotero Google Drive PDF to Markdown

中文说明见 [README.zh-CN.markdown](README.zh-CN.markdown)。

Automatically convert Zotero PDF papers stored on Google Drive into Markdown using [Marker](https://github.com/VikParuchuri/marker). Designed for a cloud-native workflow where both source PDFs and generated Markdown stay on Google Drive, enabling use with Obsidian or other Markdown-based tools.

## Features

- **Automatic PDF conversion** -- uses Marker for high-quality PDF-to-Markdown with OCR support
- **Real-time watching** -- monitors the PDF folder and converts new/modified files on the fly
- **Deletion sync** -- automatically removes Markdown artifacts when source PDFs are deleted
- **Supporting PDF bundling** -- detects supplementary/supporting information PDFs (e.g. `Paper_1.pdf`) and merges them into the main paper's Markdown bundle
- **Manifest-based state tracking** -- skips unchanged PDFs, tracks failures, prevents redundant conversions
- **Orphan cleanup** -- batch scan to remove Markdown for PDFs that no longer exist
- **Automatic raw cleanup** -- deletes the `marker_raw/` intermediate directory automatically when a run exits
- **YAML frontmatter** -- every Markdown file includes metadata (source path, converter, timestamp, document role)
- **Windows scheduled task** -- auto-start watcher at login with a resilient PowerShell supervisor
- **Cloud-native** -- all data stays on Google Drive; the project folder only stores scripts and config

## Architecture

```
Google Drive                              Local Machine
+-----------------------------+           +----------------------------------+
| Zotero_Papers/              |           | This Project                     |
|   Paper1.pdf           <----+--- read   |   pdf_to_markdown/               |
|   Paper1_1.pdf              |           |     run_once.py                  |
|   subfolder/Paper2.pdf      |           |     watch_folder_resilient.py    |
|                             |           |     pipeline.py                  |
| zoterotomarkdown/           |           |     common.py                   |
|   markdown/            <----+--- write  |     settings.json                |
|   state/manifest.json       |           |                                  |
|   marker_raw/               |           | Marker (separate install)        |
|   logs/                     |           |   marker_single.exe              |
+-----------------------------+           +----------------------------------+
```

## Prerequisites

1. **Python 3.10+**
2. **PyTorch** -- install for your platform from [pytorch.org](https://pytorch.org)
3. **Marker** -- PDF conversion engine:

```powershell
pip install marker-pdf
# or full dependency set:
pip install marker-pdf[full]
```

4. **Project dependencies**:

```powershell
pip install -r requirements.txt
```

> `requirements.txt` includes `watchdog` (file monitoring) and `PyYAML` (frontmatter generation).

5. **Google Drive for Desktop** -- source PDFs and output Markdown are accessed via the local Google Drive mount point.

## Quick Start

### 1. Configure

Copy the example config and edit it:

```powershell
Copy-Item pdf_to_markdown\settings.example.json pdf_to_markdown\settings.json
```

Edit `settings.json` with your paths:

```jsonc
{
  "source_dir": "G:\\YourDrive\\Zotero_Papers",      // PDF source folder on Google Drive
  "work_root": "G:\\YourDrive\\zoterotomarkdown",     // output root on Google Drive
  "marker_cli": "C:\\path\\to\\marker_single.exe",    // marker executable
  "marker_repo_root": "D:\\marker",                   // marker repository root
  "hf_home": "D:\\marker\\hf_cache",                  // Hugging Face model cache
  "pythonw_path": "C:\\path\\to\\pythonw.exe"         // required only for Windows scheduled task
}
```

For manual runs, `pythonw_path` can be omitted. If you plan to use the Windows scheduled task, it must point to a valid `pythonw.exe`.

### 2. Download Marker Models

Run a test conversion to trigger model download:

```powershell
marker_single C:\path\to\any_test.pdf --output_dir C:\temp\test_out --force_ocr
```

### 3. Convert All PDFs

```powershell
cd pdf_to_markdown
python run_once.py
```

`marker_raw/` is recreated during conversion and removed automatically when the run ends.

### 4. Start the Watcher

```powershell
cd pdf_to_markdown
python watch_folder_resilient.py
```

The watcher runs continuously, converting new PDFs as they appear and cleaning up when PDFs are deleted.
When the watcher process exits, it also removes `marker_raw/` automatically.

## Usage

Commands for scripts inside `pdf_to_markdown/` are run from that directory. Root-level utilities such as `backfill_supporting.py`, `monitor_conversion_progress.py`, `install_or_update_task.ps1`, and `remove_task.ps1` should be run from the project root.

### Manual Conversion (`run_once.py`)

```powershell
cd pdf_to_markdown

# Convert all unprocessed PDFs
python run_once.py

# Convert a single PDF
python run_once.py --path "G:\YourDrive\Zotero_Papers\Paper.pdf"

# Force reconvert everything (ignore manifest)
python run_once.py --force

# Test with a small batch
python run_once.py --limit 5

# Clean up orphaned Markdown (source PDF was deleted)
python run_once.py --cleanup

# Use a custom config file
python run_once.py --config /path/to/settings.json
```

### Watch Mode (`watch_folder_resilient.py`)

```powershell
cd pdf_to_markdown
python watch_folder_resilient.py
```

The watcher monitors `source_dir` recursively and handles four events:

| Event | Behavior |
|-------|----------|
| PDF created | Queue for conversion after debounce + stability check |
| PDF modified | Re-queue for conversion |
| PDF moved/renamed | Queue new path for conversion |
| PDF deleted | Delete corresponding Markdown bundle, raw output, and manifest entry |

Stop with `Ctrl+C`.

### Backfill Supporting PDFs

For historical PDFs where supporting files were missed:

```powershell
# Dry run -- see what would be converted
python backfill_supporting.py

# Actually convert missing supporting PDFs
python backfill_supporting.py --apply

# Limit inspection scope
python backfill_supporting.py --limit 10
```

Like the other entrypoints, `backfill_supporting.py` removes `marker_raw/` automatically when it exits.

### Monitor Conversion Progress

Use the standalone monitor script to estimate how much of a batch is done and how long it may take to finish:

```powershell
# One-time snapshot
python monitor_conversion_progress.py

# Refresh every 30 seconds
python monitor_conversion_progress.py --watch --interval 30
```

The monitor reads `state/manifest.json` and `logs/app.log` to report total PDFs, completed files, current file, remaining count, average conversion time, and ETA. It does not change the conversion pipeline.

### Windows Scheduled Task

Auto-start the watcher at login:

```powershell
# Install or update the scheduled task
powershell -ExecutionPolicy Bypass -File .\install_or_update_task.ps1

# Remove the scheduled task
powershell -ExecutionPolicy Bypass -File .\remove_task.ps1
```

The scheduled task runs `zotero_pdf_watch_supervisor.ps1`, a resilient supervisor that monitors the watcher process and restarts it if it crashes.
This feature requires `pythonw_path` to be set in `pdf_to_markdown/settings.json`.

## Output Structure

For a PDF library like:

```
Zotero_Papers/
  Paper1.pdf
  Paper1_1.pdf          # supporting information
  subfolder/
    Paper2.pdf
```

The generated Markdown library is:

```
zoterotomarkdown/
  markdown/
    Paper1/
      Paper1.md               # main paper with frontmatter
      supporting.md            # supporting information (from Paper1_1.pdf)
      supporting_assets/       # images from supporting PDF
      _page_0_Figure_1.jpeg    # images from main PDF
    subfolder/
      Paper2/
        Paper2.md
  state/
    manifest.json              # conversion tracking
  marker_raw/                  # raw Marker output (intermediate, auto-deleted when a run exits)
  logs/
    app.log                    # application log
    failed_pdfs.txt            # report of failed conversions
```

### Frontmatter

Every Markdown file is prepended with YAML frontmatter:

```yaml
---
source_pdf: G:/YourDrive/Zotero_Papers/Paper1.pdf
source_relpath: Paper1.pdf
source_filename: Paper1.pdf
converter: marker_single
converted_at: '2025-01-15T10:30:45.123456+00:00'
torch_device: cuda
force_ocr: true
document_role: main
---

## Full Text

[Markdown content from Marker...]
```

Supporting PDFs include additional fields: `document_role: supporting`, `supporting_index`, and `primary_source_pdf`.

## Supporting PDF Detection

A PDF is treated as supporting material when **all three conditions** are met:

1. **Filename pattern** -- ends with `_1`, `_2`, etc. (e.g. `Paper_1.pdf`)
2. **Primary PDF exists** -- `Paper.pdf` must exist in the same directory
3. **Content check** -- the first 4000 characters of the converted Markdown contain one of: `"supporting information"`, `"supplementary information"`, `"supplemental information"`

If any condition is not met, the file is converted as an independent paper with its own Markdown bundle.

## Configuration Reference

`pdf_to_markdown/settings.json`:

Defaults below describe runtime behavior when a key is omitted. `settings.example.json` includes explicit starter values, so its contents may intentionally differ from these fallback defaults.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `source_dir` | Yes | -- | Google Drive folder containing source PDFs |
| `work_root` | Yes | -- | Google Drive folder for all output (markdown, state, logs, raw) |
| `marker_cli` | Yes | -- | Path to `marker_single` executable |
| `marker_repo_root` | Yes | -- | Marker repository root directory |
| `hf_home` | Yes | -- | Hugging Face model cache directory |
| `pythonw_path` | No | -- | `pythonw.exe` path for the Windows scheduled task supervisor; required if you use auto-start at login |
| `model_cache_dir` | No | -- | Optional cache directory exported as `MODEL_CACHE_DIR` by the supervisor |
| `torch_device` | No | `cuda` | PyTorch device: `cuda` or `cpu` |
| `output_format` | No | `markdown` | Marker output format |
| `force_ocr` | No | `false` | Force OCR even on machine-readable PDFs |
| `disable_image_extraction` | No | `false` | Skip image extraction from PDFs |
| `disable_multiprocessing` | No | `false` | Disable Marker multiprocessing |
| `paginate_output` | No | `false` | Add page breaks to Markdown |
| `compute_sha256` | No | `false` | Use SHA256 (in addition to size/mtime) for change detection |
| `log_level` | No | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `watch_debounce_seconds` | No | `8` | Wait time before processing a queued file event |
| `watch_stable_checks` | No | `3` | Number of size/mtime stability checks |
| `watch_stable_interval_seconds` | No | `2` | Seconds between stability checks |
| `watch_rescan_interval_seconds` | No | `60` | Periodic full rescan interval in seconds (`0` disables periodic rescans) |
| `watch_initial_scan` | No | `true` | Scan for unprocessed PDFs on watcher startup |

## State Management

### Manifest (`state/manifest.json`)

The manifest tracks every PDF's conversion status:

- **`success`** -- converted successfully; stores fingerprint (size + mtime), output paths, metadata
- **`failed`** -- conversion failed; stores error message and timestamp

Change detection compares the current file's `size` and `mtime_ns` (and optionally `sha256`) against the manifest entry. If anything differs, the PDF is re-converted.

### Failed PDF Report (`logs/failed_pdfs.txt`)

A human-readable report of all failed conversions, updated after each batch run. Includes source path, relative key, timestamp, and error details.

## Full Library Rebuild

If you need a clean rebuild of the entire Markdown library:

```powershell
# 1. Stop any running watcher
# 2. Backup old output (rename, don't delete)
cd G:\YourDrive\zoterotomarkdown
ren markdown markdown_old
ren state state_old
ren marker_raw marker_raw_old

# 3. Test with a small batch
cd C:\path\to\this\project\pdf_to_markdown
python run_once.py --limit 2 --force

# 4. Verify output, then full rebuild
python run_once.py --force

# 5. After verifying, delete backups
# cd G:\YourDrive\zoterotomarkdown
# rmdir /s markdown_old state_old marker_raw_old
```

## Project Files

```
zotero-gdrive-markdown-project/
  pdf_to_markdown/
    __init__.py
    common.py                        # shared utilities, path helpers, safe deletion
    pipeline.py                      # core conversion logic, manifest, artifact management
    run_once.py                      # CLI: manual/batch conversion and cleanup
    watch_folder_resilient.py        # CLI: continuous folder watcher
    settings.json                    # machine-specific config (gitignored)
    settings.example.json            # config template
  backfill_supporting.py             # CLI: backfill missing supporting PDFs
  zotero_pdf_watch_supervisor.ps1    # PowerShell watcher supervisor
  install_or_update_task.ps1         # install Windows scheduled task
  remove_task.ps1                    # remove Windows scheduled task
  requirements.txt                   # Python dependencies
  .gitignore
  LICENSE
  README.zh-CN.markdown
  README.markdown
```

## Notes

- `settings.json` is machine-specific and gitignored. Commit `settings.example.json` instead.
- Marker and its model caches are installed separately and should not be stored inside this repository.
- The watcher handles Google Drive's streaming file access -- stability checks ensure files are fully synced before conversion.
- All delete operations are bounded to `markdown/` and `marker_raw/` roots to prevent accidental filesystem damage.

## License

MIT. See `LICENSE`.
