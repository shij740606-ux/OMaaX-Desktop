#!/usr/bin/env python3
"""Render atomic EMMAX Manhattan and QQ plots with CMplot."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import platform
import re
import shlex
import shutil
import struct
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from gfa_core.gwas_scientific_identity import build_identity_recipe, materialize_stage_identity, new_run_id


STAGE = "gwas_emplot"
METHOD_VERSION = "cmplot_emmax_manhattan_qq.v1"
CONTRACT_VERSION = "v1"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SCOPES = (
    ("genotype_scope_id", "genotype"),
    ("phenotype_scope_id", "phenotype"),
    ("source_model_scope_id", "gwas-model"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_prefix(prefix: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", prefix).strip("._") or "cohort"


def _require_inside(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} must be contained in the job workspace") from exc


def _natural_contig_key(contig: str) -> Tuple[Tuple[Tuple[int, Any], ...], str]:
    tokens: List[Tuple[int, Any]] = []
    for token in re.split(r"(\d+)", contig.casefold()):
        if not token:
            continue
        tokens.append((0, int(token)) if token.isdigit() else (1, token))
    return tuple(tokens), contig


def _sanitized_error(error: Exception) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(error)).strip() or error.__class__.__name__


def _markdown_table_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|")


def _authoritative_input(
    path: Path, producer_manifest_path: Path, scopes: Mapping[str, str], tested: int,
    workspace: Path,
) -> Dict[str, Any]:
    role = "emmax_pvalues"
    path, producer_manifest_path = path.resolve(), producer_manifest_path.resolve()
    if not path.is_file() or not producer_manifest_path.is_file():
        raise FileNotFoundError("authoritative emmax_pvalues or producer Manifest does not exist")
    try:
        producer = json.loads(producer_manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("gwas_emmax producer Manifest is invalid JSON") from exc
    if producer.get("manifest_version") != "1.0":
        raise ValueError("gwas_emmax producer Manifest version must be 1.0")
    if producer.get("stage") != "gwas_emmax" or producer.get("status") != "success":
        raise ValueError("emmax_pvalues producer Manifest stage/status is invalid")
    run_id = str(producer.get("run_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", run_id):
        raise ValueError("gwas_emmax producer Manifest run_id is empty or unsafe")
    for field, _prefix in SCOPES:
        upstream_field = "model_scope_id" if field == "source_model_scope_id" else field
        if producer.get(upstream_field) != scopes[field]:
            raise ValueError(f"gwas_emmax producer Manifest {upstream_field} does not match")
    producer_tested = (producer.get("metrics") or {}).get("emmax_tested_count")
    if type(producer_tested) is not int or producer_tested <= 0:
        raise ValueError("gwas_emmax producer Manifest emmax_tested_count must be a positive integer")
    if producer_tested != tested:
        raise ValueError("gwas_emmax producer Manifest emmax_tested_count does not match")
    artifact = (producer.get("artifacts") or {}).get(role)
    if not isinstance(artifact, dict) or artifact.get("artifact") != role or artifact.get("role") != role:
        raise ValueError("emmax_pvalues producer Manifest artifact role is invalid")
    relative_text = artifact.get("path")
    if not isinstance(relative_text, str) or not relative_text or Path(relative_text).is_absolute():
        raise ValueError("emmax_pvalues producer Manifest relative path is invalid")
    relative_path = (workspace.resolve() / relative_text).resolve()
    _require_inside(relative_path, workspace, "emmax_pvalues producer relative path")
    if relative_path != path:
        raise ValueError("emmax_pvalues producer Manifest relative path does not match full_path")
    if Path(str(artifact.get("full_path") or "")).resolve() != path:
        raise ValueError("emmax_pvalues producer Manifest artifact path does not match")
    size, digest = path.stat().st_size, sha256_file(path)
    if artifact.get("size_bytes", artifact.get("size")) != size:
        raise ValueError("emmax_pvalues producer Manifest artifact size does not match")
    if artifact.get("sha256") != digest:
        raise ValueError("emmax_pvalues producer Manifest artifact SHA256 does not match")
    return {
        "source": "manifest", "artifact": role, "role": role, "path": str(path),
        "full_path": str(path), "size": size, "size_bytes": size, "sha256": digest,
        "producer_stage": "gwas_emmax", "producer_run_id": run_id,
        "producer_manifest_path": str(producer_manifest_path),
        "producer_manifest_sha256": sha256_file(producer_manifest_path),
    }


def _revalidate_input(record: Mapping[str, Any]) -> None:
    path = Path(str(record["full_path"])).resolve()
    if (not path.is_file() or path.stat().st_size != record["size_bytes"]
            or sha256_file(path) != record["sha256"]):
        raise RuntimeError("authoritative emmax_pvalues changed during plotting")
    producer = Path(str(record["producer_manifest_path"])).resolve()
    if not producer.is_file() or sha256_file(producer) != record["producer_manifest_sha256"]:
        raise RuntimeError("authoritative emmax_pvalues producer Manifest changed during plotting")


def _staged_artifact(
    workspace: Path, source: Path, destination: Path, role: str, media_type: str
) -> Dict[str, Any]:
    size = source.stat().st_size
    return {
        "artifact": role,
        "role": role,
        "path": str(destination.relative_to(workspace)),
        "full_path": str(destination.resolve()),
        "type": media_type,
        "media_type": media_type,
        "size": size,
        "size_bytes": size,
        "sha256": sha256_file(source),
    }


def _read_pvalues(path: Path) -> Tuple[List[Tuple[str, int, float]], Dict[str, int]]:
    if not path.is_file():
        raise FileNotFoundError("Authoritative emmax_pvalues does not exist")
    with path.open("r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
        if not lines:
            raise ValueError("EMMAX p-value table header is missing")
        if lines[0] != "chr\tpos\tp":
            raise ValueError("EMMAX p-value table header must be exactly chr\\tpos\\tp")
        rows: List[Tuple[str, int, float]] = []
        coordinates: Counter[Tuple[str, int]] = Counter()
        for line_number, line in enumerate(lines[1:], start=2):
            fields = line.split("\t")
            if len(fields) != 3:
                raise ValueError(f"EMMAX row {line_number} must contain exactly three tab-separated fields")
            contig, position_text, p_text = fields
            if not contig:
                raise ValueError(f"EMMAX row {line_number} chr must be nonempty")
            try:
                position = int(position_text)
            except ValueError as exc:
                raise ValueError(f"EMMAX row {line_number} pos must be a positive integer") from exc
            if position <= 0:
                raise ValueError(f"EMMAX row {line_number} pos must be a positive integer")
            try:
                p_value = float(p_text)
            except ValueError as exc:
                raise ValueError(f"EMMAX row {line_number} p must be finite and in (0,1]") from exc
            if not math.isfinite(p_value):
                raise ValueError(f"EMMAX row {line_number} p must be finite")
            if not 0.0 < p_value <= 1.0:
                raise ValueError(f"EMMAX row {line_number} p must be in (0,1]")
            rows.append((contig, position, p_value))
            coordinates[(contig, position)] += 1
    if not rows:
        raise ValueError("EMMAX p-value table contains zero plottable rows")
    duplicates = sum(count - 1 for count in coordinates.values() if count > 1)
    return rows, {
        "valid_p_rows": len(rows),
        "plotted_rows": len(rows),
        "duplicate_coordinate_count": duplicates,
    }


def _r_version(executable: Path) -> str:
    completed = subprocess.run(
        [str(executable), "--version"], text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Rscript version probe failed")
    first = (completed.stdout or "").splitlines()
    if not first:
        raise RuntimeError("Rscript version could not be proven")
    return first[0].strip()


def _write_plot_input(
    path: Path, rows: Sequence[Tuple[str, int, float]], contig_to_plot: Mapping[str, int]
) -> None:
    lines = ["SNP\tChromosome\tPosition\tP"]
    lines.extend(
        f"hypothesis_{index}\t{contig_to_plot[contig]}\t{position}\t{p_value:.17g}"
        for index, (contig, position, p_value) in enumerate(rows, start=1)
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _r_source() -> str:
    return """args <- commandArgs(trailingOnly=TRUE)
