"""Deterministic, job-local authority resolution for root GWAS inputs."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping


_ROLE_SUFFIXES = {
    "phenotype_table": (".tsv", ".txt"),
    "admixture_q": (".q",),
    "sample_fam": (".fam",),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _allowed_roots(project_root: Path, extra: Iterable[str | Path] = ()) -> list[Path]:
    configured = [item for item in os.environ.get("GWAS_EXTERNAL_INPUT_ROOTS", "").split(os.pathsep) if item]
    roots = [project_root.resolve(), project_root.parent.resolve()]
    for raw in [*configured, *extra]:
        try:
            roots.append(Path(raw).expanduser().resolve(strict=True))
        except OSError:
            continue
    return list(dict.fromkeys(roots))


def _inside(path: Path, roots: Iterable[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _validate_record(
    record: Mapping[str, Any], *, role: str, source: str, roots: Iterable[Path], workspace: Path | None = None,
) -> tuple[dict[str, Any] | None, str]:
    raw_path = Path(str(record.get("path") or "")).expanduser()
    if not raw_path.is_absolute() and workspace is not None:
        raw_path = workspace / raw_path
    try:
        path = raw_path.resolve(strict=True)
    except OSError:
        return None, "input path does not exist"
    if not path.is_file() or not _inside(path, roots):
        return None, "input path is outside the allowed roots"
    if role == "phenotype_table" and path.suffix.lower() == ".csv":
        return None, "CSV phenotype tables are not supported; provide TSV"
    if role == "phenotype_table":
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError):
            return None, "phenotype table has no readable header"
        if not lines or not lines[0].strip():
            return None, "phenotype table first line must contain the header"
        header = lines[0]
        if "\t" not in header:
            return None, "phenotype table must be tab-delimited"
    declared_size = record.get("size", record.get("size_bytes"))
    declared_sha = str(record.get("sha256") or "").lower()
    if (
        isinstance(declared_size, bool)
        or not isinstance(declared_size, int)
        or declared_size != path.stat().st_size
        or not re.fullmatch(r"[0-9a-f]{64}", declared_sha)
        or _sha256(path) != declared_sha
    ):
        return None, "input size or sha256 does not match its authority record"
    return {
        **dict(record), "path": str(path), "size": path.stat().st_size,
        "sha256": declared_sha, "source": source,
    }, ""


def _binding_records(workspace: Path, role: str) -> tuple[list[Mapping[str, Any]], str]:
    path = workspace / "external_inputs.manifest.json"
    if not path.is_file():
        return [], ""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], "external input binding is unreadable"
    if manifest.get("manifest_version") != "1.0":
        return [], "external input binding version is invalid"
    inputs = manifest.get("inputs")
    if isinstance(inputs, dict):
        if role not in inputs:
            return [], ""
        value = inputs.get(role)
        if isinstance(value, dict):
            return [value], ""
        if isinstance(value, list):
            if value and all(isinstance(item, dict) for item in value):
                return value, ""
        return [], f"external input binding role {role} is malformed"
    if isinstance(inputs, list):
        if any(not isinstance(item, dict) for item in inputs):
            return [], "external input binding list contains a malformed entry"
        return [item for item in inputs if isinstance(item, dict) and item.get("role") == role], ""
    return [], "external input binding inputs must be an object or list"


def _manifest_records(workspace: Path, role: str) -> tuple[list[Mapping[str, Any]], str]:
    final_dir = workspace / "final"
    applicable: list[Mapping[str, Any]] = []
    for path in sorted(final_dir.glob("*.manifest.json")) if final_dir.is_dir() else []:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("stage") in {"gwas_trait", "gwas_cov"}:
            continue
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict) or role not in artifacts:
            continue
        if manifest.get("status") != "success":
            return [], f"authoritative Manifest for {role} is not successful"
        record = artifacts.get(role)
        if not isinstance(record, dict) or record.get("role") != role:
            return [], f"authoritative Manifest artifact {role} is malformed"
        applicable.append({
            **record,
            "source_genotype_scope_id": str(
                manifest.get("genotype_scope_id")
                or manifest.get("source_genotype_scope_id")
                or ""
            ),
            "pair_id": str(manifest.get("run_id") or path.name),
        })
    return applicable, ""


def _looks_like_role(path: Path, role: str) -> bool:
    name = path.name.lower()
    if ".gwas_" in name or not name.endswith(_ROLE_SUFFIXES.get(role, ())):
        return False
    try:
        raw_lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return False
    if role == "phenotype_table" and (not raw_lines or not raw_lines[0].strip()):
        return False
    lines = [line.strip() for line in raw_lines if line.strip()][:3]
    if not lines:
        return False
    if role == "phenotype_table":
        return bool("\t" in lines[0] and len(lines[0].split("\t")) >= 2 and not lines[0].startswith("<"))
    if role == "admixture_q":
        try:
            widths = {len(line.split()) for line in lines}
            return len(widths) == 1 and next(iter(widths)) >= 2 and all(
                all(0.0 <= float(value) <= 1.0 for value in line.split()) for line in lines
            )
        except (ValueError, StopIteration):
            return False
    if role == "sample_fam":
        return all(len(line.split()) >= 6 for line in lines)
    return False


def _result(candidates: list[dict[str, Any]], selected: str = "") -> dict[str, Any]:
    deduplicated: dict[str, dict[str, Any]] = {}
    for candidate in sorted(candidates, key=lambda item: item["path"]):
        deduplicated.setdefault(candidate["sha256"], candidate)
    unique = list(deduplicated.values())
    if selected:
        try:
            selected_path = str(Path(selected).expanduser().resolve(strict=True))
        except OSError:
            selected_path = ""
        match = next((item for item in unique if item["path"] == selected_path), None)
        if match is None:
            return {
                "valid": False, "needs_confirmation": len(unique) > 1, "candidates": unique,
                "issues": [{"code": "invalid_root_input_selection", "message": "selected input is not authoritative"}],
                "decision": {},
            }
        unique = [match]
    if len(unique) == 1:
        return {"valid": True, "needs_confirmation": False, "issues": [], "candidates": unique, "decision": unique[0]}
    code = "ambiguous_root_input" if unique else "missing_root_input"
    return {
        "valid": False, "needs_confirmation": len(unique) > 1, "candidates": unique,
        "issues": [{"code": code, "message": "multiple inputs require confirmation" if unique else "required input is missing"}],
        "decision": {},
    }


def resolve_root_input(
    *, project_root: str | Path, job_id: str, role: str, selected: str = "",
    allowed_roots: Iterable[str | Path] = (),
) -> dict[str, Any]:
    project = Path(project_root).resolve()
    workspace = project / "workspace" / job_id
    roots = _allowed_roots(project, allowed_roots)

    manifest_records, manifest_error = _manifest_records(workspace, role)
    if manifest_error:
        return {"valid": False, "needs_confirmation": False, "candidates": [], "decision": {},
                "issues": [{"code": "invalid_upstream_manifest", "message": manifest_error}]}
    if manifest_records:
        validated: list[dict[str, Any]] = []
        for record in manifest_records:
            item, error = _validate_record(
                record, role=role, source="upstream_manifest", roots=roots, workspace=workspace,
            )
            if error:
                return {"valid": False, "needs_confirmation": False, "candidates": [], "decision": {},
                        "issues": [{"code": "invalid_upstream_manifest", "message": error}]}
            validated.append(item or {})
        return _result(validated, selected)

    binding_records, binding_error = _binding_records(workspace, role)
    if binding_error:
        return {"valid": False, "needs_confirmation": False, "candidates": [], "decision": {},
                "issues": [{"code": "invalid_external_binding", "message": binding_error}]}
    if binding_records:
        validated = []
        for record in binding_records:
            item, error = _validate_record(record, role=role, source="external_binding", roots=roots)
            if error:
                return {"valid": False, "needs_confirmation": False, "candidates": [], "decision": {},
                        "issues": [{"code": "invalid_external_binding", "message": error}]}
            validated.append(item or {})
        return _result(validated, selected)

    scanned: list[dict[str, Any]] = []
    for source, root in (("input", project / "input" / job_id), ("final", workspace / "final")):
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or not _looks_like_role(path, role):
                continue
            scanned.append({
                "role": role, "path": str(path.resolve()), "size": path.stat().st_size,
                "sha256": _sha256(path), "source": source,
            })
    return _result(scanned, selected)


def resolve_root_pair(
    *, project_root: str | Path, job_id: str, first_role: str, second_role: str,
    first_selected: str = "", second_selected: str = "",
) -> dict[str, Any]:
    first = resolve_root_input(
        project_root=project_root, job_id=job_id, role=first_role, selected=first_selected,
    )
    second = resolve_root_input(
        project_root=project_root, job_id=job_id, role=second_role, selected=second_selected,
    )
    if not first["valid"] or not second["valid"]:
        return {
            "valid": False, "needs_confirmation": bool(first.get("needs_confirmation") or second.get("needs_confirmation")),
            "issues": [*first.get("issues", []), *second.get("issues", [])], "first": {}, "second": {},
        }
    first_record, second_record = first["decision"], second["decision"]
    first_pair, second_pair = str(first_record.get("pair_id") or ""), str(second_record.get("pair_id") or "")
    if first_pair or second_pair:
        if not first_pair or first_pair != second_pair:
            return {"valid": False, "needs_confirmation": True,
                    "issues": [{"code": "root_input_pair_mismatch", "message": "Q and FAM pair IDs do not match"}],
                    "first": first_record, "second": second_record}
    first_scope = str(first_record.get("source_genotype_scope_id") or "")
    second_scope = str(second_record.get("source_genotype_scope_id") or "")
    if first_record.get("source") == second_record.get("source") == "upstream_manifest":
        verified_scope = first_scope if first_scope == second_scope else ""
    else:
        verified_scope = ""
        workspace = Path(project_root).resolve() / "workspace" / job_id
        lineage_paths = [
            workspace / "legacy_lineage.manifest.json",
            workspace / "final" / "legacy_lineage.manifest.json",
            Path(project_root).resolve() / "input" / job_id / "legacy_lineage.manifest.json",
        ]
        existing = list(dict.fromkeys(path.resolve() for path in lineage_paths if path.is_file()))
        if len(existing) == 1:
            try:
                from gfa_core.gwas_legacy_lineage import validate_legacy_lineage

                lineage = validate_legacy_lineage(
                    existing[0], allowed_roots=_allowed_roots(Path(project_root).resolve()),
                )
                artifacts = lineage["artifacts"]
                if (
                    str(artifacts[first_role].get("sha256") or "") == first_record["sha256"]
                    and str(artifacts[second_role].get("sha256") or "") == second_record["sha256"]
                ):
                    verified_scope = "genotype:" + str(lineage["source_genotype_sha256"])
            except (KeyError, ValueError):
                verified_scope = ""
    if not re.fullmatch(r"genotype:[0-9a-f]{64}", verified_scope):
        return {"valid": False, "needs_confirmation": False,
                "issues": [{"code": "unverified_covariate_lineage", "message": "Q/FAM genotype lineage is not verified"}],
                "first": first_record, "second": second_record}
    return {
        "valid": True, "needs_confirmation": False, "issues": [],
        "first": first_record, "second": second_record,
        "source_genotype_scope_id": verified_scope,
    }
