#!/usr/bin/env python3
"""Generate or open a formal RNA-seq evidence report."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).absolute().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.rnaseq_report import NoSuccessfulRNASeqEvidence, generate_rnaseq_report


def _csv(value: str) -> list[str] | None:
    values = [item.strip() for item in str(value or "").split(",") if item.strip()]
    return values or None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a deterministic formal RNA-seq result report from successful manifests."
    )
    parser.add_argument("--workspace", required=True, help="RNA-seq job workspace containing final/")
    parser.add_argument("--prefix", required=True, help="RNA-seq output prefix")
    parser.add_argument("--job-id", default="")
    parser.add_argument("--rnaseq-scope-id", default="")
    parser.add_argument(
        "--expected-stages",
        default="",
        help="Comma-separated stages selected for the current RNA-seq batch",
    )
    parser.add_argument("--action", choices=("generate", "open"), default="generate")
    parser.add_argument("--no-model", action="store_true", help="Disable optional LLM language enhancement")
    parser.add_argument("--log-file", default="", help="Accepted for pipeline runner compatibility")
    args = parser.parse_args()

    try:
        result = generate_rnaseq_report(
            Path(args.workspace),
            args.prefix,
            rnaseq_scope_id=args.rnaseq_scope_id,
            expected_stages=_csv(args.expected_stages),
            use_model=not args.no_model,
            action=args.action,
            job_id=args.job_id,
        )
    except NoSuccessfulRNASeqEvidence as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"RNA-seq 结果报告生成失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    reuse = "（复用最新报告）" if result.reused else ""
    print(
        f"RNA-seq 结果报告已就绪{reuse}: {result.report_path} "
        f"[coverage={result.coverage_status}, stages={','.join(result.included_stages)}]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
