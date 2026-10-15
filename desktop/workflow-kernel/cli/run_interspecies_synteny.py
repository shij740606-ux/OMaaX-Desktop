#!/usr/bin/env python3
"""
Interspecies Synteny阶段：物种间共线性分析（完整版）

输入：
  - 参考物种蛋白序列 (final/{prefix}.final_members.pep.fa)
  - 参考物种GFF3
  - 目标物种蛋白序列
  - 目标物种GFF3

输出：
  - final/{prefix}.interspecies_tree.nwk (物种间进化树)
  - final/{prefix}.interspecies_tree.pdf/png (进化树图)
  - final/{prefix}.interspecies_synteny.pdf/png (共线性图)
  - final/{prefix}.interspecies_synteny_report.md (分析报告)
  - final/{prefix}.interspecies_synteny.manifest.json (机器清单)
"""

import os
import argparse
import json
import hashlib
import subprocess
import sys
import time
import shutil
import re
import gzip
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

try:
    from gfa_core.gene_family_scope import read_scope, sanitize_token
    from gfa_core.gene_family_stage_context import apply_stage_context, build_stage_context, infer_analysis_route, member_set_id_for
except Exception:
    def read_scope(scope_path):
        return json.loads(Path(scope_path).read_text(encoding="utf-8"))

    def sanitize_token(value: str) -> str:
        token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
        token = token.strip(".-_")
        return token or "NA"

    def infer_analysis_route(stage: str, scope_data: Dict[str, Any] | None = None) -> str:
        scope_data = scope_data or {}
        return "multi_species" if scope_data.get("reference_species") else "unscoped"

    def member_set_id_for(prefix: str, scope_data: Dict[str, Any] | None, analysis_route: str) -> str:
        scope_id = str((scope_data or {}).get("scope_id") or "").strip()
        return f"{sanitize_token(scope_id or prefix)}:{analysis_route}:all_members"

    def build_stage_context(*args, **kwargs):
        return None

    def apply_stage_context(manifest: dict, context: Any) -> dict:
        return manifest


# ??conda??bin???PATH
CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")


def open_text(path: Path, mode: str = "rt"):
    path = Path(path)
    if path.suffix == ".gz":
        return gzip.open(path, mode, encoding="utf-8", errors="ignore")
    return open(path, mode.replace("t", ""), encoding="utf-8", errors="ignore")


