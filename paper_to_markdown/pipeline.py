from __future__ import annotations

import ctypes
from difflib import SequenceMatcher
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from .common import (
        conversion_lock_path,
        bundle_dir_for_pdf,
        conversion_status_path,
        detect_marker_content_root,
        detect_marker_version,
        ensure_directories,
        failed_report_path,
        find_all_pdfs,
        find_main_markdown,
        is_relative_to,
        is_supporting_artifact_name,
        load_config,
        main_duplicate_group_pdfs,
        markdown_root,
        output_root,
        parse_frontmatter,
        pdf_bundle_relpath,
        pdf_fingerprint,
        raw_dir_for_pdf,
        raw_root,
        relative_pdf_path,
        safe_rmtree,
        setup_logger,
        supporting_assets_dir_name,
        supporting_markdown_name,
        supporting_source_info,
        to_posix_path_str,
        update_frontmatter_fields,
        utc_now_iso,
        write_frontmatter_markdown,
    )
    from .frontmatter_index import FrontmatterIndex
    from .zotero_collections import ZoteroCollectionMap
except ImportError:
    from common import (
        conversion_lock_path,
        bundle_dir_for_pdf,
        conversion_status_path,
        detect_marker_content_root,
        detect_marker_version,
        ensure_directories,
        failed_report_path,
        find_all_pdfs,
        find_main_markdown,
        is_relative_to,
        is_supporting_artifact_name,
        load_config,
        main_duplicate_group_pdfs,
        markdown_root,
        output_root,
        parse_frontmatter,
        pdf_bundle_relpath,
        pdf_fingerprint,
        raw_dir_for_pdf,
        raw_root,
        relative_pdf_path,
        safe_rmtree,
        setup_logger,
        supporting_assets_dir_name,
        supporting_markdown_name,
        supporting_source_info,
        to_posix_path_str,
        update_frontmatter_fields,
        utc_now_iso,
        write_frontmatter_markdown,
    )
    from frontmatter_index import FrontmatterIndex
    from zotero_collections import ZoteroCollectionMap

SUPPORTING_CONTENT_MARKER = "supportinginformation"
MAX_CONVERSION_RETRIES = 3
DUPLICATE_MARKDOWN_MIN_NORMALIZED_LEN = 4000
DUPLICATE_MARKDOWN_MIN_LENGTH_RATIO = 0.97
DUPLICATE_MARKDOWN_MAX_CHAR_DELTA = 1200
DUPLICATE_MARKDOWN_SIMILARITY_THRESHOLD = 0.985
SUPPORTING_MARKDOWN_FILE_RE = re.compile(r"^supporting(?:_(?P<index>\d+))?\.md$")
_HELD_CONVERSION_LOCKS: set[Path] = set()
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87


def _windows_process_is_running(pid_int: int) -> bool | None:
    process_query_limited_information = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid_int)
    if not handle:
        error = kernel32.GetLastError()
        if error == ERROR_ACCESS_DENIED:
            return True
        if error == ERROR_INVALID_PARAMETER:
            return False
        return None

    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return None
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def process_is_running(pid: Any) -> bool | None:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return None
    if pid_int <= 0:
        return None
    if os.name == "nt":
        return _windows_process_is_running(pid_int)
    try:
        os.kill(pid_int, 0)
        return True
    except OSError:
        return False


class ConversionLock:
    def __init__(self, config: dict[str, Any], owner: str) -> None:
        self.config = config
        self.owner = owner
        self.path = conversion_lock_path(config)
        self.token = f"{os.getpid()}-{time.time()}-{uuid.uuid4()}"
        self.acquired = False
        self.reentrant = False

    def __enter__(self) -> "ConversionLock":
        resolved_path = self.path.resolve()
        if resolved_path in _HELD_CONVERSION_LOCKS:
            self.reentrant = True
            return self

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "owner": self.owner,
            "started_at": utc_now_iso(),
            "started_at_epoch": time.time(),
            "token": self.token,
        }
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(str(self.path), flags)
        except FileExistsError as exc:
            existing = self._read_existing_lock()
            if process_is_running(existing.get("pid")) is False:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                try:
                    fd = os.open(str(self.path), flags)
                except FileExistsError:
                    raise RuntimeError(
                        "Another conversion appears to be running. "
                        f"Lock file: {self.path}. "
                        "A stale lock was removed, but another lock appeared before retry."
                    ) from exc
            else:
                raise RuntimeError(
                    "Another conversion appears to be running. "
                    f"Lock file: {self.path}. "
                    f"Existing owner: {existing.get('owner', 'unknown')}; "
                    f"pid: {existing.get('pid', 'unknown')}. "
                    "If no conversion is actually running, delete this stale lock file and retry."
                ) from exc
        except OSError:
            raise

        with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
            json.dump(payload, lock_file, ensure_ascii=False, indent=2)
        _HELD_CONVERSION_LOCKS.add(resolved_path)
        self.acquired = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.reentrant or not self.acquired:
            return
        _HELD_CONVERSION_LOCKS.discard(self.path.resolve())
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if payload.get("token") == self.token:
            self.path.unlink()

    def _read_existing_lock(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}


