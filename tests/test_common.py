import json
from pathlib import Path

import pytest

from paper_to_markdown.common import load_config, logs_root, setup_logger


def write_settings(tmp_path: Path, payload: dict) -> Path:
    config_path = tmp_path / "settings.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return config_path


def test_load_config_defaults_run_mode_to_all_in_one(tmp_path: Path):
    config_path = write_settings(
        tmp_path,
        {
            "input_root": str(tmp_path / "input"),
            "output_root": str(tmp_path / "output"),
            "hf_home": str(tmp_path / "hf"),
            "marker_cli": "marker_single",
        },
    )

    config = load_config(str(config_path))

    assert config["run_mode"] == "all-in-one"
    assert config["marker_cli"] == "marker_single"
    assert config["hf_home"] == str((tmp_path / "hf").resolve())


def test_controller_mode_skips_marker_requirements_and_can_setup_logger(tmp_path: Path):
    config_path = write_settings(
        tmp_path,
        {
            "run_mode": "controller",
            "input_root": str(tmp_path / "input"),
            "output_root": str(tmp_path / "output"),
        },
    )

    config = load_config(str(config_path))
    logger = setup_logger(config, logger_name="paper_to_markdown.test_common")

    assert config["run_mode"] == "controller"
    assert "hf_home" not in config
    assert "marker_cli" not in config
    assert logs_root(config).exists()
    assert logger.name == "paper_to_markdown.test_common"


def test_load_config_rejects_invalid_run_mode(tmp_path: Path):
    config_path = write_settings(
        tmp_path,
        {
            "run_mode": "sidecar",
            "input_root": str(tmp_path / "input"),
            "output_root": str(tmp_path / "output"),
            "hf_home": str(tmp_path / "hf"),
            "marker_cli": "marker_single",
        },
    )

    with pytest.raises(ValueError, match="Invalid run_mode"):
        load_config(str(config_path))
