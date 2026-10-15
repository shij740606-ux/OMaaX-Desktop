#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import sys


def main():
    ap = argparse.ArgumentParser(description="Run phylogeny stage based on identify.manifest.json")
    ap.add_argument("--manifest", required=True, help="identify.manifest.json path")
    ap.add_argument("--aligner", default="mafft", choices=["mafft", "muscle"])
    ap.add_argument("--tree-builder", default="iqtree", choices=["iqtree", "fasttree"])
    ap.add_argument("--cpu", type=int, default=8)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

    from agents.context import RunContext
    from agents.phylogeny.phylogeny_agent import PhylogenyAgent

    mp = Path(args.manifest).resolve()
    d = json.load(mp.open("r", encoding="utf-8"))

    ws = Path(d["workspace"]).resolve()
    prefix = d["prefix"]
    artifacts = d.get("artifacts", {})

    in_fa = artifacts.get("final_fa")
    if not in_fa:
        raise RuntimeError("manifest.artifacts.final_fa is missing")
    in_fa = str(Path(in_fa).resolve())

    out_aln = str(ws / f"{prefix}.final_members.aln.fa")
    out_tree = str(ws / f"{prefix}.final_members.tree.nwk")

    ctx = RunContext(workspace=ws, prefix=prefix, force=args.force)

    agent = PhylogenyAgent(aligner=args.aligner, tree_builder=args.tree_builder)
    out = agent.run(ctx, in_fa=in_fa, out_aln=out_aln, out_tree=out_tree, cpu=args.cpu, force=args.force)

    # write back into identify manifest
    artifacts["aln_fa"] = out.aln_fa
    artifacts["tree_nwk"] = out.tree_nwk

    d["artifacts"] = artifacts
    d.setdefault("steps", []).append({"agent": agent.name, "out": out.__dict__})

    mp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Phylogeny finished.")
    print("  aln :", out.aln_fa)
    print("  tree:", out.tree_nwk)
    print("  manifest updated:", str(mp))


if __name__ == "__main__":
    main()
