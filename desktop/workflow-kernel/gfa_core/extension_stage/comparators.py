"""Golden-output comparators for extension-stage conversions."""

from __future__ import annotations

import fnmatch
import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Any


TEXT_SUFFIXES = {".txt", ".log", ".out", ".err", ".md", ".csv", ".tsv", ".bed", ".blastout"}
FASTA_SUFFIXES = {".fa", ".fasta", ".faa", ".fna", ".ffn", ".fsa", ".pep"}
HMM_SUFFIXES = {".hmm"}
INDEX_SUFFIXES = {".tbi", ".csi"}


def compare_output_dirs(
    original_dir: Path,
    converted_dir: Path,
    *,
    output_contract: list[dict[str, Any]] | None = None,
    comparator_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare two output directories and return a publish-gate report."""

    all_original_files = _collect_files(original_dir)
    all_converted_files = _collect_files(converted_dir)
    original_files = _contract_files(all_original_files, original_dir, output_contract or [])
    converted_files = _contract_files(all_converted_files, converted_dir, output_contract or [])
    pairs, unpaired_original, unpaired_converted = _pair_files(original_files, converted_files, output_contract or [])
    comparisons = [_compare_pair(left, right) for left, right in pairs]
    failures = [row for row in comparisons if row.get("status") != "passed"]
    warnings: list[str] = []
    if unpaired_original:
        failures.append({
            "status": "failed",
            "reason": "missing_converted_output",
            "original": [p.relative_to(original_dir).as_posix() for p in unpaired_original],
        })
    if unpaired_converted:
        warnings.append("converted produced extra files: " + ", ".join(p.relative_to(converted_dir).as_posix() for p in unpaired_converted[:8]))
    status = "passed" if not failures else "failed"
    return {
        "schema_version": "extension_golden_comparison_v1",
        "status": status,
        "comparator_plan": comparator_plan or {},
        "summary": {
            "original_files": len(original_files),
            "converted_files": len(converted_files),
            "all_original_files": len(all_original_files),
            "all_converted_files": len(all_converted_files),
            "paired_files": len(pairs),
            "failed": len(failures),
            "warnings": len(warnings),
        },
        "file_comparisons": comparisons,
        "failures": failures,
        "warnings": warnings,
        "decision": "allow_publish" if status == "passed" else "block_publish",
    }


def file_manifest(root: Path) -> list[dict[str, Any]]:
    rows = []
    if not root.exists():
        return rows
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rows.append({
            "path": path.relative_to(root).as_posix(),
            "name": path.name,
            "size": path.stat().st_size,
            "sha256": _sha256(path),
            "type": _kind(path),
        })
    return rows



def _contract_files(files: list[Path], root: Path, contract: list[dict[str, Any]]) -> list[Path]:
    patterns = [str(item.get("name_pattern") or item.get("path") or item.get("name") or "") for item in contract if isinstance(item, dict) and bool(item.get("required_for_golden", item.get("required", True)))]
    patterns = [p for p in patterns if p and p != "*"]
    if not patterns:
        return files
    matched = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        if any(fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(rel, pattern) for pattern in patterns):
            matched.append(path)
    return matched or files

def _collect_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file())


def _pair_files(original: list[Path], converted: list[Path], contract: list[dict[str, Any]]) -> tuple[list[tuple[Path, Path]], list[Path], list[Path]]:
    pairs: list[tuple[Path, Path]] = []
    remaining_converted = list(converted)
    for left in original:
        right = _best_match(left, remaining_converted, contract)
        if right is None:
            continue
        pairs.append((left, right))
        remaining_converted.remove(right)
    paired_original = {left for left, _ in pairs}
    unpaired_original = [p for p in original if p not in paired_original]
    return pairs, unpaired_original, remaining_converted


def _best_match(original: Path, candidates: list[Path], contract: list[dict[str, Any]]) -> Path | None:
    if not candidates:
        return None
    for candidate in candidates:
        if candidate.name == original.name:
            return candidate
    same_suffix = [p for p in candidates if p.suffix.lower() == original.suffix.lower()]
    if len(same_suffix) == 1:
        return same_suffix[0]
    stem = _normalized_stem(original.name)
    for candidate in same_suffix:
        if _normalized_stem(candidate.name) == stem:
            return candidate
    if len(candidates) == 1:
        return candidates[0]
    return None


def _compare_pair(original: Path, converted: Path) -> dict[str, Any]:
    base = {
        "original": original.name,
        "converted": converted.name,
        "original_size": original.stat().st_size,
        "converted_size": converted.stat().st_size,
        "original_sha256": _sha256(original),
        "converted_sha256": _sha256(converted),
    }
    if base["original_sha256"] == base["converted_sha256"]:
        return {**base, "status": "passed", "comparator": "byte_identical"}
    kind = _kind(original)
    if kind == "fasta":
        result = _compare_fasta(original, converted)
    elif kind == "bed":
        result = _compare_record_set(original, converted, comparator="bed_record_set")
    elif kind == "hmm":
        result = _compare_hmm(original, converted)
    elif kind == "vcf":
        result = _compare_vcf(original, converted)
    elif kind == "json":
        result = _compare_json(original, converted)
    elif kind == "index":
        result = _compare_index_presence(original, converted)
    elif kind == "text":
        result = _compare_normalized_text(original, converted)
    else:
        result = _compare_normalized_text(original, converted)
    return {**base, **result}


def _compare_index_presence(original: Path, converted: Path) -> dict[str, Any]:
    metrics = {
        "original_size": original.stat().st_size,
        "converted_size": converted.stat().st_size,
    }
    if metrics["original_size"] > 0 and metrics["converted_size"] > 0:
        return {"status": "passed", "comparator": "index_file_presence", "metrics": metrics}
    return {"status": "failed", "comparator": "index_file_presence", "reason": "empty_index_file", "metrics": metrics}


def _compare_fasta(original: Path, converted: Path) -> dict[str, Any]:
    left = _read_fasta_records(original)
    right = _read_fasta_records(converted)
    metrics = {
        "original_headers": len(left),
        "converted_headers": len(right),
        "original_ids": sorted(left)[:10],
        "converted_ids": sorted(right)[:10],
    }
    if len(left) == 0 or len(right) == 0:
        return {"status": "failed", "comparator": "fasta_structured", "reason": "fasta_header_lost_or_missing", "metrics": metrics}
    if left == right:
        return {"status": "passed", "comparator": "fasta_structured", "metrics": metrics}
    if set(left) != set(right):
        metrics["missing_ids"] = sorted(set(left) - set(right))[:20]
        metrics["extra_ids"] = sorted(set(right) - set(left))[:20]
        return {"status": "failed", "comparator": "fasta_structured", "reason": "fasta_id_set_changed", "metrics": metrics}
    changed = [seq_id for seq_id in left if left.get(seq_id) != right.get(seq_id)]
    metrics["changed_sequences"] = changed[:20]
    return {"status": "failed", "comparator": "fasta_structured", "reason": "fasta_sequence_content_changed", "metrics": metrics}


def _compare_record_set(original: Path, converted: Path, *, comparator: str) -> dict[str, Any]:
    left = _normalized_records(original)
    right = _normalized_records(converted)
    metrics = {"original_records": len(left), "converted_records": len(right)}
    if left == right:
        return {"status": "passed", "comparator": comparator, "metrics": metrics}
    metrics["missing_records"] = sorted(left - right)[:20]
    metrics["extra_records"] = sorted(right - left)[:20]
    return {"status": "failed", "comparator": comparator, "reason": "record_set_changed", "metrics": metrics}


def _compare_hmm(original: Path, converted: Path) -> dict[str, Any]:
    left = _normalize_hmm_text(original)
    right = _normalize_hmm_text(converted)
    metrics = {
        "original_lines": len(left),
        "converted_lines": len(right),
        "original_leng": _hmm_field(left, "LENG"),
        "converted_leng": _hmm_field(right, "LENG"),
        "original_alph": _hmm_field(left, "ALPH"),
        "converted_alph": _hmm_field(right, "ALPH"),
    }
    if left == right:
        return {"status": "passed", "comparator": "hmmer_profile_normalized", "metrics": metrics}
    if metrics["original_leng"] != metrics["converted_leng"] or metrics["original_alph"] != metrics["converted_alph"]:
        return {"status": "failed", "comparator": "hmmer_profile_normalized", "reason": "hmm_profile_header_changed", "metrics": metrics}
    return {"status": "failed", "comparator": "hmmer_profile_normalized", "reason": "hmm_profile_body_changed", "metrics": metrics}


def _compare_vcf(original: Path, converted: Path) -> dict[str, Any]:
    left = _normalize_vcf_records(original)
    right = _normalize_vcf_records(converted)
    metrics = {
        "original_records": len(left["records"]),
        "converted_records": len(right["records"]),
        "original_samples": left["samples"],
        "converted_samples": right["samples"],
    }
    if left == right:
        return {"status": "passed", "comparator": "vcf_records", "metrics": metrics}
    if left["samples"] != right["samples"]:
        return {"status": "failed", "comparator": "vcf_records", "reason": "vcf_sample_header_changed", "metrics": metrics}
    metrics["missing_records"] = sorted(set(left["records"]) - set(right["records"]))[:20]
    metrics["extra_records"] = sorted(set(right["records"]) - set(left["records"]))[:20]
    return {"status": "failed", "comparator": "vcf_records", "reason": "vcf_records_changed", "metrics": metrics}


def _compare_json(original: Path, converted: Path) -> dict[str, Any]:
    try:
        left = _normalize_json(_json_load_path(original))
        right = _normalize_json(_json_load_path(converted))
    except Exception:
        return _compare_normalized_text(original, converted)
    if left == right:
        return {"status": "passed", "comparator": "json_structured"}
    return {
        "status": "failed",
        "comparator": "json_structured",
        "reason": "json_content_changed",
        "metrics": {"changed_keys": _changed_json_keys(left, right)[:20]},
    }


def _compare_normalized_text(original: Path, converted: Path) -> dict[str, Any]:
    left = _normalize_text(original)
    right = _normalize_text(converted)
    if left == right:
        return {"status": "passed", "comparator": "normalized_text"}
    return {
        "status": "failed",
        "comparator": "normalized_text",
        "reason": "text_content_changed",
        "metrics": {"original_lines": len(left), "converted_lines": len(right)},
    }


def _read_fasta_records(path: Path) -> dict[str, str]:
    records: dict[str, list[str]] = {}
    current = ""
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if raw.startswith(">"):
            current = raw[1:].strip().split()[0]
            if current:
                records.setdefault(current, [])
            continue
        if current:
            records[current].append(raw.strip())
    return {key: "".join(value) for key, value in records.items()}


def _json_load_path(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8", errors="ignore"))


def _normalize_json(value: Any, key: str = "") -> Any:
    key_l = key.lower()
    if key_l in {"wall_sec", "elapsed_sec", "runtime_sec", "duration_sec"}:
        return "<RUNTIME>"
    if isinstance(value, dict):
        return {str(k): _normalize_json(v, str(k)) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        return [_normalize_json(item, key) for item in value]
    if isinstance(value, str):
        return _normalize_json_string(value)
    return value


def _normalize_json_string(value: str) -> str:
    value = re.sub(r"\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+", "<DATETIME>", value)
    if "/" in value and not re.search(r"\s", value):
        return Path(value).name

    def replace_token(match: re.Match[str]) -> str:
        token = match.group(0)
        prefix = ""
        if "=" in token:
            prefix, token = token.rsplit("=", 1)
            prefix += "="
        return prefix + Path(token).name

    return re.sub(r"(?<!\S)(?:[A-Za-z0-9_.=-]*/)[^\s]+", replace_token, value)


def _changed_json_keys(left: Any, right: Any, prefix: str = "") -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        changed: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f"{prefix}.{key}" if prefix else str(key)
            changed.extend(_changed_json_keys(left.get(key), right.get(key), child))
        return changed
    if left != right:
        return [prefix or "<root>"]
    return []


def _normalized_records(path: Path) -> set[str]:
    records = set()
    for raw in _read_text_lines(path):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        records.add(re.sub(r"\s+", "\t", line))
    return records


def _normalize_vcf_records(path: Path) -> dict[str, Any]:
    chrom_header = ""
    records: list[str] = []
    for raw in _read_text_lines(path):
        line = raw.strip()
        if not line or line.startswith("##"):
            continue
        if line.startswith("#CHROM"):
            chrom_header = re.sub(r"\s+", "\t", line)
            continue
        if line.startswith("#"):
            continue
        records.append(re.sub(r"\s+", "\t", line))
    samples = chrom_header.split("\t")[9:] if chrom_header else []
    return {"chrom_header": chrom_header, "samples": samples, "records": records}


def _normalize_text(path: Path) -> list[str]:
    lines = []
    for raw in _read_text_lines(path):
        line = re.sub(r"/[A-Za-z0-9_./-]+", "<PATH>", raw.strip())
        line = re.sub(r"\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+", "<DATETIME>", line)
        if line:
            lines.append(line)
    return lines


def _read_text_lines(path: Path) -> list[str]:
    if path.name.lower().endswith(".gz"):
        try:
            with gzip.open(path, "rt", encoding="utf-8", errors="ignore") as handle:
                return handle.read().splitlines()
        except OSError:
            pass
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()


def _normalize_hmm_text(path: Path) -> list[str]:
    ignored = {"DATE", "COM", "CKSUM"}
    lines = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        key = raw.split(None, 1)[0] if raw.strip() else ""
        if key in ignored:
            continue
        lines.append(raw.rstrip())
    return lines


def _hmm_field(lines: list[str], field: str) -> str:
    for line in lines:
        if line.startswith(field + " "):
            return line.split(None, 1)[1].strip()
    return ""


def _kind(path: Path) -> str:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if suffix in FASTA_SUFFIXES:
        return "fasta"
    if suffix in HMM_SUFFIXES:
        return "hmm"
    if suffix == ".bed":
        return "bed"
    if suffix == ".json":
        return "json"
    if suffix in INDEX_SUFFIXES:
        return "index"
    if name.endswith((".vcf", ".vcf.gz", ".gvcf", ".gvcf.gz", ".g.vcf", ".g.vcf.gz")):
        return "vcf"
    if suffix in TEXT_SUFFIXES or name.endswith(".domtblout"):
        return "text"
    return "file"


def _normalized_stem(name: str) -> str:
    text = Path(name).stem.lower()
    text = re.sub(r"^(original|converted|rebuild_domain|spl_dom|cleaned)[._-]*", "", text)
    return text


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
