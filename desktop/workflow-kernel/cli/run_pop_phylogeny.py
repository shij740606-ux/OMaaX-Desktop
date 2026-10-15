#!/usr/bin/env python3
"""Build a TASSEL/FastTree exploratory population tree with allele-level QC."""

from __future__ import annotations

import argparse
import gzip
import io
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from gfa_core.genotype_context import build_population_analysis_scope_id, genotype_scope_id_for_vcf, sha256_file
from gfa_core.population_stage_runtime import acquire_identity_lock, population_manifest_base, release_identity_lock, write_reproducibility_bundle


STAGE = "pop_phylogeny"
METHOD_VERSION = "tassel_phylip_fasttree_gtr.v2"
CONTRACT_VERSION = "v1"
IUPAC = {
    frozenset(("A", "G")): "R", frozenset(("C", "T")): "Y",
    frozenset(("G", "C")): "S", frozenset(("A", "T")): "W",
    frozenset(("G", "T")): "K", frozenset(("A", "C")): "M",
}


def _open_text(path: Path) -> TextIO:
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open("r", encoding="utf-8")


def _eligible(ref: str, alt: str) -> bool:
    return "," not in alt and len(ref) == len(alt) == 1 and ref.upper() in "ACGT" and alt.upper() in "ACGT"


def _decode_gt(ref: str, alt: str, sample_field: str) -> str:
    gt = sample_field.split(":", 1)[0].replace("|", "/")
    tokens = gt.split("/")
    if not tokens or any(token == "." for token in tokens):
        return "N"
    if any(token not in {"0", "1"} for token in tokens):
        raise ValueError(f"Unexpected allele index in biallelic SNP genotype: {gt}")
    bases = [ref if token == "0" else alt for token in tokens]
    return bases[0] if len(set(bases)) == 1 else IUPAC[frozenset(bases)]


def expected_alignment_from_vcf(path: Path) -> tuple[list[str], dict[str, str], dict[str, int]]:
    samples: list[str] = []
    sequences: dict[str, list[str]] = {}
    metrics = {"input_record_count": 0, "eligible_biallelic_snp_count": 0, "excluded_multiallelic_count": 0, "excluded_non_snp_count": 0}
    with _open_text(path) as handle:
        for raw in handle:
            if raw.startswith("#CHROM\t"):
                samples = raw.rstrip("\n").split("\t")[9:]
                if not samples or len(samples) != len(set(samples)):
                    raise ValueError("VCF must contain unique sample IDs")
                if any(any(char.isspace() for char in sample) for sample in samples):
                    raise ValueError("VCF sample IDs cannot contain whitespace for PHYLIP output")
                sequences = {sample: [] for sample in samples}
                continue
            if raw.startswith("#") or not raw.strip():
                continue
            fields = raw.rstrip("\n").split("\t")
            if len(fields) < 9 + len(samples):
                raise ValueError("VCF record has fewer sample columns than its header")
            metrics["input_record_count"] += 1
            ref, alt = fields[3].upper(), fields[4].upper()
            if "," in alt:
                metrics["excluded_multiallelic_count"] += 1
                continue
            if not _eligible(ref, alt):
                metrics["excluded_non_snp_count"] += 1
                continue
            metrics["eligible_biallelic_snp_count"] += 1
            for sample, value in zip(samples, fields[9:]):
                sequences[sample].append(_decode_gt(ref, alt, value))
    if len(samples) < 2:
        raise ValueError("Population phylogeny requires at least two samples")
    if metrics["eligible_biallelic_snp_count"] <= 0:
        raise ValueError("No eligible biallelic SNPs for phylogeny")
    return samples, {sample: "".join(sequences[sample]) for sample in samples}, metrics


def _write_surrogate_vcf(source: Path, destination: Path, samples: list[str]) -> dict[str, str]:
    label_map = {f"S{index:06d}": sample for index, sample in enumerate(samples, start=1)}
    with _open_text(source) as src, destination.open("w", encoding="utf-8") as dst:
        for raw in src:
            if raw.startswith("#CHROM\t"):
                fields = raw.rstrip("\n").split("\t")
                dst.write("\t".join([*fields[:9], *label_map]) + "\n")
            elif raw.startswith("#"):
                dst.write(raw)
            elif raw.strip():
                fields = raw.rstrip("\n").split("\t")
                if _eligible(fields[3].upper(), fields[4].upper()):
                    dst.write(raw)
    return label_map


