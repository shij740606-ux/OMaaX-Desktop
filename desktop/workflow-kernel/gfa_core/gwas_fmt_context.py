"""Trusted request and result helpers for the canonical ``gwas_fmt`` stage."""

from __future__ import annotations

import gzip
import json
import os
import shutil
from pathlib import Path
from typing import Any

from gfa_core.genotype_context import genotype_scope_id_for_vcf, sha256_file


_WGS_GWAS_INPUTS = (
    ("wgs_maf_missing_filter", "final_snp_vcf"),
    ("wgs_impute", "imputed_snp_vcf"),
    ("wgs_variant_filter", "pass_snp_vcf"),
)


def _gwas_vcf(params: dict[str, Any]) -> str:
    return str(params.get("gwasVcf") or params.get("gwas_vcf") or "").strip()


def _vcf_shape(path: Path) -> tuple[int, int]:
    opener = gzip.open if path.name.lower().endswith(".gz") else open
    samples = 0
    sites = 0
    header_found = False
    with opener(path, "rt", encoding="utf-8") as handle:
        for raw in handle:
            if raw.startswith("#CHROM\t"):
                header_found = True
                samples = max(0, len(raw.rstrip("\r\n").split("\t")) - 9)
            elif raw.strip() and not raw.startswith("#"):
                sites += 1
    if not header_found:
        raise ValueError("#CHROM header was not found")
    if samples <= 0 or sites <= 0:
        raise ValueError(f"VCF requires >0 samples and >0 sites; observed samples={samples}, sites={sites}")
    return samples, sites


def _is_gwas_input_vcf(path: Path) -> bool:
    name = path.name.lower()
    if name.endswith((".g.vcf", ".g.vcf.gz", ".gvcf", ".gvcf.gz", ".tbi")):
        return False
    if ".gwas_fmt." in name:
        return False
    return name.endswith((".vcf", ".vcf.gz"))


def _vcf_candidate(path: Path, source: str, *, root: Path | None = None, source_manifest: str = "") -> dict[str, Any] | None:
    try:
        resolved = path.resolve(strict=True)
        if root is not None:
            resolved.relative_to(root.resolve(strict=True))
        if not resolved.is_file() or not _is_gwas_input_vcf(resolved):
            return None
        samples, sites = _vcf_shape(resolved)
        digest = sha256_file(resolved)
        return {
            "path": str(resolved),
            "name": resolved.name,
            "source": source,
            "source_manifest": source_manifest,
            "size_bytes": resolved.stat().st_size,
            "sha256": digest,
            "genotype_scope_id": genotype_scope_id_for_vcf(resolved),
            "samples": samples,
            "sites": sites,
        }
    except (OSError, UnicodeError, ValueError, EOFError, gzip.BadGzipFile):
        return None


def _manifest_artifact_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = manifest.get("artifacts") or []
    if isinstance(artifacts, dict):
        return [value for value in artifacts.values() if isinstance(value, dict)]
    if isinstance(artifacts, list):
        return [value for value in artifacts if isinstance(value, dict)]
    return []


def _manifest_artifact_for_role(manifest: dict[str, Any], role: str) -> dict[str, Any] | None:
    artifacts = manifest.get("artifacts") or {}
    if isinstance(artifacts, dict):
        direct = artifacts.get(role)
        if isinstance(direct, dict):
            return direct
    return next(
        (
            artifact
            for artifact in _manifest_artifact_entries(manifest)
            if str(artifact.get("role") or artifact.get("artifact") or "") == role
        ),
        None,
    )


def _manifest_artifact_path(artifact: dict[str, Any], workspace: Path, input_dir: Path) -> Path | None:
    for key in ("relpath_or_hint", "path", "full_path", "file"):
        text = str(artifact.get(key) or "").strip()
        if not text:
            continue
        raw = Path(text).expanduser()
        if raw.is_absolute():
            return raw
        candidates = (workspace / raw, workspace / "final" / raw, input_dir / raw)
        return next((candidate for candidate in candidates if candidate.exists()), candidates[0])
    return None


