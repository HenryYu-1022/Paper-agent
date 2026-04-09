from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

try:
    from .common import (
        bundle_dir_for_pdf,
        detect_marker_content_root,
        ensure_directories,
        failed_report_path,
        find_all_pdfs,
        find_main_markdown,
        is_relative_to,
        is_supporting_artifact_name,
        load_config,
        manifest_path,
        markdown_root,
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
        utc_now_iso,
        write_frontmatter_markdown,
    )
except ImportError:
    from common import (
        bundle_dir_for_pdf,
        detect_marker_content_root,
        ensure_directories,
        failed_report_path,
        find_all_pdfs,
        find_main_markdown,
        is_relative_to,
        is_supporting_artifact_name,
        load_config,
        manifest_path,
        markdown_root,
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
        utc_now_iso,
        write_frontmatter_markdown,
    )

SUPPORTING_CONTENT_MARKERS = (
    "supporting information",
    "supplementary information",
    "supplemental information",
)


class ManifestStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "files": {}}
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def get(self, rel_key: str) -> dict[str, Any] | None:
        return self.data.setdefault("files", {}).get(rel_key)

    def is_unchanged(self, rel_key: str, fingerprint: dict[str, Any]) -> bool:
        existing = self.get(rel_key)
        if not existing or existing.get("status") != "success":
            return False

        for key, value in fingerprint.items():
            if existing.get(key) != value:
                return False
        return True

    def mark_success(
        self,
        rel_key: str,
        fingerprint: dict[str, Any],
        source_pdf: Path,
        output_markdown: Path,
        raw_dir: Path,
        metadata: dict[str, Any],
    ) -> None:
        entry = {
            "status": "success",
            "source_pdf": str(source_pdf),
            "output_markdown": str(output_markdown),
            "raw_output_dir": str(raw_dir),
            "converted_at": utc_now_iso(),
            **fingerprint,
            **metadata,
        }
        self.data.setdefault("files", {})[rel_key] = entry
        self.save()

    def mark_failure(self, rel_key: str, source_pdf: Path, error: str) -> None:
        self.data.setdefault("files", {})[rel_key] = {
            "status": "failed",
            "source_pdf": str(source_pdf),
            "error": error,
            "failed_at": utc_now_iso(),
        }
        self.save()

    def remove_entry(self, rel_key: str) -> bool:
        files = self.data.get("files", {})
        if rel_key not in files:
            return False
        del files[rel_key]
        self.save()
        return True


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

    result = subprocess.run(
        command,
        cwd=config["marker_repo_root"],
        env=build_marker_env(config),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=creationflags,
        startupinfo=startupinfo,
    )
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
        logger.warning("No manifest entry for rel_key=%s, nothing to delete", rel_key)
        return {"rel_key": rel_key, "deleted": False, "reason": "no_manifest_entry"}

    md_root = markdown_root(config)
    r_root = raw_root(config)
    deleted_paths: list[str] = []

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
                safe_rmtree(bundle_path, md_root)
                deleted_paths.append(str(bundle_path))

    manifest.remove_entry(rel_key)
    logger.info("Deleted artifacts for %s: %s", rel_key, deleted_paths)
    return {"rel_key": rel_key, "deleted": True, "paths": deleted_paths}


