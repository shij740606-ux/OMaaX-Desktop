#!/usr/bin/env python3
"""
Promoter (cis-acting element) stage: PlantCARE prediction + visualization
符合 workspace_contract_v1.md 和 manifest_v1.schema.json 契约

执行流程:
1. 数据准备: GFF3 → GTF → 提取上游2000bp序列
2. PlantCARE提交: HTTP POST 上传序列
3. 邮件轮询: IMAP等待结果邮件
4. 结果解析: 解压处理PlantCARE输出
5. 可视化: 调用R脚本绘图
"""

import argparse
import csv
import email
import email.header
import gzip
import json
import os
import re
import smtplib
import socket
import subprocess
import sys
import time
import zipfile
import hashlib
import uuid
import io
import shutil
import ssl
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import urllib.request
import urllib.parse
import http.client
import imaplib
import email.parser

try:
    from gfa_core.gene_family_scope import read_scope, sanitize_token
except Exception:
    def read_scope(scope_path):
        return json.loads(Path(scope_path).read_text(encoding="utf-8"))

    def sanitize_token(value: str) -> str:
        token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
        token = token.strip(".-_")
        return token or "NA"


def load_project_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def plantcare_ssl_context() -> ssl.SSLContext:
    """Use conda/certifi CA bundle when available for PlantCARE HTTPS."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent
load_project_env(PROJECT_ROOT / ".env")

# ===================== 配置常量 =====================

# PlantCARE 服务器
PLANT_CARE_URL = "https://bioinformatics.psb.ugent.be/webtools/plantcare/cgi-bin/CallMat_onCluster.htpl"

# 邮箱配置 (QQ邮箱)
IMAP_SERVER = os.environ.get("PLANTCARE_IMAP_SERVER", "imap.qq.com")
IMAP_PORT = int(os.environ.get("PLANTCARE_IMAP_PORT", "993"))
EMAIL_ACCOUNT = os.environ.get("PLANTCARE_EMAIL_ACCOUNT", "")
EMAIL_PASSWORD = os.environ.get("PLANTCARE_EMAIL_PASSWORD", "")


def mask_email(value: str) -> str:
    if "@" not in value:
        return "<not configured>" if not value else "***"
    name, domain = value.split("@", 1)
    shown = name[:2] if len(name) > 2 else name[:1]
    return f"{shown}***@{domain}"


# 上游序列长度
DEFAULT_UPSTREAM_LENGTH = 2000

# PlantCARE 官网上传限制约 100 KB；默认保留余量避免 header/换行造成超限。
DEFAULT_PLANTCARE_MAX_UPLOAD_BYTES = 95000
DEFAULT_PLANTCARE_MAX_RECORDS = 30
DEFAULT_PLANTCARE_SUBMIT_TIMEOUT = 300

# 基础等待时间 (秒) - 固定1小时
DEFAULT_TIMEOUT = 3600  # 1小时

# 动态轮询间隔 (秒)
INITIAL_CHECK_INTERVAL = 30  #初始 每30秒检查
MAX_CHECK_INTERVAL = 60    # 后期每60秒检查

# Rscript 可执行文件（可视化阶段使用）
RSCRIPT_BIN = shutil.which("Rscript") or "Rscript"
DEFAULT_RSCRIPT = RSCRIPT_BIN


# 添加conda环境bin目录到PATH
CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")


def calculate_timeout(gene_count: int = 0) -> int:
    """固定等待时间1小时"""
    return DEFAULT_TIMEOUT


# ===================== 工具函数 =====================

def get_tool_versions() -> Dict[str, str]:
    """获取工具版本信息"""
    versions = {}
    
    # gffread 版本
    try:
        result = subprocess.run(["gffread", "--version"], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            versions["gffread"] = result.stdout.strip().split('\n')[0]
    except:
        versions["gffread"] = "not_found"
    
    # seqkit 版本
    try:
        result = subprocess.run(["seqkit", "version"], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            versions["seqkit"] = result.stdout.strip()
    except:
        versions["seqkit"] = "not_found"
    
    # R 版本
    try:
        result = subprocess.run(["R", "--version"], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            versions["R"] = result.stdout.split("\n")[0]
    except:
        versions["R"] = "not_found"
    
    return versions


def compute_file_hash(filepath: Path) -> str:
    """计算文件 SHA256 哈希"""
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except:
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
        dest_dir = workspace / "promoter" / "analysis"
    return dest_dir / f"{prefix}.{scope_token}.{sanitize_token(artifact_name)}{suffix}"


def materialize_scoped_outputs(
    artifacts: Dict[str, Dict],
    figures: List[Dict],
    workspace: Path,
    prefix: str,
    scope_id: str,
) -> Tuple[Dict[str, Dict], List[Dict]]:
    """Copy scope-specific outputs to stable names so later species runs cannot overwrite them."""
    if not scope_id:
        return artifacts, figures

    scoped_artifacts: Dict[str, Dict] = {}
    for name, info in artifacts.items():
        if not isinstance(info, dict):
            scoped_artifacts[name] = info
            continue
        source_text = info.get("full_path") or info.get("path") or ""
        source = Path(source_text)
        if not source.is_absolute():
            source = workspace / source
        if not source.exists() or name == "manifest":
            scoped_artifacts[name] = dict(info)
            continue
        dest = _scoped_copy_destination(source, workspace, prefix, scope_id, name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != dest.resolve():
            shutil.copy2(source, dest)
        copied = dict(info)
        copied["path"] = _relative_to_workspace(dest, workspace)
        copied["full_path"] = str(dest.resolve())
        copied["size"] = dest.stat().st_size
        copied["sha256"] = compute_file_hash(dest)
        scoped_artifacts[name] = copied

    scoped_figures: List[Dict] = []
    for figure in figures:
        if not isinstance(figure, dict):
            scoped_figures.append(figure)
            continue
        source_text = figure.get("full_path") or figure.get("path") or ""
        source = Path(source_text)
        if not source.is_absolute():
            source = workspace / source
        if not source.exists():
            scoped_figures.append(dict(figure))
            continue
        figure_name = figure.get("name") or source.stem
        dest = _scoped_copy_destination(source, workspace, prefix, scope_id, figure_name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != dest.resolve():
            shutil.copy2(source, dest)
        copied = dict(figure)
        copied["path"] = _relative_to_workspace(dest, workspace)
        copied["full_path"] = str(dest.resolve())
        copied["size"] = dest.stat().st_size
        scoped_figures.append(copied)

    return scoped_artifacts, scoped_figures


def run_cmd(cmd: List[str], cwd: Path = None, env=None, echo_stdout: bool = True) -> Tuple[int, str, str]:
    """运行命令并返回 (rc, stdout, stderr)"""
    print(f"CMD: {' '.join(cmd)}")
    if cwd:
        print(f"CWD: {cwd}")
    p = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        env=env,
    )
    if echo_stdout and p.stdout.strip():
        print(p.stdout)
    if p.stderr.strip():
        print(p.stderr)
    return p.returncode, p.stdout, p.stderr


def find_gff3_file(input_dir: Path, prefix: str) -> Optional[Path]:
    """查找GFF3文件"""
    species_short = prefix.split('.')[0] if '.' in prefix else prefix
    
    # 查找模式
    patterns = [
        f"{prefix}.gff3",
        f"{prefix}.gff",
        f"{species_short}*.gff3",
        f"*.gff3",
    ]
    
    for pattern in patterns:
        matches = list(input_dir.glob(pattern))
        if matches:
            # 排除明显不是目标文件的
            for m in matches:
                if m.is_file() and "pfam" not in m.name.lower():
                    return m
    return None


def find_genome_file(input_dir: Path, prefix: str) -> Optional[Path]:
    """查找基因组FASTA文件"""
    species_short = prefix.split('.')[0] if '.' in prefix else prefix
    
    patterns = [
        f"{prefix}*.fasta",
        f"{prefix}*.fa",
        f"{species_short}*.fasta",
        f"{species_short}*.fa",
        f"*.genome*.fasta",
        f"*genome*.fasta",
        f"*.chromosome*.fasta",
        f"*_genome_*.fasta",
    ]
    
    for pattern in patterns:
        matches = list(input_dir.glob(pattern))
        for m in matches:
            if m.is_file() and "genome" in m.name.lower():
                return m
    
    for pattern in patterns:
        matches = list(input_dir.glob(pattern))
        for m in matches:
            if m.is_file():
                n = m.name.lower()
                if "pep" not in n and "protein" not in n and "cds" not in n:
                    if "ath" not in n and "arabidopsis" not in n:
                        return m
    
    return None


def find_gene_id_file(input_dir: Path, final_dir: Path, prefix: str) -> Optional[Path]:
    """查找基因ID列表文件
    
    优先在 final_dir 查找（上游阶段输出，如 identify 生成的 final_members.id），
    其次在 input_dir 查找（用户手动输入的基因列表）。
    """
    species_short = prefix.split('.')[0] if '.' in prefix else prefix
    
    patterns = [
        f"{prefix}.final_members.id",  # identify 阶段生成的标准文件
        f"{prefix}.geneID*",
        f"{species_short}*.geneID*",
        f"*.geneID*",
        f"{prefix}*.id",
    ]
    
    # 先搜索 final_dir（上游阶段输出）
    for pattern in patterns:
        matches = list(final_dir.glob(pattern))
        for m in matches:
            if m.is_file():
                print(f"  Found gene ID file in final/: {m.name}")
                return m
    
    # 再搜索 input_dir（用户输入）
    for pattern in patterns:
        matches = list(input_dir.glob(pattern))
        for m in matches:
            if m.is_file():
                print(f"  Found gene ID file in input/: {m.name}")
                return m
    
    return None


def first_existing_scope_file(scope_data: Dict[str, Any], species: str, file_types: List[str]) -> Optional[Path]:
    files = scope_data.get("files") or {}
    species_files = files.get(species) or {}
    for file_type in file_types:
        for record in species_files.get(file_type, []) or []:
            path_text = record.get("path") or record.get("full_path") or ""
            if path_text:
                path = Path(path_text)
                if path.exists():
                    return path
    return None


def resolve_promoter_scope_inputs(scope_file: Optional[Path]) -> Tuple[Dict[str, Any], Optional[Path], Optional[Path]]:
    if not scope_file:
        return {}, None, None
    scope_data = read_scope(scope_file)
    targets = [str(x).strip() for x in (scope_data.get("target_species") or []) if str(x).strip()]
    if len(targets) != 1:
        raise RuntimeError(
            "Promoter 阶段一次只能分析一个目标物种；"
            f"当前 scope target_species={targets or '空'}。请在弹窗中只选择一个 target，或分别运行每个目标物种。"
        )
    species = targets[0]
    gff3_file = first_existing_scope_file(scope_data, species, ["gff"])
    genome_file = first_existing_scope_file(scope_data, species, ["genome"])
    if not gff3_file:
        raise RuntimeError(f"Promoter scope 未找到目标物种 {species} 的 GFF/GFF3 文件")
    if not genome_file:
        raise RuntimeError(f"Promoter scope 未找到目标物种 {species} 的 genome FASTA 文件")
    return scope_data, gff3_file, genome_file


def find_scope_target_final_gff3(input_dir: Path, workspace: Path, scope_data: Dict[str, Any]) -> Optional[Path]:
    """Prefer genome_prepare normalized final GFF3 for promoter extraction."""
    targets = [str(x).strip() for x in (scope_data.get("target_species") or []) if str(x).strip()]
    if len(targets) != 1:
        return None
    species = targets[0]
    candidates = [
        input_dir / f"{species}_final.gff3",
        input_dir / f"{species}.final.gff3",
        input_dir / species / f"{species}_final.gff3",
        input_dir / species / f"{species}.final.gff3",
        workspace / "final" / f"{species}_final.gff3",
        workspace / "final" / f"{species}.final.gff3",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None



def normalize_promoter_id(id_text: Optional[str]) -> str:
    """Normalize gene/transcript IDs used by promoter GTF filtering."""
    if not id_text:
        return ""
    value = str(id_text).strip().strip('"').strip("'")
    for prefix in ("gene:", "transcript:", "rna-", "gene-"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value


def promoter_gene_like_id(id_text: Optional[str]) -> str:
    """Map transcript-like IDs such as FtPinG0001.01.T01 to gene-like IDs."""
    value = normalize_promoter_id(id_text)
    value = re.sub(r"\.T\d+$", "", value)
    value = re.sub(r"_[PT]\d+$", "", value)
    return value


def promoter_id_variants(id_text: Optional[str]) -> set:
    value = normalize_promoter_id(id_text)
    variants = {value} if value else set()
    gene_like = promoter_gene_like_id(value)
    if gene_like:
        variants.add(gene_like)
    if value:
        no_version = re.sub(r"\.\d+$", "", value)
        if no_version:
            variants.add(no_version)
    return variants


def extract_gtf_attr(attr: str, key: str) -> Optional[str]:
    match = re.search(rf'{key}\s+"([^"]+)"', attr)
    return match.group(1) if match else None


def read_promoter_id_alias_map(output_dir: Path) -> Dict[str, str]:
    """Read alias -> primary target ID mapping written during promoter prep."""
    mapping_file = output_dir / "promoter_id_alias_map.tsv"
    alias_map: Dict[str, str] = {}
    if not mapping_file.exists():
        return alias_map
    with mapping_file.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2 or parts[0] == "alias_id":
                continue
            alias = normalize_promoter_id(parts[0])
            primary = normalize_promoter_id(parts[1])
            if alias and primary:
                alias_map[alias] = primary
                for variant in promoter_id_variants(alias):
                    alias_map.setdefault(variant, primary)
    return alias_map


def canonicalize_promoter_id(id_text: Optional[str], alias_map: Dict[str, str]) -> str:
    """Return the primary target ID for a promoter/PlantCARE ID when known."""
    value = normalize_promoter_id(id_text)
    if not value:
        return ""
    for variant in (value, *sorted(promoter_id_variants(value))):
        primary = alias_map.get(variant)
        if primary:
            return primary
    return value

def prepare_promoter_sequences(
    gff3_file: Path,
    genome_file: Path,
    gene_id_file: Optional[Path],
    output_dir: Path,
    upstream_length: int = 2000
) -> Path:
    """
    准备启动子序列
    1. GFF3 → GTF
    2. GTF → 提取上游序列 (只提取基因家族的基因)
    3. 格式化ID
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    def materialize_plain_file(source: Path, label: str) -> Path:
        if source.suffix.lower() != ".gz":
            return source
        plain_name = source.name[:-3]
        if not plain_name:
            plain_name = f"{label}.plain"
        dest = output_dir / plain_name
        if dest.exists() and dest.stat().st_size > 0 and dest.stat().st_mtime >= source.stat().st_mtime:
            print(f"  Reusing decompressed {label}: {dest}")
            return dest
        print(f"  Decompressing {label}: {source} -> {dest}")
        with gzip.open(source, "rb") as src, dest.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        if not dest.exists() or dest.stat().st_size == 0:
            raise RuntimeError(f"Failed to decompress {label}: {source}")
        return dest

    def sanitize_gff3_for_gffread(source: Path) -> Path:
        """Materialize a gffread-compatible GFF3 copy when strand uses nonstandard ?."""
        dest = output_dir / f"{source.stem}.gffread_compatible.gff3"
        changed = 0
        with source.open("r", encoding="utf-8", errors="ignore") as src, dest.open("w", encoding="utf-8") as out:
            for raw in src:
                if raw.startswith("#"):
                    out.write(raw)
                    continue
                line = raw.rstrip("\n")
                parts = line.split("\t")
                if len(parts) >= 7 and parts[6] == "?":
                    parts[6] = "."
                    changed += 1
                    out.write("\t".join(parts) + ("\n" if raw.endswith("\n") else ""))
                else:
                    out.write(raw)
        if changed:
            print(f"  Sanitized GFF3 strand ? for gffread: {changed} rows -> {dest}")
            return dest
        try:
            dest.unlink()
        except OSError:
            pass
        return source

    gff3_source = gff3_file
    genome_source = genome_file
    gff3_file = materialize_plain_file(gff3_file, "GFF3")
    genome_file = materialize_plain_file(genome_file, "genome")
    gff3_file = sanitize_gff3_for_gffread(gff3_file)
    
    # Step 1: GFF3 → GTF
    gtf_file = output_dir / f"{gff3_file.stem}.gtf"
    print(f"[Step 1] Converting GFF3 to GTF: {gff3_file} -> {gtf_file}")
    
    rc, stdout, stderr = run_cmd([
        "gffread", "-T", "-o", str(gtf_file), str(gff3_file)
    ])
    
    if rc != 0 or not gtf_file.exists():
        raise RuntimeError(f"Failed to convert GFF3 to GTF: {stderr}")
    
    # 读取基因ID列表
    # Read target gene/transcript IDs and build normalized variants for matching.
    target_gene_ids = None
    target_id_variants = set()
    variant_to_primary: Dict[str, str] = {}
    if gene_id_file and gene_id_file.exists():
        with open(gene_id_file, "r") as f:
            target_gene_ids = set()
            for line in f:
                parts = line.strip().split("\t")
                if parts and parts[0]:
                    raw_id = normalize_promoter_id(parts[0])
                    if raw_id:
                        target_gene_ids.add(raw_id)
                        for variant in promoter_id_variants(raw_id):
                            target_id_variants.add(variant)
                            variant_to_primary.setdefault(variant, raw_id)
        print(f"  Target ID count: {len(target_gene_ids)}")
        print(f"  Target ID variants for matching: {len(target_id_variants)}")

        # gffread may emit transcript_id from the GFF ID field. NCBI-style
        # prepared GFF keeps the mRNA accession in transcript_id and the CDS
        # accession in ID, so expand target IDs using equivalence observed in
        # the same GFF before filtering the generated GTF.  The expanded IDs are
        # aliases only; all downstream promoter outputs are canonicalized back
        # to the primary IDs from final_members.id.
        equivalence_keys = {
            'ID', 'Parent', 'Name', 'transcript_id', 'protein_id',
            'gene', 'gene_id', 'geneID', 'locus', 'locus_tag',
        }
        added_variants = 0
        with open(gff3_file, 'r', errors='replace') as gff_handle:
            for raw in gff_handle:
                if raw.startswith('#') or not raw.strip():
                    continue
                fields = raw.rstrip('\n').split('\t')
                if len(fields) < 9:
                    continue
                values = []
                for item in fields[8].split(';'):
                    if '=' not in item:
                        continue
                    key, value = item.split('=', 1)
                    if key not in equivalence_keys:
                        continue
                    values.extend(part for part in value.split(',') if part)
                value_variants = set()
                for value in values:
                    value_variants.update(promoter_id_variants(value))
                matched_primaries = {variant_to_primary[v] for v in value_variants if v in variant_to_primary}
                if len(matched_primaries) == 1:
                    primary_id = next(iter(matched_primaries))
                    before = len(target_id_variants)
                    target_id_variants.update(value_variants)
                    for variant in value_variants:
                        variant_to_primary.setdefault(variant, primary_id)
                    added_variants += len(target_id_variants) - before
        if added_variants:
            print(f"  Expanded target ID variants from GFF equivalence: +{added_variants}")

        alias_map_file = output_dir / "promoter_id_alias_map.tsv"
        with alias_map_file.open("w", encoding="utf-8") as map_out:
            map_out.write("alias_id\tprimary_id\n")
            for alias, primary in sorted(variant_to_primary.items()):
                map_out.write(f"{alias}\t{primary}\n")
        print(f"  Wrote promoter ID alias map: {alias_map_file}")
    
    # Step 1.5: filter GTF by either gene_id or transcript_id.
    if target_gene_ids:
        filtered_gtf = output_dir / f"{gtf_file.stem}.filtered.gtf"
        print(f"[Step 1.5] Filtering GTF to {len(target_gene_ids)} target genes/transcripts...")
        
        kept_lines = 0
        matched_transcripts = set()
        matched_genes = set()
        with open(gtf_file, 'r') as f_in, open(filtered_gtf, 'w') as f_out:
            for line in f_in:
                if line.startswith('#'):
                    f_out.write(line)
                    continue
                
                parts = line.rstrip('\n').split('\t')
                if len(parts) < 9:
                    continue
                
                attr = parts[8]
                gene_id = normalize_promoter_id(extract_gtf_attr(attr, 'gene_id'))
                transcript_id = normalize_promoter_id(extract_gtf_attr(attr, 'transcript_id'))
                gene_variants = promoter_id_variants(gene_id)
                transcript_variants = promoter_id_variants(transcript_id)
                is_match = bool((gene_variants | transcript_variants) & target_id_variants)
                
                if is_match:
                    f_out.write(line)
                    kept_lines += 1
                    if transcript_id:
                        matched_transcripts.add(transcript_id)
                    if gene_id:
                        matched_genes.add(gene_id)
        
        print(f"  Matched transcripts: {len(matched_transcripts)}")
        print(f"  Matched genes: {len(matched_genes)}")
        print(f"  Filtered GTF lines: {kept_lines}")
        if kept_lines == 0:
            raise RuntimeError(
                "target IDs do not match gene_id/transcript_id in GTF; "
                f"target_count={len(target_gene_ids)}, gtf={gtf_file}"
            )
        gtf_file = filtered_gtf
    
    # Step 2: 提取上游序列
    upstream_fasta = output_dir / "gene.upstream.fasta"
    print(f"[Step 2] Extracting upstream sequences ({upstream_length}bp)")
    
    cmd = [
        "seqkit", "subseq",
        "--gtf", str(gtf_file),
        "--feature", "transcript",
        "--up-stream", str(upstream_length),
        # "--id-ncbi",  # 移除：此参数导致序列ID解析失败，与GTF中的transcript_id不匹配
        "--only-flank",
        "--gtf-tag", "transcript_id",
        str(genome_file)
    ]
    
    rc, stdout, stderr = run_cmd(cmd, echo_stdout=False)
    
    if rc != 0:
        # 尝试备选方法
        print("[Warning] seqkit subseq failed, trying alternative...")
        # 使用bedtools
        cmd = [
            "seqkit", "seq",
            "-r", f"0:{upstream_length}",
            "-r", ":+",
            str(genome_file)
        ]
        rc, stdout, stderr = run_cmd(cmd, echo_stdout=False)
        
        if rc != 0:
            raise RuntimeError(f"Failed to extract upstream sequences: {stderr}")
    
    with open(upstream_fasta, "w") as f:
        f.write(stdout)
    
    if upstream_fasta.stat().st_size == 0:
        raise RuntimeError(f"Failed to extract upstream sequences: output is empty. Check if genome file '{genome_source}' matches the GFF3 '{gff3_source}'")
    
    seq_count = sum(1 for line in open(upstream_fasta) if line.startswith(">"))
    if seq_count == 0:
        raise RuntimeError(f"No sequences extracted. Verify genome file '{genome_source}' contains the chromosomes for genes in '{gff3_source}'")
    print(f"  Extracted {seq_count} upstream sequences")
    
    # Step 3: 格式化ID并过滤
    formatted_fasta = output_dir / "gene.upstream.changeID.fasta"
    print(f"[Step 3] Formatting FASTA IDs")
    
    kept_count = 0
    writing = False
    with open(upstream_fasta, "r") as f_in, open(formatted_fasta, "w") as f_out:
        for line in f_in:
            if line.startswith(">"):
                parts = line[1:].strip().split()
                if len(parts) >= 2:
                    gene_id = parts[1]
                else:
                    gene_id = parts[0]
                
                original_gene_id = gene_id
                gene_id = re.sub(r'\.\d+\.T\d+$', '', gene_id)
                gene_id = re.sub(r'\.\d+$', '', gene_id)
                
                header_variants = promoter_id_variants(original_gene_id) | promoter_id_variants(gene_id)
                primary_id = None
                for variant in sorted(header_variants):
                    if variant in variant_to_primary:
                        primary_id = variant_to_primary[variant]
                        break
                if target_gene_ids is None or primary_id or bool(header_variants & target_id_variants):
                    output_id = primary_id or gene_id
                    f_out.write(f">{output_id}\n")
                    kept_count += 1
                    writing = True
                else:
                    writing = False
            else:
                if writing:
                    f_out.write(line)
    
    print(f"  Kept {kept_count} gene sequences for PlantCARE")
    
    if kept_count == 0:
        raise RuntimeError("No gene sequences matched the target gene list")
    
    return formatted_fasta


