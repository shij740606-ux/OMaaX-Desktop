#!/usr/bin/env python3
"""Assign target-species subfamilies from a reference-labelled joint tree."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from Bio import Phylo

TOPOLOGY_SUPPORT_BOOTSTRAP = 70.0
STRONG_TOPOLOGY_BOOTSTRAP = 90.0


def read_reference_subfamily(path: Path, reference_prefix: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            gene, subfamily = parts[0], parts[1]
            labels[gene] = subfamily
            labels[gene.lower()] = subfamily
            labels[f"{reference_prefix}|{gene}"] = subfamily
            labels[f"{reference_prefix}|{gene}".lower()] = subfamily
    return labels


def lookup_subfamily(labels: dict[str, str], tip_name: str, default: str = "Reference_unclassified") -> str:
    if tip_name in labels:
        return labels[tip_name]
    lowered = tip_name.lower()
    if lowered in labels:
        return labels[lowered]
    if "|" in tip_name:
        gene = tip_name.split("|", 1)[1]
        if gene in labels:
            return labels[gene]
        if gene.lower() in labels:
            return labels[gene.lower()]
    return default


def is_reference_tip(tip_name: str, labels: dict[str, str], reference_prefix: str) -> bool:
    if tip_name.startswith(f"{reference_prefix}|"):
        return True
    return lookup_subfamily(labels, tip_name, default="") != ""


def bootstrap_value(clade) -> float | None:
    for value in (getattr(clade, "confidence", None), getattr(clade, "name", None)):
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True, type=Path)
    parser.add_argument("--reference-subfamily", required=True, type=Path)
    parser.add_argument("--target-prefix", default="Ft")
    parser.add_argument("--reference-prefix", default="At")
    parser.add_argument("--assignment-out", required=True, type=Path)
    parser.add_argument("--subfamily-out", required=True, type=Path)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--report-out", type=Path)
    args = parser.parse_args()

    ref_subfamily = read_reference_subfamily(args.reference_subfamily, args.reference_prefix)
    tree = Phylo.read(args.tree, "newick")
    terminals = tree.get_terminals()
    reference_tips = [
        t for t in terminals
        if is_reference_tip(t.name or "", ref_subfamily, args.reference_prefix)
    ]
    target_tips = [t for t in terminals if (t.name or "").startswith(f"{args.target_prefix}|")]
    if not target_tips:
        reference_names = {id(t) for t in reference_tips}
        target_tips = [t for t in terminals if id(t) not in reference_names]

    if not target_tips:
        raise SystemExit(
            f"No target tips found. Tree has no tips with prefix {args.target_prefix}| "
            "and no non-reference tips after reference detection."
        )
    if not reference_tips:
        raise SystemExit(
            f"No reference tips found. The tree must contain tips with prefix {args.reference_prefix}| "
            "or gene IDs present in the reference subfamily file."
        )

    parent: dict[object, object | None] = {tree.root: None}
    for clade in tree.find_clades(order="level"):
        for child in clade.clades:
            parent[child] = clade

    assignments: list[dict[str, str]] = []
    subfamily_rows: list[tuple[str, str]] = []

    for ref in sorted(reference_tips, key=lambda item: item.name or ""):
        subfamily_rows.append((ref.name, lookup_subfamily(ref_subfamily, ref.name or "")))

    for target in sorted(target_tips, key=lambda item: item.name or ""):
        distances = sorted(
            ((tree.distance(target, ref), ref.name, lookup_subfamily(ref_subfamily, ref.name or "")) for ref in reference_tips),
            key=lambda item: (item[0], item[1]),
        )
        nearest_distance, nearest_ref, nearest_subfamily = distances[0]
        nearest3 = distances[:3]
        nearest3_counts = Counter(item[2] for item in nearest3)
        nearest3_label, nearest3_count = nearest3_counts.most_common(1)[0]

        mixed_clade = parent.get(target)
        mixed_ref_subfamilies: list[str] = []
        mixed_bootstrap: float | None = None
        while mixed_clade is not None:
            mixed_refs = [tip for tip in mixed_clade.get_terminals() if (tip.name or "").startswith(f"{args.reference_prefix}|")]
            if not mixed_refs:
                mixed_refs = [
                    tip for tip in mixed_clade.get_terminals()
                    if is_reference_tip(tip.name or "", ref_subfamily, args.reference_prefix)
                ]
            if mixed_refs:
                mixed_ref_subfamilies = sorted({lookup_subfamily(ref_subfamily, tip.name or "") for tip in mixed_refs})
                mixed_bootstrap = bootstrap_value(mixed_clade)
                break
            mixed_clade = parent.get(mixed_clade)

        topology_subfamily = mixed_ref_subfamilies[0] if len(mixed_ref_subfamilies) == 1 else ""
        topology_supported = (
            bool(topology_subfamily)
            and mixed_bootstrap is not None
            and mixed_bootstrap >= TOPOLOGY_SUPPORT_BOOTSTRAP
        )

        if topology_subfamily and topology_subfamily == nearest_subfamily:
            if mixed_bootstrap is not None and mixed_bootstrap >= STRONG_TOPOLOGY_BOOTSTRAP:
                confidence = "high"
                note = "single_reference_subfamily_clade_strong_bootstrap"
            elif topology_supported:
                confidence = "medium"
                note = "single_reference_subfamily_clade_supported_bootstrap"
            else:
                confidence = "medium"
                note = "single_reference_subfamily_clade_low_or_missing_bootstrap"
            assigned = topology_subfamily
        elif topology_supported and topology_subfamily:
            confidence = "medium"
            note = "topology_conflict_nearest_reference_supported_clade"
            assigned = topology_subfamily
        elif nearest3_label == nearest_subfamily and nearest3_count >= 2:
            confidence = "medium"
            note = "nearest3_majority_support"
            assigned = nearest_subfamily
        else:
            confidence = "low"
            note = "nearest_reference_only_mixed_clade"
            assigned = nearest_subfamily

        assignments.append(
            {
                "target_gene": target.name,
                "assigned_subfamily": assigned,
                "confidence": confidence,
                "nearest_reference": nearest_ref,
                "nearest_reference_subfamily": nearest_subfamily,
                "nearest_reference_distance": f"{nearest_distance:.6g}",
                "nearest3_references": ";".join(f"{name}:{sub}:{dist:.6g}" for dist, name, sub in nearest3),
                "minimal_mixed_clade_ref_subfamilies": ";".join(mixed_ref_subfamilies) if mixed_ref_subfamilies else "NA",
                "minimal_mixed_clade_bootstrap": "NA" if mixed_bootstrap is None else f"{mixed_bootstrap:.6g}",
                "topology_reference_subfamily": topology_subfamily or "NA",
                "topology_bootstrap": "NA" if mixed_bootstrap is None else f"{mixed_bootstrap:.6g}",
                "nearest3_majority_subfamily": nearest3_label,
                "nearest3_majority_count": str(nearest3_count),
                "classification_conflict": "yes" if topology_supported and topology_subfamily != nearest_subfamily else "no",
                "classification_note": note,
            }
        )
        subfamily_rows.append((target.name, assigned))

    args.assignment_out.parent.mkdir(parents=True, exist_ok=True)
    with args.assignment_out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(assignments[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(assignments)

    with args.subfamily_out.open("w", encoding="utf-8") as handle:
        for label, subfamily in sorted(subfamily_rows):
            handle.write(f"{label}\t{subfamily}\n")

    assignment_counts = Counter(row["assigned_subfamily"] for row in assignments)
    confidence_counts = Counter(row["confidence"] for row in assignments)
    note_counts = Counter(row["classification_note"] for row in assignments)

    manifest_out = args.manifest_out
    if manifest_out is None:
        manifest_out = args.assignment_out.with_name(
            args.assignment_out.name.replace(".assignment.tsv", ".manifest.json")
        )
    report_out = args.report_out
    if report_out is None:
        report_out = args.assignment_out.with_name(
            args.assignment_out.name.replace(".assignment.tsv", "_report.md")
        )

    def final_artifact_path(path: Path) -> str:
        if path.parent.name == "final":
            return f"final/{path.name}"
        return str(path)

    manifest = {
        "manifest_version": "1.0",
        "stage": "reference_classification",
        "status": "success",
        "prefix": args.assignment_out.name.split(".reference_classification.assignment.tsv")[0],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "tree": str(args.tree),
            "reference_subfamily": str(args.reference_subfamily),
            "target_species": args.target_prefix,
            "reference_species": args.reference_prefix,
        },
        "artifacts": {
            "assignment_table": {"path": final_artifact_path(args.assignment_out), "type": "tsv"},
            "subfamily_file": {"path": final_artifact_path(args.subfamily_out), "type": "tsv"},
            "report": {"path": final_artifact_path(report_out), "type": "markdown"},
        },
        "metrics": {
            "target_gene_count": len(target_tips),
            "reference_gene_count": len(reference_tips),
            "total_tree_tips": len(terminals),
            "confidence_counts": dict(sorted(confidence_counts.items())),
            "assigned_subfamily_counts": dict(sorted(assignment_counts.items())),
            "classification_note_counts": dict(sorted(note_counts.items())),
        },
        "parameters": {
            "assignment_rule": (
                "a supported local topology clade containing a single reference subfamily "
                f"(bootstrap >= {TOPOLOGY_SUPPORT_BOOTSTRAP:g}) is prioritized over nearest-reference "
                "distance; nearest3 majority is used only when no supported single-subfamily topology "
                "clade is available. classification_note and classification_conflict expose conflicts."
            ),
            "script": "scripts/reference_classification_from_tree.py",
        },
    }
    manifest_out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    report_lines = [
        "# Reference-Guided Subfamily Classification",
        "",
        "## Summary",
        "",
        f"- Target genes classified: {len(target_tips)}",
        f"- Reference genes: {len(reference_tips)}",
        f"- Total joint-tree tips: {len(terminals)}",
        "",
        "## Confidence Counts",
        "",
        "| Confidence | Ft genes |",
        "|---|---:|",
    ]
    for label, count in sorted(confidence_counts.items()):
        report_lines.append(f"| {label} | {count} |")
    report_lines.extend(["", "## Assigned Subfamily Counts", "", "| Assigned subfamily | Ft genes |", "|---|---:|"])
    for label, count in sorted(assignment_counts.items()):
        report_lines.append(f"| {label} | {count} |")
    report_lines.extend(["", "## Rule", ""])
    report_lines.append(
        "A local tree clade containing a single At reference subfamily is used as the primary "
        f"assignment evidence when its bootstrap is >= {TOPOLOGY_SUPPORT_BOOTSTRAP:g}. "
        "Nearest-reference and nearest3 evidence are retained in the assignment table and used "
        "as fallback evidence when the local topology is mixed or weakly supported."
    )
    report_out.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(f"target_tips={len(target_tips)}")
    print(f"reference_tips={len(reference_tips)}")
    print(f"assignment_out={args.assignment_out}")
    print(f"subfamily_out={args.subfamily_out}")
    print(f"manifest_out={manifest_out}")
    print(f"report_out={report_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