def _gwas_resolution(candidates: list[dict[str, Any]], selected: str = "") -> dict[str, Any]:
    by_digest: dict[str, dict[str, Any]] = {}
    for candidate in sorted(candidates, key=lambda item: item["path"]):
        by_digest.setdefault(candidate["sha256"], candidate)
    unique = list(by_digest.values())

    selected_path = ""
    if selected:
        try:
            selected_path = str(Path(selected).expanduser().resolve(strict=True))
        except OSError:
            selected_path = ""
        match = next((item for item in unique if item["path"] == selected_path), None)
        if match is None:
            return {
                "valid": False,
                "needs_confirmation": bool(len(unique) > 1),
                "issues": [{
                    "severity": "error",
                    "category": "input",
                    "code": "invalid_external_vcf_selection",
                    "message": "Selected VCF is not one of the current authoritative candidates",
                }],
                "decisions": {"external_vcf": {"resolved": False, "path": selected}},
                "candidates": unique,
            }
        unique = [match]

    if len(unique) == 1:
        selected_candidate = unique[0]
        return {
            "valid": True,
            "needs_confirmation": False,
            "issues": [],
            "decisions": {"external_vcf": {"resolved": True, **selected_candidate}},
            "candidates": unique,
        }
    if len(unique) > 1:
        return {
            "valid": False,
            "needs_confirmation": True,
            "issues": [{
                "severity": "warning",
                "category": "parameter_confirmation",
                "code": "ambiguous_external_vcf",
                "message": "Multiple valid VCF inputs were found; select one before execution",
            }],
            "decisions": {"external_vcf": {"resolved": False, "path": ""}},
            "candidates": unique,
        }
    return {
        "valid": False,
        "needs_confirmation": False,
        "issues": [{
            "severity": "error",
            "category": "input",
            "code": "missing_external_vcf",
            "message": "No valid VCF was found in the current job input or final directory",
        }],
        "decisions": {"external_vcf": {"resolved": False, "path": ""}},
        "candidates": [],
    }


def resolve_gwas_fmt_input(
    *,
    job_id: str,
    prefix: str,
    params: dict[str, Any],
    project_root: Path,
) -> dict[str, Any]:
    """Resolve one authoritative GWAS VCF without filename or LLM guessing."""
    del prefix  # Reserved for future registered lineage identity; never infer it from names.
    project_root = Path(project_root)
    input_dir = project_root / "input" / job_id
    workspace = project_root / "workspace" / job_id
    final_dir = workspace / "final"
    selected = _gwas_vcf(params)

    manifests_by_stage: dict[str, list[tuple[Path, dict[str, Any]]]] = {
        stage: [] for stage, _role in _WGS_GWAS_INPUTS
    }
    saw_wgs_manifest = False
    for manifest_path in sorted(final_dir.glob("*.manifest.json")) if final_dir.is_dir() else []:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        stage = str(manifest.get("stage") or "")
        if stage not in manifests_by_stage:
            continue
        saw_wgs_manifest = True
        if str(manifest.get("status") or "") == "success":
            manifests_by_stage[stage].append((manifest_path, manifest))

    for stage, artifact_role in _WGS_GWAS_INPUTS:
        stage_manifests = manifests_by_stage[stage]
        if not stage_manifests:
            continue
        manifest_candidates: list[dict[str, Any]] = []
        manifest_errors: list[str] = []
        for manifest_path, manifest in stage_manifests:
            artifact = _manifest_artifact_for_role(manifest, artifact_role)
            if artifact is None:
                manifest_errors.append(
                    f"Manifest is missing authoritative artifact {artifact_role}: {manifest_path}"
                )
                continue
            artifact_path = _manifest_artifact_path(artifact, workspace, input_dir)
            candidate = (
                _vcf_candidate(artifact_path, "manifest", source_manifest=str(manifest_path))
                if artifact_path is not None
                else None
            )
            expected_sha = str(artifact.get("sha256") or "").strip().lower()
            if candidate is None or len(expected_sha) != 64 or candidate["sha256"] != expected_sha:
                manifest_errors.append(f"Manifest VCF evidence is missing or invalid: {manifest_path}")
                continue
            manifest_candidates.append(candidate)
        if manifest_errors:
            return {
                "valid": False,
                "needs_confirmation": False,
                "issues": [{
                    "severity": "error",
                    "category": "input",
                    "code": "invalid_manifest_input",
                    "message": manifest_errors[0],
                }],
                "decisions": {"external_vcf": {"resolved": False, "path": ""}},
                "candidates": [],
            }
        input_candidates: list[dict[str, Any]] = []
        if input_dir.is_dir():
            for path in sorted(input_dir.rglob("*")):
                if not path.is_file() or not _is_gwas_input_vcf(path):
                    continue
                candidate = _vcf_candidate(path, "input", root=input_dir)
                if candidate is not None:
                    input_candidates.append(candidate)
        manifest_digests = {candidate["sha256"] for candidate in manifest_candidates}
        input_candidates = [
            candidate for candidate in input_candidates if candidate["sha256"] not in manifest_digests
        ]
        return _gwas_resolution(manifest_candidates + input_candidates, selected)

    if saw_wgs_manifest:
        scanned_input: list[dict[str, Any]] = []
        if input_dir.is_dir():
            for path in sorted(input_dir.rglob("*")):
                if not path.is_file() or not _is_gwas_input_vcf(path):
                    continue
                candidate = _vcf_candidate(path, "input", root=input_dir)
                if candidate is not None:
                    scanned_input.append(candidate)
        return _gwas_resolution(scanned_input, selected)

    scanned: list[dict[str, Any]] = []
    for source, root in (("input", input_dir), ("final", final_dir)):
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or not _is_gwas_input_vcf(path):
                continue
            candidate = _vcf_candidate(path, source, root=root)
            if candidate is not None:
                scanned.append(candidate)
    return _gwas_resolution(scanned, selected)