def read_fasta_ids(fasta_file: Path) -> set:
    ids = set()
    if not fasta_file or not fasta_file.exists():
        return ids
    with fasta_file.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if line.startswith(">"):
                value = line[1:].strip().split()[0]
                if value:
                    ids.add(normalize_promoter_id(value))
    return ids


def read_plantcare_gene_ids(tab_file: Path) -> set:
    ids = set()
    if not tab_file or not tab_file.exists():
        return ids
    with tab_file.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if parts and parts[0].strip():
                ids.add(normalize_promoter_id(parts[0]))
    return ids


def validate_plantcare_matches_promoters(tab_file: Path, promoter_fasta: Path) -> None:
    """Fail early when a reused PlantCARE table belongs to a different gene set."""
    alias_map = read_promoter_id_alias_map(promoter_fasta.parent)
    promoter_ids = {canonicalize_promoter_id(x, alias_map) for x in read_fasta_ids(promoter_fasta)}
    plantcare_ids = {canonicalize_promoter_id(x, alias_map) for x in read_plantcare_gene_ids(tab_file)}
    if not promoter_ids or not plantcare_ids:
        return
    promoter_keys = {promoter_gene_like_id(x) for x in promoter_ids if x}
    plantcare_keys = {promoter_gene_like_id(x) for x in plantcare_ids if x}
    overlap = promoter_keys & plantcare_keys
    if not overlap:
        raise RuntimeError(
            "PlantCARE result IDs do not match the extracted promoter FASTA IDs. "
            f"promoter_ids={len(promoter_ids)}, plantcare_ids={len(plantcare_ids)}, "
            f"plantcare_file={tab_file}. Please provide the PlantCARE tab generated from "
            f"{promoter_fasta.name}, or rerun PlantCARE for the current target species."
        )
    overlap_ratio = len(overlap) / max(1, len(promoter_keys))
    if overlap_ratio < 0.5:
        print(
            "  ⚠️ PlantCARE result only partially overlaps extracted promoter IDs: "
            f"{len(overlap)}/{len(promoter_keys)}"
        )


def write_canonical_plantcare_tab(tab_file: Path, output_dir: Path) -> Path:
    """Write a PlantCARE tab copy whose first column uses primary target IDs."""
    alias_map = read_promoter_id_alias_map(output_dir)
    if not alias_map or not tab_file.exists():
        return tab_file
    canonical_file = output_dir / "plantCARE_output_canonical.tab"
    changed = 0
    total = 0
    with tab_file.open("r", encoding="utf-8", errors="ignore") as src, canonical_file.open("w", encoding="utf-8") as dst:
        for raw in src:
            if not raw.strip() or raw.startswith("#"):
                dst.write(raw)
                continue
            parts = raw.rstrip("\n").split("\t")
            if parts and parts[0].strip():
                total += 1
                original = parts[0]
                canonical = canonicalize_promoter_id(original, alias_map)
                if canonical != original:
                    changed += 1
                parts[0] = canonical
                dst.write("\t".join(parts) + ("\n" if raw.endswith("\n") else ""))
            else:
                dst.write(raw)
    print(f"  Canonicalized PlantCARE IDs: {changed}/{total} rows -> {canonical_file}")
    return canonical_file


def split_fasta_by_count(fasta_file: Path, batch_size: int = 1000) -> List[Path]:
    """
    将FASTA文件按序列数量分批
    返回: 分批后的文件路径列表
    """
    from Bio import SeqIO
    
    # 读取所有序列
    sequences = list(SeqIO.parse(fasta_file, "fasta"))
    total_seqs = len(sequences)
    
    if total_seqs <= batch_size:
        return [fasta_file]
    
    # 计算批次数量
    num_batches = (total_seqs + batch_size - 1) // batch_size
    print(f"  Splitting {total_seqs} sequences into {num_batches} batches (max {batch_size} per batch)")
    
    batch_files = []
    base_name = fasta_file.stem
    parent_dir = fasta_file.parent
    
    for i in range(num_batches):
        start_idx = i * batch_size
        end_idx = min((i + 1) * batch_size, total_seqs)
        batch_seqs = sequences[start_idx:end_idx]
        
        batch_file = parent_dir / f"{base_name}_batch{i+1:03d}.fasta"
        SeqIO.write(batch_seqs, batch_file, "fasta")
        batch_files.append(batch_file)
        print(f"    Batch {i+1}: {len(batch_seqs)} sequences -> {batch_file.name}")
    
    return batch_files


