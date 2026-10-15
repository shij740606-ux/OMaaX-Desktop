#!/usr/bin/env python3
"""
Genome Prepare: 多物种基因组数据预处理
支持批量处理多个物种的基因组数据

输入：input/job_id/ 下的多个物种原始基因组数据
  - {species}.cds.fasta.gz / {species}.pep.fasta.gz (序列文件)
  - {species}.gff3.gz (注释文件)
  - {species}.genome.fasta.gz (基因组文件，可选)

输出：
  - {species}.cds.fasta (标准化CDS序列)
  - {species}.pep.fasta (标准化蛋白序列)
  - {species}.genome.fasta (标准化基因组序列，如有)
  - {species}_final.gff3 (标准化GFF注释)
  - {prefix}.genome_prepare.manifest.json (机器清单，符合schema v1)
  - {prefix}.genome_prepare_report.md (分析报告)

多物种命名规则：
  - 文件名应包含物种标识，如：
    - Athaliana_167_TAIR10.protein_primaryTranscriptOnly.fa.gz (拟南芥)
    - FtChromosomeV2.fasta.gz (苦荞)
    - Osativa_323_v7.protein_primaryTranscriptOnly.fa.gz (水稻)

依赖工具：
  - python (converted GFF helper scripts)
  - gffread (从基因组+GFF提取蛋白序列)
  - pigz/gzip (解压.gz文件)
"""

import os
import argparse
import json
import hashlib
import logging
import re
import subprocess
import sys
import shutil
import gzip
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

# FIX: 添加 pandas 用于读取 xlsx
try:
    import pandas as pd
except ImportError:
    pd = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 添加conda环境bin目录到PATH
CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")


def _clean_text(value: Any) -> str:
    """Return a safe config string; pandas empty cells must not become literal 'nan'."""
    if value is None:
        return ""
    try:
        if pd is not None and pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "undefined", "-"} else text


def _clean_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _clean_text(value).lower() in {"true", "1", "yes", "y", "ref", "reference"}


def _is_generated_ref_combined_name(value: Any) -> bool:
    return Path(_clean_text(value)).name.startswith("Ref_combined.")


def _normalize_file_type(file_type: str, is_ref: bool, gene_family: str, file_scope: str = "") -> str:
    """Normalize config file type without conflating whole proteomes and family members."""
    ft = _clean_text(file_type).lower()
    scope = _clean_text(file_scope).lower()
    if ft == "protein":
        ft = "all_pep"
    if ft == "pep":
        if scope in {"known_family", "family", "seed"}:
            return "family_pep"
        if scope in {"whole_genome", "whole-genome", "genome", "background"}:
            return "all_pep"
        return "family_pep" if is_ref and gene_family else "all_pep"
    if ft == "cds" and scope in {"known_family", "family", "seed"}:
        return "family_cds"
    return ft


def _standardized_name(species_code: str, gene_family: str, file_type: str, original_name: str) -> str:
    """Build the standardized name used inside input/<job>/<species>/ before processing."""
    original_lower = original_name.lower()
    gz = ".gz" if original_lower.endswith(".gz") else ""

    if file_type == "gff":
        suffix = ".gff3" if ".gff3" in original_lower else ".gff"
        return f"{species_code}{suffix}{gz}"
    if file_type == "genome":
        return f"{species_code}.genome.fasta{gz}"
    if file_type in {"all_pep", "protein"}:
        return f"{species_code}.pep.fasta{gz}"
    if file_type == "cds":
        return f"{species_code}.cds.fasta{gz}"
    if file_type == "family_pep":
        if not gene_family:
            raise ValueError(f"File_Type={file_type} requires Gene_Family for species {species_code}")
        return f"{species_code}.{gene_family}.pep.fasta{gz}"
    if file_type == "family_cds":
        if not gene_family:
            raise ValueError(f"File_Type={file_type} requires Gene_Family for species {species_code}")
        return f"{species_code}.{gene_family}.cds.fasta{gz}"
    return f"{species_code}.{file_type}.fasta{gz}"


def _infer_gene_family_from_filename(original_name: str, species_code: str) -> str:
    """Infer family label from names like At.NAC.pep.fasta for known-family rows."""
    name = Path(_clean_text(original_name)).name
    species = _clean_text(species_code)
    if not name or not species:
        return ""
    match = re.match(rf"^{re.escape(species)}[._-]([A-Za-z0-9-]+)[._-]", name)
    if match:
        candidate = match.group(1)
        if candidate.lower() not in {"pep", "cds", "protein", "family", "genome", "gff", "gff3"}:
            return candidate
    return ""


def _config_species_role(row: Any, is_ref: bool) -> str:
    role = _clean_text(row.get('Species_Role', '')).lower()
    scope = _clean_text(row.get('File_Scope', '')).lower()
    if role in {"seed", "seed_reference", "known_family_reference"} or scope in {"known_family", "family", "seed"}:
        return "seed_reference"
    if is_ref or role in {"ref", "reference", "references", "comparison_reference"}:
        return "reference"
    if role in {"comparative", "comparison"}:
        return "comparative"
    if role == "outgroup":
        return "outgroup"
    return "target"


def _preflight_add_error(preflight: Dict[str, Any], species: str, row_number: int, original: str, reason: str) -> None:
    species = species or "<missing_species>"
    item = {
        "species": species,
        "row": row_number,
        "original": original,
        "reason": reason,
    }
    if item not in preflight["row_errors"]:
        preflight["row_errors"].append(item)
    errors = preflight["species_errors"].setdefault(species, [])
    if item not in errors:
        errors.append(item)


def preflight_config(input_dir: Path) -> Dict[str, Any]:
    """Validate config.xlsx before organizing files, without modifying user config."""
    result: Dict[str, Any] = {
        "enabled": False,
        "ok": True,
        "valid_species": [],
        "invalid_species": [],
        "row_errors": [],
        "species_errors": {},
        "planned_files": [],
        "gene_family": "",
    }
    config_path = input_dir / "config.xlsx"
    if not config_path.exists():
        return result
    result["enabled"] = True
    if pd is None:
        result["ok"] = False
        _preflight_add_error(result, "<config>", 0, "config.xlsx", "pandas is not available; cannot read config.xlsx")
        result["invalid_species"] = ["<config>"]
        return result
    try:
        df = pd.read_excel(config_path)
    except Exception as exc:
        result["ok"] = False
        _preflight_add_error(result, "<config>", 0, "config.xlsx", f"failed to read config.xlsx: {exc}")
        result["invalid_species"] = ["<config>"]
        return result
    if 'Original_File_Name' in df.columns:
        df = df[~df['Original_File_Name'].apply(_is_generated_ref_combined_name)].copy()
    required_cols = ['Original_File_Name', 'Species_Code', 'File_Type']
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        result["ok"] = False
        _preflight_add_error(result, "<config>", 0, "config.xlsx", f"missing required column(s): {', '.join(missing_cols)}")
        result["invalid_species"] = ["<config>"]
        return result
    if 'Gene_Family' not in df.columns:
        df['Gene_Family'] = ""
    if 'File_Scope' not in df.columns:
        df['File_Scope'] = ""
    has_ref_col = 'Ref' in df.columns
    if not has_ref_col:
        df['Ref'] = False

    species_summary: Dict[str, Dict[str, Any]] = {}
    planned_targets: Dict[Path, Dict[str, Any]] = {}
    for idx, row in df.iterrows():
        row_number = int(idx) + 2
        original_name = _clean_text(row.get('Original_File_Name', ''))
        species_code = _clean_text(row.get('Species_Code', ''))
        row_family = _clean_text(row.get('Gene_Family', ''))
        file_scope = _clean_text(row.get('File_Scope', ''))
        is_ref = _clean_bool(row.get('Ref', False)) if has_ref_col else False
        role = _config_species_role(row, is_ref)
        if not species_code:
            _preflight_add_error(result, species_code, row_number, original_name, "Species_Code is empty")
            continue
        if not original_name:
            _preflight_add_error(result, species_code, row_number, original_name, "Original_File_Name is empty")
            continue
        if not row_family and file_scope.lower() in {"known_family", "family", "seed"}:
            row_family = _infer_gene_family_from_filename(original_name, species_code)
        try:
            file_type = _normalize_file_type(row.get('File_Type', ''), is_ref, row_family, file_scope)
            source = input_dir / original_name
            if file_type == "gff" and source.exists() and source.stat().st_size == 0:
                _preflight_add_error(result, species_code, row_number, original_name, "GFF file exists but is empty")
                continue
            if file_type in {"pep", "all_pep", "cds"} and source.exists():
                genome_like, _ = _declared_sequence_looks_like_genome(source, file_type)
                if genome_like:
                    file_type = "genome"
            new_name = _standardized_name(species_code, row_family, file_type, original_name)
        except Exception as exc:
            _preflight_add_error(result, species_code, row_number, original_name, str(exc))
            continue
        target = input_dir / species_code / new_name
        target_key = target.resolve()
        planned = planned_targets.get(target_key)
        if planned and planned.get("original") != original_name:
            current_reason = (
                f"maps to {species_code}/{new_name}, but row {planned.get('row')} "
                f"({planned.get('original')}) maps to the same standardized file"
            )
            previous_reason = (
                f"maps to {species_code}/{new_name}, but row {row_number} "
                f"({original_name}) maps to the same standardized file"
            )
            _preflight_add_error(result, species_code, row_number, original_name, current_reason)
            _preflight_add_error(result, planned.get("species", species_code), planned.get("row", 0), planned.get("original", ""), previous_reason)
        else:
            planned_targets[target_key] = {"original": original_name, "species": species_code, "row": row_number}
        source_exists = source.exists()
        target_exists = target.exists()
        if not source_exists and not target_exists:
            _preflight_add_error(result, species_code, row_number, original_name, f"source file not found: {original_name}")
        summary = species_summary.setdefault(species_code, {
            "role": role,
            "has_gff": False,
            "has_sequence": False,
            "has_family_pep": False,
            "rows": [],
        })
        if summary["role"] not in {"target", "comparative", "outgroup"}:
            summary["role"] = role
        summary["rows"].append(row_number)
        if file_type == "gff" and (source_exists or target_exists):
            summary["has_gff"] = True
        if file_type in {"all_pep", "pep", "cds", "genome"} and (source_exists or target_exists):
            summary["has_sequence"] = True
        if file_type == "family_pep" and (source_exists or target_exists):
            summary["has_family_pep"] = True
        if not result["gene_family"] and row_family:
            result["gene_family"] = row_family
        result["planned_files"].append({
            "row": row_number,
            "species": species_code,
            "original": original_name,
            "new": new_name,
            "file_type": file_type,
            "role": role,
        })

    for species_code, summary in species_summary.items():
        first_row = summary["rows"][0] if summary["rows"] else 0
        role = summary.get("role", "target")
        if role == "seed_reference":
            if not summary.get("has_family_pep"):
                _preflight_add_error(result, species_code, first_row, "", "seed_reference species requires at least one known-family PEP file")
        else:
            if not summary.get("has_gff"):
                _preflight_add_error(result, species_code, first_row, "", f"{role} species requires a GFF file for genome_prepare")
            if not summary.get("has_sequence"):
                _preflight_add_error(result, species_code, first_row, "", f"{role} species requires at least one PEP/CDS/genome sequence file")
    invalid = sorted(sp for sp in result["species_errors"] if sp and sp != "<config>")
    valid = sorted(sp for sp in species_summary if sp not in set(invalid))
    if "<config>" in result["species_errors"]:
        invalid.append("<config>")
    result["invalid_species"] = invalid
    result["valid_species"] = valid
    result["ok"] = not result["row_errors"]
    return result


def _open_text_maybe_gzip(path: Path):
    if str(path).lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def _first_fasta_record_stats(path: Path, limit: int = 100000, sample_limit: int = 5000) -> Tuple[int, str, bool, str]:
    """Return first record length and a small sequence sample without scanning huge genomes."""
    if not path or not path.exists() or path.stat().st_size == 0:
        return 0, "", False, ""
    current_id = ""
    length = 0
    sample_parts: List[str] = []
    sample_len = 0
    saw_header = False
    try:
        with _open_text_maybe_gzip(path) as handle:
            for raw in handle:
                if raw.startswith(">"):
                    if saw_header:
                        return length, current_id, True, "".join(sample_parts)
                    saw_header = True
                    current_id = raw[1:].strip().split()[0]
                    length = 0
                    sample_parts = []
                    sample_len = 0
                    continue
                if not saw_header:
                    continue
                seq = raw.strip()
                length += len(seq)
                if sample_len < sample_limit:
                    take = seq[: sample_limit - sample_len]
                    sample_parts.append(take)
                    sample_len += len(take)
                if length > limit:
                    return length, current_id, True, "".join(sample_parts)
    except Exception:
        return 0, "", False, ""
    return length, current_id, saw_header, "".join(sample_parts)


