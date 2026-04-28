import json
import subprocess
import sys
from pathlib import Path

import pytest

from paper_to_markdown import convert
from paper_to_markdown import pipeline
from paper_to_markdown.common import conversion_lock_path
from paper_to_markdown.pipeline import ConversionLock


def write_settings(tmp_path: Path, payload: dict) -> Path:
    config_path = tmp_path / "settings.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return config_path


def test_convert_main_runs_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    input_root = tmp_path / "input"
    input_root.mkdir()
    output_root = tmp_path / "output"

    config_path = write_settings(
        tmp_path,
        {
            "input_root": str(input_root),
            "output_root": str(output_root),
            "hf_home": str(tmp_path / "hf"),
            "marker_cli": "marker_single",
        },
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["convert.py", "--config", str(config_path), "--cleanup"],
    )

    convert.main()

    captured = capsys.readouterr()
    assert "{'cleaned': 0, 'remaining': 0}" in captured.out


def test_convert_script_can_be_run_directly_for_help():
    script_path = Path(__file__).resolve().parents[1] / "paper_to_markdown" / "convert.py"

    result = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Convert paper PDFs to markdown" in result.stdout


def test_conversion_lock_blocks_second_conversion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = {"output_root": str(tmp_path / "output")}
    lock_path = conversion_lock_path(config)
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps({"owner": "first", "pid": 12345, "token": "other-process"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "process_is_running", lambda pid: True)

    with pytest.raises(RuntimeError) as excinfo:
        with ConversionLock(config, owner="second"):
            pass

    assert "Another conversion appears to be running" in str(excinfo.value)
    assert lock_path.exists()


def test_conversion_lock_replaces_stale_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = {"output_root": str(tmp_path / "output")}
    lock_path = conversion_lock_path(config)
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps({"owner": "dead", "pid": 12345, "token": "dead-process"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "process_is_running", lambda pid: False)

    with ConversionLock(config, owner="second"):
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["owner"] == "second"
        assert payload["token"] != "dead-process"

    assert not lock_path.exists()
