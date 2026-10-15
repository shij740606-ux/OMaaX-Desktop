"""PlantAI adapter for immutable validated workflow packages (no scientific defaults added)."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from datetime import datetime, timezone
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SPECS = ROOT / "workflow_backends" / "stages.json"
PATH_KEYS = {"samples", "input", "annotations", "abundance", "taxon_names", "counts", "metadata",
             "selected", "universe", "gmt", "gene_map", "databases", "fasta", "gtf", "host_fasta",
             "hostremoval_reference", "spaceranger_reference", "transcript_fasta"}


def read_json(path):
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Configuration must be a JSON object")
    return value


def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def resolve_path(value, base, workspace):
    text = str(value)
    if text.startswith("@"):
        match = re.fullmatch(r"@([a-z0-9_]+):(.+)", text)
        if not match:
            raise ValueError("Use @stage:relative/output/path for upstream results")
        stage, relative = match.groups()
        manifest = read_json(workspace / "final" / f"{stage}.manifest.json")
        if manifest.get("status") != "success" or manifest.get("stage") != stage:
            raise ValueError(f"Upstream {stage} has no successful result")
        root = Path(manifest["output_dir"]).resolve()
        container_root = Path("/job/workspace")
        if root.is_relative_to(container_root) and workspace != container_root:
            root = workspace / root.relative_to(container_root)
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Upstream reference escapes its output directory")
    else:
        path = Path(text).expanduser()
        path = (path if path.is_absolute() else base / path).resolve()
    if not path.exists():
        raise ValueError(f"Missing input: {path}")
    return str(path)


def prepare_config(source, spec, workspace, output):
    cfg = read_json(source)
    missing = [key for key in spec["required_config"] if cfg.get(key) in (None, "")]
    if missing:
        raise ValueError("Missing configuration fields: " + ", ".join(missing))
    if any(isinstance(cfg[key], str) and cfg[key].startswith("REPLACE_") for key in spec["required_config"]):
        raise ValueError("Replace example placeholders with reviewed configuration")
    if "paired" in cfg and not isinstance(cfg["paired"], bool):
        raise ValueError("paired must be a JSON boolean matching the experimental design")
    if spec.get("upstream"):
        expected = cfg.get("expected_outputs")
        if not isinstance(expected, list) or not expected or any(
            not isinstance(p, str) or not p or p.startswith("REPLACE_") or Path(p).is_absolute() or ".." in Path(p).parts
            or p in {"*", "**", "**/*"} or p.endswith((".html", ".log", ".json")) for p in expected
        ):
            raise ValueError("expected_outputs must list reviewed biological result patterns relative to the published output directory")
    base = Path(source).resolve().parent
    for key in PATH_KEYS & cfg.keys():
        if cfg[key]:
            cfg[key] = resolve_path(cfg[key], base, workspace)
    # Reviewed platform/QC parameters remain the backend's configuration.
    if "outdir" in cfg:
        cfg["outdir"] = str(output)
    if spec["action"] in {"analyze", "annotate", "pseudobulk", "summarize"} or spec.get("upstream"):
        cfg["outdir"] = str(output)
    if spec["action"] == "segment" and not cfg.get("profile"):
        raise ValueError("Sopa requires an explicit technology/container profile")
    return cfg


def command_for(spec, cfg, config_path, output, run_dir):
    backend = ROOT / "workflow_backends" / spec["workflow"]
    action = spec["action"]
    python = sys.executable
    if spec.get("upstream"):
        return [python, str(backend / "run.py"), action, "--params", str(config_path),
                "--profile", str(cfg.get("profile", "docker")), "--work-dir", str(run_dir / "nextflow"), "--execute"]
    if action in {"analyze", "annotate", "pseudobulk", "summarize"}:
        return [python, str(backend / "run.py"), action, "--config", str(config_path)]
    if action == "merge-bracken":
        return [python, str(backend / "run.py"), action, "--samples", cfg["samples"], "--outdir", str(output)]
    if action == "export":
        return [python, str(backend / "scripts/export_spatialdata.py"), "--input", cfg["input"],
                "--table", cfg["table"], "--counts-source", cfg["counts_source"], "--output", str(output / "exported.h5ad")]
    if action == "enrich":
        cmd = [python, str(backend / "scripts/enrichment.py"), "--selected", cfg["selected"],
               "--universe", cfg["universe"], "--gmt", cfg["gmt"], "--output", str(output / "enrichment.tsv")]
        for key in ("gene_map", "min_size", "max_size"):
            if cfg.get(key) is not None:
                cmd += ["--" + key.replace("_", "-"), str(cfg[key])]
        return cmd
    if action == "de":
        return ["Rscript", str(backend / "scripts/pseudobulk_deseq2.R"), cfg["counts"], cfg["metadata"],
                cfg["control"], cfg["treated"], str(output), "paired" if cfg.get("paired", False) else "unpaired"]
    if action == "differential":
        return ["Rscript", str(backend / "scripts/differential_abundance.R"), cfg["counts"], cfg["samples"], cfg["control"], str(output)]
    if action == "community":
        return ["Rscript", str(backend / "scripts/community_stats.R"), cfg["abundance"], cfg["samples"], str(output)]
    raise ValueError(f"Unsupported action: {action}")


def run_command(command, env, cwd):
    child = subprocess.Popen(command, env=env, cwd=cwd, start_new_session=True)
    def cancel(signum, frame):
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
        raise RuntimeError("Stage cancelled; child process group terminated")
    old = {sig: signal.signal(sig, cancel) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        code = child.wait()
        if code:
            raise RuntimeError(f"Backend exited with code {code}")
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)


def accept(spec, output, run_dir):
    status = "success"
    metrics = {}
    required = spec["required_outputs"]
    for pattern in required:
        files = list(output.glob(pattern))
        if not files or any(not p.is_file() or p.stat().st_size == 0 for p in files):
            raise ValueError(f"Required nonempty result missing: {pattern}")
    if spec.get("upstream"):
        nf = run_dir / "nextflow"
        if read_json(nf / "status.json").get("status") != "success":
            raise ValueError("Nextflow launcher did not report success")
        with (nf / "trace.tsv").open(encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        if not rows or not any(row.get("status") == "COMPLETED" for row in rows):
            raise ValueError("No newly completed Nextflow tasks; launch alone is not acceptance")
        metrics["trace_status_counts"] = {s: sum(r.get("status") == s for r in rows) for s in {r.get("status") for r in rows}}
        if any(row.get("status") not in {"COMPLETED", "CACHED"} for row in rows):
            # A pipeline may legitimately ignore a tool failure; surface it for review.
            status = "partial_success"
        if not any(p.is_file() and p.stat().st_size for p in output.rglob("*")):
            raise ValueError("Nextflow produced no nonempty published output")
    if spec["action"] == "de":
        with (output / "manifest.tsv").open(encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        successful = [r for r in rows if r["status"] == "success"]
        if not successful:
            raise ValueError("No cell type passed differential-expression acceptance")
        for row in successful:
            file = Path(row["file"])
            if not file.is_absolute():
                file = output / file
            if not file.is_file() or file.stat().st_size == 0:
                raise ValueError("DE manifest references a missing/empty result")
        metrics["cell_type_status"] = rows
        if len(successful) != len(rows):
            status = "partial_success"
    return status, metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--config-file", default="")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--job-id", default="unknown")
    args = parser.parse_args()
    spec = read_json(SPECS)[args.stage]
    workspace = args.workspace.resolve()
    final = workspace / "final"
    final.mkdir(parents=True, exist_ok=True)
    manifest_path = final / f"{args.stage}.manifest.json"
    started = datetime.now(timezone.utc).isoformat()
    job_id = args.job_id if args.job_id != "unknown" else workspace.parent.name
    manifest = {"manifest_version": "1.0", "job_id": job_id, "stage": args.stage,
                "status": "failed", "artifacts": {}, "parameters": {}, "metrics": {}, "started_at": started}
    # Clear the canonical old success before preflight; preserve old run directories.
    write_json(manifest_path, manifest)
    try:
        source = Path(args.config_file or f"{args.stage}.json")
        if not source.is_absolute():
            source = args.input_dir / source
        run_dir = workspace / args.stage / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8])
        output = run_dir / "results"
        cfg = prepare_config(source, spec, workspace, output)
        run_dir.mkdir(parents=True, exist_ok=False)
        effective = run_dir / "effective_config.json"
        effective_cfg = dict(cfg)
        if spec.get("upstream"):
            effective_cfg.pop("profile", None)
            effective_cfg.pop("expected_outputs", None)
        write_json(effective, effective_cfg)
        command = command_for(spec, cfg, effective, output, run_dir)
        if spec["action"] in {"export", "enrich"}:
            output.mkdir()
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1")
        temp = run_dir / "tmp"
        temp.mkdir()
        env.update(TMPDIR=str(temp), MPLCONFIGDIR=str(temp / "matplotlib"),
                   NUMBA_CACHE_DIR=str(temp / "numba"), XDG_CACHE_HOME=str(temp / "cache"))
        if spec.get("upstream"):
            env.update(NXF_HOME=str(run_dir / "nxf_home"), NXF_TEMP=str(temp))
            if env.get("BIOFLOW_OMICS_DOCKER_HOST"):
                env["DOCKER_HOST"] = env["BIOFLOW_OMICS_DOCKER_HOST"]
        write_json(run_dir / "command.json", {"argv": command, "source_config": str(source), "backend": spec["workflow"]})
        print(json.dumps({"stage": args.stage, "command": command}, ensure_ascii=False), flush=True)
        run_command(command, env, run_dir)
        acceptance_spec = dict(spec)
        if spec.get("upstream"):
            acceptance_spec["required_outputs"] = cfg["expected_outputs"]
        status, metrics = accept(acceptance_spec, output, run_dir)
        artifacts = {}
        patterns = acceptance_spec["required_outputs"] if spec.get("upstream") else ["**/*"]
        for pattern in patterns:
            for file in output.glob(pattern):
                if not file.is_file():
                    continue
                relative = file.relative_to(output).as_posix()
                artifacts[relative] = {"artifact": relative, "full_path": str(file), "path": str(file),
                                       "workspace_path": file.relative_to(workspace).as_posix(), "size": file.stat().st_size}
        if spec.get("upstream"):
            for name in ("trace.tsv", "status.json", "launch.json", "execution_report.html"):
                file = run_dir / "nextflow" / name
                if file.is_file():
                    artifacts[name] = {"full_path": str(file), "workspace_path": file.relative_to(workspace).as_posix(), "size": file.stat().st_size}
        manifest.update(status=status, output_dir=str(output), artifacts=artifacts, metrics=metrics,
                        parameters=cfg, command=command, backend=spec["workflow"],
                        source_config_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
        report_path = final / f"{args.stage}_report.md"
        report_path.write_text(f"# {spec['title']}\n\n状态：{status}\n\n结果目录：`{output}`\n\n"
                               "科学解释与未覆盖范围见 docs/omics_workflows.md 和冻结后端Skill。\n\n"
                               + json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["artifacts"]["stage_report"] = {"full_path": str(report_path),
            "workspace_path": report_path.relative_to(workspace).as_posix(), "size": report_path.stat().st_size}
    except Exception as exc:
        manifest["error"] = str(exc)
        print(str(exc), file=sys.stderr, flush=True)
    finally:
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        if manifest["status"] == "failed":
            (final / f"{args.stage}_report.md").write_text(
                f"# {spec['title']}\n\n本次阶段失败：{manifest.get('error', '')}\n\n"
                "本次没有发布有效结果；旧attempt仅供追溯。\n", encoding="utf-8")
        write_json(manifest_path, manifest)
    return 0 if manifest["status"] in {"success", "partial_success"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
