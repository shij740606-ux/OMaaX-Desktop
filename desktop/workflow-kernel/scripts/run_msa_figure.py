#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Dict, List, Tuple, Optional


def which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def run_cmd(cmd: List[str], cwd: Optional[Path] = None):
    subprocess.run(cmd, check=True, cwd=str(cwd) if cwd else None)


def read_fasta(path: Path) -> Dict[str, str]:
    seqs: Dict[str, str] = {}
    name = None
    chunks: List[str] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            if line.startswith(">"):
                if name is not None:
                    seqs[name] = "".join(chunks)
                name = line[1:].split()[0]
                chunks = []
            else:
                chunks.append(line.strip())
        if name is not None:
            seqs[name] = "".join(chunks)
    return seqs


def write_fasta(path: Path, seqs: Dict[str, str]):
    with path.open("w", encoding="utf-8") as w:
        for k, v in seqs.items():
            w.write(f">{k}\n")
            for i in range(0, len(v), 60):
                w.write(v[i:i+60] + "\n")


def conservation_profile(aln: Dict[str, str]) -> Tuple[List[float], List[float]]:
    # 返回: per-column score, per-column nongap fraction
    if not aln:
        return [], []
    names = list(aln.keys())
    L = len(aln[names[0]])
    for n in names[1:]:
        if len(aln[n]) != L:
            raise RuntimeError("Alignment length not equal across sequences")

    scores: List[float] = []
    nongaps: List[float] = []

    for i in range(L):
        col = [aln[n][i] for n in names]
        nongap_chars = [c for c in col if c != "-" and c != "."]
        ng = len(nongap_chars) / len(col)
        nongaps.append(ng)

        if len(nongap_chars) == 0:
            scores.append(0.0)
            continue

        freq: Dict[str, int] = {}
        for c in nongap_chars:
            freq[c] = freq.get(c, 0) + 1
        maxfreq = max(freq.values()) / len(nongap_chars)

        # 简单且稳定：保守性 = (最常见氨基酸比例) * (非gap比例)
        scores.append(maxfreq * ng)

    return scores, nongaps


def best_window(scores: List[float], nongaps: List[float], win: int, min_ng: float) -> Tuple[int, int, float]:
    # 返回 0-based [start, end) 以及 best mean score
    if win <= 0:
        raise ValueError("window length must be >0")
    L = len(scores)
    if L < win:
        return 0, L, sum(scores)/max(1, L)

    best_s = -1.0
    best_i = 0
    for i in range(0, L - win + 1):
        ng_mean = sum(nongaps[i:i+win]) / win
        if ng_mean < min_ng:
            continue
        s_mean = sum(scores[i:i+win]) / win
        if s_mean > best_s:
            best_s = s_mean
            best_i = i

    if best_s < 0:  # 没有窗口满足 min_ng，就退化为不设约束的最高均值
        best_s = -1.0
        best_i = 0
        for i in range(0, L - win + 1):
            s_mean = sum(scores[i:i+win]) / win
            if s_mean > best_s:
                best_s = s_mean
                best_i = i

    return best_i, best_i + win, best_s


def extract_region_short_fasta(aln: Dict[str, str], start: int, end: int) -> Dict[str, str]:
    # 从 alignment 区间提取“短序列”（去掉gap），用于二次绘图时再对短序列做一次align+plot
    out: Dict[str, str] = {}
    for k, v in aln.items():
        seg = v[start:end]
        seg2 = "".join([c for c in seg if c != "-" and c != "."])
        out[k] = seg2 if seg2 else "X"  # 避免空序列导致下游脚本炸（用占位 X）
    return out


def glob_outputs(msa_dir: Path, outbase: str) -> List[str]:
    # 收集 outbase 开头的一切文件（相对路径）
    files = sorted([p.name for p in msa_dir.glob(outbase + "*") if p.is_file()])
    return files


