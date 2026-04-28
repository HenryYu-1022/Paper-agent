import logging
import tempfile
import unittest
from pathlib import Path

from paper_to_markdown.daemon import DaemonContext, handle_request


def make_config(tmp_path: Path) -> dict:
    return {
        "input_root": str(tmp_path / "input"),
        "output_root": str(tmp_path / "output"),
        "hf_home": str(tmp_path / "hf"),
        "marker_cli": "marker_single",
        "compute_sha256": True,
    }


def make_context(config: dict) -> DaemonContext:
    logger = logging.getLogger(f"paper_to_markdown.daemon.test.{id(config)}")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return DaemonContext(config=config, config_path=None, logger=logger)


class DaemonTests(unittest.TestCase):
    def test_ping_returns_json_serializable_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = make_context(make_config(Path(tmp)))

            response = handle_request({"id": "req-1", "command": "ping"}, context)

            self.assertEqual(
                response,
                {
                    "id": "req-1",
                    "ok": True,
                    "result": {"status": "pong"},
                },
            )

    def test_delete_orphan_uses_frontmatter_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = make_config(tmp_path)
            md_path = tmp_path / "output" / "markdown" / "AI" / "Paper" / "Paper.md"
            md_path.parent.mkdir(parents=True)
            md_path.write_text(
                """---
source_relpath: AI/Paper.pdf
source_filename: Paper.pdf
source_pdf_sha256: gone
document_role: main
---

## Full Text
Body
""",
                encoding="utf-8",
            )
            context = make_context(config)

            response = handle_request(
                {"id": "req-2", "command": "delete_orphan", "source_relpath": "AI/Paper.pdf"},
                context,
            )

            self.assertEqual(response["id"], "req-2")
            self.assertTrue(response["ok"])
            self.assertTrue(response["result"]["deleted"])
            self.assertFalse(md_path.exists())
            self.assertIsNone(context.index.get("AI/Paper.pdf"))


if __name__ == "__main__":
    unittest.main()
