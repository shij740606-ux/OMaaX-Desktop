#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys


def _prompt_str(msg: str, default: str) -> str:
    s = input(f"{msg} [{default}]: ").strip()
    return default if s == "" else s


def _prompt_float(msg: str, default: float, min_v: float | None = None, max_v: float | None = None) -> float:
    while True:
        s = input(f"{msg} [{default}]: ").strip()
        if s == "":
            return default
        try:
            v = float(s)
            if min_v is not None and v < min_v:
                print(f"  值不能小于 {min_v}，请重输。")
                continue
            if max_v is not None and v > max_v:
                print(f"  值不能大于 {max_v}，请重输。")
                continue
            return v
        except ValueError:
            print("  请输入数字，或直接回车使用默认值。")


def _prompt_int(msg: str, default: int, min_v: int | None = None) -> int:
    while True:
        s = input(f"{msg} [{default}]: ").strip()
        if s == "":
            return default
        try:
            v = int(s)
            if min_v is not None and v < min_v:
                print(f"  值不能小于 {min_v}，请重输。")
                continue
            return v
        except ValueError:
            print("  请输入整数，或直接回车使用默认值。")


def _prompt_bool(msg: str, default: bool) -> bool:
    d = "Y/n" if default else "y/N"
    while True:
        s = input(f"{msg} ({d}): ").strip().lower()
        if s == "":
            return default
        if s in ("y", "yes"):
            return True
        if s in ("n", "no"):
            return False
        print("  请输入 y 或 n，或直接回车使用默认值。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hmm-id", required=True, help="hmm pipeline 输出的 *.domtblout.filter.id")
    ap.add_argument("--blast-id", required=True, help="blast pipeline 输出的 *.blastout.filter.id")
    ap.add_argument("--pep", required=True, help="目标物种全蛋白 fasta（Ft.pep.fasta）")
    ap.add_argument("--pfam", required=True, help="Pfam-A.hmm 路径")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--prefix", required=True, help="前缀，如 Ft.SPL")

    ap.add_argument("--required", default="PF03110", help="必需 Pfam（逗号分隔），默认 PF03110")
    ap.add_argument("--cpu", type=int, default=8)
    ap.add_argument("--dom-i-evalue", type=float, default=1e-5)
    ap.add_argument("--hmm-cov", type=float, default=0.5)
    ap.add_argument("--cut-ga", action="store_true", help="hmmscan 使用 --cut_ga（Pfam GA 阈值）")

    ap.add_argument("--interactive", action="store_true", help="交互式设置阈值（回车=默认值）")

    args = ap.parse_args()

    if args.interactive:
        print("\n[Interactive] 可直接回车使用默认阈值：")
        args.required = _prompt_str("必需Pfam(逗号分隔)", args.required)
        args.dom_i_evalue = _prompt_float("dom_i_evalue 最大值", args.dom_i_evalue, min_v=0.0)
        args.hmm_cov = _prompt_float("required域 HMM覆盖度下限(0-1)", args.hmm_cov, min_v=0.0, max_v=1.0)
        args.cpu = _prompt_int("hmmscan CPU线程数", args.cpu, min_v=1)
        args.cut_ga = _prompt_bool("是否启用 hmmscan --cut_ga", args.cut_ga)
        print()

    # 让脚本可以从项目根目录运行
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from pipelines.merge_candidates import merge_hmm_blast_ids
    from pipelines.fasta_utils import read_id_set, extract_fasta_by_ids
    from pipelines.domain_scan_pipeline import run_hmmscan_pfam_pipeline

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) merge candidates
    m = merge_hmm_blast_ids(args.hmm_id, args.blast_id, str(out_dir), args.prefix)

    # 2) extract candidates pep
    ids = read_id_set(m.merged_id)
    candidates_fa = (out_dir / f"{args.prefix}.candidates.pep.fa").resolve()
    n = extract_fasta_by_ids(args.pep, ids, str(candidates_fa))

    # 3) pfam hmmscan + required domain filter
    required = [x.strip() for x in args.required.split(",") if x.strip()]
    d = run_hmmscan_pfam_pipeline(
        pfam_hmm=args.pfam,
        query_fasta=str(candidates_fa),
        out_dir=str(out_dir),
        prefix=args.prefix,
        required_pfams=required,
        dom_i_evalue_max=args.dom_i_evalue,
        hmm_coverage_min=args.hmm_cov,
        cpu=args.cpu,
        use_cut_ga=args.cut_ga,
    )

    print("=== DONE ===")
    print(f"merged candidates: {m.merged_count} (hmm={m.hmm_count}, blast={m.blast_count}, both={m.both_count})")
    print(f"candidates pep written: {n} -> {candidates_fa}")
    print(f"domains tsv: {d.domains_tsv}")
    print(f"final id: {d.final_id}")
    print(f"final tsv: {d.final_tsv}")
    print(f"final_count: {d.final_count}")


if __name__ == "__main__":
    main()
