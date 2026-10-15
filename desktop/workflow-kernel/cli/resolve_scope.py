#!/usr/bin/env python3
"""Resolve and write a gene-family scope JSON file."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.gene_family_scope import build_gene_family_scope, scope_manifest_path, write_scope


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve gene-family analysis scope from config.xlsx and user selections.")
    parser.add_argument("--input-dir", required=True, help="input/<job_id> directory")
    parser.add_argument("--workspace", required=True, help="workspace/<job_id> directory")
    parser.add_argument("--prefix", default="", help="Compatibility output prefix")
    parser.add_argument("--family", default="", help="Gene family override")
    parser.add_argument("--target-species", default="", help="Comma-separated target species, e.g. Lc,Dl")
    parser.add_argument("--reference-species", default="", help="Comma-separated reference species, e.g. At")
    parser.add_argument("--comparative-species", default="", help="Comma-separated comparative species, e.g. Os")
    parser.add_argument(
        "--scope-mode",
        default="custom",
        choices=[
            "custom",
            "target_only",
            "target_plus_reference",
            "target_plus_comparative",
            "target_plus_reference_plus_comparative",
        ],
    )
    parser.add_argument("--output", default="", help="Output scope JSON path. Defaults to final/{prefix}.{scope_id}.scope.json")
    args = parser.parse_args()

    scope = build_gene_family_scope(
        input_dir=args.input_dir,
        workspace=args.workspace,
        prefix=args.prefix,
        family=args.family,
        target_species=args.target_species,
        reference_species=args.reference_species,
        comparative_species=args.comparative_species,
        scope_mode=args.scope_mode,
    )

    output = Path(args.output) if args.output else scope_manifest_path(args.workspace, scope.prefix, scope.scope_id)
    write_scope(scope, output)

    print(f"scope_id: {scope.scope_id}")
    print(f"family: {scope.family}")
    print(f"prefix: {scope.prefix}")
    print(f"target_species: {','.join(scope.target_species)}")
    print(f"reference_species: {','.join(scope.reference_species)}")
    print(f"comparative_species: {','.join(scope.comparative_species)}")
    print(f"selected_species: {','.join(scope.selected_species)}")
    print(f"scope_json: {output}")
    if scope.warnings:
        print("warnings:")
        for warning in scope.warnings:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
