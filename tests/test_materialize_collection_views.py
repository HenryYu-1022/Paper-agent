from pathlib import Path

from paper_to_markdown.common import build_frontmatter
from paper_to_markdown.zotero_markdown import materialize_views, zotero_markdown_root


def make_config(tmp_path: Path) -> dict:
    return {
        "input_root": str(tmp_path / "input"),
        "output_root": str(tmp_path / "output"),
        "hf_home": str(tmp_path / "hf"),
        "marker_cli": "marker_single",
        "compute_sha256": True,
    }


def test_materialize_collection_views_copy_uses_separate_root(tmp_path: Path):
    config = make_config(tmp_path)
    output_root = Path(config["output_root"])
    bundle = output_root / "markdown" / "Library" / "Paper"
    md_path = bundle / "Paper.md"
    md_path.parent.mkdir(parents=True)
    md_path.write_text(
        build_frontmatter(
            {
                "source_relpath": "Library/Paper.pdf",
                "source_filename": "Paper.pdf",
                "conversion_status": "success",
                "document_role": "main",
                "zotero_collections": ["Topic/Subtopic"],
                "markdown_bundle_dir": str(bundle),
            }
        )
        + "Body\n",
        encoding="utf-8",
    )

    summary = materialize_views(config, mode="copy", clean=True)

    copied_md = output_root / "zotero_markdown" / "Topic" / "Subtopic" / "Paper" / "Paper.md"
    assert summary["created"] == 1
    assert copied_md.exists()
    assert output_root / "markdown" not in copied_md.parents


def test_default_zotero_markdown_root_is_zotero_markdown(tmp_path: Path):
    config = make_config(tmp_path)

    assert zotero_markdown_root(config) == Path(config["output_root"]) / "zotero_markdown"


def test_zotero_markdown_root_prefers_new_config_name(tmp_path: Path):
    config = make_config(tmp_path)
    config["collection_views_root"] = str(tmp_path / "legacy")
    config["zotero_markdown_root"] = str(tmp_path / "new")

    assert zotero_markdown_root(config) == (tmp_path / "new").resolve()


def test_materialize_collection_views_skips_duplicate_alias_targets(tmp_path: Path):
    config = make_config(tmp_path)
    output_root = Path(config["output_root"])
    bundle = output_root / "markdown" / "Library" / "Paper"
    md_path = bundle / "Paper.md"
    md_path.parent.mkdir(parents=True)
    md_path.write_text(
        build_frontmatter(
            {
                "source_relpath": "Library/Paper.pdf",
                "source_filename": "Paper.pdf",
                "conversion_status": "success",
                "document_role": "main",
                "zotero_collections": ["Topic/Subtopic"],
                "markdown_bundle_dir": str(bundle),
                "source_aliases": [
                    {
                        "source_relpath": "Library/Paper 2.pdf",
                        "source_filename": "Paper 2.pdf",
                    }
                ],
            }
        )
        + "Body\n",
        encoding="utf-8",
    )

    summary = materialize_views(config, mode="copy", clean=True)

    assert summary["created"] == 1
