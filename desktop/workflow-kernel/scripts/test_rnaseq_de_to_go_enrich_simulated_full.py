#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from pathlib import Path

import requests


BASE = "http://127.0.0.1:7868"
PROJECT = Path("/home/xia-lab/wutx/00test/bbav5")
PREFIX = "Ft.SPL"


def post(path: str, payload: dict) -> dict:
    response = requests.post(f"{BASE}{path}", json=payload, timeout=45)
    response.raise_for_status()
    return response.json()


def get(path: str) -> dict:
    response = requests.get(f"{BASE}{path}", timeout=45)
    response.raise_for_status()
    return response.json()


def wait_task(task_id: str, timeout_sec: int = 240) -> dict:
    start = time.time()
    while time.time() - start < timeout_sec:
        task = get(f"/api/task/{task_id}")
        if task.get("status") in {"success", "failed"}:
            return task
        time.sleep(2)
    raise TimeoutError(task_id)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_stage_manifest(final_dir: Path, stage: str, artifact_name: str) -> None:
    artifact = final_dir / artifact_name
    write(artifact, "ok\n")
    manifest = {
        "manifest_version": "1.0",
        "stage": stage,
        "status": "success",
        "prefix": PREFIX,
        "workspace_root": str(final_dir.parent.resolve()),
        "artifacts": {
            "primary": {
                "path": f"final/{artifact.name}",
                "full_path": str(artifact.resolve()),
                "type": "file",
            }
        },
    }
    write(final_dir / f"{PREFIX}.{stage}.manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))


def execute(stage: str, job_id: str, **params) -> dict:
    submitted = post("/api/execute", {
        "stage": stage,
        "job_id": job_id,
        "prefix": PREFIX,
        "cpu": 1,
        **params,
    })
    assert submitted.get("success"), submitted
    if submitted.get("task_id"):
        return wait_task(submitted["task_id"])
    return submitted


def prepare_job() -> tuple[str, Path, Path]:
    job = post("/api/new_job", {})
    job_id = job["job_id"]
    input_dir = PROJECT / "input" / job_id
    final_dir = PROJECT / "workspace" / job_id / "final"

    for stage in ["rnaseq_qc", "rnaseq_align", "rnaseq_quant"]:
        write_stage_manifest(final_dir, stage, f"{stage}.done.txt")

    samples = ["C1", "C2", "C3", "T1", "T2", "T3"]
    rows = ["gene\t" + "\t".join(samples)]
    for idx in range(1, 201):
        gene = f"Gene{idx:02d}"
        base = 30 + ((idx * 17) % 500)
        jitter = [(idx * 3) % 11, (idx * 5) % 17, (idx * 7) % 23]
        if idx <= 50:
            counts = [
                base + jitter[0],
                base + jitter[1],
                base + jitter[2],
                base * 8 + jitter[2] * 9,
                base * 7 + jitter[1] * 11,
                base * 9 + jitter[0] * 7,
            ]
        elif idx <= 100:
            counts = [
                base * 8 + jitter[2] * 9,
                base * 7 + jitter[1] * 11,
                base * 9 + jitter[0] * 7,
                base + jitter[0],
                base + jitter[1],
                base + jitter[2],
            ]
        else:
            counts = [
                base + jitter[0] * 3,
                base + jitter[1] * 2,
                base + jitter[2],
                base + jitter[2] * 2,
                base + jitter[0],
                base + jitter[1] * 3,
            ]
        rows.append(gene + "\t" + "\t".join(str(v) for v in counts))
    write(final_dir / "mock.count_matrix.txt", "\n".join(rows) + "\n")

    write(input_dir / "samples.yaml", "\n".join([
        "groups:",
        "  C1: control",
        "  C2: control",
        "  C3: control",
        "  T1: treat",
        "  T2: treat",
        "  T3: treat",
        "compare:",
        "  - [treat, control]",
        "",
    ]))
    write(input_dir / "gene2go.txt", "\n".join([f"Gene{idx:02d}\tGO:0008150" for idx in range(1, 201)]) + "\n")
    return job_id, input_dir, final_dir


def main() -> None:
    job_id, _, final_dir = prepare_job()

    de_task = execute("rnaseq_de", job_id, rnaseqForegroundMode="global")
    assert de_task.get("status") == "success", de_task.get("message")

    de_file = final_dir / f"{PREFIX}.rnaseq_de.treat_vs_control.DE_genes.txt"
    assert de_file.exists() and de_file.stat().st_size > 0, f"DE genes file missing or empty: {de_file}"

    enrich_task = execute(
        "rnaseq_go_enrich",
        job_id,
        rnaseqForegroundMode="global",
        enrichType="go",
        enrichBackgroundSource="gene2go",
    )
    assert enrich_task.get("status") == "success", enrich_task.get("message")

    go_file = final_dir / f"{PREFIX}.rnaseq_enrich.treat_vs_control.GO_enrich.txt"
    assert go_file.exists(), f"GO enrich output missing: {go_file}"

    print(json.dumps({
        "job_id": job_id,
        "de_status": de_task.get("status"),
        "enrich_status": enrich_task.get("status"),
        "de_file": str(de_file),
        "go_file": str(go_file),
    }, ensure_ascii=False, indent=2))
    print("PASS: simulated RNA-seq DE -> GO enrichment flow")


if __name__ == "__main__":
    main()
