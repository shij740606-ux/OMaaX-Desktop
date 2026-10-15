#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path("/home/xia-lab/wutx/00test/bbav5")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.web.pipeline_backend import JobInfo, run_stage


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    job_id = "job_codex_sim_go_only_stage"
    prefix = "Ft.SPL"
    ws = PROJECT_ROOT / "workspace" / job_id
    in_dir = PROJECT_ROOT / "input" / job_id
    if ws.exists():
        shutil.rmtree(ws)
    if in_dir.exists():
        shutil.rmtree(in_dir)
    final_dir = ws / "final"
    logs_dir = ws / "logs"
    in_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    job = JobInfo(
        job_id=job_id,
        input_dir=str(in_dir.resolve()),
        workspace_dir=str(ws.resolve()),
        logs_dir=str(logs_dir.resolve()),
        created_at="2026-05-02 00:00:00",
    )

    write_text(in_dir / f"{prefix}.pep.fa", ">GeneA\nMKTLL\n>GeneB\nMSTAA\n")
    write_text(final_dir / f"{prefix}.treat_vs_control.DE_genes.txt", "GeneA\nGeneB\n")
    write_text(in_dir / "gene2go.txt", "GeneA\tGO:0008150\nGeneB\tGO:0003674\n")
    write_text(final_dir / f"{prefix}.rnaseq_de.manifest.json", json.dumps({"stage": "rnaseq_de", "status": "completed"}))

    run_stage("annotation", job, prefix, cpu=1)
    msg_go, _ = run_stage(
        "rnaseq_enrich",
        job,
        prefix,
        cpu=1,
        enrich_type="go",
        enrich_background_source="gene2go",
        rnaseq_foreground_mode="global",
    )
    print(msg_go)
    print((final_dir / f"{prefix}.rnaseq_enrich.treat_vs_control.GO_enrich.txt").exists())
    print((final_dir / f"{prefix}.rnaseq_enrich.treat_vs_control.KEGG_enrich.txt").exists())


if __name__ == "__main__":
    main()
