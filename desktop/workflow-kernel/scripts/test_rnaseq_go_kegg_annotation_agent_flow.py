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


def wait_task(task_id: str, timeout_sec: int = 180) -> dict:
    start = time.time()
    while time.time() - start < timeout_sec:
        task = get(f"/api/task/{task_id}")
        if task.get("status") in {"success", "failed"}:
            return task
        time.sleep(1.5)
    raise TimeoutError(task_id)


def prepare_job() -> tuple[str, Path, Path]:
    job = post("/api/new_job", {})
    job_id = job["job_id"]
    input_dir = PROJECT / "input" / job_id
    final_dir = PROJECT / "workspace" / job_id / "final"

    for stage in ["rnaseq_qc", "rnaseq_align", "rnaseq_quant", "rnaseq_de"]:
        write_stage_manifest(final_dir, stage, f"{stage}.done.txt")

    write(final_dir / "treat_vs_control.DE_genes.txt", "GeneA\nGeneB\nGeneC\nGeneD\n")
    write(final_dir / f"{PREFIX}.rnaseq_de.treat_vs_control.family_DE_genes.txt", "GeneA\nGeneC\n")
    write(input_dir / "gene2go.txt", "\n".join([
        "GeneA\tGO:0008150",
        "GeneB\tGO:0008150",
        "GeneC\tGO:0003674",
        "GeneD\tGO:0003674",
        "GeneE\tGO:0009987",
        "",
    ]))
    write(input_dir / "gene2ko.txt", "\n".join([
        "GeneA\tK00001",
        "GeneB\tK00001",
        "GeneC\tK00002",
        "GeneD\tK00002",
        "GeneE\tK00003",
        "",
    ]))
    write(input_dir / "Ft.SPL.pep.fa", ">GeneA\nMKTLL\n>GeneB\nMSTAA\n")
    write(input_dir / "mock_1.fastq", "@r1\nACGT\n+\nIIII\n")
    write(input_dir / "mock_2.fastq", "@r2\nTGCA\n+\nIIII\n")
    write(input_dir / "samples.yaml", "groups:\n  control: control\ncompare:\n  - [treat, control]\n")
    return job_id, input_dir, final_dir


def assert_chat_intent(message: str, job_id: str, expected_stage: str) -> dict:
    chat = post("/api/agent/chat", {"message": message, "job_id": job_id, "session_id": f"test_{int(time.time())}"})
    intent = chat.get("intent") or {}
    stages = intent.get("stages") or []
    assert expected_stage in stages or intent.get("primary_stage") == expected_stage, (
        f"{message} expected {expected_stage}, got intent={intent}"
    )
    return intent


def execute(stage: str, job_id: str, **params) -> dict:
    payload = {
        "stage": stage,
        "job_id": job_id,
        "prefix": PREFIX,
        "cpu": 1,
        "rnaseqForegroundMode": "auto",
        "enrichBackgroundSource": "gene2go",
        **params,
    }
    submitted = post("/api/execute", payload)
    assert submitted.get("success"), submitted
    if submitted.get("task_id"):
        return wait_task(submitted["task_id"])
    return submitted


def main() -> None:
    job_id, _, final_dir = prepare_job()

    go_intent = assert_chat_intent("帮我执行rnaseq GO富集分析", job_id, "rnaseq_go_enrich")
    kegg_intent = assert_chat_intent("帮我执行rnaseq KEGG富集分析", job_id, "rnaseq_kegg_enrich")
    annotation_intent = assert_chat_intent("帮我执行背景注释分析", job_id, "annotation")

    plan = post("/api/agent/plan", {
        "message": "帮我执行rnaseq GO富集分析",
        "job_id": job_id,
        "prefix": PREFIX,
        "history": [],
        "intent": go_intent,
    })
    plan_stages = [s.get("id", s) if isinstance(s, dict) else s for s in (plan.get("plan") or {}).get("stages", [])]
    assert "rnaseq_go_enrich" in plan_stages, f"GO plan did not keep split stage: {plan}"

    go_task = execute("rnaseq_go_enrich", job_id, enrichType="go")
    assert go_task.get("status") == "success", go_task.get("message")

    kegg_task = execute("rnaseq_kegg_enrich", job_id, enrichType="kegg", keggOrganism="ath")
    assert kegg_task.get("status") == "success", kegg_task.get("message")

    go_file = final_dir / f"{PREFIX}.rnaseq_enrich.treat_vs_control.GO_enrich.txt"
    kegg_file = final_dir / f"{PREFIX}.rnaseq_enrich.treat_vs_control.KEGG_enrich.txt"
    assert go_file.exists(), f"missing GO file: {go_file}"
    assert kegg_file.exists(), f"missing KEGG file: {kegg_file}"

    print(json.dumps({
        "job_id": job_id,
        "go_intent_stage": go_intent.get("primary_stage"),
        "kegg_intent_stage": kegg_intent.get("primary_stage"),
        "annotation_intent_stage": annotation_intent.get("primary_stage"),
        "plan_stages": plan_stages,
        "go_status": go_task.get("status"),
        "kegg_status": kegg_task.get("status"),
        "go_file": str(go_file),
        "kegg_file": str(kegg_file),
    }, ensure_ascii=False, indent=2))
    print("PASS: RNA-seq GO/KEGG/annotation multi-agent flow")


if __name__ == "__main__":
    main()
