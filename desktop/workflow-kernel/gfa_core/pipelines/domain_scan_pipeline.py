from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple


@dataclass
class DomainScanOutputs:
    out_dir: str
    query_fasta: str
    pfam_hmm: str
    domtblout: str
    tblout: str
    hmmscan_out: str
    domains_tsv: str
    final_id: str
    final_tsv: str
    final_count: int
    params: Dict[str, Any]


def _ensure_exe(name: str) -> str:
    exe = shutil.which(name)
    if not exe:
        raise RuntimeError(f"找不到可执行程序: {name} (请确认已安装并在 PATH 中)")
    return exe


def _parse_domtblout(parts: List[str]) -> Optional[Dict[str, Any]]:
    if len(parts) < 22:
        return None
    try:
        return {
            "target_name": parts[0],                       # Pfam 名称（hmmscan 时是 domain model）
            "target_accession": None if parts[1] == "-" else parts[1],  # PFxxxxx.xx
            "target_len": int(parts[2]),                   # HMM 长度
            "query_name": parts[3],                        # 蛋白ID
            "query_len": int(parts[5]),                    # 蛋白长度
            "full_evalue": float(parts[6]),
            "dom_i_evalue": float(parts[12]),
            "hmm_from": int(parts[15]),
            "hmm_to": int(parts[16]),
            "ali_from": int(parts[17]),
            "ali_to": int(parts[18]),
            "acc": float(parts[21]),
        }
    except Exception:
        return None


def run_hmmscan_pfam_pipeline(
    pfam_hmm: str,
    query_fasta: str,
    out_dir: str,
    prefix: str,
    required_pfams: List[str],          # 例如 ["PF03110"]（SPL 的 SBP）
    dom_i_evalue_max: float = 1e-5,
    hmm_coverage_min: float = 0.5,      # 用 HMM 覆盖度更合理（SBP 是 ~75aa）
    cpu: int = 8,
    use_cut_ga: bool = False,
) -> DomainScanOutputs:
    _ensure_exe("hmmscan")

    outp = Path(out_dir).resolve()
    outp.mkdir(parents=True, exist_ok=True)

    pfam = Path(pfam_hmm).resolve()
    qry = Path(query_fasta).resolve()

    domtblout = (outp / f"{prefix}.pfam.domtblout").resolve()
    tblout = (outp / f"{prefix}.pfam.tblout").resolve()
    hmmscan_out = (outp / f"{prefix}.pfam.out").resolve()
    domains_tsv = (outp / f"{prefix}.pfam.domains.tsv").resolve()
    final_id = (outp / f"{prefix}.final_members.id").resolve()
    final_tsv = (outp / f"{prefix}.final_members.tsv").resolve()
    summary_json = (outp / f"{prefix}.pfam.summary.json").resolve()

    cmd = ["hmmscan", "--cpu", str(cpu), "--domtblout", str(domtblout), "--tblout", str(tblout), "-o", str(hmmscan_out)]
    if use_cut_ga:
        cmd.append("--cut_ga")
    cmd += [str(pfam), str(qry)]

    proc = subprocess.run(cmd, cwd=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"hmmscan 失败\nCMD: {' '.join(cmd)}\nSTDERR:\n{proc.stderr[:4000]}")

    required_set: Set[str] = set(required_pfams)
    # 允许传 PF03110 或 PF03110.15；我们统一取 base
    required_base: Set[str] = {x.split(".")[0] for x in required_set}

    # 收集所有 domain 命中；并筛选 required domain
    rows: List[Dict[str, Any]] = []
    best_required: Dict[str, Dict[str, Any]] = {}  # query -> best hit (lowest i-evalue)

    with domtblout.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split()
            rec = _parse_domtblout(parts)
            if not rec:
                continue

            acc = rec["target_accession"] or ""
            acc_base = acc.split(".")[0] if acc else ""
            hmm_cov = (rec["hmm_to"] - rec["hmm_from"] + 1) / rec["target_len"] if rec["target_len"] else 0.0

            rec_out = {
                "query": rec["query_name"],
                "pfam_acc": acc_base,
                "pfam_name": rec["target_name"],
                "dom_i_evalue": rec["dom_i_evalue"],
                "hmm_cov": hmm_cov,
                "hmm_from": rec["hmm_from"],
                "hmm_to": rec["hmm_to"],
                "ali_from": rec["ali_from"],
                "ali_to": rec["ali_to"],
                "query_len": rec["query_len"],
            }
            rows.append(rec_out)

            is_required = (acc_base in required_base) or (rec["target_name"] in required_set)
            if not is_required:
                continue
            if rec["dom_i_evalue"] > dom_i_evalue_max:
                continue
            if hmm_cov < hmm_coverage_min:
                continue

            cur = best_required.get(rec["query_name"])
            if (cur is None) or (rec_out["dom_i_evalue"] < cur["dom_i_evalue"]):
                best_required[rec["query_name"]] = rec_out

    # 写 domains 表（全域信息，方便你后续 Rscript 画图/整合）
    with domains_tsv.open("w", encoding="utf-8") as w:
        w.write("query\tpfam_acc\tpfam_name\tdom_i_evalue\thmm_cov\thmm_from\thmm_to\tali_from\tali_to\tquery_len\n")
        rows.sort(key=lambda x: (x["query"], x["dom_i_evalue"]))
        for r in rows:
            w.write(
                f"{r['query']}\t{r['pfam_acc']}\t{r['pfam_name']}\t{r['dom_i_evalue']:.3g}\t"
                f"{r['hmm_cov']:.3f}\t{r['hmm_from']}\t{r['hmm_to']}\t{r['ali_from']}\t{r['ali_to']}\t{r['query_len']}\n"
            )

    finals = sorted(best_required.keys())
    with final_id.open("w", encoding="utf-8") as w:
        for q in finals:
            w.write(q + "\n")

    # final 成员表（带 required domain 的最好命中）
    with final_tsv.open("w", encoding="utf-8") as w:
        w.write("id\trequired_pfam\tdom_i_evalue\thmm_cov\tpfam_name\tali_range\tquery_len\n")
        for q in finals:
            r = best_required[q]
            w.write(
                f"{q}\t{r['pfam_acc']}\t{r['dom_i_evalue']:.3g}\t{r['hmm_cov']:.3f}\t{r['pfam_name']}\t"
                f"{r['ali_from']}-{r['ali_to']}\t{r['query_len']}\n"
            )

    out = DomainScanOutputs(
        out_dir=str(outp),
        query_fasta=str(qry),
        pfam_hmm=str(pfam),
        domtblout=str(domtblout),
        tblout=str(tblout),
        hmmscan_out=str(hmmscan_out),
        domains_tsv=str(domains_tsv),
        final_id=str(final_id),
        final_tsv=str(final_tsv),
        final_count=len(finals),
        params={
            "required_pfams": required_pfams,
            "dom_i_evalue_max": dom_i_evalue_max,
            "hmm_coverage_min": hmm_coverage_min,
            "cpu": cpu,
            "use_cut_ga": use_cut_ga,
        },
    )

    with summary_json.open("w", encoding="utf-8") as w:
        json.dump(asdict(out), w, ensure_ascii=False, indent=2)

    return out
