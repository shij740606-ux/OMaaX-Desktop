#!/usr/bin/env python3
"""Independent spatial transcriptomics: per-section analysis or pinned Sopa upstream."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("analyze")
    p.add_argument("--config", type=Path, required=True)
    p = sub.add_parser("segment", help="nf-core/sopa; dry run by default")
    p.add_argument("--params", type=Path, required=True)
    p.add_argument("--profile", required=True, help="Container and technology profile; see Sopa usage")
    p.add_argument("--work-dir", type=Path, required=True)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.command == "segment":
        from common import nextflow
        nextflow("nf-core/sopa", "1.0.1", args)
    else:
        from pipeline import analyze
        analyze(args.config)


if __name__ == "__main__":
    main()
