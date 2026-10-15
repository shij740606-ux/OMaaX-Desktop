#!/usr/bin/env python3
"""Run atomic TASSEL MLM and compressed MLM association models."""

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
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from cli.run_gwas_tassel import _resolved_command, _tassel_version
from gfa_core.gwas_scientific_identity import build_identity_recipe, materialize_stage_identity, new_run_id


STAGE = "gwas_mlm"
MODELS = {
    "mlm": {"compression": "None", "variance_component_estimation": "P3D"},
    "cmlm": {"compression": "Optimum", "variance_component_estimation": "P3D"},
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _open_text(path: Path):
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open("r", encoding="utf-8")


def _vcf_samples_and_markers(
    path: Path, analysis_samples: Sequence[str]
) -> Tuple[List[str], List[Dict[str, Any]]]:
    samples: Optional[List[str]] = None
    indexes: List[int] = []
    marker_candidates: List[Dict[str, Any]] = []
    with _open_text(path) as handle:
        for line_number, raw in enumerate(handle, start=1):
            if raw.startswith("#CHROM\t"):
                fields = raw.rstrip("\r\n").split("\t")
                samples = fields[9:]
                if not samples or len(samples) != len(set(samples)):
                    raise ValueError("sorted VCF sample IDs must be nonempty and unique")
                missing = sorted(set(analysis_samples) - set(samples))
                if missing:
                    raise ValueError("sorted VCF does not cover phenotype samples")
                indexes = [samples.index(sample) for sample in analysis_samples]
            elif raw.strip() and not raw.startswith("#"):
                if samples is None:
                    raise ValueError("sorted VCF #CHROM header is missing")
                fields = raw.rstrip("\r\n").split("\t")
                if len(fields) < 9 + len(samples):
                    raise ValueError(f"malformed sorted VCF marker at line {line_number}")
                format_fields = fields[8].split(":")
                if "GT" not in format_fields:
                    marker_candidates.append({"reason": "missing_gt_field"})
                    continue
                gt_index = format_fields.index("GT")
                genotypes: List[Tuple[int, Tuple[int, ...]]] = []
                for analysis_index, sample_index in enumerate(indexes):
                    values = fields[9 + sample_index].split(":")
                    gt = values[gt_index].replace("|", "/") if gt_index < len(values) else "."
                    if gt not in {".", "./."} and "." not in gt.split("/"):
                        try:
                            alleles = tuple(sorted(int(allele) for allele in gt.split("/")))
                        except ValueError as exc:
                            raise ValueError(f"invalid VCF genotype at line {line_number}") from exc
                        if any(allele < 0 for allele in alleles):
                            raise ValueError(f"invalid VCF genotype at line {line_number}")
                        genotypes.append((analysis_index, alleles))
                distinct_genotypes = {alleles for _row, alleles in genotypes}
                alt_alleles = sorted({allele for _row, alleles in genotypes for allele in alleles if allele > 0})
                if not genotypes:
                    marker_candidates.append({"reason": "no_observed_genotypes"})
                    continue
                if len(distinct_genotypes) < 2 or not alt_alleles:
                    marker_candidates.append({"reason": "no_genotype_variation"})
                    continue
                marker_candidates.append({
                    "reason": "",
                    "observed_rows": [row for row, _alleles in genotypes],
                    "marker_values": [
                        [float(alleles.count(allele)) for allele in alt_alleles]
                        for _row, alleles in genotypes
                    ],
                })
    if samples is None:
        raise ValueError("sorted VCF #CHROM header is missing")
    if not marker_candidates:
        raise ValueError("sorted VCF contains no genotype marker candidates")
    return samples, marker_candidates


def _trait_table(path: Path) -> Tuple[str, List[str], List[float]]:
    lines = [line.rstrip("\r\n") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines or not lines[0].startswith("<Trait>\t"):
        raise ValueError("prepared trait table header is invalid")
    header = lines[0].split("\t")
    if len(header) != 2 or not header[1]:
        raise ValueError("prepared trait table must contain exactly one trait")
    samples: List[str] = []
    values: List[float] = []
    for row in lines[1:]:
        fields = row.split("\t")
        if len(fields) != 2 or not fields[0]:
            raise ValueError("prepared trait table row is invalid")
        try:
            value = float(fields[1])
        except ValueError as exc:
            raise ValueError("prepared trait values must be numeric") from exc
        if not math.isfinite(value):
            raise ValueError("prepared trait values must be finite")
        samples.append(fields[0])
        values.append(value)
    if not samples or len(samples) != len(set(samples)):
        raise ValueError("prepared trait sample IDs must be nonempty and unique")
    if min(values) == max(values):
        raise ValueError("phenotype variance must be positive")
    return header[1], samples, values


def _covariate_table(path: Path) -> Tuple[List[str], List[str], List[List[float]]]:
    lines = [line.rstrip("\r\n") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) < 3 or lines[0] != "<Covariate>" or not lines[1].startswith("<Trait>\t"):
        raise ValueError("prepared covariate table header is invalid")
    columns = lines[1].split("\t")[1:]
    if len(columns) < 2 or any(not value for value in columns):
        raise ValueError("prepared covariate table must contain ADMIXTURE K >= 2")
    samples: List[str] = []
    values: List[List[float]] = []
    for row in lines[2:]:
        fields = row.split("\t")
        if len(fields) != len(columns) + 1 or not fields[0]:
            raise ValueError("prepared covariate table row is invalid")
        try:
            numeric = [float(value) for value in fields[1:]]
        except ValueError as exc:
            raise ValueError("prepared covariates must be numeric") from exc
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError("prepared covariates must be finite")
        samples.append(fields[0])
        values.append(numeric)
    if not samples or len(samples) != len(set(samples)):
        raise ValueError("prepared covariate sample IDs must be nonempty and unique")
    return columns, samples, values


def _validate_kinship(path: Path, genotype_samples: Sequence[str]) -> None:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    body = [line for line in lines if not line.startswith("##")]
    try:
        count = int(body[0])
    except (IndexError, ValueError) as exc:
        raise ValueError("kinship matrix sample count is invalid") from exc
    rows = body[1:]
    if count != len(rows) or count != len(genotype_samples):
        raise ValueError("kinship matrix does not cover sorted VCF samples")
    ids: List[str] = []
    for row in rows:
        fields = row.split()
        if len(fields) != count + 1:
            raise ValueError("kinship matrix row width is invalid")
        ids.append(fields[0])
        try:
            values = [float(value) for value in fields[1:]]
        except ValueError as exc:
            raise ValueError("kinship matrix contains nonnumeric values") from exc
        if not all(math.isfinite(value) for value in values):
            raise ValueError("kinship matrix contains nonfinite values")
    if ids != list(genotype_samples):
        raise ValueError("kinship matrix sample order does not match sorted VCF")


def validate_inputs(vcf: Path, kinship: Path, trait: Path, covariate: Path) -> Dict[str, Any]:
    trait_name, analysis_samples, phenotype_values = _trait_table(trait)
    genotype_samples, marker_candidates = _vcf_samples_and_markers(vcf, analysis_samples)
    _validate_kinship(kinship, genotype_samples)
    columns, covariate_samples, covariate_values = _covariate_table(covariate)
    covariate_index = {sample: values for sample, values in zip(covariate_samples, covariate_values)}
    if any(sample not in covariate_index for sample in analysis_samples):
        raise ValueError("prepared covariate table does not cover phenotype samples")
    effective = [[covariate_index[sample][index] for index in range(len(columns) - 1)] for sample in analysis_samples]
    design = np.column_stack([np.ones(len(analysis_samples)), np.asarray(effective, dtype=float)])
    design_rank = int(np.linalg.matrix_rank(design))
    if design_rank != design.shape[1]:
        raise ValueError("intercept plus effective covariates must have full rank")
    residual_df = len(analysis_samples) - design_rank - 1
    if residual_df <= 0:
        raise ValueError("marker model must retain positive residual degrees of freedom")
    marker_degrees_of_freedom = []
    marker_residual_dfs = []
    reason_counts: Dict[str, int] = {}
    for marker in marker_candidates:
        reason = str(marker["reason"])
        if reason:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            continue
        observed_rows = marker["observed_rows"]
        marker_values = marker["marker_values"]
        if len(observed_rows) <= design.shape[1]:
            reason_counts["insufficient_observed_rows"] = reason_counts.get("insufficient_observed_rows", 0) + 1
            continue
        covariate_design = design[observed_rows, :]
        covariate_rank = int(np.linalg.matrix_rank(covariate_design))
        if covariate_rank != design.shape[1]:
            reason_counts["covariate_rank_loss"] = reason_counts.get("covariate_rank_loss", 0) + 1
            continue
        augmented_design = np.column_stack([covariate_design, np.asarray(marker_values, dtype=float)])
        augmented_rank = int(np.linalg.matrix_rank(augmented_design))
        marker_df = augmented_rank - covariate_rank
        if marker_df <= 0:
            reason_counts["marker_collinearity"] = reason_counts.get("marker_collinearity", 0) + 1
            continue
        marker_residual_df = len(observed_rows) - augmented_rank
        if marker_residual_df <= 0:
            reason_counts["nonpositive_residual_df"] = reason_counts.get("nonpositive_residual_df", 0) + 1
            continue
        marker_degrees_of_freedom.append(marker_df)
        marker_residual_dfs.append(marker_residual_df)
    if not marker_degrees_of_freedom:
        labels = {
            "marker_collinearity": "marker degrees of freedom",
            "nonpositive_residual_df": "nonpositive residual degrees of freedom",
        }
        detail = ", ".join(
            f"{labels.get(reason, reason.replace('_', ' '))}={count}"
            for reason, count in sorted(reason_counts.items())
        )
        raise ValueError(f"sorted VCF contains no estimable genotype markers: {detail}")
    estimable_count = len(marker_degrees_of_freedom)
    return {
        "trait_name": trait_name,
        "sample_count": len(analysis_samples),
        "genotype_sample_count": len(genotype_samples),
        "marker_count": estimable_count,
        "candidate_marker_count": len(marker_candidates),
        "estimable_marker_count": estimable_count,
        "nonestimable_marker_count": len(marker_candidates) - estimable_count,
        "nonestimable_marker_reason_counts": dict(sorted(reason_counts.items())),
        "phenotype_variance": float(np.var(np.asarray(phenotype_values, dtype=float))),
        "covariate_k": len(columns),
        "effective_covariate_count": len(columns) - 1,
        "omitted_covariate": columns[-1],
        "design_rank": design_rank,
        "residual_degrees_of_freedom": residual_df,
        "minimum_marker_residual_degrees_of_freedom": min(marker_residual_dfs),
        "minimum_marker_degrees_of_freedom": min(marker_degrees_of_freedom),
        "maximum_marker_degrees_of_freedom": max(marker_degrees_of_freedom),
    }


def _run_model(command: List[str], output: Path, model: str) -> str:
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    text = completed.stdout or ""
    if completed.returncode != 0:
        raise RuntimeError(f"TASSEL {model.upper()} failed with exit code {completed.returncode}")
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError(f"TASSEL {model.upper()} completed without required output")
    return text


def _parse_association(source: Path, destination: Path) -> Dict[str, int]:
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"Trait", "Marker", "Chr", "Pos", "p"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError("TASSEL association output is missing Trait/Marker/Chr/Pos/p columns")
        output = ["chr\tpos\tp"]
        row_count = valid = nan = invalid = duplicates = 0
        loci = set()
        for row in reader:
            row_count += 1
            p_text = str(row.get("p") or "").strip()
            try:
                p_value = float(p_text)
            except ValueError:
                invalid += 1
                continue
            if math.isnan(p_value):
                nan += 1
                continue
            chrom = str(row.get("Chr") or "").strip()
            pos = str(row.get("Pos") or "").strip()
            try:
                valid_position = int(pos) > 0
            except ValueError:
                valid_position = False
            if not chrom or not valid_position or not math.isfinite(p_value) or not 0.0 < p_value <= 1.0:
                invalid += 1
                continue
            locus = (chrom, pos)
            if locus in loci:
                duplicates += 1
            loci.add(locus)
            output.append(f"{chrom}\t{pos}\t{p_text}")
            valid += 1
    if row_count == 0 or valid == 0:
        raise ValueError("TASSEL association output contains no valid p-value rows")
    destination.write_text("\n".join(output) + "\n", encoding="utf-8")
    return {
        "row_count": row_count,
        "valid_row_count": valid,
        "nan_row_count": nan,
        "invalid_row_count": invalid,
        "duplicate_locus_row_count": duplicates,
    }


def _artifact(workspace: Path, path: Path, role: str, media_type: str) -> Dict[str, Any]:
    size = path.stat().st_size
    return {
        "artifact": role, "role": role, "path": str(path.relative_to(workspace)),
        "full_path": str(path.resolve()), "type": media_type, "media_type": media_type,
        "size": size, "size_bytes": size, "sha256": sha256_file(path),
    }


def _staged_artifact(
    workspace: Path, source: Path, destination: Path, role: str, media_type: str
) -> Dict[str, Any]:
    size = source.stat().st_size
    return {
        "artifact": role, "role": role, "path": str(destination.relative_to(workspace)),
        "full_path": str(destination.resolve()), "type": media_type, "media_type": media_type,
        "size": size, "size_bytes": size, "sha256": sha256_file(source),
    }


def _input(path: Path, role: str) -> Dict[str, Any]:
    return {"source": "external", "artifact": role, "path": str(path), "full_path": str(path),
            "size": path.stat().st_size, "sha256": sha256_file(path)}


def _safe_prefix_token(prefix: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", prefix).strip("._") or "cohort"


def _failure_lock_path(workspace: Path, safe_prefix: str) -> Path:
    return workspace / ".locks" / f"{safe_prefix}.{STAGE}.failed.lock"


def _replace_publication_file(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _newer_success_exists(manifest_path: Path, started_at: str) -> bool:
    if not manifest_path.is_file():
        return False
    try:
        current = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return current.get("status") == "success" and str(current.get("start_time") or "") > started_at


def _publish_stage_atomically(
    sources: Dict[str, Path],
    destinations: Dict[str, Path],
    manifest_source: Path,
    manifest_path: Path,
    lock_path: Path,
    started_at: str,
) -> bool:
    """Publish a complete stage under one writer lock, rolling back any failed replacement."""
    token = uuid.uuid4().hex
    staged: Dict[Path, Path] = {}
    for role, destination in destinations.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{token}.publishing")
        shutil.copy2(sources[role], temporary)
        staged[destination] = temporary
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_temporary = manifest_path.with_name(f".{manifest_path.name}.{token}.publishing")
    shutil.copy2(manifest_source, manifest_temporary)
    staged[manifest_path] = manifest_temporary

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if _newer_success_exists(manifest_path, started_at):
            for temporary in staged.values():
                temporary.unlink(missing_ok=True)
            return False
        backups: Dict[Path, Optional[Path]] = {}
        try:
            for destination in staged:
                if destination.is_file():
                    backup = destination.with_name(f".{destination.name}.{token}.rollback")
                    shutil.copy2(destination, backup)
                    backups[destination] = backup
                else:
                    backups[destination] = None
            for destination, temporary in staged.items():
                _replace_publication_file(temporary, destination)
        except Exception:
            for destination, backup in backups.items():
                if backup is None:
                    destination.unlink(missing_ok=True)
                elif backup.is_file():
                    os.replace(backup, destination)
            for temporary in staged.values():
                temporary.unlink(missing_ok=True)
            for backup in backups.values():
                if backup is not None:
                    backup.unlink(missing_ok=True)
            raise
        for backup in backups.values():
            if backup is not None:
                backup.unlink(missing_ok=True)
    return True


def _remove_owned_failed_manifest(
    path: Path, run_id: str, lock_path: Optional[Path] = None, started_at: str = ""
) -> None:
    def remove_if_owned() -> None:
        if not path.is_file():
            return
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if existing.get("run_id") != run_id:
            return
        if started_at and str(existing.get("start_time") or "") > started_at:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    if lock_path is None:
        remove_if_owned()
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        remove_if_owned()


def _write_failed_manifest_if_fresh(
    path: Path,
    manifest: Dict[str, Any],
    lock_path: Path,
    success_manifest_path: Optional[Path],
) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        started_at = str(manifest.get("start_time") or "")
        if success_manifest_path is not None and _newer_success_exists(success_manifest_path, started_at):
            return False
        if path.is_file():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
            if str(existing.get("start_time") or "") > started_at:
                return False
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.publishing")
        _write_json(temporary, manifest)
        os.replace(temporary, path)
    return True


def _runner_command(args: argparse.Namespace, paths: Dict[str, Path]) -> List[str]:
    command = [
        "python", "-m", "cli.run_gwas_tassel_mlm", "--workspace", str(args.workspace.resolve()),
        "--job-id", args.job_id, "--prefix", args.prefix, "--vcf", str(paths["sorted_vcf"]),
        "--kinship", str(paths["kinship_matrix"]), "--trait", str(paths["trait_table"]),
        "--covariate", str(paths["covariate_table"]), "--genotype-scope-id", args.genotype_scope_id,
        "--phenotype-scope-id", args.phenotype_scope_id, "--covariate-scope-id", args.covariate_scope_id,
        "--tassel-pipeline", args.tassel_pipeline, "--xmx", args.xmx,
    ]
    if args.log_file:
        command.extend(["--log-file", str(args.log_file)])
    return command


def _write_reproducibility(
    workspace: Path,
    identity12: str,
    run_id: str,
    command: List[str],
    manifest_path: Path,
    manifest: Dict[str, Any],
    environment: str,
    artifact_sources: Optional[Dict[str, Path]] = None,
    public_manifest_path: Optional[Path] = None,
) -> Path:
    safe_prefix = _safe_prefix_token(manifest["prefix"])
    bundle = workspace / "reproducibility" / STAGE / f"{safe_prefix}.{identity12}" / run_id
    bundle.mkdir(parents=True, exist_ok=False)
    canonical = shlex.join(command)
    commands = bundle / "commands.sh"
    commands.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + canonical + "\n", encoding="utf-8")
    commands.chmod(0o755)
    (bundle / "environment.txt").write_text(environment, encoding="utf-8")
    recorded_manifest = public_manifest_path or manifest_path
    _write_json(bundle / "progress.json", {"stage": STAGE, "status": manifest["status"], "run_id": run_id,
                                             "manifest_path": str(recorded_manifest.relative_to(workspace))})
    _write_json(bundle / "provenance.json", {"stage": STAGE, "run_id": run_id, "runner_command": canonical,
                                               "software": manifest.get("software", {}),
                                               "inputs": manifest.get("inputs", []),
                                               "artifacts": manifest.get("artifacts", {})})
    checksum_entries = [(manifest_path, str(recorded_manifest.relative_to(workspace)))]
    for role, artifact in manifest.get("artifacts", {}).items():
        candidate = artifact_sources.get(role) if artifact_sources is not None else workspace / artifact["path"]
        if candidate is not None and candidate.is_file():
            checksum_entries.append((candidate, artifact["path"]))
    (bundle / "checksums.sha256").write_text(
        "".join(
            f"{sha256_file(path)}  {label}\n"
            for path, label in sorted(checksum_entries, key=lambda item: item[1])
        ),
        encoding="utf-8",
    )
    return bundle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--vcf", required=True, type=Path)
    parser.add_argument("--kinship", required=True, type=Path)
    parser.add_argument("--trait", required=True, type=Path)
    parser.add_argument("--covariate", required=True, type=Path)
    parser.add_argument("--genotype-scope-id", required=True)
    parser.add_argument("--phenotype-scope-id", required=True)
    parser.add_argument("--covariate-scope-id", required=True)
    parser.add_argument("--tassel-pipeline", default="run_pipeline.pl")
    parser.add_argument("--xmx", default="10g")
    parser.add_argument("--log-file", type=Path)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = args.workspace.resolve()
    final_dir = workspace / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    paths = {"sorted_vcf": args.vcf.resolve(), "kinship_matrix": args.kinship.resolve(),
             "trait_table": args.trait.resolve(), "covariate_table": args.covariate.resolve()}
    run_id = new_run_id(STAGE)
    started = datetime.now(timezone.utc)
    started_clock = time.monotonic()
    safe_prefix = _safe_prefix_token(args.prefix)
    failed_manifest = final_dir / f"{safe_prefix}.{STAGE}.failed.manifest.json"
    success_manifest_path: Optional[Path] = None
    failure_lock = _failure_lock_path(workspace, safe_prefix)
    publication_lock = workspace / ".locks" / f"{safe_prefix}.{STAGE}.unresolved.lock"
    success_bundle: Optional[Path] = None
    runner_command = _runner_command(args, paths)
    parameters = {"models": MODELS, "xmx": args.xmx, "covariate_policy": "exclude_last_trait"}
    environment = ""
    manifest: Dict[str, Any] = {
        "manifest_version": "1.0", "stage": STAGE, "prefix": args.prefix, "job_id": args.job_id,
        "run_id": run_id, "status": "failed", "timestamp": started.isoformat(),
        "start_time": started.isoformat(), "end_time": started.isoformat(), "elapsed_sec": 0.0,
        "cmd": runner_command, "workspace_root": str(workspace), "workspace": str(final_dir),
        "input_dir": str(workspace / "input"), "parameters": parameters, "metrics": {}, "artifacts": {},
    }
    identity12 = "failed"
    try:
        for role, path in paths.items():
            if not path.is_file():
                raise FileNotFoundError(f"Authoritative {role} does not exist")
        scopes = {"genotype_scope_id": args.genotype_scope_id, "phenotype_scope_id": args.phenotype_scope_id,
                  "covariate_scope_id": args.covariate_scope_id}
        for name, prefix in (("genotype_scope_id", "genotype"), ("phenotype_scope_id", "phenotype"),
                             ("covariate_scope_id", "covariate")):
            if not re.fullmatch(prefix + r":[0-9a-f]{64}", scopes[name]):
                raise ValueError(f"{name} is invalid")
        manifest.update(scopes)
        metrics = validate_inputs(paths["sorted_vcf"], paths["kinship_matrix"], paths["trait_table"],
                                  paths["covariate_table"])
        executable = _resolved_command(args.tassel_pipeline)
        if not Path(executable).is_file() or not os.access(executable, os.X_OK):
            raise FileNotFoundError("TASSEL pipeline executable was not found or is not executable")
        version = _tassel_version(executable)
        if version == "unknown":
            raise RuntimeError("TASSEL version could not be proven")
        environment = "\n".join([
            "runner=cli.run_gwas_tassel_mlm.py", f"runtime_environment={os.environ.get('CONDA_DEFAULT_ENV', 'unknown')}",
            f"python_resolved={Path(sys.executable).resolve()}", f"python_version={platform.python_version()}",
            f"tassel_resolved={executable}", f"tassel_version={version}", f"platform={platform.platform()}",
        ]) + "\n"
        input_records = [_input(paths[role], role) for role in paths]
        recipe = build_identity_recipe(stage=STAGE, required_artifact_roles=paths.keys(), parameters=parameters,
                                       method_version="tassel_mlm_p3d.v1", contract_version="v1",
                                       tool_versions={"tassel": version})
        identity = materialize_stage_identity(recipe, {item["artifact"]: item for item in input_records}, scopes)
        identity12 = identity["analysis_scope_id"].split(":", 1)[1][:12]
        stem = f"{safe_prefix}.{STAGE}.{identity12}"
        success_manifest_path = final_dir / f"{stem}.manifest.json"
        publication_lock = workspace / ".locks" / f"{stem}.lock"
        manifest.update(identity)
        manifest.update({"identity_recipe": recipe, "inputs": input_records,
                         "software": {"tassel": {"resolved_path": executable, "version": version}}})
        attempt = workspace / ".attempts" / run_id
        attempt.mkdir(parents=True, exist_ok=False)
        logs: List[str] = []
        model_files: Dict[str, Path] = {}
        pvalue_files: Dict[str, Path] = {}
        for model, config in MODELS.items():
            export_prefix = attempt / model
            full = Path(str(export_prefix) + "2.txt")
            pvalues = attempt / f"{model}.pvalues.tsv"
            tassel_command = [
                executable, "-Xms512m", f"-Xmx{args.xmx}",
                "-fork1", "-vcf", str(paths["sorted_vcf"]),
                "-fork2", "-t", str(paths["trait_table"]),
                "-fork3", "-q", str(paths["covariate_table"]), "-excludeLastTrait",
                "-fork4", "-k", str(paths["kinship_matrix"]),
                "-combine5", "-input1", "-input2", "-input3", "-intersect",
                "-combine6", "-input5", "-input4",
                "-mlm", "-mlmVarCompEst", "P3D", "-mlmCompressionLevel", config["compression"],
                "-export", str(export_prefix),
                "-runfork1", "-runfork2", "-runfork3", "-runfork4",
            ]
            logs.append(f"[{model}]\n" + _run_model(tassel_command, full, model))
            metrics[model] = _parse_association(full, pvalues)
            model_files[model] = full
            pvalue_files[model] = pvalues
        log_work = attempt / "tassel.log"
        environment_work = attempt / "environment.txt"
        report_work = attempt / "report.md"
        log_work.write_text("\n".join(logs), encoding="utf-8")
        environment_work.write_text(environment, encoding="utf-8")
        marker_reason_summary = ", ".join(
            f"{reason}={count}"
            for reason, count in metrics["nonestimable_marker_reason_counts"].items()
        ) or "none"
        report_work.write_text(
            "# GWAS TASSEL MLM/CMLM\n\n"
            f"- Genotype scope: `{args.genotype_scope_id}`\n- Phenotype scope: `{args.phenotype_scope_id}`\n"
            f"- Covariate scope: `{args.covariate_scope_id}`\n- Model scope: `{identity['model_scope_id']}`\n"
            f"- Analysis scope: `{identity['analysis_scope_id']}`\n- Samples: {metrics['sample_count']}\n"
            f"- Candidate markers: {metrics['candidate_marker_count']}\n"
            f"- Estimable markers: {metrics['estimable_marker_count']}\n"
            f"- Non-estimable markers: {metrics['nonestimable_marker_count']}\n"
            f"- Non-estimable reasons: {marker_reason_summary}\n"
            f"- Effective covariates: {metrics['effective_covariate_count']}\n"
            f"- Omitted covariate: `{metrics['omitted_covariate']}`\n- Residual degrees of freedom: {metrics['residual_degrees_of_freedom']}\n"
            f"- TASSEL: `{version}`\n", encoding="utf-8")
        destinations = {
            "mlm_full": final_dir / f"{stem}.mlm.txt", "cmlm_full": final_dir / f"{stem}.cmlm.txt",
            "mlm_pvalues": final_dir / f"{stem}.mlm.pvalues.tsv",
            "cmlm_pvalues": final_dir / f"{stem}.cmlm.pvalues.tsv", "log": final_dir / f"{stem}.log",
            "report": final_dir / f"{stem}.report.md", "environment": final_dir / f"{stem}.environment.txt",
        }
        sources = {"mlm_full": model_files["mlm"], "cmlm_full": model_files["cmlm"],
                   "mlm_pvalues": pvalue_files["mlm"], "cmlm_pvalues": pvalue_files["cmlm"],
                   "log": log_work, "report": report_work, "environment": environment_work}
        media = {"report": "text/markdown", "environment": "text/plain", "log": "text/plain"}
        manifest.update({"status": "success", "metrics": metrics,
                         "artifacts": {
                             role: _staged_artifact(
                                 workspace, sources[role], destination, role,
                                 media.get(role, "text/tab-separated-values"),
                             )
                             for role, destination in destinations.items()
                         }})
        manifest["end_time"] = datetime.now(timezone.utc).isoformat()
        manifest["elapsed_sec"] = round(time.monotonic() - started_clock, 6)
        manifest_work = attempt / "manifest.json"
        _write_json(manifest_work, manifest)
        if args.log_file:
            args.log_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(log_work, args.log_file)
        success_bundle = _write_reproducibility(
            workspace, identity12, run_id, runner_command, manifest_work, manifest, environment,
            artifact_sources=sources, public_manifest_path=success_manifest_path,
        )
        published = _publish_stage_atomically(
            sources, destinations, manifest_work, success_manifest_path, publication_lock,
            str(manifest["start_time"]),
        )
        if not published and success_bundle is not None:
            shutil.rmtree(success_bundle)
            success_bundle = None
        if published:
            _remove_owned_failed_manifest(
                failed_manifest, run_id, failure_lock, str(manifest["start_time"])
            )
        return 0
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["artifacts"] = {}
        manifest["error"] = str(exc)
        manifest["end_time"] = datetime.now(timezone.utc).isoformat()
        manifest["elapsed_sec"] = round(time.monotonic() - started_clock, 6)
        attempt = workspace / ".attempts" / run_id
        attempt.mkdir(parents=True, exist_ok=True)
        if success_bundle is not None and success_bundle.is_dir():
            shutil.rmtree(success_bundle)
        attempt_failed_manifest = attempt / "failed.manifest.json"
        _write_json(attempt_failed_manifest, manifest)
        failure_published = _write_failed_manifest_if_fresh(
            failed_manifest, manifest, failure_lock, success_manifest_path
        )
        if args.log_file:
            args.log_file.parent.mkdir(parents=True, exist_ok=True)
            args.log_file.write_text(f"ERROR: {exc}\n", encoding="utf-8")
        evidence_manifest = failed_manifest if failure_published else attempt_failed_manifest
        _write_reproducibility(
            workspace, identity12, run_id, runner_command, evidence_manifest, manifest, environment
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
