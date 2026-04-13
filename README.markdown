# Paper-agent

Chinese version: [README.zh-CN.markdown](README.zh-CN.markdown)

---

## Overview

`paper-agent` converts a PDF library into a Markdown library that AI coding agents can search, read, and cross-reference.

It is designed for Zotero-centered workflows, but it also works with any plain folder of PDFs.

The project has two independent inputs:

- The PDF tree on disk, configured by `input_root`
- The optional Zotero collection hierarchy from `zotero.sqlite`

The physical Markdown output always follows the relative folder structure under `input_root`. Zotero collections are optional metadata and optional extra mirrors; they do not choose the primary physical bundle location.

## Related Project: MarkItDown

[MarkItDown](https://github.com/microsoft/markitdown) is a separate Microsoft project focused on converting many file types to Markdown, not just PDFs.

Based on the current `paper-agent` codebase:

- `paper-agent` does not call MarkItDown directly
- `paper-agent` is still built around Marker as the actual converter
- The manifest logic, raw-output handling, supporting placement, duplicate-bundle normalization, and watcher flow all assume the current Marker-based pipeline

So the realistic way to use both projects together today is a manual, complementary workflow:

- Use `paper-agent` for the PDF-library workflow: batch conversion, watcher mode, manifest management, supporting placement, duplicate Markdown normalization, and Zotero collection metadata or mirrors
- Use MarkItDown separately when you want ad hoc Markdown conversion for other document types outside this pipeline

Work estimate under the current code:

- Low effort: mention MarkItDown in the docs and use it manually alongside `paper-agent`
- Higher effort: replace Marker or add a real MarkItDown conversion path inside `paper-agent`; that is not implemented in the current repository and is not claimed by this README

## What The Project Does

- Converts PDFs to Markdown with [Marker](https://github.com/datalab-to/marker)
- Writes YAML frontmatter into every generated Markdown file
- Keeps the physical output tree aligned with the `input_root` tree
- Places the main paper Markdown and all supporting Markdown files in the same paper folder
- Renames supporting outputs to `supporting.md`, `supporting_2.md`, `supporting_assets/`, and so on
- Detects supporting PDFs by filename heuristics such as `_1`, `_2`, `si`, `supporting`, `supplementary information`, and similar labels
- Detects duplicate main-paper exports such as `Paper.pdf`, `Paper 2.pdf`, `Paper 3.pdf`
- Deduplicates near-duplicate Markdown outputs for both main papers and supporting files, keeping one canonical bundle per paper
- Tracks conversion results in `state/manifest.json`
- Skips unchanged PDFs during normal runs
- Cleans up Markdown artifacts when source PDFs disappear
- Retries failed conversions automatically
- Optionally reads Zotero collections from `zotero.sqlite`, writes `zotero_collections` into frontmatter, and creates extra collection mirrors as `symlink` or `copy`
- Provides a one-time repair script for normalizing an already-generated Markdown library

## Output Model

For a source tree like:

```text
input_root/
  Topic A/
    Paper.pdf
    Paper 3.pdf
    Paper Supporting Information.pdf
  Topic B/
    Another Paper.pdf
```

the normalized Markdown layout is:

```text
output_root/
  markdown/
    Topic A/
      Paper/
        Paper.md
        supporting.md
        supporting_assets/
    Topic B/
      Another Paper/
        Another Paper.md
  marker_raw/
  state/
    manifest.json
    collection_state.json
  logs/
    app.log
    failed_pdfs.txt
```

Important behavior:

- `Paper 3.pdf` does not need its own Markdown folder if its converted Markdown is effectively the same as `Paper.pdf`
- `Paper Supporting Information.pdf` is stored inside `Topic A/Paper/`, not in a separate sibling bundle
- The paper bundle is the unit of organization: one paper, one folder

If Zotero collection mirroring is enabled, extra collection paths may also appear under `output_root/markdown/` as mirrors of the same canonical paper bundle:

- `symlink` mode saves space
- `copy` mode creates normal copied directories

## Start Here

| Goal | File to start | Run from | Long-running? |
|-----|-----|-----|-----|
| Convert the whole library once | `paper_to_markdown/run_once.py` | `paper_to_markdown/` | No |
| Watch for new or changed PDFs | `paper_to_markdown/watch_folder_resilient.py` | `paper_to_markdown/` | Yes |
| Sync Zotero collections into frontmatter and mirror folders | `paper_to_markdown/sync_zotero_collections.py` | `paper_to_markdown/` | Optional |
| Repair an existing Markdown library in place | `normalize_existing_markdown_library.py` | repo root | No |
| Backfill missing supporting Markdown files | `backfill_supporting.py` | repo root | No |
| Monitor batch progress and ETA | `monitor_conversion_progress.py` | repo root | No |

## Python File Guide

### Root-Level Python Files

| File | Role | Typical use |
|-----|-----|-----|
| `normalize_existing_markdown_library.py` | One-time migration tool for an already-generated Markdown library. Moves old standalone supporting bundles into the main paper folder, merges duplicate main bundles, deduplicates supporting Markdown, and refreshes manifest entries. | Run once after upgrading the pipeline logic, or when you want to normalize an old library without reconverting PDFs. |
| `backfill_supporting.py` | Scans the PDF library for supporting PDFs whose normalized `supporting*.md` output is missing, then optionally reconverts only those PDFs. | Repair incomplete supporting bundles. |
| `monitor_conversion_progress.py` | Reads `logs/app.log` and `state/manifest.json` to show batch progress, current PDF, historical average conversion time, and ETA. | Watch long conversions without opening log files manually. |

### `paper_to_markdown/` Python Files

| File | Role | Typical use |
|-----|-----|-----|
| `paper_to_markdown/__init__.py` | Marks `paper_to_markdown` as a Python package. | Imported implicitly; no direct action. |
| `paper_to_markdown/common.py` | Shared utilities for config loading, path resolution, frontmatter helpers, supporting/main duplicate filename grouping, bundle naming, and common filesystem helpers. | Internal module imported by the runnable scripts. |
| `paper_to_markdown/pipeline.py` | Core conversion engine. Runs Marker, writes Markdown bundles, applies supporting placement rules, deduplicates near-duplicate Markdown, updates the manifest, manages cleanup, and exposes the main conversion functions. | Internal engine used by `run_once.py`, the watcher, and repair tools. |
| `paper_to_markdown/run_once.py` | Main manual CLI entrypoint. Supports full-library conversion, single-PDF conversion, forced reconvert, and orphan cleanup. | Use this for normal batch runs. |
| `paper_to_markdown/watch_folder_resilient.py` | Filesystem watcher for `input_root`. Debounces events, waits for file stability, converts new/changed PDFs, and removes artifacts for deleted PDFs. | Use this for always-on background processing. |
| `paper_to_markdown/sync_zotero_collections.py` | Syncs Zotero collection assignments into Markdown frontmatter and collection mirror folders using the manifest as the source of converted files. | Run once or as a daemon when collection assignments change in Zotero. |
| `paper_to_markdown/zotero_collections.py` | Read-only Zotero SQLite adapter. Resolves collection paths and maps PDF filenames to collection lists. | Internal module used by the pipeline and collection sync script. |

## Non-Python Startup Helpers

These are not Python files, but they are part of the runtime workflow:

| File | Role |
|-----|-----|
| `watch_autostart.sh` | macOS entrypoint for install/status/remove of the watcher LaunchAgent |
| `watch_autostart.ps1` | Windows entrypoint for install/status/remove of the watcher Scheduled Task |
| `autostart/paper_agent_watch_supervisor.sh` | macOS supervisor that keeps the watcher alive |
| `autostart/paper_agent_watch_supervisor.ps1` | Windows supervisor that keeps the watcher alive |
| `autostart/install_or_update_launch_agent.sh` | Installs or updates the macOS LaunchAgent |
| `autostart/remove_launch_agent.sh` | Removes the macOS LaunchAgent |
| `autostart/install_or_update_watch_task.ps1` | Installs or updates the Windows Scheduled Task |
| `autostart/remove_watch_task.ps1` | Removes the Windows Scheduled Task |

## Main Rules The Pipeline Follows

### 1. Physical Folder Rule

The real bundle location is derived from the PDF path under `input_root`.

If a PDF lives at:

```text
input_root/My Library/Thesis/Paper.pdf
```

its canonical Markdown bundle lives at:

```text
output_root/markdown/My Library/Thesis/Paper/
```

### 2. Supporting Placement Rule

Supporting PDFs are not stored in separate paper folders anymore.

If the pipeline identifies a PDF as supporting material, it writes it into the main paper bundle as:

- `supporting.md`
- `supporting_2.md`
- `supporting_assets/`
- `supporting_2_assets/`

### 3. Duplicate Main-Paper Rule

If a directory contains files like:

- `Paper.pdf`
- `Paper 2.pdf`
- `Paper 3.pdf`

the pipeline groups them as candidate duplicates. If the converted Markdown bodies are near-duplicates, it keeps one canonical main bundle and repoints the others to the same canonical Markdown in the manifest.

### 4. Existing-Library Repair Rule

`normalize_existing_markdown_library.py` uses the same supporting and duplicate heuristics to repair an already-generated Markdown library in place. It is for old output libraries produced before the newer normalization logic existed.

## Quick Commands

### Convert Once

```bash
cd paper_to_markdown

# Convert all PDFs under input_root
python3 run_once.py

# Convert one PDF
python3 run_once.py --path "/path/to/input_root/subdir/Paper.pdf"

# Force reconvert
python3 run_once.py --force

# Test on a small batch
python3 run_once.py --limit 5

# Delete artifacts for source PDFs that no longer exist
python3 run_once.py --cleanup
```

### Watch For Changes

```bash
cd paper_to_markdown
python3 watch_folder_resilient.py
```

### Sync Zotero Collections

```bash
cd paper_to_markdown

# One-shot sync
python3 sync_zotero_collections.py --once

# Daemon mode
python3 sync_zotero_collections.py

# Custom interval
python3 sync_zotero_collections.py --interval 30
```

### Repair Missing Supporting Files

```bash
python3 backfill_supporting.py
python3 backfill_supporting.py --apply
python3 backfill_supporting.py --limit 10
```

### Normalize An Existing Markdown Library

```bash
python3 normalize_existing_markdown_library.py
python3 normalize_existing_markdown_library.py --config paper_to_markdown/settings.json
python3 normalize_existing_markdown_library.py --limit 20
```

### Monitor Progress

```bash
python3 monitor_conversion_progress.py
python3 monitor_conversion_progress.py --watch --interval 30
```

## Configuration Reference

Runtime config file: `paper_to_markdown/settings.json`

| Key | Meaning |
|-----|-----|
| `input_root` | Root of the PDF tree |
| `output_root` | Root of generated Markdown, state, logs, and Marker raw output |
| `marker_cli` | Marker executable, usually `marker_single` or an absolute path |
| `hf_home` | Hugging Face cache directory |
| `python_path` | Python path used by macOS LaunchAgent and as a general explicit interpreter path |
| `pythonw_path` | Windows-only preferred background interpreter |
| `marker_repo_root` | Optional working directory for Marker |
| `model_cache_dir` | Optional model cache environment variable for the supervisors |
| `torch_device` | Usually `cuda`, `mps`, or `cpu` |
| `force_ocr` | Whether Marker should force OCR |
| `disable_image_extraction` | Disable image extraction if needed |
| `disable_multiprocessing` | Disable Marker multiprocessing if needed |
| `paginate_output` | Add page markers in Markdown |
| `compute_sha256` | Include SHA256 in change detection |
| `log_level` | Logging level |
| `watch_debounce_seconds` | Debounce delay before the watcher processes a changed file |
| `watch_stable_checks` | Number of file-stability checks before conversion |
| `watch_stable_interval_seconds` | Delay between stability checks |
| `watch_rescan_interval_seconds` | Periodic full rescan interval; `0` disables it |
| `watch_initial_scan` | Whether the watcher queues existing PDFs on startup |
| `zotero_db_path` | Path to `zotero.sqlite` |
| `collection_mirror_mode` | `symlink` or `copy` for extra Zotero collection mirrors |
| `zotero_sync_interval_seconds` | Polling interval used by the collection sync daemon |

## Zotero Collection Behavior

If `zotero_db_path` is configured:

- The pipeline writes `zotero_collections` into frontmatter
- The physical output tree still follows `input_root`
- Extra Zotero collection locations are created as mirrors of the canonical bundle
- Those mirrors can be `symlink` or `copy`, depending on `collection_mirror_mode`

This distinction matters:

- `input_root` decides where the real bundle lives
- Zotero collections only add metadata and optional extra mirrors

## Notes And Caveats

- Keep `output_root` outside `input_root`
- A GPU is strongly recommended for Marker
- If you use collection syncing, PDF-to-collection mapping is filename-based; unique PDF filenames are safest
- `copy` mirror mode is simpler on Windows but duplicates storage
- `symlink` mirror mode saves space but may need elevated privileges depending on your platform and settings

## License

MIT. See `LICENSE`.
