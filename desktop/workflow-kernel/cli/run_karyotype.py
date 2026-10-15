#!/usr/bin/env python3
"""
Karyotype阶段：绘制基因家族在染色体上的分布图

输入：
  - Synteny阶段的GFF文件（基因位置）
  - Identify阶段的基因ID列表
  - 基因组FASTA文件（计算染色体长度）

输出：
  - {prefix}.karyotype.pdf (核型图PDF)
  - {prefix}.karyotype.png (核型图PNG)
  - {prefix}.karyotype_report.md (分析报告)
  - {prefix}.karyotype.manifest.json (机器清单)

符合 karyotype_contract_v1.md 和 manifest_v1.schema.json 契约
"""

import argparse
import json
import os
import subprocess
import sys
import shutil
import re
import gzip
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import hashlib

try:
    from gfa_core.gene_family_scope import read_scope, sanitize_token
    from gfa_core.gene_family_stage_context import apply_stage_context, build_stage_context
except Exception:
    def read_scope(scope_path):
        return json.loads(Path(scope_path).read_text(encoding="utf-8"))

    def sanitize_token(value: str) -> str:
        token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
        token = token.strip(".-_")
        return token or "NA"

    def build_stage_context(*args, **kwargs):
        return None

    def apply_stage_context(manifest: dict, context: Any) -> dict:
        return manifest

# 添加conda环境bin目录到PATH
CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")


def open_text(path: Path, mode: str = "rt"):
    path = Path(path)
    if path.suffix == ".gz":
        return gzip.open(path, mode, encoding="utf-8", errors="ignore")
    return open(path, mode.replace("t", ""), encoding="utf-8", errors="ignore")


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


def current_input_dir(workspace: Path) -> Path:
    return workspace.parent.parent / "input" / workspace.name


def rebase_current_job_path(workspace: Path, raw_path: Any, input_dir: Optional[Path] = None) -> Optional[Path]:
    """Map copied-job paths from old scope/manifest records back into this job."""
    path_text = str(raw_path or "").strip()
    if not path_text:
        return None
    path = Path(path_text)
    input_dir = input_dir or current_input_dir(workspace)
    parts = list(path.parts)
    for marker, root in (("input", input_dir), ("workspace", workspace)):
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts) and parts[idx + 1] == workspace.name:
                candidate = root.joinpath(*parts[idx + 2 :])
                if candidate.exists():
                    return candidate

    if not path.is_absolute():
        for candidate in (Path.cwd() / path, input_dir / path, input_dir / path.name, workspace / path):
            if candidate.exists():
                return candidate

    for candidate in (
        input_dir / path.name,
        workspace / "final" / path.name,
        workspace / "karyotype" / "analysis" / path.name,
    ):
        if candidate.exists():
            return candidate

    if path.exists():
        return path
    return None


