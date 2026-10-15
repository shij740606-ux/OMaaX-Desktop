#!/usr/bin/env python3
"""Convert a matched ADMIXTURE Q/FAM pair to TASSEL covariate format."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import shlex
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gfa_core.gwas_scientific_identity import (
    build_covariate_scope_id,
    build_identity_recipe,
    materialize_stage_identity,
    new_run_id,
)


STAGE = "gwas_cov"
Q_ROW_SUM_TOLERANCE = 1e-6


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _nonempty_lines(path: Path, label: str) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(f"Authoritative {label} does not exist")
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"{label} is empty")
    return lines


def validate_covariate_inputs(
    q_path: Path,
    fam_path: Path,
    k: int,
) -> tuple[list[str], list[list[float]], dict[str, Any]]:
    """Validate a Q/FAM pair and return FAM IID order plus numeric Q values."""
    if k < 2:
        raise ValueError("covariate K must be at least two")

    fam_lines = _nonempty_lines(fam_path, "sample FAM")
    sample_ids: list[str] = []
    for line_number, line in enumerate(fam_lines, start=1):
        fields = line.split()
        if len(fields) < 2 or not fields[1]:
            raise ValueError(f"FAM IID is missing at row {line_number}")
        sample_ids.append(fields[1])
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("FAM IID sample IDs must be unique")

    q_lines = _nonempty_lines(q_path, "ADMIXTURE Q")
    if len(q_lines) != len(sample_ids):
        raise ValueError("Q/FAM row count does not match")
    values: list[list[float]] = []
    for line_number, line in enumerate(q_lines, start=1):
        fields = line.split()
        if len(fields) != k:
            raise ValueError(f"ADMIXTURE Q row {line_number} does not have K={k} columns")
        try:
            row = [float(value) for value in fields]
        except ValueError as exc:
            raise ValueError(f"ADMIXTURE Q row {line_number} contains nonnumeric values") from exc
        if not all(math.isfinite(value) for value in row):
            raise ValueError(f"ADMIXTURE Q row {line_number} must contain finite values")
        if not all(0.0 <= value <= 1.0 for value in row):
            raise ValueError(f"ADMIXTURE Q row {line_number} values must be in [0, 1]")
        if not math.isclose(sum(row), 1.0, rel_tol=0.0, abs_tol=Q_ROW_SUM_TOLERANCE):
            raise ValueError(f"ADMIXTURE Q row {line_number} sum is not within tolerance of 1")
        values.append(row)

    return sample_ids, values, {
        "sample_count": len(sample_ids),
        "k": k,
        "fam_id_column": "IID",
        "sample_order_conservation": True,
        "row_sum_tolerance": Q_ROW_SUM_TOLERANCE,
        "effective_covariate_count": k - 1,
        "omitted_covariate": f"Q{k}",
    }


def _write_tassel_covariates(path: Path, sample_ids: list[str], values: list[list[float]], k: int) -> None:
    header = "\t".join(f"Q{index}" for index in range(1, k + 1))
    lines = ["<Covariate>", f"<Trait>\t{header}"]
    for sample_id, row in zip(sample_ids, values):
        lines.append("\t".join([sample_id, *(format(value, ".15g") for value in row)]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _artifact(workspace: Path, path: Path, role: str, media_type: str) -> dict[str, Any]:
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


def _input(path: Path, role: str) -> dict[str, Any]:
    return {
        "source": "external",
        "artifact": role,
        "path": str(path),
        "full_path": str(path),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _runner_command(args: argparse.Namespace, workspace: Path, q_path: Path, fam_path: Path) -> list[str]:
    command = [
        "python", "-m", "cli.run_gwas_covariate",
        "--workspace", str(workspace),
        "--job-id", args.job_id,
        "--prefix", args.prefix,
        "--q", str(q_path),
        "--fam", str(fam_path),
        "--k", str(args.k),
    ]
    if args.source_genotype_scope_id:
        command.extend(["--source-genotype-scope-id", args.source_genotype_scope_id])
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
    safe_prefix = re.sub(r"[^A-Za-z0-9._-]+", "_", manifest["prefix"]).strip("._") or "cohort"
    bundle = workspace / "reproducibility" / STAGE / f"{safe_prefix}.{identity12}" / run_id
    bundle.mkdir(parents=True, exist_ok=False)
    canonical = shlex.join(command)
    commands_path = bundle / "commands.sh"
    commands_path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + canonical + "\n", encoding="utf-8")
    commands_path.chmod(0o755)
    (bundle / "environment.txt").write_text(environment_text, encoding="utf-8")
    _write_json(bundle / "progress.json", {
        "stage": STAGE,
        "status": manifest["status"],
        "run_id": run_id,
        "manifest_path": str(manifest_path.relative_to(workspace)),
    })
    _write_json(bundle / "provenance.json", {
        "stage": STAGE,
        "run_id": run_id,
        "runner_command": canonical,
        "inputs": manifest.get("inputs", []),
        "artifacts": manifest.get("artifacts", {}),
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


def _publish(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.publishing")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--q", required=True, type=Path)
    parser.add_argument("--fam", required=True, type=Path)
    parser.add_argument("--k", required=True, type=int)
    parser.add_argument("--source-genotype-scope-id", default="")
    parser.add_argument("--log-file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = args.workspace.resolve()
    final_dir = workspace / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    q_path = args.q.resolve()
    fam_path = args.fam.resolve()
    run_id = new_run_id(STAGE)
    started = datetime.now(timezone.utc)
    started_clock = time.monotonic()
    failed_manifest_path = final_dir / f"{args.prefix}.{STAGE}.failed.manifest.json"
    command = _runner_command(args, workspace, q_path, fam_path)
    parameters = {
        "k": args.k,
        "fam_id_column": "IID",
        "effective_covariate_count": max(args.k - 1, 0),
        "omitted_covariate": f"Q{args.k}" if args.k > 0 else "",
        "q_row_sum_tolerance": Q_ROW_SUM_TOLERANCE,
    }
    environment_text = "\n".join([
        "runner=cli.run_gwas_covariate.py",
        f"runtime_environment={os.environ.get('CONDA_DEFAULT_ENV', 'unknown')}",
        f"python_resolved={Path(sys.executable).resolve()}",
        f"python_version={platform.python_version()}",
        f"platform={platform.platform()}",
    ]) + "\n"
    manifest: dict[str, Any] = {
        "manifest_version": "1.0",
        "stage": STAGE,
        "prefix": args.prefix,
        "job_id": args.job_id,
        "run_id": run_id,
        "status": "failed",
        "timestamp": started.isoformat(),
        "start_time": started.isoformat(),
        "end_time": started.isoformat(),
        "elapsed_sec": 0.0,
        "cmd": command,
        "workspace_root": str(workspace),
        "workspace": str(final_dir),
        "input_dir": str(workspace / "input"),
        "parameters": parameters,
        "metrics": {},
        "artifacts": {},
    }
    identity12 = "failed"
    manifest_path = failed_manifest_path
    try:
        if not q_path.is_file():
            raise FileNotFoundError("Authoritative ADMIXTURE Q does not exist")
        if not fam_path.is_file():
            raise FileNotFoundError("Authoritative sample FAM does not exist")
        source_scope = args.source_genotype_scope_id
        if not re.fullmatch(r"genotype:[0-9a-f]{64}", source_scope):
            raise ValueError("verified source genotype scope is required")

        q_sha = sha256_file(q_path)
        fam_sha = sha256_file(fam_path)
        covariate_scope = build_covariate_scope_id(
            q_sha256=q_sha,
            fam_sha256=fam_sha,
            source_genotype_scope_id=source_scope,
            k=args.k,
        )
        recipe = build_identity_recipe(
            stage=STAGE,
            required_artifact_roles=["admixture_q", "sample_fam"],
            parameters=parameters,
            method_version="admixture_q_to_tassel_covariate.v1",
            contract_version="v1",
            tool_versions={"python": platform.python_version()},
        )
        identity = materialize_stage_identity(
            recipe,
            {
                "admixture_q": {"sha256": q_sha},
                "sample_fam": {"sha256": fam_sha},
            },
            {
                "genotype_scope_id": source_scope,
                "covariate_scope_id": covariate_scope,
            },
        )
        identity12 = identity["analysis_scope_id"].split(":", 1)[1][:12]
        manifest.update({
            "genotype_scope_id": source_scope,
            "source_genotype_scope_id": source_scope,
            "covariate_scope_id": covariate_scope,
            "analysis_scope_id": identity["analysis_scope_id"],
            "identity_recipe": recipe,
            "inputs": [_input(q_path, "admixture_q"), _input(fam_path, "sample_fam")],
        })

        sample_ids, values, metrics = validate_covariate_inputs(q_path, fam_path, args.k)
        attempt_dir = workspace / ".attempts" / run_id
        attempt_dir.mkdir(parents=True, exist_ok=False)
        table_work = attempt_dir / "covariates.txt"
        environment_work = attempt_dir / "environment.txt"
        report_work = attempt_dir / "report.md"
        _write_tassel_covariates(table_work, sample_ids, values, args.k)
        environment_work.write_text(environment_text, encoding="utf-8")
        report_work.write_text(
            "# GWAS TASSEL covariate preparation\n\n"
            f"- Source genotype scope: `{source_scope}`\n"
            f"- Covariate scope: `{covariate_scope}`\n"
            f"- Analysis scope: `{identity['analysis_scope_id']}`\n"
            f"- Samples: {metrics['sample_count']}\n"
            f"- ADMIXTURE K: {args.k}\n"
            "- FAM sample identifier: `IID` (column 2)\n"
            f"- Effective covariates: {args.k - 1}\n"
            f"- Omitted by downstream `-excludeLastTrait`: `Q{args.k}`\n",
            encoding="utf-8",
        )

        stem = f"{args.prefix}.{STAGE}.{identity12}"
        table_final = final_dir / f"{stem}.covariates.txt"
        environment_final = final_dir / f"{stem}.environment.txt"
        report_final = final_dir / f"{stem}.report.md"
        manifest_path = final_dir / f"{stem}.manifest.json"
        _publish(table_work, table_final)
        _publish(environment_work, environment_final)
        _publish(report_work, report_final)

        manifest.update({
            "status": "success",
            "metrics": metrics,
            "tool_versions": {"python": platform.python_version()},
            "software": {"python": {"resolved_path": str(Path(sys.executable).resolve()), "version": platform.python_version()}},
            "artifacts": {
                "covariate_table": _artifact(workspace, table_final, "covariate_table", "text/tab-separated-values"),
                "environment": _artifact(workspace, environment_final, "environment", "text/plain"),
                "report": _artifact(workspace, report_final, "report", "text/markdown"),
            },
        })
        manifest["end_time"] = datetime.now(timezone.utc).isoformat()
        manifest["elapsed_sec"] = round(time.monotonic() - started_clock, 6)
        manifest_work = attempt_dir / "manifest.json"
        _write_json(manifest_work, manifest)
        _publish(manifest_work, manifest_path)
        failed_manifest_path.unlink(missing_ok=True)
        _write_reproducibility(
            workspace, identity12, run_id, command, manifest_path, manifest, environment_text
        )
        return 0
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["artifacts"] = {}
        manifest["error"] = str(exc)
        manifest["end_time"] = datetime.now(timezone.utc).isoformat()
        manifest["elapsed_sec"] = round(time.monotonic() - started_clock, 6)
        _write_json(failed_manifest_path, manifest)
        if args.log_file:
            args.log_file.parent.mkdir(parents=True, exist_ok=True)
            args.log_file.write_text(f"ERROR: {exc}\n", encoding="utf-8")
        _write_reproducibility(
            workspace, identity12, run_id, command, failed_manifest_path, manifest, environment_text
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
