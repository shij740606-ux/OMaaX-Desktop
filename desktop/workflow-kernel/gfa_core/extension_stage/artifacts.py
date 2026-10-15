"""Output naming, scanning, and preview helpers for extension stages."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
TABLE_SUFFIXES = {".tsv", ".csv", ".xlsx", ".xls"}
TEXT_SUFFIXES = {".txt", ".log", ".out", ".err"}
MARKDOWN_SUFFIXES = {".md", ".markdown"}
HTML_SUFFIXES = {".html", ".htm"}
BINARY_PREVIEW_SUFFIXES = {
    ".bam",
    ".bai",
    ".bcf",
    ".bgz",
    ".gz",
    ".tbi",
    ".zip",
}


def safe_path_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or ""))
    return token.strip("._") or "unknown"


def safe_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip().lower())
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:64]


def first_param_list(parameters: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        if key not in parameters:
            continue
        value = parameters.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            items = re.split(r"[,;，、\s]+", value)
        elif isinstance(value, (list, tuple, set)):
            items = value
        else:
            items = [value]
        cleaned = [str(x).strip() for x in items if str(x).strip()]
        if cleaned:
            return cleaned
    return []


def extension_output_prefix(extension: dict[str, Any], parameters: dict[str, Any]) -> str:
    species = first_param_list(parameters, "targetSpecies", "target_species", "species")
    if not species:
        bindings = parameters.get("config_input_bindings") if isinstance(parameters, dict) else {}
        if isinstance(bindings, dict) and bindings.get("target_species"):
            species = [str(bindings.get("target_species"))]
    if not species:
        return ""
    stage = safe_name(str(extension.get("name") or extension.get("display_name") or extension.get("extension_id") or "extension_stage"))
    return ".".join(x for x in (safe_path_token(species[0]), stage) if x)


def normalize_output_names(output_dir: Path, prefix: str) -> None:
    if not output_dir.exists() or not prefix:
        return
    for path in sorted(p for p in output_dir.rglob("*") if p.is_file()):
        remainder = path.name
        if remainder.startswith(prefix + "."):
            remainder = remainder[len(prefix) + 1:]
        remainder = _strip_source_prefix_from_output(remainder)
        target = path.with_name(prefix + "." + remainder)
        if target == path:
            continue
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            for index in range(1, 1000):
                candidate = target.with_name(f"{stem}.{index}{suffix}")
                if not candidate.exists():
                    target = candidate
                    break
        path.rename(target)


def _strip_source_prefix_from_output(name: str) -> str:
    parts = name.split(".")
    if len(parts) < 4:
        return name
    third = parts[2].lower()
    if third in {"pep", "protein", "faa", "fa", "fasta", "cds", "genome"}:
        return ".".join(parts[2:])
    return name


def classify_file(path: Path) -> str:
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix in {".fa", ".fasta", ".faa", ".fsa", ".fna", ".ffn", ".pep"}:
        return "fasta"
    if suffix == ".hmm":
        return "hmm"
    if suffix in {".gff", ".gff3", ".gtf"}:
        return "annotation"
    if suffix in TABLE_SUFFIXES:
        return "table"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in MARKDOWN_SUFFIXES:
        return "markdown"
    if suffix in HTML_SUFFIXES:
        return "html"
    if suffix in TEXT_SUFFIXES or name.endswith(".blastout"):
        return "text"
    return "file"


def preview_file(path: Path, limit: int = 1200) -> str:
    size = path.stat().st_size
    if size > 2_000_000:
        return ""
    if _has_binary_preview_suffix(path):
        return f"[binary preview omitted: {path.name}, {size} bytes]"
    try:
        raw = path.read_bytes()
    except Exception:
        return ""
    if _looks_binary(raw[:4096]):
        return f"[binary preview omitted: {path.name}, {size} bytes]"
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return ""
    return text[:limit]


def _has_binary_preview_suffix(path: Path) -> bool:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    return any(suffix in BINARY_PREVIEW_SUFFIXES for suffix in suffixes)


def _looks_binary(raw: bytes) -> bool:
    if not raw:
        return False
    if b"\0" in raw:
        return True
    control = sum(1 for byte in raw if byte < 32 and byte not in {9, 10, 13})
    return (control / max(len(raw), 1)) > 0.02


def rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def scan_outputs(output_dir: Path, run_dir: Path) -> list[dict[str, Any]]:
    outputs = []
    if not output_dir.exists():
        return outputs
    for path in sorted(p for p in output_dir.rglob("*") if p.is_file()):
        typ = classify_file(path)
        outputs.append({
            "name": path.name,
            "path": rel(path, run_dir),
            "type": typ,
            "size": path.stat().st_size,
            "mtime": path.stat().st_mtime,
            "is_empty": path.stat().st_size == 0,
            "preview": preview_file(path),
        })
    return outputs


def run_has_empty_outputs(run: dict[str, Any]) -> bool:
    outputs = run.get("outputs") or []
    return bool(outputs) and any(bool(out.get("is_empty")) or int(out.get("size") or 0) == 0 for out in outputs)


def scan_misplaced_outputs(run_dir: Path, output_dir: Path) -> list[dict[str, Any]]:
    misplaced = []
    if not run_dir.exists():
        return misplaced
    ignored_roots = {"code", "logs", "inputs"}
    for path in sorted(p for p in run_dir.rglob("*") if p.is_file()):
        if _is_relative_to(path, output_dir):
            continue
        relative = path.relative_to(run_dir)
        if relative.parts and relative.parts[0] in ignored_roots:
            continue
        if path.name == "extension_run.json":
            continue
        misplaced.append({
            "name": path.name,
            "path": relative.as_posix(),
            "type": classify_file(path),
            "size": path.stat().st_size,
            "is_empty": path.stat().st_size == 0,
        })
    return misplaced


def validate_output_contract(run: dict[str, Any], output_contract: list[dict[str, Any]] | None) -> list[str]:
    outputs = run.get("outputs") or []
    errors: list[str] = []
    if not outputs:
        message = "Extension produced no files under outputs/"
        misplaced = run.get("misplaced_outputs") or []
        if misplaced:
            preview = ", ".join(str(item.get("path") or item.get("name")) for item in misplaced[:8])
            message += "; files were written outside outputs/: " + preview
        errors.append(message)
        return errors

    contracts = output_contract or [{"name_pattern": "*", "required": True, "allow_empty": False}]
    for item in contracts:
        if not isinstance(item, dict):
            continue
        pattern = str(item.get("name_pattern") or item.get("path") or item.get("name") or "*")
        required = bool(item.get("required", True))
        allow_empty = bool(item.get("allow_empty", False))
        matches = [out for out in outputs if _output_matches(out, pattern)]
        if required and not matches:
            errors.append(f"Required output pattern not produced under outputs/: {pattern}")
            continue
        if not allow_empty:
            empty = [str(out.get("path") or out.get("name")) for out in matches if bool(out.get("is_empty")) or int(out.get("size") or 0) == 0]
            if empty:
                errors.append("Required output pattern produced empty file(s): " + ", ".join(empty))
    return errors


def _output_matches(output: dict[str, Any], pattern: str) -> bool:
    name = str(output.get("name") or "")
    path = str(output.get("path") or "")
    return fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(path, pattern)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def execution_lesson_suggestion(extension: dict[str, Any], run: dict[str, Any]) -> str:
    command = str(run.get("command") or "").lower()
    if "seqtk" in command and "subseq" in command and run_has_empty_outputs(run):
        return "seqtk subseq succeeded but produced an empty file; bind {input_fasta} to the target species pep/fasta and {input_file} to the target species ID list from config.xlsx."
    if "does not exist" in str(run.get("error") or "").lower():
        return "A command referenced a missing fixed filename; use config.xlsx species-aware materialization or supported placeholders instead of hard-coded test filenames."
    return "Review command_template, config-aware input bindings, and output patterns before generating similar extensions."
