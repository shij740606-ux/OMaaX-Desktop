#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:7868"
PROJECT = Path("/home/xia-lab/wutx/00test/bbav5")


def _post(path: str, payload: dict) -> dict:
    r = requests.post(f"{BASE}{path}", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def _get(path: str) -> dict:
    r = requests.get(f"{BASE}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

def write_stage_manifest(final_dir: Path, prefix: str, stage: str, artifact_file: Path) -> None:
    manifest = {
        "manifest_version": "1.0",
        "stage": stage,
        "status": "success",
        "prefix": prefix,
        "workspace_root": str(final_dir.parent.resolve()),
        "artifacts": {
            "primary": {
                "path": f"final/{artifact_file.name}",
                "full_path": str(artifact_file.resolve()),
                "type": "file",
            }
        },
    }
    write_text(final_dir / f"{prefix}.{stage}.manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))


def prepare_job(kind: str) -> tuple[str, Path, Path]:
    job = _post("/api/new_job", {})
    job_id = job["job_id"]
    in_dir = PROJECT / "input" / job_id
    final_dir = PROJECT / "workspace" / job_id / "final"

    # minimal DE + background
    write_text(final_dir / "treat_vs_control.DE_genes.txt", "GeneA\nGeneB\nGeneC\n")
    write_text(in_dir / "gene2go.txt", "GeneA\tGO:0008150\nGeneB\tGO:0003674\nGeneC\tGO:0009987\n")
    # 模拟上游RNA-seq依赖阶段已完成（满足多智能体preflight依赖检查）
    qc_art = final_dir / "Ft.SPL.rnaseq_qc.done.txt"
    align_art = final_dir / "Ft.SPL.rnaseq_align.done.txt"
    quant_art = final_dir / "Ft.SPL.rnaseq_quant.done.txt"
    de_art = final_dir / "Ft.SPL.rnaseq_de.done.txt"
    write_text(qc_art, "ok\n")
    write_text(align_art, "ok\n")
    write_text(quant_art, "ok\n")
    write_text(de_art, "ok\n")
    write_stage_manifest(final_dir, "Ft.SPL", "rnaseq_qc", qc_art)
    write_stage_manifest(final_dir, "Ft.SPL", "rnaseq_align", align_art)
    write_stage_manifest(final_dir, "Ft.SPL", "rnaseq_quant", quant_art)
    write_stage_manifest(final_dir, "Ft.SPL", "rnaseq_de", de_art)
    # 统一预检对RNA-seq阶段可能会检查FASTQ，放置最小模拟FASTQ用于打通联动测试
    write_text(in_dir / "mock_1.fastq", "@r1\nACGT\n+\nIIII\n")
    write_text(in_dir / "mock_2.fastq", "@r2\nTGCA\n+\nIIII\n")
    write_text(
        in_dir / "samples.yaml",
        "groups:\n  control:\n    - mock\ncompare:\n  - [control, control]\nsamples:\n  mock:\n    r1: mock_1.fastq\n    r2: mock_2.fastq\n",
    )

    if kind == "family":
        write_text(final_dir / "Ft.SPL.treat_vs_control.family_DE_genes.txt", "GeneA\nGeneC\n")

    return job_id, in_dir, final_dir


def wait_task(task_id: str, timeout_sec: int = 120) -> dict:
    start = time.time()
    while time.time() - start < timeout_sec:
        st = _get(f"/api/task/{task_id}")
        if st.get("status") in {"success", "failed"}:
            return st
        time.sleep(1.5)
    raise TimeoutError(f"Task {task_id} timeout")


def run_case(kind: str) -> dict:
    job_id, _, final_dir = prepare_job(kind)

    plan_payload = {
        "message": "帮我执行rnaseq GO富集分析",
        "job_id": job_id,
        "prefix": "Ft.SPL",
        "history": [],
        "intent": {
            "task_type": "batch_analysis",
            "stages": ["rnaseq_enrich"],
            "primary_stage": "rnaseq_enrich",
            "gene_family": "SPL",
            "species": "Ft",
            "prefix": "Ft.SPL",
            "confidence": 0.99,
        },
    }
    plan = _post("/api/agent/plan", plan_payload)

    exec_payload = {
        "stage": "rnaseq_enrich",
        "job_id": job_id,
        "prefix": "Ft.SPL",
        "cpu": 1,
        "rnaseqForegroundMode": "auto",
        "enrichType": "go",
        "enrichBackgroundSource": "gene2go",
    }
    ex = _post("/api/execute", exec_payload)
    task_id = ex.get("task_id")
    if not task_id:
        raise RuntimeError(f"execute did not return task_id: {ex}")
    st = wait_task(task_id)

    go_file = final_dir / "Ft.SPL.rnaseq_enrich.treat_vs_control.GO_enrich.txt"
    manifest_file = final_dir / "Ft.SPL.rnaseq_enrich.manifest.json"
    detected_mode = ""
    if manifest_file.exists():
        try:
            manifest_obj = json.loads(manifest_file.read_text(encoding="utf-8"))
            detected_mode = (
                (manifest_obj.get("metrics") or {}).get("foreground_mode")
                or (manifest_obj.get("parameters") or {}).get("foreground_mode")
                or ""
            )
        except Exception:
            detected_mode = ""

    return {
        "kind": kind,
        "job_id": job_id,
        "plan_ok": bool(plan.get("success")),
        "plan_primary_stage": (((plan.get("plan") or {}).get("primary_stage")) or (plan.get("intent") or {}).get("primary_stage")),
        "task_status": st.get("status"),
        "task_message": st.get("message", ""),
        "go_file_exists": go_file.exists(),
        "detected_mode": detected_mode,
    }


def main() -> None:
    results = [run_case("global"), run_case("family")]
    print(json.dumps(results, ensure_ascii=False, indent=2))

    g = next(x for x in results if x["kind"] == "global")
    f = next(x for x in results if x["kind"] == "family")
    assert g["plan_ok"] and f["plan_ok"], "plan failed"
    assert g["task_status"] == "success" and f["task_status"] == "success", "task failed"
    assert g["go_file_exists"] and f["go_file_exists"], "GO result file missing"
    assert g["detected_mode"] == "global", f"expected global mode, got {g['detected_mode']}"
    assert f["detected_mode"] == "family", f"expected family mode, got {f['detected_mode']}"
    print("PASS: multi-agent RNA-seq linkage simulation")


if __name__ == "__main__":
    main()
