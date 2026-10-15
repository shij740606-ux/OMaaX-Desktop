#!/usr/bin/env python3
"""Run LD-pruned PLINK PCA with fail-closed population-analysis publishing."""

from __future__ import annotations

import argparse
import gzip
import json
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

from gfa_core.genotype_context import (
    build_population_analysis_scope_id,
    genotype_scope_id_for_vcf,
    sha256_file,
)
from gfa_core.population_stage_runtime import acquire_identity_lock, population_manifest_base, release_identity_lock, write_reproducibility_bundle


STAGE = "pop_pca"
METHOD_VERSION = "plink_ld_pruned_pca.v1"
CONTRACT_VERSION = "v1"


def _open_text(path: Path) -> TextIO:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def scan_vcf_marker_policy(path: Path) -> tuple[list[str], dict[str, int]]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise FileNotFoundError(f"Population VCF is missing or empty: {path}")
    samples: list[str] = []
    metrics = {
        "input_record_count": 0,
        "eligible_biallelic_snp_count": 0,
        "excluded_multiallelic_count": 0,
        "excluded_non_snp_count": 0,
    }
    with _open_text(path) as handle:
        for raw in handle:
            if raw.startswith("#CHROM\t"):
                samples = raw.rstrip("\n").split("\t")[9:]
                continue
            if raw.startswith("#") or not raw.strip():
                continue
            fields = raw.rstrip("\n").split("\t")
            if len(fields) < 5:
                raise ValueError("VCF record has fewer than five columns")
            metrics["input_record_count"] += 1
            ref, alt = fields[3].upper(), fields[4].upper()
            if "," in alt:
                metrics["excluded_multiallelic_count"] += 1
            elif len(ref) != 1 or len(alt) != 1 or ref not in "ACGT" or alt not in "ACGT":
                metrics["excluded_non_snp_count"] += 1
            else:
                metrics["eligible_biallelic_snp_count"] += 1
    if len(samples) < 2:
        raise ValueError("Population PCA requires at least two VCF samples")
    if len(samples) != len(set(samples)):
        raise ValueError("VCF sample IDs must be unique")
    if metrics["eligible_biallelic_snp_count"] <= 0:
        raise ValueError("No eligible biallelic single-nucleotide SNPs")
    return samples, metrics


def validate_sample_group(path: Path, vcf_samples: list[str]) -> dict[str, str]:
    groups: dict[str, str] = {}
    allowed = set(vcf_samples)
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) != 2 or not fields[1]:
                raise ValueError(f"sample_group row {line_number} must have exactly two columns")
            sample, group = fields
            if sample in groups:
                raise ValueError("sample_group sample IDs must be unique")
            if sample not in allowed:
                raise ValueError(f"sample_group ID not present in VCF: {sample}")
            groups[sample] = group
    if not groups:
        raise ValueError("sample_group is empty")
    return groups


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _artifact(workspace: Path, path: Path, role: str, media_type: str) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"Required artifact is missing or empty: {role}")
    size = path.stat().st_size
    return {
        "artifact": role,
        "role": role,
        "path": str(path.relative_to(workspace)),
        "full_path": str(path.resolve()),
        "type": media_type,
        "size": size,
        "size_bytes": size,
        "sha256": sha256_file(path),
    }


