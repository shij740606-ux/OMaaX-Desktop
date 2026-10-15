#!/usr/bin/env python3
"""Run unstructured and Q-structured TASSEL fixed-effect GLM atomically."""

from __future__ import annotations

import argparse
import csv
import fcntl
import gzip
import hashlib
import json
import math
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from cli.run_gwas_tassel import _resolved_command
from gfa_core.gwas_scientific_identity import build_identity_recipe, materialize_stage_identity, new_run_id


STAGE = "gwas_glm"
METHOD_VERSION = "tassel_fixed_effect_glm.v1"
STATISTICS_HEADER = [
    "Trait", "Marker", "Chr", "Pos", "marker_F", "p", "marker_Rsq", "add_F",
    "add_p", "dom_F", "dom_p", "marker_df", "marker_MS", "error_df", "error_MS",
    "model_df", "model_MS", "minorObs",
]
FIXED_PARAMETERS = {
    "maxP": 1.0, "permute": False, "nperm": 0, "genotypeComponent": "Genotype",
    "minClassSize": 0, "biallelicOnly": False, "appendAddDom": False,
    "glm_population_structure": "none",
    "glm_structure_population_structure": "exclude_last_trait",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _within(workspace: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(workspace.resolve())
        return True
    except ValueError:
        return False


def _authoritative_input_record(
    workspace: Path,
    path: Path,
    role: str,
    producer_manifest_path: Path,
    producer_stage: str,
    expected_scopes: dict[str, str],
    expected_prefix: Optional[str] = None,
    require_analysis_scope: bool = True,
    expected_job_id: Optional[str] = None,
) -> dict[str, Any]:
    workspace, path, producer_manifest_path = workspace.resolve(), path.resolve(), producer_manifest_path.resolve()
    if not _within(workspace, path) or not _within(workspace, producer_manifest_path):
        raise ValueError(f"authoritative {role} and producer Manifest must be in current job workspace")
    if not path.is_file() or not producer_manifest_path.is_file():
        raise FileNotFoundError(f"authoritative {role} or producer Manifest does not exist")
    try:
        producer = json.loads(producer_manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{producer_stage} producer Manifest is invalid JSON") from exc
    if producer.get("manifest_version") != "1.0":
        raise ValueError(f"{producer_stage} producer Manifest version must be 1.0")
    if producer.get("stage") != producer_stage or producer.get("status") != "success":
        raise ValueError(f"{role} producer Manifest stage/status is invalid")
    if expected_prefix is not None and producer.get("prefix") != expected_prefix:
        raise ValueError(f"{role} producer Manifest prefix does not match")
    if expected_job_id is not None and producer.get("job_id") != expected_job_id:
        raise ValueError(f"{producer_stage} producer Manifest job_id does not match invocation")
    run_id = str(producer.get("run_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", run_id):
        raise ValueError(f"{producer_stage} producer Manifest run_id is empty or unsafe")
    analysis_scope = str(producer.get("analysis_scope_id") or "")
    if ((require_analysis_scope and not analysis_scope)
            or (analysis_scope and not re.fullmatch(r"gwas-analysis:[0-9a-f]{64}", analysis_scope))):
        raise ValueError(f"{producer_stage} producer Manifest analysis_scope_id is invalid")
    for field, expected in expected_scopes.items():
        if producer.get(field) != expected:
            raise ValueError(f"{role} producer Manifest {field} does not match")
    artifact = (producer.get("artifacts") or {}).get(role)
    if not isinstance(artifact, dict) or artifact.get("role") != role or artifact.get("artifact") != role:
        raise ValueError(f"{role} producer Manifest artifact role is invalid")
    declared_path = Path(str(artifact.get("path") or ""))
    if declared_path.is_absolute() or (workspace / declared_path).resolve() != path:
        raise ValueError(f"{role} producer Manifest artifact path does not match")
    if artifact.get("full_path") and Path(str(artifact["full_path"])).resolve() != path:
        raise ValueError(f"{role} producer Manifest artifact full_path does not match")
    size, digest = path.stat().st_size, sha256_file(path)
    if artifact.get("size_bytes", artifact.get("size")) != size:
        raise ValueError(f"{role} producer Manifest artifact size does not match")
    if artifact.get("sha256") != digest:
        raise ValueError(f"{role} producer Manifest artifact SHA256 does not match")
    record = {
        "source": "manifest", "artifact": role, "role": role, "path": str(path),
        "full_path": str(path), "size": size, "size_bytes": size, "sha256": digest,
        "producer_stage": producer_stage, "producer_run_id": run_id,
        "producer_manifest_path": str(producer_manifest_path),
        "producer_manifest_sha256": sha256_file(producer_manifest_path),
    }
    if analysis_scope:
        record["producer_analysis_scope_id"] = analysis_scope
    return record


def _revalidate_inputs(records: Sequence[dict[str, Any]]) -> None:
    for record in records:
        path = Path(record["full_path"])
        manifest = Path(record["producer_manifest_path"])
        if (not path.is_file() or path.stat().st_size != record["size_bytes"]
                or sha256_file(path) != record["sha256"]):
            raise RuntimeError(f"authoritative {record['role']} changed during execution")
        if not manifest.is_file() or sha256_file(manifest) != record["producer_manifest_sha256"]:
            raise RuntimeError(f"authoritative {record['role']} producer Manifest changed during execution")


def _trait_table(path: Path) -> tuple[str, list[str], list[float]]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines or len(lines[0].split("\t")) != 2 or not lines[0].startswith("<Trait>\t"):
        raise ValueError("prepared trait table must contain exactly one trait")
    trait_name = lines[0].split("\t")[1]
    samples: list[str] = []
    values: list[float] = []
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) != 2 or not fields[0]:
            raise ValueError("prepared trait table row is invalid")
        try:
            value = float(fields[1])
        except ValueError as exc:
            raise ValueError("prepared trait values must be numeric") from exc
        if not math.isfinite(value):
            raise ValueError("prepared trait values must be finite")
        samples.append(fields[0]); values.append(value)
    if not samples or len(samples) != len(set(samples)):
        raise ValueError("prepared trait sample IDs must be nonempty and unique")
    if min(values) == max(values):
        raise ValueError("phenotype variance must be positive")
    return trait_name, samples, values


def _covariate_table(path: Path) -> tuple[list[str], dict[str, list[float]]]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) < 3 or lines[0] != "<Covariate>" or not lines[1].startswith("<Trait>\t"):
        raise ValueError("prepared covariate table header is invalid")
    columns = lines[1].split("\t")[1:]
    if len(columns) < 2 or any(not value for value in columns):
        raise ValueError("prepared covariate table must contain K >= 2 finite Q columns")
    values: dict[str, list[float]] = {}
    for line in lines[2:]:
        fields = line.split("\t")
        if len(fields) != len(columns) + 1 or not fields[0] or fields[0] in values:
            raise ValueError("prepared covariate sample IDs must be nonempty and unique")
        try:
            numeric = [float(value) for value in fields[1:]]
        except ValueError as exc:
            raise ValueError("prepared covariates must be numeric") from exc
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError("prepared covariates must be finite")
        values[fields[0]] = numeric
    if not values:
        raise ValueError("prepared covariate table contains no samples")
    return columns, values


def _open_text(path: Path):
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open("r", encoding="utf-8")


def _vcf_candidates(path: Path, analysis_samples: Sequence[str]) -> tuple[list[str], list[dict[str, Any]], set[tuple[str, int]]]:
    samples: Optional[list[str]] = None
    indexes: list[int] = []
    candidates: list[dict[str, Any]] = []
    loci: set[tuple[str, int]] = set()
    with _open_text(path) as handle:
        for line_number, raw in enumerate(handle, 1):
            if raw.startswith("#CHROM\t"):
                fields = raw.rstrip("\r\n").split("\t")
                samples = fields[9:]
                if not samples or any(not x for x in samples) or len(samples) != len(set(samples)):
                    raise ValueError("sorted VCF sample IDs must be nonempty and unique")
                missing = sorted(set(analysis_samples) - set(samples))
                if missing:
                    raise ValueError("sorted VCF does not cover phenotype samples")
                indexes = [samples.index(sample) for sample in analysis_samples]
            elif raw.strip() and not raw.startswith("#"):
                if samples is None:
                    raise ValueError("sorted VCF #CHROM header is missing")
                fields = raw.rstrip("\r\n").split("\t")
                if len(fields) != 9 + len(samples):
                    raise ValueError(f"malformed sorted VCF marker at line {line_number}")
                try:
                    pos = int(fields[1])
                except ValueError as exc:
                    raise ValueError(f"invalid VCF position at line {line_number}") from exc
                if not fields[0] or pos <= 0:
                    raise ValueError(f"invalid VCF locus at line {line_number}")
                locus = (fields[0], pos)
                if locus in loci:
                    raise ValueError("sorted VCF contains duplicate loci")
                loci.add(locus)
                formats = fields[8].split(":")
                if "GT" not in formats:
                    candidates.append({"locus": locus, "reason": "missing_gt_field"}); continue
                gt_index = formats.index("GT")
                observed: list[int] = []
                genotype_classes: list[tuple[int, ...]] = []
                for row, sample_index in enumerate(indexes):
                    values = fields[9 + sample_index].split(":")
                    gt = values[gt_index].replace("|", "/") if gt_index < len(values) else "."
                    parts = gt.split("/")
                    if not parts or any(part == "." for part in parts):
                        continue
                    try:
                        alleles = tuple(int(part) for part in parts)
                    except ValueError as exc:
                        raise ValueError(f"invalid VCF genotype at line {line_number}") from exc
                    if any(allele < 0 for allele in alleles):
                        raise ValueError(f"invalid VCF genotype at line {line_number}")
                    observed.append(row); genotype_classes.append(tuple(sorted(alleles)))
                levels = sorted(set(genotype_classes))
                if not observed or len(levels) < 2:
                    candidates.append({"locus": locus, "reason": "no_genotype_variation"}); continue
                # TASSEL DiscreteSitesFELM uses a categorical FactorModelEffect.  Deterministic
                # treatment coding (first sorted GT level as baseline) has the same column space.
                matrix = [[1.0 if genotype == level else 0.0 for level in levels[1:]]
                          for genotype in genotype_classes]
                candidates.append({"locus": locus, "reason": "", "observed": observed, "marker": matrix})
    if samples is None:
        raise ValueError("sorted VCF #CHROM header is missing")
    if not candidates:
        raise ValueError("sorted VCF contains no genotype marker candidates")
    return samples, candidates, loci


def _design_metrics(design: np.ndarray, candidates: Sequence[dict[str, Any]], label: str) -> dict[str, Any]:
    base_rank = int(np.linalg.matrix_rank(design))
    if base_rank != design.shape[1]:
        raise ValueError(f"{label} base design must have full rank")
    base_residual = design.shape[0] - base_rank
    if base_residual <= 0:
        raise ValueError(f"{label} base design must retain positive residual degrees of freedom")
    marker_dfs: list[int] = []
    residual_dfs: list[int] = []
    reasons: dict[str, int] = {}
    for candidate in candidates:
        reason = candidate["reason"]
        if not reason:
            rows = candidate["observed"]
            base = design[rows, :]
            rank = int(np.linalg.matrix_rank(base))
            if rank != base_rank:
                reason = "base_design_rank_loss"
            else:
                augmented = np.column_stack([base, np.asarray(candidate["marker"], dtype=float)])
                augmented_rank = int(np.linalg.matrix_rank(augmented))
                marker_df = augmented_rank - rank
                residual_df = len(rows) - augmented_rank
                if marker_df <= 0:
                    reason = "marker_collinearity"
                elif residual_df <= 0:
                    reason = "nonpositive_marker_residual_df"
                else:
                    marker_dfs.append(marker_df); residual_dfs.append(residual_df)
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    if not marker_dfs:
        detail = ", ".join(f"{key}={value}" for key, value in sorted(reasons.items()))
        raise ValueError(f"{label} contains no estimable genotype markers: {detail}")
    return {
        "design_rank": base_rank, "base_residual_degrees_of_freedom": base_residual,
        "candidate_marker_count": len(candidates), "estimable_marker_count": len(marker_dfs),
        "nonestimable_marker_count": len(candidates) - len(marker_dfs),
        "nonestimable_marker_reason_counts": dict(sorted(reasons.items())),
        "minimum_marker_degrees_of_freedom": min(marker_dfs),
        "maximum_marker_degrees_of_freedom": max(marker_dfs),
        "minimum_marker_residual_degrees_of_freedom": min(residual_dfs),
    }


def validate_inputs(vcf: Path, trait: Path, covariate: Path) -> tuple[dict[str, Any], set[tuple[str, int]]]:
    trait_name, samples, values = _trait_table(trait)
    columns, covariates = _covariate_table(covariate)
    if any(sample not in covariates for sample in samples):
        raise ValueError("prepared covariate table does not cover phenotype samples")
    genotype_samples, candidates, loci = _vcf_candidates(vcf, samples)
    glm = np.ones((len(samples), 1), dtype=float)
    effective = np.asarray([[*covariates[sample][:-1]] for sample in samples], dtype=float)
    structured = np.column_stack([np.ones(len(samples)), effective])
    return {
        "trait_name": trait_name, "sample_count": len(samples),
        "genotype_sample_count": len(genotype_samples), "phenotype_variance": float(np.var(values)),
        "covariate_k": len(columns), "effective_covariate_count": len(columns) - 1,
        "omitted_covariate": columns[-1], "glm_preflight": _design_metrics(glm, candidates, "glm"),
        "glm_structure_preflight": _design_metrics(structured, candidates, "glm_structure"),
    }, loci


def _sanitize_child_output(output: str) -> str:
    retained: list[str] = []
    for line in output.splitlines():
        lowered = line.lower()
        if ("pipeline arguments" in lowered or "classpath=" in lowered
                or (line.startswith("/") and ".jar:" in line)):
            continue
        retained.append(re.sub(r"(?<![A-Za-z0-9._-])/(?:[^\s,;]+)", "<path>", line))
    return "\n".join(retained).strip()


def _actionable_child_summary(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    actionable = [line for line in lines if re.search(r"\b(?:error|exception|caused by)\b", line, re.I)]
    tail = lines[-4:]
    selected: list[str] = []
    for line in [*actionable[-6:], *tail]:
        if line not in selected:
            selected.append(line)
    return " | ".join(selected)[:4000]


def _run_model(command: list[str], required_output: Path, label: str, raw_log: Path) -> str:
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    output = completed.stdout or ""
    raw_log.write_text(output, encoding="utf-8")
    sanitized = _sanitize_child_output(output)
    if completed.returncode != 0:
        summary = _actionable_child_summary(sanitized)
        detail = f": {summary}" if summary else ""
        raise RuntimeError(f"TASSEL {label} failed with exit code {completed.returncode}{detail}")
    if not required_output.is_file() or required_output.stat().st_size == 0:
        raise RuntimeError(f"TASSEL {label} completed without exact required statistics output")
    return sanitized


def _probe_tassel_version(executable: str) -> str:
    try:
        completed = subprocess.run([executable, "-version"], text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    matches = set(re.findall(r"(?:Tassel Version:\s*|TASSEL\s+)(\d+\.\d+\.\d+)", completed.stdout or "", re.I))
    return matches.pop() if len(matches) == 1 else "unknown"


def _parse_statistics(source: Path, destination: Path, loci: set[tuple[str, int]], trait_name: str) -> dict[str, int]:
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader, None)
        if header != STATISTICS_HEADER:
            raise ValueError("TASSEL statistics output must use the exact 18-column header")
        output = ["chr\tpos\tp"]
        rows = valid = nan = duplicates = 0
        seen: set[tuple[str, int]] = set()
        tested_loci: set[tuple[str, int]] = set()
        for row_number, row in enumerate(reader, 2):
            rows += 1
            if len(row) != 18:
                raise ValueError(f"TASSEL statistics row {row_number} does not have 18 columns")
            trait, marker, chrom, pos_text, p_text = row[0], row[1], row[2].strip(), row[3].strip(), row[5].strip()
            if trait != trait_name or not marker or not chrom:
                raise ValueError(f"TASSEL statistics identity is invalid at row {row_number}")
            try:
                pos = int(pos_text)
            except ValueError as exc:
                raise ValueError(f"TASSEL statistics position is invalid at row {row_number}") from exc
            locus = (chrom, pos)
            if pos <= 0 or locus not in loci:
                raise ValueError(f"TASSEL statistics locus is absent from authoritative VCF at row {row_number}")
            if locus in tested_loci:
                duplicates += 1
            tested_loci.add(locus)
            try:
                marker_df, error_df = float(row[11]), float(row[13])
            except ValueError as exc:
                raise ValueError(f"TASSEL statistics degrees of freedom are invalid at row {row_number}") from exc
            if (not math.isfinite(marker_df) or not math.isfinite(error_df)
                    or not marker_df.is_integer() or not error_df.is_integer()):
                raise ValueError(f"TASSEL statistics degrees of freedom are invalid at row {row_number}")
            if p_text == "NaN":
                nan += 1
                continue
            try:
                p = float(p_text)
            except ValueError as exc:
                raise ValueError(f"TASSEL statistics p-value is invalid at row {row_number}") from exc
            if math.isnan(p):
                raise ValueError(f"TASSEL statistics p-value must use literal NaN at row {row_number}")
            if not math.isfinite(p) or not 0 < p <= 1:
                raise ValueError(f"TASSEL statistics p-value is invalid at row {row_number}")
            if marker_df <= 0 or error_df <= 0:
                raise ValueError(f"TASSEL statistics degrees of freedom are invalid at row {row_number}")
            seen.add(locus)
            output.append(f"{chrom}\t{pos}\t{p_text}"); valid += 1
    if rows == 0 or valid == 0:
        raise ValueError("TASSEL statistics output contains no valid p-value rows")
    destination.write_text("\n".join(output) + "\n", encoding="utf-8")
    return {"full_row_count": rows, "valid_row_count": valid, "nan_row_count": nan,
            "invalid_row_count": 0, "duplicate_locus_row_count": duplicates,
            "tested_marker_count": len(tested_loci)}


def _safe_prefix(prefix: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", prefix).strip("._") or "cohort"


def _artifact(workspace: Path, source: Path, destination: Path, role: str, media_type: str) -> dict[str, Any]:
    size = source.stat().st_size
    return {"artifact": role, "role": role, "path": str(destination.relative_to(workspace)),
            "full_path": str(destination.resolve()), "type": media_type, "media_type": media_type,
            "size": size, "size_bytes": size, "sha256": sha256_file(source)}


def _replace_publication_file(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _newer_success(path: Path, started_at: str) -> bool:
    if not path.is_file(): return False
    try: current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return False
    return current.get("status") == "success" and str(current.get("start_time") or "") > started_at


def _publish_stage_atomically(sources: dict[str, Path], destinations: dict[str, Path],
                              manifest_source: Path, manifest_destination: Path,
                              lock_path: Path, started_at: str) -> bool:
    token = uuid.uuid4().hex
    staged: dict[Path, Path] = {}
    for role, destination in destinations.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{token}.publishing")
        shutil.copy2(sources[role], temporary); staged[destination] = temporary
    manifest_temporary = manifest_destination.with_name(f".{manifest_destination.name}.{token}.publishing")
    manifest_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest_source, manifest_temporary); staged[manifest_destination] = manifest_temporary
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if _newer_success(manifest_destination, started_at):
            for path in staged.values(): path.unlink(missing_ok=True)
            return False
        backups: dict[Path, Optional[Path]] = {}
        try:
            for destination in staged:
                backup = destination.with_name(f".{destination.name}.{token}.rollback") if destination.is_file() else None
                if backup: shutil.copy2(destination, backup)
                backups[destination] = backup
            for destination, temporary in staged.items(): _replace_publication_file(temporary, destination)
        except Exception:
            for destination, backup in backups.items():
                if backup is None: destination.unlink(missing_ok=True)
                elif backup.is_file(): os.replace(backup, destination)
            for path in staged.values(): path.unlink(missing_ok=True)
            for path in backups.values():
                if path: path.unlink(missing_ok=True)
            raise
        for path in backups.values():
            if path: path.unlink(missing_ok=True)
    return True


def _publish_failed(path: Path, manifest: dict[str, Any], lock_path: Path,
                    success_path: Optional[Path]) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        started = str(manifest.get("start_time") or "")
        if success_path and _newer_success(success_path, started): return False
        if path.is_file():
            try: existing = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError): existing = {}
            if str(existing.get("start_time") or "") > started: return False
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.publishing")
        _write_json(temporary, manifest); os.replace(temporary, path)
    return True


def _clear_older_failed(path: Path, success_started_at: str, lock_path: Path) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if not path.is_file():
            return
        try:
            failed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if str(failed.get("start_time") or "") <= success_started_at:
            path.unlink(missing_ok=True)


def _mark_bundle_superseded(bundle: Path, workspace: Path, manifest_source: Path,
                            sources: dict[str, Path]) -> None:
    progress_path, provenance_path = bundle / "progress.json", bundle / "provenance.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    progress["status"] = "superseded"
    progress["manifest_path"] = str(manifest_source.relative_to(workspace))
    _write_json(progress_path, progress)
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["publication_disposition"] = "superseded_by_newer_success"
    provenance["artifacts"] = {}
    provenance["attempt_artifacts"] = {
        role: {"role": role, "path": str(path.relative_to(workspace)),
               "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for role, path in sources.items()
    }
    _write_json(provenance_path, provenance)
    checksum_entries = [(manifest_source, str(manifest_source.relative_to(workspace)))]
    checksum_entries.extend((path, str(path.relative_to(workspace))) for path in sources.values())
    (bundle / "checksums.sha256").write_text("".join(
        f"{sha256_file(path)}  {label}\n"
        for path, label in sorted(checksum_entries, key=lambda item: item[1])
    ), encoding="utf-8")


def _validated_log_path(workspace: Path, requested: Optional[Path], authorities: Sequence[Path], run_id: str) -> Path:
    logs_root = (workspace / "logs").resolve()
    candidate = (requested if requested is not None else logs_root / f"{run_id}.log").resolve()
    try:
        candidate.relative_to(logs_root)
    except ValueError as exc:
        raise ValueError("--log-file must be inside the current job logs directory") from exc
    for authority in authorities:
        resolved = authority.resolve()
        if candidate == resolved:
            raise ValueError("--log-file aliases an authoritative input or producer Manifest")
        if candidate.exists() and resolved.exists():
            try:
                if os.path.samefile(candidate, resolved):
                    raise ValueError("--log-file aliases an authoritative input or producer Manifest")
            except OSError:
                pass
    return candidate


def _runner_command(args: argparse.Namespace) -> list[str]:
    command = ["python", "-m", "cli.run_gwas_tassel_glm", "--workspace", str(args.workspace.resolve()),
        "--job-id", args.job_id, "--prefix", args.prefix, "--vcf", str(args.vcf.resolve()),
        "--trait", str(args.trait.resolve()), "--covariate", str(args.covariate.resolve()),
        "--vcf-manifest", str(args.vcf_manifest.resolve()), "--trait-manifest", str(args.trait_manifest.resolve()),
        "--covariate-manifest", str(args.covariate_manifest.resolve()),
        "--genotype-scope-id", args.genotype_scope_id, "--phenotype-scope-id", args.phenotype_scope_id,
        "--covariate-scope-id", args.covariate_scope_id, "--tassel-pipeline", args.tassel_pipeline,
        "--xmx", args.xmx]
    if args.log_file: command.extend(["--log-file", str(args.log_file.resolve())])
    return command


def _write_reproducibility(workspace: Path, identity12: str, run_id: str, command: list[str],
                           manifest_path: Path, manifest: dict[str, Any], environment: str,
                           sources: Optional[dict[str, Path]] = None,
                           public_manifest: Optional[Path] = None) -> Path:
    bundle = workspace / "reproducibility" / STAGE / f"{_safe_prefix(manifest['prefix'])}.{identity12}" / run_id
    bundle.mkdir(parents=True, exist_ok=False)
    canonical = shlex.join(command)
    (bundle / "commands.sh").write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + canonical + "\n", encoding="utf-8")
    (bundle / "commands.sh").chmod(0o755)
    (bundle / "environment.txt").write_text(environment, encoding="utf-8")
    recorded = public_manifest or manifest_path
    _write_json(bundle / "progress.json", {"stage": STAGE, "status": manifest["status"], "run_id": run_id,
                                            "manifest_path": str(recorded.relative_to(workspace))})
    _write_json(bundle / "provenance.json", {"stage": STAGE, "run_id": run_id, "runner_command": canonical,
        "software": manifest.get("software", {}), "inputs": manifest.get("inputs", []),
        "artifacts": manifest.get("artifacts", {})})
    checksums = [(manifest_path, str(recorded.relative_to(workspace)))]
    for role, artifact in manifest.get("artifacts", {}).items():
        source = sources.get(role) if sources else None
        if source and source.is_file(): checksums.append((source, artifact["path"]))
    (bundle / "checksums.sha256").write_text("".join(
        f"{sha256_file(path)}  {label}\n" for path, label in sorted(checksums, key=lambda item: item[1])), encoding="utf-8")
    return bundle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path); parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True); parser.add_argument("--vcf", required=True, type=Path)
    parser.add_argument("--trait", required=True, type=Path); parser.add_argument("--covariate", required=True, type=Path)
    parser.add_argument("--vcf-manifest", required=True, type=Path); parser.add_argument("--trait-manifest", required=True, type=Path)
    parser.add_argument("--covariate-manifest", required=True, type=Path)
    parser.add_argument("--genotype-scope-id", required=True); parser.add_argument("--phenotype-scope-id", required=True)
    parser.add_argument("--covariate-scope-id", required=True); parser.add_argument("--tassel-pipeline", default="run_pipeline.pl")
    parser.add_argument("--xmx", default="10g"); parser.add_argument("--log-file", type=Path)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = args.workspace.resolve()
    run_id = new_run_id(STAGE)
    try:
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", args.job_id)
                or workspace.name != args.job_id):
            raise ValueError("--job-id must be safe and exactly match the current job workspace")
        log_file = _validated_log_path(workspace, args.log_file,
            [args.vcf, args.trait, args.covariate, args.vcf_manifest,
             args.trait_manifest, args.covariate_manifest], run_id)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    final = workspace / "final"; final.mkdir(parents=True, exist_ok=True)
    attempt = workspace / ".attempts" / run_id; attempt.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc); clock = time.monotonic(); safe_prefix = _safe_prefix(args.prefix)
    runner = _runner_command(args)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    parameters = {**FIXED_PARAMETERS, "xmx": args.xmx}
    manifest: dict[str, Any] = {"manifest_version": "1.0", "stage": STAGE, "prefix": args.prefix,
        "job_id": args.job_id, "run_id": run_id, "status": "failed", "timestamp": started.isoformat(),
        "start_time": started.isoformat(), "end_time": started.isoformat(), "elapsed_sec": 0.0,
        "cmd": runner, "workspace_root": str(workspace), "workspace": str(final),
        "input_dir": str(workspace / "input"), "parameters": parameters, "metrics": {}, "artifacts": {}}
    identity12 = "failed"; success_path: Optional[Path] = None; environment = ""; bundle: Optional[Path] = None
    logs: list[str] = []
    try:
        scopes = {"genotype_scope_id": args.genotype_scope_id, "phenotype_scope_id": args.phenotype_scope_id,
                  "covariate_scope_id": args.covariate_scope_id}
        for field, prefix in (("genotype_scope_id", "genotype"), ("phenotype_scope_id", "phenotype"),
                              ("covariate_scope_id", "covariate")):
            if not re.fullmatch(prefix + r":[0-9a-f]{64}", scopes[field]): raise ValueError(f"{field} is invalid")
        inputs = [
            _authoritative_input_record(workspace, args.vcf, "sorted_vcf", args.vcf_manifest, "gwas_fmt",
                                        {"genotype_scope_id": args.genotype_scope_id}, args.prefix, False, args.job_id),
            _authoritative_input_record(workspace, args.trait, "trait_table", args.trait_manifest, "gwas_trait",
                                        {"phenotype_scope_id": args.phenotype_scope_id}, args.prefix, True, args.job_id),
            _authoritative_input_record(workspace, args.covariate, "covariate_table", args.covariate_manifest, "gwas_cov",
                                        {"genotype_scope_id": args.genotype_scope_id,
                                         "covariate_scope_id": args.covariate_scope_id}, args.prefix, True, args.job_id),
        ]
        executable = _resolved_command(args.tassel_pipeline)
        if not Path(executable).is_file() or not os.access(executable, os.X_OK):
            raise FileNotFoundError("TASSEL pipeline executable was not found or is not executable")
        version = _probe_tassel_version(executable)
        if version != "5.2.96": raise RuntimeError(f"TASSEL 5.2.96 is required; proven version was {version}")
        environment = "\n".join(["runner=cli.run_gwas_tassel_glm.py",
            f"runtime_environment={os.environ.get('CONDA_DEFAULT_ENV', 'unknown')}",
            f"python_resolved={Path(sys.executable).resolve()}", f"python_version={platform.python_version()}",
            f"tassel_resolved={executable}", f"tassel_version={version}", f"platform={platform.platform()}"]) + "\n"
        recipe = build_identity_recipe(stage=STAGE, required_artifact_roles=["sorted_vcf", "trait_table", "covariate_table"],
            parameters=parameters, method_version=METHOD_VERSION, contract_version="v1", tool_versions={"tassel": version})
        identity = materialize_stage_identity(recipe, {item["role"]: item for item in inputs}, scopes)
        identity12 = identity["analysis_scope_id"].split(":", 1)[1][:12]
        stem = f"{safe_prefix}.{STAGE}.{identity12}"; success_path = final / f"{stem}.manifest.json"
        manifest.update(scopes); manifest.update(identity); manifest.update({"inputs": inputs, "identity_recipe": recipe,
            "software": {"tassel": {"resolved_path": executable, "version": version}}})
        metrics, loci = validate_inputs(args.vcf.resolve(), args.trait.resolve(), args.covariate.resolve())
        full_sources: dict[str, Path] = {}; p_sources: dict[str, Path] = {}
        commands = {
            "glm": [executable, "-Xms512m", f"-Xmx{args.xmx}", "-fork1", "-vcf", str(args.vcf.resolve()),
                    "-fork2", "-t", str(args.trait.resolve()), "-combine3", "-input1", "-input2", "-intersect",
                    "-FixedEffectLMPlugin", "-endPlugin", "-export", str(attempt / "glm"), "-runfork1", "-runfork2"],
            "glm_structure": [executable, "-Xms512m", f"-Xmx{args.xmx}", "-fork1", "-vcf", str(args.vcf.resolve()),
                    "-fork2", "-t", str(args.trait.resolve()), "-fork3", "-q", str(args.covariate.resolve()),
                    "-excludeLastTrait", "-combine4", "-input1", "-input2", "-input3", "-intersect",
                    "-FixedEffectLMPlugin", "-endPlugin", "-export", str(attempt / "glm_structure"),
                    "-runfork1", "-runfork2", "-runfork3"],
        }
        for model, command in commands.items():
            full = Path(str(attempt / model) + "1.txt"); pvalues = attempt / f"{model}.pvalues.tsv"
            logs.append(f"[{model}]\n" + _run_model(command, full, model, attempt / f"{model}.raw.log"))
            metrics[model] = _parse_statistics(full, pvalues, loci, metrics["trait_name"])
            full_sources[model] = full; p_sources[model] = pvalues
        _revalidate_inputs(inputs)
        log_source, report_source, environment_source = attempt / "tassel.log", attempt / "report.md", attempt / "environment.txt"
        log_source.write_text("\n".join(logs) + "\n", encoding="utf-8"); environment_source.write_text(environment, encoding="utf-8")
        report_source.write_text("\n".join(["# GWAS TASSEL fixed-effect GLM", "",
            f"- Genotype scope: `{identity['genotype_scope_id']}`", f"- Phenotype scope: `{identity['phenotype_scope_id']}`",
            f"- Covariate scope: `{identity['covariate_scope_id']}`", f"- Model scope: `{identity['model_scope_id']}`",
            f"- Analysis scope: `{identity['analysis_scope_id']}`", f"- Trait: `{metrics['trait_name']}`",
            f"- Samples: {metrics['sample_count']}",
            f"- Candidate markers: {metrics['glm_preflight']['candidate_marker_count']}",
            f"- GLM design rank/base residual df: {metrics['glm_preflight']['design_rank']}/{metrics['glm_preflight']['base_residual_degrees_of_freedom']}",
            f"- GLM estimable/non-estimable: {metrics['glm_preflight']['estimable_marker_count']}/{metrics['glm_preflight']['nonestimable_marker_count']}",
            f"- Structured GLM design rank/base residual df: {metrics['glm_structure_preflight']['design_rank']}/{metrics['glm_structure_preflight']['base_residual_degrees_of_freedom']}",
            f"- Structured GLM estimable/non-estimable: {metrics['glm_structure_preflight']['estimable_marker_count']}/{metrics['glm_structure_preflight']['nonestimable_marker_count']}",
            f"- GLM valid/NaN rows: {metrics['glm']['valid_row_count']}/{metrics['glm']['nan_row_count']}",
            f"- Structured GLM valid/NaN rows: {metrics['glm_structure']['valid_row_count']}/{metrics['glm_structure']['nan_row_count']}",
            f"- Q omission policy: exclude last trait (`{metrics['omitted_covariate']}`)", f"- TASSEL: `{version}`", ""]), encoding="utf-8")
        destinations = {"glm_full": final / f"{stem}.glm.full.txt",
            "glm_structure_full": final / f"{stem}.glm_structure.full.txt",
            "glm_pvalues": final / f"{stem}.glm.pvalues.tsv",
            "glm_structure_pvalues": final / f"{stem}.glm_structure.pvalues.tsv",
            "log": final / f"{stem}.log.txt", "report": final / f"{stem}.report.md",
            "environment": final / f"{stem}.environment.txt"}
        sources = {"glm_full": full_sources["glm"], "glm_structure_full": full_sources["glm_structure"],
            "glm_pvalues": p_sources["glm"], "glm_structure_pvalues": p_sources["glm_structure"],
            "log": log_source, "report": report_source, "environment": environment_source}
        media = {"report": "text/markdown", "log": "text/plain", "environment": "text/plain"}
        ended = datetime.now(timezone.utc)
        manifest.update({"status": "success", "timestamp": ended.isoformat(), "end_time": ended.isoformat(),
            "elapsed_sec": round(time.monotonic() - clock, 6), "metrics": metrics,
            "artifacts": {role: _artifact(workspace, sources[role], destination, role,
                media.get(role, "text/tab-separated-values")) for role, destination in destinations.items()}})
        manifest_source = attempt / "manifest.json"; _write_json(manifest_source, manifest)
        if args.log_file: shutil.copy2(log_source, log_file)
        bundle = _write_reproducibility(workspace, identity12, run_id, runner, manifest_source, manifest,
                                        environment, sources, success_path)
        published = _publish_stage_atomically(sources, destinations, manifest_source, success_path,
                                               workspace / ".locks" / f"{stem}.lock", started.isoformat())
        if not published:
            _mark_bundle_superseded(bundle, workspace, manifest_source, sources)
        if published:
            failed = final / f"{safe_prefix}.{STAGE}.failed.manifest.json"
            _clear_older_failed(failed, started.isoformat(),
                                workspace / ".locks" / f"{safe_prefix}.{STAGE}.failed.lock")
        return 0
    except Exception as exc:
        if bundle and bundle.is_dir(): shutil.rmtree(bundle)
        root = re.sub(r"\s+", " ", str(exc)).strip()[:1000] or exc.__class__.__name__
        ended = datetime.now(timezone.utc); manifest.update({"status": "failed", "timestamp": ended.isoformat(),
            "end_time": ended.isoformat(), "elapsed_sec": round(time.monotonic() - clock, 6), "artifacts": {}, "error": root})
        failure = final / f"{safe_prefix}.{STAGE}.failed.manifest.json"
        attempt_failure = attempt / "failed.manifest.json"
        _write_json(attempt_failure, manifest)
        written = _publish_failed(failure, manifest, workspace / ".locks" / f"{safe_prefix}.{STAGE}.failed.lock", success_path)
        log_file.write_text(f"ERROR: {root}\n", encoding="utf-8")
        evidence = failure if written else attempt_failure
        try: _write_reproducibility(workspace, identity12, run_id, runner, evidence, manifest, environment)
        except Exception: pass
        print(f"ERROR: {root}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
