#!/usr/bin/env python3
"""
identify stage (web version) - 修订版
使用原始用户上传文件，而不是预处理文件

Main tasks:
- find HMM: user uploaded, or fetch from Pfam-A.hmm by required_pfams.
- hmmsearch with HMM vs protein sequences.
- domain QC and final member selection.

Inputs:
- input/<job_id>/ (包含用户上传的所有原始文件)
- 不再使用 workspace/<job_id>/prepared/ 中的预处理文件

Outputs:
- workspace/<job_id>/final/<prefix>.identify_report.md
- workspace/<job_id>/final/<prefix>.identified_members.pep.fa
- workspace/<job_id>/final/<prefix>.identified_members.id
- workspace/<job_id>/final/<prefix>.identify.manifest.json

Design:
- 直接从 input/<job_id>/ 查找原始蛋白质序列文件
- 用户上传的HMM优先使用
- 如果没有HMM，则从Pfam数据库获取
- 直接使用原始文件进行hmmsearch和blast分析
"""
import os
import gzip
import shutil
import psutil
import argparse
import json
import logging
import re
import shlex
import subprocess
import sys
import time
import hashlib
import platform
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any, Union, Set

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.gene_family_scope import read_scope, sanitize_token
from gfa_core.gene_family_stage_context import species_prefix_for


# 镜像标准形态: 环境前缀由当前解释器推导(保留环境变量覆盖点)
CONDA_PREFIX = Path(os.environ.get("BBA_CONDA_PREFIX", "")).expanduser() or Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
CONDA_LIB = str(CONDA_PREFIX / "lib")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")
if Path(CONDA_LIB).exists() and CONDA_LIB not in os.environ.get("LD_LIBRARY_PATH", ""):
    os.environ["LD_LIBRARY_PATH"] = CONDA_LIB + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")


def copy2_if_different(source: Path, dest: Path) -> bool:
    """Copy a file unless source and destination resolve to the same path."""
    source = Path(source)
    dest = Path(dest)
    if source.resolve() == dest.resolve():
        return False
    shutil.copy2(source, dest)
    return True


def get_pfam_from_rag(gene_family_name: str, rag_db_path: Path = None) -> str:
    """从本地映射或RAG服务获取Pfam ID"""
    if rag_db_path is None:
        project_root = Path(__file__).resolve().parents[2]
        candidates = []
        env_file = os.environ.get("BBA_RAG_MAPPING_FILE")
        if env_file:
            candidates.append(Path(env_file).expanduser())
        candidates.extend([
            project_root / "data" / "rag_db" / "pfam_mapping.json",
            Path(__file__).resolve().parents[1] / "data" / "rag_db" / "pfam_mapping.json",
            Path(os.environ.get("BIOFLOW_DATA", "")) / "rag_db" / "pfam_mapping.json" if os.environ.get("BIOFLOW_DATA") else None,
        ])
        candidates = [c for c in candidates if c is not None]
        rag_db_path = next((p for p in candidates if p.exists()), candidates[0])

    family_lower = gene_family_name.lower()
    if rag_db_path.exists():
        try:
            with open(rag_db_path, "r", encoding="utf-8") as f:
                rag_db = json.load(f)

            # 精确匹配
            for key, value in rag_db.items():
                if key.lower() == family_lower:
                    print(f"✅ 从本地Pfam映射找到精确匹配: {key} -> {value}")
                    return value

            # 模糊匹配（包含关系）
            for key, value in rag_db.items():
                if family_lower in key.lower() or key.lower() in family_lower:
                    print(f"🔍 本地Pfam映射模糊匹配: {key} -> {value}")
                    return value

            print(f"⚠️  本地Pfam映射中未找到基因家族: {gene_family_name}")
        except Exception as e:
            print(f"❌ 读取本地Pfam映射失败: {e}")
    else:
        print(f"⚠️  本地Pfam映射不存在: {rag_db_path}")

    inferred = query_pfam_from_rag_service(gene_family_name)
    if inferred:
        return inferred

    print(f"❌ 未能从本地映射或RAG服务获取基因家族Pfam: {gene_family_name}")
    return None


def query_pfam_from_rag_service(gene_family_name: str) -> Optional[str]:
    """Ask the running RAG API for a Pfam accession and extract PFxxxxx IDs."""
    if os.environ.get("BBA_DISABLE_RAG_PFAM_LOOKUP", "").lower() in {"1", "true", "yes"}:
        print("ℹ️  已通过 BBA_DISABLE_RAG_PFAM_LOOKUP 禁用RAG Pfam查询")
        return None

    family_url = os.environ.get("BBA_RAG_FAMILY_PFAM_URL", "http://localhost:8000/api/v1/rag/family/pfam")
    timeout = float(os.environ.get("BBA_RAG_PFAM_TIMEOUT", "20"))
    try:
        import urllib.parse
        import urllib.request

        url = f"{family_url}?family={urllib.parse.quote(gene_family_name)}"
        print(f"🔎 本地映射未命中，尝试RAG结构化Pfam接口: {url}")
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        pfam_ids = [str(x).upper() for x in data.get("pfam_ids", []) if re.match(r"^PF\d{5}$", str(x), re.I)]
        if pfam_ids:
            inferred = ",".join(dict.fromkeys(pfam_ids))
            print(f"✅ 从RAG结构化接口获取Pfam: {gene_family_name} -> {inferred}")
            return inferred
        print(f"⚠️  RAG结构化Pfam接口未返回PFxxxxx编号: {gene_family_name}")
    except Exception as e:
        print(f"⚠️  RAG结构化Pfam接口查询失败，回退到chat检索: {e}")

    rag_url = os.environ.get("BBA_RAG_PFAM_URL", "http://localhost:8000/api/v1/rag/chat")
    query = f"{gene_family_name}基因家族的Pfam号是多少"
    payload = {
        "query": query,
        "mode": "agentic",
        "top_k": 15,
        "include_sources": True,
        "use_llm": False,
    }

    try:
        import urllib.request

        req = urllib.request.Request(
            rag_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        print(f"🔎 本地映射未命中，尝试RAG服务查询Pfam: {query}")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        print(f"⚠️  RAG服务Pfam查询失败: {e}")
        return None

    text_parts = [str(data.get("response", ""))]
    for source in data.get("sources", []) or []:
        if isinstance(source, dict):
            text_parts.append(str(source.get("content", "")))
            text_parts.append(json.dumps(source.get("metadata", {}), ensure_ascii=False))

    pfam_ids = []
    seen = set()
    for match in re.findall(r"\bPF\d{5}\b", "\n".join(text_parts), flags=re.I):
        pfam_id = match.upper()
        if pfam_id not in seen:
            seen.add(pfam_id)
            pfam_ids.append(pfam_id)

    if not pfam_ids:
        print(f"⚠️  RAG服务未返回明确PFxxxxx编号: {gene_family_name}")
        return None

    inferred = ",".join(pfam_ids)
    print(f"✅ 从RAG服务推断Pfam: {gene_family_name} -> {inferred}")
    return inferred


def parse_gene_family_from_prefix(prefix: str) -> str:
    """从prefix中提取基因家族名称"""
    # 例如: "At.WRKY" -> "WRKY", "WRKY.At" -> "WRKY"
    parts = prefix.split('.')
    for part in parts:
        if len(part) > 1 and not part[0].isdigit():
            # 返回第一个看起来像基因家族名称的部分
            return part
    return parts[-1] if parts else prefix


def normalize_identify_prefix(prefix: str, gene_family: str) -> str:
    """Normalize target-scope identify labels and reject placeholder tokens."""
    family = _clean_config_text(gene_family)
    provided = _clean_config_text(prefix)
    if not family:
        return provided
    if not provided:
        return f"Target.{family}"
    parts = [_clean_config_text(p) for p in provided.split(".") if _clean_config_text(p)]
    if not parts:
        return f"Target.{family}"
    role = parts[0].lower()
    suffix = _clean_config_text(parts[-1]) if len(parts) >= 2 else ""
    if role == "multi":
        return f"Target.{family}"
    if role == "target" and suffix.lower() in {"", "nan", "none", "null", "undefined", "unknown", "family"}:
        return f"Target.{family}"
    return provided


def run_cmd(cmd: List[str], cwd: Path = None, env=None, shell: bool = False) -> Tuple[int, str, str]:
    """运行命令并返回 (rc, stdout, stderr)"""
    print(f"CMD: {' '.join(cmd)}")
    if cwd:
        print(f"CWD: {cwd}")
    if shell:
        # shell模式下，cmd可以是字符串
        print(f"SHELL CMD: {cmd}")
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd, env=env)
    else:
            p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=env)
    if p.stdout.strip():
        print(p.stdout)
    if p.stderr.strip():
        print(p.stderr)
    return p.returncode, p.stdout, p.stderr



def sanitize_fasta_for_interproscan(pep_file: Path, clean_fasta: Path) -> Dict[str, Any]:
    """Create an InterProScan-safe FASTA by removing stop markers and non-amino-acid residue symbols."""
    stats = {
        "source": str(pep_file),
        "clean_fasta": str(clean_fasta),
        "sequence_count": 0,
        "written_sequences": 0,
        "modified_sequences": 0,
        "removed_asterisks": 0,
        "removed_invalid_residues": 0,
        "skipped_empty_sequences": 0,
    }
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    clean_fasta.parent.mkdir(parents=True, exist_ok=True)

    def flush_record(header: str, chunks: List[str], outfile) -> None:
        if not header:
            return
        header_text = header[1:].strip() if header.startswith(">") else header.strip()
        if not header_text:
            stats["modified_sequences"] += 1
            stats["skipped_empty_sequences"] += 1
            return
        stats["sequence_count"] += 1
        raw_seq = "".join(chunks).replace(" ", "").replace("	", "").upper()
        clean_chars: List[str] = []
        removed_asterisks = 0
        removed_invalid = 0
        for char in raw_seq:
            if char in allowed:
                clean_chars.append(char)
            elif char == "*":
                removed_asterisks += 1
            elif char in {"\n", "\r"}:
                continue
            else:
                removed_invalid += 1
        clean_seq = "".join(clean_chars)
        if removed_asterisks or removed_invalid:
            stats["modified_sequences"] += 1
            stats["removed_asterisks"] += removed_asterisks
            stats["removed_invalid_residues"] += removed_invalid
        if not clean_seq:
            stats["skipped_empty_sequences"] += 1
            return
        outfile.write(header.rstrip("\n") + "\n")
        for idx in range(0, len(clean_seq), 60):
            outfile.write(clean_seq[idx:idx + 60] + "\n")
        stats["written_sequences"] += 1

    header = ""
    chunks: List[str] = []
    with open(pep_file, "r", encoding="utf-8", errors="ignore") as infile, open(clean_fasta, "w", encoding="utf-8") as outfile:
        for line in infile:
            if line.startswith(">"):
                flush_record(header, chunks, outfile)
                header = line.rstrip("\n")
                chunks = []
            else:
                chunks.append(line.strip())
        flush_record(header, chunks, outfile)
    return stats


def prepare_fasta_for_hmmsearch(pep_file: Path, clean_fasta: Path, max_length: int = 100000) -> Dict[str, Any]:
    """Create an HMMER-safe protein FASTA and skip records that exceed hmmsearch limits."""
    stats: Dict[str, Any] = {
        "source": str(pep_file),
        "clean_fasta": str(clean_fasta),
        "sequence_count": 0,
        "written_sequences": 0,
        "skipped_long_sequences": 0,
        "modified_sequences": 0,
        "removed_invalid_residues": 0,
        "max_length": max_length,
        "skipped_ids": [],
    }
    clean_fasta.parent.mkdir(parents=True, exist_ok=True)
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

    def flush_record(header: str, chunks: List[str], outfile) -> None:
        if not header:
            return
        header_id_text = header[1:].strip() if header.startswith(">") else header.strip()
        if not header_id_text:
            stats["modified_sequences"] += 1
            return
        stats["sequence_count"] += 1
        seq_id = header_id_text.split()[0]
        raw_seq = "".join(chunks).replace(" ", "").replace("\t", "").upper()
        clean_chars = []
        removed = 0
        for char in raw_seq:
            if char in allowed:
                clean_chars.append(char)
            elif char in {"\n", "\r"}:
                continue
            else:
                removed += 1
        clean_seq = "".join(clean_chars)
        if removed:
            stats["removed_invalid_residues"] += removed
            stats["modified_sequences"] += 1
        if len(clean_seq) > max_length:
            stats["skipped_long_sequences"] += 1
            if len(stats["skipped_ids"]) < 50:
                stats["skipped_ids"].append({"id": seq_id, "length": len(clean_seq)})
            return
        if not clean_seq:
            stats["modified_sequences"] += 1
            return
        outfile.write(header.rstrip("\n") + "\n")
        for idx in range(0, len(clean_seq), 60):
            outfile.write(clean_seq[idx:idx + 60] + "\n")
        stats["written_sequences"] += 1

    header = ""
    chunks: List[str] = []
    with open(pep_file, "r", encoding="utf-8", errors="ignore") as infile, open(clean_fasta, "w", encoding="utf-8") as outfile:
        for line in infile:
            if line.startswith(">"):
                flush_record(header, chunks, outfile)
                header = line.rstrip("\n")
                chunks = []
            else:
                chunks.append(line.strip())
        flush_record(header, chunks, outfile)

    report_file = clean_fasta.with_suffix(clean_fasta.suffix + ".sanitize.json")
    report_file.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    stats["report"] = str(report_file)
    return stats


def summarize_interproscan_tsv(tsv_file: Path) -> Dict[str, Any]:
    """Return small summary metrics for an InterProScan TSV."""
    summary = {"rows": 0, "proteins_with_hits": 0, "interpro_entries": 0, "signatures": 0}
    proteins = set()
    interpros = set()
    signatures = set()
    if not tsv_file.exists():
        return summary
    with open(tsv_file, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            summary["rows"] += 1
            if cols:
                proteins.add(cols[0])
            if len(cols) > 4 and cols[4]:
                signatures.add(cols[4])
            if len(cols) > 11 and cols[11]:
                interpros.add(cols[11])
    summary.update({
        "proteins_with_hits": len(proteins),
        "interpro_entries": len(interpros),
        "signatures": len(signatures),
    })
    return summary


def write_interproscan_status_file(status_file: Path, result: Dict[str, Any]) -> None:
    """Write a small human-readable status file so the frontend always has a visible domain-check artifact."""
    status_file.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "InterProScan domain check",
        f"status: {result.get('status', 'unknown')}",
        f"input: {result.get('input', '')}",
        f"output: {result.get('output', '')}",
        f"tool: {result.get('tool', '')}",
        f"conda_env: {result.get('conda_env', '')}",
    ]
    if result.get("clean_input"):
        lines.append(f"clean_input: {result.get('clean_input')}")
    if result.get("sanitize"):
        s = result["sanitize"]
        lines.append(
            "sanitize: "
            f"sequences={s.get('sequence_count', 0)}, "
            f"written={s.get('written_sequences', s.get('sequence_count', 0))}, "
            f"modified={s.get('modified_sequences', 0)}, "
            f"removed_asterisks={s.get('removed_asterisks', 0)}, "
            f"removed_invalid={s.get('removed_invalid_residues', 0)}"
        )
    if result.get("summary"):
        s = result["summary"]
        lines.append(
            "summary: "
            f"rows={s.get('rows', 0)}, proteins_with_hits={s.get('proteins_with_hits', 0)}, "
            f"interpro_entries={s.get('interpro_entries', 0)}, signatures={s.get('signatures', 0)}"
        )
    if result.get("returncode") is not None:
        lines.append(f"returncode: {result.get('returncode')}")
    if result.get("elapsed_sec") is not None:
        lines.append(f"elapsed_sec: {result.get('elapsed_sec')}")
    if result.get("reason"):
        lines.extend(["", "reason:", str(result.get("reason"))])
    status_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_id_list(id_file: Path) -> List[str]:
    ids: List[str] = []
    seen: Set[str] = set()
    if not id_file.exists():
        return ids
    with id_file.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            seq_id = text.split()[0]
            if seq_id and seq_id not in seen:
                ids.append(seq_id)
                seen.add(seq_id)
    return ids


def write_id_list(ids: List[str], output_file: Path) -> int:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as handle:
        for seq_id in ids:
            handle.write(f"{seq_id}\n")
    return len(ids)


