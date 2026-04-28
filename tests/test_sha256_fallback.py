import tempfile
import unittest
from pathlib import Path

from paper_to_markdown.frontmatter_index import FrontmatterIndex
from paper_to_markdown.pipeline import (
    existing_markdown_for_duplicate_pdf,
    existing_markdown_for_pdf_by_sha256,
)


def make_config(tmp_path: Path) -> dict:
    return {
        "input_root": str(tmp_path / "input"),
        "output_root": str(tmp_path / "output"),
        "hf_home": str(tmp_path / "hf"),
        "marker_cli": "marker_single",
        "compute_sha256": True,
    }


class Sha256FallbackTests(unittest.TestCase):
    def _seed_markdown(self, tmp_path: Path, rel_key: str, sha: str) -> Path:
        md_path = tmp_path / "output" / "markdown" / Path(rel_key).with_suffix("") / (Path(rel_key).stem + ".md")
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(
            f"""---
source_relpath: {rel_key}
source_filename: {Path(rel_key).name}
source_pdf_sha256: {sha}
document_role: main
---

## Full Text
Body
""",
            encoding="utf-8",
        )
        return md_path

    def test_returns_match_when_sha256_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = make_config(tmp_path)
            md_path = self._seed_markdown(tmp_path, "A/Paper.pdf", "sha-abc")
            index = FrontmatterIndex(config)

            match = existing_markdown_for_pdf_by_sha256(index, {"sha256": "sha-abc"})
            self.assertIsNotNone(match)
            rel_key, returned_md = match
            self.assertEqual(rel_key, "A/Paper.pdf")
            self.assertEqual(returned_md, md_path)

    def test_returns_none_when_sha256_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = make_config(tmp_path)
            self._seed_markdown(tmp_path, "A/Paper.pdf", "sha-abc")
            index = FrontmatterIndex(config)

            self.assertIsNone(existing_markdown_for_pdf_by_sha256(index, {"sha256": "different"}))
            self.assertIsNone(existing_markdown_for_pdf_by_sha256(index, None))
            self.assertIsNone(existing_markdown_for_pdf_by_sha256(index, {}))

    def test_skips_entry_when_markdown_file_missing_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = make_config(tmp_path)
            md_path = self._seed_markdown(tmp_path, "A/Paper.pdf", "sha-abc")
            index = FrontmatterIndex(config)
            md_path.unlink()

            self.assertIsNone(existing_markdown_for_pdf_by_sha256(index, {"sha256": "sha-abc"}))

    def test_numbered_duplicate_pdf_can_reuse_primary_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = make_config(tmp_path)
            input_root = tmp_path / "input"
            primary_pdf = input_root / "A" / "Paper.pdf"
            duplicate_pdf = input_root / "A" / "Paper 2.pdf"
            primary_pdf.parent.mkdir(parents=True)
            primary_pdf.write_bytes(b"primary")
            duplicate_pdf.write_bytes(b"same-ish")
            md_path = self._seed_markdown(tmp_path, "A/Paper.pdf", "sha-abc")
            index = FrontmatterIndex(config)

            match = existing_markdown_for_duplicate_pdf(duplicate_pdf, input_root, config, index)

            self.assertIsNotNone(match)
            rel_key, returned_md = match
            self.assertEqual(rel_key, "A/Paper.pdf")
            self.assertEqual(returned_md, md_path)

if __name__ == "__main__":
    unittest.main()
