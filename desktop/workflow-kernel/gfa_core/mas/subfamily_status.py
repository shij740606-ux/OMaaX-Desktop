"""Shared subfamily label state resolver for gene-family workflows."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json


def _read_first_column_ids(path: str | Path) -> set[str]:
    ids: set[str] = set()
    if not path:
        return ids
    try:
        with open(path, "r", encoding="utf-8") as handle:
            first = True
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t") if "\t" in line else line.split()
                if not parts:
                    continue
                token = parts[0].strip()
                if first and token.lower() in {"gene_id", "gene", "id"}:
                    first = False
                    continue
                first = False
                if token:
                    ids.add(token)
    except Exception:
        return set()
    return ids


def _first_existing(paths: list[Path]) -> str:
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        try:
            if path.exists() and path.stat().st_size > 0:
                return str(path)
        except OSError:
            continue
    return ""


def _manifest_classifiable_species(manifest_path: Path) -> list[str]:
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    species: list[str] = []
    for key in ("classifiable_species", "target_species", "comparative_species"):
        for item in data.get(key) or []:
            item = str(item or "").strip()
            if item and item not in species:
                species.append(item)
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    for item in (metrics.get("scope_target_member_counts") or {}).keys():
        item = str(item or "").strip()
        if item and item not in species:
            species.append(item)
    return species


def _joint_subfamily_candidates(final_dir: Path, species: str, family: str, suffix: str) -> list[Path]:
    """Return joint subfamily outputs whose manifest declares species as classifiable."""
    if not final_dir.exists() or not species or not family:
        return []
    candidates: list[Path] = []
    for manifest_path in sorted(
        final_dir.glob(f"Target.{family}*.reference_classification.manifest.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        if species not in _manifest_classifiable_species(manifest_path):
            continue
        output_prefix = manifest_path.name[: -len(".reference_classification.manifest.json")]
        for candidate in (
            final_dir / f"{output_prefix}{suffix}",
            final_dir / f"{output_prefix.replace('.reference_classification', '')}{suffix}",
        ):
            if candidate not in candidates:
                candidates.append(candidate)
    for pattern in (
        f"Target.{family}*.comp-*{species}*{suffix}",
        f"Target.{family}*.target-*{species}*{suffix}",
    ):
        candidates.extend(sorted(final_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True))
    return candidates


def target_member_files(workspace_root: str | Path, job_id: str, species: str, family: str) -> dict[str, str]:
    final_dir = Path(workspace_root) / job_id / "final" if job_id else Path("")
    if not final_dir.exists() or not species or not family:
        return {}
    candidates = {
        "pep": [
            final_dir / f"{species}.{family}.curated_members.pep.fa",
            final_dir / f"Target.{species}.{family}.curated_members.pep.fa",
            final_dir / f"{species}.{family}.final_members.pep.fa",
            final_dir / f"Target.{species}.{family}.final_members.pep.fa",
        ],
        "id": [
            final_dir / f"{species}.{family}.curated_members.id",
            final_dir / f"Target.{species}.{family}.curated_members.id",
            final_dir / f"{species}.{family}.final_members.id",
            final_dir / f"Target.{species}.{family}.final_members.id",
        ],
    }
    return {key: found for key, paths in candidates.items() if (found := _first_existing(paths))}


def uploaded_target_subfamily_file(
    workspace_root: str | Path,
    input_root: str | Path,
    job_id: str,
    species: str,
    family: str,
) -> str:
    if not job_id or not species or not family:
        return ""
    final_dir = Path(workspace_root) / job_id / "final"
    input_dir = Path(input_root) / job_id
    return _first_existing([
        input_dir / f"{species}.{family}.subfamily",
        input_dir / f"{species}.{family}.subfamily.tsv",
        final_dir / f"{species}.{family}.subfamily",
        final_dir / f"{species}.{family}.subfamily.tsv",
    ])


def confirmed_subfamily_file(
    workspace_root: str | Path,
    input_root: str | Path,
    job_id: str,
    species: str,
    family: str,
) -> str:
    if not job_id or not species or not family:
        return ""
    final_dir = Path(workspace_root) / job_id / "final"
    input_dir = Path(input_root) / job_id
    candidates = [
        final_dir / f"{species}.{family}.confirmed_subfamily.tsv",
        input_dir / f"{species}.{family}.confirmed_subfamily.tsv",
        final_dir / f"{species}.{family}.target_plus_reference.confirmed_subfamily.tsv",
        input_dir / f"{species}.{family}.target_plus_reference.confirmed_subfamily.tsv",
    ]
    for pattern in (
        f"Target.{family}.target-{species}*.subfamily_prediction.confirmed_subfamily.tsv",
        f"Target.{family}.target-{species}*.confirmed_subfamily.tsv",
        f"*target-{species}*.subfamily_prediction.confirmed_subfamily.tsv",
    ):
        candidates.extend(sorted(final_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True))
    candidates.extend(_joint_subfamily_candidates(final_dir, species, family, ".confirmed_subfamily.tsv"))
    return _first_existing(candidates)


def prediction_subfamily_file(workspace_root: str | Path, job_id: str, species: str, family: str) -> str:
    if not job_id or not species or not family:
        return ""
    final_dir = Path(workspace_root) / job_id / "final"
    candidates = [
        final_dir / f"{species}.{family}.target_plus_reference.reference_classification.subfamily.tsv",
        final_dir / f"{species}.{family}.reference_classification.subfamily.tsv",
        final_dir / f"{species}.{family}.{family}.target-{species}.ref-At.target_plus_reference.reference_classification.subfamily.tsv",
    ]
    for pattern in (
        f"{species}.{family}*.reference_classification.subfamily.tsv",
        f"*target-{species}*.reference_classification.subfamily.tsv",
    ):
        candidates.extend(sorted(final_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True))
    candidates.extend(_joint_subfamily_candidates(final_dir, species, family, ".reference_classification.subfamily.tsv"))
    return _first_existing(candidates)


def reference_subfamily_file(
    workspace_root: str | Path,
    input_root: str | Path,
    job_id: str,
    species: str,
    family: str,
) -> str:
    if not job_id or not species or not family:
        return ""
    final_dir = Path(workspace_root) / job_id / "final"
    input_dir = Path(input_root) / job_id
    return _first_existing([
        input_dir / f"{species}.{family}.subfamily",
        input_dir / f"{species}.{family}.subfamily.tsv",
        input_dir / f"{species}.{family}.geneID.subfamily",
        final_dir / f"{species}.{family}.subfamily",
        final_dir / f"{species}.{family}.subfamily.tsv",
        final_dir / f"{species}.{family}.geneID.subfamily",
    ])


def _label_file_status(
    status_name: str,
    path: str,
    member_ids: set[str],
    species: str,
    family: str,
) -> dict[str, Any]:
    label_ids = _read_first_column_ids(path)
    missing = sorted(member_ids - label_ids)
    extra = sorted(label_ids - member_ids)
    state: dict[str, Any] = {
        "species": species,
        "family": family,
        "status": status_name,
        "label_file": path,
        "label_count": len(label_ids),
        "missing_member_ids": missing[:20],
        "extra_label_ids": extra[:20],
        "issues": [],
    }
    if missing:
        state["status"] = "confirmed_mismatch"
        state["issues"].append({
            "severity": "error",
            "code": "subfamily_label_member_mismatch",
            "message": f"{Path(path).name} 未覆盖当前 {species}/{family} 目标成员集合。",
            "suggestion": "请重新确认亚家族标签，或检查当前成员集合是否已更新。",
            "missing_count": len(missing),
            "extra_reference_or_non_target_count": len(extra),
        })
    return state


def subfamily_state_for_species(
    workspace_root: str | Path,
    input_root: str | Path,
    job_id: str,
    species: str,
    family: str,
    role: str = "target",
) -> dict[str, Any]:
    role = str(role or "target").strip().lower()
    reference_label = reference_subfamily_file(workspace_root, input_root, job_id, species, family)
    if role == "reference":
        return {
            "species": species,
            "family": family,
            "role": role,
            "reference_subfamily_file": reference_label,
            "status": "reference_label_available" if reference_label else "reference_label_missing",
            "issues": [] if reference_label else [{
                "severity": "error",
                "code": "missing_reference_subfamily",
                "message": f"缺少参考物种 {species}.{family}.subfamily。",
                "suggestion": f"请上传 {species}.{family}.subfamily 作为参考亚家族标签。",
            }],
        }

    member_files = target_member_files(workspace_root, job_id, species, family)
    member_ids = _read_first_column_ids(member_files.get("id", ""))
    state: dict[str, Any] = {
        "species": species,
        "family": family,
        "role": role,
        "member_files": member_files,
        "member_count": len(member_ids),
        "uploaded_subfamily_file": uploaded_target_subfamily_file(workspace_root, input_root, job_id, species, family),
        "confirmed_file": confirmed_subfamily_file(workspace_root, input_root, job_id, species, family),
        "prediction_file": prediction_subfamily_file(workspace_root, job_id, species, family),
        "reference_subfamily_file": reference_label,
        "status": "unknown",
        "issues": [],
    }
    if not member_files.get("pep") or not member_files.get("id"):
        state["status"] = "missing_identify"
        state["issues"].append({
            "severity": "error",
            "code": "missing_target_family_members",
            "message": f"{species}/{family} 尚未完成基因家族成员鉴定，不能进行亚家族确认或传统基因家族分析。",
            "suggestion": "请先执行 identify。",
        })
        return state

    if state["confirmed_file"]:
        state.update(_label_file_status("confirmed_valid", state["confirmed_file"], member_ids, species, family))
        state["confirmed_file"] = state["label_file"]
        return state
    if state["uploaded_subfamily_file"]:
        state.update(_label_file_status("uploaded_confirmed", state["uploaded_subfamily_file"], member_ids, species, family))
        state["uploaded_subfamily_file"] = state["label_file"]
        return state
    if state["prediction_file"]:
        state["status"] = "predicted_unconfirmed"
        prediction_name = Path(state["prediction_file"]).name
        if prediction_name.startswith(f"Target.{family}.") and ".reference_classification.subfamily.tsv" in prediction_name:
            state["review_prefix"] = prediction_name[: -len(".reference_classification.subfamily.tsv")]
        else:
            state["review_prefix"] = f"{species}.{family}"
        state["issues"].append({
            "severity": "warning",
            "code": "subfamily_prediction_unconfirmed",
            "message": f"检测到 {species}/{family} 已有亚家族分类预测文件，但还没有保存确认标签。",
            "suggestion": "请先打开亚家族确认表并保存。",
        })
        return state

    state["status"] = "not_classified"
    state["issues"].append({
        "severity": "warning",
        "code": "subfamily_classification_missing",
        "message": f"{species}/{family} 尚未进行目标物种+参考物种的亚家族分类。",
        "suggestion": "请先执行亚家族分类并确认标签，或上传目标物种亚家族文件。",
    })
    return state


def subfamily_state_for_scope(
    workspace_root: str | Path,
    input_root: str | Path,
    job_id: str,
    family: str,
    target_species: list[str] | tuple[str, ...] | None = None,
    reference_species: list[str] | tuple[str, ...] | None = None,
    comparative_species: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    targets = [str(x).strip() for x in (target_species or []) if str(x).strip()]
    refs = [str(x).strip() for x in (reference_species or []) if str(x).strip()]
    comps = [str(x).strip() for x in (comparative_species or []) if str(x).strip()]
    label_species = []
    for sp in [*targets, *comps]:
        if sp and sp not in label_species:
            label_species.append(sp)
    by_species = {
        sp: subfamily_state_for_species(workspace_root, input_root, job_id, sp, family, "target")
        for sp in label_species
    }
    reference_by_species = {
        sp: subfamily_state_for_species(workspace_root, input_root, job_id, sp, family, "reference")
        for sp in refs
    }
    statuses = {sp: data.get("status") for sp, data in by_species.items()}
    ready_statuses = {"confirmed_valid", "uploaded_confirmed"}
    all_labels_ready = bool(by_species) and all(status in ready_statuses for status in statuses.values())
    issues: list[dict[str, Any]] = []
    for sp, data in {**by_species, **reference_by_species}.items():
        for issue in data.get("issues") or []:
            item = dict(issue)
            item.setdefault("species", sp)
            issues.append(item)
    if all_labels_ready:
        overall = "confirmed_valid"
    elif any(status == "missing_identify" for status in statuses.values()):
        overall = "missing_identify"
    elif any(status == "confirmed_mismatch" for status in statuses.values()):
        overall = "confirmed_mismatch"
    elif any(status == "predicted_unconfirmed" for status in statuses.values()):
        overall = "predicted_unconfirmed"
    elif any(status == "not_classified" for status in statuses.values()):
        overall = "not_classified"
    else:
        overall = "unknown"
    return {
        "overall": overall,
        "by_species": by_species,
        "reference_by_species": reference_by_species,
        "issues": issues,
        "statuses": statuses,
        "all_confirmed": all_labels_ready,
        "all_labels_ready": all_labels_ready,
        "any_uploaded_confirmed": any(status == "uploaded_confirmed" for status in statuses.values()),
        "any_confirmed_valid": any(status == "confirmed_valid" for status in statuses.values()),
        "any_predicted": any(status == "predicted_unconfirmed" for status in statuses.values()),
        "any_not_classified": any(status == "not_classified" for status in statuses.values()),
        "label_species": label_species,
        "reference_species": refs,
    }