def rebase_job_path_text(workspace: Path, value: str) -> str:
    if not value:
        return value
    direct = rebase_current_job_path(workspace, value)
    if direct:
        return str(direct.absolute())

    job_id = re.escape(workspace.name)
    pattern = re.compile(rf"/[^\s\"']*?(?:input|workspace)/{job_id}/[^\s\"']+")

    def replace_match(match: re.Match[str]) -> str:
        original = match.group(0).rstrip(",)]}")
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
    
    # R
    try:
        result = subprocess.run(
            ["R", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        output = result.stdout.strip() or result.stderr.strip()
        versions["R"] = output.split('\n')[0] if output else "unknown"
    except Exception:
        versions["R"] = "not_found"
    
    # seqkit
    try:
        result = subprocess.run(
            ["seqkit", "version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        versions["seqkit"] = result.stdout.strip() if result.returncode == 0 else "not_found"
    except Exception:
        versions["seqkit"] = "not_found"
    
    return versions


def prepare_directories(workspace: Path) -> Dict[str, Path]:
    """创建标准目录结构"""
    analysis_dir = workspace / "karyotype" / "analysis"
    final_dir = workspace / "final"
    logs_dir = workspace / "logs"
    
    for d in [analysis_dir, final_dir, logs_dir]:
        d.mkdir(parents=True, exist_ok=True)
    
    return {
        "analysis_dir": analysis_dir,
        "final_dir": final_dir,
        "logs_dir": logs_dir
    }


def clean_id(value: Optional[str]) -> str:
    if value is None:
        return ""
    value = str(value).strip().strip('"').strip("'")
    for prefix in ("gene:", "transcript:", "rna-", "gene-"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value


def gene_like_id(value: Optional[str]) -> str:
    value = clean_id(value)
    if ".T" in value:
        head, tail = value.rsplit(".T", 1)
        if tail.isdigit():
            value = head
    for suffix in ("_T", "_P"):
        if suffix in value:
            head, tail = value.rsplit(suffix, 1)
            if tail.isdigit():
                value = head
    return value


def id_variants(value: Optional[str]) -> set:
    value = clean_id(value)
    variants = {value} if value else set()
    gene_like = gene_like_id(value)
    if gene_like:
        variants.add(gene_like)
    return variants


def parse_gff_attributes(attr: str) -> Dict[str, str]:
    result = {}
    for item in attr.strip().strip(';').split(';'):
        item = item.strip()
        if not item:
            continue
        if '=' in item:
            key, value = item.split('=', 1)
        elif ' ' in item:
            key, value = item.split(' ', 1)
        else:
            continue
        result[key.strip()] = value.strip().strip('"')
    return result


def read_target_ids(gene_id_file: Path) -> List[str]:
    ids = []
    seen = set()
    with open(gene_id_file, 'r') as f:
        for line in f:
            if not line.strip():
                continue
            raw = clean_id(line.strip().split('\t')[0])
            if raw and raw not in seen:
                ids.append(raw)
                seen.add(raw)
    return ids


def read_config_targets(input_dir: Path) -> Dict[str, Dict[str, Any]]:
    config_path = input_dir / "config.xlsx"
    targets: Dict[str, Dict[str, Any]] = {}
    if not config_path.exists():
        return targets
    try:
        import pandas as pd
        df = pd.read_excel(config_path)
    except Exception as exc:
        print(f"[WARN] Failed to read config.xlsx: {exc}")
        return targets
    required = {"Species_Code", "Gene_Family", "File_Type", "Ref"}
    if not required.issubset(set(df.columns)):
        print(f"[WARN] config.xlsx missing columns for karyotype discovery: {required - set(df.columns)}")
        return targets
    def clean_cell(value: Any) -> str:
        if value is None:
            return ""
        try:
            import pandas as pd
            if pd.isna(value):
                return ""
        except Exception:
            pass
        text = str(value).strip()
        return "" if text.lower() in {"nan", "none", "null"} else text

    for _, row in df.iterrows():
        ref_value = str(row.get("Ref", "")).strip().lower()
        if ref_value in {"true", "1", "yes", "y"}:
            continue
        species = clean_cell(row.get("Species_Code", ""))
        family = clean_cell(row.get("Gene_Family", ""))
        file_type = clean_cell(row.get("File_Type", "")).lower()
        if not species:
            continue
        targets.setdefault(species, {"species": species, "gene_family": family})
        if family:
            targets[species]["gene_family"] = family
        original_name = clean_cell(row.get("Original_File_Name", ""))
        if file_type:
            targets[species][file_type] = original_name
    return targets


def resolve_path(path_text: str, input_dir: Path, workspace: Optional[Path] = None) -> Optional[Path]:
    if not path_text:
        return None
    if workspace:
        rebased = rebase_current_job_path(workspace, path_text, input_dir=input_dir)
        if rebased:
            return rebased
    path = Path(path_text)
    if path.is_absolute():
        return path if path.exists() else None
    for candidate in (input_dir / path, input_dir / path.name):
        if candidate.exists():
            return candidate
    return None


def read_genome_prepare_artifacts(workspace: Path, input_dir: Path, prefix: str) -> Dict[str, Dict[str, Path]]:
    artifacts_by_species: Dict[str, Dict[str, Path]] = {}
    for manifest_path in (workspace / "final" / f"{prefix}.genome_prepare.manifest.json", input_dir / "final" / f"{prefix}.genome_prepare.manifest.json"):
        if not manifest_path.exists():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding='utf-8'))
        except Exception as exc:
            print(f"[WARN] Failed to read genome_prepare manifest {manifest_path}: {exc}")
            continue
        for key, artifact in (data.get("artifacts") or {}).items():
            artifact_kind = artifact.get("artifact")
            if artifact_kind not in {"gff", "genome"}:
                continue
            species = key.rsplit("_", 1)[0]
            found = resolve_path(artifact.get("full_path") or artifact.get("path") or "", input_dir, workspace=workspace)
            if found:
                artifacts_by_species.setdefault(species, {})[artifact_kind] = found
    return artifacts_by_species


def find_standardized_file(input_dir: Path, species: str, family: str, file_type: str, original_name: str = "") -> Optional[Path]:
    if file_type == "gff":
        patterns = [f"{species}.{family}_final.gff3", f"{species}.{family}.gff3", f"{species}.{family}.gff", f"{species}_final.gff3", f"{species}.gff3", f"{species}.gff"]
    else:
        patterns = [
            f"{species}.{family}.genome.fasta", f"{species}.{family}.genome.fa",
            f"{species}.{family}.genome.fasta.gz", f"{species}.{family}.genome.fa.gz",
            f"{species}.genome.fasta", f"{species}.genome.fa",
            f"{species}.genome.fasta.gz", f"{species}.genome.fa.gz",
            f"{species}_genome.fasta", f"{species}_genome.fa",
            f"{species}_genome.fasta.gz", f"{species}_genome.fa.gz",
            f"{species}.fasta", f"{species}.fa",
            f"{species}.fasta.gz", f"{species}.fa.gz",
        ]
    for pattern in patterns:
        for candidate in (input_dir / pattern, input_dir / species / pattern):
            if candidate.exists():
                return candidate
    if original_name:
        original = Path(original_name).name
        for candidate in (input_dir / original, input_dir / species / original):
            if candidate.exists():
                return candidate
    return None


def scope_file_record(workspace: Path, input_dir: Path, scope_data: Dict[str, Any], species: str, file_type: str) -> Optional[Path]:
    for record in ((scope_data.get("files") or {}).get(species) or {}).get(file_type, []) or []:
        path_text = record.get("path") or record.get("full_path") or ""
        if path_text:
            path = rebase_current_job_path(workspace, path_text, input_dir=input_dir) or Path(path_text)
            if path.exists():
                return path
    return None


def discover_species_inputs(workspace: Path, prefix: str, input_dir: Path, scope_data: Optional[Dict[str, Any]] = None, gene_id_file: Optional[Path] = None) -> Dict[str, Any]:
    scope_data = scope_data or {}
    gene_family = prefix.split('.', 1)[1] if '.' in prefix else prefix
    if scope_data.get("family"):
        gene_family = scope_data.get("family")
    config_targets = read_config_targets(input_dir)
    manifest_artifacts = read_genome_prepare_artifacts(workspace, input_dir, prefix)
    if scope_data:
        species_names = [str(x).strip() for x in (scope_data.get("target_species") or []) if str(x).strip()]
    else:
        species_names = sorted(set(config_targets) | set(manifest_artifacts))
    errors = []
    species_inputs = []
    gene_id_file = gene_id_file or workspace / "final" / f"{prefix}.final_members.id"
    if not gene_id_file.exists():
        errors.append(f"Gene ID file missing: {gene_id_file}")
    for species in species_names:
        info = config_targets.get(species, {"species": species, "gene_family": gene_family})
        family = info.get("gene_family") or gene_family
        artifact_paths = manifest_artifacts.get(species, {})
        scope_gff = scope_file_record(workspace, input_dir, scope_data, species, "gff") if scope_data else None
        scope_genome = scope_file_record(workspace, input_dir, scope_data, species, "genome") if scope_data else None
        gff_file = find_standardized_file(input_dir, species, family, "gff", info.get("gff", "")) or artifact_paths.get("gff") or scope_gff
        genome_file = find_standardized_file(input_dir, species, family, "genome", info.get("genome", "")) or artifact_paths.get("genome") or scope_genome
        if not gff_file or not gff_file.exists():
            errors.append(f"{species}: GFF file missing")
            continue
        if not genome_file or not genome_file.exists():
            errors.append(f"{species}: genome FASTA file missing")
            continue
        species_inputs.append({"species": species, "gene_family": family, "gff_file": gff_file, "genome_file": genome_file, "gene_id_file": gene_id_file, "tandem_file": workspace / "synteny" / "analysis" / f"{species}.tandem"})
    if not species_inputs and not errors:
        errors.append("No non-reference target species found from genome_prepare manifest or config.xlsx")
    return {"success": bool(species_inputs) and gene_id_file.exists(), "species_inputs": species_inputs, "errors": errors, "gene_family": gene_family, "gene_id_file": gene_id_file}


def build_simplified_gff(gff3_file: Path, output_gff: Path, target_ids: List[str]) -> Dict[str, Any]:
    target_variants = set()
    for target_id in target_ids:
        target_variants.update(id_variants(target_id))
    present_keys = set()
    rows = []
    allowed_features = {"mRNA", "transcript", "RNA", "gene"}
    with open_text(gff3_file, 'rt') as f:
        for line in f:
            if not line.strip() or line.startswith('#'):
                continue
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 9 or parts[2] not in allowed_features:
                continue
            attrs = parse_gff_attributes(parts[8])
            # NCBI-style GFF often stores the transcript accession used by
            # identify/synteny in transcript_id while ID is a protein accession.
            # Test every relevant attribute instead of stopping at the first one.
            candidates = []
            for key in ('ID', 'transcript_id', 'Name', 'Parent', 'gene_id', 'gene'):
                for variant in id_variants(attrs.get(key)):
                    if variant and variant not in candidates:
                        candidates.append(variant)
            matched = [candidate for candidate in candidates if candidate in target_variants]
            for gene_id in matched:
                key = (parts[0], gene_id, parts[3], parts[4])
                if key in present_keys:
                    continue
                rows.append(key)
                present_keys.add(key)
    output_gff.parent.mkdir(parents=True, exist_ok=True)
    with open(output_gff, 'w') as out:
        for chrom, gene_id, start, end in rows:
            out.write(f"{chrom}\t{gene_id}\t{start}\t{end}\n")
    return {"success": bool(rows), "gene_ids": sorted({row[1] for row in rows}), "row_count": len(rows)}


def prepare_gene_id_file_for_species(target_ids: List[str], available_ids: List[str], output_file: Path) -> Dict[str, Any]:
    available = set(available_ids)
    selected = []
    seen = set()
    for target_id in target_ids:
        candidates = [target_id] + sorted(v for v in id_variants(target_id) if v != target_id)
        for candidate in candidates:
            if candidate in available and candidate not in seen:
                selected.append(candidate)
                seen.add(candidate)
                break
    with open(output_file, 'w') as f:
        for gene_id in selected:
            f.write(f"{gene_id}\n")
    return {"success": bool(selected), "gene_count": len(selected), "gene_ids": selected}


def is_primary_chromosome_name(chrom: str) -> bool:
    name = str(chrom or "").strip()
    if re.fullmatch(r"\d+", name):
        return True
    return bool(re.fullmatch(r"chr(?:omosome)?[\s_.-]*\d+[A-Za-z]?", name, flags=re.IGNORECASE))


def is_obvious_non_nuclear_sequence(chrom: str) -> bool:
    name = str(chrom or "").strip().lower()
    if re.search(r"(chloroplast|plastid|mitochond|mitoch|scaffold|contig|unplaced|unlocalized|random)", name):
        return True
    return bool(re.fullmatch(r"(chrm|chrmt|mt|mito|chrc|chrpt|pt|cp)", name))


def select_primary_chromosomes(parsed: List[tuple]) -> tuple:
    records = []
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
            "chromosomes_total": total,
            "chromosomes_plotted": len(parsed),
            "chromosomes_filtered": 0,
            "chromosome_filter_mode": "fallback_unparsed_lengths",
            "chromosomes_filtered_names": [],
        }

    selected = []
    mode = ""
    named_primary = [(chrom, length) for chrom, length in records if is_primary_chromosome_name(chrom)]
    if named_primary:
        selected = named_primary
        mode = "primary_name"
    else:
        nuclear_candidates = [
            (chrom, length)
            for chrom, length in records
            if not is_obvious_non_nuclear_sequence(chrom)
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
        "chromosomes_total": total,
        "chromosomes_plotted": len(selected_text),
        "chromosomes_filtered": max(0, total - len(selected_text)),
        "chromosome_filter_mode": mode,
        "chromosomes_filtered_names": filtered_names,
    }


def generate_genome_len(genome_file: Path, output_file: Path, species_prefix: str) -> Dict[str, Any]:
    """Generate chromosome lengths; prefer primary nuclear chromosomes when detectable."""
    try:
        cmd = ["seqkit", "fx2tab", "-l", "-n", "-i", str(genome_file)]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return {"success": False, "stderr": result.stderr}
        parsed = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split('\t')
            if len(parts) >= 2:
                parsed.append((parts[0], parts[1]))
        chromosomes, filter_metrics = select_primary_chromosomes(parsed)
        with open(output_file, 'w') as f:
            for chrom, length in chromosomes:
                f.write(f"{chrom}\t{length}\n")
        return {
            "success": bool(chromosomes),
            "chromosome_count": len(chromosomes),
            "chromosomes": [chrom for chrom, _ in chromosomes],
            "stderr": "" if chromosomes else "No sequences found in genome FASTA",
            **filter_metrics,
        }
    except Exception as e:
        return {"success": False, "stderr": str(e)}


def prepare_gene_id_file(input_file: Path, output_file: Path) -> Dict[str, Any]:
    """准备基因ID文件，确保格式一致"""
    try:
        with open(input_file, 'r') as f:
            gene_ids = [line.strip() for line in f if line.strip()]
        
        with open(output_file, 'w') as f:
            for gene_id in gene_ids:
                f.write(f"{gene_id}\n")
        
        return {
            "success": True,
            "gene_count": len(gene_ids)
        }
        
    except Exception as e:
        return {"success": False, "stderr": str(e)}


def create_empty_tandem_file(output_file: Path) -> Dict[str, Any]:
    """创建空的串联重复文件"""
    try:
        output_file.touch()
        return {"success": True}
    except Exception as e:
        return {"success": False, "stderr": str(e)}


def summarize_family_tandem_genes(gene_id_file: Path, tandem_file: Path) -> Dict[str, Any]:
    """Summarize tandem genes that are also in the plotted family gene set."""
    family_ids: set[str] = set()
    tandem_pairs: List[List[str]] = []
    tandem_genes: set[str] = set()

    try:
        with open(gene_id_file, "r", encoding="utf-8", errors="ignore") as f:
            family_ids = {line.strip() for line in f if line.strip()}
    except Exception as e:
        return {
            "success": False,
            "tandem_count": 0,
            "tandem_pair_count": 0,
            "family_tandem_genes": [],
            "stderr": str(e),
        }

    try:
        if tandem_file.exists() and tandem_file.stat().st_size > 0:
            with open(tandem_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    parts = [part.strip() for part in re.split(r"[\t,]+", line.strip()) if part.strip()]
                    if len(parts) < 2:
                        continue
                    pair = parts[:2]
                    tandem_pairs.append(pair)
                    tandem_genes.update(pair)
    except Exception as e:
        return {
            "success": False,
            "tandem_count": 0,
            "tandem_pair_count": len(tandem_pairs),
            "family_tandem_genes": [],
            "stderr": str(e),
        }

    family_tandem_genes = sorted(family_ids & tandem_genes)
    return {
        "success": True,
        "tandem_count": len(family_tandem_genes),
        "tandem_pair_count": len(tandem_pairs),
        "family_tandem_genes": family_tandem_genes,
    }


def run_karyotype_plot(
    gff_file: Path,
    gene_id_file: Path,
    genome_len_file: Path,
    tandem_file: Path,
    output_pdf: Path,
    output_png: Path,
    label_gap_mb: int = 2
) -> Dict[str, Any]:
    """运行R脚本生成核型图"""
    
    # 查找R脚本
    r_script = Path("scripts/karyogram_gene_family.R")
    if not r_script.exists():
        return {"success": False, "stderr": f"R脚本不存在: {r_script}"}
    
    try:
        # 运行R脚本生成PDF
        cmd = [
            "Rscript", str(r_script),
            str(gff_file),
            str(gene_id_file),
            str(genome_len_file),
            str(tandem_file),
            str(output_pdf),
            str(label_gap_mb)
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=300
        )
        
        if result.returncode != 0:
            return {
                "success": False,
                "stderr": result.stderr,
                "stdout": result.stdout
            }
        
        # 检查PDF是否生成
        if not output_pdf.exists():
            return {
                "success": False,
                "stderr": "PDF文件未生成"
            }
        
        # 生成PNG（使用pdftoppm或ImageMagick）
        png_result = convert_pdf_to_png(output_pdf, output_png)
        
        return {
            "success": True,
            "pdf_file": str(output_pdf),
            "png_file": str(output_png) if output_png.exists() else None,
            "png_conversion": png_result
        }
        
    except Exception as e:
        return {"success": False, "stderr": str(e)}


def convert_pdf_to_png(pdf_file: Path, png_file: Path, dpi: int = 300) -> Dict[str, Any]:
    """将PDF转换为PNG"""
    try:
        # 方法1: 使用 pdftoppm
        cmd = [
            "pdftoppm",
            "-png",
            "-r", str(dpi),
            "-singlefile",
            str(pdf_file),
            str(png_file.with_suffix(''))
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        
        if result.returncode == 0 and png_file.exists():
            return {"success": True, "method": "pdftoppm"}
        
        # 方法2: 使用 ImageMagick
        cmd = [
            "convert",
            "-density", str(dpi),
            str(pdf_file),
            str(png_file)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        
        if result.returncode == 0 and png_file.exists():
            return {"success": True, "method": "convert"}
        
        return {"success": False, "stderr": "PDF转PNG失败"}
        
    except Exception as e:
        return {"success": False, "stderr": str(e)}


def write_report(
    workspace: Path,
    prefix: str,
    metrics: Dict[str, Any],
    parameters: Dict[str, Any],
    tool_versions: Dict[str, str],
    plot_result: Dict[str, Any]
) -> Path:
    """写入人可读的分析报告"""
    report_path = workspace / "final" / f"{prefix}.karyotype_report.md"
    
    print(f"生成分析报告: {report_path}")
    
    with open(report_path, "w") as f:
        f.write(f"# 核型分析报告 - {prefix}\n\n")
        f.write(f"**生成时间**: {datetime.now().isoformat()}\n\n")
        
        f.write("## 1. 分析概述\n")
        f.write(f"- **分析阶段**: Karyotype\n")
        f.write(f"- **分析状态**: {'成功' if plot_result.get('success', False) else '失败'}\n")
        f.write(f"- **染色体数量**: {metrics.get('chromosome_count', 'N/A')}\n")
        f.write(f"- **家族基因数量**: {metrics.get('gene_count', 'N/A')}\n")
        f.write(f"- **串联重复基因**: {metrics.get('tandem_count', 'N/A')}\n\n")
        
        f.write("## 2. 工具版本\n")
        for tool, version in tool_versions.items():
            f.write(f"- **{tool}**: {version}\n")
        f.write("\n")
        
        f.write("## 3. 执行参数\n")
        for param, value in parameters.items():
            f.write(f"- **{param}**: {value}\n")
        f.write("\n")
        
        f.write("## 4. 染色体分布\n")
        if "chromosomes" in metrics:
            for chr_name in metrics["chromosomes"]:
                f.write(f"- **{chr_name}**\n")
        f.write("\n")
        
        f.write("## 5. 输出文件\n")
        if plot_result.get('pdf_file'):
            f.write(f"- **PDF图**: {plot_result['pdf_file']}\n")
        if plot_result.get('png_file'):
            f.write(f"- **PNG图**: {plot_result['png_file']}\n")
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
    status: str = "success"
) -> Path:
    """写入符合schema v1的manifest文件"""
    elapsed_sec = (end_time - start_time).total_seconds()
    rebased_artifacts = rebase_manifest_payload_paths(workspace, artifacts)
    rebased_metrics = rebase_manifest_payload_paths(workspace, metrics)
    rebased_parameters = rebase_manifest_payload_paths(workspace, parameters)
    
    manifest_data = {
        "manifest_version": "1.0",
        "job_id": job_id,
        "stage": "karyotype",
        "run_id": run_id,
        "prefix": prefix,
        
        "timestamp": end_time.isoformat(),
        "status": status,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "elapsed_sec": round(elapsed_sec, 2),
        
        "tool_versions": tool_versions,
        
        "workspace_root": str(workspace.parent.absolute()),
        "workspace": str((workspace / "final").absolute()),
        "input_dir": str(current_input_dir(workspace).absolute()),
        
        "artifacts": rebased_artifacts,
        "parameters": rebased_parameters,
        "metrics": rebased_metrics,
        
        "provenance": {
            "derived_from": f"{prefix}.synteny.manifest.json",
            "stage_sequence": ["identify", "synteny", "karyotype"]
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

    if rebased_parameters.get("scope_id"):
        scope_data = {
            "scope_id": rebased_parameters.get("scope_id", ""),
            "scope_mode": rebased_parameters.get("scope_mode", ""),
            "family": rebased_parameters.get("family", ""),
            "target_species": rebased_parameters.get("target_species", []),
            "reference_species": rebased_parameters.get("reference_species", []),
            "comparative_species": rebased_parameters.get("comparative_species", []),
            "selected_species": rebased_parameters.get("selected_species", []),
        }
        context = build_stage_context(
            stage="karyotype",
            prefix=prefix,
            scope_data=scope_data,
            scope_file=rebased_parameters.get("scope_file", ""),
            output_prefix=prefix,
            analysis_route=rebased_parameters.get("analysis_route", ""),
            member_set_id=rebased_parameters.get("member_set_id", ""),
        )
        if context:
            manifest_data = apply_stage_context(manifest_data, context)
    
    manifest_path = workspace / "final" / f"{prefix}.karyotype.manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"生成Manifest文件: {manifest_path}")
    
    with open(manifest_path, "w", encoding='utf-8') as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)
    if rebased_parameters.get("scope_id"):
        scoped_manifest_path = workspace / "final" / f"{prefix}.{sanitize_token(rebased_parameters.get('scope_id', ''))}.karyotype.manifest.json"
        manifest_data["scope_manifest"] = str(scoped_manifest_path.absolute())
        with open(scoped_manifest_path, "w", encoding='utf-8') as f:
            json.dump(manifest_data, f, indent=2, ensure_ascii=False)
    
    return manifest_path


def main():
    """主函数：执行Karyotype分析流程"""
    parser = argparse.ArgumentParser(
        description="Karyotype阶段：绘制基因家族在染色体上的分布图",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python run_karyotype.py --prefix Ft.SPL --workspace /path/to/workspace

输入要求：
  - Synteny阶段必须已成功运行（提供GFF文件）
  - Identify阶段必须已成功运行（提供基因ID列表）

输出产物：
  - final/{prefix}.karyotype.pdf (核型图PDF)
  - final/{prefix}.karyotype.png (核型图PNG)
  - final/{prefix}.karyotype_report.md (分析报告)
  - final/{prefix}.karyotype.manifest.json (机器清单)
        """
    )
    
    parser.add_argument("--prefix", required=True,
                        help="分析前缀，格式：物种.家族（如 Ft.SPL）")
    parser.add_argument("--workspace", required=True,
                        help="工作空间目录（workspace/<job_id>/)")
    parser.add_argument("--input-dir",
                        help="输入目录（默认：workspace/<job_id>/../input/<job_id>）")
    parser.add_argument("--label-gap-mb", type=int, default=2,
                        help="标签最小间距（单位Mb，默认：2）")
    parser.add_argument("--log-file",
                        help="日志文件路径")
    
    # 前端兼容性参数（保留但不使用，自动推断）
    parser.add_argument("--cpu", type=int, default=8,
                        help="CPU线程数（兼容性参数，当前版本自动推断）")
    parser.add_argument("--gff3", dest="gff3_file",
                        help="GFF3文件路径（兼容性参数，当前版本自动推断）")
    parser.add_argument("--genome", dest="genome_file",
                        help="基因组文件路径（兼容性参数，当前版本自动推断）")
    parser.add_argument("--gene-id-file", dest="gene_id_file",
                        help="基因ID文件路径（兼容性参数，当前版本自动推断）")
    parser.add_argument("--scope-file",
                        help="Gene-family scope JSON from resolve_scope.py")
    parser.add_argument("--analysis-route",
                        help="Scoped analysis route recorded in manifest, e.g. single_species")
    parser.add_argument("--member-set-id",
                        help="Scoped member set identity recorded in manifest")
    
    args = parser.parse_args()
    
    # 记录开始时间
    start_time = datetime.now()
    run_id = f"karyotype_{start_time.strftime('%Y%m%d_%H%M%S_%f')[:-3]}"
    
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
    scope_file = ""
    analysis_route = str(args.analysis_route or "").strip()
    member_set_id = str(args.member_set_id or "").strip()
    if args.scope_file:
        scope_path = Path(args.scope_file)
        if not scope_path.exists():
            print(f"[ERROR] Scope file not found: {args.scope_file}")
            scope_data = {}
        else:
            scope_file = str(scope_path.absolute())
            scope_data = read_scope(scope_path)
            if not analysis_route:
                analysis_route = "single_species"
            if not member_set_id and scope_data.get("scope_id") and analysis_route:
                member_set_id = f"{scope_data.get('scope_id')}:{analysis_route}:all_members"
    
    print("=" * 60)
    print(f"🚀 启动核型分析")
    print("=" * 60)
    print(f"分析前缀: {args.prefix}")
    print(f"工作空间: {workspace}")
    print(f"任务ID: {job_id}")
    print(f"运行ID: {run_id}")
    
    try:
        # 1. 准备目录结构
        print("\n📁 准备目录结构...")
        paths = prepare_directories(workspace)
        
        # 2. 获取工具版本
        print("\n🔧 检查工具版本...")
        tool_versions = get_tool_versions()
        for tool, version in tool_versions.items():
            print(f"   {tool}: {version}")
        
        # 3. Discover per-species input files
        print("\nDiscovering target species input files...")
        gene_id_override = Path(args.gene_id_file) if args.gene_id_file else None
        discovery = discover_species_inputs(workspace, args.prefix, input_dir, scope_data=scope_data, gene_id_file=gene_id_override)
        if discovery["errors"]:
            for error in discovery["errors"]:
                print(f"   - {error}")
        if not discovery["success"]:
            raise RuntimeError("Input files missing")

        species_inputs = discovery["species_inputs"]
        target_ids = read_target_ids(Path(discovery["gene_id_file"]))
        print(f"   Target species: {', '.join(item['species'] for item in species_inputs)}")
        print(f"   Target ID count: {len(target_ids)}")

        artifacts = {}
        species_metrics = {}
        success_count = 0
        failure_count = 0

        for species_input in species_inputs:
            species = species_input["species"]
            family = species_input["gene_family"]
            species_run_prefix = f"{species}.{family}"
            print("\n" + "-" * 60)
            print(f"Processing species: {species} ({species_run_prefix})")
            print(f"   GFF3: {species_input['gff_file']}")
            print(f"   Genome: {species_input['genome_file']}")

            try:
                work_gff = paths["analysis_dir"] / f"{species_run_prefix}.gff"
                gff_result = build_simplified_gff(species_input["gff_file"], work_gff, target_ids)
                if not gff_result["success"]:
                    raise RuntimeError("No target gene/transcript IDs were found in GFF")
                print(f"   Simplified GFF: {work_gff} ({gff_result['row_count']} rows)")

                work_gene_id = paths["analysis_dir"] / f"{species_run_prefix}.geneID"
                gene_id_result = prepare_gene_id_file_for_species(target_ids, gff_result["gene_ids"], work_gene_id)
                if not gene_id_result["success"]:
                    raise RuntimeError("Target IDs did not match simplified GFF")
                print(f"   Gene ID file: {work_gene_id} ({gene_id_result['gene_count']} genes)")

                genome_len_file = paths["analysis_dir"] / f"{species}.genome.len"
                genome_result = generate_genome_len(species_input["genome_file"], genome_len_file, species)
                if not genome_result["success"]:
                    raise RuntimeError(f"Failed to generate chromosome lengths: {genome_result.get('stderr')}")
                print(f"   Genome lengths: {genome_len_file} ({genome_result['chromosome_count']} chromosomes)")

                work_tandem = paths["analysis_dir"] / f"{species}.tandem"
                if species_input.get("tandem_file") and species_input["tandem_file"].exists():
                    shutil.copy(species_input["tandem_file"], work_tandem)
                else:
                    create_empty_tandem_file(work_tandem)

                tandem_summary = summarize_family_tandem_genes(work_gene_id, work_tandem)
                print(
                    f"   Tandem family genes: {tandem_summary.get('tandem_count', 0)} "
                    f"from {tandem_summary.get('tandem_pair_count', 0)} tandem pairs"
                )

                output_pdf = paths["analysis_dir"] / f"{species_run_prefix}.karyotype.pdf"
                output_png = paths["analysis_dir"] / f"{species_run_prefix}.karyotype.png"
                plot_result = run_karyotype_plot(work_gff, work_gene_id, genome_len_file, work_tandem, output_pdf, output_png, args.label_gap_mb)
                if not plot_result["success"]:
                    raise RuntimeError(plot_result.get("stderr", "Karyotype plotting failed"))

                final_pdf = paths["final_dir"] / f"{species_run_prefix}.karyotype.pdf"
                final_png = paths["final_dir"] / f"{species_run_prefix}.karyotype.png"
                shutil.copy(output_pdf, final_pdf)
                if output_png.exists():
                    shutil.copy(output_png, final_png)

                metrics_one = {
                    "species": species,
                    "prefix": species_run_prefix,
                    "chromosome_count": genome_result["chromosome_count"],
                    "gene_count": gene_id_result["gene_count"],
                    "tandem_count": tandem_summary.get("tandem_count", 0),
                    "tandem_pair_count": tandem_summary.get("tandem_pair_count", 0),
                    "family_tandem_genes": tandem_summary.get("family_tandem_genes", []),
                    "chromosomes": genome_result.get("chromosomes", []),
                    "chromosomes_total": genome_result.get("chromosomes_total"),
                    "chromosomes_plotted": genome_result.get("chromosomes_plotted"),
                    "chromosomes_filtered": genome_result.get("chromosomes_filtered"),
                    "chromosome_filter_mode": genome_result.get("chromosome_filter_mode"),
                    "chromosomes_filtered_names": genome_result.get("chromosomes_filtered_names", []),
                    "label_gap_mb": args.label_gap_mb,
                    "simplified_gff_rows": gff_result["row_count"],
                }
                report_path = write_report(workspace, species_run_prefix, metrics_one, {"prefix": args.prefix, "species_prefix": species, "label_gap_mb": args.label_gap_mb, "stage": "karyotype", "job_id": job_id, "run_id": run_id}, tool_versions, plot_result)

                if final_pdf.exists():
                    artifacts[f"{species}_karyotype_pdf"] = {"artifact": "karyotype_pdf", "species": species, "path": f"final/{species_run_prefix}.karyotype.pdf", "full_path": str(final_pdf.absolute()), "type": "image", "format": "pdf", "size": final_pdf.stat().st_size, "sha256": compute_file_hash(final_pdf)}
                if final_png.exists():
                    artifacts[f"{species}_karyotype_png"] = {"artifact": "karyotype_png", "species": species, "path": f"final/{species_run_prefix}.karyotype.png", "full_path": str(final_png.absolute()), "type": "image", "format": "png", "size": final_png.stat().st_size, "sha256": compute_file_hash(final_png)}
                artifacts[f"{species}_report"] = {"artifact": "report", "species": species, "path": f"final/{species_run_prefix}.karyotype_report.md", "full_path": str(report_path.absolute()), "type": "markdown", "size": report_path.stat().st_size, "sha256": compute_file_hash(report_path)}
                species_metrics[species] = metrics_one
                success_count += 1
                print(f"   {species} karyotype complete")
            except Exception as species_error:
                failure_count += 1
                species_metrics[species] = {"species": species, "status": "failed", "error": str(species_error)}
                print(f"   {species} karyotype failed: {species_error}")

        status = "success" if success_count == len(species_inputs) else ("partial_success" if success_count > 0 else "failed")
        metrics = {"species_count": len(species_inputs), "success_count": success_count, "failure_count": failure_count, "species": species_metrics, "label_gap_mb": args.label_gap_mb}
        parameters = {"prefix": args.prefix, "gene_family": discovery.get("gene_family"), "stage": "karyotype", "job_id": job_id, "run_id": run_id, "input_source": "scope" if scope_data else "genome_prepare_manifest_or_config"}
        if scope_data:
            parameters.update({
                "scope_file": scope_file,
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
        end_time = datetime.now()
        manifest_path = write_manifest(workspace, job_id, run_id, args.prefix, start_time, end_time, artifacts, metrics, parameters, tool_versions, status=status)
        artifacts["manifest"] = {"artifact": "manifest", "path": f"final/{args.prefix}.karyotype.manifest.json", "full_path": str(manifest_path.absolute()), "type": "json", "size": manifest_path.stat().st_size, "sha256": compute_file_hash(manifest_path)}
        write_manifest(workspace, job_id, run_id, args.prefix, start_time, end_time, artifacts, metrics, parameters, tool_versions, status=status)

        print("\n" + "=" * 60)
        print(f"Karyotype analysis finished with status: {status}")
        print(f"Successful species: {success_count}; failed species: {failure_count}")
        print(f"Manifest: {manifest_path}")
        print("=" * 60)

        return 0 if success_count > 0 else 1
        
    except Exception as e:
        print(f"\n❌ 核型分析失败: {e}")
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
                "stage": "karyotype",
                "scope_file": scope_file,
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
            status="failed"
        )
        
        return 1


if __name__ == "__main__":
    sys.exit(main())
