import json
from pathlib import Path

import pytest

from paper_to_markdown.common import build_frontmatter, parse_frontmatter


def make_config(tmp_path: Path) -> dict:
    return {
        "input_root": str(tmp_path / "input"),
        "output_root": str(tmp_path / "output"),
        "hf_home": str(tmp_path / "hf"),
        "marker_cli": "marker_single",
        "zotero_library_type": "user",
        "zotero_library_id": "12345",
        "agent_min_confidence_to_apply": 0.75,
        "agent_allow_remove_collections": False,
        "rag_chunks_jsonl_paths": [],
    }


def write_markdown(
    config: dict,
    relpath: str,
    metadata: dict | None = None,
    body: str = "## Full Text\n\nExample body.",
) -> Path:
    md_path = Path(config["output_root"]) / "markdown" / relpath
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(build_frontmatter(metadata or {}) + body, encoding="utf-8")
    return md_path


class FakeZoteroClient:
    def __init__(self, items: list[dict] | None = None):
        self.items = items or []
        self.created_paths: list[str] = []
        self.patches: list[dict] = []
        self.fetches: list[str] = []

    def list_items(self) -> list[dict]:
        return list(self.items)

    def get_item(self, item_key: str) -> dict:
        self.fetches.append(item_key)
        for item in self.items:
            if item["key"] == item_key:
                return json.loads(json.dumps(item))
        raise KeyError(item_key)

    def ensure_collection_path(self, path: str) -> str:
        self.created_paths.append(path)
        return "COL_" + path.replace("/", "_").replace(" ", "_")

    def patch_item(self, item_key: str, payload: dict, version: int) -> None:
        self.patches.append({"item_key": item_key, "payload": payload, "version": version})


def zotero_item(
    key: str,
    *,
    title: str = "CO2 Hydrogenation on Oxides",
    year: int = 2024,
    doi: str = "10.1234/example",
    citekey: str = "smith2024co2",
    attachment: str = "Paper.pdf",
    collections: list[str] | None = None,
    collection_keys: list[str] | None = None,
    tags: list[str] | None = None,
    version: int = 7,
) -> dict:
    return {
        "key": key,
        "version": version,
        "title": title,
        "year": year,
        "journal": "Journal of Catalysis",
        "doi": doi,
        "citekey": citekey,
        "attachment_paths": [f"storage:{attachment}"],
        "attachment_filenames": [attachment],
        "collections": collections or ["Existing/Collection"],
        "collection_keys": collection_keys or ["EXISTING"],
        "tags": tags or ["reviewed"],
    }


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_backfill_existing_markdown_without_reconversion(tmp_path, monkeypatch):
    from paper_to_markdown import pipeline
    from paper_to_markdown.zotero_backfill import backfill_existing_markdowns

    config = make_config(tmp_path)
    write_markdown(
        config,
        "Library/Paper/Paper.md",
        {"source_filename": "Paper.pdf", "doi": "10.1234/example"},
    )
    monkeypatch.setattr(
        pipeline,
        "convert_one_pdf_with_retries",
        lambda *args, **kwargs: pytest.fail("backfill must not reconvert PDFs"),
    )

    summary = backfill_existing_markdowns(
        config,
        zotero_client=FakeZoteroClient([zotero_item("ITEM1")]),
        dry_run=True,
    )

    matches = read_jsonl(Path(config["output_root"]) / "state" / "markdown_zotero_matches.jsonl")
    assert summary["matched"] == 1
    assert matches[0]["zotero_item_key"] == "ITEM1"
    assert matches[0]["markdown_path"].endswith("Paper.md")


def test_backfill_apply_updates_markdown_frontmatter_from_zotero(tmp_path):
    from paper_to_markdown.zotero_backfill import backfill_existing_markdowns

    config = make_config(tmp_path)
    md_path = write_markdown(
        config,
        "Library/Paper/Paper.md",
        {"source_filename": "Paper.pdf", "doi": "10.1234/example"},
    )

    summary = backfill_existing_markdowns(
        config,
        zotero_client=FakeZoteroClient(
            [
                zotero_item(
                    "ITEM1",
                    collections=["Existing/Collection"],
                    collection_keys=["COL1"],
                    tags=["reviewed", "catalysis"],
                )
            ]
        ),
        dry_run=False,
    )

    metadata, _body = parse_frontmatter(md_path)
    assert summary["updated_markdown"] == 1
    assert metadata["zotero_item_key"] == "ITEM1"
    assert metadata["citekey"] == "smith2024co2"
    assert metadata["doi"] == "10.1234/example"
    assert metadata["collections"] == ["Existing/Collection"]
    assert metadata["zotero_collections"] == ["Existing/Collection"]
    assert metadata["collection_keys"] == ["COL1"]
    assert metadata["tags"] == ["reviewed", "catalysis"]
    assert metadata["zotero_match_method"] == "doi"
    assert metadata["zotero_match_confidence"] == 0.98


