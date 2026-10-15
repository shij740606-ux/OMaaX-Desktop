#!/usr/bin/env python3
"""
RNA-Seq Alignment阶段：将clean reads比对到参考基因组

输入：
  - 来自RNA_QC阶段的clean reads
  - 参考基因组FASTA
  - HISAT2索引

输出：
  - 排序后的BAM文件
  - 比对报告和manifest
"""

import argparse
import json
import subprocess
import shutil
import sys
import time
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import hashlib
import os

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import re

from gfa_core.rnaseq_context import (
    apply_rnaseq_context,
    build_rnaseq_context,
    find_rnaseq_hisat2_index,
    find_rnaseq_reference_file,
    hisat2_rna_strandness_args,
    require_rnaseq_library_strandness,
    RnaseqLibraryStrandnessError,
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


def species_token_from_prefix(prefix: str) -> str:
    """Extract species token from prefixes like Os, Os.WRKY, or Os_WRKY."""
    prefix = (prefix or "").strip()
    if not prefix:
        return ""
    return re.split(r"[._]", prefix, maxsplit=1)[0].strip()


def find_canonical_genome_fasta(ref_dir: Path, species_token: str) -> Optional[Path]:
    """Find the canonical genome FASTA: {species}.genome.fasta/fa/fna."""
    if not species_token:
        return None
    for suffix in ("fasta", "fa", "fna"):
        candidate = ref_dir / f"{species_token}.genome.{suffix}"
        if candidate.is_file():
            return candidate
    return None


def canonical_genome_index_prefix(ref_dir: Path, species_token: str) -> Optional[Path]:
    """Return exact {species}.genome HISAT2 index prefix if it exists."""
    if not species_token:
        return None
    prefix = ref_dir / f"{species_token}.genome"
    if Path(f"{prefix}.1.ht2").is_file():
        return prefix
    return None


def build_hisat2_index(genome_fasta: Path, index_prefix: Path, cpu: int = 4) -> bool:
    """Build HISAT2 index from the locked genome FASTA."""
    index_prefix.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        f"{RNASEQ_BIN}/hisat2-build",
        str(genome_fasta),
        str(index_prefix),
        "-p", str(cpu),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return Path(f"{index_prefix}.1.ht2").exists()
    except subprocess.CalledProcessError as e:
        print(f"hisat2-build failed: {e.stderr}", file=sys.stderr)
        return False


def resolve_locked_hisat2_index(
    requested_index: Path,
    input_dir: Path,
    align_dir: Path,
    prefix: str,
    sample_config: Optional[str],
    cpu: int = 4,
) -> Path:
    """Lock RNA-seq alignment reference to {species}.genome.*."""
    # OMaaX: use a complete explicitly selected index without imposing file naming.
    for suffix in ("ht2", "ht2l"):
        if all(Path(f"{requested_index}.{number}.{suffix}").is_file() for number in range(1, 9)):
            return requested_index
    sample_dir = Path(sample_config).parent if sample_config else None
    context = resolve_rnaseq_project_context(
        input_dir=sample_dir or requested_index.parent,
        final_dir=input_dir,
        requested_prefix=prefix,
        sample_config=sample_config,
    )
    species_token = context.get("species_code") or species_token_from_prefix(context.get("output_prefix") or prefix)
    reference_id = context.get("reference_id", "")
    if not species_token or species_token.lower() in {"unknown", "genome", "genome_prepare", "rnaseq"}:
        raise ValueError(
            "RNA-seq Align requires a real species code from samples.yaml/config.xlsx, "
            "for example species.code: Os and rnaseq.outputPrefix: Os"
        )

    ref_dirs = []
    if requested_index:
        ref_dirs.append(requested_index.parent)
    if sample_dir:
        ref_dirs.append(sample_dir)
    ref_dirs.append(input_dir)

    seen = set()
    unique_ref_dirs = []
    for ref_dir in ref_dirs:
        resolved = ref_dir.resolve()
        if resolved not in seen:
            unique_ref_dirs.append(ref_dir)
            seen.add(resolved)

    for ref_dir in unique_ref_dirs:
        exact_index = find_rnaseq_hisat2_index(ref_dir, species_token, reference_id)
        if exact_index:
            print(f"[INFO] Locked RNA-seq genome index: {exact_index}")
            return exact_index

    for ref_dir in unique_ref_dirs:
        genome_fasta = find_rnaseq_reference_file(ref_dir, "genome", species_token, reference_id)
        if genome_fasta is None:
            continue
        build_prefix = align_dir / "index" / f"{species_token}.genome"
        exact_built = canonical_genome_index_prefix(build_prefix.parent, species_token)
        if exact_built:
            print(f"[INFO] Locked RNA-seq genome index: {exact_built}")
            return exact_built
        print(f"[INFO] Building locked HISAT2 genome index from {genome_fasta}: {build_prefix}")
        if not build_hisat2_index(genome_fasta, build_prefix, cpu):
            raise RuntimeError(f"Failed to build HISAT2 index from {genome_fasta}")
        return build_prefix

    searched = ", ".join(str(p) for p in unique_ref_dirs)
    raise FileNotFoundError(
        f"RNA-seq Align locked reference lookup failed: resolved species={species_token!r}, "
        f"reference_id={reference_id!r}; expected {species_token}.genome.fasta "
        f"(or .fa/.fna) or {species_token}.genome.1.ht2 under: {searched}. "
        f"Protein/CDS indexes such as {species_token}.pep.*.ht2 or *.cds.*.ht2 are not valid for RNA-seq alignment."
    )

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


def parse_annotation_attributes(attr_text: str) -> Dict[str, str]:
    attrs: Dict[str, str] = {}
    for item in attr_text.split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            key, value = item.split("=", 1)
            attrs[key.strip()] = value.strip().strip('"')
        elif " " in item:
            key, value = item.split(" ", 1)
            attrs[key.strip()] = value.strip().strip('"')
    return attrs


def annotation_transcript_id(feature_type: str, attr_text: str) -> Optional[str]:
    attrs = parse_annotation_attributes(attr_text)
    feature = feature_type.lower()
    if feature in {"mrna", "transcript"}:
        return attrs.get("ID") or attrs.get("transcript_id") or attrs.get("Name")
    if feature == "exon":
        parent = attrs.get("Parent") or attrs.get("transcript_id")
        if parent:
            return parent.split(",")[0]
        return attrs.get("ID")
    return None


def extract_hisat2_splice_sites(annotation: Path, output_file: Path) -> Dict[str, Any]:
    """Extract HISAT2 known splice sites from exon records in GTF/GFF3."""
    transcripts: Dict[str, List[Tuple[str, int, int, str]]] = {}
    with open(annotation, "r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 9 or parts[2].lower() != "exon":
                continue
            transcript_id = annotation_transcript_id(parts[2], parts[8])
            if not transcript_id:
                continue
            try:
                start = int(parts[3])
                end = int(parts[4])
            except ValueError:
                continue
            transcripts.setdefault(transcript_id, []).append((parts[0], start, end, parts[6] or "."))

    splice_sites = set()
    for exons in transcripts.values():
        exons = sorted(exons, key=lambda row: (row[0], row[1], row[2]))
        for left, right in zip(exons, exons[1:]):
            if left[0] != right[0]:
                continue
            intron_start = left[2] + 1
            intron_end = right[1] - 1
            if intron_start <= intron_end:
                splice_sites.add((left[0], intron_start, intron_end, left[3]))

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as out:
        for chrom, start, end, strand in sorted(splice_sites):
            out.write(f"{chrom}\t{start}\t{end}\t{strand}\n")

    return {
        "status": "generated" if splice_sites else "empty",
        "source": str(annotation),
        "path": str(output_file),
        "count": len(splice_sites),
    }


def resolve_known_splice_site(
    source_mode: str,
    known_splice_site: Optional[str],
    final_dir: Path,
    prefix: str,
) -> Tuple[Optional[Path], Dict[str, Any]]:
    mode = (source_mode or "auto").strip().lower()
    if mode not in {"auto", "off", "manual", "annotation"}:
        mode = "auto"
    info: Dict[str, Any] = {
        "mode": mode,
        "status": "disabled" if mode == "off" else "not_found",
        "source": "",
        "path": "",
        "count": 0,
    }
    if mode == "off":
        return None, info

    source_path = Path(known_splice_site).expanduser() if known_splice_site else None
    if not source_path or not source_path.exists():
        if mode == "manual":
            raise FileNotFoundError(f"--known-splice-site does not exist: {known_splice_site}")
        return None, info

    suffixes = "".join(source_path.suffixes).lower()
    if source_path.suffix.lower() in {".ss", ".txt", ".tsv"} and mode != "annotation":
        info.update({
            "status": "provided",
            "source": str(source_path),
            "path": str(source_path),
            "count": sum(1 for line in source_path.read_text(errors="ignore").splitlines() if line.strip() and not line.startswith("#")),
        })
        return source_path, info

    if source_path.suffix.lower() in {".gtf", ".gff", ".gff3"} or any(token in suffixes for token in (".gtf", ".gff")):
        out = final_dir / f"{prefix}.hisat2.splice_sites.ss"
        generated = extract_hisat2_splice_sites(source_path, out)
        info.update(generated)
        info["mode"] = mode
        return (out if out.exists() and out.stat().st_size > 0 else None), info

    if mode in {"manual", "annotation"}:
        raise ValueError(f"Unsupported known splice site source format: {source_path}")
    return None, info


def get_tool_versions() -> Dict[str, str]:
    """获取所需工具版本信息"""
    versions = {}
    
    # hisat2
    try:
        result = subprocess.run(
            [f"{RNASEQ_BIN}/hisat2", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        output = result.stdout.strip() or result.stderr.strip()
        versions["hisat2"] = output.split('\n')[0] if output else "unknown"
    except Exception as e:
        versions["hisat2"] = f"not_found: {e}"
    
    # samtools
    try:
        result = subprocess.run(
            [f"{RNASEQ_BIN}/samtools", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        output = result.stdout.strip() or result.stderr.strip()
        versions["samtools"] = output.split('\n')[0] if output else "unknown"
    except Exception as e:
        versions["samtools"] = f"not_found: {e}"

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
    except Exception as e:
        versions["multiqc"] = f"not_found: {e}"
    
    return versions


def run_hisat2_sort(
    hisat2_idx: Path,
    r1: Path,
    r2: Path,
    sorted_bam: Path,
    log_out: Path,
    cpu: int = 4,
    library_strandness: str = "non_stranded",
    known_splice_site: Optional[Path] = None,
) -> bool:
    """Run HISAT2 and stream SAM directly into samtools sort."""
    idx_prefix = str(hisat2_idx).rsplit('.', 1)[0] if hisat2_idx.suffix == '.ht2' else str(hisat2_idx)
    hisat2_cmd = [
        f"{RNASEQ_BIN}/hisat2",
        "-x", idx_prefix,
        "-1", str(r1),
        "-2", str(r2),
        "-p", str(cpu),
        "--new-summary",
    ]
    hisat2_cmd.extend(hisat2_rna_strandness_args(library_strandness))
    if known_splice_site:
        hisat2_cmd.extend(["--known-splicesite-infile", str(known_splice_site)])
    sort_cmd = [
        f"{RNASEQ_BIN}/samtools",
        "sort",
        "-@", str(cpu),
        "-o", str(sorted_bam),
        "-",
    ]

    sorted_bam.parent.mkdir(parents=True, exist_ok=True)
    with open(log_out, "w") as log:
        log.write("Command: " + " ".join(hisat2_cmd) + " | " + " ".join(sort_cmd) + "\n\n")
        hisat2_proc = subprocess.Popen(hisat2_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert hisat2_proc.stdout is not None
        sort_proc = subprocess.Popen(sort_cmd, stdin=hisat2_proc.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        hisat2_proc.stdout.close()
        sort_stdout, sort_stderr = sort_proc.communicate()
        hisat2_stderr = hisat2_proc.stderr.read() if hisat2_proc.stderr else b""
        hisat2_return = hisat2_proc.wait()

        log.write("--- HISAT2 STDERR ---\n")
        log.write(hisat2_stderr.decode("utf-8", errors="replace"))
        log.write("\n--- SAMTOOLS SORT STDOUT ---\n")
        log.write((sort_stdout or b"").decode("utf-8", errors="replace"))
        log.write("\n--- SAMTOOLS SORT STDERR ---\n")
        log.write((sort_stderr or b"").decode("utf-8", errors="replace"))

    return hisat2_return == 0 and sort_proc.returncode == 0 and sorted_bam.exists() and sorted_bam.stat().st_size > 0


def index_bam(bam: Path) -> bool:
    """索引BAM"""
    cmd = [f"{RNASEQ_BIN}/samtools", "index", str(bam)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"samtools index failed: {e.stderr}", file=sys.stderr)
        return False


def run_samtools_qc(bam: Path, flagstat_out: Path, stats_out: Path, cpu: int = 4) -> bool:
    """Generate samtools flagstat and stats files for an aligned BAM."""
    ok = True
    try:
        with open(flagstat_out, "w") as out:
            subprocess.run(
                [f"{RNASEQ_BIN}/samtools", "flagstat", "-@", str(cpu), str(bam)],
                check=True,
                stdout=out,
                stderr=subprocess.PIPE,
                text=True,
            )
    except subprocess.CalledProcessError as e:
        print(f"samtools flagstat failed for {bam.name}: {e.stderr}", file=sys.stderr)
        ok = False
    try:
        with open(stats_out, "w") as out:
            subprocess.run(
                [f"{RNASEQ_BIN}/samtools", "stats", "-@", str(cpu), str(bam)],
                check=True,
                stdout=out,
                stderr=subprocess.PIPE,
                text=True,
            )
    except subprocess.CalledProcessError as e:
        print(f"samtools stats failed for {bam.name}: {e.stderr}", file=sys.stderr)
        ok = False
    return ok


def parse_samtools_flagstat(flagstat_file: Path) -> Dict[str, Any]:
    """Parse a small subset of samtools flagstat metrics."""
    stats: Dict[str, Any] = {}
    if not flagstat_file.exists():
        return stats
    content = flagstat_file.read_text(errors="replace")
    patterns = {
        "flagstat_total": r"^(\d+) \+ \d+ in total",
        "flagstat_mapped": r"^(\d+) \+ \d+ mapped ",
        "flagstat_properly_paired": r"^(\d+) \+ \d+ properly paired ",
        "flagstat_duplicates": r"^(\d+) \+ \d+ duplicates",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, content, re.MULTILINE)
        if match:
            stats[key] = int(match.group(1))
    match = re.search(r"\((\d+(?:\.\d+)?)% : N/A\)", content)
    if match:
        stats["flagstat_mapped_rate"] = float(match.group(1))
    return stats


def run_multiqc(search_paths: Sequence[Path], output_dir: Path, report_name: str) -> Optional[Path]:
    """Run MultiQC over selected stage outputs and return the report path."""
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

def parse_hisat2_log(log_file: Path) -> Dict[str, Any]:
    """解析HISAT2日志获取统计信息"""
    stats = {
        "total_reads": 0,
        "aligned_reads": 0,
        "unaligned_reads": 0,
        "alignment_rate": 0.0,
        "unique_alignments": 0,
        "multiple_alignments": 0
    }
    
    if not log_file.exists():
        return stats
    
    with open(log_file, 'r') as f:
        content = f.read()
    
    # 解析总reads数
    match = re.search(r'(\d+) reads; of these:', content)
    if match:
        stats["total_reads"] = int(match.group(1))
    else:
        match = re.search(r'Total pairs:\s+(\d+)', content)
        if match:
            stats["total_reads"] = int(match.group(1))
    
    # 解析比对率
    match = re.search(r'([\d.]+)% overall alignment rate', content)
    if match:
        stats["alignment_rate"] = float(match.group(1))
    else:
        match = re.search(r'Overall alignment rate:\s+([\d.]+)%', content)
        if match:
            stats["alignment_rate"] = float(match.group(1))
    
    # 解析唯一比对和多比对
    match = re.search(r'(\d+) \(.*?\) aligned concordantly exactly 1 time', content)
    if match:
        stats["unique_alignments"] = int(match.group(1))
    else:
        match = re.search(r'Aligned concordantly 1 time:\s+(\d+)', content)
        if match:
            stats["unique_alignments"] = int(match.group(1))
    
    match = re.search(r'(\d+) \(.*?\) aligned concordantly >1 times', content)
    if match:
        stats["multiple_alignments"] = int(match.group(1))
    else:
        match = re.search(r'Aligned concordantly >1 times:\s+(\d+)', content)
        if match:
            stats["multiple_alignments"] = int(match.group(1))
        match = re.search(r'Aligned discordantly 1 time:\s+(\d+)', content)
        if match:
            stats["multiple_alignments"] += int(match.group(1))
    
    # 计算未比对
    if stats["total_reads"] > 0:
        aligned = stats["unique_alignments"] + stats["multiple_alignments"]
        if "Total pairs:" in content:
            stats["aligned_reads"] = aligned
            stats["unaligned_reads"] = max(stats["total_reads"] - aligned, 0)
        else:
            stats["aligned_reads"] = aligned * 2  # 双端reads
            stats["unaligned_reads"] = stats["total_reads"] * 2 - stats["aligned_reads"]
    
    return stats


def resolve_clean_fastq(input_dir: Path, sample_name: str, original_path: Path, mate: int) -> Optional[Path]:
    """Resolve clean FASTQ generated by rnaseq_qc, including prefixed final/ names."""
    candidates = []

    original_stem = original_path.name
    for suffix in (".fastq.gz", ".fq.gz", ".fastq", ".fq", ".gz"):
        if original_stem.endswith(suffix):
            original_stem = original_stem[: -len(suffix)]
            break
    candidates.extend(
        [
            input_dir / f"{original_stem}.clean.fastq.gz",
            input_dir / f"{sample_name}_{mate}.clean.fastq.gz",
            input_dir / f"{sample_name}_R{mate}.clean.fastq.gz",
        ]
    )
    candidates.extend(sorted(input_dir.glob(f"*.{sample_name}_{mate}.clean.fastq.gz")))
    candidates.extend(sorted(input_dir.glob(f"*{sample_name}*_{mate}.clean.fastq.gz")))
    candidates.extend(sorted(input_dir.glob(f"*{sample_name}*_R{mate}*.clean.fastq.gz")))

    if original_path.exists():
        candidates.append(original_path)

    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


def generate_report(
    output_dir: Path,
    sample_stats: Dict[str, Dict[str, Any]],
    versions: Dict[str, str],
    start_time: datetime,
    end_time: datetime,
    prefix: str = "",
    cpu: int = 4,
    sample_parallel_jobs: int = 1,
    threads_per_sample: int = 4,
    sample_config: Optional[str] = None,
) -> Path:
    """生成比对报告"""
    report_name = f"{prefix}.rnaseq_align_report.md" if prefix else "rnaseq_align_report.md"
    report_file = output_dir / report_name
    
    elapsed = (end_time - start_time).total_seconds()
    
    content = f"""# RNA-Seq Alignment Report

## Summary

- **Analysis Date**: {end_time.strftime("%Y-%m-%d %H:%M:%S")}
- **Total Samples**: {len(sample_stats)}
- **Total Runtime**: {elapsed:.1f} seconds

## Tool Versions

"""
    for tool, version in versions.items():
        content += f"- **{tool}**: {version}\n"
    
    content += "\n## Sample Alignment Statistics\n\n"
    content += "| Sample | Total Reads | Aligned | Unaligned | Alignment Rate |\n"
    content += "|--------|-------------|---------|-----------|----------------|\n"
    
    for sample, stats in sample_stats.items():
        content += f"| {sample} | {stats.get('total_reads', 'N/A')} | {stats.get('aligned_reads', 'N/A')} | {stats.get('unaligned_reads', 'N/A')} | {stats.get('alignment_rate', 'N/A')}% |\n"
    
    with open(report_file, 'w') as f:
        f.write(content)
    
    return report_file


def generate_manifest(
    output_dir: Path,
    sample_stats: Dict[str, Dict[str, Any]],
    versions: Dict[str, str],
    start_time: datetime,
    end_time: datetime,
    cmd: List[str],
    input_dir: Path,
    workspace_dir: Path,
    job_id: str,
    run_id: str,
    prefix: str = "",
    cpu: int = 4,
    sample_parallel_jobs: int = 1,
    threads_per_sample: int = 4,
    sample_config: Optional[str] = None,
    library_strandness: Optional[Dict[str, Any]] = None,
    splice_site_info: Optional[Dict[str, Any]] = None,
) -> Path:
    """生成manifest文件"""
    manifest_name = f"{prefix}.rnaseq_align.manifest.json" if prefix else "rnaseq_align.manifest.json"
    manifest_file = output_dir / manifest_name
    
    total_aligned = sum(s.get('aligned_reads', 0) for s in sample_stats.values())
    total_reads = sum(s.get('total_reads', 0) for s in sample_stats.values())
    
    avg_rate = (total_aligned / total_reads * 100) if total_reads > 0 else 0
    
    splice_info = splice_site_info or {}
    splice_param = ""
    if splice_info.get("path"):
        splice_param = f" --known-splicesite-infile {splice_info.get('path')}"
    manifest = {
        "manifest_version": "1.0",
        "stage": "rnaseq_align",
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
            "total_samples": len(sample_stats),
            "total_reads": total_reads,
            "aligned_reads": total_aligned,
            "average_alignment_rate": avg_rate,
            "samples": sample_stats
        },
        "parameters": {
            "cpu": cpu,
            "sample_parallel_jobs": sample_parallel_jobs,
            "threads_per_sample": threads_per_sample,
            "hisat2_params": f"-p {threads_per_sample} --new-summary{splice_param} | samtools sort",
            "samtools_params": f"sort/index/flagstat/stats -@ {threads_per_sample}",
            "splice_site_source": splice_info.get("mode", "auto"),
            "known_splice_site_status": splice_info.get("status", "not_found"),
            "known_splice_site": splice_info.get("path", ""),
            "known_splice_site_input": splice_info.get("source", ""),
            "known_splice_site_count": splice_info.get("count", 0),
            "library_strandness_by_sample": (library_strandness or {}).get("library_strandness_by_sample", {}),
            "library_strandness_values": (library_strandness or {}).get("library_strandness_values", []),
            "library_strandness_signature": (library_strandness or {}).get("library_strandness_signature", ""),
        },
        "artifacts": {},
        "tools": versions
    }
    context = build_rnaseq_context(
        prefix=prefix,
        sample_config=sample_config,
        foreground_mode="global",
        stage="rnaseq_align",
    )
    apply_rnaseq_context(manifest, context)
    if library_strandness:
        manifest["rnaseq_library_strandness_by_sample"] = library_strandness.get("library_strandness_by_sample", {})
        manifest["rnaseq_library_strandness_values"] = library_strandness.get("library_strandness_values", [])
        manifest["rnaseq_library_strandness_signature"] = library_strandness.get("library_strandness_signature", "")
        manifest["rnaseq_library_strandness_mixed"] = library_strandness.get("library_strandness_mixed", False)
    if splice_info:
        manifest["rnaseq_known_splice_site"] = splice_info
    
    # 添加artifacts（参考rnaseq_qc契约，包含完整字段）
    def add_artifact(name: str, filepath: Path, artifact_type: str, **extra):
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
    
    for sample in sample_stats.keys():
        # 排序后的BAM文件 - 在output_dir (即final_dir)
        sorted_bam = output_dir / f"{sample}.sorted.bam"
        add_artifact(f"sorted_bam_{sample}", sorted_bam, "bam", format="bam")
        
        # BAM索引 - 在output_dir
        bam_index = output_dir / f"{sample}.sorted.bam.bai"
        add_artifact(f"bam_index_{sample}", bam_index, "bai", format="bai")
        
        # HISAT2日志和 samtools QC 文件 - 在output_dir
        log_file = output_dir / f"{sample}_hisat2.log"
        add_artifact(f"alignment_log_{sample}", log_file, "log", format="txt")
        flagstat_file = output_dir / f"{sample}.flagstat.txt"
        stats_file = output_dir / f"{sample}.samtools.stats.txt"
        add_artifact(f"samtools_flagstat_{sample}", flagstat_file, "text", format="txt")
        add_artifact(f"samtools_stats_{sample}", stats_file, "text", format="txt")
    
    # 报告、MultiQC和manifest - 在output_dir
    report_name = f"{prefix}.rnaseq_align_report.md" if prefix else "rnaseq_align_report.md"
    report_file = output_dir / report_name
    add_artifact("report", report_file, "markdown", format="md")
    generated_splice = output_dir / f"{prefix}.hisat2.splice_sites.ss"
    add_artifact("known_splice_site", generated_splice, "tsv", format="hisat2_splice_site")
    multiqc_name = f"{prefix}.rnaseq_align_multiqc_report.html" if prefix else "rnaseq_align_multiqc_report.html"
    multiqc_file = output_dir / multiqc_name
    add_artifact("multiqc_report", multiqc_file, "html", format="html")
    multiqc_data = output_dir / multiqc_name.replace(".html", "_data")
    if multiqc_data.exists():
        manifest["artifacts"]["multiqc_data"] = {
            "artifact": "multiqc_data",
            "path": f"final/{multiqc_data.name}",
            "full_path": str(multiqc_data.absolute()),
            "type": "directory",
            "format": "multiqc_data"
        }
    
    # manifest自身
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
    parser = argparse.ArgumentParser(description="RNA-Seq Alignment")
    parser.add_argument("-i", "--input", required=True, help="Input directory with clean FASTQ files")
    parser.add_argument("-x", "--hisat2-index", required=True, help="HISAT2 index prefix")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    parser.add_argument("-j", "--job-id", default="unknown", help="Job ID")
    parser.add_argument("-r", "--run-id", default="unknown", help="Run ID")
    parser.add_argument("-t", "--cpu", type=int, default=4, help="Total CPU threads for this stage")
    parser.add_argument("--sample-parallel-jobs", type=int, default=1, help="Number of samples to process concurrently")
    parser.add_argument("--threads-per-sample", type=int, help="Threads used by each per-sample tool invocation")
    parser.add_argument("-p", "--prefix", default="", help="Output file prefix")
    parser.add_argument("--sample-config", help="Sample config YAML for sample list")
    parser.add_argument(
        "--splice-site-source",
        default="auto",
        choices=["auto", "off", "manual", "annotation"],
        help="HISAT2 known splice site source. auto/manual use --known-splice-site when provided; annotation extracts .ss from GTF/GFF.",
    )
    parser.add_argument(
        "--known-splice-site",
        help="HISAT2 splice site file (.ss/.txt) or annotation GTF/GFF used to generate a splice-site file.",
    )
    
    args = parser.parse_args()
    thread_plan = resolve_thread_plan(args.cpu, args.sample_parallel_jobs, args.threads_per_sample)
    threads_per_sample = thread_plan["threads_per_sample"]
    
    start_time = datetime.now()
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    hisat2_idx = Path(args.hisat2_index)
    context_for_prefix = resolve_rnaseq_project_context(
        input_dir=Path(args.sample_config).parent if args.sample_config else hisat2_idx.parent,
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
    
    # 创建工作目录
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 最终结果目录: workspace/<job_id>/final/
    # 根据契约，最终产物必须位于 workspace/<job_id>/final/
    workspace_dir = output_dir.parent  # job_id 目录
    final_dir = workspace_dir / "final"
    final_dir.mkdir(exist_ok=True)
    
    # 中间文件目录: rnaseq_align/analysis/
    align_dir = output_dir / "analysis"
    align_dir.mkdir(exist_ok=True)
    
    # 确定样本列表
    samples = {}
    if args.sample_config:
        import yaml
        with open(args.sample_config, 'r') as f:
            config = yaml.safe_load(f)
        samples = config.get("samples", {})
        if not samples:
            raise ValueError(f"Config file {args.sample_config} must contain 'samples' field")
    else:
        # 从输入目录自动检测样本
        for f in sorted(input_dir.glob("*_1.clean.fastq.gz")):
            sample_name = f.stem.replace("_1.clean", "")
            r1 = f
            r2 = input_dir / f"{sample_name}_2.clean.fastq.gz"
            if r2.exists():
                samples[sample_name] = (str(r1), str(r2))
    
    print(f"Processing {len(samples)} samples...")
    sample_names_for_strandness = list(map(str, samples.keys()))
    try:
        strandness_info = require_rnaseq_library_strandness(args.sample_config, sample_names_for_strandness)
    except RnaseqLibraryStrandnessError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    strandness_by_sample = strandness_info.get("library_strandness_by_sample", {})
    print(f"Library strandness: {strandness_by_sample}")
    
    # 获取工具版本
    versions = get_tool_versions()
    print(f"Tool versions: {versions}")
    
    try:
        hisat2_idx = resolve_locked_hisat2_index(
            requested_index=hisat2_idx,
            input_dir=input_dir,
            align_dir=align_dir,
            prefix=args.prefix,
            sample_config=args.sample_config,
            cpu=threads_per_sample,
        )
        print(f"Using locked HISAT2 genome index prefix: {hisat2_idx}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        known_splice_site, splice_site_info = resolve_known_splice_site(
            source_mode=args.splice_site_source,
            known_splice_site=args.known_splice_site,
            final_dir=final_dir,
            prefix=args.prefix,
        )
    except Exception as e:
        print(f"Error: failed to prepare HISAT2 known splice sites: {e}", file=sys.stderr)
        sys.exit(1)
    if known_splice_site:
        print(
            f"Using HISAT2 known splice sites: {known_splice_site} "
            f"(source={splice_site_info.get('source')}, count={splice_site_info.get('count')})"
        )
    else:
        print(f"HISAT2 known splice sites: {splice_site_info.get('status', 'not_found')}")
    
    sample_stats = {}

    def align_one_sample(sample_name: str, paths: Any) -> tuple[str, Optional[Dict[str, Any]], Optional[str]]:
        # 支持两种格式：列表 [r1, r2] 或字典 {"r1": ..., "r2": ...}
        if isinstance(paths, list):
            if len(paths) < 2:
                return sample_name, None, f"Sample {sample_name} has insufficient paths"
            r1_path, r2_path = paths[0], paths[1]
        elif isinstance(paths, dict):
            r1_path = paths.get("r1") or paths.get("read1")
            r2_path = paths.get("r2") or paths.get("read2")
            if not r1_path or not r2_path:
                return sample_name, None, f"Sample {sample_name} missing r1/r2 paths"
        else:
            return sample_name, None, f"Unknown format for sample {sample_name}"

        r1 = Path(r1_path)
        r2 = Path(r2_path)

        # 如果路径是相对路径，从input_dir查找
        if not r1.is_absolute():
            r1 = input_dir / r1.name
        if not r2.is_absolute():
            r2 = input_dir / r2.name

        r1 = resolve_clean_fastq(input_dir, sample_name, r1, 1) or r1
        r2 = resolve_clean_fastq(input_dir, sample_name, r2, 2) or r2

        if not r1.exists() or not r2.exists():
            return sample_name, None, f"FASTQ files not found for sample {sample_name}: R1={r1}, R2={r2}"

        print(f"Aligning sample: {sample_name}")
        print(f"  R1: {r1}")
        print(f"  R2: {r2}")
        sample_strandness = strandness_by_sample.get(str(sample_name), "")
        print(f"  library_strandness: {sample_strandness}")

        # HISAT2 比对直接 pipe 到 samtools sort，减少 SAM/BAM 中间落盘
        log_file = align_dir / f"{sample_name}_hisat2.log"
        sorted_bam = final_dir / f"{sample_name}.sorted.bam"
        if not run_hisat2_sort(
            hisat2_idx,
            r1,
            r2,
            sorted_bam,
            log_file,
            threads_per_sample,
            sample_strandness,
            known_splice_site=known_splice_site,
        ):
            return sample_name, None, f"HISAT2/samtools sort failed for sample {sample_name}"

        # 索引BAM、生成 samtools QC 文件
        index_bam(sorted_bam)
        flagstat_file = final_dir / f"{sample_name}.flagstat.txt"
        samtools_stats_file = final_dir / f"{sample_name}.samtools.stats.txt"
        run_samtools_qc(sorted_bam, flagstat_file, samtools_stats_file, threads_per_sample)

        # 复制日志到final_dir
        final_log = final_dir / f"{sample_name}_hisat2.log"
        shutil.copy2(str(log_file), str(final_log))

        # 解析日志和 samtools flagstat 获取统计
        stats = parse_hisat2_log(log_file)
        stats.update(parse_samtools_flagstat(flagstat_file))
        return sample_name, stats, None

    print(
        f"Thread plan: sample_parallel_jobs={thread_plan['sample_parallel_jobs']}, "
        f"threads_per_sample={threads_per_sample}, total_cpu={thread_plan['cpu']}"
    )
    max_workers = max(1, int(thread_plan["sample_parallel_jobs"]))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(align_one_sample, sample_name, paths): sample_name
            for sample_name, paths in samples.items()
        }
        for future in as_completed(future_map):
            sample_name = future_map[future]
            try:
                completed_name, stats, error = future.result()
            except Exception as exc:
                print(f"Warning: sample {sample_name} failed with exception: {exc}", file=sys.stderr)
                continue
            if error:
                print(f"Warning: {error}", file=sys.stderr)
                continue
            if stats is not None:
                sample_stats[completed_name] = stats

    # 生成 MultiQC 报告
    multiqc_report = run_multiqc([align_dir, final_dir], final_dir, f"{args.prefix}.rnaseq_align_multiqc_report.html" if args.prefix else "rnaseq_align_multiqc_report.html")
    if multiqc_report:
        print(f"MultiQC report: {multiqc_report}")

    # 生成报告
    if not sample_stats:
        print("Error: no samples were aligned. Check clean FASTQ names and samples.yaml.", file=sys.stderr)
        sys.exit(1)

    end_time = datetime.now()
    generate_report(final_dir, sample_stats, versions, start_time, end_time, args.prefix)
    
    # 生成manifest
    cmd = ["python", __file__, "-i", str(input_dir), "-x", str(hisat2_idx),
           "-o", str(output_dir), "-j", args.job_id, "-r", args.run_id, "-p", args.prefix,
           "-t", str(thread_plan["cpu"]),
           "--sample-parallel-jobs", str(thread_plan["sample_parallel_jobs"]),
           "--threads-per-sample", str(thread_plan["threads_per_sample"])]
    if args.sample_config:
        cmd.extend(["--sample-config", str(args.sample_config)])
    cmd.extend(["--splice-site-source", args.splice_site_source])
    if args.known_splice_site:
        cmd.extend(["--known-splice-site", str(args.known_splice_site)])
    generate_manifest(
        final_dir, sample_stats, versions, start_time, end_time,
        cmd, input_dir, output_dir, args.job_id, args.run_id, args.prefix,
        cpu=args.cpu,
        sample_parallel_jobs=thread_plan["sample_parallel_jobs"],
        threads_per_sample=threads_per_sample,
        sample_config=args.sample_config,
        library_strandness=strandness_info,
        splice_site_info=splice_site_info,
    )
    
    print(f"RNA-ALN completed. Results in {output_dir}")


if __name__ == "__main__":
    main()
