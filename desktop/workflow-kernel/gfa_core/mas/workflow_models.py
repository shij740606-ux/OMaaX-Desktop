"""Strict data models shared by the versioned PlantAI workflow layers.

This module intentionally contains no workflow transitions, persistence, file
access, or route handling.  It only defines immutable, fail-closed data shapes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    field_serializer,
    model_validator,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTITY_RE = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"

SpeciesCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9._-]*$"),
]
GeneFamily = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
]
WorkflowIdentity = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, pattern=_IDENTITY_RE),
]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _require_nonblank(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must be nonblank")


def _require_sha256(value: str, field_name: str) -> None:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _validate_projection_identity(
    scope_id: str,
    analysis_route: str,
    member_set_id: str,
    *,
    member_route: str | None = None,
) -> bool:
    triplet = (scope_id, analysis_route, member_set_id)
    if any(triplet) and not all(triplet):
        raise ValueError("a complete family projection triplet is required")
    if not all(triplet):
        return False
    expected_route = member_route or analysis_route
    prefix = f"{scope_id}:{expected_route}:"
    if not member_set_id.startswith(prefix) or not member_set_id.removeprefix(prefix):
        raise ValueError(f"member_set_id prefix must be {prefix} with a nonempty suffix")
    return True


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")


def _freeze_json(value: JsonValue) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _require_job_relative_path(value: str, field_name: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or path.as_posix() != value
    ):
        raise ValueError(f"{field_name} must be a normalized job-relative path")


def _require_absolute_canonical_path(value: str, field_name: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not path.is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/")[1:])
        or path.as_posix() != value
    ):
        raise ValueError(f"{field_name} must use absolute canonical text")
    return path


class _WorkflowModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> "_WorkflowModel":
        if not update:
            return super().model_copy(deep=deep)
        data = self.model_dump(mode="python", round_trip=True)
        data.update(dict(update))
        return type(self).model_validate(data)


class RNARoute(str, Enum):
    WHOLE_GENOME = "whole_genome"
    FAMILY_MEMBER = "family_member"


class RNAForegroundMode(str, Enum):
    GLOBAL = "global"
    FAMILY = "family"


class RNAFactKind(str, Enum):
    EXPERIMENT_INPUT = "rna_experiment_input"
    ALIGNMENT_STRANDNESS = "rna_alignment_strandness"
    DE_DESIGN = "rna_de_design"
    GENOME_RESOURCE = "rna_genome_resource"


class RNALibraryStrandness(str, Enum):
    NON_STRANDED = "non_stranded"
    FORWARD = "forward"
    REVERSE = "reverse"


class RNAStrandnessPolicy(str, Enum):
    ALL_SAMPLES_EXPLICIT = "all_samples_explicit"
    PROJECT_DEFAULT = "project_default"
    MIXED_EXPLICIT_PROJECT_DEFAULT = "mixed_explicit_project_default"


class RNAExperimentDesign(str, Enum):
    TWO_GROUP = "two_group"
    MULTI_GROUP = "multi_group"
    TIME_SERIES = "time_series"


class RNADEComparisonMode(str, Enum):
    CONFIGURED = "configured"
    GROUP_ROLE_CONTROL = "group_role_control"


class RNAReplicateViolationPolicy(str, Enum):
    WARN = "warn"
    ERROR = "error"


class RNADependencyKind(str, Enum):
    QC_INPUT = "rna_qc_input"
    ALIGNMENT_INPUT = "rna_alignment_input"
    QUANTIFICATION_INPUT = "rna_quantification_input"
    SAMPLE_QC_INPUT = "rna_sample_qc_input"
    DE_INPUT = "rna_de_input"
    FAMILY_PROJECTION = "family_projection"


_RNA_STAGE_DEPENDENCY_KIND = {
    "rnaseq_qc": RNADependencyKind.QC_INPUT,
    "rnaseq_align": RNADependencyKind.ALIGNMENT_INPUT,
    "rnaseq_quant": RNADependencyKind.QUANTIFICATION_INPUT,
    "rnaseq_sample_qc": RNADependencyKind.SAMPLE_QC_INPUT,
    "rnaseq_de": RNADependencyKind.DE_INPUT,
    "rnaseq_family_projection": RNADependencyKind.FAMILY_PROJECTION,
}


def _rna_fact_evidence_id(
    *,
    job_id: str,
    species: str,
    rnaseq_scope_id: str,
    fact_kind: RNAFactKind,
    schema_version: str,
    digest: str,
) -> str:
    identity = _digest(
        {
            "job_id": job_id,
            "species": species,
            "rnaseq_scope_id": rnaseq_scope_id,
            "fact_kind": fact_kind.value,
            "schema_version": schema_version,
            "digest": digest,
        }
    )
    return f"rna_fact:{identity}"


class RNAReadIdentity(_WorkflowModel):
    read_identity_version: Literal["rna_read_identity_v1"] = "rna_read_identity_v1"
    sample_id: WorkflowIdentity
    read_end: Literal["r1", "r2"]
    path: str
    cache_key: str
    content_sha256: str

    @model_validator(mode="after")
    def validate_read_identity(self) -> "RNAReadIdentity":
        _require_job_relative_path(self.path, "path")
        _require_sha256(self.cache_key, "cache_key")
        _require_sha256(self.content_sha256, "content_sha256")
        return self


class RNAArtifactIdentity(_WorkflowModel):
    artifact_identity_version: Literal["rna_artifact_identity_v1"] = (
        "rna_artifact_identity_v1"
    )
    path: str
    cache_key: str
    content_sha256: str

    @model_validator(mode="after")
    def validate_artifact_identity(self) -> "RNAArtifactIdentity":
        _require_job_relative_path(self.path, "path")
        _require_sha256(self.cache_key, "cache_key")
        _require_sha256(self.content_sha256, "content_sha256")
        return self


class RNAExperimentInputSample(_WorkflowModel):
    sample_id: WorkflowIdentity
    run: WorkflowIdentity
    r1: RNAReadIdentity
    r2: RNAReadIdentity
    condition: str = Field(min_length=1, max_length=256)
    time: str = Field(max_length=256)
    replicate: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_read_binding(self) -> "RNAExperimentInputSample":
        for expected_end, identity in (("r1", self.r1), ("r2", self.r2)):
            if identity.sample_id != self.sample_id:
                raise ValueError("read identity sample must match experiment sample")
            if identity.read_end != expected_end:
                raise ValueError("read identity mate must match r1/r2 position")
        if self.r1.path == self.r2.path:
            raise ValueError("paired reads must use distinct paths")
        return self


class RNAExperimentInputFact(_WorkflowModel):
    fact_kind: Literal[RNAFactKind.EXPERIMENT_INPUT] = (
        RNAFactKind.EXPERIMENT_INPUT
    )
    schema_version: Literal["rna_experiment_input_v1"] = (
        "rna_experiment_input_v1"
    )
    job_id: str
    species: SpeciesCode
    samples: tuple[RNAExperimentInputSample, ...] = Field(min_length=1)
    rnaseq_scope_id: WorkflowIdentity
    source_attestation_sha256: str
    digest: str
    evidence_id: str

    @field_validator("samples")
    @classmethod
    def sort_samples(
        cls, value: tuple[RNAExperimentInputSample, ...]
    ) -> tuple[RNAExperimentInputSample, ...]:
        return tuple(sorted(value, key=lambda item: item.sample_id))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "fact_kind": self.fact_kind.value,
            "schema_version": self.schema_version,
            "species": self.species,
            "samples": [item.model_dump(mode="json") for item in self.samples],
        }

    @classmethod
    def build(
        cls,
        *,
        job_id: str,
        species: str,
        samples: tuple[RNAExperimentInputSample, ...],
        source_attestation_sha256: str,
    ) -> "RNAExperimentInputFact":
        ordered = tuple(sorted(samples, key=lambda item: item.sample_id))
        canonical = {
            "fact_kind": RNAFactKind.EXPERIMENT_INPUT.value,
            "schema_version": "rna_experiment_input_v1",
            "species": species,
            "samples": [item.model_dump(mode="json") for item in ordered],
        }
        digest = _digest(canonical)
        rnaseq_scope_id = f"{species}.rnaseq.{digest}"
        return cls(
            job_id=job_id,
            species=species,
            samples=ordered,
            rnaseq_scope_id=rnaseq_scope_id,
            source_attestation_sha256=source_attestation_sha256,
            digest=digest,
            evidence_id=_rna_fact_evidence_id(
                job_id=job_id,
                species=species,
                rnaseq_scope_id=rnaseq_scope_id,
                fact_kind=RNAFactKind.EXPERIMENT_INPUT,
                schema_version="rna_experiment_input_v1",
                digest=digest,
            ),
        )

    @model_validator(mode="after")
    def validate_fact(self) -> "RNAExperimentInputFact":
        _require_nonblank(self.job_id, "job_id")
        _require_sha256(
            self.source_attestation_sha256, "source_attestation_sha256"
        )
        _require_sha256(self.digest, "digest")
        sample_ids = [item.sample_id for item in self.samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("experiment input sample IDs must be unique")
        expected_digest = _digest(self.canonical_payload())
        if self.digest != expected_digest:
            raise ValueError("digest does not match canonical experiment input")
        expected_scope = f"{self.species}.rnaseq.{expected_digest}"
        if self.rnaseq_scope_id != expected_scope:
            raise ValueError("rnaseq_scope_id must derive from complete experiment input")
        expected_evidence_id = _rna_fact_evidence_id(
            job_id=self.job_id,
            species=self.species,
            rnaseq_scope_id=self.rnaseq_scope_id,
            fact_kind=RNAFactKind.EXPERIMENT_INPUT,
            schema_version=self.schema_version,
            digest=self.digest,
        )
        if self.evidence_id != expected_evidence_id:
            raise ValueError("evidence_id does not match experiment input fact")
        return self


class RNAStrandnessSample(_WorkflowModel):
    sample_id: WorkflowIdentity
    strandness: RNALibraryStrandness


class RNAAlignmentStrandnessFact(_WorkflowModel):
    fact_kind: Literal[RNAFactKind.ALIGNMENT_STRANDNESS] = (
        RNAFactKind.ALIGNMENT_STRANDNESS
    )
    schema_version: Literal["rna_alignment_strandness_v1"] = (
        "rna_alignment_strandness_v1"
    )
    job_id: str
    species: SpeciesCode
    rnaseq_scope_id: WorkflowIdentity
    samples: tuple[RNAStrandnessSample, ...] = Field(min_length=1)
    policy: RNAStrandnessPolicy
    mixed: StrictBool
    source_attestation_sha256: str
    digest: str
    evidence_id: str

    @field_validator("samples")
    @classmethod
    def sort_samples(
        cls, value: tuple[RNAStrandnessSample, ...]
    ) -> tuple[RNAStrandnessSample, ...]:
        return tuple(sorted(value, key=lambda item: item.sample_id))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "fact_kind": self.fact_kind.value,
            "schema_version": self.schema_version,
            "species": self.species,
            "rnaseq_scope_id": self.rnaseq_scope_id,
            "samples": [item.model_dump(mode="json") for item in self.samples],
            "policy": self.policy.value,
            "mixed": self.mixed,
        }

    @classmethod
    def build(cls, **values: Any) -> "RNAAlignmentStrandnessFact":
        payload = dict(values)
        ordered = tuple(
            sorted(payload.pop("samples"), key=lambda item: item.sample_id)
        )
        canonical = {
            "fact_kind": RNAFactKind.ALIGNMENT_STRANDNESS.value,
            "schema_version": "rna_alignment_strandness_v1",
            "species": payload["species"],
            "rnaseq_scope_id": payload["rnaseq_scope_id"],
            "samples": [item.model_dump(mode="json") for item in ordered],
            "policy": RNAStrandnessPolicy(payload["policy"]).value,
            "mixed": payload["mixed"],
        }
        digest = _digest(canonical)
        return cls(
            **payload,
            samples=ordered,
            digest=digest,
            evidence_id=_rna_fact_evidence_id(
                job_id=payload["job_id"],
                species=payload["species"],
                rnaseq_scope_id=payload["rnaseq_scope_id"],
                fact_kind=RNAFactKind.ALIGNMENT_STRANDNESS,
                schema_version="rna_alignment_strandness_v1",
                digest=digest,
            ),
        )

    @model_validator(mode="after")
    def validate_fact(self) -> "RNAAlignmentStrandnessFact":
        _require_nonblank(self.job_id, "job_id")
        _require_sha256(
            self.source_attestation_sha256, "source_attestation_sha256"
        )
        _require_sha256(self.digest, "digest")
        sample_ids = [item.sample_id for item in self.samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("strandness sample IDs must be unique")
        if self.mixed != (len({item.strandness for item in self.samples}) > 1):
            raise ValueError("mixed must reflect normalized strandness values")
        expected_digest = _digest(self.canonical_payload())
        if self.digest != expected_digest:
            raise ValueError("digest does not match canonical strandness")
        expected_id = _rna_fact_evidence_id(
            job_id=self.job_id,
            species=self.species,
            rnaseq_scope_id=self.rnaseq_scope_id,
            fact_kind=RNAFactKind.ALIGNMENT_STRANDNESS,
            schema_version=self.schema_version,
            digest=self.digest,
        )
        if self.evidence_id != expected_id:
            raise ValueError("evidence_id does not match strandness fact")
        return self


class RNADEGroupAssignment(_WorkflowModel):
    sample_id: WorkflowIdentity
    group: str = Field(min_length=1, max_length=256)


class RNADEContrast(_WorkflowModel):
    treatment: str = Field(min_length=1, max_length=256)
    reference: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_contrast(self) -> "RNADEContrast":
        if self.treatment == self.reference:
            raise ValueError("DE contrast endpoints must differ")
        return self


class RNAReplicatePolicy(_WorkflowModel):
    min_reps_per_group: StrictInt = Field(ge=1)
    allow_no_replicate: StrictBool
    on_violation: RNAReplicateViolationPolicy


class RNADEGroupCount(_WorkflowModel):
    group: str = Field(min_length=1, max_length=256)
    count: StrictInt = Field(ge=1)


class RNADEGroupTime(_WorkflowModel):
    group: str = Field(min_length=1, max_length=256)
    time: str = Field(max_length=256)


class RNADEReplicateViolation(_WorkflowModel):
    group: str = Field(min_length=1, max_length=256)
    count: StrictInt = Field(ge=1)


class RNADEDesignFact(_WorkflowModel):
    fact_kind: Literal[RNAFactKind.DE_DESIGN] = RNAFactKind.DE_DESIGN
    schema_version: Literal["rna_de_design_v1"] = "rna_de_design_v1"
    job_id: str
    species: SpeciesCode
    rnaseq_scope_id: WorkflowIdentity
    strategy: Literal["auto"]
    design: RNAExperimentDesign
    comparison_mode: RNADEComparisonMode
    effective_control_group: str = Field(min_length=1, max_length=256)
    active_samples: tuple[WorkflowIdentity, ...] = Field(min_length=1)
    ignored_samples: tuple[WorkflowIdentity, ...]
    groups: tuple[RNADEGroupAssignment, ...] = Field(min_length=1)
    contrasts: tuple[RNADEContrast, ...] = Field(min_length=1)
    replicate_counts: tuple[RNADEGroupCount, ...] = Field(min_length=1)
    replicate_policy: RNAReplicatePolicy
    replicate_violations: tuple[RNADEReplicateViolation, ...]
    group_times: tuple[RNADEGroupTime, ...]
    time_levels: tuple[str, ...]
    run_lrt: StrictBool
    run_spline_lrt: StrictBool
    spline_df: StrictInt = Field(ge=1)
    source_attestation_sha256: str
    digest: str
    evidence_id: str

    @field_validator("groups")
    @classmethod
    def sort_groups(
        cls, value: tuple[RNADEGroupAssignment, ...]
    ) -> tuple[RNADEGroupAssignment, ...]:
        return tuple(sorted(value, key=lambda item: item.sample_id))

    @field_validator("contrasts")
    @classmethod
    def sort_contrasts(
        cls, value: tuple[RNADEContrast, ...]
    ) -> tuple[RNADEContrast, ...]:
        return tuple(
            sorted(value, key=lambda item: (item.treatment, item.reference))
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "fact_kind": self.fact_kind.value,
            "schema_version": self.schema_version,
            "species": self.species,
            "rnaseq_scope_id": self.rnaseq_scope_id,
            "strategy": self.strategy,
            "design": self.design.value,
            "comparison_mode": self.comparison_mode.value,
            "effective_control_group": self.effective_control_group,
            "active_samples": list(self.active_samples),
            "ignored_samples": list(self.ignored_samples),
            "groups": [item.model_dump(mode="json") for item in self.groups],
            "contrasts": [
                item.model_dump(mode="json") for item in self.contrasts
            ],
            "replicate_counts": [
                item.model_dump(mode="json") for item in self.replicate_counts
            ],
            "replicate_policy": self.replicate_policy.model_dump(mode="json"),
            "replicate_violations": [
                item.model_dump(mode="json") for item in self.replicate_violations
            ],
            "group_times": [
                item.model_dump(mode="json") for item in self.group_times
            ],
            "time_levels": list(self.time_levels),
            "run_lrt": self.run_lrt,
            "run_spline_lrt": self.run_spline_lrt,
            "spline_df": self.spline_df,
        }

    @classmethod
    def build(cls, **values: Any) -> "RNADEDesignFact":
        payload = dict(values)
        groups = tuple(
            sorted(payload.pop("groups"), key=lambda item: item.sample_id)
        )
        contrasts = tuple(
            sorted(
                payload.pop("contrasts"),
                key=lambda item: (item.treatment, item.reference),
            )
        )
        active_samples = tuple(sorted(payload.pop("active_samples")))
        ignored_samples = tuple(sorted(payload.pop("ignored_samples")))
        replicate_counts = tuple(
            sorted(payload.pop("replicate_counts"), key=lambda item: item.group)
        )
        replicate_violations = tuple(
            sorted(payload.pop("replicate_violations"), key=lambda item: item.group)
        )
        group_times = tuple(
            sorted(payload.pop("group_times"), key=lambda item: item.group)
        )
        time_levels = tuple(payload.pop("time_levels"))
        canonical = {
            "fact_kind": RNAFactKind.DE_DESIGN.value,
            "schema_version": "rna_de_design_v1",
            "species": payload["species"],
            "rnaseq_scope_id": payload["rnaseq_scope_id"],
            "strategy": payload["strategy"],
            "design": RNAExperimentDesign(payload["design"]).value,
            "comparison_mode": RNADEComparisonMode(
                payload["comparison_mode"]
            ).value,
            "effective_control_group": payload["effective_control_group"],
            "active_samples": list(active_samples),
            "ignored_samples": list(ignored_samples),
            "groups": [item.model_dump(mode="json") for item in groups],
            "contrasts": [
                item.model_dump(mode="json") for item in contrasts
            ],
            "replicate_counts": [
                item.model_dump(mode="json") for item in replicate_counts
            ],
            "replicate_policy": payload["replicate_policy"].model_dump(
                mode="json"
            ),
            "replicate_violations": [
                item.model_dump(mode="json") for item in replicate_violations
            ],
            "group_times": [
                item.model_dump(mode="json") for item in group_times
            ],
            "time_levels": list(time_levels),
            "run_lrt": payload["run_lrt"],
            "run_spline_lrt": payload["run_spline_lrt"],
            "spline_df": payload["spline_df"],
        }
        digest = _digest(canonical)
        return cls(
            **payload,
            active_samples=active_samples,
            ignored_samples=ignored_samples,
            groups=groups,
            contrasts=contrasts,
            replicate_counts=replicate_counts,
            replicate_violations=replicate_violations,
            group_times=group_times,
            time_levels=time_levels,
            digest=digest,
            evidence_id=_rna_fact_evidence_id(
                job_id=payload["job_id"],
                species=payload["species"],
                rnaseq_scope_id=payload["rnaseq_scope_id"],
                fact_kind=RNAFactKind.DE_DESIGN,
                schema_version="rna_de_design_v1",
                digest=digest,
            ),
        )

    @model_validator(mode="after")
    def validate_fact(self) -> "RNADEDesignFact":
        _require_nonblank(self.job_id, "job_id")
        _require_sha256(
            self.source_attestation_sha256, "source_attestation_sha256"
        )
        _require_sha256(self.digest, "digest")
        sample_ids = [item.sample_id for item in self.groups]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("DE group sample IDs must be unique")
        contrast_ids = [
            (item.treatment, item.reference) for item in self.contrasts
        ]
        if len(contrast_ids) != len(set(contrast_ids)):
            raise ValueError("DE contrasts must be unique")
        known_groups = {item.group for item in self.groups}
        if set(sample_ids) != set(self.active_samples):
            raise ValueError("active_samples must exactly match DE group samples")
        if set(self.active_samples) & set(self.ignored_samples):
            raise ValueError("active and ignored DE samples must be disjoint")
        if len(self.active_samples) != len(set(self.active_samples)):
            raise ValueError("active_samples must be unique")
        if len(self.ignored_samples) != len(set(self.ignored_samples)):
            raise ValueError("ignored_samples must be unique")
        count_groups = [item.group for item in self.replicate_counts]
        if set(count_groups) != known_groups or len(count_groups) != len(set(count_groups)):
            raise ValueError("replicate_counts must exactly cover DE groups")
        if sum(item.count for item in self.replicate_counts) != len(self.active_samples):
            raise ValueError("replicate_counts must total active samples")
        violation_groups = [item.group for item in self.replicate_violations]
        if not set(violation_groups).issubset(known_groups):
            raise ValueError("replicate violations must reference DE groups")
        if len(violation_groups) != len(set(violation_groups)):
            raise ValueError("replicate violations must be unique")
        if self.run_lrt and self.design != RNAExperimentDesign.TIME_SERIES:
            raise ValueError("run_lrt is only valid for time_series design")
        group_time_names = [item.group for item in self.group_times]
        if len(group_time_names) != len(set(group_time_names)):
            raise ValueError("group_times must be unique")
        if not set(group_time_names).issubset(known_groups):
            raise ValueError("group_times must reference DE groups")
        if set(self.time_levels) != set(group_time_names):
            raise ValueError("time_levels must exactly match group_times")
        if self.effective_control_group not in known_groups:
            raise ValueError("effective control group must be a declared group")
        for contrast in self.contrasts:
            if (
                contrast.treatment not in known_groups
                or contrast.reference not in known_groups
            ):
                raise ValueError("DE contrast must reference declared groups")
        expected_digest = _digest(self.canonical_payload())
        if self.digest != expected_digest:
            raise ValueError("digest does not match canonical DE design")
        expected_id = _rna_fact_evidence_id(
            job_id=self.job_id,
            species=self.species,
            rnaseq_scope_id=self.rnaseq_scope_id,
            fact_kind=RNAFactKind.DE_DESIGN,
            schema_version=self.schema_version,
            digest=self.digest,
        )
        if self.evidence_id != expected_id:
            raise ValueError("evidence_id does not match DE design fact")
        return self


class RNAGenomeResourceFact(_WorkflowModel):
    fact_kind: Literal[RNAFactKind.GENOME_RESOURCE] = RNAFactKind.GENOME_RESOURCE
    schema_version: Literal["rna_genome_resource_v1"] = "rna_genome_resource_v1"
    job_id: str
    species: SpeciesCode
    rnaseq_scope_id: Literal[""] = ""
    reference_id: WorkflowIdentity
    promoted_config_sha256: str
    genome: RNAArtifactIdentity
    gff: RNAArtifactIdentity
    genome_prepare_manifest: RNAArtifactIdentity
    source_attestation_sha256: str
    digest: str
    evidence_id: str

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "fact_kind": self.fact_kind.value,
            "schema_version": self.schema_version,
            "species": self.species,
            "reference_id": self.reference_id,
            "promoted_config_sha256": self.promoted_config_sha256,
            "genome": self.genome.model_dump(mode="json"),
            "gff": self.gff.model_dump(mode="json"),
            "genome_prepare_manifest": self.genome_prepare_manifest.model_dump(
                mode="json"
            ),
        }

    @classmethod
    def build(cls, **values: Any) -> "RNAGenomeResourceFact":
        payload = dict(values)
        canonical = {
            "fact_kind": RNAFactKind.GENOME_RESOURCE.value,
            "schema_version": "rna_genome_resource_v1",
            "species": payload["species"],
            "reference_id": payload["reference_id"],
            "promoted_config_sha256": payload["promoted_config_sha256"],
            "genome": payload["genome"].model_dump(mode="json"),
            "gff": payload["gff"].model_dump(mode="json"),
            "genome_prepare_manifest": payload[
                "genome_prepare_manifest"
            ].model_dump(mode="json"),
        }
        digest = _digest(canonical)
        return cls(
            **payload,
            digest=digest,
            evidence_id=_rna_fact_evidence_id(
                job_id=payload["job_id"],
                species=payload["species"],
                rnaseq_scope_id="",
                fact_kind=RNAFactKind.GENOME_RESOURCE,
                schema_version="rna_genome_resource_v1",
                digest=digest,
            ),
        )

    @model_validator(mode="after")
    def validate_fact(self) -> "RNAGenomeResourceFact":
        _require_nonblank(self.job_id, "job_id")
        _require_sha256(
            self.promoted_config_sha256, "promoted_config_sha256"
        )
        _require_sha256(
            self.source_attestation_sha256, "source_attestation_sha256"
        )
        _require_sha256(self.digest, "digest")
        expected_digest = _digest(self.canonical_payload())
        if self.digest != expected_digest:
            raise ValueError("digest does not match canonical genome resources")
        expected_id = _rna_fact_evidence_id(
            job_id=self.job_id,
            species=self.species,
            rnaseq_scope_id="",
            fact_kind=RNAFactKind.GENOME_RESOURCE,
            schema_version=self.schema_version,
            digest=self.digest,
        )
        if self.evidence_id != expected_id:
            raise ValueError("evidence_id does not match genome resource fact")
        return self


class RNASamplesFactSourceClosure(_WorkflowModel):
    closure_kind: Literal["samples_confirmation"]
    source_evidence_id: str
    source_manifest_path: str
    source_manifest_sha256: str
    confirmed_artifact_path: str
    confirmed_artifact_sha256: str
    digest: str

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"digest"})

    @model_validator(mode="after")
    def validate_closure(self) -> "RNASamplesFactSourceClosure":
        _require_job_relative_path(self.source_manifest_path, "source_manifest_path")
        _require_sha256(self.source_manifest_sha256, "source_manifest_sha256")
        _require_job_relative_path(
            self.confirmed_artifact_path,
            "confirmed_artifact_path",
        )
        _require_sha256(
            self.confirmed_artifact_sha256,
            "confirmed_artifact_sha256",
        )
        expected_id = (
            f"formal_confirmation:{self.source_manifest_sha256}:"
            f"{self.source_manifest_path}"
        )
        if self.source_evidence_id != expected_id:
            raise ValueError("samples source evidence identity does not match closure")
        if self.digest != _digest(self.canonical_payload()):
            raise ValueError("samples source closure digest does not match")
        return self


class RNAGenomeFactSourceClosure(_WorkflowModel):
    closure_kind: Literal["genome_resource"]
    config_evidence_id: str
    config_manifest_path: str
    config_manifest_sha256: str
    confirmed_config_artifact_path: str
    confirmed_config_sha256: str
    genome_prepare_evidence_id: str
    genome_prepare_manifest_path: str
    genome_prepare_manifest_sha256: str
    source_config_artifact_sha256: str
    digest: str

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"digest"})

    @model_validator(mode="after")
    def validate_closure(self) -> "RNAGenomeFactSourceClosure":
        for field_name in (
            "config_manifest_path",
            "confirmed_config_artifact_path",
            "genome_prepare_manifest_path",
        ):
            _require_job_relative_path(getattr(self, field_name), field_name)
        for field_name in (
            "config_manifest_sha256",
            "confirmed_config_sha256",
            "genome_prepare_manifest_sha256",
            "source_config_artifact_sha256",
        ):
            _require_sha256(getattr(self, field_name), field_name)
        expected_config_id = (
            f"formal_confirmation:{self.config_manifest_sha256}:"
            f"{self.config_manifest_path}"
        )
        expected_prepare_id = (
            f"strict_v2:{self.genome_prepare_manifest_sha256}:"
            f"{self.genome_prepare_manifest_path}"
        )
        if self.config_evidence_id != expected_config_id:
            raise ValueError("config source evidence identity does not match closure")
        if self.genome_prepare_evidence_id != expected_prepare_id:
            raise ValueError("genome-prepare evidence identity does not match closure")
        if self.digest != _digest(self.canonical_payload()):
            raise ValueError("genome source closure digest does not match")
        return self


RNAFactProjectionSourceClosure = Annotated[
    RNASamplesFactSourceClosure | RNAGenomeFactSourceClosure,
    Field(discriminator="closure_kind"),
]
RNAProjectedFact = Annotated[
    RNAExperimentInputFact
    | RNAAlignmentStrandnessFact
    | RNADEDesignFact
    | RNAGenomeResourceFact,
    Field(discriminator="fact_kind"),
]


class RNAFactProjectionDocument(_WorkflowModel):
    projection_record_version: Literal["rna_fact_projection_record_v1"]
    record_type: Literal["rna_fact_projection"]
    job_id: str
    species: SpeciesCode
    rnaseq_scope_id: str
    fact_kind: RNAFactKind
    source_closure: RNAFactProjectionSourceClosure
    fact: RNAProjectedFact

    @model_validator(mode="after")
    def validate_document(self) -> "RNAFactProjectionDocument":
        fact = self.fact
        if (
            fact.job_id != self.job_id
            or fact.species != self.species
            or fact.rnaseq_scope_id != self.rnaseq_scope_id
            or fact.fact_kind != self.fact_kind
        ):
            raise ValueError("projection document identity must match its fact")
        if fact.source_attestation_sha256 != self.source_closure.digest:
            raise ValueError("fact source attestation must match source closure digest")
        sample_kinds = {
            RNAFactKind.EXPERIMENT_INPUT,
            RNAFactKind.ALIGNMENT_STRANDNESS,
            RNAFactKind.DE_DESIGN,
        }
        if self.fact_kind in sample_kinds:
            if not isinstance(self.source_closure, RNASamplesFactSourceClosure):
                raise ValueError("sample-derived RNA facts require samples closure")
        elif not isinstance(self.source_closure, RNAGenomeFactSourceClosure):
            raise ValueError("genome resource fact requires genome closure")
        return self


class EvidenceKind(str, Enum):
    CONFIG_DRAFT = "config_draft"
    SAMPLES_DRAFT = "samples_draft"
    CONFIG_CONFIRMATION = "config_confirmation"
    SAMPLES_CONFIRMATION = "samples_confirmation"
    STAGE_MANIFEST = "stage_manifest"
    SUBFAMILY_CONFIRMATION = "subfamily_confirmation"
    WHOLE_GENOME_RNA = "whole_genome_rna"
    RNA_EXPERIMENT_INPUT = "rna_experiment_input"
    RNA_ALIGNMENT_STRANDNESS = "rna_alignment_strandness"
    RNA_DE_DESIGN = "rna_de_design"
    RNA_GENOME_RESOURCE = "rna_genome_resource"


class RNAStableFactRef(_WorkflowModel):
    fact_kind: RNAFactKind
    schema_version: str
    job_id: str
    species: SpeciesCode
    rnaseq_scope_id: str
    digest: str
    evidence_id: str

    @model_validator(mode="after")
    def validate_stable_fact(self) -> "RNAStableFactRef":
        _require_nonblank(self.job_id, "job_id")
        _require_sha256(self.digest, "digest")
        expected_schemas = {
            RNAFactKind.EXPERIMENT_INPUT: "rna_experiment_input_v1",
            RNAFactKind.ALIGNMENT_STRANDNESS: "rna_alignment_strandness_v1",
            RNAFactKind.DE_DESIGN: "rna_de_design_v1",
            RNAFactKind.GENOME_RESOURCE: "rna_genome_resource_v1",
        }
        if self.schema_version != expected_schemas[self.fact_kind]:
            raise ValueError("schema_version must match fact_kind")
        if self.fact_kind == RNAFactKind.GENOME_RESOURCE:
            if self.rnaseq_scope_id:
                raise ValueError("genome resource fact cannot carry rnaseq_scope_id")
        elif not self.rnaseq_scope_id:
            raise ValueError("RNA experiment fact requires rnaseq_scope_id")
        elif not re.fullmatch(_IDENTITY_RE, self.rnaseq_scope_id):
            raise ValueError("rnaseq_scope_id must be a workflow identity")
        expected_id = _rna_fact_evidence_id(
            job_id=self.job_id,
            species=self.species,
            rnaseq_scope_id=self.rnaseq_scope_id,
            fact_kind=self.fact_kind,
            schema_version=self.schema_version,
            digest=self.digest,
        )
        if self.evidence_id != expected_id:
            raise ValueError("evidence_id does not match stable RNA fact")
        return self


class RNAFactProjectionState(_WorkflowModel):
    projection_state_version: Literal["rna_fact_projection_state_v1"] = (
        "rna_fact_projection_state_v1"
    )
    record_path: str
    record_sha256: str
    document: RNAFactProjectionDocument

    @property
    def fact(self) -> RNAProjectedFact:
        return self.document.fact

    @property
    def stable_ref(self) -> RNAStableFactRef:
        fact = self.fact
        return RNAStableFactRef(
            fact_kind=fact.fact_kind,
            schema_version=fact.schema_version,
            job_id=fact.job_id,
            species=fact.species,
            rnaseq_scope_id=fact.rnaseq_scope_id,
            digest=fact.digest,
            evidence_id=fact.evidence_id,
        )

    @model_validator(mode="after")
    def validate_projection_state(self) -> "RNAFactProjectionState":
        _require_job_relative_path(self.record_path, "record_path")
        _require_sha256(self.record_sha256, "record_sha256")
        RNAFactProjectionDocument.model_validate(
            self.document.model_dump(mode="python", round_trip=True)
        )
        expected_paths = {
            "rna_experiment_input_v1": (
                "final/workflow/rna_facts/current/"
                "rna_experiment_input_v1.json"
            ),
            "rna_alignment_strandness_v1": (
                "final/workflow/rna_facts/current/"
                "rna_alignment_strandness_v1.json"
            ),
            "rna_de_design_v1": (
                "final/workflow/rna_facts/current/rna_de_design_v1.json"
            ),
            "rna_genome_resource_v1": (
                "final/workflow/rna_facts/current/"
                "rna_genome_resource_v1.json"
            ),
        }
        expected_path = expected_paths[self.fact.schema_version]
        if self.record_path != expected_path:
            raise ValueError("projection record path must match fact schema")
        return self


class ManifestStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


class EvidenceRef(_WorkflowModel):
    evidence_ref_version: Literal["evidence_ref_v2"] = "evidence_ref_v2"
    evidence_id: str
    evidence_kind: EvidenceKind
    job_id: str
    authority: Literal["strict_v2", "formal_confirmation", "legacy_display"]
    manifest_identity_version: Literal["v2", "formal_v1", "legacy"]
    stage: WorkflowIdentity
    manifest_status: ManifestStatus
    manifest_path: str
    manifest_sha256: str
    confirmed_artifact_path: str = ""
    confirmed_artifact_sha256: str = ""
    confirmation_schema_version: str = ""
    source_draft_path: str = ""
    source_draft_sha256: str = ""
    draft_artifact_path: str = ""
    draft_artifact_sha256: str = ""
    source_config_artifact_sha256: str = ""
    species: str = ""
    rnaseq_scope_id: str = ""
    scope_id: str = ""
    analysis_route: str = ""
    member_set_id: str = ""
    source_confirmation_digest: str = ""
    source_dependency_kind: RNADependencyKind | Literal[""] = ""
    source_dependency_fingerprint_digest: str = ""

    @model_validator(mode="after")
    def validate_evidence_identity(self) -> "EvidenceRef":
        for field_name in (
            "evidence_id",
            "job_id",
        ):
            _require_nonblank(getattr(self, field_name), field_name)
        _require_sha256(self.manifest_sha256, "manifest_sha256")
        _require_job_relative_path(self.manifest_path, "manifest_path")
        expected_versions = {
            "strict_v2": "v2",
            "formal_confirmation": "formal_v1",
            "legacy_display": "legacy",
        }
        if self.manifest_identity_version != expected_versions[self.authority]:
            raise ValueError("evidence authority and identity version must match")
        source_dependency = (
            self.source_dependency_kind,
            self.source_dependency_fingerprint_digest,
        )
        if any(source_dependency) and not all(source_dependency):
            raise ValueError(
                "source dependency kind and fingerprint digest must be provided together"
            )
        if all(source_dependency):
            expected_dependency_kind = _RNA_STAGE_DEPENDENCY_KIND.get(self.stage)
            expected_evidence_kind = (
                EvidenceKind.STAGE_MANIFEST
                if self.stage == "rnaseq_family_projection"
                else EvidenceKind.WHOLE_GENOME_RNA
            )
            if (
                self.authority != "strict_v2"
                or self.manifest_identity_version != "v2"
                or self.evidence_kind != expected_evidence_kind
                or expected_dependency_kind is None
                or self.source_dependency_kind != expected_dependency_kind
            ):
                raise ValueError(
                    "source dependency kind must match the strict RNA output stage"
                )
            _require_sha256(
                self.source_dependency_fingerprint_digest,
                "source_dependency_fingerprint_digest",
            )
        for value in (
            self.rnaseq_scope_id,
            self.scope_id,
            self.analysis_route,
            self.member_set_id,
        ):
            if value and not re.fullmatch(_IDENTITY_RE, value):
                raise ValueError("evidence identity fields must be safe tokens")

        formal_fields = (
            self.confirmed_artifact_path,
            self.confirmed_artifact_sha256,
            self.confirmation_schema_version,
            self.source_draft_path,
            self.source_draft_sha256,
        )
        draft_fields = (
            self.draft_artifact_path,
            self.draft_artifact_sha256,
        )
        if self.authority == "formal_confirmation":
            if any((*draft_fields, self.source_config_artifact_sha256)):
                raise ValueError(
                    "formal confirmation cannot carry draft artifact or "
                    "source config fields"
                )
        elif any(formal_fields):
            raise ValueError(
                "formal confirmation fields are only valid for formal evidence"
            )
        if (
            self.evidence_kind
            not in {EvidenceKind.CONFIG_DRAFT, EvidenceKind.SAMPLES_DRAFT}
            and any(draft_fields)
        ):
            raise ValueError(
                "draft artifact fields are only valid for draft evidence"
            )
        if self.source_config_artifact_sha256 and not (
            self.authority == "strict_v2"
            and self.manifest_identity_version == "v2"
            and self.evidence_kind == EvidenceKind.STAGE_MANIFEST
            and self.stage == "genome_prepare"
        ):
            raise ValueError(
                "source config identity is only valid for genome_prepare"
            )

        scientific_identity = (
            self.species,
            self.rnaseq_scope_id,
            self.scope_id,
            self.analysis_route,
            self.member_set_id,
            self.source_confirmation_digest,
        )
        if self.authority == "formal_confirmation":
            if self.evidence_kind not in {
                EvidenceKind.CONFIG_CONFIRMATION,
                EvidenceKind.SAMPLES_CONFIRMATION,
            }:
                raise ValueError("formal_confirmation is only valid for config/sample evidence")
            if any(scientific_identity):
                raise ValueError("config/sample confirmation evidence cannot carry scientific identity")
            expected_stage = {
                EvidenceKind.CONFIG_CONFIRMATION: "config_autofill",
                EvidenceKind.SAMPLES_CONFIRMATION: "samples_autofill",
            }[self.evidence_kind]
            if self.stage != expected_stage:
                raise ValueError("formal confirmation kind and stage must match")
            if not self.source_draft_path or not self.source_draft_sha256:
                raise ValueError(
                    "formal confirmation requires source draft path and SHA256"
                )
            if (
                self.manifest_status != ManifestStatus.SUCCESS
                or not self.confirmed_artifact_path
                or not self.confirmed_artifact_sha256
                or not self.confirmation_schema_version
            ):
                raise ValueError("formal confirmation fields require successful closure")
            try:
                _require_job_relative_path(
                    self.source_draft_path,
                    "source_draft_path",
                )
                _require_sha256(
                    self.source_draft_sha256,
                    "source_draft_sha256",
                )
            except ValueError as exc:
                raise ValueError(f"invalid source draft evidence: {exc}") from exc
            _require_job_relative_path(
                self.confirmed_artifact_path,
                "confirmed_artifact_path",
            )
            _require_sha256(
                self.confirmed_artifact_sha256,
                "confirmed_artifact_sha256",
            )
            _require_nonblank(
                self.confirmation_schema_version,
                "confirmation_schema_version",
            )
            return self
        if self.evidence_kind in {
            EvidenceKind.CONFIG_DRAFT,
            EvidenceKind.SAMPLES_DRAFT,
        }:
            expected_stage = {
                EvidenceKind.CONFIG_DRAFT: "config_autofill",
                EvidenceKind.SAMPLES_DRAFT: "samples_autofill",
            }[self.evidence_kind]
            base_valid = (
                self.authority == "strict_v2"
                and self.manifest_identity_version == "v2"
                and self.manifest_status == ManifestStatus.SUCCESS
                and self.stage == expected_stage
                and all(draft_fields)
                and not self.source_config_artifact_sha256
            )
            if self.evidence_kind == EvidenceKind.CONFIG_DRAFT:
                identity_valid = not any(scientific_identity)
            else:
                identity_valid = (
                    bool(self.species)
                    and bool(self.rnaseq_scope_id)
                    and self.analysis_route == RNARoute.WHOLE_GENOME.value
                    and not any(
                        (
                            self.scope_id,
                            self.member_set_id,
                            self.source_confirmation_digest,
                        )
                    )
                )
            if not base_valid or not identity_valid:
                raise ValueError(
                    "draft evidence requires an exact successful strict-v2 artifact"
                )
            _require_job_relative_path(
                self.draft_artifact_path,
                "draft_artifact_path",
            )
            _require_sha256(
                self.draft_artifact_sha256,
                "draft_artifact_sha256",
            )
            if not re.fullmatch(r"^[A-Za-z][A-Za-z0-9._-]*$", self.species or "A"):
                raise ValueError("draft evidence species must be a species code")
            return self
        if self.evidence_kind in {
            EvidenceKind.CONFIG_CONFIRMATION,
            EvidenceKind.SAMPLES_CONFIRMATION,
        }:
            raise ValueError("config/sample confirmation evidence requires formal_confirmation")
        if self.evidence_kind in {
            EvidenceKind.RNA_EXPERIMENT_INPUT,
            EvidenceKind.RNA_ALIGNMENT_STRANDNESS,
            EvidenceKind.RNA_DE_DESIGN,
            EvidenceKind.RNA_GENOME_RESOURCE,
        }:
            raise ValueError("RNA fact selectors require RNAStableFactRef")
        if self.authority == "legacy_display":
            if self.source_confirmation_digest:
                raise ValueError("legacy display evidence cannot carry confirmation authority")
            return self

        if self.evidence_kind == EvidenceKind.SUBFAMILY_CONFIRMATION:
            if (
                self.stage != "subfamily_confirm"
                or self.species
                or self.rnaseq_scope_id
                or not self.scope_id
                or self.analysis_route != "confirmed_subfamily"
                or not self.member_set_id
            ):
                raise ValueError("family strict_v2 evidence requires scope/route/member identity")
            member_prefix = f"{self.scope_id}:confirmed_subfamily:"
            if not self.member_set_id.startswith(member_prefix) or not self.member_set_id[
                len(member_prefix) :
            ]:
                raise ValueError("family member identity must be scope-bound")
            _require_sha256(
                self.source_confirmation_digest,
                "source_confirmation_digest",
            )
        elif self.evidence_kind == EvidenceKind.WHOLE_GENOME_RNA:
            if (
                not self.stage.startswith("rnaseq_")
                or not self.rnaseq_scope_id
                or self.analysis_route != RNARoute.WHOLE_GENOME.value
                or any(
                    (
                        self.species,
                        self.scope_id,
                        self.member_set_id,
                        self.source_confirmation_digest,
                    )
                )
            ):
                raise ValueError(
                    "whole-genome RNA strict_v2 evidence requires RNA scope and whole route only"
                )
        elif self.evidence_kind == EvidenceKind.STAGE_MANIFEST:
            if self.stage == "rnaseq_family_projection":
                member_prefix = f"{self.scope_id}:confirmed_subfamily:"
                if (
                    self.species
                    or not self.rnaseq_scope_id
                    or not self.scope_id
                    or self.analysis_route != RNARoute.FAMILY_MEMBER.value
                    or not self.member_set_id.startswith(member_prefix)
                    or self.member_set_id[len(member_prefix) :] != "all_members"
                    or not self.source_confirmation_digest
                    or self.source_dependency_kind
                    != RNADependencyKind.FAMILY_PROJECTION
                ):
                    raise ValueError(
                        "family projection evidence requires exact RNA/family/source identity"
                    )
                _require_sha256(
                    self.source_confirmation_digest,
                    "source_confirmation_digest",
                )
            elif not self.analysis_route or self.source_confirmation_digest:
                raise ValueError("strict_v2 stage evidence requires a route without confirmation authority")
            if self.stage == "genome_prepare":
                if not self.source_config_artifact_sha256:
                    raise ValueError(
                        "genome_prepare evidence requires source config identity"
                    )
                _require_sha256(
                    self.source_config_artifact_sha256,
                    "source_config_artifact_sha256",
                )
            if self.species and not re.fullmatch(
                r"^[A-Za-z][A-Za-z0-9._-]*$",
                self.species,
            ):
                raise ValueError("stage evidence species must be a species code")
        elif self.species:
            raise ValueError("species binding is not valid for this evidence kind")
        return self


class DependencyFingerprint(_WorkflowModel):
    dependency_fingerprint_version: Literal["dependency_fingerprint_v1"] = (
        "dependency_fingerprint_v1"
    )
    job_id: str
    dependency_kind: str
    evidence: tuple[EvidenceRef, ...] = ()
    rna_facts: tuple[RNAStableFactRef, ...] = ()
    species: str = ""
    rnaseq_scope_id: str = ""
    family_scope_id: str = ""
    scope_id: str = ""
    analysis_route: str = ""
    member_set_id: str = ""
    digest: str

    @field_validator("evidence")
    @classmethod
    def sort_evidence(cls, value: tuple[EvidenceRef, ...]) -> tuple[EvidenceRef, ...]:
        return tuple(sorted(value, key=lambda item: item.evidence_id))

    @field_validator("rna_facts")
    @classmethod
    def sort_rna_facts(
        cls, value: tuple[RNAStableFactRef, ...]
    ) -> tuple[RNAStableFactRef, ...]:
        return tuple(
            sorted(
                value,
                key=lambda item: (item.fact_kind.value, item.evidence_id),
            )
        )

    def canonical_input(self) -> dict[str, Any]:
        return {
            "dependency_fingerprint_version": self.dependency_fingerprint_version,
            "job_id": self.job_id,
            "dependency_kind": self.dependency_kind,
            "evidence": [item.model_dump(mode="json") for item in self.evidence],
            "rna_facts": [
                item.model_dump(mode="json") for item in self.rna_facts
            ],
            "species": self.species,
            "rnaseq_scope_id": self.rnaseq_scope_id,
            "family_scope_id": self.family_scope_id,
            "scope_id": self.scope_id,
            "analysis_route": self.analysis_route,
            "member_set_id": self.member_set_id,
        }

    @model_validator(mode="after")
    def validate_fingerprint(self) -> "DependencyFingerprint":
        _require_nonblank(self.job_id, "job_id")
        _require_nonblank(self.dependency_kind, "dependency_kind")
        _require_sha256(self.digest, "digest")
        if not self.evidence and not self.rna_facts:
            raise ValueError("dependency fingerprint requires evidence or RNA facts")
        for item in self.rna_facts:
            RNAStableFactRef.model_validate(
                item.model_dump(mode="python", round_trip=True)
            )
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("dependency evidence IDs must be unique")
        if any(item.job_id != self.job_id for item in self.evidence):
            raise ValueError("dependency evidence must belong to the same job")
        if any(item.authority == "legacy_display" for item in self.evidence):
            raise ValueError("dependency fingerprints cannot use legacy evidence")
        if any(item.manifest_status != ManifestStatus.SUCCESS for item in self.evidence):
            raise ValueError("dependency fingerprints require successful evidence")
        fact_ids = [item.evidence_id for item in self.rna_facts]
        fact_kinds = [item.fact_kind for item in self.rna_facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("dependency RNA fact evidence IDs must be unique")
        if len(fact_kinds) != len(set(fact_kinds)):
            raise ValueError("dependency RNA fact kinds must be unique")
        for item in self.rna_facts:
            if item.job_id != self.job_id:
                raise ValueError("RNA fact job_id must match fingerprint job_id")
            if item.species != self.species:
                raise ValueError("RNA fact species must match fingerprint species")
            if (
                item.fact_kind != RNAFactKind.GENOME_RESOURCE
                and item.rnaseq_scope_id != self.rnaseq_scope_id
            ):
                raise ValueError(
                    "RNA fact rnaseq_scope_id must match fingerprint rnaseq_scope_id"
                )
        if self.rna_facts and (
            not self.species
            or not self.rnaseq_scope_id
            or self.analysis_route != RNARoute.WHOLE_GENOME.value
            or any((self.family_scope_id, self.scope_id, self.member_set_id))
        ):
            raise ValueError(
                "RNA facts require exact whole-genome species and RNA scope identity"
            )
        identity_values = (
            self.species,
            self.rnaseq_scope_id,
            self.family_scope_id,
            self.scope_id,
            self.analysis_route,
            self.member_set_id,
        )
        for value in identity_values:
            if value and not re.fullmatch(_IDENTITY_RE, value):
                raise ValueError("dependency identity fields must be safe tokens")
        whole_matches = []
        shared_de_matches = []
        family_matches = []
        for item in self.evidence:
            if item.authority == "formal_confirmation":
                continue
            if item.authority != "strict_v2" or item.manifest_identity_version != "v2":
                raise ValueError("scientific dependency evidence must be strict_v2")
            if item.evidence_kind == EvidenceKind.WHOLE_GENOME_RNA:
                if self.analysis_route == RNARoute.FAMILY_MEMBER.value:
                    matches = (
                        item.stage == "rnaseq_de"
                        and item.analysis_route == RNARoute.WHOLE_GENOME.value
                        and self.rnaseq_scope_id == item.rnaseq_scope_id
                        and not any(
                            (
                                item.scope_id,
                                item.member_set_id,
                                item.source_confirmation_digest,
                            )
                        )
                    )
                    shared_de_matches.append(matches)
                else:
                    matches = (
                        self.analysis_route == RNARoute.WHOLE_GENOME.value
                        and self.rnaseq_scope_id == item.rnaseq_scope_id
                        and not any(
                            (
                                self.family_scope_id,
                                self.scope_id,
                                self.member_set_id,
                            )
                        )
                    )
                whole_matches.append(matches)
                if not matches:
                    raise ValueError("evidence identity must match fingerprint identity")
            elif item.evidence_kind == EvidenceKind.SUBFAMILY_CONFIRMATION:
                matches = (
                    self.analysis_route
                    in {"confirmed_subfamily", RNARoute.FAMILY_MEMBER.value}
                    and self.scope_id == item.scope_id
                    and self.member_set_id == item.member_set_id
                    and (not self.family_scope_id or self.family_scope_id == item.scope_id)
                )
                family_matches.append(matches)
                if not matches:
                    raise ValueError("evidence identity must match fingerprint identity")
            elif item.evidence_kind == EvidenceKind.STAGE_MANIFEST:
                bootstrap_preparation = (
                    self.dependency_kind == "whole_genome_rna_bootstrap"
                    and self.analysis_route == RNARoute.WHOLE_GENOME.value
                    and item.stage == "genome_prepare"
                    and item.analysis_route == "data_preparation"
                    and item.species == self.species
                    and not any(
                        (
                            item.rnaseq_scope_id,
                            item.scope_id,
                            item.member_set_id,
                            item.source_confirmation_digest,
                        )
                    )
                )
                pairs = (
                    (item.species, self.species),
                    (item.rnaseq_scope_id, self.rnaseq_scope_id),
                    (item.scope_id, self.scope_id),
                    (item.analysis_route, self.analysis_route),
                    (item.member_set_id, self.member_set_id),
                )
                if not bootstrap_preparation and any(
                    evidence_value != fingerprint_value
                    for evidence_value, fingerprint_value in pairs
                ):
                    raise ValueError("evidence identity must match fingerprint identity")
        draft_kinds = {
            EvidenceKind.CONFIG_DRAFT,
            EvidenceKind.SAMPLES_DRAFT,
        }
        is_bootstrap = self.dependency_kind == "whole_genome_rna_bootstrap"
        if is_bootstrap:
            by_kind = {
                kind: [item for item in self.evidence if item.evidence_kind == kind]
                for kind in (
                    EvidenceKind.CONFIG_DRAFT,
                    EvidenceKind.CONFIG_CONFIRMATION,
                    EvidenceKind.SAMPLES_DRAFT,
                    EvidenceKind.SAMPLES_CONFIRMATION,
                    EvidenceKind.STAGE_MANIFEST,
                )
            }
            config_draft = by_kind[EvidenceKind.CONFIG_DRAFT]
            config_confirmed = by_kind[EvidenceKind.CONFIG_CONFIRMATION]
            samples_draft = by_kind[EvidenceKind.SAMPLES_DRAFT]
            samples_confirmed = by_kind[EvidenceKind.SAMPLES_CONFIRMATION]
            prepared = [
                item
                for item in by_kind[EvidenceKind.STAGE_MANIFEST]
                if item.stage == "genome_prepare"
                and item.analysis_route == "data_preparation"
                and item.species == self.species
            ]
            exact_shape = (
                len(self.evidence) == 5
                and all(
                    len(items) == 1
                    for items in (
                        config_draft,
                        config_confirmed,
                        samples_draft,
                        samples_confirmed,
                        prepared,
                    )
                )
            )
            if exact_shape:
                config_draft_item = config_draft[0]
                config_confirmed_item = config_confirmed[0]
                samples_draft_item = samples_draft[0]
                samples_confirmed_item = samples_confirmed[0]
                prepared_item = prepared[0]
                exact_shape = (
                    config_draft_item.draft_artifact_path
                    == config_confirmed_item.source_draft_path
                    and config_draft_item.draft_artifact_sha256
                    == config_confirmed_item.source_draft_sha256
                    and samples_draft_item.draft_artifact_path
                    == samples_confirmed_item.source_draft_path
                    and samples_draft_item.draft_artifact_sha256
                    == samples_confirmed_item.source_draft_sha256
                    and samples_draft_item.species == self.species
                    and samples_draft_item.rnaseq_scope_id == self.rnaseq_scope_id
                    and samples_draft_item.analysis_route
                    == RNARoute.WHOLE_GENOME.value
                    and prepared_item.source_config_artifact_sha256
                    == config_confirmed_item.confirmed_artifact_sha256
                )
            if (
                not exact_shape
                or self.analysis_route != RNARoute.WHOLE_GENOME.value
                or not self.species
                or not self.rnaseq_scope_id
                or any(
                    (
                        self.family_scope_id,
                        self.scope_id,
                        self.member_set_id,
                    )
                )
                or whole_matches
            ):
                raise ValueError(
                    "whole-genome bootstrap requires the exact five input authorities"
                )
        elif self.dependency_kind == "config_draft_confirmation":
            if (
                len(self.evidence) != 1
                or self.evidence[0].evidence_kind != EvidenceKind.CONFIG_DRAFT
                or any(identity_values)
            ):
                raise ValueError(
                    "config confirmation requires exactly one job-wide config draft"
                )
        elif self.dependency_kind == "samples_draft_confirmation":
            if (
                len(self.evidence) != 1
                or self.evidence[0].evidence_kind != EvidenceKind.SAMPLES_DRAFT
                or self.species != self.evidence[0].species
                or self.rnaseq_scope_id != self.evidence[0].rnaseq_scope_id
                or self.analysis_route != RNARoute.WHOLE_GENOME.value
            ):
                raise ValueError(
                    "samples confirmation requires exactly one experiment draft"
                )
        elif any(item.evidence_kind in draft_kinds for item in self.evidence):
            raise ValueError("draft evidence is not valid for this dependency kind")
        elif (
            self.species
            and self.analysis_route != RNARoute.WHOLE_GENOME.value
            and not self.rna_facts
        ):
            raise ValueError(
                "fingerprint species requires whole-genome, draft, or bootstrap identity"
            )
        if (
            self.analysis_route == RNARoute.WHOLE_GENOME.value
            and not is_bootstrap
            and self.dependency_kind != "samples_draft_confirmation"
            and not any(whole_matches)
            and not self.rna_facts
        ):
            raise ValueError("whole-genome dependency requires matching whole-genome RNA evidence")
        if self.analysis_route == RNARoute.FAMILY_MEMBER.value:
            if self.dependency_kind != "family_projection":
                raise ValueError(
                    "family_member dependency kind must be family_projection"
                )
            if not self.rnaseq_scope_id or not all(
                (self.family_scope_id, self.scope_id, self.member_set_id)
            ):
                raise ValueError("family dependency requires complete top-level identity")
            if sum(shared_de_matches) != 1 or sum(family_matches) != 1:
                raise ValueError(
                    "family dependency requires exactly one shared whole-genome DE "
                    "and one matching subfamily confirmation"
                )
        if self.analysis_route == "confirmed_subfamily" and not any(family_matches):
            raise ValueError("confirmed family dependency requires matching subfamily confirmation")
        if self.digest != _digest(self.canonical_input()):
            raise ValueError("digest does not match canonical dependency input")
        return self

    @classmethod
    def build(
        cls,
        *,
        job_id: str,
        dependency_kind: str,
        evidence: tuple[EvidenceRef, ...],
        rna_facts: tuple[RNAStableFactRef | Mapping[str, Any], ...] = (),
        species: str = "",
        rnaseq_scope_id: str = "",
        family_scope_id: str = "",
        scope_id: str = "",
        analysis_route: str = "",
        member_set_id: str = "",
    ) -> "DependencyFingerprint":
        ordered = tuple(sorted(evidence, key=lambda item: item.evidence_id))
        normalized_facts = tuple(
            RNAStableFactRef.model_validate(
                item.model_dump(mode="python", round_trip=True)
                if isinstance(item, RNAStableFactRef)
                else item
            )
            for item in rna_facts
        )
        ordered_facts = tuple(
            sorted(
                normalized_facts,
                key=lambda item: (item.fact_kind.value, item.evidence_id),
            )
        )
        canonical = {
            "dependency_fingerprint_version": "dependency_fingerprint_v1",
            "job_id": job_id,
            "dependency_kind": dependency_kind,
            "evidence": [item.model_dump(mode="json") for item in ordered],
            "rna_facts": [
                item.model_dump(mode="json") for item in ordered_facts
            ],
            "species": species,
            "rnaseq_scope_id": rnaseq_scope_id,
            "family_scope_id": family_scope_id,
            "scope_id": scope_id,
            "analysis_route": analysis_route,
            "member_set_id": member_set_id,
        }
        return cls(
            job_id=job_id,
            dependency_kind=dependency_kind,
            evidence=ordered,
            rna_facts=ordered_facts,
            species=species,
            rnaseq_scope_id=rnaseq_scope_id,
            family_scope_id=family_scope_id,
            scope_id=scope_id,
            analysis_route=analysis_route,
            member_set_id=member_set_id,
            digest=_digest(canonical),
        )


class SourceConfirmationV1(_WorkflowModel):
    source_confirmation_version: Literal["source_confirmation_v1"] = (
        "source_confirmation_v1"
    )
    job_id: str
    species: str
    family: str
    scope_id: str
    member_set_id: str
    manifest_path: str
    manifest_sha256: str
    confirmed_subfamily_path: str
    confirmed_subfamily_raw_sha256: str
    confirmed_subfamily_row_count: int = Field(gt=0)
    label_canonicalization_version: str
    canonical_labels_path: str
    canonical_labels_sha256: str
    gene_label_digest: str
    member_id_path: str
    member_id_sha256: str
    member_pep_path: str
    member_pep_sha256: str
    member_count: int = Field(gt=0)
    confirmed_at: datetime
    source_confirmation_digest: str

    @property
    def analysis_route(self) -> Literal["confirmed_subfamily"]:
        return "confirmed_subfamily"

    def canonical_input(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"source_confirmation_digest"},
        )

    @classmethod
    def build(cls, **values: Any) -> "SourceConfirmationV1":
        payload = dict(values)
        payload.pop("source_confirmation_digest", None)
        version = payload.get(
            "source_confirmation_version",
            "source_confirmation_v1",
        )
        payload["source_confirmation_version"] = version
        confirmed_at = payload.get("confirmed_at")
        if isinstance(confirmed_at, str):
            confirmed_at = datetime.fromisoformat(confirmed_at.replace("Z", "+00:00"))
            payload["confirmed_at"] = confirmed_at
        canonical = dict(payload)
        if isinstance(confirmed_at, datetime):
            canonical["confirmed_at"] = confirmed_at.isoformat().replace("+00:00", "Z")
        return cls(
            **payload,
            source_confirmation_digest=_digest(canonical),
        )

    @model_validator(mode="after")
    def validate_confirmation(self) -> "SourceConfirmationV1":
        for field_name in (
            "job_id",
            "species",
            "family",
            "scope_id",
            "member_set_id",
            "manifest_path",
            "confirmed_subfamily_path",
            "label_canonicalization_version",
            "canonical_labels_path",
            "member_id_path",
            "member_pep_path",
        ):
            _require_nonblank(getattr(self, field_name), field_name)
        for field_name in (
            "manifest_sha256",
            "confirmed_subfamily_raw_sha256",
            "canonical_labels_sha256",
            "gene_label_digest",
            "member_id_sha256",
            "member_pep_sha256",
        ):
            _require_sha256(getattr(self, field_name), field_name)
        expected_member_prefix = f"{self.scope_id}:confirmed_subfamily:"
        suffix = (
            self.member_set_id[len(expected_member_prefix) :]
            if self.member_set_id.startswith(expected_member_prefix)
            else ""
        )
        if suffix != "all_members":
            raise ValueError(
                "member_set_id must bind confirmed_subfamily to the valid nonempty "
                "suffix all_members"
            )
        for field_name in (
            "manifest_path",
            "confirmed_subfamily_path",
            "canonical_labels_path",
            "member_id_path",
            "member_pep_path",
        ):
            _require_job_relative_path(getattr(self, field_name), field_name)
        _require_aware(self.confirmed_at, "confirmed_at")
        _require_sha256(
            self.source_confirmation_digest,
            "source_confirmation_digest",
        )
        if self.source_confirmation_digest != _digest(self.canonical_input()):
            raise ValueError(
                "source_confirmation_digest does not match canonical confirmation input"
            )
        return self


class FamilyConfirmationBinding(_WorkflowModel):
    family_binding_version: Literal["family_confirmation_binding_v1"] = (
        "family_confirmation_binding_v1"
    )
    source_confirmation: SourceConfirmationV1
    source_confirmation_digest: str
    rnaseq_scope_id: WorkflowIdentity
    family_scope_id: WorkflowIdentity
    family_projection_fingerprint: DependencyFingerprint

    @model_validator(mode="after")
    def validate_binding(self) -> "FamilyConfirmationBinding":
        if (
            self.source_confirmation.job_id
            != self.family_projection_fingerprint.job_id
        ):
            raise ValueError("family binding and fingerprint must use the same job")
        if self.source_confirmation_digest != self.source_confirmation.source_confirmation_digest:
            raise ValueError("family binding must use the exact source confirmation digest")
        _require_sha256(
            self.source_confirmation_digest,
            "source_confirmation_digest",
        )
        if self.family_scope_id != self.source_confirmation.scope_id:
            raise ValueError("family_scope_id must match source confirmation scope")
        fingerprint = self.family_projection_fingerprint
        if (
            fingerprint.dependency_kind != "family_projection"
            or fingerprint.rnaseq_scope_id != self.rnaseq_scope_id
            or fingerprint.family_scope_id != self.family_scope_id
            or fingerprint.scope_id != self.source_confirmation.scope_id
            or fingerprint.analysis_route != "family_member"
            or fingerprint.member_set_id != self.source_confirmation.member_set_id
        ):
            raise ValueError("family binding identity must match projection fingerprint")
        source = self.source_confirmation
        if not any(
            evidence.evidence_kind == EvidenceKind.SUBFAMILY_CONFIRMATION
            and evidence.manifest_path == source.manifest_path
            and evidence.manifest_sha256 == source.manifest_sha256
            and evidence.scope_id == source.scope_id
            and evidence.analysis_route == source.analysis_route
            and evidence.member_set_id == source.member_set_id
            and evidence.source_confirmation_digest == self.source_confirmation_digest
            for evidence in self.family_projection_fingerprint.evidence
        ):
            raise ValueError(
                "family projection fingerprint must include the exact source confirmation digest"
            )
        return self


class ConfirmationEvidenceStatus(str, Enum):
    MISSING = "missing"
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    INVALID = "invalid"


def _validate_confirmation_evidence_state(
    *,
    job_id: str,
    status: ConfirmationEvidenceStatus,
    schema_version: str,
    confirmed_artifact_path: str,
    confirmed_artifact_sha256: str,
    evidence: EvidenceRef | None,
    expected_kind: EvidenceKind,
) -> None:
    _require_nonblank(job_id, "job_id")
    if status != ConfirmationEvidenceStatus.CONFIRMED:
        if (
            schema_version
            or confirmed_artifact_path
            or confirmed_artifact_sha256
            or evidence is not None
        ):
            raise ValueError("unconfirmed state cannot carry evidence")
        return
    _require_nonblank(schema_version, "schema_version")
    _require_job_relative_path(confirmed_artifact_path, "confirmed_artifact_path")
    _require_sha256(confirmed_artifact_sha256, "confirmed_artifact_sha256")
    if evidence is not None:
        evidence = EvidenceRef.model_validate(
            evidence.model_dump(mode="python", round_trip=True)
        )
    if evidence is None or evidence.evidence_kind != expected_kind:
        raise ValueError(f"confirmation state requires {expected_kind.value} evidence")
    if evidence.job_id != job_id:
        raise ValueError("confirmation evidence must belong to the same job")
    if (
        evidence.authority != "formal_confirmation"
        or evidence.manifest_identity_version != "formal_v1"
    ):
        raise ValueError("confirmed state requires formal confirmation evidence")
    if confirmed_artifact_path != evidence.confirmed_artifact_path:
        raise ValueError("confirmed artifact path must match evidence")
    if confirmed_artifact_sha256 != evidence.confirmed_artifact_sha256:
        raise ValueError("confirmed artifact hash must match evidence")
    if schema_version != evidence.confirmation_schema_version:
        raise ValueError("confirmation schema version must match evidence")


class ConfigEvidenceState(_WorkflowModel):
    config_evidence_version: Literal["config_evidence_state_v1"] = (
        "config_evidence_state_v1"
    )
    job_id: str
    status: ConfirmationEvidenceStatus
    schema_version: str = ""
    confirmed_artifact_path: str = ""
    confirmed_artifact_sha256: str = ""
    evidence: EvidenceRef | None = None

    @model_validator(mode="after")
    def validate_config_evidence(self) -> "ConfigEvidenceState":
        _validate_confirmation_evidence_state(
            job_id=self.job_id,
            status=self.status,
            schema_version=self.schema_version,
            confirmed_artifact_path=self.confirmed_artifact_path,
            confirmed_artifact_sha256=self.confirmed_artifact_sha256,
            evidence=self.evidence,
            expected_kind=EvidenceKind.CONFIG_CONFIRMATION,
        )
        return self


class SamplesEvidenceState(_WorkflowModel):
    samples_evidence_version: Literal["samples_evidence_state_v1"] = (
        "samples_evidence_state_v1"
    )
    job_id: str
    status: ConfirmationEvidenceStatus
    schema_version: str = ""
    confirmed_artifact_path: str = ""
    confirmed_artifact_sha256: str = ""
    evidence: EvidenceRef | None = None

    @model_validator(mode="after")
    def validate_samples_evidence(self) -> "SamplesEvidenceState":
        _validate_confirmation_evidence_state(
            job_id=self.job_id,
            status=self.status,
            schema_version=self.schema_version,
            confirmed_artifact_path=self.confirmed_artifact_path,
            confirmed_artifact_sha256=self.confirmed_artifact_sha256,
            evidence=self.evidence,
            expected_kind=EvidenceKind.SAMPLES_CONFIRMATION,
        )
        return self


class StageEvidenceStatus(str, Enum):
    MISSING = "missing"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


class StageEvidenceState(_WorkflowModel):
    stage_evidence_version: Literal["stage_evidence_state_v1"] = (
        "stage_evidence_state_v1"
    )
    job_id: str
    stage: WorkflowIdentity
    status: StageEvidenceStatus
    evidence: EvidenceRef | None = None

    @model_validator(mode="after")
    def validate_stage_evidence(self) -> "StageEvidenceState":
        _require_nonblank(self.job_id, "job_id")
        if self.evidence is not None:
            evidence = EvidenceRef.model_validate(
                self.evidence.model_dump(mode="python", round_trip=True)
            )
            if evidence.job_id != self.job_id:
                raise ValueError("stage evidence must belong to the same job")
            if self.stage != evidence.stage:
                raise ValueError("stage must match evidence stage")
            if evidence.evidence_kind in {
                EvidenceKind.CONFIG_CONFIRMATION,
                EvidenceKind.SAMPLES_CONFIRMATION,
            }:
                raise ValueError("stage evidence cannot use config/sample confirmation")
            if evidence.evidence_kind == EvidenceKind.WHOLE_GENOME_RNA and not self.stage.startswith("rnaseq_"):
                raise ValueError("whole-genome RNA evidence requires an RNA stage")
            if evidence.evidence_kind == EvidenceKind.SUBFAMILY_CONFIRMATION and self.stage != "subfamily_confirm":
                raise ValueError("subfamily evidence requires the subfamily_confirm stage")
            if self.status.value != evidence.manifest_status.value:
                raise ValueError("stage status must match evidence manifest status")
        elif self.status not in {StageEvidenceStatus.MISSING, StageEvidenceStatus.RUNNING}:
            raise ValueError("terminal stage evidence requires a manifest")
        return self


class TaskEvidenceStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    WAITING_RESOURCE = "waiting_resource"
    RUNNING = "running"
    WAITING_CONFIRMATION = "waiting_confirmation"
    SUCCESS = "success"
    COMPLETED = "completed"
    FAILED = "failed"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    REVOKED = "revoked"
    QUEUE_UNAVAILABLE = "queue_unavailable"
    STALLED = "stalled"
    STALLED_WARNING = "stalled_warning"


class TaskEvidenceSource(str, Enum):
    RUNTIME = "runtime"
    RESOURCE_LEDGER = "resource_ledger"


class WorkClaimKind(str, Enum):
    JOB_WIDE = "job_wide"
    STAGE = "stage"
    RNA_EXPERIMENT = "rna_experiment"
    FAMILY_PROJECTION = "family_projection"


class WorkClaim(_WorkflowModel):
    work_claim_version: Literal["work_claim_v1"] = "work_claim_v1"
    kind: WorkClaimKind
    job_id: str
    target_stage: WorkflowIdentity
    rnaseq_scope_id: str = ""
    family_scope_id: str = ""
    scope_id: str = ""
    analysis_route: str = ""
    member_set_id: str = ""
    source_confirmation_digest: str = ""
    resource_key: str

    @classmethod
    def build(
        cls,
        *,
        kind: WorkClaimKind,
        job_id: str,
        target_stage: str,
        rnaseq_scope_id: str = "",
        family_scope_id: str = "",
        scope_id: str = "",
        analysis_route: str = "",
        member_set_id: str = "",
        source_confirmation_digest: str = "",
    ) -> "WorkClaim":
        values = {
            "kind": kind,
            "job_id": job_id,
            "target_stage": target_stage,
            "rnaseq_scope_id": rnaseq_scope_id,
            "family_scope_id": family_scope_id,
            "scope_id": scope_id,
            "analysis_route": analysis_route,
            "member_set_id": member_set_id,
            "source_confirmation_digest": source_confirmation_digest,
        }
        return cls(**values, resource_key=cls._resource_key_for(values))

    @staticmethod
    def _resource_key_for(values: Mapping[str, Any]) -> str:
        kind = WorkClaimKind(values["kind"])
        identity: dict[str, str] = {
            "work_claim_version": "work_claim_v1",
            "kind": kind.value,
            "job_id": str(values["job_id"]),
        }
        if kind == WorkClaimKind.STAGE:
            identity["target_stage"] = str(values["target_stage"])
            if values.get("scope_id"):
                identity.update(
                    scope_id=str(values["scope_id"]),
                    analysis_route=str(values["analysis_route"]),
                    member_set_id=str(values["member_set_id"]),
                )
        elif kind == WorkClaimKind.RNA_EXPERIMENT:
            identity["rnaseq_scope_id"] = str(values["rnaseq_scope_id"])
        elif kind == WorkClaimKind.FAMILY_PROJECTION:
            identity.update(
                rnaseq_scope_id=str(values["rnaseq_scope_id"]),
                family_scope_id=str(values["family_scope_id"]),
                scope_id=str(values["scope_id"]),
                analysis_route=str(values["analysis_route"]),
                member_set_id=str(values["member_set_id"]),
                source_confirmation_digest=str(values["source_confirmation_digest"]),
            )
        return _digest(identity)

    @model_validator(mode="after")
    def validate_claim(self) -> "WorkClaim":
        _require_nonblank(self.job_id, "job_id")
        optional_identities = {
            "rnaseq_scope_id": self.rnaseq_scope_id,
            "family_scope_id": self.family_scope_id,
            "scope_id": self.scope_id,
            "analysis_route": self.analysis_route,
            "member_set_id": self.member_set_id,
        }
        for field_name, value in optional_identities.items():
            if value and not re.fullmatch(_IDENTITY_RE, value):
                raise ValueError(f"{field_name} must be a workflow identity")

        if self.kind == WorkClaimKind.JOB_WIDE:
            if any(optional_identities.values()) or self.source_confirmation_digest:
                raise ValueError("scope fields are not valid for job_wide")
        elif self.kind == WorkClaimKind.STAGE:
            if self.rnaseq_scope_id or self.family_scope_id or self.source_confirmation_digest:
                raise ValueError("RNA/family scope is not valid for stage")
            _validate_projection_identity(
                self.scope_id,
                self.analysis_route,
                self.member_set_id,
            )
        elif self.kind == WorkClaimKind.RNA_EXPERIMENT:
            if not self.rnaseq_scope_id:
                raise ValueError("rnaseq_scope_id is required for RNA experiment")
            if (
                self.family_scope_id
                or self.scope_id
                or self.analysis_route
                or self.member_set_id
                or self.source_confirmation_digest
            ):
                raise ValueError("family scope is not valid for RNA experiment")
        elif self.kind == WorkClaimKind.FAMILY_PROJECTION:
            if not self.rnaseq_scope_id or not self.family_scope_id:
                raise ValueError("family projection requires all typed identity fields")
            if self.family_scope_id != self.scope_id:
                raise ValueError("family_scope_id must equal scope_id")
            if self.analysis_route != "family_member":
                raise ValueError("family projection analysis_route must be family_member")
            _validate_projection_identity(
                self.scope_id,
                self.analysis_route,
                self.member_set_id,
                member_route="confirmed_subfamily",
            )
            expected_member_set_id = (
                f"{self.scope_id}:confirmed_subfamily:all_members"
            )
            if self.member_set_id != expected_member_set_id:
                raise ValueError(
                    f"member_set_id must be exactly {expected_member_set_id}"
                )
            _require_sha256(
                self.source_confirmation_digest,
                "source_confirmation_digest",
            )

        expected = self._resource_key_for(self.model_dump(mode="python"))
        _require_sha256(self.resource_key, "resource_key")
        if self.resource_key != expected:
            raise ValueError("resource_key must match the canonical work identity")
        return self


class TaskClaimAuthority(str, Enum):
    TYPED = "typed"
    LEGACY_UNSCOPED = "legacy_unscoped"


class TaskEvidenceState(_WorkflowModel):
    task_evidence_version: Literal["task_evidence_state_v1"] = (
        "task_evidence_state_v1"
    )
    job_id: str
    task_id: WorkflowIdentity
    stage: WorkflowIdentity
    status: TaskEvidenceStatus
    source: TaskEvidenceSource
    source_revision: StrictInt = Field(ge=0)
    claim_authority: TaskClaimAuthority = TaskClaimAuthority.LEGACY_UNSCOPED
    claim: WorkClaim | None = None

    @model_validator(mode="after")
    def validate_task_evidence(self) -> "TaskEvidenceState":
        _require_nonblank(self.job_id, "job_id")
        resource_statuses = {
            TaskEvidenceStatus.QUEUED,
            TaskEvidenceStatus.WAITING_RESOURCE,
            TaskEvidenceStatus.RUNNING,
            TaskEvidenceStatus.SUCCESS,
            TaskEvidenceStatus.COMPLETED,
            TaskEvidenceStatus.FAILED,
            TaskEvidenceStatus.FAILURE,
            TaskEvidenceStatus.CANCELLED,
            TaskEvidenceStatus.REVOKED,
        }
        runtime_statuses = {
            TaskEvidenceStatus.PENDING,
            TaskEvidenceStatus.QUEUED,
            TaskEvidenceStatus.WAITING_RESOURCE,
            TaskEvidenceStatus.WAITING_CONFIRMATION,
            TaskEvidenceStatus.RUNNING,
            TaskEvidenceStatus.SUCCESS,
            TaskEvidenceStatus.COMPLETED,
            TaskEvidenceStatus.FAILED,
            TaskEvidenceStatus.CANCELLED,
            TaskEvidenceStatus.QUEUE_UNAVAILABLE,
            TaskEvidenceStatus.STALLED,
            TaskEvidenceStatus.STALLED_WARNING,
        }
        allowed = (
            resource_statuses
            if self.source == TaskEvidenceSource.RESOURCE_LEDGER
            else runtime_statuses
        )
        if self.status not in allowed:
            raise ValueError(f"{self.source.value} status is not valid for that source")
        if self.claim_authority == TaskClaimAuthority.TYPED:
            if self.claim is None:
                raise ValueError("typed task evidence requires a claim")
            if self.claim.job_id != self.job_id:
                raise ValueError("claim job must match task evidence job")
            if self.claim.target_stage != self.stage:
                raise ValueError("claim stage must match task evidence stage")
        elif self.claim is not None:
            raise ValueError("legacy_unscoped task evidence cannot retain a claim")
        return self


class LargeInputEvidence(_WorkflowModel):
    large_input_evidence_version: Literal["large_input_evidence_v2"] = (
        "large_input_evidence_v2"
    )
    job_id: str
    path: str
    size: int = Field(gt=0)
    mtime_ns: int = Field(gt=0)
    device: int = Field(ge=0)
    inode: int = Field(gt=0)
    ctime_ns: int = Field(gt=0)
    recorded_sha256: str
    cache_key: str
    source: Literal["manifest_metadata", "reproducibility_cache", "metadata_snapshot"]

    def canonical_input(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "device": self.device,
            "inode": self.inode,
            "ctime_ns": self.ctime_ns,
            "source": self.source,
            "recorded_sha256": self.recorded_sha256,
        }

    @classmethod
    def build(cls, **values: Any) -> "LargeInputEvidence":
        payload = dict(values)
        payload.pop("cache_key", None)
        required = (
            "path",
            "size",
            "mtime_ns",
            "device",
            "inode",
            "ctime_ns",
            "source",
            "recorded_sha256",
        )
        if any(key not in payload for key in required):
            # Delegate missing-field reporting to Pydantic so callers always
            # receive the model's public ValidationError contract.
            return cls(**payload)
        canonical = {
            key: payload[key]
            for key in required
        }
        return cls(**payload, cache_key=_digest(canonical))

    @model_validator(mode="after")
    def validate_large_input(self) -> "LargeInputEvidence":
        _require_nonblank(self.job_id, "job_id")
        _require_job_relative_path(self.path, "path")
        if len(self.path) > 4096:
            raise ValueError("path is too long")
        _require_sha256(self.recorded_sha256, "recorded_sha256")
        _require_sha256(self.cache_key, "cache_key")
        if self.cache_key != _digest(self.canonical_input()):
            raise ValueError("cache_key does not match canonical large-input identity")
        return self


class SnapshotIncompleteReason(str, Enum):
    PATH_BUDGET_EXHAUSTED = "path_budget_exhausted"
    METADATA_BUDGET_EXHAUSTED = "metadata_budget_exhausted"
    WALL_TIME_BUDGET_EXHAUSTED = "wall_time_budget_exhausted"
    SCAN_ERROR = "scan_error"


class SnapshotCompleteness(_WorkflowModel):
    snapshot_completeness_version: Literal["snapshot_completeness_v1"] = (
        "snapshot_completeness_v1"
    )
    complete: StrictBool
    incomplete_reasons: tuple[SnapshotIncompleteReason, ...] = ()
    scanned_paths: int = Field(ge=0)
    scanned_metadata_bytes: int = Field(ge=0)
    elapsed_ms: int = Field(ge=0)
    max_paths: int = Field(gt=0)
    max_metadata_bytes: int = Field(gt=0)
    max_wall_time_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_completeness(self) -> "SnapshotCompleteness":
        if self.complete and self.incomplete_reasons:
            raise ValueError("complete scan cannot have incomplete_reasons")
        if not self.complete and not self.incomplete_reasons:
            raise ValueError("incomplete scan requires incomplete_reasons")
        if len(self.incomplete_reasons) != len(set(self.incomplete_reasons)):
            raise ValueError("incomplete_reasons must be unique")
        if self.scanned_metadata_bytes < self.scanned_paths:
            raise ValueError("scanned metadata bytes cannot be less than scanned paths")
        if (
            self.scanned_paths > self.max_paths
            or self.scanned_metadata_bytes > self.max_metadata_bytes
            or self.elapsed_ms > self.max_wall_time_ms
        ):
            raise ValueError("scan counters cannot exceed declared budgets")
        reason_set = set(self.incomplete_reasons)
        if (
            SnapshotIncompleteReason.PATH_BUDGET_EXHAUSTED in reason_set
            and self.scanned_paths != self.max_paths
        ):
            raise ValueError("path budget reason requires reaching max_paths")
        if (
            SnapshotIncompleteReason.METADATA_BUDGET_EXHAUSTED in reason_set
            and self.scanned_metadata_bytes != self.max_metadata_bytes
        ):
            raise ValueError("metadata budget reason requires reaching max_metadata_bytes")
        if (
            SnapshotIncompleteReason.WALL_TIME_BUDGET_EXHAUSTED in reason_set
            and self.elapsed_ms != self.max_wall_time_ms
        ):
            raise ValueError("wall time budget reason requires reaching max_wall_time_ms")
        return self


class WorkflowStateSnapshot(_WorkflowModel):
    workflow_state_version: Literal["workflow_state_snapshot_v2"] = (
        "workflow_state_snapshot_v2"
    )
    active_job_id: str
    revision: int = Field(ge=0)
    completeness: SnapshotCompleteness
    evidence: tuple[EvidenceRef, ...] = ()
    config_evidence: ConfigEvidenceState | None = None
    samples_evidence: SamplesEvidenceState | None = None
    stage_evidence: tuple[StageEvidenceState, ...] = ()
    task_evidence: tuple[TaskEvidenceState, ...] = ()
    large_inputs: tuple[LargeInputEvidence, ...] = ()
    rna_fact_projections: tuple[RNAFactProjectionState, ...] = ()
    rna_route: RNARoute | None = None
    foreground_mode: RNAForegroundMode | None = None
    species: SpeciesCode | None = None
    family: GeneFamily | None = None
    rnaseq_scope_id: WorkflowIdentity | None = None
    family_scope_id: WorkflowIdentity | None = None
    scope_id: WorkflowIdentity | None = None
    analysis_route: WorkflowIdentity | None = None
    member_set_id: WorkflowIdentity | None = None
    family_binding: FamilyConfirmationBinding | None = None

    @property
    def complete(self) -> bool:
        return self.completeness.complete

    @property
    def incomplete_reasons(self) -> tuple[SnapshotIncompleteReason, ...]:
        return self.completeness.incomplete_reasons

    @model_validator(mode="after")
    def validate_snapshot(self) -> "WorkflowStateSnapshot":
        _require_nonblank(self.active_job_id, "active_job_id")
        for item in self.evidence:
            EvidenceRef.model_validate(
                item.model_dump(mode="python", round_trip=True)
            )
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("snapshot evidence IDs must be unique")
        if any(item.job_id != self.active_job_id for item in self.evidence):
            raise ValueError("snapshot evidence must belong to the active job")
        state_jobs = [
            state.job_id
            for state in (
                self.config_evidence,
                self.samples_evidence,
                *self.stage_evidence,
                *self.task_evidence,
                *self.large_inputs,
            )
            if state is not None
        ]
        if any(job_id != self.active_job_id for job_id in state_jobs):
            raise ValueError("snapshot state evidence must belong to the active job")
        task_ids = [item.task_id for item in self.task_evidence]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("snapshot task IDs must be unique")
        large_paths = [item.path for item in self.large_inputs]
        if len(large_paths) != len(set(large_paths)):
            raise ValueError("snapshot large-input paths must be unique")
        cache_keys = [item.cache_key for item in self.large_inputs]
        if len(cache_keys) != len(set(cache_keys)):
            raise ValueError("snapshot large-input cache keys must be unique")
        projection_kinds = []
        projection_paths = []
        for item in self.rna_fact_projections:
            validated = RNAFactProjectionState.model_validate(
                item.model_dump(mode="python", round_trip=True)
            )
            if validated.fact.job_id != self.active_job_id:
                raise ValueError("RNA fact projection must belong to active job")
            projection_kinds.append(validated.fact.fact_kind)
            projection_paths.append(validated.record_path)
        if len(projection_kinds) != len(set(projection_kinds)):
            raise ValueError("snapshot RNA fact kinds must be unique")
        if len(projection_paths) != len(set(projection_paths)):
            raise ValueError("snapshot RNA fact paths must be unique")
        stage_keys = []
        for item in self.stage_evidence:
            evidence = item.evidence
            stage_keys.append(
                (
                    item.stage,
                    evidence.rnaseq_scope_id if evidence else "",
                    evidence.scope_id if evidence else "",
                    evidence.analysis_route if evidence else "",
                    evidence.member_set_id if evidence else "",
                )
            )
        if len(stage_keys) != len(set(stage_keys)):
            raise ValueError("snapshot stage identity must be unique")
        carrier_lower_bound = (
            len(self.evidence)
            + int(self.config_evidence is not None)
            + int(self.samples_evidence is not None)
            + len(self.stage_evidence)
            + len(self.task_evidence)
            + len(self.large_inputs)
            + len(self.rna_fact_projections)
        )
        if self.completeness.scanned_paths < carrier_lower_bound:
            raise ValueError("scanned_paths cannot be lower than the carrier count")
        if self.family_binding is not None and (
            self.family_binding.source_confirmation.job_id != self.active_job_id
        ):
            raise ValueError("family binding must belong to the active job")
        if self.rna_route is None:
            if any(
                value is not None
                for value in (
                    self.foreground_mode,
                    self.species,
                    self.family,
                    self.rnaseq_scope_id,
                    self.family_scope_id,
                    self.scope_id,
                    self.analysis_route,
                    self.member_set_id,
                    self.family_binding,
                )
            ):
                raise ValueError("RNA identity requires an explicit rna_route")
        elif self.rna_route == RNARoute.WHOLE_GENOME:
            if (
                self.foreground_mode != RNAForegroundMode.GLOBAL
                or self.species is None
                or self.rnaseq_scope_id is None
                or self.analysis_route != RNARoute.WHOLE_GENOME.value
                or any(
                    value is not None
                    for value in (
                        self.family,
                        self.family_scope_id,
                        self.scope_id,
                        self.member_set_id,
                        self.family_binding,
                    )
                )
            ):
                raise ValueError("whole_genome snapshot identity is inconsistent")
        else:
            required = (
                self.species,
                self.family,
                self.rnaseq_scope_id,
                self.family_scope_id,
                self.scope_id,
                self.member_set_id,
                self.family_binding,
            )
            if (
                self.foreground_mode != RNAForegroundMode.FAMILY
                or self.analysis_route != RNARoute.FAMILY_MEMBER.value
                or any(value is None for value in required)
            ):
                raise ValueError("family_member snapshot requires complete family identity")
            binding = self.family_binding
            assert binding is not None
            source = binding.source_confirmation
            if (
                binding.rnaseq_scope_id != self.rnaseq_scope_id
                or binding.family_scope_id != self.family_scope_id
                or source.species != self.species
                or source.family != self.family
                or source.scope_id != self.scope_id
                or source.member_set_id != self.member_set_id
            ):
                raise ValueError("family_member snapshot identity does not match binding")
        return self


class CurrentRecommendation(_WorkflowModel):
    recommendation_id: str
    job_id: str
    workflow_id: Literal[
        "data_preparation",
        "gene_family",
        "rnaseq_whole_genome",
        "rnaseq_family_member",
    ]
    transition_id: str
    from_state_id: str
    to_state_id: str
    target_stage: str
    authorized_stages: tuple[str, ...] = Field(min_length=1)
    evidence_digest: str
    family: str | None = None
    scope_id: str | None = None

    @model_validator(mode="after")
    def validate_recommendation(self) -> "CurrentRecommendation":
        for field_name in (
            "recommendation_id",
            "job_id",
            "transition_id",
            "from_state_id",
            "to_state_id",
            "target_stage",
        ):
            _require_nonblank(getattr(self, field_name), field_name)
        if len(self.authorized_stages) != len(set(self.authorized_stages)):
            raise ValueError("authorized_stages must contain unique stages")
        if any(not stage.strip() for stage in self.authorized_stages):
            raise ValueError("authorized_stages cannot contain blank stages")
        if self.target_stage not in self.authorized_stages:
            raise ValueError("target_stage must be authorized")
        if not _SHA256_RE.fullmatch(self.evidence_digest):
            raise ValueError("evidence_digest must be a sha256 hex digest")
        for field_name in ("family", "scope_id"):
            value = getattr(self, field_name)
            if value is not None:
                _require_nonblank(value, field_name)
        return self


class GoalLedger(_WorkflowModel):
    goal_ledger_version: Literal["goal_ledger_v1"] = "goal_ledger_v1"
    conversation_id: str
    revision: int = Field(ge=0)
    active_goal: str | None = None
    queued_goals: tuple[str, ...] = ()
    approved_decisions: tuple[str, ...] = ()
    rejected_routes: tuple[RNARoute, ...] = ()
    unresolved_blockers: tuple[str, ...] = ()
    user_terms: dict[str, JsonValue] = Field(default_factory=dict)
    corrections: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    @field_validator("user_terms")
    @classmethod
    def freeze_user_terms(cls, value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        return _freeze_json(dict(value))

    @field_serializer("user_terms")
    def serialize_user_terms(self, value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        thawed = _thaw_json(value)
        assert isinstance(thawed, dict)
        return thawed

    @model_validator(mode="after")
    def validate_ledger(self) -> "GoalLedger":
        _require_nonblank(self.conversation_id, "conversation_id")
        for field_name in (
            "queued_goals",
            "approved_decisions",
            "unresolved_blockers",
            "corrections",
            "evidence_ids",
        ):
            values = getattr(self, field_name)
            if any(not value.strip() for value in values):
                raise ValueError(f"{field_name} cannot contain blank values")
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must contain unique values")
        if self.active_goal is not None:
            _require_nonblank(self.active_goal, "active_goal")
            if self.active_goal in self.queued_goals:
                raise ValueError("active_goal cannot also be queued")
        if len(self.rejected_routes) != len(set(self.rejected_routes)):
            raise ValueError("rejected_routes must contain unique values")
        if any(not key.strip() for key in self.user_terms):
            raise ValueError("user_terms cannot contain blank keys")
        return self

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> "GoalLedger":
        candidate = super().model_copy(update=update, deep=deep)
        assert isinstance(candidate, GoalLedger)
        if candidate.revision < self.revision:
            raise ValueError("goal ledger revision cannot decrease")
        persisted_fields = (
            "active_goal",
            "queued_goals",
            "approved_decisions",
            "rejected_routes",
            "unresolved_blockers",
            "user_terms",
            "corrections",
            "evidence_ids",
        )
        content_changed = any(
            getattr(candidate, field_name) != getattr(self, field_name)
            for field_name in persisted_fields
        )
        if content_changed and candidate.revision <= self.revision:
            raise ValueError(
                "persisted goal ledger changes require a larger revision"
            )
        return candidate


class ActionEnvelope(_WorkflowModel):
    action_envelope_version: Literal["action_envelope_v1"] = "action_envelope_v1"
    action_type: str
    job_id: str
    payload: dict[str, JsonValue]
    payload_sha256: str
    dependency_fingerprint: DependencyFingerprint
    rna_route: RNARoute | None = None
    foreground_mode: RNAForegroundMode | None = None
    species: SpeciesCode | None = None
    family: GeneFamily | None = None
    rnaseq_scope_id: WorkflowIdentity | None = None
    family_scope_id: WorkflowIdentity | None = None
    scope_id: WorkflowIdentity | None = None
    analysis_route: WorkflowIdentity | None = None
    member_set_id: WorkflowIdentity | None = None
    family_binding: FamilyConfirmationBinding | None = None

    @field_validator("payload")
    @classmethod
    def freeze_payload(cls, value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        return _freeze_json(dict(value))

    @field_serializer("payload")
    def serialize_payload(self, value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        thawed = _thaw_json(value)
        assert isinstance(thawed, dict)
        return thawed

    @model_validator(mode="after")
    def validate_action(self) -> "ActionEnvelope":
        _require_nonblank(self.action_type, "action_type")
        _require_nonblank(self.job_id, "job_id")
        _require_sha256(self.payload_sha256, "payload_sha256")
        payload = _thaw_json(self.payload)
        if self.payload_sha256 != _digest(payload):
            raise ValueError("payload_sha256 does not match canonical payload")
        authority_keys = {
            "job_id",
            "species",
            "family",
            "rnaseq_scope_id",
            "family_scope_id",
            "scope_id",
            "analysis_route",
            "member_set_id",
            "rna_route",
            "foreground_mode",
            "source_confirmation",
            "source_confirmation_digest",
        }
        if authority_keys.intersection(self.payload):
            raise ValueError("payload cannot contain workflow authority fields")
        if self.dependency_fingerprint.job_id != self.job_id:
            raise ValueError("action and dependencies must use the same job")
        if (self.rna_route is None) != (self.foreground_mode is None):
            raise ValueError("RNA route and foreground mode must be supplied together")
        if self.rna_route is None and any(
            value is not None
            for value in (
                self.species,
                self.family,
                self.rnaseq_scope_id,
                self.family_scope_id,
                self.scope_id,
                self.analysis_route,
                self.member_set_id,
                self.family_binding,
            )
        ):
            raise ValueError("RNA identity requires an explicit rna_route")
        if self.rna_route == RNARoute.WHOLE_GENOME:
            if (
                self.foreground_mode != RNAForegroundMode.GLOBAL
                or self.species is None
                or self.rnaseq_scope_id is None
                or self.analysis_route != RNARoute.WHOLE_GENOME.value
                or any(
                    value is not None
                    for value in (
                        self.family,
                        self.family_scope_id,
                        self.scope_id,
                        self.member_set_id,
                        self.family_binding,
                    )
                )
            ):
                raise ValueError(
                    "whole_genome requires global foreground and whole-genome identity"
                )
            fingerprint = self.dependency_fingerprint
            if (
                fingerprint.rnaseq_scope_id != self.rnaseq_scope_id
                or fingerprint.analysis_route != self.analysis_route
                or fingerprint.species != self.species
                or any(
                    (
                        fingerprint.family_scope_id,
                        fingerprint.scope_id,
                        fingerprint.member_set_id,
                    )
                )
            ):
                if fingerprint.species != self.species:
                    raise ValueError(
                        "whole_genome dependency species does not match action"
                    )
                raise ValueError("whole_genome dependency identity does not match action")
        elif self.rna_route == RNARoute.FAMILY_MEMBER:
            required = (
                self.species,
                self.family,
                self.rnaseq_scope_id,
                self.family_scope_id,
                self.scope_id,
                self.member_set_id,
                self.family_binding,
            )
            if (
                self.foreground_mode != RNAForegroundMode.FAMILY
                or self.analysis_route != RNARoute.FAMILY_MEMBER.value
                or any(value is None for value in required)
            ):
                raise ValueError(
                    "family_member requires complete family foreground identity"
                )
            binding = self.family_binding
            assert binding is not None
            source = binding.source_confirmation
            fingerprint = self.dependency_fingerprint
            if binding.rnaseq_scope_id != self.rnaseq_scope_id:
                raise ValueError("rnaseq_scope_id does not match family binding")
            if binding.family_scope_id != self.family_scope_id:
                raise ValueError("family_scope_id does not match family binding")
            if (
                source.species != self.species
                or source.family != self.family
                or source.scope_id != self.scope_id
                or source.member_set_id != self.member_set_id
                or fingerprint.digest != binding.family_projection_fingerprint.digest
                or fingerprint.rnaseq_scope_id != self.rnaseq_scope_id
                or fingerprint.family_scope_id != self.family_scope_id
                or fingerprint.scope_id != self.scope_id
                or fingerprint.analysis_route != self.analysis_route
                or fingerprint.member_set_id != self.member_set_id
            ):
                raise ValueError("family_member identity does not match exact binding")
        elif self.family_binding is not None:
            raise ValueError("family binding requires an explicit family_member route")
        if self.family_binding is not None and (
            self.family_binding.source_confirmation.job_id != self.job_id
        ):
            raise ValueError("action and family binding must use the same job")
        return self


class PendingAction(_WorkflowModel):
    pending_action_version: Literal["pending_action_v1"] = "pending_action_v1"
    action_id: UUID
    principal_id: str
    session_id: str
    conversation_id: str
    envelope: ActionEnvelope
    token_sha256: str
    issued_at: datetime
    expires_at: datetime

    @property
    def job_id(self) -> str:
        return self.envelope.job_id

    @model_validator(mode="after")
    def validate_pending_action(self) -> "PendingAction":
        for field_name in ("principal_id", "session_id", "conversation_id"):
            _require_nonblank(getattr(self, field_name), field_name)
        _require_sha256(self.token_sha256, "token_sha256")
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        return self


class ImportGrant(_WorkflowModel):
    """Bind identical normalized path text without resolving the filesystem.

    The service must normalize before constructing the grant. Kernel code must
    resolve safely and recheck the stored device, inode, size, modification
    time, and digest metadata. Original user text belongs in later audit data.
    """

    import_grant_version: Literal["import_grant_v1"] = "import_grant_v1"
    grant_id: UUID
    principal_id: str
    session_id: str
    conversation_id: str
    job_id: str
    authorized_root: str
    requested_path: Annotated[str, StringConstraints(strip_whitespace=False)]
    resolved_path: str
    source_device: int = Field(ge=0)
    source_inode: int = Field(gt=0)
    source_size: int = Field(ge=0)
    source_mtime_ns: int = Field(ge=0)
    source_sha256: str
    allowed_suffixes: tuple[str, ...]
    file_count: int = Field(gt=0)
    total_size: int = Field(ge=0)
    largest_file_size: int = Field(ge=0)
    max_file_count: int = Field(gt=0)
    max_total_size: int = Field(gt=0)
    max_file_size: int = Field(gt=0)
    issued_at: datetime
    expires_at: datetime
    token_sha256: str

    @model_validator(mode="after")
    def validate_grant(self) -> "ImportGrant":
        for field_name in (
            "principal_id",
            "session_id",
            "conversation_id",
            "job_id",
            "authorized_root",
            "requested_path",
            "resolved_path",
        ):
            _require_nonblank(getattr(self, field_name), field_name)
        authorized_root = _require_absolute_canonical_path(
            self.authorized_root,
            "authorized_root",
        )
        requested_path = _require_absolute_canonical_path(
            self.requested_path,
            "requested_path",
        )
        resolved_path = _require_absolute_canonical_path(
            self.resolved_path,
            "resolved_path",
        )
        try:
            relative = resolved_path.relative_to(authorized_root)
        except ValueError as exc:
            raise ValueError("resolved_path must be within authorized_root") from exc
        if not relative.parts:
            raise ValueError("resolved_path must identify an item below authorized_root")
        if requested_path != resolved_path:
            raise ValueError(
                "requested_path and resolved_path must be identical normalized text"
            )
        _require_sha256(self.source_sha256, "source_sha256")
        _require_sha256(self.token_sha256, "token_sha256")
        if not self.allowed_suffixes or any(
            not suffix.startswith(".") or suffix != suffix.lower()
            for suffix in self.allowed_suffixes
        ):
            raise ValueError("allowed_suffixes must contain lowercase dot suffixes")
        if len(self.allowed_suffixes) != len(set(self.allowed_suffixes)):
            raise ValueError("allowed_suffixes must be unique")
        if self.file_count > self.max_file_count:
            raise ValueError("file count exceeds grant limit")
        if self.total_size > self.max_total_size:
            raise ValueError("total size exceeds grant limit")
        if self.largest_file_size > self.max_file_size:
            raise ValueError("largest file exceeds grant limit")
        if self.largest_file_size > self.total_size:
            raise ValueError("largest file cannot exceed total size")
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        return self


__all__ = [
    "ActionEnvelope",
    "ConfigEvidenceState",
    "ConfirmationEvidenceStatus",
    "DependencyFingerprint",
    "EvidenceKind",
    "EvidenceRef",
    "FamilyConfirmationBinding",
    "GeneFamily",
    "GoalLedger",
    "ImportGrant",
    "LargeInputEvidence",
    "ManifestStatus",
    "PendingAction",
    "RNAForegroundMode",
    "RNAAlignmentStrandnessFact",
    "RNAArtifactIdentity",
    "RNADEComparisonMode",
    "RNADEContrast",
    "RNADEDesignFact",
    "RNADEGroupAssignment",
    "RNADEGroupCount",
    "RNADEGroupTime",
    "RNADEReplicateViolation",
    "RNAExperimentDesign",
    "RNAExperimentInputFact",
    "RNAExperimentInputSample",
    "RNAFactKind",
    "RNAFactProjectionDocument",
    "RNAFactProjectionState",
    "RNAGenomeResourceFact",
    "RNAGenomeFactSourceClosure",
    "RNALibraryStrandness",
    "RNAReadIdentity",
    "RNADependencyKind",
    "RNAReplicatePolicy",
    "RNAReplicateViolationPolicy",
    "RNARoute",
    "RNAStableFactRef",
    "RNAStrandnessPolicy",
    "RNAStrandnessSample",
    "RNASamplesFactSourceClosure",
    "SamplesEvidenceState",
    "SnapshotCompleteness",
    "SnapshotIncompleteReason",
    "SpeciesCode",
    "SourceConfirmationV1",
    "StageEvidenceState",
    "StageEvidenceStatus",
    "TaskEvidenceSource",
    "TaskEvidenceState",
    "TaskEvidenceStatus",
    "TaskClaimAuthority",
    "WorkClaim",
    "WorkClaimKind",
    "WorkflowIdentity",
    "WorkflowStateSnapshot",
]
