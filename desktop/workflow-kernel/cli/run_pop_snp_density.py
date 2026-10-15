#!/usr/bin/env python3
"""Count VCF SNP records in physical windows and publish verified density plots."""

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
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from gfa_core.genotype_context import build_population_analysis_scope_id, genotype_scope_id_for_vcf, sha256_file
from gfa_core.population_stage_runtime import acquire_identity_lock, population_manifest_base, release_identity_lock, write_reproducibility_bundle


STAGE = "pop_snp_density"
METHOD_VERSION = "vcf_physical_windows_cmplot.v2"
CONTRACT_VERSION = "v1"
CM_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pop_snp_density_cmplot.R"


def _open(path: Path) -> TextIO:
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open("r", encoding="utf-8")


def _rice_alias(value: str) -> str | None:
    match = re.fullmatch(r"(?:chr)?0*([1-9]|1[0-2])", value.strip(), flags=re.IGNORECASE)
    return str(int(match.group(1))) if match else None


def select_snp_records(path: Path, *, policy: str, explicit: list[str]) -> tuple[list[tuple[str, int, str, int]], dict[str, Any]]:
    if policy not in {"all_vcf_contigs", "rice_1_12", "explicit"}:
        raise ValueError("Invalid chromosome_policy")
    if policy == "explicit" and not explicit:
        raise ValueError("explicit chromosome policy requires chromosomes")
    selected_tokens = {(_rice_alias(item) or item): item for item in explicit}
    raw_records: list[tuple[str, int, str]] = []
    input_record_count = 0
    excluded_non_snp_count = 0
    with _open(path) as handle:
        for raw in handle:
            if raw.startswith("#") or not raw.strip():
                continue
            fields = raw.rstrip("\n").split("\t")
            if len(fields) < 5:
                raise ValueError("VCF record has fewer than five columns")
            input_record_count += 1
            position = int(fields[1])
            if position <= 0:
                raise ValueError("VCF positions must be positive")
            ref = fields[3].upper()
            alts = [allele.upper() for allele in fields[4].split(",")]
            if len(ref) != 1 or ref not in "ACGT" or not alts or any(len(allele) != 1 or allele not in "ACGT" for allele in alts):
                excluded_non_snp_count += 1
                continue
            raw_records.append((fields[0], position, fields[2] if fields[2] != "." else f"{fields[0]}:{position}"))
    canonical_sources: dict[str, set[str]] = {}
    kept: list[tuple[str, int, str]] = []
    for chrom, position, marker in raw_records:
        if policy == "all_vcf_contigs":
            canonical = chrom
        else:
            canonical = _rice_alias(chrom) or chrom
            if policy == "rice_1_12" and _rice_alias(chrom) is None:
                continue
            if policy == "explicit" and canonical not in selected_tokens and chrom not in selected_tokens:
                continue
        canonical_sources.setdefault(canonical, set()).add(chrom)
        kept.append((canonical, position, marker))
    conflicts = {canonical: sorted(sources) for canonical, sources in canonical_sources.items() if len(sources) > 1}
    if conflicts:
        raise ValueError(f"Chromosome alias normalization collision: {conflicts}")
    if not kept:
        raise ValueError("Chromosome policy selected zero SNP records")
    contigs = list(dict.fromkeys(chrom for chrom, _position, _marker in kept))
    plot_index = {chrom: index for index, chrom in enumerate(contigs, start=1)}
    records = [(chrom, position, marker, plot_index[chrom]) for chrom, position, marker in kept]
    input_snp_count = len(raw_records)
    return records, {
        "input_record_count": input_record_count,
        "input_snp_record_count": input_snp_count,
        "excluded_non_snp_count": excluded_non_snp_count,
        "selected_snp_count": len(records),
        "excluded_by_chromosome_policy_count": input_snp_count - len(records),
        "selected_chromosomes": contigs,
    }


