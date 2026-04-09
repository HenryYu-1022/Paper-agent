from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from paper_to_markdown.common import load_config, output_root


class LoadConfigTests(unittest.TestCase):
    def write_config(self, payload: dict[str, object]) -> Path:
        temp_dir = Path(tempfile.mkdtemp())
        config_path = temp_dir / "settings.json"
        config_path.write_text(json.dumps(payload), encoding="utf-8")
        return config_path

    def test_load_config_accepts_new_root_keys(self) -> None:
        temp_dir = Path(tempfile.mkdtemp())
        config_path = self.write_config(
            {
                "input_root": str(temp_dir / "input"),
                "output_root": str(temp_dir / "output"),
                "marker_cli": "marker_single",
                "hf_home": str(temp_dir / "hf"),
            }
        )

        config = load_config(str(config_path))

        self.assertEqual(config["marker_cli"], "marker_single")
        self.assertEqual(output_root(config), (temp_dir / "output").resolve())
        self.assertNotIn("marker_repo_root", config)

    def test_load_config_resolves_explicit_paths(self) -> None:
        temp_dir = Path(tempfile.mkdtemp())
        config_path = self.write_config(
            {
                "input_root": str(temp_dir / "input"),
                "output_root": str(temp_dir / "output"),
                "marker_cli": ".venv/bin/marker_single",
                "marker_repo_root": ".",
                "hf_home": str(temp_dir / "hf"),
            }
        )

        config = load_config(str(config_path))

        self.assertTrue(Path(config["marker_cli"]).is_absolute())
        self.assertTrue(Path(config["marker_repo_root"]).is_absolute())

    def test_load_config_rejects_legacy_root_keys(self) -> None:
        temp_dir = Path(tempfile.mkdtemp())
        config_path = self.write_config(
            {
                "source_dir": str(temp_dir / "input"),
                "work_root": str(temp_dir / "output"),
                "marker_cli": "marker_single",
                "hf_home": str(temp_dir / "hf"),
            }
        )

        with self.assertRaises(ValueError):
            load_config(str(config_path))


if __name__ == "__main__":
    unittest.main()
