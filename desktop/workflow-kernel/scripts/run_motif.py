#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys


def which(cmd: str):
    return shutil.which(cmd)


def run_cmd(cmd, cwd: Path | None = None, lg=None):
    if lg:
        lg.info("CMD: " + " ".join(cmd) + (f"  (cwd={cwd})" if cwd else ""))
    subprocess.run(cmd, check=True, cwd=str(cwd) if cwd else None)


def default_label(prefix: str) -> str:
    # 让 Ft.SPL -> Ft_SPL，避免点号影响下游脚本/文件名
    return prefix.replace(".", "_")


def main():
    ap = argparse.ArgumentParser(description="Run MEME motif + seqlogo pipeline based on identify.manifest.json")

    ap.add_argument("--manifest", required=True, help="identify.manifest.json path")

    # MEME params (✅ 默认按你要求)
    ap.add_argument("--cpu", type=int, default=8)
    ap.add_argument("--model", default="anr", choices=["anr", "zoops", "ops"])
    ap.add_argument("--nmotifs", type=int, default=10)
    ap.add_argument("--minw", type=int, default=6)
    ap.add_argument("--maxw", type=int, default=200)

    # label/output
    ap.add_argument("--label", default=None, help="used by get_meme_pos.py / draw_motif_seqlogo.R (default: prefix with '.'->'_')")
    ap.add_argument("--outdir", default=None, help="motif root dir (default: <workspace>/<prefix>.motif)")
    ap.add_argument("--force", action="store_true")

    # downstream scripts (default: project scripts/)
    ap.add_argument("--get-meme-pos", default=None, help="path to get_meme_pos.py (default: scripts/get_meme_pos.py)")
    ap.add_argument("--draw-seqlogo", default=None, help="path to draw_motif_seqlogo.R (default: scripts/draw_motif_seqlogo.R)")
    ap.add_argument("--no-logo", action="store_true", help="run MEME only, skip Python/R logo steps")

    args = ap.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

    from agents.context import RunContext
    from agents.motif.meme_agent import MemeAgent

    mp = Path(args.manifest).resolve()
    d = json.load(mp.open("r", encoding="utf-8"))

    ws = Path(d["workspace"]).resolve()
    prefix = d["prefix"]
    artifacts = d.get("artifacts", {})

    in_fa = artifacts.get("final_fa")
    if not in_fa:
        raise RuntimeError("manifest.artifacts.final_fa is missing")
    in_fa = str(Path(in_fa).resolve())

    label = args.label if args.label else default_label(prefix)

    # 输出目录布局
    motif_root = Path(args.outdir).resolve() if args.outdir else (ws / f"{prefix}.motif").resolve()
    meme_dir = motif_root / "meme_out"  # ✅ 模仿你原始的 meme_out 目录命名
    motif_root.mkdir(parents=True, exist_ok=True)

    ctx = RunContext(workspace=ws, prefix=prefix, force=args.force)
    lg = ctx.logger()

    # 1) MEME
    agent = MemeAgent()
    meme_out = agent.run(
        ctx,
        in_fa=in_fa,
        out_dir=str(meme_dir),
        cpu=args.cpu,
        nmotifs=args.nmotifs,
        minw=args.minw,
        maxw=args.maxw,
        model=args.model,
        force=args.force,
    )

    # 2) get_meme_pos.py + draw_motif_seqlogo.R
    motif_prot_txt = str((motif_root / f"{label}.motif_prot.txt").resolve())
    motif_logo_prefix = str((motif_root / f"{label}_motif").resolve())

    if not args.no_logo:
        rscript = which("Rscript")
        if not rscript:
            raise RuntimeError("Rscript not found in PATH")

        get_meme_pos = Path(args.get_meme_pos).resolve() if args.get_meme_pos else (project_root / "scripts" / "get_meme_pos.py")
        draw_seqlogo = Path(args.draw_seqlogo).resolve() if args.draw_seqlogo else (project_root / "scripts" / "draw_motif_seqlogo.R")

        if not get_meme_pos.exists():
            raise RuntimeError(f"get_meme_pos.py not found: {get_meme_pos}")
        if not draw_seqlogo.exists():
            raise RuntimeError(f"draw_motif_seqlogo.R not found: {draw_seqlogo}")

        # 你原来的命令：
        # python get_meme_pos.py meme_out/meme.txt Ft_SPL
        # Rscript draw_motif_seqlogo.R Ft_SPL.motif_prot.txt Ft_SPL_motif
        #
        # ✅ 为了让输出都落到 motif_root，我们设 cwd=motif_root，并传入 meme.txt 的绝对路径
        run_cmd([sys.executable, str(get_meme_pos), meme_out.meme_txt, label], cwd=motif_root, lg=lg)

        # sanity: ?? Python ??????
        if not Path(motif_prot_txt).exists():
            # 兜底：有些版本会输出到当前目录 label.motif_prot.txt（我们 cwd 已经是 motif_root，理论上一定命中）
            raise RuntimeError(f"Expected motif_prot file not found: {motif_prot_txt}")

        run_cmd([rscript, str(draw_seqlogo), Path(motif_prot_txt).name, Path(motif_logo_prefix).name], cwd=motif_root, lg=lg)

    # 3) 写回 manifest
    artifacts["motif_root"] = str(motif_root)
    artifacts["meme_dir"] = meme_out.meme_dir
    artifacts["meme_txt"] = meme_out.meme_txt
    artifacts["meme_html"] = meme_out.meme_html
    artifacts["motif_label"] = label
    if not args.no_logo:
        artifacts["motif_prot_txt"] = motif_prot_txt
        artifacts["motif_logo_prefix"] = motif_logo_prefix

    d["artifacts"] = artifacts
    d.setdefault("steps", []).append({"agent": agent.name, "out": meme_out.__dict__})
    d.setdefault("steps", []).append({
        "agent": "motif_seqlogo",
        "out": {
            "motif_root": str(motif_root),
            "label": label,
            "motif_prot_txt": (motif_prot_txt if not args.no_logo else None),
            "motif_logo_prefix": (motif_logo_prefix if not args.no_logo else None),
            "no_logo": args.no_logo,
        }
    })

    mp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Motif pipeline finished.")
    print("  motif_root:", motif_root)
    print("  meme_dir  :", meme_out.meme_dir)
    print("  meme_txt  :", meme_out.meme_txt)
    print("  meme_html :", meme_out.meme_html)
    if not args.no_logo:
        print("  motif_txt :", motif_prot_txt)
        print("  logo_pref :", motif_logo_prefix)
    print("  manifest updated:", str(mp))


if __name__ == "__main__":
    main()
