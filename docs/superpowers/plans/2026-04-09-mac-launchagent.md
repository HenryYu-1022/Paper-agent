# macOS Watcher Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add macOS LaunchAgent-based watcher startup that matches the existing Windows supervisor flow and document the macOS setup as the default example.

**Architecture:** Keep the Python conversion pipeline platform-agnostic and add macOS-specific shell entrypoints at the repository root. Reuse the existing `settings.json` model, adding a generic `python_path` while preserving Windows `pythonw_path` support.

**Tech Stack:** Python, PowerShell, zsh, launchd, JSON config, Markdown docs

---

### Task 1: Add macOS supervisor scripts

**Files:**
- Create: `paper_agent_watch_supervisor.sh`
- Create: `install_or_update_launch_agent.sh`
- Create: `remove_launch_agent.sh`

- [ ] Write the shell scripts that read `paper_to_markdown/settings.json`, manage watcher lifecycle, and install/remove a LaunchAgent plist.
- [ ] Validate quoting, absolute-path handling, and log/state file locations under `output_root/logs`.
- [ ] Run shell syntax checks for all new scripts.

### Task 2: Preserve Windows compatibility

**Files:**
- Modify: `paper_agent_watch_supervisor.ps1`

- [ ] Add `python_path` fallback support while preserving existing `pythonw_path` behavior.
- [ ] Keep current scheduled-task logic intact so Windows users do not need script changes.

### Task 3: Update starter config

**Files:**
- Modify: `paper_to_markdown/settings.example.json`

- [ ] Switch example paths to macOS-style values.
- [ ] Add `python_path` to the example config.
- [ ] Keep the remaining keys aligned with the existing runtime behavior.

### Task 4: Update documentation

**Files:**
- Modify: `README.zh-CN.markdown`
- Modify: `README.markdown`

- [ ] Document macOS config, manual runs, and LaunchAgent auto-start.
- [ ] Keep Windows scheduled-task instructions and explain `pythonw_path` fallback.
- [ ] Update feature lists, config tables, and project file listings for the new macOS scripts.

### Task 5: Verify and summarize

**Files:**
- None

- [ ] Run `zsh -n` on the new macOS scripts.
- [ ] Run `python3 -m py_compile` on modified Python files.
- [ ] Review the README/config references for consistency before reporting completion.
