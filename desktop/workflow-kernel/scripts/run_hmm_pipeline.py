# scripts/run_hmm_pipeline.py
import argparse
from pathlib import Path
import sys

# 把 bba/bba 这一层加入 Python 搜索路径，这样才能 import pipelines.*
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipelines.hmm_pipeline import run_hmmsearch_pipeline

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hmm", required=True)
    ap.add_argument("--pep", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--cpu", type=int, default=4)
    ap.add_argument("--no-cut-tc", action="store_true")
    ap.add_argument("--full-evalue", type=float, default=None)
    ap.add_argument("--dom-i-evalue", type=float, default=None)
    args = ap.parse_args()

    out = run_hmmsearch_pipeline(
        hmm_profile=args.hmm,
        protein_fasta=args.pep,
        out_dir=args.out,
        prefix=args.prefix,
        cut_tc=not args.no_cut_tc,
        cpu=args.cpu,
        full_evalue_max=args.full_evalue,
        dom_i_evalue_max=args.dom_i_evalue,
    )

    print("=== DONE ===")
    print("domtblout:", out.domtblout)
    print("hmmout:", out.hmmout)
    print("filter:", out.domtblout_filter)
    print("id_list:", out.id_list)
    print(f"hits: {out.hit_count}, unique_ids: {out.unique_id_count}")

if __name__ == "__main__":
    main()
