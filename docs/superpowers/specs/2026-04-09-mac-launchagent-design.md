# macOS Watcher Parity Design

## Goal

Add a macOS auto-start path that mirrors the existing Windows experience: manual watcher runs still work, and users can also enable a resilient login-time background watcher.

## Scope

- Keep the existing Python conversion pipeline unchanged.
- Add macOS-specific supervisor and LaunchAgent scripts at the project root.
- Keep Windows scheduled-task behavior working.
- Update the starter config and both README files so macOS is the default documented setup.

## Design

### Cross-platform config

- Add a generic `python_path` setting for macOS LaunchAgent startup.
- Keep `pythonw_path` as an optional Windows-only override.
- Windows supervisor should use `pythonw_path` first and fall back to `python_path`.
- Keep `marker_cli` flexible so it can be an absolute path or a PATH-visible command name.
- Make `marker_repo_root` optional because the common pip-installed Marker workflow does not require a repo checkout as the working directory.

### macOS runtime model

- Add `paper_agent_watch_supervisor.sh` as the long-running supervisor.
- The supervisor reads `paper_to_markdown/settings.json`, validates required paths, starts `watch_folder_resilient.py`, and restarts it if it exits.
- The supervisor writes state and log files under `output_root/logs`, matching the Windows structure.

### macOS installation flow

- Add `install_or_update_launch_agent.sh` to generate and install a user LaunchAgent plist under `~/Library/LaunchAgents/`.
- Add `remove_launch_agent.sh` to unload and remove the plist.
- Installation should stop stale watcher/supervisor processes before bootstrapping the updated LaunchAgent.

### Documentation

- Change `paper_to_markdown/settings.example.json` to macOS-style paths.
- Update both README files to document:
  - macOS config and manual commands
  - macOS LaunchAgent setup
  - Windows scheduled task notes with `pythonw_path` fallback behavior
  - the new `python_path` config key

## Risks

- macOS `launchd` has a limited environment, so `python_path` should be an absolute path.
- `marker_cli` may need to be absolute for background jobs if `launchd` or Windows scheduled tasks do not inherit a usable PATH.

## Validation

- Shell syntax check for new macOS scripts.
- Python syntax check for modified Python files.
- Manual sanity check that README references and config keys match the implemented scripts.