def count_fasta_records(fasta_file: Path) -> int:
    """Count FASTA records without loading sequences into memory."""
    if not fasta_file.exists():
        return 0
    with fasta_file.open("r", encoding="utf-8", errors="ignore") as handle:
        return sum(1 for line in handle if line.startswith(">"))


def safe_plantcare_upload_stem(prefix: str, index: int) -> str:
    """Short PlantCARE upload stem; avoid dots/long descriptive filenames."""
    token = re.sub(r"[^A-Za-z0-9]+", "", str(prefix or "")) or "job"
    token = token[:12]
    return f"pm{token}{index:03d}"


def materialize_plantcare_upload_fastas(batch_fastas: List[Path], prefix: str, output_dir: Path) -> List[Path]:
    """Copy PlantCARE upload FASTAs to short safe filenames without changing content."""
    upload_fastas: List[Path] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for idx, source in enumerate(batch_fastas, 1):
        safe_path = output_dir / f"{safe_plantcare_upload_stem(prefix, idx)}.fasta"
        if source.resolve() != safe_path.resolve():
            shutil.copy2(source, safe_path)
        upload_fastas.append(safe_path)
        if source.name != safe_path.name:
            print(f"  PlantCARE safe upload filename: {source.name} -> {safe_path.name}")
    return upload_fastas


def split_fasta_by_upload_size(
    fasta_file: Path,
    max_bytes: int = DEFAULT_PLANTCARE_MAX_UPLOAD_BYTES,
    max_records: int = DEFAULT_PLANTCARE_MAX_RECORDS,
) -> List[Path]:
    """Split FASTA into batches that stay below PlantCARE's web upload limits."""
    if max_bytes <= 0 and max_records <= 0:
        return [fasta_file]
    total_records = count_fasta_records(fasta_file)
    if (max_bytes <= 0 or fasta_file.stat().st_size <= max_bytes) and (
        max_records <= 0 or total_records <= max_records
    ):
        return [fasta_file]

    records: List[Tuple[str, List[str], int]] = []
    header: Optional[str] = None
    seq_lines: List[str] = []

    def flush_record() -> None:
        if header is None:
            return
        size = len((header + "\n").encode("utf-8")) + sum(
            len((line + "\n").encode("utf-8")) for line in seq_lines
        )
        records.append((header, list(seq_lines), size))

    with fasta_file.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            if line.startswith(">"):
                flush_record()
                header = line
                seq_lines = []
            elif header is not None:
                seq_lines.append(line)
        flush_record()

    if not records:
        return [fasta_file]

    batches: List[List[Tuple[str, List[str], int]]] = []
    current: List[Tuple[str, List[str], int]] = []
    current_size = 0
    for record in records:
        rec_size = record[2]
        would_exceed_size = max_bytes > 0 and current_size + rec_size > max_bytes
        would_exceed_count = max_records > 0 and len(current) >= max_records
        if current and (would_exceed_size or would_exceed_count):
            batches.append(current)
            current = []
            current_size = 0
        current.append(record)
        current_size += rec_size
    if current:
        batches.append(current)

    print(
        f"  PlantCARE upload batching required: {len(records)} sequences, "
        f"{fasta_file.stat().st_size} bytes; max {max_records} records and "
        f"{max_bytes} bytes per batch"
    )

    batch_files: List[Path] = []
    for idx, batch in enumerate(batches, 1):
        batch_file = fasta_file.parent / f"{fasta_file.stem}_batch{idx:03d}.fasta"
        with batch_file.open("w", encoding="utf-8") as out:
            for rec_header, rec_lines, _size in batch:
                out.write(rec_header + "\n")
                for seq_line in rec_lines:
                    out.write(seq_line + "\n")
        batch_files.append(batch_file)
        print(
            f"    Batch {idx}: {len(batch)} sequences, "
            f"{batch_file.stat().st_size} bytes -> {batch_file.name}"
        )

    return batch_files


def merge_plantcare_results(result_files: List[Path], output_file: Path) -> bool:
    """
    合并多个PlantCARE结果文件
    返回: 是否成功
    """
    print(f"\n  Merging {len(result_files)} result files...")
    
    try:
        with open(output_file, 'w', encoding='utf-8') as out_f:
            for i, result_file in enumerate(result_files, 1):
                if not result_file.exists():
                    print(f"    ⚠️  Batch {i} result not found: {result_file}")
                    continue
                
                with open(result_file, 'r', encoding='utf-8', errors='ignore') as in_f:
                    content = in_f.read()
                    out_f.write(content)
                    if not content.endswith('\n'):
                        out_f.write('\n')
                
                print(f"    ✅  Merged: {result_file.name}")
        
        print(f"  ✅  Merged result saved to: {output_file}")
        return True
    except Exception as e:
        print(f"  ❌  Merge failed: {e}")
        return False


def submit_to_plantcare(
    fasta_file: Path,
    email_addr: str,
    reference: str,
    submit_timeout: int = DEFAULT_PLANTCARE_SUBMIT_TIMEOUT,
) -> Tuple[bool, str]:
    """
    提交序列到PlantCARE服务器
    返回: (success, message)
    """
    print(f"[Step 4] Submitting to PlantCARE: {PLANT_CARE_URL}")
    print(f"  Email: {email_addr}")
    print(f"  Reference: {reference}")
    print(f"  File: {fasta_file}")
    
    # 读取FASTA文件内容
    with open(fasta_file, "rb") as f:
        fasta_content = f.read()
    
    # 构建multipart form data
    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex[:16]
    
    body = f"--{boundary}\r\n"
    body += 'Content-Disposition: form-data; name="Field_UserEmail"\r\n\r\n'
    body += f"{email_addr}\r\n"
    
    body += f"--{boundary}\r\n"
    body += 'Content-Disposition: form-data; name="Field_REF"\r\n\r\n'
    body += f"{reference}\r\n"
    
    body += f"--{boundary}\r\n"
    body += 'Content-Disposition: form-data; name="Field_Sequence"\r\n\r\n'
    body += "\r\n"
    
    body += f"--{boundary}\r\n"
    body += f'Content-Disposition: form-data; name="Field_File"; filename="{fasta_file.name}"\r\n'
    body += "Content-Type: application/octet-stream\r\n\r\n"
    body = body.encode("utf-8")
    
    body += fasta_content
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")
    
    # 发送请求
    req = urllib.request.Request(
        PLANT_CARE_URL,
        data=body,
        method="POST"
    )
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    req.add_header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
    req.add_header("Origin", "https://bioinformatics.psb.ugent.be")
    req.add_header("Referer", "https://bioinformatics.psb.ugent.be/webtools/plantcare/html/search_CARE_onCluster.html")
    
    try:
        print(f"  Sending request to PlantCARE... (timeout: {submit_timeout}s)")
        with urllib.request.urlopen(req, timeout=submit_timeout, context=plantcare_ssl_context()) as response:
            response_text = response.read().decode("utf-8", errors="ignore")

            # 检查是否提交成功
            if "Your search has been launched" in response_text or "submitted" in response_text.lower():
                print("  ✅ PlantCARE submission successful!")
                return True, "Submission successful"
            else:
                # 检查响应中是否有其他信息
                if "error" in response_text.lower():
                    return False, f"PlantCARE error: {response_text[:500]}"
                print(f"  ⚠️ Response: {response_text[:500]}")
                return True, "Request sent (may be processing)"

    except http.client.IncompleteRead as e:
        partial = e.partial or b""
        response_text = partial.decode("utf-8", errors="ignore")
        if "error" in response_text.lower():
            return False, f"PlantCARE partial response error: {response_text[:500]}"
        if "Your search has been launched" in response_text or "submitted" in response_text.lower() or partial:
            print(f"  ⚠️ PlantCARE response ended early after {len(partial)} bytes; treating as submitted")
            if response_text:
                print(f"  Partial response: {response_text[:300]}")
            return True, "Partial PlantCARE response received; assuming request was accepted"
        return False, "PlantCARE response ended early with no content"
    except urllib.error.URLError as e:
        return False, f"Network error: {e}"
    except Exception as e:
        return False, f"Error: {e}"


def wait_for_plantcare_email(
    output_dir: Path,
    gene_count: int = 0,
    timeout: int = 0,
    check_interval: int = 0,
    reference: str = "",
    min_date: Optional[datetime] = None
) -> Optional[Path]:
    """
    等待并下载PlantCARE结果邮件
    返回: 下载的zip文件路径
    """
    if timeout == 0:
        timeout = calculate_timeout(gene_count)
    
    if check_interval == 0:
        check_interval = INITIAL_CHECK_INTERVAL
    
    print(f"[Step 5] Waiting for PlantCARE result email...")
    print(f"  IMAP server: {IMAP_SERVER}:{IMAP_PORT}")
    print(f"  Account: {mask_email(EMAIL_ACCOUNT)}")
    print(f"  Gene count: {gene_count}")
    print(f"  Reference: {reference}")
    if min_date:
        print(f"  Min date filter: {min_date.isoformat()}")
    print(f"  Timeout: {timeout}s ({timeout//60}min)")
    print(f"  Initial check interval: {check_interval}s")
    
    start_time = time.time()
    current_interval = check_interval
    
    # 连接邮箱
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        print("  ✅ Email login successful")
    except Exception as e:
        print(f"  ❌ Email login failed: {e}")
        return None
    
    # 轮询检查新邮件
    while time.time() - start_time < timeout:
        try:
            # 选择收件箱
            mail.select("INBOX")
            
            # 搜索来自 PlantCARE 的邮件
            # 发件人: no-reply@psb.vib-ugent.be
            # 尝试多种搜索策略
            message_ids = []
            
            # 策略1: 搜索发件人域名
            for criteria in ['FROM "psb.vib-ugent.be"', 'FROM "no-reply@psb.vib-ugent.be"']:
                try:
                    result, ids = mail.search(None, criteria)
                    if result == 'OK' and ids[0]:
                        message_ids.extend(ids[0].split())
                except:
                    pass
            
            # 策略2: 如果域名搜索失败，搜索所有邮件然后手动筛选
            if not message_ids:
                try:
                    result, all_ids = mail.search(None, "ALL")
                    if result == 'OK' and all_ids[0]:
                        # 获取最近20封邮件检查发件人
                        for msg_id in reversed(all_ids[0].split()[-20:]):
                            try:
                                result, msg_data = mail.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM)])")
                                if result == 'OK' and msg_data[0]:
                                    from_header = msg_data[0][1].decode('utf-8', errors='ignore')
                                    if 'psb.vib-ugent.be' in from_header.lower():
                                        message_ids.append(msg_id)
                            except:
                                continue
                except:
                    pass
            
            # 去重但保留从服务器返回的大致顺序；message_ids 已经是 bytes 列表。
            seen_ids = set()
            deduped_ids = []
            for msg_id in message_ids:
                if msg_id not in seen_ids:
                    deduped_ids.append(msg_id)
                    seen_ids.add(msg_id)
            message_ids = deduped_ids
            
            if message_ids:
                print(f"  📬 Found {len(message_ids)} email(s) from PlantCARE")
                
                # 查找匹配reference的邮件
                for msg_id in reversed(message_ids):
                    result, msg_data = mail.fetch(msg_id, "(RFC822)")
                    
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg_content = response_part[1]
                            msg = email.parser.Parser().parsestr(msg_content.decode("utf-8", errors="ignore"))
                            subject = msg.get("Subject", "")
                            
                            # 获取邮件日期并过滤
                            if min_date:
                                date_str = msg.get("Date", "")
                                try:
                                    from email.utils import parsedate_to_datetime
                                    email_date = parsedate_to_datetime(date_str)
                                    if email_date < min_date:
                                        continue  # 跳过旧邮件
                                except:
                                    pass  # 日期解析失败，不过滤
                            
                            # 检查是否匹配reference (支持多种匹配方式)
                            print(f"  🔍 Checking email subject: {subject}")
                            target_found = False
                            if reference:
                                # 只接受和本次提交 reference 明确匹配的邮件，避免误用旧任务结果。
                                ref_variants = [
                                    reference,
                                    reference.replace("_", "."),
                                    reference.replace(".", "_"),
                                    reference.replace("_", ""),
                                    reference.replace(".", ""),
                                ]
                                subject_lower = subject.lower()
                                for ref in ref_variants:
                                    if ref and ref.lower() in subject_lower:
                                        target_found = True
                                        print(f"  ✅ Matched reference variant: {ref}")
                                        break
                            else:
                                target_found = True
                            
                            if target_found:
                                print(f"  ✅ Found matching email: {subject}")
                                
                                # 查找附件
                                for part in msg.walk():
                                    content_disposition = part.get("Content-Disposition", "")
                                    
                                    if "attachment" in content_disposition:
                                        filename = part.get_filename()
                                        print(f"  📎 Found attachment: {filename}")
                                        
                                        # 检查附件文件名是否匹配 reference（更精确的匹配）
                                        attachment_matches = False
                                        if filename and reference:
                                            # 检查文件名是否包含 reference（支持多种变体）
                                            ref_variants = [
                                                reference,
                                                reference.replace("_", ""),
                                                reference.replace(".", ""),
                                                reference.replace("_", "."),
                                                reference.replace(".", "_")
                                            ]
                                            for ref in ref_variants:
                                                if ref and ref in filename:
                                                    attachment_matches = True
                                                    print(f"  ✅ Attachment matches reference variant: {ref}")
                                                    break
                                        
                                        # 有 reference 时附件也必须匹配，防止下载其他任务的 PlantCARE 包。
                                        if not reference and target_found:
                                            attachment_matches = True
                                        
                                        # 下载附件
                                        if attachment_matches and filename and (".tar.gz" in filename or ".zip" in filename or "PlantCARE" in filename):
                                            # 解码附件
                                            data = part.get_payload(decode=True)
                                            if data:
                                                existing_tabs = {
                                                    p.resolve(): p.stat().st_mtime
                                                    for p in output_dir.glob("*.tab")
                                                }

                                                # 保存到文件 (保持原始文件名)
                                                output_file = output_dir / filename
                                                with open(output_file, "wb") as f:
                                                    f.write(data)
                                                print(f"  💾 Saved attachment: {output_file}")
                                                
                                                # 如果是.tar.gz，尝试解压
                                                if filename.endswith(".tar.gz"):
                                                    import tarfile
                                                    try:
                                                        with tarfile.open(output_file, "r:gz") as tar:
                                                            tar.extractall(output_dir)
                                                        print(f"  ✅ Extracted tar.gz: {output_file}")
                                                        # 只使用本次附件解压产生或更新的 tab，避免误用目录中的旧任务结果。
                                                        tab_files = []
                                                        for tab_file in output_dir.glob("*.tab"):
                                                            resolved = tab_file.resolve()
                                                            old_mtime = existing_tabs.get(resolved)
                                                            if old_mtime is None or tab_file.stat().st_mtime > old_mtime:
                                                                tab_files.append(tab_file)
                                                        tab_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                                                        if tab_files:
                                                            print(f"  📄 Found tab file: {tab_files[0]}")
                                                            return tab_files[0]
                                                    except Exception as e:
                                                        print(f"  ⚠️ Error extracting tar.gz: {e}")
                                                
                                                return output_file
            
            # 等待下次检查 - 动态调整间隔
            elapsed = time.time() - start_time
            remaining = timeout - elapsed
            
            # 动态调整轮询间隔：开始快，后期慢（增长更慢）
            if elapsed > 120:
                current_interval = min(current_interval * 1.2, MAX_CHECK_INTERVAL)
            
            print(f"  ⏳ Waiting... ({elapsed:.0f}s elapsed, {remaining:.0f}s remaining, check every {current_interval:.0f}s)")
            time.sleep(current_interval)
            
        except Exception as e:
            print(f"  ⚠️ Error checking email: {e}")
            time.sleep(check_interval)
    
    print("  ❌ Timeout waiting for PlantCARE result")
    return None


