#!/usr/bin/env python3
"""Single-cell / single-nucleus analysis. See README.md for data contracts."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("analyze", "annotate", "pseudobulk"):
        p = sub.add_parser(name)
        p.add_argument("--config", type=Path, required=True)
    p = sub.add_parser("upstream", help="Pinned nf-core/scrnaseq; dry run by default")
    p.add_argument("--params", type=Path, required=True)
    p.add_argument("--profile", default="docker")
    p.add_argument("--work-dir", type=Path, required=True)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.command == "upstream":
        from common import nextflow
        nextflow("nf-core/scrnaseq", "4.2.0", args)
    else:
        import pipeline
        getattr(pipeline, args.command)(args.config)


if __name__ == "__main__":
    main()