def semantic_external_vcf_preflight(params: dict[str, Any]) -> dict[str, Any]:
    """Validate an explicitly supplied VCF without sending it to artifact discovery."""
    raw = _gwas_vcf(params)
    path = Path(raw).expanduser() if raw else None
    if path is None or not path.is_absolute():
        return {
            "valid": False,
            "issues": [{
                "severity": "error",
                "category": "input",
                "code": "missing_external_vcf",
                "message": "gwas_fmt requires params.gwasVcf as an absolute path",
            }],
            "decisions": {"external_vcf": {"resolved": False, "path": raw}},
        }
    resolved = path.resolve()
    issues: list[dict[str, str]] = []
    if not resolved.is_file():
        issues.append({
                "severity": "error",
                "category": "input",
                "code": "missing_external_vcf",
                "message": f"External VCF does not exist: {resolved}",
        })
    samples = 0
    sites = 0
    if resolved.is_file():
        try:
            samples, sites = _vcf_shape(resolved)
        except Exception as exc:
            issues.append({
                "severity": "error", "category": "input", "code": "invalid_external_vcf",
                "message": f"External VCF is not a readable semantic VCF: {exc}",
            })
    tassel_text = str(params.get("tasselPipeline") or params.get("tassel_pipeline") or "run_pipeline.pl").strip()
    tassel_lookup = tassel_text
    if tassel_text == "run_pipeline.pl":
        tassel_lookup = str(os.environ.get("TASSEL_PIPELINE") or tassel_text).strip()
    tassel_candidate = Path(tassel_lookup).expanduser()
    tassel_resolved = (
        str(tassel_candidate.resolve()) if (tassel_candidate.is_absolute() or "/" in tassel_lookup)
        else str(shutil.which(tassel_lookup) or "")
    )
    tassel_executable = bool(tassel_resolved and Path(tassel_resolved).is_file() and os.access(tassel_resolved, os.X_OK))
    if not tassel_executable:
        issues.append({
            "severity": "error", "category": "tool", "code": "missing_tassel_executable",
            "message": f"TASSEL pipeline executable was not found or is not executable: {tassel_text}",
        })
    return {
        "valid": not issues,
        "needs_confirmation": False,
        "issues": issues,
        "decisions": {
            "external_vcf": {
                "resolved": resolved.is_file() and not any(item["code"] in {"missing_external_vcf", "invalid_external_vcf"} for item in issues),
                "path": str(resolved),
                "reason": "explicit params.gwasVcf",
                "samples": samples,
                "sites": sites,
            },
            "tassel": {"requested": tassel_text, "path": tassel_resolved, "executable": tassel_executable},
        },
    }


def trusted_genotype_scope_id(stage: str, params: dict[str, Any]) -> str:
    """Derive genotype identity from the selected VCF; never trust a client hash."""
    if str(stage or "") != "gwas_fmt":
        return str(params.get("genotypeScopeId") or params.get("genotype_scope_id") or "").strip()
    preflight = semantic_external_vcf_preflight(params)
    decision = preflight["decisions"]["external_vcf"]
    if not decision.get("resolved"):
        return ""
    return genotype_scope_id_for_vcf(Path(str(decision["path"])))


def _gwas_fmt_metrics_proven(manifest: dict[str, Any]) -> bool:
    metrics = manifest.get("metrics") if isinstance(manifest.get("metrics"), dict) else {}
    if metrics.get("sample_conservation") is not True or metrics.get("variant_conservation") is not True:
        return False
    params = manifest.get("parameters") if isinstance(manifest.get("parameters"), dict) else {}
    lossy_mask = params.get("unsupported_marker_policy") == "mask" and metrics.get("lossy_conversion") is True
    if lossy_mask:
        try:
            return (
                metrics.get("strict_genotype_conservation") is True
                and int(metrics.get("strict_marker_count") or 0) > 0
                and int(metrics.get("genotypes_compared") or 0) > 0
                and int(metrics.get("unsupported_marker_count") or 0) > 0
            )
        except (TypeError, ValueError):
            return False
    return metrics.get("genotype_conservation") is True


def _gwas_fmt_tassel_version_proven(manifest: dict[str, Any]) -> bool:
    software = manifest.get("software") if isinstance(manifest.get("software"), dict) else {}
    tassel = software.get("tassel") if isinstance(software.get("tassel"), dict) else {}
    return str(tassel.get("version") or "unknown").strip().lower() != "unknown"


