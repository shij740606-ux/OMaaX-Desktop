"""Resolve WGS inputs only from one immutable authoritative Manifest."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Collection, Mapping, Sequence


class WgsUpstreamManifestError(ValueError):
    """Raised when a WGS producer Manifest cannot prove its artifacts."""


@dataclass(frozen=True)
class ResolvedWgsArtifact:
    key: str
    role: str
    path: Path
    sha256: str
    size: int
    sample_id: str
    component: str


_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BWA_MEM2_COMPONENTS = {".0123", ".amb", ".ann", ".bwt.2bit.64", ".pac"}
_SAMPLE_ROLES = {
    "clean_reads_r1", "clean_reads_r2", "sorted_bam", "sorted_bam_index", "align_log",
    "markdup_bam", "markdup_bam_index", "markdup_metrics", "markdup_log",
    "bam_flagstat", "bam_depth_summary",
    "gvcf", "gvcf_index", "haplotype_log",
}

_PRODUCER_ROLES: dict[str, tuple[str, ...]] = {
    "wgs_qc": ("clean_reads_r1", "clean_reads_r2"),
    "wgs_index": (
        "reference_fasta",
        "reference_fai",
        "reference_dict",
        "bwa_mem2_index_component",
    ),
    "wgs_align": ("sorted_bam", "sorted_bam_index", "align_log"),
    "wgs_markdup": ("markdup_bam", "markdup_bam_index"),
    "wgs_bam_qc": (
        "bam_flagstat",
        "bam_depth_summary",
        "bam_qc_summary",
        "bam_qc_report",
    ),
    "wgs_haplotype_calling": ("gvcf", "gvcf_index"),
    "wgs_gvcf_combine": ("combined_gvcf", "combined_gvcf_index"),
    "wgs_joint_genotyping": ("joint_vcf", "joint_vcf_index"),
    "wgs_variant_select": (
        "selected_snp_vcf",
        "selected_snp_vcf_index",
        "selected_indel_vcf",
        "selected_indel_vcf_index",
    ),
    "wgs_variant_filter": ("pass_snp_vcf", "pass_snp_vcf_index"),
    "wgs_impute": ("imputed_snp_vcf", "imputed_snp_vcf_index"),
}

# producer, Manifest camel/snake keys, upstream-analysis camel/snake keys
_MATERIALIZATION_SPECS: dict[
    str, tuple[tuple[str, str, str, str, str], ...]
] = {
    "wgs_align": (
        ("wgs_qc", "wgsQcManifest", "wgs_qc_manifest", "", ""),
        ("wgs_index", "wgsIndexManifest", "wgs_index_manifest", "", ""),
    ),
    "wgs_markdup": (
        (
            "wgs_align",
            "wgsAlignManifest",
            "wgs_align_manifest",
            "wgsAnalysisId",
            "wgs_analysis_id",
        ),
    ),
    "wgs_bam_qc": (
        (
            "wgs_markdup",
            "wgsMarkdupManifest",
            "wgs_markdup_manifest",
            "wgsAnalysisId",
            "wgs_analysis_id",
        ),
        (
            "wgs_index",
            "wgsIndexManifest",
            "wgs_index_manifest",
            "wgsIndexAnalysisId",
            "wgs_index_analysis_id",
        ),
    ),
    "wgs_haplotype_calling": (
        (
            "wgs_markdup",
            "wgsMarkdupManifest",
            "wgs_markdup_manifest",
            "wgsMarkdupAnalysisId",
            "wgs_markdup_analysis_id",
        ),
        (
            "wgs_bam_qc",
            "wgsBamQcManifest",
            "wgs_bam_qc_manifest",
            "wgsBamQcAnalysisId",
            "wgs_bam_qc_analysis_id",
        ),
        (
            "wgs_index",
            "wgsIndexManifest",
            "wgs_index_manifest",
            "wgsIndexAnalysisId",
            "wgs_index_analysis_id",
        ),
    ),
    "wgs_gvcf_combine": (
        (
            "wgs_haplotype_calling",
            "wgsHaplotypeManifest",
            "wgs_haplotype_manifest",
            "wgsHaplotypeAnalysisId",
            "wgs_haplotype_analysis_id",
        ),
        (
            "wgs_index",
            "wgsIndexManifest",
            "wgs_index_manifest",
            "wgsIndexAnalysisId",
            "wgs_index_analysis_id",
        ),
    ),
    "wgs_joint_genotyping": (
        (
            "wgs_gvcf_combine",
            "wgsCombineManifest",
            "wgs_combine_manifest",
            "wgsCombineAnalysisId",
            "wgs_combine_analysis_id",
        ),
        (
            "wgs_index",
            "wgsIndexManifest",
            "wgs_index_manifest",
            "wgsIndexAnalysisId",
            "wgs_index_analysis_id",
        ),
    ),
    "wgs_variant_select": (
        (
            "wgs_joint_genotyping",
            "wgsJointManifest",
            "wgs_joint_manifest",
            "wgsJointAnalysisId",
            "wgs_joint_analysis_id",
        ),
    ),
    "wgs_variant_filter": (
        (
            "wgs_variant_select",
            "wgsSelectManifest",
            "wgs_select_manifest",
            "wgsSelectAnalysisId",
            "wgs_select_analysis_id",
        ),
    ),
    "wgs_impute": (
        (
            "wgs_variant_filter",
            "wgsFilterManifest",
            "wgs_filter_manifest",
            "wgsFilterAnalysisId",
            "wgs_filter_analysis_id",
        ),
    ),
    "wgs_maf_missing_filter": (
        (
            "wgs_impute",
            "wgsImputeManifest",
            "wgs_impute_manifest",
            "wgsImputeAnalysisId",
            "wgs_impute_analysis_id",
        ),
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(path: Path) -> dict:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise WgsUpstreamManifestError(f"duplicate Manifest key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except WgsUpstreamManifestError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WgsUpstreamManifestError("upstream Manifest is unreadable") from exc
    if not isinstance(value, dict):
        raise WgsUpstreamManifestError("upstream Manifest root must be an object")
    return value


def _has_symlink_component(path: Path, boundary: Path) -> bool:
    try:
        relative = path.relative_to(boundary)
    except ValueError:
        return True
    current = boundary
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _workspace_relative_identity(value: str | Path) -> tuple[str, ...]:
    path = Path(str(value or ""))
    if not path.parts or ".." in path.parts:
        return ()
    anchors = [index for index, part in enumerate(path.parts) if part == "workspace"]
    if not anchors:
        return ()
    return tuple(path.parts[anchors[-1] :])


def wgs_workspace_identity_matches(
    declared: str | Path, expected: str | Path
) -> bool:
    """Compare stable workspace-relative identity across mount aliases."""
    declared_path = Path(str(declared or ""))
    expected_path = Path(str(expected or ""))
    if (
        declared_path.parts
        and expected_path.parts
        and ".." not in declared_path.parts
        and ".." not in expected_path.parts
        and declared_path == expected_path
    ):
        return True
    declared_identity = _workspace_relative_identity(declared)
    expected_identity = _workspace_relative_identity(expected)
    return bool(declared_identity and declared_identity == expected_identity)


def validate_wgs_bam_qc_completion(
    manifest: Mapping[str, object], expected_sample_ids: Sequence[str]
) -> None:
    """Validate the fixed Stage05 completion gate without filtering by depth."""
    expected = sorted(str(sample) for sample in expected_sample_ids)
    parameters = manifest.get("parameters")
    metrics = manifest.get("metrics")
    if (
        manifest.get("stage") != "wgs_bam_qc"
        or manifest.get("status") != "success"
        or not isinstance(parameters, Mapping)
        or parameters.get("metric_name") != "coverage_ge_1x"
        or parameters.get("coverage_threshold") != 1
        or isinstance(parameters.get("coverage_threshold"), bool)
        or not isinstance(metrics, Mapping)
        or metrics.get("input_sample_ids") != expected
        or metrics.get("output_sample_ids") != expected
        or not expected
        or len(set(expected)) != len(expected)
    ):
        raise WgsUpstreamManifestError(
            "wgs_bam_qc completion gate contract mismatch"
        )


def resolve_wgs_artifacts(
    manifest_path: str | Path,
    expected_stage: str,
    required_roles: Sequence[str],
    expected_sample_set_id: str = "",
    expected_reference_id: str = "",
    expected_analysis_id: str = "",
    expected_method: str = "",
) -> Mapping[str, tuple[ResolvedWgsArtifact, ...]]:
    """Validate one authority Manifest and return immutable artifacts by role."""
    authority = Path(manifest_path)
    if authority.is_symlink() or not authority.is_file():
        raise WgsUpstreamManifestError("upstream authority Manifest must be a regular non-symlink file")
    authority = authority.resolve()
    manifest = _load_manifest(authority)
    if manifest.get("manifest_version") != "1.0":
        raise WgsUpstreamManifestError("upstream Manifest version must be 1.0")
    if manifest.get("stage") != expected_stage:
        raise WgsUpstreamManifestError("upstream producer stage mismatch")
    if manifest.get("status") != "success":
        raise WgsUpstreamManifestError("upstream Manifest is not successful")

    prefix = str(manifest.get("prefix") or "")
    run_id = str(manifest.get("run_id") or "")
    if not _SAFE_COMPONENT.fullmatch(prefix):
        raise WgsUpstreamManifestError("upstream prefix is empty or unsafe")
    if not _RUN_ID.fullmatch(run_id):
        raise WgsUpstreamManifestError("upstream run_id is empty or unsafe")
    workspace_raw = str(manifest.get("workspace") or "")
    workspace = authority.parent.parent.resolve()
    if not wgs_workspace_identity_matches(workspace_raw, workspace):
        raise WgsUpstreamManifestError("upstream workspace/prefix authority mismatch")
    if expected_method:
        if (
            not _SAFE_COMPONENT.fullmatch(expected_method)
            or manifest.get("method") != expected_method
        ):
            raise WgsUpstreamManifestError("upstream method mismatch")
        expected_authority = (
            workspace
            / "final"
            / f"{prefix}.{expected_stage}.{expected_method}.manifest.json"
        )
    else:
        expected_authority = (
            workspace / "final" / f"{prefix}.{expected_stage}.manifest.json"
        )
    if authority != expected_authority or _has_symlink_component(authority, workspace):
        raise WgsUpstreamManifestError("upstream workspace/prefix authority mismatch")
    if expected_sample_set_id and manifest.get("wgs_sample_set_id") != expected_sample_set_id:
        raise WgsUpstreamManifestError("upstream sample identity mismatch")
    if expected_reference_id and manifest.get("wgs_reference_id") != expected_reference_id:
        raise WgsUpstreamManifestError("upstream reference identity mismatch")
    if expected_analysis_id and manifest.get("wgs_analysis_id") != expected_analysis_id:
        raise WgsUpstreamManifestError("upstream analysis identity mismatch")

    roles = tuple(required_roles)
    if not roles or len(set(roles)) != len(roles) or any(not isinstance(role, str) or not role for role in roles):
        raise WgsUpstreamManifestError("required roles must be unique non-empty strings")
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, dict):
        raise WgsUpstreamManifestError("upstream artifacts must be an object")
    bundle = workspace / "final" / f".{expected_stage}_runs" / prefix
    if expected_method:
        bundle = bundle / expected_method
    bundle = bundle / run_id
    if _has_symlink_component(bundle, workspace):
        raise WgsUpstreamManifestError("upstream immutable bundle path contains a symlink")
    bundle = bundle.resolve()

    grouped: dict[str, list[ResolvedWgsArtifact]] = {role: [] for role in roles}
    seen: set[tuple[str, str, str]] = set()
    for key, raw in raw_artifacts.items():
        if not isinstance(raw, dict):
            raise WgsUpstreamManifestError(f"artifact {key} must be an object")
        role = str(raw.get("role") or "")
        if role not in grouped:
            continue
        if raw.get("artifact") != role:
            raise WgsUpstreamManifestError(f"artifact {key} role identity mismatch")
        sample_id = str(raw.get("sample_id") or "")
        component = str(raw.get("component") or "")
        if sample_id and not _SAFE_COMPONENT.fullmatch(sample_id):
            raise WgsUpstreamManifestError(f"artifact {key} sample_id is unsafe")
        if role in _SAMPLE_ROLES and not sample_id:
            raise WgsUpstreamManifestError(f"artifact {key} is missing sample_id")
        if role == "bwa_mem2_index_component" and component not in _BWA_MEM2_COMPONENTS:
            raise WgsUpstreamManifestError(f"artifact {key} component is missing or invalid")
        identity = (role, sample_id, component)
        if identity in seen:
            raise WgsUpstreamManifestError(f"duplicate artifact identity for role {role}")
        seen.add(identity)

        relative = Path(str(raw.get("path") or ""))
        full = Path(str(raw.get("full_path") or ""))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts or not full.is_absolute() or ".." in full.parts:
            raise WgsUpstreamManifestError(f"artifact {key} path is invalid or escapes workspace")
        candidate = workspace / relative
        if _has_symlink_component(candidate, workspace):
            raise WgsUpstreamManifestError(f"artifact {key} path contains a symlink")
        resolved = candidate.resolve()
        if not wgs_workspace_identity_matches(full, resolved):
            raise WgsUpstreamManifestError(f"artifact {key} full_path mismatch")
        try:
            resolved.relative_to(bundle)
        except ValueError as exc:
            raise WgsUpstreamManifestError(f"artifact {key} path is outside immutable run bundle") from exc
        if resolved.is_symlink() or not resolved.is_file():
            raise WgsUpstreamManifestError(f"artifact {key} file is missing or not regular")
        size = raw.get("size")
        actual_size = resolved.stat().st_size
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0 or size != actual_size:
            raise WgsUpstreamManifestError(f"artifact {key} size mismatch")
        if raw.get("size_bytes") != actual_size:
            raise WgsUpstreamManifestError(f"artifact {key} size_bytes mismatch")
        declared_sha = str(raw.get("sha256") or "").lower()
        if not _SHA256.fullmatch(declared_sha) or _sha256(resolved) != declared_sha:
            raise WgsUpstreamManifestError(f"artifact {key} sha256 mismatch")
        grouped[role].append(ResolvedWgsArtifact(str(key), role, resolved, declared_sha, size, sample_id, component))

    missing = [role for role, values in grouped.items() if not values]
    if missing:
        raise WgsUpstreamManifestError(f"missing required role(s): {', '.join(missing)}")
    sample_roles = [role for role in roles if role in _SAMPLE_ROLES]
    if sample_roles:
        sample_sets = [{item.sample_id for item in grouped[role]} for role in sample_roles]
        if any(values != sample_sets[0] for values in sample_sets[1:]):
            raise WgsUpstreamManifestError("required sample roles have mismatched sample sets")
    if "bwa_mem2_index_component" in grouped:
        components = {item.component for item in grouped["bwa_mem2_index_component"]}
        if components != _BWA_MEM2_COMPONENTS:
            raise WgsUpstreamManifestError("missing required BWA-MEM2 index component")
        references = grouped.get("reference_fasta", [])
        if references:
            if len(references) != 1:
                raise WgsUpstreamManifestError("reference_fasta role must contain exactly one artifact")
            reference_path = references[0].path
            for item in grouped["bwa_mem2_index_component"]:
                if item.path != Path(f"{reference_path}{item.component}"):
                    raise WgsUpstreamManifestError(
                        "BWA-MEM2 index component does not match reference basename"
                    )
    return MappingProxyType({role: tuple(sorted(values, key=lambda item: (item.sample_id, item.component, item.key))) for role, values in grouped.items()})


def _paired_param(
    params: Mapping[str, Any], camel_key: str, snake_key: str
) -> str:
    camel = str(params.get(camel_key) or "").strip()
    snake = str(params.get(snake_key) or "").strip()
    if camel and snake and camel != snake:
        raise WgsUpstreamManifestError(
            f"conflicting WGS parameter aliases: {camel_key}/{snake_key}"
        )
    return camel or snake


def _authority_path(
    workspace: Path, prefix: str, producer: str, method: str
) -> Path:
    suffix = f".{method}" if producer in {
        "wgs_variant_select",
        "wgs_variant_filter",
    } else ""
    return (
        workspace.resolve()
        / "final"
        / f"{prefix}.{producer}{suffix}.manifest.json"
    )


def resolve_unique_wgs_prefix(
    *,
    stage: str,
    job_id: str,
    params: Mapping[str, Any] | None,
    workspace: str | Path,
) -> str:
    """Return the only prefix backed by all required successful authorities."""
    specs = _MATERIALIZATION_SPECS.get(stage)
    if specs is None:
        raise WgsUpstreamManifestError(f"unsupported WGS stage: {stage}")

    values = dict(params or {})
    method = str(
        values.get("wgsMethod")
        or values.get("wgs_method")
        or values.get("method")
        or "gatk"
    ).strip().lower()
    if stage in {"wgs_variant_select", "wgs_variant_filter", "wgs_impute"}:
        if method not in {"gatk", "bcftools"}:
            raise WgsUpstreamManifestError(
                f"{stage} method must be gatk or bcftools"
            )
    else:
        method = ""

    final_dir = Path(workspace).resolve() / "final"
    prefix_sets: list[set[str]] = []
    for producer, *_ in specs:
        producer_method = (
            method
            if producer in {"wgs_variant_select", "wgs_variant_filter"}
            else ""
        )
        suffix = (
            f".{producer}.{producer_method}.manifest.json"
            if producer_method
            else f".{producer}.manifest.json"
        )
        prefixes = {
            path.name[: -len(suffix)]
            for path in final_dir.glob(f"*{suffix}")
            if path.name.endswith(suffix) and path.name[: -len(suffix)]
        } if final_dir.is_dir() else set()
        prefix_sets.append(prefixes)

    candidates = set.intersection(*prefix_sets) if prefix_sets else set()
    valid: list[str] = []
    materialize_params = {"wgsMethod": method} if method else {}
    for candidate in sorted(candidates):
        try:
            materialize_wgs_identity_params(
                stage=stage,
                job_id=job_id,
                prefix=candidate,
                params=materialize_params,
                workspace=workspace,
            )
        except WgsUpstreamManifestError:
            continue
        valid.append(candidate)

    if len(valid) == 1:
        return valid[0]
    if len(valid) > 1:
        raise WgsUpstreamManifestError(
            "multiple complete WGS authority prefixes found: "
            + ", ".join(valid)
        )
    raise WgsUpstreamManifestError(
        f"no complete WGS authority prefix found for {stage}"
    )


def materialize_completed_wgs_upstream_params(
    *,
    stage: str,
    job_id: str,
    prefix: str,
    params: Mapping[str, Any] | None,
    completed_stages: Collection[str],
    workspace: str | Path,
) -> dict[str, Any]:
    """Materialize canonical direct authorities completed before this plan."""
    specs = _MATERIALIZATION_SPECS.get(stage)
    if specs is None:
        raise WgsUpstreamManifestError(f"unsupported WGS stage: {stage}")
    completed = set(completed_stages)
    selected = [spec for spec in specs if spec[0] in completed]
    if not selected:
        return {}

    values = dict(params or {})
    method = str(
        values.get("wgsMethod")
        or values.get("wgs_method")
        or values.get("method")
        or "gatk"
    ).strip().lower()
    if stage in {"wgs_variant_select", "wgs_variant_filter", "wgs_impute"}:
        if method not in {"gatk", "bcftools"}:
            raise WgsUpstreamManifestError(
                f"{stage} method must be gatk or bcftools"
            )
    else:
        method = ""

    workspace_path = Path(workspace).resolve()
    headers: dict[str, dict[str, Any]] = {}
    paths: dict[str, Path] = {}
    analysis_ids: dict[str, str] = {}
    for producer, *_ in selected:
        producer_method = (
            method
            if producer in {"wgs_variant_select", "wgs_variant_filter"}
            else ""
        )
        path = _authority_path(
            workspace_path, prefix, producer, producer_method
        )
        try:
            header = _load_manifest(path)
        except WgsUpstreamManifestError as exc:
            label = f"{producer}.{producer_method}" if producer_method else producer
            raise WgsUpstreamManifestError(
                f"{label} authority is invalid: {exc}"
            ) from exc
        if (
            header.get("job_id") != job_id
            or header.get("prefix") != prefix
            or header.get("stage") != producer
            or header.get("status") != "success"
        ):
            raise WgsUpstreamManifestError(
                f"{producer} authority identity mismatch"
            )
        analysis_id = str(header.get("wgs_analysis_id") or "")
        if not analysis_id:
            raise WgsUpstreamManifestError(
                f"{producer} authority is missing wgs_analysis_id"
            )
        resolve_wgs_artifacts(
            path,
            producer,
            _PRODUCER_ROLES[producer],
            expected_sample_set_id=str(header.get("wgs_sample_set_id") or ""),
            expected_reference_id=str(header.get("wgs_reference_id") or ""),
            expected_analysis_id=analysis_id,
            expected_method=producer_method,
        )
        headers[producer] = header
        paths[producer] = path
        analysis_ids[producer] = analysis_id

    sample_ids = {
        str(header.get("wgs_sample_set_id"))
        for header in headers.values()
        if header.get("wgs_sample_set_id")
    }
    reference_ids = {
        str(header.get("wgs_reference_id"))
        for header in headers.values()
        if header.get("wgs_reference_id")
    }
    if len(sample_ids) > 1 or len(reference_ids) > 1:
        raise WgsUpstreamManifestError(
            "WGS completed upstream sample/reference identities conflict"
        )
    for producer, header in headers.items():
        lineage = header.get("upstream_wgs_analysis_ids")
        if not isinstance(lineage, Mapping):
            continue
        for related, related_analysis in analysis_ids.items():
            if related in lineage and lineage[related] != related_analysis:
                raise WgsUpstreamManifestError(
                    f"{producer} upstream WGS analysis lineage mismatch"
                )

    result: dict[str, Any] = {}
    if sample_ids:
        sample_id = next(iter(sample_ids))
        result.update({
            "wgsSampleSetId": sample_id,
            "wgs_sample_set_id": sample_id,
        })
    if reference_ids:
        reference_id = next(iter(reference_ids))
        result.update({
            "wgsReferenceId": reference_id,
            "wgs_reference_id": reference_id,
        })
    for (
        producer,
        manifest_camel,
        manifest_snake,
        analysis_camel,
        analysis_snake,
    ) in selected:
        result[manifest_camel] = str(paths[producer])
        result[manifest_snake] = str(paths[producer])
        if analysis_camel:
            result[analysis_camel] = analysis_ids[producer]
            result[analysis_snake] = analysis_ids[producer]
    return result


def materialize_wgs_identity_params(
    *,
    stage: str,
    job_id: str,
    prefix: str,
    params: Mapping[str, Any] | None,
    workspace: str | Path,
    current_stage: bool = False,
) -> dict[str, Any]:
    """Materialize one WGS consumer from exact upstream authorities."""
    values = dict(params or {})
    workspace_path = Path(workspace).resolve()
    requested_method = str(
        values.get("wgsMethod")
        or values.get("wgs_method")
        or values.get("method")
        or "gatk"
    ).strip().lower()
    if current_stage:
        method = (
            requested_method
            if stage in {"wgs_variant_select", "wgs_variant_filter"}
            else ""
        )
        if method and method not in {"gatk", "bcftools"}:
            raise WgsUpstreamManifestError(
                f"{stage} method must be gatk or bcftools"
            )
        path = _authority_path(workspace_path, prefix, stage, method)
        try:
            header = _load_manifest(path)
        except WgsUpstreamManifestError as exc:
            raise WgsUpstreamManifestError(
                f"{stage} current authority is invalid: {exc}"
            ) from exc
        if (
            header.get("job_id") != job_id
            or header.get("prefix") != prefix
            or header.get("stage") != stage
            or header.get("status") != "success"
        ):
            raise WgsUpstreamManifestError(
                f"{stage} current authority identity mismatch"
            )
        result = {}
        for manifest_key, camel_key in (
            ("wgs_sample_set_id", "wgsSampleSetId"),
            ("wgs_reference_id", "wgsReferenceId"),
            ("wgs_analysis_id", "wgsAnalysisId"),
        ):
            value = str(header.get(manifest_key) or "")
            if value:
                result[manifest_key] = value
                result[camel_key] = value
        if not result.get("wgsAnalysisId"):
            raise WgsUpstreamManifestError(
                f"{stage} current authority is missing wgs_analysis_id"
            )
        if method:
            result["wgsMethod"] = method
            result["wgs_method"] = method
        return result

    if stage in {"wgs_qc", "wgs_index"}:
        return {}
    specs = _MATERIALIZATION_SPECS.get(stage)
    if specs is None:
        raise WgsUpstreamManifestError(f"unsupported WGS stage: {stage}")

    method = str(
        values.get("wgsMethod")
        or values.get("wgs_method")
        or values.get("method")
        or "gatk"
    ).strip().lower()
    if stage in {"wgs_variant_select", "wgs_variant_filter", "wgs_impute"}:
        if method not in {"gatk", "bcftools"}:
            raise WgsUpstreamManifestError(
                f"{stage} method must be gatk or bcftools"
            )
    else:
        method = ""

    required_pairs: list[tuple[str, str]] = [
        ("wgsSampleSetId", "wgs_sample_set_id"),
        ("wgsReferenceId", "wgs_reference_id"),
    ]
    for _, manifest_camel, manifest_snake, analysis_camel, analysis_snake in specs:
        required_pairs.append((manifest_camel, manifest_snake))
        if analysis_camel:
            required_pairs.append((analysis_camel, analysis_snake))
    supplied = [_paired_param(values, *pair) for pair in required_pairs]
    if any(supplied) and not all(supplied):
        missing = [
            camel
            for (camel, _), value in zip(required_pairs, supplied)
            if not value
        ]
        raise WgsUpstreamManifestError(
            "partial WGS identity parameters are not allowed; missing: "
            + ", ".join(missing)
        )
    explicit = bool(supplied and all(supplied))
    headers: dict[str, dict[str, Any]] = {}
    paths: dict[str, Path] = {}
    analysis_ids: dict[str, str] = {}
    for producer, manifest_camel, manifest_snake, _, _ in specs:
        producer_method = (
            method
            if producer in {"wgs_variant_select", "wgs_variant_filter"}
            else ""
        )
        canonical = _authority_path(
            workspace_path, prefix, producer, producer_method
        )
        requested = _paired_param(values, manifest_camel, manifest_snake)
        path = Path(requested) if explicit else canonical
        if not path.is_absolute():
            path = workspace_path / path
        path = path.resolve()
        if path != canonical:
            raise WgsUpstreamManifestError(
                f"{producer} authority must be {canonical}"
            )
        try:
            header = _load_manifest(path)
        except WgsUpstreamManifestError as exc:
            label = f"{producer}.{producer_method}" if producer_method else producer
            raise WgsUpstreamManifestError(
                f"{label} authority is invalid: {exc}"
            ) from exc
        if (
            header.get("job_id") != job_id
            or header.get("prefix") != prefix
            or header.get("stage") != producer
            or header.get("status") != "success"
        ):
            raise WgsUpstreamManifestError(
                f"{producer} authority identity mismatch"
            )
        analysis_id = str(header.get("wgs_analysis_id") or "")
        if not analysis_id:
            raise WgsUpstreamManifestError(
                f"{producer} authority is missing wgs_analysis_id"
            )
        headers[producer] = header
        paths[producer] = path
        analysis_ids[producer] = analysis_id

    sample_ids = {
        str(header.get("wgs_sample_set_id") or "")
        for header in headers.values()
        if header.get("wgs_sample_set_id")
    }
    reference_ids = {
        str(header.get("wgs_reference_id") or "")
        for header in headers.values()
        if header.get("wgs_reference_id")
    }
    if len(sample_ids) != 1 or len(reference_ids) != 1:
        raise WgsUpstreamManifestError(
            "WGS upstream sample/reference identities conflict or are missing"
        )
    sample_id = next(iter(sample_ids))
    reference_id = next(iter(reference_ids))

    for producer, header in headers.items():
        lineage = header.get("upstream_wgs_analysis_ids")
        if isinstance(lineage, Mapping):
            for related, related_analysis in analysis_ids.items():
                if (
                    related in lineage
                    and lineage[related] != related_analysis
                ):
                    raise WgsUpstreamManifestError(
                        f"{producer} upstream WGS analysis lineage mismatch"
                    )

    result: dict[str, Any] = {
        "wgsSampleSetId": sample_id,
        "wgs_sample_set_id": sample_id,
        "wgsReferenceId": reference_id,
        "wgs_reference_id": reference_id,
    }
    for (
        producer,
        manifest_camel,
        manifest_snake,
        analysis_camel,
        analysis_snake,
    ) in specs:
        expected_analysis = analysis_ids[producer]
        if explicit:
            supplied_analysis = (
                _paired_param(values, analysis_camel, analysis_snake)
                if analysis_camel
                else expected_analysis
            )
            if supplied_analysis != expected_analysis:
                raise WgsUpstreamManifestError(
                    f"{producer} upstream analysis identity mismatch"
                )
        resolve_wgs_artifacts(
            paths[producer],
            producer,
            _PRODUCER_ROLES[producer],
            expected_sample_set_id=(
                sample_id if headers[producer].get("wgs_sample_set_id") else ""
            ),
            expected_reference_id=(
                reference_id
                if headers[producer].get("wgs_reference_id")
                else ""
            ),
            expected_analysis_id=expected_analysis,
            expected_method=(
                method
                if producer
                in {"wgs_variant_select", "wgs_variant_filter"}
                else ""
            ),
        )
        result[manifest_camel] = str(paths[producer])
        result[manifest_snake] = str(paths[producer])
        if analysis_camel:
            result[analysis_camel] = expected_analysis
            result[analysis_snake] = expected_analysis

    if stage in {"wgs_variant_select", "wgs_variant_filter"}:
        result["wgsMethod"] = method
        result["wgs_method"] = method
    return result