def parse_plantcare_output(tab_file: Path, output_dir: Path) -> Dict:
    """
    解析PlantCARE输出文件
    生成:
    - 元件统计（过滤unknown/Unnamed元件）
    - BED格式文件
    - 元件描述
    """
    print(f"[Step 6] Parsing PlantCARE output: {tab_file}")
    
    results = {
        "total_elements": 0,
        "unique_elements": 0,
        "genes_with_elements": 0,
        "element_counts": {},
        "gene_element_counts": {},
        "filtered_elements": 0,
        "filtered_by_type": {},
    }
    
    if not tab_file.exists():
        print(f"  ⚠️ Tab file not found: {tab_file}")
        return results
    
    # 定义需要过滤的元件类型
    filtered_patterns = ['unknown', 'Unnamed__', 'motif_sequence']
    alias_map = read_promoter_id_alias_map(output_dir)
    
    def should_filter_element(element_name: str) -> bool:
        """判断元件是否应该被过滤"""
        if not element_name:
            return True
        element_lower = element_name.lower()
        for pattern in filtered_patterns:
            if pattern.lower() in element_lower:
                return True
        return False
    
    # 统计元件
    element_counts = {}
    gene_element_counts = {}
    unique_genes = set()
    filtered_count = 0
    filtered_by_type = {}
    
    # 同时创建过滤后的tab文件供后续使用
    filtered_tab_file = output_dir / "plantCARE_output_filtered.tab"
    
    with open(tab_file, "r") as f_in, open(filtered_tab_file, "w") as f_filtered:
        for line in f_in:
            parts = line.strip().split("\t")
            if len(parts) >= 4:
                # PlantCARE输出格式: gene, element, sequence, position, length, strand, organism, function
                gene = canonicalize_promoter_id(parts[0], alias_map)
                parts[0] = gene
                element = parts[1] if parts[1] else "unknown"
                sequence = parts[2]
                
                # 尝试解析位置 (可能在第4列)
                try:
                    position = int(parts[3])
                except (ValueError, IndexError):
                    continue
                
                # 过滤unknown/Unnamed元件
                if should_filter_element(element):
                    filtered_count += 1
                    filtered_by_type[element] = filtered_by_type.get(element, 0) + 1
                    continue
                
                # 写入过滤后的文件，第一列统一回 final_members.id 主 ID
                f_filtered.write("\t".join(parts) + "\n")
                
                unique_genes.add(gene)
                
                # 元件统计
                element_counts[element] = element_counts.get(element, 0) + 1
                
                # 基因-元件统计
                key = f"{gene}\t{element}"
                gene_element_counts[key] = gene_element_counts.get(key, 0) + 1
    
    results["total_elements"] = sum(element_counts.values())
    results["unique_elements"] = len(element_counts)
    results["genes_with_elements"] = len(unique_genes)
    results["element_counts"] = element_counts
    results["gene_element_counts"] = gene_element_counts
    results["filtered_elements"] = filtered_count
    results["filtered_by_type"] = filtered_by_type
    
    print(f"  📊 Total elements: {results['total_elements'] + filtered_count}")
    print(f"  ✅ Valid elements: {results['total_elements']} (filtered {filtered_count} unknown/Unnamed)")
    print(f"  🧬 Unique element types: {results['unique_elements']}")
    print(f"  📝 Genes with elements: {results['genes_with_elements']}")
    
    # 保存统计文件（只包含有效元件）
    element_counts_file = output_dir / "element_counts.txt"
    with open(element_counts_file, "w") as f:
        for elem, count in sorted(element_counts.items(), key=lambda x: -x[1]):
            f.write(f"{count}\t{elem}\n")
    print(f"  📊 Saved element counts: {element_counts_file}")
    
    # 保存过滤统计
    filtered_stats_file = output_dir / "filtered_elements_stats.txt"
    with open(filtered_stats_file, "w") as f:
        f.write(f"# Filtered Elements Statistics\n")
        f.write(f"Total filtered: {filtered_count}\n\n")
        f.write("Filtered by type:\n")
        for elem, count in sorted(filtered_by_type.items(), key=lambda x: -x[1]):
            f.write(f"{count}\t{elem}\n")
    print(f"  📊 Saved filtered stats: {filtered_stats_file}")
    
    # 生成BED文件（使用过滤后的数据）
    bed_file = output_dir / "PlantCARE.bed"
    bed_count = 0
    with open(filtered_tab_file, "r") as f_in, open(bed_file, "w") as f_out:
        for line in f_in:
            parts = line.strip().split("\t")
            if len(parts) >= 4:
                gene = parts[0]
                element = parts[1]
                sequence = parts[2]
                try:
                    pos = int(parts[3])
                    # BED格式: chrom, start, end, name
                    f_out.write(f"{gene}\t{pos-1}\t{pos+len(sequence)}\t{element}\n")
                    bed_count += 1
                except (ValueError, IndexError):
                    pass
    print(f"  📍 Saved BED file: {bed_file} ({bed_count} entries)")
    
    return results


def categorize_element(element_name: str, description: str = "") -> str:
    """
    对元件进行分类
    返回: category_name
    """
    element_lower = element_name.lower()
    desc_lower = description.lower()
    
    # 1. 核心启动子元件 (最高优先级)
    if any(x in element_lower for x in ['tata', 'caat', 'inr', 'y-patch']):
        return "Core promoter"
    
    # 2. 激素响应元件
    if 'abre' in element_lower or 'abscisic' in desc_lower:
        return "ABA signaling"
    if any(x in element_lower for x in ['p-box', 'py-box', 'gare']) or 'gibberellin' in desc_lower:
        return "GA signaling"
    if any(x in element_lower for x in ['cgtca', 'tgacg']) or 'jasmonate' in desc_lower or 'meja' in desc_lower:
        return "JA signaling"
    if any(x in element_lower for x in ['tca', 'as-1']) or 'salicylic' in desc_lower:
        return "SA signaling"
    if any(x in element_lower for x in ['tga', 'auxre']) or 'auxin' in desc_lower:
        return "Auxin response"
    if 'ere' in element_lower or 'ethylene' in desc_lower:
        return "Ethylene response"
    
    # 3. 胁迫响应元件
    if any(x in element_lower for x in ['dre', 'crt', 'ltr', 'are', 'stre', 'wre', 'w-box', 'w box']):
        return "Stress response"
    if 'mbs' in element_lower and ('drought' in desc_lower or 'stress' in desc_lower):
        return "Stress response"
    
    # 4. 转录因子结合位点
    if element_lower in ['myb', 'myb-like', 'mre'] or 'myb binding' in desc_lower:
        return "MYB-related"
    if element_lower in ['myc'] or 'e-box' in element_lower:
        return "MYC-related"
    if 'wrky' in element_lower:
        return "WRKY-related"
    
    # 5. 光响应元件
    if any(x in element_lower for x in ['g-box', 'g-box', 'gt1', 'i-box', 'sp1', 'box 4', 'gata']):
        return "Light response"
    
    # 6. 发育调控元件
    if any(x in element_lower for x in ['ry-element', 'aagaa', 'cat-box', 'gcn4', 'skn-1']):
        return "Development"
    
    # 7. 基于description的补充分类
    if 'light' in desc_lower:
        return "Light response"
    if any(x in desc_lower for x in ['drought', 'dehydration', 'low temperature', 'stress', 'anaerobic', 'cold', 'wound', 'pathogen', 'defense']):
        return "Stress response"
    if 'seed specific' in desc_lower or 'endosperm' in desc_lower or 'seed' in desc_lower:
        return "Development"
    if 'meristem' in desc_lower:
        return "Development"
    if 'cell cycle' in desc_lower or 'dna synthesis' in desc_lower:
        return "Cell cycle"
    
    # 8. 其他
    return "Other"


def generate_promoter_report(
    prefix: str,
    parse_results: Dict,
    element_desc_file: Optional[Path],
    output_file: Path,
    elapsed_time: float,
    parameters: Dict,
    enrichment_results: Optional[Dict] = None,
    cooccurrence_results: Optional[Dict] = None,
    complexity_results: Optional[Dict] = None
) -> Path:
    """
    生成启动子分析报告 (Markdown格式)
    """
    print(f"[Step 8] Generating promoter report: {output_file}")
    
    # 准备统计数据
    total_elements = parse_results.get('total_elements', 0)
    unique_elements = parse_results.get('unique_elements', 0)
    genes_with_elements = parse_results.get('genes_with_elements', 0)
    filtered_elements = parse_results.get('filtered_elements', 0)
    element_counts = parse_results.get('element_counts', {})
    
    # 按类别统计元件
    category_counts = {}
    element_desc_map = {}
    
    # 读取元件描述
    if element_desc_file and element_desc_file.exists():
        with open(element_desc_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                elem = row.get('element', '').strip()
                desc = row.get('description', '').strip()
                if elem:
                    element_desc_map[elem] = desc
    
    # 统计各类别
    for elem, count in element_counts.items():
        desc = element_desc_map.get(elem, '')
        category = categorize_element(elem, desc)
        category_counts[category] = category_counts.get(category, 0) + count
    
    # 生成报告内容
    report_content = f"""# Promoter Analysis Report - {prefix}

**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Analysis Overview

- **Status**: ✅ Success
- **Total Elements**: {total_elements} (filtered {filtered_elements} unknown/Unnamed)
- **Unique Element Types**: {unique_elements}
- **Genes with Elements**: {genes_with_elements}
- **Analysis Time**: {elapsed_time:.1f}s

## Parameters

| Parameter | Value |
|-----------|-------|
| Upstream Length | {parameters.get('upstream_length', 2000)} bp |
| Email | {parameters.get('email', 'N/A')} |

## Element Statistics

### Top 20 Most Abundant Elements

| Rank | Element | Count | Category |
|------|---------|-------|----------|
"""
    
    # 添加Top 20元件
    sorted_elements = sorted(element_counts.items(), key=lambda x: -x[1])[:20]
    for rank, (elem, count) in enumerate(sorted_elements, 1):
        desc = element_desc_map.get(elem, '')
        category = categorize_element(elem, desc)
        report_content += f"| {rank} | {elem} | {count} | {category} |\n"
    
    report_content += f"""
### Element Distribution by Category

| Category | Count | Percentage |
|----------|-------|------------|
"""
    
    # 按类别排序
    sorted_categories = sorted(category_counts.items(), key=lambda x: -x[1])
    for category, count in sorted_categories:
        percentage = (count / total_elements * 100) if total_elements > 0 else 0
        report_content += f"| {category} | {count} | {percentage:.1f}% |\n"
    
    report_content += f"""
## Biological Interpretation

### Core Promoter Elements
Core promoter elements (TATA-box, CAAT-box) are essential for transcription initiation.
- **TATA-box**: Found in {element_counts.get('TATA-box', 0)} instances
- **CAAT-box**: Found in {element_counts.get('CAAT-box', 0)} instances

### Hormone Response Elements
Hormone-responsive elements indicate potential regulatory roles:
"""
    
    # 激素响应统计
    hormone_categories = ['ABA signaling', 'GA signaling', 'JA signaling', 'SA signaling', 'Auxin response', 'Ethylene response']
    for cat in hormone_categories:
        count = category_counts.get(cat, 0)
        if count > 0:
            report_content += f"- **{cat}**: {count} elements\n"
    
    report_content += f"""
### Stress Response Elements
Elements involved in stress responses:
"""
    
    # 胁迫响应统计
    stress_count = category_counts.get('Stress response', 0)
    if stress_count > 0:
        report_content += f"- **Stress response**: {stress_count} elements\n"
    
    report_content += f"""
### Transcription Factor Binding Sites
"""
    
    # TF结合位点统计
    tf_categories = ['MYB-related', 'MYC-related', 'WRKY-related']
    for cat in tf_categories:
        count = category_counts.get(cat, 0)
        if count > 0:
            report_content += f"- **{cat}**: {count} elements\n"
    
    report_content += f"""
## Output Files

| File | Description |
|------|-------------|
| `{prefix}.promoter.manifest.json` | Analysis manifest |
| `element_counts.txt` | Element frequency table |
| `PlantCARE.bed` | BED format element positions |
| `plantCARE_output_filtered.tab` | Filtered PlantCARE results |
| `filtered_elements_stats.txt` | Statistics of filtered elements |
"""
    
    # 添加实际存在的可视化文件，避免报告列出未生成的图件。
    existing_viz_files = sorted(
        p.name for pattern in (f"{prefix}_promoter*.pdf", f"{prefix}_promoter*.png")
        for p in output_file.parent.glob(pattern)
    )
    report_content += """
## Visualization

"""
    if existing_viz_files:
        report_content += "The following visualization files were generated:\n\n"
        for viz_name in existing_viz_files:
            report_content += f"- `{viz_name}`\n"
    else:
        report_content += "No visualization files were generated. Use the tabular promoter outputs for interpretation.\n"

    report_content += """
## Advanced Analysis

### Subfamily Enrichment Analysis
"""
    
    # 添加富集分析结果
    if enrichment_results and enrichment_results.get('enriched_elements'):
        report_content += f"""
**Summary**: {enrichment_results['summary'].get('total_enriched', 0)} enriched element-subfamily pairs identified across {enrichment_results['summary'].get('subfamilies_analyzed', 0)} subfamilies.

| Subfamily | Element | Count | Enrichment Ratio |
|-----------|---------|-------|------------------|
"""
        for result in enrichment_results['enriched_elements'][:10]:
            report_content += f"| {result['subfamily']} | {result['element']} | {result['count']} | {result['enrichment_ratio']:.2f} |\n"
    else:
        report_content += "\nNo subfamily enrichment data available.\n"
    
    report_content += f"""

### Element Co-occurrence Patterns
"""
    
    # 添加共现分析结果
    if cooccurrence_results:
        report_content += f"""
**Summary**: {cooccurrence_results.get('significant_pairs', 0)} significant co-occurrence patterns identified (≥2 genes).

**Top Co-occurring Element Pairs**:

| Element 1 | Element 2 | Co-occurrence Count |
|-----------|-----------|---------------------|
"""
        for pair, count in cooccurrence_results.get('top_pairs', [])[:10]:
            report_content += f"| {pair[0]} | {pair[1]} | {count} |\n"
        
        # Hub元件
        if cooccurrence_results.get('hub_elements'):
            report_content += f"""

**Hub Elements** (most connected):

| Element | Connections |
|---------|-------------|
"""
            for elem, connections in cooccurrence_results['hub_elements'][:5]:
                report_content += f"| {elem} | {len(connections)} |\n"
    else:
        report_content += "\nNo co-occurrence data available.\n"
    
    report_content += f"""

### Promoter Complexity Analysis
"""
    
    # 添加复杂度分析结果
    if complexity_results:
        report_content += f"""
**Summary**: {complexity_results.get('total_genes', 0)} genes analyzed.
- Mean complexity score: {complexity_results.get('mean_complexity', 0):.1f}
- Complexity class distribution: {complexity_results.get('class_distribution', {})}

**Top 10 Most Complex Promoters**:

| Gene | Total Elements | Unique Elements | Complexity Score | Class |
|------|---------------|-----------------|------------------|-------|
"""
        for score in complexity_results.get('top_complex', [])[:10]:
            report_content += f"| {score['gene']} | {score['total_elements']} | {score['unique_elements']} | {score['complexity_score']} | {score['complexity_class']} |\n"
    else:
        report_content += "\nNo complexity data available.\n"
    
    report_content += f"""

## Notes

- Unknown and Unnamed elements have been filtered out for cleaner analysis
- Element categorization is based on both element name and functional description
- For detailed element positions, refer to the BED file
- Enrichment ratio > 1.5 indicates significant enrichment in a subfamily
- Complexity score combines element count, diversity, and distribution uniformity

## References

1. Lescot et al. (2002) PlantCARE, a database of plant cis-acting regulatory elements. *Nucleic Acids Research*, 30(1), 325-327.
2. PlantCARE Database: https://bioinformatics.psb.ugent.be/webtools/plantcare/
"""
    
    # 写入报告文件
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report_content)
    
    print(f"  ✅ Report generated: {output_file}")
    return output_file


