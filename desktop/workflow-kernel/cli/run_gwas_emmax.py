#!/usr/bin/env python3
"""Run PLINK-derived kinship/PCA and EMMAX association atomically."""

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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import numpy as np

from gfa_core.gwas_scientific_identity import build_identity_recipe, materialize_stage_identity, new_run_id


STAGE = "gwas_emmax"
METHOD_VERSION = "plink_emmax_pca.v1"
MARKER_ID_SCHEME = "sha256_normalized_variant_tuple.v1.prefix24"
PSD_RELATIVE_TOLERANCE = 0.01
RANK_RELATIVE_TOLERANCE = 1e-10
KNOWN_EMMAX_SHA256 = "ae2a0ffa285b846b785efe68f0cefeb9bcd4b2a288e53e8368fd6ef01dd8a76b"
ESTIMABILITY_POLICY = "at_least_two_observed_diploid_genotype_states.v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _open_text(path: Path):
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open("r", encoding="utf-8")


@dataclass(frozen=True)
class CanonicalVcf:
    samples: list[str]
    marker_map: list[dict[str, Any]]
    input_variant_count: int
    nonbiallelic_excluded_count: int
    half_call_count: int


@dataclass(frozen=True)
class Relationship:
    matrix: np.ndarray
    sample_ids: list[str]
    eigenvalues: np.ndarray
    minimum_eigenvalue: float
    maximum_eigenvalue: float
    tolerance: float
    marker_rank: int
    diagonal_min: float
    diagonal_max: float


def _canonical_id(chrom: str, pos: int, ref: str, alt: str) -> str:
    payload = json.dumps([chrom, pos, ref, alt], ensure_ascii=False, separators=(",", ":"))
    return "v_" + hashlib.sha256(payload.encode()).hexdigest()[:24]


def _canonicalize_vcf(source: Path, destination: Path) -> CanonicalVcf:
    samples: Optional[list[str]] = None
    marker_map: list[dict[str, Any]] = []
    tuples: set[tuple[str, int, str, str]] = set()
    ids: dict[str, tuple[str, int, str, str]] = {}
    input_count = excluded = half_calls = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _open_text(source) as incoming, destination.open("w", encoding="utf-8") as outgoing:
        for line_number, raw in enumerate(incoming, 1):
            if raw.startswith("##"):
                outgoing.write(raw)
                continue
            if raw.startswith("#CHROM\t"):
                if samples is not None:
                    raise ValueError("sorted VCF contains multiple #CHROM headers")
                fields = raw.rstrip("\r\n").split("\t")
                if len(fields) < 10:
                    raise ValueError("sorted VCF must contain genotype samples")
                samples = fields[9:]
                if any(not sample for sample in samples) or len(samples) != len(set(samples)):
                    raise ValueError("sorted VCF sample IDs must be nonempty and unique")
                outgoing.write(raw)
                continue
            if not raw.strip() or raw.startswith("#"):
                continue
            if samples is None:
                raise ValueError("sorted VCF #CHROM header is missing")
            fields = raw.rstrip("\r\n").split("\t")
            if len(fields) != 9 + len(samples):
                raise ValueError(f"malformed sorted VCF variant at line {line_number}")
            input_count += 1
            chrom, pos_text, original_id, ref, alt = fields[:5]
            try:
                pos = int(pos_text)
            except ValueError as exc:
                raise ValueError(f"VCF position must be an integer at line {line_number}") from exc
            chrom, ref, alt = chrom.strip(), ref.strip().upper(), alt.strip().upper()
            if not chrom or pos <= 0 or not ref or not alt or alt == ".":
                raise ValueError(f"VCF variant tuple is invalid at line {line_number}")
            normalized = (chrom, pos, ref, alt)
            if normalized in tuples:
                raise ValueError("duplicate normalized variant tuple")
            tuples.add(normalized)
            format_fields = fields[8].split(":")
            if "GT" in format_fields:
                gt_index = format_fields.index("GT")
                for sample_field in fields[9:]:
                    values = sample_field.split(":")
                    gt = values[gt_index].replace("|", "/") if gt_index < len(values) else "."
                    alleles = gt.split("/")
                    if len(alleles) >= 2 and any(value == "." for value in alleles) and not all(value == "." for value in alleles):
                        half_calls += 1
            if "," in alt:
                excluded += 1
                continue
            canonical = _canonical_id(chrom, pos, ref, alt)
            if canonical in ids and ids[canonical] != normalized:
                raise ValueError("canonical marker hash-prefix collision")
            ids[canonical] = normalized
            marker_map.append({"canonical_id": canonical, "original_id": original_id,
                               "chrom": chrom, "pos": pos, "ref": ref, "alt": alt})
            fields[2], fields[3], fields[4] = canonical, ref, alt
            outgoing.write("\t".join(fields) + "\n")
    if samples is None:
        raise ValueError("sorted VCF #CHROM header is missing")
    if input_count == 0 or not marker_map:
        raise ValueError("sorted VCF contains no eligible biallelic variants")
    return CanonicalVcf(samples, marker_map, input_count, excluded, half_calls)


def _read_trait(path: Path) -> dict[str, Any]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines or not lines[0].startswith("<Trait>\t") or len(lines[0].split("\t")) != 2:
        raise ValueError("prepared trait table header is invalid")
    trait_name = lines[0].split("\t")[1]
    values: dict[str, float] = {}
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) != 2 or not fields[0]:
            raise ValueError("prepared trait table row is invalid")
        if fields[0] in values:
            raise ValueError("prepared trait sample IDs must be unique")
        try:
            value = float(fields[1])
        except ValueError as exc:
            raise ValueError("prepared trait values must be numeric") from exc
        if not math.isfinite(value):
            raise ValueError("prepared trait values must be finite")
        values[fields[0]] = value
    if not values:
        raise ValueError("prepared trait table contains no samples")
    if min(values.values()) == max(values.values()):
        raise ValueError("phenotype variance must be positive")
    return {"trait_name": trait_name, "values": values}