def _first_fasta_record_length(path: Path, limit: int = 100000) -> Tuple[int, str, bool]:
    """Return first record length up to limit+1 without scanning huge genome files."""
    length, seq_id, ok, _ = _first_fasta_record_stats(path, limit=limit)
    return length, seq_id, ok


def _looks_like_genome_fasta(path: Path, max_protein_len: int = 100000) -> Tuple[bool, str]:
    length, seq_id, ok = _first_fasta_record_length(path, max_protein_len)
    if not ok:
        return False, "not a readable FASTA"
    if length > max_protein_len:
        return True, f"{seq_id or 'first_record'} length {length} > {max_protein_len}"
    lname = path.name.lower()
    if any(token in lname for token in ["genome", "chromosome", "genomic", "pmol.fasta"]):
        return True, "filename suggests genome FASTA"
    return False, "protein/CDS-like"


def _declared_sequence_looks_like_genome(path: Path, file_type: str, max_record_len: int = 100000) -> Tuple[bool, str]:
    """Validate explicit config File_Type without letting filename hints override it."""
    normalized = _clean_text(file_type).lower()
    length, seq_id, ok, sample = _first_fasta_record_stats(path, limit=max_record_len)
    if not ok:
        return False, "not a readable FASTA"
    if length > max_record_len:
        return True, f"{seq_id or 'first_record'} length {length} > {max_record_len}"
    if normalized in {"pep", "all_pep", "protein", "family_pep"}:
        letters = {ch for ch in sample.upper() if ch.isalpha()}
        if letters and letters.issubset(set("ACGTN")) and length >= 3000:
            return True, "protein-declared FASTA contains only DNA letters in a long first record"
    return False, "explicit config File_Type accepted"