def build_conversion_metadata(
    pdf_path: Path,
    source_dir: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    rel_pdf = relative_pdf_path(pdf_path, source_dir)
    metadata = {
        "source_pdf": to_posix_path_str(pdf_path),
        "source_relpath": to_posix_path_str(rel_pdf),
        "source_filename": pdf_path.name,
        "converter": "marker_single",
        "converted_at": utc_now_iso(),
        "torch_device": config.get("torch_device", "cuda"),
        "force_ocr": bool(config.get("force_ocr", False)),
    }
    if config.get("compute_sha256", False):
        metadata["source_sha256"] = pdf_fingerprint(pdf_path, use_sha256=True)["sha256"]
    return metadata


def looks_like_supporting_markdown(markdown_path: Path) -> bool:
    lead_text = markdown_path.read_text(encoding="utf-8", errors="replace")[:4000].lower()
    return any(marker in lead_text for marker in SUPPORTING_CONTENT_MARKERS)


def remove_primary_bundle_content(bundle_dir: Path, allowed_root: Path) -> None:
    if not bundle_dir.exists():
        bundle_dir.mkdir(parents=True, exist_ok=True)
        return

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


def materialize_primary_bundle(
    config: dict[str, Any],
    pdf_path: Path,
    source_dir: Path,
    copy_root: Path,
    main_raw_md: Path,
) -> Path:
    bundle_dir = bundle_dir_for_pdf(pdf_path, source_dir, config)
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

    metadata = build_conversion_metadata(pdf_path, source_dir, config)
    metadata["document_role"] = "main"
    write_frontmatter_markdown(main_bundle_md, metadata)
    return main_bundle_md


def materialize_supporting_bundle(
    config: dict[str, Any],
    pdf_path: Path,
    source_dir: Path,
    copy_root: Path,
    main_raw_md: Path,
    primary_pdf: Path,
    supporting_index: int,
) -> Path:
    bundle_dir = bundle_dir_for_pdf(primary_pdf, source_dir, config)
    bundle_dir.mkdir(parents=True, exist_ok=True)

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

    metadata = build_conversion_metadata(pdf_path, source_dir, config)
    metadata["document_role"] = "supporting"
    metadata["supporting_index"] = supporting_index
    metadata["primary_source_pdf"] = to_posix_path_str(primary_pdf)
    metadata["primary_source_relpath"] = to_posix_path_str(relative_pdf_path(primary_pdf, source_dir))
    metadata["primary_source_filename"] = primary_pdf.name
    write_frontmatter_markdown(target_md, metadata)
    return target_md


def materialize_final_bundle(
    config: dict[str, Any],
    pdf_path: Path,
    source_dir: Path,
    raw_output_dir: Path,
) -> Path:
    copy_root = detect_marker_content_root(raw_output_dir)
    main_raw_md = find_main_markdown(copy_root)
    supporting_info = supporting_source_info(pdf_path)
    if supporting_info and looks_like_supporting_markdown(main_raw_md):
        primary_pdf, supporting_index = supporting_info
        return materialize_supporting_bundle(
            config,
            pdf_path=pdf_path,
            source_dir=source_dir,
            copy_root=copy_root,
            main_raw_md=main_raw_md,
            primary_pdf=primary_pdf,
            supporting_index=supporting_index,
        )

    return materialize_primary_bundle(
        config,
        pdf_path=pdf_path,
        source_dir=source_dir,
        copy_root=copy_root,
        main_raw_md=main_raw_md,
    )


def convert_one_pdf(
    pdf_path: str | Path,
    config_path: str | None = None,
    force_reconvert: bool = False,
) -> Path | None:
    config = load_config(config_path)
    ensure_directories(config)
    logger = setup_logger(config)

    source_dir = Path(config["source_dir"])
    pdf = Path(pdf_path).resolve()

    if not pdf.exists():
        raise FileNotFoundError(f"PDF does not exist: {pdf}")
    if not is_relative_to(pdf, source_dir):
        raise ValueError(f"PDF is outside source_dir: {pdf}")

    rel_key = str(relative_pdf_path(pdf, source_dir)).replace("\\", "/")
    fingerprint = pdf_fingerprint(pdf, use_sha256=config.get("compute_sha256", False))
    manifest = ManifestStore(manifest_path(config))

    if not force_reconvert and manifest.is_unchanged(rel_key, fingerprint):
        logger.info("Skipping unchanged PDF: %s", rel_key)
        existing = manifest.get(rel_key)
        if existing:
            return Path(existing["output_markdown"])
        return None

    raw_output_dir = raw_dir_for_pdf(pdf, source_dir, config)

    try:
        run_marker(config, pdf, raw_output_dir, logger)
        final_md = materialize_final_bundle(config, pdf, source_dir, raw_output_dir)
        manifest.mark_success(
            rel_key=rel_key,
            fingerprint=fingerprint,
            source_pdf=pdf,
            output_markdown=final_md,
            raw_dir=raw_output_dir,
            metadata={
                "torch_device": config.get("torch_device", "cuda"),
                "force_ocr": bool(config.get("force_ocr", False)),
                "markdown_bundle_dir": str(final_md.parent),
            },
        )
        logger.info("Conversion completed: %s -> %s", rel_key, final_md)
        return final_md
    except Exception as exc:
        logger.exception("Conversion failed: %s", rel_key)
        manifest.mark_failure(rel_key, pdf, str(exc))
        raise


def convert_all_pdfs(
    config_path: str | None = None,
    force_reconvert: bool = False,
    limit: int | None = None,
) -> dict[str, int]:
    config = load_config(config_path)
    ensure_directories(config)
    logger = setup_logger(config)

    source_dir = Path(config["source_dir"])
    if not source_dir.exists():
        raise FileNotFoundError(f"source_dir does not exist: {source_dir}")

    pdfs = find_all_pdfs(source_dir)
    if limit is not None:
        pdfs = pdfs[:limit]

    logger.info("PDFs discovered: %s", len(pdfs))

    converted = 0
    skipped = 0
    failed = 0

    manifest = ManifestStore(manifest_path(config))
    write_failed_pdf_report(config, manifest)

    for pdf in pdfs:
        rel_key = str(relative_pdf_path(pdf, source_dir)).replace("\\", "/")
        fingerprint = pdf_fingerprint(pdf, use_sha256=config.get("compute_sha256", False))
        if not force_reconvert and manifest.is_unchanged(rel_key, fingerprint):
            logger.info("Skipping unchanged PDF: %s", rel_key)
            skipped += 1
            continue

        try:
            convert_one_pdf(pdf, config_path=config_path, force_reconvert=True)
            converted += 1
            manifest = ManifestStore(manifest_path(config))
            write_failed_pdf_report(config, manifest)
        except Exception:
            failed += 1
            manifest = ManifestStore(manifest_path(config))
            write_failed_pdf_report(config, manifest)

    summary = {"converted": converted, "skipped": skipped, "failed": failed}
    logger.info(
        "Batch finished: converted=%s skipped=%s failed=%s",
        converted,
        skipped,
        failed,
    )
    return summary
