#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys


def main():
    ap = argparse.ArgumentParser(description="Run identification workflow (multi-agent).")

    ap.add_argument("--hmm-id", required=True)
    ap.add_argument("--blast-id", required=True)
    ap.add_argument("--pep", required=True, help="proteins fasta")
    ap.add_argument("--pfam", required=True, help="Pfam-A.hmm")
    ap.add_argument("--out", required=True, help="workspace output dir")
    ap.add_argument("--prefix", required=True)

    ap.add_argument("--required", default="PF03110", help="comma-separated")
    ap.add_argument("--dom-i-evalue", type=float, default=1e-5)
    ap.add_argument("--hmm-cov", type=float, default=0.5)
    ap.add_argument("--cpu", type=int, default=8)
    ap.add_argument("--cut-ga", action="store_true")
    ap.add_argument("--force", action="store_true")

    args = ap.parse_args()

    # ✅ 先把项目根加入 sys.path（你的结构是 .../bba/bba/{agents,workflows,pipelines,scripts}）
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

    # ✅ 再 import 项目内模块（避免 “No module named agents”）
    from agents.context import RunContext
    from agents.params import IdentifyParams
    from agents.identify.merge_agent import MergeAgent
    from agents.identify.extract_agent import ExtractAgent
    from agents.identify.domain_agent import DomainAgent
    from agents.identify.report_agent import ReportAgent

    # 如果你已经加了 FinalFastaAgent（可选）
    try:
        from agents.identify.final_fasta_agent import FinalFastaAgent
    except ModuleNotFoundError:
        FinalFastaAgent = None

    from workflows.identify_workflow import IdentifyWorkflow

    ctx = RunContext(workspace=Path(args.out), prefix=args.prefix, force=args.force)

    params = IdentifyParams(
        hmm_id=args.hmm_id,
        blast_id=args.blast_id,
        pep_fasta=args.pep,
        pfam_hmm=args.pfam,
        required_pfams=args.required,
        dom_i_evalue=args.dom_i_evalue,
        hmm_cov=args.hmm_cov,
        cpu=args.cpu,
        cut_ga=args.cut_ga,
    )

    # 兼容：如果 workflow 你已经扩展成 5 个 agent（含 FinalFastaAgent）
    if FinalFastaAgent is not None:
        wf = IdentifyWorkflow(
            merge_agent=MergeAgent(),
            extract_agent=ExtractAgent(),
            domain_agent=DomainAgent(),
            final_fasta_agent=FinalFastaAgent(),
            report_agent=ReportAgent(),
        )
    else:
        wf = IdentifyWorkflow(
            merge_agent=MergeAgent(),
            extract_agent=ExtractAgent(),
            domain_agent=DomainAgent(),
            report_agent=ReportAgent(),
        )

    wf.run(ctx, params)
    print("Identify workflow finished.")


if __name__ == "__main__":
    main()
