#!/usr/bin/env python3
"""Validate one phenotype and write a TASSEL trait table."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import shlex
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gfa_core.gwas_scientific_identity import (
    build_identity_recipe,
    build_phenotype_scope_id,
    materialize_stage_identity,
    new_run_id,
)


STAGE = "gwas_trait"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_phenotype(
    path: Path,
    sample_column: str,
    trait_column: str,
) -> tuple[list[str], list[str], list[float]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        columns = reader.fieldnames or []
        if sample_column not in columns:
            raise ValueError(f"selected sample column does not exist: {sample_column}")
        if trait_column not in columns:
            raise ValueError(f"selected trait column does not exist: {trait_column}")

        sample_ids: list[str] = []
        trait_texts: list[str] = []
        trait_values: list[float] = []
        for row in reader:
            sample_id = str(row.get(sample_column) or "").strip()
            trait_text = str(row.get(trait_column) or "").strip()
            if not sample_id:
                raise ValueError("sample IDs must be nonempty")
            if not trait_text:
                raise ValueError("missing values are not allowed")
            try:
                trait_value = float(trait_text)
            except ValueError as exc:
                raise ValueError("trait values must be numeric") from exc
            if not math.isfinite(trait_value):
                raise ValueError("trait values must be finite")
            sample_ids.append(sample_id)
            trait_texts.append(trait_text)
            trait_values.append(trait_value)

    if not sample_ids:
        raise ValueError("phenotype table contains no data rows")
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("sample IDs must be unique")
    if min(trait_values) == max(trait_values):
        raise ValueError("trait variance must be positive")
    return sample_ids, trait_texts, trait_values


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


def _runner_command(args: argparse.Namespace, workspace: Path, phenotype: Path) -> list[str]:
    command = [
        "python",
        "-m",
        "cli.run_gwas_trait",
        "--workspace",
        str(workspace),
        "--job-id",
        args.job_id,
        "--prefix",
        args.prefix,
        "--phenotype",
        str(phenotype),
        "--sample-column",
        args.sample_column,
        "--trait-column",
        args.trait_column,
        "--trait-name",
        args.trait_name,
    ]
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
    (bundle / "commands.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n" + canonical + "\n",
        encoding="utf-8",
    )
    (bundle / "environment.txt").write_text(environment_text, encoding="utf-8")
    _write_json(
        bundle / "progress.json",
        {
            "stage": STAGE,
            "status": manifest["status"],
            "run_id": run_id,
            "manifest_path": str(manifest_path.relative_to(workspace)),
        },
    )
    _write_json(
        bundle / "provenance.json",
        {
            "stage": STAGE,
            "run_id": run_id,
            "runner_command": canonical,
            "software": manifest.get("software", {}),
            "artifacts": manifest.get("artifacts", {}),
        },
    )
    targets = [manifest_path]
    for artifact in manifest.get("artifacts", {}).values():
        candidate = workspace / artifact["path"]
        if candidate.is_file():
            targets.append(candidate)
    (bundle / "checksums.sha256").write_text(
        "".join(
            f"{sha256_file(path)}  {path.relative_to(workspace)}\n"
            for path in sorted(targets)
        ),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--phenotype", required=True, type=Path)
    parser.add_argument("--sample-column", required=True)
    parser.add_argument("--trait-column", required=True)
    parser.add_argument("--trait-name", required=True)
    parser.add_argument("--log-file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = args.workspace.resolve()
    final_dir = workspace / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    phenotype = args.phenotype.resolve()
    run_id = new_run_id(STAGE)
    started = datetime.now(timezone.utc)
    started_clock = time.monotonic()
    failed_manifest = final_dir / f"{args.prefix}.{STAGE}.failed.manifest.json"
    command = _runner_command(args, workspace, phenotype)
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
        "artifacts": {},
        "cmd": command,
        "workspace_root": str(workspace),
        "workspace": str(final_dir),
        "input_dir": str(workspace / "input"),
    }
    try:
        if not phenotype.is_file():
            raise FileNotFoundError("Authoritative phenotype table does not exist")
        if not args.sample_column.strip():
            raise ValueError("sample column must be explicit")
        if not args.trait_column.strip():
            raise ValueError("trait column must be explicit")
        trait_name = args.trait_name.strip()
        if not trait_name or any(character in trait_name for character in "\t\r\n"):
            raise ValueError("trait name must be a nonempty single field")

        phenotype_sha = sha256_file(phenotype)
        sample_ids, trait_texts, trait_values = _read_phenotype(
            phenotype, args.sample_column, args.trait_column
        )
        phenotype_scope = build_phenotype_scope_id(
            phenotype_sha256=phenotype_sha,
            sample_column=args.sample_column,
            trait_column=args.trait_column,
            trait_name=trait_name,
            retained_sample_ids=sample_ids,
        )
        parameters = {
            "sample_column": args.sample_column,
            "trait_column": args.trait_column,
            "trait_name": trait_name,
            "missing_policy": "reject",
        }
        python_version = platform.python_version()
        recipe = build_identity_recipe(
            stage=STAGE,
            required_artifact_roles=["phenotype_table"],
            parameters=parameters,
            method_version="tassel_trait_format.v1",
            contract_version="v1",
            tool_versions={"python": python_version},
        )
        identity = materialize_stage_identity(
            recipe,
            {"phenotype_table": {"sha256": phenotype_sha}},
            {"phenotype_scope_id": phenotype_scope},
        )
        identity12 = identity["analysis_scope_id"].split(":", 1)[1][:12]
        attempt_dir = workspace / ".attempts" / run_id
        attempt_dir.mkdir(parents=True, exist_ok=False)
        trait_work = attempt_dir / "trait.txt"
        trait_work.write_text(
            f"<Trait>\t{trait_name}\n"
            + "".join(
                f"{sample_id}\t{trait_text}\n"
                for sample_id, trait_text in zip(sample_ids, trait_texts)
            ),
            encoding="utf-8",
        )

        stem = f"{args.prefix}.{STAGE}.{identity12}"
        trait_final = final_dir / f"{stem}.trait.txt"
        environment_final = final_dir / f"{stem}.environment.txt"
        report_final = final_dir / f"{stem}.report.md"
        manifest_path = final_dir / f"{stem}.manifest.json"
        shutil.copy2(trait_work, trait_final)
        environment_text = "\n".join(
            [
                "runner=cli.run_gwas_trait.py",
                f"runtime_environment={os.environ.get('CONDA_DEFAULT_ENV', 'unknown')}",
                f"python_resolved={Path(sys.executable).resolve()}",
                f"python_version={python_version}",
                f"platform={platform.platform()}",
            ]
        ) + "\n"
        environment_final.write_text(environment_text, encoding="utf-8")
        report_final.write_text(
            "# GWAS phenotype preparation\n\n"
            f"- Phenotype scope: `{phenotype_scope}`\n"
            f"- Analysis scope: `{identity['analysis_scope_id']}`\n"
            f"- Sample column: `{args.sample_column}`\n"
            f"- Trait column: `{args.trait_column}`\n"
            f"- Trait name: `{trait_name}`\n"
            "- Missing policy: `reject`\n"
            f"- Samples: {len(sample_ids)}\n",
            encoding="utf-8",
        )
        mean = sum(trait_values) / len(trait_values)
        variance = sum((value - mean) ** 2 for value in trait_values) / len(trait_values)
        manifest.update(
            {
                "status": "success",
                "phenotype_scope_id": phenotype_scope,
                "analysis_scope_id": identity["analysis_scope_id"],
                "identity_recipe": recipe,
                "inputs": {
                    "phenotype_table": {
                        "path": str(phenotype),
                        "size": phenotype.stat().st_size,
                        "sha256": phenotype_sha,
                    }
                },
                "parameters": parameters,
                "metrics": {
                    "sample_count": len(sample_ids),
                    "sample_order_conservation": True,
                    "value_pair_conservation": True,
                    "trait_numeric_finite": True,
                    "trait_variance_positive": True,
                    "trait_variance": variance,
                },
                "software": {
                    "python": {
                        "resolved_path": str(Path(sys.executable).resolve()),
                        "version": python_version,
                    }
                },
                "artifacts": {
                    "trait_table": _artifact(
                        workspace, trait_final, "trait_table", "text/tab-separated-values"
                    ),
                    "environment": _artifact(
                        workspace, environment_final, "environment", "text/plain"
                    ),
                    "report": _artifact(workspace, report_final, "report", "text/markdown"),
                },
            }
        )
        manifest["end_time"] = datetime.now(timezone.utc).isoformat()
        manifest["elapsed_sec"] = round(time.monotonic() - started_clock, 6)
        _write_json(manifest_path, manifest)
        failed_manifest.unlink(missing_ok=True)
        _write_reproducibility(
            workspace,
            identity12,
            run_id,
            command,
            manifest_path,
            manifest,
            environment_text,
        )
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