@dataclass
class SpeciesFiles:
    """表示一个物种的文件集合"""
    species_code: str
    cds: Optional[Path] = None
    pep: Optional[Path] = None  # legacy/internal protein file
    all_pep: Optional[Path] = None
    family_pep: Optional[Path] = None
    gff: Optional[Path] = None
    genome: Optional[Path] = None
    db_type: str = "unknown"
    
    def is_valid(self) -> bool:
        """检查是否有足够的文件进行分析
        
        参考物种可只有 PEP；目标物种可用 PEP+GFF，或 genome+GFF 现场提取 CDS/PEP。
        """
        if self.pep is not None:
            return True
        return self.gff is not None and self.genome is not None
    
    def __str__(self) -> str:
        files = []
        if self.cds: files.append("CDS")
        if self.pep: files.append("PEP")
        if self.all_pep: files.append("ALL_PEP")
        if self.family_pep: files.append("FAMILY_PEP")
        if self.gff: files.append("GFF")
        if self.genome: files.append("GENOME")
        return f"{self.species_code}({','.join(files)})"


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
    """获取工具版本信息"""
    versions = {}
    versions["python"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    
    
    try:
        subprocess.run(['seqtk'], capture_output=True, check=False)
        versions["seqtk"] = "available"
    except:
        versions["seqtk"] = "not_found"
    
    try:
        result = subprocess.run(['gffread', '--version'], capture_output=True, text=True, check=False)
        versions["gffread"] = (result.stdout.strip() or result.stderr.strip() or "unknown").split('\n')[0]
    except:
        versions["gffread"] = "not_found"
    
    return versions


class MultiSpeciesDetector:
    """多物种文件检测器"""
    
    # 物种代码映射
    SPECIES_PATTERNS = {
        # 模式: (物种代码, 物种名称)
        r'^[Aa]th.*TAIR': ('Ath', 'Arabidopsis thaliana'),
        r'^[Ff]t.*Chromosome': ('Ft', 'Fagopyrum tataricum'),
        r'^[Oo]s.*': ('Os', 'Oryza sativa'),
        r'^[Zz]m.*': ('Zm', 'Zea mays'),
        r'^[Ss]l.*': ('Sl', 'Solanum lycopersicum'),
        r'^[Gg]m.*': ('Gm', 'Glycine max'),
        r'^[Bb]d.*': ('Bd', 'Brachypodium distachyon'),
        r'^[Hh]v.*': ('Hv', 'Hordeum vulgare'),
        r'^[Ss]b.*': ('Sb', 'Sorghum bicolor'),
        r'^[Pp]t.*': ('Pt', 'Populus trichocarpa'),
        r'^[Vv].*': ('Vv', 'Vitis vinifera'),
        r'^[Cc]r.*': ('Cr', 'Chlamydomonas reinhardtii'),
        # 新增物种
        r'^[Ff]vesca.*|^[Ff]v.*': ('Fv', 'Fragaria vesca (Strawberry)'),
        r'^[Mm]alus.*|^[Mm]d.*': ('Md', 'Malus domestica (Apple)'),
        r'^[Pp]runus.*|^[Pp]p.*': ('Pp', 'Prunus persica (Peach)'),
        r'^[Pp]yrus.*|^[Pp]b.*': ('Pb', 'Pyrus communis (Pear)'),
        r'^[Rr]ubus.*|^[Rr]o.*': ('Ro', 'Rubus occidentalis (Raspberry)'),
        r'^[Oo]BDH.*': ('OBDH', 'OBDH (Unknown species)'),
    }
    
    @classmethod
    def detect_species(cls, filename: str) -> Tuple[str, str]:
        """从文件名检测物种代码"""
        for pattern, (code, name) in cls.SPECIES_PATTERNS.items():
            if re.match(pattern, filename):
                return code, name
        
        # 默认：提取前两个大写字母作为物种代码
        match = re.match(r'^([A-Z][a-z])', filename)
        if match:
            return match.group(1), f"Unknown ({filename[:10]}...)"
        
        return "UNK", "Unknown"
    
    @classmethod
    def detect_db_type(cls, filename: str) -> str:
        """检测数据库类型"""
        name_lower = filename.lower()
        
        # JGI特征
        if 'primarytranscriptonly' in name_lower or '_tair' in name_lower:
            return "jgi"
        
        # Ensembl特征
        if '.cds.all.fa' in name_lower or '.pep.all.fa' in name_lower:
            return "ensembl"
        
        # NCBI特征
        if 'gcf_' in name_lower or '_cds_from_genomic' in name_lower:
            return "ncbi"
        
        return "unknown"
    
    @classmethod
    def scan_input_dir(cls, input_dir: Path) -> Dict[str, SpeciesFiles]:
        """扫描输入目录，识别所有物种。

        有 config.xlsx 时必须以 Species_Code 为准；文件名识别只作为无 config 的兜底。
        """
        species_map: Dict[str, SpeciesFiles] = {}
        config_species_codes = set()
        config_original_names = set()
        config_strict = False
        config_path = input_dir / "config.xlsx"
        if config_path.exists() and pd is not None:
            try:
                df = pd.read_excel(config_path)
                config_species_codes = {str(x).strip() for x in df.get('Species_Code', []) if str(x).strip() and str(x).strip().lower() != 'nan' and str(x).strip() != '-'}
                config_original_names = {Path(str(x).strip()).name for x in df.get('Original_File_Name', []) if str(x).strip() and str(x).strip().lower() != 'nan'}
                config_strict = bool(config_species_codes)
            except Exception:
                config_species_codes = set()
                config_strict = False
        
        # FIX: 递归扫描所有文件（包括子目录）
        def prefer_candidate(current: Optional[Path], candidate: Path) -> bool:
            if current is None:
                return True
            if current.stat().st_size == 0 and candidate.stat().st_size > 0:
                return True
            if current.parent == input_dir and candidate.parent != input_dir:
                return True
            return False

        for file in input_dir.rglob("*"):
            if not file.is_file():
                continue
            
            name = file.name
            name_lower = name.lower()
            
            # Skip generated files, merged reference files, and config.xlsx.
            if '_final.gff3' in name or name.startswith('Ref_combined.') or name == 'config.xlsx':
                continue
            first_token = name.split('.', 1)[0]
            if file.parent == input_dir and first_token in config_species_codes and (input_dir / first_token).exists():
                continue
            # After config organization, keep standardized files and ignore leftover original root files.
            if file.parent == input_dir and name in config_original_names and any((input_dir / sp).exists() for sp in config_species_codes):
                continue
            
            # FIX: 从父文件夹名获取 Species_Code（如果不在根目录）
            if file.parent != input_dir:
                # 子目录中的文件，使用文件夹名作为 Species_Code。
                parent_name = file.parent.name
                if parent_name in ['genome_prepare', 'final', 'logs', 'analysis']:
                    continue
                if config_strict and parent_name not in config_species_codes:
                    continue
                species_code = parent_name
            else:
                if config_strict:
                    # config 存在时根目录只接受已经按 Species_Code 标准化的文件；
                    # 原始文件绝不能再按文件名前缀猜成 He/Ar/UNK/At。
                    if first_token in config_species_codes:
                        species_code = first_token
                    else:
                        continue
                elif '.' in name and re.match(r'^[A-Za-z][A-Za-z0-9_-]{0,9}$', first_token):
                    species_code = first_token
                else:
                    species_code, _ = cls.detect_species(name)
            
            # Classify file before creating a species entry; ignore logs, HMMs, reports, etc.
            file_kind = None
            if ('cds' in name_lower or 'cdna' in name_lower or 'rna' in name_lower) and ('.fa' in name_lower or '.fasta' in name_lower or '.fna' in name_lower):
                file_kind = 'cds'
            elif (
                ('family_pep' in name_lower or 'familypep' in name_lower)
                or _extract_gene_family_from_filename(name)
            ) and ('pep' in name_lower) and ('.fa' in name_lower or '.fasta' in name_lower):
                file_kind = 'family_pep'
            elif ('all_pep' in name_lower or 'allpep' in name_lower or 'protein' in name_lower or name_lower.endswith('.faa') or name_lower.endswith('.faa.gz')) and ('.fa' in name_lower or '.fasta' in name_lower):
                file_kind = 'all_pep'
            elif ('pep' in name_lower) and ('.fa' in name_lower or '.fasta' in name_lower):
                file_kind = 'pep'
            elif name_lower.endswith('.gff3') or name_lower.endswith('.gff') or '.gff3.' in name_lower or '.gff.' in name_lower:
                file_kind = 'gff'
            elif ('genome' in name_lower or 'chromosome' in name_lower or 'genomic' in name_lower) and ('.fa' in name_lower or '.fasta' in name_lower or '.fna' in name_lower):
                file_kind = 'genome'

            if file_kind is None:
                continue

            if species_code not in species_map:
                species_map[species_code] = SpeciesFiles(
                    species_code=species_code,
                    db_type="unknown"
                )

            if file_kind == 'cds':
                if prefer_candidate(species_map[species_code].cds, file):
                    species_map[species_code].cds = file
            elif file_kind == 'pep':
                if prefer_candidate(species_map[species_code].pep, file):
                    species_map[species_code].pep = file
            elif file_kind == 'all_pep':
                if prefer_candidate(species_map[species_code].all_pep, file):
                    species_map[species_code].all_pep = file
                if species_map[species_code].pep is None or prefer_candidate(species_map[species_code].pep, file):
                    species_map[species_code].pep = file
            elif file_kind == 'family_pep':
                if prefer_candidate(species_map[species_code].family_pep, file):
                    species_map[species_code].family_pep = file
                if species_map[species_code].pep is None or prefer_candidate(species_map[species_code].pep, file):
                    species_map[species_code].pep = file
            elif file_kind == 'gff':
                current = species_map[species_code].gff
                if current is None or current.stat().st_size == 0 or file.stat().st_size > current.stat().st_size:
                    species_map[species_code].gff = file
            elif file_kind == 'genome':
                species_map[species_code].genome = file
        
        return species_map


def organize_files_by_config(input_dir: Path, preflight: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    根据 config.xlsx 组织文件到对应 Species_Code 文件夹
    
    FIX: 新增函数，实现文件分类移动和重命名
    FIX: 新增 Ref 列支持，区分目标物种和参考物种
    
    Args:
        input_dir: 输入目录路径（input/<job_id>/）
    
    Returns:
        包含组织结果的字典
    """
    config_path = input_dir / "config.xlsx"
    
    if not config_path.exists():
        logger.warning(f"config.xlsx not found in {input_dir}, skipping organization")
        return {"organized": False, "reason": "config.xlsx not found"}
    
    if pd is None:
        logger.error("pandas not available, cannot read config.xlsx")
        return {"organized": False, "reason": "pandas not available"}
    
    try:
        # 读取 config.xlsx
        df = pd.read_excel(config_path)
        if 'Original_File_Name' in df.columns:
            before_rows = len(df)
            df = df[~df['Original_File_Name'].apply(_is_generated_ref_combined_name)].copy()
            skipped_rows = before_rows - len(df)
            if skipped_rows:
                logger.info(f"Skipped {skipped_rows} generated Ref_combined config row(s)")
        logger.info(f"Read config.xlsx with {len(df)} entries")
        
        # 检查必要列。Gene_Family 是可选分析标签，不是数据准备必填项。
        required_cols = ['Original_File_Name', 'Species_Code', 'File_Type']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            raise ValueError(f"Missing columns in config.xlsx: {missing_cols}")
        if 'Gene_Family' not in df.columns:
            df['Gene_Family'] = ""
        if 'File_Scope' not in df.columns:
            df['File_Scope'] = ""
        
        # FIX: 检查 Ref 列，如果没有则默认 FALSE
        has_ref_col = 'Ref' in df.columns
        if not has_ref_col:
            logger.warning("Ref column not found, defaulting to FALSE for all entries")
            df['Ref'] = False
        
        invalid_species = set((preflight or {}).get("invalid_species", []))
        valid_species_filter = set((preflight or {}).get("valid_species", []))
        species_codes = df['Species_Code'].unique()
        for code in species_codes:
            clean_code = _clean_text(code)
            if not clean_code or clean_code in invalid_species:
                continue
            if valid_species_filter and clean_code not in valid_species_filter:
                continue
            species_dir = input_dir / clean_code
            species_dir.mkdir(exist_ok=True)
            logger.info(f"Created directory: {species_dir}")
        
        # 移动并重命名文件
        moved_files = []
        missing_files = []
        planned_targets: Dict[Path, Dict[str, str]] = {}
        gene_family = _clean_text((preflight or {}).get("gene_family", ""))
        
        for _, row in df.iterrows():
            original_name = _clean_text(row['Original_File_Name'])
            species_code = _clean_text(row['Species_Code'])
            row_family = _clean_text(row.get('Gene_Family', ''))
            is_ref = _clean_bool(row['Ref']) if has_ref_col else False
            file_scope = _clean_text(row.get('File_Scope', ''))
            if not row_family and file_scope.lower() in {"known_family", "family", "seed"}:
                row_family = _infer_gene_family_from_filename(original_name, species_code)
                if row_family:
                    logger.info(f"Inferred Gene_Family={row_family} from known-family file {original_name}")
            file_type = _normalize_file_type(row['File_Type'], is_ref, row_family, row.get('File_Scope', ''))
            
            # 跳过无效或预检失败的 Species_Code
            if not species_code:
                logger.info(f"Skipping file with no species code: {original_name}")
                continue
            if species_code in invalid_species:
                logger.info(f"Skipping preflight-invalid species {species_code}: {original_name}")
                continue
            if valid_species_filter and species_code not in valid_species_filter:
                logger.info(f"Skipping species outside preflight-valid set {species_code}: {original_name}")
                continue
            
            # FIX: 记录 gene_family（从第一个有效行获取）
            if not gene_family and row_family:
                gene_family = row_family

            source = input_dir / original_name
            if file_type == "gff" and source.exists() and source.stat().st_size == 0:
                logger.warning(f"Skipping empty GFF listed in config.xlsx: {original_name}")
                continue
            if file_type in {"pep", "all_pep", "cds"} and source.exists():
                genome_like, genome_reason = _declared_sequence_looks_like_genome(source, file_type)
                if genome_like:
                    logger.warning(
                        f"{original_name} is declared as {file_type} but has strong genome FASTA evidence "
                        f"({genome_reason}); standardizing it as genome instead of protein/CDS."
                    )
                    file_type = "genome"
            
            # 生成新文件名，保留 .gz 后缀。全物种资源只按 Species 命名；
            # 只有 known-family seed 序列才按 Species.Family 命名。
            new_name = _standardized_name(species_code, row_family, file_type, original_name)
            
            target = input_dir / species_code / new_name
            target_key = target.resolve()
            planned = planned_targets.get(target_key)
            if planned and planned.get('original') != original_name:
                reason = (
                    "config.xlsx species/file conflict after preflight filtering: "
                    f"{planned.get('original')} and {original_name} both map to {species_code}/{new_name}."
                )
                missing_files.append(original_name)
                logger.error(reason)
                continue
            planned_targets[target_key] = {
                'original': original_name,
                'species': species_code,
                'new': new_name,
                'file_type': file_type,
            }
            
            file_record = {
                'original': original_name,
                'new': new_name,
                'species': species_code,
                'gene_family': row_family,
                'file_type': file_type,
                'is_ref': is_ref
            }
            if source.exists():
                # Keep uploaded originals intact; data preparation must be reproducible and non-destructive.
                if target.exists():
                    source_hash = compute_file_hash(source)
                    target_hash = compute_file_hash(target)
                    if source_hash and target_hash and source_hash != target_hash:
                        shutil.copy2(str(source), str(target))
                        logger.info(f"Updated standardized file from current config source: {target}")
                    else:
                        logger.info(f"Target exists, keeping existing standardized file: {target}")
                else:
                    shutil.copy2(str(source), str(target))
                moved_files.append(file_record)
                logger.info(f"Copied & Renamed: {original_name} -> {species_code}/{new_name}")
            elif target.exists():
                # ????????????????????????????????????
                moved_files.append(file_record)
                logger.info(f"Standardized file already exists: {species_code}/{new_name}")
            else:
                missing_files.append(original_name)
                logger.warning(f"Source file not found: {original_name}")
        
        # FIX: 分离目标物种和参考物种
        target_species = [f['species'] for f in moved_files if not f.get('is_ref', False)]
        ref_species = [f['species'] for f in moved_files if f.get('is_ref', False)]
        
        return {
            "organized": True,
            "species_codes": list(species_codes),
            "target_species": list(set(target_species)),
            "ref_species": list(set(ref_species)),
            "moved_files": moved_files,
            "missing_files": missing_files,
            "gene_family": gene_family,
            "preflight": preflight or {}
        }
        
    except Exception as e:
        logger.error(f"Failed to organize files: {e}")
        return {"organized": False, "reason": str(e)}


def merge_species_files(input_dir: Path, genome_prepare_dir: Path, org_result: Dict[str, Any]) -> Dict[str, Any]:
    """Merge reference family peptide files; keep whole proteomes per species."""
    gene_family = _clean_text(org_result.get("gene_family", ""))
    target_species = org_result.get("target_species", [])
    ref_species = org_result.get("ref_species", [])
    invalid_species = set((org_result.get("preflight") or {}).get("invalid_species", []))
    config_path = input_dir / "config.xlsx"
    known_family_by_species: Dict[str, Dict[str, Path]] = {}
    if config_path.exists() and pd is not None:
        try:
            df = pd.read_excel(config_path)
            if 'Original_File_Name' in df.columns:
                df = df[~df['Original_File_Name'].apply(_is_generated_ref_combined_name)].copy()
            if 'Ref' in df.columns or 'Species_Role' in df.columns:
                target_species, ref_species = [], []
                for spc in df.get('Species_Code', []).unique():
                    if pd.isna(spc) or spc == '-':
                        continue
                    spc = _clean_text(spc)
                    if not spc or spc in invalid_species:
                        continue
                    species_records = df[df['Species_Code'] == spc]
                    is_reference_species = False
                    if 'Ref' in species_records:
                        is_reference_species = any(_clean_bool(v) for v in species_records['Ref'])
                    if not is_reference_species and 'Species_Role' in species_records:
                        roles = {_clean_text(v).lower() for v in species_records['Species_Role']}
                        is_reference_species = bool(roles & {"reference", "ref", "references", "seed_reference", "seed", "known_reference"})
                    (ref_species if is_reference_species else target_species).append(spc)
                logger.info(f"Re-extracted from config.xlsx for merge: target={target_species}, ref={ref_species}")

                if gene_family:
                    for _, row in df.iterrows():
                        spc = _clean_text(row.get('Species_Code', ''))
                        row_family = _clean_text(row.get('Gene_Family', ''))
                        file_scope = _clean_text(row.get('File_Scope', '')).lower()
                        file_type = _clean_text(row.get('File_Type', '')).lower()
                        if not spc or spc in invalid_species or row_family != gene_family or file_scope not in {"known_family", "family", "seed"}:
                            continue
                        if file_type not in {"pep", "family_pep", "protein"}:
                            continue
                        original_name = _clean_text(row.get('Original_File_Name', ''))
                        normalized_type = _normalize_file_type(file_type, True, row_family, file_scope)
                        if normalized_type != "family_pep":
                            continue
                        candidates = [
                            input_dir / spc / _standardized_name(spc, row_family, normalized_type, original_name),
                            input_dir / original_name,
                        ]
                        found = next((c for c in candidates if c.exists() and c.stat().st_size > 0), None)
                        if found:
                            known_family_by_species.setdefault(spc, {})["family_pep"] = found
        except Exception as e:
            logger.warning(f"Failed to re-extract species from config.xlsx: {e}")
    merged_files: Dict[str, str] = {}
    if ref_species and gene_family:
        for file_type in ['cds', 'family_pep']:
            output_file_type = 'pep' if file_type == 'family_pep' else file_type
            output_name = f"Ref_combined.{gene_family}.{output_file_type}.fasta"
            output_path = input_dir / output_name
            files_to_merge = []
            for spc in ref_species:
                if file_type == 'family_pep':
                    known_family_file = known_family_by_species.get(spc, {}).get("family_pep")
                    if known_family_file:
                        files_to_merge.append(known_family_file)
                        logger.info(f"Found reference {file_type} from config known_family: {known_family_file}")
                    else:
                        logger.warning(
                            f"No config-declared known_family peptide found for reference species {spc} "
                            f"and family {gene_family}; skipping Ref_combined peptide merge for this species."
                        )
                    continue
                candidates = [
                    input_dir / spc / f"{spc}.{gene_family}.{file_type}.fasta",
                    input_dir / f"{spc}.{gene_family}.{file_type}.fasta",
                ]
                if file_type == 'cds':
                    candidates.extend([
                        input_dir / spc / f"{spc}.{gene_family}.cds.fasta",
                        input_dir / f"{spc}.{gene_family}.cds.fasta",
                    ])
                if file_type == 'family_pep':
                    candidates.extend([
                        input_dir / spc / f"{spc}.{gene_family}.pep.fasta",
                        input_dir / f"{spc}.{gene_family}.pep.fasta",
                    ])
                found = next((c for c in candidates if c.exists()), None)
                if found:
                    files_to_merge.append(found)
                    logger.info(f"Found reference {file_type}: {found}")
            if files_to_merge:
                with open(output_path, 'w') as outfile:
                    for file_path in files_to_merge:
                        with open(file_path, 'r') as infile:
                            content = infile.read()
                            outfile.write(content)
                            if not content.endswith('\n'):
                                outfile.write('\n')
                key = f"ref_{output_file_type}" if file_type != 'family_pep' else 'ref_family_pep'
                merged_files[key] = str(output_path)
                if file_type == 'family_pep':
                    merged_files['ref_pep'] = str(output_path)
                logger.info(f"Merged reference {file_type}: {output_name} ({len(files_to_merge)} files)")
    return {
        "merged": True,
        "target_species": target_species,
        "ref_species": ref_species,
        "merged_files": merged_files,
        "gene_family": gene_family,
    }

def generate_species_config(input_dir: Path, genome_prepare_dir: Path, org_result: Dict[str, Any]) -> Path:
    """
    生成 species_config.json 配置文件
    
    FIX: 新增函数，用于 R 脚本读取物种信息
    FIX: 直接从 config.xlsx 读取参考物种信息
    
    Args:
        input_dir: 输入目录路径
        genome_prepare_dir: genome_prepare 目录路径
        org_result: organize_files_by_config 的返回结果
    
    Returns:
        生成的配置文件路径
    """
    gene_family = _clean_text(org_result.get("gene_family", ""))
    target_species = org_result.get("target_species", [])
    ref_species = org_result.get("ref_species", [])
    invalid_species = set((org_result.get("preflight") or {}).get("invalid_species", []))
    
    # FIX: 直接从 config.xlsx 读取参考物种信息（因为 organize_files_by_config 可能遗漏）
    config_path = input_dir / "config.xlsx"
    if config_path.exists() and pd is not None:
        try:
            df = pd.read_excel(config_path)
            if 'Original_File_Name' in df.columns:
                df = df[~df['Original_File_Name'].apply(_is_generated_ref_combined_name)].copy()
            if 'Ref' in df.columns or 'Species_Role' in df.columns:
                # 重新提取目标物种和参考物种
                all_species = df['Species_Code'].unique()
                target_species = []
                ref_species = []
                for spc in all_species:
                    if pd.isna(spc) or spc == '-':
                        continue
                    spc = _clean_text(spc)
                    if not spc or spc in invalid_species:
                        continue
                    species_records = df[df['Species_Code'] == spc]
                    is_reference_species = False
                    if 'Ref' in species_records:
                        is_reference_species = any(_clean_bool(v) for v in species_records['Ref'])
                    if not is_reference_species and 'Species_Role' in species_records:
                        roles = {_clean_text(v).lower() for v in species_records['Species_Role']}
                        is_reference_species = bool(roles & {"reference", "ref", "references", "seed_reference", "seed", "known_reference"})
                    if is_reference_species:
                        ref_species.append(spc)
                    else:
                        target_species.append(spc)
                logger.info(f"Re-extracted from config.xlsx: target={target_species}, ref={ref_species}")
        except Exception as e:
            logger.warning(f"Failed to re-extract species from config.xlsx: {e}")
    
    # 预定义颜色
    colors = ["#E41A1C", "#377EB8", "#4DAF4A", "#984EA3", "#FF7F00", "#FFFF33", "#A65628", "#F781BF"]
    
    # 构建物种映射
    species_mapping = {}
    all_species = target_species + ref_species
    
    for i, spc in enumerate(all_species):
        color = colors[i % len(colors)]
        species_mapping[spc] = {
            "type": "target" if spc in target_species else "reference",
            "color": color,
            "pattern": f"^{spc}"
        }
    
    config = {
        "gene_family": gene_family,
        "prefix": None,
        "target": {
            "prefix": None,
            "species": target_species,
            "files": {
                "cds": None,  # 修订 2026-04-26: 不再合并目标物种
                "pep": None,
                "all_pep": None,
                "family_pep": None,
                "gff": None
            }
        },
        "reference": {
            "prefix": f"Ref_combined.{gene_family}" if gene_family else None,
            "species": ref_species,
            "files": {
                "cds": f"Ref_combined.{gene_family}.cds.fasta" if gene_family else None,
                "pep": f"Ref_combined.{gene_family}.pep.fasta" if gene_family else None,
                "family_pep": f"Ref_combined.{gene_family}.pep.fasta" if gene_family else None,
                "all_pep": None
            }
        },
        "species_mapping": species_mapping
    }
    
    config_path = genome_prepare_dir / "species_config.json"
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    
    logger.info(f"Generated species_config.json: {config_path}")
    return config_path


def _extract_gene_family_from_filename(filename: str) -> str:
    """从文件名提取 Gene_Family
    
    假设文件名格式: {Species_Code}.{Gene_Family}.{File_Type}.fasta
    例如: Lc.SUTs.pep.fasta -> SUTs
    """
    parts = filename.split('.')
    if len(parts) >= 4 and parts[1].lower() not in {"pep", "cds", "genome", "gff", "gff3", "all_pep", "protein"}:
        return parts[1]  # Gene_Family 是第二部分
    return ""


def validate_input_directory(input_dir: Path) -> Dict[str, Any]:
    """
    验证输入目录并获取必要信息
    
    Args:
        input_dir: 输入目录路径（input/<job_id>/）
    
    Returns:
        包含输入文件信息的字典
    
    Raises:
        FileNotFoundError: 输入目录不存在
        ValueError: 输入目录无效
    """
    print(f"正在验证输入目录: {input_dir}")
    
    # 1. 检查输入目录是否存在
    if not input_dir.exists():
        raise FileNotFoundError(f"输入目录不存在: {input_dir}")
    
    if not input_dir.is_dir():
        raise ValueError(f"输入路径不是目录: {input_dir}")
    
    print(f"  输入目录存在: {input_dir}")
    
    # 2. 扫描物种文件
    species_map = MultiSpeciesDetector.scan_input_dir(input_dir)
    
    if not species_map:
        raise ValueError(f"在输入目录中未找到有效的物种文件: {input_dir}")
    
    print(f"  检测到 {len(species_map)} 个物种")
    for code, species in species_map.items():
        print(f"    - {species}")
    
    # 3. 验证每个物种的文件完整性
    valid_species = {}
    invalid_species = []
    
    for code, species in species_map.items():
        if species.is_valid():
            valid_species[code] = species
        else:
            invalid_species.append(code)
            print(f"  ⚠️  物种 {code} 文件不完整: {species}（需要 PEP，或 genome+GFF 用于提取）")
    
    if not valid_species:
        raise ValueError("没有有效的物种文件（需要 PEP，或 genome+GFF 用于提取）")
    
    print(f"  有效物种: {len(valid_species)}/{len(species_map)}")
    
    return {
        "species_map": valid_species,
        "invalid_species": invalid_species,
        "total_files": len(list(input_dir.iterdir())),
        "input_dir": input_dir
    }


def prepare_directories(workspace: Path) -> Dict[str, Path]:
    """创建Genome Prepare阶段的标准目录结构"""
    paths = {
        "work_dir": workspace / "genome_prepare",
        "final_dir": workspace,
        "logs_dir": workspace / "logs",
    }
    
    print("创建目录结构:")
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        print(f"  {name}: {path}")
    
    return paths


class MultiSpeciesGenomePreparer:
    """??????????"""

    def __init__(self, input_dir: Path, output_dir: Path, scripts_dir: Path, paths: Dict[str, Path]):
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.scripts_dir = scripts_dir.expanduser().resolve()
        self.paths = paths
        self.work_dir = paths["work_dir"]

        self.cmd_history = []
        self.results = []

    def _extract_gene_family(self, species: SpeciesFiles) -> str:
        """???????? Gene_Family?"""
        for file_attr in [species.all_pep, species.family_pep, species.pep, species.cds, species.gff, species.genome]:
            if file_attr:
                return _extract_gene_family_from_filename(file_attr.name)
        return "unknown"

    def _is_reference_file(self, species: SpeciesFiles) -> bool:
        """???????????? config.xlsx ?? Ref ???"""
        config_path = self.input_dir / "config.xlsx"

        if not config_path.exists() or pd is None:
            for file_attr in [species.pep, species.cds]:
                if file_attr and 'ref' in file_attr.name.lower():
                    return True
            return False

        try:
            df = pd.read_excel(config_path)
            if 'Ref' not in df.columns:
                return False

            species_records = df[df['Species_Code'] == species.species_code]
            if not species_records.empty:
                return any(_clean_bool(v) for v in species_records['Ref'])

            return False
        except Exception as e:
            logger.warning(f"Failed to read config.xlsx for ref check: {e}")
            return False

    def run_command(self, cmd: List[str], description: str = "") -> Tuple[int, str, str]:
        """????????"""
        cmd_str = ' '.join(str(c) for c in cmd)
        self.cmd_history.append(cmd_str)
        logger.info(f"Running: {description or cmd_str}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=self.work_dir, check=False)
            return result.returncode, result.stdout, result.stderr
        except Exception as e:
            logger.error(f"Command failed: {e}")
            return -1, "", str(e)

    def run_command_to_file(self, cmd: List[str], output_file: Path, description: str = "") -> Tuple[int, str, str]:
        """?????? stdout ?????????? shell ???"""
        cmd_str = ' '.join(str(c) for c in cmd) + f" > {output_file}"
        self.cmd_history.append(cmd_str)
        logger.info(f"Running: {description or cmd_str}")

        try:
            with open(output_file, 'wb') as out:
                result = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE, cwd=self.work_dir, check=False)
            stderr = result.stderr.decode(errors='replace') if isinstance(result.stderr, bytes) else (result.stderr or "")
            return result.returncode, "", stderr
        except Exception as e:
            logger.error(f"Command failed: {e}")
            return -1, "", str(e)

    def decompress_if_needed(self, filepath: Path) -> Path:
        """????? .gz ???????????????"""
        if filepath.suffix == '.gz':
            decompressed_path = filepath.parent / filepath.stem
            logger.info(f"Decompressing {filepath.name}...")

            decompressed = False
            try:
                result = subprocess.run(['pigz', '-d', '-c', str(filepath)], capture_output=True, text=False, check=False)
                if result.returncode == 0:
                    with open(decompressed_path, 'wb') as f:
                        f.write(result.stdout)
                    logger.info(f"Decompressed to {decompressed_path.name}")
                    decompressed = True
            except FileNotFoundError:
                pass

            if not decompressed:
                result = subprocess.run(['gzip', '-d', '-c', str(filepath)], capture_output=True, text=False, check=False)
                if result.returncode == 0:
                    with open(decompressed_path, 'wb') as f:
                        f.write(result.stdout)
                    logger.info(f"Decompressed to {decompressed_path.name}")
                    decompressed = True

            if decompressed:
                logger.info(f"Kept compressed source file: {filepath.name}")
                return decompressed_path

        return filepath

    def detect_db_type_from_gff(self, gff_file: Optional[Path]) -> str:
        """??? GFF ??????????"""
        if not gff_file or not gff_file.exists():
            return "unknown_user"

        ensembl_score = 0
        jgi_score = 0
        ncbi_score = 0
        checked = 0

        with open(gff_file, 'r', errors='replace') as handle:
            for line in handle:
                if line.startswith('#') or not line.strip():
                    continue
                fields = line.rstrip('\n').split('\t')
                if len(fields) < 9:
                    continue
                attrs = fields[8]
                checked += 1

                if 'ID=transcript:' in attrs or 'Parent=gene:' in attrs or 'ID=CDS:' in attrs:
                    ensembl_score += 1
                if '.TAIR' in attrs or 'longest=1' in attrs or 'pacid=' in attrs:
                    jgi_score += 1
                if 'ID=rna-' in attrs or 'ID=cds-' in attrs or 'gbkey=' in attrs or 'Genbank:' in attrs:
                    ncbi_score += 1

                if checked >= 500:
                    break

        scores = {"ensembl": ensembl_score, "jgi": jgi_score, "ncbi": ncbi_score}
        db_type, score = max(scores.items(), key=lambda item: item[1])
        return db_type if score > 0 else "unknown_user"

    def _ensure_script(self, name: str) -> Path:
        script = self.scripts_dir / name
        if not script.exists():
            raise FileNotFoundError(f"Required script not found: {script}")
        return script

    def _ensure_tool(self, name: str) -> None:
        if shutil.which(name) is None:
            raise RuntimeError(f"Required tool not found in PATH: {name}")

    def _copy_if_needed(self, src: Optional[Path], dst: Path) -> None:
        if not src:
            return
        if src.resolve() == dst.resolve():
            return
        shutil.copy2(src, dst)

    def _canonical_prepare_id(self, value: str) -> str:
        """Return the ID written to prepared FASTA/GFF outputs.

        Genome providers sometimes nest typed prefixes, e.g.
        transcript:mRNA:HanXR... . Provider feature-type labels are not stable
        biological identifiers, and raw colons later break Newick-safe stage
        matching. Keep the accession-like token but remove typed prefixes.
        """
        text = (value or "").strip()
        if not text:
            return ""
        for _ in range(8):
            updated = re.sub(r'^(gene|transcript|CDS|cds|mRNA|mrna)[:_]', '', text, flags=re.I)
            if updated == text:
                break
            text = updated
        return text.replace(':', '_')

    def _prefixless_prepare_id(self, value: str) -> str:
        """Loose lookup key used only for matching raw FASTA/GFF aliases."""
        text = (value or "").strip()
        if not text:
            return ""
        for _ in range(8):
            updated = re.sub(r'^(gene|transcript|CDS|cds|mRNA|mrna)[:_]', '', text, flags=re.I)
            if updated == text:
                break
            text = updated
        return text.replace(':', '_')

    def _canonical_attr_value(self, value: str) -> str:
        parts = [self._canonical_prepare_id(part) for part in (value or "").split(',')]
        return ','.join(part for part in parts if part)

    def _canonicalize_final_gff_fields(self, fields: List[str]) -> List[str]:
        """Normalize final prepared GFF rows without preserving provider ID prefixes."""
        if len(fields) != 9:
            return fields
        if fields[6] == '?':
            fields[6] = '.'

        id_like_keys = {
            'ID', 'Parent', 'Derives_from', 'Name', 'transcript_id', 'protein_id',
            'geneID', 'gene_id', 'gene', 'locus', 'locus_tag', 'exon_id',
        }
        drop_keys = {'Alias', 'gbkey'}
        rewritten_attrs = []
        for item in fields[8].split(';'):
            if not item or '=' not in item:
                continue
            key, value = item.split('=', 1)
            if key in drop_keys:
                continue
            if key in id_like_keys:
                new_value = self._canonical_attr_value(value)
                if new_value:
                    rewritten_attrs.append(f"{key}={new_value}")
            else:
                rewritten_attrs.append(item)
        fields[8] = ';'.join(rewritten_attrs) if rewritten_attrs else '.'
        return fields

    def _read_fasta_ids(self, fasta_file: Path) -> List[str]:
        ids = []
        with open(fasta_file, 'r', errors='replace') as handle:
            for line in handle:
                if line.startswith('>'):
                    ids.append(line[1:].split()[0])
        return ids

    def _write_ids_from_fasta(self, fasta_file: Path, output_file: Path) -> int:
        ids = self._read_fasta_ids(fasta_file)
        with open(output_file, 'w') as out:
            for seq_id in ids:
                out.write(f"{seq_id}\n")
        return len(ids)

    def _write_ids_from_longest_table(self, table_file: Path, output_file: Path) -> int:
        count = 0
        with open(table_file, 'r') as table, open(output_file, 'w') as out:
            for line in table:
                parts = line.split()
                if len(parts) >= 3:
                    out.write(self._canonical_prepare_id(parts[1]) + '\n')
                    count += 1
        return count

    def _write_map_from_longest_table(self, table_file: Path, output_file: Path) -> int:
        count = 0
        with open(table_file, 'r') as table, open(output_file, 'w') as out:
            for line in table:
                parts = line.split()
                if len(parts) >= 3:
                    out.write(f"{parts[1]}\t{parts[2]}\n")
                    count += 1
        return count

    def _read_longest_rows(self, table_file: Path) -> List[Tuple[str, str, str]]:
        rows: List[Tuple[str, str, str]] = []
        with open(table_file, 'r', errors='replace') as table:
            for line in table:
                parts = line.split()
                if len(parts) >= 3:
                    rows.append((parts[0], parts[1], parts[2]))
        return rows

    def _header_lookup_keys(self, header: str) -> List[str]:
        token = header.split()[0] if header.split() else header
        keys = [token]
        patterns = [
            r'\btranscript:([^\s;]+)',
            r'\btranscript=([^\s;]+)',
            r'\btranscript_id=([^\s;]+)',
            r'\bgene:([^\s;]+)',
            r'\bgene=([^\s;]+)',
            r'\bgene_id=([^\s;]+)',
            r'\bgeneID=([^\s;]+)',
            r'\bprotein_id=([^\s;]+)',
            r'\bParent=([^\s;]+)',
            r'\bID=([^\s;]+)',
            r'\bName=([^\s;]+)',
            r'\blocus=([^\s;]+)',
            r'\bpacid=([^\s;]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, header)
            if match:
                keys.append(match.group(1))
        expanded: List[str] = []
        seen = set()
        for key in keys:
            pac_alias = f"PAC:{key}" if key.isdigit() else key
            for candidate in [
                key,
                self._canonical_prepare_id(key),
                self._prefixless_prepare_id(key),
                pac_alias,
                key[:-4] if key.endswith('.CDS') else key + '.CDS',
                key[4:] if key.startswith('cds-') else key,
            ]:
                if candidate and candidate not in seen:
                    seen.add(candidate)
                    expanded.append(candidate)
        return expanded

    def _longest_alias_map(self, table_file: Path) -> Dict[str, str]:
        alias_map: Dict[str, str] = {}
        for gene_id, mrna_id, cds_id in self._read_longest_rows(table_file):
            canonical_mrna = self._canonical_prepare_id(mrna_id)
            aliases = {
                gene_id,
                mrna_id,
                cds_id,
                self._canonical_prepare_id(gene_id),
                self._canonical_prepare_id(mrna_id),
                self._canonical_prepare_id(cds_id),
                self._prefixless_prepare_id(gene_id),
                self._prefixless_prepare_id(mrna_id),
                self._prefixless_prepare_id(cds_id),
                mrna_id.replace('PAC:', '') if mrna_id.startswith('PAC:') else mrna_id,
                cds_id.replace('PAC:', '') if cds_id.startswith('PAC:') else cds_id,
                cds_id[:-4] if cds_id.endswith('.CDS') else cds_id + '.CDS',
                mrna_id[:-4] if mrna_id.endswith('.CDS') else mrna_id + '.CDS',
                f"cds-{cds_id}",
                f"cds-{mrna_id}",
            }
            for alias in aliases:
                if alias:
                    alias_map[alias] = canonical_mrna
        return alias_map

    def _extract_fasta_by_longest_table(self, source: Path, table_file: Path, output: Path, label: str) -> int:
        alias_map = self._longest_alias_map(table_file)
        selected = set(alias_map.values())
        written: set[str] = set()
        output.parent.mkdir(parents=True, exist_ok=True)
        write_target = output
        if source.resolve() == output.resolve():
            write_target = output.with_name(output.name + ".tmp_longest")
        current_header = ""
        current_target = ""
        seq_parts: List[str] = []

        def flush(out_handle) -> None:
            nonlocal current_header, current_target, seq_parts
            if current_target and current_target not in written:
                seq = ''.join(seq_parts).strip()
                if seq:
                    out_handle.write(f">{current_target} original_header={current_header}\n")
                    for i in range(0, len(seq), 80):
                        out_handle.write(seq[i:i + 80] + "\n")
                    written.add(current_target)
            current_header = ""
            current_target = ""
            seq_parts = []

        with open(source, 'r', errors='replace') as infile, open(write_target, 'w') as out:
            for raw in infile:
                line = raw.rstrip('\n')
                if line.startswith('>'):
                    flush(out)
                    current_header = line[1:].strip()
                    current_target = ""
                    for key in self._header_lookup_keys(current_header):
                        if key in alias_map:
                            current_target = alias_map[key]
                            break
                elif current_header:
                    seq_parts.append(line.strip())
            flush(out)
        if write_target != output:
            write_target.replace(output)

        missing = len(selected) - len(written)
        if missing:
            logger.warning(
                f"{label}: extracted {len(written)}/{len(selected)} longest-transcript records from {source.name}; "
                f"{missing} selected IDs were not found in FASTA headers."
            )
        else:
            logger.info(f"{label}: extracted {len(written)} longest-transcript records from {source.name} with unified mRNA IDs.")
        return len(written)

    def _sync_longest_outputs_to_extracted_ids(
        self,
        table_file: Path,
        gff_file: Path,
        mrna_id_file: Path,
        cds_out: Path,
        pep_out: Path,
        id_alias_file: Path,
    ) -> int:
        """Keep final GFF and mRNA.id aligned with records actually present in CDS/PEP FASTA."""
        id_sets = []
        if cds_out.exists() and cds_out.stat().st_size > 0:
            id_sets.append(set(self._read_fasta_ids(cds_out)))
        if pep_out.exists() and pep_out.stat().st_size > 0:
            id_sets.append(set(self._read_fasta_ids(pep_out)))
        if not id_sets:
            return 0
        keep_mrnas = set.intersection(*id_sets)
        rows = self._read_longest_rows(table_file)
        kept_rows = [
            (gene, mrna, cds)
            for gene, mrna, cds in rows
            if self._canonical_prepare_id(mrna) in keep_mrnas
        ]
        selected_genes = {self._canonical_prepare_id(gene) for gene, _, _ in kept_rows}
        selected_feature_ids = set(keep_mrnas)
        selected_feature_ids.update(self._canonical_prepare_id(cds) for _, _, cds in kept_rows)
        original_count = len(rows)

        with open(mrna_id_file, 'w') as out:
            for _, mrna, _ in kept_rows:
                out.write(self._canonical_prepare_id(mrna) + '\n')

        aliases_written = set()
        with open(id_alias_file, 'w') as out:
            out.write("role\traw_id\tcanonical_id\n")
            for gene, mrna, cds in rows:
                for role, raw_id in [("gene", gene), ("mrna", mrna), ("cds", cds)]:
                    canonical_id = self._canonical_prepare_id(raw_id)
                    key = (role, raw_id, canonical_id)
                    if raw_id and canonical_id and key not in aliases_written:
                        out.write(f"{role}\t{raw_id}\t{canonical_id}\n")
                        aliases_written.add(key)

        if gff_file.exists():
            tmp_gff = gff_file.with_suffix(gff_file.suffix + '.tmp_sync')
            with open(gff_file, 'r', errors='replace') as infile, open(tmp_gff, 'w') as out:
                for raw in infile:
                    if raw.startswith('#') or not raw.strip():
                        continue
                    fields = raw.rstrip('\n').split('\t')
                    if len(fields) != 9:
                        continue
                    attrs = {}
                    for item in fields[8].split(';'):
                        if '=' in item:
                            key, value = item.split('=', 1)
                            attrs[key] = value.split(',')[0]
                    fid = attrs.get('ID') or attrs.get('transcript_id') or ''
                    parent = attrs.get('Parent') or ''
                    gene_id = attrs.get('geneID') or attrs.get('gene_id') or attrs.get('gene') or attrs.get('Name') or ''
                    fid_c = self._canonical_prepare_id(fid)
                    parent_c = self._canonical_prepare_id(parent)
                    gene_id_c = self._canonical_prepare_id(gene_id)
                    feature = fields[2].lower()
                    keep = (
                        (feature == 'gene' and (fid_c in selected_genes or gene_id_c in selected_genes))
                        or (feature in {'mrna', 'transcript'} and fid_c in selected_feature_ids)
                        or (re.search(r'CDS|exon|UTR', fields[2], re.I) and parent_c in selected_feature_ids)
                    )
                    if keep:
                        fields = self._canonicalize_final_gff_fields(fields)
                        out.write('\t'.join(fields) + '\n')
            tmp_gff.replace(gff_file)
            logger.warning(
                f"Filtered {gff_file.name} and mRNA.id to {len(keep_mrnas)}/{original_count} records "
                "that are present in extracted CDS/PEP FASTA; canonicalized final GFF IDs."
            )
        return len(keep_mrnas)

    def _preprocess_gff(self, gff_file: Path, gff_tmp: Path, db_type: str) -> None:
        with open(gff_file, 'r', errors='replace') as infile, open(gff_tmp, 'w') as outfile:
            for line in infile:
                if db_type == "jgi":
                    line = re.sub(r'\.TAIR\d*', '', line)
                elif db_type == "ensembl":
                    line = re.sub(r'([=,])(gene|transcript|CDS|cds|mRNA|mrna):', r'\1', line)
                elif db_type == "ncbi":
                    line = re.sub(r'([=,])gene-', r'\1', line)
                    line = re.sub(r'([=,])rna-', r'\1', line)
                    line = re.sub(r'([=,])cds-', r'\1', line)
                else:
                    line = re.sub(r'\.TAIR\d*', '', line)
                    line = re.sub(r'([=,])(gene|transcript|CDS|cds|mRNA|mrna):', r'\1', line)
                outfile.write(line)

    def _normalize_ncbi_cds_headers(self, cds_file: Path, output_file: Path) -> Path:
        pattern = re.compile(r'^>\S+_cds_(\S+)_[0-9]+\s+')
        with open(cds_file, 'r', errors='replace') as infile, open(output_file, 'w') as outfile:
            for line in infile:
                if line.startswith('>'):
                    match = pattern.match(line)
                    if match:
                        line = '>' + match.group(1) + ' ' + line[match.end():]
                outfile.write(line)
        return output_file

    def _simplify_ncbi_gff_attrs(self, gff_file: Path) -> None:
        """Trim verbose NCBI annotations while keeping structural GFF relationships."""
        keep_order = [
            'ID', 'Parent', 'Name', 'Alias', 'gene', 'locus_tag',
            'protein_id', 'transcript_id', 'gbkey', 'gene_biotype'
        ]
        keep = set(keep_order)
        tmp_file = gff_file.with_suffix(gff_file.suffix + '.slim_tmp')
        with open(gff_file, 'r', errors='replace') as infile, open(tmp_file, 'w') as outfile:
            for line in infile:
                if line.startswith('#') or not line.strip():
                    outfile.write(line)
                    continue
                fields = line.rstrip('\n').split('\t')
                if len(fields) != 9:
                    outfile.write(line)
                    continue
                fields = self._canonicalize_final_gff_fields(fields)
                attrs = {}
                for item in fields[8].split(';'):
                    if not item or '=' not in item:
                        continue
                    key, value = item.split('=', 1)
                    if key in keep and value:
                        attrs[key] = value
                slim_attrs = []
                for key in keep_order:
                    if key in attrs:
                        slim_attrs.append(f'{key}={attrs[key]}')
                fields[8] = ';'.join(slim_attrs) if slim_attrs else fields[8]
                outfile.write('\t'.join(fields) + '\n')
        tmp_file.replace(gff_file)

    def _extract_pep_if_needed(self, species: SpeciesFiles, pep_file: Optional[Path], genome_file: Optional[Path], gff_file: Optional[Path]) -> Optional[Path]:
        if pep_file or not genome_file or not gff_file:
            return pep_file
        logger.info("No protein file found, attempting to extract from genome+GFF using gffread...")
        self._ensure_tool('gffread')
        pep_temp = self.work_dir / f"{species.species_code}_extracted.pep.fa"
        cmd = ['gffread', '-y', str(pep_temp.resolve()), '-g', str(genome_file.resolve()), str(gff_file.resolve())]
        rc, _, stderr = self.run_command(cmd, f"Extract protein from genome for {species.species_code}")
        if rc == 0 and pep_temp.exists() and pep_temp.stat().st_size > 0:
            logger.info(f"Successfully extracted protein sequences to {pep_temp.name}")
            return pep_temp
        raise RuntimeError(f"No protein file and failed to extract from genome+GFF: {stderr}")

    def _extract_cds_if_needed(self, species: SpeciesFiles, cds_file: Optional[Path], genome_file: Optional[Path], gff_file: Optional[Path]) -> Optional[Path]:
        if cds_file or not genome_file or not gff_file:
            return cds_file
        logger.info("No CDS file found, attempting to extract from genome+GFF using gffread...")
        self._ensure_tool('gffread')
        cds_temp = self.work_dir / f"{species.species_code}_extracted.cds.fa"
        cmd = ['gffread', '-x', str(cds_temp.resolve()), '-g', str(genome_file.resolve()), str(gff_file.resolve())]
        rc, _, stderr = self.run_command(cmd, f"Extract CDS from genome for {species.species_code}")
        if rc == 0 and cds_temp.exists() and cds_temp.stat().st_size > 0:
            logger.info(f"Successfully extracted CDS sequences to {cds_temp.name}")
            return cds_temp
        logger.warning(f"No CDS file and failed to extract CDS from genome+GFF: {stderr}")
        return cds_file

    def _sanitize_sequence_inputs(
        self,
        species: SpeciesFiles,
        cds_file: Optional[Path],
        pep_file: Optional[Path],
        genome_file: Optional[Path],
    ) -> Tuple[Optional[Path], Optional[Path], Optional[Path]]:
        """Reject genome-like files that were mislabeled as CDS/PEP."""
        for label, path in [("CDS", cds_file), ("PEP", pep_file)]:
            if not path:
                continue
            genome_like, reason = _looks_like_genome_fasta(path)
            if not genome_like:
                continue
            logger.warning(
                f"{species.species_code} {label} file {path.name} looks like genome FASTA ({reason}); "
                "it will not be used as CDS/PEP."
            )
            if genome_file is None:
                genome_file = path
                logger.info(f"{species.species_code}: using mislabeled {label} file as genome input: {path.name}")
            if label == "CDS":
                cds_file = None
            else:
                pep_file = None
        return cds_file, pep_file, genome_file

    def _build_artifacts(self, cds_out: Path, pep_out: Path, gff_out: Path, genome_out: Path, genome_file: Optional[Path], id_alias_out: Optional[Path] = None) -> Dict[str, Any]:
        artifacts: Dict[str, Any] = {}
        for key, path, artifact_type in [("cds", cds_out, "fasta"), ("pep", pep_out, "fasta"), ("gff", gff_out, "gff3")]:
            if path.exists():
                artifacts[key] = {"artifact": key, "path": str(path.name), "full_path": str(path.absolute()), "type": artifact_type, "size": path.stat().st_size, "sha256": compute_file_hash(path), "mtime": path.stat().st_mtime}
        if genome_file and genome_out.exists():
            artifacts["genome"] = {"artifact": "genome", "path": str(genome_out.name), "full_path": str(genome_out.absolute()), "type": "fasta", "size": genome_out.stat().st_size, "sha256": compute_file_hash(genome_out), "mtime": genome_out.stat().st_mtime}
        if id_alias_out and id_alias_out.exists():
            artifacts["id_alias"] = {"artifact": "id_alias", "path": str(id_alias_out.name), "full_path": str(id_alias_out.absolute()), "type": "tsv", "size": id_alias_out.stat().st_size, "sha256": compute_file_hash(id_alias_out), "mtime": id_alias_out.stat().st_mtime}
        return artifacts

    def _process_reference(self, species: SpeciesFiles, db_type: str, cds_file: Optional[Path], family_pep_file: Optional[Path], all_pep_file: Optional[Path], gff_file: Optional[Path]) -> Dict[str, Any]:
        id_source = cds_file if cds_file else family_pep_file or all_pep_file
        gene_count = self._write_ids_from_fasta(id_source, self.work_dir / f"{species.species_code}_mRNA.id") if id_source else 0
        logger.info("Reference file detected, using lightweight processing")
        artifacts = {}
        if cds_file:
            artifacts["ref_cds"] = {"artifact": "ref_cds", "path": str(cds_file.name), "full_path": str(cds_file.absolute()), "type": "fasta", "size": cds_file.stat().st_size, "sha256": compute_file_hash(cds_file), "mtime": cds_file.stat().st_mtime}
        if family_pep_file:
            artifacts["ref_family_pep"] = {"artifact": "ref_family_pep", "path": str(family_pep_file.name), "full_path": str(family_pep_file.absolute()), "type": "fasta", "size": family_pep_file.stat().st_size, "sha256": compute_file_hash(family_pep_file), "mtime": family_pep_file.stat().st_mtime}
            artifacts["ref_pep"] = dict(artifacts["ref_family_pep"], artifact="ref_pep")
        if all_pep_file:
            artifacts["ref_all_pep"] = {"artifact": "ref_all_pep", "path": str(all_pep_file.name), "full_path": str(all_pep_file.absolute()), "type": "fasta", "size": all_pep_file.stat().st_size, "sha256": compute_file_hash(all_pep_file), "mtime": all_pep_file.stat().st_mtime}
        if gff_file:
            artifacts["ref_gff"] = {"artifact": "ref_gff", "path": str(gff_file.name), "full_path": str(gff_file.absolute()), "type": "gff3", "size": gff_file.stat().st_size, "sha256": compute_file_hash(gff_file), "mtime": gff_file.stat().st_mtime}
        return {"species": species.species_code, "success": True, "db_type": db_type, "gene_count": gene_count, "is_reference": True, "artifacts": artifacts, "files": {"cds": str(cds_file) if cds_file else None, "family_pep": str(family_pep_file) if family_pep_file else None, "all_pep": str(all_pep_file) if all_pep_file else None, "gff": str(gff_file) if gff_file else None}}

    def _process_jgi_or_unknown(self, species: SpeciesFiles, db_type: str, gff_file: Path, cds_file: Optional[Path], pep_file: Optional[Path], genome_file: Optional[Path], outputs: Dict[str, Path]) -> Tuple[int, Dict[str, Any]]:
        filter_script = self._ensure_script('gff_filter_bymRNAID.py')
        longest_script = self._ensure_script('gff_filter_longest.py')
        cds_file = self._extract_cds_if_needed(species, cds_file, genome_file, gff_file)
        if not pep_file:
            pep_file = self._extract_pep_if_needed(species, pep_file, genome_file, gff_file)
        if not cds_file and not pep_file:
            raise RuntimeError("No CDS or PEP file available")
        gff_tmp = self.work_dir / f"{species.species_code}.gff3.tmp"
        self._preprocess_gff(gff_file, gff_tmp, db_type)
        longest_table = self.work_dir / f"{species.species_code}_gene_mrna_cds.ids"
        rc, _, stderr = self.run_command(
            [sys.executable, str(longest_script), str(gff_tmp.resolve()), str(longest_table.resolve()), str(outputs['gff'].resolve())],
            f"Select longest CDS and filter GFF for {species.species_code} ({db_type})"
        )
        if rc == 0 and longest_table.exists() and longest_table.stat().st_size > 0 and outputs['gff'].exists() and outputs['gff'].stat().st_size > 0:
            mrna_id_file = self.work_dir / f"{species.species_code}_mRNA.id"
            gene_count = self._write_ids_from_longest_table(longest_table, mrna_id_file)
            logger.info(f"Selected {gene_count} longest CDS IDs from {longest_table.name}")
            extracted_any = False
            if cds_file:
                cds_count = self._extract_fasta_by_longest_table(cds_file, longest_table, outputs['cds'], f"{species.species_code} CDS")
                extracted_any = extracted_any or cds_count > 0
            if pep_file:
                pep_count = self._extract_fasta_by_longest_table(pep_file, longest_table, outputs['pep'], f"{species.species_code} PEP")
                extracted_any = extracted_any or pep_count > 0
            if not extracted_any:
                raise RuntimeError("Longest-transcript table was generated, but no selected CDS/PEP IDs matched FASTA headers")
            gene_count = self._sync_longest_outputs_to_extracted_ids(longest_table, outputs['gff'], mrna_id_file, outputs['cds'], outputs['pep'], outputs['id_alias'])
            self._copy_if_needed(genome_file, outputs['genome'])
            return gene_count, self._build_artifacts(outputs['cds'], outputs['pep'], outputs['gff'], outputs['genome'], genome_file, outputs.get('id_alias'))

        logger.warning(
            f"{species.species_code}: longest-transcript selection was not applicable ({stderr.strip() if stderr else 'empty table/output'}); "
            "falling back to FASTA-ID based GFF filtering."
        )
        id_source = cds_file if cds_file else pep_file
        mrna_id_file = self.work_dir / f"{species.species_code}_mRNA.id"
        gene_count = self._write_ids_from_fasta(id_source, mrna_id_file)
        logger.info(f"Extracted {gene_count} gene IDs from {id_source.name}")
        gene_table = self.work_dir / f"{species.species_code}_geneID_mrnaID.table"
        cmd = [sys.executable, str(filter_script), str(gff_tmp.resolve()), str(mrna_id_file.resolve()), str(gene_table.resolve()), str(outputs['gff'].resolve())]
        rc, _, stderr = self.run_command(cmd, f"Filter GFF for {species.species_code} ({db_type})")
        if rc != 0:
            raise RuntimeError(f"GFF filtering failed: {stderr}")
        if outputs['gff'].exists() and outputs['gff'].stat().st_size == 0:
            raise RuntimeError("GFF filtering produced an empty final GFF3; FASTA IDs do not match GFF mRNA IDs")
        self._copy_if_needed(cds_file if cds_file else pep_file, outputs['cds'])
        self._copy_if_needed(pep_file, outputs['pep'])
        self._copy_if_needed(genome_file, outputs['genome'])
        return gene_count, self._build_artifacts(outputs['cds'], outputs['pep'], outputs['gff'], outputs['genome'], genome_file, outputs.get('id_alias'))

    def _process_ensembl(self, species: SpeciesFiles, gff_file: Path, cds_file: Optional[Path], pep_file: Optional[Path], genome_file: Optional[Path], outputs: Dict[str, Path]) -> Tuple[int, Dict[str, Any]]:
        self._ensure_tool('seqtk')
        longest_script = self._ensure_script('gff_filter_longest.py')
        cds_file = self._extract_cds_if_needed(species, cds_file, genome_file, gff_file)
        if not pep_file:
            pep_file = self._extract_pep_if_needed(species, pep_file, genome_file, gff_file)
        if not cds_file or not pep_file:
            raise RuntimeError("Ensembl processing requires CDS and PEP files, or genome+GFF that can be used to extract them")
        gff_tmp = self.work_dir / f"{species.species_code}.gff3.tmp"
        self._preprocess_gff(gff_file, gff_tmp, "ensembl")
        longest_table = self.work_dir / f"{species.species_code}_gene_mrna_cds.ids"
        cmd = [sys.executable, str(longest_script), str(gff_tmp.resolve()), str(longest_table.resolve()), str(outputs['gff'].resolve())]
        rc, _, stderr = self.run_command(cmd, f"Select longest CDS and filter GFF for {species.species_code} (ensembl)")
        if rc != 0:
            raise RuntimeError(f"Longest CDS filtering failed: {stderr}")
        mrna_id_file = self.work_dir / f"{species.species_code}_mRNA.id"
        gene_count = self._write_ids_from_longest_table(longest_table, mrna_id_file)
        logger.info(f"Selected {gene_count} longest CDS IDs from {longest_table.name}")
        cds_count = self._extract_fasta_by_longest_table(cds_file, longest_table, outputs['cds'], f"{species.species_code} CDS")
        pep_count = self._extract_fasta_by_longest_table(pep_file, longest_table, outputs['pep'], f"{species.species_code} PEP")
        if cds_count == 0 or pep_count == 0:
            raise RuntimeError(f"Failed to extract unified longest CDS/PEP records: CDS={cds_count}, PEP={pep_count}")
        gene_count = self._sync_longest_outputs_to_extracted_ids(longest_table, outputs['gff'], mrna_id_file, outputs['cds'], outputs['pep'], outputs['id_alias'])
        self._copy_if_needed(genome_file, outputs['genome'])
        return gene_count, self._build_artifacts(outputs['cds'], outputs['pep'], outputs['gff'], outputs['genome'], genome_file, outputs.get('id_alias'))

    def _process_ncbi(self, species: SpeciesFiles, gff_file: Path, cds_file: Optional[Path], pep_file: Optional[Path], genome_file: Optional[Path], outputs: Dict[str, Path]) -> Tuple[int, Dict[str, Any]]:
        self._ensure_tool('seqtk')
        longest_script = self._ensure_script('gff_filter_longest.py')
        map_script = self._ensure_script('map_gff_ids.py')
        cds_file = self._extract_cds_if_needed(species, cds_file, genome_file, gff_file)
        if not pep_file:
            pep_file = self._extract_pep_if_needed(species, pep_file, genome_file, gff_file)
        if not cds_file or not pep_file:
            raise RuntimeError("NCBI processing requires CDS and PEP files, or genome+GFF that can be used to extract them")
        normalized_cds = self.work_dir / f"{species.species_code}_all.cds.fasta"
        self._normalize_ncbi_cds_headers(cds_file, normalized_cds)
        gff_tmp = self.work_dir / f"{species.species_code}.gff3.tmp"
        self._preprocess_gff(gff_file, gff_tmp, "ncbi")
        longest_table = self.work_dir / f"{species.species_code}_gene_mrna_cds.ids"
        cmd = [sys.executable, str(longest_script), str(gff_tmp.resolve()), str(longest_table.resolve()), str(outputs['gff'].resolve())]
        rc, _, stderr = self.run_command(cmd, f"Select longest CDS and filter GFF for {species.species_code} (ncbi)")
        if rc != 0:
            raise RuntimeError(f"Longest CDS filtering failed: {stderr}")
        mrna_map_file = self.work_dir / f"{species.species_code}_mRNA.mapid"
        self._write_map_from_longest_table(longest_table, mrna_map_file)
        cmd = [sys.executable, str(map_script), str(mrna_map_file.resolve()), str(outputs['gff'].resolve())]
        rc, _, stderr = self.run_command(cmd, f"Map NCBI mRNA IDs to CDS IDs for {species.species_code}")
        if rc != 0:
            raise RuntimeError(f"NCBI GFF ID mapping failed: {stderr}")
        self._simplify_ncbi_gff_attrs(outputs['gff'])
        mrna_id_file = self.work_dir / f"{species.species_code}_mRNA.id"
        gene_count = self._write_ids_from_longest_table(longest_table, mrna_id_file)
        logger.info(f"Selected {gene_count} longest NCBI CDS IDs from {longest_table.name}")
        cds_count = self._extract_fasta_by_longest_table(normalized_cds, longest_table, outputs['cds'], f"{species.species_code} CDS")
        pep_count = self._extract_fasta_by_longest_table(pep_file, longest_table, outputs['pep'], f"{species.species_code} PEP")
        if cds_count == 0 or pep_count == 0:
            raise RuntimeError(f"Failed to extract unified longest CDS/PEP records: CDS={cds_count}, PEP={pep_count}")
        gene_count = self._sync_longest_outputs_to_extracted_ids(longest_table, outputs['gff'], mrna_id_file, outputs['cds'], outputs['pep'], outputs['id_alias'])
        self._copy_if_needed(genome_file, outputs['genome'])
        return gene_count, self._build_artifacts(outputs['cds'], outputs['pep'], outputs['gff'], outputs['genome'], genome_file, outputs.get('id_alias'))

    def process_species(self, species: SpeciesFiles) -> Dict[str, Any]:
        """???????"""
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing species: {species.species_code}")
        logger.info(f"{'='*60}")
        gene_family = self._extract_gene_family(species)
        is_ref = self._is_reference_file(species)
        outputs = {
            "cds": self.input_dir / f"{species.species_code}.cds.fasta",
            "pep": self.input_dir / f"{species.species_code}.pep.fasta",
            "genome": self.input_dir / f"{species.species_code}.genome.fasta",
            "gff": self.input_dir / f"{species.species_code}_final.gff3",
            "id_alias": self.input_dir / f"{species.species_code}.id_alias.tsv",
        }
        try:
            gff_file = self.decompress_if_needed(species.gff) if species.gff else None
            genome_file = self.decompress_if_needed(species.genome) if species.genome else None
            cds_file = self.decompress_if_needed(species.cds) if species.cds else None
            all_pep_file = self.decompress_if_needed(species.all_pep) if species.all_pep else None
            family_pep_file = self.decompress_if_needed(species.family_pep) if species.family_pep else None
            pep_file = all_pep_file or (self.decompress_if_needed(species.pep) if species.pep else None)
            cds_file, pep_file, genome_file = self._sanitize_sequence_inputs(species, cds_file, pep_file, genome_file)
            if all_pep_file and pep_file is None:
                all_pep_file = None
            if is_ref and pep_file and not family_pep_file and _extract_gene_family_from_filename(pep_file.name):
                family_pep_file = pep_file
                if all_pep_file == pep_file:
                    all_pep_file = None
            elif is_ref and pep_file and not all_pep_file:
                all_pep_file = pep_file
            db_type = self.detect_db_type_from_gff(gff_file)
            species.db_type = db_type
            logger.info(f"Detected database type from GFF for {species.species_code}: {db_type}")
            if is_ref:
                return self._process_reference(species, db_type, cds_file, family_pep_file, all_pep_file, gff_file)
            if not gff_file:
                raise RuntimeError("Target species requires a GFF file for genome_prepare")
            if db_type == "jgi":
                gene_count, artifacts = self._process_jgi_or_unknown(species, db_type, gff_file, cds_file, pep_file, genome_file, outputs)
            elif db_type == "ensembl":
                gene_count, artifacts = self._process_ensembl(species, gff_file, cds_file, pep_file, genome_file, outputs)
            elif db_type == "ncbi":
                gene_count, artifacts = self._process_ncbi(species, gff_file, cds_file, pep_file, genome_file, outputs)
            else:
                gene_count, artifacts = self._process_jgi_or_unknown(species, "unknown_user", gff_file, cds_file, pep_file, genome_file, outputs)
            logger.info(f"{species.species_code} processing complete")
            if outputs['pep'].exists():
                artifacts["all_pep"] = dict(artifacts["pep"], artifact="all_pep")
            return {"species": species.species_code, "success": True, "db_type": db_type, "gene_count": gene_count, "artifacts": artifacts, "files": {"cds": str(outputs['cds']), "pep": str(outputs['pep']), "all_pep": str(outputs['pep']), "gff": str(outputs['gff']), "genome": str(outputs['genome']) if genome_file else None}}
        except Exception as e:
            logger.error(f"{species.species_code} processing failed: {e}")
            return {"species": species.species_code, "success": False, "db_type": getattr(species, 'db_type', 'unknown_user'), "error": str(e)}

    def process_all(self, valid_species: Optional[List[str]] = None, preflight_results: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """Process preflight-valid species and preserve skipped species results."""
        species_map = MultiSpeciesDetector.scan_input_dir(self.input_dir)
        if valid_species is not None:
            valid_set = set(valid_species)
            species_map = {code: species for code, species in species_map.items() if code in valid_set}
        logger.info(f"\nDetected {len(species_map)} executable species:")
        for code, species in species_map.items():
            logger.info(f"  - {species}")
        results = list(preflight_results or [])
        for species in species_map.values():
            result = self.process_species(species)
            results.append(result)
        return results

def scan_hmm_files(input_dir: Path) -> List[Dict[str, Any]]:
    """扫描 HMM 文件
    
    FIX: 新增函数，扫描 input_dir 下的 .hmm 文件
    """
    hmm_files = []
    for file in input_dir.iterdir():
        if file.is_file() and file.suffix == '.hmm':
            hmm_files.append({
                "path": str(file.name),
                "full_path": str(file.absolute()),
                "sha256": compute_file_hash(file),
                "size": file.stat().st_size,
                "mtime": file.stat().st_mtime
            })
    return hmm_files


def write_manifest(
    output_dir: Path,
    job_id: str,
    run_id: str,
    prefix: str,
    start_time: datetime,
    end_time: datetime,
    results: List[Dict[str, Any]],
    tool_versions: Dict[str, str],
    cmd_history: List[str],
    input_dir: Path,
    status: str = "success",
    merge_result: Optional[Dict[str, Any]] = None,
    species_config: Optional[Path] = None,
    workspace_dir: Optional[Path] = None,  # REVISION 2026-04-28: 新增workspace_dir参数
    config_preflight: Optional[Dict[str, Any]] = None
) -> Path:
    """
    写入符合schema v1的manifest文件
    
    注意：artifacts必须是字典格式，不是列表
    """
    elapsed_sec = (end_time - start_time).total_seconds()
    success_count = sum(1 for r in results if r.get("success"))
    total_count = len(results)
    
    # 确定状态（确保不为None）
    if success_count == 0:
        status = "failed"
    elif success_count < total_count:
        status = "partial_success"
    elif status is None:
        status = "success"
    
    # 收集所有 artifacts
    all_artifacts = {}
    for result in results:
        if result.get("success") and "artifacts" in result:
            species = result.get("species", "unknown")
            for key, artifact in result["artifacts"].items():
                artifact_key = f"{species}_{key}"
                all_artifacts[artifact_key] = artifact
    
    # FIX: 扫描 HMM 文件
    hmm_files = scan_hmm_files(input_dir)
    
    # FIX: 构建 inputs 列表（从子目录收集）
    inputs_list = []
    for species_dir in input_dir.iterdir():
        if species_dir.is_dir() and not species_dir.name.startswith('.'):
            for file in species_dir.iterdir():
                if file.is_file():
                    inputs_list.append({
                        "path": f"{species_dir.name}/{file.name}",
                        "full_path": str(file.absolute()),
                        "sha256": compute_file_hash(file),
                        "size": file.stat().st_size,
                        "mtime": file.stat().st_mtime
                    })
    
    # FIX: 添加合并后的文件信息
    merged_data = {}
    merged_family = _clean_text((merge_result or {}).get("gene_family", ""))
    if merge_result and merge_result.get("merged"):
        merged_data = {
            "target": {
                "species": merge_result.get("target_species", []),
                "files": merge_result.get("merged_files", {})
            },
            "reference": {
                "species": merge_result.get("ref_species", []),
                "files": {k: v for k, v in merge_result.get("merged_files", {}).items() if k.startswith("ref_")}
            }
        }
    
    manifest_data = {
        # 基础标识字段（必需）
        "manifest_version": "1.0",
        "job_id": job_id,
        "stage": "genome_prepare",
        "implementation_version": "v2",
        "run_id": run_id,
        "prefix": prefix,
        
        # FIX: 添加物种配置路径
        "species_config": str(species_config) if species_config else None,
        
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
        "workspace_root": str(output_dir.parent),
        "workspace": str(output_dir),
        "input_dir": str(input_dir),
        
        # 输入文件（必需）
        "inputs": inputs_list,
        
        # FIX: HMM 文件单独记录
        "hmm": hmm_files,
        
        # 输出产物（必需）- 字典格式
        "artifacts": all_artifacts,
        
        # FIX: 添加合并后的文件信息
        "merged": merged_data,
        
        # 参数和指标（必需）
        "parameters": {
            "total_species": total_count,
            "success_species": success_count,
            "failed_species": total_count - success_count
        },
        "metrics": {
            "species_results": results,
            "config_preflight": config_preflight or {}
        },
        
        # 修订 2026-04-26: 更新合并文件信息
        "merged_files": {
            "target": {
                "prefix": None,  # 不再合并目标物种
                "cds": None,
                "pep": None,
                "all_pep": None,
                "family_pep": None,
                "gff": None
            },
            "reference": {
                "prefix": f"Ref_combined.{merged_family}" if merged_family else None,
                "cds": f"Ref_combined.{merged_family}.cds.fasta" if merged_family and (input_dir / f"Ref_combined.{merged_family}.cds.fasta").exists() else None,
                "pep": f"Ref_combined.{merged_family}.pep.fasta" if merged_family and (input_dir / f"Ref_combined.{merged_family}.pep.fasta").exists() else None
            }
        },
        
        # 溯源信息（可选但推荐）
        "provenance": {
            "derived_from": "raw_genome_data",
            "stage_sequence": ["genome_prepare"]
        }
    }
    
    # 确保输出到 final/ 子目录
    final_dir = output_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    
    manifest_path = final_dir / f"{prefix}.genome_prepare.manifest.json"
    
    print(f"生成Manifest文件: {manifest_path}")
    
    # 确保JSON严格性：无注释，确保ASCII
    with open(manifest_path, "w", encoding='utf-8') as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)
    
    # 确认文件已生成
    if manifest_path.exists():
        print(f"  ✅ Manifest文件已生成: {manifest_path} ({manifest_path.stat().st_size} 字节)")
        
        # REVISION 2026-04-28: 同时复制manifest到input_dir/final/目录
        # 因为实际输出文件都在input_dir，后端API会去input_dir查找
        input_final_dir = input_dir / "final"
        input_final_dir.mkdir(parents=True, exist_ok=True)
        input_manifest = input_final_dir / manifest_path.name
        try:
            if manifest_path.resolve() != input_manifest.resolve():
                shutil.copy2(str(manifest_path), str(input_manifest))
                print(f"  ✅ Manifest已复制到input目录: {input_manifest}")
        except Exception as e:
            print(f"  ⚠️ Manifest复制到input目录失败: {e}")
        
        # REVISION 2026-04-28: 同时复制manifest到workspace/final/目录
        # 供后端API查找（pipeline_backend.py去workspace查找manifest）
        if workspace_dir:
            workspace_final_dir = workspace_dir / "final"
            workspace_final_dir.mkdir(parents=True, exist_ok=True)
            workspace_manifest = workspace_final_dir / manifest_path.name
            try:
                shutil.copy2(str(manifest_path), str(workspace_manifest))
                print(f"  ✅ Manifest已复制到workspace目录: {workspace_manifest}")
            except Exception as e:
                print(f"  ⚠️ Manifest复制到workspace目录失败: {e}")
    else:
        print(f"  ❌ Manifest文件生成失败: {manifest_path}")
    
    return manifest_path


def write_report(
    output_dir: Path,
    prefix: str,
    results: List[Dict[str, Any]],
    tool_versions: Dict[str, str],
    parameters: Dict[str, Any],
    config_preflight: Optional[Dict[str, Any]] = None
) -> Path:
    """写入人可读的分析报告"""
    # 确保输出到 final/ 子目录
    final_dir = output_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    
    report_path = final_dir / f"{prefix}.genome_prepare_report.md"
    
    success_count = sum(1 for r in results if r.get("success"))
    total_count = len(results)
    
    print(f"生成分析报告: {report_path}")
    
    with open(report_path, 'w') as f:
        f.write(f"# Genome Prepare 分析报告 - {prefix}\n\n")
        f.write(f"**生成时间**: {datetime.now().isoformat()}\n\n")
        
        f.write("## 1. 分析概述\n")
        f.write(f"- **分析阶段**: Genome Prepare\n")
        f.write(f"- **分析状态**: {'✅ 成功' if success_count == total_count else '⚠️ 部分成功' if success_count > 0 else '❌ 失败'}\n")
        f.write(f"- **总物种数**: {total_count}\n")
        f.write(f"- **成功**: {success_count}\n")
        f.write(f"- **失败**: {total_count - success_count}\n\n")
        
        f.write("## 2. 工具版本\n")
        for tool, version in tool_versions.items():
            f.write(f"- **{tool}**: {version}\n")
        f.write("\n")
        
        f.write("## 3. 执行参数\n")
        for param, value in parameters.items():
            f.write(f"- **{param}**: {value}\n")
        f.write("\n")
        
        if config_preflight and config_preflight.get("enabled"):
            f.write("## 4. 配置预检\n")
            f.write(f"- **通过物种**: {', '.join(config_preflight.get('valid_species', [])) or '无'}\n")
            f.write(f"- **跳过物种**: {', '.join(config_preflight.get('invalid_species', [])) or '无'}\n")
            row_errors = config_preflight.get("row_errors", [])
            if row_errors:
                f.write("- **具体错误**:\n")
                for item in row_errors:
                    row = item.get("row", "?")
                    species = item.get("species", "?")
                    original = item.get("original") or "-"
                    reason = item.get("reason", "unknown")
                    f.write(f"  - Row {row}, {species}, {original}: {reason}\n")
            f.write("\n")

        f.write("## 5. 物种详细信息\n\n")
        for result in results:
            species = result.get("species", "Unknown")
            success = result.get("success", False)
            
            f.write(f"### {species}\n")
            f.write(f"- **状态**: {'✅ 成功' if success else '❌ 失败'}\n")
            
            if success:
                f.write(f"- **数据库类型**: {result.get('db_type', 'unknown')}\n")
                f.write(f"- **基因数量**: {result.get('gene_count', 'N/A')}\n")
                
                # 输出文件信息（使用 artifacts 格式）
                artifacts = result.get("artifacts", {})
                if artifacts:
                    f.write("- **输出文件**:\n")
                    for artifact_name, artifact_info in artifacts.items():
                        if isinstance(artifact_info, dict):
                            f.write(f"  - **{artifact_name}**: {artifact_info.get('path', 'N/A')}\n")
                            if artifact_info.get('size'):
                                f.write(f"    - 大小: {artifact_info['size']} 字节\n")
                            if artifact_info.get('sha256'):
                                f.write(f"    - SHA256: {artifact_info['sha256'][:16]}...\n")
            else:
                f.write(f"- **错误信息**: {result.get('error', '未知错误')}\n")
                for item in result.get("errors", []) or []:
                    row = item.get("row", "?")
                    original = item.get("original") or "-"
                    reason = item.get("reason", "unknown")
                    f.write(f"  - Row {row}, {original}: {reason}\n")
            
            f.write("\n")
        
        f.write("## 6. 输出文件汇总\n")
        for result in results:
            if result.get("success"):
                species = result.get("species", "Unknown")
                artifacts = result.get("artifacts", {})
                for artifact_name, artifact_info in artifacts.items():
                    if isinstance(artifact_info, dict) and artifact_info.get('path'):
                        f.write(f"- `{artifact_info['path']}`\n")
        f.write("\n")
    
    return report_path


def main():
    parser = argparse.ArgumentParser(
        description="Genome Prepare - 多物种基因组数据预处理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python run_genome_prepare.py -i /path/to/input -o /path/to/output -p multi_species

输入要求：
  - 输入目录应包含原始基因组数据文件
  - 支持的文件格式：.fasta, .fa, .gff3, .gff (可带.gz压缩)
  - 文件名应包含物种标识（如 Ath, Ft, Os 等）

输出产物：
  - {species}.cds.fasta (标准化CDS序列)
  - {species}.pep.fasta (标准化蛋白序列)
  - {species}.genome.fasta (标准化基因组，如有)
  - {species}_final.gff3 (标准化GFF注释)
  - {prefix}.genome_prepare.manifest.json (机器清单)
  - {prefix}.genome_prepare_report.md (分析报告)
        """
    )
    parser.add_argument("-i", "--input", required=True, help="输入目录路径（包含原始基因组数据）")
    parser.add_argument("-o", "--output", required=True, help="输出目录路径（准备的fasta文件输出位置）")
    parser.add_argument("-w", "--workspace", help="工作目录路径（用于final/logs等标准输出，默认与--output相同）")
    parser.add_argument("-p", "--prefix", default="genome_prepare", help="输出文件前缀（默认：genome_prepare）")
    parser.add_argument("--scripts-dir", default=str(PROJECT_ROOT / "scripts"), help="Perl脚本目录")
    
    args = parser.parse_args()
    
    input_dir = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()
    
    if not args.prefix or args.prefix.strip() == "":
        args.prefix = "genome_prepare"
    output_dir.mkdir(parents=True, exist_ok=True)
    # FIX: 支持双输出位置，workspace用于标准输出，output用于准备的fasta文件
    # FIX: 标准化输出到 input_dir 根目录（平铺），而不是 output_dir
    workspace_dir = Path(args.workspace).resolve() if args.workspace else output_dir
    workspace_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir = Path(args.scripts_dir)
    
    # FIX: 创建 genome_prepare 过程文件夹
    genome_prepare_dir = input_dir / "genome_prepare"
    genome_prepare_dir.mkdir(exist_ok=True)
    
    start_time = datetime.now()
    tool_versions = get_tool_versions()
    
    # FIX: 步骤0 - 预检并根据 config.xlsx 组织文件
    print("\n📋 预检 config.xlsx...")
    config_preflight = preflight_config(input_dir)
    preflight_results = []
    if config_preflight.get("enabled"):
        valid_species = config_preflight.get("valid_species", [])
        invalid_species = config_preflight.get("invalid_species", [])
        print(f"  ✅ 预检通过物种: {valid_species or '无'}")
        if invalid_species:
            print(f"  ⚠️ 预检跳过物种: {invalid_species}")
            for species, errors in config_preflight.get("species_errors", {}).items():
                if species == "<config>":
                    continue
                messages = []
                for item in errors:
                    row = item.get("row", "?")
                    original = item.get("original") or "-"
                    reason = item.get("reason", "unknown")
                    messages.append(f"Row {row}, {original}: {reason}")
                    print(f"     - {species}: Row {row}, {original}: {reason}")
                preflight_results.append({
                    "species": species,
                    "success": False,
                    "db_type": "config_preflight",
                    "error_type": "config_preflight_failed",
                    "error": "; ".join(messages) or "config preflight failed",
                    "errors": errors,
                })
    else:
        print("  ℹ️ 未发现 config.xlsx，使用文件名扫描兜底")

    print("\n📋 根据 config.xlsx 组织文件...")
    try:
        org_result = organize_files_by_config(input_dir, config_preflight)
        if org_result.get("organized"):
            print(f"  ✅ 文件组织完成")
            print(f"     物种: {org_result.get('species_codes', [])}")
            print(f"     移动: {len(org_result.get('moved_files', []))} 个文件")
            if org_result.get('missing_files'):
                print(f"     缺失/跳过: {len(org_result['missing_files'])} 个文件")
        else:
            print(f"  ⚠️ 跳过文件组织: {org_result.get('reason', 'unknown')}")
    except Exception as e:
        org_result = {"organized": False, "reason": str(e), "preflight": config_preflight}
        print(f"  ⚠️ 文件组织失败: {e}")
    
    # 1. 验证输入目录
    print("\n🔍 验证输入目录...")
    try:
        input_info = validate_input_directory(input_dir)
        print(f"  ✅ 输入验证通过，检测到 {len(input_info['species_map'])} 个有效物种")
    except Exception as e:
        print(f"  ❌ 输入验证失败: {e}")
        sys.exit(1)
    
    # 2. 准备目录结构
    print("\n📁 准备目录结构...")
    print(f"   工作目录(workspace): {workspace_dir}")
    print(f"   输出目录(output): {output_dir}")
    paths = prepare_directories(workspace_dir)
    
    # 3. 获取工具版本
    print("\n🔧 检查工具版本...")
    tool_versions = get_tool_versions()
    print(f"   Python: {tool_versions.get('python', 'unknown')}")
    print(f"   Perl: {tool_versions.get('perl', 'unknown')}")
    print(f"   gffread: {tool_versions.get('gffread', 'unknown')}")
    print(f"   seqtk: {tool_versions.get('seqtk', 'unknown')}")
    
    # 4. 创建预处理器并执行
    # FIX: 使用 input_dir 作为输出目录（标准化文件输出到 input 根目录）
    # FIX: 修改 work_dir 为 genome_prepare_dir
    paths["work_dir"] = genome_prepare_dir
    preparer = MultiSpeciesGenomePreparer(input_dir, input_dir, scripts_dir, paths)
    
    try:
        # 处理所有物种
        print("\n🚀 开始处理物种...")
        valid_species_filter = config_preflight.get("valid_species") if config_preflight.get("enabled") else None
        results = preparer.process_all(valid_species_filter, preflight_results)
        
        # 记录结束时间
        end_time = datetime.now()
        
        # 生成运行ID
        run_id = f"genome_prepare_{start_time.strftime('%Y%m%d_%H%M%S_%f')[:-3]}"
        job_id = output_dir.name
        
        # FIX: 步骤5 - 合并物种文件
        merge_result = None
        config_path = None
        print("\n🔗 合并物种文件...")
        try:
            if org_result.get("organized"):
                merge_result = merge_species_files(input_dir, genome_prepare_dir, org_result)
                if merge_result.get("merged"):
                    print(f"  ✅ 文件合并完成")
                    print(f"     目标物种: {merge_result.get('target_species', [])}")
                    print(f"     参考物种: {merge_result.get('ref_species', [])}")
                    
                    # FIX: 生成 species_config.json
                    config_path = generate_species_config(input_dir, genome_prepare_dir, org_result)
                    print(f"  ✅ 物种配置已生成: {config_path}")
                else:
                    print(f"  ⚠️ 文件合并失败")
            else:
                print(f"  ⚠️ 跳过文件合并（未组织文件）")
        except Exception as e:
            print(f"  ⚠️ 文件合并失败: {e}")
        
        # 6. 生成manifest和报告
        print("\n📝 生成输出文件...")
        print(f"  DEBUG: prefix={args.prefix}, job_id={job_id}, run_id={run_id}")
        manifest_file = write_manifest(
            output_dir=output_dir,
            job_id=job_id,
            run_id=run_id,
            prefix=args.prefix,
            start_time=start_time,
            end_time=end_time,
            results=results,
            tool_versions=tool_versions,
            cmd_history=preparer.cmd_history,
            input_dir=input_dir,
            merge_result=merge_result,
            species_config=config_path,
            workspace_dir=workspace_dir,  # REVISION 2026-04-28: 传入workspace_dir用于复制manifest
            config_preflight=config_preflight
        )
        
        report_file = write_report(
            output_dir=output_dir,
            prefix=args.prefix,
            results=results,
            tool_versions=tool_versions,
            parameters={"input_dir": str(input_dir), "output_dir": str(output_dir)},
            config_preflight=config_preflight
        )
        
        # 6. 输出总结
        success_count = sum(1 for r in results if r.get("success"))
        print(f"\n{'='*60}")
        print(f"🎉 Genome Prepare 完成!")
        print(f"{'='*60}")
        print(f"总物种数: {len(results)}")
        print(f"成功: {success_count}")
        print(f"失败: {len(results) - success_count}")
        print(f"Manifest: {manifest_file}")
        print(f"报告: {report_file}")
        
        # 根据成功率返回退出码
        if success_count == 0:
            sys.exit(1)
        elif success_count < len(results):
            sys.exit(2)  # 部分成功
        
    except Exception as e:
        print(f"\n❌ Genome prepare 失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