def build_density_rows(records: list[tuple[str, int, str, int]], window_bp: int) -> list[tuple[str, int, int, int]]:
    if window_bp <= 0:
        raise ValueError("Density window must be positive")
    order = list(dict.fromkeys(record[0] for record in records))
    counts = Counter((chrom, (position - 1) // window_bp) for chrom, position, _marker, _plot in records)
    max_index = {
        chrom: max((position - 1) // window_bp for chrom2, position, _marker, _plot in records if chrom2 == chrom)
        for chrom in order
    }
    return [
        (chrom, index * window_bp + 1, (index + 1) * window_bp, counts[(chrom, index)])
        for chrom in order for index in range(max_index[chrom] + 1)
    ]


def _resolve(value: Path) -> Path:
    path = value.resolve() if value.is_absolute() or value.exists() else Path(shutil.which(str(value)) or "")
    if not path.is_file(): raise FileNotFoundError(f"Executable not found: {value}")
    return path


def _artifact(workspace: Path, path: Path, role: str, media: str) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0: raise ValueError(f"Required artifact is missing or empty: {role}")
    size = path.stat().st_size
    return {"artifact": role, "role": role, "path": str(path.relative_to(workspace)), "full_path": str(path.resolve()), "type": media, "size": size, "size_bytes": size, "sha256": sha256_file(path)}


def _publish(source: Path, destination: Path) -> None:
    temp = destination.with_name(f".{destination.name}.{os.getpid()}.publishing"); shutil.copy2(source, temp); os.replace(temp, destination)


def _write_table(path: Path, rows: list[tuple[str, int, int, int]]) -> None:
    path.write_text("chromosome\twindow_start\twindow_end\tsnp_count\n" + "".join(f"{chrom}\t{start}\t{end}\t{count}\n" for chrom, start, end, count in rows), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path); parser.add_argument("--job-id", required=True); parser.add_argument("--prefix", required=True); parser.add_argument("--vcf", required=True, type=Path)
    parser.add_argument("--chromosome-policy", default="all_vcf_contigs", choices=("all_vcf_contigs", "rice_1_12", "explicit")); parser.add_argument("--chromosomes", default="")
    parser.add_argument("--coarse-window-bp", default=1_000_000, type=int); parser.add_argument("--fine-window-bp", default=500_000, type=int); parser.add_argument("--rscript", default="Rscript", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv); workspace = args.workspace.resolve(); final = workspace / "final"; final.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc); clock = time.monotonic(); run_id = f"{STAGE}_{started.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"; failed = final / f"{args.prefix}.{STAGE}.failed.{run_id}.manifest.json"; explicit = [item.strip() for item in args.chromosomes.split(",") if item.strip()]
    identity12 = "unresolved"; runner_command = [sys.executable, "-m", "cli.run_pop_snp_density", *(argv if argv is not None else sys.argv[1:])]
    lock_handle = None
    parameters = {"chromosome_policy": args.chromosome_policy, "chromosomes": explicit, "coarse_window_bp": args.coarse_window_bp, "fine_window_bp": args.fine_window_bp}
    manifest = population_manifest_base(
        workspace=workspace, stage=STAGE, prefix=args.prefix, job_id=args.job_id,
        run_id=run_id, started=started, runner_command=runner_command,
        parameters=parameters,
    )
    try:
        vcf = args.vcf.resolve(); records, qc = select_snp_records(vcf, policy=args.chromosome_policy, explicit=explicit); coarse_rows = build_density_rows(records, args.coarse_window_bp); fine_rows = build_density_rows(records, args.fine_window_bp)
        if sum(row[3] for row in coarse_rows) != len(records) or sum(row[3] for row in fine_rows) != len(records): raise ValueError("Density window count conservation failed")
        rscript = _resolve(args.rscript); probe = subprocess.run([str(rscript), "--version"], text=True, capture_output=True, timeout=30)
        if probe.returncode != 0: raise RuntimeError("Rscript version probe failed")
        versions = {"rscript": (probe.stdout + probe.stderr).strip().splitlines()[0], "cmplot_script_sha256": sha256_file(CM_SCRIPT), "python": platform.python_version()}
        genotype_scope = genotype_scope_id_for_vcf(vcf); input_hashes = {"population_vcf": sha256_file(vcf)}; analysis_scope = build_population_analysis_scope_id(stage=STAGE, genotype_scope_id=genotype_scope, input_artifact_hashes=input_hashes, parameters=parameters, method_version=METHOD_VERSION, contract_version=CONTRACT_VERSION, tool_versions=versions); identity12 = analysis_scope.split(":", 1)[1][:12]
        lock_handle = acquire_identity_lock(workspace, STAGE, args.prefix, identity12)
        manifest.update({"genotype_scope_id": genotype_scope, "analysis_scope_id": analysis_scope, "method_version": METHOD_VERSION, "contract_version": CONTRACT_VERSION, "tool_versions": versions, "inputs": [{"role": "population_vcf", "sha256": input_hashes["population_vcf"]}]})
        attempt = workspace / ".attempts" / run_id; attempt.mkdir(parents=True, exist_ok=False)
        coords = attempt / "snp_coordinates.tsv"; coords.write_text("SNP\tChromosome\tPosition\tPlotChromosome\n" + "".join(f"{marker}\t{chrom}\t{position}\t{plot}\n" for chrom, position, marker, plot in records), encoding="utf-8")
        coarse_table, fine_table = attempt / "density_1mb.tsv", attempt / "density_500kb.tsv"; _write_table(coarse_table, coarse_rows); _write_table(fine_table, fine_rows)
        coarse_plot, fine_plot = attempt / "density_1mb.png", attempt / "density_500kb.png"; command = [str(rscript), str(CM_SCRIPT), str(coords), str(args.coarse_window_bp), str(args.fine_window_bp), str(coarse_plot), str(fine_plot)]
        result = subprocess.run(command, text=True, capture_output=True, cwd=attempt); (attempt / "cmplot.log").write_text("$ " + shlex.join(command) + "\n\nSTDOUT\n" + result.stdout + "\nSTDERR\n" + result.stderr, encoding="utf-8")
        if result.returncode != 0: raise RuntimeError(f"CMplot command failed with exit code {result.returncode}")
        if any(not path.is_file() or path.stat().st_size <= 0 for path in (coarse_plot, fine_plot)): raise ValueError("CMplot did not create both canonical density plots in the current attempt")
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        contigs = qc["selected_chromosomes"]; bins = sorted({start for _chrom, start, _end, _count in fine_rows}); matrix = [[next((count for chrom2, start2, _end, count in fine_rows if chrom2 == chrom and start2 == start), 0) for start in bins] for chrom in contigs]
        heatmap = attempt / "density_heatmap_500kb.png"; fig, ax = plt.subplots(figsize=(max(7, len(bins) * 0.25), max(4, len(contigs) * 0.35))); image = ax.imshow(matrix, aspect="auto", interpolation="nearest"); ax.set_yticks(range(len(contigs))); ax.set_yticklabels(contigs); ax.set_xlabel("Physical 500 kb window start"); fig.colorbar(image, ax=ax, label="SNP count"); fig.tight_layout(); fig.savefig(heatmap, dpi=180); plt.close(fig)
        chr_plot = attempt / "chromosome_snp_count.png"; chr_counts = Counter(record[0] for record in records); fig, ax = plt.subplots(figsize=(max(7, len(contigs) * 0.5), 5)); ax.bar(contigs, [chr_counts[chrom] for chrom in contigs]); ax.set_ylabel("SNP record count"); ax.tick_params(axis="x", rotation=90); fig.tight_layout(); fig.savefig(chr_plot, dpi=180); plt.close(fig)
        report = attempt / "report.md"; report.write_text(f"# SNP density\n\n- Chromosome policy: {args.chromosome_policy}\n- Selected SNP records: {len(records)}\n- Physical windows: {args.coarse_window_bp} bp and {args.fine_window_bp} bp\n- Both density tables passed count conservation.\n", encoding="utf-8")
        environment = attempt / "environment.txt"; environment.write_text("\n".join(f"{key}={value}" for key, value in versions.items()) + "\n", encoding="utf-8")
        outputs = {"snp_coordinates": (coords, "coordinates.tsv", "text/tab-separated-values"), "density_1mb_table": (coarse_table, "density_1mb.tsv", "text/tab-separated-values"), "density_500kb_table": (fine_table, "density_500kb.tsv", "text/tab-separated-values"), "density_1mb_plot": (coarse_plot, "density_1mb.png", "image/png"), "density_500kb_plot": (fine_plot, "density_500kb.png", "image/png"), "density_heatmap_500kb": (heatmap, "heatmap_500kb.png", "image/png"), "chromosome_snp_count_plot": (chr_plot, "chromosome_counts.png", "image/png"), "report": (report, "report.md", "text/markdown"), "environment": (environment, "environment.txt", "text/plain")}
        stem = f"{args.prefix}.{STAGE}.{identity12}.{run_id}"; published: dict[str, tuple[Path, str]] = {}
        for role, (source, suffix, media) in outputs.items(): destination = final / f"{stem}.{suffix}"; _publish(source, destination); published[role] = (destination, media)
        manifest.update({"status": "success", "metrics": {**qc, "density_1mb_count_sum": sum(row[3] for row in coarse_rows), "density_500kb_count_sum": sum(row[3] for row in fine_rows)}, "commands": [command], "artifacts": {role: _artifact(workspace, path, role, media) for role, (path, media) in published.items()}}); manifest["end_time"] = datetime.now(timezone.utc).isoformat(); manifest["elapsed_sec"] = round(time.monotonic() - clock, 6)
        work = attempt / "manifest.json"; work.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); manifest_path = final / f"{stem}.manifest.json"
        write_reproducibility_bundle(workspace=workspace, stage=STAGE, prefix=args.prefix, identity12=identity12, run_id=run_id, runner_command=runner_command, manifest_source=work, public_manifest=manifest_path, manifest=manifest, environment=environment.read_text(encoding="utf-8"), artifact_sources={role: source for role, (source, _suffix, _media) in outputs.items()})
        _publish(work, manifest_path); return 0
    except Exception as exc:
        manifest.update({"status": "failed", "artifacts": {}, "error": str(exc), "end_time": datetime.now(timezone.utc).isoformat(), "elapsed_sec": round(time.monotonic() - clock, 6)}); failed.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            write_reproducibility_bundle(workspace=workspace, stage=STAGE, prefix=args.prefix, identity12=identity12, run_id=run_id, runner_command=runner_command, manifest_source=failed, public_manifest=failed, manifest=manifest, environment=json.dumps(manifest.get("tool_versions", {}), ensure_ascii=False))
        except Exception as bundle_exc:
            manifest["reproducibility_error"] = str(bundle_exc); failed.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"ERROR: {exc}", file=sys.stderr); return 1
    finally:
        release_identity_lock(lock_handle)


if __name__ == "__main__": raise SystemExit(main())
