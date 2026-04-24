from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKFLOW_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = WORKFLOW_DIR / "settings.json"
SUPPORTING_SUFFIX_RE = re.compile(r"^(?P<base>.+)_(?P<index>[1-9]\d*)$")
MAIN_DUPLICATE_SUFFIX_RE = re.compile(r"^(?P<base>.+?)(?:[\s_-]+)(?P<index>[2-9]\d*)$")
GENERIC_SUPPORTING_NAME_RE = re.compile(
    r"^(?P<label>"
    r"si|"
    r"supporting|supportinginfo|supportinginformation|"
    r"supplement|supplemental|supplementary|"
    r"suppinfo|supinfo|"
    r"supplementinfo|supplementinformation|"
    r"supplementalinfo|supplementalinformation|"
    r"supplementaryinfo|supplementaryinformation"
    r")(?P<index>[1-9]\d*)?$"
)
SUPPORTING_LABEL_TOKEN_RE = re.compile(
    r"(?:^|[\s_\-()]+)"
    r"(?:si|supporting(?:\s+information|\s+info)?|supplement(?:ary|al)?(?:\s+information|\s+info)?)"
    r"(?:[\s_\-()]+|$)",
    re.IGNORECASE,
)


def _require_non_empty(config: dict[str, Any], field: str) -> str:
    value = str(config.get(field, "")).strip()
    if not value:
        raise ValueError(f"Missing required config field: {field}")
    return value


def _resolve_path_value(value: str) -> str:
    return str(Path(value).expanduser().resolve())


def _normalize_command_value(value: str) -> str:
    command = value.strip()
    if not command:
        raise ValueError("Missing required config field: marker_cli")

    path_separators = [os.sep]
    if os.altsep:
        path_separators.append(os.altsep)

    if command.startswith(("~", ".", "..")) or any(separator in command for separator in path_separators):
        return _resolve_path_value(command)

    return command


RUN_MODES = {"all-in-one", "runner", "controller"}