def write_domain_review_from_interproscan(
    interproscan_tsv: Path,
    required_domains: List[str],
    output_tsv: Path,
    species: str = "",
    id_filter: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Write a compact review table for member confirmation after InterProScan."""
    result = {
        "status": "skipped",
        "input": str(interproscan_tsv),
        "output": str(output_tsv),
        "required_domains": required_domains,
        "species": species,
        "default_keep_ids": [],
    }
    if not interproscan_tsv.exists():
        result["reason"] = f"InterProScan TSV not found: {interproscan_tsv}"
        return result

    required = {str(item).upper() for item in required_domains if str(item).strip()}
    header = [
        "gene_id", "species", "analysis", "signature_accession", "signature_description",
        "interpro_accession", "interpro_description", "start", "end", "protein_length",
        "domain_coverage", "score_or_evalue", "required_domain", "default_keep",
    ]
    rows = 0
    proteins: Set[str] = set()
    default_keep: Set[str] = set()
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    with interproscan_tsv.open("r", encoding="utf-8", errors="ignore") as src, output_tsv.open("w", encoding="utf-8") as out:
        out.write("\t".join(header) + "\n")
        for line in src:
            if not line.strip():
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 11:
                continue
            gene_id = cols[0].strip()
            if id_filter is not None and gene_id not in id_filter:
                continue
            analysis = cols[3].strip() if len(cols) > 3 else ""
            signature = cols[4].strip() if len(cols) > 4 else ""
            signature_desc = cols[5].strip() if len(cols) > 5 else ""
            start = cols[6].strip() if len(cols) > 6 else ""
            end = cols[7].strip() if len(cols) > 7 else ""
            score = cols[8].strip() if len(cols) > 8 else ""
            protein_length = cols[2].strip() if len(cols) > 2 else ""
            interpro_acc = cols[11].strip() if len(cols) > 11 else ""
            interpro_desc = cols[12].strip() if len(cols) > 12 else ""
            coverage = ""
            try:
                length = float(protein_length)
                begin = float(start)
                finish = float(end)
                if length > 0 and finish >= begin:
                    coverage = f"{((finish - begin + 1) / length):.4f}"
            except Exception:
                coverage = ""
            required_hit = bool(required) and (
                signature.upper() in required or interpro_acc.upper() in required
            )
            if required_hit:
                default_keep.add(gene_id)
            proteins.add(gene_id)
            rows += 1
            out.write("\t".join([
                gene_id, species, analysis, signature, signature_desc,
                interpro_acc, interpro_desc, start, end, protein_length,
                coverage, score, "TRUE" if required_hit else "FALSE",
                "TRUE" if required_hit else "FALSE",
            ]) + "\n")

    result.update({
        "status": "success",
        "rows": rows,
        "proteins_with_hits": len(proteins),
        "default_keep_count": len(default_keep),
        "default_keep_ids": sorted(default_keep),
        "full_path": str(output_tsv.resolve()),
        "relpath_or_hint": f"final/{output_tsv.name}",
        "size": output_tsv.stat().st_size if output_tsv.exists() else 0,
    })
    return result


def run_interproscan_domain_check(pep_file: Path, output_tsv: Path) -> Dict[str, Any]:
    """Run InterProScan on final members as a non-blocking domain check artifact."""
    default_interproscan = PROJECT_ROOT / "data" / "tools" / "interproscan5.77" / "interproscan-5.77-108.0" / "interproscan.sh"
    script = Path(os.environ.get(
        "BBA_INTERPROSCAN_SH",
        str(default_interproscan),
    ))
    conda_env = os.environ.get("BBA_INTERPROSCAN_ENV", "interproscanpy311")
    # 镜像标准形态: 环境前缀默认取当前解释器目录; BBA_INTERPROSCAN_PREFIX 保留覆盖点(容器内 /opt/conda)
    env_prefix = Path(os.environ.get("BBA_INTERPROSCAN_PREFIX", "")).expanduser() or Path(sys.executable).resolve().parent.parent
    conda_sh = None
    conda_root = env_prefix.parent
    java_home = Path(os.environ.get("BBA_INTERPROSCAN_JAVA_HOME", str(env_prefix / "lib" / "jvm")))

    result = {
        "artifact": "interproscan_domain_check",
        "status": "skipped",
        "input": str(pep_file),
        "output": str(output_tsv),
        "tool": str(script),
        "conda_env": conda_env,
        "java_home": str(java_home),
    }

    if not pep_file.exists():
        result["reason"] = f"input FASTA not found: {pep_file}"
        print(f"InterProScan domain check skipped: {result['reason']}")
        return result
    if not script.exists():
        result["reason"] = f"interproscan.sh not found: {script}"
        print(f"InterProScan domain check skipped: {result['reason']}")
        return result
    java_bin = java_home / "bin" / "java"
    if not java_bin.exists():
        result["reason"] = f"Java executable not found: {java_bin}"
        print(f"InterProScan domain check skipped: {result['reason']}")
        return result

    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    if output_tsv.exists():
        output_tsv.unlink()
    clean_fasta = output_tsv.with_suffix(".input.pep.fa")
    sanitize_stats = sanitize_fasta_for_interproscan(pep_file, clean_fasta)
    scan_input = clean_fasta
    result["clean_input"] = str(scan_input)
    result["sanitize"] = sanitize_stats
    if sanitize_stats.get("written_sequences", sanitize_stats.get("sequence_count", 0)) == 0:
        result["reason"] = f"input FASTA has no usable sequences after identify: {pep_file}"
        print(f"InterProScan domain check skipped: {result['reason']}")
        return result

    # 镜像标准形态: 无 conda activate —— 当前容器环境即 target 环境(编排层按阶段选镜像)
    cmd = (
        f"export JAVA_HOME={shlex.quote(str(java_home))} && "
        f"export PATH={shlex.quote(str(java_home / 'bin'))}:$PATH && "
        "java -version && "
        f"{shlex.quote(str(script))} -appl Pfam,CDD -i {shlex.quote(str(scan_input))} -f TSV -o {shlex.quote(str(output_tsv))}"
    )
    print("Running InterProScan domain check:")
    print(f"  {cmd}")
    start = time.time()
    proc = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True)
    elapsed = round(time.time() - start, 2)
    if proc.stdout.strip():
        print(proc.stdout)
    if proc.stderr.strip():
        print(proc.stderr)

    result.update({
        "returncode": proc.returncode,
        "elapsed_sec": elapsed,
        "cmd": cmd,
    })
    if proc.returncode == 0 and output_tsv.exists():
        summary = summarize_interproscan_tsv(output_tsv)
        result.update({
            "status": "success",
            "full_path": str(output_tsv.resolve()),
            "relpath_or_hint": f"final/{output_tsv.name}",
            "size": output_tsv.stat().st_size,
            "summary": summary,
        })
        print(f"InterProScan domain check completed: {output_tsv}")
    else:
        result.update({
            "status": "failed",
            "reason": (proc.stderr or proc.stdout or "InterProScan failed without output")[-2000:],
        })
        print(f"InterProScan domain check failed, but identify will continue: {result['reason']}")
    return result

def prepare_directories(workspace: Path) -> Dict[str, Path]:
    """创建Identify阶段的标准目录结构"""
    paths = {
        "analysis_dir": workspace / "identify" / "analysis",
        "combine_dir": workspace / "identify" / "combine",
        "final_dir": workspace / "final",
        "logs_dir": workspace / "logs",
    }
    
    print("创建目录结构:")
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        print(f"  {name}: {path}")
    
    return paths


def write_manifest(manifest_path: Path, data: dict):
    """写入 manifest.json"""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Manifest: {manifest_path}")


def write_failed_identify_manifest(
    workspace: Path,
    prefix: str,
    gene_family: str,
    input_dir: Path,
    start_time: datetime,
    error: str,
    parameters: Optional[Dict[str, Any]] = None,
    hmm_models: Optional[Dict[str, Any]] = None,
) -> Path:
    """Write a minimal failed identify manifest so backend/diagnosis can gate downstream stages."""
    final_dir = workspace / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = final_dir / f"{prefix}.identify.manifest.json"
    manifest = {
        "manifest_version": "1.0",
        "job_id": workspace.name,
        "run_id": f"identify_{datetime.now():%Y%m%d_%H%M%S}",
        "stage": "identify",
        "prefix": prefix,
        "output_prefix": prefix,
        "result_group": "gene_family_identify",
        "gene_family": gene_family,
        "status": "failed",
        "timestamp": datetime.now().isoformat(),
        "start_time": start_time.isoformat(),
        "end_time": datetime.now().isoformat(),
        "elapsed_sec": (datetime.now() - start_time).total_seconds(),
        "workspace_root": str(workspace.resolve()),
        "workspace": str(final_dir.resolve()),
        "input_dir": str(input_dir.resolve()),
        "cmd": sys.argv,
        "parameters": parameters or {},
        "metrics": {},
        "hmm_models": hmm_models or {},
        "error": error,
        "warnings": [
            {"step": "reference_policy", "message": warning}
            for warning in (reference_policy.get("warnings") or [])
        ],
        "artifacts": {},
    }
    write_manifest(manifest_path, manifest)
    return manifest_path


def _is_user_hmm_name(filename: str) -> bool:
    """Return True for user query HMM files, excluding bundled/system Pfam DBs."""
    n = filename.lower()
    if 'pfam-a' in n or 'pfam_a' in n:
        return False
    return n.endswith('.hmm') or n.endswith('.hmm.gz')


def _decompress_hmm_gz(hmm_gz: Path, cache_dir: Path) -> Path:
    """Decompress a user-uploaded .hmm.gz into a stable cache and return .hmm path."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_name = hmm_gz.name[:-3]  # PFxxxxx.hmm.gz -> PFxxxxx.hmm
    out_path = cache_dir / out_name

    if out_path.exists() and out_path.stat().st_mtime >= hmm_gz.stat().st_mtime:
        print(f"  Using decompressed HMM cache: {out_path}")
        return out_path

    print(f"  Decompressing user HMM: {hmm_gz.name} -> {out_path}")
    with gzip.open(hmm_gz, 'rb') as src, open(out_path, 'wb') as dst:
        shutil.copyfileobj(src, dst)
    return out_path


def find_user_query_hmms(input_dir: Path) -> List[Path]:
    """Find user-uploaded .hmm/.hmm.gz files usable by hmmsearch."""
    print(f"Searching user HMM files in: {input_dir}")

    hmms = []
    cache_dir = input_dir / "genome_prepare" / "hmm_cache"
    for p in input_dir.iterdir():
        if not p.is_file():
            continue
        if not _is_user_hmm_name(p.name):
            continue

        n = p.name.lower()
        if n.endswith('.hmm.gz'):
            hmm_path = _decompress_hmm_gz(p, cache_dir)
            hmms.append((hmm_path, p.stat().st_mtime))
            print(f"  Found compressed user HMM: {p.name}")
        else:
            hmms.append((p, p.stat().st_mtime))
            print(f"  Found user HMM: {p.name}")

    if not hmms:
        print("  No user-uploaded HMM files found")
        return []

    hmms.sort(key=lambda item: item[1], reverse=True)
    result = [item[0] for item in hmms]
    print(f"Found {len(result)} user HMM file(s)")
    return result

def find_pep_file(input_dir: Path) -> Optional[Path]:
    """
    在 input_dir 中查找蛋白质序列文件
    支持多种命名格式：
    - *.pep.fasta, *.pep.fa
    - *.protein.fasta, *.protein.fa
    - *.faa
    - 或者包含 "pep" 或 "protein" 的fasta文件
    """
    print(f"🔍 查找蛋白质序列文件在: {input_dir}")
    
    patterns = ["*.pep.fasta", "*.pep.fa", "*.protein.fasta", "*.protein.fa", "*.faa", "*.fasta"]
    
    for pattern in patterns:
        for file in input_dir.rglob(pattern):
            if file.is_file():
                # 检查文件名是否包含pep或protein（不区分大小写）
                filename = file.name.lower()
                if "pep" in filename or "protein" in filename:
                    print(f"✅ 找到蛋白质序列文件: {file}")
                    return file
                # 对于通用的.fasta或.faa文件，也考虑
                if pattern in ["*.fasta", "*.faa"] and file.stat().st_size > 10000:  # 大于10KB
                    print(f"✅ 找到可能的蛋白质序列文件: {file}")
                    return file
    
    print("❌ 未找到蛋白质序列文件")
    return None

def _clean_config_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"true", "1", "yes", "y", "ref"}


def _clean_config_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        import pandas as pd
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "undefined", "-"} else text


def _config_file_candidates(input_dir: Path, workspace: Optional[Path] = None) -> List[Path]:
    candidates = [
        input_dir / "config.xlsx",
        input_dir / "config.autofill.draft.xlsx",
    ]
    if workspace:
        candidates.extend([
            workspace / "final" / "config.xlsx",
            workspace / "final" / "config.autofill.draft.xlsx",
            workspace / "config.xlsx",
            workspace / "config.autofill.draft.xlsx",
        ])
    seen: Set[str] = set()
    result: List[Path] = []
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.exists():
            result.append(path)
    return result


def read_analysis_family(input_dir: Path, workspace: Optional[Path] = None) -> str:
    """Read the job-level family value from config.xlsx Analysis sheet or config columns."""
    config_paths = _config_file_candidates(input_dir, workspace)
    if not config_paths:
        return ""
    try:
        import pandas as pd
        for config_path in config_paths:
            xls = pd.ExcelFile(config_path)
            for sheet in xls.sheet_names:
                if sheet.strip().lower() not in {"analysis", "project", "metadata"}:
                    continue
                df = pd.read_excel(config_path, sheet_name=sheet, header=None)
                for _, row in df.iterrows():
                    key = _clean_config_text(row.iloc[0] if len(row) > 0 else "").lower()
                    value = _clean_config_text(row.iloc[1] if len(row) > 1 else "")
                    if key in {"family", "gene_family", "analysis_family", "project_family"} and value:
                        print(f"🧬 从 {config_path.name}/{sheet} 读取家族: {value}")
                        return value
            df = pd.read_excel(config_path)
            for col in ["Family", "Analysis_Family", "Project_Family", "Gene_Family"]:
                if col not in df.columns:
                    continue
                values = [_clean_config_text(v) for v in df[col].tolist()]
                values = [v for v in values if v]
                unique = sorted(set(values))
                if len(unique) == 1:
                    print(f"🧬 从 {config_path.name} 列 {col} 读取家族: {unique[0]}")
                    return unique[0]
    except Exception as exc:
        print(f"⚠️  读取 config.xlsx 家族字段失败: {exc}")
    return ""


def read_required_pfams_from_config(input_dir: Path, workspace: Optional[Path] = None) -> str:
    """Read Required_Pfams from config.xlsx Analysis sheet or config columns."""
    config_paths = _config_file_candidates(input_dir, workspace)
    if not config_paths:
        return ""
    try:
        import pandas as pd
        for config_path in config_paths:
            xls = pd.ExcelFile(config_path)
            for sheet in xls.sheet_names:
                if sheet.strip().lower() not in {"analysis", "project", "metadata"}:
                    continue
                df = pd.read_excel(config_path, sheet_name=sheet, header=None)
                for _, row in df.iterrows():
                    key = _clean_config_text(row.iloc[0] if len(row) > 0 else "").lower()
                    value = _clean_config_text(row.iloc[1] if len(row) > 1 else "")
                    if key in {"required_pfams", "required_pfam", "pfam", "pfams", "pfam_ids"} and value:
                        print(f"🧬 从 {config_path.name}/{sheet} 读取 Required_Pfams: {value}")
                        return value
            df = pd.read_excel(config_path)
            for col in ["Required_Pfams", "Required_Pfam", "Pfam", "Pfams", "Pfam_IDs", "PFAM"]:
                if col not in df.columns:
                    continue
                values = [_clean_config_text(v) for v in df[col].tolist()]
                values = [v for v in values if v]
                if values:
                    merged = ",".join(dict.fromkeys(
                        item.strip()
                        for value in values
                        for item in re.split(r"[,;，；\s]+", value)
                        if item.strip()
                    ))
                    if merged:
                        print(f"🧬 从 {config_path.name} 列 {col} 读取 Required_Pfams: {merged}")
                        return merged
    except Exception as exc:
        print(f"⚠️  读取 config.xlsx Required_Pfams 失败: {exc}")
    return ""


def _input_search_dirs(input_dir: Path, workspace: Optional[Path] = None) -> List[Path]:
    dirs = [input_dir]
    if workspace:
        dirs.extend([workspace / "final", workspace])
    dirs.append(input_dir / "final")
    seen: Set[str] = set()
    result: List[Path] = []
    for d in dirs:
        key = str(d.resolve()) if d.exists() else str(d)
        if key not in seen:
            result.append(d)
            seen.add(key)
    return result


def _resolve_config_file(
    input_dir: Path,
    species: str,
    family: str,
    file_type: str,
    original_name: str = "",
    search_dirs: Optional[List[Path]] = None,
) -> Optional[Path]:
    """Resolve standardized or original files declared in config.xlsx."""
    if file_type == "all_pep":
        patterns = [
            f"{species}.{family}.all_pep.fasta", f"{species}.{family}.all_pep.fa",
            f"{species}.{family}.pep.fasta", f"{species}.{family}.pep.fa",
            f"{species}.{family}.protein.fasta", f"{species}.{family}.faa",
            f"{species}.pep.fasta", f"{species}.pep.fa",
            f"{species}.protein.fasta", f"{species}.protein.fa", f"{species}.faa",
        ]
    elif file_type == "family_pep":
        patterns = [
            f"{species}.{family}.family_pep.fasta", f"{species}.{family}.family_pep.fa",
            f"{species}.{family}.pep.fasta", f"{species}.{family}.pep.fa",
        ]
    elif file_type == "pep":
        patterns = [f"{species}.{family}.pep.fasta", f"{species}.{family}.pep.fa"]
    else:
        patterns = []
    if original_name:
        patterns.append(Path(original_name).name)
    for pattern in patterns:
        for root in (search_dirs or [input_dir]):
            for candidate in (root / pattern, root / species / pattern):
                if candidate.exists():
                    return candidate
    return None


def is_likely_protein_fasta(path: Path, max_first_seq_len: int = 100000, sample_records: int = 3) -> Tuple[bool, str]:
    """Lightweight guard against genome/promoter FASTA files mislabeled as pep."""
    if not path.exists() or path.stat().st_size == 0:
        return False, "file missing or empty"
    records = 0
    current_id = ""
    current_len = 0
    saw_header = False
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw in handle:
                if raw.startswith(">"):
                    if saw_header:
                        records += 1
                        if current_len > max_first_seq_len:
                            return False, f"{current_id} length {current_len} > {max_first_seq_len}"
                        if records >= sample_records:
                            return True, f"sampled {records} protein-like records"
                    saw_header = True
                    current_id = raw[1:].strip().split()[0]
                    current_len = 0
                    if re.match(r"^(chr|chromosome|scaffold|contig)_?\d*", current_id, re.I):
                        # Chromosome IDs are not decisive alone, but combined with length threshold below
                        # they catch common genome FASTA mislabels quickly.
                        pass
                    continue
                if not saw_header:
                    continue
                current_len += len(raw.strip())
                if current_len > max_first_seq_len:
                    return False, f"{current_id or 'first_record'} length {current_len} > {max_first_seq_len}"
        if saw_header:
            records += 1
            if current_len > max_first_seq_len:
                return False, f"{current_id} length {current_len} > {max_first_seq_len}"
        if records == 0:
            return False, "no FASTA records"
        return True, f"sampled {records} protein-like records"
    except Exception as exc:
        return False, f"failed to read FASTA: {exc}"


def find_pep_files(input_dir: Path, workspace: Optional[Path] = None, target_species: Optional[Set[str]] = None) -> List[Path]:
    """Find target whole-proteome FASTA files for identify.

    File_Type=all_pep is searched; File_Type=family_pep is reference-only.
    Legacy target File_Type=pep is kept as a fallback.
    """
    print(f"?? ?????????????: {input_dir}")
    target_peps: List[Path] = []
    seen: Set[str] = set()
    search_dirs = _input_search_dirs(input_dir, workspace)

    config_path = input_dir / "config.xlsx"
    if config_path.exists():
        try:
            import pandas as pd
            df = pd.read_excel(config_path)
            roles_by_species: Dict[str, Set[str]] = {}
            for _, row in df.iterrows():
                species = _clean_config_text(row.get("Species_Code", ""))
                role = _clean_config_text(row.get("Species_Role", "")).lower()
                if species and role:
                    roles_by_species.setdefault(species, set()).add(role)
            for _, row in df.iterrows():
                raw_file_type = _clean_config_text(row.get("File_Type", "")).lower()
                is_ref = _clean_config_bool(row.get("Ref", False))
                role = _clean_config_text(row.get("Species_Role", "")).lower()
                scope = _clean_config_text(row.get("File_Scope", "")).lower()
                species = _clean_config_text(row.get("Species_Code", ""))
                family = _clean_config_text(row.get("Gene_Family", ""))
                original = _clean_config_text(row.get("Original_File_Name", ""))
                forced_target = bool(target_species and species in target_species)
                file_type = "all_pep" if raw_file_type in {"protein", "all_pep"} else raw_file_type
                if raw_file_type == "pep" and not (scope in {"known_family", "family", "seed"} or (is_ref and _clean_config_text(row.get("Gene_Family", "")) and role in {"", "seed", "seed_reference"})):
                    file_type = "all_pep"
                seed_role = role in {"seed", "seed_reference", "known_family_reference"}
                legacy_seed_ref = is_ref and role == "" and _clean_config_text(row.get("Gene_Family", "")) and raw_file_type in {"pep", "family_pep"}
                if file_type not in {"all_pep", "pep", "protein"}:
                    continue
                if not species:
                    continue
                if target_species and species not in target_species:
                    continue
                if not forced_target and (is_ref or seed_role or legacy_seed_ref):
                    continue
                species_roles = roles_by_species.get(species, set())
                if not forced_target and "ref" in species_roles and "target" not in species_roles:
                    print(f"  - 跳过参考物种全蛋白: {species}")
                    continue
                if not forced_target and role and role not in {"target", "comparative", "compactive"}:
                    continue
                resolved = _resolve_config_file(input_dir, species, family, "all_pep", original, search_dirs)
                if resolved and str(resolved.resolve()) not in seen:
                    protein_ok, protein_reason = is_likely_protein_fasta(resolved)
                    if not protein_ok:
                        print(f"  - 跳过疑似非蛋白FASTA: {species} -> {resolved.name} ({protein_reason})")
                        continue
                    target_peps.append(resolved)
                    seen.add(str(resolved.resolve()))
                    print(f"  ✓ 目标/比较物种蛋白: {species} ({role or 'unspecified'}) -> {resolved.name}")
        except Exception as exc:
            print(f"  ?? ?? config.xlsx ??????????: {exc}")

    if not target_peps:
        for pattern in ("*.all_pep.fasta", "*.all_pep.fa", "*.pep.fasta", "*.pep.fa"):
            for f in input_dir.glob(pattern):
                lname = f.name.lower()
                if f.name.startswith("Ref_combined") or ".family_pep." in lname:
                    continue
                if target_species and f.name.split(".", 1)[0] not in target_species:
                    continue
                protein_ok, protein_reason = is_likely_protein_fasta(f)
                if not protein_ok:
                    print(f"  - 跳过疑似非蛋白FASTA(legacy): {f.name} ({protein_reason})")
                    continue
                target_peps.append(f)
                print(f"  ? ????????(??): {f.name}")

    if not target_peps:
        print("  ? ??????? all_pep/pep ??")
    else:
        print(f"  ?? ??? {len(target_peps)} ?????????")
    return sorted(target_peps)




def read_config_sheet_dataframe(config_path: Path):
    import pandas as pd
    xls = pd.ExcelFile(config_path)
    sheet_name = "config" if "config" in xls.sheet_names else xls.sheet_names[0]
    return pd.read_excel(config_path, sheet_name=sheet_name)

