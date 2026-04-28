from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable

try:
    from .common import load_config, markdown_root, parse_frontmatter, state_root, update_frontmatter_fields, utc_now_iso
    from .jsonl_utils import read_jsonl, write_jsonl
    from .zotero_api import ZoteroApiClient
except ImportError:
    from common import load_config, markdown_root, parse_frontmatter, state_root, update_frontmatter_fields, utc_now_iso
    from jsonl_utils import read_jsonl, write_jsonl
    from zotero_api import ZoteroApiClient


ClassifierRunner = Callable[[Path, str, dict[str, Any]], dict[str, Any]]


def classification_plan_path(config: dict[str, Any]) -> Path:
    return state_root(config) / "classification_plan.jsonl"


def matches_path(config: dict[str, Any]) -> Path:
    return state_root(config) / "markdown_zotero_matches.jsonl"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def external_classifier_runner(markdown_path: Path, markdown_text: str, config: dict[str, Any]) -> dict[str, Any]:
    command = config.get("classification_agent_command")
    if not command:
        try:
            from .simple_classifier import classify
        except ImportError:
            from simple_classifier import classify

        return classify(markdown_path)
    argv = shlex.split(command) if isinstance(command, str) else [str(part) for part in command]
    payload = {"markdown_path": str(markdown_path), "markdown": markdown_text}
    result = subprocess.run(
        argv,
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"classification agent failed: {result.stderr[-4000:]}")
    return json.loads(result.stdout)


def _classification_inputs(config: dict[str, Any]) -> list[dict[str, Any]]:
    rows = read_jsonl(matches_path(config))
    if rows:
        return rows

    root = markdown_root(config)
    inputs: list[dict[str, Any]] = []
    if not root.exists():
        return inputs
    for md_path in sorted(path for path in root.rglob("*.md") if path.is_file()):
        metadata, _body = parse_frontmatter(md_path)
        if metadata.get("zotero_item_key"):
            inputs.append(
                {
                    "markdown_path": str(md_path),
                    "zotero_item_key": metadata["zotero_item_key"],
                }
            )
    return inputs


def classify_existing_markdowns(
    config: dict[str, Any],
    *,
    classifier_runner: ClassifierRunner | None = None,
    dry_run: bool = True,
) -> dict[str, int]:
    runner = classifier_runner or external_classifier_runner
    plans: list[dict[str, Any]] = []
    for match in _classification_inputs(config):
        md_path = Path(match["markdown_path"])
        markdown_text = md_path.read_text(encoding="utf-8", errors="replace")
        recommendation = runner(md_path, markdown_text, config)
        plans.append(
            {
                "markdown_path": str(md_path),
                "zotero_item_key": match["zotero_item_key"],
                "citekey": match.get("citekey") or "",
                "doi": match.get("doi") or "",
                "title": match.get("title") or "",
                "year": match.get("year"),
                "journal": match.get("journal") or "",
                "collections": _as_list(match.get("collections")),
                "collection_keys": _as_list(match.get("collection_keys")),
                "tags": _as_list(match.get("tags")),
                "zotero_match_method": match.get("zotero_match_method") or "",
                "zotero_match_confidence": match.get("zotero_match_confidence"),
                "recommended_collections": _as_list(recommendation.get("recommended_collections")),
                "recommended_tags": _as_list(recommendation.get("recommended_tags")),
                "confidence": float(recommendation.get("confidence") or 0),
            }
        )
    write_jsonl(classification_plan_path(config), plans)
    return {"planned": len(plans)}


def _ai_collection_path(path: str) -> str:
    cleaned = "/".join(part.strip() for part in path.split("/") if part.strip())
    if not cleaned:
        return "AI Classified"
    if cleaned == "AI Classified" or cleaned.startswith("AI Classified/"):
        return cleaned
    return f"AI Classified/{cleaned}"


def _merge_unique(existing: list[str], additions: list[str]) -> list[str]:
    merged = list(existing)
    for item in additions:
        if item not in merged:
            merged.append(item)
    return merged


def _tag_payload(tags: list[str]) -> list[dict[str, str]]:
    return [{"tag": tag} for tag in tags]


def apply_zotero_classification(
    config: dict[str, Any],
    *,
    zotero_client: Any | None = None,
    apply: bool = False,
) -> dict[str, int]:
    if not apply:
        return {"applied": 0, "skipped": len(read_jsonl(classification_plan_path(config)))}

    client = zotero_client or ZoteroApiClient.from_config(config)
    threshold = float(config.get("agent_min_confidence_to_apply", 0.75))
    allow_remove = bool(config.get("agent_allow_remove_collections", False))
    applied = 0
    skipped = 0

    for plan in read_jsonl(classification_plan_path(config)):
        confidence = float(plan.get("confidence") or 0)
        if confidence < threshold:
            skipped += 1
            continue

        item_key = str(plan["zotero_item_key"])
        item = client.get_item(item_key)
        collection_paths = [_ai_collection_path(path) for path in _as_list(plan.get("recommended_collections"))]
        collection_keys = [client.ensure_collection_path(path) for path in collection_paths]
        existing_keys = [str(key) for key in item.get("collection_keys") or []]
        merged_keys = collection_keys if allow_remove else _merge_unique(existing_keys, collection_keys)

        existing_tags = [str(tag) for tag in item.get("tags") or []]
        recommended_tags = _as_list(plan.get("recommended_tags"))
        merged_tags = _merge_unique(existing_tags, recommended_tags)

        payload = {"collections": merged_keys, "tags": _tag_payload(merged_tags)}
        client.patch_item(item_key, payload, int(item.get("version") or 0))

        markdown_path = Path(plan["markdown_path"])
        existing_paths = [str(path) for path in item.get("collections") or []]
        merged_paths = collection_paths if allow_remove else _merge_unique(existing_paths, collection_paths)
        update_frontmatter_fields(
            markdown_path,
            {
                "zotero_item_key": item_key,
                "citekey": item.get("citekey") or "",
                "doi": item.get("doi") or "",
                "title": item.get("title") or plan.get("title") or "",
                "year": item.get("year"),
                "journal": item.get("journal") or "",
                "collections": merged_paths,
                "zotero_collections": merged_paths,
                "collection_keys": merged_keys,
                "tags": merged_tags,
                "zotero_match_method": plan.get("zotero_match_method") or "",
                "zotero_match_confidence": plan.get("zotero_match_confidence"),
                "agent_recommended_collections": collection_paths,
                "agent_recommended_tags": recommended_tags,
                "agent_classification_confidence": confidence,
                "agent_classification_applied_at": utc_now_iso(),
            },
        )
        applied += 1

    return {"applied": applied, "skipped": skipped}


def main_classify() -> None:
    parser = argparse.ArgumentParser(description="Classify existing Markdown files.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--dry-run", action="store_true", default=True)
    args = parser.parse_args()
    config = load_config(args.config)
    print(classify_existing_markdowns(config, dry_run=True))


def main_apply() -> None:
    parser = argparse.ArgumentParser(description="Apply Zotero classification plan.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--apply", action="store_true", required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    print(apply_zotero_classification(config, apply=args.apply))


if __name__ == "__main__":
    main_classify()
