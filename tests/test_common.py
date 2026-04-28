import json
import logging
from pathlib import Path

from paper_to_markdown.common import load_config, logs_root, setup_logger


def write_settings(tmp_path: Path, payload: dict) -> Path:
    config_path = tmp_path / "settings.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return config_path


def test_load_config_resolves_conversion_paths(tmp_path: Path):
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

    assert config["marker_cli"] == "marker_single"
    assert config["hf_home"] == str((tmp_path / "hf").resolve())


def test_load_config_ignores_zotero_fields_in_runner_mode(tmp_path: Path):
    config_path = write_settings(
        tmp_path,
        {
            "run_mode": "runner",
            "input_root": str(tmp_path / "input"),
            "output_root": str(tmp_path / "output"),
            "hf_home": str(tmp_path / "hf"),
            "marker_cli": "marker_single",
            "zotero_db_path": str(tmp_path / "zotero.sqlite"),
            "collection_mirror_mode": "copy",
            "collection_views_root": str(tmp_path / "views"),
            "zotero_markdown_root": str(tmp_path / "zotero_markdown"),
            "zotero_sync_interval_seconds": 60,
        },
    )

    config = load_config(str(config_path))

    assert "zotero_db_path" not in config
    assert "collection_mirror_mode" not in config
    assert "collection_views_root" not in config
    assert "zotero_markdown_root" not in config
    assert "zotero_sync_interval_seconds" not in config


def test_setup_logger_creates_logs_root(tmp_path: Path):
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
    logger = setup_logger(config, logger_name="paper_to_markdown.test_common")

    assert logs_root(config).exists()
    assert logger.name == "paper_to_markdown.test_common"


def test_setup_logger_can_skip_console_handler(tmp_path: Path):
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
    logger = setup_logger(
        config,
        logger_name="paper_to_markdown.test_common.no_console",
        console=False,
    )

    console_handlers = [
        handler for handler in logger.handlers
        if type(handler) is logging.StreamHandler
    ]
    assert console_handlers == []