def _publish(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.publishing")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def _tool_version(executable: Path) -> str:
    result = subprocess.run([str(executable), "--version"], text=True, capture_output=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"PLINK version probe failed ({result.returncode}): {result.stderr.strip()}")
    return (result.stdout or result.stderr).strip().splitlines()[0]


def _run(command: list[str], log_path: Path) -> None:
    result = subprocess.run(command, text=True, capture_output=True)
    log_path.write_text(
        "$ " + shlex.join(command) + "\n\nSTDOUT\n" + result.stdout + "\nSTDERR\n" + result.stderr,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {shlex.join(command)}")


def _parse_pca(eigenvec: Path, eigenval: Path, expected_samples: list[str]) -> tuple[list[str], list[list[str]], list[float]]:
    lines = [line.split() for line in eigenvec.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise ValueError("PLINK PCA eigenvector file is empty")
    if lines[0][0].lstrip("#") == "FID":
        header, rows = [lines[0][0].lstrip("#"), *lines[0][1:]], lines[1:]
    else:
        rows = lines
        header = ["FID", "IID", *(f"PC{i}" for i in range(1, len(rows[0]) - 1))]
    if any(len(row) != len(header) for row in rows):
        raise ValueError("PLINK PCA rows have inconsistent columns")
    sample_ids = [row[1] for row in rows]
    if sample_ids != expected_samples:
        raise ValueError("PCA sample order does not exactly match the VCF")
    eigenvalues = [float(value) for value in eigenval.read_text(encoding="utf-8").split()]
    if len(eigenvalues) != len(header) - 2 or any(value < 0 for value in eigenvalues):
        raise ValueError("PCA eigenvalue count does not match eigenvectors")
    return header, rows, eigenvalues


def _write_outputs(
    attempt: Path,
    header: list[str],
    rows: list[list[str]],
    eigenvalues: list[float],
    groups: dict[str, str],
    requested_plot_pcs: int,
    tassel_covariate_pcs: int,
) -> dict[str, Path]:
    scores = attempt / "pca_scores.tsv"
    scores.write_text("\t".join(header) + "\n" + "\n".join("\t".join(row) for row in rows) + "\n", encoding="utf-8")
    eigen = attempt / "pca_eigenvalues.tsv"
    eigen.write_text("PC\teigenvalue\n" + "".join(f"PC{i}\t{value:.15g}\n" for i, value in enumerate(eigenvalues, 1)), encoding="utf-8")
    total = sum(eigenvalues)
    if total <= 0:
        raise ValueError("PCA eigenvalues have no positive total")
    variance = attempt / "pca_variance.tsv"
    cumulative = 0.0
    variance_lines = ["PC\teigenvalue\tvariance_percent\tcumulative_percent"]
    for index, value in enumerate(eigenvalues, 1):
        percent = 100.0 * value / total
        cumulative += percent
        variance_lines.append(f"PC{index}\t{value:.15g}\t{percent:.12g}\t{cumulative:.12g}")
    variance.write_text("\n".join(variance_lines) + "\n", encoding="utf-8")

    covariate_count = min(tassel_covariate_pcs, len(eigenvalues))
    covariate = attempt / "pca_tassel_covariate.txt"
    cov_lines = ["<Covariate>", "<Trait>\t" + "\t".join(f"PC{i}" for i in range(1, covariate_count + 1))]
    for row in rows:
        cov_lines.append("\t".join([row[1], *row[2 : 2 + covariate_count]]))
    covariate.write_text("\n".join(cov_lines) + "\n", encoding="utf-8")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if len(eigenvalues) < 2:
        raise ValueError("At least two computed PCs are required for the PC1/PC2 plot")
    pc1 = [float(row[2]) for row in rows]
    pc2 = [float(row[3]) for row in rows]
    scatter = attempt / "pca_pc1_pc2.png"
    fig, ax = plt.subplots(figsize=(7, 6))
    labels = [groups.get(row[1], "Unassigned") for row in rows]
    for label in sorted(set(labels)):
        indices = [i for i, value in enumerate(labels) if value == label]
        ax.scatter([pc1[i] for i in indices], [pc2[i] for i in indices], label=label)
    ax.set_xlabel("PC1")
    ax.set_ylabel("PC2")
    if groups:
        ax.legend()
    fig.tight_layout()
    fig.savefig(scatter, dpi=180)
    plt.close(fig)

    scree = attempt / "pca_scree.png"
    fig, ax = plt.subplots(figsize=(7, 5))
    percentages = [100.0 * value / total for value in eigenvalues[:requested_plot_pcs]]
    ax.bar(range(1, len(percentages) + 1), percentages)
    ax.set_xlabel("Principal component")
    ax.set_ylabel("Variance explained (%)")
    fig.tight_layout()
    fig.savefig(scree, dpi=180)
    plt.close(fig)
    return {
        "pca_scores": scores,
        "pca_eigenvalues": eigen,
        "pca_variance_table": variance,
        "pca_pc1_pc2_plot": scatter,
        "pca_scree_plot": scree,
        "pca_tassel_covariate": covariate,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--vcf", required=True, type=Path)
    parser.add_argument("--sample-group", type=Path)
    parser.add_argument("--plink", default="plink", type=Path)
    parser.add_argument("--ld-window-variants", default=50, type=int)
    parser.add_argument("--ld-step-variants", default=10, type=int)
    parser.add_argument("--ld-r2", default=0.2, type=float)
    parser.add_argument("--requested-plot-pcs", default=10, type=int)
    parser.add_argument("--tassel-covariate-pcs", default=3, type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = args.workspace.resolve()
    final = workspace / "final"
    final.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    start_clock = time.monotonic()
    run_id = f"{STAGE}_{started.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    failed_manifest = final / f"{args.prefix}.{STAGE}.failed.{run_id}.manifest.json"
    identity12 = "unresolved"
    lock_handle = None
    runner_command = [sys.executable, "-m", "cli.run_pop_pca", *(argv if argv is not None else sys.argv[1:])]
    parameters = {
        "marker_policy": "biallelic_snp",
        "ld_window_variants": args.ld_window_variants,
        "ld_step_variants": args.ld_step_variants,
        "ld_r2": args.ld_r2,
        "requested_plot_pcs": args.requested_plot_pcs,
        "tassel_covariate_pcs": args.tassel_covariate_pcs,
    }
    manifest = population_manifest_base(
        workspace=workspace, stage=STAGE, prefix=args.prefix, job_id=args.job_id,
        run_id=run_id, started=started, runner_command=runner_command,
        parameters=parameters,
    )
    try:
        vcf = args.vcf.resolve()
        samples, marker_metrics = scan_vcf_marker_policy(vcf)
        groups = validate_sample_group(args.sample_group.resolve(), samples) if args.sample_group else {}
        if args.ld_window_variants <= 0 or args.ld_step_variants <= 0 or not 0 < args.ld_r2 <= 1:
            raise ValueError("Invalid LD pruning parameters")
        if args.tassel_covariate_pcs <= 0 or args.requested_plot_pcs <= 0:
            raise ValueError("requested_plot_pcs and tassel_covariate_pcs must be positive")
        plink = args.plink.resolve() if args.plink.is_absolute() or args.plink.exists() else Path(shutil.which(str(args.plink)) or "")
        if not plink.is_file():
            raise FileNotFoundError(f"PLINK executable not found: {args.plink}")
        plink_version = _tool_version(plink)
        genotype_scope = genotype_scope_id_for_vcf(vcf)
        input_hashes = {"population_vcf": sha256_file(vcf)}
        if args.sample_group:
            input_hashes["sample_group"] = sha256_file(args.sample_group.resolve())
        analysis_scope = build_population_analysis_scope_id(
            stage=STAGE, genotype_scope_id=genotype_scope,
            input_artifact_hashes=input_hashes, parameters=parameters,
            method_version=METHOD_VERSION, contract_version=CONTRACT_VERSION,
            tool_versions={"plink": plink_version, "python": platform.python_version()},
        )
        identity12 = analysis_scope.split(":", 1)[1][:12]
        lock_handle = acquire_identity_lock(workspace, STAGE, args.prefix, identity12)
        manifest.update({
            "genotype_scope_id": genotype_scope, "analysis_scope_id": analysis_scope,
            "method_version": METHOD_VERSION, "contract_version": CONTRACT_VERSION,
            "tool_versions": {"plink": plink_version, "python": platform.python_version()},
            "inputs": [{"role": role, "sha256": digest} for role, digest in input_hashes.items()],
        })
        attempt = workspace / ".attempts" / run_id
        attempt.mkdir(parents=True, exist_ok=False)
        prune_prefix = attempt / "prune"
        common = [str(plink), "--vcf", str(vcf), "--double-id", "--allow-extra-chr", "--biallelic-only", "strict", "--snps-only", "just-acgt", "--set-missing-var-ids", "@:#:$1:$2"]
        prune_command = [*common, "--indep-pairwise", str(args.ld_window_variants), str(args.ld_step_variants), str(args.ld_r2), "--out", str(prune_prefix)]
        _run(prune_command, attempt / "plink_prune.log")
        prune_in = prune_prefix.with_suffix(".prune.in")
        markers = [line.strip() for line in prune_in.read_text(encoding="utf-8").splitlines() if line.strip()] if prune_in.is_file() else []
        if not markers:
            raise ValueError("PLINK LD pruning retained zero markers")
        pc_count = min(len(samples) - 1, len(markers))
        if pc_count < 2:
            raise ValueError("Fewer than two PCs are available after LD pruning")
        pca_prefix = attempt / "pca"
        pca_command = [*common, "--extract", str(prune_in), "--pca", str(pc_count), "header", "tabs", "--out", str(pca_prefix)]
        _run(pca_command, attempt / "plink_pca.log")
        header, rows, eigenvalues = _parse_pca(pca_prefix.with_suffix(".eigenvec"), pca_prefix.with_suffix(".eigenval"), samples)
        outputs = _write_outputs(
            attempt, header, rows, eigenvalues, groups,
            args.requested_plot_pcs, args.tassel_covariate_pcs,
        )
        outputs["pca_pruning_markers"] = prune_in
        environment = attempt / "environment.txt"
        environment.write_text(
            f"runner=cli.run_pop_pca.py\npython={sys.executable}\npython_version={platform.python_version()}\nplink={plink}\nplink_version={plink_version}\n",
            encoding="utf-8",
        )
        outputs["environment"] = environment
        report = attempt / "report.md"
        report.write_text(
            "# Population PCA\n\n"
            f"- Samples: {len(samples)}\n- Eligible biallelic SNPs: {marker_metrics['eligible_biallelic_snp_count']}\n"
            f"- LD-pruned markers: {len(markers)}\n- Computed PCs: {len(eigenvalues)}\n"
            "- Validation scope: program/flow output; biological inference depends on cohort adequacy.\n",
            encoding="utf-8",
        )
        outputs["report"] = report
        stem = f"{args.prefix}.{STAGE}.{identity12}.{run_id}"
        media_types = {
            "pca_scores": "text/tab-separated-values", "pca_eigenvalues": "text/tab-separated-values",
            "pca_variance_table": "text/tab-separated-values", "pca_pruning_markers": "text/plain",
            "pca_pc1_pc2_plot": "image/png", "pca_scree_plot": "image/png",
            "pca_tassel_covariate": "text/tab-separated-values", "report": "text/markdown",
            "environment": "text/plain",
        }
        suffixes = {
            "pca_scores": "scores.tsv", "pca_eigenvalues": "eigenvalues.tsv", "pca_variance_table": "variance.tsv",
            "pca_pruning_markers": "prune.in", "pca_pc1_pc2_plot": "pc1_pc2.png", "pca_scree_plot": "scree.png",
            "pca_tassel_covariate": "tassel_covariate.txt", "report": "report.md", "environment": "environment.txt",
        }
        published: dict[str, Path] = {}
        for role, source in outputs.items():
            destination = final / f"{stem}.{suffixes[role]}"
            _publish(source, destination)
            published[role] = destination
        manifest.update({
            "status": "success",
            "metrics": {**marker_metrics, "sample_count": len(samples), "grouped_sample_count": len(groups), "pruned_marker_count": len(markers), "computed_pc_count": len(eigenvalues)},
            "commands": [prune_command, pca_command],
            "artifacts": {role: _artifact(workspace, path, role, media_types[role]) for role, path in published.items()},
        })
        manifest["end_time"] = datetime.now(timezone.utc).isoformat()
        manifest["elapsed_sec"] = round(time.monotonic() - start_clock, 6)
        manifest_work = attempt / "manifest.json"
        _write_json(manifest_work, manifest)
        manifest_path = final / f"{stem}.manifest.json"
        write_reproducibility_bundle(
            workspace=workspace, stage=STAGE, prefix=args.prefix, identity12=identity12,
            run_id=run_id, runner_command=runner_command, manifest_source=manifest_work,
            public_manifest=manifest_path, manifest=manifest,
            environment=environment.read_text(encoding="utf-8"), artifact_sources=outputs,
        )
        _publish(manifest_work, manifest_path)
        return 0
    except Exception as exc:
        manifest.update({"status": "failed", "artifacts": {}, "error": str(exc), "end_time": datetime.now(timezone.utc).isoformat(), "elapsed_sec": round(time.monotonic() - start_clock, 6)})
        _write_json(failed_manifest, manifest)
        try:
            write_reproducibility_bundle(
                workspace=workspace, stage=STAGE, prefix=args.prefix, identity12=identity12,
                run_id=run_id, runner_command=runner_command, manifest_source=failed_manifest,
                public_manifest=failed_manifest, manifest=manifest,
                environment=json.dumps(manifest.get("tool_versions", {}), ensure_ascii=False),
            )
        except Exception as bundle_exc:
            manifest["reproducibility_error"] = str(bundle_exc)
            _write_json(failed_manifest, manifest)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        release_identity_lock(lock_handle)


if __name__ == "__main__":
    raise SystemExit(main())