def _parse_phylip(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise ValueError("PHYLIP alignment is empty")
    try:
        sample_count, site_count = map(int, lines[0].split()[:2])
    except Exception as exc:
        raise ValueError("Invalid PHYLIP header") from exc
    body = lines[1:]
    if len(body) < sample_count:
        raise ValueError("PHYLIP alignment has too few sample rows")
    order: list[str] = []
    sequences: dict[str, str] = {}
    for line in body[:sample_count]:
        fields = line.split()
        if len(fields) < 2:
            raise ValueError("Invalid PHYLIP first block")
        label, chunk = fields[0], "".join(fields[1:])
        if label in sequences:
            raise ValueError("PHYLIP labels must be unique")
        order.append(label)
        sequences[label] = chunk.upper()
    remaining = body[sample_count:]
    for offset in range(0, len(remaining), sample_count):
        block = remaining[offset : offset + sample_count]
        if len(block) != sample_count:
            raise ValueError("Incomplete PHYLIP interleaved block")
        for label, line in zip(order, block):
            fields = line.split()
            chunk = "".join(fields[1:] if fields and fields[0] == label else fields)
            sequences[label] += chunk.upper()
    if any(len(sequence) != site_count for sequence in sequences.values()):
        raise ValueError("PHYLIP sequence lengths do not match its header")
    return order, sequences


def _run(command: list[str], log: Path, *, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(command, text=True, capture_output=True, env=env)
    log.write_text("$ " + shlex.join(command) + "\n\nSTDOUT\n" + result.stdout + "\nSTDERR\n" + result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {shlex.join(command)}")


def _version(tool: Path, args: list[str]) -> str:
    result = subprocess.run([str(tool), *args], text=True, capture_output=True, timeout=30)
    text = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode != 0 or not text:
        raise RuntimeError(f"Tool version probe failed: {tool}")
    return text.splitlines()[-1]


def _tassel_version(tool: Path) -> str:
    """Support modern `-version` and the TASSEL 5.2 legacy wrapper."""
    for probe_args in (["-version"], []):
        tool_env = dict(os.environ)
        tool_env["PATH"] = f"{tool.parent}:{tool_env.get('PATH', '')}"
        result = subprocess.run([str(tool), *probe_args], text=True, capture_output=True, timeout=30, env=tool_env)
        text = (result.stdout + "\n" + result.stderr).strip()
        if result.returncode != 0 or not text:
            continue
        match = re.search(r"tassel[-_/ ](?:version[-_/ ]?)?([0-9]+(?:\.[0-9]+)+)", text, flags=re.IGNORECASE)
        version = f"TASSEL {match.group(1)}" if match else text.splitlines()[-1]
        return f"{version}; wrapper_sha256={sha256_file(tool)}"
    raise RuntimeError(f"Tool version probe failed: {tool}")


def _artifact(workspace: Path, path: Path, role: str, media_type: str) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"Required artifact is missing or empty: {role}")
    size = path.stat().st_size
    return {"artifact": role, "role": role, "path": str(path.relative_to(workspace)), "full_path": str(path.resolve()), "type": media_type, "size": size, "size_bytes": size, "sha256": sha256_file(path)}


def _newick_label(value: str) -> str:
    return value if re.fullmatch(r"[A-Za-z0-9_.-]+", value) else "'" + value.replace("'", "''") + "'"


def _publish(source: Path, destination: Path) -> None:
    temp = destination.with_name(f".{destination.name}.{os.getpid()}.publishing")
    shutil.copy2(source, temp)
    os.replace(temp, destination)


def _circular_tree_segments(tree: Any) -> list[tuple[str, float, float, float, float]]:
    """Return topology-preserving radial and arc segments for a parsed tree."""
    terminals = sorted(tree.get_terminals(), key=lambda tip: str(tip.name))
    angles = {tip: 2 * math.pi * index / len(terminals) for index, tip in enumerate(terminals)}
    for clade in tree.get_nonterminals(order="postorder"):
        child_angles = [angles[child] for child in clade.clades]
        angles[clade] = sum(child_angles) / len(child_angles)
    lengths = [clade.branch_length for clade in tree.find_clades() if clade.branch_length is not None]
    depths = tree.depths(unit_branch_lengths=not bool(lengths))
    segments: list[tuple[str, float, float, float, float]] = []
    for parent in tree.find_clades(order="preorder"):
        if not parent.clades:
            continue
        child_angles = [angles[child] for child in parent.clades]
        segments.append(("arc", min(child_angles), max(child_angles), depths[parent], depths[parent]))
        for child in parent.clades:
            segments.append(("radial", angles[child], angles[child], depths[parent], depths[child]))
    return segments


def _write_plots(newick: Path, rectangular: Path, circular: Path, expected_tips: list[str]) -> dict[str, Any]:
    from Bio import Phylo
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    tree = Phylo.read(str(newick), "newick")
    tips = [tip.name for tip in tree.get_terminals()]
    if len(tips) != len(set(tips)) or set(tips) != set(expected_tips):
        raise ValueError("Tree tip set does not match VCF samples")
    lengths = [clade.branch_length for clade in tree.find_clades() if clade.branch_length is not None]
    if any(not math.isfinite(value) or value < 0 for value in lengths):
        raise ValueError("Tree contains invalid branch lengths")
    fig = plt.figure(figsize=(9, 7))
    ax = fig.add_subplot(111)
    Phylo.draw(tree, axes=ax, do_show=False)
    fig.tight_layout()
    fig.savefig(rectangular, dpi=180)
    plt.close(fig)

    depths = tree.depths(unit_branch_lengths=not bool(lengths))
    max_depth = max((depths[tip] for tip in tree.get_terminals()), default=1.0) or 1.0
    fig = plt.figure(figsize=(8, 8))
    ax = fig.add_subplot(111, projection="polar")
    for kind, theta1, theta2, radius1, radius2 in _circular_tree_segments(tree):
        if kind == "radial":
            ax.plot([theta1, theta2], [radius1, radius2], color="#4C78A8", linewidth=1)
        else:
            steps = max(8, int(abs(theta2 - theta1) * 32))
            theta = [theta1 + (theta2 - theta1) * index / steps for index in range(steps + 1)]
            ax.plot(theta, [radius1] * len(theta), color="#4C78A8", linewidth=1)
    tip_angles = {tip: 2 * math.pi * index / len(tips) for index, tip in enumerate(sorted(tree.get_terminals(), key=lambda item: str(item.name)))}
    for tip in tree.get_terminals():
        ax.text(tip_angles[tip], depths.get(tip, max_depth) + max_depth * 0.05, tip.name, fontsize=7, ha="center", va="center")
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(circular, dpi=180)
    plt.close(fig)
    return {"tip_count": len(tips), "tip_set_matches_vcf": True, "branch_lengths_finite": True}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--vcf", required=True, type=Path)
    parser.add_argument("--tassel", default="run_pipeline.pl", type=Path)
    parser.add_argument("--fasttree", default="FastTree", type=Path)
    parser.add_argument("--java-memory", default="5G")
    return parser


def _resolve_tool(value: Path) -> Path:
    candidate = value.resolve() if value.is_absolute() or value.exists() else Path(shutil.which(str(value)) or "")
    if not candidate.is_file():
        raise FileNotFoundError(f"Executable not found: {value}")
    return candidate


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = args.workspace.resolve()
    final = workspace / "final"
    final.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    clock = time.monotonic()
    run_id = f"{STAGE}_{started.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    failed = final / f"{args.prefix}.{STAGE}.failed.{run_id}.manifest.json"
    identity12 = "unresolved"
    lock_handle = None
    runner_command = [sys.executable, "-m", "cli.run_pop_phylogeny", *(argv if argv is not None else sys.argv[1:])]
    parameters = {"marker_policy": "biallelic_snp", "heterozygote_policy": "iupac", "missing_symbol": "N", "fasttree_model": "gtr", "java_memory": args.java_memory}
    manifest = population_manifest_base(
        workspace=workspace, stage=STAGE, prefix=args.prefix, job_id=args.job_id,
        run_id=run_id, started=started, runner_command=runner_command,
        parameters=parameters,
    )
    try:
        vcf = args.vcf.resolve()
        samples, _expected, marker_metrics = expected_alignment_from_vcf(vcf)
        tassel, fasttree = _resolve_tool(args.tassel), _resolve_tool(args.fasttree)
        versions = {"tassel": _tassel_version(tassel), "fasttree": _version(fasttree, ["-help"]), "python": platform.python_version()}
        genotype_scope = genotype_scope_id_for_vcf(vcf)
        input_hashes = {"population_vcf": sha256_file(vcf)}
        analysis_scope = build_population_analysis_scope_id(stage=STAGE, genotype_scope_id=genotype_scope, input_artifact_hashes=input_hashes, parameters=parameters, method_version=METHOD_VERSION, contract_version=CONTRACT_VERSION, tool_versions=versions)
        identity12 = analysis_scope.split(":", 1)[1][:12]
        lock_handle = acquire_identity_lock(workspace, STAGE, args.prefix, identity12)
        manifest.update({"genotype_scope_id": genotype_scope, "analysis_scope_id": analysis_scope, "method_version": METHOD_VERSION, "contract_version": CONTRACT_VERSION, "tool_versions": versions, "inputs": [{"role": "population_vcf", "sha256": input_hashes["population_vcf"]}]})
        attempt = workspace / ".attempts" / run_id
        attempt.mkdir(parents=True, exist_ok=False)
        filtered = attempt / "biallelic_surrogate.vcf"
        label_map = _write_surrogate_vcf(vcf, filtered, samples)
        sorted_vcf = attempt / "clean.sorted.vcf.gz"
        tassel_alignment = attempt / "tassel_alignment.phy"
        sort_command = [str(tassel), f"-Xmx{args.java_memory}", "-SortGenotypeFilePlugin", "-inputFile", str(filtered), "-outputFile", str(sorted_vcf), "-fileType", "VCF"]
        export_command = [str(tassel), f"-Xmx{args.java_memory}", "-importGuess", str(sorted_vcf), "-ExportPlugin", "-saveAs", str(tassel_alignment), "-format", "Phylip_Inter"]
        tassel_env = dict(os.environ); tassel_env["PATH"] = f"{tassel.parent}:{tassel_env.get('PATH', '')}"
        _run(sort_command, attempt / "tassel_sort.log", env=tassel_env)
        _run(export_command, attempt / "tassel_export.log", env=tassel_env)
        surrogate_samples, sorted_expected, _ = expected_alignment_from_vcf(sorted_vcf)
        alignment_order, observed = _parse_phylip(tassel_alignment)
        if alignment_order != surrogate_samples or observed != sorted_expected:
            raise ValueError("TASSEL PHYLIP alignment does not match VCF REF/ALT genotypes")
        if set(alignment_order) != set(label_map):
            raise ValueError("TASSEL label set does not match the reversible label map")
        restored_alignment = attempt / "alignment.phy"
        restored_alignment.write_text(
            f"{len(samples)} {len(next(iter(observed.values())))}\n" + "".join(f"{label_map[label]} {observed[label]}\n" for label in alignment_order),
            encoding="utf-8",
        )
        newick = attempt / "tree.nwk"
        # Keep collision-free surrogate labels through FastTree.  Numeric or long
        # original labels are restored only in Newick, after tree construction.
        tree_command = [str(fasttree), "-nt", "-gtr", str(tassel_alignment)]
        tree_result = subprocess.run(tree_command, text=True, capture_output=True)
        (attempt / "fasttree.log").write_text("$ " + shlex.join(tree_command) + "\n\nSTDERR\n" + tree_result.stderr, encoding="utf-8")
        if tree_result.returncode != 0 or not tree_result.stdout.strip():
            raise RuntimeError(f"FastTree failed with exit code {tree_result.returncode}")
        restored_newick = tree_result.stdout.strip()
        for surrogate, original in label_map.items():
            restored_newick = restored_newick.replace(surrogate, _newick_label(original))
        newick.write_text(restored_newick + "\n", encoding="utf-8")
        rectangular, circular = attempt / "tree_rectangular.png", attempt / "tree_circular.png"
        tree_metrics = _write_plots(newick, rectangular, circular, samples)
        alignment_qc = attempt / "alignment_qc.json"
        alignment_metrics = {**marker_metrics, "sample_count": len(samples), "alignment_site_count": len(next(iter(observed.values()))), "label_map": label_map, "alignment_matches_vcf": True, "valid_characters": sorted(set("".join(observed.values())))}
        alignment_qc.write_text(json.dumps(alignment_metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tree_qc = attempt / "tree_qc.json"
        tree_qc.write_text(json.dumps(tree_metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report = attempt / "report.md"
        report.write_text("# Population phylogeny\n\n- Method: biallelic SNP VCF → TASSEL Phylip_Inter → FastTree `-nt -gtr`.\n- Genotypes were independently checked against REF/ALT with IUPAC heterozygotes.\n- Scope: exploratory SNP-only tree; not ascertainment-corrected publication phylogeny.\n", encoding="utf-8")
        environment = attempt / "environment.txt"
        environment.write_text(f"python={sys.executable}\ntassel={tassel}\ntassel_version={versions['tassel']}\nfasttree={fasttree}\nfasttree_version={versions['fasttree']}\n", encoding="utf-8")
        outputs = {
            "phylogeny_alignment_phylip": (restored_alignment, "alignment.phy", "text/plain"),
            "phylogeny_newick": (newick, "tree.nwk", "text/plain"),
            "phylogeny_rectangular_plot": (rectangular, "rectangular.png", "image/png"),
            "phylogeny_circular_plot": (circular, "circular.png", "image/png"),
            "alignment_qc": (alignment_qc, "alignment_qc.json", "application/json"),
            "tree_qc": (tree_qc, "tree_qc.json", "application/json"),
            "report": (report, "report.md", "text/markdown"),
            "environment": (environment, "environment.txt", "text/plain"),
        }
        stem = f"{args.prefix}.{STAGE}.{identity12}.{run_id}"
        published: dict[str, tuple[Path, str]] = {}
        for role, (source, suffix, media_type) in outputs.items():
            destination = final / f"{stem}.{suffix}"
            _publish(source, destination)
            published[role] = (destination, media_type)
        manifest.update({"status": "success", "metrics": {**alignment_metrics, **tree_metrics}, "commands": [sort_command, export_command, tree_command], "artifacts": {role: _artifact(workspace, path, role, media_type) for role, (path, media_type) in published.items()}})
        manifest["end_time"] = datetime.now(timezone.utc).isoformat()
        manifest["elapsed_sec"] = round(time.monotonic() - clock, 6)
        manifest_work = attempt / "manifest.json"
        _write = lambda p, value: p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _write(manifest_work, manifest)
        manifest_path = final / f"{stem}.manifest.json"
        write_reproducibility_bundle(
            workspace=workspace, stage=STAGE, prefix=args.prefix, identity12=identity12,
            run_id=run_id, runner_command=runner_command, manifest_source=manifest_work,
            public_manifest=manifest_path, manifest=manifest,
            environment=environment.read_text(encoding="utf-8"),
            artifact_sources={role: source for role, (source, _suffix, _media) in outputs.items()},
        )
        _publish(manifest_work, manifest_path)
        return 0
    except Exception as exc:
        manifest.update({"status": "failed", "artifacts": {}, "error": str(exc), "end_time": datetime.now(timezone.utc).isoformat(), "elapsed_sec": round(time.monotonic() - clock, 6)})
        failed.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            write_reproducibility_bundle(
                workspace=workspace, stage=STAGE, prefix=args.prefix, identity12=identity12,
                run_id=run_id, runner_command=runner_command, manifest_source=failed,
                public_manifest=failed, manifest=manifest,
                environment=json.dumps(manifest.get("tool_versions", {}), ensure_ascii=False),
            )
        except Exception as bundle_exc:
            manifest["reproducibility_error"] = str(bundle_exc)
            failed.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        release_identity_lock(lock_handle)


if __name__ == "__main__":
    raise SystemExit(main())
