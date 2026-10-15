#!/usr/bin/env python3
"""Run LD-pruned ADMIXTURE across a complete K range with strict Q/FAM QC."""

from __future__ import annotations

import argparse
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
from typing import Any

from cli.run_pop_pca import scan_vcf_marker_policy
from gfa_core.genotype_context import build_population_analysis_scope_id, genotype_scope_id_for_vcf, sha256_file
from gfa_core.population_stage_runtime import acquire_identity_lock, population_manifest_base, release_identity_lock, write_reproducibility_bundle


STAGE = "pop_structure"
METHOD_VERSION = "plink_prune_admixture_cv.v1"
CONTRACT_VERSION = "v1"
Q_TOLERANCE = 1e-4


def validate_q_matrix(q_path: Path, fam_path: Path, k: int) -> tuple[list[str], list[list[float]]]:
    fam_rows = [line.split() for line in fam_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    sample_ids = [row[1] for row in fam_rows if len(row) >= 2]
    if len(sample_ids) != len(fam_rows) or not sample_ids or len(sample_ids) != len(set(sample_ids)):
        raise ValueError("FAM IID values must be nonempty and unique")
    rows: list[list[float]] = []
    for line_number, line in enumerate(q_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            values = [float(value) for value in line.split()]
        except ValueError as exc:
            raise ValueError(f"Q row {line_number} is not numeric") from exc
        if len(values) != k:
            raise ValueError(f"Q row {line_number} does not have K={k} columns")
        if not all(math.isfinite(value) and 0 <= value <= 1 for value in values):
            raise ValueError(f"Q row {line_number} has values outside [0,1]")
        if not math.isclose(sum(values), 1.0, rel_tol=0, abs_tol=Q_TOLERANCE):
            raise ValueError(f"Q row {line_number} sum is not one")
        rows.append(values)
    if len(rows) != len(sample_ids):
        raise ValueError("Q/FAM row count mismatch")
    return sample_ids, rows


def _validate_p_matrix(path: Path, k: int, marker_count: int) -> None:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        values = [float(value) for value in line.split()]
        if len(values) != k or not all(math.isfinite(value) and 0 <= value <= 1 for value in values):
            raise ValueError(f"ADMIXTURE P matrix is invalid for K={k}")
        rows.append(values)
    if len(rows) != marker_count:
        raise ValueError(f"ADMIXTURE P rows do not match BIM markers for K={k}")


def choose_best_k(cv_by_k: dict[int, float]) -> tuple[int, list[int]]:
    if not cv_by_k or any(not math.isfinite(value) for value in cv_by_k.values()):
        raise ValueError("Every requested K must have a finite CV error")
    minimum = min(cv_by_k.values())
    ties = sorted(k for k, value in cv_by_k.items() if value == minimum)
    return ties[0], ties


def _resolve_tool(value: Path) -> Path:
    candidate = value.resolve() if value.is_absolute() or value.exists() else Path(shutil.which(str(value)) or "")
    if not candidate.is_file():
        raise FileNotFoundError(f"Executable not found: {value}")
    return candidate


def _version(tool: Path, args: list[str], accepted: set[int] = {0}) -> str:
    result = subprocess.run([str(tool), *args], text=True, capture_output=True, timeout=30)
    text = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode not in accepted or not text:
        raise RuntimeError(f"Tool version probe failed: {tool}")
    return text.splitlines()[0]


def _run(command: list[str], log: Path, cwd: Path | None = None) -> str:
    result = subprocess.run(command, text=True, capture_output=True, cwd=cwd)
    text = "$ " + shlex.join(command) + "\n\nSTDOUT\n" + result.stdout + "\nSTDERR\n" + result.stderr
    log.write_text(text, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {shlex.join(command)}")
    return result.stdout + "\n" + result.stderr


def _artifact(workspace: Path, path: Path, role: str, media_type: str) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"Required artifact is missing or empty: {role}")
    size = path.stat().st_size
    return {"artifact": role, "role": role, "path": str(path.relative_to(workspace)), "full_path": str(path.resolve()), "type": media_type, "size": size, "size_bytes": size, "sha256": sha256_file(path)}


def _publish(source: Path, destination: Path) -> None:
    temp = destination.with_name(f".{destination.name}.{os.getpid()}.publishing")
    shutil.copy2(source, temp)
    os.replace(temp, destination)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--vcf", required=True, type=Path)
    parser.add_argument("--plink", default="plink", type=Path)
    parser.add_argument("--admixture", default="admixture", type=Path)
    parser.add_argument("--ld-window-variants", default=50, type=int)
    parser.add_argument("--ld-step-variants", default=10, type=int)
    parser.add_argument("--ld-r2", default=0.2, type=float)
    parser.add_argument("--k-min", default=2, type=int)
    parser.add_argument("--k-max", default=10, type=int)
    parser.add_argument("--cv-folds", default=5, type=int)
    parser.add_argument("--random-seed", default=20260723, type=int)
    parser.add_argument("--threads", default=6, type=int)
    parser.add_argument("--gwas-k", default=0, type=int)
    return parser


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
    runner_command = [sys.executable, "-m", "cli.run_pop_structure", *(argv if argv is not None else sys.argv[1:])]
    parameters = {
        "marker_policy": "biallelic_snp", "ld_window_variants": args.ld_window_variants,
        "ld_step_variants": args.ld_step_variants, "ld_r2": args.ld_r2,
        "k_min": args.k_min, "k_max": args.k_max, "cv_folds": args.cv_folds,
        "random_seed": args.random_seed, "threads": args.threads, "gwas_k": args.gwas_k or None,
    }
    manifest = population_manifest_base(
        workspace=workspace, stage=STAGE, prefix=args.prefix, job_id=args.job_id,
        run_id=run_id, started=started, runner_command=runner_command,
        parameters=parameters,
    )
    try:
        vcf = args.vcf.resolve()
        samples, marker_metrics = scan_vcf_marker_policy(vcf)
        if args.k_min < 2 or args.k_max < args.k_min or args.k_max >= len(samples):
            raise ValueError("ADMIXTURE requires 2 <= k_min <= k_max < sample_count")
        if args.gwas_k and not args.k_min <= args.gwas_k <= args.k_max:
            raise ValueError("gwas_k must be within the requested K range")
        if args.cv_folds < 2 or args.cv_folds > len(samples) or args.threads <= 0:
            raise ValueError("Invalid cv_folds or thread count")
        plink, admixture = _resolve_tool(args.plink), _resolve_tool(args.admixture)
        versions = {"plink": _version(plink, ["--version"]), "admixture": _version(admixture, [], {0, 255}), "python": platform.python_version()}
        genotype_scope = genotype_scope_id_for_vcf(vcf)
        input_hashes = {"population_vcf": sha256_file(vcf)}
        analysis_scope = build_population_analysis_scope_id(stage=STAGE, genotype_scope_id=genotype_scope, input_artifact_hashes=input_hashes, parameters=parameters, method_version=METHOD_VERSION, contract_version=CONTRACT_VERSION, tool_versions=versions)
        identity12 = analysis_scope.split(":", 1)[1][:12]
        lock_handle = acquire_identity_lock(workspace, STAGE, args.prefix, identity12)
        manifest.update({"genotype_scope_id": genotype_scope, "source_genotype_scope_id": genotype_scope, "analysis_scope_id": analysis_scope, "method_version": METHOD_VERSION, "contract_version": CONTRACT_VERSION, "tool_versions": versions, "inputs": [{"role": "population_vcf", "sha256": input_hashes["population_vcf"]}]})
        attempt = workspace / ".attempts" / run_id
        attempt.mkdir(parents=True, exist_ok=False)
        common = [str(plink), "--vcf", str(vcf), "--double-id", "--allow-extra-chr", "--biallelic-only", "strict", "--snps-only", "just-acgt", "--set-missing-var-ids", "@:#:$1:$2"]
        prune_prefix = attempt / "prune"
        prune_command = [*common, "--indep-pairwise", str(args.ld_window_variants), str(args.ld_step_variants), str(args.ld_r2), "--out", str(prune_prefix)]
        _run(prune_command, attempt / "plink_prune.log")
        prune_in = prune_prefix.with_suffix(".prune.in")
        markers = [line.strip() for line in prune_in.read_text().splitlines() if line.strip()] if prune_in.is_file() else []
        if not markers:
            raise ValueError("PLINK LD pruning retained zero markers")
        bed_prefix = attempt / "ld_pruned"
        bed_command = [*common, "--extract", str(prune_in), "--make-bed", "--out", str(bed_prefix)]
        _run(bed_command, attempt / "plink_make_bed.log")
        bed, bim, fam = (bed_prefix.with_suffix(suffix) for suffix in (".bed", ".bim", ".fam"))
        if any(not path.is_file() or path.stat().st_size <= 0 for path in (bed, bim, fam)):
            raise ValueError("PLINK BED/BIM/FAM output is incomplete")
        fam_ids = [row.split()[1] for row in fam.read_text().splitlines() if row.strip()]
        if fam_ids != samples:
            raise ValueError("BED FAM sample order does not match the VCF")
        marker_count = len([line for line in bim.read_text().splitlines() if line.strip()])
        if marker_count != len(markers):
            raise ValueError("BIM marker count does not match pruning marker count")
        cv_by_k: dict[int, float] = {}
        q_by_k: dict[int, Path] = {}
        p_by_k: dict[int, Path] = {}
        logs_by_k: dict[int, Path] = {}
        commands = [prune_command, bed_command]
        q_values_by_k: dict[int, list[list[float]]] = {}
        for k in range(args.k_min, args.k_max + 1):
            command = [str(admixture), f"--cv={args.cv_folds}", "-s", str(args.random_seed), f"-j{args.threads}", str(bed), str(k)]
            log = attempt / f"admixture_k{k}.log"
            output = _run(command, log, cwd=attempt)
            commands.append(command)
            match = re.search(rf"CV error \(K={k}\):\s*([0-9.eE+-]+)", output)
            if not match:
                raise ValueError(f"ADMIXTURE CV error is missing for K={k}")
            cv = float(match.group(1))
            if not math.isfinite(cv):
                raise ValueError(f"ADMIXTURE CV error is not finite for K={k}")
            q = Path(str(bed_prefix) + f".{k}.Q")
            p = Path(str(bed_prefix) + f".{k}.P")
            _, q_values = validate_q_matrix(q, fam, k)
            _validate_p_matrix(p, k, marker_count)
            cv_by_k[k], q_by_k[k], p_by_k[k], logs_by_k[k], q_values_by_k[k] = cv, q, p, log, q_values
        best_k, ties = choose_best_k(cv_by_k)
        cv_table = attempt / "cv.tsv"
        cv_table.write_text("K\tCV_error\n" + "".join(f"{k}\t{cv_by_k[k]:.15g}\n" for k in sorted(cv_by_k)), encoding="utf-8")
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        cv_plot = attempt / "cv.png"
        fig, ax = plt.subplots(figsize=(6, 4)); ax.plot(sorted(cv_by_k), [cv_by_k[k] for k in sorted(cv_by_k)], marker="o"); ax.set_xlabel("K"); ax.set_ylabel("CV error"); fig.tight_layout(); fig.savefig(cv_plot, dpi=180); plt.close(fig)
        structure_plot = attempt / "best_k.png"
        fig, ax = plt.subplots(figsize=(max(7, len(samples) * 0.5), 5)); bottom = [0.0] * len(samples)
        for component in range(best_k):
            values = [row[component] for row in q_values_by_k[best_k]]; ax.bar(range(len(samples)), values, bottom=bottom, width=1.0); bottom = [a + b for a, b in zip(bottom, values)]
        ax.set_xticks(range(len(samples))); ax.set_xticklabels(samples, rotation=90); ax.set_ylabel("Ancestry proportion"); fig.tight_layout(); fig.savefig(structure_plot, dpi=180); plt.close(fig)
        environment = attempt / "environment.txt"
        environment.write_text(f"python={sys.executable}\nplink={plink}\nplink_version={versions['plink']}\nadmixture={admixture}\nadmixture_version={versions['admixture']}\n", encoding="utf-8")
        report = attempt / "report.md"
        report.write_text(f"# Population structure\n\n- Requested K: {args.k_min}..{args.k_max}\n- Best K by minimum CV: {best_k}\n- Exact CV tie set: {ties}\n- Explicit GWAS K: {args.gwas_k or 'not selected'}\n- Validation scope: 11-sample runs are flow smoke only.\n", encoding="utf-8")
        outputs: dict[str, tuple[Path, str, str]] = {
            "ld_pruned_bed": (bed, "ld_pruned.bed", "application/octet-stream"), "ld_pruned_bim": (bim, "ld_pruned.bim", "text/plain"),
            "ld_pruned_fam": (fam, "ld_pruned.fam", "text/plain"), "pruning_markers": (prune_in, "prune.in", "text/plain"),
            "cv_table": (cv_table, "cv.tsv", "text/tab-separated-values"), "best_k_q": (q_by_k[best_k], f"best_k{best_k}.Q", "text/plain"),
            "best_k_p": (p_by_k[best_k], f"best_k{best_k}.P", "text/plain"), "structure_best_k_plot": (structure_plot, "best_k.png", "image/png"),
            "structure_cv_plot": (cv_plot, "cv.png", "image/png"), "report": (report, "report.md", "text/markdown"), "environment": (environment, "environment.txt", "text/plain"),
        }
        for k in sorted(cv_by_k):
            outputs[f"admixture_q_k{k}"] = (q_by_k[k], f"k{k}.Q", "text/plain")
            outputs[f"admixture_p_k{k}"] = (p_by_k[k], f"k{k}.P", "text/plain")
            outputs[f"admixture_log_k{k}"] = (logs_by_k[k], f"k{k}.log", "text/plain")
        if args.gwas_k:
            outputs.update({
                "gwas_k_q": (q_by_k[args.gwas_k], f"gwas_k{args.gwas_k}.Q", "text/plain"),
                "gwas_k_fam": (fam, f"gwas_k{args.gwas_k}.fam", "text/plain"),
                "admixture_q": (q_by_k[args.gwas_k], f"admixture_k{args.gwas_k}.q", "text/plain"),
                "sample_fam": (fam, f"admixture_k{args.gwas_k}.fam", "text/plain"),
            })
        stem = f"{args.prefix}.{STAGE}.{identity12}.{run_id}"
        published: dict[str, tuple[Path, str]] = {}
        for role, (source, suffix, media_type) in outputs.items():
            destination = final / f"{stem}.{suffix}"; _publish(source, destination); published[role] = (destination, media_type)
        manifest.update({"status": "success", "metrics": {**marker_metrics, "sample_count": len(samples), "pruned_marker_count": len(markers), "requested_k": list(range(args.k_min, args.k_max + 1)), "best_k": best_k, "best_k_ties": ties, "gwas_k": args.gwas_k or None, "q_fam_order_conservation": True}, "commands": commands, "artifacts": {role: _artifact(workspace, path, role, media_type) for role, (path, media_type) in published.items()}})
        manifest["end_time"] = datetime.now(timezone.utc).isoformat(); manifest["elapsed_sec"] = round(time.monotonic() - clock, 6)
        manifest_work = attempt / "manifest.json"; manifest_work.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest_path = final / f"{stem}.manifest.json"
        write_reproducibility_bundle(
            workspace=workspace, stage=STAGE, prefix=args.prefix, identity12=identity12,
            run_id=run_id, runner_command=runner_command, manifest_source=manifest_work,
            public_manifest=manifest_path, manifest=manifest,
            environment=environment.read_text(encoding="utf-8"),
            artifact_sources={role: source for role, (source, _suffix, _media) in outputs.items()},
        )
        _publish(manifest_work, manifest_path); return 0
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
        print(f"ERROR: {exc}", file=sys.stderr); return 1
    finally:
        release_identity_lock(lock_handle)


if __name__ == "__main__":
    raise SystemExit(main())
