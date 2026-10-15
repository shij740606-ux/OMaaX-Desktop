"""Single provenance contract for PPI preflight, execution, status and diagnosis."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


PPI_PROVENANCE_SCHEMA_VERSION = "ppi_provenance_v1"
PPI_DIAGNOSIS_CODES = {
    "exact_string": "ppi_string_exact",
    "tree_nearest": "ppi_string_tree_nearest",
    "model_fallback": "ppi_string_model_fallback",
    "unresolved": "ppi_string_unresolved",
    "resource_error": "ppi_string_resource_error",
}


def ppi_diagnosis_code(selection_mode: str, status: str = "") -> str:
    mode = str(selection_mode or "").strip()
    current_status = str(status or "").strip()
    if current_status == "resource_error":
        mode = "resource_error"
    elif current_status in {"unresolved", "conflict"} and mode not in PPI_DIAGNOSIS_CODES:
        mode = "unresolved"
    return PPI_DIAGNOSIS_CODES.get(mode, "ppi_string_unresolved")


def string_snapshot_provenance(snapshot_root: Path, species_resolution: dict[str, Any]) -> dict[str, str]:
    manifest_path = Path(snapshot_root) / "source_manifest.json"
    digest = ""
    version = str(species_resolution.get("string_version") or "")
    snapshot_id = str(species_resolution.get("snapshot_id") or "")
    try:
        raw = manifest_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        payload = json.loads(raw.decode("utf-8"))
        version = version or str(payload.get("string_version") or "")
        snapshot_id = snapshot_id or str(payload.get("snapshot_id") or "")
    except Exception:
        pass
    return {"version": version, "snapshot_id": snapshot_id, "sha256": digest}


def build_ppi_provenance(
    *,
    raw_target_species: Iterable[str],
    target_identity: dict[str, Any],
    species_resolution: dict[str, Any],
    selected_string_taxon_id: str,
    selection_mode: str,
    user_confirmed: bool,
    mapping_reference_species: str,
    mapping_path: str,
    normalization_species_label: str,
    snapshot_root: Path,
    status: str = "",
) -> dict[str, Any]:
    targets = [str(value) for value in raw_target_species if str(value)]
    mode = str(selection_mode or species_resolution.get("selection_mode") or "unresolved")
    return {
        "schema_version": PPI_PROVENANCE_SCHEMA_VERSION,
        "raw_target_species": targets,
        "canonical_target_name": str(
            target_identity.get("scientific_name")
            or species_resolution.get("target_tree_name")
            or ""
        ),
        "target_tree_taxon_id": str(
            target_identity.get("tree_taxon_id")
            or species_resolution.get("target_tree_taxon_id")
            or ""
        ),
        "selected_string_taxon_id": str(selected_string_taxon_id or ""),
        "selection_mode": mode,
        "string_snapshot": string_snapshot_provenance(snapshot_root, species_resolution),
        "user_confirmation": {
            "required": mode in {"tree_nearest", "model_fallback"},
            "confirmed": bool(user_confirmed),
        },
        "ortholog_mapping": {
            "reference_species": str(mapping_reference_species or ""),
            "path": str(mapping_path or ""),
        },
        "normalization_species_label": str(normalization_species_label or ""),
        "diagnosis_code": ppi_diagnosis_code(mode, status),
    }


def record_ppi_preflight_decision(
    workspace: Path,
    prefix: str,
    result: dict[str, Any],
) -> Path:
    destination = Path(workspace) / "reproducibility" / f"{prefix}.ppi.preflight_decisions.jsonl"
    destination.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "record_type": "ppi_preflight_decision",
        "recorded_at": datetime.now().isoformat(),
        "prefix": prefix,
        "status": str(result.get("status") or ""),
        "issue_code": str(result.get("issue_code") or ""),
        "decision": str(result.get("decision") or ""),
        "required_next_stage": str(result.get("required_next_stage") or ""),
        "ppi_provenance": dict(result.get("ppi_provenance") or {}),
    }
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return destination


def apply_ppi_provenance(
    manifest_path: Path,
    provenance: dict[str, Any],
    *,
    status: str | None = None,
    error: str = "",
) -> Path:
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except Exception:
        payload = {}
    payload.setdefault("manifest_version", "1.0")
    payload.setdefault("stage", "ppi")
    if status:
        payload["status"] = status
    payload["ppi_provenance"] = dict(provenance)
    if error:
        payload["error"] = error
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
