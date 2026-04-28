import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import monitor
from paper_to_markdown.common import build_frontmatter, conversion_lock_path, conversion_status_path, logs_root


def make_config(tmp_path: Path) -> dict:
    return {
        "input_root": str(tmp_path / "input"),
        "output_root": str(tmp_path / "output"),
        "hf_home": str(tmp_path / "hf"),
        "marker_cli": "marker_single",
        "compute_sha256": True,
    }


def write_pdf(input_root: Path, rel_path: str) -> None:
    pdf_path = input_root / rel_path
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF")


def write_indexed_markdown(output_root: Path, rel_path: str, status: str) -> None:
    md_path = output_root / "markdown" / Path(rel_path).with_suffix("") / (Path(rel_path).stem + ".md")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(
        build_frontmatter(
            {
                "source_relpath": rel_path,
                "source_filename": Path(rel_path).name,
                "conversion_status": status,
                "document_role": "main",
            }
        )
        + "## Full Text\n\nBody\n",
        encoding="utf-8",
    )


def test_eta_tracker_uses_processed_growth_when_remaining_is_flat(monkeypatch):
    timestamps = iter([0.0, 10.0, 20.0])
    monkeypatch.setattr(monitor.time, "time", lambda: next(timestamps))

    tracker = monitor.EtaTracker()

    assert tracker.estimate(remaining=5, processed=10) is None
    assert tracker.estimate(remaining=5, processed=12) == "25s"
    assert tracker.estimate(remaining=4, processed=12) == "20s"


def test_process_is_running_checks_windows_process_handle(monkeypatch):
    class FakeKernel32:
        def __init__(self) -> None:
            self.closed_handles: list[int] = []

        def OpenProcess(self, _access, _inherit, pid):
            return 100 if pid == 1234 else 0

        def GetLastError(self):
            return monitor.ERROR_INVALID_PARAMETER

        def GetExitCodeProcess(self, _handle, exit_code_ptr):
            exit_code_ptr._obj.value = monitor.STILL_ACTIVE
            return 1

        def CloseHandle(self, handle):
            self.closed_handles.append(handle)
            return 1

    fake_kernel32 = FakeKernel32()
    monkeypatch.setattr(monitor.os, "name", "nt")
    monkeypatch.setattr(monitor.ctypes, "windll", SimpleNamespace(kernel32=fake_kernel32), raising=False)

    assert monitor.process_is_running(1234) is True
    assert fake_kernel32.closed_handles == [100]
    assert monitor.process_is_running(9999) is False


def test_parser_converts_by_default_and_can_disable():
    parser = monitor.build_parser()

    assert parser.parse_args([]).convert is True
    assert parser.parse_args(["--no-convert"]).convert is False