def test_match_by_doi(tmp_path):
    from paper_to_markdown.zotero_backfill import backfill_existing_markdowns

    config = make_config(tmp_path)
    write_markdown(config, "Library/Paper/Paper.md", {}, "DOI: 10.9999/doi-only\n")

    summary = backfill_existing_markdowns(
        config,
        zotero_client=FakeZoteroClient([zotero_item("ITEM_DOI", doi="10.9999/doi-only")]),
        dry_run=True,
    )

    matches = read_jsonl(Path(config["output_root"]) / "state" / "markdown_zotero_matches.jsonl")
    assert summary["matched"] == 1
    assert matches[0]["zotero_match_method"] == "doi"
    assert matches[0]["zotero_match_confidence"] == 0.98


def test_match_by_citekey(tmp_path):
    from paper_to_markdown.zotero_backfill import backfill_existing_markdowns

    config = make_config(tmp_path)
    write_markdown(
        config,
        "Library/Paper/Paper.md",
        {"citekey": "chen2025oxide", "doi": "10.0000/wrong"},
    )

    backfill_existing_markdowns(
        config,
        zotero_client=FakeZoteroClient(
            [zotero_item("ITEM_CITE", citekey="chen2025oxide", doi="10.1111/right")]
        ),
        dry_run=True,
    )

    matches = read_jsonl(Path(config["output_root"]) / "state" / "markdown_zotero_matches.jsonl")
    assert matches[0]["zotero_item_key"] == "ITEM_CITE"
    assert matches[0]["zotero_match_method"] == "citekey"


def test_match_by_attachment_filename(tmp_path):
    from paper_to_markdown.zotero_backfill import backfill_existing_markdowns

    config = make_config(tmp_path)
    write_markdown(config, "Library/Paper/Paper.md", {"source_filename": "Unique.pdf"})

    backfill_existing_markdowns(
        config,
        zotero_client=FakeZoteroClient([zotero_item("ITEM_FILE", attachment="Unique.pdf")]),
        dry_run=True,
    )

    matches = read_jsonl(Path(config["output_root"]) / "state" / "markdown_zotero_matches.jsonl")
    assert matches[0]["zotero_match_method"] == "attachment_filename"
    assert matches[0]["zotero_item_key"] == "ITEM_FILE"


def test_unmatched_markdown_not_written(tmp_path):
    from paper_to_markdown.zotero_backfill import backfill_existing_markdowns

    config = make_config(tmp_path)
    md_path = write_markdown(
        config,
        "Library/Paper/Paper.md",
        {"title": "Ambiguous Catalyst", "year": 2024},
    )
    client = FakeZoteroClient(
        [
            zotero_item("ITEM_A", title="Ambiguous Catalyst", year=2024, doi="10.1/a"),
            zotero_item("ITEM_B", title="Ambiguous Catalyst", year=2024, doi="10.1/b"),
        ]
    )

    summary = backfill_existing_markdowns(config, zotero_client=client, dry_run=True)

    metadata, _body = parse_frontmatter(md_path)
    unmatched = read_jsonl(Path(config["output_root"]) / "state" / "unmatched_markdowns.jsonl")
    assert summary["unmatched"] == 1
    assert unmatched[0]["reason"] == "ambiguous_title_year"
    assert "zotero_item_key" not in metadata
    assert client.patches == []


def test_classification_dry_run_creates_plan_only(tmp_path):
    from paper_to_markdown.classification_workflow import classify_existing_markdowns

    config = make_config(tmp_path)
    md_path = write_markdown(
        config,
        "Library/Paper/Paper.md",
        {"zotero_item_key": "ITEM1", "title": "Paper"},
    )

    def fake_runner(markdown_path: Path, markdown_text: str, config: dict) -> dict:
        assert markdown_path == md_path
        assert "Paper" in markdown_text
        return {
            "recommended_collections": ["AI Classified/CO2 Hydrogenation"],
            "recommended_tags": ["catalysis"],
            "confidence": 0.91,
        }

    summary = classify_existing_markdowns(config, classifier_runner=fake_runner, dry_run=True)

    plan = read_jsonl(Path(config["output_root"]) / "state" / "classification_plan.jsonl")
    metadata, _body = parse_frontmatter(md_path)
    assert summary["planned"] == 1
    assert plan[0]["recommended_tags"] == ["catalysis"]
    assert "agent_recommended_tags" not in metadata


def test_classification_uses_default_classifier_when_command_missing(tmp_path):
    from paper_to_markdown.classification_workflow import classify_existing_markdowns

    config = make_config(tmp_path)
    config.pop("classification_agent_command", None)
    write_markdown(
        config,
        "Library/Paper/Paper.md",
        {
            "zotero_item_key": "ITEM1",
            "collections": ["Existing/Collection"],
            "tags": ["reviewed"],
        },
    )

    summary = classify_existing_markdowns(config, dry_run=True)

    plan = read_jsonl(Path(config["output_root"]) / "state" / "classification_plan.jsonl")
    assert summary["planned"] == 1
    assert plan[0]["recommended_collections"] == ["Existing/Collection"]
    assert plan[0]["recommended_tags"] == ["reviewed"]


