#!/usr/bin/env python3
"""
Synteny阶段：对基因家族进行种内共线性分析

输入：
  - Identify阶段的蛋白序列文件
  - GFF3注释文件
  - 基因组文件

输出：
  - {prefix}.blast (BLAST比对结果)
  - {prefix}.gff (过滤后的GFF)
  - {prefix}.dup_class.log (复制基因分类)
  - {prefix}.collinearity (MCScanX共线性结果)
  - {prefix}.synteny_report.md (分析报告)
  - {prefix}.synteny.manifest.json (机器清单)
"""

import argparse
import gzip
import json
import os
import subprocess
import sys
import time
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import hashlib
import re

try:
    from gfa_core.gene_family_scope import read_scope, sanitize_token
except Exception:
    def read_scope(scope_path):
        return json.loads(Path(scope_path).read_text(encoding="utf-8"))

    def sanitize_token(value: str) -> str:
        token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
        token = token.strip(".-_")
        return token or "NA"

# 添加conda环境bin目录到PATH
CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")


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


def _relative_to_workspace(path: Path, workspace: Path) -> str:
    try:
        return str(path.resolve().relative_to(workspace.resolve()))
    except Exception:
        return str(path)


def _scoped_copy_destination(
    source: Path,
    workspace: Path,
    prefix: str,
    scope_id: str,
    artifact_name: str,
) -> Path:
    scope_token = sanitize_token(scope_id)
    suffix = source.suffix
    lower_parts = {part.lower() for part in source.parts}
    if "final" in lower_parts:
        dest_dir = workspace / "final"
    else:
        dest_dir = workspace / "synteny" / "analysis"
    return dest_dir / f"{prefix}.{scope_token}.{sanitize_token(artifact_name)}{suffix}"


def materialize_scoped_artifacts(
    artifacts: Dict[str, Dict],
    workspace: Path,
    prefix: str,
    scope_id: str,
) -> Dict[str, Dict]:
    """Copy scope-specific outputs to stable names so later species runs cannot overwrite them."""
    if not scope_id:
        return artifacts

    scoped: Dict[str, Dict] = {}
    for name, info in artifacts.items():
        if not isinstance(info, dict):
            scoped[name] = info
            continue
        if name == "manifest":
            scoped[name] = dict(info)
            continue
        source_text = info.get("full_path") or info.get("path") or ""
        source = Path(source_text)
        if not source.is_absolute():
            source = workspace / source
        if not source.exists():
            scoped[name] = dict(info)
            continue
        dest = _scoped_copy_destination(source, workspace, prefix, scope_id, name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.absolute() != dest.absolute():
            shutil.copy2(source, dest)
        copied = dict(info)
        copied["path"] = _relative_to_workspace(dest, workspace)
        copied["full_path"] = str(dest.absolute())
        copied["size"] = dest.stat().st_size
        copied["sha256"] = compute_file_hash(dest)
        scoped[name] = copied
    return scoped


def current_input_dir(workspace: Path) -> Path:
    return workspace.parent.parent / "input" / workspace.name


def rebase_current_job_path(workspace: Path, raw_path: Any) -> Optional[Path]:
    """Map copied-job paths from old scope/manifest records back into this job."""
    path_text = str(raw_path or "").strip()
    if not path_text:
        return None
    path = Path(path_text)
    job_id = workspace.name
    input_dir = current_input_dir(workspace)

    parts = list(path.parts)
    for marker, root in (("input", input_dir), ("workspace", workspace)):
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts) and parts[idx + 1] == job_id:
                suffix = parts[idx + 2 :]
                candidate = root.joinpath(*suffix)
                if candidate.exists():
                    return candidate

    if not path.is_absolute():
        for candidate in (Path.cwd() / path, workspace / path):
            if candidate.exists():
                return candidate

    by_name_candidates = [
        input_dir / path.name,
        workspace / "final" / path.name,
        workspace / "synteny" / "analysis" / path.name,
    ]
    for candidate in by_name_candidates:
        if candidate.exists():
            return candidate

    if path.exists():
        return path
    return None


def rebase_job_path_text(workspace: Path, value: str) -> str:
    job_id = re.escape(workspace.name)
    pattern = re.compile(rf"/[^\s\"']*?(?:input|workspace)/{job_id}/[^\s\"']+")

    def replace_match(match: re.Match[str]) -> str:
        original = match.group(0).rstrip(",)")
        suffix = match.group(0)[len(original):]
        rebased = rebase_current_job_path(workspace, original)
        if rebased:
            return str(rebased.absolute()) + suffix
        return match.group(0)

    return pattern.sub(replace_match, value)


def rebase_manifest_payload_paths(workspace: Path, value: Any) -> Any:
    if isinstance(value, dict):
        return {key: rebase_manifest_payload_paths(workspace, item) for key, item in value.items()}
    if isinstance(value, list):
        return [rebase_manifest_payload_paths(workspace, item) for item in value]
    if isinstance(value, str):
        return rebase_job_path_text(workspace, value)
    return value