def _is_core_manifest_v1_shape(manifest: dict[str, Any]) -> bool:
    string_fields = (
        "manifest_version", "job_id", "stage", "run_id", "prefix", "timestamp",
        "status", "start_time", "end_time", "workspace_root", "workspace", "input_dir",
    )
    if any(not isinstance(manifest.get(field), str) for field in string_fields):
        return False
    elapsed = manifest.get("elapsed_sec")
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or elapsed < 0:
        return False
    cmd = manifest.get("cmd")
    if not isinstance(cmd, list) or any(not isinstance(item, str) for item in cmd):
        return False
    if manifest.get("manifest_version") != "1.0" or manifest.get("status") not in {"success", "failed"}:
        return False
    return (
        isinstance(manifest.get("artifacts"), dict)
        and isinstance(manifest.get("parameters"), dict)
        and isinstance(manifest.get("metrics"), dict)
    )


def gwas_fmt_result_artifacts(
    manifest: dict[str, Any], expected_scope: str = "", workspace_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Return only canonical successful result roles from the requested genotype scope."""
    if not _is_core_manifest_v1_shape(manifest):
        return []
    if str(manifest.get("stage") or "") != "gwas_fmt" or str(manifest.get("status") or "") != "success":
        return []
    manifest_scope = str(
        manifest.get("genotype_scope_id")
        or (manifest.get("parameters") or {}).get("genotype_scope_id")
        or (manifest.get("stage_context") or {}).get("genotype_scope_id")
        or ""
    ).strip()
    if not manifest_scope:
        return []
    if expected_scope and manifest_scope != expected_scope:
        return []
    if workspace_dir is not None and (
        not _gwas_fmt_metrics_proven(manifest) or not _gwas_fmt_tassel_version_proven(manifest)
    ):
        return []
    artifacts = manifest.get("artifacts")
    if (
        not isinstance(artifacts, dict)
        or not artifacts
        or any(not isinstance(value, dict) for value in artifacts.values())
    ):
        return []
    canonical_roles = {"sorted_vcf", "tassel_hapmap", "environment", "report"}
    if set(artifacts) != canonical_roles:
        return []
    records = []
    for key, value in artifacts.items():
        if value.get("role") != key or value.get("artifact") != key:
            return []
        if any(not isinstance(value.get(field), str) or not value.get(field) for field in ("path", "full_path", "type", "sha256")):
            return []
        declared_size = value.get("size")
        declared_size_bytes = value.get("size_bytes")
        if (
            isinstance(declared_size, bool)
            or not isinstance(declared_size, int)
            or declared_size < 0
            or isinstance(declared_size_bytes, bool)
            or not isinstance(declared_size_bytes, int)
            or declared_size_bytes != declared_size
        ):
            return []
        declared_sha = str(value["sha256"]).strip().lower()
        if len(declared_sha) != 64 or any(c not in "0123456789abcdef" for c in declared_sha):
            return []
        records.append(dict(value))
    if workspace_dir is not None:
        base = Path(workspace_dir).resolve()
        if (
            Path(manifest["workspace_root"]).resolve() != base
            or Path(manifest["workspace"]).resolve() != (base / "final").resolve()
        ):
            return []
        for record in records:
            raw_path = Path(str(record["path"]))
            if raw_path.is_absolute():
                return []
            try:
                artifact_path = (raw_path if raw_path.is_absolute() else base / raw_path).resolve(strict=True)
                artifact_path.relative_to(base)
            except (OSError, ValueError):
                return []
            try:
                if Path(str(record["full_path"])).resolve(strict=True) != artifact_path:
                    return []
            except OSError:
                return []
            declared_sha = str(record["sha256"]).strip().lower()
            if not artifact_path.is_file():
                return []
            if sha256_file(artifact_path) != declared_sha:
                return []
            if record["size"] != artifact_path.stat().st_size:
                return []
    return records


def canonicalize_gwas_task_results(
    stage_files: dict[str, dict[str, list]], all_images: list[str], all_files: list[dict],
    canonical_files: list[dict],
) -> tuple[list[str], list[dict]]:
    """Replace filename-discovered GWAS task output with manifest-authoritative files."""
    previous = stage_files.setdefault("gwas_fmt", {"images": [], "files": []})
    stale_images = set(previous.get("images") or [])
    stale_file_paths = {item.get("path") for item in previous.get("files") or []}
    previous["images"] = []
    previous["files"] = list(canonical_files)
    filtered_images = [item for item in all_images if item not in stale_images]
    filtered_files = [item for item in all_files if item.get("path") not in stale_file_paths]
    filtered_files.extend(canonical_files)
    return filtered_images, filtered_files
