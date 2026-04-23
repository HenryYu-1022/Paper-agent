# Zotero Paper Agent

Zotero 7 plugin for the local `paper_to_markdown` daemon.

## Build

```bash
./scripts/build.sh
```

Install `zotero-paper-agent.xpi` in Zotero via Tools -> Plugins/Add-ons -> Install Add-on From File.

## Preferences

Set these in Zotero Preferences -> Zotero Paper Agent:

- `daemon.py`: absolute path to this repo's `paper_to_markdown/daemon.py`
- `Python`: Python executable with `requirements.txt` installed
- `PDF root`: same as `input_root`
- `Output root`: same as `output_root`
- `Marker`: `marker_single` or an absolute marker executable path
- `HF cache`: Hugging Face cache directory
- `Device`: `mps`, `cuda`, or `cpu`
- `Idle timeout`: daemon idle seconds before exit; `0` disables auto-exit

The plugin listens for Zotero attachment add/modify/trash/delete notifications, optionally renames PDFs using Zotero's built-in file-renaming API, and sends JSON-line commands to the Python daemon over stdin/stdout.