def load_config(config_path: str | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    with path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    run_mode = str(config.get("run_mode", "all-in-one")).strip()
    if run_mode not in RUN_MODES:
        raise ValueError(f"Invalid run_mode '{run_mode}'. Must be one of: {', '.join(sorted(RUN_MODES))}")
    config["run_mode"] = run_mode

    for field in ["input_root", "output_root"]:
        config[field] = _resolve_path_value(_require_non_empty(config, field))

    if run_mode != "controller":
        config["hf_home"] = _resolve_path_value(_require_non_empty(config, "hf_home"))
        config["marker_cli"] = _normalize_command_value(_require_non_empty(config, "marker_cli"))

        marker_repo_root = str(config.get("marker_repo_root", "")).strip()
        if marker_repo_root:
            config["marker_repo_root"] = _resolve_path_value(marker_repo_root)
        else:
            config.pop("marker_repo_root", None)

    # Optional: Zotero database path for collection hierarchy mirroring
    zotero_db_path = str(config.get("zotero_db_path", "")).strip()
    if zotero_db_path:
        config["zotero_db_path"] = _resolve_path_value(zotero_db_path)
    else:
        config.pop("zotero_db_path", None)

    # SHA256 fingerprinting defaults on so frontmatter remains stable across
    # devices when the user has not explicitly set it.
    config["compute_sha256"] = bool(config.get("compute_sha256", True))

    return config


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def output_root(config: dict[str, Any]) -> Path:
    return Path(config["output_root"])


def markdown_root(config: dict[str, Any]) -> Path:
    return output_root(config) / "markdown"


def raw_root(config: dict[str, Any]) -> Path:
    return output_root(config) / "marker_raw"


def state_root(config: dict[str, Any]) -> Path:
    return output_root(config) / "state"


def logs_root(config: dict[str, Any]) -> Path:
    return output_root(config) / "logs"


def failed_report_path(config: dict[str, Any]) -> Path:
    return logs_root(config) / "failed_pdfs.txt"


def manifest_path(config: dict[str, Any]) -> Path:
    return state_root(config) / "manifest.json"


def collection_state_path(config: dict[str, Any]) -> Path:
    return state_root(config) / "collection_state.json"


def to_posix_path_str(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def safe_rmtree(target: Path, allowed_root: Path) -> None:
    if not target.exists():
        return
    if not is_relative_to(target, allowed_root):
        raise ValueError(f"Refusing to delete path outside allowed root: {target}")
    shutil.rmtree(target)


def cleanup_marker_raw_root(config: dict[str, Any], logger: logging.Logger | None = None) -> bool:
    target = raw_root(config)
    if not target.exists():
        return False

    safe_rmtree(target, output_root(config))
    if logger is not None:
        logger.info("Removed marker_raw root after run: %s", target)
    return True


def ensure_directories(config: dict[str, Any]) -> None:
    paths = {
        output_root(config),
        markdown_root(config),
        raw_root(config),
        state_root(config),
        logs_root(config),
    }
    hf_home = str(config.get("hf_home", "")).strip()
    if hf_home:
        paths.add(Path(hf_home))

    for path in sorted(paths, key=lambda item: len(str(item))):
        path.mkdir(parents=True, exist_ok=True)


def setup_logger(config: dict[str, Any], logger_name: str = "paper_to_markdown") -> logging.Logger:
    ensure_directories(config)

    logger = logging.getLogger(logger_name)
    logger.setLevel(getattr(logging, config.get("log_level", "INFO").upper(), logging.INFO))
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(logs_root(config) / "app.log", encoding="utf-8")
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def find_all_pdfs(input_root: Path) -> list[Path]:
    return sorted(
        path
        for path in input_root.rglob("*")
        if path.is_file() and path.suffix.lower() == ".pdf"
    )


def relative_pdf_path(pdf_path: Path, input_root: Path) -> Path:
    return pdf_path.resolve().relative_to(input_root.resolve())


def pdf_bundle_relpath(rel_pdf_path: Path) -> Path:
    return rel_pdf_path.with_suffix("")


def _normalize_pdf_stem_key(stem: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", stem.lower())


def _explicit_supporting_source_info(pdf_path: Path) -> tuple[Path, int] | None:
    match = SUPPORTING_SUFFIX_RE.fullmatch(pdf_path.stem)
    if not match:
        return None

    primary_pdf = pdf_path.with_name(match.group("base") + pdf_path.suffix)
    if not primary_pdf.exists():
        return None

    return primary_pdf, int(match.group("index"))


def _generic_supporting_name_index(pdf_path: Path) -> int | None:
    match = GENERIC_SUPPORTING_NAME_RE.fullmatch(_normalize_pdf_stem_key(pdf_path.stem))
    if not match:
        return None

    index_text = match.group("index")
    if not index_text:
        return 1
    return int(index_text)


def _has_supporting_label(stem: str) -> bool:
    return SUPPORTING_LABEL_TOKEN_RE.search(stem) is not None


def _iter_sibling_pdfs(pdf_path: Path) -> list[Path]:
    return sorted(
        path
        for path in pdf_path.parent.iterdir()
        if path.is_file() and path.suffix.lower() == pdf_path.suffix.lower()
    )


def _supporting_name_matches_primary(pdf_path: Path, primary_pdf: Path) -> bool:
    if pdf_path == primary_pdf:
        return False

    explicit_info = _explicit_supporting_source_info(pdf_path)
    if explicit_info and explicit_info[0] == primary_pdf:
        return True

    if _generic_supporting_name_index(pdf_path) is not None:
        return True
    if not _has_supporting_label(pdf_path.stem):
        return False

    pdf_key = _normalize_pdf_stem_key(pdf_path.stem)
    primary_key = _normalize_pdf_stem_key(primary_pdf.stem)
    return bool(primary_key) and len(primary_key) < len(pdf_key) and primary_key in pdf_key


def _supporting_sort_key(pdf_path: Path, primary_pdf: Path) -> tuple[int, int | str, str]:
    explicit_info = _explicit_supporting_source_info(pdf_path)
    if explicit_info and explicit_info[0] == primary_pdf:
        return 0, explicit_info[1], pdf_path.name.lower()
    return 1, _normalize_pdf_stem_key(pdf_path.stem), pdf_path.name.lower()


def _generic_supporting_sort_key(pdf_path: Path) -> tuple[int, str]:
    index = _generic_supporting_name_index(pdf_path)
    if index is None:
        return 10**9, pdf_path.name.lower()
    return index, pdf_path.name.lower()


def _supporting_index_for_primary(pdf_path: Path, primary_pdf: Path) -> int:
    grouped_supporting = sorted(
        (
            sibling
            for sibling in _iter_sibling_pdfs(pdf_path)
            if _supporting_name_matches_primary(sibling, primary_pdf)
        ),
        key=lambda path: _supporting_sort_key(path, primary_pdf),
    )
    try:
        return grouped_supporting.index(pdf_path) + 1
    except ValueError:
        return 1


def _generic_supporting_source_info(pdf_path: Path) -> tuple[Path, int] | None:
    generic_index = _generic_supporting_name_index(pdf_path)
    if generic_index is None:
        return None

    primary_candidates = [
        sibling
        for sibling in _iter_sibling_pdfs(pdf_path)
        if sibling != pdf_path
        and _generic_supporting_name_index(sibling) is None
        and _explicit_supporting_source_info(sibling) is None
    ]
    if len(primary_candidates) != 1:
        return None

    primary_pdf = primary_candidates[0]
    generic_group = sorted(
        (
            sibling
            for sibling in _iter_sibling_pdfs(pdf_path)
            if _generic_supporting_name_index(sibling) is not None
        ),
        key=_generic_supporting_sort_key,
    )
    try:
        return primary_pdf, generic_group.index(pdf_path) + 1
    except ValueError:
        return primary_pdf, generic_index


def _explicit_main_duplicate_source_info(pdf_path: Path) -> tuple[Path, int] | None:
    if _explicit_supporting_source_info(pdf_path) is not None:
        return None
    if _generic_supporting_name_index(pdf_path) is not None:
        return None

    match = MAIN_DUPLICATE_SUFFIX_RE.fullmatch(pdf_path.stem)
    if not match:
        return None

    primary_pdf = pdf_path.with_name(match.group("base") + pdf_path.suffix)
    if not primary_pdf.exists():
        return None

    return primary_pdf, int(match.group("index"))


def _main_duplicate_sort_key(pdf_path: Path, primary_pdf: Path) -> tuple[int, int, str]:
    if pdf_path == primary_pdf:
        return 0, 0, pdf_path.name.lower()

    explicit_info = _explicit_main_duplicate_source_info(pdf_path)
    if explicit_info and explicit_info[0] == primary_pdf:
        return 1, explicit_info[1], pdf_path.name.lower()

    return 2, 10**9, pdf_path.name.lower()


def main_duplicate_group_pdfs(pdf_path: Path) -> list[Path]:
    explicit_info = _explicit_main_duplicate_source_info(pdf_path)
    primary_pdf = explicit_info[0] if explicit_info else pdf_path

    group = [primary_pdf]
    for sibling in _iter_sibling_pdfs(primary_pdf):
        if sibling == primary_pdf:
            continue
        sibling_info = _explicit_main_duplicate_source_info(sibling)
        if sibling_info and sibling_info[0] == primary_pdf:
            group.append(sibling)

    group = sorted(
        {path.resolve(): path for path in group}.values(),
        key=lambda path: _main_duplicate_sort_key(path, primary_pdf),
    )
    if pdf_path not in group:
        group.append(pdf_path)
        group = sorted(group, key=lambda path: _main_duplicate_sort_key(path, primary_pdf))
    return group


def supporting_source_info(pdf_path: Path) -> tuple[Path, int] | None:
    explicit_info = _explicit_supporting_source_info(pdf_path)
    if explicit_info:
        return explicit_info

    generic_info = _generic_supporting_source_info(pdf_path)
    if generic_info:
        return generic_info

    if not _has_supporting_label(pdf_path.stem):
        return None

    pdf_key = _normalize_pdf_stem_key(pdf_path.stem)
    if not pdf_key:
        return None

    candidates: list[tuple[int, str, Path]] = []
    for sibling in _iter_sibling_pdfs(pdf_path):
        if sibling == pdf_path:
            continue
        sibling_key = _normalize_pdf_stem_key(sibling.stem)
        if not sibling_key or len(sibling_key) >= len(pdf_key):
            continue
        if sibling_key not in pdf_key:
            continue
        candidates.append((len(sibling_key), sibling_key, sibling))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (-item[0], item[1], item[2].name.lower()))
    primary_pdf = candidates[0][2]
    return primary_pdf, _supporting_index_for_primary(pdf_path, primary_pdf)


def supporting_markdown_name(index: int) -> str:
    if index <= 1:
        return "supporting.md"
    return f"supporting_{index}.md"


def supporting_assets_dir_name(index: int) -> str:
    if index <= 1:
        return "supporting_assets"
    return f"supporting_{index}_assets"


def is_supporting_artifact_name(name: str) -> bool:
    if name in {"supporting.md", "supporting_assets"}:
        return True
    return bool(re.fullmatch(r"supporting_\d+\.md|supporting_\d+_assets", name))


def bundle_dir_for_pdf(pdf_path: Path, input_root: Path, config: dict[str, Any]) -> Path:
    return markdown_root(config) / pdf_bundle_relpath(relative_pdf_path(pdf_path, input_root))


def raw_dir_for_pdf(pdf_path: Path, input_root: Path, config: dict[str, Any]) -> Path:
    return raw_root(config) / pdf_bundle_relpath(relative_pdf_path(pdf_path, input_root))


def compute_sha256(file_path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def pdf_fingerprint(pdf_path: Path, use_sha256: bool) -> dict[str, Any]:
    stat = pdf_path.stat()
    data: dict[str, Any] = {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
    if use_sha256:
        data["sha256"] = compute_sha256(pdf_path)
    return data


_MARKER_VERSION_CACHE: str | None = None


def detect_marker_version() -> str:
    """Best-effort lookup of the installed Marker package version.

    Returns ``"unknown"`` when the package metadata cannot be located,
    e.g. when ``marker_cli`` lives in a different Python environment.
    The result is cached for the lifetime of the process.
    """
    global _MARKER_VERSION_CACHE
    if _MARKER_VERSION_CACHE is not None:
        return _MARKER_VERSION_CACHE

    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:
        _MARKER_VERSION_CACHE = "unknown"
        return _MARKER_VERSION_CACHE

    for distribution_name in ("marker-pdf", "marker"):
        try:
            _MARKER_VERSION_CACHE = version(distribution_name)
            return _MARKER_VERSION_CACHE
        except PackageNotFoundError:
            continue

    _MARKER_VERSION_CACHE = "unknown"
    return _MARKER_VERSION_CACHE


def find_main_markdown(raw_output_dir: Path) -> Path:
    markdown_files = [path for path in raw_output_dir.rglob("*.md") if path.is_file()]
    if not markdown_files:
        raise FileNotFoundError(f"No markdown file found in marker output: {raw_output_dir}")
    return max(markdown_files, key=lambda path: path.stat().st_size)


def detect_marker_content_root(raw_output_dir: Path) -> Path:
    children = list(raw_output_dir.iterdir())
    dirs = [path for path in children if path.is_dir()]
    files = [path for path in children if path.is_file()]
    if len(dirs) == 1 and not files:
        return dirs[0]
    return raw_output_dir


def build_frontmatter(metadata: dict[str, Any]) -> str:
    import yaml

    yaml_text = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=4096,
    ).strip()
    return f"---\n{yaml_text}\n---\n\n"


def write_frontmatter_markdown(markdown_path: Path, metadata: dict[str, Any]) -> None:
    body = markdown_path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
    frontmatter = build_frontmatter(metadata)
    markdown_path.write_text(
        frontmatter + "## Full Text\n\n" + body.lstrip(),
        encoding="utf-8",
    )


def parse_frontmatter(markdown_path: Path) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter and body from a Markdown file.

    Returns ``(metadata_dict, body_text)``.  If no frontmatter is found,
    *metadata_dict* is empty and *body_text* is the full file content.
    """
    import yaml

    text = markdown_path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
    if not text.startswith("---"):
        return {}, text

    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    yaml_block = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    try:
        metadata = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError:
        metadata = {}
    return metadata, body


def update_frontmatter_fields(
    markdown_path: Path,
    updates: dict[str, Any],
) -> None:
    """Update specific fields in the YAML frontmatter of a Markdown file.

    Only the given *updates* keys are changed; the remaining metadata and
    the body text are preserved exactly.
    """
    metadata, body = parse_frontmatter(markdown_path)
    metadata.update(updates)
    frontmatter = build_frontmatter(metadata)
    markdown_path.write_text(frontmatter + body, encoding="utf-8")