def main():
    ap = argparse.ArgumentParser(description="MSA full figure + conserved region re-figure (mafft preferred).")
    ap.add_argument("--manifest", required=True)

    ap.add_argument("--method", default="mafft", choices=["mafft", "muscle"],
                    help="which R plotting script to use for full plot (default: mafft)")

    ap.add_argument("--r-mafft", default=None, help="msa_figure.mattf.R (default: scripts/msa_figure.mattf.R)")
    ap.add_argument("--r-muscle", default=None, help="msa_figure.R (default: scripts/msa_figure.R)")

    ap.add_argument("--outdir", default=None, help="default: <workspace>/<prefix>.msa")
    ap.add_argument("--cpu", type=int, default=8)
    ap.add_argument("--force", action="store_true")

    # conserved region params
    ap.add_argument("--window-len", type=int, default=30, help="length of conserved window (aa columns)")
    ap.add_argument("--min-nongap", type=float, default=0.6, help="mean nongap fraction threshold for the window")
    args = ap.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

    from agents.context import RunContext

    mp = Path(args.manifest).resolve()
    d = json.load(mp.open("r", encoding="utf-8"))
    ws = Path(d["workspace"]).resolve()
    prefix = d["prefix"]
    artifacts = d.get("artifacts", {})

    ctx = RunContext(workspace=ws, prefix=prefix, force=args.force)
    lg = ctx.logger()

    rscript = which("Rscript")
    if not rscript:
        raise RuntimeError("Rscript not found in PATH")

    # 输入 fasta：优先 final_fa（你鉴定出的成员）
    in_fa = artifacts.get("final_fa")
    if not in_fa:
        raise RuntimeError("manifest.artifacts.final_fa is missing")
    in_fa = str(Path(in_fa).resolve())

    # alignment：优先用 phylogeny 产物 aln_fa（最稳定）
    aln_fa = artifacts.get("aln_fa")
    if not aln_fa:
        # fallback: 约定位置
        aln_fa = str((ws / f"{prefix}.final_members.aln.fa").resolve())
    aln_path = Path(aln_fa)
    if not aln_path.exists():
        raise RuntimeError(f"alignment fasta not found: {aln_path} (please run phylogeny stage first)")

    # 输出目录
    msa_dir = Path(args.outdir).resolve() if args.outdir else (ws / f"{prefix}.msa").resolve()
    msa_dir.mkdir(parents=True, exist_ok=True)

    # 脚本路径
    r_mafft = Path(args.r_mafft).resolve() if args.r_mafft else (project_root / "scripts" / "msa_figure.mattf.R")
    r_muscle = Path(args.r_muscle).resolve() if args.r_muscle else (project_root / "scripts" / "msa_figure.R")
    if not r_mafft.exists():
        raise RuntimeError(f"msa_figure.mattf.R not found: {r_mafft}")
    if not r_muscle.exists():
        raise RuntimeError(f"msa_figure.R not found: {r_muscle}")

    # 1) 全长 MSA 图（按你习惯：mafft 绘图法为主）
    if args.method == "mafft":
        outbase_full = f"{prefix}.pep.msa.mattf"
        cmd = [rscript, str(r_mafft), in_fa, outbase_full]
    else:
        outbase_full = f"{prefix}.pep.msa"
        cmd = [rscript, str(r_muscle), in_fa, outbase_full]

    # 只要已有输出且不 force，就跳过（粗略判断：有没有 outbase 开头文件）
    exist_full = len(glob_outputs(msa_dir, outbase_full)) > 0
    if (not args.force) and exist_full:
        lg.info("skip full msa figure (outputs exist)")
    else:
        lg.info("Run full msa figure...")
        run_cmd(cmd, cwd=msa_dir)

    full_files = glob_outputs(msa_dir, outbase_full)

    # 2) 自动找最保守区域（基于 alignment）
    aln = read_fasta(aln_path)
    scores, nongaps = conservation_profile(aln)
    start, end, best = best_window(scores, nongaps, args.window_len, args.min_nongap)

    # 输出 profile / region report
    profile_tsv = msa_dir / f"{prefix}.msa.conservation_profile.tsv"
    with profile_tsv.open("w", encoding="utf-8") as w:
        w.write("col_1based\tscore\tnongap_frac\n")
        for i, (s, ng) in enumerate(zip(scores, nongaps), start=1):
            w.write(f"{i}\t{s:.6f}\t{ng:.6f}\n")

    region_tsv = msa_dir / f"{prefix}.msa.conserved_region.tsv"
    with region_tsv.open("w", encoding="utf-8") as w:
        w.write("start_col_1based\tend_col_1based_inclusive\twindow_len\tmean_score\tmin_nongap\n")
        w.write(f"{start+1}\t{end}\t{args.window_len}\t{best:.6f}\t{args.min_nongap}\n")

    # 3) 导出保守区短序列 fasta（用于二次绘图）
    region_fa = msa_dir / f"{prefix}.msa.conserved_region.short.fa"
    short_seqs = extract_region_short_fasta(aln, start, end)
    write_fasta(region_fa, short_seqs)

    # 4) 二次绘图：对“短序列 fasta”再跑一次你选定的 R 绘图脚本
    #    （这样得到的图就是“最保守区域放大图”，也最接近期刊常用做法）
    outbase_region = f"{prefix}.pep.msa.mattf.conserved" if args.method == "mafft" else f"{prefix}.pep.msa.conserved"
    exist_region = len(glob_outputs(msa_dir, outbase_region)) > 0
    if (not args.force) and exist_region:
        lg.info("skip conserved-region figure (outputs exist)")
    else:
        lg.info("Run conserved-region msa figure...")
        if args.method == "mafft":
            cmd2 = [rscript, str(r_mafft), str(region_fa), outbase_region]
        else:
            cmd2 = [rscript, str(r_muscle), str(region_fa), outbase_region]
        run_cmd(cmd2, cwd=msa_dir)

    region_files = glob_outputs(msa_dir, outbase_region)

    # 5) 写回 manifest
    artifacts["msa_dir"] = str(msa_dir)
    artifacts["msa_full_outbase"] = outbase_full
    artifacts["msa_full_files"] = full_files
    artifacts["msa_conservation_profile_tsv"] = str(profile_tsv)
    artifacts["msa_conserved_region_tsv"] = str(region_tsv)
    artifacts["msa_conserved_region_short_fa"] = str(region_fa)
    artifacts["msa_conserved_window_start_col_1based"] = start + 1
    artifacts["msa_conserved_window_end_col_1based_inclusive"] = end
    artifacts["msa_conserved_window_mean_score"] = best
    artifacts["msa_region_outbase"] = outbase_region
    artifacts["msa_region_files"] = region_files

    d["artifacts"] = artifacts
    d.setdefault("steps", []).append({
        "agent": "msa_figure",
        "out": {
            "msa_dir": str(msa_dir),
            "full_outbase": outbase_full,
            "full_files": full_files,
            "alignment_used": str(aln_path),
            "window_len": args.window_len,
            "min_nongap": args.min_nongap,
            "region_start_col_1based": start + 1,
            "region_end_col_1based_inclusive": end,
            "region_mean_score": best,
            "region_short_fa": str(region_fa),
            "region_outbase": outbase_region,
            "region_files": region_files,
        }
    })

    mp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    print("MSA figure finished.")
    print("  msa_dir:", msa_dir)
    print("  full_outbase:", outbase_full)
    print("  full_files:", full_files)
    print("  conserved window (1-based):", start + 1, "-", end, "mean_score=", best)
    print("  region_short_fa:", region_fa)
    print("  region_outbase:", outbase_region)
    print("  region_files:", region_files)
    print("  manifest updated:", mp)


if __name__ == "__main__":
    main()
