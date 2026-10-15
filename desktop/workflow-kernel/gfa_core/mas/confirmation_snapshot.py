"""Read authoritative confirmation evidence without mutating a PlantAI job."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


_JOB_ID = re.compile(r"^job_[A-Za-z0-9_-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _is_beneath(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _resolve_job_layout(root: Path, job_id: str) -> tuple[Path, Path, Path]:
    logical_input_job = root / "input" / job_id
    logical_workspace = root / "workspace" / job_id
    input_root = (root / "input").resolve(strict=False)
    workspace_root = (root / "workspace").resolve(strict=False)
    input_job = logical_input_job.resolve(strict=False)
    job_workspace = logical_workspace.resolve(strict=False)
    if _is_beneath(input_job, input_root) and _is_beneath(
        job_workspace,
        workspace_root,
    ):
        return root, input_job, job_workspace

    if not (logical_input_job.is_symlink() and logical_workspace.is_symlink()):
        if not _is_beneath(input_job, input_root):
            raise ValueError("input job path escapes project root")
        raise ValueError("workspace job path escapes project root")

    input_job = logical_input_job.resolve(strict=True)
    job_workspace = logical_workspace.resolve(strict=True)
    linked_root = input_job.parents[1]
    if (
        input_job != linked_root / "input" / job_id
        or job_workspace != linked_root / "workspace" / job_id
    ):
        raise ValueError("linked job paths do not share one project root")
    return linked_root, input_job, job_workspace


def _read_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if type(payload) is not dict:
        raise ValueError("manifest must be an object")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    artifacts = payload.get("artifacts")
    if type(artifacts) is not dict or type(artifacts.get(key)) is not dict:
        raise ValueError(f"missing artifacts.{key}")
    return artifacts[key]


def _formal_artifact(
    root: Path,
    artifact: Mapping[str, Any],
    *,
    expected_relative: str,
    expected_type: str,
) -> Path:
    if artifact.get("path") != expected_relative:
        raise ValueError(f"artifact path must be {expected_relative}")
    if artifact.get("type") != expected_type:
        raise ValueError(f"artifact type must be {expected_type}")
    path = (root / expected_relative).resolve(strict=True)
    if path.is_dir() or path.stat().st_size <= 0:
        raise ValueError("artifact must be a nonempty file")
    recorded_hash = artifact.get("sha256")
    recorded_size = artifact.get("size")
    recorded_mtime = artifact.get("mtime_ns")
    if not isinstance(recorded_hash, str) or not _SHA256.fullmatch(recorded_hash):
        raise ValueError("artifact sha256 is invalid")
    if type(recorded_size) is not int or recorded_size != path.stat().st_size:
        raise ValueError("artifact size does not match")
    if type(recorded_mtime) is not int or recorded_mtime != path.stat().st_mtime_ns:
        raise ValueError("artifact mtime does not match")
    if _sha256(path) != recorded_hash:
        raise ValueError("artifact sha256 does not match")
    return path


def _legacy_artifact(
    root: Path,
    artifact: Mapping[str, Any],
    *,
    expected: Path,
    expected_type: str,
    manifest_path: Path,
) -> Path:
    raw_path = artifact.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("legacy artifact path is missing")
    if artifact.get("type") != expected_type:
        raise ValueError(f"legacy artifact type must be {expected_type}")
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve(strict=True)
    if candidate != expected.resolve(strict=True):
        raise ValueError("legacy artifact is not the authoritative job file")
    if candidate.is_dir() or candidate.stat().st_size <= 0:
        raise ValueError("legacy artifact must be a nonempty file")
    if manifest_path.stat().st_mtime_ns < candidate.stat().st_mtime_ns:
        raise ValueError("legacy manifest is older than its artifact")
    return candidate


def _confirmation_for_kind(
    root: Path,
    job_id: str,
    final_dir: Path,
    *,
    kind: str,
) -> dict[str, str] | None:
    if kind == "config":
        stage = "config_autofill"
        schema = "config_confirmation_v1"
        confirmed_name = "config.xlsx"
        draft_name = "config.autofill.draft.xlsx"
        artifact_type = "xlsx"
    else:
        stage = "samples_autofill"
        schema = "samples_confirmation_v1"
        confirmed_name = "samples.yaml"
        draft_name = "samples.autofill.draft.yaml"
        artifact_type = "yaml"

    formal_path = final_dir / f"{kind}-confirmation.manifest.json"
    legacy_path = final_dir / f"{kind}_autofill.manifest.json"
    expected_confirmed = root / "input" / job_id / confirmed_name

    if formal_path.is_file():
        payload = _read_manifest(formal_path)
        required = {
            "manifest_version": "2.0",
            "manifest_identity_version": "formal_v1",
            "stage": stage,
            "status": "success",
            "confirmation_schema_version": schema,
        }
        if any(payload.get(key) != value for key, value in required.items()):
            raise ValueError(f"invalid formal {kind} confirmation identity")
        confirmed = _formal_artifact(
            root,
            _artifact(payload, f"confirmed_{kind}"),
            expected_relative=f"input/{job_id}/{confirmed_name}",
            expected_type=artifact_type,
        )
        _formal_artifact(
            root,
            _artifact(payload, f"draft_{kind}"),
            expected_relative=f"workspace/{job_id}/final/{draft_name}",
            expected_type=artifact_type,
        )
        return {
            "layout": "formal",
            "manifest_path": str(formal_path),
            "artifact_path": str(confirmed),
        }

    if legacy_path.is_file():
        payload = _read_manifest(legacy_path)
        if (
            payload.get("manifest_version") != "1.0"
            or payload.get("stage") != stage
            or payload.get("status") != "success"
        ):
            raise ValueError(f"invalid legacy {kind} confirmation identity")
        confirmed = _legacy_artifact(
            root,
            _artifact(payload, f"confirmed_{kind}"),
            expected=expected_confirmed,
            expected_type=artifact_type,
            manifest_path=legacy_path,
        )
        return {
            "layout": "legacy",
            "manifest_path": str(legacy_path),
            "artifact_path": str(confirmed),
        }
    return None


def _resolve_subfamily_artifact(
    root: Path,
    job_workspace: Path,
    final_dir: Path,
    raw_path: str,
) -> Path:
    raw = Path(raw_path).expanduser()
    candidates = [raw] if raw.is_absolute() else [root / raw, job_workspace / raw, final_dir / raw]
    existing = [candidate.resolve(strict=True) for candidate in candidates if candidate.exists()]
    unique = list(dict.fromkeys(existing))
    if len(unique) != 1:
        raise ValueError("subfamily artifact path is missing or ambiguous")
    path = unique[0]
    if not _is_beneath(path, final_dir.resolve(strict=True)):
        raise ValueError("subfamily artifact escapes the current job final directory")
    if path.is_dir() or path.stat().st_size <= 0:
        raise ValueError("subfamily artifact must be a nonempty file")
    return path


def _subfamily_confirmation(
    root: Path,
    job_workspace: Path,
    final_dir: Path,
    expected_identity: Mapping[str, Any],
) -> tuple[dict[str, str] | None, tuple[str, ...]]:
    required_identity = {
        key: expected_identity.get(key) for key in ("prefix", "scope_id", "member_set_id")
    }
    if not all(isinstance(value, str) and value for value in required_identity.values()):
        return None, ()

    matches: list[dict[str, str]] = []
    errors: list[str] = []
    for manifest_path in sorted(final_dir.glob("*.subfamily_confirm.manifest.json")):
        try:
            payload = _read_manifest(manifest_path)
            parameters = payload.get("parameters")
            stage_context = payload.get("stage_context")
            if type(parameters) is not dict or type(stage_context) is not dict:
                raise ValueError("subfamily identity sections are missing")
            if (
                payload.get("manifest_version") != "1.0"
                or payload.get("stage") != "subfamily_confirm"
                or payload.get("status") != "success"
                or payload.get("prefix") != required_identity["prefix"]
                or payload.get("scope_id") != required_identity["scope_id"]
                or stage_context.get("scope_id") != required_identity["scope_id"]
                or parameters.get("analysis_route") != "confirmed_subfamily"
                or parameters.get("member_set_id") != required_identity["member_set_id"]
                or stage_context.get("member_set_id") != required_identity["member_set_id"]
            ):
                raise ValueError("subfamily confirmation identity mismatch")
            artifact = _artifact(payload, "confirmed_subfamily")
            if artifact.get("type") != "tsv" or not isinstance(artifact.get("path"), str):
                raise ValueError("subfamily artifact must be a TSV path")
            resolved = _resolve_subfamily_artifact(
                root,
                job_workspace,
                final_dir,
                artifact["path"],
            )
            matches.append(
                {
                    "layout": "manifest_v1",
                    "manifest_path": str(manifest_path),
                    "artifact_path": str(resolved),
                    "prefix": required_identity["prefix"],
                    "scope_id": required_identity["scope_id"],
                    "member_set_id": required_identity["member_set_id"],
                }
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"subfamily:{manifest_path.name}:{exc}")
    if len(matches) > 1:
        errors.append("subfamily:ambiguous identity-matched confirmation manifests")
        return None, tuple(errors)
    return (matches[0] if matches else None), tuple(errors)


def build_confirmation_snapshot(
    project_root: Path | str,
    job_id: str,
    expected_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a read-only confirmation view derived from authoritative job evidence."""

    if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
        raise ValueError("invalid job_id")
    root = Path(project_root).expanduser().resolve(strict=True)
    root, input_job, job_workspace = _resolve_job_layout(root, job_id)
    final_dir = job_workspace / "final"

    result: dict[str, Any] = {
        "job_id": job_id,
        "config": None,
        "samples": None,
        "subfamily": None,
        "config_confirmed": False,
        "samples_confirmed": False,
        "subfamily_confirmed": False,
        "errors": (),
    }
    if not final_dir.is_dir():
        return result

    errors: list[str] = []
    for kind in ("config", "samples"):
        try:
            evidence = _confirmation_for_kind(root, job_id, final_dir, kind=kind)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{kind}:{exc}")
            evidence = None
        result[kind] = evidence
        result[f"{kind}_confirmed"] = evidence is not None

    subfamily, subfamily_errors = _subfamily_confirmation(
        root,
        job_workspace,
        final_dir,
        expected_identity or {},
    )
    result["subfamily"] = subfamily
    result["subfamily_confirmed"] = subfamily is not None
    result["errors"] = tuple(errors) + subfamily_errors
    return result


__all__ = ["build_confirmation_snapshot"]
