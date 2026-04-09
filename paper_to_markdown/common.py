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


def load_config(config_path: str | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    with path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    for field in ["input_root", "output_root", "hf_home"]:
        config[field] = _resolve_path_value(_require_non_empty(config, field))

    config["marker_cli"] = _normalize_command_value(_require_non_empty(config, "marker_cli"))

    marker_repo_root = str(config.get("marker_repo_root", "")).strip()
    if marker_repo_root:
        config["marker_repo_root"] = _resolve_path_value(marker_repo_root)
    else:
        config.pop("marker_repo_root", None)

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
        Path(config["hf_home"]),
    }

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


def supporting_source_info(pdf_path: Path) -> tuple[Path, int] | None:
    match = SUPPORTING_SUFFIX_RE.fullmatch(pdf_path.stem)
    if not match:
        return None

    primary_pdf = pdf_path.with_name(match.group("base") + pdf_path.suffix)
    if not primary_pdf.exists():
        return None

    return primary_pdf, int(match.group("index"))


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
