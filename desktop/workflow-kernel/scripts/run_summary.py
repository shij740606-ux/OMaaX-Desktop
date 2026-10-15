#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import sys


def main():
    ap = argparse.ArgumentParser(description="Generate summary.md + files.tsv based on identify.manifest.json")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

    from agents.context import RunContext
    from agents.summary.summary_agent import SummaryAgent

    mp = Path(args.manifest).resolve()
    d = json.load(mp.open("r", encoding="utf-8"))
    ws = Path(d["workspace"]).resolve()
    prefix = d["prefix"]

    ctx = RunContext(workspace=ws, prefix=prefix, force=args.force)

    agent = SummaryAgent()
    out = agent.run(ctx, manifest_path=str(mp), force=args.force)

    # write back manifest
    artifacts = d.get("artifacts", {})
    artifacts["summary_md"] = out.summary_md
    artifacts["summary_files_tsv"] = out.files_tsv
    d["artifacts"] = artifacts
    d.setdefault("steps", []).append({"agent": agent.name, "out": out.__dict__})

    mp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Summary finished.")
    print("  md :", out.summary_md)
    print("  tsv:", out.files_tsv)
    print("  manifest updated:", str(mp))


if __name__ == "__main__":
    main()
