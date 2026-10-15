#!/usr/bin/env python3
"""Run PopLDdecay for all samples and validated groups with strict artifacts."""

from __future__ import annotations

import argparse
import gzip
import hashlib
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
from typing import Any

from cli.run_pop_pca import scan_vcf_marker_policy
from gfa_core.genotype_context import build_population_analysis_scope_id, genotype_scope_id_for_vcf, sha256_file
from gfa_core.population_stage_runtime import acquire_identity_lock, population_manifest_base, release_identity_lock, write_reproducibility_bundle


STAGE = "pop_ld_decay"
METHOD_VERSION = "poplddecay.v2"
CONTRACT_VERSION = "v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BCFTOOLS = PROJECT_ROOT / "scripts" / "pysam_bcftools.py"
DEFAULT_LD_PLOTTER = PROJECT_ROOT / "scripts" / "plot_pop_ld_decay.py"


def validate_groups(path: Path, vcf_samples: list[str], *, min_group_samples: int, invalid_policy: str) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    if invalid_policy not in {"fail", "skip"}:
        raise ValueError("invalid_group_policy must be fail or skip")
    sample_to_group: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t", 1)
        if len(fields) != 2 or not fields[0].strip() or not fields[1].strip():
            raise ValueError(f"sample_group row {line_number} must have exactly two tab-separated columns")
        sample, group = fields[0].strip(), fields[1].strip()
        if sample in sample_to_group:
            raise ValueError("sample_group sample IDs must be unique")
        if sample not in set(vcf_samples):
            raise ValueError(f"sample_group ID not present in VCF: {sample}")
        sample_to_group[sample] = group
    if not sample_to_group:
        raise ValueError("sample_group contains no data rows")
    raw_groups: dict[str, list[str]] = {}
    for sample in vcf_samples:
        if sample in sample_to_group:
            raw_groups.setdefault(sample_to_group[sample], []).append(sample)
    valid: dict[str, list[str]] = {}
    skipped: list[dict[str, Any]] = []
    for group, samples in raw_groups.items():
        if len(samples) < min_group_samples:
            reason = f"group {group} has fewer than {min_group_samples} samples"
            if invalid_policy == "fail":
                raise ValueError(reason)
            skipped.append({"group": group, "sample_count": len(samples), "reason": reason})
        else:
            valid[group] = samples
    if not valid and raw_groups:
        raise ValueError("No valid sample groups remain")
    return valid, skipped


def _slugs(groups: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    used: set[str] = set()
    for group in groups:
        base = re.sub(r"[^a-z0-9]+", "_", group.lower()).strip("_") or "group"
        slug = base
        if slug in used:
            slug = f"{base}_{hashlib.sha256(group.encode()).hexdigest()[:8]}"
        used.add(slug); result[group] = slug
    return result


def _resolve(value: Path) -> Path:
    path = value.resolve() if value.is_absolute() or value.exists() else Path(shutil.which(str(value)) or "")
    if not path.is_file():
        raise FileNotFoundError(f"Executable not found: {value}")
    return path


def _tool_command(tool: Path, args: list[str]) -> list[str]:
    return [sys.executable, str(tool), *args] if tool.suffix == ".py" else [str(tool), *args]


def _probe(tool: Path, args: list[str], accepted: set[int] = {0}) -> str:
    result = subprocess.run(_tool_command(tool, args), text=True, capture_output=True, timeout=30)
    text = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode not in accepted or not text:
        raise RuntimeError(f"Tool probe failed: {tool}")
    return text.splitlines()[0]


def _run(command: list[str], log: Path, cwd: Path) -> str:
    result = subprocess.run(command, text=True, capture_output=True, cwd=cwd)
    log.write_text("$ " + shlex.join(command) + "\n\nSTDOUT\n" + result.stdout + "\nSTDERR\n" + result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {shlex.join(command)}")
    return result.stdout + "\n" + result.stderr


def _accepted_snp_count(output: str) -> int | None:
    match = re.search(r"Remain SNP Number\s*:\s*(\d+)", output)
    return int(match.group(1)) if match else None


def _check_stat(path: Path) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"PopLDdecay statistic is missing or empty: {path}")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        if not any(line.strip() for line in handle):
            raise ValueError(f"PopLDdecay statistic has no readable content: {path}")


def _artifact(workspace: Path, path: Path, role: str, media_type: str) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"Required artifact is missing or empty: {role}")
    size = path.stat().st_size
    return {"artifact": role, "role": role, "path": str(path.relative_to(workspace)), "full_path": str(path.resolve()), "type": media_type, "size": size, "size_bytes": size, "sha256": sha256_file(path)}


