#!/usr/bin/env python3
"""Calculate and validate a TASSEL centered-IBS kinship matrix."""

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from cli.run_gwas_tassel import _resolved_command, _tassel_version
from gfa_core.gwas_scientific_identity import build_identity_recipe, materialize_stage_identity, new_run_id


STAGE = "gwas_kinship"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_executable(value: str) -> str:
    return _resolved_command(value)


def tassel_version(value: str) -> str:
    return _tassel_version(value)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _vcf_samples(path: Path) -> list[str]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#CHROM\t"):
                samples = line.rstrip("\n").split("\t")[9:]
                if not samples or len(samples) != len(set(samples)):
                    raise ValueError("sorted VCF sample IDs must be nonempty and unique")
                return samples
    raise ValueError("sorted VCF #CHROM header is missing")


def validate_kinship_matrix(path: Path, expected_samples: list[str]) -> dict[str, Any]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    body = [line for line in lines if not line.startswith("##")]
    if not body:
        raise ValueError("kinship matrix is empty")
    try:
        declared_count = int(body[0])
    except ValueError as exc:
        raise ValueError("kinship matrix sample count is invalid") from exc
    rows = body[1:]
    if declared_count != len(rows) or declared_count != len(expected_samples):
        raise ValueError("kinship matrix is not square for the expected sample count")
    ids: list[str] = []
    values: list[list[float]] = []
    for line in rows:
        fields = line.split()
        if len(fields) != declared_count + 1:
            raise ValueError("kinship matrix row width is invalid")
        ids.append(fields[0])
        try:
            values.append([float(value) for value in fields[1:]])
        except ValueError as exc:
            raise ValueError("kinship matrix contains nonnumeric values") from exc
    matrix = np.asarray(values, dtype=float)
    if not np.isfinite(matrix).all():
        raise ValueError("kinship matrix contains nonfinite values")
    if ids != expected_samples:
        raise ValueError("kinship matrix sample order does not match sorted VCF")
    if not np.allclose(matrix, matrix.T, rtol=1e-7, atol=1e-7):
        raise ValueError("kinship matrix must be symmetric")
    minimum_eigenvalue = float(np.linalg.eigvalsh((matrix + matrix.T) / 2.0).min())
    if minimum_eigenvalue < -1e-6:
        raise ValueError("kinship matrix is not positive-semidefinite")
    return {
        "sample_count": declared_count,
        "sample_order_conservation": True,
        "symmetric": True,
        "positive_semidefinite": True,
        "minimum_eigenvalue": minimum_eigenvalue,
        "matrix_type": "Centered_IBS",
    }


def run_tassel_kinship(command: list[str], output: Path) -> str:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    combined = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        raise RuntimeError(f"TASSEL kinship failed with exit code {completed.returncode}")
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("TASSEL completed without a kinship matrix")
    return combined