def write_conversion_status(config: dict[str, Any], rel_key: str, pdf_path: Path) -> None:
    status_path = conversion_status_path(config)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "running",
        "pid": os.getpid(),
        "source_relpath": rel_key,
        "source_pdf": to_posix_path_str(pdf_path),
        "source_filename": pdf_path.name,
        "started_at": utc_now_iso(),
        "started_at_epoch": time.time(),
    }
    tmp_path = status_path.with_name(status_path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(status_path)


def clear_conversion_status(config: dict[str, Any], rel_key: str) -> None:
    status_path = conversion_status_path(config)
    if not status_path.exists():
        return
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if payload.get("pid") != os.getpid() or payload.get("source_relpath") != rel_key:
        return
    status_path.unlink()


def _path_match_key(path: Path | str | None) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    try:
        normalized = str(Path(text).resolve(strict=False))
    except OSError:
        normalized = str(Path(text))
    return os.path.normcase(os.path.normpath(normalized))


def _normalized_stem_key(stem: str) -> str:
    """Match already-converted papers even after Zotero/Drive path moves.

    Google Drive can change mtimes and Zotero can move PDFs between collection
    folders.  For the non-force path we treat an existing Markdown with the
    same normalized paper title as sufficient evidence that the PDF has already
    been converted.
    """
    base_stem = re.sub(r"(?:[\s_-]+[1-9]\d*)$", "", stem)
    return re.sub(r"[^0-9a-z]+", "", base_stem.lower())


def _build_markdown_stem_index(config: dict[str, Any]) -> dict[str, Path]:
    stem_index: dict[str, Path] = {}
    md_root = markdown_root(config)
    if not md_root.exists():
        return stem_index

    for markdown_path in sorted(md_root.rglob("*.md")):
        if not markdown_path.is_file():
            continue
        key = _normalized_stem_key(markdown_path.stem)
        if key and key not in stem_index:
            stem_index[key] = markdown_path
    return stem_index


def _existing_main_markdown_by_stem(
    pdf_path: Path,
    config: dict[str, Any],
    markdown_stem_index: dict[str, Path] | None = None,
) -> Path | None:
    stem_index = markdown_stem_index if markdown_stem_index is not None else _build_markdown_stem_index(config)
    return stem_index.get(_normalized_stem_key(pdf_path.stem))


def _expected_output_markdown_for_pdf(
    pdf_path: Path,
    input_root: Path,
    config: dict[str, Any],
) -> Path:
    supporting_info = supporting_source_info(pdf_path)
    if supporting_info:
        primary_pdf, supporting_index = supporting_info
        return bundle_dir_for_pdf(primary_pdf, input_root, config) / supporting_markdown_name(supporting_index)
    return bundle_dir_for_pdf(pdf_path, input_root, config) / f"{pdf_path.stem}.md"


def existing_markdown_for_pdf(
    pdf_path: Path,
    input_root: Path,
    config: dict[str, Any],
    manifest_entry: dict[str, Any] | None = None,
    markdown_stem_index: dict[str, Path] | None = None,
) -> Path | None:
    del markdown_stem_index

    if output_markdown_matches_current_layout(pdf_path, input_root, config, manifest_entry):
        output_markdown = str(manifest_entry.get("output_markdown", "")) if manifest_entry else ""
        if output_markdown:
            return Path(output_markdown)
    return None


def _find_manifest_entry_by_sha256(
    manifest: ManifestStore,
    sha256: str,
) -> tuple[str, dict[str, Any]] | None:
    if not sha256:
        return None
    for rel_key, entry in manifest.data.get("files", {}).items():
        if entry.get("status") != "success":
            continue
        if entry.get("sha256") != sha256:
            continue
        output_markdown = str(entry.get("output_markdown", "")).strip()
        if output_markdown and Path(output_markdown).exists():
            return rel_key, entry
    return None


def existing_markdown_for_pdf_by_sha256(
    manifest: ManifestStore,
    fingerprint: dict[str, Any] | None,
) -> tuple[str, Path] | None:
    """Fallback match: locate a successful entry whose PDF bytes match *fingerprint*.

    Returns ``(existing_rel_key, markdown_path)`` so callers can auto-heal the
    frontmatter with an alias for the PDF's current rel_key.
    """
    if not fingerprint:
        return None
    match = _find_manifest_entry_by_sha256(manifest, fingerprint.get("sha256", ""))
    if match is None:
        return None
    existing_rel_key, entry = match
    return existing_rel_key, Path(str(entry.get("output_markdown", "")))


def existing_markdown_for_duplicate_pdf(
    pdf_path: Path,
    input_root: Path,
    config: dict[str, Any],
    manifest: ManifestStore,
) -> tuple[str, Path] | None:
    """Find a converted canonical bundle for a numbered duplicate PDF.

    Zotero/Drive often produces files such as ``Paper.pdf`` and ``Paper 2.pdf``.
    When the canonical sibling is already converted, this check avoids another
    Marker run and lets the caller register the numbered file as an alias.
    """
    duplicate_group = main_duplicate_group_pdfs(pdf_path)
    if len(duplicate_group) < 2:
        return None
    if duplicate_group[0].resolve() == pdf_path.resolve():
        return None

    for candidate_pdf in duplicate_group:
        if candidate_pdf.resolve() == pdf_path.resolve():
            continue
        try:
            candidate_rel_key = to_posix_path_str(relative_pdf_path(candidate_pdf, input_root))
        except ValueError:
            continue
        candidate_entry = manifest.get(candidate_rel_key)
        if not candidate_entry or candidate_entry.get("status") != "success":
            continue
        if candidate_entry.get("document_role", "main") != "main":
            continue
        candidate_markdown = existing_markdown_for_pdf(
            candidate_pdf, input_root, config, candidate_entry,
        )
        if candidate_markdown is not None:
            return candidate_rel_key, candidate_markdown

        output_markdown = str(candidate_entry.get("output_markdown", "")).strip()
        if output_markdown and Path(output_markdown).exists():
            return candidate_rel_key, Path(output_markdown)

    return None


class ManifestStore(FrontmatterIndex):
    """Backward-compatible name for the frontmatter-backed state index."""

    pass


def _success_entries_with_output_markdown(
    manifest: ManifestStore,
    output_markdown: Path,
    exclude_rel_key: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    output_markdown_key = _path_match_key(output_markdown)
    matches: list[tuple[str, dict[str, Any]]] = []
    for rel_key, entry in manifest.data.get("files", {}).items():
        if rel_key == exclude_rel_key:
            continue
        if entry.get("status") != "success":
            continue
        if _path_match_key(entry.get("output_markdown")) != output_markdown_key:
            continue
        matches.append((rel_key, entry))
    return matches


def _success_entries_with_bundle_dir(
    manifest: ManifestStore,
    bundle_dir: Path,
    exclude_rel_key: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    bundle_dir_key = _path_match_key(bundle_dir)
    matches: list[tuple[str, dict[str, Any]]] = []
    for rel_key, entry in manifest.data.get("files", {}).items():
        if rel_key == exclude_rel_key:
            continue
        if entry.get("status") != "success":
            continue
        if _path_match_key(entry.get("markdown_bundle_dir")) != bundle_dir_key:
            continue
        matches.append((rel_key, entry))
    return matches


def write_failed_pdf_report(config: dict[str, Any], manifest: ManifestStore) -> None:
    failed_entries: list[dict[str, str]] = []
    for rel_key, entry in manifest.data.get("files", {}).items():
        if entry.get("status") != "failed":
            continue

        source_pdf = Path(entry.get("source_pdf", ""))
        failed_entries.append(
            {
                "rel_key": rel_key,
                "source_pdf": to_posix_path_str(source_pdf),
                "parent_dir": to_posix_path_str(source_pdf.parent) if str(source_pdf) else "",
                "failed_at": entry.get("failed_at", ""),
                "error": entry.get("error", ""),
            }
        )

    lines = [
        "# Failed PDF Report",
        f"# Updated At: {utc_now_iso()}",
        f"# Failed Count: {len(failed_entries)}",
        "",
    ]

    if failed_entries:
        for index, item in enumerate(sorted(failed_entries, key=lambda value: value["source_pdf"]), start=1):
            lines.extend(
                [
                    f"[{index}] Source PDF",
                    item["source_pdf"],
                    f"Parent Dir: {item['parent_dir']}",
                    f"Relative Key: {item['rel_key']}",
                    f"Failed At: {item['failed_at']}",
                    "Error:",
                    item["error"],
                    "",
                ]
            )
    else:
        lines.extend(["No failed PDFs.", ""])

    report_text = "\n".join(lines)
    report_path = failed_report_path(config)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")


def build_marker_command(config: dict[str, Any], pdf_path: Path, raw_output_dir: Path) -> list[str]:
    command = [
        config["marker_cli"],
        str(pdf_path),
        "--output_dir",
        str(raw_output_dir),
        "--output_format",
        config.get("output_format", "markdown"),
        "--disable_tqdm",
    ]

    if config.get("force_ocr", False):
        command.append("--force_ocr")
    if config.get("disable_image_extraction", False):
        command.append("--disable_image_extraction")
    if config.get("disable_multiprocessing", False):
        command.append("--disable_multiprocessing")
    if config.get("paginate_output", False):
        command.append("--paginate_output")
    return command


def build_marker_env(config: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    env["TORCH_DEVICE"] = config.get("torch_device", "cuda")
    env["HF_HOME"] = config["hf_home"]
    env["HUGGINGFACE_HUB_CACHE"] = str(Path(config["hf_home"]) / "hub")
    env["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_marker(config: dict[str, Any], pdf_path: Path, raw_output_dir: Path, logger) -> None:
    safe_rmtree(raw_output_dir, raw_root(config))
    raw_output_dir.mkdir(parents=True, exist_ok=True)

    command = build_marker_command(config, pdf_path, raw_output_dir)
    logger.info("Starting marker conversion: %s", pdf_path)
    logger.info("Marker command: %s", " ".join(f'"{part}"' if " " in part else part for part in command))

    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)

    try:
        result = subprocess.run(
            command,
            cwd=config.get("marker_repo_root"),
            env=build_marker_env(config),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            "Marker executable not found. Set marker_cli to an absolute path, or ensure it is available on PATH."
        ) from exc

    if result.returncode != 0:
        message = (
            f"marker conversion failed with exit code {result.returncode}\n"
            f"STDOUT:\n{result.stdout[-4000:]}\n"
            f"STDERR:\n{result.stderr[-4000:]}"
        )
        raise RuntimeError(message)


def safe_unlink(target: Path, allowed_root: Path) -> None:
    if not target.exists():
        return
    if not is_relative_to(target, allowed_root):
        raise ValueError(f"Refusing to delete path outside allowed root: {target}")
    target.unlink()


def delete_pdf_artifacts(
    rel_key: str,
    config: dict[str, Any],
    manifest: ManifestStore,
    logger,
) -> dict[str, Any]:
    entry = manifest.get(rel_key)
    if not entry:
        logger.warning("No frontmatter entry for rel_key=%s, nothing to delete", rel_key)
        return {"rel_key": rel_key, "deleted": False, "reason": "no_frontmatter_entry"}

    md_root = markdown_root(config)
    r_root = raw_root(config)
    deleted_paths: list[str] = []

    # Remove collection mirror symlinks/copies first
    mirror_paths = entry.get("mirror_paths", [])
    if mirror_paths:
        remove_collection_mirrors(mirror_paths, config, logger)
        deleted_paths.extend(mirror_paths)

    raw_dir = entry.get("raw_output_dir")
    if raw_dir:
        raw_dir_path = Path(raw_dir)
        if raw_dir_path.exists():
            safe_rmtree(raw_dir_path, r_root)
            deleted_paths.append(str(raw_dir_path))

    role = entry.get("document_role", "main")
    if role == "supporting":
        output_md = entry.get("output_markdown")
        if output_md:
            md_path = Path(output_md)
            other_md_refs = _success_entries_with_output_markdown(
                manifest, md_path, exclude_rel_key=rel_key,
            )
            if other_md_refs:
                logger.info(
                    "Keeping shared supporting markdown for %s; still referenced by %s",
                    rel_key,
                    [other_rel_key for other_rel_key, _entry in other_md_refs],
                )
            else:
                safe_unlink(md_path, md_root)
                deleted_paths.append(str(md_path))

                assets_dir = md_path.with_name(md_path.stem + "_assets")
                if assets_dir.exists():
                    safe_rmtree(assets_dir, md_root)
                    deleted_paths.append(str(assets_dir))
    else:
        bundle_dir = entry.get("markdown_bundle_dir")
        if bundle_dir:
            bundle_path = Path(bundle_dir)
            if bundle_path.exists():
                other_main_md_refs: list[tuple[str, dict[str, Any]]] = []
                output_md = entry.get("output_markdown")
                if output_md:
                    other_main_md_refs = _success_entries_with_output_markdown(
                        manifest, Path(output_md), exclude_rel_key=rel_key,
                    )
                other_bundle_refs = _success_entries_with_bundle_dir(
                    manifest, bundle_path, exclude_rel_key=rel_key,
                )
                if other_main_md_refs:
                    logger.info(
                        "Keeping shared primary bundle for %s; still referenced by %s",
                        rel_key,
                        [other_rel_key for other_rel_key, _entry in other_main_md_refs],
                    )
                elif other_bundle_refs:
                    remove_primary_bundle_content(bundle_path, md_root)
                    deleted_paths.append(str(bundle_path))
                    logger.info(
                        "Removed primary bundle content for %s but kept shared bundle for %s",
                        rel_key,
                        [other_rel_key for other_rel_key, _entry in other_bundle_refs],
                    )
                else:
                    safe_rmtree(bundle_path, md_root)
                    deleted_paths.append(str(bundle_path))

    manifest.remove_entry(rel_key)
    logger.info("Deleted artifacts for %s: %s", rel_key, deleted_paths)
    return {"rel_key": rel_key, "deleted": True, "paths": deleted_paths}


def archive_pdf_artifacts(
    rel_key: str,
    config: dict[str, Any],
    manifest: ManifestStore,
    logger,
) -> dict[str, Any]:
    entry = manifest.get(rel_key)
    if not entry:
        logger.warning("No frontmatter entry for rel_key=%s, nothing to archive", rel_key)
        return {"rel_key": rel_key, "archived": False, "reason": "no_frontmatter_entry"}

    md_root = markdown_root(config)
    archive_root = output_root(config) / "archive" / utc_now_iso().replace(":", "-")
    archived_paths: list[str] = []

    mirror_paths = entry.get("mirror_paths", [])
    if mirror_paths:
        remove_collection_mirrors(mirror_paths, config, logger)

    role = entry.get("document_role", "main")
    if role == "supporting":
        output_md = entry.get("output_markdown")
        if output_md:
            md_path = Path(output_md)
            if md_path.exists():
                destination = archive_root / entry.get("source_relpath", rel_key)
                destination = destination.with_suffix(".md")
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not is_relative_to(md_path, md_root):
                    raise ValueError(f"Refusing to archive path outside markdown root: {md_path}")
                shutil.move(str(md_path), str(destination))
                archived_paths.append(str(destination))

                assets_dir = md_path.with_name(md_path.stem + "_assets")
                if assets_dir.exists():
                    assets_destination = destination.with_name(destination.stem + "_assets")
                    shutil.move(str(assets_dir), str(assets_destination))
                    archived_paths.append(str(assets_destination))
    else:
        bundle_dir = entry.get("markdown_bundle_dir")
        if bundle_dir:
            bundle_path = Path(bundle_dir)
            if bundle_path.exists():
                if not is_relative_to(bundle_path, md_root):
                    raise ValueError(f"Refusing to archive path outside markdown root: {bundle_path}")
                destination = archive_root / entry.get("source_relpath", rel_key)
                destination = destination.with_suffix("")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(bundle_path), str(destination))
                archived_paths.append(str(destination))

    manifest.remove_entry(rel_key)
    logger.info("Archived artifacts for %s: %s", rel_key, archived_paths)
    return {
        "rel_key": rel_key,
        "archived": bool(archived_paths),
        "archive_root": str(archive_root),
        "paths": archived_paths,
    }


def _get_zotero_map(config: dict[str, Any]) -> ZoteroCollectionMap | None:
    """Return a *ZoteroCollectionMap* if ``zotero_db_path`` is configured."""
    if config.get("run_mode") == "runner":
        return None
    db_path = config.get("zotero_db_path")
    if not db_path:
        return None
    return ZoteroCollectionMap(db_path)


def build_conversion_metadata(
    pdf_path: Path,
    input_root: Path,
    config: dict[str, Any],
    zotero_map: ZoteroCollectionMap | None = None,
) -> dict[str, Any]:
    rel_pdf = relative_pdf_path(pdf_path, input_root)
    fingerprint = pdf_fingerprint(
        pdf_path,
        use_sha256=config.get("compute_sha256", True),
    )
    metadata: dict[str, Any] = {
        "source_pdf": to_posix_path_str(pdf_path),
        "source_relpath": to_posix_path_str(rel_pdf),
        "source_filename": pdf_path.name,
        "source_size": fingerprint["size"],
        "source_mtime_ns": fingerprint["mtime_ns"],
        "converter": "marker_single",
        "marker_version": detect_marker_version(),
        "converted_at": utc_now_iso(),
        "torch_device": config.get("torch_device", "cuda"),
        "force_ocr": bool(config.get("force_ocr", False)),
    }
    if "sha256" in fingerprint:
        metadata["source_pdf_sha256"] = fingerprint["sha256"]

    # Zotero collection hierarchy + stable item keys
    if zotero_map is not None:
        collections = zotero_map.get_collections_for_pdf(pdf_path.name)
        if collections:
            metadata["zotero_collections"] = collections

        zotero_meta = zotero_map.get_metadata_for_pdf(pdf_path.name)
        if zotero_meta:
            if zotero_meta.get("item_key"):
                metadata["zotero_item_key"] = zotero_meta["item_key"]
            if zotero_meta.get("attachment_key"):
                metadata["zotero_attachment_key"] = zotero_meta["attachment_key"]
            metadata["annotations_count"] = int(zotero_meta.get("annotation_count", 0))

    return metadata


def looks_like_supporting_markdown(markdown_path: Path) -> bool:
    lead_text = markdown_path.read_text(encoding="utf-8", errors="replace")[:4000].lower()
    normalized_text = re.sub(r"[^0-9a-z]+", "", lead_text)
    return SUPPORTING_CONTENT_MARKER in normalized_text


def _normalize_markdown_for_dedupe(markdown_path: Path) -> str:
    _metadata, body = parse_frontmatter(markdown_path)
    body = body.lstrip("\ufeff").lstrip()
    if body[:12].lower() == "## full text":
        body = body[12:].lstrip(" \t\r\n#")
    return re.sub(r"[^0-9a-z]+", "", body.lower())


def _markdowns_are_near_duplicates(
    path_a: Path,
    path_b: Path,
    normalized_cache: dict[Path, str],
) -> bool:
    text_a = normalized_cache.setdefault(path_a, _normalize_markdown_for_dedupe(path_a))
    text_b = normalized_cache.setdefault(path_b, _normalize_markdown_for_dedupe(path_b))

    if not text_a or not text_b:
        return False

    min_len = min(len(text_a), len(text_b))
    max_len = max(len(text_a), len(text_b))
    if min_len < DUPLICATE_MARKDOWN_MIN_NORMALIZED_LEN:
        return False
    if min_len / max_len < DUPLICATE_MARKDOWN_MIN_LENGTH_RATIO:
        return False
    if text_a == text_b:
        return True

    length_delta_limit = max(
        DUPLICATE_MARKDOWN_MAX_CHAR_DELTA,
        int(max_len * (1 - DUPLICATE_MARKDOWN_MIN_LENGTH_RATIO)),
    )
    if text_a in text_b or text_b in text_a:
        return max_len - min_len <= length_delta_limit

    similarity = max(
        SequenceMatcher(None, text_a, text_b, autojunk=False).ratio(),
        SequenceMatcher(None, text_b, text_a, autojunk=False).ratio(),
    )
    return similarity >= DUPLICATE_MARKDOWN_SIMILARITY_THRESHOLD


def _supporting_markdown_sort_key(markdown_path: Path) -> tuple[int, str]:
    match = SUPPORTING_MARKDOWN_FILE_RE.fullmatch(markdown_path.name)
    if not match:
        return 10**9, markdown_path.name.lower()

    index_text = match.group("index")
    index = 1 if index_text is None else int(index_text)
    return index, markdown_path.name.lower()


def _repoint_manifest_output_markdown(
    manifest: ManifestStore,
    old_markdown_path: Path,
    canonical_markdown_path: Path,
) -> list[str]:
    canonical_markdown_str = str(canonical_markdown_path)
    old_markdown_key = _path_match_key(old_markdown_path)
    updated_rel_keys: list[str] = []

    for rel_key, entry in manifest.data.get("files", {}).items():
        if entry.get("status") != "success":
            continue
        if _path_match_key(entry.get("output_markdown")) != old_markdown_key:
            continue
        entry["output_markdown"] = canonical_markdown_str
        entry["markdown_bundle_dir"] = str(canonical_markdown_path.parent)
        updated_rel_keys.append(rel_key)

    if updated_rel_keys:
        manifest.save()
    return updated_rel_keys


def _remove_supporting_markdown_artifacts(markdown_path: Path, config: dict[str, Any]) -> None:
    safe_unlink(markdown_path, markdown_root(config))
    assets_dir = markdown_path.with_name(markdown_path.stem + "_assets")
    if assets_dir.exists():
        safe_rmtree(assets_dir, markdown_root(config))


def dedupe_supporting_markdown_bundle(
    bundle_dir: Path,
    current_markdown_path: Path,
    config: dict[str, Any],
    manifest: ManifestStore,
    logger,
) -> Path:
    supporting_paths = sorted(
        (
            path
            for path in bundle_dir.iterdir()
            if path.is_file() and SUPPORTING_MARKDOWN_FILE_RE.fullmatch(path.name)
        ),
        key=_supporting_markdown_sort_key,
    )
    if len(supporting_paths) < 2:
        return current_markdown_path

    normalized_cache: dict[Path, str] = {}
    canonical_paths: list[Path] = []
    duplicate_pairs: list[tuple[Path, Path]] = []

    for markdown_path in supporting_paths:
        matched_canonical: Path | None = None
        for canonical_path in canonical_paths:
            if _markdowns_are_near_duplicates(markdown_path, canonical_path, normalized_cache):
                matched_canonical = canonical_path
                break
        if matched_canonical is None:
            canonical_paths.append(markdown_path)
            continue
        duplicate_pairs.append((markdown_path, matched_canonical))

    canonical_for_current = current_markdown_path
    for duplicate_path, canonical_path in duplicate_pairs:
        updated_rel_keys = _repoint_manifest_output_markdown(
            manifest, duplicate_path, canonical_path,
        )
        _remove_supporting_markdown_artifacts(duplicate_path, config)
        logger.info(
            "Removed duplicate supporting markdown: %s -> keep %s%s",
            duplicate_path,
            canonical_path,
            f" (updated frontmatter index: {updated_rel_keys})" if updated_rel_keys else "",
        )
        if duplicate_path == current_markdown_path:
            canonical_for_current = canonical_path

    return canonical_for_current


def _iter_supporting_markdown_paths(bundle_dir: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in bundle_dir.iterdir()
            if path.is_file() and SUPPORTING_MARKDOWN_FILE_RE.fullmatch(path.name)
        ),
        key=_supporting_markdown_sort_key,
    )


def _find_main_markdown_in_bundle(bundle_dir: Path, expected_stem: str) -> Path | None:
    expected = bundle_dir / f"{expected_stem}.md"
    if expected.exists():
        return expected

    candidates = [
        path
        for path in bundle_dir.glob("*.md")
        if path.is_file() and not SUPPORTING_MARKDOWN_FILE_RE.fullmatch(path.name)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_size)


def _next_available_supporting_index(bundle_dir: Path) -> int:
    index = 1
    while True:
        if not (bundle_dir / supporting_markdown_name(index)).exists() and not (
            bundle_dir / supporting_assets_dir_name(index)
        ).exists():
            return index
        index += 1


def _repoint_manifest_primary_markdown(
    manifest: ManifestStore,
    old_markdown_path: Path,
    canonical_markdown_path: Path,
    canonical_source_pdf: Path,
    input_root: Path,
    config: dict[str, Any] | None = None,
    logger=None,
) -> list[str]:
    canonical_markdown_str = str(canonical_markdown_path)
    old_markdown_key = _path_match_key(old_markdown_path)
    canonical_source_pdf_str = to_posix_path_str(canonical_source_pdf)
    canonical_source_relpath = to_posix_path_str(relative_pdf_path(canonical_source_pdf, input_root))
    updated_rel_keys: list[str] = []

    for rel_key, entry in manifest.data.get("files", {}).items():
        if entry.get("status") != "success":
            continue
        if _path_match_key(entry.get("output_markdown")) != old_markdown_key:
            continue
        if entry.get("document_role") == "supporting":
            continue

        old_mirror_paths = list(entry.get("mirror_paths", []))
        if old_mirror_paths and config is not None and logger is not None:
            remove_collection_mirrors(old_mirror_paths, config, logger)

        entry["output_markdown"] = canonical_markdown_str
        entry["markdown_bundle_dir"] = str(canonical_markdown_path.parent)
        entry["document_role"] = "main"
        entry["canonical_source_pdf"] = canonical_source_pdf_str
        entry["canonical_source_relpath"] = canonical_source_relpath
        entry["canonical_source_filename"] = canonical_source_pdf.name
        entry["mirror_paths"] = []
        for field in [
            "supporting_index",
            "primary_source_pdf",
            "primary_source_relpath",
            "primary_source_filename",
        ]:
            entry.pop(field, None)
        updated_rel_keys.append(rel_key)

    if updated_rel_keys:
        manifest.save()
    return updated_rel_keys


def _merge_supporting_artifacts_into_bundle(
    source_bundle_dir: Path,
    target_bundle_dir: Path,
    config: dict[str, Any],
    manifest: ManifestStore,
    logger,
) -> None:
    if source_bundle_dir == target_bundle_dir or not source_bundle_dir.exists():
        return

    normalized_cache: dict[Path, str] = {}
    moved_targets: list[Path] = []

    for source_md in _iter_supporting_markdown_paths(source_bundle_dir):
        target_supporting_paths = _iter_supporting_markdown_paths(target_bundle_dir)
        matched_target: Path | None = None
        for target_md in target_supporting_paths:
            if _markdowns_are_near_duplicates(source_md, target_md, normalized_cache):
                matched_target = target_md
                break

        if matched_target is not None:
            updated_rel_keys = _repoint_manifest_output_markdown(
                manifest, source_md, matched_target,
            )
            _remove_supporting_markdown_artifacts(source_md, config)
            logger.info(
                "Merged duplicate supporting markdown from duplicate primary bundle: %s -> %s%s",
                source_md,
                matched_target,
                f" (updated frontmatter index: {updated_rel_keys})" if updated_rel_keys else "",
            )
            continue

        target_index = _next_available_supporting_index(target_bundle_dir)
        target_md = target_bundle_dir / supporting_markdown_name(target_index)
        source_assets = source_md.with_name(source_md.stem + "_assets")
        target_assets = target_bundle_dir / supporting_assets_dir_name(target_index)

        source_md.rename(target_md)
        if source_assets.exists():
            source_assets.rename(target_assets)

        updated_rel_keys = _repoint_manifest_output_markdown(
            manifest, source_md, target_md,
        )
        moved_targets.append(target_md)
        logger.info(
            "Moved supporting markdown into canonical primary bundle: %s -> %s%s",
            source_md,
            target_md,
            f" (updated frontmatter index: {updated_rel_keys})" if updated_rel_keys else "",
        )

    if moved_targets:
        dedupe_supporting_markdown_bundle(
            target_bundle_dir,
            moved_targets[-1],
            config,
            manifest,
            logger,
        )


def dedupe_primary_markdown_bundle(
    current_pdf: Path,
    current_markdown_path: Path,
    input_root: Path,
    config: dict[str, Any],
    manifest: ManifestStore,
    logger,
) -> tuple[Path, Path]:
    duplicate_group_pdfs = main_duplicate_group_pdfs(current_pdf)
    if len(duplicate_group_pdfs) < 2:
        return current_markdown_path, current_pdf

    group_order = {pdf.resolve(): index for index, pdf in enumerate(duplicate_group_pdfs)}
    normalized_cache: dict[Path, str] = {}
    available_main_markdowns: list[tuple[Path, Path]] = []

    for group_pdf in duplicate_group_pdfs:
        if group_pdf == current_pdf:
            available_main_markdowns.append((group_pdf, current_markdown_path))
            continue

        bundle_dir = bundle_dir_for_pdf(group_pdf, input_root, config)
        if not bundle_dir.exists():
            continue
        group_md = _find_main_markdown_in_bundle(bundle_dir, group_pdf.stem)
        if group_md is None or not group_md.exists():
            continue
        available_main_markdowns.append((group_pdf, group_md))

    if len(available_main_markdowns) < 2:
        return current_markdown_path, current_pdf

    duplicate_group: list[tuple[Path, Path]] = [(current_pdf, current_markdown_path)]
    for group_pdf, group_md in available_main_markdowns:
        if group_pdf == current_pdf:
            continue
        if _markdowns_are_near_duplicates(current_markdown_path, group_md, normalized_cache):
            duplicate_group.append((group_pdf, group_md))

    if len(duplicate_group) < 2:
        return current_markdown_path, current_pdf

    canonical_pdf, canonical_md = min(
        duplicate_group,
        key=lambda item: (
            group_order.get(item[0].resolve(), 10**9),
            len(item[0].stem),
            item[0].name.lower(),
        ),
    )
    canonical_bundle_dir = canonical_md.parent

    for duplicate_pdf, duplicate_md in duplicate_group:
        if duplicate_pdf == canonical_pdf and duplicate_md == canonical_md:
            continue

        duplicate_bundle_dir = duplicate_md.parent
        _merge_supporting_artifacts_into_bundle(
            duplicate_bundle_dir, canonical_bundle_dir, config, manifest, logger,
        )
        updated_rel_keys = _repoint_manifest_primary_markdown(
            manifest,
            duplicate_md,
            canonical_md,
            canonical_pdf,
            input_root,
            config=config,
            logger=logger,
        )

        if duplicate_bundle_dir.exists() and duplicate_bundle_dir != canonical_bundle_dir:
            remove_primary_bundle_content(duplicate_bundle_dir, markdown_root(config))
            if duplicate_bundle_dir.exists() and not any(duplicate_bundle_dir.iterdir()):
                duplicate_bundle_dir.rmdir()

        logger.info(
            "Merged duplicate primary markdown bundle: %s -> keep %s%s",
            duplicate_bundle_dir,
            canonical_bundle_dir,
            f" (updated frontmatter index: {updated_rel_keys})" if updated_rel_keys else "",
        )

    return canonical_md, canonical_pdf


def output_markdown_matches_current_layout(
    pdf_path: Path,
    input_root: Path,
    config: dict[str, Any],
    manifest_entry: dict[str, Any] | None,
) -> bool:
    if not manifest_entry or manifest_entry.get("status") != "success":
        return False

    output_markdown_str = str(manifest_entry.get("output_markdown", "")).strip()
    if not output_markdown_str:
        return False

    output_markdown_path = Path(output_markdown_str)
    if not output_markdown_path.exists():
        return False
    output_markdown_key = _path_match_key(output_markdown_path)

    supporting_info = supporting_source_info(pdf_path)
    if supporting_info:
        primary_pdf, _supporting_index = supporting_info
        primary_bundle_dir = bundle_dir_for_pdf(primary_pdf, input_root, config)
        return (
            _path_match_key(output_markdown_path.parent) == _path_match_key(primary_bundle_dir)
            and SUPPORTING_MARKDOWN_FILE_RE.fullmatch(output_markdown_path.name) is not None
        )

    expected_main_md = bundle_dir_for_pdf(pdf_path, input_root, config) / f"{pdf_path.stem}.md"
    if output_markdown_key == _path_match_key(expected_main_md):
        return True

    canonical_source_pdf = str(manifest_entry.get("canonical_source_pdf", "")).strip()
    if not canonical_source_pdf:
        return False

    canonical_pdf = Path(canonical_source_pdf)
    canonical_main_md = (
        bundle_dir_for_pdf(canonical_pdf, input_root, config) / f"{canonical_pdf.stem}.md"
    )
    return output_markdown_key == _path_match_key(canonical_main_md)


def cleanup_standalone_supporting_bundle(
    pdf_path: Path,
    primary_pdf: Path,
    input_root: Path,
    config: dict[str, Any],
    logger=None,
) -> bool:
    standalone_bundle_dir = bundle_dir_for_pdf(pdf_path, input_root, config)
    primary_bundle_dir = bundle_dir_for_pdf(primary_pdf, input_root, config)
    if standalone_bundle_dir == primary_bundle_dir or not standalone_bundle_dir.exists():
        return False

    safe_rmtree(standalone_bundle_dir, markdown_root(config))
    if logger is not None:
        logger.info(
            "Removed standalone supporting bundle after merging into primary bundle: %s -> %s",
            standalone_bundle_dir,
            primary_bundle_dir,
        )
    return True


def build_manifest_runtime_metadata(
    pdf_path: Path,
    input_root: Path,
    final_md: Path,
    mirror_paths: list[str],
    config: dict[str, Any],
    canonical_source_pdf: Path | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "torch_device": config.get("torch_device", "cuda"),
        "force_ocr": bool(config.get("force_ocr", False)),
        "markdown_bundle_dir": str(final_md.parent),
        "mirror_paths": [],
    }

    supporting_info = supporting_source_info(pdf_path)
    if supporting_info and SUPPORTING_MARKDOWN_FILE_RE.fullmatch(final_md.name):
        primary_pdf, supporting_index = supporting_info
        metadata["document_role"] = "supporting"
        metadata["supporting_index"] = supporting_index
        metadata["primary_source_pdf"] = to_posix_path_str(primary_pdf)
        metadata["primary_source_relpath"] = to_posix_path_str(relative_pdf_path(primary_pdf, input_root))
        metadata["primary_source_filename"] = primary_pdf.name
        return metadata

    metadata["document_role"] = "main"
    if canonical_source_pdf is not None and canonical_source_pdf != pdf_path:
        metadata["canonical_source_pdf"] = to_posix_path_str(canonical_source_pdf)
        metadata["canonical_source_relpath"] = to_posix_path_str(relative_pdf_path(canonical_source_pdf, input_root))
        metadata["canonical_source_filename"] = canonical_source_pdf.name
        metadata["mirror_paths"] = []
    return metadata


def remove_primary_bundle_content(bundle_dir: Path, allowed_root: Path) -> None:
    if not bundle_dir.exists():
        bundle_dir.mkdir(parents=True, exist_ok=True)
        return

    if not bundle_dir.is_dir():
        raise NotADirectoryError(
            f"Expected bundle directory but found non-directory at {bundle_dir}. "
            "This usually indicates leftover mirror artifacts or a stale markdown file "
            "sitting at the bundle path; move or delete that file, then ensure the PDF's sha256 "
            "is not already present elsewhere in the index."
        )

    for child in bundle_dir.iterdir():
        if is_supporting_artifact_name(child.name):
            continue
        if child.is_dir():
            safe_rmtree(child, allowed_root)
        else:
            safe_unlink(child, allowed_root)


def build_supporting_path_map(
    copy_root: Path,
    main_raw_md: Path,
    asset_dir_name: str,
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for path in copy_root.rglob("*"):
        if not path.is_file() or path == main_raw_md:
            continue
        source_relative = path.relative_to(copy_root)
        rel_from_md = to_posix_path_str(Path(os.path.relpath(path, start=main_raw_md.parent)))
        new_relative = to_posix_path_str(Path(asset_dir_name) / source_relative)
        mapping[rel_from_md] = new_relative
        mapping[f"./{rel_from_md}"] = new_relative
    return mapping


def rewrite_supporting_markdown_links(
    markdown_text: str,
    copy_root: Path,
    main_raw_md: Path,
    asset_dir_name: str,
) -> str:
    updated = markdown_text
    path_map = build_supporting_path_map(copy_root, main_raw_md, asset_dir_name)
    for old_path, new_path in sorted(path_map.items(), key=lambda item: len(item[0]), reverse=True):
        updated = updated.replace(f"]({old_path})", f"]({new_path})")
        updated = updated.replace(f'"{old_path}"', f'"{new_path}"')
        updated = updated.replace(f"'{old_path}'", f"'{new_path}'")
    return updated


def copy_supporting_assets(copy_root: Path, main_raw_md: Path, target_dir: Path) -> None:
    if target_dir.exists():
        safe_rmtree(target_dir, target_dir.parent)
    target_dir.mkdir(parents=True, exist_ok=True)

    for path in copy_root.rglob("*"):
        if path == main_raw_md:
            continue
        relative_path = path.relative_to(copy_root)
        destination = target_dir / relative_path
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def create_collection_mirrors(
    bundle_dir: Path,
    pdf_path: Path,
    input_root: Path,
    config: dict[str, Any],
    zotero_map: ZoteroCollectionMap | None,
    logger,
) -> list[str]:
    """Create symlink mirrors for each extra Zotero collection the PDF belongs to.

    Returns a list of mirror directory paths (as posix strings) that were created.
    The bundle's own physical location is excluded from mirroring.
    """
    if config.get("run_mode") == "runner":
        return []

    if zotero_map is None:
        return []

    collections = zotero_map.get_collections_for_pdf(pdf_path.name)
    if not collections:
        return []

    md_root = markdown_root(config)
    # The physical collection path for this PDF (derived from input_root structure)
    physical_relpath = pdf_bundle_relpath(relative_pdf_path(pdf_path, input_root))
    physical_collection = str(physical_relpath.parent).replace("\\", "/")

    mirror_mode = config.get("collection_mirror_mode", "symlink")
    if mirror_mode == "none":
        return []
    mirror_paths: list[str] = []

    for col_path in collections:
        # Skip if this collection matches the physical location
        if col_path == physical_collection:
            continue

        mirror_dir = md_root / col_path / pdf_path.stem
        if mirror_dir.exists():
            # Already exists (could be from a previous run)
            if mirror_dir.is_symlink() or mirror_dir.is_dir():
                continue

        mirror_dir.parent.mkdir(parents=True, exist_ok=True)

        if mirror_mode == "symlink":
            try:
                mirror_dir.symlink_to(bundle_dir)
                logger.info("Created mirror symlink: %s -> %s", mirror_dir, bundle_dir)
            except OSError as exc:
                logger.warning("Failed to create symlink %s: %s; falling back to copy", mirror_dir, exc)
                shutil.copytree(bundle_dir, mirror_dir, dirs_exist_ok=True)
        else:
            shutil.copytree(bundle_dir, mirror_dir, dirs_exist_ok=True)
            logger.info("Created mirror copy: %s", mirror_dir)

        mirror_paths.append(to_posix_path_str(mirror_dir))

    return mirror_paths


def remove_collection_mirrors(
    mirror_paths: list[str],
    config: dict[str, Any],
    logger,
) -> None:
    """Remove symlink mirrors (or copied directories) listed in *mirror_paths*."""
    md_root = markdown_root(config)
    for mp in mirror_paths:
        path = Path(mp)
        if not path.exists() and not path.is_symlink():
            continue
        if not is_relative_to(path, md_root):
            logger.warning("Mirror path outside markdown root, skipping: %s", path)
            continue
        if path.is_symlink():
            path.unlink()
            logger.info("Removed mirror symlink: %s", path)
        elif path.is_dir():
            safe_rmtree(path, md_root)
            logger.info("Removed mirror directory: %s", path)

        # Clean up empty parent directories up to markdown_root
        parent = path.parent
        while parent != md_root and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
            logger.info("Removed empty directory: %s", parent)
            parent = parent.parent


def materialize_primary_bundle(
    config: dict[str, Any],
    pdf_path: Path,
    input_root: Path,
    copy_root: Path,
    main_raw_md: Path,
    zotero_map: ZoteroCollectionMap | None = None,
    logger=None,
) -> tuple[Path, list[str]]:
    bundle_dir = bundle_dir_for_pdf(pdf_path, input_root, config)
    remove_primary_bundle_content(bundle_dir, markdown_root(config))

    shutil.copytree(copy_root, bundle_dir, dirs_exist_ok=True)

    main_bundle_md = bundle_dir / main_raw_md.relative_to(copy_root)
    desired_md_name = pdf_path.stem + ".md"
    desired_md_path = main_bundle_md.with_name(desired_md_name)

    if main_bundle_md != desired_md_path:
        if desired_md_path.exists():
            safe_unlink(desired_md_path, markdown_root(config))
        main_bundle_md.rename(desired_md_path)
        main_bundle_md = desired_md_path

    metadata = build_conversion_metadata(pdf_path, input_root, config, zotero_map=zotero_map)
    metadata["document_role"] = "main"
    write_frontmatter_markdown(main_bundle_md, metadata)

    return main_bundle_md, []


def materialize_supporting_bundle(
    config: dict[str, Any],
    pdf_path: Path,
    input_root: Path,
    copy_root: Path,
    main_raw_md: Path,
    primary_pdf: Path,
    supporting_index: int,
    zotero_map: ZoteroCollectionMap | None = None,
    logger=None,
) -> Path:
    bundle_dir = bundle_dir_for_pdf(primary_pdf, input_root, config)
    if bundle_dir.exists() and not bundle_dir.is_dir():
        existing_primary_markdown = _existing_main_markdown_by_stem(primary_pdf, config)
        if existing_primary_markdown is not None and existing_primary_markdown.parent.exists():
            if logger is not None:
                logger.info(
                    "Using existing primary bundle for supporting PDF because target path is not a directory: %s -> %s",
                    bundle_dir,
                    existing_primary_markdown.parent,
                )
            bundle_dir = existing_primary_markdown.parent
    bundle_dir.mkdir(parents=True, exist_ok=True)
    cleanup_standalone_supporting_bundle(
        pdf_path, primary_pdf, input_root, config, logger=logger,
    )

    target_md = bundle_dir / supporting_markdown_name(supporting_index)
    asset_dir = bundle_dir / supporting_assets_dir_name(supporting_index)
    safe_unlink(target_md, markdown_root(config))
    if asset_dir.exists():
        safe_rmtree(asset_dir, markdown_root(config))

    copy_supporting_assets(copy_root, main_raw_md, asset_dir)
    supporting_text = main_raw_md.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
    supporting_text = rewrite_supporting_markdown_links(
        supporting_text,
        copy_root=copy_root,
        main_raw_md=main_raw_md,
        asset_dir_name=asset_dir.name,
    )
    target_md.write_text(supporting_text, encoding="utf-8")

    metadata = build_conversion_metadata(pdf_path, input_root, config, zotero_map=zotero_map)
    metadata["document_role"] = "supporting"
    metadata["supporting_index"] = supporting_index
    metadata["primary_source_pdf"] = to_posix_path_str(primary_pdf)
    metadata["primary_source_relpath"] = to_posix_path_str(relative_pdf_path(primary_pdf, input_root))
    metadata["primary_source_filename"] = primary_pdf.name
    write_frontmatter_markdown(target_md, metadata)
    return target_md


def materialize_final_bundle(
    config: dict[str, Any],
    pdf_path: Path,
    input_root: Path,
    raw_output_dir: Path,
    logger=None,
) -> tuple[Path, list[str]]:
    copy_root = detect_marker_content_root(raw_output_dir)
    main_raw_md = find_main_markdown(copy_root)
    supporting_info = supporting_source_info(pdf_path)

    zotero_map = _get_zotero_map(config)

    if supporting_info:
        primary_pdf, supporting_index = supporting_info
        if logger is not None and not looks_like_supporting_markdown(main_raw_md):
            logger.info(
                "Treating PDF as supporting based on filename grouping: %s -> %s",
                pdf_path,
                primary_pdf,
            )
        md_path = materialize_supporting_bundle(
            config,
            pdf_path=pdf_path,
            input_root=input_root,
            copy_root=copy_root,
            main_raw_md=main_raw_md,
            primary_pdf=primary_pdf,
            supporting_index=supporting_index,
            zotero_map=zotero_map,
            logger=logger,
        )
        return md_path, []

    return materialize_primary_bundle(
        config,
        pdf_path=pdf_path,
        input_root=input_root,
        copy_root=copy_root,
        main_raw_md=main_raw_md,
        zotero_map=zotero_map,
        logger=logger,
    )


def convert_one_pdf(
    pdf_path: str | Path,
    config_path: str | None = None,
    force_reconvert: bool = False,
) -> Path | None:
    config = load_config(config_path)
    ensure_directories(config)
    logger = setup_logger(config)

    input_root = Path(config["input_root"])
    pdf = Path(pdf_path).resolve()

    if not pdf.exists():
        raise FileNotFoundError(f"PDF does not exist: {pdf}")
    if not is_relative_to(pdf, input_root):
        raise ValueError(f"PDF is outside input_root: {pdf}")

    rel_key = str(relative_pdf_path(pdf, input_root)).replace("\\", "/")
    fingerprint = pdf_fingerprint(pdf, use_sha256=config.get("compute_sha256", True))
    manifest = ManifestStore(config)
    existing_entry = manifest.get(rel_key)

    if not force_reconvert:
        existing_markdown = existing_markdown_for_pdf(pdf, input_root, config, existing_entry)
        if existing_markdown is not None:
            supporting_info = supporting_source_info(pdf)
            if supporting_info:
                primary_pdf, _supporting_index = supporting_info
                cleanup_standalone_supporting_bundle(
                    pdf, primary_pdf, input_root, config, logger=logger,
                )
            logger.info("Skipping existing Markdown PDF: %s -> %s", rel_key, existing_markdown)
            return existing_markdown

        sha256_match = existing_markdown_for_pdf_by_sha256(manifest, fingerprint)
        if sha256_match is not None:
            existing_rel_key, existing_markdown = sha256_match
            if manifest.register_alias_for_rel_key(
                existing_rel_key, rel_key, pdf, fingerprint,
            ):
                logger.info(
                    "Skipping existing Markdown PDF via sha256 match: %s -> %s (aliased as %s)",
                    existing_rel_key, existing_markdown, rel_key,
                )
            else:
                logger.info(
                    "Skipping existing Markdown PDF via sha256 match: %s -> %s",
                    existing_rel_key, existing_markdown,
                )
            return existing_markdown

        duplicate_match = existing_markdown_for_duplicate_pdf(pdf, input_root, config, manifest)
        if duplicate_match is not None:
            existing_rel_key, existing_markdown = duplicate_match
            if manifest.register_alias_for_rel_key(
                existing_rel_key, rel_key, pdf, fingerprint,
            ):
                logger.info(
                    "Skipping numbered duplicate PDF: %s -> %s (aliased as %s)",
                    existing_rel_key, existing_markdown, rel_key,
                )
            else:
                logger.info(
                    "Skipping numbered duplicate PDF: %s -> %s",
                    existing_rel_key, existing_markdown,
                )
            return existing_markdown

    if not force_reconvert and manifest.is_unchanged(rel_key, fingerprint):
        logger.info(
            "Reprocessing unchanged PDF because markdown layout is missing or outdated: %s",
            rel_key,
        )
        if existing_entry and existing_entry.get("output_markdown"):
            logger.info("Current frontmatter output_markdown: %s", existing_entry["output_markdown"])

    raw_output_dir = raw_dir_for_pdf(pdf, input_root, config)

    write_conversion_status(config, rel_key, pdf)
    try:
        run_marker(config, pdf, raw_output_dir, logger)
        final_md, _mirror_paths = materialize_final_bundle(
            config, pdf, input_root, raw_output_dir, logger=logger,
        )
        mirror_paths: list[str] = []
        canonical_source_pdf = pdf
        if SUPPORTING_MARKDOWN_FILE_RE.fullmatch(final_md.name):
            if existing_entry and existing_entry.get("mirror_paths"):
                remove_collection_mirrors(existing_entry["mirror_paths"], config, logger)
            final_md = dedupe_supporting_markdown_bundle(
                bundle_dir=final_md.parent,
                current_markdown_path=final_md,
                config=config,
                manifest=manifest,
                logger=logger,
            )
        else:
            final_md, canonical_source_pdf = dedupe_primary_markdown_bundle(
                current_pdf=pdf,
                current_markdown_path=final_md,
                input_root=input_root,
                config=config,
                manifest=manifest,
                logger=logger,
            )
        manifest.mark_success(
            rel_key=rel_key,
            fingerprint=fingerprint,
            source_pdf=pdf,
            output_markdown=final_md,
            raw_dir=raw_output_dir,
            metadata=build_manifest_runtime_metadata(
                pdf, input_root, final_md, mirror_paths, config,
                canonical_source_pdf=canonical_source_pdf,
            ),
        )
        logger.info("Conversion completed: %s -> %s", rel_key, final_md)
        return final_md
    except Exception as exc:
        logger.exception("Conversion failed: %s", rel_key)
        manifest.mark_failure(rel_key, pdf, str(exc))
        raise
    finally:
        clear_conversion_status(config, rel_key)


def _convert_one_pdf_with_retries_unlocked(
    pdf_path: str | Path,
    config_path: str | None = None,
    force_reconvert: bool = False,
    max_retries: int = MAX_CONVERSION_RETRIES,
) -> Path | None:
    """Try to convert a single PDF, retrying up to *max_retries* times on failure.

    Returns the output markdown path on success, or raises the last exception
    after all retries are exhausted.
    """
    config = load_config(config_path)
    logger = setup_logger(config)
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            return convert_one_pdf(
                pdf_path, config_path=config_path, force_reconvert=force_reconvert,
            )
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Conversion attempt %s/%s failed for %s: %s",
                attempt, max_retries, pdf_path, exc,
            )
            if attempt < max_retries:
                logger.info("Retrying %s (attempt %s/%s) …", pdf_path, attempt + 1, max_retries)

    # All retries exhausted – re-raise the last exception
    raise last_exc  # type: ignore[misc]


def convert_one_pdf_with_retries(
    pdf_path: str | Path,
    config_path: str | None = None,
    force_reconvert: bool = False,
    max_retries: int = MAX_CONVERSION_RETRIES,
) -> Path | None:
    config = load_config(config_path)
    with ConversionLock(config, owner=f"single:{Path(pdf_path).name}"):
        return _convert_one_pdf_with_retries_unlocked(
            pdf_path,
            config_path=config_path,
            force_reconvert=force_reconvert,
            max_retries=max_retries,
        )


def _reconcile_only_report(
    pdfs: list[Path],
    config: dict[str, Any],
    input_root: Path,
    logger,
) -> dict[str, int]:
    manifest = ManifestStore(config)
    matched = 0
    missing: list[tuple[str, Path]] = []
    sha256_heal: list[tuple[str, str]] = []
    duplicate_aliases: list[tuple[str, str]] = []

    for pdf in pdfs:
        rel_key = str(relative_pdf_path(pdf, input_root)).replace("\\", "/")
        existing_entry = manifest.get(rel_key)
        if existing_markdown_for_pdf(pdf, input_root, config, existing_entry) is not None:
            matched += 1
            continue

        fingerprint = pdf_fingerprint(pdf, use_sha256=config.get("compute_sha256", True))
        sha256_match = existing_markdown_for_pdf_by_sha256(manifest, fingerprint)
        if sha256_match is not None:
            existing_rel_key, _existing_md = sha256_match
            sha256_heal.append((existing_rel_key, rel_key))
            matched += 1
            continue

        duplicate_match = existing_markdown_for_duplicate_pdf(pdf, input_root, config, manifest)
        if duplicate_match is not None:
            existing_rel_key, _existing_md = duplicate_match
            duplicate_aliases.append((existing_rel_key, rel_key))
            matched += 1
            continue

        missing.append((rel_key, pdf))

    logger.info(
        "Reconcile summary: matched=%s missing=%s sha256_rematch=%s numbered_duplicate=%s",
        matched,
        len(missing),
        len(sha256_heal),
        len(duplicate_aliases),
    )
    if sha256_heal:
        logger.info("%s PDF(s) match existing markdown by sha256 under a different rel_key "
                    "(alias would be written on next non-reconcile run):", len(sha256_heal))
        for old_rel, new_rel in sha256_heal:
            logger.info("  %s -> %s", old_rel, new_rel)
    if duplicate_aliases:
        logger.info("%s numbered duplicate PDF(s) can reuse existing markdown:",
                    len(duplicate_aliases))
        for old_rel, new_rel in duplicate_aliases:
            logger.info("  %s -> %s", old_rel, new_rel)
    if missing:
        logger.info("%s PDF(s) missing a markdown:", len(missing))
        for rel_key, _pdf in missing:
            logger.info("  %s", rel_key)
    return {
        "matched": matched,
        "missing": len(missing),
        "sha256_rematch": len(sha256_heal),
        "numbered_duplicate": len(duplicate_aliases),
        "converted": 0,
        "skipped": matched,
        "failed": 0,
    }


def _convert_all_pdfs_unlocked(
    config_path: str | None = None,
    force_reconvert: bool = False,
    limit: int | None = None,
    reconcile_only: bool = False,
) -> dict[str, int]:
    config = load_config(config_path)
    ensure_directories(config)
    logger = setup_logger(config)

    input_root = Path(config["input_root"])
    if not input_root.exists():
        raise FileNotFoundError(f"input_root does not exist: {input_root}")

    pdfs = find_all_pdfs(input_root)
    if limit is not None:
        pdfs = pdfs[:limit]

    logger.info("PDFs discovered: %s", len(pdfs))

    if reconcile_only:
        return _reconcile_only_report(pdfs, config, input_root, logger)

    converted = 0
    skipped = 0
    failed_pdfs: list[Path] = []
    failure_errors: dict[Path, str] = {}

    manifest = ManifestStore(config)
    # ── First pass ──────────────────────────────────────────────────────
    for pdf in pdfs:
        rel_key = str(relative_pdf_path(pdf, input_root)).replace("\\", "/")
        fingerprint = pdf_fingerprint(pdf, use_sha256=config.get("compute_sha256", True))
        existing_entry = manifest.get(rel_key)
        if not force_reconvert:
            existing_markdown = existing_markdown_for_pdf(
                pdf,
                input_root,
                config,
                existing_entry,
            )
            if existing_markdown is not None:
                logger.info("Skipping existing Markdown PDF: %s -> %s", rel_key, existing_markdown)
                skipped += 1
                continue

        if not force_reconvert and manifest.is_unchanged(rel_key, fingerprint):
            logger.info("Skipping unchanged PDF without current Markdown match: %s", rel_key)
            skipped += 1
            continue

        try:
            convert_one_pdf(pdf, config_path=config_path, force_reconvert=force_reconvert)
            converted += 1
            manifest = ManifestStore(config)
        except Exception as exc:
            failed_pdfs.append(pdf)
            failure_errors[pdf] = str(exc)
            manifest = ManifestStore(config)

    # ── Retry pass (up to MAX_CONVERSION_RETRIES rounds) ────────────────
    if failed_pdfs:
        logger.info(
            "First pass complete. %s PDF(s) failed, starting retry (max %s attempts per PDF) …",
            len(failed_pdfs), MAX_CONVERSION_RETRIES,
        )

    still_failed: list[Path] = []
    for pdf in failed_pdfs:
        success = False
        for attempt in range(1, MAX_CONVERSION_RETRIES + 1):
            try:
                logger.info(
                    "Retry attempt %s/%s for: %s", attempt, MAX_CONVERSION_RETRIES, pdf,
                )
                convert_one_pdf(pdf, config_path=config_path, force_reconvert=True)
                converted += 1
                success = True
                manifest = ManifestStore(config)
                break
            except Exception as exc:
                logger.warning(
                    "Retry attempt %s/%s failed for: %s", attempt, MAX_CONVERSION_RETRIES, pdf,
                )
                failure_errors[pdf] = str(exc)
                manifest = ManifestStore(config)

        if not success:
            still_failed.append(pdf)

    failed = len(still_failed)

    # ── Write final report (only truly persistent failures) ─────────────
    manifest = ManifestStore(config)
    for pdf in still_failed:
        rel_key = str(relative_pdf_path(pdf, input_root)).replace("\\", "/")
        manifest.mark_failure(rel_key, pdf, failure_errors.get(pdf, "conversion failed"))
    write_failed_pdf_report(config, manifest)

    summary = {"converted": converted, "skipped": skipped, "failed": failed}
    if still_failed:
        logger.warning(
            "PDFs that failed after all retries (%s): %s",
            MAX_CONVERSION_RETRIES,
            [str(p) for p in still_failed],
        )
    logger.info(
        "Batch finished: converted=%s skipped=%s failed=%s",
        converted,
        skipped,
        failed,
    )
    return summary


def convert_all_pdfs(
    config_path: str | None = None,
    force_reconvert: bool = False,
    limit: int | None = None,
    reconcile_only: bool = False,
) -> dict[str, int]:
    if reconcile_only:
        return _convert_all_pdfs_unlocked(
            config_path=config_path,
            force_reconvert=force_reconvert,
            limit=limit,
            reconcile_only=reconcile_only,
        )

    config = load_config(config_path)
    with ConversionLock(config, owner="batch"):
        return _convert_all_pdfs_unlocked(
            config_path=config_path,
            force_reconvert=force_reconvert,
            limit=limit,
            reconcile_only=reconcile_only,
        )
