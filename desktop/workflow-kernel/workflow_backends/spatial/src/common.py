"""Local helpers copied into each independent package; no cross-package imports."""
import csv
import hashlib
import html
import importlib.metadata
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


def load_config(path):
    path = Path(path).resolve()
    with path.open(encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict): raise ValueError("Config must be a JSON object")
    return data, path.parent


def resolve(base, value):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else Path(base) / path).resolve()


def output_dir(path):
    path = Path(path).resolve()
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError(f"Output must be a new or empty directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def read_samples(path, required):
    import pandas as pd
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        columns = next(csv.reader(handle), [])
    if len(columns) != len(set(columns)):
        raise ValueError("Duplicate sample-sheet columns")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if frame.empty or not set(required) <= set(frame.columns):
        raise ValueError(f"Sample sheet requires nonempty rows and columns: {required}")
    for key in required:
        if (frame[key].str.strip() == "").any(): raise ValueError(f"Empty sample field: {key}")
    if frame.sample_id.duplicated().any(): raise ValueError("Duplicate sample_id")
    if not frame.sample_id.map(lambda x: bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", x))).all():
        raise ValueError("sample_id must contain letters/digits/_/./- and start with a letter or digit")
    return frame


def validate_counts(matrix):
    import numpy as np
    # scipy sparse uses .data; ndarray also has .data, so distinguish by tocsr.
    values = matrix.data if hasattr(matrix, "tocsr") else np.asarray(matrix)
    if not np.isfinite(values).all() or (values < 0).any() or not np.allclose(values, np.round(values), rtol=0, atol=1e-6):
        raise ValueError("Expected finite nonnegative integer raw counts, not normalized/log values")
    if values.size and np.max(values) > np.iinfo(np.int32).max:
        raise ValueError("Count exceeds supported integer range")


def report(out, title, config, metrics):
    versions = {"python": sys.version}
    for name in ("numpy", "pandas", "scanpy", "anndata", "squidpy", "scipy", "matplotlib", "harmonypy"):
        try: versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: pass
    package = Path(__file__).resolve().parent.parent
    files = [package / "run.py", package / "requirements.txt"]
    files += list((package / "src").glob("*.py")) + list((package / "scripts").glob("*"))
    sources = {p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in files if p.is_file()}
    save_json(out / "provenance.json", {"status": "complete_for_requested_command", "created_utc": datetime.now(timezone.utc).isoformat(),
                                        "config": config, "versions": versions, "source_sha256": sources, "metrics": metrics})
    links = []
    for file in sorted(out.rglob("*")):
        if file.is_file() and file.name != "report.html":
            rel = file.relative_to(out).as_posix()
            links.append(f'<li><a href="{quote(rel)}">{html.escape(rel)}</a></li>')
    body = f'<h1>{html.escape(title)}</h1><pre>{html.escape(json.dumps(metrics, ensure_ascii=False, indent=2))}</pre><ul>{"".join(links)}</ul>'
    (out / "report.html").write_text('<!doctype html><meta charset="utf-8"><style>body{font:16px sans-serif;max-width:1100px;margin:40px auto}pre{white-space:pre-wrap}</style>' + body, encoding="utf-8")



def validate_sheet_paths(path):
    """Validate local CSV inputs; remote URIs are left for the upstream workflow."""
    columns = {"fastq_1", "fastq_2", "fasta", "db_path", "fastq", "image", "transcripts"}
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        for number, row in enumerate(csv.DictReader(handle), start=2):
            for key in columns & row.keys():
                value = (row[key] or "").strip()
                if not value or "://" in value:
                    continue
                target = Path(value)
                if not target.is_absolute():
                    raise ValueError(f"{path}:{number} {key} must be absolute: {value}")
                if not target.exists():
                    raise ValueError(f"{path}:{number} missing {key}: {value}")


def nextflow(repo, revision, args):
    params_path = args.params.resolve()
    cfg, base = load_config(params_path)
    if not {"input", "outdir"} <= set(cfg): raise ValueError("Upstream params require input and outdir")
    path_keys = {"input", "outdir", "databases", "fasta", "gtf", "host_fasta", "hostremoval_reference", "spaceranger_reference"}
    effective = dict(cfg)
    for key in path_keys & set(cfg):
        if cfg[key] and "://" not in str(cfg[key]): effective[key] = str(resolve(base, cfg[key]))
    work = args.work_dir.resolve()
    params_file = work / "effective_params.json"
    command = ["nextflow", "run", repo, "-r", revision, "-profile", args.profile,
               "-params-file", str(params_file), "-work-dir", str(work / "tasks"),
               "-with-report", str(work / "execution_report.html"), "-with-trace", str(work / "trace.tsv")]
    if args.resume: command.append("-resume")
    plan = {"repository": repo, "revision": revision, "argv": command, "effective_params": effective,
            "notes": "Paths inside sample/database CSV files must be absolute. Databases are not downloaded by this launcher."}
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if not args.execute: return
    if shutil.which("nextflow") is None: raise RuntimeError("Nextflow not found; use Linux/WSL with Java and Docker/Apptainer")
    for key in path_keys & set(effective) - {"outdir"}:
        if effective[key] and "://" not in str(effective[key]) and not Path(effective[key]).exists():
            raise ValueError(f"Missing upstream input {key}: {effective[key]}")
    for key in ("input", "databases"):
        value = effective.get(key)
        if value and "://" not in str(value) and Path(value).suffix.lower() == ".csv":
            validate_sheet_paths(value)
    if "://" in str(effective["outdir"]): raise ValueError("This launcher requires a local outdir")
    out = Path(effective["outdir"])
    if not args.resume:
        output_dir(out)
        if work.exists() and any(work.iterdir()): raise ValueError("Use a new work-dir or explicit --resume")
    elif not (work / ".nextflow").exists():
        raise ValueError("Resume requires the original work-dir with .nextflow cache")
    work.mkdir(parents=True, exist_ok=True)
    lock = work / ".package-running.lock"
    try:
        with lock.open("x", encoding="utf-8") as handle: handle.write(str(datetime.now(timezone.utc)))
    except FileExistsError:
        raise RuntimeError("Work directory is in use; investigate stale lock before restarting")
    try:
        if args.resume:
            # Preserve earlier attempts before recreating canonical Nextflow reports.
            # Nextflow otherwise leaves a stale trace/report when the file exists.
            history = work / "launch_history" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
            history.mkdir(parents=True, exist_ok=False)
            for name in ("execution_report.html", "trace.tsv", "status.json", "launch.json", "effective_params.json", "nextflow.log"):
                previous = work / name
                if previous.is_file(): shutil.copy2(previous, history / name)
            for name in ("execution_report.html", "trace.tsv"):
                previous = work / name
                if previous.is_file(): previous.unlink()
        save_json(params_file, effective)
        save_json(work / "launch.json", plan)
        save_json(work / "status.json", {"status": "running", "started_utc": datetime.now(timezone.utc).isoformat()})
        with (work / "nextflow.log").open("a", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=work, stdout=log, stderr=subprocess.STDOUT, check=False)
        save_json(work / "status.json", {"returncode": result.returncode, "status": "success" if result.returncode == 0 else "failed"})
        if result.returncode: raise RuntimeError(f"Nextflow failed ({result.returncode}); see {work / 'nextflow.log'}")
    finally:
        lock.unlink(missing_ok=True)
