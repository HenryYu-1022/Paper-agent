import json
import sys
from pathlib import Path

import pytest

from paper_to_markdown import convert


def write_settings(tmp_path: Path, payload: dict) -> Path:
    config_path = tmp_path / "settings.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return config_path


@pytest.mark.parametrize("extra_args", [[], ["--cleanup"]])
def test_convert_main_rejects_controller_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, extra_args: list[str]):
    input_root = tmp_path / "input"
    input_root.mkdir()
    output_root = tmp_path / "output"

    config_path = write_settings(
        tmp_path,
        {
            "run_mode": "controller",
            "input_root": str(input_root),
            "output_root": str(output_root),
        },
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["convert.py", "--config", str(config_path), *extra_args],
    )

    with pytest.raises(SystemExit) as excinfo:
        convert.main()

    captured = capsys.readouterr()
    assert excinfo.value.code == 1
    assert "controller mode" in captured.err
    assert "convert.py" in captured.err