def _publish(source: Path, destination: Path) -> None:
    temp = destination.with_name(f".{destination.name}.{os.getpid()}.publishing"); shutil.copy2(source, temp); os.replace(temp, destination)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path); parser.add_argument("--job-id", required=True); parser.add_argument("--prefix", required=True)
    parser.add_argument("--vcf", required=True, type=Path); parser.add_argument("--sample-group", type=Path)
    parser.add_argument("--bcftools", default=DEFAULT_BCFTOOLS, type=Path); parser.add_argument("--poplddecay", default="PopLDdecay", type=Path)
    parser.add_argument("--plot-one", default=DEFAULT_LD_PLOTTER, type=Path); parser.add_argument("--plot-multi", default=DEFAULT_LD_PLOTTER, type=Path)
    parser.add_argument("--max-dist-kb", default=500, type=int); parser.add_argument("--maf", default=0.05, type=float); parser.add_argument("--missing", default=0.2, type=float)
    parser.add_argument("--bin1", default=10, type=int); parser.add_argument("--bin2", default=500, type=int)
    parser.add_argument("--min-group-samples", default=3, type=int); parser.add_argument("--invalid-group-policy", default="fail", choices=("fail", "skip"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv); workspace = args.workspace.resolve(); final = workspace / "final"; final.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc); clock = time.monotonic(); run_id = f"{STAGE}_{started.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"; failed = final / f"{args.prefix}.{STAGE}.failed.{run_id}.manifest.json"
    identity12 = "unresolved"; runner_command = [sys.executable, "-m", "cli.run_pop_ld_decay", *(argv if argv is not None else sys.argv[1:])]
    lock_handle = None
    parameters = {"max_dist_kb": args.max_dist_kb, "maf": args.maf, "missing": args.missing, "bin1": args.bin1, "bin2": args.bin2, "min_group_samples": args.min_group_samples, "invalid_group_policy": args.invalid_group_policy}
    manifest = population_manifest_base(
        workspace=workspace, stage=STAGE, prefix=args.prefix, job_id=args.job_id,
        run_id=run_id, started=started, runner_command=runner_command,
        parameters=parameters,
    )
    try:
        if args.max_dist_kb <= 0 or not 0 <= args.maf <= 0.5 or not 0 <= args.missing <= 1 or args.min_group_samples < 1:
            raise ValueError("Invalid PopLDdecay parameters")
        vcf = args.vcf.resolve(); samples, marker_metrics = scan_vcf_marker_policy(vcf)
        groups: dict[str, list[str]] = {}; skipped: list[dict[str, Any]] = []
        if args.sample_group:
            groups, skipped = validate_groups(args.sample_group.resolve(), samples, min_group_samples=args.min_group_samples, invalid_policy=args.invalid_group_policy)
        slug_map = _slugs(sorted(groups))
        bcftools, popld, plot_one, plot_multi = map(_resolve, (args.bcftools, args.poplddecay, args.plot_one, args.plot_multi))
        versions = {"bcftools": _probe(bcftools, ["--version"]), "poplddecay": _probe(popld, [], {0, 1, 255}), "plot_one": _probe(plot_one, ["-h"], {0, 1, 255}), "plot_multi": _probe(plot_multi, ["-h"], {0, 1, 255}), "python": platform.python_version()}
        genotype_scope = genotype_scope_id_for_vcf(vcf); input_hashes = {"population_vcf": sha256_file(vcf)}
        if args.sample_group: input_hashes["sample_group"] = sha256_file(args.sample_group.resolve())
        analysis_scope = build_population_analysis_scope_id(stage=STAGE, genotype_scope_id=genotype_scope, input_artifact_hashes=input_hashes, parameters=parameters, method_version=METHOD_VERSION, contract_version=CONTRACT_VERSION, tool_versions=versions); identity12 = analysis_scope.split(":", 1)[1][:12]
        lock_handle = acquire_identity_lock(workspace, STAGE, args.prefix, identity12)
        manifest.update({"genotype_scope_id": genotype_scope, "analysis_scope_id": analysis_scope, "method_version": METHOD_VERSION, "contract_version": CONTRACT_VERSION, "tool_versions": versions, "inputs": [{"role": role, "sha256": digest} for role, digest in input_hashes.items()]})
        attempt = workspace / ".attempts" / run_id; attempt.mkdir(parents=True, exist_ok=False); commands: list[list[str]] = []
        all_samples = attempt / "pop_all.txt"; all_samples.write_text("\n".join(samples) + "\n", encoding="utf-8")
        all_prefix = attempt / "ld_all"; command = [str(popld), "-InVCF", str(vcf), "-SubPop", str(all_samples), "-MaxDist", str(args.max_dist_kb), "-MAF", str(args.maf), "-Miss", str(args.missing), "-OutStat", str(all_prefix)]; all_output = _run(command, attempt / "ld_all.log", attempt); commands.append(command)
        all_stat = Path(str(all_prefix) + ".stat.gz"); _check_stat(all_stat)
        all_plot_prefix = attempt / "plot_all"; command = _tool_command(plot_one, ["-inFile", str(all_stat), "--output", str(all_plot_prefix), "-bin1", str(args.bin1), "-bin2", str(args.bin2)]); _run(command, attempt / "plot_all.log", attempt); commands.append(command)
        all_png, all_pdf = all_plot_prefix.with_suffix(".png"), all_plot_prefix.with_suffix(".pdf")
        group_stats: dict[str, Path] = {}; group_plots: dict[str, tuple[Path, Path]] = {}; group_accepted: dict[str, int | None] = {}; multi_rows: list[str] = []
        for group in sorted(groups):
            slug = slug_map[group]; sample_file = attempt / f"pop_{slug}.txt"; sample_file.write_text("\n".join(groups[group]) + "\n", encoding="utf-8")
            prefix = attempt / f"ld_{slug}"; command = [str(popld), "-InVCF", str(vcf), "-SubPop", str(sample_file), "-MaxDist", str(args.max_dist_kb), "-MAF", str(args.maf), "-Miss", str(args.missing), "-OutStat", str(prefix)]; group_output = _run(command, attempt / f"ld_{slug}.log", attempt); commands.append(command)
            stat = Path(str(prefix) + ".stat.gz"); _check_stat(stat); group_stats[slug] = stat; group_accepted[slug] = _accepted_snp_count(group_output); multi_rows.append(f"{stat} {group}")
            group_plot_prefix = attempt / f"plot_{slug}"; command = _tool_command(plot_one, ["-inFile", str(stat), "--output", str(group_plot_prefix), "-bin1", str(args.bin1), "-bin2", str(args.bin2)]); _run(command, attempt / f"plot_{slug}.log", attempt); commands.append(command)
            group_plots[slug] = (group_plot_prefix.with_suffix(".png"), group_plot_prefix.with_suffix(".pdf"))
        multi_png = multi_pdf = None
        if len(groups) >= 2:
            multi_list = attempt / "multi.list"; multi_list.write_text("\n".join(multi_rows) + "\n", encoding="utf-8"); multi_prefix = attempt / "plot_multi"
            command = _tool_command(plot_multi, ["-inList", str(multi_list), "--output", str(multi_prefix), "-bin1", str(args.bin1), "-bin2", str(args.bin2)]); _run(command, attempt / "plot_multi.log", attempt); commands.append(command); multi_png, multi_pdf = multi_prefix.with_suffix(".png"), multi_prefix.with_suffix(".pdf")
        qc = attempt / "group_sample_qc.json"; qc_data = {"vcf_sample_count": len(samples), "valid_groups": [{"group": group, "slug": slug_map[group], "sample_count": len(groups[group]), "samples": groups[group]} for group in sorted(groups)], "skipped_groups": skipped}; qc.write_text(json.dumps(qc_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report = attempt / "report.md"; report.write_text(f"# Population LD decay\n\n- Samples: {len(samples)}\n- Valid groups: {len(groups)}\n- Max distance: {args.max_dist_kb} kb\n- MAF: {args.maf}; missing: {args.missing}\n- Single-group 11-sample output is workflow smoke, not multi-population scientific validation.\n", encoding="utf-8")
        environment = attempt / "environment.txt"; environment.write_text("\n".join(f"{key}={value}" for key, value in versions.items()) + "\n", encoding="utf-8")
        outputs: dict[str, tuple[Path, str, str]] = {"ld_all_stat": (all_stat, "all.stat.gz", "application/gzip"), "ld_all_plot_png": (all_png, "all.png", "image/png"), "ld_all_plot_pdf": (all_pdf, "all.pdf", "application/pdf"), "group_sample_qc": (qc, "group_qc.json", "application/json"), "report": (report, "report.md", "text/markdown"), "environment": (environment, "environment.txt", "text/plain")}
        for slug, stat in group_stats.items():
            outputs[f"ld_group_stat_{slug}"] = (stat, f"group_{slug}.stat.gz", "application/gzip")
            outputs[f"ld_group_plot_png_{slug}"] = (group_plots[slug][0], f"group_{slug}.png", "image/png")
            outputs[f"ld_group_plot_pdf_{slug}"] = (group_plots[slug][1], f"group_{slug}.pdf", "application/pdf")
        if multi_png and multi_pdf: outputs.update({"ld_multi_plot_png": (multi_png, "multi.png", "image/png"), "ld_multi_plot_pdf": (multi_pdf, "multi.pdf", "application/pdf")})
        stem = f"{args.prefix}.{STAGE}.{identity12}.{run_id}"; published: dict[str, tuple[Path, str]] = {}
        for role, (source, suffix, media) in outputs.items(): destination = final / f"{stem}.{suffix}"; _publish(source, destination); published[role] = (destination, media)
        manifest.update({"status": "success", "metrics": {**marker_metrics, "sample_count": len(samples), "valid_group_count": len(groups), "skipped_group_count": len(skipped), "multi_group_plot_created": len(groups) >= 2, "popld_accepted_snp_count_all": _accepted_snp_count(all_output), "popld_accepted_snp_count_by_group": group_accepted}, "commands": commands, "artifacts": {role: _artifact(workspace, path, role, media) for role, (path, media) in published.items()}}); manifest["end_time"] = datetime.now(timezone.utc).isoformat(); manifest["elapsed_sec"] = round(time.monotonic() - clock, 6)
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
