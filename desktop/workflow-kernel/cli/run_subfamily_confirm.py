#!/usr/bin/env python3
"""Create a confirmed subfamily table from predicted or user-edited labels."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
import sys
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.gene_family_scope import read_scope
from gfa_core.gene_family_stage_context import apply_stage_context, build_stage_context, user_output_prefix_for, write_legacy_manifest_copy


def _read_table(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    rows = [line for line in text if line.strip() and not line.lstrip().startswith("#")]
    if not rows:
        return []

    delimiter = "\t" if "\t" in rows[0] else ","
    first = [x.strip() for x in rows[0].split(delimiter)]
    header_tokens = {x.lower() for x in first}
    has_header = bool(header_tokens & {"gene_id", "gene", "id", "subfamily", "predicted_subfamily", "confirmed_subfamily"})

    if has_header:
        reader = csv.DictReader(rows, delimiter=delimiter)
        return [{str(k or "").strip(): str(v or "").strip() for k, v in row.items()} for row in reader]

    parsed = []
    for line in rows:
        parts = [x.strip() for x in line.split(delimiter)]
        if len(parts) >= 2:
            parsed.append({"gene_id": parts[0], "confirmed_subfamily": parts[1], "predicted_subfamily": parts[1]})
    return parsed


def _row_value(row: dict[str, str], names: Iterable[str]) -> str:
    lowered = {k.lower(): v for k, v in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value:
            return value.strip()
    return ""


def _load_labels(path: Path) -> Dict[str, dict[str, str]]:
    labels: Dict[str, dict[str, str]] = {}
    for row in _read_table(path):
        gene_id = _row_value(row, ["gene_id", "gene", "id", "target_gene", "label"])
        confirmed = _row_value(row, ["confirmed_subfamily", "subfamily", "assigned_subfamily", "prediction"])
        predicted = _row_value(row, ["predicted_subfamily", "assigned_subfamily", "subfamily", "prediction"])
        confidence = _row_value(row, ["confidence", "score"])
        note = _row_value(row, ["note", "status", "comment"])
        if not gene_id or not confirmed:
            continue
        labels[gene_id] = {
            "gene_id": gene_id,
            "predicted_subfamily": predicted or confirmed,
            "confirmed_subfamily": confirmed,
            "confidence": confidence,
            "note": note,
        }
    return labels


def _read_id_list(path: Path) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        gene_id = line.strip()
        if not gene_id or gene_id.startswith("#"):
            continue
        if gene_id in seen:
            raise ValueError(f"Duplicate ID in {path}: {gene_id}")
        seen.add(gene_id)
        ids.append(gene_id)
    return ids


def _read_fasta_ids(path: Path) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.startswith(">"):
                continue
            gene_id = line[1:].strip().split()[0]
            if not gene_id:
                continue
            if gene_id in seen:
                raise ValueError(f"Duplicate FASTA ID in {path}: {gene_id}")
            seen.add(gene_id)
            ids.append(gene_id)
    return ids


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_promote_pair(curated_id: Path, curated_pep: Path, final_id: Path, final_pep: Path) -> None:
    tmp_id = final_id.with_name(f".{final_id.name}.tmp")
    tmp_pep = final_pep.with_name(f".{final_pep.name}.tmp")
    tmp_id.write_text(curated_id.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
    tmp_pep.write_text(curated_pep.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
    tmp_ids = _read_id_list(tmp_id)
    tmp_fasta_ids = _read_fasta_ids(tmp_pep)
    if sorted(tmp_ids) != sorted(tmp_fasta_ids):
        tmp_id.unlink(missing_ok=True)
        tmp_pep.unlink(missing_ok=True)
        raise RuntimeError(f"Temporary final member validation failed for {final_id.stem}")
    tmp_id.replace(final_id)
    tmp_pep.replace(final_pep)


def _member_prefix_candidates(prefix: str, output_prefix: str = "", compact_prefix: str = "") -> list[str]:
    candidates: list[str] = []

    def add(value: str) -> None:
        value = str(value or "").strip()
        if value and value not in candidates:
            candidates.append(value)

    for value in (prefix, output_prefix, compact_prefix):
        add(value)
        parts = [part for part in str(value or "").split(".") if part]
        if len(parts) == 2:
            add(f"Target.{parts[0]}.{parts[1]}")
        elif len(parts) >= 3 and parts[0] == "Target":
            add(".".join(parts[1:3]))
    return candidates


def _promote_curated_members_to_final(final_dir: Path, prefixes: Iterable[str], labels: Dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    label_ids = set(labels)
    promotions: list[dict[str, Any]] = []
    for member_prefix in prefixes:
        curated_id = final_dir / f"{member_prefix}.curated_members.id"
        curated_pep = final_dir / f"{member_prefix}.curated_members.pep.fa"
        if not curated_id.exists() and not curated_pep.exists():
            continue
        if not curated_id.exists() or not curated_pep.exists():
            raise FileNotFoundError(
                f"Incomplete curated members for {member_prefix}: expected both {curated_id.name} and {curated_pep.name}"
            )
        curated_ids = _read_id_list(curated_id)
        if not curated_ids:
            raise ValueError(f"Curated member ID file is empty: {curated_id}")
        fasta_ids = _read_fasta_ids(curated_pep)
        if sorted(curated_ids) != sorted(fasta_ids):
            raise ValueError(
                f"Curated member ID/FASTA mismatch for {member_prefix}: "
                f"{len(curated_ids)} IDs vs {len(fasta_ids)} FASTA records"
            )
        missing_labels = [gene_id for gene_id in curated_ids if gene_id not in label_ids]
        if missing_labels:
            preview = ", ".join(missing_labels[:5])
            raise ValueError(
                f"Confirmed subfamily labels do not cover curated members for {member_prefix}: "
                f"{len(missing_labels)} missing ({preview})"
            )

        final_id = final_dir / f"{member_prefix}.final_members.id"
        final_pep = final_dir / f"{member_prefix}.final_members.pep.fa"
        _atomic_promote_pair(curated_id, curated_pep, final_id, final_pep)

        promoted_ids = _read_id_list(final_id)
        promoted_fasta_ids = _read_fasta_ids(final_pep)
        if sorted(promoted_ids) != sorted(curated_ids) or sorted(promoted_fasta_ids) != sorted(curated_ids):
            raise RuntimeError(f"Post-promotion final member validation failed for {member_prefix}")
        promotions.append({
            "prefix": member_prefix,
            "count": len(curated_ids),
            "curated_id": str(curated_id),
            "curated_pep": str(curated_pep),
            "final_id": str(final_id),
            "final_pep": str(final_pep),
            "id_checksum": _sha256(final_id),
            "pep_checksum": _sha256(final_pep),
            "source_curated_id_checksum": _sha256(curated_id),
            "source_curated_pep_checksum": _sha256(curated_pep),
        })
    return promotions


def _find_source(final_dir: Path, input_dir: Path, prefix: str, explicit: str = "", output_prefix: str = "", source_prefixes: Iterable[str] = ()) -> Tuple[Path, str]:
    candidates = []
    if explicit:
        explicit_path = Path(explicit).expanduser()
        if explicit_path.is_absolute():
            candidates.append((explicit_path, "explicit"))
        else:
            workspace = final_dir.parent
            candidates.extend([
                (Path(explicit), "explicit"),
                (workspace / explicit_path, "explicit"),
                (final_dir / explicit_path, "explicit"),
                (input_dir / explicit_path, "explicit"),
                (final_dir / explicit_path.name, "explicit"),
                (input_dir / explicit_path.name, "explicit"),
            ])
    for base in [*source_prefixes, output_prefix, prefix]:
        if not base:
            continue
        candidates.extend([
            (final_dir / f"{base}.confirmed_subfamily.edited.tsv", "edited"),
            (input_dir / f"{base}.confirmed_subfamily.edited.tsv", "edited"),
            (final_dir / f"{base}.reference_classification.subfamily.tsv", "reference_classification"),
            (final_dir / f"{base}.geneID.subfamily", "geneID_subfamily"),
            (input_dir / f"{base}.geneID.subfamily", "input_geneID_subfamily"),
        ])
    for path, source in candidates:
        if path.exists() and path.stat().st_size > 0:
            return path, source
    raise FileNotFoundError(
        "No subfamily source found. Provide --edited-subfamily or run reference_classification first."
    )


def _compact_confirmed_prefix(args_prefix: str, output_prefix: str, scope_data: dict[str, Any]) -> str:
    family = str(
        scope_data.get("family")
        or scope_data.get("gene_family")
        or scope_data.get("geneFamily")
        or ""
    ).strip()
    targets = scope_data.get("target_species") or scope_data.get("targets") or []
    if isinstance(targets, str):
        targets = [targets]
    targets = [str(item or "").strip() for item in targets if str(item or "").strip()]

    for candidate in (args_prefix, output_prefix):
        parts = [part for part in str(candidate or "").split(".") if part]
        if len(parts) >= 2 and parts[0] not in {"Target", "Multi"}:
            return f"{parts[0]}.{parts[1]}"
        if not family and len(parts) >= 2:
            family = parts[1]

    if len(targets) == 1 and family:
        return f"{targets[0]}.{family}"
    return ""


def _load_assignment_confidence(final_dir: Path, prefix: str, output_prefix: str = "") -> Dict[str, dict[str, str]]:
    assignment = final_dir / f"{output_prefix or prefix}.reference_classification.assignment.tsv"
    if not assignment.exists() and output_prefix:
        assignment = final_dir / f"{prefix}.reference_classification.assignment.tsv"
    if not assignment.exists():
        return {}
    out: Dict[str, dict[str, str]] = {}
    for row in _read_table(assignment):
        gene_id = _row_value(row, ["gene_id", "target_gene", "gene", "id"])
        if not gene_id:
            continue
        out[gene_id] = {
            "predicted_subfamily": _row_value(row, ["assigned_subfamily", "predicted_subfamily", "subfamily"]),
            "confidence": _row_value(row, ["confidence", "score"]),
            "note": _row_value(row, ["note", "status"]),
        }
    return out


def _read_manifest(path_text: str) -> dict[str, Any]:
    path_text = str(path_text or "").strip()
    if not path_text:
        return {}
    path = Path(path_text).expanduser()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _family_from_scope_or_prefix(scope_id: str, prefix: str, manifest: dict[str, Any]) -> str:
    stage_context = manifest.get("stage_context") if isinstance(manifest.get("stage_context"), dict) else {}
    scope_obj = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
    for source in (manifest.get("family"), stage_context.get("family"), scope_obj.get("family")):
        text = str(source or "").strip()
        if text:
            return text
    scope_id = str(scope_id or "").strip()
    if ".target-" in scope_id:
        return scope_id.split(".target-", 1)[0].strip()
    parts = [part for part in str(prefix or "").split(".") if part]
    if len(parts) >= 2:
        return parts[1]
    return ""


def _scope_data_from_prediction_manifest(manifest: dict[str, Any], prefix: str) -> dict[str, Any]:
    if not manifest:
        return {}
    stage_context = manifest.get("stage_context") if isinstance(manifest.get("stage_context"), dict) else {}
    scope_obj = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
    params = manifest.get("parameters") if isinstance(manifest.get("parameters"), dict) else {}

    def pick(key: str, default: Any = "") -> Any:
        for source in (manifest, stage_context, scope_obj, params):
            if isinstance(source, dict) and source.get(key) not in (None, "", []):
                return source.get(key)
        return default

    scope_id = str(pick("scope_id", "") or "").strip()
    if not scope_id:
        return {}
    target_species = [str(x) for x in (pick("target_species", []) or []) if str(x).strip()]
    reference_species = [str(x) for x in (pick("reference_species", []) or []) if str(x).strip()]
    comparative_species = [str(x) for x in (pick("comparative_species", []) or []) if str(x).strip()]
    selected_species = [str(x) for x in (pick("selected_species", []) or []) if str(x).strip()]
    if not selected_species:
        selected_species = [*target_species, *reference_species, *comparative_species]
    return {
        "family": _family_from_scope_or_prefix(scope_id, prefix, manifest),
        "prefix": pick("prefix", prefix),
        "scope_id": scope_id,
        "scope_mode": str(pick("scope_mode", "") or ""),
        "target_species": target_species,
        "reference_species": reference_species,
        "comparative_species": comparative_species,
        "selected_species": selected_species,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Confirm predicted subfamily labels for downstream plotting.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--input-dir", default="")
    parser.add_argument("--edited-subfamily", default="", help="User-edited table. Supports gene_id/subfamily or confirmed_subfamily columns.")
    parser.add_argument("--scope-file", default="")
    parser.add_argument("--analysis-route", default="confirmed_subfamily")
    parser.add_argument("--member-set-id", default="")
    parser.add_argument("--source-prediction-manifest", default="", help="Reference-classification manifest that produced the labels being confirmed.")
    parser.add_argument("--source-prediction-prefix", default="", help="Output prefix of the source subfamily prediction.")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    final_dir = workspace / "final"
    input_dir = Path(args.input_dir).resolve() if args.input_dir else workspace.parent.parent / "input" / workspace.name
    final_dir.mkdir(parents=True, exist_ok=True)
    scope_data = {}
    scope_file = Path(args.scope_file).expanduser() if args.scope_file else None
    if scope_file and scope_file.exists():
        scope_data = read_scope(scope_file)
    source_prediction_manifest = _read_manifest(args.source_prediction_manifest)
    source_params = source_prediction_manifest.get("parameters") if isinstance(source_prediction_manifest.get("parameters"), dict) else {}
    source_prediction_prefix = str(
        args.source_prediction_prefix
        or source_prediction_manifest.get("output_prefix")
        or source_params.get("output_prefix")
        or ""
    ).strip()
    if not scope_data and source_prediction_manifest:
        scope_data = _scope_data_from_prediction_manifest(source_prediction_manifest, args.prefix)
    output_prefix = user_output_prefix_for(
        "subfamily_confirm",
        args.prefix,
        scope_data,
        analysis_route=args.analysis_route or "confirmed_subfamily",
        member_set_id=args.member_set_id,
    ) if scope_data.get("scope_id") and not source_prediction_manifest else args.prefix
    prediction_prefix = user_output_prefix_for(
        "reference_classification",
        args.prefix,
        scope_data,
        analysis_route="subfamily_prediction",
        member_set_id=args.member_set_id,
    ) if scope_data.get("scope_id") else args.prefix
    if source_prediction_prefix:
        prediction_prefix = source_prediction_prefix

    source_path, source_type = _find_source(
        final_dir,
        input_dir,
        args.prefix,
        args.edited_subfamily,
        output_prefix=output_prefix,
        source_prefixes=[prediction_prefix],
    )
    labels = _load_labels(source_path)
    assignment_prefix = source_prediction_prefix or output_prefix
    assignment = _load_assignment_confidence(final_dir, args.prefix, output_prefix=assignment_prefix)
    for gene_id, meta in labels.items():
        if gene_id in assignment:
            meta["predicted_subfamily"] = assignment[gene_id].get("predicted_subfamily") or meta["predicted_subfamily"]
            meta["confidence"] = meta.get("confidence") or assignment[gene_id].get("confidence", "")
            meta["note"] = meta.get("note") or assignment[gene_id].get("note", "")

    if not labels:
        raise SystemExit(f"No valid confirmed subfamily labels parsed from {source_path}")

    confirmed_stem = output_prefix if output_prefix.endswith(".confirmed_subfamily") else f"{output_prefix}.confirmed_subfamily"
    confirmed_out = final_dir / f"{confirmed_stem}.tsv"
    review_out = final_dir / f"{confirmed_stem}.review.tsv"
    report_out = final_dir / f"{confirmed_stem}_report.md"
    manifest_out = final_dir / f"{output_prefix}.subfamily_confirm.manifest.json"

    with confirmed_out.open("w", encoding="utf-8") as handle:
        for gene_id in sorted(labels):
            handle.write(f"{gene_id}\t{labels[gene_id]['confirmed_subfamily']}\n")

    with review_out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            delimiter="\t",
            fieldnames=["gene_id", "predicted_subfamily", "confirmed_subfamily", "confidence", "source", "note"],
        )
        writer.writeheader()
        for gene_id in sorted(labels):
            row = labels[gene_id]
            writer.writerow({
                "gene_id": gene_id,
                "predicted_subfamily": row.get("predicted_subfamily", ""),
                "confirmed_subfamily": row.get("confirmed_subfamily", ""),
                "confidence": row.get("confidence", ""),
                "source": source_type,
                "note": row.get("note", ""),
            })

    compact_confirmed_out = None
    compact_review_out = None
    compact_prefix = _compact_confirmed_prefix(args.prefix, output_prefix, scope_data)
    current_prefix = confirmed_stem[: -len(".confirmed_subfamily")] if confirmed_stem.endswith(".confirmed_subfamily") else confirmed_stem
    if compact_prefix and compact_prefix != current_prefix:
        compact_confirmed_out = final_dir / f"{compact_prefix}.confirmed_subfamily.tsv"
        compact_review_out = final_dir / f"{compact_prefix}.confirmed_subfamily.review.tsv"
        compact_confirmed_out.write_text(confirmed_out.read_text(encoding="utf-8"), encoding="utf-8")
        compact_review_out.write_text(review_out.read_text(encoding="utf-8"), encoding="utf-8")

    member_promotions = _promote_curated_members_to_final(
        final_dir,
        _member_prefix_candidates(args.prefix, output_prefix, compact_prefix),
        labels,
    )

    counts: Dict[str, int] = {}
    for row in labels.values():
        sub = row["confirmed_subfamily"]
        counts[sub] = counts.get(sub, 0) + 1

    report_out.write_text(
        "# Confirmed Subfamily Report\n\n"
        f"- Prefix: {args.prefix}\n"
        f"- Source: {source_path}\n"
        f"- Confirmed genes: {len(labels)}\n"
        f"- Subfamilies: {', '.join(f'{k}={v}' for k, v in sorted(counts.items()))}\n",
        encoding="utf-8",
    )

    manifest = {
        "manifest_version": "1.0",
        "stage": "subfamily_confirm",
        "status": "success",
        "prefix": args.prefix,
        "output_prefix": output_prefix,
        "timestamp": datetime.now().isoformat(),
        "parameters": {
            "source": str(source_path),
            "source_type": source_type,
            "scope_file": args.scope_file,
            "source_prediction_manifest": str(Path(args.source_prediction_manifest).expanduser()) if args.source_prediction_manifest else "",
            "source_prediction_prefix": source_prediction_prefix,
            "source_prediction_scope_id": scope_data.get("scope_id", ""),
            "analysis_route": args.analysis_route or "confirmed_subfamily",
            "member_set_id": args.member_set_id,
        },
        "metrics": {
            "confirmed_genes": len(labels),
            "subfamily_counts": dict(sorted(counts.items())),
            "final_members_count": member_promotions[0]["count"] if member_promotions else 0,
        },
        "artifacts": {
            "confirmed_subfamily": {"path": f"final/{confirmed_out.name}", "full_path": str(confirmed_out), "type": "tsv"},
            "review_table": {"path": f"final/{review_out.name}", "full_path": str(review_out), "type": "tsv"},
            "report": {"path": f"final/{report_out.name}", "full_path": str(report_out), "type": "markdown"},
            "manifest": {"path": f"final/{manifest_out.name}", "full_path": str(manifest_out), "type": "json"},
        },
    }
    manifest["parameters"]["final_members_source_stage"] = "subfamily_confirm" if member_promotions else ""
    manifest["parameters"]["final_members_promoted"] = bool(member_promotions)
    if member_promotions:
        primary_promotion = member_promotions[0]
        manifest["member_promotions"] = member_promotions
        manifest["parameters"].update({
            "member_set_source_stage": "subfamily_confirm",
            "member_set_source_kind": "confirmed_final_members",
            "member_set_source_path": primary_promotion["final_id"],
            "member_count": primary_promotion["count"],
            "member_id_checksum": primary_promotion["id_checksum"],
            "member_pep_checksum": primary_promotion["pep_checksum"],
            "source_curated_path": primary_promotion["curated_id"],
            "source_curated_checksum": primary_promotion["source_curated_id_checksum"],
        })
        manifest["artifacts"]["final_members_id"] = {
            "path": f"final/{Path(primary_promotion['final_id']).name}",
            "full_path": primary_promotion["final_id"],
            "type": "id_list",
            "sha256": primary_promotion["id_checksum"],
            "members": primary_promotion["count"],
        }
        manifest["artifacts"]["final_members_pep"] = {
            "path": f"final/{Path(primary_promotion['final_pep']).name}",
            "full_path": primary_promotion["final_pep"],
            "type": "fasta",
            "sha256": primary_promotion["pep_checksum"],
            "members": primary_promotion["count"],
        }
    else:
        manifest.setdefault("warnings", []).append(
            "No curated_members.* files were found; final_members were not promoted by subfamily_confirm."
        )
    if compact_confirmed_out and compact_review_out:
        manifest["artifacts"]["compact_confirmed_subfamily"] = {
            "path": f"final/{compact_confirmed_out.name}",
            "full_path": str(compact_confirmed_out),
            "type": "tsv",
        }
        manifest["artifacts"]["compact_review_table"] = {
            "path": f"final/{compact_review_out.name}",
            "full_path": str(compact_review_out),
            "type": "tsv",
        }
    if args.scope_file:
        try:
            if not scope_data:
                scope_data = read_scope(Path(args.scope_file))
            manifest["scope_id"] = scope_data.get("scope_id", "")
            manifest["scope_mode"] = scope_data.get("scope_mode", "")
            manifest["target_species"] = scope_data.get("target_species", [])
            manifest["reference_species"] = scope_data.get("reference_species", [])
            manifest["comparative_species"] = scope_data.get("comparative_species", [])
            manifest["selected_species"] = scope_data.get("selected_species", [])
            manifest["scope"] = scope_data
        except Exception as exc:
            manifest.setdefault("warnings", []).append(f"Could not read scope file: {exc}")
    context = build_stage_context(
        stage="subfamily_confirm",
        prefix=args.prefix,
        scope_data=scope_data,
        scope_file=args.scope_file,
        output_prefix=output_prefix,
        subfamily_file=confirmed_out,
        analysis_route=args.analysis_route or "confirmed_subfamily",
        member_set_id=args.member_set_id,
    )
    apply_stage_context(manifest, context)
    manifest_out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if scope_data.get("scope_id"):
        write_legacy_manifest_copy(manifest_out, args.prefix, str(scope_data.get("scope_id") or ""), "subfamily_confirm")

    print(f"confirmed_subfamily={confirmed_out}")
    print(f"review_table={review_out}")
    if compact_confirmed_out:
        print(f"compact_confirmed_subfamily={compact_confirmed_out}")
    print(f"manifest={manifest_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