def calculate_enrichment(
    element_counts: Dict[str, int],
    gene_element_counts: Dict[str, int],
    subfamily_file: Optional[Path],
    output_dir: Path,
    prefix: str
) -> Dict:
    """
    计算亚家族水平的元件富集分析
    使用超几何分布检验
    """
    print(f"[Enrichment] Calculating subfamily enrichment...")
    
    results = {
        "subfamilies": {},
        "enriched_elements": [],
        "summary": {}
    }
    
    if not subfamily_file or not subfamily_file.exists():
        print("  ⚠️ Subfamily file not found, skipping enrichment analysis")
        return results
    
    # 读取亚家族信息
    subfamily_map = {}
    subfamily_genes = {}
    with open(subfamily_file, 'r') as f:
        for line in f:
            parts = line.strip().split('\t')
            if len(parts) >= 2:
                gene_id = parts[0]
                subfamily = parts[1]
                subfamily_map[gene_id] = subfamily
                if subfamily not in subfamily_genes:
                    subfamily_genes[subfamily] = []
                subfamily_genes[subfamily].append(gene_id)
    
    print(f"  Found {len(subfamily_genes)} subfamilies")
    
    # 计算每个亚家族的元件富集
    all_elements = set()
    for key in gene_element_counts.keys():
        gene, element = key.split('\t', 1)
        all_elements.add(element)
    
    enrichment_results = []
    
    for subfamily, genes in subfamily_genes.items():
        subfamily_element_counts = {}
        
        # 统计该亚家族的元件
        for gene in genes:
            gene_base = re.sub(r'\.\d+\.T\d+$', '', gene)
            for key, count in gene_element_counts.items():
                key_gene, element = key.split('\t', 1)
                key_gene_base = re.sub(r'\.\d+\.T\d+$', '', key_gene)
                if key_gene == gene or key_gene_base == gene_base or gene in key_gene:
                    subfamily_element_counts[element] = subfamily_element_counts.get(element, 0) + count
        
        # 计算富集（简化版：使用频率比）
        subfamily_total = sum(subfamily_element_counts.values())
        global_total = sum(element_counts.values())
        
        for element, count in subfamily_element_counts.items():
            global_count = element_counts.get(element, 0)
            if global_count > 0 and subfamily_total > 0:
                observed_ratio = count / subfamily_total
                expected_ratio = global_count / global_total
                enrichment_ratio = observed_ratio / expected_ratio if expected_ratio > 0 else 0
                
                if enrichment_ratio > 1.5:  # 富集阈值
                    enrichment_results.append({
                        'subfamily': subfamily,
                        'element': element,
                        'count': count,
                        'subfamily_total': subfamily_total,
                        'global_count': global_count,
                        'enrichment_ratio': enrichment_ratio
                    })
        
        results["subfamilies"][subfamily] = {
            "gene_count": len(genes),
            "element_count": subfamily_total,
            "top_elements": sorted(subfamily_element_counts.items(), key=lambda x: -x[1])[:5]
        }
    
    # 保存富集结果
    enrichment_file = output_dir / f"{prefix}_enrichment_analysis.txt"
    with open(enrichment_file, 'w') as f:
        f.write("# Element Enrichment Analysis by Subfamily\n\n")
        f.write("Subfamily\tElement\tCount\tSubfamilyTotal\tGlobalCount\tEnrichmentRatio\n")
        for result in sorted(enrichment_results, key=lambda x: -x['enrichment_ratio']):
            f.write(f"{result['subfamily']}\t{result['element']}\t{result['count']}\t"
                   f"{result['subfamily_total']}\t{result['global_count']}\t"
                   f"{result['enrichment_ratio']:.2f}\n")
    
    results["enriched_elements"] = enrichment_results
    results["summary"]["total_enriched"] = len(enrichment_results)
    results["summary"]["subfamilies_analyzed"] = len(subfamily_genes)
    
    print(f"  ✅ Enrichment analysis saved: {enrichment_file}")
    print(f"     Found {len(enrichment_results)} enriched element-subfamily pairs")
    
    return results


def calculate_cooccurrence(
    gene_element_counts: Dict[str, int],
    output_dir: Path,
    prefix: str,
    min_cooccurrence: int = 2
) -> Dict:
    """
    计算元件共现模式
    分析哪些元件经常一起出现在同一个基因中
    """
    print(f"[Co-occurrence] Analyzing element co-occurrence patterns...")
    
    # 构建基因-元件矩阵
    gene_elements = {}
    for key, count in gene_element_counts.items():
        gene, element = key.split('\t', 1)
        if gene not in gene_elements:
            gene_elements[gene] = set()
        if count > 0:
            gene_elements[gene].add(element)
    
    # 计算共现
    cooccurrence_counts = {}
    element_pair_genes = {}
    
    for gene, elements in gene_elements.items():
        element_list = sorted(list(elements))
        for i in range(len(element_list)):
            for j in range(i + 1, len(element_list)):
                pair = (element_list[i], element_list[j])
                cooccurrence_counts[pair] = cooccurrence_counts.get(pair, 0) + 1
                if pair not in element_pair_genes:
                    element_pair_genes[pair] = []
                element_pair_genes[pair].append(gene)
    
    # 筛选显著共现
    significant_pairs = {pair: count for pair, count in cooccurrence_counts.items() 
                        if count >= min_cooccurrence}
    
    # 保存共现结果
    cooccurrence_file = output_dir / f"{prefix}_cooccurrence_analysis.txt"
    with open(cooccurrence_file, 'w') as f:
        f.write("# Element Co-occurrence Analysis\n\n")
        f.write("Element1\tElement2\tCo-occurrenceCount\tGenes\n")
        for pair, count in sorted(significant_pairs.items(), key=lambda x: -x[1]):
            genes = ','.join(element_pair_genes[pair][:5])  # 只显示前5个基因
            if len(element_pair_genes[pair]) > 5:
                genes += f",...({len(element_pair_genes[pair])} total)"
            f.write(f"{pair[0]}\t{pair[1]}\t{count}\t{genes}\n")
    
    # 计算共现网络统计
    element_connections = {}
    for pair, count in significant_pairs.items():
        for elem in pair:
            if elem not in element_connections:
                element_connections[elem] = []
            element_connections[elem].append((pair[0] if pair[1] == elem else pair[1], count))
    
    # 找出hub元件（连接最多的元件）
    hub_elements = sorted(element_connections.items(), 
                         key=lambda x: len(x[1]), reverse=True)[:10]
    
    results = {
        "total_pairs": len(cooccurrence_counts),
        "significant_pairs": len(significant_pairs),
        "hub_elements": hub_elements,
        "top_pairs": sorted(significant_pairs.items(), key=lambda x: -x[1])[:20]
    }
    
    print(f"  ✅ Co-occurrence analysis saved: {cooccurrence_file}")
    print(f"     Total pairs: {len(cooccurrence_counts)}")
    print(f"     Significant pairs (≥{min_cooccurrence}): {len(significant_pairs)}")
    
    return results


def calculate_promoter_complexity(
    gene_element_counts: Dict[str, int],
    element_counts: Dict[str, int],
    output_dir: Path,
    prefix: str
) -> Dict:
    """
    计算启动子复杂度评分
    基于元件数量、多样性和分布
    """
    print(f"[Complexity] Calculating promoter complexity scores...")
    
    # 按基因统计
    gene_stats = {}
    for key, count in gene_element_counts.items():
        gene, element = key.split('\t', 1)
        if gene not in gene_stats:
            gene_stats[gene] = {
                'total_elements': 0,
                'unique_elements': 0,
                'element_types': set(),
                'element_counts': {}
            }
        gene_stats[gene]['total_elements'] += count
        gene_stats[gene]['unique_elements'] += 1
        gene_stats[gene]['element_types'].add(element)
        gene_stats[gene]['element_counts'][element] = count
    
    # 计算复杂度评分
    complexity_scores = []
    
    for gene, stats in gene_stats.items():
        # 基础分数：元件总数
        base_score = stats['total_elements']
        
        # 多样性加分：独特元件类型数
        diversity_bonus = stats['unique_elements'] * 2
        
        # 分布均匀度（Shannon多样性指数简化版）
        total = stats['total_elements']
        if total > 0:
            shannon = 0
            for count in stats['element_counts'].values():
                if count > 0:
                    p = count / total
                    shannon += p * (p ** 0.5)  # 简化计算
            uniformity_score = stats['unique_elements'] * (1 - shannon)
        else:
            uniformity_score = 0
        
        # 综合复杂度评分
        complexity = base_score + diversity_bonus + uniformity_score
        
        # 分类
        if complexity >= 50:
            complexity_class = "Very High"
        elif complexity >= 30:
            complexity_class = "High"
        elif complexity >= 15:
            complexity_class = "Medium"
        else:
            complexity_class = "Low"
        
        complexity_scores.append({
            'gene': gene,
            'total_elements': stats['total_elements'],
            'unique_elements': stats['unique_elements'],
            'complexity_score': round(complexity, 2),
            'complexity_class': complexity_class
        })
    
    # 排序
    complexity_scores.sort(key=lambda x: -x['complexity_score'])
    
    # 保存复杂度评分
    complexity_file = output_dir / f"{prefix}_promoter_complexity.txt"
    with open(complexity_file, 'w') as f:
        f.write("# Promoter Complexity Analysis\n\n")
        f.write("Gene\tTotalElements\tUniqueElements\tComplexityScore\tComplexityClass\n")
        for score in complexity_scores:
            f.write(f"{score['gene']}\t{score['total_elements']}\t"
                   f"{score['unique_elements']}\t{score['complexity_score']}\t"
                   f"{score['complexity_class']}\n")
    
    # 统计摘要
    class_distribution = {}
    for score in complexity_scores:
        cclass = score['complexity_class']
        class_distribution[cclass] = class_distribution.get(cclass, 0) + 1
    
    results = {
        "total_genes": len(complexity_scores),
        "mean_complexity": sum(s['complexity_score'] for s in complexity_scores) / len(complexity_scores) if complexity_scores else 0,
        "max_complexity": complexity_scores[0] if complexity_scores else None,
        "min_complexity": complexity_scores[-1] if complexity_scores else None,
        "class_distribution": class_distribution,
        "top_complex": complexity_scores[:10]
    }
    
    print(f"  ✅ Complexity analysis saved: {complexity_file}")
    print(f"     Genes analyzed: {len(complexity_scores)}")
    print(f"     Mean complexity: {results['mean_complexity']:.1f}")
    print(f"     Class distribution: {class_distribution}")
    
    return results


PROMOTER_VIZ_SUFFIXES = {".pdf", ".png", ".csv", ".tsv"}


def _promoter_viz_glob_patterns(output_prefix: str) -> List[str]:
    return [
        f"{output_prefix}_v6_combined.*",
        f"{output_prefix}_*.pdf",
        f"{output_prefix}_*.png",
        f"{output_prefix}_*.csv",
    ]


