#!/usr/bin/env python3
"""CLI for the plan-scoped formal gene-family result report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.gene_family_report import GeneFamilyReportResult, generate_gene_family_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate or open a gene-family result report")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--job-id", default="")
    parser.add_argument("--expected-stages", default="")
    parser.add_argument("--stage-statuses-json", default="{}")
    parser.add_argument("--scope-id", default="")
    parser.add_argument("--analysis-route", default="")
    parser.add_argument("--member-set-id", default="")
    parser.add_argument("--rnaseq-scope-id", default="")
    parser.add_argument("--action", choices=("generate", "open"), default="generate")
    parser.add_argument("--no-model", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> GeneFamilyReportResult:
    args = _parser().parse_args(argv)
    statuses = json.loads(args.stage_statuses_json)
    if not isinstance(statuses, dict):
        raise ValueError("--stage-statuses-json must contain a JSON object")
    result = generate_gene_family_report(
        args.workspace,
        args.prefix,
        expected_stages=[value.strip() for value in args.expected_stages.split(",") if value.strip()],
        stage_statuses={str(key): str(value) for key, value in statuses.items()},
        scope_id=args.scope_id,
        analysis_route=args.analysis_route,
        member_set_id=args.member_set_id,
        rnaseq_scope_id=args.rnaseq_scope_id,
        use_model=not args.no_model,
        action=args.action,
        job_id=args.job_id,
    )
    print(
        json.dumps(
            {
                "status": "success",
                "report_path": str(result.report_path),
                "manifest_path": str(result.manifest_path),
                "report_status": result.report_status,
                "generation_mode": result.generation_mode,
                "reused": result.reused,
            },
            ensure_ascii=False,
        )
    )
    return result


if __name__ == "__main__":
    main()