def materialize_plain_fasta(source: Path, output_dir: Path, label: str) -> Path:
    """Return a plain, search-safe protein FASTA for DIAMOND/BLASTP."""
    source = Path(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = source.name[:-3] if source.suffix.lower() == ".gz" else source.name
    stem = stem or f"{sanitize_token(label)}.fa"
    dest = output_dir / f"{sanitize_token(label)}.{stem}"
    if dest.exists() and dest.stat().st_size > 0 and dest.stat().st_mtime >= source.stat().st_mtime:
        return dest
    print(f"  Materializing search-safe protein FASTA: {source} -> {dest}")
    replacements = 0
    sequence_lines = 0
    valid_letters = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    with open_text(source, "rt") as src, dest.open("w", encoding="utf-8") as dst:
        for line in src:
            if line.startswith(">"):
                dst.write(line.rstrip("\n") + "\n")
                continue
            cleaned = []
            for char in line.strip().upper():
                if not char:
                    continue
                if char in valid_letters:
                    cleaned.append(char)
                else:
                    cleaned.append("X")
                    replacements += 1
            if cleaned:
                dst.write("".join(cleaned) + "\n")
                sequence_lines += 1
    if not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError(f"Failed to materialize protein FASTA: {source}")
    if replacements:
        print(f"  Replaced {replacements} non-letter protein character(s) with X in {dest.name}")
    if sequence_lines == 0:
        raise RuntimeError(f"No sequence lines found in protein FASTA: {source}")
    return dest


def pdf_to_png(pdf_path: Path, png_path: Path, dpi: int = 300) -> bool:
    """将PDF转换为PNG"""
    try:
        # 方法1: 使用pdftoppm (poppler-utils)
        cmd = ["pdftoppm", "-png", "-r", str(dpi), "-singlefile", 
               str(pdf_path), str(png_path.with_suffix(''))]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return True
    except Exception:
        pass
    
    try:
        # 方法2: 使用convert (ImageMagick)
        cmd = ["convert", "-density", str(dpi), str(pdf_path), "-quality", "100", str(png_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return True
    except Exception:
        pass
    
    return False


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


def looks_like_path_candidate(value: str) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 2048 or any(ch in text for ch in "\r\n\t"):
        return False
    if text.startswith(("/", "./", "../", "~")) or "/" in text or "\\" in text:
        return True
    if any(ch.isspace() for ch in text):
        return False
    return bool(Path(text).suffix)


def rebase_current_job_path(workspace: Path, raw_path: Any, input_dir: Optional[Path] = None) -> Optional[Path]:
    path_text = str(raw_path or "").strip()
    if not looks_like_path_candidate(path_text):
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
        workspace / "interspecies_synteny" / "analysis" / path.name,
    ):
        if candidate.exists():
            return candidate
    if path.exists():
        return path
    return None


def rebase_job_path_text(workspace: Path, value: str) -> str:
    if not value:
        return value
    if looks_like_path_candidate(value):
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


def summarize_process_result(result: subprocess.CompletedProcess) -> Dict[str, Any]:
    """Keep process diagnostics compact and avoid persisting wrapped absolute paths."""
    def clean_lines(text: str) -> List[str]:
        cleaned: List[str] = []
        for line in (text or "").splitlines():
            if any(marker in line for marker in ("/data_old", ".bak", "bbav5_0502_Yang_Tihuan.7880")):
                continue
            stripped = line.strip()
            if stripped:
                cleaned.append(stripped)
        return cleaned[-20:]

    stdout = "\n".join(clean_lines(result.stdout))
    stderr = "\n".join(clean_lines(result.stderr))
    return {
        "returncode": result.returncode,
        "stdout_tail": stdout[:2000],
        "stderr_tail": stderr[:2000],
    }


def resolve_iqtree_executable() -> str:
    """Resolve IQ-TREE consistently with the phylogeny stage."""
    candidates = [
        Path(sys.executable).resolve().parent / "iqtree3",
        Path(sys.executable).resolve().parent / "iqtree2",
        shutil.which("iqtree3"),
        shutil.which("iqtree2"),
        shutil.which("iqtree"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(str(candidate))
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
        if isinstance(candidate, str) and shutil.which(candidate):
            return str(candidate)
    return "iqtree3"


def get_tool_versions() -> Dict[str, str]:
    """获取所需工具版本信息"""
    versions = {}
    
    for tool, cmd in [
        ("mafft", ["mafft", "--version"]),
        ("iqtree", [resolve_iqtree_executable(), "--version"]),
        ("diamond", ["diamond", "--version"]),
        ("blastp", ["blastp", "-version"]),
        ("MCScanX", ["MCScanX", "-h"]),
    ]:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=5)
            versions[tool] = "available" if result.returncode in [0, 1] else "not_found"
        except Exception:
            versions[tool] = "not_found"
    
    return versions


def prepare_directories(workspace: Path) -> Dict[str, Path]:
    """创建目录结构"""
    paths = {
        "analysis_dir": workspace / "interspecies_synteny" / "analysis",
        "final_dir": workspace / "final",
        "logs_dir": workspace / "logs",
    }
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def merge_protein_sequences(
    target_protein: Path,
    ref_protein: Path,
    output_fasta: Path
) -> Dict[str, Any]:
    """Merge target and reference family proteins for the interspecies tree."""
    print("Merging family protein sequences...")

    try:
        with open(output_fasta, 'w') as outfile:
            with open_text(target_protein, 'rt') as f:
                outfile.write(f.read().rstrip() + "\n")
            with open_text(ref_protein, 'rt') as f:
                outfile.write(f.read().rstrip() + "\n")

        target_count = sum(1 for line in open_text(target_protein, 'rt') if line.startswith('>'))
        ref_count = sum(1 for line in open_text(ref_protein, 'rt') if line.startswith('>'))

        print(f"  merged target/ref family proteins: {target_count} + {ref_count} = {target_count + ref_count}")

        return {
            "success": True,
            "output_file": output_fasta,
            "target_count": target_count,
            "ref_count": ref_count
        }
    except Exception as e:
        return {"success": False, "stderr": str(e)}


def build_phylogenetic_tree(
    merged_fasta: Path,
    output_dir: Path,
    prefix: str,
    cpu: int = 8
) -> Dict[str, Any]:
    """构建系统发育树 (MAFFT + IQ-TREE)"""
    print(f"构建进化树...")
    expected_tips = sum(1 for line in open_text(merged_fasta, "rt") if line.startswith(">"))
    
    # MAFFT比对
    msa_file = output_dir / f"{prefix}_interspecies.mafft.msa.fa"
    mafft_cmd = ["mafft", "--auto", "--thread", str(cpu), str(merged_fasta)]
    
    try:
        with open(msa_file, "w") as f_out:
            result = subprocess.run(mafft_cmd, stdout=f_out, stderr=subprocess.PIPE, 
                                   text=True, check=False)
        
        if result.returncode != 0:
            return {"success": False, "error": f"MAFFT失败: {result.stderr}"}
        
        # IQ-TREE建树
        base_name = output_dir / f"{prefix}_interspecies.iqtree"
        tree_file = Path(f"{base_name}.treefile")
        existing_tips = _read_tree_tip_labels(tree_file) if tree_file.exists() else set()
        if existing_tips and len(existing_tips) != expected_tips:
            print(
                f"  Removing stale IQ-TREE outputs ({len(existing_tips)} tips != {expected_tips} merged sequences)"
            )
            for stale in output_dir.glob(f"{prefix}_interspecies.iqtree*"):
                try:
                    stale.unlink()
                except OSError:
                    pass
        iqtree_cmd = [
            resolve_iqtree_executable(), "-s", str(msa_file), "-m", "MFP", "-bb", "1000",
            "-nt", str(cpu), "-pre", str(base_name)
        ]
        
        result = subprocess.run(iqtree_cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return {"success": False, "error": f"IQ-TREE失败: {result.stderr or result.stdout}"}
        
        if not tree_file.exists():
            return {"success": False, "error": "IQ-TREE未生成 treefile"}
        built_tips = _read_tree_tip_labels(tree_file)
        if len(built_tips) != expected_tips:
            return {
                "success": False,
                "error": f"IQ-TREE tree tip count ({len(built_tips)}) != merged sequence count ({expected_tips})",
            }
        
        return {
            "success": True,
            "tree_file": tree_file,
            "msa_file": msa_file,
            "tip_count": len(built_tips),
            "iqtree_executable": iqtree_cmd[0],
            "command": " ".join(iqtree_cmd),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def filter_gff_files(
    ref_gff3: Path,
    target_gff3: Path,
    output_dir: Path,
    ref_species: str,
    target_species: str
) -> Dict[str, Any]:
    """过滤GFF3文件 - 提取基因ID（去掉转录本版本后缀如 .1, .2）"""
    print(f"过滤GFF3文件...")
    
    import re
    
    results = {}
    for gff3_file, species, prefix in [
        (ref_gff3, ref_species, "ref"),
        (target_gff3, target_species, "target")
    ]:
        output_gff = output_dir / f"{species}.gff"
        
        try:
            with open_text(gff3_file, 'rt') as f:
                lines = f.readlines()
            
            count = 0
            seen_genes = set()  # 去重
            
            with open(output_gff, 'w') as f:
                for line in lines:
                    parts = line.strip().split('\t')
                    if len(parts) >= 9 and parts[2] == "mRNA":
                        attr = parts[8]
                        # 提取ID（如 ID=AT2G47070.1），保留版本号与BLAST结果匹配
                        gene_id = attr.split(';')[0].replace('ID=', '')
                        
                        # 处理染色体名称：去掉 "Chr" 前缀（Ath用1,2,3而非Chr1,Chr2）
                        chr_name = parts[0]
                        if chr_name.startswith('Chr'):
                            chr_name = chr_name[3:]  # Chr1 -> 1
                        
                        # 去重：一个基因可能有多个转录本
                        if gene_id not in seen_genes:
                            seen_genes.add(gene_id)
                            f.write(f"{chr_name}\t{gene_id}\t{parts[3]}\t{parts[4]}\n")
                            count += 1
            
            print(f"  {species}: {count} 个基因（去重后）")
            results[prefix] = {"success": True, "output_file": output_gff, "gene_count": count}
        except Exception as e:
            results[prefix] = {"success": False, "stderr": str(e)}
    
    if results.get("ref", {}).get("success") and results.get("target", {}).get("success"):
        return {
            "success": True,
            "ref_gff": results["ref"]["output_file"],
            "target_gff": results["target"]["output_file"]
        }
    return {"success": False}


def run_reciprocal_blast(
    ref_protein: Path,
    target_protein: Path,
    output_dir: Path,
    ref_species: str,
    target_species: str,
    cpu: int = 8
) -> Dict[str, Any]:
    """双向BLAST比对 - 结果中基因名去掉版本后缀以匹配GFF"""
    import re
    print(f"双向BLAST比对...")
    
    # 构建数据库
    for protein_file in [ref_protein, target_protein]:
        subprocess.run(["makeblastdb", "-in", str(protein_file), "-dbtype", "prot"],
                      capture_output=True, check=False)
    
    # 双向blastp
    blast1_out = output_dir / f"tmp.{ref_species}_{target_species}.blast"
    subprocess.run([
        "blastp", "-query", str(ref_protein), "-db", str(target_protein),
        "-outfmt", "6", "-out", str(blast1_out), "-evalue", "1e-10",
        "-num_alignments", "5", "-num_threads", str(cpu)
    ], capture_output=True, check=False)
    
    blast2_out = output_dir / f"tmp.{target_species}_{ref_species}.blast"
    subprocess.run([
        "blastp", "-query", str(target_protein), "-db", str(ref_protein),
        "-outfmt", "6", "-out", str(blast2_out), "-evalue", "1e-10",
        "-num_alignments", "5", "-num_threads", str(cpu)
    ], capture_output=True, check=False)
    
    # 合并BLAST结果（保留版本号与GFF匹配）
    merged_blast = output_dir / f"{ref_species}_{target_species}.blast"
    with open(merged_blast, 'w') as outfile:
        for blast_file in [blast1_out, blast2_out]:
            if blast_file.exists():
                with open(blast_file, 'r') as infile:
                    for line in infile:
                        outfile.write(line)  # 直接写入，保留版本号
    
    return {"success": True, "output_file": merged_blast}


def run_mcscanx_interspecies(
    work_dir: Path,
    ref_species: str,
    target_species: str
) -> Dict[str, Any]:
    """MCScanX种间共线性 - 需要合并的GFF文件"""
    print(f"MCScanX种间共线性...")
    
    prefix = f"{ref_species}_{target_species}"
    
    # MCScanX 需要合并的 GFF 文件 (prefix.gff)
    merged_gff = work_dir / f"{prefix}.gff"
    ref_gff = work_dir / f"{ref_species}.gff"
    target_gff = work_dir / f"{target_species}.gff"
    
    if not merged_gff.exists() and ref_gff.exists() and target_gff.exists():
        print(f"  合并 GFF 文件: {ref_gff.name} + {target_gff.name} -> {merged_gff.name}")
        with open(merged_gff, 'w') as outfile:
            for gff_file in [target_gff, ref_gff]:  # 目标物种在前，参考物种在后
                with open(gff_file, 'r') as infile:
                    outfile.write(infile.read())
    
    cmd = ["MCScanX", f"./{prefix}"]
    
    result = subprocess.run(cmd, capture_output=True, text=True, 
                           cwd=str(work_dir), check=False)
    
    collinearity_file = work_dir / f"{prefix}.collinearity"
    
    return {
        "success": collinearity_file.exists(),
        "collinearity_file": collinearity_file if collinearity_file.exists() else None
    }




def fasta_ids(fasta_file: Path) -> List[str]:
    ids = []
    with open_text(fasta_file, "rt") as f:
        for line in f:
            if line.startswith(">"):
                ids.append(line[1:].strip().split()[0])
    return ids


def display_species_code(species: str, gene_id: str = "") -> str:
    if re.match(r"^AT[1-5CM]G", gene_id, re.IGNORECASE):
        return "AT"
    return species


def species_for_gene(gene_id: str, species_list: List[str], default_species: str) -> str:
    for species in sorted(species_list, key=len, reverse=True):
        if gene_id.lower().startswith(species.lower()):
            return display_species_code(species, gene_id)
    if re.match(r"^AT[1-5CM]G", gene_id, re.IGNORECASE):
        return "AT"
    return display_species_code(default_species, gene_id)


def write_gene_species_map(target_fasta: Path, ref_fasta: Path, target_species: List[str], ref_species: List[str], output_file: Path) -> Path:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    default_target = target_species[0] if target_species else "Target"
    default_ref = ref_species[0] if ref_species else "Ref"
    with open(output_file, "w") as out:
        out.write("gene_id\tspecies\tgroup\n")
        for gene_id in fasta_ids(target_fasta):
            out.write(f"{gene_id}\t{species_for_gene(gene_id, target_species, default_target)}\ttarget\n")
        for gene_id in fasta_ids(ref_fasta):
            out.write(f"{gene_id}\t{species_for_gene(gene_id, ref_species, default_ref)}\tref\n")
    return output_file
def normalize_seq_id(text: str) -> str:
    """Normalize FASTA/GFF IDs while preserving biologically meaningful versions."""
    value = (text or "").strip()
    if value.startswith(">"):
        value = value[1:]
    value = value.split()[0] if value.split() else value
    if "|" in value:
        value = value.split("|", 1)[0]
    for prefix in ("gene:", "transcript:", "CDS:", "rna-", "gene-", "cds-"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value.strip().strip('"')


def id_variants(gene_id: str) -> set:
    norm = normalize_seq_id(gene_id)
    seeds = {norm}
    if "|" in (gene_id or ""):
        seeds.add(normalize_seq_id(gene_id.split("|", 1)[0]))
    expanded = set()
    for seed in seeds:
        if not seed:
            continue
        expanded.update({seed, seed.upper(), seed.lower()})
        if re.search(r"\.\d+$", seed) and not seed.lower().startswith(("evm.model.", "evm.tu.")):
            stripped = re.sub(r"\.\d+$", "", seed)
            expanded.update({stripped, stripped.upper(), stripped.lower()})
        suffix_stripped = re.sub(r"_[PT]\d+$", "", seed)
        expanded.update({suffix_stripped, suffix_stripped.upper(), suffix_stripped.lower()})
    prefixed = set(expanded)
    for value in expanded:
        for prefix in ("gene:", "transcript:", "CDS:", "rna-", "gene-", "cds-"):
            prefixed.add(normalize_seq_id(prefix + value))
    return {v for v in prefixed if v}


def fasta_ids(fasta_file: Path) -> List[str]:
    ids = []
    with open_text(fasta_file, "rt") as f:
        for line in f:
            if line.startswith(">"):
                seq_id = normalize_seq_id(line[1:])
                if seq_id:
                    ids.append(seq_id)
    return ids


def fasta_id_set(fasta_file: Path) -> set:
    ids = set()
    for seq_id in fasta_ids(fasta_file):
        ids.update(id_variants(seq_id))
    return ids


def display_species_code(species: str, gene_id: str = "") -> str:
    if re.match(r"^AT[1-5CM]G", gene_id, re.IGNORECASE):
        return "AT"
    return species


def species_for_gene(gene_id: str, species_list: List[str], default_species: str, species_id_sets: Optional[Dict[str, set]] = None) -> str:
    variants = id_variants(gene_id)
    if species_id_sets:
        for species, ids in species_id_sets.items():
            if variants & ids:
                return display_species_code(species, gene_id)
    for species in sorted(species_list, key=len, reverse=True):
        if normalize_seq_id(gene_id).lower().startswith(species.lower()):
            return display_species_code(species, gene_id)
    if re.match(r"^AT[1-5CM]G", gene_id, re.IGNORECASE):
        return "AT"
    return display_species_code(default_species, gene_id)


def collect_species_id_sets(groups: Dict[str, Any]) -> Dict[str, set]:
    species_id_sets: Dict[str, set] = {}
    for group_name in ("target", "ref"):
        for species, item in groups.get(group_name, {}).items():
            pep = item.get("resolved", {}).get("pep")
            if pep and Path(pep).exists():
                species_id_sets[species] = fasta_id_set(Path(pep))
    return species_id_sets


def write_gene_species_map(
    target_fasta: Path,
    ref_fasta: Path,
    target_species: List[str],
    ref_species: List[str],
    output_file: Path,
    species_id_sets: Optional[Dict[str, set]] = None,
) -> Path:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    default_target = target_species[0] if target_species else "Target"
    default_ref = ref_species[0] if ref_species else "Ref"
    with open(output_file, "w") as out:
        out.write("gene_id\tspecies\tgroup\n")
        for gene_id in fasta_ids(target_fasta):
            species = species_for_gene(gene_id, target_species, default_target, species_id_sets)
            out.write(f"{gene_id}\t{species}\ttarget\n")
        for gene_id in fasta_ids(ref_fasta):
            species = species_for_gene(gene_id, ref_species, default_ref, species_id_sets)
            out.write(f"{gene_id}\t{species}\tref\n")
    return output_file


def _merge_blast_outputs(blast_files: List[Path], merged_blast: Path) -> bool:
    with open(merged_blast, "w") as outfile:
        for blast_file in blast_files:
            if blast_file.exists():
                with open(blast_file, "r") as infile:
                    for line in infile:
                        if line.strip():
                            outfile.write(line)
    return merged_blast.exists() and merged_blast.stat().st_size > 0


def _run_reciprocal_blastp_engine(
    target_protein: Path,
    ref_protein: Path,
    output_dir: Path,
    target_species: str,
    ref_species: str,
    cpu: int,
    evalue: str,
    max_target_seqs: int,
) -> Dict[str, Any]:
    pair_prefix = f"{target_species}.{ref_species}"
    target_db = output_dir / f"{target_species}.blastdb"
    ref_db = output_dir / f"{ref_species}.blastdb"
    commands = []
    stderr_items = []
    start_time = time.time()

    for protein_file, db_prefix in [(target_protein, target_db), (ref_protein, ref_db)]:
        cmd = ["makeblastdb", "-in", str(protein_file), "-dbtype", "prot", "-out", str(db_prefix)]
        commands.append(" ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            return {"success": False, "stderr": "makeblastdb not found", "commands": commands, "engine_used": "blastp"}
        if result.stderr:
            stderr_items.append(result.stderr)
        if result.returncode != 0:
            return {"success": False, "stderr": "\n".join(stderr_items), "commands": commands, "engine_used": "blastp"}

    target_to_ref = output_dir / f"tmp.{target_species}_{ref_species}.blast"
    ref_to_target = output_dir / f"tmp.{ref_species}_{target_species}.blast"
    for query, db_prefix, out_file in [(target_protein, ref_db, target_to_ref), (ref_protein, target_db, ref_to_target)]:
        cmd = [
            "blastp", "-query", str(query), "-db", str(db_prefix),
            "-outfmt", "6", "-out", str(out_file), "-evalue", str(evalue),
            "-num_alignments", str(max_target_seqs), "-num_threads", str(cpu),
        ]
        commands.append(" ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            return {"success": False, "stderr": "blastp not found", "commands": commands, "engine_used": "blastp"}
        if result.stderr:
            stderr_items.append(result.stderr)
        if result.returncode != 0:
            return {"success": False, "stderr": "\n".join(stderr_items), "commands": commands, "engine_used": "blastp"}

    merged_blast = output_dir / f"{pair_prefix}.blast"
    success = _merge_blast_outputs([target_to_ref, ref_to_target], merged_blast)
    return {
        "success": success,
        "output_file": merged_blast if success else None,
        "commands": commands,
        "stderr": "\n".join(stderr_items),
        "output_stats": {
            str(path.name): path.stat().st_size if path.exists() else -1
            for path in [target_to_ref, ref_to_target, merged_blast]
        },
        "engine_used": "blastp",
        "elapsed_sec": time.time() - start_time,
    }


def _run_reciprocal_diamond_engine(
    target_protein: Path,
    ref_protein: Path,
    output_dir: Path,
    target_species: str,
    ref_species: str,
    cpu: int,
    evalue: str,
    max_target_seqs: int,
) -> Dict[str, Any]:
    pair_prefix = f"{target_species}.{ref_species}"
    target_db = output_dir / f"{target_species}.diamond_db"
    ref_db = output_dir / f"{ref_species}.diamond_db"
    commands = []
    stderr_items = []
    start_time = time.time()

    for protein_file, db_prefix in [(target_protein, target_db), (ref_protein, ref_db)]:
        cmd = ["diamond", "makedb", "--in", str(protein_file), "--db", str(db_prefix)]
        commands.append(" ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            return {"success": False, "stderr": "diamond not found", "commands": commands, "engine_used": "diamond"}
        if result.stderr:
            stderr_items.append(result.stderr)
        if result.returncode != 0:
            return {"success": False, "stderr": "\n".join(stderr_items), "commands": commands, "engine_used": "diamond"}

    target_to_ref = output_dir / f"tmp.{target_species}_{ref_species}.blast"
    ref_to_target = output_dir / f"tmp.{ref_species}_{target_species}.blast"
    for query, db_prefix, out_file in [(target_protein, ref_db, target_to_ref), (ref_protein, target_db, ref_to_target)]:
        cmd = [
            "diamond", "blastp", "--query", str(query), "--db", str(db_prefix),
            "--out", str(out_file), "--outfmt", "6", "--max-target-seqs", str(max_target_seqs),
            "--evalue", str(evalue), "--threads", str(cpu),
        ]
        commands.append(" ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            return {"success": False, "stderr": "diamond not found", "commands": commands, "engine_used": "diamond"}
        if result.stderr:
            stderr_items.append(result.stderr)
        if result.returncode != 0:
            return {"success": False, "stderr": "\n".join(stderr_items), "commands": commands, "engine_used": "diamond"}

    merged_blast = output_dir / f"{pair_prefix}.blast"
    success = _merge_blast_outputs([target_to_ref, ref_to_target], merged_blast)
    return {
        "success": success,
        "output_file": merged_blast if success else None,
        "commands": commands,
        "stderr": "\n".join(stderr_items),
        "output_stats": {
            str(path.name): path.stat().st_size if path.exists() else -1
            for path in [target_to_ref, ref_to_target, merged_blast]
        },
        "engine_used": "diamond",
        "elapsed_sec": time.time() - start_time,
    }


def run_reciprocal_blast(
    target_protein: Path,
    ref_protein: Path,
    output_dir: Path,
    target_species: str,
    ref_species: str,
    cpu: int = 8,
    engine: str = "diamond",
    evalue: str = "1e-10",
    max_target_seqs: int = 5,
) -> Dict[str, Any]:
    """Run pairwise reciprocal protein search for one target-ref species pair."""
    target_protein = materialize_plain_fasta(target_protein, output_dir, f"{target_species}.search")
    ref_protein = materialize_plain_fasta(ref_protein, output_dir, f"{ref_species}.search")
    requested_engine = (engine or "diamond").strip().lower()
    if requested_engine not in {"diamond", "blastp"}:
        requested_engine = "diamond"
    print(f"Reciprocal protein search ({requested_engine}): {target_species} vs {ref_species}")

    if requested_engine == "blastp":
        result = _run_reciprocal_blastp_engine(target_protein, ref_protein, output_dir, target_species, ref_species, cpu, evalue, max_target_seqs)
        result["engine_requested"] = requested_engine
        result["fallback_from"] = None
        return result

    result = _run_reciprocal_diamond_engine(target_protein, ref_protein, output_dir, target_species, ref_species, cpu, evalue, max_target_seqs)
    if result.get("success"):
        result["engine_requested"] = requested_engine
        result["fallback_from"] = None
        return result

    diamond_stderr = result.get("stderr", "")
    diamond_commands = list(result.get("commands", []))
    print(f"  Diamond failed for {target_species}.{ref_species}; falling back to NCBI blastp")
    fallback = _run_reciprocal_blastp_engine(target_protein, ref_protein, output_dir, target_species, ref_species, cpu, evalue, max_target_seqs)
    fallback["engine_requested"] = requested_engine
    fallback["fallback_from"] = "diamond"
    fallback["diamond_stderr"] = diamond_stderr
    fallback["commands"] = diamond_commands + list(fallback.get("commands", []))
    return fallback


def run_mcscanx_interspecies(work_dir: Path, target_species: str, ref_species: str) -> Dict[str, Any]:
    """Run MCScanX for one target-ref pair."""
    pair_prefix = f"{target_species}.{ref_species}"
    print(f"MCScanX pair: {pair_prefix}")

    merged_gff = work_dir / f"{pair_prefix}.gff"
    target_gff = work_dir / f"{target_species}.gff"
    ref_gff = work_dir / f"{ref_species}.gff"

    if target_gff.exists() and ref_gff.exists():
        with open(merged_gff, 'w') as outfile:
            for gff_file in [target_gff, ref_gff]:
                with open(gff_file, 'r') as infile:
                    outfile.write(infile.read().rstrip() + "\n")
    else:
        return {"success": False, "error": f"Missing simplified GFF for {pair_prefix}"}

    result = subprocess.run(["MCScanX", f"./{pair_prefix}"], capture_output=True, text=True, cwd=str(work_dir), check=False)
    collinearity_file = work_dir / f"{pair_prefix}.collinearity"
    return {
        "success": collinearity_file.exists() and collinearity_file.stat().st_size > 0,
        "collinearity_file": collinearity_file if collinearity_file.exists() else None,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
    }


def _read_tree_tip_labels(tree_path: Path) -> set[str]:
    try:
        content = tree_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return set()
    return {m.group(1).strip().strip("'").strip('"') for m in re.finditer(r"(?<=[(,])([^():,;]+):", content) if m.group(1).strip()}


def _merge_matching_subfamily_files(candidates: List[Path], tree_file: Path, output_dir: Path, prefix: str) -> Optional[Path]:
    tips = _read_tree_tip_labels(tree_file)
    if not tips:
        return None
    merged: Dict[str, str] = {}
    used_files: List[str] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if not candidate or candidate in seen or not candidate.exists() or not candidate.is_file():
            continue
        seen.add(candidate)
        matched_here = False
        try:
            with candidate.open("r", encoding="utf-8", errors="ignore") as handle:
                for raw in handle:
                    parts = raw.strip().split()
                    if len(parts) < 2:
                        continue
                    gene_id, subfamily = parts[0], parts[1]
                    if gene_id in tips and gene_id not in merged:
                        merged[gene_id] = subfamily
                        matched_here = True
        except Exception:
            continue
        if matched_here:
            used_files.append(str(candidate))
    if not merged:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    merged_path = output_dir / f"{prefix}.merged.geneID.subfamily"
    with merged_path.open("w", encoding="utf-8") as handle:
        for gene_id in sorted(merged):
            handle.write(f"{gene_id}\t{merged[gene_id]}\n")
    print(f"  合并亚家族文件: {merged_path} ({len(merged)}/{len(tips)} tips; sources={len(used_files)})")
    return merged_path


def plot_interspecies_tree(
    tree_file: Path,
    output_dir: Path,
    prefix: str,
    target_species: str,
    ref_species: str,
    target_subfamily_file: Optional[Path] = None,
    ref_subfamily_file: Optional[Path] = None,
    gene_species_map: Optional[Path] = None,
    subfamily_arc_enabled: bool = True,
    subfamily_arc_gap: float = 1.2,
    subfamily_arc_label_gap: float = 0.6,
    subfamily_arc_label_size: float = 0.0,
) -> Dict[str, Any]:
    """绘制物种间进化树图 - 使用专用的 interspecies_tree.R 脚本"""
    print(f"绘制进化树图...")
    
    # 查找专用的种间进化树R脚本
    r_script = Path("scripts/interspecies_tree.R")
    if not r_script.exists():
        return {"success": False, "error": f"R脚本不存在: {r_script}"}
    
    try:
        # 使用专用的 interspecies_tree.R 脚本，传递物种参数和亚家族文件
        # 使用conda环境的Rscript
        conda_rscript = str(shutil.which("Rscript") or "Rscript")
        cmd = [
            conda_rscript, str(r_script), 
            str(tree_file), 
            str(output_dir),
            target_species,
            ref_species
        ]
        
        # 添加亚家族文件参数（如果存在）
        if target_subfamily_file and target_subfamily_file.exists():
            cmd.append(str(target_subfamily_file))
            print(f"  目标物种亚家族文件: {target_subfamily_file}")
        else:
            cmd.append("")  # 空字符串表示无文件
            
        if ref_subfamily_file and ref_subfamily_file.exists():
            cmd.append(str(ref_subfamily_file))
            print(f"  参考物种亚家族文件: {ref_subfamily_file}")
        else:
            cmd.append("")  # 空字符串表示无文件
        if gene_species_map and gene_species_map.exists():
            cmd.append(str(gene_species_map))
            print(f"  Gene-to-species map: {gene_species_map}")
        else:
            cmd.append("")
        cmd.extend([
            str(0.4),
            str(2.8),
            "black",
            str(0.5),
            "triangle",
            "circle",
            str(3.0),
            str(2.0),
            "true" if subfamily_arc_enabled else "false",
            str(subfamily_arc_gap),
            str(subfamily_arc_label_gap),
            str(subfamily_arc_label_size if subfamily_arc_label_size and subfamily_arc_label_size > 0 else ""),
        ])
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
        
        # 调试输出
        print(f"  R脚本返回码: {result.returncode}")
        if result.stdout:
            print(f"  R脚本输出: {result.stdout[:500]}")
        if result.stderr:
            print(f"  R脚本错误: {result.stderr[:500]}")
        
        # 专用脚本输出固定为 interspecies_tree.pdf/png
        output_pdf = output_dir / "interspecies_tree.pdf"
        output_png = output_dir / "interspecies_tree.png"
        output_circular_pdf = output_dir / "interspecies_tree.circular_tree.pdf"
        output_circular_png = output_dir / "interspecies_tree.circular_tree.png"
        
        # 重命名为规范名称
        final_pdf = output_dir / f"{prefix}.interspecies_tree.pdf"
        final_png = output_dir / f"{prefix}.interspecies_tree.png"
        final_circular_pdf = output_dir / f"{prefix}.interspecies_tree.circular_tree.pdf"
        final_circular_png = output_dir / f"{prefix}.interspecies_tree.circular_tree.png"
        
        if output_pdf.exists():
            shutil.move(output_pdf, final_pdf)
            print(f"  进化树PDF: {final_pdf}")
        if output_png.exists():
            shutil.move(output_png, final_png)
            print(f"  进化树PNG: {final_png}")
        if output_circular_pdf.exists():
            shutil.move(output_circular_pdf, final_circular_pdf)
            print(f"  进化树圈图PDF: {final_circular_pdf}")
        if output_circular_png.exists():
            shutil.move(output_circular_png, final_circular_png)
            print(f"  进化树圈图PNG: {final_circular_png}")
        
        # PDF转PNG（如果没有生成PNG）
        if final_pdf.exists() and not final_png.exists():
            png_from_pdf = output_dir / "interspecies_tree.png"
            if pdf_to_png(final_pdf, png_from_pdf):
                final_png = output_dir / f"{prefix}.interspecies_tree.png"
                shutil.move(png_from_pdf, final_png)
                print(f"  PDF转PNG: {final_png}")
        
        return {
            "success": final_pdf.exists() or final_png.exists(),
            "pdf_file": final_pdf if final_pdf.exists() else None,
            "png_file": final_png if final_png.exists() else None,
            "circular_pdf_file": final_circular_pdf if final_circular_pdf.exists() else None,
            "circular_png_file": final_circular_png if final_circular_png.exists() else None,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "subfamily_arc_enabled": subfamily_arc_enabled,
            "subfamily_arc_gap": subfamily_arc_gap,
            "subfamily_arc_label_gap": subfamily_arc_label_gap,
            "subfamily_arc_label_size": subfamily_arc_label_size,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def convert_blast_to_family_simple(
    work_dir: Path,
    blast_file: Path,
    target_species: str,
    ref_species: str,
) -> Dict[str, Any]:
    """Fallback: convert pairwise BLAST hits directly to JCVI anchors/simple.

    This is not a strict MCScanX collinearity block. It is used when the ref side
    only has family proteins, so MCScanX cannot satisfy whole-genome block rules.
    """
    pair_prefix = f"{target_species}.{ref_species}"
    target_bed = work_dir / f"{target_species}.bed"
    ref_bed = work_dir / f"{ref_species}.bed"
    anchors_file = work_dir / f"{pair_prefix}.anchors"
    simple_file = work_dir / f"{pair_prefix}.anchors.simple"

    valid_target = {line.rstrip("\n").split("\t")[3] for line in open(target_bed) if len(line.rstrip("\n").split("\t")) >= 4}
    valid_ref = {line.rstrip("\n").split("\t")[3] for line in open(ref_bed) if len(line.rstrip("\n").split("\t")) >= 4}
    seen = set()
    links = []
    if not blast_file.exists():
        return {"success": False, "error": "BLAST file missing for family-only fallback"}
    with open(blast_file, "r") as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            a = normalize_seq_id(parts[0])
            b = normalize_seq_id(parts[1])
            target_gene = ref_gene = None
            if a in valid_target and b in valid_ref:
                target_gene, ref_gene = a, b
            elif b in valid_target and a in valid_ref:
                target_gene, ref_gene = b, a
            if not target_gene or not ref_gene:
                continue
            key = (target_gene, ref_gene)
            if key in seen:
                continue
            seen.add(key)
            score_text = parts[11] if len(parts) > 11 else "1000"
            try:
                score = str(int(float(score_text)))
            except Exception:
                score = "1000"
            links.append((target_gene, ref_gene, score))
    if not links:
        return {"success": False, "error": "No BLAST hits matched pair BED IDs for family-only fallback"}
    with open(anchors_file, "w") as out:
        out.write("##\n")
        for target_gene, ref_gene, score in links:
            out.write(f"{target_gene}\t{ref_gene}\t{score}\n")
    with open(simple_file, "w") as out:
        for target_gene, ref_gene, score in links:
            out.write(f"{target_gene}\t{target_gene}\t{ref_gene}\t{ref_gene}\t{score}\t+\n")
    return {
        "success": True,
        "anchors_file": anchors_file,
        "simple_file": simple_file,
        "anchor_count": len(links),
        "fallback": "family_only_blast_links",
    }


def convert_collinearity_to_simple(
    work_dir: Path,
    collinearity_file: Path,
    target_species: str,
    ref_species: str,
) -> Dict[str, Any]:
    """Convert one MCScanX .collinearity file into JCVI anchors/simple files."""
    conda_python = sys.executable
    pair_prefix = f"{target_species}.{ref_species}"
    target_bed = work_dir / f"{target_species}.bed"
    ref_bed = work_dir / f"{ref_species}.bed"
    anchors_file = work_dir / f"{pair_prefix}.anchors"
    anchors_new_file = work_dir / f"{pair_prefix}.anchors.new"
    simple_file = work_dir / f"{pair_prefix}.anchors.simple"

    valid_target = {line.rstrip("\n").split("\t")[3] for line in open(target_bed) if len(line.rstrip("\n").split("\t")) >= 4}
    valid_ref = {line.rstrip("\n").split("\t")[3] for line in open(ref_bed) if len(line.rstrip("\n").split("\t")) >= 4}

    current_block = []
    anchors = 0
    filtered = 0
    with open(collinearity_file, "r") as f_in, open(anchors_file, "w") as f_out:
        for line in f_in:
            if not line.strip():
                continue
            if line.startswith("## Alignment"):
                if current_block:
                    f_out.write("##\n")
                    f_out.writelines(current_block)
                    current_block = []
                continue
            if line.startswith("#"):
                continue
            parts = line.strip().split("\t")
            if len(parts) < 3:
                continue
            gene_a = normalize_seq_id(parts[1])
            gene_b = normalize_seq_id(parts[2])
            target_gene = ref_gene = None
            if gene_a in valid_target and gene_b in valid_ref:
                target_gene, ref_gene = gene_a, gene_b
            elif gene_b in valid_target and gene_a in valid_ref:
                target_gene, ref_gene = gene_b, gene_a
            if target_gene and ref_gene:
                current_block.append(f"{target_gene}	{ref_gene}	1000\n")
                anchors += 1
            else:
                filtered += 1
        if current_block:
            f_out.write("##\n")
            f_out.writelines(current_block)

    if anchors == 0:
        return {"success": False, "error": "No anchors matched pair BED IDs", "anchors_file": anchors_file, "filtered": filtered}

    cmd_screen = [
        conda_python, "-m", "jcvi.compara.synteny", "screen",
        "--minspan=10", "--simple", str(anchors_file.absolute()), str(anchors_new_file.absolute()),
        f"--qbed={target_bed.name}", f"--sbed={ref_bed.name}",
    ]
    result = subprocess.run(cmd_screen, capture_output=True, text=True, cwd=str(work_dir), check=False, timeout=180)
    screen_summary = summarize_process_result(result)
    for candidate in (work_dir / f"{pair_prefix}.anchors.new.simple", work_dir / f"{pair_prefix}.new.simple"):
        if candidate.exists():
            shutil.copy(candidate, simple_file)
            break
    if not simple_file.exists():
        shutil.copy(anchors_file, simple_file)

    return {
        "success": simple_file.exists() and simple_file.stat().st_size > 0,
        "anchors_file": anchors_file,
        "anchors_new_file": anchors_new_file,
        "simple_file": simple_file,
        "anchor_count": anchors,
        "filtered_count": filtered,
        "screen_returncode": result.returncode,
        "screen_stderr": screen_summary.get("stderr_tail", ""),
    }


def _chr_sort_key(chrom: str):
    cleaned = re.sub(r"^(chr|Chr|CHR)", "", chrom)
    if cleaned.isdigit():
        return (0, int(cleaned))
    return (1, cleaned)


def _read_bed_chroms(bed_file: Path) -> List[str]:
    skip = {"C", "M", "ChrC", "ChrM", "chrc", "chrm"}
    chroms = set()
    with open(bed_file, "r") as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) >= 4 and parts[0] not in skip:
                chroms.add(parts[0])
    return sorted(chroms, key=_chr_sort_key)


def _bed_gene_ids(bed_file: Path) -> set:
    ids = set()
    if not bed_file.exists():
        return ids
    with open(bed_file, "r") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 4:
                ids.add(normalize_seq_id(parts[3]))
    return ids


def _is_family_gene(gene_id: str, family_ids: set) -> bool:
    return bool(set(id_variants(gene_id)) & family_ids)


def _family_links_from_blast(
    blast_file: Path,
    family_ids: set,
    target_bed_ids: set,
    ref_bed_ids: set,
    max_links: int = 300,
) -> List[Tuple[str, str, int]]:
    if not blast_file or not blast_file.exists() or not family_ids:
        return []
    best: Dict[Tuple[str, str], float] = {}
    with open(blast_file, "r") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 12:
                continue
            q, s = normalize_seq_id(parts[0]), normalize_seq_id(parts[1])
            if q not in target_bed_ids or s not in ref_bed_ids:
                continue
            if not (_is_family_gene(q, family_ids) and _is_family_gene(s, family_ids)):
                continue
            try:
                score = float(parts[11])
            except Exception:
                score = 1.0
            key = (q, s)
            if score > best.get(key, -1):
                best[key] = score
    ranked = sorted(best.items(), key=lambda item: item[1], reverse=True)[:max_links]
    return [(q, s, max(1, int(round(score)))) for (q, s), score in ranked]


def _family_block_signatures_from_anchors(anchors_file: Path, family_ids: set) -> set:
    """Return JCVI simple-row signatures for blocks containing family genes.

    JCVI's anchors.new keeps full block membership, while anchors.simple stores
    only block endpoints.  Published-style family highlighting should mark the
    whole block when any family member occurs inside the block, not only when the
    member happens to be one of the simple endpoints.
    """
    signatures = set()
    if not anchors_file or not anchors_file.exists() or not family_ids:
        return signatures

    def flush(block: List[Tuple[str, str]]) -> None:
        if not block:
            return
        contains_family = any(_is_family_gene(q, family_ids) or _is_family_gene(s, family_ids) for q, s in block)
        if not contains_family:
            return
        q1, s1 = block[0]
        q2, s2 = block[-1]
        signatures.add((frozenset((q1, q2)), frozenset((s1, s2))))

    block: List[Tuple[str, str]] = []
    with open(anchors_file, "r") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("###") or line.startswith("#"):
                flush(block)
                block = []
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            block.append((normalize_seq_id(parts[0]), normalize_seq_id(parts[1])))
    flush(block)
    return signatures


def _simple_block_signature(parts: List[str]) -> Optional[Tuple[frozenset, frozenset]]:
    if len(parts) < 4:
        return None
    q1, q2, s1, s2 = [normalize_seq_id(p.split("*", 1)[-1]) for p in parts[:4]]
    return (frozenset((q1, q2)), frozenset((s1, s2)))


def _write_marked_simple_file(
    simple_file: Path,
    marked_file: Path,
    family_ids: set,
    family_links: List[Tuple[str, str, int]],
    anchors_new_file: Optional[Path] = None,
) -> int:
    if not simple_file.exists() or not family_ids:
        return 0
    family_block_signatures = _family_block_signatures_from_anchors(anchors_new_file, family_ids) if anchors_new_file else set()
    marked_count = 0
    seen_rows = set()
    with open(simple_file, "r") as f_in, open(marked_file, "w") as f_out:
        for line in f_in:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                f_out.write(line)
                continue
            parts = stripped.split()
            genes = [normalize_seq_id(p.split("*", 1)[-1]) for p in parts[:4]]
            row_key = tuple(parts[:6]) if len(parts) >= 6 else tuple(parts)
            row_sig = _simple_block_signature(parts)
            seen_rows.add(row_key)
            direct_endpoint_hit = any(_is_family_gene(g, family_ids) for g in genes)
            block_hit = row_sig in family_block_signatures if row_sig is not None else False
            if (direct_endpoint_hit or block_hit) and "*" not in parts[0]:
                f_out.write("#e31a1c*" + "\t".join(parts) + "\n")
                marked_count += 1
            else:
                f_out.write(line)
        for q, s, score in family_links:
            row = (q, q, s, s, str(score), "+")
            if row in seen_rows:
                continue
            f_out.write("#e31a1c*" + "\t".join(row) + "\n")
            seen_rows.add(row)
            marked_count += 1
    return marked_count

def plot_multi_species_synteny(
    work_dir: Path,
    output_dir: Path,
    prefix: str,
    species_order: List[str],
    pair_results: List[Dict[str, Any]],
    family_ids: set,
) -> Dict[str, Any]:
    """Draw one multi-track JCVI karyotype figure from all successful target-ref pairs."""
    print("Plotting multi-species JCVI synteny...")
    conda_python = sys.executable
    usable_pairs = [p for p in pair_results if p.get("simple_file") and Path(p["simple_file"]).exists()]
    if not usable_pairs:
        return {"success": False, "error": "No pairwise anchors.simple files available for JCVI"}

    seqids_file = work_dir / f"{prefix}.seqids"
    layout_file = work_dir / f"{prefix}.layout"
    layout_mark_file = work_dir / f"{prefix}.layout_mark"

    species_with_bed = []
    with open(seqids_file, "w") as f:
        for species in species_order:
            bed = work_dir / f"{species}.bed"
            if not bed.exists():
                continue
            chroms = _read_bed_chroms(bed)
            if not chroms:
                continue
            species_with_bed.append(species)
            f.write(",".join(chroms) + "\n")

    if len(species_with_bed) < 2:
        return {"success": False, "error": "Fewer than two species BED tracks are available"}

    index = {species: i for i, species in enumerate(species_with_bed)}
    n = len(species_with_bed)
    y_values = [round(0.85 - (0.7 * i / max(n - 1, 1)), 3) for i in range(n)]
    palette = ["#d73027", "#1a9850", "#4575b4", "#984ea3", "#ff7f00", "#4daf4a", "#377eb8", "#a65628"]

    def write_layout(layout: Path, use_marked: bool) -> None:
        with open(layout, "w") as f:
            f.write("# y, xstart, xend, rotation, color, label, va, bed\n")
            for i, species in enumerate(species_with_bed):
                va = "top" if i == 0 else "bottom"
                f.write(f"{y_values[i]}, .08, .92, 0, {palette[i % len(palette)]}, {species}, {va}, {species}.bed\n")
            f.write("# edges\n")
            for pair in usable_pairs:
                target = pair["target_species"]
                ref = pair["ref_species"]
                if target not in index or ref not in index:
                    continue
                edge = Path(pair.get("marked_simple_file") if use_marked and pair.get("marked_simple_file") else pair["simple_file"]).name
                f.write(f"e, {index[target]}, {index[ref]}, {edge}\n")

    for pair in usable_pairs:
        simple = Path(pair["simple_file"])
        marked = work_dir / f"{pair['target_species']}.{pair['ref_species']}.anchors.simple.mark"
        target_bed_ids = _bed_gene_ids(work_dir / f"{pair['target_species']}.bed")
        ref_bed_ids = _bed_gene_ids(work_dir / f"{pair['ref_species']}.bed")
        family_links = _family_links_from_blast(Path(pair.get("blast_file", "")), family_ids, target_bed_ids, ref_bed_ids)
        marked_count = _write_marked_simple_file(simple, marked, family_ids, family_links, Path(pair.get("anchors_new_file", "")))
        pair["family_highlight_links"] = len(family_links)
        pair["marked_block_count"] = marked_count
        if marked_count > 0:
            pair["marked_simple_file"] = str(marked)

    write_layout(layout_file, use_marked=False)
    write_layout(layout_mark_file, use_marked=True)

    def run_plot(layout: Path, final_pdf: Path, final_png: Path) -> Dict[str, Any]:
        for stale in [work_dir / "karyotype.pdf", work_dir / "karyotype.png", final_pdf, final_png]:
            if stale.exists():
                stale.unlink()
        cmd = [conda_python, "-m", "jcvi.graphics.karyotype", str(seqids_file.absolute()), str(layout.absolute()), "--notex"]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(work_dir), check=False, timeout=180)
        if (work_dir / "karyotype.pdf").exists():
            shutil.copy(work_dir / "karyotype.pdf", final_pdf.absolute())
        if (work_dir / "karyotype.png").exists():
            shutil.copy(work_dir / "karyotype.png", final_png.absolute())
        if final_pdf.exists() and (not final_png.exists() or final_png.stat().st_mtime < final_pdf.stat().st_mtime):
            pdf_to_png(final_pdf, final_png)
        return summarize_process_result(result)

    final_pdf = output_dir / f"{prefix}.interspecies_synteny.pdf"
    final_png = output_dir / f"{prefix}.interspecies_synteny.png"
    final_mark_pdf = output_dir / f"{prefix}.interspecies_synteny.marked.pdf"
    final_mark_png = output_dir / f"{prefix}.interspecies_synteny.marked.png"

    base_result = run_plot(layout_file, final_pdf, final_png)
    mark_result = run_plot(layout_mark_file, final_mark_pdf, final_mark_png)

    return {
        "success": final_pdf.exists() or final_png.exists(),
        "pdf_file": final_pdf if final_pdf.exists() else None,
        "png_file": final_png if final_png.exists() else None,
        "marked_pdf_file": final_mark_pdf if final_mark_pdf.exists() else None,
        "marked_png_file": final_mark_png if final_mark_png.exists() else None,
        "seqids_file": seqids_file,
        "layout_file": layout_file,
        "layout_mark_file": layout_mark_file,
        "base_plot": base_result,
        "marked_plot": mark_result,
    }


def plot_pairwise_synteny(
    work_dir: Path,
    output_dir: Path,
    prefix: str,
    pair_results: List[Dict[str, Any]],
    family_ids: set,
) -> Dict[str, Any]:
    """Draw one JCVI karyotype figure for each successful target-ref pair."""
    print("Plotting pairwise JCVI synteny...")
    conda_python = sys.executable
    usable_pairs = [p for p in pair_results if p.get("success") and p.get("simple_file") and Path(p["simple_file"]).exists()]
    if not usable_pairs:
        return {"success": False, "error": "No successful pairwise anchors.simple files available for JCVI", "pairs": []}

    pair_outputs: List[Dict[str, Any]] = []
    palette = ["#d73027", "#4575b4"]

    def run_plot(seqids: Path, layout: Path, final_pdf: Path, final_png: Path) -> Dict[str, Any]:
        for stale in [work_dir / "karyotype.pdf", work_dir / "karyotype.png", final_pdf, final_png]:
            if stale.exists():
                stale.unlink()
        cmd = [conda_python, "-m", "jcvi.graphics.karyotype", str(seqids.absolute()), str(layout.absolute()), "--notex"]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(work_dir), check=False, timeout=180)
        if (work_dir / "karyotype.pdf").exists():
            shutil.copy(work_dir / "karyotype.pdf", final_pdf.absolute())
        if (work_dir / "karyotype.png").exists():
            shutil.copy(work_dir / "karyotype.png", final_png.absolute())
        if final_pdf.exists() and (not final_png.exists() or final_png.stat().st_mtime < final_pdf.stat().st_mtime):
            pdf_to_png(final_pdf, final_png)
        return summarize_process_result(result)

    for pair in usable_pairs:
        target = pair["target_species"]
        ref = pair["ref_species"]
        pair_key = f"{target}.{ref}"
        target_bed = work_dir / f"{target}.bed"
        ref_bed = work_dir / f"{ref}.bed"
        simple = Path(pair["simple_file"])
        if not target_bed.exists() or not ref_bed.exists():
            pair_outputs.append({"pair": pair_key, "success": False, "error": "missing pair BED file"})
            continue

        marked = work_dir / f"{pair_key}.anchors.simple.mark"
        target_bed_ids = _bed_gene_ids(target_bed)
        ref_bed_ids = _bed_gene_ids(ref_bed)
        family_links = _family_links_from_blast(Path(pair.get("blast_file", "")), family_ids, target_bed_ids, ref_bed_ids)
        marked_count = _write_marked_simple_file(simple, marked, family_ids, family_links, Path(pair.get("anchors_new_file", "")))
        pair["pairwise_family_highlight_links"] = len(family_links)
        pair["pairwise_marked_block_count"] = marked_count
        if marked_count > 0:
            pair["marked_simple_file"] = str(marked)

        seqids_file = work_dir / f"{pair_key}.seqids"
        layout_file = work_dir / f"{pair_key}.layout"
        layout_mark_file = work_dir / f"{pair_key}.layout_mark"
        species_rows = [(target, target_bed), (ref, ref_bed)]
        with open(seqids_file, "w") as f:
            for _, bed in species_rows:
                chroms = _read_bed_chroms(bed)
                if not chroms:
                    pair_outputs.append({"pair": pair_key, "success": False, "error": f"no chromosomes found in {bed.name}"})
                    break
                f.write(",".join(chroms) + "\n")
        if pair_outputs and pair_outputs[-1].get("pair") == pair_key and not pair_outputs[-1].get("success") and pair_outputs[-1].get("error", "").startswith("no chromosomes"):
            continue

        def write_layout(layout: Path, use_marked: bool) -> None:
            edge_file = Path(pair.get("marked_simple_file") if use_marked and pair.get("marked_simple_file") else simple).name
            with open(layout, "w") as f:
                f.write("# y, xstart, xend, rotation, color, label, va, bed\n")
                f.write(f"0.75, .08, .92, 0, {palette[0]}, {target}, top, {target}.bed\n")
                f.write(f"0.25, .08, .92, 0, {palette[1]}, {ref}, bottom, {ref}.bed\n")
                f.write("# edges\n")
                f.write(f"e, 0, 1, {edge_file}\n")

        write_layout(layout_file, use_marked=False)
        write_layout(layout_mark_file, use_marked=True)

        final_pdf = output_dir / f"{prefix}.{pair_key}.interspecies_synteny.pdf"
        final_png = output_dir / f"{prefix}.{pair_key}.interspecies_synteny.png"
        final_mark_pdf = output_dir / f"{prefix}.{pair_key}.interspecies_synteny.marked.pdf"
        final_mark_png = output_dir / f"{prefix}.{pair_key}.interspecies_synteny.marked.png"
        base_result = run_plot(seqids_file, layout_file, final_pdf, final_png)
        mark_result = run_plot(seqids_file, layout_mark_file, final_mark_pdf, final_mark_png)
        success = final_pdf.exists() or final_png.exists()
        pair_outputs.append({
            "pair": pair_key,
            "target_species": target,
            "ref_species": ref,
            "success": success,
            "pdf_file": str(final_pdf) if final_pdf.exists() else "",
            "png_file": str(final_png) if final_png.exists() else "",
            "marked_pdf_file": str(final_mark_pdf) if final_mark_pdf.exists() else "",
            "marked_png_file": str(final_mark_png) if final_mark_png.exists() else "",
            "seqids_file": str(seqids_file),
            "layout_file": str(layout_file),
            "layout_mark_file": str(layout_mark_file),
            "base_plot": base_result,
            "marked_plot": mark_result,
        })

    return {
        "success": any(item.get("success") for item in pair_outputs),
        "pairs": pair_outputs,
        "pairwise_plot_count": sum(1 for item in pair_outputs if item.get("success")),
    }


def write_report(workspace: Path, prefix: str, metrics: Dict, parameters: Dict,
                 tool_versions: Dict, start_time: datetime, end_time: datetime,
                 elapsed_sec: float, cmd: List[str]) -> Path:
    """写入报告"""
    report_path = workspace / "final" / f"{prefix}.interspecies_synteny_report.md"

    # 收集可复现性信息
    reproducibility_info = []
    reproducibility_info.append("## 可复现性信息\n\n")
    reproducibility_info.append("### 执行环境\n")
    reproducibility_info.append(f"- **分析时间**: {start_time.isoformat()}\n")
    reproducibility_info.append(f"- **完成时间**: {end_time.isoformat()}\n")
    reproducibility_info.append(f"- **运行时长**: {elapsed_sec:.2f} 秒\n")
    reproducibility_info.append(f"- **工作目录**: {workspace}\n")
    reproducibility_info.append(f"- **输入目录**: {parameters.get('input_dir', 'N/A')}\n\n")

    reproducibility_info.append("### 工具版本\n")
    for tool, version in tool_versions.items():
        reproducibility_info.append(f"- **{tool}**: {version}\n")
    reproducibility_info.append("\n")

    reproducibility_info.append("### 执行命令\n")
    reproducibility_info.append(f"```bash\n{' '.join(cmd)}\n```\n\n")

    reproducibility_info.append("### 输入参数\n")
    for key, value in parameters.items():
        if key != 'input_dir':
            reproducibility_info.append(f"- **{key}**: {value}\n")
    reproducibility_info.append("\n")

    with open(report_path, "w") as f:
        f.write(f"# 物种间共线性分析 - {prefix}\n\n")
        f.write(f"**时间**: {datetime.now().isoformat()}\n\n")
        f.write(f"- 目标物种 (研究对象): {parameters.get('target_species')}\n")
        f.write(f"- 参考物种 (对照): {parameters.get('ref_species')}\n")
        f.write(f"- 目标蛋白文件: {parameters.get('target_protein_spl')}\n")
        f.write(f"- 参考蛋白文件: {parameters.get('ref_protein_spl')}\n")
        f.write(f"- 目标全蛋白组: {parameters.get('target_protein_all')}\n")
        f.write(f"- 参考全蛋白组: {parameters.get('ref_protein_all')}\n")
        f.write(f"- BLAST engine requested: {parameters.get('blast_engine_requested')}\n")
        f.write(f"- BLAST engine used: {metrics.get('blast_engine_used')}\n")
        f.write(f"- BLAST fallback count: {metrics.get('blast_fallback_count', 0)}\n")
        species_positions = metrics.get("species_position_results") or {}
        if species_positions:
            f.write("- 染色体背景: 保留主核染色体/染色体尺度序列，过滤疑似细胞器和短 contig/scaffold；无法识别主序列时回退全量。\n")
            for species, info in species_positions.items():
                f.write(
                    f"  - {species}: plotted {info.get('chromosomes_plotted', 'N/A')}/"
                    f"{info.get('chromosomes_total', 'N/A')} seqids; "
                    f"filtered genes {info.get('genes_filtered_by_chromosome', 'N/A')} "
                    f"({info.get('chromosomes_filter_mode', 'N/A')})\n"
                )
        f.write("\n")
        f.writelines(reproducibility_info)
    return report_path


def get_file_info(file_path: Path) -> Dict[str, Any]:
    """获取文件的详细信息，用于artifact规范"""
    info = {
        "size": 0,
        "sha256": "",
        "mtime": 0
    }
    if file_path.exists():
        info["size"] = file_path.stat().st_size
        info["mtime"] = file_path.stat().st_mtime
        info["sha256"] = compute_file_hash(file_path)
    return info


def write_manifest(workspace: Path, job_id: str, run_id: str, prefix: str,
                  start_time: datetime, end_time: datetime, artifacts: Dict,
                  metrics: Dict, parameters: Dict, tool_versions: Dict,
                  workspace_root: Path, input_dir: Path, cmd: List[str],
                  status: str = "success") -> Path:
    """写入manifest - 符合schema v1标准"""
    elapsed_sec = (end_time - start_time).total_seconds()

    # 构建符合规范的artifacts结构
    formatted_artifacts = {}
    for key, value in artifacts.items():
        if isinstance(value, dict):
            # 已经是字典格式，确保包含必需字段
            formatted_artifacts[key] = value
        elif isinstance(value, str):
            # 字符串格式，转换为artifact对象
            file_path = workspace / value
            file_info = get_file_info(file_path)
            formatted_artifacts[key] = {
                "artifact": key,
                "path": value,
                "full_path": str(file_path.absolute()),
                "type": "file",
                "size": file_info["size"],
                "sha256": file_info["sha256"],
                "mtime": file_info["mtime"]
            }

    formatted_artifacts = rebase_manifest_payload_paths(workspace, formatted_artifacts)
    rebased_metrics = rebase_manifest_payload_paths(workspace, metrics)
    rebased_parameters = rebase_manifest_payload_paths(workspace, parameters)

    manifest_data = {
        "manifest_version": "1.0",
        "job_id": job_id,
        "stage": "interspecies_synteny",
        "run_id": run_id,
        "prefix": prefix,
        "timestamp": end_time.isoformat(),
        "status": status,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "elapsed_sec": round(elapsed_sec, 2),
        "cmd": rebase_manifest_payload_paths(workspace, cmd),
        "workspace_root": str(workspace_root.absolute()),
        "workspace": str(workspace.absolute()),
        "input_dir": str(input_dir.absolute()),
        "tool_versions": tool_versions,
        "artifacts": formatted_artifacts,
        "parameters": rebased_parameters,
        "metrics": rebased_metrics
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
        "analysis_route",
        "member_set_id",
    ):
        if key in rebased_parameters:
            manifest_data[key] = rebased_parameters[key]

    if rebased_parameters.get("scope_id"):
        scope_context_data = {
            "scope_id": rebased_parameters.get("scope_id", ""),
            "scope_mode": rebased_parameters.get("scope_mode", ""),
            "family": rebased_parameters.get("family", ""),
            "target_species": rebased_parameters.get("target_species", []),
            "reference_species": rebased_parameters.get("reference_species", []),
            "comparative_species": rebased_parameters.get("comparative_species", []),
            "selected_species": rebased_parameters.get("selected_species", []),
        }
        context = build_stage_context(
            stage="interspecies_synteny",
            prefix=prefix,
            scope_data=scope_context_data,
            scope_file=rebased_parameters.get("scope_file", ""),
            output_prefix=prefix,
            analysis_route=rebased_parameters.get("analysis_route", ""),
            member_set_id=rebased_parameters.get("member_set_id", ""),
        )
        if context:
            manifest_data = apply_stage_context(manifest_data, context)

    manifest_path = workspace / "final" / f"{prefix}.interspecies_synteny.manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding='utf-8') as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)
    if rebased_parameters.get("scope_id"):
        scoped_manifest_path = workspace / "final" / f"{prefix}.{sanitize_token(rebased_parameters.get('scope_id', ''))}.interspecies_synteny.manifest.json"
        manifest_data["scope_manifest"] = str(scoped_manifest_path.absolute())
        with open(scoped_manifest_path, "w", encoding='utf-8') as f:
            json.dump(manifest_data, f, indent=2, ensure_ascii=False)

    return manifest_path



def _clean_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _clean_text(value: Any) -> str:
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


def _gene_family_from_prefix(prefix: str) -> str:
    return prefix.split('.', 1)[1] if '.' in prefix else prefix


def _scope_file_record(workspace: Path, input_dir: Path, scope_data: Dict[str, Any], species: str, file_type: str) -> Optional[Path]:
    for record in ((scope_data.get("files") or {}).get(species) or {}).get(file_type, []) or []:
        path_text = record.get("path") or record.get("full_path") or ""
        if path_text:
            path = rebase_current_job_path(workspace, path_text, input_dir=input_dir) or Path(path_text)
            if path.exists():
                return path
    return None


def _add_scope_species_to_groups(workspace: Path, input_dir: Path, groups: Dict[str, Any], scope_data: Dict[str, Any]) -> None:
    family = scope_data.get("family") or groups.get("gene_family") or ""
    target_set = set(scope_data.get("target_species") or [])
    reference_set = set(scope_data.get("reference_species") or [])
    comparative_set = set(scope_data.get("comparative_species") or [])
    for species in target_set | reference_set | comparative_set:
        group_name = "target" if species in target_set else "ref"
        item = groups[group_name].setdefault(species, {"species": species, "gene_family": family, "files": {}})
        item["gene_family"] = item.get("gene_family") or family
        item["role"] = "target" if species in target_set else ("reference" if species in reference_set else "comparative")
        files = item.setdefault("files", {})
        for file_type in ("pep", "family_pep", "gff"):
            path = _scope_file_record(workspace, input_dir, scope_data, species, file_type)
            if path:
                key = "all_pep" if file_type == "pep" else file_type
                files[key] = str(path)


def _apply_scope_selection_to_args(args, scope_data: Dict[str, Any]) -> None:
    targets = [str(x).strip() for x in (scope_data.get("target_species") or []) if str(x).strip()]
    selected = [str(x).strip() for x in (scope_data.get("selected_species") or []) if str(x).strip()]
    refs = [str(x).strip() for x in (scope_data.get("reference_species") or []) if str(x).strip()]
    comps = [str(x).strip() for x in (scope_data.get("comparative_species") or []) if str(x).strip()]
    selected_ref_side = [sp for sp in selected if sp not in set(targets)]
    if not selected_ref_side:
        selected_ref_side = refs + comps
    if targets and not getattr(args, "target_species", None):
        args.target_species = ",".join(targets)
    if selected_ref_side and not getattr(args, "ref_species", None):
        args.ref_species = ",".join(dict.fromkeys(selected_ref_side))


def _resolve_path(path_text: str, input_dir: Path, workspace: Optional[Path] = None) -> Optional[Path]:
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


def _read_config_groups(input_dir: Path, default_family: str) -> Dict[str, Any]:
    config_path = input_dir / "config.xlsx"
    groups = {"target": {}, "ref": {}, "seed_reference": {}, "gene_family": default_family}
    if not config_path.exists():
        return groups
    try:
        import pandas as pd
        df = pd.read_excel(config_path)
    except Exception as exc:
        print(f"[WARN] Failed to read config.xlsx: {exc}")
        return groups
    for _, row in df.iterrows():
        species = _clean_text(row.get("Species_Code", ""))
        row_family = _clean_text(row.get("Gene_Family", ""))
        family = row_family or default_family
        raw_file_type = _clean_text(row.get("File_Type", "")).lower()
        file_scope = _clean_text(row.get("File_Scope", "")).lower()
        file_type = raw_file_type
        original_name = _clean_text(row.get("Original_File_Name", ""))
        role = _clean_text(row.get("Species_Role", "")).lower()
        if not species:
            continue
        if role in {"seed", "seed_reference", "known_family_reference"}:
            group_name = "seed_reference"
        elif role in {"comparative", "comparison", "outgroup", "reference", "ref", "comparison_reference"}:
            group_name = "ref"
        else:
            group_name = "ref" if _clean_bool(row.get("Ref", False)) else "target"
        item = groups[group_name].setdefault(species, {"species": species, "gene_family": family, "files": {}})
        item["gene_family"] = family
        item["role"] = role or group_name
        if raw_file_type in {"pep", "protein"}:
            if file_scope in {"known_family", "family", "seed"} or group_name == "seed_reference":
                file_type = "family_pep"
            else:
                file_type = "all_pep"
        if file_type:
            item["files"][file_type] = original_name
        if row_family:
            groups["gene_family"] = row_family
    return groups


def _read_genome_prepare_artifacts(workspace: Path, input_dir: Path, prefix: str) -> Dict[str, Dict[str, Path]]:
    result: Dict[str, Dict[str, Path]] = {}
    for manifest_path in (workspace / "final" / f"{prefix}.genome_prepare.manifest.json", input_dir / "final" / f"{prefix}.genome_prepare.manifest.json"):
        if not manifest_path.exists():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[WARN] Failed to read genome_prepare manifest {manifest_path}: {exc}")
            continue
        for key, artifact in (data.get("artifacts") or {}).items():
            kind = artifact.get("artifact")
            if "_ref_" in key:
                species = key.split("_ref_", 1)[0]
            else:
                species = key.split("_", 1)[0]
            found = _resolve_path(artifact.get("full_path") or artifact.get("path") or "", input_dir, workspace=workspace)
            if found and kind:
                result.setdefault(species, {})[kind] = found
    return result


def _find_species_file(input_dir: Path, species: str, family: str, file_type: str, original_name: str = "") -> Optional[Path]:
    if file_type == "all_pep":
        patterns = [
            f"{species}.all_pep.fasta", f"{species}.pep.fasta", f"{species}.protein.fasta", f"{species}.faa",
            f"{species}.{family}.all_pep.fasta", f"{species}.{family}.all_pep.fa",
            f"{species}.{family}.protein.fasta", f"{species}.{family}.faa",
            f"{species}.{family}.pep.fasta", f"{species}.{family}.pep.fa",
        ]
    elif file_type == "family_pep":
        patterns = [
            f"{species}.{family}.family_pep.fasta", f"{species}.{family}.family_pep.fa",
            f"{species}.{family}.pep.fasta", f"{species}.{family}.pep.fa",
        ]
    elif file_type == "pep":
        patterns = [
            f"{species}.{family}.pep.fasta", f"{species}.{family}.pep.fa",
            f"{species}.{family}.protein.fasta", f"{species}.{family}.faa",
            f"{species}.pep.fasta", f"{species}.pep.fa", f"{species}.protein.fasta", f"{species}.faa",
        ]
    elif file_type == "gff":
        patterns = [
            f"{species}.{family}_final.gff3", f"{species}.{family}.gff3", f"{species}.{family}.gff",
            f"{species}_final.gff3", f"{species}.gff3", f"{species}.gff",
        ]
    else:
        patterns = []
    if original_name:
        patterns.append(Path(original_name).name)
    for pattern in patterns:
        for candidate in (input_dir / pattern, input_dir / species / pattern):
            if candidate.exists():
                return candidate
    return None


def _find_species_family_pep(
    workspace: Path,
    input_dir: Path,
    species: str,
    family: str,
    original_name: str = "",
) -> Optional[Path]:
    """Resolve per-species family protein FASTA for interspecies tree building."""
    found = _find_species_file(input_dir, species, family, "family_pep", original_name)
    if found:
        return found
    for candidate in (
        workspace / "final" / f"{species}.{family}.final_members.pep.fa",
        workspace / "final" / f"{species}.{family}.curated_members.pep.fa",
        input_dir / "final" / f"{species}.{family}.final_members.pep.fa",
        input_dir / f"{species}.{family}.pep.fa",
        input_dir / f"{species}.{family}.pep.fasta",
    ):
        if candidate.exists():
            return candidate
    return None


def _parse_species_selection(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in str(value).split(',') if item.strip()]


def _filter_group_by_selection(group: Dict[str, Any], selected: List[str]) -> Dict[str, Any]:
    if not selected:
        return group
    selected_set = set(selected)
    return {species: item for species, item in group.items() if species in selected_set}


def _species_default_selection(available: List[str], preferred_ref: bool = False) -> List[str]:
    if not available:
        return []
    if preferred_ref:
        for preferred in ("at", "ath"):
            for species in available:
                if species.lower() == preferred:
                    return [species]
        return [available[0]]
    return list(available)


def _gene_matches_species(gene_id: str, species: str, species_id_sets: Dict[str, set]) -> bool:
    variants = id_variants(gene_id)
    if species in species_id_sets and variants & species_id_sets[species]:
        return True
    return normalize_seq_id(gene_id).lower().startswith(species.lower())


def _filter_fasta_by_species(source: Path, output_file: Path, selected_species: List[str], species_id_sets: Dict[str, set]) -> Path:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    if not selected_species:
        shutil.copy(source, output_file)
        return output_file
    keep = False
    written = 0
    with open_text(source, "rt") as f_in, open(output_file, "w") as f_out:
        for line in f_in:
            if line.startswith(">"):
                gene_id = normalize_seq_id(line[1:])
                keep = any(_gene_matches_species(gene_id, species, species_id_sets) for species in selected_species)
                if keep:
                    written += 1
                    f_out.write(line)
            elif keep:
                f_out.write(line)
    if written == 0:
        raise RuntimeError(f"Selected species {selected_species} produced an empty filtered FASTA from {source}")
    return output_file


def discover_group_inputs(workspace: Path, input_dir: Path, prefix: str, args) -> Dict[str, Any]:
    family = _gene_family_from_prefix(prefix)
    groups = _read_config_groups(input_dir, family)
    scope_data = getattr(args, "scope_data", {}) or {}
    if scope_data:
        groups["gene_family"] = scope_data.get("family") or groups.get("gene_family") or family
        _add_scope_species_to_groups(workspace, input_dir, groups, scope_data)
    # Interspecies synteny compares target species against the reference side.
    # Known-family seed references such as At.SPL are valid reference inputs even
    # when config marks them as seed_reference instead of comparative/ref.
    for species, item in (groups.get("seed_reference") or {}).items():
        groups["ref"].setdefault(species, item)
    artifacts = _read_genome_prepare_artifacts(workspace, input_dir, prefix)
    family = groups.get("gene_family") or family

    available_target_species = list(groups["target"].keys())
    available_ref_species = list(groups["ref"].keys())
    requested_targets = _parse_species_selection(getattr(args, "target_species", None))
    requested_refs = _parse_species_selection(getattr(args, "ref_species", None))
    selected_targets = requested_targets or _species_default_selection(available_target_species, preferred_ref=False)
    selected_refs = requested_refs or _species_default_selection(available_ref_species, preferred_ref=True)

    unknown_targets = [sp for sp in selected_targets if sp not in groups["target"]]
    unknown_refs = [sp for sp in selected_refs if sp not in groups["ref"]]
    if unknown_targets:
        raise RuntimeError(f"Selected target species not found in config Ref=False group: {', '.join(unknown_targets)}")
    if unknown_refs:
        raise RuntimeError(f"Selected reference species not found in config Ref=True group: {', '.join(unknown_refs)}")
    if not selected_targets:
        raise RuntimeError("No target species selected for interspecies analysis")
    if not selected_refs:
        raise RuntimeError("No reference species selected for interspecies analysis")

    groups["target"] = _filter_group_by_selection(groups["target"], selected_targets)
    groups["ref"] = _filter_group_by_selection(groups["ref"], selected_refs)

    for group_name in ("target", "ref"):
        for species, item in groups[group_name].items():
            files = item.setdefault("resolved", {})
            artifact_paths = artifacts.get(species, {})
            original_files = item.get("files", {})
            item_family = item.get("gene_family", family)
            all_original = original_files.get("all_pep", "") or original_files.get("protein", "")
            family_original = original_files.get("family_pep", "")
            legacy_pep_original = original_files.get("pep", "")
            if group_name == "ref" and legacy_pep_original and not family_original:
                family_original = legacy_pep_original
            if group_name == "target" and legacy_pep_original and not all_original:
                all_original = legacy_pep_original
            scope_all_pep = _scope_file_record(workspace, input_dir, scope_data, species, "pep") if scope_data else None
            scope_family_pep = _scope_file_record(workspace, input_dir, scope_data, species, "family_pep") if scope_data else None
            scope_gff = _scope_file_record(workspace, input_dir, scope_data, species, "gff") if scope_data else None
            prepared_all_pep = (
                _find_species_file(input_dir, species, item_family, "all_pep", all_original) or
                artifact_paths.get("all_pep") or artifact_paths.get("ref_all_pep") or artifact_paths.get("pep")
            )
            files["all_pep"] = prepared_all_pep or scope_all_pep
            files["family_pep"] = (
                scope_family_pep or
                _find_species_family_pep(workspace, input_dir, species, item_family, family_original) or
                artifact_paths.get("family_pep") or artifact_paths.get("ref_family_pep") or artifact_paths.get("ref_pep")
            )
            files["pep"] = files.get("all_pep") or files.get("family_pep")
            files["gff"] = (
                artifact_paths.get("gff") or artifact_paths.get("ref_gff") or
                _find_species_file(input_dir, species, item_family, "gff", original_files.get("gff", "")) or
                scope_gff
            )

    analysis_dir = workspace / "interspecies_synteny" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    species_id_sets = collect_species_id_sets(groups)

    raw_target_family_protein = Path(args.target_protein) if args.target_protein else None
    if raw_target_family_protein is None:
        target_peps = [item.get("resolved", {}).get("family_pep") for item in groups["target"].values() if item.get("resolved", {}).get("family_pep")]
        if target_peps:
            raw_target_family_protein = analysis_dir / f"Target_selected.{family}.pep.fasta"
            merge_many_fastas(target_peps, raw_target_family_protein)
        else:
            raw_target_family_protein = workspace / "final" / f"{prefix}.final_members.pep.fa"
    target_family_protein = raw_target_family_protein
    if raw_target_family_protein and raw_target_family_protein.exists():
        selected_target_output = analysis_dir / f"{prefix}.selected_target.family.pep.fa"
        prefix_species = prefix.split(".", 1)[0] if "." in prefix else ""
        single_species_family_fasta = (
            len(selected_targets) == 1
            and prefix_species == selected_targets[0]
            and raw_target_family_protein.name == f"{prefix}.final_members.pep.fa"
        )
        if single_species_family_fasta:
            # Species-specific Identify output may use transcript IDs while the
            # standardized whole-proteome ID set uses protein IDs. It is already
            # the selected species, so do not split it again by species IDs.
            shutil.copy2(raw_target_family_protein, selected_target_output)
            target_family_protein = selected_target_output
        else:
            target_family_protein = _filter_fasta_by_species(
                raw_target_family_protein,
                selected_target_output,
                selected_targets,
                species_id_sets,
            )

    ref_family_protein = Path(args.ref_protein) if args.ref_protein else None
    if ref_family_protein is None:
        ref_peps = [item.get("resolved", {}).get("family_pep") for item in groups["ref"].values() if item.get("resolved", {}).get("family_pep")]
        if ref_peps:
            ref_family_protein = analysis_dir / f"Ref_selected.{family}.pep.fasta"
            merge_many_fastas(ref_peps, ref_family_protein)
        else:
            for candidate in [input_dir / f"Ref_combined.{family}.pep.fasta", input_dir / f"Ref_combined.{family}.pep.fa", input_dir / "Ref_combined.pep.fasta"]:
                if candidate.exists():
                    ref_family_protein = candidate
                    break
    if ref_family_protein is None and raw_target_family_protein and raw_target_family_protein.exists() and selected_refs:
        # Comparative species often have no curated family FASTA. If identify was
        # run on their whole proteomes, split their family members from the same
        # final_members file used for targets.
        try:
            ref_family_protein = _filter_fasta_by_species(
                raw_target_family_protein,
                analysis_dir / f"{prefix}.selected_ref.family.pep.fa",
                selected_refs,
                species_id_sets,
            )
        except Exception as exc:
            print(f"[WARN] Could not derive reference family proteins from final_members: {exc}")
            ref_family_protein = None
    selected_ref_output = analysis_dir / f"{prefix}.selected_ref.family.pep.fa"
    if ref_family_protein and Path(ref_family_protein).exists() and Path(ref_family_protein).resolve() != selected_ref_output.resolve():
        ref_family_protein = _filter_fasta_by_species(
            Path(ref_family_protein),
            selected_ref_output,
            selected_refs,
            species_id_sets,
        )

    target_all_peps = {sp: item.get("resolved", {}).get("all_pep") for sp, item in groups["target"].items() if item.get("resolved", {}).get("all_pep")}
    ref_all_peps = {sp: item.get("resolved", {}).get("all_pep") for sp, item in groups["ref"].items() if item.get("resolved", {}).get("all_pep")}
    target_gffs = {sp: item.get("resolved", {}).get("gff") for sp, item in groups["target"].items() if item.get("resolved", {}).get("gff")}
    ref_gffs = {sp: item.get("resolved", {}).get("gff") for sp, item in groups["ref"].items() if item.get("resolved", {}).get("gff")}

    return {
        "gene_family": family,
        "target_species": selected_targets,
        "ref_species": selected_refs,
        "available_target_species": available_target_species,
        "available_ref_species": available_ref_species,
        "selected_target_species": selected_targets,
        "selected_ref_species": selected_refs,
        "target_family_protein": target_family_protein,
        "ref_family_protein": ref_family_protein,
        "target_all_peps": target_all_peps,
        "ref_all_peps": ref_all_peps,
        "target_gffs": target_gffs,
        "ref_gffs": ref_gffs,
        "groups": groups,
        "scope": scope_data,
    }


def merge_many_fastas(files: List[Path], output_file: Path) -> Path:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as out:
        for fasta in files:
            with open_text(fasta, "rt") as f:
                out.write(f.read().rstrip() + "\n")
    return output_file


def _parse_gff_attr(attr: str) -> Dict[str, str]:
    result = {}
    for item in attr.strip().strip(';').split(';'):
        item = item.strip()
        if not item:
            continue
        if '=' in item:
            k, v = item.split('=', 1)
        elif ' ' in item:
            k, v = item.split(' ', 1)
        else:
            continue
        result[k.strip()] = v.strip().strip('"')
    return result


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


def _select_primary_chromosomes_from_lengths(chrom_lengths: Dict[str, int]) -> Tuple[set, Dict[str, Any]]:
    """Select nuclear chromosome-scale records while falling back on unusual assemblies."""
    records = [(chrom, int(length)) for chrom, length in chrom_lengths.items() if chrom and int(length) > 0]
    total = len(records)
    if not records:
        return set(), {
            "chromosomes_total": 0,
            "chromosomes_plotted": 0,
            "chromosomes_filtered": 0,
            "chromosomes_filter_mode": "empty",
            "chromosomes_filtered_names": [],
        }

    selected: List[Tuple[str, int]]
    mode: str
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
    filtered_names = sorted(chrom for chrom, _ in records if chrom not in selected_names)
    return selected_names, {
        "chromosomes_total": total,
        "chromosomes_plotted": len(selected_names),
        "chromosomes_filtered": max(0, total - len(selected_names)),
        "chromosomes_filter_mode": mode,
        "chromosomes_filtered_names": filtered_names,
    }


def _match_gff_id(attrs: Dict[str, str], protein_ids: set) -> Optional[str]:
    candidates = [attrs.get("ID"), attrs.get("transcript_id"), attrs.get("Name"), attrs.get("Parent")]
    for candidate in candidates:
        if not candidate:
            continue
        for item in str(candidate).split(','):
            norm = normalize_seq_id(item)
            if norm in protein_ids:
                return norm
            for variant in id_variants(norm):
                if variant in protein_ids:
                    return variant
    return None


def build_species_position_files(species: str, gff3: Path, protein_fasta: Path, work_dir: Path) -> Dict[str, Any]:
    """Create filtered MCScanX simplified GFF and JCVI BED for one species."""
    simple_gff = work_dir / f"{species}.gff"
    bed_file = work_dir / f"{species}.bed"
    protein_ids = fasta_id_set(protein_fasta)
    seen = set()
    records: List[Tuple[str, str, str, str]] = []
    chrom_lengths: Dict[str, int] = {}
    features_seen = 0
    work_dir.mkdir(parents=True, exist_ok=True)
    with open_text(gff3, "rt") as f_in:
        for line in f_in:
            if not line.strip() or line.startswith('#'):
                continue
            parts = line.rstrip('\n').split('	')
            if len(parts) < 9 or parts[2] not in {"mRNA", "transcript", "gene"}:
                continue
            features_seen += 1
            attrs = _parse_gff_attr(parts[8])
            gene_id = _match_gff_id(attrs, protein_ids)
            if not gene_id or gene_id in seen:
                continue
            seen.add(gene_id)
            chrom = parts[0]
            start, end = parts[3], parts[4]
            try:
                chrom_lengths[chrom] = max(chrom_lengths.get(chrom, 0), int(float(end)))
            except (TypeError, ValueError):
                pass
            records.append((chrom, start, end, gene_id))

    selected_chroms, filter_metrics = _select_primary_chromosomes_from_lengths(chrom_lengths)
    if not selected_chroms:
        selected_chroms = set(chrom_lengths)
    filtered_records = [record for record in records if record[0] in selected_chroms]
    if not filtered_records and records:
        filtered_records = records
        filter_metrics["chromosomes_filter_mode"] = "fallback_all_records"
        filter_metrics["chromosomes_plotted"] = filter_metrics["chromosomes_total"]
        filter_metrics["chromosomes_filtered"] = 0
        filter_metrics["chromosomes_filtered_names"] = []

    with open(simple_gff, "w") as gff_out, open(bed_file, "w") as bed_out:
        for chrom, start, end, gene_id in filtered_records:
            gff_out.write(f"{chrom}	{gene_id}	{start}	{end}\n")
            bed_out.write(f"{chrom}	{start}	{end}	{gene_id}\n")

    count = len(filtered_records)
    filter_metrics["genes_before_chromosome_filter"] = len(records)
    filter_metrics["genes_after_chromosome_filter"] = count
    filter_metrics["genes_filtered_by_chromosome"] = max(0, len(records) - count)
    return {
        "success": count > 0,
        "species": species,
        "gff": simple_gff,
        "bed": bed_file,
        "gene_count": count,
        "features_seen": features_seen,
        "protein_id_count": len(protein_ids),
        "error": "No GFF IDs matched protein FASTA IDs" if count == 0 else "",
        **filter_metrics,
    }


def collect_family_ids(target_family_fasta: Path, ref_family_fasta: Path, final_id_file: Optional[Path] = None) -> set:
    ids = set()
    for fasta in [target_family_fasta, ref_family_fasta]:
        if fasta and Path(fasta).exists():
            ids.update(fasta_id_set(Path(fasta)))
    if final_id_file and final_id_file.exists():
        with open(final_id_file, "r") as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    ids.update(id_variants(line.strip().split()[0]))
    return ids


def add_artifact(artifacts: Dict[str, Dict], key: str, file_path: Path, rel_path: str, artifact: str, file_type: str) -> None:
    if not file_path or not file_path.exists():
        return
    info = get_file_info(file_path)
    artifacts[key] = {
        "artifact": artifact,
        "path": rel_path,
        "full_path": str(file_path.absolute()),
        "type": file_type,
        "size": info["size"],
        "sha256": info["sha256"],
        "mtime": info["mtime"],
    }


def write_failure_manifest(workspace: Path, job_id: str, run_id: str, prefix: str, start_time: datetime,
                           workspace_root: Path, input_dir: Path, cmd: List[str], error: str,
                           scope_data: Optional[Dict[str, Any]] = None, scope_file: str = "",
                           analysis_route: str = "", member_set_id: str = "") -> Path:
    end_time = datetime.now()
    scope_data = scope_data or {}
    parameters = {
        "prefix": prefix,
        "error": error,
        "scope_file": scope_file,
        "scope_id": scope_data.get("scope_id", ""),
        "scope_mode": scope_data.get("scope_mode", ""),
        "target_species": scope_data.get("target_species", []),
        "reference_species": scope_data.get("reference_species", []),
        "comparative_species": scope_data.get("comparative_species", []),
        "selected_species": scope_data.get("selected_species", []),
        "family": scope_data.get("family", ""),
    }
    if scope_data:
        route = analysis_route or infer_analysis_route("interspecies_synteny", scope_data)
        parameters["analysis_route"] = route
        parameters["member_set_id"] = member_set_id or member_set_id_for(prefix, scope_data, route)
    return write_manifest(
        workspace, job_id, run_id, prefix, start_time, end_time,
        artifacts={}, metrics={"error": error}, parameters=parameters,
        tool_versions={}, workspace_root=workspace_root, input_dir=input_dir, cmd=cmd, status="failed"
    )


def main():
    parser = argparse.ArgumentParser(description="????????")
    parser.add_argument("--prefix", required=True, help="?????? Multi.SPL")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--input-dir")
    parser.add_argument("--ref-species", default=None, help="???????? config.xlsx ??")
    parser.add_argument("--target-species", default=None, help="???????? config.xlsx ??")
    parser.add_argument("--blast-engine", choices=["diamond", "blastp"], default="diamond", help="protein search engine for MCScanX pairwise BLAST file")
    parser.add_argument("--plot-mode", choices=["multi", "pairwise", "both"], default="both", help="synteny plot outputs to generate: multi species, pairwise, or both")
    parser.add_argument("--blast-evalue", default="1e-10", help="evalue cutoff for diamond/blastp search")
    parser.add_argument("--max-target-seqs", type=int, default=5, help="maximum target sequences per query")
    parser.add_argument("--ref-protein", help="?????????")
    parser.add_argument("--target-protein", help="?????????")
    parser.add_argument("--cpu", type=int, default=8)
    parser.add_argument("--log-file", help="??????????????")
    parser.add_argument("--workspace-root", help="??????????manifest?")
    parser.add_argument("--reuse-tree", action="store_true", help="Reuse existing final interspecies tree if present")
    parser.add_argument("--scope-file", help="Gene-family scope JSON from resolve_scope.py")
    parser.add_argument("--analysis-route", help="Scoped analysis route recorded in manifest")
    parser.add_argument("--member-set-id", help="Scoped member set identity recorded in manifest")
    parser.add_argument("--tree-subfamily-arc-enabled", choices=["true", "false"], default="true",
                        help="Enable dynamic subfamily outer arcs in circular interspecies tree plots")
    parser.add_argument("--tree-subfamily-arc-gap", type=float, default=1.2,
                        help="Extra gap between gene labels and circular subfamily arcs")
    parser.add_argument("--tree-subfamily-arc-label-gap", type=float, default=0.6,
                        help="Extra gap between circular subfamily arcs and labels")
    parser.add_argument("--tree-subfamily-arc-label-size", type=float, default=0.0,
                        help="Subfamily arc label size; 0 means automatic")
    args = parser.parse_args()

    start_time = datetime.now()
    run_id = f"interspecies_synteny_{start_time.strftime('%Y%m%d_%H%M%S')}"
    workspace = Path(args.workspace)
    job_id = workspace.name
    input_dir = Path(args.input_dir) if args.input_dir else workspace.parent.parent / "input" / job_id
    workspace_root = Path(args.workspace_root) if args.workspace_root else workspace.parent
    cmd = sys.argv
    scope_data: Dict[str, Any] = {}
    scope_file = ""
    analysis_route = str(args.analysis_route or "").strip()
    member_set_id = str(args.member_set_id or "").strip()

    print("=" * 60)
    print("Interspecies synteny analysis")
    print("=" * 60)

    try:
        if args.scope_file:
            scope_path = Path(args.scope_file)
            if not scope_path.exists():
                raise FileNotFoundError(f"Scope file not found: {args.scope_file}")
            scope_file = str(scope_path.absolute())
            scope_data = read_scope(scope_path)
            args.scope_data = scope_data
            _apply_scope_selection_to_args(args, scope_data)
            if not analysis_route:
                analysis_route = infer_analysis_route("interspecies_synteny", scope_data)
            if not member_set_id and scope_data.get("scope_id") and analysis_route:
                member_set_id = member_set_id_for(args.prefix, scope_data, analysis_route)
        else:
            args.scope_data = {}
        paths = prepare_directories(workspace)
        tool_versions = get_tool_versions()
        discovery = discover_group_inputs(workspace, input_dir, args.prefix, args)
        target_group = "Target_combined"
        ref_group = "Ref_combined"
        print(f"Target species: {', '.join(discovery['target_species']) or 'NONE'}")
        print(f"Reference species: {', '.join(discovery['ref_species']) or 'NONE'}")
        print(f"Target family protein: {discovery['target_family_protein']}")
        print(f"Ref family protein: {discovery['ref_family_protein']}")

        if not discovery["target_family_protein"] or not Path(discovery["target_family_protein"]).exists():
            raise RuntimeError(f"Target family protein missing: {discovery['target_family_protein']}")
        if not discovery["ref_family_protein"] or not Path(discovery["ref_family_protein"]).exists():
            raise RuntimeError("Reference family protein missing; expected Ref_combined.<Family>.pep.fasta or Ref species pep files")

        artifacts = {}
        metrics = {
            "target_species": discovery["target_species"],
            "ref_species": discovery["ref_species"],
            "available_target_species": discovery.get("available_target_species", discovery["target_species"]),
            "available_ref_species": discovery.get("available_ref_species", discovery["ref_species"]),
            "selected_target_species": discovery.get("selected_target_species", discovery["target_species"]),
            "selected_ref_species": discovery.get("selected_ref_species", discovery["ref_species"]),
            "tree_success": False,
            "synteny_success": False,
            "synteny_skipped_reason": "",
            "blast_engine_requested": args.blast_engine,
            "plot_mode": args.plot_mode,
            "multi_plot_generated": False,
            "pairwise_plot_count": 0,
            "pairwise_plot_success": False,
            "blast_engine_used": [],
            "blast_fallback_count": 0,
        }

        print("\nMerging family proteins for tree...")
        merged_fasta = paths["analysis_dir"] / f"{args.prefix}.target_ref.family.pep.fa"
        merge_result = merge_protein_sequences(Path(discovery["target_family_protein"]), Path(discovery["ref_family_protein"]), merged_fasta)
        if not merge_result.get("success"):
            raise RuntimeError(f"Failed to merge family proteins: {merge_result.get('stderr')}")
        metrics["target_family_count"] = merge_result.get("target_count")
        metrics["ref_family_count"] = merge_result.get("ref_count")
        metrics["tree_merged_sequence_count"] = (merge_result.get("target_count") or 0) + (merge_result.get("ref_count") or 0)
        ref_family_sources = [
            str(item.get("resolved", {}).get("family_pep"))
            for item in discovery["groups"].get("ref", {}).values()
            if item.get("resolved", {}).get("family_pep")
        ]
        metrics["tree_ref_family_sources"] = ref_family_sources

        print("\nBuilding interspecies tree...")
        final_tree = paths["final_dir"] / f"{args.prefix}.interspecies_tree.nwk"
        reuse_tree = bool(args.reuse_tree and final_tree.exists())
        if reuse_tree:
            merged_tip_count = sum(1 for line in open_text(merged_fasta, "rt") if line.startswith(">"))
            existing_tip_count = len(_read_tree_tip_labels(final_tree))
            if existing_tip_count != merged_tip_count:
                print(
                    f"Existing tree tip count ({existing_tip_count}) != merged family count ({merged_tip_count}); rebuilding tree"
                )
                reuse_tree = False
        if reuse_tree:
            print(f"Reusing existing interspecies tree: {final_tree}")
            tree_result = {"tree_file": final_tree}
        else:
            tree_result = build_phylogenetic_tree(merged_fasta, paths["analysis_dir"], args.prefix, args.cpu)
        if not tree_result.get("tree_file"):
            raise RuntimeError(f"Tree construction failed: {tree_result.get('error', 'unknown error')}")
        if Path(tree_result["tree_file"]).resolve() != final_tree.resolve():
            shutil.copy(Path(tree_result["tree_file"]), final_tree)
        metrics["tree_success"] = True
        if tree_result.get("iqtree_executable"):
            metrics["iqtree_executable"] = tree_result.get("iqtree_executable")
        if tree_result.get("command"):
            metrics["iqtree_command"] = tree_result.get("command")
        add_artifact(artifacts, "tree_nwk", final_tree, f"final/{args.prefix}.interspecies_tree.nwk", "tree_nwk", "tree")

        gene_species_map = paths["analysis_dir"] / f"{args.prefix}.gene_to_species.tsv"
        write_gene_species_map(Path(discovery["target_family_protein"]), Path(discovery["ref_family_protein"]), discovery["target_species"], discovery["ref_species"], gene_species_map, collect_species_id_sets(discovery["groups"]))
        add_artifact(artifacts, "gene_to_species", gene_species_map, f"interspecies_synteny/analysis/{args.prefix}.gene_to_species.tsv", "gene_to_species", "tsv")

        print("\nPlotting interspecies tree...")
        target_subfamily_file = input_dir / f"{args.prefix}.geneID.subfamily"
        if not target_subfamily_file.exists():
            target_subfamily_file = workspace / "final" / f"{args.prefix}.geneID.subfamily"
        ref_subfamily_file = input_dir / f"SPL_{discovery['ref_species'][0]}.subfamily" if discovery["ref_species"] else input_dir / "SPL_ref.subfamily"
        if not ref_subfamily_file.exists():
            candidates = sorted(input_dir.glob("SPL_*.subfamily")) + sorted(input_dir.glob("*.SPL.subfamily"))
            ref_subfamily_file = candidates[0] if candidates else ref_subfamily_file

        subfamily_candidates: List[Path] = [
            target_subfamily_file,
            ref_subfamily_file,
            input_dir / f"{args.prefix}.target_plus_reference.geneID.subfamily",
            workspace / "final" / f"{args.prefix}.target_plus_reference.geneID.subfamily",
        ]
        for species_value in list(discovery.get("target_species") or []) + list(discovery.get("ref_species") or []):
            species_stem = str(species_value).strip()
            if not species_stem:
                continue
            subfamily_candidates.extend([
                input_dir / f"{species_stem}.SPL.target_plus_reference.geneID.subfamily",
                workspace / "final" / f"{species_stem}.SPL.target_plus_reference.geneID.subfamily",
                input_dir / f"{species_stem}.SPL.geneID.subfamily",
                workspace / "final" / f"{species_stem}.SPL.geneID.subfamily",
            ])
            subfamily_candidates.extend(sorted((workspace / "final").glob(f"{species_stem}.*.geneID.subfamily"), key=lambda p: p.stat().st_mtime, reverse=True))
        merged_subfamily_file = _merge_matching_subfamily_files(subfamily_candidates, final_tree, paths["analysis_dir"], args.prefix)
        if merged_subfamily_file:
            target_subfamily_file = merged_subfamily_file
            ref_subfamily_file = None

        tree_plot_result = plot_interspecies_tree(
            final_tree,
            paths["final_dir"],
            args.prefix,
            target_group,
            ref_group,
            target_subfamily_file if target_subfamily_file and target_subfamily_file.exists() else None,
            ref_subfamily_file if ref_subfamily_file and ref_subfamily_file.exists() else None,
            gene_species_map,
            subfamily_arc_enabled=str(args.tree_subfamily_arc_enabled).lower() != "false",
            subfamily_arc_gap=args.tree_subfamily_arc_gap,
            subfamily_arc_label_gap=args.tree_subfamily_arc_label_gap,
            subfamily_arc_label_size=args.tree_subfamily_arc_label_size,
        )
        if tree_plot_result.get("pdf_file"):
            add_artifact(artifacts, "tree_pdf", tree_plot_result["pdf_file"], f"final/{args.prefix}.interspecies_tree.pdf", "tree_pdf", "plot")
        if tree_plot_result.get("png_file"):
            add_artifact(artifacts, "tree_png", tree_plot_result["png_file"], f"final/{args.prefix}.interspecies_tree.png", "tree_png", "plot")
        if tree_plot_result.get("circular_pdf_file"):
            add_artifact(artifacts, "tree_circular_pdf", tree_plot_result["circular_pdf_file"], f"final/{args.prefix}.interspecies_tree.circular_tree.pdf", "tree_circular_pdf", "plot")
        if tree_plot_result.get("circular_png_file"):
            add_artifact(artifacts, "tree_circular_png", tree_plot_result["circular_png_file"], f"final/{args.prefix}.interspecies_tree.circular_tree.png", "tree_circular_png", "plot")

        synteny_plot_result = {"success": False}
        pair_results: List[Dict[str, Any]] = []
        species_position_results: Dict[str, Dict[str, Any]] = {}

        missing = []
        if not discovery["target_all_peps"]:
            missing.append("target all protein")
        if not discovery["ref_all_peps"]:
            missing.append("reference all protein")
        if not discovery["target_gffs"]:
            missing.append("target GFF")
        if not discovery["ref_gffs"]:
            missing.append("reference GFF")

        if not missing:
            print("\nPreparing per-species GFF/BED files for pairwise MCScanX...")
            all_species = discovery["target_species"] + discovery["ref_species"]
            for species in all_species:
                pep = discovery["target_all_peps"].get(species) or discovery["ref_all_peps"].get(species)
                gff = discovery["target_gffs"].get(species) or discovery["ref_gffs"].get(species)
                if not pep or not gff:
                    species_position_results[species] = {"success": False, "error": "missing pep or gff"}
                    continue
                result = build_species_position_files(species, Path(gff), Path(pep), paths["analysis_dir"])
                species_position_results[species] = result
                if result.get("success"):
                    add_artifact(artifacts, f"{species}_mcscanx_gff", result["gff"], f"interspecies_synteny/analysis/{species}.gff", "mcscanx_gff", "gff")
                    add_artifact(artifacts, f"{species}_bed", result["bed"], f"interspecies_synteny/analysis/{species}.bed", "jcvi_bed", "bed")
                print(
                    f"  {species}: {result.get('gene_count', 0)} matched genes after chromosome filter "
                    f"({result.get('chromosomes_plotted', 'N/A')}/{result.get('chromosomes_total', 'N/A')} seqids plotted; "
                    f"filtered genes={result.get('genes_filtered_by_chromosome', 0)})"
                )

            print("\nRunning pairwise target-ref MCScanX...")
            for target_species in discovery["target_species"]:
                for ref_species in discovery["ref_species"]:
                    pair_key = f"{target_species}.{ref_species}"
                    pair = {"pair": pair_key, "target_species": target_species, "ref_species": ref_species, "success": False}
                    target_ready = species_position_results.get(target_species, {}).get("success")
                    ref_ready = species_position_results.get(ref_species, {}).get("success")
                    target_pep = discovery["target_all_peps"].get(target_species)
                    ref_pep = discovery["ref_all_peps"].get(ref_species)
                    if not (target_ready and ref_ready and target_pep and ref_pep):
                        pair["error"] = "missing matched GFF/BED or protein FASTA"
                        pair_results.append(pair)
                        print(f"  {pair_key}: skipped ({pair['error']})")
                        continue
                    blast_result = run_reciprocal_blast(Path(target_pep), Path(ref_pep), paths["analysis_dir"], target_species, ref_species, args.cpu, args.blast_engine, args.blast_evalue, args.max_target_seqs)
                    pair["blast_success"] = bool(blast_result.get("success"))
                    pair["blast_engine_requested"] = blast_result.get("engine_requested", args.blast_engine)
                    pair["blast_engine_used"] = blast_result.get("engine_used")
                    pair["fallback_from"] = blast_result.get("fallback_from")
                    pair["blast_commands"] = blast_result.get("commands", [])
                    pair["blast_stderr"] = blast_result.get("stderr", "")
                    pair["diamond_stderr"] = blast_result.get("diamond_stderr", "")
                    pair["blast_output_stats"] = blast_result.get("output_stats", {})
                    if blast_result.get("engine_used"):
                        metrics.setdefault("blast_engine_used", []).append(blast_result.get("engine_used"))
                    if blast_result.get("fallback_from"):
                        metrics["blast_fallback_count"] = metrics.get("blast_fallback_count", 0) + 1
                    if blast_result.get("output_file"):
                        pair["blast_file"] = str(blast_result["output_file"])
                        add_artifact(artifacts, f"{pair_key}_blast", blast_result["output_file"], f"interspecies_synteny/analysis/{pair_key}.blast", "blast", "data")
                    if not blast_result.get("success"):
                        pair["error"] = "BLAST failed or produced no hits"
                        pair_results.append(pair)
                        print(f"  {pair_key}: {pair['error']}")
                        continue
                    mcscanx_result = run_mcscanx_interspecies(paths["analysis_dir"], target_species, ref_species)
                    pair_gff = paths["analysis_dir"] / f"{pair_key}.gff"
                    if pair_gff.exists():
                        pair["pair_gff"] = str(pair_gff)
                        add_artifact(artifacts, f"{pair_key}_gff", pair_gff, f"interspecies_synteny/analysis/{pair_key}.gff", "pair_mcscanx_gff", "gff")
                    pair["mcscanx_success"] = bool(mcscanx_result.get("success"))
                    if mcscanx_result.get("collinearity_file"):
                        pair["collinearity_file"] = str(mcscanx_result["collinearity_file"])
                        add_artifact(artifacts, f"{pair_key}_collinearity", mcscanx_result["collinearity_file"], f"interspecies_synteny/analysis/{pair_key}.collinearity", "collinearity", "data")
                    if not mcscanx_result.get("success"):
                        pair["error"] = "MCScanX did not produce collinearity"
                        pair_results.append(pair)
                        print(f"  {pair_key}: {pair['error']}")
                        continue
                    simple_result = convert_collinearity_to_simple(paths["analysis_dir"], Path(mcscanx_result["collinearity_file"]), target_species, ref_species)
                    if not simple_result.get("success") and blast_result.get("output_file"):
                        fallback_result = convert_blast_to_family_simple(paths["analysis_dir"], Path(blast_result["output_file"]), target_species, ref_species)
                        if fallback_result.get("success"):
                            fallback_result["note"] = "MCScanX produced no whole-genome blocks; using family-only BLAST links for JCVI visualization"
                            simple_result = fallback_result
                    pair.update({k: str(v) if isinstance(v, Path) else v for k, v in simple_result.items()})
                    pair["success"] = bool(simple_result.get("success"))
                    if simple_result.get("anchors_file"):
                        add_artifact(artifacts, f"{pair_key}_anchors", simple_result["anchors_file"], f"interspecies_synteny/analysis/{pair_key}.anchors", "anchors", "data")
                    if simple_result.get("simple_file"):
                        add_artifact(artifacts, f"{pair_key}_anchors_simple", simple_result["simple_file"], f"interspecies_synteny/analysis/{pair_key}.anchors.simple", "anchors_simple", "data")
                    pair_results.append(pair)
                    print(f"  {pair_key}: {'success' if pair['success'] else 'anchors conversion failed'}")

            family_ids = collect_family_ids(Path(discovery["target_family_protein"]), Path(discovery["ref_family_protein"]), workspace / "final" / f"{args.prefix}.final_members.id")
            successful_pairs = [p for p in pair_results if p.get("success")]
            pairwise_plot_result = {"success": False, "pairs": [], "pairwise_plot_count": 0}
            if args.plot_mode in {"multi", "both"}:
                synteny_plot_result = plot_multi_species_synteny(
                    paths["analysis_dir"], paths["final_dir"], args.prefix,
                    discovery["target_species"] + discovery["ref_species"], pair_results, family_ids,
                )
            if args.plot_mode in {"pairwise", "both"}:
                pairwise_plot_result = plot_pairwise_synteny(
                    paths["analysis_dir"], paths["final_dir"], args.prefix, pair_results, family_ids,
                )
            metrics["multi_plot_generated"] = bool(synteny_plot_result.get("success"))
            metrics["pairwise_plot_success"] = bool(pairwise_plot_result.get("success"))
            metrics["pairwise_plot_count"] = int(pairwise_plot_result.get("pairwise_plot_count") or 0)
            metrics["pairwise_plots"] = pairwise_plot_result.get("pairs", [])
            requested_plot_success = True
            if args.plot_mode in {"multi", "both"}:
                requested_plot_success = requested_plot_success and bool(synteny_plot_result.get("success"))
            if args.plot_mode in {"pairwise", "both"}:
                requested_plot_success = requested_plot_success and bool(pairwise_plot_result.get("success"))
            metrics["synteny_success"] = requested_plot_success and len(successful_pairs) == len(pair_results) and bool(pair_results)
            if not successful_pairs:
                metrics["synteny_skipped_reason"] = synteny_plot_result.get("error") or pairwise_plot_result.get("error") or "No target-ref pair produced JCVI anchors"
            elif not requested_plot_success:
                metrics["synteny_skipped_reason"] = synteny_plot_result.get("error") or pairwise_plot_result.get("error") or "Requested synteny plot output was not generated"
        else:
            metrics["synteny_skipped_reason"] = "Missing full data for MCScanX: " + ", ".join(missing)
            print(metrics["synteny_skipped_reason"])

        metrics["species_position_results"] = {
            sp: {k: str(v) if isinstance(v, Path) else v for k, v in result.items()}
            for sp, result in species_position_results.items()
        }
        metrics["pairwise_synteny"] = pair_results
        metrics["pair_success_count"] = sum(1 for p in pair_results if p.get("success"))
        metrics["pair_total_count"] = len(pair_results)

        if synteny_plot_result.get("pdf_file"):
            add_artifact(artifacts, "synteny_pdf", synteny_plot_result["pdf_file"], f"final/{args.prefix}.interspecies_synteny.pdf", "synteny_pdf", "plot")
        if synteny_plot_result.get("png_file"):
            add_artifact(artifacts, "synteny_png", synteny_plot_result["png_file"], f"final/{args.prefix}.interspecies_synteny.png", "synteny_png", "plot")
        if synteny_plot_result.get("marked_pdf_file"):
            add_artifact(artifacts, "synteny_marked_pdf", synteny_plot_result["marked_pdf_file"], f"final/{args.prefix}.interspecies_synteny.marked.pdf", "synteny_marked_pdf", "plot")
        if synteny_plot_result.get("marked_png_file"):
            add_artifact(artifacts, "synteny_marked_png", synteny_plot_result["marked_png_file"], f"final/{args.prefix}.interspecies_synteny.marked.png", "synteny_marked_png", "plot")
        if synteny_plot_result.get("seqids_file"):
            add_artifact(artifacts, "jcvi_seqids", synteny_plot_result["seqids_file"], f"interspecies_synteny/analysis/{args.prefix}.seqids", "jcvi_seqids", "data")
        if synteny_plot_result.get("layout_file"):
            add_artifact(artifacts, "jcvi_layout", synteny_plot_result["layout_file"], f"interspecies_synteny/analysis/{args.prefix}.layout", "jcvi_layout", "data")
        for pair_plot in (metrics.get("pairwise_plots") or []):
            pair_key = sanitize_token(pair_plot.get("pair") or "pair")
            for artifact_key, file_key, artifact_name, file_type in [
                ("pairwise_pdf", "pdf_file", "pairwise_synteny_pdf", "plot"),
                ("pairwise_png", "png_file", "pairwise_synteny_png", "plot"),
                ("pairwise_marked_pdf", "marked_pdf_file", "pairwise_synteny_marked_pdf", "plot"),
                ("pairwise_marked_png", "marked_png_file", "pairwise_synteny_marked_png", "plot"),
                ("pairwise_seqids", "seqids_file", "pairwise_jcvi_seqids", "data"),
                ("pairwise_layout", "layout_file", "pairwise_jcvi_layout", "data"),
                ("pairwise_layout_mark", "layout_mark_file", "pairwise_jcvi_layout_mark", "data"),
            ]:
                file_text = pair_plot.get(file_key) or ""
                if not file_text:
                    continue
                file_path = Path(file_text)
                if not file_path.exists():
                    continue
                rel = f"final/{file_path.name}" if file_path.parent == paths["final_dir"] else f"interspecies_synteny/analysis/{file_path.name}"
                add_artifact(artifacts, f"{pair_key}_{artifact_key}", file_path, rel, artifact_name, file_type)

        end_time = datetime.now()
        status = "success" if metrics["tree_success"] and metrics["synteny_success"] else "partial_success"
        parameters = {
            "prefix": args.prefix,
            "gene_family": discovery["gene_family"],
            "target_group": target_group,
            "ref_group": ref_group,
            "target_species": discovery["target_species"],
            "ref_species": discovery["ref_species"],
            "available_target_species": discovery.get("available_target_species", discovery["target_species"]),
            "available_ref_species": discovery.get("available_ref_species", discovery["ref_species"]),
            "selected_target_species": discovery.get("selected_target_species", discovery["target_species"]),
            "selected_ref_species": discovery.get("selected_ref_species", discovery["ref_species"]),
            "target_protein_spl": str(discovery["target_family_protein"]),
            "ref_protein_spl": str(discovery["ref_family_protein"]),
            "input_dir": str(input_dir),
            "cpu": args.cpu,
            "blast_engine_requested": args.blast_engine,
            "plot_mode": args.plot_mode,
            "blast_evalue": args.blast_evalue,
            "max_target_seqs": args.max_target_seqs,
            "tree_subfamily_arc_enabled": str(args.tree_subfamily_arc_enabled).lower() != "false",
            "tree_subfamily_arc_gap": args.tree_subfamily_arc_gap,
            "tree_subfamily_arc_label_gap": args.tree_subfamily_arc_label_gap,
            "tree_subfamily_arc_label_size": args.tree_subfamily_arc_label_size,
        }
        if scope_data:
            parameters.update({
                "scope_file": scope_file,
                "scope_id": scope_data.get("scope_id", ""),
                "scope_mode": scope_data.get("scope_mode", ""),
                "target_species": scope_data.get("target_species", []),
                "reference_species": scope_data.get("reference_species", []),
                "comparative_species": scope_data.get("comparative_species", []),
                "selected_species": scope_data.get("selected_species", []),
                "family": scope_data.get("family", ""),
            })
            route = analysis_route or infer_analysis_route("interspecies_synteny", scope_data)
            parameters["analysis_route"] = route
            parameters["member_set_id"] = member_set_id or member_set_id_for(args.prefix, scope_data, route)
        report_path = write_report(workspace, args.prefix, metrics, parameters, tool_versions, start_time, end_time, (end_time - start_time).total_seconds(), cmd)
        add_artifact(artifacts, "report", report_path, f"final/{args.prefix}.interspecies_synteny_report.md", "report", "report")
        manifest_path = write_manifest(workspace, job_id, run_id, args.prefix, start_time, end_time, artifacts, metrics, parameters, tool_versions, workspace_root, input_dir, cmd, status=status)
        add_artifact(artifacts, "manifest", manifest_path, f"final/{args.prefix}.interspecies_synteny.manifest.json", "manifest", "json")
        write_manifest(workspace, job_id, run_id, args.prefix, start_time, end_time, artifacts, metrics, parameters, tool_versions, workspace_root, input_dir, cmd, status=status)
        print(f"Interspecies synteny finished with status: {status}")
        print(f"Manifest: {manifest_path}")
        return 0 if metrics["tree_success"] else 1
    except Exception as exc:
        print(f"Interspecies synteny failed: {exc}")
        try:
            write_failure_manifest(
                workspace, job_id, run_id, args.prefix, start_time,
                workspace_root, input_dir, cmd, str(exc),
                scope_data=scope_data, scope_file=scope_file,
                analysis_route=analysis_route, member_set_id=member_set_id,
            )
        except Exception as manifest_exc:
            print(f"Failed to write failure manifest: {manifest_exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
