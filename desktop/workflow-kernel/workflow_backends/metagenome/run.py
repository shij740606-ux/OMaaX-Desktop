#!/usr/bin/env python3
"""Shotgun metagenomics: pinned upstream and actual community statistics."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import argparse
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("profile", "mag"):
        s = sub.add_parser(name, help="Nextflow dry run by default")
        s.add_argument("--params", type=Path, required=True)
        s.add_argument("--profile", default="docker")
        s.add_argument("--work-dir", type=Path, required=True)
        s.add_argument("--execute", action="store_true")
        s.add_argument("--resume", action="store_true")
    s = sub.add_parser("summarize")
    s.add_argument("--config", type=Path, required=True)
    s = sub.add_parser("merge-bracken")
    s.add_argument("--samples", type=Path, required=True, help="sample_id,path CSV of Bracken files of one taxonomic level")
    s.add_argument("--outdir", type=Path, required=True)
    args = p.parse_args()
    if args.command in ("profile", "mag"):
        from common import nextflow
        repo, rev = ("nf-core/taxprofiler", "2.0.1") if args.command == "profile" else ("nf-core/mag", "5.5.0")
        nextflow(repo, rev, args)
    else:
        import pipeline
        if args.command == "summarize": pipeline.summarize(args.config)
        else: pipeline.merge_bracken(args.samples, args.outdir)


if __name__ == "__main__":
    main()
