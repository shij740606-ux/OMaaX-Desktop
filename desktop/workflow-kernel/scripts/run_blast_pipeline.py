# scripts/run_blast_pipeline.py
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipelines.blast_pipeline import run_blastp_pipeline


def main():
    ap = argparse.ArgumentParser("--interactive", action="store_true", help="交互式设置阈值（回车=默认值）")
    ap.add_argument("--ref", required=True, help="参考家族蛋白 fasta（如 SPL_Ath.fasta）")
    ap.add_argument("--query", required=True, help="目标物种全蛋白 fasta（如 Ft.pep.fasta）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--prefix", required=True, help="输出前缀（如 Ft.SPL）")
    ap.add_argument("--evalue", type=float, default=1e-5)
    ap.add_argument("--pident", type=float, default=30.0)
    ap.add_argument("--qcov", type=float, default=40.0)
    ap.add_argument("--scov", type=float, default=40.0)
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()

    out = run_blastp_pipeline(
        reference_fasta=args.ref,
        query_fasta=args.query,
        out_dir=args.out,
        prefix=args.prefix,
        evalue_max=args.evalue,
        pident_min=args.pident,
        qcov_min=args.qcov,
        scov_min=args.scov,
        threads=args.threads,
    )

    print("=== DONE ===")
    print("blastout:", out.blastout)
    print("filtered_tsv:", out.filtered_tsv)
    print("id_list:", out.id_list)
    print(f"hits: {out.hit_count}, unique_ids: {out.unique_id_count}")


if __name__ == "__main__":
    main()