def collect_promoter_viz_files(output_dir: Path, output_prefix: str) -> List[Path]:
    """Collect promoter visualization artifacts from the target output directory."""
    seen: set[Path] = set()
    files: List[Path] = []
    for pattern in _promoter_viz_glob_patterns(output_prefix):
        for path in sorted(output_dir.glob(pattern)):
            if not path.is_file() or path.suffix.lower() not in PROMOTER_VIZ_SUFFIXES:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(path)
    return files


def relocate_stray_promoter_viz(project_root: Path, final_dir: Path, output_prefix: str) -> List[Path]:
    """Move or remove promoter viz files accidentally written to the project root."""
    relocated: List[Path] = []
    if project_root.resolve() == final_dir.resolve():
        return relocated
    for pattern in _promoter_viz_glob_patterns(output_prefix):
        for path in sorted(project_root.glob(pattern)):
            if not path.is_file() or path.suffix.lower() not in PROMOTER_VIZ_SUFFIXES:
                continue
            dest = final_dir / path.name
            if dest.exists():
                path.unlink(missing_ok=True)
                relocated.append(dest)
                print(f"  🧹 Removed duplicate root viz file: {path.name}")
                continue
            shutil.move(str(path), str(dest))
            relocated.append(dest)
            print(f"  🧹 Relocated stray root viz file: {path.name} -> final/")
    return relocated


def run_r_visualization(
    tree_file: Path,
    subfamily_file: Optional[Path],
    promoter_file: Path,
    element_desc_file: Optional[Path],
    output_prefix: str,
    output_dir: Path,
    project_root: Path = PROJECT_ROOT,
    rscript_path: str = DEFAULT_RSCRIPT,
) -> Tuple[bool, List[Path]]:
    """
    运行R脚本进行可视化。

    R 脚本 historically wrote relative paths into the process CWD. We now run them
    with ``cwd=output_dir`` (workspace/<job>/final) and relocate any legacy root
    artifacts afterward.
    """
    print(f"[Step 7] Running R visualization")
    print(f"  Tree: {tree_file}")
    print(f"  Subfamily: {subfamily_file}")
    print(f"  Promoter: {promoter_file}")
    print(f"  Element desc: {element_desc_file}")
    print(f"  Output prefix: {output_prefix}")
    print(f"  Output dir: {output_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)

    def _abs_path(path: Optional[Path]) -> str:
        if path and path.exists():
            return str(path.resolve())
        return "NA"

    def _run_r_script(script_rel: str, label: str) -> None:
        script = project_root / script_rel
        if not script.exists():
            print(f"  ⚠️ {label} script not found: {script}")
            return
        cmd = [
            rscript_path if Path(rscript_path).name.lower().startswith("rscript") else RSCRIPT_BIN,
            str(script.resolve()),
            _abs_path(tree_file),
            _abs_path(subfamily_file),
            _abs_path(promoter_file),
            _abs_path(element_desc_file),
            output_prefix,
        ]
        rc, _stdout, stderr = run_cmd(cmd, cwd=output_dir)
        collected = collect_promoter_viz_files(output_dir, output_prefix)
        if rc == 0:
            print(f"  ✅ {label} completed ({len(collected)} files in final/)")
        else:
            print(f"  ⚠️ {label} failed (exit {rc}); kept {len(collected)} partial outputs in final/")
            if stderr.strip():
                print(stderr[-1200:])

    _run_r_script("scripts/promoter_v6_combined.R", "V6 visualization")
    _run_r_script("scripts/promoter_position_distribution.R", "Position distribution plot")
    _run_r_script("scripts/promoter_visualization_enhanced_v2.R", "Additional plots")

    relocate_stray_promoter_viz(project_root, output_dir, output_prefix)
    output_files = collect_promoter_viz_files(output_dir, output_prefix)
    if output_files:
        return True, output_files
    print("  ❌ No visualization files generated")
    return False, []


def prepare_directories(workspace: Path) -> Dict[str, Path]:
    """创建Promoter阶段的标准目录结构"""
    paths = {
        "analysis_dir": workspace / "promoter" / "analysis",
        "final_dir": workspace / "final",
        "logs_dir": workspace / "logs",
    }
    
    print("创建目录结构:")
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        print(f"  {name}: {path}")
    
    return paths


def write_manifest(
    manifest_path: Path,
    job_id: str,
    run_id: str,
    prefix: str,
    start_time: datetime,
    end_time: datetime,
    artifacts: Dict[str, Dict],
    figures: List[Dict],
    metrics: Dict[str, Any],
    parameters: Dict[str, Any],
    tool_versions: Dict[str, str],
    cmd_history: List[str],
    workspace: Path,
    input_dir: Path,
    status: str = "success"
):
    """
    写入符合schema v1的manifest文件
    
    注意：artifacts必须是字典格式，不是列表
    """
    elapsed_sec = (end_time - start_time).total_seconds()
    
    manifest_data = {
        # 基础标识字段（必需）
        "manifest_version": "1.0",
        "job_id": job_id,
        "stage": "promoter",
        "run_id": run_id,
        "prefix": prefix,
        
        # 状态和时间字段（必需）
        "timestamp": end_time.isoformat(),
        "status": status,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "elapsed_sec": round(elapsed_sec, 2),
        
        # 执行信息（必需）
        "cmd": cmd_history,
        "tool_versions": tool_versions,
        
        # 目录路径（必需）
        "workspace_root": str(workspace.parent),
        "workspace": str(workspace),
        "input_dir": str(input_dir),
        
        # 输出产物（必需）- 必须是字典格式
        "artifacts": artifacts,
        
        # 图片列表（用于前端展示）
        "figures": figures,
        
        # 参数和指标（必需）
        "parameters": parameters,
        "metrics": metrics,
        
        # 溯源信息（可选但推荐）
        "provenance": {
            "stage_sequence": ["promoter"]
        }
    }
    for key in (
        "scope_id",
        "scope_mode",
        "scope_file",
        "target_species",
        "reference_species",
        "comparative_species",
        "selected_species",
        "family",
    ):
        if key in parameters:
            manifest_data[key] = parameters[key]
    
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"生成Manifest文件: {manifest_path}")
    
    # 确保JSON严格性：无注释，确保ASCII
    with open(manifest_path, "w", encoding='utf-8') as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)
    
    return manifest_path



def _arg_value(argv: List[str], name: str, default: Optional[str] = None) -> Optional[str]:
    try:
        idx = argv.index(name)
    except ValueError:
        return default
    if idx + 1 >= len(argv):
        return default
    return argv[idx + 1]