def test_apply_plan_creates_missing_nested_collection(tmp_path):
    from paper_to_markdown.classification_workflow import apply_zotero_classification

    config = make_config(tmp_path)
    md_path = write_markdown(config, "Library/Paper/Paper.md", {"zotero_item_key": "ITEM1"})
    plan_path = Path(config["output_root"]) / "state" / "classification_plan.jsonl"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps(
            {
                "markdown_path": str(md_path),
                "zotero_item_key": "ITEM1",
                "recommended_collections": [
                    "AI Classified/CO2 Hydrogenation/High-entropy oxides"
                ],
                "recommended_tags": [],
                "confidence": 0.95,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    client = FakeZoteroClient([zotero_item("ITEM1")])

    summary = apply_zotero_classification(config, zotero_client=client, apply=True)

    assert summary["applied"] == 1
    assert client.created_paths == ["AI Classified/CO2 Hydrogenation/High-entropy oxides"]
    assert "COL_AI_Classified_CO2_Hydrogenation_High-entropy_oxides" in client.patches[0]["payload"]["collections"]


def test_apply_plan_merges_existing_collections(tmp_path):
    from paper_to_markdown.classification_workflow import apply_zotero_classification

    config = make_config(tmp_path)
    md_path = write_markdown(config, "Library/Paper/Paper.md", {"zotero_item_key": "ITEM1"})
    plan_path = Path(config["output_root"]) / "state" / "classification_plan.jsonl"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps(
            {
                "markdown_path": str(md_path),
                "zotero_item_key": "ITEM1",
                "recommended_collections": ["AI Classified/New"],
                "recommended_tags": ["ai-tag"],
                "confidence": 0.95,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    client = FakeZoteroClient([zotero_item("ITEM1", collection_keys=["KEEP"], tags=["manual"])])

    apply_zotero_classification(config, zotero_client=client, apply=True)

    payload = client.patches[0]["payload"]
    assert payload["collections"] == ["KEEP", "COL_AI_Classified_New"]
    assert payload["tags"] == [{"tag": "manual"}, {"tag": "ai-tag"}]


def test_patch_uses_if_unmodified_since_version(tmp_path):
    from paper_to_markdown.classification_workflow import apply_zotero_classification

    config = make_config(tmp_path)
    md_path = write_markdown(config, "Library/Paper/Paper.md", {"zotero_item_key": "ITEM1"})
    plan_path = Path(config["output_root"]) / "state" / "classification_plan.jsonl"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps(
            {
                "markdown_path": str(md_path),
                "zotero_item_key": "ITEM1",
                "recommended_collections": ["AI Classified/New"],
                "recommended_tags": [],
                "confidence": 0.95,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    client = FakeZoteroClient([zotero_item("ITEM1", version=42)])

    apply_zotero_classification(config, zotero_client=client, apply=True)

    assert client.patches[0]["version"] == 42


def test_rag_chunks_inherit_zotero_metadata(tmp_path):
    from paper_to_markdown.rag_metadata import sync_rag_metadata

    config = make_config(tmp_path)
    md_path = write_markdown(
        config,
        "Library/Paper/Paper.md",
        {
            "zotero_item_key": "ITEM1",
            "citekey": "smith2024co2",
            "doi": "10.1234/example",
            "title": "CO2 Hydrogenation on Oxides",
            "year": 2024,
            "journal": "Journal of Catalysis",
            "collections": ["AI Classified/CO2 Hydrogenation"],
            "collection_keys": ["COL1"],
            "tags": ["catalysis"],
        },
    )
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text(
        json.dumps(
            {
                "text": "chunk",
                "metadata": {
                    "source_markdown_path": str(md_path),
                    "section_heading": "Introduction",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    config["rag_chunks_jsonl_paths"] = [str(chunks_path)]

    summary = sync_rag_metadata(config)

    chunk = read_jsonl(chunks_path)[0]
    assert summary["updated_chunks"] == 1
    assert chunk["metadata"]["zotero_item_key"] == "ITEM1"
    assert chunk["metadata"]["collections"] == ["AI Classified/CO2 Hydrogenation"]
    assert chunk["metadata"]["section_heading"] == "Introduction"
    assert chunk["metadata"]["source_markdown_path"] == str(md_path)


def test_zotero_api_key_can_come_from_settings():
    from paper_to_markdown.zotero_api import ZoteroApiClient

    client = ZoteroApiClient.from_config(
        {
            "zotero_library_type": "user",
            "zotero_library_id": "12345",
            "zotero_api_key": "secret-from-settings",
        }
    )

    assert client.api_key == "secret-from-settings"
