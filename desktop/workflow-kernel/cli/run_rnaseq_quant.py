#!/usr/bin/env python3
"""
RNA-Seq Quantification阶段：对BAM文件进行定量分析

输入：
  - 排序后的BAM文件（来自RNA_ALN阶段）
  - 参考基因组GTF注释

输出：
  - 基因计数矩阵
  - FPKM/TPM表达矩阵
  - 定量报告和manifest
"""

import argparse
import json
import subprocess
import sys
import time
import yaml
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import hashlib
import os

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import re
import shutil

from gfa_core.rnaseq_context import (
    RnaseqLibraryStrandnessError,
    apply_rnaseq_context,
    build_rnaseq_context,
    featurecounts_strandness_value,
    require_rnaseq_library_strandness,
    resolve_rnaseq_project_context,
)
from cli.rnaseq_env import prepend_path, rnaseq_bin

RNASEQ_BIN = str(rnaseq_bin())
prepend_path(RNASEQ_BIN)


def resolve_thread_plan(cpu: int, sample_parallel_jobs: int = 1, threads_per_sample: Optional[int] = None) -> Dict[str, int]:
    """Resolve conservative RNA-seq sample/thread settings from CLI args."""
    total_cpu = max(1, int(cpu or 1))
    jobs = max(1, int(sample_parallel_jobs or 1))
    if threads_per_sample is None or int(threads_per_sample) < 1:
        threads = max(1, total_cpu // jobs)
    else:
        threads = max(1, int(threads_per_sample))
    if jobs * threads > total_cpu:
        threads = max(1, total_cpu // jobs)
    return {"cpu": total_cpu, "sample_parallel_jobs": jobs, "threads_per_sample": threads}


def compute_file_hash(filepath: Path) -> str:
    """计算文件的SHA256哈希值"""
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception:
        return ""


def get_tool_versions() -> Dict[str, str]:
    """获取所需工具版本信息"""
    versions = {}
    
    # stringtie
    try:
        result = subprocess.run(
            [f"{RNASEQ_BIN}/stringtie", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        output = result.stdout.strip() or result.stderr.strip()
        versions["stringtie"] = output.strip() if output else "unknown"
    except Exception:
        versions["stringtie"] = "not_found"
    
    # featureCounts
    try:
        result = subprocess.run(
            [f"{RNASEQ_BIN}/featureCounts", "-v"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        output = result.stderr.strip() or result.stdout.strip()
        if "featureCounts" in output:
            versions["featureCounts"] = output.split('\n')[0]
        else:
            versions["featureCounts"] = "not_found"
    except Exception:
        versions["featureCounts"] = "not_found"

    # MultiQC
    try:
        result = subprocess.run(
            [f"{RNASEQ_BIN}/multiqc", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10
        )
        output = result.stdout.strip() or result.stderr.strip()
        versions["multiqc"] = output.split('\n')[0] if output else "unknown"
    except Exception:
        versions["multiqc"] = "not_found"

    for tool, cmd in {
        "salmon": [f"{RNASEQ_BIN}/salmon", "--version"],
        "kallisto": [f"{RNASEQ_BIN}/kallisto", "version"],
        "rsem": [f"{RNASEQ_BIN}/rsem-calculate-expression", "--version"],
    }.items():
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=10)
            output = result.stdout.strip() or result.stderr.strip()
            versions[tool] = output.split('\n')[0] if output else "unknown"
        except Exception:
            versions[tool] = "not_found"

    try:
        result = subprocess.run(
            [f"{RNASEQ_BIN}/Rscript", "-e", "cat(requireNamespace('tximport', quietly=TRUE))"],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        versions["tximport"] = "available" if result.stdout.strip() == "TRUE" else "not_found"
    except Exception:
        versions["tximport"] = "not_found"
    
    return versions


def resolve_sample_bam(input_dir: Path, sample_name: str, sample_value: Any) -> Optional[Path]:
    """Resolve BAM path from old BAM configs or shared samples.yaml r1/r2 configs."""
    if isinstance(sample_value, str):
        bam = Path(sample_value)
        return bam if bam.is_absolute() else input_dir / bam
    if isinstance(sample_value, dict):
        bam_value = sample_value.get("bam") or sample_value.get("bam_path") or sample_value.get("sorted_bam")
        if bam_value:
            bam = Path(str(bam_value))
            return bam if bam.is_absolute() else input_dir / bam
        return input_dir / f"{sample_name}.sorted.bam"
    return None


def run_stringtie(
    bam: Path,
    gtf: Path,
    output_gtf: Path,
    output_cov: Path,
    cpu: int = 4
) -> bool:
    """运行StringTie进行转录本组装和定量"""
    cmd = [
        f"{RNASEQ_BIN}/stringtie",
        str(bam),
        "-G", str(gtf),
        "-o", str(output_gtf),
        "-A", str(output_cov),
        "-p", str(cpu)
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"StringTie failed: {e.stderr}", file=sys.stderr)
        return False


def convert_gff3_to_gtf(gff3: Path, output_dir: Path) -> Optional[Path]:
    """将GFF3转换为GTF格式"""
    gtf_output = output_dir / f"{gff3.stem}.converted.gtf"
    
    # 使用 gffread 转换（如果可用）
    gffread_cmd = [f"{RNASEQ_BIN}/gffread", str(gff3), "-T", "-o", str(gtf_output)]
    try:
        result = subprocess.run(gffread_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and gtf_output.exists() and gtf_output.stat().st_size > 100:
            print(f"[INFO] Converted {gff3.name} to GTF using gffread")
            return gtf_output
    except Exception:
        pass
    
    # 如果 gffread 失败，使用 Python 转换
    # 注意：需要正确处理基因层次结构
    print(f"[INFO] Converting {gff3.name} to GTF format with gene hierarchy...")
    try:
        # 第一遍：收集基因和转录本信息
        genes = {}  # ID -> gene_id
        transcripts = {}  # ID -> (gene_id, transcript_id)
        
        with open(gff3, 'r') as f_in:
            for line in f_in:
                if line.startswith('#') or line.strip() == '':
                    continue
                parts = line.strip().split('\t')
                if len(parts) < 9:
                    continue
                
                feature_type = parts[2]
                attr = parts[8]
                
                # 解析属性
                id_val = None
                parent_val = None
                name_val = None
                
                for item in attr.split(';'):
                    item = item.strip()
                    if item.startswith('ID='):
                        id_val = item[3:]
                    elif item.startswith('Parent='):
                        parent_val = item[7:]
                    elif item.startswith('Name='):
                        name_val = item[5:]
                
                # 记录基因和转录本
                if feature_type == 'gene' and id_val:
                    genes[id_val] = name_val or id_val
                elif feature_type == 'mRNA' and id_val:
                    # 转录本的Parent是基因
                    gene_id = genes.get(parent_val, parent_val)
                    transcripts[id_val] = (gene_id, name_val or id_val)
        
        # 第二遍：写入GTF
        with open(gff3, 'r') as f_in, open(gtf_output, 'w') as f_out:
            for line in f_in:
                if line.startswith('#') or line.strip() == '':
                    continue
                parts = line.strip().split('\t')
                if len(parts) < 9:
                    continue
                
                feature_type = parts[2]
                attr = parts[8]
                
                # 解析属性
                id_val = None
                parent_val = None
                
                for item in attr.split(';'):
                    item = item.strip()
                    if item.startswith('ID='):
                        id_val = item[3:]
                    elif item.startswith('Parent='):
                        parent_val = item[7:]
                
                # 确定gene_id和transcript_id
                gene_id = None
                transcript_id = None
                
                if feature_type == 'gene':
                    gene_id = genes.get(id_val, id_val)
                    transcript_id = gene_id
                elif feature_type == 'mRNA':
                    gene_id, transcript_id = transcripts.get(id_val, (parent_val, id_val))
                elif feature_type in ('exon', 'CDS', 'five_prime_utr', 'three_prime_utr'):
                    # 这些特征的Parent是转录本
                    if parent_val in transcripts:
                        gene_id, transcript_id = transcripts[parent_val]
                    else:
                        # Parent可能是基因（直接连接的exon）
                        gene_id = genes.get(parent_val, parent_val)
                        transcript_id = gene_id
                
                if gene_id:
                    new_attr = f'gene_id "{gene_id}"; transcript_id "{transcript_id}"'
                    parts[8] = new_attr
                    f_out.write('\t'.join(parts) + '\n')
        
        if gtf_output.exists() and gtf_output.stat().st_size > 100:
            print(f"[INFO] Converted {gff3.name} to {gtf_output}")
            return gtf_output
    except Exception as e:
        print(f"[WARN] GFF3 conversion failed: {e}")
        import traceback
        traceback.print_exc()
    
    return None


def run_featurecounts(
    bam_files: List[Path],
    gtf: Path,
    output: Path,
    cpu: int = 4,
    library_strandness: str = "non_stranded",
    command_log: Optional[Path] = None,
) -> Optional[Path]:
    """运行featureCounts进行基因计数"""
    # 检测GTF/GFF格式
    gtf_suffix = gtf.suffix.lower()
    annotation_file = gtf
    
    # 如果是GFF3，先转换为GTF
    if gtf_suffix in ['.gff', '.gff3'] or 'gff' in gtf.name.lower():
        converted = convert_gff3_to_gtf(gtf, output.parent)
        if converted:
            annotation_file = converted
            print(f"[INFO] Using converted GTF: {annotation_file}")
        else:
            print(f"[WARN] GFF3 conversion failed, trying original file...")
    
    cmd = [
        f"{RNASEQ_BIN}/featureCounts",
        "-t", "exon",
        "-g", "gene_id",
        "-a", str(annotation_file),
        "-o", str(output),
        "-p",  # paired-end
        "--countReadPairs",  # count fragments/read pairs with Subread >=2.0
        "-B",  # require both ends
        "-C",  # exclude chimeric
        "-s", featurecounts_strandness_value(library_strandness),
        "-T", str(min(cpu, 64))
    ]
    cmd.extend([str(b) for b in bam_files])
    if command_log:
        command_log.parent.mkdir(parents=True, exist_ok=True)
        command_log.write_text("Command: " + " ".join(cmd) + "\n", encoding="utf-8")
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        return annotation_file
    except subprocess.CalledProcessError as e:
        print(f"featureCounts failed: {e.stderr}", file=sys.stderr)
        return None



def run_multiqc(search_paths: Sequence[Path], output_dir: Path, report_name: str) -> Optional[Path]:
    """Run MultiQC over quantification outputs and return the report path."""
    multiqc = Path(f"{RNASEQ_BIN}/multiqc")
    existing = [str(path) for path in search_paths if path.exists()]
    if not multiqc.exists() or not existing:
        return None
    cmd = [str(multiqc), *existing, "-o", str(output_dir), "-n", report_name, "-f"]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        report = output_dir / report_name
        return report if report.exists() else None
    except subprocess.CalledProcessError as e:
        print(f"MultiQC failed: {e.stderr}", file=sys.stderr)
        return None



def parse_gff3_attrs(attr_text: str) -> Dict[str, str]:
    return parse_annotation_attrs(attr_text)


def parse_annotation_attrs(attr_text: str) -> Dict[str, str]:
    attrs: Dict[str, str] = {}
    for item in attr_text.split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            key, value = item.split("=", 1)
            attrs[key.strip()] = value.strip().strip('"')
            continue
        match = re.match(r"^(\S+)\s+\"([^\"]+)\"$", item)
        if match:
            attrs[match.group(1)] = match.group(2)
            continue
        match = re.match(r"^(\S+)\s+(.+)$", item)
        if match:
            attrs[match.group(1)] = match.group(2).strip().strip('"')
    return attrs


def first_annotation_value(attrs: Dict[str, str], keys: Sequence[str]) -> Optional[str]:
    for key in keys:
        value = attrs.get(key)
        if value:
            return str(value).split(",")[0].strip()
    return None


def extract_gene_id_from_fasta_header(raw_header: str) -> Optional[str]:
    for key in ("gene", "gene_id", "Parent", "locus_tag", "Name"):
        match = re.search(rf"(?:^|\s){re.escape(key)}[:=]([^\s;]+)", raw_header)
        if match:
            return match.group(1).split(",")[0].strip()
    return None


def parse_tx2gene_from_gff(annotation: Path) -> Dict[str, str]:
    """Parse transcript-to-gene mapping from a GFF3/GTF-like annotation."""
    tx2gene: Dict[str, str] = {}
    if not annotation.exists():
        return tx2gene
    with open(annotation, "r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 9:
                continue
            feature_type = parts[2].lower()
            attrs = parse_annotation_attrs(parts[8])
            if feature_type in {"mrna", "transcript"}:
                tx_id = first_annotation_value(attrs, ("ID", "transcript_id", "Name"))
                gene_id = first_annotation_value(attrs, ("Parent", "gene_id", "gene", "locus_tag", "Name"))
                if tx_id and gene_id:
                    tx2gene[tx_id] = gene_id
            elif feature_type == "cds":
                tx_id = first_annotation_value(attrs, ("transcript_id", "Parent", "ID", "protein_id"))
                gene_id = first_annotation_value(attrs, ("gene_id", "gene", "locus_tag", "Parent", "Name"))
                if tx_id and gene_id:
                    tx2gene.setdefault(tx_id, gene_id)
    return tx2gene


def normalize_transcriptome_and_tx2gene(
    transcriptome_fasta: Path,
    annotation: Optional[Path],
    output_dir: Path,
    prefix: str,
) -> Tuple[Optional[Path], Optional[Path], Dict[str, Any]]:
    """Write a Salmon-safe transcript FASTA and a tx2gene table."""
    if not transcriptome_fasta.exists():
        return None, None, {"status": "skipped", "reason": "transcriptome_fasta_not_found"}

    gff_tx2gene = parse_tx2gene_from_gff(annotation) if annotation else {}
    normalized_fasta = output_dir / f"{prefix}.salmon.transcripts.fa"
    tx2gene_file = output_dir / f"{prefix}.salmon.tx2gene.tsv"
    transcript_count = 0
    header_gene_count = 0
    annotation_gene_count = 0
    fallback_gene_count = 0
    seen: set = set()
    write_sequence = False

    with open(transcriptome_fasta, "r", encoding="utf-8", errors="ignore") as inp, \
            open(normalized_fasta, "w", encoding="utf-8") as fasta_out, \
            open(tx2gene_file, "w", encoding="utf-8") as tx_out:
        tx_out.write("transcript_id\tgene_id\n")
        for line in inp:
            if line.startswith(">"):
                raw_header = line[1:].strip()
                tx_id = raw_header.split()[0]
                write_sequence = bool(tx_id and tx_id not in seen)
                if not write_sequence:
                    continue
                seen.add(tx_id)
                gene_id = None
                header_gene_id = extract_gene_id_from_fasta_header(raw_header)
                if header_gene_id:
                    gene_id = header_gene_id
                    header_gene_count += 1
                elif tx_id in gff_tx2gene:
                    gene_id = gff_tx2gene[tx_id]
                    annotation_gene_count += 1
                else:
                    gene_id = re.sub(r"(\.\d+|-RA|-RB|-RC|-T\d+)$", "", tx_id)
                    fallback_gene_count += 1
                transcript_count += 1
                fasta_out.write(f">{tx_id}\n")
                tx_out.write(f"{tx_id}\t{gene_id}\n")
            elif write_sequence:
                fasta_out.write(line)

    if transcript_count == 0:
        return None, None, {"status": "failed", "reason": "no_transcripts_in_fasta"}
    fallback_pct = (fallback_gene_count / transcript_count * 100.0) if transcript_count else 0.0
    return normalized_fasta, tx2gene_file, {
        "status": "prepared",
        "source_transcriptome": str(transcriptome_fasta),
        "transcripts": transcript_count,
        "tx2gene_rows": transcript_count,
        "tx2gene_fallback_pct": round(fallback_pct, 3),
        "warning": "tx2gene_fallback_gt_5pct" if fallback_pct > 5.0 else "",
        "gene_id_sources": {
            "fasta_gene_field": header_gene_count,
            "annotation": annotation_gene_count,
            "transcript_id_fallback": fallback_gene_count,
        },
    }


def read_gene_matrix_signature(matrix_file: Optional[Path]) -> Dict[str, Any]:
    signature: Dict[str, Any] = {"exists": False, "samples": [], "genes": [], "column_sums": {}}
    if not matrix_file or not matrix_file.exists():
        return signature
    signature["exists"] = True
    with open(matrix_file, "r", encoding="utf-8", errors="ignore") as handle:
        header = handle.readline().rstrip("\n").split("\t")
        signature["samples"] = header[1:] if len(header) > 1 else []
        sums = {sample: 0.0 for sample in signature["samples"]}
        genes: List[str] = []
        for line in handle:
            if not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            gene_id = parts[0]
            if gene_id:
                genes.append(gene_id)
            for idx, sample in enumerate(signature["samples"], start=1):
                try:
                    sums[sample] += float(parts[idx]) if idx < len(parts) and parts[idx] != "" else 0.0
                except ValueError:
                    pass
    signature["genes"] = genes
    signature["column_sums"] = sums
    return signature


def read_tx2gene_stats(tx2gene_file: Optional[Path]) -> Dict[str, Any]:
    stats = {"exists": False, "rows": 0, "unique_transcripts": 0, "unique_gene_ids": 0}
    if not tx2gene_file or not tx2gene_file.exists():
        return stats
    transcripts = set()
    genes = set()
    with open(tx2gene_file, "r", encoding="utf-8", errors="ignore") as handle:
        first = True
        for line in handle:
            if not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            if first and len(parts) >= 2 and parts[0].lower() in {"tx", "transcript", "transcript_id"}:
                first = False
                continue
            first = False
            if len(parts) < 2:
                continue
            stats["rows"] += 1
            transcripts.add(parts[0])
            genes.add(parts[1])
    stats.update({"exists": True, "unique_transcripts": len(transcripts), "unique_gene_ids": len(genes)})
    return stats


def parse_salmon_index_duplicate_count(index_dir: Optional[Path]) -> Dict[str, Any]:
    info: Dict[str, Any] = {"duplicate_transcripts_removed": 0, "keep_duplicates": None}
    if not index_dir or not index_dir.exists():
        return info
    info_file = index_dir / "info.json"
    if info_file.exists():
        try:
            data = json.loads(info_file.read_text(encoding="utf-8", errors="ignore"))
            if isinstance(data, dict):
                info["keep_duplicates"] = data.get("keep_duplicates")
        except Exception:
            pass
    for log_file in [index_dir / "ref_indexing.log", *sorted(index_dir.glob("*.log"))]:
        if not log_file.exists():
            continue
        text = log_file.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r"Removed\s+(\d+)\s+transcripts\s+that\s+were\s+sequence\s+duplicates", text)
        if match:
            info["duplicate_transcripts_removed"] = int(match.group(1))
            break
    return info


def format_metric_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def write_matrix_bridge_qc(qc: Dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write("metric\tvalue\n")
        for key in sorted(qc.keys()):
            handle.write(f"{key}\t{format_metric_value(qc[key])}\n")


def compute_matrix_bridge_qc(
    count_matrix: Path,
    abundance_tpm: Optional[Path],
    tx2gene: Optional[Path],
    engine: str = "salmon",
    salmon_index_dir: Optional[Path] = None,
    tx2gene_metrics: Optional[Dict[str, Any]] = None,
    output_path: Optional[Path] = None,
    overlap_threshold_pct: float = 95.0,
    tpm_sum_tolerance: float = 1.0,
) -> Dict[str, Any]:
    count_sig = read_gene_matrix_signature(count_matrix)
    abundance_sig = read_gene_matrix_signature(abundance_tpm)
    count_genes = set(count_sig.get("genes", []))
    abundance_genes = set(abundance_sig.get("genes", []))
    overlap = len(count_genes & abundance_genes)
    overlap_vs_featurecounts = (overlap / len(count_genes) * 100.0) if count_genes else 0.0
    overlap_vs_abundance = (overlap / len(abundance_genes) * 100.0) if abundance_genes else 0.0
    sample_columns_match = count_sig.get("samples") == abundance_sig.get("samples")
    tpm_sums = abundance_sig.get("column_sums", {})
    tpm_sum_deviation_max = max((abs(float(value) - 1_000_000.0) for value in tpm_sums.values()), default=0.0)
    tpm_sums_ok = bool(tpm_sums) and tpm_sum_deviation_max <= tpm_sum_tolerance
    tx2gene_stats = read_tx2gene_stats(tx2gene)
    tx2gene_metrics = tx2gene_metrics or {}
    fallback_pct = float(tx2gene_metrics.get("tx2gene_fallback_pct") or 0.0)
    abundance_exists = bool(abundance_tpm and abundance_tpm.exists())

    status = "pass"
    failure_reasons: List[str] = []
    if not count_sig.get("exists"):
        failure_reasons.append("count_matrix_missing")
    if not abundance_exists:
        failure_reasons.append(f"{engine}_tpm_missing")
    if abundance_exists and not sample_columns_match:
        failure_reasons.append("sample_columns_mismatch")
    if abundance_exists and overlap_vs_featurecounts < overlap_threshold_pct:
        failure_reasons.append("gene_overlap_below_95pct")
    if abundance_exists and not tpm_sums_ok:
        failure_reasons.append("tpm_column_sums_not_1e6")
    if fallback_pct > 5.0:
        failure_reasons.append("tx2gene_fallback_gt_5pct")
    if failure_reasons:
        status = "fallback"

    recommended = f"{engine}_tximport_tpm" if status == "pass" else "featurecounts_tpm"
    qc: Dict[str, Any] = {
        "status": status,
        "engine": engine,
        "count_matrix": str(count_matrix),
        "abundance_tpm": str(abundance_tpm) if abundance_tpm else "",
        "tx2gene": str(tx2gene) if tx2gene else "",
        "featurecounts_genes": len(count_genes),
        "abundance_genes": len(abundance_genes),
        "gene_id_overlap": overlap,
        "gene_overlap_vs_featurecounts_pct": round(overlap_vs_featurecounts, 3),
        "gene_overlap_vs_abundance_pct": round(overlap_vs_abundance, 3),
        "sample_columns_match": sample_columns_match,
        "featurecounts_samples": count_sig.get("samples", []),
        "abundance_samples": abundance_sig.get("samples", []),
        "tpm_column_sums": {sample: round(float(value), 6) for sample, value in tpm_sums.items()},
        "tpm_column_sums_within_tolerance": tpm_sums_ok,
        "tpm_sum_tolerance": tpm_sum_tolerance,
        "tx2gene_rows": tx2gene_stats.get("rows", 0),
        "tx2gene_unique_gene_ids": tx2gene_stats.get("unique_gene_ids", 0),
        "tx2gene_fallback_pct": round(fallback_pct, 3),
        "recommended_expression_tpm_source": recommended,
        "recommended_expression_tpm_file": str(abundance_tpm) if status == "pass" and abundance_tpm else str(count_matrix.with_name(count_matrix.name.replace(".count_matrix.", ".tpm_matrix."))),
        "failure_reasons": failure_reasons,
        "display_evidence_policy": "DE uses featureCounts raw counts; expression abundance plots may use tximport TPM only when this bridge QC passes.",
    }
    if engine == "salmon":
        qc.update(parse_salmon_index_duplicate_count(salmon_index_dir))
    if output_path:
        write_matrix_bridge_qc(qc, output_path)
        qc["qc_file"] = str(output_path)
    return qc


def auto_find_transcriptome(workspace_dir: Path, input_dir: Path, gtf: Path, species_prefix: str) -> Optional[Path]:
    search_dirs = [gtf.parent, input_dir, workspace_dir / "input", Path("input") / workspace_dir.name]
    patterns = [
        f"{species_prefix}.cds.fasta",
        f"{species_prefix}.cds.fa",
        "*.cds.fasta",
        "*.cds.fa",
        "*cds*.fasta",
        "*cds*.fa",
        "*transcript*.fasta",
        "*transcript*.fa",
    ]
    for base in search_dirs:
        if not base.exists():
            continue
        for pattern in patterns:
            for candidate in sorted(base.glob(pattern)):
                if candidate.exists() and candidate.stat().st_size > 0:
                    return candidate
    return None


def resolve_sample_fastqs(
    input_dir: Path,
    workspace_dir: Path,
    prefix: str,
    sample_name: str,
    sample_value: Any,
) -> Tuple[Optional[Path], Optional[Path]]:
    def resolve_path(value: str) -> Optional[Path]:
        path = Path(value)
        candidates = [path] if path.is_absolute() else [
            input_dir / path,
            workspace_dir / "rnaseq_qc" / "analysis" / path.name,
            workspace_dir / "final" / path.name,
            Path("input") / workspace_dir.name / path,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    if isinstance(sample_value, dict):
        r1_value = sample_value.get("clean_r1") or sample_value.get("r1_clean") or sample_value.get("r1")
        r2_value = sample_value.get("clean_r2") or sample_value.get("r2_clean") or sample_value.get("r2")
        r1 = resolve_path(str(r1_value)) if r1_value else None
        r2 = resolve_path(str(r2_value)) if r2_value else None
        if r1 and r2:
            return r1, r2

    search_dirs = [input_dir, workspace_dir / "final", workspace_dir / "rnaseq_qc" / "analysis"]
    r1_patterns = [
        f"{prefix}.rnaseq_qc.{sample_name}_1.clean.fastq.gz",
        f"{sample_name}_1.clean.fastq.gz",
        f"{sample_name}*_1.clean.fastq.gz",
        f"{sample_name}*_R1*.fastq.gz",
        f"{sample_name}*_1.fq.gz",
    ]
    r2_patterns = [
        f"{prefix}.rnaseq_qc.{sample_name}_2.clean.fastq.gz",
        f"{sample_name}_2.clean.fastq.gz",
        f"{sample_name}*_2.clean.fastq.gz",
        f"{sample_name}*_R2*.fastq.gz",
        f"{sample_name}*_2.fq.gz",
    ]
    for base in search_dirs:
        if not base.exists():
            continue
        r1 = next((p for pattern in r1_patterns for p in sorted(base.glob(pattern)) if p.exists()), None)
        r2 = next((p for pattern in r2_patterns for p in sorted(base.glob(pattern)) if p.exists()), None)
        if r1 and r2:
            return r1, r2
    return None, None


def run_salmon_abundance_track(
    samples: Dict[str, Any],
    input_dir: Path,
    workspace_dir: Path,
    final_dir: Path,
    analysis_dir: Path,
    prefix: str,
    gtf: Path,
    transcriptome_fasta: Optional[Path],
    tx2gene_override: Optional[Path],
    threads: int,
    libtype: str,
    counts_from_abundance: str,
) -> Dict[str, Any]:
    salmon = Path(f"{RNASEQ_BIN}/salmon")
    rscript = Path(f"{RNASEQ_BIN}/Rscript")
    if not salmon.exists():
        return {"engine": "salmon", "status": "skipped", "reason": "salmon_not_found"}
    if not rscript.exists():
        return {"engine": "salmon", "status": "skipped", "reason": "Rscript_not_found"}

    species_prefix = prefix.split(".")[0] if prefix else ""
    transcriptome = transcriptome_fasta or auto_find_transcriptome(workspace_dir, input_dir, gtf, species_prefix)
    if not transcriptome:
        return {"engine": "salmon", "status": "skipped", "reason": "transcriptome_fasta_not_found"}

    abundance_dir = analysis_dir / "salmon"
    abundance_dir.mkdir(parents=True, exist_ok=True)

    normalized_fasta = None
    tx2gene_file = tx2gene_override
    tx2gene_metrics: Dict[str, Any] = {}
    if not tx2gene_file:
        normalized_fasta, tx2gene_file, tx2gene_metrics = normalize_transcriptome_and_tx2gene(
            transcriptome, gtf, abundance_dir, prefix
        )
    else:
        normalized_fasta = transcriptome
        tx2gene_metrics = {"status": "provided", "tx2gene": str(tx2gene_file)}
    if not normalized_fasta or not tx2gene_file:
        return {"engine": "salmon", "status": "skipped", "reason": "tx2gene_or_transcriptome_prepare_failed", "tx2gene": tx2gene_metrics}

    salmon_env = os.environ.copy()
    salmon_env["LC_ALL"] = "C"
    salmon_env["LANG"] = "C"
    salmon_env["LANGUAGE"] = "C"

    index_dir = abundance_dir / "salmon_index"
    if not (index_dir / "versionInfo.json").exists():
        if index_dir.exists():
            shutil.rmtree(index_dir)
        index_cmd = [str(salmon), "index", "-t", str(normalized_fasta), "-i", str(index_dir), "-k", "31"]
        result = subprocess.run(index_cmd, capture_output=True, text=True, check=False, env=salmon_env)
        if result.returncode != 0:
            return {
                "engine": "salmon",
                "status": "failed",
                "reason": "salmon_index_failed",
                "stderr": result.stderr[-2000:],
                "cmd": index_cmd,
                "tx2gene": tx2gene_metrics,
            }

    sample_quant_file = abundance_dir / f"{prefix}.salmon.sample_quant_files.tsv"
    quant_records: List[Dict[str, str]] = []
    failed_samples: Dict[str, str] = {}
    for sample_name, sample_value in samples.items():
        r1, r2 = resolve_sample_fastqs(input_dir, workspace_dir, prefix, str(sample_name), sample_value)
        if not r1 or not r2:
            failed_samples[str(sample_name)] = "clean_fastq_pair_not_found"
            continue
        out_dir = abundance_dir / str(sample_name)
        quant_cmd = [
            str(salmon), "quant",
            "-i", str(index_dir),
            "-l", libtype,
            "-1", str(r1),
            "-2", str(r2),
            "--validateMappings",
            "--seqBias",
            "--gcBias",
            "-p", str(max(1, threads)),
            "-o", str(out_dir),
        ]
        result = subprocess.run(quant_cmd, capture_output=True, text=True, check=False, env=salmon_env)
        quant_file = out_dir / "quant.sf"
        if result.returncode == 0 and quant_file.exists():
            quant_records.append({"sample": str(sample_name), "quant_file": str(quant_file)})
        else:
            failed_samples[str(sample_name)] = (result.stderr or result.stdout or "salmon_quant_failed")[-1000:]

    if not quant_records:
        return {
            "engine": "salmon",
            "status": "failed",
            "reason": "no_successful_salmon_quant",
            "failed_samples": failed_samples,
            "tx2gene": tx2gene_metrics,
        }

    with open(sample_quant_file, "w", encoding="utf-8") as handle:
        handle.write("sample\tquant_file\n")
        for record in quant_records:
            handle.write(f"{record['sample']}\t{record['quant_file']}\n")

    script = Path("scripts/tximport_abundance.R")
    tximport_cmd = [
        str(rscript), str(script),
        "--samples", str(sample_quant_file),
        "--tx2gene", str(tx2gene_file),
        "--output", str(final_dir),
        "--prefix", prefix,
        "--engine", "salmon",
        "--counts-from-abundance", counts_from_abundance,
    ]
    result = subprocess.run(tximport_cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return {
            "engine": "salmon",
            "status": "failed",
            "reason": "tximport_failed",
            "stderr": result.stderr[-2000:],
            "cmd": tximport_cmd,
            "successful_samples": len(quant_records),
            "failed_samples": failed_samples,
            "tx2gene": tx2gene_metrics,
        }

    final_tx2gene = final_dir / f"{prefix}.salmon.tx2gene.tsv"
    if tx2gene_file.exists():
        shutil.copy2(tx2gene_file, final_tx2gene)
    return {
        "engine": "salmon",
        "status": "success" if not failed_samples else "partial_success",
        "role": "publication_expression_plots",
        "successful_samples": len(quant_records),
        "failed_samples": failed_samples,
        "libtype": libtype,
        "bias_correction": {"seqBias": True, "gcBias": True},
        "counts_from_abundance": counts_from_abundance,
        "transcriptome_fasta": str(transcriptome),
        "normalized_transcriptome_fasta": str(normalized_fasta),
        "index_dir": str(index_dir),
        "tx2gene": tx2gene_metrics,
        "abundance_track_warning": tx2gene_metrics.get("warning", ""),
        "artifacts": {
            "tpm_matrix": f"final/{prefix}.salmon.tpm_matrix.txt",
            "tximport_counts": f"final/{prefix}.salmon.tximport_counts.txt",
            "length_matrix": f"final/{prefix}.salmon.length_matrix.txt",
            "tx2gene": f"final/{prefix}.salmon.tx2gene.tsv",
        },
    }



def run_kallisto_abundance_track(
    samples: Dict[str, Any],
    input_dir: Path,
    workspace_dir: Path,
    final_dir: Path,
    analysis_dir: Path,
    prefix: str,
    gtf: Path,
    transcriptome_fasta: Optional[Path],
    tx2gene_override: Optional[Path],
    threads: int,
    counts_from_abundance: str,
    bias: bool = False,
) -> Dict[str, Any]:
    kallisto = Path(f"{RNASEQ_BIN}/kallisto")
    rscript = Path(f"{RNASEQ_BIN}/Rscript")
    if not kallisto.exists():
        return {"engine": "kallisto", "status": "skipped", "reason": "kallisto_not_found"}
    if not rscript.exists():
        return {"engine": "kallisto", "status": "skipped", "reason": "Rscript_not_found"}

    species_prefix = prefix.split(".")[0] if prefix else ""
    transcriptome = transcriptome_fasta or auto_find_transcriptome(workspace_dir, input_dir, gtf, species_prefix)
    if not transcriptome:
        return {"engine": "kallisto", "status": "skipped", "reason": "transcriptome_fasta_not_found"}

    abundance_dir = analysis_dir / "kallisto"
    abundance_dir.mkdir(parents=True, exist_ok=True)
    normalized_fasta = None
    tx2gene_file = tx2gene_override
    tx2gene_metrics: Dict[str, Any] = {}
    if not tx2gene_file:
        normalized_fasta, tx2gene_file, tx2gene_metrics = normalize_transcriptome_and_tx2gene(
            transcriptome, gtf, abundance_dir, prefix
        )
    else:
        normalized_fasta = transcriptome
        tx2gene_metrics = {"status": "provided", "tx2gene": str(tx2gene_file)}
    if not normalized_fasta or not tx2gene_file:
        return {"engine": "kallisto", "status": "skipped", "reason": "tx2gene_or_transcriptome_prepare_failed", "tx2gene": tx2gene_metrics}

    index_file = abundance_dir / f"{prefix}.kallisto.idx"
    if not index_file.exists():
        index_cmd = [str(kallisto), "index", "-i", str(index_file), str(normalized_fasta)]
        result = subprocess.run(index_cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return {
                "engine": "kallisto",
                "status": "failed",
                "reason": "kallisto_index_failed",
                "stderr": result.stderr[-2000:],
                "cmd": index_cmd,
                "tx2gene": tx2gene_metrics,
            }

    sample_quant_file = abundance_dir / f"{prefix}.kallisto.sample_quant_files.tsv"
    quant_records: List[Dict[str, str]] = []
    failed_samples: Dict[str, str] = {}
    for sample_name, sample_value in samples.items():
        r1, r2 = resolve_sample_fastqs(input_dir, workspace_dir, prefix, str(sample_name), sample_value)
        if not r1 or not r2:
            failed_samples[str(sample_name)] = "clean_fastq_pair_not_found"
            continue
        out_dir = abundance_dir / str(sample_name)
        quant_cmd = [
            str(kallisto), "quant",
            "-i", str(index_file),
            "-o", str(out_dir),
            "-t", str(max(1, threads)),
        ]
        if bias:
            quant_cmd.append("--bias")
        quant_cmd.extend([str(r1), str(r2)])
        result = subprocess.run(quant_cmd, capture_output=True, text=True, check=False)
        quant_file = out_dir / "abundance.tsv"
        if result.returncode == 0 and quant_file.exists():
            quant_records.append({"sample": str(sample_name), "quant_file": str(quant_file)})
        else:
            failure_text = (result.stderr or result.stdout or "kallisto_quant_failed")[-1000:]
            failed_samples[str(sample_name)] = f"returncode={result.returncode}; {failure_text}"

    if not quant_records:
        return {
            "engine": "kallisto",
            "status": "failed",
            "reason": "no_successful_kallisto_quant",
            "failed_samples": failed_samples,
            "tx2gene": tx2gene_metrics,
        }

    with open(sample_quant_file, "w", encoding="utf-8") as handle:
        handle.write("sample\tquant_file\n")
        for record in quant_records:
            handle.write(f"{record['sample']}\t{record['quant_file']}\n")

    script = Path("scripts/tximport_abundance.R")
    tximport_cmd = [
        str(rscript), str(script),
        "--samples", str(sample_quant_file),
        "--tx2gene", str(tx2gene_file),
        "--output", str(final_dir),
        "--prefix", prefix,
        "--engine", "kallisto",
        "--counts-from-abundance", counts_from_abundance,
    ]
    result = subprocess.run(tximport_cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return {
            "engine": "kallisto",
            "status": "failed",
            "reason": "tximport_failed",
            "stderr": result.stderr[-2000:],
            "cmd": tximport_cmd,
            "successful_samples": len(quant_records),
            "failed_samples": failed_samples,
            "tx2gene": tx2gene_metrics,
        }

    final_tx2gene = final_dir / f"{prefix}.kallisto.tx2gene.tsv"
    if tx2gene_file.exists():
        shutil.copy2(tx2gene_file, final_tx2gene)
    return {
        "engine": "kallisto",
        "status": "success" if not failed_samples else "partial_success",
        "role": "publication_expression_plots",
        "successful_samples": len(quant_records),
        "failed_samples": failed_samples,
        "bias_correction": {"bias": bias},
        "counts_from_abundance": counts_from_abundance,
        "transcriptome_fasta": str(transcriptome),
        "normalized_transcriptome_fasta": str(normalized_fasta),
        "index_file": str(index_file),
        "tx2gene": tx2gene_metrics,
        "abundance_track_warning": tx2gene_metrics.get("warning", ""),
        "artifacts": {
            "tpm_matrix": f"final/{prefix}.kallisto.tpm_matrix.txt",
            "tximport_counts": f"final/{prefix}.kallisto.tximport_counts.txt",
            "length_matrix": f"final/{prefix}.kallisto.length_matrix.txt",
            "tx2gene": f"final/{prefix}.kallisto.tx2gene.tsv",
        },
    }


def sample_name_from_featurecounts_column(sample_path: str) -> str:
    """Convert a featureCounts sample column path to a stable sample name."""
    name = Path(str(sample_path)).name
    for suffix in (".sorted.bam", ".bam"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem.replace(".sorted", "")


def read_featurecounts_table(count_file: Path) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Read featureCounts output and return sample column names plus row dicts."""
    if not count_file.exists():
        return [], []

    with open(count_file, "r") as f:
        lines = f.readlines()

    header_idx = None
    for i, line in enumerate(lines):
        if not line.startswith("#") and line.strip():
            header_idx = i
            break
    if header_idx is None or header_idx >= len(lines) - 1:
        return [], []

    header = lines[header_idx].rstrip("\n").split("\t")
    sample_columns = header[6:]
    rows: List[Dict[str, Any]] = []
    for line in lines[header_idx + 1:]:
        if not line.strip():
            continue
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 7:
            continue
        try:
            length = int(float(parts[5]))
        except ValueError:
            length = 0
        counts: List[int] = []
        for value in parts[6:]:
            try:
                counts.append(int(round(float(value))))
            except ValueError:
                counts.append(0)
        rows.append(
            {
                "gene_id": parts[0],
                "chr": parts[1],
                "start": parts[2],
                "end": parts[3],
                "strand": parts[4],
                "length": length,
                "counts": counts,
            }
        )
    return sample_columns, rows


def parse_featurecounts_summary(summary_file: Path, sample_names: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    """Parse featureCounts .summary and calculate Assigned rate per sample."""
    if not summary_file.exists():
        return {}

    with open(summary_file, "r") as f:
        lines = [line.rstrip("\n").split("\t") for line in f if line.strip()]
    if not lines:
        return {}

    header = lines[0][1:]
    samples = sample_names if sample_names and len(sample_names) == len(header) else [
        sample_name_from_featurecounts_column(item) for item in header
    ]
    by_sample: Dict[str, Dict[str, Any]] = {
        sample: {"featurecounts_status_counts": {}} for sample in samples
    }

    for row in lines[1:]:
        if len(row) < 2:
            continue
        status = row[0]
        for idx, sample in enumerate(samples, start=1):
            try:
                value = int(float(row[idx])) if idx < len(row) else 0
            except ValueError:
                value = 0
            by_sample[sample]["featurecounts_status_counts"][status] = value

    for sample, data in by_sample.items():
        status_counts = data["featurecounts_status_counts"]
        assigned = int(status_counts.get("Assigned", 0))
        total = sum(int(v) for v in status_counts.values())
        data["assigned_reads"] = assigned
        data["featurecounts_total_reads"] = total
        data["featurecounts_assigned_rate"] = (assigned / total * 100.0) if total else 0.0
    return by_sample


def read_featurecounts_summary_table(summary_file: Path) -> Tuple[List[str], Dict[str, Dict[str, int]]]:
    if not summary_file.exists():
        return [], {}
    with open(summary_file, "r") as f:
        lines = [line.rstrip("\n").split("\t") for line in f if line.strip()]
    if not lines:
        return [], {}
    samples = [sample_name_from_featurecounts_column(item) for item in lines[0][1:]]
    by_status: Dict[str, Dict[str, int]] = {}
    for row in lines[1:]:
        status = row[0]
        by_status[status] = {}
        for idx, sample in enumerate(samples, start=1):
            try:
                value = int(float(row[idx])) if idx < len(row) else 0
            except ValueError:
                value = 0
            by_status[status][sample] = value
    return samples, by_status


def write_featurecounts_table(sample_names: List[str], rows: List[Dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as out:
        out.write("Geneid\tChr\tStart\tEnd\tStrand\tLength\t" + "\t".join(sample_names) + "\n")
        for row in rows:
            out.write(
                "\t".join(
                    [
                        str(row["gene_id"]),
                        str(row["chr"]),
                        str(row["start"]),
                        str(row["end"]),
                        str(row["strand"]),
                        str(row["length"]),
                        *[str(value) for value in row["counts"]],
                    ]
                )
                + "\n"
            )


def merge_featurecounts_outputs(
    group_outputs: List[Dict[str, Any]],
    sample_order: List[str],
    merged_output: Path,
) -> None:
    gene_rows: Dict[str, Dict[str, Any]] = {}
    gene_order: List[str] = []
    for group in group_outputs:
        sample_columns, rows = read_featurecounts_table(Path(group["output"]))
        group_samples = [sample_name_from_featurecounts_column(item) for item in sample_columns]
        for row in rows:
            gene_id = row["gene_id"]
            if gene_id not in gene_rows:
                gene_order.append(gene_id)
                gene_rows[gene_id] = {
                    "gene_id": gene_id,
                    "chr": row["chr"],
                    "start": row["start"],
                    "end": row["end"],
                    "strand": row["strand"],
                    "length": row["length"],
                    "counts_by_sample": {sample: 0 for sample in sample_order},
                }
            for idx, sample in enumerate(group_samples):
                if sample in gene_rows[gene_id]["counts_by_sample"] and idx < len(row["counts"]):
                    gene_rows[gene_id]["counts_by_sample"][sample] = row["counts"][idx]

    merged_rows: List[Dict[str, Any]] = []
    for gene_id in gene_order:
        row = gene_rows[gene_id]
        merged_rows.append(
            {
                "gene_id": row["gene_id"],
                "chr": row["chr"],
                "start": row["start"],
                "end": row["end"],
                "strand": row["strand"],
                "length": row["length"],
                "counts": [row["counts_by_sample"].get(sample, 0) for sample in sample_order],
            }
        )
    write_featurecounts_table(sample_order, merged_rows, merged_output)


def merge_featurecounts_summaries(
    group_outputs: List[Dict[str, Any]],
    sample_order: List[str],
    merged_summary: Path,
) -> None:
    status_order: List[str] = []
    merged: Dict[str, Dict[str, int]] = {}
    for group in group_outputs:
        _, by_status = read_featurecounts_summary_table(Path(group["summary"]))
        for status, values in by_status.items():
            if status not in merged:
                status_order.append(status)
                merged[status] = {sample: 0 for sample in sample_order}
            for sample, value in values.items():
                if sample in merged[status]:
                    merged[status][sample] = value

    merged_summary.parent.mkdir(parents=True, exist_ok=True)
    with open(merged_summary, "w", encoding="utf-8") as out:
        out.write("Status\t" + "\t".join(sample_order) + "\n")
        for status in status_order:
            out.write(status + "\t" + "\t".join(str(merged[status].get(sample, 0)) for sample in sample_order) + "\n")


def run_featurecounts_by_strandness(
    bam_files: List[Path],
    sample_names: List[str],
    strandness_by_sample: Dict[str, str],
    gtf: Path,
    count_output: Path,
    final_dir: Path,
    prefix: str,
    cpu: int,
) -> Tuple[Optional[Path], List[Dict[str, Any]]]:
    grouped: Dict[str, List[Tuple[str, Path]]] = {}
    for sample_name, bam in zip(sample_names, bam_files):
        strandness = strandness_by_sample.get(str(sample_name), "non_stranded") or "non_stranded"
        grouped.setdefault(strandness, []).append((str(sample_name), bam))

    group_results: List[Dict[str, Any]] = []
    if len(grouped) <= 1:
        strandness = next(iter(grouped.keys()), "non_stranded")
        command_log = final_dir / f"{prefix}.featurecounts.command.txt"
        annotation = run_featurecounts(
            bam_files,
            gtf,
            count_output,
            cpu,
            library_strandness=strandness,
            command_log=command_log,
        )
        summary = count_output.with_name(count_output.name + ".summary")
        group_results.append(
            {
                "strandness": strandness,
                "featurecounts_s": featurecounts_strandness_value(strandness),
                "samples": sample_names,
                "output": str(count_output),
                "summary": str(summary),
                "command_log": str(command_log),
            }
        )
        return annotation, group_results

    print(f"[INFO] Mixed library strandness detected; running featureCounts in {len(grouped)} groups.")
    counting_annotation: Optional[Path] = None
    for strandness, group_items in grouped.items():
        group_samples = [item[0] for item in group_items]
        group_bams = [item[1] for item in group_items]
        safe_strandness = re.sub(r"[^A-Za-z0-9_.-]+", "_", strandness or "non_stranded")
        group_output = count_output.with_name(f"featurecounts.{safe_strandness}.txt")
        command_log = final_dir / f"{prefix}.featurecounts.{safe_strandness}.command.txt"
        annotation = run_featurecounts(
            group_bams,
            gtf,
            group_output,
            cpu,
            library_strandness=strandness,
            command_log=command_log,
        )
        if annotation is None:
            return None, group_results
        counting_annotation = annotation
        summary = group_output.with_name(group_output.name + ".summary")
        group_results.append(
            {
                "strandness": strandness,
                "featurecounts_s": featurecounts_strandness_value(strandness),
                "samples": group_samples,
                "output": str(group_output),
                "summary": str(summary),
                "command_log": str(command_log),
            }
        )

    merge_featurecounts_outputs(group_results, sample_names, count_output)
    merge_featurecounts_summaries(group_results, sample_names, count_output.with_name(count_output.name + ".summary"))
    for group in group_results:
        safe_strandness = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(group.get("strandness") or "group"))
        final_group_output = final_dir / f"{prefix}.featurecounts.{safe_strandness}.txt"
        final_group_summary = final_dir / f"{prefix}.featurecounts.{safe_strandness}.txt.summary"
        shutil.copy2(Path(str(group["output"])), final_group_output)
        shutil.copy2(Path(str(group["summary"])), final_group_summary)
        group["output"] = str(final_group_output)
        group["summary"] = str(final_group_summary)
    merged_command_log = final_dir / f"{prefix}.featurecounts.command.txt"
    with open(merged_command_log, "w", encoding="utf-8") as out:
        out.write("Mode: grouped_by_library_strandness\n")
        for group in group_results:
            out.write(
                f"Group {group['strandness']} (-s {group['featurecounts_s']}): "
                f"{','.join(group['samples'])}; command_log={group['command_log']}\n"
            )
    return counting_annotation, group_results


def parse_featurecounts_output(
    count_file: Path,
    summary_file: Optional[Path] = None,
    sample_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """解析featureCounts输出"""
    stats = {
        "total_genes": 0,
        "total_transcripts": 0,
        "expressed_genes": 0,
        "samples": {},
        "expression_matrix_status": "not_generated",
        "gene_length_source": "featureCounts Length column",
    }

    sample_columns, rows = read_featurecounts_table(count_file)
    if not rows:
        return stats

    samples = sample_names if sample_names and len(sample_names) == len(sample_columns) else [
        sample_name_from_featurecounts_column(item) for item in sample_columns
    ]

    stats["total_genes"] = len(rows)
    stats["expressed_genes"] = sum(1 for row in rows if any(count > 0 for count in row["counts"]))

    summary_stats = parse_featurecounts_summary(summary_file, samples) if summary_file else {}
    for idx, sample_name in enumerate(samples):
        assigned_reads = sum(row["counts"][idx] for row in rows if idx < len(row["counts"]))
        stats["samples"][sample_name] = {
            "assigned_reads": assigned_reads,
            "unassigned_reads": 0,
        }
        if sample_name in summary_stats:
            stats["samples"][sample_name].update(summary_stats[sample_name])

    return stats


def generate_expression_matrices(
    count_file: Path,
    output_dir: Path,
    prefix: str,
    sample_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Generate count, FPKM, TPM, gene length, and normalization matrices.

    FPKM and TPM are calculated from the featureCounts Length column and raw
    assigned fragment counts. They are intended for expression visualization;
    differential expression should continue to use raw count_matrix.
    """
    sample_columns, rows = read_featurecounts_table(count_file)
    if not rows:
        raise RuntimeError(f"Cannot parse featureCounts output: {count_file}")

    samples = sample_names if sample_names and len(sample_names) == len(sample_columns) else [
        sample_name_from_featurecounts_column(item) for item in sample_columns
    ]

    bad_length_genes = [row["gene_id"] for row in rows if row["length"] <= 0]
    usable_rows = [row for row in rows if row["length"] > 0]
    if not usable_rows:
        raise RuntimeError("featureCounts output has no genes with positive Length; cannot calculate FPKM/TPM")

    count_matrix = output_dir / f"{prefix}.count_matrix.txt"
    fpkm_matrix = output_dir / f"{prefix}.fpkm_matrix.txt"
    tpm_matrix = output_dir / f"{prefix}.tpm_matrix.txt"
    gene_lengths_file = output_dir / f"{prefix}.gene_lengths.txt"
    norm_info_file = output_dir / f"{prefix}.normalization_info.txt"

    assigned_totals = [
        sum(row["counts"][idx] for row in rows if idx < len(row["counts"]))
        for idx in range(len(samples))
    ]
    rpk_sums: List[float] = []
    for idx in range(len(samples)):
        rpk_sum = 0.0
        for row in usable_rows:
            if idx < len(row["counts"]):
                rpk_sum += row["counts"][idx] / (row["length"] / 1000.0)
        rpk_sums.append(rpk_sum)

    with open(count_matrix, "w") as out:
        out.write("gene_id\t" + "\t".join(samples) + "\n")
        for row in rows:
            out.write(row["gene_id"] + "\t" + "\t".join(str(v) for v in row["counts"]) + "\n")

    with open(gene_lengths_file, "w") as out:
        out.write("gene_id\tlength\n")
        for row in rows:
            out.write(f"{row['gene_id']}\t{row['length']}\n")

    with open(fpkm_matrix, "w") as fpkm_out, open(tpm_matrix, "w") as tpm_out:
        header = "gene_id\t" + "\t".join(samples) + "\n"
        fpkm_out.write(header)
        tpm_out.write(header)
        for row in rows:
            fpkm_values: List[str] = []
            tpm_values: List[str] = []
            length_kb = row["length"] / 1000.0 if row["length"] > 0 else 0.0
            for idx, count in enumerate(row["counts"]):
                if length_kb <= 0:
                    fpkm = 0.0
                    tpm = 0.0
                else:
                    assigned_million = assigned_totals[idx] / 1_000_000.0 if assigned_totals[idx] else 0.0
                    rpk = count / length_kb
                    fpkm = (rpk / assigned_million) if assigned_million else 0.0
                    tpm = (rpk / rpk_sums[idx] * 1_000_000.0) if rpk_sums[idx] else 0.0
                fpkm_values.append(f"{fpkm:.6f}")
                tpm_values.append(f"{tpm:.6f}")
            fpkm_out.write(row["gene_id"] + "\t" + "\t".join(fpkm_values) + "\n")
            tpm_out.write(row["gene_id"] + "\t" + "\t".join(tpm_values) + "\n")

    with open(norm_info_file, "w") as out:
        out.write("sample\tassigned_fragments\trpk_sum\ttpm_sum\n")
        for idx, sample in enumerate(samples):
            out.write(f"{sample}\t{assigned_totals[idx]}\t{rpk_sums[idx]:.6f}\t1000000\n")

    return {
        "count_matrix": count_matrix,
        "fpkm_matrix": fpkm_matrix,
        "tpm_matrix": tpm_matrix,
        "gene_lengths_file": gene_lengths_file,
        "normalization_info_file": norm_info_file,
        "samples": samples,
        "expression_matrix_status": "computed_fpkm_tpm",
        "gene_length_source": "featureCounts Length column",
        "library_size_source": "featureCounts assigned fragments",
        "bad_length_genes": len(bad_length_genes),
        "gene_length_match_rate": len(usable_rows) / len(rows) * 100.0,
    }


def generate_report(
    output_dir: Path,
    stats: Dict[str, Any],
    versions: Dict[str, str],
    start_time: datetime,
    end_time: datetime,
    prefix: str = "",
    abundance_track: Optional[Dict[str, Any]] = None,
    sample_config: Optional[str] = None,
    library_strandness: Optional[Dict[str, Any]] = None,
) -> Path:
    """生成定量报告"""
    report_name = f"{prefix}.rnaseq_quant_report.md" if prefix else "rnaseq_quant_report.md"
    report_file = output_dir / report_name

    elapsed = (end_time - start_time).total_seconds()
    expression_status = stats.get("expression_matrix_status", "unknown")

    content = f"""# RNA-Seq Quantification Report

## Summary

- **Analysis Date**: {end_time.strftime("%Y-%m-%d %H:%M:%S")}
- **Total Genes**: {stats.get('total_genes', 'N/A')}
- **Expressed Genes**: {stats.get('expressed_genes', 'N/A')}
- **Samples Processed**: {len(stats.get('samples', {}))}
- **Expression Matrix Status**: {expression_status}
- **Total Runtime**: {elapsed:.1f} seconds
- **Library Strandness**: {", ".join((library_strandness or {}).get("library_strandness_values", [])) or "not_recorded"}

## Tool Versions

"""
    for tool, version in versions.items():
        content += f"- **{tool}**: {version}\n"

    content += "\n## Sample Assignment Statistics\n\n"
    content += "| Sample | Assigned Fragments | featureCounts Total | Assigned Rate |\n"
    content += "|--------|--------------------|---------------------|---------------|\n"
    for sample, row in (stats.get("samples") or {}).items():
        assigned = row.get("assigned_reads", "N/A")
        total = row.get("featurecounts_total_reads", "N/A")
        rate = row.get("featurecounts_assigned_rate")
        rate_text = f"{rate:.2f}%" if isinstance(rate, (int, float)) else "N/A"
        content += f"| {sample} | {assigned} | {total} | {rate_text} |\n"

    content += "\n## Output Files\n\n"
    content += f"- Count matrix: `{prefix}.count_matrix.txt` (raw featureCounts counts; DESeq2/edgeR input)\n"
    content += f"- FPKM matrix: `{prefix}.fpkm_matrix.txt` (featureCounts-derived fallback for expression visualization)\n"
    content += f"- TPM matrix: `{prefix}.tpm_matrix.txt` (featureCounts-derived fallback; not the publication-plot default when transcript-aware TPM exists)\n"
    content += f"- Gene lengths: `{prefix}.gene_lengths.txt`\n"
    content += f"- Normalization info: `{prefix}.normalization_info.txt`\n"
    content += f"- featureCounts summary: `{prefix}.featurecounts.txt.summary`\n"
    if abundance_track and abundance_track.get("status") in {"success", "partial_success"}:
        engine = abundance_track.get("engine", "salmon")
        content += f"- Transcript-aware TPM matrix: `{prefix}.{engine}.tpm_matrix.txt` (preferred for publication expression plots)\n"
        content += f"- tximport counts: `{prefix}.{engine}.tximport_counts.txt` (not used for DE by default)\n"
        content += f"- tximport effective lengths: `{prefix}.{engine}.length_matrix.txt`\n"

    if abundance_track:
        content += "\n## Quantification Tracks\n\n"
        content += "| Track | Engine | Status | Role |\n"
        content += "|---|---|---|---|\n"
        content += "| count_track | featureCounts | success | DE primary raw counts |\n"
        content += f"| abundance_track | {abundance_track.get('engine', 'salmon')} | {abundance_track.get('status', 'unknown')} | publication expression plots |\n"
        if abundance_track.get("reason"):
            content += f"\nAbundance track note: `{abundance_track.get('reason')}`.\n"

    matrix_bridge_qc = stats.get("matrix_bridge_qc") or {}
    if matrix_bridge_qc:
        content += "\n## Matrix Bridge QC\n\n"
        content += "| Check | Value |\n"
        content += "|---|---|\n"
        content += f"| Status | {matrix_bridge_qc.get('status', 'unknown')} |\n"
        content += f"| Recommended expression TPM | {matrix_bridge_qc.get('recommended_expression_tpm_source', 'unknown')} |\n"
        content += f"| Sample columns match | {matrix_bridge_qc.get('sample_columns_match', 'unknown')} |\n"
        content += f"| Gene overlap vs featureCounts | {matrix_bridge_qc.get('gene_overlap_vs_featurecounts_pct', 'NA')}% |\n"
        content += f"| TPM sums within tolerance | {matrix_bridge_qc.get('tpm_column_sums_within_tolerance', 'unknown')} |\n"
        if "duplicate_transcripts_removed" in matrix_bridge_qc:
            content += f"| Salmon duplicate transcripts removed | {matrix_bridge_qc.get('duplicate_transcripts_removed', 0)} |\n"
        if matrix_bridge_qc.get("failure_reasons"):
            content += f"| Fallback reasons | {', '.join(matrix_bridge_qc.get('failure_reasons', []))} |\n"

    content += "\n## Methods\n\n"
    content += "Raw gene counts were generated by featureCounts. FPKM and TPM were calculated from the featureCounts `Length` column and raw assigned fragment counts. "
    content += "FPKM uses each sample's assigned fragments as the library size denominator; TPM is calculated from reads per kilobase (RPK) and scaled so each sample sums to 1e6. "
    content += "FPKM/TPM are intended for expression visualization and clustering; differential expression should use the raw count matrix.\n"
    if abundance_track and abundance_track.get("status") in {"success", "partial_success"}:
        content += "A second transcript-aware abundance track was generated with Salmon/Kallisto and tximport. Its TPM matrix is the preferred matrix for publication-style expression plots only when Matrix Bridge QC passes; DE remains on featureCounts raw counts unless explicitly overridden.\n"

    if stats.get("bad_length_genes"):
        content += f"\n## Notes\n\n- {stats.get('bad_length_genes')} genes had non-positive featureCounts Length and were assigned 0 FPKM/TPM.\n"

    with open(report_file, 'w') as f:
        f.write(content)

    return report_file


def add_artifact_to_manifest(manifest: Dict, name: str, filepath: Path, artifact_type: str, **extra):
    """添加 artifact 到 manifest"""
    if filepath.exists():
        manifest["artifacts"][name] = {
            "artifact": name,
            "path": f"final/{filepath.name}",
            "full_path": str(filepath.absolute()),
            "type": artifact_type,
            "size": filepath.stat().st_size,
            "sha256": compute_file_hash(filepath),
            **extra
        }


def generate_manifest(
    output_dir: Path,
    stats: Dict[str, Any],
    versions: Dict[str, str],
    start_time: datetime,
    end_time: datetime,
    cmd: List[str],
    input_dir: Path,
    workspace_dir: Path,
    job_id: str,
    run_id: str,
    prefix: str,
    cpu: int = 4,
    sample_parallel_jobs: int = 1,
    threads_per_sample: int = 4,
    quant_mode: str = "dual",
    abundance_engine: str = "salmon",
    abundance_track: Optional[Dict[str, Any]] = None,
    sample_config: Optional[str] = None,
    library_strandness: Optional[Dict[str, Any]] = None,
    featurecounts_groups: Optional[List[Dict[str, Any]]] = None,
) -> Path:
    """生成manifest文件"""
    manifest_name = f"{prefix}.rnaseq_quant.manifest.json" if prefix else "rnaseq_quant.manifest.json"
    manifest_file = output_dir / manifest_name

    groups = featurecounts_groups or []
    grouped_featurecounts = len(groups) > 1
    if grouped_featurecounts:
        group_param_text = "; ".join(
            f"{group.get('strandness')}=-s {group.get('featurecounts_s')} ({len(group.get('samples', []))} samples)"
            for group in groups
        )
        featurecounts_param_text = f"-t exon -g gene_id -p --countReadPairs -B -C; grouped_by_library_strandness: {group_param_text}"
    else:
        default_strandness = (
            groups[0].get("strandness")
            if groups
            else ((library_strandness or {}).get("library_strandness_values", ["non_stranded"])[0]
                  if (library_strandness or {}).get("library_strandness_values")
                  else "non_stranded")
        )
        featurecounts_param_text = (
            f"-T {threads_per_sample} -t exon -g gene_id -p --countReadPairs -B -C "
            f"-s {featurecounts_strandness_value(default_strandness)}"
        )
    matrix_bridge_qc = stats.get("matrix_bridge_qc") or {}
    manifest = {
        "manifest_version": "1.0",
        "stage": "rnaseq_quant",
        "status": "success",
        "job_id": job_id,
        "run_id": run_id,
        "timestamp": end_time.isoformat(),
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "elapsed_sec": (end_time - start_time).total_seconds(),
        "cmd": cmd,
        "workspace_root": str(output_dir.parent.parent),
        "workspace": str(output_dir),
        "input_dir": str(input_dir),
        "metrics": {
            "total_genes": stats.get("total_genes", 0),
            "total_transcripts": stats.get("total_transcripts", 0),
            "expressed_genes": stats.get("expressed_genes", 0),
            "samples_processed": len(stats.get("samples", {})),
            "samples": stats.get("samples", {}),
            "expression_matrix_status": stats.get("expression_matrix_status", "unknown"),
            "abundance_track_status": (abundance_track or {}).get("status", "not_requested"),
            "gene_length_source": stats.get("gene_length_source", "featureCounts Length column"),
            "library_size_source": stats.get("library_size_source", "featureCounts assigned fragments"),
            "gene_length_match_rate": stats.get("gene_length_match_rate"),
            "bad_length_genes": stats.get("bad_length_genes", 0),
            "matrix_bridge_qc": matrix_bridge_qc,
        },
        "parameters": {
            "cpu": cpu,
            "sample_parallel_jobs": sample_parallel_jobs,
            "threads_per_sample": threads_per_sample,
            "prefix": prefix,
            "stringtie_params": f"-p {threads_per_sample}",
            "featurecounts_params": featurecounts_param_text,
            "featurecounts_strandness": ",".join((library_strandness or {}).get("library_strandness_values", [])),
            "featurecounts_grouped": grouped_featurecounts,
            "featurecounts_groups": groups,
            "library_strandness_by_sample": (library_strandness or {}).get("library_strandness_by_sample", {}),
            "library_strandness_values": (library_strandness or {}).get("library_strandness_values", []),
            "library_strandness_signature": (library_strandness or {}).get("library_strandness_signature", ""),
            "fpkm_formula": "count / (gene_length_kb * assigned_fragments_million)",
            "tpm_formula": "(count / gene_length_kb) / sum(count / gene_length_kb) * 1e6",
            "expression_matrix_usage": "FPKM/TPM are for visualization; raw count_matrix is the DE input",
            "quant_mode": quant_mode,
            "abundance_engine": abundance_engine,
            "kallisto_bias": (abundance_track or {}).get("bias_correction"),
        },
        "quantification_tracks": {
            "count_track": {
                "engine": "featureCounts",
                "status": "success",
                "role": "de_primary_raw_counts",
                "count_matrix": f"final/{prefix}.count_matrix.txt",
            },
            "abundance_track": abundance_track or {
                "engine": abundance_engine,
                "status": "not_requested" if quant_mode == "count_only" else "skipped",
            },
        },
        "matrix_policy": {
            "de_default": "featurecounts_raw_counts",
            "expression_plot_default": matrix_bridge_qc.get("recommended_expression_tpm_source", "salmon_tximport_tpm_if_bridge_qc_passes_else_featurecounts_tpm"),
            "matrix_bridge_qc": f"final/{prefix}.rnaseq_quant.matrix_bridge_qc.tsv",
            "scope_policy": "global_quantification_scope_agnostic; subset matrices after quantification",
        },
        "artifacts": {},
        "tools": versions
    }
    context = build_rnaseq_context(
        prefix=prefix,
        sample_config=sample_config,
        count_matrix=output_dir / f"{prefix}.count_matrix.txt",
        foreground_mode="global",
        stage="rnaseq_quant",
    )
    apply_rnaseq_context(manifest, context)
    if library_strandness:
        manifest["rnaseq_library_strandness_by_sample"] = library_strandness.get("library_strandness_by_sample", {})
        manifest["rnaseq_library_strandness_values"] = library_strandness.get("library_strandness_values", [])
        manifest["rnaseq_library_strandness_signature"] = library_strandness.get("library_strandness_signature", "")
        manifest["rnaseq_library_strandness_mixed"] = library_strandness.get("library_strandness_mixed", False)

    count_matrix = output_dir / f"{prefix}.count_matrix.txt"
    fpkm_matrix = output_dir / f"{prefix}.fpkm_matrix.txt"
    tpm_matrix = output_dir / f"{prefix}.tpm_matrix.txt"
    report_file = output_dir / f"{prefix}.rnaseq_quant_report.md"
    gene_lengths_file = output_dir / f"{prefix}.gene_lengths.txt"
    norm_info_file = output_dir / f"{prefix}.normalization_info.txt"
    featurecounts_summary = output_dir / f"{prefix}.featurecounts.txt.summary"
    featurecounts_command = output_dir / f"{prefix}.featurecounts.command.txt"
    matrix_bridge_qc_file = output_dir / f"{prefix}.rnaseq_quant.matrix_bridge_qc.tsv"

    add_artifact_to_manifest(manifest, "count_matrix", count_matrix, "tsv", format="tsv")
    add_artifact_to_manifest(manifest, "fpkm_matrix", fpkm_matrix, "tsv", format="tsv", status="computed_fpkm_tpm")
    add_artifact_to_manifest(manifest, "tpm_matrix", tpm_matrix, "tsv", format="tsv", status="computed_fpkm_tpm")
    add_artifact_to_manifest(manifest, "report", report_file, "markdown", format="md")
    add_artifact_to_manifest(manifest, "gene_lengths", gene_lengths_file, "tsv", format="tsv")
    add_artifact_to_manifest(manifest, "normalization_info", norm_info_file, "tsv", format="tsv")
    add_artifact_to_manifest(manifest, "featurecounts_summary", featurecounts_summary, "tsv", format="tsv")
    add_artifact_to_manifest(manifest, "featurecounts_command", featurecounts_command, "text", format="txt")
    add_artifact_to_manifest(manifest, "matrix_bridge_qc", matrix_bridge_qc_file, "tsv", format="tsv", status=matrix_bridge_qc.get("status", "unknown"))
    if grouped_featurecounts:
        for idx, group in enumerate(groups, start=1):
            suffix = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(group.get("strandness") or f"group{idx}"))
            add_artifact_to_manifest(
                manifest,
                f"featurecounts_group_{suffix}",
                Path(str(group.get("output", ""))),
                "tsv",
                format="featurecounts",
                samples=group.get("samples", []),
                strandness=group.get("strandness", ""),
                featurecounts_s=group.get("featurecounts_s", ""),
            )
            add_artifact_to_manifest(
                manifest,
                f"featurecounts_group_{suffix}_summary",
                Path(str(group.get("summary", ""))),
                "tsv",
                format="featurecounts_summary",
                samples=group.get("samples", []),
                strandness=group.get("strandness", ""),
            )
            add_artifact_to_manifest(
                manifest,
                f"featurecounts_group_{suffix}_command",
                Path(str(group.get("command_log", ""))),
                "text",
                format="txt",
                samples=group.get("samples", []),
                strandness=group.get("strandness", ""),
            )
    for artifact_name, suffix in {
        "salmon_tpm_matrix": "salmon.tpm_matrix.txt",
        "salmon_tximport_counts": "salmon.tximport_counts.txt",
        "salmon_length_matrix": "salmon.length_matrix.txt",
        "salmon_tx2gene": "salmon.tx2gene.tsv",
        "salmon_tximport_summary": "salmon.tximport_summary.tsv",
        "kallisto_tpm_matrix": "kallisto.tpm_matrix.txt",
        "kallisto_tximport_counts": "kallisto.tximport_counts.txt",
        "kallisto_length_matrix": "kallisto.length_matrix.txt",
        "kallisto_tx2gene": "kallisto.tx2gene.tsv",
        "kallisto_tximport_summary": "kallisto.tximport_summary.tsv",
    }.items():
        add_artifact_to_manifest(manifest, artifact_name, output_dir / f"{prefix}.{suffix}", "tsv", format="tsv", status=(abundance_track or {}).get("status", "unknown"))
    multiqc_name = f"{prefix}.rnaseq_quant_multiqc_report.html" if prefix else "rnaseq_quant_multiqc_report.html"
    multiqc_file = output_dir / multiqc_name
    add_artifact_to_manifest(manifest, "multiqc_report", multiqc_file, "html", format="html")
    multiqc_data = output_dir / multiqc_name.replace(".html", "_data")
    if multiqc_data.exists():
        manifest["artifacts"]["multiqc_data"] = {
            "artifact": "multiqc_data",
            "path": f"final/{multiqc_data.name}",
            "full_path": str(multiqc_data.absolute()),
            "type": "directory",
            "format": "multiqc_data"
        }

    manifest["artifacts"]["manifest"] = {
        "artifact": "manifest",
        "path": f"final/{manifest_name}",
        "type": "json",
        "format": "json"
    }

    with open(manifest_file, 'w') as f:
        json.dump(manifest, f, indent=2)

    return manifest_file


def main():
    parser = argparse.ArgumentParser(description="RNA-Seq Quantification")
    parser.add_argument("-i", "--input", required=True, help="Input directory with BAM files")
    parser.add_argument("-g", "--gtf", required=True, help="Reference genome GTF annotation")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    parser.add_argument("-p", "--prefix", default="rnaseq", help="Output prefix")
    parser.add_argument("-j", "--job-id", default="unknown", help="Job ID")
    parser.add_argument("-r", "--run-id", default="unknown", help="Run ID")
    parser.add_argument("-t", "--cpu", type=int, default=4, help="Total CPU threads for this stage")
    parser.add_argument("--sample-parallel-jobs", type=int, default=1, help="Number of samples to process concurrently (reserved; default serial)")
    parser.add_argument("--threads-per-sample", type=int, help="Threads used by each per-sample tool invocation")
    parser.add_argument("--sample-config", help="Sample config YAML for sample list")
    parser.add_argument("--quant-mode", default="dual", choices=["dual", "count_only"], help="Run count track only or count + transcript-aware abundance track")
    parser.add_argument("--abundance-engine", default="auto", choices=["auto", "salmon", "kallisto", "none"], help="Transcript-aware abundance engine; auto tries Salmon then kallisto fallback.")
    parser.add_argument("--transcriptome-fasta", help="Transcript/CDS FASTA for Salmon index; auto-detected if omitted")
    parser.add_argument("--tx2gene", help="Optional transcript-to-gene TSV with transcript_id and gene_id columns")
    parser.add_argument("--salmon-libtype", default="A", help="Salmon library type; A enables automatic inference")
    parser.add_argument("--tximport-counts-from-abundance", default="no", choices=["no", "scaledTPM", "lengthScaledTPM", "dtuScaledTPM"], help="tximport countsFromAbundance setting")
    parser.add_argument("--kallisto-bias", action="store_true", help="Enable kallisto --bias. Default is off because it is optional and can be unstable in some conda builds.")
    
    args = parser.parse_args()
    thread_plan = resolve_thread_plan(args.cpu, args.sample_parallel_jobs, args.threads_per_sample)
    threads_per_sample = thread_plan["threads_per_sample"]
    
    start_time = datetime.now()
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    gtf = Path(args.gtf)
    context_for_prefix = resolve_rnaseq_project_context(
        input_dir=Path(args.sample_config).parent if args.sample_config else input_dir,
        final_dir=input_dir,
        requested_prefix=args.prefix,
        sample_config=args.sample_config,
    )
    resolved_prefix = context_for_prefix.get("output_prefix")
    if resolved_prefix and resolved_prefix != args.prefix:
        print(
            f"[INFO] Using RNA-seq output prefix {resolved_prefix} "
            f"instead of requested {args.prefix or '<empty>'} "
            f"(source={context_for_prefix.get('source', '')})"
        )
        args.prefix = resolved_prefix
    
    # 创建输出目录
    # output_dir 是 workspace/{job_id}/rnaseq_quant (由 pipeline_backend.py 传入)
    # final_dir 是 workspace/{job_id}/final (契约要求最终产物必须放在这里)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 从 output_dir 推导 workspace_dir 和 final_dir
    # output_dir = workspace/{job_id}/rnaseq_quant
    # workspace_dir = workspace/{job_id}
    # final_dir = workspace/{job_id}/final
    workspace_dir = output_dir.parent
    final_dir = workspace_dir / "final"
    final_dir.mkdir(exist_ok=True)
    
    # 中间文件放在 rnaseq_quant/analysis/ 下（契约要求）
    quant_dir = output_dir / "analysis"
    quant_dir.mkdir(exist_ok=True)
    
    # 确定样本列表
    samples = {}
    if args.sample_config:
        with open(args.sample_config, 'r') as f:
            config = yaml.safe_load(f)
        samples = config.get("samples", {})
        if not samples:
            raise ValueError(f"Config file {args.sample_config} must contain 'samples' field")
    else:
        # 从输入目录自动检测BAM文件
        for f in sorted(input_dir.glob("*.sorted.bam")):
                sample_name = f.stem.replace(".sorted", "")
                samples[sample_name] = str(f)
    
    print(f"Processing {len(samples)} samples...")
    sample_names_for_strandness = list(map(str, samples.keys()))
    try:
        strandness_info = require_rnaseq_library_strandness(args.sample_config, sample_names_for_strandness)
    except RnaseqLibraryStrandnessError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    strandness_values = strandness_info.get("library_strandness_values", [])
    featurecounts_strandness = strandness_values[0] if strandness_values else ""
    print(f"Library strandness: {strandness_info.get('library_strandness_by_sample', {})}")
    if len(strandness_values) > 1:
        print(f"[INFO] Mixed library strandness will be counted by featureCounts groups: {strandness_values}")
    
    # 获取工具版本
    versions = get_tool_versions()
    print(f"Tool versions: {versions}")
    
    bam_files = []
    sample_names = []
    
    # 处理每个样本
    for sample_name, sample_value in samples.items():
        bam = resolve_sample_bam(input_dir, str(sample_name), sample_value)
        if bam is None:
            print(f"Warning: Unknown sample config format for sample {sample_name}, skipping...")
            continue
        
        if not bam.exists():
            print(f"Warning: BAM file not found for sample {sample_name}, skipping...")
            continue
        
        print(f"Quantifying sample: {sample_name}")
        
        # 运行StringTie
        output_gtf = quant_dir / f"{sample_name}_stringtie.gtf"
        output_cov = quant_dir / f"{sample_name}_coverage.txt"
        
        run_stringtie(bam, gtf, output_gtf, output_cov, threads_per_sample)
        
        bam_files.append(bam)
        sample_names.append(sample_name)
    
    # 运行featureCounts
    count_output = quant_dir / "featurecounts.txt"
    final_command_log = final_dir / f"{args.prefix}.featurecounts.command.txt"
    if not bam_files:
        raise RuntimeError("No valid BAM files were found; cannot run featureCounts")

    counting_gtf, featurecounts_groups = run_featurecounts_by_strandness(
        bam_files=bam_files,
        sample_names=sample_names,
        strandness_by_sample=strandness_info.get("library_strandness_by_sample", {}),
        gtf=gtf,
        count_output=count_output,
        final_dir=final_dir,
        prefix=args.prefix,
        cpu=threads_per_sample,
    )
    if counting_gtf is None:
        raise RuntimeError("featureCounts failed; cannot generate expression matrices")

    summary_output = count_output.with_name(count_output.name + ".summary")
    final_summary = final_dir / f"{args.prefix}.featurecounts.txt.summary"
    if summary_output.exists():
        shutil.copy2(summary_output, final_summary)

    print("[INFO] Calculating gene-level FPKM/TPM from featureCounts Length and counts...")
    matrix_result = generate_expression_matrices(
        count_file=count_output,
        output_dir=final_dir,
        prefix=args.prefix,
        sample_names=sample_names,
    )

    # 统计
    stats = parse_featurecounts_output(count_output, final_summary, matrix_result.get("samples"))
    for key in (
        "expression_matrix_status",
        "gene_length_source",
        "library_size_source",
        "gene_length_match_rate",
        "bad_length_genes",
    ):
        stats[key] = matrix_result.get(key)
    
    abundance_track = {"engine": args.abundance_engine, "status": "not_requested"}
    transcriptome_arg = Path(args.transcriptome_fasta) if args.transcriptome_fasta else None
    tx2gene_arg = Path(args.tx2gene) if args.tx2gene else None
    if args.quant_mode == "dual" and args.abundance_engine in {"auto", "salmon"}:
        print("[INFO] Running transcript-aware abundance track with Salmon + tximport...")
        abundance_track = run_salmon_abundance_track(
            samples=samples, input_dir=input_dir, workspace_dir=workspace_dir, final_dir=final_dir,
            analysis_dir=quant_dir, prefix=args.prefix, gtf=gtf, transcriptome_fasta=transcriptome_arg,
            tx2gene_override=tx2gene_arg, threads=threads_per_sample, libtype=args.salmon_libtype,
            counts_from_abundance=args.tximport_counts_from_abundance,
        )
        if args.abundance_engine == "auto" and abundance_track.get("status") not in {"success", "partial_success"}:
            salmon_failure = abundance_track
            print(f"[INFO] Salmon abundance track did not complete ({salmon_failure.get('reason', salmon_failure.get('status'))}); trying kallisto + tximport...")
            abundance_track = run_kallisto_abundance_track(
                samples=samples, input_dir=input_dir, workspace_dir=workspace_dir, final_dir=final_dir,
                analysis_dir=quant_dir, prefix=args.prefix, gtf=gtf, transcriptome_fasta=transcriptome_arg,
                tx2gene_override=tx2gene_arg, threads=threads_per_sample,
                counts_from_abundance=args.tximport_counts_from_abundance, bias=args.kallisto_bias,
            )
            abundance_track["fallback_from"] = salmon_failure
        print(f"[INFO] Abundance track status: {abundance_track.get('status')} ({abundance_track.get('reason', 'ok')})")
    elif args.quant_mode == "dual" and args.abundance_engine == "kallisto":
        print("[INFO] Running transcript-aware abundance track with kallisto + tximport...")
        abundance_track = run_kallisto_abundance_track(
            samples=samples, input_dir=input_dir, workspace_dir=workspace_dir, final_dir=final_dir,
            analysis_dir=quant_dir, prefix=args.prefix, gtf=gtf, transcriptome_fasta=transcriptome_arg,
            tx2gene_override=tx2gene_arg, threads=threads_per_sample,
            counts_from_abundance=args.tximport_counts_from_abundance,
        )
        print(f"[INFO] Abundance track status: {abundance_track.get('status')} ({abundance_track.get('reason', 'ok')})")
    elif args.quant_mode == "count_only" or args.abundance_engine == "none":
        abundance_track = {"engine": args.abundance_engine, "status": "not_requested", "reason": "disabled_by_cli"}

    bridge_engine = str(abundance_track.get("engine") or args.abundance_engine or "salmon")
    bridge_tpm = final_dir / f"{args.prefix}.{bridge_engine}.tpm_matrix.txt"
    bridge_tx2gene = final_dir / f"{args.prefix}.{bridge_engine}.tx2gene.tsv"
    if abundance_track.get("status") not in {"success", "partial_success"} or not bridge_tpm.exists():
        bridge_tpm_arg: Optional[Path] = None
    else:
        bridge_tpm_arg = bridge_tpm
    bridge_index_dir = Path(str(abundance_track.get("index_dir", ""))) if abundance_track.get("index_dir") else None
    matrix_bridge_qc = compute_matrix_bridge_qc(
        count_matrix=final_dir / f"{args.prefix}.count_matrix.txt",
        abundance_tpm=bridge_tpm_arg,
        tx2gene=bridge_tx2gene if bridge_tx2gene.exists() else None,
        engine=bridge_engine,
        salmon_index_dir=bridge_index_dir if bridge_engine == "salmon" else None,
        tx2gene_metrics=abundance_track.get("tx2gene") if isinstance(abundance_track.get("tx2gene"), dict) else None,
        output_path=final_dir / f"{args.prefix}.rnaseq_quant.matrix_bridge_qc.tsv",
    )
    stats["matrix_bridge_qc"] = matrix_bridge_qc
    abundance_track["matrix_bridge_qc"] = matrix_bridge_qc

    # 生成 MultiQC 报告
    multiqc_name = f"{args.prefix}.rnaseq_quant_multiqc_report.html" if args.prefix else "rnaseq_quant_multiqc_report.html"
    multiqc_report = run_multiqc([quant_dir, final_dir], final_dir, multiqc_name)
    if multiqc_report:
        print(f"MultiQC report: {multiqc_report}")

    # 生成报告
    end_time = datetime.now()
    generate_report(
        final_dir,
        stats,
        versions,
        start_time,
        end_time,
        args.prefix,
        abundance_track=abundance_track,
        library_strandness=strandness_info,
    )
    
    # 生成manifest
    # 注意：按照rnaseq_qc的实际行为（虽然与契约不符）
    # manifest文件放在final_dir，workspace字段也指向final_dir
    cmd = ["python", __file__, "-i", str(input_dir), "-g", str(gtf),
           "-o", str(output_dir), "-p", args.prefix, "-j", args.job_id, "-r", args.run_id,
           "--quant-mode", args.quant_mode, "--abundance-engine", args.abundance_engine]
    generate_manifest(
        final_dir,  # manifest文件放在final_dir
        stats, 
        versions, 
        start_time, 
        end_time,
        cmd, 
        input_dir, 
        quant_dir,  # 这个参数在generate_manifest中被忽略，实际使用output_dir(final_dir)
        args.job_id, 
        args.run_id, 
        args.prefix,
        cpu=args.cpu,
        sample_parallel_jobs=thread_plan["sample_parallel_jobs"],
        threads_per_sample=threads_per_sample,
        quant_mode=args.quant_mode,
        abundance_engine=args.abundance_engine,
        abundance_track=abundance_track,
        sample_config=args.sample_config,
        library_strandness=strandness_info,
        featurecounts_groups=featurecounts_groups,
    )
    
    print(f"RNA-QUAN completed. Results in {output_dir}")


if __name__ == "__main__":
    main()