suppressPackageStartupMessages(library(CMplot))
cat(paste0("CMplot_version=", as.character(packageVersion("CMplot")), "\\n"))
emmax <- read.table(args[[1]], header=TRUE, sep="\\t", check.names=FALSE)
png("emmax.manhattan.png", width=2400, height=1400, res=200)
CMplot(emmax, plot.type="m", LOG10=TRUE, threshold=as.numeric(args[[2]]), file.output=FALSE, verbose=FALSE)
dev.off()
png("emmax.qq.png", width=1800, height=1800, res=200)
CMplot(emmax, plot.type="q", LOG10=TRUE, file.output=FALSE, verbose=FALSE)
dev.off()
"""


def _png_dimensions(path: Path, role: str) -> Tuple[int, int]:
    if not path.is_file():
        raise RuntimeError(f"CMplot did not produce required image: {role}")
    data = path.read_bytes()
    if len(data) < 33 or data[:8] != PNG_SIGNATURE:
        raise ValueError(f"CMplot image is not a valid PNG: {role}")
    if struct.unpack(">I", data[8:12])[0] != 13 or data[12:16] != b"IHDR":
        raise ValueError(f"CMplot image has an invalid IHDR: {role}")
    width, height = struct.unpack(">II", data[16:24])
    if width <= 0 or height <= 0:
        raise ValueError(f"CMplot image dimensions must be positive: {role}")
    return width, height


def _runner_command(args: argparse.Namespace, paths: Mapping[str, Path]) -> List[str]:
    return [
        "python", "-m", "cli.run_gwas_emmax_plot",
        "--workspace", str(args.workspace.resolve()), "--job-id", args.job_id,
        "--prefix", args.prefix, "--emmax-pvalues", str(paths["emmax_pvalues"]),
        "--emmax-manifest", str(args.emmax_manifest.resolve()),
        "--genotype-scope-id", args.genotype_scope_id,
        "--phenotype-scope-id", args.phenotype_scope_id,
        "--source-model-scope-id", args.source_model_scope_id,
        "--emmax-tested-hypotheses", str(args.emmax_tested_hypotheses),
        "--rscript", str(args.rscript.resolve()), "--log-file", str(args.log_file.resolve()),
    ]


def _write_reproducibility(
    workspace: Path, identity12: str, run_id: str, command: Sequence[str], manifest_path: Path,
    public_manifest: Path, manifest: Mapping[str, Any], environment: str,
    artifact_sources: Optional[Mapping[str, Path]] = None,
) -> Path:
    bundle = workspace / "reproducibility" / STAGE / f"{_safe_prefix(str(manifest['prefix']))}.{identity12}" / run_id
    bundle.mkdir(parents=True, exist_ok=False)
    canonical = shlex.join(command)
    commands = bundle / "commands.sh"
    commands.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + canonical + "\n", encoding="utf-8")
    commands.chmod(0o755)
    (bundle / "environment.txt").write_text(environment, encoding="utf-8")
    _write_json(bundle / "progress.json", {
        "stage": STAGE, "status": manifest["status"], "run_id": run_id,
        "manifest_path": str(public_manifest.relative_to(workspace)),
    })
    _write_json(bundle / "provenance.json", {
        "stage": STAGE, "run_id": run_id, "runner_command": canonical,
        "software": manifest.get("software", {}), "inputs": manifest.get("inputs", []),
        "artifacts": manifest.get("artifacts", {}),
    })
    entries = [(manifest_path, str(public_manifest.relative_to(workspace)))]
    for role, artifact in manifest.get("artifacts", {}).items():
        source = artifact_sources.get(role) if artifact_sources else workspace / artifact["path"]
        if source and source.is_file():
            entries.append((source, artifact["path"]))
    (bundle / "checksums.sha256").write_text(
        "".join(f"{sha256_file(path)}  {label}\n" for path, label in sorted(entries, key=lambda item: item[1])),
        encoding="utf-8",
    )
    return bundle


def _publish_atomically(
    sources: Mapping[str, Path], destinations: Mapping[str, Path], manifest_source: Path,
    manifest_destination: Path, lock_path: Path, started_at: str,
) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if manifest_destination.is_file():
            try:
                previous = json.loads(manifest_destination.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous = {}
            if previous.get("status") == "success" and str(previous.get("start_time", "")) > started_at:
                return False
        token = uuid.uuid4().hex
        staged: Dict[Path, Path] = {}
        for role, destination in destinations.items():
            temporary = destination.with_name(f".{destination.name}.{token}.publishing")
            shutil.copy2(sources[role], temporary)
            staged[destination] = temporary
        temporary_manifest = manifest_destination.with_name(f".{manifest_destination.name}.{token}.publishing")
        shutil.copy2(manifest_source, temporary_manifest)
        staged[manifest_destination] = temporary_manifest
        backups: Dict[Path, Optional[Path]] = {}
        try:
            for destination in staged:
                if destination.is_file():
                    backup = destination.with_name(f".{destination.name}.{token}.rollback")
                    shutil.copy2(destination, backup)
                    backups[destination] = backup
                else:
                    backups[destination] = None
            for destination, temporary in staged.items():
                os.replace(temporary, destination)
        except Exception:
            for destination, backup in backups.items():
                if backup is None:
                    destination.unlink(missing_ok=True)
                elif backup.is_file():
                    os.replace(backup, destination)
            for temporary in staged.values():
                temporary.unlink(missing_ok=True)
            raise
        finally:
            for backup in backups.values():
                if backup is not None:
                    backup.unlink(missing_ok=True)
    return True


def _publish_with_bundle(
    sources: Mapping[str, Path], destinations: Mapping[str, Path], manifest_source: Path,
    manifest_destination: Path, lock_path: Path, started_at: str, bundle: Path,
) -> bool:
    try:
        published = _publish_atomically(
            sources, destinations, manifest_source, manifest_destination, lock_path, started_at
        )
    except Exception:
        shutil.rmtree(bundle, ignore_errors=True)
        raise
    if not published:
        shutil.rmtree(bundle, ignore_errors=True)
    return published


def _publish_failed_manifest(
    manifest: Mapping[str, Any], path: Path, lock_path: Path,
    success_manifest_path: Optional[Path] = None,
) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        started_at = str(manifest.get("start_time") or "")
        for candidate in (success_manifest_path, path):
            if candidate is None or not candidate.is_file():
                continue
            try:
                existing = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if str(existing.get("start_time") or "") > started_at:
                return False
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.publishing")
        _write_json(temporary, manifest)
        os.replace(temporary, path)
    return True


def _clear_failed_manifest_if_winner(
    failed_path: Path, lock_path: Path, success_manifest_path: Path, run_id: str,
) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            success = json.loads(success_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if success.get("status") != "success" or success.get("run_id") != run_id:
            return False
        if not failed_path.is_file():
            return True
        try:
            failed = json.loads(failed_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if str(failed.get("start_time") or "") > str(success.get("start_time") or ""):
            return False
        failed_path.unlink()
        return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--emmax-pvalues", required=True, type=Path)
    parser.add_argument("--emmax-manifest", required=True, type=Path)
    parser.add_argument("--genotype-scope-id", required=True)
    parser.add_argument("--phenotype-scope-id", required=True)
    parser.add_argument("--source-model-scope-id", required=True)
    parser.add_argument("--emmax-tested-hypotheses", required=True, type=int)
    parser.add_argument("--rscript", required=True, type=Path)
    parser.add_argument("--log-file", required=True, type=Path)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = args.workspace.resolve()
    final_dir = workspace / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    run_id = new_run_id(STAGE)
    started = datetime.now(timezone.utc)
    started_clock = time.monotonic()
    safe_prefix = _safe_prefix(args.prefix)
    failed_manifest = final_dir / f"{safe_prefix}.{STAGE}.failed.manifest.json"
    paths = {"emmax_pvalues": args.emmax_pvalues.resolve()}
    command = _runner_command(args, paths)
    tested = args.emmax_tested_hypotheses
    threshold = 0.05 / tested if tested > 0 else 0.0
    parameters: Dict[str, Any] = {
        "tested_hypotheses": tested,
        "bonferroni_threshold": threshold,
        "plots": ["manhattan", "qq"],
        "plot_devices": {
            "manhattan": {"width_px": 2400, "height_px": 1400, "resolution_dpi": 200},
            "qq": {"width_px": 1800, "height_px": 1800, "resolution_dpi": 200},
        },
        "cmplot": {"log10": True, "file_output": False},
        "contig_mapping_policy": "natural_v1",
    }
    manifest: Dict[str, Any] = {
        "manifest_version": "1.0", "stage": STAGE, "prefix": args.prefix,
        "job_id": args.job_id, "run_id": run_id, "status": "failed",
        "timestamp": started.isoformat(), "start_time": started.isoformat(),
        "end_time": started.isoformat(), "elapsed_sec": 0.0, "cmd": command,
        "workspace_root": str(workspace), "workspace": str(final_dir),
        "input_dir": str(workspace / "input"), "parameters": parameters,
        "metrics": {}, "artifacts": {},
    }
    environment = ""
    log_is_safe = True
    identity12 = "failed"
    success_bundle: Optional[Path] = None
    success_manifest: Optional[Path] = None
    attempt = workspace / ".attempts" / run_id
    attempt.mkdir(parents=True, exist_ok=False)
    try:
        scopes = {name: str(getattr(args, name)) for name, _prefix in SCOPES}
        for name, prefix in SCOPES:
            if not re.fullmatch(prefix + r":[0-9a-f]{64}", scopes[name]):
                raise ValueError(f"{name} is invalid")
        manifest.update(scopes)
        input_path, log_path = paths["emmax_pvalues"], args.log_file.resolve()
        _require_inside(input_path, workspace, "authoritative emmax_pvalues")
        _require_inside(args.emmax_manifest, workspace, "gwas_emmax producer Manifest")
        producer_path = args.emmax_manifest.resolve()
        if (log_path in {input_path, producer_path}
                or (log_path.exists() and any(
                    candidate.exists() and os.path.samefile(log_path, candidate)
                    for candidate in (input_path, producer_path)
                ))):
            log_is_safe = False
            raise ValueError("log path must not alias an authoritative input or producer Manifest")
        if type(tested) is not int or tested <= 0:
            raise ValueError("emmax tested_hypotheses must be a positive integer")
        input_record = _authoritative_input(
            input_path, args.emmax_manifest, scopes, tested, workspace
        )
        manifest["inputs"] = [input_record]
        rows, row_metrics = _read_pvalues(input_path)
        if tested < row_metrics["valid_p_rows"]:
            raise ValueError("emmax tested_hypotheses must be >= valid_p_rows >= plotted_rows")
        contig_order = sorted({row[0] for row in rows}, key=_natural_contig_key)
        contig_to_plot = {contig: index for index, contig in enumerate(contig_order, 1)}
        contig_counts = {contig: sum(row[0] == contig for row in rows) for contig in contig_order}
        rscript = args.rscript.resolve()
        if not rscript.is_file() or not os.access(str(rscript), os.X_OK):
            raise FileNotFoundError("explicit Rscript executable was not found or is not executable")
        r_version = _r_version(rscript)
        plot_input = attempt / "emmax.cmplot.tsv"
        _write_plot_input(plot_input, rows, contig_to_plot)
        script = attempt / "run_cmplot.R"
        script.write_text(_r_source(), encoding="utf-8")
        completed = subprocess.run(
            [str(rscript), str(script), str(plot_input), str(threshold)],
            cwd=attempt, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        log_text = completed.stdout or ""
        attempt_log = attempt / "cmplot.log"
        attempt_log.write_text(log_text, encoding="utf-8")
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(attempt_log, args.log_file)
        if completed.returncode != 0:
            raise RuntimeError(f"CMplot failed with exit code {completed.returncode}")
        _revalidate_input(input_record)
        match = re.search(r"^CMplot_version=([^\r\n]+)$", log_text, re.MULTILINE)
        if not match:
            raise RuntimeError("CMplot version could not be proven")
        cmplot_version = match.group(1).strip()
        image_sources = {
            "emmax_manhattan": attempt / "emmax.manhattan.png",
            "emmax_qq": attempt / "emmax.qq.png",
        }
        image_dimensions = {role: _png_dimensions(path, role) for role, path in image_sources.items()}
        environment = "\n".join([
            "runner=cli.run_gwas_emmax_plot.py",
            f"runtime_environment={os.environ.get('CONDA_DEFAULT_ENV', 'unknown')}",
            f"python_resolved={Path(sys.executable).resolve()}",
            f"python_version={platform.python_version()}",
            f"rscript_resolved={rscript}", f"r_version={r_version}",
            f"cmplot_version={cmplot_version}", f"platform={platform.platform()}",
            f"tested_hypotheses={tested}",
            f"valid_p_rows={row_metrics['valid_p_rows']}",
            f"plotted_rows={row_metrics['plotted_rows']}",
            f"duplicate_coordinate_count={row_metrics['duplicate_coordinate_count']}",
            f"bonferroni_threshold={threshold}",
        ]) + "\n"
        recipe = build_identity_recipe(
            stage=STAGE, required_artifact_roles=paths.keys(), parameters=parameters,
            method_version=METHOD_VERSION, contract_version=CONTRACT_VERSION,
            tool_versions={"R": r_version, "CMplot": cmplot_version},
        )
        identity = materialize_stage_identity(recipe, {"emmax_pvalues": input_record}, scopes)
        identity12 = identity["analysis_scope_id"].split(":", 1)[1][:12]
        stem = f"{safe_prefix}.{STAGE}.{identity12}"
        success_manifest = final_dir / f"{stem}.manifest.json"
        manifest.update(identity)
        manifest.update({
            "identity_recipe": recipe,
            "software": {
                "R": {"resolved_path": str(rscript), "version": r_version},
                "CMplot": {"version": cmplot_version},
            },
        })
        mapping_rows = "".join(
            f"| {_markdown_table_cell(contig)} | {contig_to_plot[contig]} | {contig_counts[contig]} |\n"
            for contig in contig_order
        )
        report_source, environment_source = attempt / "report.md", attempt / "environment.txt"
        report_source.write_text(
            "# GWAS EMMAX plots\n\n"
            f"- Analysis scope: `{identity['analysis_scope_id']}`\n"
            f"- Source model scope: `{args.source_model_scope_id}`\n"
            f"- Genotype scope: `{args.genotype_scope_id}`\n"
            f"- Phenotype scope: `{args.phenotype_scope_id}`\n"
            "- Contig classification: unavailable; not inferred from contig names.\n\n"
            "## Multiple-testing threshold\n\n"
            "| Model | Tested hypotheses | Bonferroni threshold |\n|---|---:|---:|\n"
            f"| EMMAX | {tested} | {threshold} |\n\n"
            "## Row validation\n\n"
            "| Metric | Count |\n|---|---:|\n"
            f"| Valid p-value rows | {row_metrics['valid_p_rows']} |\n"
            f"| Plotted rows | {row_metrics['plotted_rows']} |\n"
            f"| Duplicate coordinate rows | {row_metrics['duplicate_coordinate_count']} |\n\n"
            "## Reversible contig plotting map\n\n"
            "| Contig | Plot integer | Rows |\n|---|---:|---:|\n" + mapping_rows + "\n"
            f"Duplicate hypotheses were preserved. Duplicate coordinate row count: {row_metrics['duplicate_coordinate_count']}.\n",
            encoding="utf-8",
        )
        environment_source.write_text(environment, encoding="utf-8")
        sources = {**image_sources, "report": report_source, "environment": environment_source}
        destinations = {
            "emmax_manhattan": final_dir / f"{stem}.emmax_manhattan.png",
            "emmax_qq": final_dir / f"{stem}.emmax_qq.png",
            "report": final_dir / f"{stem}.report.md",
            "environment": final_dir / f"{stem}.environment.txt",
        }
        row_metrics.update({
            "tested_hypotheses": tested,
            "bonferroni_threshold": threshold,
            "contig_count": len(contig_order), "contig_order": contig_order,
            "contig_to_plot": contig_to_plot,
            "plot_to_contig": {str(value): key for key, value in contig_to_plot.items()},
            "contig_row_counts": contig_counts,
            "contig_classification": "unavailable; not inferred from contig names",
            "image_dimensions": {
                role: {"width": dims[0], "height": dims[1]} for role, dims in image_dimensions.items()
            },
        })
        media = {"report": "text/markdown", "environment": "text/plain"}
        manifest.update({
            "status": "success", "metrics": row_metrics,
            "artifacts": {
                role: _staged_artifact(workspace, source, destinations[role], role, media.get(role, "image/png"))
                for role, source in sources.items()
            },
            "end_time": datetime.now(timezone.utc).isoformat(),
            "elapsed_sec": round(time.monotonic() - started_clock, 6),
        })
        manifest_source = attempt / "manifest.json"
        _write_json(manifest_source, manifest)
        success_bundle = _write_reproducibility(
            workspace, identity12, run_id, command, manifest_source, success_manifest,
            manifest, environment, sources,
        )
        _revalidate_input(input_record)
        published = _publish_with_bundle(
            sources, destinations, manifest_source, success_manifest,
            workspace / ".locks" / f"{stem}.lock", started.isoformat(), success_bundle,
        )
        if published:
            _clear_failed_manifest_if_winner(
                failed_manifest, workspace / ".locks" / f"{safe_prefix}.{STAGE}.failed.lock",
                success_manifest, run_id,
            )
        return 0
    except Exception as exc:
        error = _sanitized_error(exc)
        if success_bundle is not None and success_bundle.is_dir():
            shutil.rmtree(success_bundle)
        manifest.update({
            "status": "failed", "artifacts": {}, "error": error,
            "end_time": datetime.now(timezone.utc).isoformat(),
            "elapsed_sec": round(time.monotonic() - started_clock, 6),
        })
        failure_published = _publish_failed_manifest(
            manifest, failed_manifest, workspace / ".locks" / f"{safe_prefix}.{STAGE}.failed.lock",
            success_manifest,
        )
        if log_is_safe:
            args.log_file.parent.mkdir(parents=True, exist_ok=True)
            with args.log_file.open("a", encoding="utf-8") as handle:
                handle.write(("" if not args.log_file.stat().st_size else "\n") + f"ERROR: {error}\n")
        failure_source = attempt / "failed.manifest.json"
        _write_json(failure_source, manifest)
        failure_bundle = _write_reproducibility(
            workspace, identity12, run_id, command, failure_source, failed_manifest,
            manifest, environment,
        )
        if not failure_published:
            shutil.rmtree(failure_bundle, ignore_errors=True)
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