def test_launch_background_monitor_uses_pythonw_and_hides_window(tmp_path: Path, monkeypatch):
    pythonw_path = tmp_path / "pythonw.exe"
    pythonw_path.write_text("", encoding="utf-8")
    config_path = tmp_path / "settings.json"
    config_path.write_text(
        json.dumps(
            {
                "input_root": str(tmp_path / "input"),
                "output_root": str(tmp_path / "output"),
                "hf_home": str(tmp_path / "hf"),
                "marker_cli": "marker_single",
                "pythonw_path": str(pythonw_path),
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 2468

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    class FakeStartupInfo:
        def __init__(self) -> None:
            self.dwFlags = 0
            self.wShowWindow = None

    monkeypatch.setattr(monitor.os, "name", "nt")
    monkeypatch.setattr(monitor.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(monitor.subprocess, "STARTUPINFO", FakeStartupInfo, raising=False)
    monkeypatch.setattr(monitor.subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(monitor.subprocess, "SW_HIDE", 0, raising=False)
    monkeypatch.setattr(monitor.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    args = monitor.build_parser().parse_args(
        ["--config", str(config_path), "--background", "--watch", "--interval", "60", "--apply"]
    )

    assert monitor.launch_background_monitor(args) == 2468
    command = captured["command"]
    kwargs = captured["kwargs"]
    assert command[0] == str(pythonw_path)
    assert "--background" not in command
    assert "--watch" in command
    assert "--apply" in command
    assert kwargs["creationflags"] == 0x08000000
    assert (tmp_path / "output" / "logs" / "monitor_background.log").exists()


def test_load_index_summary_splits_pending_from_failed(tmp_path: Path):
    config = make_config(tmp_path)
    input_root = Path(config["input_root"])
    output_root = Path(config["output_root"])

    write_pdf(input_root, "A/Done.pdf")
    write_pdf(input_root, "A/Failed.pdf")
    write_pdf(input_root, "A/Pending.pdf")
    write_indexed_markdown(output_root, "A/Done.pdf", "success")
    write_indexed_markdown(output_root, "A/Failed.pdf", "failed")

    summary = monitor.load_index_summary(config)

    assert summary["matched_success"] == 1
    assert summary["matched_failed"] == 1
    assert summary["needs_conversion"] == ["A/Failed.pdf", "A/Pending.pdf"]
    assert summary["pending_conversion"] == ["A/Pending.pdf"]


def test_load_current_conversion_uses_running_lock_when_status_missing(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    lock_path = conversion_lock_path(config)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps(
            {
                "pid": 1234,
                "owner": "single:Paper.pdf",
                "started_at_epoch": 100.0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(monitor, "process_is_running", lambda pid: True)
    monkeypatch.setattr(monitor.time, "time", lambda: 165.0)

    current = monitor.load_current_conversion(config)

    assert current is not None
    assert current["lock_only"] is True
    assert current["source_relpath"] == "single:Paper.pdf"
    assert current["elapsed"] == "1m 5s"
    assert monitor.current_conversion_is_active(current) is True


def test_historical_eta_uses_recent_log_average(tmp_path: Path):
    config = make_config(tmp_path)
    logs_root(config).mkdir(parents=True, exist_ok=True)
    (logs_root(config) / "app.log").write_text(
        "\n".join(
            [
                "2026-04-27 10:00:00 | INFO | Starting marker conversion: A.pdf",
                "2026-04-27 10:02:00 | INFO | Conversion completed: A.pdf -> A.md",
                "2026-04-27 10:03:00 | INFO | Starting marker conversion: B.pdf",
                "2026-04-27 10:05:00 | INFO | Conversion completed: B.pdf -> B.md",
            ]
        ),
        encoding="utf-8",
    )

    assert monitor.historical_eta_text(config, 2, None) == "4m 0s (avg 2m 0s/PDF, 2 recent)"


def test_build_report_does_not_show_unknown_for_failed_only(tmp_path: Path):
    config = make_config(tmp_path)
    input_root = Path(config["input_root"])
    output_root = Path(config["output_root"])
    config_path = tmp_path / "settings.json"

    write_pdf(input_root, "A/Failed.pdf")
    write_indexed_markdown(output_root, "A/Failed.pdf", "failed")
    config_path.write_text(
        json.dumps(
            {
                "input_root": str(input_root),
                "output_root": str(output_root),
                "hf_home": str(tmp_path / "hf"),
                "marker_cli": "marker_single",
            }
        ),
        encoding="utf-8",
    )

    report = monitor.build_report(str(config_path))

    assert "Estimated time remaining: unknown" not in report
    assert "Estimated time remaining: 0s (failed PDFs need retry)" in report


def test_build_report_shows_current_pdf_and_elapsed_time(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    input_root = Path(config["input_root"])
    output_root = Path(config["output_root"])
    config_path = tmp_path / "settings.json"

    write_pdf(input_root, "A/Running.pdf")
    output_root.mkdir(parents=True, exist_ok=True)
    status_path = conversion_status_path(config)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(
        json.dumps(
            {
                "status": "running",
                "pid": os.getpid(),
                "source_relpath": "A/Running.pdf",
                "source_pdf": str(input_root / "A/Running.pdf"),
                "source_filename": "Running.pdf",
                "started_at_epoch": 100.0,
            }
        ),
        encoding="utf-8",
    )
    config_path.write_text(
        json.dumps(
            {
                "input_root": str(input_root),
                "output_root": str(output_root),
                "hf_home": str(tmp_path / "hf"),
                "marker_cli": "marker_single",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(monitor.time, "time", lambda: 165.0)

    report = monitor.build_report(str(config_path))

    assert "Current PDF: A/Running.pdf" in report
    assert "Current PDF index state: pending" in report
    assert "Current PDF elapsed: 1m 5s" in report


def _silent_logger() -> logging.Logger:
    logger = logging.getLogger("paper_to_markdown.tests.monitor")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


def test_apply_orphan_cleanup_archives_when_pdf_deleted(tmp_path: Path):
    config = make_config(tmp_path)
    config["archive_before_delete"] = True
    input_root = Path(config["input_root"])
    output_root = Path(config["output_root"])

    write_pdf(input_root, "A/Kept.pdf")
    write_pdf(input_root, "A/Gone.pdf")
    write_indexed_markdown(output_root, "A/Kept.pdf", "success")
    write_indexed_markdown(output_root, "A/Gone.pdf", "success")
    (input_root / "A/Gone.pdf").unlink()

    bundle = output_root / "markdown" / "A" / "Gone"
    assert bundle.exists()

    result = monitor.apply_orphan_cleanup(config, None, _silent_logger())

    assert result["removed"] == ["A/Gone.pdf"]
    assert result["errors"] == []
    assert not bundle.exists()
    archive_root = output_root / "archive"
    assert archive_root.exists()
    archived_md = list(archive_root.rglob("Gone.md"))
    assert archived_md, "expected archived markdown to be moved under archive/"
    # Kept bundle is untouched
    assert (output_root / "markdown" / "A" / "Kept" / "Kept.md").exists()


def test_apply_orphan_cleanup_skips_current_conversion(tmp_path: Path):
    config = make_config(tmp_path)
    config["archive_before_delete"] = True
    input_root = Path(config["input_root"])
    output_root = Path(config["output_root"])

    write_pdf(input_root, "A/Running.pdf")
    write_indexed_markdown(output_root, "A/Running.pdf", "success")
    # Simulate the source PDF temporarily missing while marker is reading it.
    (input_root / "A/Running.pdf").unlink()

    bundle = output_root / "markdown" / "A" / "Running"
    assert bundle.exists()

    current_conversion = {"source_relpath": "A/Running.pdf"}
    result = monitor.apply_orphan_cleanup(config, current_conversion, _silent_logger())

    assert result["removed"] == []
    assert result["skipped"] == ["A/Running.pdf"]
    assert bundle.exists(), "bundle for currently-converting PDF must not be touched"


def test_apply_pending_conversions_invokes_converter_for_each_pending(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    input_root = Path(config["input_root"])

    write_pdf(input_root, "A/Failed.pdf")
    write_pdf(input_root, "A/Pending1.pdf")
    write_pdf(input_root, "A/Pending2.pdf")

    called: list[Path] = []

    def fake_convert(pdf_path, config_path=None, force_reconvert=False):
        called.append(Path(pdf_path))
        return Path(pdf_path).with_suffix(".md")

    monkeypatch.setattr(monitor, "convert_one_pdf_with_retries", fake_convert)

    summary = {
        "needs_conversion": ["A/Failed.pdf", "A/Pending1.pdf", "A/Pending2.pdf"],
        "pending_conversion": ["A/Pending1.pdf", "A/Pending2.pdf"],
    }
    result = monitor.apply_pending_conversions(
        config, summary, config_path=None, current_conversion=None, logger=_silent_logger()
    )

    assert {p.name for p in called} == {"Failed.pdf", "Pending1.pdf", "Pending2.pdf"}
    assert sorted(result["converted"]) == ["A/Failed.pdf", "A/Pending1.pdf", "A/Pending2.pdf"]
    assert result["errors"] == []
    assert result["skipped_running"] is False


def test_apply_pending_conversions_defers_when_running(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)

    called: list[Path] = []

    def fake_convert(pdf_path, config_path=None, force_reconvert=False):
        called.append(Path(pdf_path))
        return Path(pdf_path).with_suffix(".md")

    monkeypatch.setattr(monitor, "convert_one_pdf_with_retries", fake_convert)

    summary = {"pending_conversion": ["A/Pending.pdf"]}
    current = {"status": "running", "pid": os.getpid(), "source_relpath": "A/Other.pdf"}
    result = monitor.apply_pending_conversions(
        config, summary, config_path=None, current_conversion=current, logger=_silent_logger()
    )

    assert called == []
    assert result["converted"] == []
    assert result["skipped_running"] is True


def test_apply_pending_conversions_skips_missing_pdf(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    input_root = Path(config["input_root"])
    input_root.mkdir(parents=True, exist_ok=True)

    called: list[Path] = []

    def fake_convert(pdf_path, config_path=None, force_reconvert=False):
        called.append(Path(pdf_path))
        return Path(pdf_path).with_suffix(".md")

    monkeypatch.setattr(monitor, "convert_one_pdf_with_retries", fake_convert)

    # PDF listed in pending but not actually on disk (e.g., deleted between scans)
    summary = {"pending_conversion": ["A/Ghost.pdf"]}
    result = monitor.apply_pending_conversions(
        config, summary, config_path=None, current_conversion=None, logger=_silent_logger()
    )

    assert called == []
    assert result["converted"] == []
    assert result["errors"] == []


def test_apply_orphan_cleanup_deletes_when_archive_disabled(tmp_path: Path):
    config = make_config(tmp_path)
    config["archive_before_delete"] = False
    input_root = Path(config["input_root"])
    output_root = Path(config["output_root"])

    write_pdf(input_root, "Solo.pdf")
    write_indexed_markdown(output_root, "Solo.pdf", "success")
    (input_root / "Solo.pdf").unlink()

    bundle = output_root / "markdown" / "Solo"
    assert bundle.exists()

    result = monitor.apply_orphan_cleanup(config, None, _silent_logger())

    assert result["removed"] == ["Solo.pdf"]
    assert not bundle.exists()
    # No archive directory created when archive_before_delete is false
    assert not (output_root / "archive").exists()