def _validate_trait_subset(trait: dict[str, Any], genotype_samples: Sequence[str]) -> list[str]:
    missing = sorted(set(trait["values"]) - set(genotype_samples))
    if missing:
        raise ValueError("phenotype samples absent from genotype: " + ",".join(missing))
    return [sample for sample in genotype_samples if sample not in trait["values"]]


def _ordered_phenotype(trait: dict[str, Any], sample_order: Sequence[str]) -> list[tuple[str, float]]:
    missing = [sample for sample in sample_order if sample not in trait["values"]]
    if missing or set(sample_order) != set(trait["values"]):
        raise ValueError("TFAM sample set does not match phenotype sample set")
    return [(sample, float(trait["values"][sample])) for sample in sample_order]


def _read_two_id_file(path: Path, label: str) -> list[str]:
    rows: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) < 2 or fields[0] != fields[1]:
            raise ValueError(f"{label} must contain matching FID/IID")
        rows.append(fields[1])
    if not rows or len(rows) != len(set(rows)):
        raise ValueError(f"{label} sample IDs must be nonempty and unique")
    return rows


def _read_relationship(rel_path: Path, rel_id_path: Path, expected_order: Sequence[str]) -> Relationship:
    ids = _read_two_id_file(rel_id_path, "REL-ID")
    if ids != list(expected_order):
        raise ValueError("REL-ID order does not match TFAM order")
    lines = [line for line in rel_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    n = len(ids)
    if len(lines) != n:
        raise ValueError("relationship lower triangle row count is invalid")
    matrix = np.zeros((n, n), dtype=float)
    for i, line in enumerate(lines):
        fields = line.split()
        if len(fields) != i + 1:
            raise ValueError("relationship lower triangle width is invalid")
        try:
            values = [float(value) for value in fields]
        except ValueError as exc:
            raise ValueError("relationship matrix contains nonnumeric values") from exc
        if not all(math.isfinite(value) for value in values):
            raise ValueError("relationship matrix contains nonfinite values")
        matrix[i, : i + 1] = values
        matrix[: i + 1, i] = values
    if not np.allclose(matrix, matrix.T, rtol=0, atol=1e-12):
        raise ValueError("relationship matrix is asymmetric")
    eigenvalues = np.linalg.eigvalsh(matrix)
    scale = max(float(np.max(np.abs(eigenvalues))), np.finfo(float).eps)
    tolerance = PSD_RELATIVE_TOLERANCE * scale
    if float(eigenvalues[0]) < -tolerance:
        raise ValueError("relationship matrix exceeds positive-semidefinite tolerance")
    rank_tolerance = RANK_RELATIVE_TOLERANCE * scale
    rank = int(np.count_nonzero(eigenvalues > rank_tolerance))
    diagonal = np.diag(matrix)
    return Relationship(matrix, ids, eigenvalues, float(eigenvalues[0]), float(eigenvalues[-1]),
                        tolerance, rank, float(diagonal.min()), float(diagonal.max()))


def _realized_pc_count(*, requested: int, sample_count: int, marker_rank: int) -> int:
    if requested < 0 or sample_count < 3 or marker_rank < 0:
        raise ValueError("PCA dimensions are invalid")
    return min(requested, sample_count - 2, marker_rank)


def _marker_matrix_rank(tped_path: Path, sample_count: int) -> tuple[int, np.ndarray]:
    """Return rank of the centered genotype matrix without materializing M x N."""
    if sample_count < 1:
        raise ValueError("TPED sample count is invalid")
    gram = np.zeros((sample_count, sample_count), dtype=float)
    marker_count = 0
    for line_number, line in enumerate(tped_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4 + 2 * sample_count:
            raise ValueError(f"TPED genotype width is invalid at line {line_number}")
        dosage = np.empty(sample_count, dtype=float)
        dosage.fill(np.nan)
        for index in range(sample_count):
            alleles = fields[4 + 2 * index: 6 + 2 * index]
            if "0" in alleles:
                continue
            if any(allele not in {"1", "2"} for allele in alleles):
                raise ValueError(f"TPED genotype allele is invalid at line {line_number}")
            dosage[index] = float(alleles.count("2"))
        observed = np.isfinite(dosage)
        if not observed.any():
            continue
        mean = float(np.mean(dosage[observed]))
        dosage[~observed] = mean
        dosage -= mean
        gram += np.outer(dosage, dosage)
        marker_count += 1
    if marker_count == 0:
        raise ValueError("TPED contains no usable genotype markers")
    eigenvalues = np.linalg.eigvalsh(gram)
    scale = max(float(np.max(np.abs(eigenvalues))), np.finfo(float).eps)
    rank = int(np.count_nonzero(eigenvalues > RANK_RELATIVE_TOLERANCE * scale))
    return rank, eigenvalues


def _filter_tped_estimable_markers(
    tped_path: Path, *, sample_count: int, marker_map: dict[str, dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Atomically retain TPED markers with at least two observed genotype states."""
    if sample_count < 1:
        raise ValueError("TPED sample count is invalid")
    seen: set[str] = set()
    retained_ids: list[str] = []
    retained_lines: list[str] = []
    no_called = nonvariable = 0
    for line_number, line in enumerate(tped_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4 + 2 * sample_count:
            raise ValueError(f"TPED genotype width is invalid at line {line_number}")
        marker_id = fields[1]
        if marker_id in seen:
            raise ValueError("TPED marker IDs must be unique")
        seen.add(marker_id)
        if marker_id not in marker_map:
            raise ValueError(f"TPED contains unexpected marker ID: {marker_id}")
        states: set[tuple[str, str]] = set()
        for index in range(sample_count):
            first, second = fields[4 + 2 * index: 6 + 2 * index]
            if (first == "0") != (second == "0"):
                raise ValueError(f"TPED contains a half-call at line {line_number}")
            if first == second == "0":
                continue
            if first not in {"1", "2"} or second not in {"1", "2"}:
                raise ValueError(f"TPED genotype allele is invalid at line {line_number}")
            states.add(tuple(sorted((first, second))))
        if not states:
            no_called += 1
        elif len(states) == 1:
            nonvariable += 1
        else:
            retained_ids.append(marker_id)
            retained_lines.append(line)
    if not retained_ids:
        raise ValueError("TPED contains no estimable markers")
    temporary = tped_path.with_name(f".{tped_path.name}.{uuid.uuid4().hex}.estimability")
    try:
        temporary.write_text("\n".join(retained_lines) + "\n", encoding="utf-8")
        os.replace(temporary, tped_path)
    finally:
        temporary.unlink(missing_ok=True)
    candidate_count = len(seen)
    tested_count = len(retained_ids)
    if candidate_count != no_called + nonvariable + tested_count:
        raise AssertionError("TPED estimability accounting is inconsistent")
    return (
        {marker_id: marker_map[marker_id] for marker_id in retained_ids},
        {
            "emmax_candidate_marker_count": candidate_count,
            "no_called_genotype_excluded_count": no_called,
            "nonvariable_genotype_excluded_count": nonvariable,
            "emmax_tested_count": tested_count,
            "estimability_policy": ESTIMABILITY_POLICY,
        },
    )


def _validate_design(eigenvectors: np.ndarray, *, realized_pcs: int) -> tuple[int, int]:
    if eigenvectors.ndim != 2 or eigenvectors.shape[1] < realized_pcs:
        raise ValueError("PCA eigenvector dimensions are invalid")
    design = np.column_stack([np.ones(eigenvectors.shape[0]), eigenvectors[:, :realized_pcs]])
    rank = int(np.linalg.matrix_rank(design))
    if rank != design.shape[1]:
        raise ValueError("intercept plus PCA covariates must have full rank")
    residual_df = eigenvectors.shape[0] - rank
    if residual_df <= 0:
        raise ValueError("model must retain positive residual degrees of freedom")
    return rank, residual_df


def _run_required_tool(command: list[str], label: str, *, required_output: Optional[Path] = None,
                       cwd: Optional[Path] = None,
                       evidence_sink: Optional[list[str]] = None) -> str:
    completed = subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, check=False)
    output = completed.stdout or ""
    if evidence_sink is not None:
        evidence_sink.append(f"[tool:{label}]\n{output.rstrip()}\n")
    if completed.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {completed.returncode}")
    if required_output is not None and (not required_output.is_file() or required_output.stat().st_size == 0):
        raise RuntimeError(f"{label} completed without required output")
    return output


def _parse_emmax_ps(source: Path, marker_map: dict[str, dict[str, Any]], full_path: Path,
                    pvalue_path: Path) -> dict[str, Any]:
    full = ["SNPID\tChr\tPos\tbeta\tSE\tp"]
    pvalues = ["chr\tpos\tp"]
    seen: set[str] = set()
    source_field_count: Optional[int] = None
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) not in {3, 4}:
            raise ValueError("EMMAX .ps rows must contain exactly three or four fields")
        if source_field_count is None:
            source_field_count = len(fields)
        elif len(fields) != source_field_count:
            raise ValueError("EMMAX .ps must use one consistent three- or four-field schema")
        if source_field_count == 3:
            marker, beta_text, p_text = fields
            se_text: Optional[str] = None
        else:
            marker, beta_text, se_text, p_text = fields
        if marker in seen:
            raise ValueError("EMMAX .ps contains duplicate SNPID")
        if marker not in marker_map:
            raise ValueError(f"EMMAX .ps contains unexpected SNPID: {marker}")
        try:
            beta, pvalue = float(beta_text), float(p_text)
            se = float(se_text) if se_text is not None else None
        except ValueError as exc:
            raise ValueError("EMMAX beta/SE/p must be numeric") from exc
        if not math.isfinite(beta) or not math.isfinite(pvalue) or (se is not None and not math.isfinite(se)):
            raise ValueError("EMMAX beta/SE/p must be finite")
        if se is not None and se < 0:
            raise ValueError("EMMAX SE must be nonnegative")
        if not 0 < pvalue <= 1:
            raise ValueError("EMMAX p-value must satisfy 0 < p <= 1")
        seen.add(marker)
        record = marker_map[marker]
        rendered_se = se_text if se_text is not None else "NA"
        full.append(f"{marker}\t{record['chrom']}\t{record['pos']}\t{beta_text}\t{rendered_se}\t{p_text}")
        pvalues.append(f"{record['chrom']}\t{record['pos']}\t{p_text}")
    if not seen:
        raise ValueError("EMMAX .ps contains no association rows")
    missing = set(marker_map) - seen
    if missing:
        raise ValueError(f"EMMAX .ps is missing {len(missing)} mapped SNPIDs")
    full_path.write_text("\n".join(full) + "\n", encoding="utf-8")
    pvalue_path.write_text("\n".join(pvalues) + "\n", encoding="utf-8")
    assert source_field_count in {3, 4}
    return {"emmax_tested_count": len(seen), "mapped_count": len(seen), "duplicate_count": 0,
            "unmapped_count": 0, "unexpected_count": 0, "finite_beta_count": len(seen),
            "finite_se_count": len(seen) if source_field_count == 4 else 0,
            "missing_se_count": len(seen) if source_field_count == 3 else 0,
            "valid_p_count": len(seen), "source_ps_field_count": source_field_count,
            "source_ps_schema": "SNPID_beta_p" if source_field_count == 3 else "SNPID_beta_SE_p"}


def _safe_prefix(prefix: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", prefix).strip("._") or "cohort"


def _runner_command(args: argparse.Namespace) -> list[str]:
    command = ["python", "-m", "cli.run_gwas_emmax", "--workspace", str(args.workspace.resolve()),
               "--job-id", args.job_id, "--prefix", args.prefix, "--vcf", str(args.vcf.resolve()),
               "--trait", str(args.trait.resolve()), "--vcf-manifest", str(args.vcf_manifest.resolve()),
               "--trait-manifest", str(args.trait_manifest.resolve()),
               "--genotype-scope-id", args.genotype_scope_id,
               "--phenotype-scope-id", args.phenotype_scope_id, "--plink", args.plink,
               "--emmax", args.emmax, "--rscript", args.rscript, "--requested-pcs", str(args.requested_pcs),
               "--threads", str(args.threads)]
    if args.log_file:
        command.extend(["--log-file", str(args.log_file.resolve())])
    return command


def _artifact(workspace: Path, source: Path, destination: Path, role: str, media_type: str) -> dict[str, Any]:
    size = source.stat().st_size
    return {"artifact": role, "role": role, "path": str(destination.relative_to(workspace)),
            "full_path": str(destination.resolve()), "type": media_type, "media_type": media_type,
            "size": size, "size_bytes": size, "sha256": sha256_file(source)}


def _authoritative_input_record(path: Path, role: str, producer_manifest_path: Path,
                                producer_stage: str, scope_field: str,
                                expected_scope: str,
                                require_analysis_scope: bool = False) -> dict[str, Any]:
    path = path.resolve()
    producer_manifest_path = producer_manifest_path.resolve()
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
    run_id = str(producer.get("run_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", run_id):
        raise ValueError(f"{producer_stage} producer Manifest run_id is empty or unsafe")
    analysis_scope = str(producer.get("analysis_scope_id") or "")
    if ((require_analysis_scope and not analysis_scope)
            or (analysis_scope and not re.fullmatch(r"gwas-analysis:[0-9a-f]{64}", analysis_scope))):
        raise ValueError(f"{producer_stage} producer Manifest analysis_scope_id is invalid")
    if producer.get(scope_field) != expected_scope:
        raise ValueError(f"{role} producer Manifest {scope_field} does not match")
    artifact = (producer.get("artifacts") or {}).get(role)
    if not isinstance(artifact, dict) or artifact.get("role") != role or artifact.get("artifact") != role:
        raise ValueError(f"{role} producer Manifest artifact role is invalid")
    if Path(str(artifact.get("full_path") or "")).resolve() != path:
        raise ValueError(f"{role} producer Manifest artifact path does not match")
    size = path.stat().st_size
    digest = sha256_file(path)
    if artifact.get("size_bytes", artifact.get("size")) != size:
        raise ValueError(f"{role} producer Manifest artifact size does not match")
    if artifact.get("sha256") != digest:
        raise ValueError(f"{role} producer Manifest artifact SHA256 does not match")
    record = {"source": "manifest", "artifact": role, "role": role, "path": str(path),
            "full_path": str(path), "size": size, "size_bytes": size, "sha256": digest,
            "producer_stage": producer_stage, "producer_run_id": run_id,
            "producer_manifest_path": str(producer_manifest_path),
            "producer_manifest_sha256": sha256_file(producer_manifest_path),
            "producer_scope_field": scope_field, "producer_scope_id": expected_scope}
    if analysis_scope:
        record["producer_analysis_scope_id"] = analysis_scope
    return record


def _revalidate_input_records(records: Sequence[dict[str, Any]]) -> None:
    for record in records:
        path = Path(str(record["full_path"])).resolve()
        if (not path.is_file() or path.stat().st_size != record["size_bytes"]
                or sha256_file(path) != record["sha256"]):
            raise RuntimeError(f"authoritative {record['artifact']} changed during execution")
        manifest_path = Path(str(record["producer_manifest_path"])).resolve()
        if (not manifest_path.is_file()
                or sha256_file(manifest_path) != record["producer_manifest_sha256"]):
            raise RuntimeError(f"authoritative {record['artifact']} producer Manifest changed during execution")


def _warning_lines(outputs: Iterable[str]) -> list[str]:
    return [line.strip() for output in outputs for line in output.splitlines()
            if "warning" in line.lower()]


def _write_reproducibility(workspace: Path, identity12: str, run_id: str, command: list[str],
                           manifest_path: Path, manifest: dict[str, Any], environment: str,
                           artifact_sources: Optional[dict[str, Path]] = None,
                           public_manifest_path: Optional[Path] = None) -> Path:
    bundle = workspace / "reproducibility" / STAGE / f"{_safe_prefix(manifest['prefix'])}.{identity12}" / run_id
    bundle.mkdir(parents=True, exist_ok=False)
    canonical = shlex.join(command)
    (bundle / "commands.sh").write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + canonical + "\n", encoding="utf-8")
    (bundle / "commands.sh").chmod(0o755)
    (bundle / "environment.txt").write_text(environment, encoding="utf-8")
    recorded_manifest = public_manifest_path or manifest_path
    _write_json(bundle / "progress.json", {"stage": STAGE, "status": manifest["status"], "run_id": run_id,
                                             "manifest_path": str(recorded_manifest.relative_to(workspace))})
    _write_json(bundle / "provenance.json", {"stage": STAGE, "run_id": run_id,
                                               "runner_command": canonical, "software": manifest.get("software", {}),
                                               "inputs": manifest.get("inputs", []), "artifacts": manifest.get("artifacts", {})})
    checksums = [(manifest_path, str(recorded_manifest.relative_to(workspace)))]
    for role, artifact in manifest.get("artifacts", {}).items():
        source = artifact_sources.get(role) if artifact_sources else workspace / artifact["path"]
        if source and source.is_file():
            checksums.append((source, artifact["path"]))
    (bundle / "checksums.sha256").write_text("".join(f"{sha256_file(path)}  {label}\n" for path, label in sorted(checksums, key=lambda item: item[1])), encoding="utf-8")
    return bundle


def _publish(sources: dict[str, Path], destinations: dict[str, Path], manifest_source: Path,
             manifest_destination: Path, lock_path: Path, started_at: str) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if manifest_destination.is_file():
            try:
                previous = json.loads(manifest_destination.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous = {}
            if previous.get("status") == "success" and str(previous.get("start_time", "")) > started_at:
                return False
        token = uuid.uuid4().hex
        staged: list[tuple[Path, Path]] = []
        for role, source in sources.items():
            destination = destinations[role]
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.{token}.publishing")
            shutil.copy2(source, temporary)
            staged.append((temporary, destination))
        temporary_manifest = manifest_destination.with_name(f".{manifest_destination.name}.{token}.publishing")
        shutil.copy2(manifest_source, temporary_manifest)
        backups: dict[Path, Optional[Path]] = {}
        try:
            for _temporary, destination in [*staged, (temporary_manifest, manifest_destination)]:
                if destination.is_file():
                    backup = destination.with_name(f".{destination.name}.{token}.rollback")
                    shutil.copy2(destination, backup)
                    backups[destination] = backup
                else:
                    backups[destination] = None
            for temporary, destination in staged:
                os.replace(temporary, destination)
            os.replace(temporary_manifest, manifest_destination)
        except Exception:
            for destination, backup in backups.items():
                if backup is None:
                    destination.unlink(missing_ok=True)
                elif backup.is_file():
                    os.replace(backup, destination)
            for temporary, _destination in staged:
                temporary.unlink(missing_ok=True)
            temporary_manifest.unlink(missing_ok=True)
            for backup in backups.values():
                if backup is not None:
                    backup.unlink(missing_ok=True)
            raise
        for backup in backups.values():
            if backup is not None:
                backup.unlink(missing_ok=True)
    return True


def _publish_failed_manifest(path: Path, manifest: dict[str, Any], lock_path: Path,
                             success_manifest_path: Optional[Path] = None) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        started_at = str(manifest.get("start_time") or "")
        for candidate in (success_manifest_path, path):
            if candidate is None or not candidate.is_file():
                continue
            try:
                existing = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if str(existing.get("start_time") or "") > started_at:
                return False
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.publishing")
        _write_json(temporary, manifest)
        os.replace(temporary, path)
    return True


def _clear_failed_manifest_if_winner(failed_path: Path, lock_path: Path,
                                     success_manifest_path: Path, run_id: str) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            success = json.loads(success_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if success.get("status") != "success" or success.get("run_id") != run_id:
            return False
        if not failed_path.is_file():
            return True
        try:
            failed = json.loads(failed_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if str(failed.get("start_time") or "") > str(success.get("start_time") or ""):
            return False
        failed_path.unlink()
        return True


def _publish_with_bundle(sources: dict[str, Path], destinations: dict[str, Path],
                         manifest_source: Path, manifest_destination: Path, lock_path: Path,
                         started_at: str, bundle: Path) -> bool:
    try:
        published = _publish(sources, destinations, manifest_source, manifest_destination,
                             lock_path, started_at)
    except Exception:
        shutil.rmtree(bundle, ignore_errors=True)
        raise
    if not published:
        # Its checksum labels target the public paths, but those paths belong to
        # the newer winner. Keeping this bundle would make replay verification
        # falsely compare the older attempt hash with the newer Manifest.
        shutil.rmtree(bundle, ignore_errors=True)
    return published


def _tool_version(command: list[str], label: str) -> str:
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    text = (completed.stdout or "").strip().splitlines()
    if not text:
        raise RuntimeError(f"{label} version could not be proven")
    return text[0].strip()


def _emmax_provenance(path: Path) -> dict[str, str]:
    executable_hash = sha256_file(path)
    if executable_hash == KNOWN_EMMAX_SHA256:
        return {"release": "20120210", "sha256": executable_hash, "package": "upstream-binary"}
    owner = subprocess.run(["dpkg-query", "-S", str(path)], text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, check=False)
    if owner.returncode != 0 or ":" not in (owner.stdout or ""):
        raise RuntimeError("EMMAX executable provenance could not be proven")
    package = (owner.stdout or "").split(":", 1)[0].strip()
    if package != "emmax":
        raise RuntimeError("EMMAX executable is not owned by the official emmax package")
    version = subprocess.run(["dpkg-query", "-W", "-f=${Package}\t${Version}\\n", package],
                             text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    fields = (version.stdout or "").strip().split("\t")
    if version.returncode != 0 or len(fields) != 2 or fields[0] != package or not fields[1]:
        raise RuntimeError("EMMAX package version could not be proven")
    return {"release": fields[1], "sha256": executable_hash, "package": package}


def _write_matrix_via_r(rscript: Path, relationship: Relationship, attempt: Path,
                        evidence_sink: Optional[list[str]] = None) -> Path:
    source = attempt / "write_kinship.R"
    input_path = attempt / "kinship.python.tsv"
    output_path = attempt / "pop.kinship.txt"
    np.savetxt(input_path, relationship.matrix, delimiter="\t", fmt="%.10g")
    source.write_text("args <- commandArgs(trailingOnly=TRUE)\nx <- as.matrix(read.table(args[1], header=FALSE, check.names=FALSE))\nwrite.table(x, args[2], row.names=FALSE, col.names=FALSE, quote=FALSE, sep=' ')\n", encoding="utf-8")
    _run_required_tool([str(rscript), str(source), str(input_path), str(output_path)],
                       "R kinship materialization", required_output=output_path, cwd=attempt,
                       evidence_sink=evidence_sink)
    rendered = np.loadtxt(output_path, dtype=float, ndmin=2)
    if rendered.shape != relationship.matrix.shape or not np.allclose(rendered, relationship.matrix, rtol=1e-8, atol=1e-10):
        raise ValueError("R materialized relationship matrix does not preserve PLINK values")
    return output_path


def _read_marker_ids(path: Path, column: int, label: str) -> list[str]:
    ids = [line.split()[column] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError(f"{label} marker IDs must be nonempty and unique")
    return ids


def _require_exact_marker_set(actual: Sequence[str], expected: set[str], label: str) -> None:
    if set(actual) != expected:
        raise ValueError(f"{label} is not authorized")


def _read_eigenvectors(path: Path, expected_order: Sequence[str], realized_pcs: int) -> np.ndarray:
    ids: list[str] = []
    rows: list[list[float]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 2 + realized_pcs or fields[0] != fields[1]:
            raise ValueError("PLINK PCA eigenvector row width or FID/IID is invalid")
        ids.append(fields[1])
        try:
            rows.append([float(value) for value in fields[2:]])
        except ValueError as exc:
            raise ValueError("PLINK PCA eigenvectors must be numeric") from exc
    if ids != list(expected_order):
        raise ValueError("PLINK PCA sample order does not match TFAM order")
    matrix = np.asarray(rows, dtype=float)
    if not np.isfinite(matrix).all():
        raise ValueError("PLINK PCA eigenvectors must be finite")
    return matrix


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--vcf", required=True, type=Path)
    parser.add_argument("--trait", required=True, type=Path)
    parser.add_argument("--vcf-manifest", required=True, type=Path)
    parser.add_argument("--trait-manifest", required=True, type=Path)
    parser.add_argument("--genotype-scope-id", required=True)
    parser.add_argument("--phenotype-scope-id", required=True)
    parser.add_argument("--plink", required=True)
    parser.add_argument("--emmax", required=True)
    parser.add_argument("--rscript", required=True)
    parser.add_argument("--requested-pcs", type=int, default=3)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--log-file", type=Path)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    workspace, vcf, trait_path = args.workspace.resolve(), args.vcf.resolve(), args.trait.resolve()
    final_dir = workspace / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    run_id = new_run_id(STAGE)
    attempt = workspace / ".attempts" / run_id
    attempt.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc)
    clock = time.monotonic()
    safe_prefix = _safe_prefix(args.prefix)
    runner = _runner_command(args)
    parameters = {"requested_pcs": args.requested_pcs,
                  "pc_selection_rule": "min(requested_pcs,N-2,marker_matrix_rank)",
                  "multiallelic_policy": "exclude", "vcf_half_call_policy": "missing",
                  "estimability_policy": ESTIMABILITY_POLICY,
                  "marker_id_scheme": MARKER_ID_SCHEME, "emmax_precision_digits": 10,
                  "psd_relative_tolerance": PSD_RELATIVE_TOLERANCE,
                  "rank_relative_tolerance": RANK_RELATIVE_TOLERANCE}
    manifest: dict[str, Any] = {"manifest_version": "1.0", "stage": STAGE, "prefix": args.prefix,
                                "job_id": args.job_id, "run_id": run_id, "status": "failed",
                                "timestamp": started.isoformat(), "start_time": started.isoformat(),
                                "end_time": started.isoformat(), "elapsed_sec": 0.0, "cmd": runner,
                                "workspace_root": str(workspace), "workspace": str(final_dir),
                                "input_dir": str(workspace / "input"), "parameters": parameters,
                                "metrics": {}, "artifacts": {}}
    environment = ""
    identity12 = "failed"
    logs: list[str] = []
    log_file = args.log_file.resolve() if args.log_file else workspace / "logs" / f"{run_id}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        if args.threads < 1 or args.threads > 64 or args.requested_pcs < 0:
            raise ValueError("threads must be 1..64 and requested PCs must be nonnegative")
        for name, value, prefix in (("genotype_scope_id", args.genotype_scope_id, "genotype"),
                                    ("phenotype_scope_id", args.phenotype_scope_id, "phenotype")):
            if not re.fullmatch(prefix + r":[0-9a-f]{64}", value):
                raise ValueError(f"{name} is invalid")
        if not vcf.is_file() or not trait_path.is_file():
            raise FileNotFoundError("authoritative sorted_vcf and trait_table are required")
        tools = {name: Path(value).resolve() for name, value in (("plink", args.plink), ("emmax", args.emmax), ("rscript", args.rscript))}
        for name, path in tools.items():
            if not path.is_file() or not os.access(path, os.X_OK):
                raise FileNotFoundError(f"{name} executable was not found or is not executable")
        plink_version = _tool_version([str(tools["plink"]), "--version"], "PLINK")
        r_version = _tool_version([str(tools["rscript"]), "--version"], "Rscript")
        emmax_provenance = _emmax_provenance(tools["emmax"])
        emmax_hash = emmax_provenance["sha256"]
        emmax_release = emmax_provenance["release"]
        environment = "\n".join(["runner=cli.run_gwas_emmax.py",
            f"runtime_environment={os.environ.get('CONDA_DEFAULT_ENV', 'unknown')}",
            f"python_resolved={Path(sys.executable).resolve()}", f"python_version={platform.python_version()}",
            f"plink_resolved={tools['plink']}", f"plink_version={plink_version}",
            f"emmax_resolved={tools['emmax']}", f"emmax_release={emmax_release}",
            f"emmax_package={emmax_provenance['package']}", f"emmax_sha256={emmax_hash}",
            "emmax_ps_supported_schemas=SNPID_beta_p|SNPID_beta_SE_p",
            "emmax_ps_official_schema=SNPID_beta_p",
            "emmax_ps_missing_se_policy=NA",
            f"rscript_resolved={tools['rscript']}", f"r_version={r_version}", f"platform={platform.platform()}"]) + "\n"
        inputs = [
            _authoritative_input_record(vcf, "sorted_vcf", args.vcf_manifest, "gwas_fmt",
                                        "genotype_scope_id", args.genotype_scope_id),
            _authoritative_input_record(trait_path, "trait_table", args.trait_manifest,
                                        "gwas_trait", "phenotype_scope_id",
                                        args.phenotype_scope_id, require_analysis_scope=True),
        ]
        manifest["inputs"] = inputs
        scopes = {"genotype_scope_id": args.genotype_scope_id, "phenotype_scope_id": args.phenotype_scope_id}
        recipe = build_identity_recipe(stage=STAGE, required_artifact_roles=["sorted_vcf", "trait_table"],
            parameters=parameters, method_version=METHOD_VERSION, contract_version="v1",
            tool_versions={"plink": plink_version, "emmax_release": emmax_release,
                           "emmax_sha256": emmax_hash, "R": r_version, "python": platform.python_version()})
        identity = materialize_stage_identity(recipe, {item["artifact"]: item for item in inputs}, scopes)
        manifest.update(identity)
        identity12 = identity["analysis_scope_id"].split(":", 1)[1][:12]
        canonical = _canonicalize_vcf(vcf, attempt / "canonical.vcf")
        trait = _read_trait(trait_path)
        genotype_only = _validate_trait_subset(trait, canonical.samples)
        keep = attempt / "trait.keep"
        keep.write_text("".join(f"{sample}\t{sample}\n" for sample in trait["values"]), encoding="utf-8")
        bed = attempt / "genotype"
        plink_outputs: list[str] = []
        plink_outputs.append(_run_required_tool([str(tools["plink"]), "--vcf", str(attempt / "canonical.vcf"),
            "--keep", str(keep), "--vcf-half-call", "missing", "--biallelic-only", "strict",
            "--allow-extra-chr", "--double-id", "--make-bed", "--threads", str(args.threads),
            "--out", str(bed)], "PLINK VCF conversion", required_output=bed.with_suffix(".bed"),
            cwd=attempt, evidence_sink=logs))
        fam_ids = _read_two_id_file(bed.with_suffix(".fam"), "FAM")
        if set(fam_ids) != set(trait["values"]):
            raise ValueError("PLINK retained FAM sample set does not match phenotype")
        marker_map = {row["canonical_id"]: row for row in canonical.marker_map}
        bim_ids = _read_marker_ids(bed.with_suffix(".bim"), 1, "BIM")
        _require_exact_marker_set(bim_ids, set(marker_map), "PLINK conversion marker loss")
        if canonical.input_variant_count != canonical.nonbiallelic_excluded_count + len(bim_ids):
            raise ValueError("PLINK variant loss accounting is inconsistent")
        rel = attempt / "relationship"
        plink_outputs.append(_run_required_tool([str(tools["plink"]), "--bfile", str(bed), "--make-rel",
            "--allow-extra-chr", "--out", str(rel)], "PLINK relationship",
            required_output=rel.with_suffix(".rel"), cwd=attempt, evidence_sink=logs))
        relationship = _read_relationship(rel.with_suffix(".rel"), rel.with_suffix(".rel.id"), fam_ids)
        kinship = _write_matrix_via_r(tools["rscript"], relationship, attempt, logs)
        trans = attempt / "emmax_genotype"
        plink_outputs.append(_run_required_tool([str(tools["plink"]), "--bfile", str(bed), "--recode", "12", "transpose",
            "--output-missing-genotype", "0", "--allow-extra-chr", "--out", str(trans)],
            "PLINK transpose", required_output=trans.with_suffix(".tped"), cwd=attempt,
            evidence_sink=logs))
        tfam_ids = _read_two_id_file(trans.with_suffix(".tfam"), "TFAM")
        if tfam_ids != fam_ids:
            raise ValueError("TFAM sample order does not match FAM order")
        tped_ids = _read_marker_ids(trans.with_suffix(".tped"), 1, "TPED")
        _require_exact_marker_set(tped_ids, set(marker_map), "TPED marker mismatch")
        tested_marker_map, estimability_metrics = _filter_tped_estimable_markers(
            trans.with_suffix(".tped"), sample_count=len(tfam_ids), marker_map=marker_map
        )
        environment += "\n".join(
            f"{key}={value}" for key, value in estimability_metrics.items()
        ) + "\n"
        marker_matrix_rank, marker_matrix_eigenvalues = _marker_matrix_rank(
            trans.with_suffix(".tped"), len(tfam_ids)
        )
        realized = _realized_pc_count(requested=args.requested_pcs, sample_count=len(fam_ids),
                                      marker_rank=marker_matrix_rank)
        pca = attempt / "pca"
        if realized:
            plink_outputs.append(_run_required_tool([str(tools["plink"]), "--bfile", str(bed), "--pca", str(realized),
                "--allow-extra-chr", "--out", str(pca)], "PLINK PCA",
                required_output=pca.with_suffix(".eigenvec"), cwd=attempt, evidence_sink=logs))
            eigenvectors = _read_eigenvectors(pca.with_suffix(".eigenvec"), tfam_ids, realized)
        else:
            eigenvectors = np.empty((len(tfam_ids), 0))
        design_rank, residual_df = _validate_design(eigenvectors, realized_pcs=realized)
        phenotype = attempt / "phenotype.txt"
        phenotype.write_text("".join(f"{sample}\t{sample}\t{value:.10g}\n" for sample, value in _ordered_phenotype(trait, tfam_ids)), encoding="utf-8")
        covariates = attempt / "covariates.txt"
        covariates.write_text("".join("\t".join([sample, sample, "1"] + [f"{value:.10g}" for value in eigenvectors[i]]) + "\n" for i, sample in enumerate(tfam_ids)), encoding="utf-8")
        output = attempt / "emmax_output"
        _run_required_tool([str(tools["emmax"]), "-v", "-d", "10", "-t", str(trans),
            "-p", str(phenotype), "-k", str(kinship), "-c", str(covariates), "-o", str(output)],
            "EMMAX", required_output=output.with_suffix(".ps"), cwd=attempt, evidence_sink=logs)
        full_source, p_source = attempt / "emmax_full.tsv", attempt / "emmax_pvalues.tsv"
        ps_metrics = _parse_emmax_ps(
            output.with_suffix(".ps"), tested_marker_map, full_source, p_source
        )
        if ps_metrics["emmax_tested_count"] != estimability_metrics["emmax_tested_count"]:
            raise ValueError("EMMAX tested-marker accounting is inconsistent")
        environment += f"emmax_ps_source_schema={ps_metrics['source_ps_schema']}\n"
        metrics = {"genotype_sample_count": len(canonical.samples), "phenotype_sample_count": len(trait["values"]),
            "retained_sample_count": len(tfam_ids), "genotype_only_sample_ids": genotype_only,
            "input_variant_count": canonical.input_variant_count,
            "nonbiallelic_excluded_count": canonical.nonbiallelic_excluded_count,
            "plink_retained_count": len(bim_ids), "tped_marker_count": len(tped_ids),
            "half_call_count": canonical.half_call_count, "vcf_half_call_policy": "missing",
            "requested_pcs": args.requested_pcs, "realized_pcs": realized,
            "marker_matrix_rank": marker_matrix_rank,
            "marker_matrix_eigenvalues": [float(value) for value in marker_matrix_eigenvalues],
            "marker_matrix_minimum_eigenvalue": float(marker_matrix_eigenvalues[0]),
            "marker_matrix_maximum_eigenvalue": float(marker_matrix_eigenvalues[-1]),
            "design_rank": design_rank,
            "residual_degrees_of_freedom": residual_df, "relationship_rows": len(tfam_ids),
            "relationship_columns": len(tfam_ids), "relationship_diagonal_min": relationship.diagonal_min,
            "relationship_diagonal_max": relationship.diagonal_max,
            "relationship_minimum_eigenvalue": relationship.minimum_eigenvalue,
            "relationship_maximum_eigenvalue": relationship.maximum_eigenvalue,
            "relationship_eigenvalues": [float(value) for value in relationship.eigenvalues],
            "relationship_psd_tolerance": relationship.tolerance,
            "plink_warnings": _warning_lines(plink_outputs), **estimability_metrics,
            **ps_metrics}
        stem = f"{safe_prefix}.{STAGE}.{identity12}"
        destinations = {"emmax_full": final_dir / f"{stem}.full.tsv",
                        "emmax_pvalues": final_dir / f"{stem}.pvalues.tsv",
                        "report": final_dir / f"{stem}.report.md",
                        "environment": final_dir / f"{stem}.environment.txt"}
        report_source, environment_source = attempt / "report.md", attempt / "environment.txt"
        report_source.write_text("\n".join(["# EMMAX association report", "", f"- Samples retained: {len(tfam_ids)}",
            f"- Input variants: {canonical.input_variant_count}", f"- Multiallelic excluded: {canonical.nonbiallelic_excluded_count}",
            f"- Candidate markers: {estimability_metrics['emmax_candidate_marker_count']}",
            f"- No-called genotype excluded: {estimability_metrics['no_called_genotype_excluded_count']}",
            f"- Nonvariable genotype excluded: {estimability_metrics['nonvariable_genotype_excluded_count']}",
            f"- Estimability policy: {ESTIMABILITY_POLICY}",
            f"- EMMAX tested markers: {ps_metrics['emmax_tested_count']}", f"- Requested/realized PCs: {args.requested_pcs}/{realized}",
            f"- Source .ps schema: {ps_metrics['source_ps_schema']} ({ps_metrics['source_ps_field_count']} fields)",
            f"- Standard errors: {ps_metrics['finite_se_count']} finite; {ps_metrics['missing_se_count']} missing (NA)",
            "- Official package .ps schema: SNPID_beta_p (3 fields; SE=NA)",
            f"- Kinship diagonal range: {relationship.diagonal_min:.10g}..{relationship.diagonal_max:.10g}",
            f"- Kinship eigenvalue range: {relationship.minimum_eigenvalue:.10g}..{relationship.maximum_eigenvalue:.10g}", ""]) , encoding="utf-8")
        environment_source.write_text(environment, encoding="utf-8")
        sources = {"emmax_full": full_source, "emmax_pvalues": p_source,
                   "report": report_source, "environment": environment_source}
        _revalidate_input_records(inputs)
        ended = datetime.now(timezone.utc)
        manifest.update({"status": "success", "timestamp": ended.isoformat(), "end_time": ended.isoformat(),
                         "elapsed_sec": round(time.monotonic() - clock, 6), "inputs": inputs,
                         "software": {"plink": plink_version, "emmax_release": emmax_release,
                                      "emmax_package": emmax_provenance["package"],
                                      "emmax_sha256": emmax_hash, "R": r_version,
                                      "python": platform.python_version()}, "metrics": metrics,
                         "artifacts": {role: _artifact(workspace, source, destinations[role], role,
                            "text/markdown" if role == "report" else "text/plain" if role == "environment" else "text/tab-separated-values")
                            for role, source in sources.items()}})
        manifest_source = attempt / "manifest.json"
        _write_json(manifest_source, manifest)
        manifest_destination = final_dir / f"{stem}.manifest.json"
        bundle = _write_reproducibility(workspace, identity12, run_id, runner, manifest_source,
                                        manifest, environment, sources, manifest_destination)
        published = _publish_with_bundle(
            sources, destinations, manifest_source, manifest_destination,
            workspace / ".locks" / f"{stem}.lock", started.isoformat(), bundle,
        )
        failed = final_dir / f"{safe_prefix}.{STAGE}.failed.manifest.json"
        if published:
            _clear_failed_manifest_if_winner(
                failed, workspace / ".locks" / f"{safe_prefix}.{STAGE}.failed.lock",
                manifest_destination, run_id,
            )
        disposition = "published" if published else "superseded by a newer success"
        log_file.write_text("".join(logs) + f"\n[success] {STAGE} completed ({disposition})\n", encoding="utf-8")
        return 0
    except Exception as exc:
        ended = datetime.now(timezone.utc)
        root_cause = re.sub(r"\s+", " ", str(exc)).strip()[:1000] or exc.__class__.__name__
        manifest.update({"status": "failed", "timestamp": ended.isoformat(), "end_time": ended.isoformat(),
                         "elapsed_sec": round(time.monotonic() - clock, 6), "artifacts": {}, "error": root_cause})
        failure_path = final_dir / f"{safe_prefix}.{STAGE}.failed.manifest.json"
        success_path = None
        if identity12 != "failed":
            success_path = final_dir / f"{safe_prefix}.{STAGE}.{identity12}.manifest.json"
        failure_written = _publish_failed_manifest(
            failure_path, manifest, workspace / ".locks" / f"{safe_prefix}.{STAGE}.failed.lock",
            success_path,
        )
        try:
            if failure_written:
                _write_reproducibility(workspace, identity12, run_id, runner, failure_path, manifest, environment)
        except Exception:
            pass
        log_file.write_text("".join(logs) + f"\n[error] {root_cause}\n", encoding="utf-8")
        print(f"ERROR: {root_cause}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
