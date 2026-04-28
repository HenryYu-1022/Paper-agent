from pathlib import Path

from paper_to_markdown.common import build_frontmatter, parse_frontmatter
from paper_to_markdown.postprocess_markdown import looks_like_supporting_by_content, postprocess_library


def make_config(tmp_path: Path) -> dict:
    return {
        "input_root": str(tmp_path / "input"),
        "output_root": str(tmp_path / "output"),
        "hf_home": str(tmp_path / "hf"),
        "marker_cli": "marker_single",
        "compute_sha256": True,
    }


def seed_main_markdown(config: dict, rel_key: str, body: str) -> Path:
    output_root = Path(config["output_root"])
    md_path = output_root / "markdown" / Path(rel_key).with_suffix("") / f"{Path(rel_key).stem}.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(
        build_frontmatter(
            {
                "source_relpath": rel_key,
                "source_filename": Path(rel_key).name,
                "source_pdf": str(Path(config["input_root"]) / rel_key),
                "conversion_status": "success",
                "document_role": "main",
                "markdown_bundle_dir": str(md_path.parent),
            }
        )
        + body,
        encoding="utf-8",
    )
    return md_path


def test_postprocess_deletes_numbered_duplicate_main_pdf_and_markdown(tmp_path: Path):
    config = make_config(tmp_path)
    input_root = Path(config["input_root"])
    primary_pdf = input_root / "A" / "Paper.pdf"
    duplicate_pdf = input_root / "A" / "Paper 2.pdf"
    primary_pdf.parent.mkdir(parents=True)
    primary_pdf.write_bytes(b"primary")
    duplicate_pdf.write_bytes(b"duplicate")
    body = "## Full Text\n\n" + ("same converted main body " * 300)
    primary_md = seed_main_markdown(config, "A/Paper.pdf", body)
    duplicate_md = seed_main_markdown(config, "A/Paper 2.pdf", body)

    summary = postprocess_library(config, apply=True)

    assert summary["duplicate_main"] == 1
    assert summary["pdf_deleted"] == 1
    assert not duplicate_pdf.exists()
    assert not duplicate_md.parent.exists()
    assert primary_pdf.exists()
    assert primary_md.exists()


def test_postprocess_moves_supporting_markdown_into_primary_bundle(tmp_path: Path):
    config = make_config(tmp_path)
    input_root = Path(config["input_root"])
    primary_pdf = input_root / "A" / "Paper.pdf"
    si_pdf = input_root / "A" / "Paper SI.pdf"
    primary_pdf.parent.mkdir(parents=True)
    primary_pdf.write_bytes(b"primary")
    si_pdf.write_bytes(b"supporting")
    primary_md = seed_main_markdown(config, "A/Paper.pdf", "## Full Text\n\nMain body\n")
    si_md = seed_main_markdown(
        config,
        "A/Paper SI.pdf",
        "## Supporting Information\n\nSupporting information for this paper.\n\n![](figures/a.png)\n",
    )
    asset = si_md.parent / "figures" / "a.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"image")

    summary = postprocess_library(config, apply=True)

    target_md = primary_md.parent / "supporting.md"
    target_asset = primary_md.parent / "supporting_assets" / "figures" / "a.png"
    assert summary["supporting_moved"] == 1
    assert si_pdf.exists()
    assert not si_md.parent.exists()
    assert target_md.exists()
    assert target_asset.exists()
    metadata, body = parse_frontmatter(target_md)
    assert metadata["document_role"] == "supporting"
    assert metadata["primary_source_relpath"] == "A/Paper.pdf"
    assert "](supporting_assets/figures/a.png)" in body


def test_postprocess_dry_run_leaves_files_in_place(tmp_path: Path):
    config = make_config(tmp_path)
    input_root = Path(config["input_root"])
    primary_pdf = input_root / "A" / "Paper.pdf"
    duplicate_pdf = input_root / "A" / "Paper 2.pdf"
    primary_pdf.parent.mkdir(parents=True)
    primary_pdf.write_bytes(b"primary")
    duplicate_pdf.write_bytes(b"duplicate")
    body = "## Full Text\n\n" + ("same converted main body " * 300)
    seed_main_markdown(config, "A/Paper.pdf", body)
    duplicate_md = seed_main_markdown(config, "A/Paper 2.pdf", body)

    summary = postprocess_library(config, apply=False)

    assert summary["duplicate_main"] == 1
    assert duplicate_pdf.exists()
    assert duplicate_md.exists()


def test_supporting_detection_does_not_flag_main_article_with_later_supplementary_text(tmp_path: Path):
    config = make_config(tmp_path)
    md_path = seed_main_markdown(
        config,
        "A/Paper 3.pdf",
        "## Full Text\n\n# ARTICLE\nDOI: 10.1234/example\n\nMain paper body.\n\n"
        + ("ordinary article text " * 80)
        + "See Supplementary Information for extra details.\n",
    )

    assert not looks_like_supporting_by_content(md_path)