def get_tool_versions() -> Dict[str, str]:
    """获取所需工具版本信息"""
    versions = {}
    
    # diamond
    try:
        result = subprocess.run(
            ["diamond", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        output = result.stdout.strip() or result.stderr.strip()
        versions["diamond"] = output.split('\n')[0] if output else "unknown"
    except Exception:
        versions["diamond"] = "not_found"
    
    # blastp
    try:
        result = subprocess.run(
            ["blastp", "-version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        output = result.stdout.strip() or result.stderr.strip()
        versions["blastp"] = output.split('\n')[0] if output else "unknown"
    except Exception:
        versions["blastp"] = "not_found"
    
    # MCScanX
    try:
        result = subprocess.run(
            ["MCScanX", "-h"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        versions["MCScanX"] = "available" if result.returncode in [0, 1] else "not_found"
    except Exception:
        versions["MCScanX"] = "not_found"
    
    # duplicate_gene_classifier
    try:
        result = subprocess.run(
            ["duplicate_gene_classifier", "-h"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        versions["duplicate_gene_classifier"] = "available" if result.returncode in [0, 1] else "not_found"
    except Exception:
        versions["duplicate_gene_classifier"] = "not_found"
    
    # circos
    try:
        result = subprocess.run(
            ["circos", "-version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        output = result.stdout.strip() or result.stderr.strip()
        versions["circos"] = output.split('\n')[0] if output else "available"
    except Exception:
        versions["circos"] = "not_found"
    
    return versions


def validate_inputs(
    workspace: Path,
    prefix: str,
    input_dir: Path,
    gff3: Optional[Path] = None,
    genome: Optional[Path] = None,
    protein_fasta: Optional[Path] = None,
) -> Dict[str, Any]:
    """验证输入文件"""
    print(f"正在验证输入文件...")
    
    results = {
        "protein_fasta": None,
        "gff3": None,
        "genome": None,
        "gene_id_file": None
    }
    
    # 1. 查找蛋白序列文件。Synteny/MCScanX 需要目标物种全蛋白组，
    # scope 模式或 input 中的 *.pep.* 应优先于 identify 的家族蛋白。
    if protein_fasta:
        protein_path = Path(protein_fasta)
        if not protein_path.exists():
            raise FileNotFoundError(f"蛋白组文件不存在: {protein_fasta}")
        results["protein_fasta"] = protein_path

    species_prefix = prefix.split(".")[0] if "." in prefix else prefix
    if not results["protein_fasta"]:
        for pattern in [
            f"{species_prefix}.pep.fasta",
            f"{species_prefix}.pep.fa",
            "*.pep.fasta",
            "*.pep.fa",
            f"{prefix}*.fa",
            f"{prefix}*.fasta",
            "*.fasta"
        ]:
            files = list(input_dir.glob(pattern))
            if files:
                results["protein_fasta"] = files[0]
                break

    if not results["protein_fasta"]:
        identify_manifest = workspace / "final" / f"{prefix}.identify.manifest.json"
        if identify_manifest.exists():
            with open(identify_manifest, 'r') as f:
                m = json.load(f)
                arts = m.get("artifacts", {})
                if isinstance(arts, dict):
                    protein_path = arts.get("final_proteins", {}).get("full_path")
                    if protein_path and Path(protein_path).exists():
                        results["protein_fasta"] = Path(protein_path)

    if not results["protein_fasta"]:
        raise FileNotFoundError("找不到目标物种全蛋白组 FASTA")

    # 2. GFF3 文件
    if gff3:
        gff3_path = Path(gff3)
        if not gff3_path.exists():
            raise FileNotFoundError(f"GFF3文件不存在: {gff3}")
        results["gff3"] = gff3_path
    else:
        # 自动查找
        for pattern in ["*.gff3", "*.gff"]:
            files = list(input_dir.glob(pattern))
            if files:
                results["gff3"] = files[0]
                break

    if not results["gff3"]:
        raise FileNotFoundError("找不到GFF3注释文件")
    
    print(f"  GFF3: {results['gff3']}")
    
    # 3. 基因组文件
    if genome:
        genome_path = Path(genome)
        if not genome_path.exists():
            raise FileNotFoundError(f"基因组文件不存在: {genome}")
        results["genome"] = genome_path
    else:
        # 自动查找
        for pattern in ["*.genome.fasta", "*.genome.fa", "*.fa", "*.fasta"]:
            files = list(input_dir.glob(pattern))
            if files:
                results["genome"] = files[0]
                break
    
    if not results["genome"]:
        raise FileNotFoundError("找不到基因组文件")
    
    print(f"  基因组: {results['genome']}")
    
    # 4. 基因ID列表文件（可选）
    final_id = workspace / "final" / f"{prefix}.final_members.id"
    if final_id.exists():
        results["gene_id_file"] = final_id
    for pattern in ["*.geneID*", "*.gene_id*"]:
        if results["gene_id_file"]:
            break
        files = list(input_dir.glob(pattern))
        if files:
            results["gene_id_file"] = files[0]
            break
    
    if results["gene_id_file"]:
        print(f"  基因ID文件: {results['gene_id_file']}")
    
    return results


def first_existing_scope_file(
    workspace: Path,
    scope_data: Dict[str, Any],
    species: str,
    file_types: List[str],
) -> Optional[Path]:
    species_files = (scope_data.get("files") or {}).get(species) or {}
    for file_type in file_types:
        for record in species_files.get(file_type, []) or []:
            path_text = record.get("path") or record.get("full_path") or ""
            if path_text:
                path = rebase_current_job_path(workspace, path_text)
                if path and path.exists():
                    return path
    return None


def first_existing_path(candidates: List[Path]) -> Optional[Path]:
    for candidate in candidates:
        if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def prefer_representative_target_file(
    input_dir: Path,
    species: str,
    file_type: str,
    fallback: Optional[Path],
) -> tuple[Optional[Path], str]:
    """Prefer genome_prepare/standardized representative-transcript files for MCScanX.

    Scope records preserve uploaded raw files, which may include multiple
    isoforms per locus. MCScanX should receive one representative protein/GFF
    per gene whenever the standardized prepare/genome_prepare outputs exist.
    """
    species = str(species or "").strip()
    if not species:
        return fallback, "scope" if fallback else "missing"

    if file_type == "pep":
        candidates = [
            input_dir / f"{species}.pep.fasta",
            input_dir / f"{species}.pep.fa",
            input_dir / species / f"{species}.pep.fasta",
            input_dir / species / f"{species}.pep.fa",
            input_dir / "genome_prepare" / f"{species}_extracted.pep.fa",
            input_dir / "genome_prepare" / f"{species}.pep.fasta",
            input_dir / "genome_prepare" / f"{species}.pep.fa",
        ]
    elif file_type == "gff":
        candidates = [
            input_dir / f"{species}_final.gff3",
            input_dir / f"{species}_final.gff",
            input_dir / f"{species}.gff3",
            input_dir / f"{species}.gff",
            input_dir / species / f"{species}.gff3",
            input_dir / species / f"{species}.gff",
            input_dir / "genome_prepare" / f"{species}.gff3.tmp",
            input_dir / "genome_prepare" / f"{species}_final.gff3",
        ]
    elif file_type == "genome":
        candidates = [
            input_dir / f"{species}.genome.fasta",
            input_dir / f"{species}.genome.fa",
            input_dir / f"{species}.genome.fasta.gz",
            input_dir / f"{species}.genome.fa.gz",
            input_dir / f"{species}.fasta",
            input_dir / f"{species}.fa",
            input_dir / species / f"{species}.genome.fasta",
            input_dir / species / f"{species}.genome.fa",
            input_dir / species / f"{species}.genome.fasta.gz",
            input_dir / species / f"{species}.genome.fa.gz",
            input_dir / species / f"{species}.fasta",
            input_dir / species / f"{species}.fa",
            input_dir / "genome_prepare" / f"{species}.genome.fasta",
            input_dir / "genome_prepare" / f"{species}.genome.fa",
            input_dir / "genome_prepare" / f"{species}.fasta",
            input_dir / "genome_prepare" / f"{species}.fa",
        ]
    else:
        return fallback, "scope" if fallback else "missing"

    representative = first_existing_path(candidates)
    if representative:
        return representative, "representative"
    return fallback, "scope_raw_fallback" if fallback else "missing"


def resolve_synteny_scope_inputs(
    workspace: Path,
    scope_file: Optional[Path],
) -> tuple[Dict[str, Any], Optional[Path], Optional[Path], Optional[Path]]:
    if not scope_file:
        return {}, None, None, None
    scope_data = read_scope(scope_file)
    targets = [str(x).strip() for x in (scope_data.get("target_species") or []) if str(x).strip()]
    if len(targets) != 1:
        raise RuntimeError(
            "Synteny 阶段是种内共线性，一次只能分析一个目标物种；"
            f"当前 scope target_species={targets or '空'}。请在弹窗中只选择一个 target，或分别运行每个目标物种。"
    )
    species = targets[0]
    protein_fasta = first_existing_scope_file(workspace, scope_data, species, ["pep"])
    gff3_file = first_existing_scope_file(workspace, scope_data, species, ["gff"])
    genome_file = first_existing_scope_file(workspace, scope_data, species, ["genome"])
    if not protein_fasta:
        raise RuntimeError(f"Synteny scope 未找到目标物种 {species} 的全蛋白组 pep FASTA")
    if not gff3_file:
        raise RuntimeError(f"Synteny scope 未找到目标物种 {species} 的 GFF/GFF3 文件")
    if not genome_file:
        raise RuntimeError(f"Synteny scope 未找到目标物种 {species} 的 genome FASTA 文件")
    return scope_data, protein_fasta, gff3_file, genome_file


def open_text_maybe_gzip(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def write_blast_safe_protein_fasta(input_fasta: Path, output_fasta: Path) -> tuple[Path, Dict[str, int]]:
    """Write a DIAMOND/BLAST-safe protein FASTA.

    Whole proteomes produced by different preparation tools may contain terminal
    stop markers (`*` or `.`) or other non-amino-acid characters. DIAMOND rejects
    those records during makedb, causing the pipeline to fall back to very slow
    BLASTP. For synteny, remove non-letter characters while keeping the first
    FASTA header token as the stable protein ID.
    """
    output_fasta.parent.mkdir(parents=True, exist_ok=True)
    records = 0
    removed_chars = 0
    total_aa = 0
    header = ""
    seq_parts: list[str] = []

    def flush_record(out_handle) -> None:
        nonlocal records, removed_chars, total_aa, header, seq_parts
        if not header:
            return
        raw_seq = "".join(seq_parts)
        clean_seq = "".join(ch for ch in raw_seq.upper() if ch.isalpha())
        removed_chars += len(raw_seq) - len(clean_seq)
        if clean_seq:
            out_handle.write(f">{header}\n")
            for i in range(0, len(clean_seq), 80):
                out_handle.write(clean_seq[i:i + 80] + "\n")
            records += 1
            total_aa += len(clean_seq)
        header = ""
        seq_parts = []

    with open_text_maybe_gzip(input_fasta) as in_handle, output_fasta.open("w", encoding="utf-8") as out_handle:
        for raw in in_handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                flush_record(out_handle)
                header = line[1:].split()[0]
                seq_parts = []
            else:
                seq_parts.append(line)
        flush_record(out_handle)

    if records == 0:
        raise RuntimeError(f"No valid protein records after cleaning: {input_fasta}")
    return output_fasta, {
        "records": records,
        "removed_chars": removed_chars,
        "total_aa": total_aa,
    }


def prepare_directories(workspace: Path) -> Dict[str, Path]:
    """创建Synteny阶段的标准目录结构"""
    paths = {
        "analysis_dir": workspace / "synteny" / "analysis",
        "final_dir": workspace / "final",
        "logs_dir": workspace / "logs",
    }
    
    print("创建目录结构:")
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        print(f"  {name}: {path}")
    
    return paths


def run_diamond_blastp(
    input_fasta: Path,
    output_blast: Path,
    cpu: int = 8,
    evalue: float = 1e-10,
    max_target_seqs: int = 5
) -> Dict[str, Any]:
    """运行diamond blastp比对"""
    print(f"运行Diamond BLASTP: {input_fasta} -> {output_blast}")
    
    # 构建数据库
    db_path = input_fasta.with_suffix('')
    db_cmd = [
        "diamond", "makedb",
        "--in", str(input_fasta),
        "--db", str(db_path)
    ]
    
    start_time = time.time()
    
    # 创建数据库
    try:
        result = subprocess.run(
            db_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False
        )
    except FileNotFoundError:
        print(f"  Diamond未安装，无法使用")
        return {
            "success": False,
            "command": " ".join(db_cmd),
            "stderr": "diamond not found"
        }
    
    if result.returncode != 0:
        print(f"  Diamond makedb 失败: {result.stderr}")
        return {
            "success": False,
            "command": " ".join(db_cmd),
            "stderr": result.stderr
        }
    
    # 运行比对
    blast_cmd = [
        "diamond", "blastp",
        "--query", str(input_fasta),
        "--db", str(db_path),
        "--out", str(output_blast),
        "--outfmt", "6",
        "--max-target-seqs", str(max_target_seqs),
        "--evalue", str(evalue),
        "--threads", str(cpu)
    ]
    
    try:
        result = subprocess.run(
            blast_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False
        )
    except FileNotFoundError:
        print(f"  Diamond未安装，无法使用")
        return {
            "success": False,
            "command": " ".join(blast_cmd),
            "stderr": "diamond not found"
        }
    
    elapsed = time.time() - start_time
    
    if result.returncode == 0:
        print(f"  Diamond成功完成，耗时: {elapsed:.1f}秒")
        return {
            "success": True,
            "command": " ".join(blast_cmd),
            "returncode": result.returncode,
            "stderr": result.stderr,
            "output_file": output_blast,
            "elapsed_sec": elapsed
        }
    else:
        print(f"  Diamond失败，返回码: {result.returncode}")
        return {
            "success": False,
            "command": " ".join(blast_cmd),
            "returncode": result.returncode,
            "stderr": result.stderr,
            "output_file": None,
            "elapsed_sec": elapsed
        }


def run_blastp(
    input_fasta: Path,
    output_blast: Path,
    cpu: int = 8,
    evalue: float = 1e-10,
    max_alignments: int = 5
) -> Dict[str, Any]:
    """运行 NCBI blastp 比对（备选方案）"""
    print(f"运行NCBI BLASTP: {input_fasta} -> {output_blast}")
    
    # 构建数据库
    db_path = input_fasta.with_suffix('')
    db_cmd = [
        "makeblastdb",
        "-in", str(input_fasta),
        "-dbtype", "prot",
        "-out", str(db_path)
    ]
    
    start_time = time.time()
    
    # 创建数据库
    try:
        result = subprocess.run(
            db_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False
        )
    except FileNotFoundError:
        print(f"  makeblastdb未安装，无法使用NCBI blastp")
        return {
            "success": False,
            "command": " ".join(db_cmd),
            "stderr": "makeblastdb not found - NCBI BLAST+ not installed"
        }
    
    if result.returncode != 0:
        print(f"  makeblastdb 失败: {result.stderr}")
        return {
            "success": False,
            "command": " ".join(db_cmd),
            "stderr": result.stderr
        }
    
    # 运行比对
    blast_cmd = [
        "blastp",
        "-query", str(input_fasta),
        "-db", str(db_path),
        "-outfmt", "6",
        "-out", str(output_blast),
        "-num_threads", str(cpu),
        "-num_alignments", str(max_alignments),
        "-evalue", str(evalue)
    ]
    
    result = subprocess.run(
        blast_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False
    )
    
    elapsed = time.time() - start_time
    
    if result.returncode == 0:
        print(f"  BLASTP成功完成，耗时: {elapsed:.1f}秒")
        return {
            "success": True,
            "command": " ".join(blast_cmd),
            "returncode": result.returncode,
            "stderr": result.stderr,
            "output_file": output_blast,
            "elapsed_sec": elapsed
        }
    else:
        print(f"  BLASTP失败，返回码: {result.returncode}")
        return {
            "success": False,
            "command": " ".join(blast_cmd),
            "returncode": result.returncode,
            "stderr": result.stderr,
            "output_file": None,
            "elapsed_sec": elapsed
        }


def read_fasta_ids_for_matching(fasta_path: Path) -> set[str]:
    """Read FASTA primary IDs used by BLAST/MCScanX."""
    ids: set[str] = set()
    try:
        with open_text_maybe_gzip(fasta_path) as handle:
            for line in handle:
                if line.startswith(">"):
                    token = line[1:].strip().split()[0]
                    if token:
                        ids.add(token)
    except Exception:
        pass
    return ids


def parse_gff3_attributes(attr_text: str) -> Dict[str, List[str]]:
    attrs: Dict[str, List[str]] = {}
    for item in attr_text.strip().split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            key, value = item.split("=", 1)
        elif " " in item:
            key, value = item.split(" ", 1)
        else:
            continue
        key = key.strip()
        values = [v.strip() for v in value.strip().split(",") if v.strip()]
        if key and values:
            attrs.setdefault(key, []).extend(values)
    return attrs


def gff_id_candidates(attrs: Dict[str, List[str]]) -> List[str]:
    ordered_keys = [
        "Name",
        "transcript_id",
        "protein_id",
        "locus_tag",
        "gene_id",
        "gene",
        "ID",
        "Parent",
    ]
    candidates: List[str] = []
    for key in ordered_keys:
        for value in attrs.get(key, []):
            candidates.append(value)
            for prefix in ("gene:", "transcript:", "rna:", "mRNA:", "CDS:", "protein:"):
                if value.startswith(prefix):
                    candidates.append(value[len(prefix):])
            for prefix in ("gene-", "rna-", "cds-", "mrna-", "protein-"):
                if value.lower().startswith(prefix):
                    candidates.append(value[len(prefix):])

    seen = set()
    unique: List[str] = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            unique.append(candidate)
            seen.add(candidate)
    return unique


def choose_gff_feature_id(attrs: Dict[str, List[str]], preferred_ids: Optional[set[str]]) -> str:
    candidates = gff_id_candidates(attrs)
    if preferred_ids:
        for candidate in candidates:
            if candidate in preferred_ids:
                return candidate
        preferred_base = {item.split(".")[0]: item for item in preferred_ids}
        for candidate in candidates:
            match = preferred_base.get(candidate.split(".")[0])
            if match:
                return match
    if candidates:
        return candidates[0]
    return ""


def filter_gff3(
    gff3_file: Path,
    output_gff: Path,
    feature_type: str = "mRNA",
    preferred_ids: Optional[set[str]] = None,
) -> Dict[str, Any]:
    """Filter GFF3 and use IDs compatible with the stage protein FASTA."""
    print(f"过滤GFF3文件: {gff3_file} -> {output_gff}")

    start_time = time.time()

    try:
        gene_count = 0
        matched_count = 0
        with open_text_maybe_gzip(gff3_file) as src, open(output_gff, "w", encoding="utf-8") as dst:
            for raw in src:
                if not raw.strip() or raw.startswith("#"):
                    continue
                parts = raw.rstrip("\n").split("\t")
                if len(parts) < 9 or parts[2] != feature_type:
                    continue
                attrs = parse_gff3_attributes(parts[8])
                feature_id = choose_gff_feature_id(attrs, preferred_ids)
                if not feature_id:
                    continue
                if preferred_ids and feature_id in preferred_ids:
                    matched_count += 1
                dst.write(f"{parts[0]}\t{feature_id}\t{parts[3]}\t{parts[4]}\n")
                gene_count += 1

        elapsed = time.time() - start_time

        print(f"  成功过滤 {gene_count} 个基因，匹配FASTA ID {matched_count} 个，耗时: {elapsed:.1f}秒")

        return {
            "success": True,
            "output_file": output_gff,
            "gene_count": gene_count,
            "matched_fasta_id_count": matched_count,
            "elapsed_sec": elapsed,
        }

    except Exception as e:
        return {
            "success": False,
            "stderr": str(e),
        }

def run_duplicate_classifier(
    work_dir: Path,
    prefix: str,
    blast_file: Path,
    gff_file: Path
) -> Dict[str, Any]:
    """运行duplicate_gene_classifier进行复制基因分类"""
    print(f"运行Duplicate Gene Classifier...")
    
    start_time = time.time()
    
    try:
        result = subprocess.run(
            f"duplicate_gene_classifier {prefix}",
            capture_output=True,
            text=True,
            cwd=str(work_dir),
            check=False,
            shell=True
        )
        
        elapsed = time.time() - start_time
        
        # 输出文件
        log_file = work_dir / f"{prefix}.dup_class.log"
        gene_type_file = work_dir / "gene_type"
        
        # 写入日志
        with open(log_file, 'w') as f:
            f.write(result.stdout)
            if result.stderr:
                f.write("\n=== STDERR ===\n")
        print(f"  完成，耗时: {elapsed:.1f}秒")
        print(f"  日志文件: {log_file}")
        
        return {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "log_file": log_file,
            "gene_type_file": gene_type_file,
            "elapsed_sec": elapsed
        }
        
    except Exception as e:
        return {
            "success": False,
            "stderr": str(e)
        }



def run_mcscanx(
    work_dir: Path,
    prefix: str
) -> Dict[str, Any]:
    """运行MCScanX进行共线性分析"""
    print(f"运行MCScanX...")
    
    # 命令格式: MCScanX ./
    # 需要在包含 {prefix}.gff 和 {prefix}.blast 的目录中运行
    
    cmd = [
        "MCScanX",
        prefix
    ]
    
    start_time = time.time()
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(work_dir),
            check=False
        )
        
        elapsed = time.time() - start_time
        
        # MCScanX 可能输出到 .collinearity 或 {prefix}.collinearity
        # 检查两种可能性
        collinearity_file = work_dir / f"{prefix}.collinearity"
        if not collinearity_file.exists():
            collinearity_file = work_dir / ".collinearity"
        
        print(f"  完成，耗时: {elapsed:.1f}秒")
        print(f"  共线性文件: {collinearity_file}")
        
        return {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "collinearity_file": collinearity_file,
            "elapsed_sec": elapsed
        }
        
    except Exception as e:
        return {
            "success": False,
            "stderr": str(e)
        }




def find_scope_gene_id_file(workspace: Path, prefix: str, scope_data: Dict[str, Any]) -> Optional[Path]:
    """Find family member IDs for scoped target-species synteny highlighting."""
    if not scope_data:
        return None
    final_dir = workspace / "final"
    family = str(scope_data.get("family") or "").strip()
    targets = [str(x).strip() for x in (scope_data.get("target_species") or []) if str(x).strip()]
    candidates: List[Path] = []
    if targets and family:
        for species in targets:
            candidates.extend([
                final_dir / f"Target.{species}.{family}.final_members.id",
                final_dir / f"{species}.{family}.final_members.id",
                final_dir / f"{species}.{prefix.split('.')[-1]}.final_members.id",
            ])
    candidates.append(final_dir / f"{prefix}.final_members.id")
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


def _read_id_set(path: Optional[Path]) -> set[str]:
    ids: set[str] = set()
    if not path or not path.exists():
        return ids
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            token = raw.strip().split()[0] if raw.strip() else ""
            if token:
                ids.add(token)
    return ids


def _parse_mcscanx_gff(gff_file: Path) -> tuple[Dict[str, tuple[str, int, int]], Dict[str, int]]:
    genes: Dict[str, tuple[str, int, int]] = {}
    chrom_lengths: Dict[str, int] = {}
    with gff_file.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            if not raw.strip() or raw.startswith("#"):
                continue
            parts = raw.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            chrom, gene_id = parts[0], parts[1]
            try:
                start, end = int(float(parts[2])), int(float(parts[3]))
            except ValueError:
                continue
            if start > end:
                start, end = end, start
            genes[gene_id] = (chrom, start, end)
            chrom_lengths[chrom] = max(chrom_lengths.get(chrom, 0), end)
    return genes, chrom_lengths


def _parse_collinearity_pairs(collinearity_file: Path) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    with collinearity_file.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 3:
                pairs.append((parts[1], parts[2]))
    return pairs


def generate_synteny_fallback_plot(
    gff_file: Path,
    collinearity_file: Path,
    output_prefix: Path,
    gene_id_file: Optional[Path] = None,
    max_background_links: int = 3000,
) -> Dict[str, Any]:
    """Generate a chromosome-link synteny plot when Circos is unavailable.

    This is a fallback visualization from the same MCScanX GFF/collinearity
    outputs: chromosome models from gene coordinates, grey collinear gene-pair
    links, and red links/ticks for pairs touching target family members.
    """
    genes, chrom_lengths = _parse_mcscanx_gff(gff_file)
    pairs = [(a, b) for a, b in _parse_collinearity_pairs(collinearity_file) if a in genes and b in genes]
    family_ids = _read_id_set(gene_id_file)
    if not genes or not chrom_lengths or not pairs:
        return {
            "success": False,
            "reason": "fallback_plot_missing_gff_or_collinearity_pairs",
            "genes": len(genes),
            "chromosomes": len(chrom_lengths),
            "pairs": len(pairs),
        }

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.path import Path as MplPath
    from matplotlib.patches import PathPatch

    def chrom_key(name: str):
        m = re.search(r"(\d+)$", name)
        return (0, int(m.group(1))) if m else (1, name)

    chroms = sorted(chrom_lengths, key=chrom_key)
    n_chrom = len(chroms)
    max_len = max(chrom_lengths.values()) or 1
    fig_h = max(6.0, min(18.0, n_chrom * 0.45 + 2.2))
    fig, ax = plt.subplots(figsize=(11.5, fig_h), dpi=220)
    y_for = {chrom: n_chrom - i for i, chrom in enumerate(chroms)}

    for chrom in chroms:
        y = y_for[chrom]
        length = chrom_lengths[chrom]
        ax.plot([0, length], [y, y], color="#263238", linewidth=4, solid_capstyle="round", zorder=3)
        ax.text(-max_len * 0.015, y, chrom, ha="right", va="center", fontsize=8, color="#263238")

    highlighted = []
    background = []
    for gene_a, gene_b in pairs:
        is_family = gene_a in family_ids or gene_b in family_ids
        (highlighted if is_family else background).append((gene_a, gene_b))

    if len(background) > max_background_links:
        step = max(1, len(background) // max_background_links)
        background = background[::step][:max_background_links]

    def draw_pair(gene_a: str, gene_b: str, color: str, alpha: float, lw: float, z: int):
        chr_a, start_a, end_a = genes[gene_a]
        chr_b, start_b, end_b = genes[gene_b]
        x1 = (start_a + end_a) / 2
        x2 = (start_b + end_b) / 2
        y1 = y_for[chr_a]
        y2 = y_for[chr_b]
        if chr_a == chr_b:
            lift = 0.18 + min(0.75, abs(x2 - x1) / max_len * 2.0)
            verts = [(x1, y1), ((x1 + x2) / 2, y1 + lift), (x2, y2)]
        else:
            lift = 0.22
            verts = [(x1, y1), (x1, (y1 + y2) / 2 + lift), (x2, y2)]
        patch = PathPatch(
            MplPath(verts, [MplPath.MOVETO, MplPath.CURVE3, MplPath.CURVE3]),
            facecolor="none",
            edgecolor=color,
            linewidth=lw,
            alpha=alpha,
            zorder=z,
        )
        ax.add_patch(patch)

    for gene_a, gene_b in background:
        draw_pair(gene_a, gene_b, "#9E9E9E", 0.18, 0.35, 1)
    for gene_a, gene_b in highlighted:
        draw_pair(gene_a, gene_b, "#D32F2F", 0.75, 0.85, 2)

    plotted_family = 0
    for gene_id in sorted(family_ids):
        if gene_id not in genes:
            continue
        chrom, start, end = genes[gene_id]
        y = y_for[chrom]
        x = (start + end) / 2
        ax.scatter([x], [y], s=10, color="#B71C1C", zorder=4)
        plotted_family += 1
        if plotted_family <= 80:
            ax.text(x, y + 0.10, gene_id, rotation=60, ha="left", va="bottom", fontsize=4.5, color="#B71C1C")

    ax.set_xlim(-max_len * 0.08, max_len * 1.03)
    ax.set_ylim(0.4, n_chrom + 0.9)
    ax.set_xlabel("Genomic position (bp)", fontsize=9)
    ax.set_yticks([])
    title = "Intraspecies synteny (MCScanX chromosome-link fallback)"
    subtitle = f"{len(pairs)} collinear gene pairs"
    if family_ids:
        subtitle += f"; {len(highlighted)} pairs involving target-family genes"
    ax.set_title(f"{title}\n{subtitle}", fontsize=11, pad=12)
    ax.spines[["left", "right", "top"]].set_visible(False)
    ax.grid(axis="x", color="#EEEEEE", linewidth=0.6)
    fig.tight_layout()

    png_file = output_prefix.with_suffix(".synteny.png")
    pdf_file = output_prefix.with_suffix(".synteny.pdf")
    fig.savefig(png_file, bbox_inches="tight")
    fig.savefig(pdf_file, bbox_inches="tight")
    plt.close(fig)
    return {
        "success": png_file.exists() and pdf_file.exists(),
        "png_file": png_file,
        "pdf_file": pdf_file,
        "total_pairs_plotted_source": len(pairs),
        "background_pairs_plotted": len(background),
        "highlighted_pairs_plotted": len(highlighted),
        "family_genes_plotted": plotted_family,
        "fallback_reason": "circos_output_unavailable",
    }


def _is_primary_chromosome_name(chrom: str) -> bool:
    name = str(chrom or "").strip()
    if re.fullmatch(r"\d+", name):
        return True
    return bool(re.fullmatch(r"chr(?:omosome)?[\s_.-]*\d+[A-Za-z]?", name, flags=re.IGNORECASE))


def _is_obvious_non_nuclear_sequence(chrom: str) -> bool:
    name = str(chrom or "").strip().lower()
    if re.search(r"(chloroplast|plastid|mitochond|mitoch|scaffold|contig|unplaced|unlocalized|random)", name):
        return True
    return bool(re.fullmatch(r"(chrm|chrmt|mt|mito|chrc|chrpt|pt|cp)", name))


def _select_circos_chromosomes(parsed: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], Dict[str, Any]]:
    """Keep the nuclear chromosome background while dropping organelles/tiny accessions.

    The fallback to all parsed records is intentional: species with unusual naming
    should not fail synteny visualization just because a conservative filter cannot
    identify the main chromosome set.
    """
    records: list[tuple[str, int]] = []
    for chrom, length_text in parsed:
        try:
            length = int(float(length_text))
        except (TypeError, ValueError):
            continue
        if chrom and length > 0:
            records.append((chrom, length))

    total = len(parsed)
    if not records:
        return parsed, {
            "circos_chromosomes_total": total,
            "circos_chromosomes_plotted": len(parsed),
            "circos_chromosomes_filtered": 0,
            "circos_chromosomes_filter_mode": "fallback_unparsed_lengths",
            "circos_chromosomes_filtered_names": [],
        }

    selected: list[tuple[str, int]] = []
    mode = ""
    named_primary = [(chrom, length) for chrom, length in records if _is_primary_chromosome_name(chrom)]
    if named_primary:
        selected = named_primary
        mode = "primary_name"
    else:
        nuclear_candidates = [
            (chrom, length)
            for chrom, length in records
            if not _is_obvious_non_nuclear_sequence(chrom)
        ]
        length_source = nuclear_candidates or records
        sorted_lengths = sorted((length for _, length in length_source), reverse=True)
        median_length = sorted_lengths[len(sorted_lengths) // 2]
        max_length = sorted_lengths[0]
        threshold = max(1_000_000, int(median_length * 0.10), int(max_length * 0.02))
        selected = [(chrom, length) for chrom, length in length_source if length >= threshold]
        mode = "length_distribution"

    if not selected:
        selected = records
        mode = "fallback_all_records"

    selected_names = {chrom for chrom, _ in selected}
    filtered_names = [chrom for chrom, _ in records if chrom not in selected_names]
    selected_text = [(chrom, str(length)) for chrom, length in selected]
    return selected_text, {
        "circos_chromosomes_total": total,
        "circos_chromosomes_plotted": len(selected_text),
        "circos_chromosomes_filtered": max(0, total - len(selected_text)),
        "circos_chromosomes_filter_mode": mode,
        "circos_chromosomes_filtered_names": filtered_names,
    }

def _format_circos_points(value: Any, default: float) -> str:
    if value is None or value == "":
        value = default
    text = str(value).strip()
    if text.endswith("p"):
        return text
    try:
        return f"{float(text):g}p"
    except ValueError:
        return f"{default:g}p"


def _format_circos_radius(value: Any, default: float) -> str:
    if value is None or value == "":
        value = default
    text = str(value).strip()
    if "r" in text or "p" in text:
        return text
    try:
        return f"{float(text):.2f}r"
    except ValueError:
        return f"{default:.2f}r"


def run_circos_prepare(
    gff_file: Path,
    collinearity_file: Path,
    gene_id_file: Path,
    output_prefix: Path,
    genome_file: Path,
    gene_label_size: Any = 38,
    gene_track_r0: Any = 1.03,
    gene_track_r1: Any = 1.30,
    chrom_label_radius: Any = "1r - 60p",
    chrom_label_size: Any = 40,
    ideogram_thickness: Any = 90,
) -> Dict[str, Any]:
    """准备Circos绘图数据"""
    print(f"准备Circos绘图数据...")
    gene_label_size_text = _format_circos_points(gene_label_size, 38)
    gene_track_r0_text = _format_circos_radius(gene_track_r0, 1.03)
    gene_track_r1_text = _format_circos_radius(gene_track_r1, 1.30)
    chrom_label_radius_text = _format_circos_radius(chrom_label_radius, 1.0)
    chrom_label_size_text = _format_circos_points(chrom_label_size, 40)
    ideogram_thickness_text = _format_circos_points(ideogram_thickness, 90)
    
    # 1. 计算染色体长度
    genome_len_file = output_prefix.parent / "genome.len"
    cmd = [
        "seqkit", "fx2tab", "-l", "-n", "-i", str(genome_file)
    ]
    
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False
    )
    
    if result.returncode != 0:
        return {"success": False, "stderr": result.stderr}
    
    lines = result.stdout.strip().split('\n')
    parsed = []
    for line in lines:
        parts = line.split('\t')
        if len(parts) >= 2:
            parsed.append((parts[0], parts[1]))
    chromosomes, chromosome_metrics = _select_circos_chromosomes(parsed)
    with open(genome_len_file, 'w') as f:
        for chrom, length in chromosomes:
            f.write(f"{chrom}\t{length}\n")
    
    # 2. 生成核型文件
    karyotype_file = output_prefix.with_suffix('.karyotype.circos.txt')
    cmd2 = [
        "awk",
        '{print "chr\t-\t"$1"\t"$1"\t0\t"$2"\tchr"NR}',
        str(genome_len_file)
    ]
    
    result2 = subprocess.run(
        cmd2,
        capture_output=True,
        text=True,
        check=False
    )
    
    if result2.returncode == 0:
        with open(karyotype_file, 'w') as f:
            f.write(result2.stdout)
    
    # 3. 使用get_mcscanx_circos.py生成绘图数据
    circos_script = Path("scripts/get_mcscanx_circos.py")
    
    if circos_script.exists() and gene_id_file.exists():
        cmd3 = [
            sys.executable,
            str(circos_script),
            str(gff_file),
            str(collinearity_file),
            str(gene_id_file),
            str(output_prefix)
        ]
        
        result3 = subprocess.run(
            cmd3,
            capture_output=True,
            text=True,
            check=False
        )
        
        print(f"  Circos数据准备完成")
        
        # 4. 创建Circos配置文件并绘图
        circos_dir = output_prefix.parent / "circos_config"
        circos_dir.mkdir(exist_ok=True)
        
        # 创建简化版circos.conf配置文件
        circos_conf = circos_dir / "circos.conf"
        karyotype_rel = karyotype_file.name
        links_rel = f"{output_prefix.name}.links.txt"
        gene_text_rel = f"{output_prefix.name}.geneText.txt"
        gene_block_links_rel = f"{output_prefix.name}.geneBlocklinks.txt"
        
        # 检查SPL基因相关文件是否存在
        gene_text_file = Path(f"{output_prefix}.geneText.txt")
        gene_block_links_file = Path(f"{output_prefix}.geneBlocklinks.txt")
        has_spl_data = gene_text_file.exists() and gene_block_links_file.exists()
        
        with open(circos_conf, 'w') as f:
            f.write(f"""# Circos configuration file
# Generated by BotanicalBaseAgent Synteny pipeline

karyotype = {karyotype_rel}
chromosomes_units = 1000000
chromosomes_display_default = yes

<<include etc/colors_fonts_patterns.conf>>

<ideogram>
<spacing>
default = 0.01r
</spacing>
radius = 0.5r
thickness = {ideogram_thickness_text}
fill = yes
fill_color = black
stroke_color = dgrey
stroke_thickness = 1p
show_label = yes
label_font = default
label_radius = {chrom_label_radius_text}
label_size = {chrom_label_size_text}
label_parallel = yes
</ideogram>

<ticks>
radius = 1r
color = black
thickness = 2p
multiplier = 1e-6
orientation = out
format = %d
<tick>
spacing = 10u
size = 15p
show_label = no
format = %d
</tick>
</ticks>
""")
            # 添加plots区块显示基因ID
            if has_spl_data:
                f.write(f"""
<plots>
<plot>
type = text
file = {gene_text_rel}
color = red
r1 = {gene_track_r1_text}
r0 = {gene_track_r0_text}
label_font = default
label_size = {gene_label_size_text}
label_snuggle = yes
show_links = yes
link_dims = 5p,4p,60p,2p,2p
link_thickness = 2p
link_color = green
</plot>
</plots>
""")
            # 添加links区块
            f.write(f"""
<links>
<link>
file = {links_rel}
radius = 0.9r
bezier_radius = 0r
color = lgrey
thickness = 2
ribbon = yes
</link>
""")
            # 添加SPL基因高亮连线
            if has_spl_data:
                f.write(f"""<link>
file = {gene_block_links_rel}
radius = 0.9r
bezier_radius = 0r
color = red
thickness = 2
ribbon = yes
</link>
""")
            f.write(f"""</links>

<image>
dir = .
file = {output_prefix.name}.circos
png = yes
svg = yes
radius = 3000p
background = white
angle_offset = -90
</image>

<<include etc/housekeeping.conf>>
""")
        
        # 复制Circos输入文件到circos_config目录。Circos以cwd运行，
        # 配置中使用相对文件名，必须覆盖旧的空文件并同步links/text输入。
        import shutil
        for src in (
            karyotype_file,
            Path(f"{output_prefix}.links.txt"),
            Path(f"{output_prefix}.geneText.txt"),
            Path(f"{output_prefix}.geneBlocklinks.txt"),
        ):
            if src.exists():
                shutil.copy2(src, circos_dir / src.name)
        
        # 运行Circos绘图
        print(f"  运行Circos绘图...")
        cmd4 = [
            "circos",
            "-conf", str(circos_conf.resolve())
        ]
        
        circos_env = os.environ.copy()
        conda_bin = str(CONDA_PREFIX / "bin")
        circos_env["PATH"] = conda_bin + os.pathsep + circos_env.get("PATH", "")
        circos_env.pop("PERL5LIB", None)
        result4 = subprocess.run(
            cmd4,
            capture_output=True,
            text=True,
            check=False,
            cwd=str(circos_dir),  # 在circos_config目录中运行
            env=circos_env
        )
        
        # 检查输出文件（在circos_config目录中）
        svg_file_in_config = circos_dir / f"{output_prefix.name}.circos.svg"
        png_file_in_config = circos_dir / f"{output_prefix.name}.circos.png"
        
        # 输出文件目标位置（analysis目录）
        svg_file = output_prefix.parent / f"{output_prefix.name}.circos.svg"
        png_file = output_prefix.parent / f"{output_prefix.name}.circos.png"
        
        # 移动输出文件到analysis目录
        if svg_file_in_config.exists():
            shutil.move(svg_file_in_config, svg_file)
        if png_file_in_config.exists():
            shutil.move(png_file_in_config, png_file)
        
        # 如果只有SVG没有PNG，尝试转换
        if svg_file.exists() and not png_file.exists():
            print(f"  转换SVG为PNG...")
            convert_cmd = [
                "convert",
                "-density", "300",
                str(svg_file),
                str(png_file)
            ]
            subprocess.run(convert_cmd, capture_output=True, check=False)
        
        circos_success = result4.returncode == 0
        print(f"  Circos绘图完成" if circos_success else f"  Circos绘图失败: {result4.stderr[:200]}")
        
        return {
            "success": circos_success,
            "circos_returncode": result4.returncode,
            "circos_stdout": result4.stdout,
            "circos_stderr": result4.stderr,
            "genome_len_file": genome_len_file,
            "karyotype_file": karyotype_file,
            "links_file": f"{output_prefix}.links.txt",
            "genepair_file": f"{output_prefix}.genepair.txt",
            "circos_conf": circos_conf,
            "svg_file": svg_file if svg_file.exists() else None,
            "png_file": png_file if png_file.exists() else None,
            **chromosome_metrics,
        }
    
    return {
        "success": True,
        "genome_len_file": genome_len_file,
        "karyotype_file": karyotype_file,
        **chromosome_metrics,
    }


def _create_circos_config_files(config_dir: Path):
    """创建Circos所需的配置文件"""
    # ideogram.conf - 使用内联配置，避免嵌套include
    with open(config_dir / "ideogram.conf", 'w') as f:
        f.write("""<ideogram>
<spacing>
default = 0.01r
break = 0.5r
</spacing>

radius = 0.90r
thickness = 100p
fill = yes
fill_color = black

show_label = yes
label_font = default
label_radius = 1.075r
label_size = 10p
label_parallel = yes

show_bands = yes
fill_bands = yes
thickness = 2p
</ideogram>
""")
    
    # ticks.conf
    with open(config_dir / "ticks.conf", 'w') as f:
        f.write("""show_ticks = yes
show_tick_labels = yes

<ticks>
radius = 1r
color = black
thickness = 2p

<tick>
spacing = 5u
size = 10p
show_label = no
</tick>

<tick>
spacing = 25u
size = 15p
show_label = yes
label_size = 10p
label_offset = 5p
format = %d
</tick>
</ticks>
""")
    
    # image.conf
    with open(config_dir / "image.conf", 'w') as f:
        f.write("""dir = .
file = circos
png = yes
svg = yes
radius = 1500p
background = white
angle_offset = -90
""")
    
    # colors_fonts_patterns.conf - 使用系统默认配置
    with open(config_dir / "colors_fonts_patterns.conf", 'w') as f:
        f.write("""# 使用Circos系统默认颜色配置
# 这些文件在Circos安装目录的etc/下
<<include etc/colors.ucsc.conf>>
<<include etc/colors.brewer.conf>>
<<include etc/colors.hsv.conf>>

<<include etc/fonts.conf>>

<<include etc/patterns.conf>>
""")
    
    # housekeeping.conf
    with open(config_dir / "housekeeping.conf", 'w') as f:
        f.write("""anglestep = 0.5
minslicestep = 10
beziersamples = 40

warnings = 1
debug_group = summary
debug_verbosity = 5

text_snuggle_method = max
snuggle_tolerance = 1
snuggle_sampling = 2
snuggle_refine = yes
snuggle_link_overlap_tolerance = 1

offsets = 0

file_delim = \\s
file_delim_collapse = yes

list_record_delim = \\s*[;,]\\s*
list_field_delim = \\s*[:=]\\s*
options_record_delim = \\s*[;,]\\s*
options_field_delim = \\s*[:=]\\s*
""")


def compute_blast_stats(blast_file: Path) -> Dict[str, Any]:
    """计算BLAST比对统计"""
    try:
        with open(blast_file, 'r') as f:
            lines = f.readlines()
        
        # 统计比对数量
        return {
            "total_alignments": len(lines),
            "unique_queries": len(set(l.split('\t')[0] for l in lines if l.strip())),
            "unique_subjects": len(set(l.split('\t')[1] for l in lines if l.strip()))
        }
    except Exception:
        return {}


def compute_collinearity_stats(collinearity_file: Path) -> Dict[str, Any]:
    """计算共线性统计"""
    try:
        with open(collinearity_file, 'r') as f:
            content = f.read()
        
        # 统计共线性块数量
        blocks = content.count('##')
        
        # 统计基因对数量
        gene_pairs = 0
        for line in content.split('\n'):
            if line.startswith('#') or not line.strip():
                continue
            parts = line.split('\t')
            if len(parts) >= 3:
                gene_pairs += 1
        
        return {
            "synteny_blocks": blocks,
            "gene_pairs": gene_pairs
        }
    except Exception:
        return {}


def write_report(
    workspace: Path,
    prefix: str,
    metrics: Dict[str, Any],
    parameters: Dict[str, Any],
    tool_versions: Dict[str, str],
    diamond_result: Dict[str, Any],
    gff_filter_result: Dict[str, Any],
    classifier_result: Dict[str, Any],
    mcscanx_result: Dict[str, Any],
    artifacts: Dict[str, Dict]
) -> Path:
    """写入人可读的分析报告"""
    report_path = workspace / "final" / f"{prefix}.synteny_report.md"
    
    print(f"生成分析报告: {report_path}")
    
    with open(report_path, "w") as f:
        f.write(f"# 共线性分析报告 - {prefix}\n\n")
        f.write(f"**生成时间**: {datetime.now().isoformat()}\n\n")
        
        f.write("## 1. 分析概述\n")
        f.write(f"- **分析阶段**: Synteny\n")
        f.write(f"- **分析状态**: {'成功' if mcscanx_result.get('success', False) else '部分成功'}\n")
        f.write(f"- **基因数量**: {metrics.get('gene_count', 'N/A')}\n")
        f.write(f"- **BLAST比对数**: {metrics.get('blast_alignments', 'N/A')}\n")
        f.write(f"- **共线性块数**: {metrics.get('synteny_blocks', 'N/A')}\n\n")
        
        f.write("## 2. 工具版本\n")
        for tool, version in tool_versions.items():
            f.write(f"- **{tool}**: {version}\n")
        f.write("\n")
        
        f.write("## 3. 执行参数\n")
        for param, value in parameters.items():
            f.write(f"- **{param}**: {value}\n")
        f.write("\n")
        
        f.write("## 4. 执行命令\n")
        f.write("### BLAST比对\n")
        if diamond_result.get('command'):
            f.write(f"```bash\n{diamond_result.get('command')}\n```\n\n")
        
        f.write("### GFF过滤\n")
        f.write(f"```bash\nawk -F '\\t' ...\n```\n\n")
        
        f.write("### 复制基因分类\n")
        if classifier_result.get('returncode') == 0:
            f.write("✅ **复制基因分类成功**\n\n")
        
        f.write("### MCScanX共线性分析\n")
        if mcscanx_result.get('success'):
            f.write("✅ **共线性分析成功**\n\n")
        
        f.write("### Circos共线性绘图\n")
        if artifacts.get('circos_svg') or artifacts.get('circos_png'):
            f.write("✅ **Circos共线性绘图成功**\n\n")
            f.write("Circos图展示了基因在染色体间的共线性关系，包括：\n")
            f.write("- **染色体圈图**: 外圈显示染色体及其长度\n")
            f.write("- **共线性连线**: 弧线连接具有共线性关系的基因对\n")
            f.write("- **基因标签**: 显示关键基因的名称和位置\n")
            if metrics.get('circos_chromosomes_total') is not None:
                f.write(
                    "- **染色体筛选**: 圈图保留主核染色体背景，"
                    "过滤掉疑似细胞器/非主序列 "
                    f"({metrics.get('circos_chromosomes_plotted', 'N/A')}/"
                    f"{metrics.get('circos_chromosomes_total', 'N/A')} plotted)\n"
                )
            f.write("\n")
        else:
            f.write("⚠️ **Circos绘图未生成**\n\n")
        
        f.write("## 5. 产出文件\n")
        for artifact_name, artifact_info in artifacts.items():
            if isinstance(artifact_info, dict):
                f.write(f"- **{artifact_name}**: {artifact_info.get('path', 'N/A')}\n")
        f.write("\n")
        
        f.write("## 6. 详细统计\n")
        f.write("```json\n")
        f.write(json.dumps(metrics, indent=2, ensure_ascii=False))
        f.write("\n```\n")
    
    return report_path


def write_manifest(
    workspace: Path,
    job_id: str,
    run_id: str,
    prefix: str,
    start_time: datetime,
    end_time: datetime,
    artifacts: Dict[str, Dict],
    metrics: Dict[str, Any],
    parameters: Dict[str, Any],
    tool_versions: Dict[str, str],
    cmd_history: List[str],
    status: str = "success"
) -> Path:
    """写入符合schema v1的manifest文件"""
    elapsed_sec = (end_time - start_time).total_seconds()
    rebased_parameters = rebase_manifest_payload_paths(workspace, parameters)
    rebased_artifacts = rebase_manifest_payload_paths(workspace, artifacts)
    rebased_metrics = rebase_manifest_payload_paths(workspace, metrics)
    
    manifest_data = {
        "manifest_version": "1.0",
        "job_id": job_id,
        "stage": "synteny",
        "run_id": run_id,
        "prefix": prefix,
        
        "timestamp": end_time.isoformat(),
        "status": status,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "elapsed_sec": round(elapsed_sec, 2),
        
        "cmd": rebase_manifest_payload_paths(workspace, cmd_history),
        "tool_versions": tool_versions,
        
        "workspace_root": str(workspace.parent.absolute()),
        "workspace": str((workspace / "final").absolute()),
        "input_dir": str(current_input_dir(workspace).absolute()),
        
        "inputs": [
            {"type": "protein_fasta", "path": rebased_parameters.get("protein_fasta", "")},
            {"type": "gff3", "path": rebased_parameters.get("gff3", "")},
            {"type": "genome", "path": rebased_parameters.get("genome", "")}
        ],
        
        "artifacts": rebased_artifacts,
        
        "parameters": rebased_parameters,
        "metrics": rebased_metrics,
        
        "provenance": {
            "derived_from": f"{prefix}.identify.manifest.json",
            "stage_sequence": ["identify", "phylogeny", "synteny"]
        }
    }
    for key in (
        "scope_id",
        "scope_mode",
        "scope_file",
        "analysis_route",
        "member_set_id",
        "target_species",
        "reference_species",
        "comparative_species",
        "selected_species",
        "family",
    ):
        if key in rebased_parameters:
            manifest_data[key] = rebased_parameters[key]
    
    manifest_path = workspace / "final" / f"{prefix}.synteny.manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"生成Manifest文件: {manifest_path}")
    
    with open(manifest_path, "w", encoding='utf-8') as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)

    if parameters.get("scope_id"):
        scoped_manifest_path = workspace / "final" / f"{prefix}.{sanitize_token(parameters.get('scope_id', ''))}.synteny.manifest.json"
        scoped_manifest_data = dict(manifest_data)
        scoped_manifest_data["artifacts"] = materialize_scoped_artifacts(
            artifacts=rebased_artifacts,
            workspace=workspace,
            prefix=prefix,
            scope_id=parameters.get("scope_id", ""),
        )
        with open(scoped_manifest_path, "w", encoding='utf-8') as f:
            json.dump(scoped_manifest_data, f, indent=2, ensure_ascii=False)
    
    return manifest_path


def main():
    """主函数：执行Synteny分析流程"""
    parser = argparse.ArgumentParser(
        description="Synteny阶段：种内共线性分析",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python run_synteny.py --prefix Ft.SPL --workspace /path/to/workspace --cpu 4
  
输入要求：
  - Identify阶段必须已成功运行
  - 需要GFF3注释文件和基因组文件
  
输出产物：
  - final/{prefix}.blast (BLAST比对结果)
  - final/{prefix}.gff (过滤后的GFF)
  - final/{prefix}.collinearity (MCScanX共线性结果)
  - final/{prefix}.synteny_report.md (分析报告)
  - final/{prefix}.synteny.manifest.json (机器清单)
        """
    )
    
    parser.add_argument("--prefix", required=True,
                        help="分析前缀，格式：物种.家族（如 Ft.SPL）")
    parser.add_argument("--workspace", required=True,
                        help="工作空间目录（workspace/<job_id>/)")
    parser.add_argument("--input-dir",
                        help="输入目录（默认：workspace/<job_id>/../input/<job_id>）")
    parser.add_argument("--cpu", type=int, default=8,
                        help="CPU核心数（默认：8）")
    parser.add_argument("--gff3",
                        help="GFF3注释文件路径")
    parser.add_argument("--genome",
                        help="基因组FASTA文件路径")
    parser.add_argument("--evalue", type=float, default=1e-10,
                        help="BLAST E-value阈值（默认：1e-10）")
    parser.add_argument("--max-target-seqs", type=int, default=5,
                        help="最大目标序列数（默认：5）")
    parser.add_argument("--use-blastp", action="store_true",
                        help="使用NCBI blastp代替diamond")
    parser.add_argument("--log-file",
                        help="日志文件路径")
    parser.add_argument("--gene-id-file",
                        help="基因ID列表文件路径（可选，用于Circos绘图）")
    parser.add_argument("--scope-file",
                        help="Gene-family scope JSON from resolve_scope.py")
    parser.add_argument("--analysis-route",
                        help="Scoped analysis route recorded in manifest, e.g. single_species")
    parser.add_argument("--member-set-id",
                        help="Scoped member set identity recorded in manifest")
    args = parser.parse_args()
    
    # 记录开始时间
    start_time = datetime.now()
    run_id = f"synteny_{start_time.strftime('%Y%m%d_%H%M%S_%f')[:-3]}"
    
    # 设置工作空间
    workspace = Path(args.workspace)
    job_id = workspace.name
    
    # 设置输入目录
    if args.input_dir:
        input_dir = Path(args.input_dir)
    else:
        project_root = workspace.parent.parent
        input_dir = project_root / "input" / job_id

    scope_data: Dict[str, Any] = {}
    scope_protein: Optional[Path] = None
    scope_gff3: Optional[Path] = None
    scope_genome: Optional[Path] = None
    analysis_route = str(args.analysis_route or "").strip()
    member_set_id = str(args.member_set_id or "").strip()
    
    print("=" * 60)
    print(f"🚀 启动共线性分析")
    print("=" * 60)
    print(f"分析前缀: {args.prefix}")
    print(f"工作空间: {workspace}")
    print(f"任务ID: {job_id}")
    print(f"运行ID: {run_id}")
    print(f"CPU核心: {args.cpu}")
    
    cmd_history = []
    
    try:
        scope_file: Optional[Path] = None
        if args.scope_file:
            scope_file = Path(args.scope_file)
            if not scope_file.exists():
                raise FileNotFoundError(f"Scope file not found: {args.scope_file}")
            scope_data = read_scope(scope_file)
            scope_data, scope_protein, scope_gff3, scope_genome = resolve_synteny_scope_inputs(workspace, scope_file)
            scope_targets = [str(x).strip() for x in (scope_data.get("target_species") or []) if str(x).strip()]
            scope_target_species = scope_targets[0] if scope_targets else ""
            scope_protein, scope_protein_source = prefer_representative_target_file(
                input_dir, scope_target_species, "pep", scope_protein
            )
            scope_gff3, scope_gff_source = prefer_representative_target_file(
                input_dir, scope_target_species, "gff", scope_gff3
            )
            scope_genome, scope_genome_source = prefer_representative_target_file(
                input_dir, scope_target_species, "genome", scope_genome
            )
            if scope_protein_source == "representative":
                print(f"  Scope代表转录本PEP: {scope_protein}")
            elif scope_protein_source == "scope_raw_fallback":
                print(f"  ⚠️ 未找到 {scope_target_species} 代表转录本PEP，回退使用scope原始PEP: {scope_protein}")
            if scope_gff_source == "representative":
                print(f"  Scope代表转录本GFF: {scope_gff3}")
            elif scope_gff_source == "scope_raw_fallback":
                print(f"  ⚠️ 未找到 {scope_target_species} 代表转录本GFF，回退使用scope原始GFF: {scope_gff3}")
            if scope_genome_source == "representative":
                print(f"  Scope代表基因组FASTA: {scope_genome}")
            elif scope_genome_source == "scope_raw_fallback":
                print(f"  ⚠️ 未找到 {scope_target_species} 代表基因组FASTA，回退使用scope原始genome: {scope_genome}")
            if not analysis_route:
                analysis_route = "single_species"
            if not member_set_id and scope_data.get("scope_id") and analysis_route:
                member_set_id = f"{scope_data.get('scope_id')}:{analysis_route}:all_members"

        # 1. 准备目录结构
        print("\n📁 准备目录结构...")
        paths = prepare_directories(workspace)
        
        # 2. 获取工具版本
        print("\n🔧 检查工具版本...")
        tool_versions = get_tool_versions()
        for tool, version in tool_versions.items():
            print(f"   {tool}: {version}")
        
        # 3. 验证输入文件
        print("\n🔍 验证输入文件...")
        inputs = validate_inputs(
            workspace=workspace,
            prefix=args.prefix,
            input_dir=input_dir,
            gff3=args.gff3 or scope_gff3,
            genome=args.genome or scope_genome,
            protein_fasta=scope_protein,
        )
        if not inputs.get("gene_id_file") and scope_data:
            scoped_gene_id_file = find_scope_gene_id_file(workspace, args.prefix, scope_data)
            if scoped_gene_id_file:
                inputs["gene_id_file"] = scoped_gene_id_file
                print(f"  Scope基因ID文件: {scoped_gene_id_file}")
        original_protein_fasta = inputs["protein_fasta"]
        safe_protein_fasta, safe_stats = write_blast_safe_protein_fasta(
            Path(original_protein_fasta),
            paths["analysis_dir"] / f"{args.prefix}.blast_safe.pep.fa",
        )
        inputs["protein_fasta"] = safe_protein_fasta
        print(
            "  BLAST-safe protein FASTA: "
            f"{safe_protein_fasta} "
            f"(records={safe_stats['records']}, removed_chars={safe_stats['removed_chars']})"
        )
        
        # 4. 运行BLAST比对
        print("\n🔄 运行BLAST比对...")
        blast_file = paths["analysis_dir"] / f"{args.prefix}.blast"
        
        if args.use_blastp:
            blast_result = run_blastp(
                input_fasta=inputs["protein_fasta"],
                output_blast=blast_file,
                cpu=args.cpu,
                evalue=args.evalue,
                max_alignments=args.max_target_seqs
            )
        else:
            # 优先尝试diamond
            blast_result = run_diamond_blastp(
                input_fasta=inputs["protein_fasta"],
                output_blast=blast_file,
                cpu=args.cpu,
                evalue=args.evalue,
                max_target_seqs=args.max_target_seqs
            )
            
            # 如果diamond失败，尝试blastp
            if not blast_result.get("success", False):
                print("  Diamond失败，尝试使用NCBI blastp...")
                blast_result = run_blastp(
                    input_fasta=inputs["protein_fasta"],
                    output_blast=blast_file,
                    cpu=args.cpu,
                    evalue=args.evalue,
                    max_alignments=args.max_target_seqs
                )
        
        if not blast_result.get("success", False):
            print("❌ BLAST比对失败")
            raise RuntimeError(f"BLAST失败: {blast_result.get('stderr', '未知错误')}")
        
        cmd_history.append(blast_result.get("command", ""))
        
        # 5. 复制blast文件到工作目录
        work_blast = paths["analysis_dir"] / f"{args.prefix}.blast"
        import shutil
        # 只有在源和目标不同时才复制
        if blast_file != work_blast:
            shutil.copy(blast_file, work_blast)
        # 6. 过滤GFF3
        print("\n🔍 过滤GFF3文件...")
        gff_file = paths["analysis_dir"] / f"{args.prefix}.gff"
        preferred_gff_ids = read_fasta_ids_for_matching(inputs["protein_fasta"])
        gff_filter_result = filter_gff3(
            gff3_file=inputs["gff3"],
            output_gff=gff_file,
            preferred_ids=preferred_gff_ids,
        )
        
        if not gff_filter_result.get("success", False):
            print("❌ GFF3过滤失败")
            raise RuntimeError(f"GFF3过滤失败")
        
        work_gff = paths["analysis_dir"] / f"{args.prefix}.gff"
        # 只有在源和目标不同时才复制
        if gff_file != work_gff:
            shutil.copy(gff_file, work_gff)
        
        # 7. 运行duplicate_gene_classifier
        print("\n🔍 运行复制基因分类...")

        # 对于 MCScanX 和 duplicate_gene_classifier，使用物种前缀（如 Ft）而非完整前缀（如 Ft.SPL）
        # 因为这些工具在处理带点的前缀时会有问题
        species_prefix = args.prefix.split('.')[0] if '.' in args.prefix else args.prefix

        # 复制文件并重命名为物种前缀
        import shutil
        work_blast_species = paths["analysis_dir"] / f"{species_prefix}.blast"
        work_gff_species = paths["analysis_dir"] / f"{species_prefix}.gff"
        if work_blast != work_blast_species:
            shutil.copy(work_blast, work_blast_species)
        if work_gff != work_gff_species:
            shutil.copy(work_gff, work_gff_species)

        # 使用物种前缀运行 duplicate_gene_classifier
        classifier_result = run_duplicate_classifier(
            work_dir=paths["analysis_dir"],
            prefix=species_prefix,
            blast_file=work_blast_species,
            gff_file=work_gff_species
        )
        # 检查是否成功 - 只要有返回结果就算成功
        if classifier_result and (classifier_result.get("returncode") == 0 or classifier_result.get("log_file")):

            print("  复制基因分类完成")
        
        # 8. 运行MCScanX
        print("\n🔍 运行MCScanX共线性分析...")
        mcscanx_result = run_mcscanx(
            work_dir=paths["analysis_dir"],
            prefix=species_prefix
        )
        
        if mcscanx_result.get("success", False):
            print("  共线性分析完成")
        else:
            print("  ⚠️ MCScanX可能存在问题")
        
        # 9. 准备Circos绘图数据（可选）
        circos_data = {}
        if mcscanx_result.get("success", False) and inputs.get("gene_id_file"):
            print("\n🔍 准备Circos绘图数据...")
            circos_data = run_circos_prepare(
                gff_file=gff_file,
                collinearity_file=mcscanx_result.get("collinearity_file", paths["analysis_dir"] / f"{args.prefix}.collinearity"),
                gene_id_file=inputs["gene_id_file"],
                output_prefix=paths["analysis_dir"] / f"{args.prefix}.circos",
                genome_file=inputs["genome"]
            )
        
        # 10. 计算统计信息
        print("\n📊 计算统计信息...")
        metrics = {}
        
        # BLAST统计
        if blast_file.exists():
            blast_stats = compute_blast_stats(blast_file)
            metrics.update(blast_stats)
        
        # GFF统计
        metrics["gene_count"] = gff_filter_result.get("gene_count", 0)
        
        # 共线性统计 - 使用 species_prefix
        collinearity_file = paths["analysis_dir"] / f"{species_prefix}.collinearity"
        if collinearity_file.exists():
            collinearity_stats = compute_collinearity_stats(collinearity_file)
            metrics.update(collinearity_stats)

        for key in (
            "circos_chromosomes_total",
            "circos_chromosomes_plotted",
            "circos_chromosomes_filtered",
            "circos_chromosomes_filter_mode",
            "circos_chromosomes_filtered_names",
        ):
            if key in circos_data:
                metrics[key] = circos_data[key]

        # 10.5 Circos不可用或未产图时，使用MCScanX结果生成标准染色体-link后备图
        fallback_plot = {}
        circos_has_image = bool(
            circos_data.get("success", False)
            and (
                (circos_data.get("svg_file") and Path(circos_data["svg_file"]).exists())
                or (circos_data.get("png_file") and Path(circos_data["png_file"]).exists())
            )
        )
        if collinearity_file.exists() and not circos_has_image:
            print("\n🎨 Circos未产出图片，生成MCScanX染色体-link后备图...")
            fallback_plot = generate_synteny_fallback_plot(
                gff_file=gff_file,
                collinearity_file=collinearity_file,
                output_prefix=paths["analysis_dir"] / f"{args.prefix}.fallback",
                gene_id_file=inputs.get("gene_id_file"),
            )
            if fallback_plot.get("success"):
                print(
                    "  后备图完成: "
                    f"highlighted_pairs={fallback_plot.get('highlighted_pairs_plotted', 0)}, "
                    f"family_genes={fallback_plot.get('family_genes_plotted', 0)}"
                )
            else:
                print(f"  ⚠️ 后备图未生成: {fallback_plot.get('reason', 'unknown')}")

        # 11. 准备artifacts
        artifacts = {}
        
        # blast文件
        if blast_file.exists():
            artifacts["blast"] = {
                "artifact": "blast",
                "path": f"synteny/analysis/{args.prefix}.blast",
                "full_path": str(blast_file.absolute()),
                "type": "blast",
                "size": blast_file.stat().st_size,
                "sha256": compute_file_hash(blast_file)
            }
        
        # gff文件
        if gff_file.exists():
            artifacts["gff"] = {
                "artifact": "gff",
                "path": f"synteny/analysis/{args.prefix}.gff",
                "full_path": str(gff_file.absolute()),
                "type": "gff",
                "size": gff_file.stat().st_size,
                "sha256": compute_file_hash(gff_file)
            }
        
        # 共线性文件
        if collinearity_file.exists():
            artifacts["collinearity"] = {
                "artifact": "collinearity",
                "path": f"synteny/analysis/{species_prefix}.collinearity",
                "full_path": str(collinearity_file.absolute()),
                "type": "text",
                "size": collinearity_file.stat().st_size,
                "sha256": compute_file_hash(collinearity_file)
            }
        
        # 复制基因分类日志 - 使用 species_prefix
        dup_log = paths["analysis_dir"] / f"{species_prefix}.dup_class.log"
        if not dup_log.exists():
            # 也检查 args.prefix 版本
            dup_log = paths["analysis_dir"] / f"{args.prefix}.dup_class.log"

        if dup_log.exists():
            artifacts["dup_class_log"] = {
                "artifact": "dup_class_log",
                "path": f"synteny/analysis/{args.prefix}.dup_class.log",
                "full_path": str(dup_log.absolute()),
                "type": "text",
                "size": dup_log.stat().st_size,
                "sha256": compute_file_hash(dup_log)
            }
        
        # Circos绘图文件
        if circos_data.get("success", False):
            # SVG文件 - 复制到final目录
            if circos_data.get("svg_file") and Path(circos_data["svg_file"]).exists():
                svg_file = Path(circos_data["svg_file"])
                final_svg = paths["final_dir"] / f"{args.prefix}.circos.svg"
                shutil.copy(svg_file, final_svg)
                artifacts["circos_svg"] = {
                    "artifact": "circos_svg",
                    "path": f"final/{args.prefix}.circos.svg",
                    "full_path": str(final_svg.absolute()),
                    "type": "image",
                    "format": "svg",
                    "size": final_svg.stat().st_size,
                    "sha256": compute_file_hash(final_svg)
                }
            
            # PNG文件 - 复制到final目录
            if circos_data.get("png_file") and Path(circos_data["png_file"]).exists():
                png_file = Path(circos_data["png_file"])
                final_png = paths["final_dir"] / f"{args.prefix}.circos.png"
                shutil.copy(png_file, final_png)
                artifacts["circos_png"] = {
                    "artifact": "circos_png",
                    "path": f"final/{args.prefix}.circos.png",
                    "full_path": str(final_png.absolute()),
                    "type": "image",
                    "format": "png",
                    "size": final_png.stat().st_size,
                    "sha256": compute_file_hash(final_png)
                }
            
            # Links文件
            links_file = paths["analysis_dir"] / f"{args.prefix}.circos.links.txt"
            if links_file.exists():
                artifacts["circos_links"] = {
                    "artifact": "circos_links",
                    "path": f"synteny/analysis/{args.prefix}.circos.links.txt",
                    "full_path": str(links_file.absolute()),
                    "type": "text",
                    "size": links_file.stat().st_size,
                    "sha256": compute_file_hash(links_file)
                }

        if fallback_plot.get("success"):
            for key, fmt in (("png_file", "png"), ("pdf_file", "pdf")):
                src = Path(fallback_plot[key])
                final_plot = paths["final_dir"] / f"{args.prefix}.synteny.{fmt}"
                shutil.copy(src, final_plot)
                artifacts[f"synteny_{fmt}"] = {
                    "artifact": f"synteny_{fmt}",
                    "path": f"final/{args.prefix}.synteny.{fmt}",
                    "full_path": str(final_plot.absolute()),
                    "type": "image" if fmt == "png" else "pdf",
                    "format": fmt,
                    "size": final_plot.stat().st_size,
                    "sha256": compute_file_hash(final_plot),
                    "source": "MCScanX fallback chromosome-link plot",
                }
            artifacts["synteny_plot_metadata"] = {
                "artifact": "synteny_plot_metadata",
                "path": "",
                "type": "metadata",
                "fallback_reason": fallback_plot.get("fallback_reason"),
                "total_pairs_plotted_source": fallback_plot.get("total_pairs_plotted_source", 0),
                "background_pairs_plotted": fallback_plot.get("background_pairs_plotted", 0),
                "highlighted_pairs_plotted": fallback_plot.get("highlighted_pairs_plotted", 0),
                "family_genes_plotted": fallback_plot.get("family_genes_plotted", 0),
            }
        
        # 12. 确定状态
        status = "success"
        if not blast_result.get("success", False):
            status = "failed"
        
        # 13. 准备参数
        parameters = {
            "cpu": args.cpu,
            "prefix": args.prefix,
            "evalue": args.evalue,
            "max_target_seqs": args.max_target_seqs,
            "use_blastp": args.use_blastp,
            "stage": "synteny",
            "job_id": job_id,
            "run_id": run_id,
            "protein_fasta": str(inputs["protein_fasta"]),
            "gff3": str(inputs["gff3"]),
            "genome": str(inputs["genome"]),
            "tool_versions": tool_versions
        }
        if scope_data:
            parameters.update({
                "synteny_pep_source": locals().get("scope_protein_source", ""),
                "synteny_gff_source": locals().get("scope_gff_source", ""),
                "synteny_genome_source": locals().get("scope_genome_source", ""),
            })
        if scope_data:
            parameters.update({
                "scope_file": str(Path(args.scope_file).absolute()) if args.scope_file else "",
                "scope_id": scope_data.get("scope_id", ""),
                "scope_mode": scope_data.get("scope_mode", ""),
                "analysis_route": analysis_route,
                "member_set_id": member_set_id,
                "target_species": scope_data.get("target_species", []),
                "reference_species": scope_data.get("reference_species", []),
                "comparative_species": scope_data.get("comparative_species", []),
                "selected_species": scope_data.get("selected_species", []),
                "family": scope_data.get("family", ""),
            })
        
        # 14. 写入报告
        end_time = datetime.now()
        
        print("\n📝 生成分析报告...")
        report_path = write_report(
            workspace=workspace,
            prefix=args.prefix,
            metrics=metrics,
            parameters=parameters,
            tool_versions=tool_versions,
            diamond_result=blast_result,
            gff_filter_result=gff_filter_result,
            classifier_result=classifier_result,
            mcscanx_result=mcscanx_result,
            artifacts=artifacts
        )
        
        artifacts["report"] = {
            "artifact": "report",
            "path": f"final/{args.prefix}.synteny_report.md",
            "full_path": str(report_path.absolute()),
            "type": "markdown",
            "size": report_path.stat().st_size,
            "sha256": compute_file_hash(report_path)
        }
        
        # 15. 写入Manifest
        print("\n📋 生成Manifest文件...")
        manifest_path = write_manifest(
            workspace=workspace,
            job_id=job_id,
            run_id=run_id,
            prefix=args.prefix,
            start_time=start_time,
            end_time=end_time,
            artifacts=artifacts,
            metrics=metrics,
            parameters=parameters,
            tool_versions=tool_versions,
            cmd_history=cmd_history,
            status=status
        )
        
        artifacts["manifest"] = {
            "artifact": "manifest",
            "path": f"final/{args.prefix}.synteny.manifest.json",
            "full_path": str(manifest_path.absolute()),
            "type": "json",
            "size": manifest_path.stat().st_size,
            "sha256": compute_file_hash(manifest_path)
        }
        
        print("\n" + "=" * 60)
        print(f"✅ 共线性分析完成！")
        print(f"📊 基因数量: {metrics.get('gene_count', 'N/A')}")
        print(f"🔗 共线性块数: {metrics.get('synteny_blocks', 'N/A')}")
        print(f"📁 结果目录: {workspace}")
        print("=" * 60)
        
        return 0
        
    except Exception as e:
        print(f"\n❌ 共线性分析失败: {e}")
        import traceback
        traceback.print_exc()
        
        # 写入失败manifest
        end_time = datetime.now()
        write_manifest(
            workspace=workspace,
            job_id=job_id,
            run_id=run_id,
            prefix=args.prefix,
            start_time=start_time,
            end_time=end_time,
            artifacts={},
            metrics={},
            parameters={
                "prefix": args.prefix,
                "stage": "synteny",
                "scope_file": str(Path(args.scope_file).absolute()) if args.scope_file else "",
                "scope_id": scope_data.get("scope_id", ""),
                "scope_mode": scope_data.get("scope_mode", ""),
                "analysis_route": analysis_route,
                "member_set_id": member_set_id,
                "target_species": scope_data.get("target_species", []),
                "reference_species": scope_data.get("reference_species", []),
                "comparative_species": scope_data.get("comparative_species", []),
                "selected_species": scope_data.get("selected_species", []),
                "family": scope_data.get("family", ""),
            },
            tool_versions={},
            cmd_history=cmd_history,
            status="failed"
        )
        
        return 1


if __name__ == "__main__":
    sys.exit(main())
