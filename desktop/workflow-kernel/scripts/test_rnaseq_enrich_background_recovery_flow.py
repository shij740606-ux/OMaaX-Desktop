#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:7868"
PROJECT = Path("/home/xia-lab/wutx/00test/bbav5")


def post(path: str, payload: dict) -> dict:
    r = requests.post(f"{BASE}{path}", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def get(path: str) -> dict:
    r = requests.get(f"{BASE}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def wait_task(task_id: str, timeout_sec: int = 180) -> dict:
    start = time.time()
    while time.time() - start < timeout_sec:
        t = get(f"/api/task/{task_id}")
        if t.get("status") in {"success", "failed"}:
            return t
        time.sleep(2)
    raise TimeoutError(task_id)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

def write_manifest(final_dir: Path, stage: str, artifact_name: str) -> None:
    artifact = final_dir / artifact_name
    write(artifact, "ok\n")
    manifest = {
        "manifest_version": "1.0",
        "stage": stage,
        "status": "success",
        "prefix": "Ft.SPL",
        "workspace_root": str(final_dir.parent.resolve()),
        "artifacts": {
            "primary": {
                "path": f"final/{artifact.name}",
                "full_path": str(artifact.resolve()),
                "type": "file",
            }
        },
    }
    write(final_dir / f"Ft.SPL.{stage}.manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))


def main() -> None:
    job = post("/api/new_job", {})
    job_id = job["job_id"]
    in_dir = PROJECT / "input" / job_id
    final_dir = PROJECT / "workspace" / job_id / "final"

    # minimal upstream condition for rnaseq_enrich
    write(final_dir / "treat_vs_control.DE_genes.txt", "GeneA\nGeneB\n")
    write_manifest(final_dir, "rnaseq_qc", "qc.done.txt")
    write_manifest(final_dir, "rnaseq_align", "align.done.txt")
    write_manifest(final_dir, "rnaseq_quant", "quant.done.txt")
    write_manifest(final_dir, "rnaseq_de", "de.done.txt")
    write(in_dir / "mock_1.fastq", "@r1\nACGT\n+\nIIII\n")
    write(in_dir / "mock_2.fastq", "@r2\nTGCA\n+\nIIII\n")
    write(
        in_dir / "samples.yaml",
        "groups:\n  control:\n    - mock\ncompare:\n  - [control, control]\nsamples:\n  mock:\n    r1: mock_1.fastq\n    r2: mock_2.fastq\n",
    )
    # protein reserved for annotation mode (if user chooses auto-annotation)
    write(in_dir / "Ft.SPL.pep.fa", ">GeneA\nMKTLL\n>GeneB\nMSTAA\n")

    # Step1: run enrich with auto background, should fail due missing gene2go for non-model species
    req1 = {
        "stage": "rnaseq_enrich",
        "job_id": job_id,
        "prefix": "Ft.SPL",
        "cpu": 1,
        "rnaseqForegroundMode": "auto",
        "enrichType": "go",
        "enrichBackgroundSource": "auto",
        "enrichSpecies": "Ft",
    }
    ex1 = post("/api/execute", req1)
    t1 = wait_task(ex1["task_id"])
    msg1 = t1.get("message", "")
    assert t1.get("status") == "failed", "first run should fail for missing background"
    assert "缺少背景注释文件" in msg1, f"unexpected first error: {msg1}"

    # Step2: simulate user selecting "upload background file and retry" in popup
    write(in_dir / "gene2go.txt", "GeneA\tGO:0008150\nGeneB\tGO:0003674\n")
    req2 = dict(req1)
    req2["enrichBackgroundSource"] = "gene2go"
    ex2 = post("/api/execute", req2)
    t2 = wait_task(ex2["task_id"])
    assert t2.get("status") == "success", f"fallback rerun failed: {t2.get('message')}"

    go_file = final_dir / "Ft.SPL.rnaseq_enrich.treat_vs_control.GO_enrich.txt"
    print(
        json.dumps(
            {
                "job_id": job_id,
                "first_run_status": t1.get("status"),
                "first_run_message": msg1[:200],
                "retry_status": t2.get("status"),
                "go_file_exists": go_file.exists(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    assert go_file.exists(), "GO output not generated after background-upload retry"
    print("PASS: rnaseq enrich background recovery flow")


if __name__ == "__main__":
    main()
