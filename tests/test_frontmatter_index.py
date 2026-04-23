import tempfile
import unittest
from pathlib import Path

from paper_to_markdown.common import parse_frontmatter
from paper_to_markdown.frontmatter_index import FrontmatterIndex


def make_config(tmp_path: Path) -> dict:
    return {
        "input_root": str(tmp_path / "input"),
        "output_root": str(tmp_path / "output"),
        "hf_home": str(tmp_path / "hf"),
        "marker_cli": "marker_single",
        "compute_sha256": True,
    }


class FrontmatterIndexTests(unittest.TestCase):
    def test_scans_markdown_without_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = make_config(tmp_path)
            md_path = tmp_path / "output" / "markdown" / "AI" / "Paper" / "Paper.md"
            md_path.parent.mkdir(parents=True)
            md_path.write_text(
                """---
source_relpath: AI/Paper.pdf
source_filename: Paper.pdf
source_pdf_sha256: abc123
source_size: 10
source_mtime_ns: 20
document_role: main
mirror_paths:
  - /tmp/mirror/Paper
---

## Full Text
Body
""",
                encoding="utf-8",
            )

            index = FrontmatterIndex(config)

            entry = index.get("AI/Paper.pdf")
            self.assertIsNotNone(entry)
            self.assertEqual(entry["status"], "success")
            self.assertEqual(entry["output_markdown"], str(md_path))
            self.assertEqual(entry["markdown_bundle_dir"], str(md_path.parent))
            self.assertEqual(entry["mirror_paths"], ["/tmp/mirror/Paper"])
            self.assertTrue(
                index.is_unchanged(
                    "AI/Paper.pdf",
                    {"sha256": "abc123", "size": 10, "mtime_ns": 20},
                )
            )
            self.assertFalse((tmp_path / "output" / "state" / "manifest.json").exists())

    def test_mark_success_updates_frontmatter_and_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = make_config(tmp_path)
            input_root = Path(config["input_root"])
            pdf_path = input_root / "AI" / "Paper.pdf"
            pdf_path.parent.mkdir(parents=True)
            pdf_path.write_bytes(b"pdf")

            md_path = tmp_path / "output" / "markdown" / "AI" / "Paper" / "Paper.md"
            md_path.parent.mkdir(parents=True)
            md_path.write_text(
                """---
source_relpath: AI/Paper.pdf
source_filename: Paper.pdf
document_role: main
---

## Full Text
Body
""",
                encoding="utf-8",
            )

            index = FrontmatterIndex(config)
            index.mark_success(
                rel_key="AI/Paper.pdf",
                fingerprint={"sha256": "fresh", "size": 3, "mtime_ns": 123},
                source_pdf=pdf_path,
                output_markdown=md_path,
                raw_dir=tmp_path / "output" / "marker_raw" / "AI" / "Paper",
                metadata={"document_role": "main", "mirror_paths": ["/tmp/mirror/Paper"]},
            )

            metadata, _body = parse_frontmatter(md_path)
            self.assertEqual(metadata["conversion_status"], "success")
            self.assertEqual(metadata["source_relpath"], "AI/Paper.pdf")
            self.assertEqual(metadata["source_pdf_sha256"], "fresh")
            self.assertEqual(metadata["source_size"], 3)
            self.assertEqual(metadata["source_mtime_ns"], 123)
            self.assertEqual(metadata["mirror_paths"], ["/tmp/mirror/Paper"])
            self.assertEqual(index.get("AI/Paper.pdf")["sha256"], "fresh")

    def test_mark_success_adds_source_alias_when_markdown_is_canonical_for_another_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = make_config(tmp_path)
            input_root = Path(config["input_root"])
            alias_pdf = input_root / "AI" / "Paper 2.pdf"
            alias_pdf.parent.mkdir(parents=True)
            alias_pdf.write_bytes(b"pdf")

            md_path = tmp_path / "output" / "markdown" / "AI" / "Paper" / "Paper.md"
            md_path.parent.mkdir(parents=True)
            md_path.write_text(
                """---
source_relpath: AI/Paper.pdf
source_filename: Paper.pdf
source_pdf_sha256: canonical
document_role: main
---

## Full Text
Body
""",
                encoding="utf-8",
            )

            index = FrontmatterIndex(config)
            index.mark_success(
                rel_key="AI/Paper 2.pdf",
                fingerprint={"sha256": "alias", "size": 4, "mtime_ns": 456},
                source_pdf=alias_pdf,
                output_markdown=md_path,
                raw_dir=tmp_path / "output" / "marker_raw" / "AI" / "Paper 2",
                metadata={"document_role": "main"},
            )

            metadata, _body = parse_frontmatter(md_path)
            self.assertEqual(metadata["source_relpath"], "AI/Paper.pdf")
            self.assertEqual(metadata["source_pdf_sha256"], "canonical")
            self.assertEqual(metadata["source_aliases"][0]["source_relpath"], "AI/Paper 2.pdf")
            self.assertEqual(metadata["source_aliases"][0]["source_pdf_sha256"], "alias")
            self.assertEqual(index.get("AI/Paper 2.pdf")["output_markdown"], str(md_path))


if __name__ == "__main__":
    unittest.main()
