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
    project = PROJECT_ROOT
    job_id = "job_codex_sim_three_stage_api"
    prefix = "Ft.SPL"
    ws = project / "workspace" / job_id
    in_root = project / "input"
    in_dir = in_root / job_id
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

    # RNA-seq DE output (minimal simulated)
    write_text(
        final_dir / f"{prefix}.de.tsv",
        "gene\tlog2FoldChange\tpadj\nGeneA\t2.0\t0.001\nGeneB\t-1.2\t0.01\nGeneC\t1.5\t0.02\n",
    )
    write_text(final_dir / f"{prefix}.treat_vs_control.DE_genes.txt", "GeneA\nGeneB\nGeneC\n")
    write_text(in_dir / f"{prefix}.pep.fa", ">GeneA\nMKTLL\n>GeneB\nMSTAA\n>GeneC\nMNNQQ\n")
    write_text(
        final_dir / f"{prefix}.rnaseq_de.manifest.json",
        json.dumps({"stage": "rnaseq_de", "status": "completed"}, ensure_ascii=False),
    )

    # Annotation / background files
    write_text(in_dir / "gene2go.txt", "GeneA\tGO:0008150\nGeneB\tGO:0003674\nGeneC\tGO:0009987\n")
    write_text(in_dir / "gene2ko.txt", "GeneA\tK00001\nGeneB\tK00002\nGeneC\tK00001\n")

    # A lightweight annotation stage execution to create annotation manifest
    msg_ann, man_ann = run_stage("annotation", job, prefix, cpu=1)
    print("[annotation]", msg_ann, man_ann)

    # GO enrich
    msg_go, man_go = run_stage(
        "rnaseq_enrich",
        job,
        prefix,
        cpu=1,
        enrich_type="go",
        enrich_background_source="gene2go",
        rnaseq_foreground_mode="global",
    )
    print("[go]", msg_go, man_go)

    # KEGG enrich
    msg_kegg, man_kegg = run_stage(
        "rnaseq_enrich",
        job,
        prefix,
        cpu=1,
        enrich_type="kegg",
        enrich_background_source="auto",
        enrich_species="ath",
        kegg_organism="ath",
        rnaseq_foreground_mode="global",
    )
    print("[kegg]", msg_kegg, man_kegg)

    expected = [
        final_dir / f"{prefix}.annotation.manifest.json",
        final_dir / f"{prefix}.rnaseq_enrich.treat_vs_control.GO_enrich.txt",
        final_dir / f"{prefix}.rnaseq_enrich.treat_vs_control.KEGG_enrich.txt",
    ]
    print("[files]")
    for p in expected:
        print(f"{p}: {p.exists()}")


if __name__ == "__main__":
    main()