def write_failure_promoter_manifest_from_argv(error_msg: str) -> None:
    """Best-effort failure manifest so the web UI can show the real promoter error."""
    workspace_arg = _arg_value(sys.argv, "--workspace")
    prefix = _arg_value(sys.argv, "--prefix", "promoter") or "promoter"
    input_arg = _arg_value(sys.argv, "--input-dir", "") or ""
    scope_arg = _arg_value(sys.argv, "--scope-file", "") or ""
    scope_data = {}
    if scope_arg:
        try:
            scope_path = Path(scope_arg)
            if scope_path.exists():
                scope_data = read_scope(scope_path)
        except Exception:
            scope_data = {}
    if not workspace_arg:
        return

    workspace = Path(workspace_arg)
    input_dir = Path(input_arg) if input_arg else workspace.parent / "input" / workspace.name
    manifest_path = workspace / "final" / f"{prefix}.promoter.manifest.json"
    now = datetime.now()
    manifest = {
        "manifest_version": "1.0",
        "job_id": workspace.name,
        "stage": "promoter",
        "run_id": str(uuid.uuid4()),
        "prefix": prefix,
        "timestamp": now.isoformat(),
        "status": "failed",
        "start_time": now.isoformat(),
        "end_time": now.isoformat(),
        "elapsed_sec": 0,
        "cmd": [" ".join(sys.argv)],
        "tool_versions": get_tool_versions(),
        "workspace_root": str(workspace.parent),
        "workspace": str(workspace),
        "input_dir": str(input_dir),
        "artifacts": {},
        "figures": [],
        "parameters": {
            "error": error_msg,
            "scope_file": scope_arg,
            "scope_id": scope_data.get("scope_id", ""),
            "scope_mode": scope_data.get("scope_mode", ""),
            "target_species": scope_data.get("target_species", []),
            "reference_species": scope_data.get("reference_species", []),
            "comparative_species": scope_data.get("comparative_species", []),
            "selected_species": scope_data.get("selected_species", []),
            "family": scope_data.get("family", ""),
        },
        "metrics": {"error": error_msg},
        "provenance": {"stage_sequence": ["promoter"]},
    }
    if scope_data:
        manifest.update({
            "scope_id": scope_data.get("scope_id", ""),
            "scope_mode": scope_data.get("scope_mode", ""),
            "scope_file": scope_arg,
            "target_species": scope_data.get("target_species", []),
            "reference_species": scope_data.get("reference_species", []),
            "comparative_species": scope_data.get("comparative_species", []),
            "selected_species": scope_data.get("selected_species", []),
            "family": scope_data.get("family", ""),
        })
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        print(f"[ERROR] Wrote failure manifest: {manifest_path}")
        scope_id = scope_data.get("scope_id", "")
        if scope_id:
            scoped_path = workspace / "final" / f"{prefix}.{sanitize_token(scope_id)}.promoter.manifest.json"
            with open(scoped_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
            print(f"[ERROR] Wrote scoped failure manifest: {scoped_path}")
    except Exception as manifest_error:
        print(f"[WARN] Failed to write promoter failure manifest: {manifest_error}")

def main():
    parser = argparse.ArgumentParser(
        description="Promoter (cis-acting element) analysis using PlantCARE"
    )
    
    # 输入参数
    parser.add_argument("--input-dir", type=str, required=True,
                        help="Input directory containing GFF3 and genome files")
    parser.add_argument("--workspace", type=str, required=True,
                        help="Workspace directory")
    parser.add_argument("--prefix", type=str, required=True,
                        help="Analysis prefix (e.g., Ft.SPL)")
    
    # 可选参数
    parser.add_argument("--upstream-length", type=int, default=DEFAULT_UPSTREAM_LENGTH,
                        help=f"Upstream sequence length (default: {DEFAULT_UPSTREAM_LENGTH})")
    parser.add_argument("--cpu", type=int, default=8,
                        help="Number of CPU cores")
    parser.add_argument("--email", type=str, default=EMAIL_ACCOUNT,
                        help="Email address for PlantCARE results (default: PLANTCARE_EMAIL_ACCOUNT)")
    parser.add_argument("--timeout", type=int, default=0,
                        help="Timeout waiting for email (auto-calculated based on gene count, max 60min)")
    parser.add_argument("--plantcare-max-records", type=int, default=DEFAULT_PLANTCARE_MAX_RECORDS,
                        help=f"Maximum FASTA records per PlantCARE upload batch (default: {DEFAULT_PLANTCARE_MAX_RECORDS})")
    parser.add_argument("--plantcare-max-upload-bytes", type=int, default=DEFAULT_PLANTCARE_MAX_UPLOAD_BYTES,
                        help=f"Maximum FASTA bytes per PlantCARE upload batch (default: {DEFAULT_PLANTCARE_MAX_UPLOAD_BYTES})")
    parser.add_argument("--plantcare-submit-timeout", type=int, default=DEFAULT_PLANTCARE_SUBMIT_TIMEOUT,
                        help=f"HTTP timeout for each PlantCARE submission request in seconds (default: {DEFAULT_PLANTCARE_SUBMIT_TIMEOUT})")
    parser.add_argument("--rscript", type=str, default=DEFAULT_RSCRIPT,
                        help="Rscript executable for visualization (default: Rscript)")
    parser.add_argument("--log-file", type=str, default=None,
                        help="Log file path")
    
    # 直接指定文件
    parser.add_argument("--gff3", type=str, default=None,
                        help="GFF3 file (optional, will auto-detect if not provided)")
    parser.add_argument("--genome", type=str, default=None,
                        help="Genome FASTA file (optional, will auto-detect if not provided)")
    parser.add_argument("--gene-id", type=str, default=None,
                        help="Gene ID list file (optional)")
    parser.add_argument("--tree", type=str, default=None,
                        help="Phylogenetic tree file (Newick)")
    parser.add_argument("--subfamily", type=str, default=None,
                        help="Subfamily classification file")
    parser.add_argument("--element-desc", type=str, default=None,
                        help="Element description CSV file")
    parser.add_argument("--plantcare-tab", type=str, default=None,
                        help="Existing PlantCARE .tab result; skips submission/email polling")
    parser.add_argument("--scope-file", type=str, default=None,
                        help="Gene-family scope JSON from resolve_scope.py")
    parser.add_argument("--force", action="store_true",
                        help="Force re-submission to PlantCARE (ignore existing results)")
    
    args = parser.parse_args()

    if not args.plantcare_tab:
        if not args.email:
            raise RuntimeError("PlantCARE email is not configured. Set PLANTCARE_EMAIL_ACCOUNT in .env or pass --email.")
        if not EMAIL_PASSWORD:
            raise RuntimeError("PlantCARE IMAP authorization code is not configured. Set PLANTCARE_EMAIL_PASSWORD in .env.")
    
    # 初始化路径
    input_dir = Path(args.input_dir)
    workspace = Path(args.workspace)
    prefix = args.prefix
    scope_file: Optional[Path] = None
    scope_data: Dict[str, Any] = {}
    scope_gff3: Optional[Path] = None
    scope_genome: Optional[Path] = None
    if args.scope_file:
        scope_file = Path(args.scope_file)
        if not scope_file.exists():
            raise FileNotFoundError(f"Scope file not found: {args.scope_file}")
        scope_data, scope_gff3, scope_genome = resolve_promoter_scope_inputs(scope_file)
    
    # 创建标准目录结构
    print("\n📁 准备目录结构...")
    paths = prepare_directories(workspace)
    analysis_dir = paths["analysis_dir"]
    final_dir = paths["final_dir"]
    promoter_dir = workspace / "promoter"
    
    # 如果强制重新提交，清除旧的 PlantCARE 结果
    if args.force:
        print("\n⚠️  Force mode: removing existing PlantCARE results...")
        for old_file in analysis_dir.glob("*.tab"):
            print(f"  Removing: {old_file.name}")
            old_file.unlink()
        for old_file in analysis_dir.glob("PlantCARE_output_*.tar.gz"):
            print(f"  Removing: {old_file.name}")
            old_file.unlink()
        print("  ✅ Old results cleared\n")
    
    print("=" * 60)
    print(f"Promoter Analysis - PlantCARE")
    print(f"Prefix: {prefix}")
    print(f"Input: {input_dir}")
    print(f"Workspace: {workspace}")
    print("=" * 60)
    
    start_time = datetime.now()
    
    # ========== Step 1: 准备数据 ==========
    print("\n[Step 1] Preparing data...")
    
    # 查找输入文件。Promoter 优先使用 genome_prepare 的 *_final.gff3，
    # 因为它是后续 structure/synteny 等阶段共用的规范化注释。
    final_gff3 = find_scope_target_final_gff3(input_dir, workspace, scope_data) if scope_data else None
    if final_gff3:
        print(f"  ✅ Using genome_prepare final GFF3 for promoter: {final_gff3}")
    gff3_file = Path(args.gff3) if args.gff3 else (final_gff3 or scope_gff3 or find_gff3_file(input_dir, prefix))
    genome_file = Path(args.genome) if args.genome else (scope_genome or find_genome_file(input_dir, prefix))
    gene_id_file = Path(args.gene_id) if args.gene_id else find_gene_id_file(input_dir, final_dir, prefix)
    
    if not gff3_file:
        raise FileNotFoundError(f"GFF3 file not found in {input_dir}")
    if not genome_file:
        raise FileNotFoundError(f"Genome file not found in {input_dir}")
    
    print(f"  GFF3: {gff3_file}")
    print(f"  Genome: {genome_file}")
    print(f"  Gene ID: {gene_id_file}")
    
    # 准备启动子序列
    promoter_fasta = prepare_promoter_sequences(
        gff3_file, genome_file, gene_id_file,
        analysis_dir, args.upstream_length
    )
    print(f"  Promoter FASTA: {promoter_fasta}")
    
    plantcare_batching_enabled = False
    plantcare_batch_files: List[Path] = []
    plantcare_upload_files: List[Path] = []
    plantcare_batch_result_files: List[Path] = []
    plantcare_merged_result: Optional[Path] = None
    plantcare_max_upload_bytes = max(0, int(args.plantcare_max_upload_bytes or 0))
    plantcare_max_records = max(0, int(args.plantcare_max_records or 0))
    plantcare_submit_timeout = max(1, int(args.plantcare_submit_timeout or DEFAULT_PLANTCARE_SUBMIT_TIMEOUT))
    plantcare_submission_mode = "manual_tab" if args.plantcare_tab else "single"

    if args.plantcare_tab:
        result_file = Path(args.plantcare_tab)
        if not result_file.exists():
            raise FileNotFoundError(f"PlantCARE tab file not found: {result_file}")
        try:
            result_file.resolve().relative_to(workspace.resolve())
        except ValueError:
            copied_tab = analysis_dir / result_file.name
            if copied_tab.resolve() != result_file.resolve():
                shutil.copy2(result_file, copied_tab)
            result_file = copied_tab
        print("\n[Step 2] Using existing PlantCARE result")
        print(f"  PlantCARE tab: {result_file}")
    else:
        # ========== Step 2: 提交到PlantCARE ==========
        print("\n[Step 2] Submitting to PlantCARE...")

        # 替换点号为下划线，避免PlantCARE处理问题
        plantcare_ref = prefix.replace(".", "_")
        batch_fastas = split_fasta_by_upload_size(
            promoter_fasta,
            max_bytes=plantcare_max_upload_bytes,
            max_records=plantcare_max_records,
        )
        plantcare_upload_files = materialize_plantcare_upload_fastas(batch_fastas, plantcare_ref, analysis_dir)
        plantcare_batching_enabled = len(plantcare_upload_files) > 1
        plantcare_submission_mode = "batched" if plantcare_batching_enabled else "single"
        plantcare_batch_files = batch_fastas if plantcare_batching_enabled else []

        if plantcare_batching_enabled:
            print(f"  Submitting {len(plantcare_upload_files)} PlantCARE batches for reference {plantcare_ref}")
        else:
            print(f"  Single PlantCARE upload: {plantcare_upload_files[0].stat().st_size} bytes")

        submitted_batches: List[Tuple[Path, str, datetime]] = []
        for batch_index, batch_fasta in enumerate(plantcare_upload_files, 1):
            batch_ref = plantcare_ref if len(batch_fastas) == 1 else f"{plantcare_ref}_b{batch_index:03d}"
            print(f"\n  [Batch {batch_index}/{len(batch_fastas)}] Reference: {batch_ref}")
            submission_time = datetime.now()
            success, msg = submit_to_plantcare(
                batch_fasta,
                args.email,
                batch_ref,
                submit_timeout=plantcare_submit_timeout,
            )
            if not success:
                print(f"  ❌ Submission failed for {batch_ref}: {msg}")
                sys.exit(1)
            submitted_batches.append((batch_fasta, batch_ref, submission_time))

        # ========== Step 3: 等待结果邮件 ==========
        print("\n[Step 3] Waiting for PlantCARE results...")
        for batch_index, (batch_fasta, batch_ref, submission_time) in enumerate(submitted_batches, 1):
            print(f"\n  [Batch {batch_index}/{len(submitted_batches)}] Waiting result for reference: {batch_ref}")
            gene_count = count_fasta_records(batch_fasta)
            print(f"  Gene count for timeout calculation: {gene_count}")

            batch_result = wait_for_plantcare_email(analysis_dir, gene_count, args.timeout, 0, batch_ref, submission_time)
            if not batch_result:
                print(f"  ❌ No PlantCARE result returned for batch reference {batch_ref}")
                result_file = None
                break
            plantcare_batch_result_files.append(batch_result)

        if plantcare_batch_result_files and len(plantcare_batch_result_files) == len(batch_fastas):
            if len(plantcare_batch_result_files) == 1:
                result_file = plantcare_batch_result_files[0]
            else:
                plantcare_merged_result = analysis_dir / "plantCARE_output_merged.tab"
                if not merge_plantcare_results(plantcare_batch_result_files, plantcare_merged_result):
                    print("  ❌ Failed to merge PlantCARE batch results")
                    sys.exit(1)
                result_file = plantcare_merged_result

    if not result_file:
        print("  ⚠️ Could not retrieve PlantCARE results via email")
        print("  💡 Please manually download from email and place in analysis/")
        # 创建空的结果文件以便继续
        print("  🔄 Attempting to continue with manual result handling...")
        # 查找已存在的tab文件
        tab_files = list(analysis_dir.glob("*.tab"))
        if tab_files:
            result_file = tab_files[0]
            print(f"  📂 Using existing tab file: {result_file}")
        else:
            print("  ❌ No result file found, exiting")
            sys.exit(1)

    validate_plantcare_matches_promoters(result_file, promoter_fasta)
    result_file = write_canonical_plantcare_tab(result_file, analysis_dir)
    
    # ========== Step 4: 解析结果 ==========
    print("\n[Step 4] Parsing PlantCARE results...")
    
    parse_results = parse_plantcare_output(result_file, analysis_dir)
    print(f"  Total elements: {parse_results['total_elements']}")
    print(f"  Unique elements: {parse_results['unique_elements']}")
    print(f"  Genes with elements: {parse_results['genes_with_elements']}")
    
    # ========== Step 5: R可视化 (如果树文件存在) ==========
    print("\n[Step 5] Visualization...")
    
    tree_file = Path(args.tree) if args.tree else None
    subfamily_file = Path(args.subfamily) if args.subfamily else None
    element_desc_file = Path(args.element_desc) if args.element_desc else None
    
    # 生成可能的prefix变体（供后续查找使用）
    prefix_variants = [
        prefix,
        prefix.replace("_", "."),
        prefix.replace(".", "_"),
        prefix.replace("_", ""),
        prefix.replace(".", "")
    ]
    
    # 尝试自动查找树文件（支持多种prefix变体）
    if not tree_file:
        if scope_data.get("scope_id"):
            scoped_tree_manifest = final_dir / f"{prefix}.{sanitize_token(str(scope_data['scope_id']))}.phylogeny.manifest.json"
            if scoped_tree_manifest.exists():
                try:
                    manifest_data = json.loads(scoped_tree_manifest.read_text(encoding="utf-8"))
                    tree_text = str((manifest_data.get("artifacts", {}).get("tree", {}) or {}).get("full_path") or "")
                    if tree_text and Path(tree_text).exists():
                        tree_file = Path(tree_text)
                        print(f"  ✅ Found scoped tree file: {tree_file}")
                except Exception as exc:
                    print(f"  ⚠️ Could not read scoped phylogeny manifest: {exc}")
        # 尝试每种变体
        if not tree_file:
            for pv in prefix_variants:
                possible_tree = final_dir / f"{pv}.tree.nwk"
                print(f"  Looking for tree file: {possible_tree}")
                if possible_tree.exists():
                    tree_file = possible_tree
                    print(f"  ✅ Found tree file with prefix variant: {pv}")
                    break
        
        if not tree_file:
            print(f"  ⚠️ Tree file not found. Tried variants: {prefix_variants}")
    
    # 自动查找亚家族文件时只接受 confirmed_subfamily。
    # reference_classification/geneID 属于预测或旧格式，不能在正式下游阶段静默替代人工确认。
    if not subfamily_file:
        confirmed_candidates = []
        if scope_data.get("scope_id"):
            scoped = f"{prefix}.{sanitize_token(str(scope_data['scope_id']))}"
            confirmed_candidates.extend([
                final_dir / f"{scoped}.confirmed_subfamily.tsv",
                input_dir / f"{scoped}.confirmed_subfamily.tsv",
            ])
        confirmed_candidates.extend([
            final_dir / f"{prefix}.confirmed_subfamily.tsv",
            input_dir / f"{prefix}.confirmed_subfamily.tsv",
        ])
        for pv in prefix_variants:
            confirmed_candidates.extend([
                final_dir / f"{pv}.confirmed_subfamily.tsv",
                input_dir / f"{pv}.confirmed_subfamily.tsv",
            ])
        seen = set()
        for candidate in confirmed_candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.exists() and candidate.stat().st_size > 0:
                subfamily_file = candidate
                print(f"  ✅ Found confirmed subfamily file: {subfamily_file}")
                break
    
    # 如果没有提供元件描述文件，尝试生成完整描述
    if element_desc_file is None or not element_desc_file.exists():
        print("  📝 Generating element description file...")
        desc_file = analysis_dir / f"{prefix}_element_desc.csv"
        
        # 从结果文件中提取唯一元件列表
        elements = set()
        if result_file.exists():
            with open(result_file, 'r') as f:
                for line in f:
                    parts = line.strip().split('\t')
                    if len(parts) >= 2 and parts[1]:
                        elements.add(parts[1])
        
        if elements:
            element_list = sorted(elements)
            print(f"  ✅ Found {len(element_list)} unique elements")
            
            # 尝试使用 tiqu_miaoshuwenjian.py 生成完整描述
            tiqu_script = PROJECT_ROOT / "scripts" / "tiqu_miaoshuwenjian.py"
            if tiqu_script.exists():
                print(f"  🔍 Querying PlantCARE database for full descriptions...")
                
                # 创建初始描述文件
                desc_data = {elem: elem for elem in element_list}  # 默认使用元件名作为描述
                
                # 循环查询直到所有描述都获取完毕或达到最大重试次数
                max_rounds = 3  # 最多查询3轮
                for round_num in range(max_rounds):
                    # 统计还缺少描述的元件
                    missing_elements = [elem for elem, desc in desc_data.items() if desc == elem or len(desc) < 5]
                    
                    if not missing_elements:
                        print(f"  ✅ All {len(element_list)} descriptions completed!")
                        break
                    
                    print(f"\n  📊 Round {round_num + 1}/{max_rounds}: {len(missing_elements)} elements missing descriptions")
                    
                    # 为缺少描述的元件创建临时查询文件
                    temp_input = analysis_dir / f"{prefix}_element_desc_temp_round{round_num + 1}.csv"
                    with open(temp_input, 'w', newline='', encoding='utf-8') as f:
                        writer = csv.writer(f)
                        writer.writerow(['element', 'description'])
                        for elem in missing_elements:
                            writer.writerow([elem, elem])
                    
                    temp_output = analysis_dir / f"{prefix}_element_desc_round{round_num + 1}.csv"
                    
                    # 调用 tiqu_miaoshuwenjian.py 查询
                    cmd = [
                        "python", str(tiqu_script),
                        "--input", str(temp_input),
                        "--output", str(temp_output)
                    ]
                    
                    try:
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                        
                        if result.returncode == 0 and temp_output.exists():
                            # 读取查询结果并更新描述
                            with open(temp_output, 'r', encoding='utf-8') as f:
                                reader = csv.DictReader(f)
                                for row in reader:
                                    elem = row.get('element', '').strip()
                                    desc = row.get('description', '').strip()
                                    if elem and desc and desc != elem and len(desc) > 3:
                                        desc_data[elem] = desc
                            
                            # 统计本轮更新了多少
                            updated = sum(1 for elem in missing_elements 
                                        if desc_data.get(elem, elem) != elem and len(desc_data.get(elem, elem)) > 3)
                            print(f"     ✓ Updated {updated}/{len(missing_elements)} descriptions in this round")
                        else:
                            print(f"     ⚠️ Query failed for this round")
                            
                    except subprocess.TimeoutExpired:
                        print(f"     ⏱️ Query timeout for this round")
                    except Exception as e:
                        print(f"     ⚠️ Query error: {e}")
                
                # 保存最终描述文件
                with open(desc_file, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(['element', 'description'])
                    for elem in sorted(desc_data.keys()):
                        writer.writerow([elem, desc_data[elem]])
                
                # 统计最终结果
                completed = sum(1 for elem, desc in desc_data.items() if desc != elem and len(desc) > 3)
                print(f"\n  ✅ Description generation complete: {completed}/{len(element_list)} elements have full descriptions")
                element_desc_file = desc_file
                
                # 清理临时文件
                for temp_file in analysis_dir.glob(f"{prefix}_element_desc_temp_round*.csv"):
                    temp_file.unlink(missing_ok=True)
                for temp_file in analysis_dir.glob(f"{prefix}_element_desc_round*.csv"):
                    temp_file.unlink(missing_ok=True)
                    
            else:
                # tiqu_miaoshuwenjian.py 不存在，使用简化版本
                print(f"  ℹ️  tiqu_miaoshuwenjian.py not found, using simplified version")
                with open(desc_file, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(['element', 'description'])
                    for elem in sorted(elements):
                        writer.writerow([elem, elem])
                element_desc_file = desc_file
                print(f"     Created simplified description file: {desc_file.name}")
    
    viz_success = False
    viz_files = []
    if tree_file and tree_file.exists():
        # 使用过滤后的tab文件进行可视化
        filtered_tab_file = analysis_dir / "plantCARE_output_filtered.tab"
        viz_input_file = filtered_tab_file if filtered_tab_file.exists() else result_file
        print(f"  📊 Using filtered data for visualization: {viz_input_file.name}")
        
        viz_success, viz_files = run_r_visualization(
            tree_file,
            subfamily_file,
            viz_input_file,
            element_desc_file,
            f"{prefix}_promoter",
            output_dir=final_dir,
            project_root=PROJECT_ROOT,
            rscript_path=args.rscript,
        )
        if viz_files:
            print(f"  📁 Visualization outputs in final/: {len(viz_files)} file(s)")
    else:
        print("  ⚠️ Tree file not found, skipping visualization")
        print("  💡 Run phylogeny first or provide --tree parameter")
    
    # ========== Step 6: 高级分析 ==========
    print("\n[Step 6] Advanced analysis...")
    
    # 6.1 元件富集分析
    enrichment_results = calculate_enrichment(
        element_counts=parse_results.get('element_counts', {}),
        gene_element_counts=parse_results.get('gene_element_counts', {}),
        subfamily_file=subfamily_file,
        output_dir=analysis_dir,
        prefix=prefix
    )
    
    # 6.2 元件共现模式分析
    cooccurrence_results = calculate_cooccurrence(
        gene_element_counts=parse_results.get('gene_element_counts', {}),
        output_dir=analysis_dir,
        prefix=prefix,
        min_cooccurrence=2
    )
    
    # 6.3 启动子复杂度评分
    complexity_results = calculate_promoter_complexity(
        gene_element_counts=parse_results.get('gene_element_counts', {}),
        element_counts=parse_results.get('element_counts', {}),
        output_dir=analysis_dir,
        prefix=prefix
    )
    
    # ========== Step 7: 生成manifest ==========
    print("\n[Step 7] Generating manifest...")
    
    end_time = datetime.now()
    elapsed = (end_time - start_time).total_seconds()
    
    # 收集artifacts
    artifacts = {}
    
    # promoter结果文件
    if result_file.exists():
        artifacts["plantcare_output"] = {
            "artifact": "plantcare_output",
            "path": str(result_file.relative_to(workspace)),
            "full_path": str(result_file.resolve()),
            "type": "table",
            "size": result_file.stat().st_size,
            "sha256": compute_file_hash(result_file)
        }
    
    # 元件统计
    element_counts_file = analysis_dir / "element_counts.txt"
    if element_counts_file.exists():
        artifacts["element_counts"] = {
            "artifact": "element_counts",
            "path": str(element_counts_file.relative_to(workspace)),
            "full_path": str(element_counts_file.resolve()),
            "type": "table",
            "size": element_counts_file.stat().st_size
        }
    
    # BED文件
    bed_file = analysis_dir / "PlantCARE.bed"
    if bed_file.exists():
        artifacts["bed_file"] = {
            "artifact": "bed_file",
            "path": str(bed_file.relative_to(workspace)),
            "full_path": str(bed_file.resolve()),
            "type": "bed",
            "size": bed_file.stat().st_size
        }
    
    # 过滤后的结果文件
    filtered_tab_file = analysis_dir / "plantCARE_output_filtered.tab"
    if filtered_tab_file.exists():
        artifacts["filtered_plantcare_output"] = {
            "artifact": "filtered_plantcare_output",
            "path": str(filtered_tab_file.relative_to(workspace)),
            "full_path": str(filtered_tab_file.resolve()),
            "type": "table",
            "size": filtered_tab_file.stat().st_size,
            "sha256": compute_file_hash(filtered_tab_file)
        }
        print(f"  📊 Added filtered output to artifacts")
    
    # 过滤统计文件
    filtered_stats_file = analysis_dir / "filtered_elements_stats.txt"
    if filtered_stats_file.exists():
        artifacts["filtered_stats"] = {
            "artifact": "filtered_stats",
            "path": str(filtered_stats_file.relative_to(workspace)),
            "full_path": str(filtered_stats_file.resolve()),
            "type": "text",
            "size": filtered_stats_file.stat().st_size
        }
        print(f"  📊 Added filtered stats to artifacts")
    
    # 富集分析结果
    enrichment_file = analysis_dir / f"{prefix}_enrichment_analysis.txt"
    if enrichment_file.exists():
        artifacts["enrichment_analysis"] = {
            "artifact": "enrichment_analysis",
            "path": str(enrichment_file.relative_to(workspace)),
            "full_path": str(enrichment_file.resolve()),
            "type": "text",
            "size": enrichment_file.stat().st_size
        }
        print(f"  📊 Added enrichment analysis to artifacts")
    
    # 共现分析结果
    cooccurrence_file = analysis_dir / f"{prefix}_cooccurrence_analysis.txt"
    if cooccurrence_file.exists():
        artifacts["cooccurrence_analysis"] = {
            "artifact": "cooccurrence_analysis",
            "path": str(cooccurrence_file.relative_to(workspace)),
            "full_path": str(cooccurrence_file.resolve()),
            "type": "text",
            "size": cooccurrence_file.stat().st_size
        }
        print(f"  📊 Added co-occurrence analysis to artifacts")
    
    # 复杂度分析结果
    complexity_file = analysis_dir / f"{prefix}_promoter_complexity.txt"
    if complexity_file.exists():
        artifacts["complexity_analysis"] = {
            "artifact": "complexity_analysis",
            "path": str(complexity_file.relative_to(workspace)),
            "full_path": str(complexity_file.resolve()),
            "type": "text",
            "size": complexity_file.stat().st_size
        }
        print(f"  📊 Added complexity analysis to artifacts")
    
    # 可视化文件 (移动到 final 目录后重新查找)
    figures = []
    if viz_success:
        for ext in ["pdf", "png", "csv", "tsv"]:
            for viz_file in final_dir.glob(f"{prefix}_promoter*.{ext}"):
                artifact_key = f"viz_{viz_file.stem}"
                artifacts[artifact_key] = {
                    "artifact": artifact_key,
                    "path": str(f"final/{viz_file.name}"),
                    "full_path": str(viz_file.resolve()),
                    "type": ext,
                    "size": viz_file.stat().st_size
                }
                print(f"  📊 Added to artifacts: {viz_file.name}")
        
        # 构建figures列表（用于前端显示）
        for ext in ["pdf", "png"]:
            for viz_file in sorted(final_dir.glob(f"{prefix}_promoter*.{ext}")):
                figures.append({
                    "name": viz_file.stem,
                    "path": str(f"final/{viz_file.name}"),
                    "full_path": str(viz_file.resolve()),
                    "type": ext,
                    "size": viz_file.stat().st_size
                })
    
    # 准备参数和指标
    parameters = {
        "upstream_length": args.upstream_length,
        "email": args.email,
        "timeout": args.timeout,
        "gff3_file": str(gff3_file.resolve()) if gff3_file else "",
        "genome_file": str(genome_file.resolve()) if genome_file else "",
        "gene_id_file": str(gene_id_file.resolve()) if gene_id_file else "",
        "plantcare_submission_mode": plantcare_submission_mode,
        "plantcare_batching_enabled": plantcare_batching_enabled,
        "plantcare_max_upload_bytes": plantcare_max_upload_bytes,
        "plantcare_max_records": plantcare_max_records,
        "plantcare_submit_timeout": plantcare_submit_timeout,
        "plantcare_batch_count": len(plantcare_batch_files) if plantcare_batching_enabled else (1 if plantcare_submission_mode == "single" else 0),
        "plantcare_batch_files": [str(p.resolve()) for p in plantcare_batch_files],
        "plantcare_upload_files": [str(p.resolve()) for p in plantcare_upload_files],
        "plantcare_safe_filename_policy": "short_alnum_pm_prefix_index",
        "plantcare_batch_result_files": [str(p.resolve()) for p in plantcare_batch_result_files],
        "plantcare_merged_result": str(plantcare_merged_result.resolve()) if plantcare_merged_result else "",
    }
    if scope_file:
        parameters.update({
            "scope_file": str(scope_file.resolve()),
            "scope_id": scope_data.get("scope_id", ""),
            "scope_mode": scope_data.get("scope_mode", ""),
            "target_species": scope_data.get("target_species", []),
            "reference_species": scope_data.get("reference_species", []),
            "comparative_species": scope_data.get("comparative_species", []),
            "selected_species": scope_data.get("selected_species", []),
            "family": scope_data.get("family", ""),
        })
    
    position_plot_png = final_dir / f"{prefix}_promoter_position_distribution.png"
    position_plot_pdf = final_dir / f"{prefix}_promoter_position_distribution.pdf"
    promoter_png_files = sorted(final_dir.glob(f"{prefix}_promoter*.png"))
    promoter_pdf_files = sorted(final_dir.glob(f"{prefix}_promoter*.pdf"))
    metrics = {
        "total_elements": parse_results["total_elements"],
        "unique_elements": parse_results["unique_elements"],
        "genes_with_elements": parse_results["genes_with_elements"],
        "filtered_elements": parse_results.get("filtered_elements", 0),
        "original_elements": parse_results["total_elements"] + parse_results.get("filtered_elements", 0),
        "enriched_pairs": enrichment_results.get("summary", {}).get("total_enriched", 0) if enrichment_results else 0,
        "cooccurrence_pairs": cooccurrence_results.get("significant_pairs", 0) if cooccurrence_results else 0,
        "mean_complexity": round(complexity_results.get("mean_complexity", 0), 2) if complexity_results else 0,
        "promoter_records_for_plot": parse_results.get("total_elements", 0),
        "matched_genes_for_plot": parse_results.get("genes_with_elements", 0),
        "position_distribution_success": position_plot_png.exists() and position_plot_pdf.exists(),
        "promoter_png_count": len(promoter_png_files),
        "promoter_pdf_count": len(promoter_pdf_files),
        "legacy_empty_plot_guard": True,
    }
    
    # 准备命令历史
    cmd_history = sys.argv
    
    # 获取工具版本
    tool_versions = get_tool_versions()
    
    # 生成 run_id
    run_id = str(uuid.uuid4())
    job_id = workspace.name
    
    # 写入 manifest
    manifest_path = final_dir / f"{prefix}.promoter.manifest.json"
    write_manifest(
        manifest_path=manifest_path,
        job_id=job_id,
        run_id=run_id,
        prefix=prefix,
        start_time=start_time,
        end_time=end_time,
        artifacts=artifacts,
        figures=figures,
        metrics=metrics,
        parameters=parameters,
        tool_versions=tool_versions,
        cmd_history=cmd_history,
        workspace=workspace,
        input_dir=input_dir,
        status="success"
    )
    if scope_data.get("scope_id"):
        scoped_manifest_path = final_dir / f"{prefix}.{sanitize_token(scope_data.get('scope_id', ''))}.promoter.manifest.json"
        scoped_artifacts, scoped_figures = materialize_scoped_outputs(
            artifacts=artifacts,
            figures=figures,
            workspace=workspace,
            prefix=prefix,
            scope_id=scope_data.get("scope_id", ""),
        )
        write_manifest(
            manifest_path=scoped_manifest_path,
            job_id=job_id,
            run_id=run_id,
            prefix=prefix,
            start_time=start_time,
            end_time=end_time,
            artifacts=scoped_artifacts,
            figures=scoped_figures,
            metrics=metrics,
            parameters=parameters,
            tool_versions=tool_versions,
            cmd_history=cmd_history,
            workspace=workspace,
            input_dir=input_dir,
            status="success"
        )
    
    # ========== Step 7: 生成报告 ==========
    print("\n[Step 7] Generating report...")
    
    report_file = final_dir / f"{prefix}.promoter_report.md"
    report_params = {
        "upstream_length": args.upstream_length,
        "email": args.email
    }
    
    generate_promoter_report(
        prefix=prefix,
        parse_results=parse_results,
        element_desc_file=element_desc_file,
        output_file=report_file,
        elapsed_time=elapsed,
        parameters=report_params,
        enrichment_results=enrichment_results,
        cooccurrence_results=cooccurrence_results,
        complexity_results=complexity_results
    )
    
    # 添加报告到artifacts
    if report_file.exists():
        artifacts["report"] = {
            "artifact": "report",
            "path": str(f"final/{report_file.name}"),
            "full_path": str(report_file.resolve()),
            "type": "markdown",
            "size": report_file.stat().st_size
        }
        print(f"  📄 Added report to artifacts")
    
    # 更新manifest（包含报告）- 重新生成完整的manifest
    manifest_path = final_dir / f"{prefix}.promoter.manifest.json"
    write_manifest(
        manifest_path=manifest_path,
        job_id=job_id,
        run_id=run_id,
        prefix=prefix,
        start_time=start_time,
        end_time=end_time,
        artifacts=artifacts,
        figures=figures,
        metrics=metrics,
        parameters=parameters,
        tool_versions=tool_versions,
        cmd_history=cmd_history,
        workspace=workspace,
        input_dir=input_dir,
        status="success"
    )
    if scope_data.get("scope_id"):
        scoped_manifest_path = final_dir / f"{prefix}.{sanitize_token(scope_data.get('scope_id', ''))}.promoter.manifest.json"
        scoped_artifacts, scoped_figures = materialize_scoped_outputs(
            artifacts=artifacts,
            figures=figures,
            workspace=workspace,
            prefix=prefix,
            scope_id=scope_data.get("scope_id", ""),
        )
        write_manifest(
            manifest_path=scoped_manifest_path,
            job_id=job_id,
            run_id=run_id,
            prefix=prefix,
            start_time=start_time,
            end_time=end_time,
            artifacts=scoped_artifacts,
            figures=scoped_figures,
            metrics=metrics,
            parameters=parameters,
            tool_versions=tool_versions,
            cmd_history=cmd_history,
            workspace=workspace,
            input_dir=input_dir,
            status="success"
        )
    
    print("\n" + "=" * 60)
    print("✅ Promoter analysis completed!")
    print(f"   Time: {elapsed:.1f}s")
    print(f"   Manifest: {manifest_path}")
    print(f"   Report: {report_file}")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {exc}")
        write_failure_promoter_manifest_from_argv(str(exc))
        raise