def read_reference_policy_from_config(input_dir: Path) -> Dict[str, Any]:
    policy = {
        "primary_reference_species": [],
        "identify_blast_reference_species": [],
        "warnings": [],
    }
    config_path = input_dir / "config.xlsx"
    if not config_path.exists():
        return policy
    try:
        import pandas as pd
        try:
            analysis_df = pd.read_excel(config_path, sheet_name="Analysis", header=None)
            analysis: Dict[str, str] = {}
            for _, row in analysis_df.iterrows():
                key = _clean_config_text(row.iloc[0] if len(row) > 0 else "")
                value = _clean_config_text(row.iloc[1] if len(row) > 1 else "")
                if key:
                    analysis[key] = value
            primary = analysis.get("Primary_Reference_Species") or analysis.get("Reference_Species") or ""
            identify_refs = analysis.get("Identify_Blast_Reference_Species") or ""
            policy["primary_reference_species"] = [x.strip() for x in re.split(r"[,;，、\s]+", primary) if x.strip()]
            policy["identify_blast_reference_species"] = [x.strip() for x in re.split(r"[,;，、\s]+", identify_refs) if x.strip()]
            if analysis.get("Reference_Species") and not analysis.get("Primary_Reference_Species"):
                policy["warnings"].append("Using legacy Analysis.Reference_Species as primary downstream reference; prefer Primary_Reference_Species.")
        except Exception:
            pass
        df = read_config_sheet_dataframe(config_path)
        if "Reference_Use" in df.columns:
            row_identify_refs: List[str] = []
            row_primary_refs: List[str] = []
            for _, row in df.iterrows():
                species = _clean_config_text(row.get("Species_Code", ""))
                use = _clean_config_text(row.get("Reference_Use", "")).lower().replace("-", "_").replace(" ", "_")
                if not species:
                    continue
                if use in {"identify", "blast", "identify_blast", "identify_reference", "both", "all"}:
                    row_identify_refs.append(species)
                if use in {"primary", "downstream", "downstream_primary", "primary_reference", "both", "all"}:
                    row_primary_refs.append(species)
            if not policy["identify_blast_reference_species"] and row_identify_refs:
                policy["identify_blast_reference_species"] = list(dict.fromkeys(row_identify_refs))
            if not policy["primary_reference_species"] and row_primary_refs:
                policy["primary_reference_species"] = list(dict.fromkeys(row_primary_refs))
    except Exception as exc:
        policy["warnings"].append(f"Failed to read reference policy from config.xlsx: {exc}")
    return policy

