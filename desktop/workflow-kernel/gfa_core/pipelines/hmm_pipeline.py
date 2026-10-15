# pipelines/hmm_pipeline.py
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple


@dataclass
class HmmsearchOutputs:
    out_dir: str
    domtblout: str
    hmmout: str
    domtblout_filter: str
    id_list: str
    hit_count: int
    unique_id_count: int
    params: Dict[str, Any]


def _ensure_exe(name: str) -> str:
    exe = shutil.which(name)
    if not exe:
        raise RuntimeError(
            f"找不到可执行程序: {name}。请先在 Ubuntu 上安装并确保在 PATH 中：\n"
            f"  - HMMER: sudo apt-get install hmmer\n"
            f"  - 或者你用 conda 安装 hmmer\n"
        )
    return exe


def _parse_domtblout_line(parts: List[str]) -> Optional[Dict[str, Any]]:
    """
    domtblout 标准列（HMMER 3）：
    0 target_name
    1 target_accession
    2 target_len
    3 query_name
    4 query_accession
    5 query_len
    6 full_evalue
    7 full_score
    8 full_bias
    9 dom_num
    10 dom_of
    11 dom_c_evalue
    12 dom_i_evalue
    13 dom_score
    14 dom_bias
    15 hmm_from
    16 hmm_to
    17 ali_from
    18 ali_to
    19 env_from
    20 env_to
    21 acc
    22... description
    """
    if len(parts) < 22:
        return None
    try:
        return {
            "target_name": parts[0],
            "target_accession": None if parts[1] == "-" else parts[1],
            "target_len": int(parts[2]),
            "query_name": parts[3],
            "query_accession": None if parts[4] == "-" else parts[4],
            "query_len": int(parts[5]),
            "full_evalue": float(parts[6]),
            "dom_i_evalue": float(parts[12]),
            "acc": float(parts[21]),
        }
    except Exception:
        return None


def filter_domtblout(
    domtblout_path: str,
    out_filter_path: str,
    full_evalue_max: Optional[float] = None,
    dom_i_evalue_max: Optional[float] = None,
) -> Tuple[int, List[str]]:
    """
    读取 domtblout，输出过滤后的“无#注释行”到 out_filter_path，
    同时返回：过滤后命中行数、unique target_name 列表
    """
    domtblout = Path(domtblout_path)
    out_filter = Path(out_filter_path)
    out_filter.parent.mkdir(parents=True, exist_ok=True)

    kept_lines: List[str] = []
    ids: List[str] = []

    with domtblout.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split()
            parsed = _parse_domtblout_line(parts)
            if not parsed:
                continue

            ok = True
            if full_evalue_max is not None and parsed["full_evalue"] > full_evalue_max:
                ok = False
            if dom_i_evalue_max is not None and parsed["dom_i_evalue"] > dom_i_evalue_max:
                ok = False

            if ok:
                kept_lines.append(line)
                ids.append(parsed["target_name"])

    # 写 filter 文件（保持原始行，后续你也可继续 awk/grep）
    with out_filter.open("w", encoding="utf-8") as w:
        for line in kept_lines:
            w.write(line)

    unique_ids = sorted(set(ids))
    return len(kept_lines), unique_ids

def run_hmmsearch_pipeline(
    hmm_profile: str,
    protein_fasta: str,
    out_dir: str,
    prefix: str,
    cut_tc: bool = True,
    cpu: int = 4,
    full_evalue_max: Optional[float] = None,
    dom_i_evalue_max: Optional[float] = None,
    extra_args: Optional[List[str]] = None,
) -> HmmsearchOutputs:
    """
    通用封装：hmmsearch -> domtblout -> filter -> 提取 unique ID list
    """
    _ensure_exe("hmmsearch")

    # 关键：统一用绝对路径，避免 cwd 影响
    outp = Path(out_dir).resolve()
    outp.mkdir(parents=True, exist_ok=True)

    domtblout = (outp / f"{prefix}.domtblout").resolve()
    hmmout = (outp / f"{prefix}.hmmout").resolve()
    domtblout_filter = (outp / f"{prefix}.domtblout.filter").resolve()
    id_list = (outp / f"{prefix}.domtblout.filter.id").resolve()
    summary_json = (outp / f"{prefix}.summary.json").resolve()

    # 关键：先构建 cmd，再 run
    cmd = ["hmmsearch"]
    if cut_tc:
        cmd.append("--cut_tc")
    cmd += ["--cpu", str(cpu)]
    cmd += ["--domtblout", str(domtblout)]
    cmd += ["-o", str(hmmout)]
    if extra_args:
        cmd += extra_args
    cmd += [str(Path(hmm_profile).resolve()), str(Path(protein_fasta).resolve())]

    proc = subprocess.run(
        cmd,
        cwd=None,  # 关键：不要设置 cwd=str(outp)，否则相对路径会被重复拼接
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "hmmsearch 执行失败。\n"
            f"CMD: {' '.join(cmd)}\n"
            f"STDERR:\n{proc.stderr[:4000]}\n"
        )

    hit_count, unique_ids = filter_domtblout(
        domtblout_path=str(domtblout),
        out_filter_path=str(domtblout_filter),
        full_evalue_max=full_evalue_max,
        dom_i_evalue_max=dom_i_evalue_max,
    )

    with id_list.open("w", encoding="utf-8") as w:
        for gid in sorted(set(unique_ids)):
            w.write(gid + "\n")

    outputs = HmmsearchOutputs(
        out_dir=str(outp),
        domtblout=str(domtblout),
        hmmout=str(hmmout),
        domtblout_filter=str(domtblout_filter),
        id_list=str(id_list),
        hit_count=hit_count,
        unique_id_count=len(set(unique_ids)),
        params={
            "hmm_profile": str(Path(hmm_profile).resolve()),
            "protein_fasta": str(Path(protein_fasta).resolve()),
            "prefix": prefix,
            "cut_tc": cut_tc,
            "cpu": cpu,
            "full_evalue_max": full_evalue_max,
            "dom_i_evalue_max": dom_i_evalue_max,
            "extra_args": extra_args or [],
        },
    )

    with summary_json.open("w", encoding="utf-8") as w:
        json.dump(asdict(outputs), w, ensure_ascii=False, indent=2)

    return outputs