def _artifact(workspace: Path, path: Path, role: str, media_type: str) -> dict[str, Any]:
    return {
        "artifact": role,
        "role": role,
        "path": str(path.relative_to(workspace)),
        "full_path": str(path.resolve()),
        "type": media_type,
        "size": path.stat().st_size,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _runner_command(args: argparse.Namespace, workspace: Path, vcf: Path) -> list[str]:
    command = [
        "python", "-m", "cli.run_gwas_kinship",
        "--workspace", str(workspace), "--job-id", args.job_id,
        "--prefix", args.prefix, "--vcf", str(vcf),
        "--tassel-pipeline", args.tassel_pipeline, "--xmx", args.xmx,
    ]
    if args.genotype_scope_id:
        command.extend(["--genotype-scope-id", args.genotype_scope_id])
    if args.log_file:
        command.extend(["--log-file", str(args.log_file)])
    return command


def _write_reproducibility(
    workspace: Path,
    identity12: str,
    run_id: str,
    command: list[str],
    manifest_path: Path,
    manifest: dict[str, Any],
    environment_text: str,
) -> None:
    bundle = workspace / "reproducibility" / STAGE / f"{manifest['prefix']}.{identity12}" / run_id
    bundle.mkdir(parents=True, exist_ok=True)
    canonical = shlex.join(command)
    (bundle / "commands.sh").write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + canonical + "\n", encoding="utf-8")
    (bundle / "environment.txt").write_text(environment_text, encoding="utf-8")
    _write_json(bundle / "progress.json", {
        "stage": STAGE, "status": manifest["status"], "run_id": run_id,
        "manifest_path": str(manifest_path.relative_to(workspace)),
    })
    _write_json(bundle / "provenance.json", {
        "stage": STAGE, "run_id": run_id, "runner_command": canonical,
        "software": manifest.get("software", {}), "artifacts": manifest.get("artifacts", {}),
    })
    targets = [manifest_path]
    for artifact in manifest.get("artifacts", {}).values():
        candidate = workspace / artifact["path"]
        if candidate.is_file():
            targets.append(candidate)
    (bundle / "checksums.sha256").write_text(
        "".join(f"{sha256_file(path)}  {path.relative_to(workspace)}\n" for path in sorted(targets)),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--vcf", required=True, type=Path)
    parser.add_argument("--genotype-scope-id", default="")
    parser.add_argument("--tassel-pipeline", default="run_pipeline.pl")
    parser.add_argument("--xmx", default="10g")
    parser.add_argument("--log-file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = args.workspace.resolve()
    final_dir = workspace / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    vcf = args.vcf.resolve()
    run_id = new_run_id(STAGE)
    started = datetime.now(timezone.utc)
    started_clock = time.monotonic()
    failed_manifest = final_dir / f"{args.prefix}.{STAGE}.failed.manifest.json"
    command = _runner_command(args, workspace, vcf)
    manifest: dict[str, Any] = {
        "manifest_version": "1.0", "stage": STAGE, "prefix": args.prefix,
        "job_id": args.job_id, "run_id": run_id, "status": "failed",
        "timestamp": started.isoformat(),
        "start_time": started.isoformat(), "end_time": started.isoformat(),
        "elapsed_sec": 0.0, "artifacts": {}, "cmd": command,
        "workspace_root": str(workspace), "workspace": str(final_dir),
        "input_dir": str(workspace / "input"),
    }
    environment_text = ""
    try:
        if not vcf.is_file():
            raise FileNotFoundError("Authoritative sorted VCF does not exist")
        vcf_sha = sha256_file(vcf)
        genotype_scope = args.genotype_scope_id or f"genotype:{vcf_sha}"
        if not re.fullmatch(r"genotype:[0-9a-f]{64}", genotype_scope):
            raise ValueError("Provided genotype scope is invalid")
        samples = _vcf_samples(vcf)
        executable = resolve_executable(args.tassel_pipeline)
        if not Path(executable).is_file() or not os.access(executable, os.X_OK):
            raise FileNotFoundError("TASSEL pipeline executable was not found or is not executable")
        version = tassel_version(executable)
        if version == "unknown":
            raise RuntimeError("TASSEL version could not be proven")
        recipe = build_identity_recipe(
            stage=STAGE, required_artifact_roles=["sorted_vcf"],
            parameters={"kinship_method": "Centered_IBS", "xmx": args.xmx},
            method_version="tassel_centered_ibs.v1", contract_version="v1",
            tool_versions={"tassel": version},
        )
        identity = materialize_stage_identity(
            recipe, {"sorted_vcf": {"sha256": vcf_sha}},
            {"genotype_scope_id": genotype_scope},
        )
        identity12 = identity["analysis_scope_id"].split(":", 1)[1][:12]
        attempt_dir = workspace / ".attempts" / run_id
        attempt_dir.mkdir(parents=True, exist_ok=False)
        matrix_work = attempt_dir / "kinship.txt"
        tassel_command = [
            executable, f"-Xmx{args.xmx}", "-fork1", "-vcf", str(vcf),
            "-ck", "-export", str(matrix_work), "-runfork1",
        ]
        run_tassel_kinship(tassel_command, matrix_work)
        metrics = validate_kinship_matrix(matrix_work, samples)

        stem = f"{args.prefix}.{STAGE}.{identity12}"
        matrix_final = final_dir / f"{stem}.kinship.txt"
        environment_final = final_dir / f"{stem}.environment.txt"
        report_final = final_dir / f"{stem}.report.md"
        manifest_path = final_dir / f"{stem}.manifest.json"
        shutil.copy2(matrix_work, matrix_final)
        environment_text = "\n".join([
            "runner=cli.run_gwas_kinship.py",
            f"runtime_environment={os.environ.get('CONDA_DEFAULT_ENV', 'unknown')}",
            f"python_resolved={Path(sys.executable).resolve()}",
            f"python_version={platform.python_version()}",
            f"tassel_resolved={executable}", f"tassel_version={version}",
            f"platform={platform.platform()}",
        ]) + "\n"
        environment_final.write_text(environment_text, encoding="utf-8")
        report_final.write_text(
            "# GWAS TASSEL kinship\n\n"
            f"- Genotype scope: `{genotype_scope}`\n"
            f"- Analysis scope: `{identity['analysis_scope_id']}`\n"
            f"- Samples: {metrics['sample_count']}\n"
            f"- Matrix type: `Centered_IBS`\n"
            f"- Minimum eigenvalue: {metrics['minimum_eigenvalue']:.10g}\n",
            encoding="utf-8",
        )
        manifest.update({
            "status": "success", "genotype_scope_id": genotype_scope,
            "analysis_scope_id": identity["analysis_scope_id"],
            "identity_recipe": recipe,
            "inputs": {"sorted_vcf": {"path": str(vcf), "size": vcf.stat().st_size, "sha256": vcf_sha}},
            "parameters": {"kinship_method": "Centered_IBS", "xmx": args.xmx},
            "metrics": metrics,
            "software": {"tassel": {"resolved_path": executable, "version": version}},
            "artifacts": {
                "kinship_matrix": _artifact(workspace, matrix_final, "kinship_matrix", "text/tab-separated-values"),
                "environment": _artifact(workspace, environment_final, "environment", "text/plain"),
                "report": _artifact(workspace, report_final, "report", "text/markdown"),
            },
        })
        manifest["end_time"] = datetime.now(timezone.utc).isoformat()
        manifest["elapsed_sec"] = round(time.monotonic() - started_clock, 6)
        _write_json(manifest_path, manifest)
        failed_manifest.unlink(missing_ok=True)
        _write_reproducibility(workspace, identity12, run_id, command, manifest_path, manifest, environment_text)
        return 0
    except Exception as exc:
        manifest["error"] = str(exc)
        manifest["end_time"] = datetime.now(timezone.utc).isoformat()
        manifest["elapsed_sec"] = round(time.monotonic() - started_clock, 6)
        _write_json(failed_manifest, manifest)
        if args.log_file:
            args.log_file.parent.mkdir(parents=True, exist_ok=True)
            args.log_file.write_text(f"ERROR: {exc}\n", encoding="utf-8")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