def find_reference_file_simple(input_dir: Path, gene_family: str = "", workspace: Optional[Path] = None) -> Optional[Path]:
    """
    简化版：查找参考物种合并文件
    命名规范: Ref_combined.{gene_family}.pep.fasta
    
    Args:
        input_dir: 输入目录
    
    Returns:
        参考物种 pep 文件路径，未找到返回 None
    """
    print(f"🔍 查找参考物种文件在: {input_dir}")
    
    search_dirs = _input_search_dirs(input_dir, workspace)
    family = (gene_family or "").strip()
    family_patterns = []
    if family:
        family_patterns.extend([
            f"*.{family}.ncbi_fetch.protein.fa",
            f"*.{family}.ncbi_fetch.protein.fasta",
            f"*.{family}.family_pep.fa",
            f"*.{family}.family_pep.fasta",
            f"*.{family}.pep.fa",
            f"*.{family}.pep.fasta",
            f"Ref_combined.{family}.pep.fasta",
        ])
    family_patterns.append("Ref_combined.*.pep.fasta")

    for root in search_dirs:
        for pattern in family_patterns:
            ref_files = sorted(root.glob(pattern), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            if ref_files:
                print(f"  ✅ 参考家族蛋白: {ref_files[0].name} ({root})")
                return ref_files[0]

    ref_files = list(input_dir.glob("Ref_combined.*.pep.fasta"))
    if ref_files:
        print(f"  ✅ 参考物种: {ref_files[0].name}")
        return ref_files[0]
    
    print("  ❌ 未找到参考物种文件 Ref_combined.*.pep.fasta")
    return None


def infer_reference_label(ref_file: Path, gene_family: str = "") -> str:
    """Infer a stable reference set label from a family protein FASTA name."""
    stem = Path(ref_file).name
    for suffix in (".fasta", ".fa", ".faa"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    for suffix in (".pep", ".protein", ".family_pep", ".ncbi_fetch.protein"):
        stem = re.sub(re.escape(suffix) + r"$", "", stem, flags=re.IGNORECASE)
    if gene_family:
        stem = re.sub(rf"([._-]){re.escape(gene_family)}$", "", stem, flags=re.IGNORECASE)
        stem = re.sub(rf"{re.escape(gene_family)}$", "", stem, flags=re.IGNORECASE)
    stem = stem.strip("._-") or Path(ref_file).stem
    return sanitize_token(stem)


def collect_reference_files(
    input_dir: Path,
    gene_family: str = "",
    workspace: Optional[Path] = None,
    explicit_ref_file: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Collect reference family protein FASTA files declared for identify BLAST.

    Web pipeline semantics: reference-protein BLAST is enabled only by config.xlsx
    rows that explicitly declare known-family/seed reference proteins. File-name
    discovery alone must not turn HMM-only identify into HMM+BLAST.
    """
    references: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    def add_ref(path: Path, species: str = "", source: str = "auto") -> None:
        if not path:
            return
        path = Path(path).resolve()
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"参考家族蛋白文件不存在: {path}")
        key = str(path)
        if key in seen:
            return
        label = sanitize_token(species) if species else infer_reference_label(path, gene_family)
        references.append({
            "label": label,
            "species": species or label,
            "path": path,
            "source": source,
            "records": count_fasta_records(path),
            "sha256": compute_file_hash(path),
            "size": path.stat().st_size,
        })
        seen.add(key)

    if explicit_ref_file:
        for raw in re.split(r"[,;]", explicit_ref_file):
            text_value = raw.strip()
            if text_value:
                add_ref(Path(text_value), source="--ref-file")
        return references

    search_dirs = _input_search_dirs(input_dir, workspace)
    policy = read_reference_policy_from_config(input_dir)
    allowed_identify_refs = set(policy.get("identify_blast_reference_species") or [])
    config_path = input_dir / "config.xlsx"
    if config_path.exists():
        try:
            df = read_config_sheet_dataframe(config_path)
            for _, row in df.iterrows():
                species = _clean_config_text(row.get("Species_Code", ""))
                family = _clean_config_text(row.get("Gene_Family", ""))
                role = _clean_config_text(row.get("Species_Role", "")).lower()
                scope = _clean_config_text(row.get("File_Scope", "")).lower()
                file_type = _clean_config_text(row.get("File_Type", "")).lower()
                is_ref = _clean_config_bool(row.get("Ref", False))
                original = _clean_config_text(row.get("Original_File_Name", ""))
                reference_use = _clean_config_text(row.get("Reference_Use", "")).lower().replace("-", "_").replace(" ", "_")
                family_match = not gene_family or not family or family.lower() == gene_family.lower()
                use_allows_identify = reference_use in {"identify", "blast", "identify_blast", "identify_reference", "both", "all"}
                use_blocks_identify = reference_use in {"downstream", "downstream_primary", "primary", "primary_reference", "none", "no", "false"}
                species_allowed = (not allowed_identify_refs) or (species in allowed_identify_refs)
                known_family_row = (
                    scope in {"known_family", "family", "seed"}
                    or role in {"seed", "seed_reference", "known_family_reference"}
                    or use_allows_identify
                )
                is_family_ref = species_allowed and known_family_row and (
                    use_allows_identify
                    or not use_blocks_identify
                )
                is_pep = file_type in {"pep", "protein", "family_pep", "all_pep"}
                if not family_match or not is_family_ref or not is_pep:
                    continue
                resolved = _resolve_config_file(input_dir, species, family or gene_family, "family_pep", original, search_dirs)
                if not resolved:
                    resolved = _resolve_config_file(input_dir, species, family or gene_family, "pep", original, search_dirs)
                if resolved:
                    add_ref(resolved, species=species, source="config.xlsx")
        except Exception as exc:
            print(f"⚠️  读取 config.xlsx 参考家族蛋白失败，将按无参考蛋白处理: {exc}")

    if references:
        print(f"📚 参考家族蛋白集合: {len(references)}")
        for ref in references:
            print(f"   - {ref['label']}: {ref['path'].name} ({ref['records']} seqs)")
        return references

    return references


def extract_gene_family(input_dir: Path, prefix: str = "", workspace: Optional[Path] = None) -> str:
    """
    从文件名提取基因家族名称
    优先从 Ref_combined 文件提取，其次从目标文件
    
    命名规范: {species}.{gene_family}.pep.fasta
    例如: Ath.SPL.pep.fasta -> "SPL"
    
    Args:
        input_dir: 输入目录
    
    Returns:
        基因家族名称（大写）
    """
    if prefix:
        parts = [_clean_config_text(p) for p in prefix.split(".") if _clean_config_text(p)]
        invalid_tokens = {"family", "unknown", "none", "multi", "target", "ref", "reference", "comparative"}
        if len(parts) >= 2 and parts[-1].lower() not in invalid_tokens:
            print(f"🧬 从 prefix 提取基因家族: {parts[-1]}")
            return parts[-1]
        if len(parts) == 1 and not re.match(r"^[A-Z][a-z]?$", parts[0]) and parts[0].lower() not in invalid_tokens:
            print(f"🧬 从 prefix 提取基因家族: {parts[0]}")
            return parts[0]

    config_family = read_analysis_family(input_dir, workspace)
    if config_family:
        return config_family

    search_dirs = _input_search_dirs(input_dir, workspace)
    for root in search_dirs:
        for ref_file in sorted(root.glob("*.ncbi_fetch.protein.fa"), key=lambda p: p.stat().st_mtime, reverse=True):
            parts = ref_file.name.split(".")
            if len(parts) >= 2:
                print(f"🧬 从 NCBI 参考蛋白提取基因家族: {parts[1]}")
                return parts[1]

    # 优先从参考文件提取
    ref_files = list(input_dir.glob("Ref_combined.*.pep.fasta"))
    if ref_files:
        # Ref_combined.SPL.pep.fasta -> SPL
        parts = ref_files[0].stem.split('.')
        if len(parts) >= 2:
            # Karpathy: 保持原始大小写，不强制转大写
            gene_family = parts[1]
            print(f"🧬 从参考文件提取基因家族: {gene_family}")
            return gene_family
    
    # 从第一个目标文件提取
    target_files = [f for f in input_dir.glob("*.pep.fasta") 
                    if not f.name.startswith("Ref_combined")]
    if target_files:
        parts = target_files[0].stem.split('.')
        if len(parts) >= 2:
            # Karpathy: 保持原始大小写，不强制转大写
            gene_family = parts[1]
            print(f"🧬 从目标文件提取基因家族: {gene_family}")
            return gene_family
    
    raise RuntimeError("无法从文件名提取基因家族名称")


def count_fasta_records(fasta_file: Path) -> int:
    count = 0
    if not fasta_file.exists():
        return count
    with open(fasta_file, 'r', encoding='utf-8', errors='ignore') as handle:
        for line in handle:
            if line.startswith('>'):
                count += 1
    return count


def write_fasta_ids(fasta_file: Path, output_file: Path) -> int:
    """Write one sequence ID per FASTA record using the first header token."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(fasta_file, 'r') as infile, open(output_file, 'w') as outfile:
        for line in infile:
            if line.startswith('>'):
                seq_id = line[1:].strip().split()[0]
                if seq_id:
                    outfile.write(seq_id + '\n')
                    count += 1
    print(f"ID list written: {output_file} ({count} IDs)")
    return count


def merge_final_members(
    final_member_files: List[Path], 
    output_file: Path
) -> None:
    """
    合并所有目标物种的 final_gfmembers 文件
    
    Args:
        final_member_files: 各物种 final_gfmembers 文件列表
        output_file: 合并后的输出文件
    """
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_file, 'w') as outfile:
        for member_file in final_member_files:
            if member_file.exists():
                with open(member_file, 'r') as infile:
                    content = infile.read()
                    outfile.write(content)
                    # 确保文件末尾有换行
                    if not content.endswith('\n'):
                        outfile.write('\n')
    
    print(f"✅ 合并完成: {output_file} ({len(final_member_files)} 个文件)")




# def find_reference_file(input_dir: Path) -> Optional[Path]:
#     """
#     在 input_dir 中查找参考序列文件
#     用于blast分析
#     """
#     print(f"🔍 查找参考序列文件在: {input_dir}")
    
#     patterns = ["*.fasta", "*.fa", "*.faa"]
    
#     for pattern in patterns:
#         for file in input_dir.rglob(pattern):
#             if file.is_file():
#                 # 排除蛋白质序列文件
#                 filename = file.name.lower()
#                 if not ("pep" in filename or "protein" in filename):
#                     print(f"✅ 找到参考序列文件: {file}")
#                     return file
    
#     print("⚠️  未找到参考序列文件")
#     return None
def find_reference_file(input_dir: Path, prefix: str, gene_family: str = None) -> Union[Path, List[Path], None]:
    """
    在 input_dir 中查找参考序列文件（排除用户数据文件）
    支持命名规范: At.pep.fasta (全蛋白), At_NAC.pep.fasta (基因家族蛋白)
    
    修订 2026-04-26: 新增对 Ref_combined.*.pep.fasta 的支持
    优先使用 genome_prepare 生成的参考物种合并文件
    
    对于identify阶段:
    - 优先寻找基因家族特定的参考序列 (如 At_NAC.pep.fasta)
    - 找到多个时返回列表供用户选择
    - 未找到时返回None并提示用户上传
    
    Args:
        input_dir: 输入目录
        prefix: 分析前缀，例如 "At.WRKY" 或 "Ft"
        gene_family: 基因家族名称，如 "NAC"
    """
    print(f"🔍 查找参考序列文件（排除用户文件）在: {input_dir}")
    
    # 修订 2026-04-26: 优先检查 Ref_combined.*.pep.fasta
    ref_combined_files = list(input_dir.glob("Ref_combined.*.pep.fasta"))
    if ref_combined_files:
        print(f"✅ 找到参考物种合并文件: {ref_combined_files[0].name}")
        return ref_combined_files[0]
    
    if gene_family:
        print(f"🧬 目标基因家族: {gene_family}")
    
    # 从prefix中提取物种前缀和基因家族
    species_prefix = prefix.split('.')[0] if '.' in prefix else prefix
    if not gene_family and '.' in prefix:
        gene_family = prefix.split('.')[1] if len(prefix.split('.')) > 1 else None
    
    patterns = ["*.pep.fasta", "*.pep.fa", "*.protein.fasta", "*.protein.fa","*.fasta", "*.fa", "*.faa"]
    
    # 分类存储
    # 使用 set 去重，避免同一文件被多个 pattern 重复匹配
    family_specific_refs = set()  # 基因家族特定的参考序列 (如 At_NAC.pep.fasta)
    full_protein_refs = set()     # 全蛋白参考序列 (如 At.pep.fasta)
    other_refs = set()            # 其他参考序列
    
    # 参考物种关键词映射
    ref_species_keywords = ["ath", "at.", "osa", "os.", "gma", "gm.", "bra", "br.", "aly", "al.", "tig", "tg.", "sly", "sl."]
    
    for pattern in patterns:
        for file in input_dir.rglob(pattern):
            if file.is_file():
                filename = file.name.lower()
                original_filename = file.name
                
                # 排除用户自己的蛋白质序列文件 (如 Zm.pep.fasta, Zm_NAC.pep.fasta)
                if filename.startswith(species_prefix.lower() + ".") or filename.startswith(species_prefix.lower() + "_"):
                    print(f"  排除用户文件: {original_filename}")
                    continue
                
                # 排除CDS文件
                if filename.endswith(".cds.fasta") or ".cds." in filename:
                    print(f"  排除CDS文件: {original_filename}")
                    continue
                
                # 判断是否为基因家族特定的参考序列 (如 At_NAC.pep.fasta)
                # 格式: 物种_基因家族.pep.fasta 或 物种基因家族.pep.fasta
                is_family_specific = False
                if gene_family:
                    # 检查文件名是否包含基因家族名
                    gene_family_lower = gene_family.lower()
                    # 匹配模式: At_NAC, AtNAC, ATH_NAC 等
                    if f"_{gene_family_lower}." in filename or f"_{gene_family_lower}_" in filename:
                        is_family_specific = True
                    elif gene_family_lower in filename.replace("_", "").replace(".", ""):
                        # 更宽松的匹配: AtNAC, athnac 等
                        is_family_specific = True
                
                # 判断是否为全蛋白参考序列 (如 At.pep.fasta)
                is_full_protein = False
                if any(keyword in filename for keyword in ref_species_keywords):
                    # 检查是否为纯物种名+扩展名 (如 At.pep.fasta, not At_NAC.pep.fasta)
                    base_name = filename.replace(".pep.fasta", "").replace(".pep.fa", "").replace(".protein.fasta", "").replace(".protein.fa", "").replace(".fa", "").replace(".fasta", "")
                    if "_" not in base_name and gene_family and gene_family.lower() not in base_name:
                        is_full_protein = True
                
                if is_family_specific:
                    print(f"  ✅ 找到基因家族特定参考: {original_filename}")
                    family_specific_refs.add(file)
                elif is_full_protein:
                    print(f"  📄 找到全蛋白参考: {original_filename}")
                    full_protein_refs.add(file)
                else:
                    other_refs.add(file)
    
    # 对于identify阶段: 优先使用基因家族特定的参考序列
    # 将 set 转换为 list 以便索引访问
    family_list = sorted(list(family_specific_refs), key=lambda x: x.name)
    
    if family_list:
        print(f"\n🎯 找到 {len(family_list)} 个基因家族特定参考序列:")
        for i, ref in enumerate(family_list, 1):
            print(f"   {i}. {ref.name}")
        
        # 如果只有一个，直接返回
        if len(family_list) == 1:
            print(f"✅ 使用唯一的基因家族特定参考: {family_list[0].name}")
            return family_list[0]
        else:
            # 多个时返回列表（调用方需要处理选择）
            print(f"⚠️  找到多个基因家族特定参考序列，需要用户选择")
            return family_list  # 返回列表
    
    # 没有找到基因家族特定的参考序列
    full_list = sorted(list(full_protein_refs), key=lambda x: x.name)
    if full_list:
        print(f"\n⚠️  警告: 未找到基因家族特定参考序列 (如 At_{gene_family}.pep.fasta)")
        print(f"   但找到了全蛋白参考序列:")
        for ref in full_list:
            print(f"     - {ref.name}")
        print(f"\n❌ 对于identify阶段，需要使用基因家族特定的参考序列")
        return None  # 返回None，提示用户上传
    
    print("⚠️  未找到任何参考序列文件")
    return None

def _normalize_pfam_query(value: str) -> str:
    """Normalize a Pfam accession/name query for matching Pfam-A.hmm records."""
    value = (value or "").strip()
    pfam_word_match = re.match(r"^PFAM(\d+)(?:\.\d+)?$", value, flags=re.IGNORECASE)
    if pfam_word_match:
        return f"PF{pfam_word_match.group(1)}"
    if re.match(r"^PF\d+\.\d+$", value, flags=re.IGNORECASE):
        return value.split(".", 1)[0].upper()
    if re.match(r"^PF\d+$", value, flags=re.IGNORECASE):
        return value.upper()
    mapped = _pfam_alias_to_accession(value)
    if mapped:
        return mapped
    return value


_PFAM_ALIAS_CACHE: Optional[Dict[str, str]] = None


def _pfam_alias_to_accession(value: str) -> Optional[str]:
    """Resolve common family/domain aliases such as SBP to PFxxxxx accessions."""
    global _PFAM_ALIAS_CACHE
    query = (value or "").strip()
    if not query:
        return None
    if _PFAM_ALIAS_CACHE is None:
        mapping: Dict[str, str] = {
            "SPL": "PF03110",
            "SBP": "PF03110",
            "WRKY": "PF03106",
            "NAC": "PF02365",
        }
        mapping_path = Path(os.environ.get("BBA_RAG_MAPPING_FILE", "")).expanduser() if os.environ.get("BBA_RAG_MAPPING_FILE") else PROJECT_ROOT / "data" / "rag_db" / "pfam_mapping.json"
        if mapping_path.exists():
            try:
                raw_mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
                for key, val in raw_mapping.items():
                    text_val = str(val).strip()
                    match = re.search(r"\bPF\d{5}(?:\.\d+)?\b", text_val, flags=re.I)
                    if match:
                        mapping[str(key).strip().upper()] = _normalize_pfam_accession(match.group(0))
            except Exception as exc:
                print(f"⚠️  读取Pfam别名映射失败，使用内置映射: {exc}")
        _PFAM_ALIAS_CACHE = mapping
    return _PFAM_ALIAS_CACHE.get(query.upper())


def _normalize_pfam_accession(value: str) -> str:
    value = (value or "").strip()
    pfam_word_match = re.match(r"^PFAM(\d+)(?:\.\d+)?$", value, flags=re.IGNORECASE)
    if pfam_word_match:
        return f"PF{pfam_word_match.group(1)}"
    if re.match(r"^PF\d+\.\d+$", value, flags=re.IGNORECASE):
        return value.split(".", 1)[0].upper()
    return value.upper()



def _is_pfam_accession(value: str) -> bool:
    return bool(re.match(r"^PF\d{5}$", (value or "").strip(), flags=re.IGNORECASE))


def parse_hmm_models(hmm_file: Path) -> List[Dict[str, Any]]:
    """Parse HMMER3 models and return NAME/ACC metadata without loading scores into memory."""
    models: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    if not hmm_file or not hmm_file.exists():
        return models
    with open(hmm_file, "r", encoding="latin-1", errors="ignore") as handle:
        for raw in handle:
            if raw.startswith("HMMER3/"):
                if current:
                    models.append(current)
                current = {"name": "", "accession": "", "accession_version": ""}
                continue
            if current is None:
                continue
            if raw.startswith("NAME "):
                parts = raw.split()
                if len(parts) > 1:
                    current["name"] = parts[1]
            elif raw.startswith("ACC "):
                parts = raw.split()
                if len(parts) > 1:
                    current["accession_version"] = parts[1]
                    current["accession"] = _normalize_pfam_accession(parts[1])
            elif raw.startswith("//"):
                models.append(current)
                current = None
    if current:
        models.append(current)
    return models


def hmm_model_audit(hmm_file: Path, requested_pfams: List[str], fetch_map: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Summarize requested vs loaded Pfam models for manifest/status gating."""
    requested = [_normalize_pfam_query(q) for q in requested_pfams or [] if str(q).strip()]
    requested_unique = list(dict.fromkeys(requested))
    models = parse_hmm_models(hmm_file)
    loaded_accessions = sorted({m.get("accession") for m in models if m.get("accession")})
    query_name_to_accession = {
        str(m.get("name")): str(m.get("accession"))
        for m in models
        if m.get("name") and m.get("accession")
    }
    requested_accessions = [q for q in requested_unique if _is_pfam_accession(q)]
    missing = sorted(set(requested_accessions) - set(loaded_accessions))
    return {
        "status": "failed" if missing else "success",
        "query_hmm": str(hmm_file.resolve()) if hmm_file and hmm_file.exists() else str(hmm_file or ""),
        "requested_pfams": requested_unique,
        "loaded_accessions": loaded_accessions,
        "missing_requested_pfams": missing,
        "query_name_to_accession": query_name_to_accession,
        "models": models,
        "fetch_map": fetch_map or {},
        "model_count": len(models),
    }


def assert_hmm_models_complete(hmm_file: Path, requested_pfams: List[str], fetch_map: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    audit = hmm_model_audit(hmm_file, requested_pfams, fetch_map)
    missing = audit.get("missing_requested_pfams") or []
    if missing:
        raise RuntimeError(
            "Requested Pfam model(s) missing from query HMM: "
            + ", ".join(missing)
            + f"; query_hmm={hmm_file}"
        )
    return audit


def _split_pfam_queries(pfams: str) -> List[str]:
    queries: List[str] = []
    seen: Set[str] = set()
    for item in str(pfams).split(","):
        query = _normalize_pfam_query(item)
        if query and query not in seen:
            seen.add(query)
            queries.append(query)
    return queries


def _resolve_pfam_db(pfam_dir_arg: Optional[str] = None) -> Optional[Path]:
    """Find Pfam-A.hmm using CLI/env hints and conservative local defaults."""
    candidates: List[Path] = []
    for raw in [pfam_dir_arg, os.environ.get("BBA_PFAM_DIR"), os.environ.get("PFAM_DIR")]:
        if not raw:
            continue
        p = Path(raw).expanduser()
        candidates.extend([p / "Pfam-A.hmm", p / "Pfam-A.hmm.gz", p])

    candidates.extend([
        PROJECT_ROOT / "data" / "hmm" / "Pfam-A.hmm",
        PROJECT_ROOT / "data" / "hmm" / "Pfam-A.hmm.gz",
        Path("data/hmm/Pfam-A.hmm"),
        Path("data/hmm/Pfam-A.hmm.gz"),
        Path("data/pfam/Pfam-A.hmm"),
        Path("data/pfam/Pfam-A.hmm.gz"),
        Path("config/Pfam-A.hmm"),
        Path("config/Pfam-A.hmm.gz"),
        PROJECT_ROOT / "assets" / "hmm" / "Pfam-A.hmm",
        PROJECT_ROOT / "assets" / "hmm" / "Pfam-A.hmm.gz",
    ])

    for candidate in candidates:
        candidate = candidate.resolve() if not candidate.is_absolute() else candidate
        if candidate.is_file():
            return candidate
    return None


def _decompress_pfam_db_if_needed(pfam_db: Path, cache_dir: Path) -> Path:
    if pfam_db.suffix != ".gz":
        return pfam_db
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / pfam_db.stem
    if out_path.exists() and out_path.stat().st_mtime >= pfam_db.stat().st_mtime:
        return out_path
    print(f"Decompressing Pfam DB: {pfam_db} -> {out_path}")
    with gzip.open(pfam_db, "rb") as src, open(out_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return out_path


def _is_valid_hmm_file(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        with open(path, "r", encoding="latin-1") as handle:
            return handle.readline().startswith("HMMER3/")
    except Exception:
        return False


def _pfam_hmm_matches_query(path: Path, query: str) -> bool:
    query_norm = _normalize_pfam_query(query)
    query_upper = query_norm.upper()
    if not _is_valid_hmm_file(path):
        return False
    try:
        for model in parse_hmm_models(path):
            model_acc = str(model.get("accession") or "").upper()
            model_name = str(model.get("name") or "").upper()
            if _is_pfam_accession(query_norm):
                if model_acc == query_upper:
                    return True
            elif model_name == query_upper:
                return True
    except Exception:
        return False
    return False


def _local_hmm_search_roots(cache_dir: Path) -> List[Path]:
    roots = [
        cache_dir,
        PROJECT_ROOT / "assets" / "hmm",
        PROJECT_ROOT / "assets" / "examples" / "test_input",
        PROJECT_ROOT / "data" / "pfam",
        PROJECT_ROOT / "data" / "hmm",
        PROJECT_ROOT / "input",
        PROJECT_ROOT / "workspace",
    ]
    seen: Set[str] = set()
    existing: List[Path] = []
    for root in roots:
        try:
            resolved = root.resolve()
        except Exception:
            continue
        key = str(resolved)
        if key in seen or not resolved.exists():
            continue
        seen.add(key)
        existing.append(resolved)
    return existing


def _find_cached_pfam_hmm(query: str, cache_dir: Path) -> Optional[Path]:
    """Find a valid local single-model HMM by accession or NAME before using network."""
    query_norm = _normalize_pfam_query(query)
    candidate_names = [
        f"{query_norm}.hmm",
        f"{query_norm.upper()}.hmm",
        f"{query}.hmm",
    ]
    for root in _local_hmm_search_roots(cache_dir):
        for name in dict.fromkeys(candidate_names):
            candidate = root / name
            if _pfam_hmm_matches_query(candidate, query_norm):
                return candidate
        try:
            hmm_files = root.rglob("*.hmm")
        except Exception:
            continue
        for candidate in hmm_files:
            if candidate.name == "Pfam-A.hmm":
                continue
            if _pfam_hmm_matches_query(candidate, query_norm):
                return candidate
    return None


def _copy_cached_hmm_to_query_cache(source: Path, query: str, cache_dir: Path) -> Path:
    query_norm = _normalize_pfam_query(query)
    dest = cache_dir / f"{query_norm}.hmm"
    cache_dir.mkdir(parents=True, exist_ok=True)
    if source.resolve() != dest.resolve():
        shutil.copy2(source, dest)
    return dest


def _download_single_pfam_hmm(query: str, cache_dir: Path) -> Path:
    """Download one Pfam HMM from the official InterPro/Pfam endpoint."""
    import urllib.request
    import urllib.error

    query = _normalize_pfam_query(query)
    gz_path = cache_dir / f"{query}.hmm.gz"
    hmm_path = cache_dir / f"{query}.hmm"
    if _is_valid_hmm_file(hmm_path):
        return hmm_path
    cached = _find_cached_pfam_hmm(query, cache_dir)
    if cached:
        print(f"Using cached Pfam HMM for {query}: {cached}")
        return _copy_cached_hmm_to_query_cache(cached, query, cache_dir)

    url = f"https://www.ebi.ac.uk/interpro/wwwapi/entry/pfam/{query}?annotation=hmm"
    print(f"Downloading Pfam HMM from InterPro: {query} -> {hmm_path}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        cached = _find_cached_pfam_hmm(query, cache_dir)
        if cached:
            print(f"InterPro returned HTTP {exc.code} for {query}; using cached HMM: {cached}")
            return _copy_cached_hmm_to_query_cache(cached, query, cache_dir)
        raise RuntimeError(
            f"Pfam HMM download failed for {query}: HTTP {exc.code}. "
            "Please provide Pfam-A.hmm, upload a .hmm file, or add the family/Pfam mapping."
        ) from exc
    except Exception as exc:
        cached = _find_cached_pfam_hmm(query, cache_dir)
        if cached:
            print(f"InterPro download failed for {query}; using cached HMM: {cached}")
            return _copy_cached_hmm_to_query_cache(cached, query, cache_dir)
        raise RuntimeError(
            f"Pfam HMM download failed for {query}: {exc}. "
            "Please provide Pfam-A.hmm, upload a .hmm file, or add the family/Pfam mapping."
        ) from exc
    gz_path.write_bytes(data)
    try:
        text = gzip.decompress(data).decode("latin-1")
    except OSError:
        text = data.decode("latin-1")
    if not text.startswith("HMMER3/"):
        raise RuntimeError(f"Downloaded Pfam HMM for {query} is not a HMMER profile")
    hmm_path.write_text(text, encoding="latin-1")
    return hmm_path


def download_required_pfam_hmms(required_pfams: str, cache_dir: Path) -> Tuple[Path, List[str], Dict[str, str]]:
    """Build a query HMM by downloading individual Pfam models."""
    queries = _split_pfam_queries(required_pfams)
    if not queries:
        raise RuntimeError("--required-pfams was provided but no valid Pfam IDs/names were parsed")
    output = cache_dir / f"{'_'.join(queries)}.hmm"
    fetch_map: Dict[str, str] = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="latin-1") as out:
        for query in queries:
            hmm_path = _download_single_pfam_hmm(query, cache_dir)
            text = hmm_path.read_text(encoding="latin-1")
            name = query
            for line in text.splitlines():
                if line.startswith("NAME "):
                    parts = line.split()
                    if len(parts) > 1:
                        name = parts[1]
                    break
            out.write(text)
            if not text.endswith("\n"):
                out.write("\n")
            fetch_map[query] = name
    assert_hmm_models_complete(output, queries, fetch_map)
    return output, queries, fetch_map


def hmmfetch_models(pfam_db_unz: Path, pfams: str, out_hmm: Path) -> Dict[str, str]:
    """Fetch one or more Pfam HMM models into a merged query HMM.

    HMMER hmmfetch does not accept comma-joined keys. Fetch models one by one,
    then validate the merged ACC list; if indexed lookup cannot resolve an
    accession/name, fall back to strict Python parsing.
    """
    print(f"Fetching {pfams} from {pfam_db_unz}")
    queries = _split_pfam_queries(pfams)
    if not queries:
        raise RuntimeError("No Pfam query was provided")
    out_hmm.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_hmm.parent / f"{out_hmm.stem}.hmmfetch_tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fetch_map: Dict[str, str] = {}
    parts: List[Path] = []
    failed: List[str] = []
    for query in queries:
        part = tmp_dir / f"{query}.hmm"
        cmd = [str(hmmfetch_bin()), '-o', str(part), str(pfam_db_unz), query]
        rc, out, err = run_cmd(cmd)
        if rc == 0 and part.exists() and part.stat().st_size > 0:
            audit = hmm_model_audit(part, [query])
            if not audit.get("missing_requested_pfams"):
                parts.append(part)
                model = (audit.get("models") or [{}])[0]
                fetch_map[query] = str(model.get("name") or query)
                continue
        failed.append(query)
    if failed:
        print(f"hmmfetch could not resolve {', '.join(failed)}, trying Python ACC/NAME fallback...")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return python_fetch_models(pfam_db_unz, ",".join(queries), out_hmm)

    with open(out_hmm, 'w', encoding='latin-1') as fout:
        for part in parts:
            text = part.read_text(encoding='latin-1')
            fout.write(text)
            if not text.endswith("\n"):
                fout.write("\n")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    assert_hmm_models_complete(out_hmm, queries, fetch_map)
    return fetch_map


def python_fetch_models(pfam_db: Path, pfams: str, out_hmm: Path) -> Dict[str, str]:
    """
    手动从 Pfam HMM 文件中提取指定的模型。
    """
    print(f"Python fetching {pfams} from {pfam_db}")
    pfam_list = _split_pfam_queries(pfams)
    requested = set(pfam_list)
    found: Dict[str, str] = {}
    out_lines = []

    def flush_model(model_lines: List[str]) -> None:
        if not model_lines:
            return
        name = ""
        accessions: Set[str] = set()
        for model_line in model_lines:
            if model_line.startswith("NAME "):
                parts = model_line.split()
                if len(parts) > 1:
                    name = parts[1]
            elif model_line.startswith("ACC "):
                parts = model_line.split()
                if len(parts) > 1:
                    acc = _normalize_pfam_query(parts[1])
                    accessions.add(acc)
        name_key = name.upper() if name else ""
        matched = []
        for q in sorted(requested - set(found)):
            q_norm = _normalize_pfam_query(q)
            if _is_pfam_accession(q_norm):
                if q_norm in accessions:
                    matched.append(q)
            elif name_key and q_norm.upper() == name_key:
                matched.append(q)
        if matched:
            out_lines.extend(model_lines)
            if not model_lines[-1].endswith("\n"):
                out_lines.append("\n")
            for q in matched:
                found[q] = name or q
            print(f"  ✓ {', '.join(matched)} -> {name or matched[0]}")

    model: List[str] = []
    with open(pfam_db, 'r', encoding='latin-1') as fin:
        for line in fin:
            if line.startswith('HMMER3/'):
                flush_model(model)
                model = [line]
            elif model:
                model.append(line)
        flush_model(model)

    missing = requested - set(found)
    if missing:
        raise RuntimeError(f"Python fetch models failed, missing: {', '.join(missing)} from {pfam_db}")
    
    out_hmm.parent.mkdir(parents=True, exist_ok=True)
    with open(out_hmm, 'w', encoding='latin-1') as fout:
        fout.writelines(out_lines)
    audit = assert_hmm_models_complete(out_hmm, pfam_list, found)
    print(f"Saved {len(found)} requested model(s) to {out_hmm}; loaded ACCs: {', '.join(audit.get('loaded_accessions') or [])}")
    return found


def build_required_pfam_hmm(required_pfams: str, pfam_dir: Optional[str], cache_dir: Path) -> Tuple[Path, List[str], Dict[str, str]]:
    """Build a query HMM from Pfam-A.hmm for --required-pfams."""
    queries = _split_pfam_queries(required_pfams)
    if not queries:
        raise RuntimeError("--required-pfams was provided but no valid Pfam IDs/names were parsed")
    pfam_db = _resolve_pfam_db(pfam_dir)
    if not pfam_db:
        print("未找到本地 Pfam-A.hmm，改用官方 InterPro/Pfam 单模型下载。")
        return download_required_pfam_hmms(required_pfams, cache_dir)
    pfam_db_unz = _decompress_pfam_db_if_needed(pfam_db, cache_dir)
    out_hmm = cache_dir / f"{'_'.join(queries)}.hmm"
    fetch_map = hmmfetch_models(pfam_db_unz, ",".join(queries), out_hmm)
    assert_hmm_models_complete(out_hmm, queries, fetch_map)
    return out_hmm, queries, fetch_map


def hmmsearch_bin() -> Path:
    """返回 hmmsearch 二进制路径"""
    import shutil as _shutil
    resolved = _shutil.which("hmmsearch")
    if resolved:
        return Path(resolved)
    raise RuntimeError("hmmsearch not found in PATH")


def hmmfetch_bin() -> Path:
    """返回 hmmfetch 二进制路径"""
    import shutil as _shutil
    resolved = _shutil.which("hmmfetch")
    if resolved:
        return Path(resolved)
    raise RuntimeError("hmmfetch not found in PATH")


def read_pep_ids(pep_fasta: Path) -> List[str]:
    """读取蛋白质fasta中的所有ID"""
    ids = []
    with open(pep_fasta, 'r') as f:
        for line in f:
            if line.startswith('>'):
                # 去除 > 和可能的注释
                seq_id = line[1:].split()[0].strip()
                ids.append(seq_id)
    return ids


def read_ids_from_file(ids_file: Path) -> Set[str]:
    """读取ID文件，返回集合"""
    ids = set()
    if ids_file and ids_file.exists():
        with open(ids_file, 'r') as f:
            for line in f:
                id_str = line.strip()
                if id_str:
                    ids.add(id_str)
    return ids


def summarize_domtblout_by_model(
    domtblout: Path,
    evalue_max: float,
    query_name_to_accession: Optional[Dict[str, str]] = None,
) -> Dict[str, int]:
    """Count unique target IDs per HMM query model in a domtblout file."""
    counts: Dict[str, Set[str]] = {}
    mapping = query_name_to_accession or {}
    if not domtblout.exists():
        return {}
    with open(domtblout, 'r', encoding='utf-8', errors='ignore') as handle:
        for line in handle:
            if line.startswith('#') or not line.strip():
                continue
            parts = line.strip().split()
            if len(parts) < 13:
                continue
            try:
                evalue = float(parts[12])
            except Exception:
                continue
            if evalue > evalue_max:
                continue
            target = parts[0]
            query = parts[3]
            key = mapping.get(query) or query
            counts.setdefault(key, set()).add(target)
    return {key: len(value) for key, value in sorted(counts.items())}


def parse_domtblout(domtblout: Path, evalue_max: float, cov_min: float, required_pfams: List[str], query_name_to_accession: Optional[Dict[str, str]] = None) -> Tuple[dict, dict]:
    """
    解析 hmmsearch 的 domtblout 文件。
    返回:
      - per_seq: {seq_id: {pfam: [(evalue, cov)]}}
      - per_seq_best: {seq_id: {pfam: (evalue, cov)}}
    """
    per_seq = {}
    per_seq_best = {}
    
    # FIX: 防御性处理 - 如果 required_pfams 为 None，设为空集合（不过滤）
    required_set = set(required_pfams) if required_pfams is not None else set()
    
    with open(domtblout, 'r') as f:
        for line in f:
            if line.startswith('#'):
                continue
            parts = line.strip().split()
            if len(parts) < 23:
                continue
            target = parts[0]          # 序列ID
            query_name = parts[3]      # HMM名称 (NAME in query HMM)
            query = (query_name_to_accession or {}).get(query_name, query_name)
            if required_set and query not in required_set:
                continue  # 只关注我们需要的Pfam（如果设置了过滤集合）
            
            evalue = float(parts[12])  # domain i-Evalue
            # 计算覆盖度
            alen = int(parts[2])       # 序列长度
            hmm_from = int(parts[15])
            hmm_to = int(parts[16])
            hmm_cov = (hmm_to - hmm_from + 1) / alen if alen > 0 else 0
            
            # 初步过滤
            if evalue > evalue_max or hmm_cov < cov_min:
                continue
            
            # 记录到 per_seq
            if target not in per_seq:
                per_seq[target] = {}
                per_seq_best[target] = {}
            if query not in per_seq[target]:
                per_seq[target][query] = []
                per_seq_best[target][query] = (evalue, hmm_cov)
            
            per_seq[target][query].append((evalue, hmm_cov))
            # 更新最佳值（E-value最小）
            if evalue < per_seq_best[target][query][0]:
                per_seq_best[target][query] = (evalue, hmm_cov)
    
    return per_seq, per_seq_best



# ======== 从这里开始插入 ========
def compute_file_hash(filepath: Path) -> str:
    """计算文件的 SHA256 哈希值"""
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception as e:
        return f"error: {e}"
# ======== 插入结束 ========

# 添加工具版本打印信息
def get_tool_versions() -> dict:
    """获取关键工具版本信息"""
    versions = {}

    # 1. HMMER
    try:
  #      result = subprocess.run(["hmmsearch", "-h"], shell=Ture, apture_output=True, text=True, timeout=5)
   #     for line in result.stderr.split('\n'):
    #        if "HMMER" in line:
     #           versions["hmmer"] = line.strip()
      #          break
   # except:
    #    versions["hmmer"] = "unknown"
        # 先检查命令是否存在
        import shutil
        if not shutil.which("hmmsearch"):
            versions["hmmer"] = "command not found in PATH"
        else:
            # 使用更全面的参数尝试
            for arg in ["-h", "--help", "-version", "--version"]:
                try:
                    result = subprocess.run(
                        ["hmmsearch", arg],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        encoding='utf-8',
                        errors='ignore'
                    )
                    
                    # 合并所有输出
                    all_output = result.stdout + result.stderr
                    
                    # 使用正则表达式搜索版本
                    import re
                    version_match = re.search(r'HMMER\s+(\d+\.\d+\.?\d*)', all_output)
                    if version_match:
                        versions["hmmer"] = f"HMMER {version_match.group(1)}"
                        break
                        
                    # 或者搜索包含 HMMER 的行
                    for line in all_output.split('\n'):
                        if "HMMER" in line.upper():
                            versions["hmmer"] = line.strip()
                            break
                    if "hmmer" in versions:
                        break
                        
                except Exception:
                    continue
                    
            if "hmmer" not in versions:
                versions["hmmer"] = "version detection failed"
                
    except Exception as e:
        versions["hmmer"] = f"error: {str(e)[:50]}"
    # 2. BLAST
    try:
        result = subprocess.run(["blastp", "-version"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.split('\n'):
            if "blastp" in line:
                versions["blast"] = line.strip()
                break
    except:
        versions["blast"] = "unknown"

    # 3. Python 版本
    versions["python"] = sys.version.split()[0]

    return versions


def select_final_members(
    pep_ids: List[str],
    per_seq: dict,
    per_seq_best: dict,
    required_pfams: List[str],
    evalue_max: float,
    cov_min: float
) -> Tuple[List[str], dict]:
    """
    选择最终的成员。
    严格模式：必须包含所有 required_pfams。
    返回 (final_ids, drop_reasons)
    """
    final = []
    drop_reasons = {}
    
    required_set = set(required_pfams)
    
    for seq_id in pep_ids:
        if seq_id not in per_seq:
            drop_reasons[seq_id] = 'no_hit'
            continue
        
        # 检查是否包含所有必需的 Pfam
        hits = set(per_seq[seq_id].keys())
        missing = required_set - hits
        if missing:
            drop_reasons[seq_id] = f'missing_required'
            continue
        
        # 检查每个Pfam是否通过阈值
        passed_all = True
        for pfam in required_set:
            best_e, best_cov = per_seq_best[seq_id][pfam]
            if best_e > evalue_max or best_cov < cov_min:
                passed_all = False
                drop_reasons[seq_id] = f'failed_qc_{pfam}'
                break
        
        if passed_all:
            final.append(seq_id)
        else:
            # 如果已经在上面记录了原因，这里不再记录
            if seq_id not in drop_reasons:
                drop_reasons[seq_id] = 'failed_qc'
    
    return final, drop_reasons


def write_fasta_subset(source_fasta: Path, ids: List[str], out_fasta: Path):
    """从source_fasta中提取指定的IDs写入out_fasta"""
    id_set = set(ids)
    writing = False
    current_id = None
    out_lines = []
    
    with open(source_fasta, 'r') as f:
        for line in f:
            if line.startswith('>'):
                # 提取ID
                header = line[1:].strip()
                seq_id = header.split()[0]
                if seq_id in id_set:
                    writing = True
                    current_id = seq_id
                    out_lines.append(line)
                else:
                    writing = False
                    current_id = None
            elif writing:
                out_lines.append(line)
    
    out_fasta.parent.mkdir(parents=True, exist_ok=True)
    with open(out_fasta, 'w') as f:
        f.writelines(out_lines)
    print(f"Saved {len(ids)} sequences to {out_fasta}")


def write_report(
    out_report: Path,
    prefix: str,
    required_pfams: List[str],
    evalue_max: float,
    cov_min: float,
    cut_ga: bool,
    cpu: int,
    pfam_db: Path,
    retention_days: int,
    total_proteins: int,
    candidates: int,
    final_members: int,
    drop_reasons: dict,
    start_time: datetime,
        # ====== 新增参数 ======
    tool_versions: dict = None,
    cmd: List[str] = None,
    inputs: List[dict] = None,
    end_time: datetime = None,
    elapsed_sec: float = None
):
    """写入鉴定报告"""
 
    # 设置默认值
    tool_versions = tool_versions or {}
    cmd = cmd or []
    inputs = inputs or []

    lines = [
        f"# Identify Report: {prefix}",
        "",
        "## Parameters",
        f"- required_pfams: {', '.join(required_pfams)}",
        f"- dom_i_evalue_max: {evalue_max}",
        f"- hmm_cov_min: {cov_min}",
        f"- cut_ga: {cut_ga}",
        f"- cpu: {cpu}",
        f"- pfam_db: {pfam_db}",
        f"- retention_days: {retention_days} (expires around {start_time + timedelta(days=retention_days)})",
        "",
        "## Summary",
        f"- total input proteins: {total_proteins}",
        f"- candidates (any required Pfam hit passing QC): {candidates}",
        f"- strict final members (contain ALL required Pfams): {final_members}",
    ]
    
    # 统计 drop 原因
    reason_counts = {}
    for reason in drop_reasons.values():
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    
    for reason, cnt in reason_counts.items():
        lines.append(f"- dropped {reason}: {cnt}")
        # ====== 新增：Reproducibility Section ======
    lines.append("")
    lines.append("## Reproducibility")
    lines.append("")
    
    # 工具版本
    if tool_versions:
        lines.append("### Tool Versions")
        for tool, version in sorted(tool_versions.items()):
            lines.append(f"- **{tool}**: `{version}`")
        lines.append("")
    
    # 执行时间
    if elapsed_sec is not None:
        lines.append(f"- **Elapsed time**: {elapsed_sec:.2f} seconds")
    if start_time:
        lines.append(f"- **Start time**: {start_time.isoformat()}")
    if end_time:
        lines.append(f"- **End time**: {end_time.isoformat()}")
    lines.append("")
    
    # 执行命令
    if cmd:
        lines.append("### Command")
        lines.append("```bash")
        # 美化命令：每行显示一部分，避免过长
        cmd_str = " ".join(cmd)
        if len(cmd_str) > 80:
            # 简单换行：每80个字符换行
            import textwrap
            wrapped = textwrap.wrap(cmd_str, width=80)
            lines.extend(wrapped)
        else:
            lines.append(cmd_str)
        lines.append("```")
        lines.append("")
    
    # 输入文件摘要
    if inputs:
        lines.append("### Input Files")
        lines.append("| File | Size (MB) | SHA256 (first 12) |")
        lines.append("|------|-----------|-------------------|")
        for inp in inputs:
            name = inp.get("path", "unknown")
            size_bytes = inp.get("size", 0)
            size_mb = size_bytes / (1024 * 1024) if size_bytes > 0 else 0
            sha_full = inp.get("sha256", "")
            sha_short = sha_full[:12] if sha_full else "N/A"
            lines.append(f"| `{name}` | {size_mb:.2f} | `{sha_short}` |")
        lines.append("")
    # ====== 新增部分结束 ======
    

    lines.append("")
    lines.append("## Dropped IDs (auto explanation)")
    lines.append("")
    
    # 列出前20个被drop的ID
    dropped_items = list(drop_reasons.items())
    for seq_id, reason in dropped_items[:20]:
        lines.append(f"- {seq_id:<25} REASON={reason}")
    
    if len(dropped_items) > 20:
        lines.append(f"- ... and {len(dropped_items) - 20} more")
    
    lines.append("")
    lines.append(f"Report generated at {datetime.now().isoformat()}")
    
    out_report.parent.mkdir(parents=True, exist_ok=True)
    with open(out_report, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    
    print(f"Report: {out_report}")


def run_hmm_analysis(
    input_dir: Path,
    output_dir: Path,
    prefix: str,
    hmm_file: Path,
    pep_file: Path,
    cpu: int = 8,
    evalue: float = 1e-5  # 默认E值阈值
) -> Tuple[Path, Path]:
    """
    运行HMM分析（使用原始蛋白质序列文件）
    返回: (domtblout_file, ids_file)
    """
    print("\n" + "=" * 60)
    print("🔎 运行HMM分析（使用原始文件）")
    print("=" * 60)

    # 创建分析目录
    analysis_dir = output_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    # 定义输出文件
    domtblout_file = analysis_dir / f"{prefix}.domtblout"
    hmmout_file = analysis_dir / f"{prefix}.hmmout"
    filtered_file = analysis_dir / f"{prefix}.domtblout.filter"
    ids_file = analysis_dir / f"{prefix}.hmm.ids"
    hmm_safe_pep = analysis_dir / f"{prefix}.hmmsearch_safe.pep.fa"
    sanitize_stats = prepare_fasta_for_hmmsearch(pep_file, hmm_safe_pep)
    if sanitize_stats["skipped_long_sequences"] or sanitize_stats["removed_invalid_residues"]:
        print(
            "🧹 HMMER输入清洗: "
            f"原始序列 {sanitize_stats['sequence_count']} 条，"
            f"写入 {sanitize_stats['written_sequences']} 条，"
            f"跳过超长序列 {sanitize_stats['skipped_long_sequences']} 条，"
            f"移除非法字符 {sanitize_stats['removed_invalid_residues']} 个。"
        )
        print(f"   清洗报告: {sanitize_stats['report']}")
    if sanitize_stats["written_sequences"] == 0:
        raise RuntimeError(
            f"hmmsearch无法执行：{pep_file.name} 中没有可用蛋白序列。"
            f"可能是文件不是蛋白FASTA，或所有序列长度超过 {sanitize_stats['max_length']}。"
        )

    # 1. 运行hmmsearch
    print(f"\n🚀 执行hmmsearch命令:")
    cmd = [
        str(hmmsearch_bin()),
        '-E', str(evalue),  # 使用传入的E值阈值
        '--domtblout', str(domtblout_file),
        '-o', str(hmmout_file),
        '--cpu', str(cpu),
        str(hmm_file),
        str(hmm_safe_pep)
    ]
    print(f"  {' '.join(cmd)}")
    
    rc, out, err = run_cmd(cmd)
    if rc != 0:
        raise RuntimeError(f"hmmsearch失败: {err}")
    
    print(f"✅ hmmsearch完成: {domtblout_file}")
    
    # 2. 过滤 domain i-Evalue
    print(f"\n🚀 过滤结果 (domain i-Evalue < {evalue}):")
    awk_cmd = f"awk '$13<{evalue} && $1 !~ /^#/ {{print $0 }}' {domtblout_file} > {filtered_file}"
    print(f"  {awk_cmd}")
    
    rc, out, err = run_cmd(awk_cmd, shell=True)
    if rc != 0:
        raise RuntimeError(f"过滤失败: {err}")
    
    print(f"✅ 过滤完成: {filtered_file}")
    
    # 3. 提取基因ID
    print(f"\n🚀 提取基因ID:")
    id_cmd = f"awk '{{print $1}}' {filtered_file} | sort -u > {ids_file}"
    print(f"  {id_cmd}")
    
    rc, out, err = run_cmd(id_cmd, shell=True)
    if rc != 0:
        raise RuntimeError(f"提取ID失败: {err}")
    
    print(f"✅ 提取ID完成: {ids_file}")
    
    # 显示结果
    with open(ids_file, 'r') as f:
        gene_ids = [line.strip() for line in f if line.strip()]
    
    print(f"\n📊 HMM分析结果:")
    print(f"  鉴定到的基因数: {len(gene_ids)}")
    
    if gene_ids:
        print(f"\n🔝 前10个基因:")
        for i, gene_id in enumerate(gene_ids[:10]):
            print(f"  {i+1:2d}. {gene_id}")
        if len(gene_ids) > 10:
            print(f"  ... 还有 {len(gene_ids)-10} 个基因")
    
    return domtblout_file, ids_file


def cleanup_current_blast_intermediates(analysis_dir: Path, prefix: str) -> None:
    """Remove current-prefix BLAST outputs and legacy BLAST DB files before rerun."""
    analysis_dir.mkdir(parents=True, exist_ok=True)
    patterns = [f"{prefix}.blast*", "blast_db.*"]
    removed = 0
    for pattern in patterns:
        for path in analysis_dir.glob(pattern):
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed += 1
            except FileNotFoundError:
                continue
            except Exception as exc:
                print(f"⚠️  无法清理旧BLAST中间文件 {path}: {exc}")
    if removed:
        print(f"🧹 已清理当前prefix旧BLAST中间文件/数据库: {removed} 个")


def build_target_proteome_blast_db(analysis_dir: Path, pep_file: Path) -> Dict[str, Any]:
    """Build the fixed identify BLAST database from the target whole proteome."""
    analysis_dir.mkdir(parents=True, exist_ok=True)
    db_prefix = analysis_dir / "blast_db.target_proteome"
    print(f"\n🚀 创建目标全蛋白Blast数据库:")
    cmd = ['makeblastdb', '-in', str(pep_file), '-dbtype', 'prot', '-out', str(db_prefix)]
    print(f"  {' '.join(cmd)}")
    rc, out, err = run_cmd(cmd)
    if rc != 0:
        print(f"⚠️  创建目标全蛋白数据库失败: {err}")
        return {
            "status": "failed",
            "target_database_input": str(pep_file.resolve()),
            "target_database_role": "target_proteome",
            "target_db_prefix": str(db_prefix.resolve()),
            "error": err,
        }
    database_files = [
        str(path.resolve()) for path in sorted(db_prefix.parent.glob(db_prefix.name + ".*"))
    ]
    print("✅ 目标全蛋白数据库创建完成")
    return {
        "status": "success",
        "target_database_input": str(pep_file.resolve()),
        "target_database_role": "target_proteome",
        "target_db_prefix": str(db_prefix.resolve()),
        "target_database_files": database_files,
    }


def parse_blast_outfmt6_target_hits(
    blast_output: Path,
    evalue: float = 1e-7,
    identity_min: float = 30.0,
) -> Tuple[Set[str], Dict[str, Dict[str, Any]]]:
    """Parse identify BLAST outfmt6 and return target candidate IDs from sseqid."""
    kept_ids: Set[str] = set()
    support: Dict[str, Dict[str, Any]] = {}
    with Path(blast_output).open("r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            parts = raw.rstrip("\n").split("\t")
            if len(parts) < 12:
                continue
            query_id = parts[0].strip()
            target_id = parts[1].strip()
            if not query_id or not target_id:
                continue
            try:
                pident = float(parts[2])
                hit_evalue = float(parts[10])
                bitscore = float(parts[11])
            except ValueError:
                continue
            if pident <= identity_min or hit_evalue > evalue:
                continue
            kept_ids.add(target_id)
            entry = support.setdefault(
                target_id,
                {"query_ids": set(), "best_evalue": hit_evalue, "best_bitscore": bitscore},
            )
            entry["query_ids"].add(query_id)
            if hit_evalue < entry["best_evalue"] or (
                hit_evalue == entry["best_evalue"] and bitscore > entry["best_bitscore"]
            ):
                entry["best_evalue"] = hit_evalue
                entry["best_bitscore"] = bitscore

    evidence_by_gene: Dict[str, Dict[str, Any]] = {}
    for target_id, entry in support.items():
        evidence_by_gene[target_id] = {
            "query_ids": sorted(entry["query_ids"]),
            "best_evalue": entry["best_evalue"],
            "best_bitscore": entry["best_bitscore"],
        }
    return kept_ids, evidence_by_gene


def run_blast_analysis(
    input_dir: Path,
    output_dir: Path,
    prefix: str,
    ref_file: Path,
    pep_file: Path,
    cpu: int = 8,
    evalue: float = 1e-7,
    identity_min: float = 30.0,
    reference_label: str = "reference",
    target_db_info: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Run one fixed-direction identify BLAST search.

    The target species whole-proteome FASTA is the BLAST database, the reference
    family protein FASTA is the query, and candidate target IDs are parsed from
    outfmt6 sseqid.
    """
    print("\n" + "=" * 60)
    print(f"🧬 运行Blast分析（参考集合: {reference_label}）")
    print("=" * 60)

    if not ref_file or not ref_file.exists():
        print("⚠️  未提供参考序列文件，跳过Blast分析")
        return None

    analysis_dir = output_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    if target_db_info is None:
        target_db_info = build_target_proteome_blast_db(analysis_dir, pep_file)
    if target_db_info.get("status") != "success":
        return {
            "status": "failed",
            "reference_label": sanitize_token(reference_label or infer_reference_label(ref_file)),
            "reference_file": str(ref_file.resolve()),
            "query_file": str(ref_file.resolve()),
            "query_role": "reference_family_pep",
            "target_database_input": str(pep_file.resolve()),
            "target_database_role": "target_proteome",
            "target_db_prefix": target_db_info.get("target_db_prefix", ""),
            "target_database_files": target_db_info.get("target_database_files", []),
            "candidate_id_column": "sseqid",
            "error": target_db_info.get("error", "target proteome BLAST database build failed"),
        }

    ref_token = sanitize_token(reference_label or infer_reference_label(ref_file))
    db_prefix = Path(str(target_db_info["target_db_prefix"]))
    blast_output = analysis_dir / f"{prefix}.blast.{ref_token}.out"
    ids_file = analysis_dir / f"{prefix}.blast.{ref_token}.ids"

    print(f"\n🚀 运行blastp:")
    cmd = [
        'blastp',
        '-query', str(ref_file),
        '-db', str(db_prefix),
        '-out', str(blast_output),
        '-evalue', str(evalue),
        '-outfmt', '6 std qlen slen',
        '-num_threads', str(cpu),
    ]
    print(f"  {' '.join(cmd)}")

    rc, out, err = run_cmd(cmd)
    if rc != 0:
        print(f"⚠️  blastp失败: {err}")
        return {
            "status": "failed",
            "reference_label": ref_token,
            "reference_file": str(ref_file.resolve()),
            "reference_file_name": ref_file.name,
            "query_file": str(ref_file.resolve()),
            "query_role": "reference_family_pep",
            "target_database_input": str(pep_file.resolve()),
            "target_database_role": "target_proteome",
            "target_db_prefix": str(db_prefix.resolve()),
            "target_database_files": target_db_info.get("target_database_files", []),
            "candidate_id_column": "sseqid",
            "blast_output": str(blast_output.resolve()),
            "error": err,
        }

    print(f"✅ blastp完成: {blast_output}")

    kept_ids, evidence_by_gene = parse_blast_outfmt6_target_hits(blast_output, evalue=evalue, identity_min=identity_min)
    write_id_list(sorted(kept_ids), ids_file)
    print(f"✅ 过滤完成: {ids_file}")

    print(f"\n📊 Blast分析结果 ({ref_token}):")
    print(f"  鉴定到的目标基因数: {len(kept_ids)}")
    for i, gene_id in enumerate(sorted(kept_ids)[:10], 1):
        print(f"  {i:2d}. {gene_id}")
    if len(kept_ids) > 10:
        print(f"  ... 还有 {len(kept_ids)-10} 个基因")

    return {
        "status": "success",
        "reference_label": ref_token,
        "reference_file": str(ref_file.resolve()),
        "reference_file_name": ref_file.name,
        "reference_records": count_fasta_records(ref_file),
        "query_file": str(ref_file.resolve()),
        "query_role": "reference_family_pep",
        "target_database_input": str(pep_file.resolve()),
        "target_database_role": "target_proteome",
        "target_db_prefix": str(db_prefix.resolve()),
        "target_database_files": target_db_info.get("target_database_files", []),
        "candidate_id_column": "sseqid",
        "blast_output": str(blast_output.resolve()),
        "blast_identity_min": identity_min,
        "ids_file": str(ids_file.resolve()),
        "members": len(kept_ids),
        "ids": sorted(kept_ids),
        "evidence_by_gene": evidence_by_gene,
    }


def run_multi_reference_blast_analysis(
    input_dir: Path,
    output_dir: Path,
    prefix: str,
    references: List[Dict[str, Any]],
    pep_file: Path,
    cpu: int = 8,
    evalue: float = 1e-7,
    identity_min: float = 30.0,
) -> Dict[str, Any]:
    """Run each reference family query against one target-proteome BLAST DB."""
    successful: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    source_by_gene: Dict[str, Set[str]] = {}
    query_by_gene: Dict[str, Set[str]] = {}
    best_by_gene: Dict[str, Dict[str, float]] = {}

    analysis_dir = output_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    cleanup_current_blast_intermediates(analysis_dir, prefix)
    target_db_info = build_target_proteome_blast_db(analysis_dir, pep_file)
    if target_db_info.get("status") != "success":
        return {
            "status": "failed",
            "references": [
                {k: v for k, v in ref.items() if k != "path"} | {"path": str(Path(ref["path"]).resolve())}
                for ref in references
            ],
            "successful": [],
            "failed": [target_db_info],
            "ids_file": "",
            "source_matrix": "",
            "members": 0,
            "source_by_gene": {},
            "target_db": target_db_info,
        }

    for ref in references:
        result = run_blast_analysis(
            input_dir=input_dir,
            output_dir=output_dir,
            prefix=prefix,
            ref_file=Path(ref["path"]),
            pep_file=pep_file,
            cpu=cpu,
            evalue=evalue,
            identity_min=identity_min,
            reference_label=str(ref.get("label") or infer_reference_label(Path(ref["path"]))),
            target_db_info=target_db_info,
        )
        if not result:
            continue
        if result.get("status") != "success":
            failed.append(result)
            continue
        successful.append(result)
        label = str(result.get("reference_label") or "reference")
        evidence_by_gene = result.get("evidence_by_gene") or {}
        for gene_id in result.get("ids") or []:
            source_by_gene.setdefault(gene_id, set()).add(label)
            evidence = evidence_by_gene.get(gene_id) or {}
            query_by_gene.setdefault(gene_id, set()).update(str(q) for q in evidence.get("query_ids") or [])
            hit_evalue = evidence.get("best_evalue")
            bitscore = evidence.get("best_bitscore")
            if hit_evalue is None or bitscore is None:
                continue
            current = best_by_gene.get(gene_id)
            if current is None or hit_evalue < current["best_evalue"] or (
                hit_evalue == current["best_evalue"] and bitscore > current["best_bitscore"]
            ):
                best_by_gene[gene_id] = {"best_evalue": hit_evalue, "best_bitscore": bitscore}

    merged_ids_file = analysis_dir / f"{prefix}.blast.ids"
    source_matrix_file = analysis_dir / f"{prefix}.blast.source_matrix.tsv"
    merged_ids = sorted(source_by_gene)
    write_id_list(merged_ids, merged_ids_file)
    source_matrix_file.parent.mkdir(parents=True, exist_ok=True)
    with source_matrix_file.open("w", encoding="utf-8") as handle:
        handle.write("gene_id\tblast_reference_sources\tblast_reference_count\tblast_query_ids\tbest_evalue\tbest_bitscore\n")
        for gene_id in merged_ids:
            sources = sorted(source_by_gene.get(gene_id, set()))
            queries = sorted(query_by_gene.get(gene_id, set()))
            best = best_by_gene.get(gene_id, {})
            handle.write(
                f"{gene_id}\t{';'.join(sources)}\t{len(sources)}\t"
                f"{';'.join(queries)}\t{best.get('best_evalue', '')}\t{best.get('best_bitscore', '')}\n"
            )

    return {
        "status": "success" if successful else "skipped",
        "references": [
            {k: v for k, v in ref.items() if k != "path"} | {"path": str(Path(ref["path"]).resolve())}
            for ref in references
        ],
        "successful": successful,
        "failed": failed,
        "ids_file": str(merged_ids_file.resolve()),
        "source_matrix": str(source_matrix_file.resolve()),
        "members": len(merged_ids),
        "source_by_gene": {gene_id: sorted(sources) for gene_id, sources in source_by_gene.items()},
        "query_by_gene": {gene_id: sorted(queries) for gene_id, queries in query_by_gene.items()},
        "target_db": target_db_info,
        "blast_database_role": "target_proteome",
        "blast_query_role": "reference_family_pep",
        "blast_candidate_id_column": "sseqid",
    }

def combine_hmm_blast_results(
    hmm_ids_file: Path,
    blast_ids_file: Optional[Path],
    output_dir: Path,
    prefix: str,
    merge_mode: str = 'intersection'
) -> Path:
    """
    整合HMM和Blast结果。
    merge_mode: 'intersection'（交集）或 'union'（并集）。
    """
    print("\n" + "=" * 60)
    print(f"🔄 整合HMM和Blast结果 ({merge_mode}模式)")
    print("=" * 60)

    hmm_ids = read_ids_from_file(hmm_ids_file)
    blast_ids = read_ids_from_file(blast_ids_file) if blast_ids_file else set()

    if blast_ids:
        if merge_mode == 'intersection':
            combined_ids = hmm_ids.intersection(blast_ids)
            print("  合并方式: 交集 (HMM ∩ BLAST)")
        else:
            combined_ids = hmm_ids.union(blast_ids)
            print("  合并方式: 并集 (HMM ∪ BLAST)")
    else:
        combined_ids = hmm_ids
        print("  无BLAST结果，仅使用HMM结果")

    combine_dir = output_dir / "combine"
    combine_dir.mkdir(parents=True, exist_ok=True)
    combined_file = combine_dir / f"{prefix}.combined.ids"
    write_id_list(sorted(combined_ids), combined_file)

    print("\n📊 整合结果:")
    print(f"  HMM鉴定基因数: {len(hmm_ids)}")
    if blast_ids_file:
        print(f"  Blast鉴定基因数: {len(blast_ids)}")
    print(f"  整合后基因数: {len(combined_ids)}")
    print(f"  结果文件: {combined_file}")

    for i, gene_id in enumerate(sorted(combined_ids)[:10], 1):
        print(f"  {i:2d}. {gene_id}")
    if len(combined_ids) > 10:
        print(f"  ... 还有 {len(combined_ids)-10} 个基因")

    return combined_file


def main():
    print(f"🐛 DEBUG: Python {sys.version}")
    print(f"🐛 DEBUG: 脚本路径: {Path(__file__).resolve()}")
    print(f"🐛 DEBUG: 当前目录: {Path.cwd()}")
   
    try:
        test_versions = get_tool_versions()
        print(f"🛠️  DEBUG: 工具版本测试: {test_versions}")
    except Exception as e:
        print(f"❌ DEBUG: 获取工具版本失败: {e}")

    script_start_time = datetime.now()
   
    parser = argparse.ArgumentParser(description='Identify stage for web pipeline - 多物种修订版')
    parser.add_argument('--input-dir', required=True, help='input/<job_id>/ (用户上传文件目录)')
    parser.add_argument('--workspace', required=True, help='workspace/<job_id>/')
    parser.add_argument('--prefix', required=True, help='分析前缀，例如 At.WRKY')
    parser.add_argument('--cpu', type=int, default=8)
    parser.add_argument('--force', action='store_true', help='overwrite existing results')
    parser.add_argument('--required-pfams', help='comma-separated Pfam accessions, e.g. PF03110,PFxxxxx')
    parser.add_argument('--dom-i-evalue-max', type=float, default=1e-3, help='domain i-Evalue threshold')
    parser.add_argument('--hmm-cov-min', type=float, default=0.3, help='HMM coverage threshold')
    parser.add_argument('--blast-evalue', type=float, default=1e-7, help='Blast E-value threshold')
    parser.add_argument('--blast-identity-min', type=float, default=30.0, help='minimum BLAST percent identity for reference-protein hits')
    parser.add_argument('--cut-ga', action='store_true', help='use Pfam GA thresholds')
    parser.add_argument('--pfam-dir', help='directory containing Pfam-A.hmm (default: env BBA_PFAM_DIR)')
    parser.add_argument('--retention-days', type=int, default=7, help='days to keep workspace')
    parser.add_argument('--ref-root', help='reference root directory')
    parser.add_argument('--ref-species', help='comma-separated reference species names; can also use env BBA_REF_SPECIES')
    parser.add_argument('--ref-file', help='指定参考序列文件（用于多个参考序列时选择，如 At_NAC_pep.fasta）')
    parser.add_argument('--target-species', help='comma-separated target species codes, e.g. Lc,Dl')
    parser.add_argument('--scope-file', help='Scope JSON from apps/cli/resolve_scope.py')
    parser.add_argument('--log-file', help='日志文件路径（从pipeline_backend传入）')
    parser.add_argument('--hmm-combine-mode', choices=['intersection', 'union'], default='union',
                        help='多HMM文件时的整合方式（交集intersection/并集union），默认并集')
    parser.add_argument('--hmm-blast-merge-mode', choices=['intersection', 'union'], default=None,
                        help='HMM和BLAST结果整合方式（交集intersection/并集union）')
    parser.add_argument('--merge-mode', choices=['intersection', 'union'], default='intersection',
                        help='HMM和BLAST结果整合方式（默认交集intersection）')
    parser.add_argument('--skip-ref-warning', action='store_true',
                        help='CLI模式：跳过无参考序列的提醒，直接继续')
    parser.add_argument('--countdown-action', choices=['continue', 'cancel'], default='continue',
                        help='前端倒计时结束后发送的动作')
    
    args = parser.parse_args()

    in_dir = Path(args.input_dir).resolve()
    ws_root = Path(args.workspace).resolve()
    prefix = args.prefix
    scope_data: Dict[str, Any] = {}
    if args.scope_file:
        scope_path = Path(args.scope_file).resolve()
        if not scope_path.exists():
            raise RuntimeError(f"--scope-file 不存在: {scope_path}")
        scope_data = read_scope(scope_path)
        print(f"🧭 Scope: {scope_data.get('scope_id', scope_path.name)}")
        print(f"   target: {','.join(scope_data.get('target_species') or []) or '未指定'}")
        print(f"   reference: {','.join(scope_data.get('reference_species') or []) or '未指定'}")
        print(f"   comparative: {','.join(scope_data.get('comparative_species') or []) or '未指定'}")
    
    tool_versions = get_tool_versions()
    print(f"🔧 工具版本: {tool_versions}")

    inputs_info = []
    for file_path in in_dir.iterdir():
        if file_path.is_file():
            inputs_info.append({
                "path": str(file_path.relative_to(in_dir)),
                "full_path": str(file_path.resolve()),
                "sha256": compute_file_hash(file_path),
                "size": file_path.stat().st_size,
                "mtime": file_path.stat().st_mtime
            })
    
    if not in_dir.exists():
        raise RuntimeError(f"Input directory not found: {in_dir}")
    
    log_file_path = None
    if args.log_file:
        log_file_path = Path(args.log_file).resolve()
        print(f"📝 日志文件: {log_file_path}")
    
    print(f"🌱 Identify stage for {prefix} (多物种修订版)")
    print(f"   input目录: {in_dir}")
    print(f"   workspace目录: {ws_root}")
    
    # 多物种支持
    gene_family = extract_gene_family(in_dir, prefix, ws_root)
    normalized_prefix = normalize_identify_prefix(prefix, gene_family)
    if normalized_prefix != prefix:
        print(f"🧭 输出标签归一化: {prefix} -> {normalized_prefix}")
        prefix = normalized_prefix
    print(f"🧬 基因家族: {gene_family}")
    
    reference_policy = read_reference_policy_from_config(in_dir)
    reference_sets = collect_reference_files(
        in_dir,
        gene_family=gene_family,
        workspace=ws_root,
        explicit_ref_file=args.ref_file,
    )
    if reference_sets and args.ref_species:
        requested_refs = {item.strip() for item in re.split(r"[,;]", args.ref_species) if item.strip()}
        before_count = len(reference_sets)
        reference_sets = [
            ref for ref in reference_sets
            if str(ref.get("species") or "") in requested_refs or str(ref.get("label") or "") in requested_refs
        ]
        print(f"📚 根据 --ref-species 筛选参考集合: {before_count} -> {len(reference_sets)}")
        missing_refs = requested_refs - {str(ref.get("species") or ref.get("label")) for ref in reference_sets} - {str(ref.get("label") or ref.get("species")) for ref in reference_sets}
        if missing_refs:
            print(f"⚠️  未找到指定参考物种家族蛋白: {', '.join(sorted(missing_refs))}")

    if not reference_sets:
        print("ℹ️  config.xlsx 未声明 identify 参考家族蛋白，后续将仅使用 HMM 结果进行鉴定")
    else:
        print(f"📚 参考家族蛋白集合数: {len(reference_sets)}")
        for ref in reference_sets:
            print(f"   - {ref['label']}: {Path(ref['path']).name}")
    blast_enabled = bool(reference_sets)
    identify_strategy = "hmm_plus_blast" if blast_enabled else "hmm_only"
    
    target_species_value = args.target_species
    if not target_species_value and scope_data.get("target_species"):
        target_species_value = ",".join(scope_data.get("target_species") or [])
    if not target_species_value and "." in prefix:
        prefix_species = prefix.split(".", 1)[0].strip()
        if prefix_species and prefix_species not in {"Target", "Multi"}:
            target_species_value = prefix_species
            print(f"🎯 Scope 未指定目标物种，已从 prefix 推断: {prefix_species}")
    selected_target_species = {item.strip() for item in str(target_species_value or "").split(",") if item.strip()}
    if selected_target_species:
        print(f"🎯 用户指定目标物种: {', '.join(sorted(selected_target_species))}")
    target_pep_files = find_pep_files(in_dir, ws_root, selected_target_species or None)
    if not target_pep_files:
        raise RuntimeError("未找到目标物种 pep 文件")
    if selected_target_species:
        found_species = {pep_file.stem.replace(".pep", "").split(".")[0] for pep_file in target_pep_files}
        missing_species = sorted(selected_target_species - found_species)
        if missing_species:
            raise RuntimeError("显式指定的目标物种缺少可用全蛋白文件，Identify 未执行: " + ", ".join(missing_species))
    print(f"🎯 目标物种数: {len(target_pep_files)}")
    for f in target_pep_files:
        print(f"   - {f.name}")
    
    print("\n🎯 Pfam获取策略:")
    
    use_user_hmm = False
    query_hmm = None
    required_pfams = None
    pfam_fetch_map: Dict[str, str] = {}
    hmm_models_audit: Dict[str, Any] = {}
    hmm_run_summaries: List[Dict[str, Any]] = []
    hmm_combine_mode = None
    
    user_hmms = find_user_query_hmms(in_dir)
    
    if not user_hmms:
        if not args.required_pfams:
            config_pfams = read_required_pfams_from_config(in_dir, ws_root)
            if config_pfams:
                args.required_pfams = config_pfams
                print(f"✅ 根据 config.xlsx 自动补全 Pfam: {args.required_pfams}")
        if not args.required_pfams:
            inferred_pfam = get_pfam_from_rag(gene_family)
            if inferred_pfam:
                args.required_pfams = inferred_pfam
                print(f"✅ 根据基因家族 {gene_family} 自动补全 Pfam: {args.required_pfams}")
        if args.required_pfams:
            cache_dir = in_dir / "genome_prepare" / "hmm_cache"
            try:
                query_hmm, required_pfams, pfam_fetch_map = build_required_pfam_hmm(
                    args.required_pfams,
                    args.pfam_dir,
                    cache_dir,
                )
                hmm_models_audit = assert_hmm_models_complete(query_hmm, required_pfams, pfam_fetch_map)
            except Exception as exc:
                error_msg = f"Identify Pfam/HMM model preparation failed: {exc}"
                write_failed_identify_manifest(
                    ws_root,
                    prefix,
                    gene_family,
                    in_dir,
                    script_start_time,
                    error_msg,
                    parameters={
                        "required_pfams": _split_pfam_queries(args.required_pfams),
                        "dom_i_evalue_max": args.dom_i_evalue_max,
                        "hmm_cov_min": args.hmm_cov_min,
                        "blast_evalue": args.blast_evalue,
                        "blast_identity_min": args.blast_identity_min,
                        "hmm_combine_mode": args.hmm_combine_mode,
                        "hmm_blast_merge_mode": args.hmm_blast_merge_mode or "union",
                    },
                    hmm_models=hmm_model_audit(cache_dir / f"{'_'.join(_split_pfam_queries(args.required_pfams))}.hmm", _split_pfam_queries(args.required_pfams)) if _split_pfam_queries(args.required_pfams) else {},
                )
                raise RuntimeError(error_msg) from exc
            user_hmms = [query_hmm]
            use_user_hmm = False
            print(f"✅ 未上传HMM，已根据 --required-pfams 构建查询HMM: {query_hmm}")
            print(f"   Pfam映射: {pfam_fetch_map}")
            print(f"   实际加载Pfam: {', '.join(hmm_models_audit.get('loaded_accessions') or [])}")
        else:
            error_data = {
                "error_type": "NO_HMM_FILE_ERROR",
                "stage": "identify",
                "action": "terminate",
                "message": "必须上传至少一个HMM文件，或提供 --required-pfams 并配置 Pfam-A.hmm"
            }
            raise RuntimeError(json.dumps(error_data, ensure_ascii=False))
    
    if False:  # 禁用多HMM选择弹窗，改为单HMM模式
        print(f"⚠️ 发现 {len(user_hmms)} 个HMM文件:")
        for i, hmm in enumerate(user_hmms, 1):
            print(f"   {i}. {hmm.name}")
        
        if args.hmm_combine_mode:
            hmm_combine_mode = args.hmm_combine_mode
            use_user_hmm = True
            print(f"✅ 使用HMM整合模式: {hmm_combine_mode}")
        else:
            pause_manifest = {
                "manifest_version": "1.0",
                "job_id": prefix,
                "stage": "identify",
                "status": "paused",
                "pause_reason": "MULTIPLE_HMM_SELECTION",
                "timestamp": datetime.now().isoformat(),
                "message": f"发现{len(user_hmms)}个HMM文件，需要用户选择整合方式",
                "requires_user_input": True,
                "input_type": "selection",
                "hmm_files": [{"name": h.name, "size": f"{h.stat().st_size//1024}KB"} for h in user_hmms],
                "options": [
                    {"label": "交集（严格筛选）", "value": "intersection", "description": "序列必须同时匹配所有HMM", "recommended": True},
                    {"label": "并集（宽松筛选）", "value": "union", "description": "序列匹配任意HMM即可", "recommended": False}
                ],
                "selection_param": "--hmm-combine-mode",
        "prefix": effective_prefix,
                "workspace": str(ws_root),
                "input_dir": str(in_dir)
            }
            
            final_dir_temp = ws_root / "final"
            final_dir_temp.mkdir(parents=True, exist_ok=True)
            manifest_file = final_dir_temp / f"{prefix}.identify.manifest.json"
            
            with open(manifest_file, 'w', encoding='utf-8') as f:
                json.dump(pause_manifest, f, indent=2, ensure_ascii=False)
            
            print(f"\n⏸️ 任务暂停：发现多个HMM文件，需要用户选择整合方式")
            print(f"   Manifest已写入: {manifest_file}")
            sys.exit(0)
    
    # 多HMM模式：保留所有用户HMM，并按 --hmm-combine-mode 整合
    if len(user_hmms) >= 1 and query_hmm is None:
        print(f"✅ 使用用户上传的HMM文件: {user_hmms[0].name}")
        use_user_hmm = True
        query_hmm = user_hmms[0]
        hmm_name = user_hmms[0].stem
        required_pfams = _split_pfam_queries(args.required_pfams) if args.required_pfams else [hmm_name]
        uploaded_models = [m for hmm in user_hmms for m in parse_hmm_models(hmm)]
        loaded_accessions = sorted({m.get("accession") for m in uploaded_models if m.get("accession")})
        requested_accessions = [q for q in required_pfams if _is_pfam_accession(q)]
        missing_uploaded = sorted(set(requested_accessions) - set(loaded_accessions))
        query_name_to_accession = {
            str(m.get("name")): str(m.get("accession"))
            for m in uploaded_models
            if m.get("name") and m.get("accession")
        }
        hmm_models_audit = {
            "status": "failed" if missing_uploaded else "success",
            "source": "user_uploaded_hmm",
            "requested_pfams": required_pfams,
            "loaded_accessions": loaded_accessions,
            "missing_requested_pfams": missing_uploaded,
            "query_name_to_accession": query_name_to_accession,
            "query_hmms": [str(h.resolve()) for h in user_hmms],
            "models": uploaded_models,
        }
        if missing_uploaded:
            error_msg = f"Uploaded HMM file(s) do not contain requested Pfam model(s): {', '.join(missing_uploaded)}"
            write_failed_identify_manifest(
                ws_root,
                prefix,
                gene_family,
                in_dir,
                script_start_time,
                error_msg,
                parameters={
                    "required_pfams": required_pfams,
                    "dom_i_evalue_max": args.dom_i_evalue_max,
                    "hmm_cov_min": args.hmm_cov_min,
                    "blast_evalue": args.blast_evalue,
                    "hmm_combine_mode": args.hmm_combine_mode,
                    "hmm_blast_merge_mode": args.hmm_blast_merge_mode or "union",
                },
                hmm_models=hmm_models_audit,
            )
            raise RuntimeError(error_msg)
        if len(user_hmms) > 1:
            print(f"   发现 {len(user_hmms)} 个HMM文件，将按 {args.hmm_combine_mode} 模式整合")
        print(f"   HMM名称: {hmm_name}")
    
    # Karpathy: 设置 HMM/BLAST 合并模式默认值
    # 用户未指定时，默认使用并集（union/宽松筛选）
    if not args.hmm_blast_merge_mode:
        args.hmm_blast_merge_mode = "union"
        print(f"\n🔄 未指定HMM/BLAST合并模式，默认使用: {args.hmm_blast_merge_mode} (并集/宽松筛选)")
    else:
        print(f"\n🔄 使用HMM/BLAST合并模式: {args.hmm_blast_merge_mode}")
    
    final_member_files = []
    species_results = []
    
    for pep_file in target_pep_files:
        file_prefix = pep_file.stem.replace(".pep", "")
        species_code = file_prefix.split('.')[0]
        
        print(f"\n{'='*60}")
        print(f"🧬 处理目标物种: {file_prefix}")
        print(f"{'='*60}")
        
        # 修订 2026-04-26: 输出目录改为 identify/{species}/ 结构
        species_workspace = ws_root / "identify" / file_prefix
        species_workspace.mkdir(parents=True, exist_ok=True)
        
        paths = prepare_directories(species_workspace)
        
        start_time = datetime.now()
        
        # 修订 2026-04-28: 多HMM支持 - 每个HMM分别运行，根据参数取交集或并集
        hmm_ids_files = []
        
        for i, hmm_file in enumerate(user_hmms, 1):
            hmm_name = hmm_file.stem
            hmm_prefix = f"{file_prefix}.{hmm_name}"
            
            print(f"\n  [{i}/{len(user_hmms)}] 运行HMM: {hmm_file.name}")
            
            hmm_domtblout, hmm_ids_file = run_hmm_analysis(
                input_dir=in_dir,
                output_dir=species_workspace,
                prefix=hmm_prefix,
                hmm_file=hmm_file,
                pep_file=pep_file,
                cpu=args.cpu,
                evalue=args.dom_i_evalue_max
            )
            per_model_hits = summarize_domtblout_by_model(
                hmm_domtblout,
                args.dom_i_evalue_max,
                hmm_models_audit.get("query_name_to_accession") if isinstance(hmm_models_audit, dict) else {},
            )
            hmm_run_summaries.append({
                "species": species_code,
                "hmm_file": str(hmm_file.resolve()),
                "domtblout": str(hmm_domtblout.resolve()),
                "ids_file": str(hmm_ids_file.resolve()),
                "members": len(read_id_list(hmm_ids_file)),
                "per_model_hits": per_model_hits,
            })
            
            hmm_ids_files.append(hmm_ids_file)
        
        # 取所有HMM结果的交集或并集（根据参数）
        if len(hmm_ids_files) > 1:
            # 读取所有HMM的IDs
            hmm_id_sets = []
            for ids_file in hmm_ids_files:
                with open(ids_file, 'r') as f:
                    ids = set(line.strip() for line in f if line.strip())
                    hmm_id_sets.append(ids)
            
            # 根据参数决定取交集还是并集
            if args.hmm_combine_mode == 'intersection':
                print(f"\n🔄 取 {len(hmm_ids_files)} 个HMM结果的交集:")
                for i, ids in enumerate(hmm_id_sets):
                    print(f"   HMM {i+1}: {len(ids)} 个基因")
                final_hmm_ids = set.intersection(*hmm_id_sets)
                print(f"   交集结果: {len(final_hmm_ids)} 个基因")
            else:  # union (默认)
                print(f"\n🔄 取 {len(hmm_ids_files)} 个HMM结果的并集:")
                for i, ids in enumerate(hmm_id_sets):
                    print(f"   HMM {i+1}: {len(ids)} 个基因")
                final_hmm_ids = set.union(*hmm_id_sets)
                print(f"   并集结果: {len(final_hmm_ids)} 个基因")
            
            # 输出结果到 analysis/{species}.{gene_family}.hmm.ids
            analysis_dir = species_workspace / "analysis"
            hmm_ids_file = analysis_dir / f"{file_prefix}.hmm.ids"
            with open(hmm_ids_file, 'w') as f:
                for gene_id in sorted(final_hmm_ids):
                    f.write(f"{gene_id}\n")
            
            print(f"   输出: {hmm_ids_file.name}")
        
        blast_result = run_multi_reference_blast_analysis(
            input_dir=in_dir,
            output_dir=species_workspace,
            prefix=file_prefix,
            references=reference_sets,
            pep_file=pep_file,
            cpu=args.cpu,
            evalue=args.blast_evalue,
            identity_min=args.blast_identity_min,
        ) if reference_sets else {
            "status": "skipped",
            "references": [],
            "successful": [],
            "failed": [],
            "ids_file": "",
            "source_matrix": "",
            "members": 0,
            "source_by_gene": {},
            "target_db": {},
            "blast_database_role": "target_proteome",
            "blast_query_role": "reference_family_pep",
            "blast_candidate_id_column": "sseqid",
        }
        blast_ids_file = Path(blast_result["ids_file"]) if blast_result.get("ids_file") else None
        
        # 修订 2026-05-18/06-13: BLAST参考可选；多个参考家族蛋白逐路运行后按target gene取并集。
        with open(hmm_ids_file, 'r') as f:
            hmm_ids = set(line.strip() for line in f if line.strip())
        blast_ids = read_ids_from_file(blast_ids_file) if blast_ids_file else set()
        species_hmm_members = len(hmm_ids)
        species_blast_members = len(blast_ids)
        
        # 根据用户选择的模式合并结果
        if args.hmm_blast_merge_mode == "intersection":
            final_ids = (hmm_ids & blast_ids) if blast_ids else hmm_ids
            print(f"\n🔄 使用交集模式: {len(final_ids)} 个基因" + ("（无BLAST时退回HMM）" if not blast_ids else ""))
        else:  # union
            final_ids = hmm_ids | blast_ids
            print(f"\n🔄 使用并集模式: {len(final_ids)} 个基因")
        
        # 写入合并后的结果文件
        combined_ids_file = species_workspace / "combine" / f"{file_prefix}.combined.ids"
        combined_ids_file.parent.mkdir(parents=True, exist_ok=True)
        with open(combined_ids_file, 'w') as f:
            for gene_id in sorted(final_ids):
                f.write(f"{gene_id}\n")
        
        final_dir = species_workspace / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        
        final_ids_file = final_dir / f"{file_prefix}.final_gfmembers.id"
        with open(final_ids_file, 'w') as f:
            for gene_id in final_ids:
                f.write(f"{gene_id}\n")
        
        final_pep_file = final_dir / f"{file_prefix}.final_gfmembers.pep.fa"
        write_fasta_subset(pep_file, final_ids, final_pep_file)
        
        final_member_files.append(final_pep_file)
        
        species_results.append({
            "species": species_code,
            "file_prefix": file_prefix,
            "input_file": str(pep_file.name),
            "final_members": len(final_ids),
            "hmm_members": species_hmm_members,
            "blast_members": species_blast_members,
            "blast_reference_count": len(blast_result.get("successful") or []),
            "blast_result": {
                "status": blast_result.get("status"),
                "ids_file": str(Path(blast_result["ids_file"]).relative_to(ws_root)) if blast_result.get("ids_file") else "",
                "source_matrix": str(Path(blast_result["source_matrix"]).relative_to(ws_root)) if blast_result.get("source_matrix") else "",
                "successful": [
                    {k: v for k, v in item.items() if k not in {"ids", "evidence_by_gene"}}
                    for item in (blast_result.get("successful") or [])
                ],
                "failed": blast_result.get("failed") or [],
                "target_db": blast_result.get("target_db") or {},
                "blast_database_role": blast_result.get("blast_database_role") or "target_proteome",
                "blast_query_role": blast_result.get("blast_query_role") or "reference_family_pep",
                "blast_candidate_id_column": blast_result.get("blast_candidate_id_column") or "sseqid",
            },
            "candidate_members": len(final_ids),
            "final_pep_file": str(final_pep_file.relative_to(ws_root)),
            "final_id_file": str(final_ids_file.relative_to(ws_root)),
        })
        
        print(f"✅ {file_prefix}: {len(final_ids)} 个成员")
    
    final_dir = ws_root / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    
    # Use role-aware target naming for target-scope identify outputs.  Older
    # Multi.* names were ambiguous once target/reference/comparative scopes
    # became explicit.
    target_prefix = f"Target.{gene_family}"
    legacy_multi_prefix = f"Multi.{gene_family}"
    effective_prefix = prefix or target_prefix
    if effective_prefix == legacy_multi_prefix:
        effective_prefix = target_prefix

    single_species_artifacts = []
    for r in species_results:
        species = r["species"]
        single_prefix = species_prefix_for(species, gene_family)
        compat_prefix = f"Target.{species}.{gene_family}"
        source_pep = ws_root / r["final_pep_file"]
        source_id = ws_root / r["final_id_file"]
        target_pep = final_dir / f"{single_prefix}.identified_members.pep.fa"
        target_id = final_dir / f"{single_prefix}.identified_members.id"
        compat_pep = final_dir / f"{compat_prefix}.identified_members.pep.fa"
        compat_id = final_dir / f"{compat_prefix}.identified_members.id"
        if source_pep.exists():
            copy2_if_different(source_pep, target_pep)
            if compat_pep != target_pep:
                copy2_if_different(source_pep, compat_pep)
        if source_id.exists():
            copy2_if_different(source_id, target_id)
            if compat_id != target_id:
                copy2_if_different(source_id, compat_id)
        r["canonical_prefix"] = single_prefix
        r["compat_prefix"] = compat_prefix
        r["final_pep_file_in_final"] = f"final/{target_pep.name}"
        r["final_id_file_in_final"] = f"final/{target_id.name}"
        r["compat_pep_file_in_final"] = f"final/{compat_pep.name}"
        r["compat_id_file_in_final"] = f"final/{compat_id.name}"
        single_species_artifacts.append({
            "species": species,
            "prefix": single_prefix,
            "compat_prefix": compat_prefix,
            "protein_fasta": target_pep,
            "id_list": target_id,
            "compat_protein_fasta": compat_pep,
            "compat_id_list": compat_id,
            "members": r["final_members"],
        })

    single_target_species = ""
    if len(species_results) == 1:
        single_target_species = str(species_results[0].get("species") or "").strip()

    merged_pep_file = final_dir / f"{effective_prefix}.identified_members.pep.fa"
    merge_final_members(final_member_files, merged_pep_file)
    merged_id_file = final_dir / f"{effective_prefix}.identified_members.id"
    merged_id_count = write_fasta_ids(merged_pep_file, merged_id_file)
    interproscan_tsv = final_dir / f"{effective_prefix}.identified_members.interproscan.tsv"
    interproscan_result = run_interproscan_domain_check(merged_pep_file, interproscan_tsv)
    interproscan_status_file = final_dir / f"{effective_prefix}.interproscan_domain_check.txt"
    write_interproscan_status_file(interproscan_status_file, interproscan_result)
    domain_review_tsv = final_dir / f"{effective_prefix}.domain_review.tsv"
    domain_review_result = write_domain_review_from_interproscan(
        interproscan_tsv,
        required_pfams or [],
        domain_review_tsv,
        species="merged",
    )
    for r in species_results:
        species = r["species"]
        species_prefix = species_prefix_for(species, gene_family)
        compat_species_prefix = f"Target.{species}.{gene_family}"
        source_id = ws_root / r["final_id_file"]
        species_original_ids = read_id_list(source_id)
        species_review_tsv = final_dir / f"{species_prefix}.domain_review.tsv"
        species_review_result = write_domain_review_from_interproscan(
            interproscan_tsv,
            required_pfams or [],
            species_review_tsv,
            species=species,
            id_filter=set(species_original_ids),
        )
        r["domain_review_file_in_final"] = f"final/{species_review_tsv.name}"
        r["domain_review"] = {
            "status": species_review_result.get("status"),
            "rows": species_review_result.get("rows", 0),
            "default_keep_count": species_review_result.get("default_keep_count", 0),
        }
    default_domain_ids = domain_review_result.get("default_keep_ids", []) if domain_review_result.get("status") == "success" else []
    if default_domain_ids:
        curated_default_id = final_dir / f"{effective_prefix}.curated_members.id"
        curated_default_pep = final_dir / f"{effective_prefix}.curated_members.pep.fa"
        write_id_list(default_domain_ids, curated_default_id)
        candidate_pep_file = merged_pep_file.with_name(f"{effective_prefix}.candidate_members.pep.fa")
        candidate_id_file = merged_id_file.with_name(f"{effective_prefix}.candidate_members.id")
        if merged_pep_file.exists():
            copy2_if_different(merged_pep_file, candidate_pep_file)
        if merged_id_file.exists():
            copy2_if_different(merged_id_file, candidate_id_file)
        write_fasta_subset(candidate_pep_file, default_domain_ids, curated_default_pep)
        write_fasta_subset(candidate_pep_file, default_domain_ids, merged_pep_file)
        merged_id_count = write_id_list(default_domain_ids, merged_id_file)
        interproscan_result["default_domain_filter"] = {
            "enabled": True,
            "required_domains": required_pfams or [],
            "candidate_members": len(read_id_list(candidate_id_file)),
            "kept_members": merged_id_count,
            "curated_id_file": str(curated_default_id.resolve()),
            "candidate_pep_file": str(candidate_pep_file.resolve()),
        }
        print(f"✅ 默认结构域过滤: {merged_id_count} 个成员保留（required domains: {','.join(required_pfams or [])}）")
        for r in species_results:
            species = r["species"]
            species_prefix = species_prefix_for(species, gene_family)
            compat_species_prefix = f"Target.{species}.{gene_family}"
            source_pep = ws_root / r["final_pep_file"]
            source_id = ws_root / r["final_id_file"]
            species_original_ids = read_id_list(source_id)
            species_default_ids = [seq_id for seq_id in species_original_ids if seq_id in set(default_domain_ids)]
            target_pep = final_dir / f"{species_prefix}.identified_members.pep.fa"
            target_id = final_dir / f"{species_prefix}.identified_members.id"
            target_curated_pep = final_dir / f"{species_prefix}.curated_members.pep.fa"
            target_curated_id = final_dir / f"{species_prefix}.curated_members.id"
            compat_pep = final_dir / f"{compat_species_prefix}.identified_members.pep.fa"
            compat_id = final_dir / f"{compat_species_prefix}.identified_members.id"
            compat_curated_pep = final_dir / f"{compat_species_prefix}.curated_members.pep.fa"
            compat_curated_id = final_dir / f"{compat_species_prefix}.curated_members.id"
            write_id_list(species_default_ids, target_id)
            write_id_list(species_default_ids, target_curated_id)
            write_fasta_subset(source_pep, species_default_ids, target_pep)
            write_fasta_subset(source_pep, species_default_ids, target_curated_pep)
            if compat_pep != target_pep:
                copy2_if_different(target_pep, compat_pep)
            if compat_id != target_id:
                copy2_if_different(target_id, compat_id)
            if compat_curated_pep != target_curated_pep:
                copy2_if_different(target_curated_pep, compat_curated_pep)
            if compat_curated_id != target_curated_id:
                copy2_if_different(target_curated_id, compat_curated_id)
            if single_target_species and species == single_target_species:
                copy2_if_different(target_pep, merged_pep_file)
                copy2_if_different(target_id, merged_id_file)
                write_id_list(species_default_ids, curated_default_id)
                copy2_if_different(target_curated_pep, curated_default_pep)
                merged_id_count = len(species_default_ids)
                interproscan_result.setdefault("default_domain_filter", {})["single_target_compat_sync"] = {
                    "species": species,
                    "target_prefix": species_prefix,
                    "legacy_target_prefix": compat_species_prefix,
                    "compat_prefix": effective_prefix,
                    "members": merged_id_count,
                }
            r["final_members_before_domain_filter"] = r["final_members"]
            r["final_members"] = len(species_default_ids)
    elif interproscan_result.get("status") == "success" and required_pfams:
        warning_msg = "InterProScan完成，但未找到命中目标Pfam的成员；保留HMM/BLAST候选结果，需人工检查domain_review.tsv。"
        print(f"⚠️  {warning_msg}")
        interproscan_result["default_domain_filter"] = {
            "enabled": False,
            "required_domains": required_pfams or [],
            "reason": warning_msg,
        }

    hmm_models_audit = dict(hmm_models_audit or {})
    hmm_models_audit.setdefault("status", "success")
    hmm_models_audit["run_summaries"] = hmm_run_summaries
    merged_per_model_hits: Dict[str, int] = {}
    for item in hmm_run_summaries:
        for key, value in (item.get("per_model_hits") or {}).items():
            merged_per_model_hits[key] = merged_per_model_hits.get(key, 0) + int(value or 0)
    hmm_models_audit["per_model_hits"] = merged_per_model_hits
    missing_hmm_models = hmm_models_audit.get("missing_requested_pfams") or []
    if missing_hmm_models:
        raise RuntimeError(f"Identify HMM audit failed, missing requested Pfam model(s): {', '.join(missing_hmm_models)}")

    candidate_members_count = sum(int(r.get("final_members_before_domain_filter", r.get("candidate_members", r.get("final_members", 0)))) for r in species_results)
    hmm_members_count = sum(int(r.get("hmm_members", 0)) for r in species_results)
    blast_members_count = sum(int(r.get("blast_members", 0)) for r in species_results)
    final_members_count = sum(int(r.get("final_members", 0)) for r in species_results)

    hmm_audit_file = final_dir / f"{effective_prefix}.hmm_model_audit.txt"
    audit_lines = [
        f"status: {hmm_models_audit.get('status', 'unknown')}",
        "requested_pfams: " + ",".join(hmm_models_audit.get("requested_pfams") or []),
        "loaded_accessions: " + ",".join(hmm_models_audit.get("loaded_accessions") or []),
        "missing_requested_pfams: " + ",".join(missing_hmm_models),
        f"query_hmm: {hmm_models_audit.get('query_hmm') or ';'.join(hmm_models_audit.get('query_hmms') or [])}",
        "",
        "per_model_hits:",
    ]
    for key, value in sorted(merged_per_model_hits.items()):
        audit_lines.append(f"- {key}: {value}")
    hmm_audit_file.write_text("\n".join(audit_lines) + "\n", encoding="utf-8")
    
    manifest_data = {
        "manifest_version": "1.0",
        "job_id": ws_root.name,
        "run_id": f"identify_{script_start_time:%Y%m%d_%H%M%S}",
        "stage": "identify",
        "prefix": effective_prefix,
        "output_prefix": effective_prefix,
        "canonical_member_prefix": species_results[0].get("canonical_prefix") if len(species_results) == 1 else "",
        "analysis_route": "single_species" if len(species_results) == 1 else "multi_target",
        "identify_strategy": identify_strategy,
        "blast_enabled": blast_enabled,
        "result_group": "gene_family_identify",
        "gene_family": gene_family,
        "status": "success",
        "timestamp": datetime.now().isoformat(),
        "start_time": script_start_time.isoformat(),
        "end_time": datetime.now().isoformat(),
        "elapsed_sec": (datetime.now() - script_start_time).total_seconds(),
        "target_species": scope_data.get("target_species") or [r["species"] for r in species_results],
        "reference_species": scope_data.get("reference_species") or (reference_policy.get("primary_reference_species") or []),
        "comparative_species": scope_data.get("comparative_species") or [],
        "selected_species": scope_data.get("selected_species") or [r["species"] for r in species_results],
        "scope_id": scope_data.get("scope_id", ""),
        "scope_mode": scope_data.get("scope_mode", ""),
        "scope_file": str(Path(args.scope_file).resolve()) if args.scope_file else "",
        "scope": {
            "family": scope_data.get("family", gene_family),
            "prefix": scope_data.get("prefix", effective_prefix),
            "scope_id": scope_data.get("scope_id", ""),
            "scope_mode": scope_data.get("scope_mode", ""),
            "target_species": scope_data.get("target_species") or [r["species"] for r in species_results],
            "reference_species": scope_data.get("reference_species") or (reference_policy.get("primary_reference_species") or []),
            "comparative_species": scope_data.get("comparative_species") or [],
            "selected_species": scope_data.get("selected_species") or [r["species"] for r in species_results],
        },
        "species_count": len(species_results),
        "species_results": species_results,
        "reference_sets": [
            {k: v for k, v in ref.items() if k != "path"} | {"path": str(Path(ref["path"]).resolve())}
            for ref in reference_sets
        ],
        "primary_reference_species": reference_policy.get("primary_reference_species") or (scope_data.get("reference_species") or []),
        "identify_blast_reference_species": reference_policy.get("identify_blast_reference_species") or [str(ref.get("species") or ref.get("label")) for ref in reference_sets],
        "reference_policy_warnings": reference_policy.get("warnings") or [],
        "workspace_root": str(ws_root.resolve()),
        "workspace": str(final_dir.resolve()),
        "input_dir": str(in_dir.resolve()),
        "tool_versions": tool_versions,
        "cmd": sys.argv,
        "inputs": inputs_info,
        "platform": platform.platform(),
        "python_version": sys.version.split()[0],
        "parameters": {
            "required_pfams": required_pfams or [],
            "dom_i_evalue_max": args.dom_i_evalue_max,
            "hmm_cov_min": args.hmm_cov_min,
            "blast_evalue": args.blast_evalue,
            "cut_ga": bool(args.cut_ga),
            "cpu": args.cpu,
            "hmm_combine_mode": args.hmm_combine_mode,
            "hmm_blast_merge_mode": args.hmm_blast_merge_mode or "union",
            "identify_strategy": identify_strategy,
            "blast_enabled": blast_enabled,
            "blast_reference_mode": "per_reference_union",
            "blast_database_role": "target_proteome",
            "blast_query_role": "reference_family_pep",
            "blast_candidate_id_column": "sseqid",
            "blast_reference_sets": [str(ref.get("label")) for ref in reference_sets],
            "identify_blast_reference_species": reference_policy.get("identify_blast_reference_species") or [str(ref.get("species") or ref.get("label")) for ref in reference_sets],
            "primary_reference_species": reference_policy.get("primary_reference_species") or (scope_data.get("reference_species") or []),
        },
        "blast_method": {
            "database_role": "target_proteome",
            "query_role": "reference_family_pep",
            "candidate_id_column": "sseqid",
            "reference_mode": "per_reference_union",
        },
        "metrics": {
            "total_proteins": sum(count_fasta_records(p) for p in target_pep_files),
            "hmm_members": hmm_members_count,
            "blast_members": blast_members_count,
            "blast_enabled": blast_enabled,
            "identify_strategy": identify_strategy,
            "blast_reference_sets": len(reference_sets),
            "candidate_members": candidate_members_count,
            "identified_members_count": final_members_count,
            "final_members": final_members_count,
        },
        "hmm_models": hmm_models_audit,
        "domain_check": {"interproscan": interproscan_result, "review": domain_review_result},
        "warnings": [
            {"step": "reference_policy", "message": warning}
            for warning in (reference_policy.get("warnings") or [])
        ],
        "artifacts": {}
    }
    
    for r in species_results:
        species = r["species"]
        final_pep_path = ws_root / r["final_pep_file"]
        final_id_path = ws_root / r["final_id_file"]
        final_pep_in_final = Path(r.get("final_pep_file_in_final", ""))
        final_id_in_final = Path(r.get("final_id_file_in_final", ""))
        compat_pep_in_final = Path(r.get("compat_pep_file_in_final", ""))
        compat_id_in_final = Path(r.get("compat_id_file_in_final", ""))
        manifest_data["artifacts"][f"{species}_identified_members"] = {
            "artifact": f"{species}_identified_members",
            "file": f"{r['file_prefix']}.final_gfmembers.pep.fa",
            "full_path": str(final_pep_path.resolve()),
            "relpath_or_hint": r["final_pep_file"],
            "type": "fasta",
            "species": species,
            "members": r["final_members"]
        }
        final_species_pep = final_dir / final_pep_in_final.name if final_pep_in_final.name else None
        final_species_id = final_dir / final_id_in_final.name if final_id_in_final.name else None
        if final_species_pep and final_species_pep.exists():
            manifest_data["artifacts"][f"target_{species}_identified_members_pep"] = {
                "artifact": f"target_{species}_identified_members_pep",
                "file": final_species_pep.name,
                "full_path": str(final_species_pep.resolve()),
                "relpath_or_hint": f"final/{final_species_pep.name}",
                "type": "fasta",
                "role": "target",
                "canonical_prefix": r.get("canonical_prefix"),
                "species": species,
                "members": r["final_members"],
                "description": "Single target species identified protein FASTA",
            }
        if final_species_id and final_species_id.exists():
            manifest_data["artifacts"][f"target_{species}_identified_members_id"] = {
                "artifact": f"target_{species}_identified_members_id",
                "file": final_species_id.name,
                "full_path": str(final_species_id.resolve()),
                "relpath_or_hint": f"final/{final_species_id.name}",
                "type": "id_list",
                "role": "target",
                "canonical_prefix": r.get("canonical_prefix"),
                "species": species,
                "members": r["final_members"],
                "description": "Single target species identified member IDs",
            }
        compat_species_pep = final_dir / compat_pep_in_final.name if compat_pep_in_final.name else None
        compat_species_id = final_dir / compat_id_in_final.name if compat_id_in_final.name else None
        if compat_species_pep and compat_species_pep.exists():
            manifest_data["artifacts"][f"target_{species}_identified_members_pep_compat"] = {
                "artifact": f"target_{species}_identified_members_pep_compat",
                "file": compat_species_pep.name,
                "full_path": str(compat_species_pep.resolve()),
                "relpath_or_hint": f"final/{compat_species_pep.name}",
                "type": "fasta",
                "role": "target",
                "species": species,
                "canonical_prefix": r.get("canonical_prefix"),
                "description": "Target-prefixed identified member compatibility copy",
            }
        if compat_species_id and compat_species_id.exists():
            manifest_data["artifacts"][f"target_{species}_identified_members_id_compat"] = {
                "artifact": f"target_{species}_identified_members_id_compat",
                "file": compat_species_id.name,
                "full_path": str(compat_species_id.resolve()),
                "relpath_or_hint": f"final/{compat_species_id.name}",
                "type": "id_list",
                "role": "target",
                "species": species,
                "canonical_prefix": r.get("canonical_prefix"),
                "description": "Target-prefixed identified member compatibility copy",
            }
        species_review = final_dir / Path(r.get("domain_review_file_in_final", "")).name if r.get("domain_review_file_in_final") else None
        if species_review and species_review.exists():
            manifest_data["artifacts"][f"target_{species}_domain_review"] = {
                "artifact": f"target_{species}_domain_review",
                "file": species_review.name,
                "full_path": str(species_review.resolve()),
                "relpath_or_hint": f"final/{species_review.name}",
                "type": "tsv",
                "role": "target",
                "species": species,
                "description": "Species-specific Pfam/CDD domain review table for identify member confirmation",
                "summary": r.get("domain_review", {}),
            }
    
    for r in species_results:
        blast_result = r.get("blast_result") or {}
        source_matrix = blast_result.get("source_matrix")
        if source_matrix:
            source_matrix_path = ws_root / source_matrix
            if source_matrix_path.exists():
                manifest_data["artifacts"][f"{r['species']}_blast_source_matrix"] = {
                    "artifact": f"{r['species']}_blast_source_matrix",
                    "file": source_matrix_path.name,
                    "full_path": str(source_matrix_path.resolve()),
                    "relpath_or_hint": source_matrix,
                    "type": "tsv",
                    "species": r["species"],
                    "description": "Per-target candidate support from reference family protein queries against the target whole-proteome BLAST database",
                    "blast_database_role": "target_proteome",
                    "blast_query_role": "reference_family_pep",
                    "blast_candidate_id_column": "sseqid",
                    "size": source_matrix_path.stat().st_size,
                }
        ids_file = blast_result.get("ids_file")
        if ids_file:
            ids_path = ws_root / ids_file
            if ids_path.exists():
                manifest_data["artifacts"][f"{r['species']}_blast_results"] = {
                    "artifact": f"{r['species']}_blast_results",
                    "file": ids_path.name,
                    "full_path": str(ids_path.resolve()),
                    "relpath_or_hint": ids_file,
                    "type": "id_list",
                    "species": r["species"],
                    "members": r.get("blast_members", 0),
                    "description": "Union of per-reference BLAST candidate target IDs parsed from outfmt6 sseqid",
                    "blast_database_role": "target_proteome",
                    "blast_query_role": "reference_family_pep",
                    "blast_candidate_id_column": "sseqid",
                    "size": ids_path.stat().st_size,
                }
        for item in blast_result.get("successful") or []:
            blast_output = item.get("blast_output")
            if not blast_output:
                continue
            blast_path = Path(blast_output)
            if not blast_path.exists():
                continue
            label = sanitize_token(str(item.get("reference_label") or "reference"))
            manifest_data["artifacts"][f"{r['species']}_blast_{label}_out"] = {
                "artifact": f"{r['species']}_blast_{label}_out",
                "file": blast_path.name,
                "full_path": str(blast_path.resolve()),
                "relpath_or_hint": str(blast_path.relative_to(ws_root)) if blast_path.is_relative_to(ws_root) else str(blast_path),
                "type": "blast_outfmt6",
                "species": r["species"],
                "reference_label": label,
                "members": item.get("members", 0),
                "description": "Raw BLAST outfmt 6 output: reference family protein query against target whole-proteome database",
                "query_file": item.get("query_file"),
                "query_role": item.get("query_role") or "reference_family_pep",
                "target_database_input": item.get("target_database_input"),
                "target_database_role": item.get("target_database_role") or "target_proteome",
                "target_db_prefix": item.get("target_db_prefix"),
                "target_database_files": item.get("target_database_files") or [],
                "candidate_id_column": item.get("candidate_id_column") or "sseqid",
                "size": blast_path.stat().st_size,
            }

    manifest_data["artifacts"]["hmm_model_audit"] = {
        "artifact": "hmm_model_audit",
        "file": hmm_audit_file.name,
        "full_path": str(hmm_audit_file.resolve()),
        "relpath_or_hint": f"final/{hmm_audit_file.name}",
        "type": "txt",
        "size": hmm_audit_file.stat().st_size if hmm_audit_file.exists() else 0,
        "description": "Audit of requested versus loaded HMM/Pfam models for identify",
        "summary": {
            "requested_pfams": hmm_models_audit.get("requested_pfams") or [],
            "loaded_accessions": hmm_models_audit.get("loaded_accessions") or [],
            "missing_requested_pfams": hmm_models_audit.get("missing_requested_pfams") or [],
            "per_model_hits": hmm_models_audit.get("per_model_hits") or {},
        },
    }
    query_hmm_path = hmm_models_audit.get("query_hmm")
    if query_hmm_path and Path(str(query_hmm_path)).exists():
        query_hmm_artifact = Path(str(query_hmm_path))
        manifest_data["artifacts"]["query_hmm"] = {
            "artifact": "query_hmm",
            "file": query_hmm_artifact.name,
            "full_path": str(query_hmm_artifact.resolve()),
            "relpath_or_hint": str(query_hmm_artifact.resolve()),
            "type": "hmm",
            "size": query_hmm_artifact.stat().st_size,
            "description": "Merged query HMM used by identify",
        }

    manifest_data["artifacts"]["identified_members"] = {
        "artifact": "identified_members",
        "file": merged_pep_file.name,
        "full_path": str(merged_pep_file.resolve()),
        "relpath_or_hint": f"final/{merged_pep_file.name}",
        "type": "fasta",
        "size": merged_pep_file.stat().st_size if merged_pep_file.exists() else 0,
        "merged_from": [r["file_prefix"] for r in species_results]
    }
    manifest_data["artifacts"]["identified_members_id"] = {
        "artifact": "identified_members_id",
        "file": merged_id_file.name,
        "full_path": str(merged_id_file.resolve()),
        "relpath_or_hint": f"final/{merged_id_file.name}",
        "type": "id_list",
        "size": merged_id_file.stat().st_size if merged_id_file.exists() else 0,
        "members": merged_id_count,
        "derived_from": merged_pep_file.name
    }
    if domain_review_tsv.exists():
        manifest_data["artifacts"]["domain_review"] = {
            "artifact": "domain_review",
            "file": domain_review_tsv.name,
            "full_path": str(domain_review_tsv.resolve()),
            "relpath_or_hint": f"final/{domain_review_tsv.name}",
            "type": "tsv",
            "size": domain_review_tsv.stat().st_size,
            "derived_from": interproscan_tsv.name,
            "description": "Pfam/CDD domain review table for identify member confirmation; default_keep marks selected family domains",
            "summary": {
                "rows": domain_review_result.get("rows", 0),
                "proteins_with_hits": domain_review_result.get("proteins_with_hits", 0),
                "default_keep_count": domain_review_result.get("default_keep_count", 0),
                "required_domains": required_pfams or [],
            },
        }
    curated_default_id = final_dir / f"{effective_prefix}.curated_members.id"
    if curated_default_id.exists():
        manifest_data["artifacts"]["curated_members_id"] = {
            "artifact": "curated_members_id",
            "file": curated_default_id.name,
            "full_path": str(curated_default_id.resolve()),
            "relpath_or_hint": f"final/{curated_default_id.name}",
            "type": "id_list",
            "size": curated_default_id.stat().st_size,
            "description": "Default curated ID list generated from required family domain hits",
        }
    curated_default_pep = final_dir / f"{effective_prefix}.curated_members.pep.fa"
    if curated_default_pep.exists():
        manifest_data["artifacts"]["curated_members"] = {
            "artifact": "curated_members",
            "file": curated_default_pep.name,
            "full_path": str(curated_default_pep.resolve()),
            "relpath_or_hint": f"final/{curated_default_pep.name}",
            "type": "fasta",
            "size": curated_default_pep.stat().st_size,
            "description": "Default curated protein FASTA generated from required family domain hits; used by subfamily evidence route before final confirmation",
        }
    candidate_pep_file = final_dir / f"{effective_prefix}.candidate_members.pep.fa"
    candidate_id_file = final_dir / f"{effective_prefix}.candidate_members.id"
    if candidate_pep_file.exists():
        manifest_data["artifacts"]["candidate_members"] = {
            "artifact": "candidate_members",
            "file": candidate_pep_file.name,
            "full_path": str(candidate_pep_file.resolve()),
            "relpath_or_hint": f"final/{candidate_pep_file.name}",
            "type": "fasta",
            "size": candidate_pep_file.stat().st_size,
            "description": "Pre-domain-review candidate member FASTA from HMM/BLAST",
        }
    if candidate_id_file.exists():
        manifest_data["artifacts"]["candidate_members_id"] = {
            "artifact": "candidate_members_id",
            "file": candidate_id_file.name,
            "full_path": str(candidate_id_file.resolve()),
            "relpath_or_hint": f"final/{candidate_id_file.name}",
            "type": "id_list",
            "size": candidate_id_file.stat().st_size,
            "description": "Pre-domain-review candidate member IDs from HMM/BLAST",
        }
    if interproscan_result.get("status") == "success":
        manifest_data["artifacts"]["interproscan_domain_check"] = {
            "artifact": "interproscan_domain_check",
            "file": interproscan_tsv.name,
            "full_path": str(interproscan_tsv.resolve()),
            "relpath_or_hint": f"final/{interproscan_tsv.name}",
            "type": "tsv",
            "size": interproscan_tsv.stat().st_size if interproscan_tsv.exists() else 0,
            "derived_from": merged_pep_file.name,
            "description": "InterProScan TSV for domain-checking identified members",
            "summary": interproscan_result.get("summary", {}),
        }
    else:
        manifest_data["warnings"].append({
            "step": "interproscan_domain_check",
            "status": interproscan_result.get("status"),
            "reason": interproscan_result.get("reason", "InterProScan did not produce a TSV")
        })
    manifest_data["artifacts"]["interproscan_domain_check_status"] = {
        "artifact": "interproscan_domain_check_status",
        "file": interproscan_status_file.name,
        "full_path": str(interproscan_status_file.resolve()),
        "relpath_or_hint": f"final/{interproscan_status_file.name}",
        "type": "txt",
        "size": interproscan_status_file.stat().st_size if interproscan_status_file.exists() else 0,
        "status": interproscan_result.get("status"),
        "derived_from": merged_pep_file.name,
        "description": "Readable InterProScan domain-check status for frontend result review",
    }
    clean_input_value = interproscan_result.get("clean_input")
    clean_input = Path(clean_input_value) if clean_input_value else None
    if clean_input and clean_input.exists():
        manifest_data["artifacts"]["interproscan_clean_input"] = {
            "artifact": "interproscan_clean_input",
            "file": clean_input.name,
            "full_path": str(clean_input.resolve()),
            "relpath_or_hint": f"final/{clean_input.name}",
            "type": "fasta",
            "size": clean_input.stat().st_size,
            "derived_from": merged_pep_file.name,
            "description": "Final member FASTA sanitized for InterProScan by removing '*' stop markers",
            "sanitize": interproscan_result.get("sanitize", {}),
        }
    
    species_manifest_files = []
    for r in species_results:
        species = r["species"]
        canonical_prefix = r.get("canonical_prefix") or species_prefix_for(species, gene_family)
        species_manifest = dict(manifest_data)
        species_manifest["prefix"] = canonical_prefix
        species_manifest["output_prefix"] = canonical_prefix
        species_manifest["canonical_member_prefix"] = canonical_prefix
        species_manifest["species"] = species
        species_manifest["species_role"] = "target"
        species_manifest["target_species"] = [species]
        species_manifest["selected_species"] = [species]
        species_manifest["species_count"] = 1
        species_manifest["species_results"] = [r]
        species_manifest["legacy_output_prefix"] = r.get("compat_prefix") or f"Target.{species}.{gene_family}"
        species_manifest["artifacts"] = dict(manifest_data.get("artifacts") or {})
        species_manifest_file = final_dir / f"{canonical_prefix}.identify.manifest.json"
        species_manifest_files.append(species_manifest_file)
        write_manifest(species_manifest_file, species_manifest)

    scope_manifest_file = None
    if scope_data.get("scope_id"):
        scope_manifest_file = final_dir / f"{effective_prefix}.{sanitize_token(scope_data['scope_id'])}.identify.manifest.json"
        manifest_data["scope_manifest"] = str(scope_manifest_file.resolve())

    # Write a non-hidden manifest name even when the submitted prefix is empty.
    manifest_file = final_dir / f"{effective_prefix}.identify.manifest.json"
    write_manifest(manifest_file, manifest_data)
    if scope_manifest_file:
        write_manifest(scope_manifest_file, manifest_data)
    
    print(f"\n🎉 Identify 阶段完成!")
    print(f"  处理物种数: {len(species_results)}")
    print(f"  总成员数: {sum(r['final_members'] for r in species_results)}")
    print(f"  合并文件: {merged_pep_file}")
    for item in single_species_artifacts:
        print(f"  单物种主文件: {item['protein_fasta']}")
        print(f"  兼容Target文件: {item['compat_protein_fasta']}")
    print(f"  Manifest: {manifest_file}")
    if scope_manifest_file:
        print(f"  Scope manifest: {scope_manifest_file}")


if __name__ == "__main__":
    main()
