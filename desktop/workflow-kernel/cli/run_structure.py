#!/usr/bin/env python3
"""
Structure stage: 基因结构可视化
符合 workspace_contract_v1.md 和 manifest_v1.schema.json 契约
"""

import os
import shutil
import argparse
import json
import subprocess
import sys
import re
import uuid
import gzip
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import hashlib

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.gene_family_scope import read_scope, sanitize_token
from gfa_core.gene_family_stage_context import apply_stage_context, build_stage_context, user_output_prefix_for, write_legacy_manifest_copy
from gfa_core.mas.effective_members import MemberResolutionError, resolve_effective_members


CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")


def display_path(path: Path | str) -> str:
    """Return the user-facing symlink path instead of resolving data-root links."""
    return str(Path(path).absolute())


def current_input_dir(workspace: Path) -> Path:
    return workspace.parent.parent / "input" / workspace.name


def inside_current_job(workspace: Path, path: Path) -> bool:
    input_dir = current_input_dir(workspace)
    try:
        resolved = path.resolve()
        return resolved.is_relative_to(workspace.resolve()) or resolved.is_relative_to(input_dir.resolve())
    except OSError:
        return False


def rebase_current_job_path(workspace: Path, path: Path) -> Optional[Path]:
    """Map copied legacy workspace/input paths back onto the current job roots."""
    parts = path.parts
    for root_name, current_root in (("workspace", workspace), ("input", current_input_dir(workspace))):
        for idx, part in enumerate(parts):
            if part != root_name:
                continue
            if idx + 1 >= len(parts) or parts[idx + 1] != workspace.name:
                continue
            suffix = Path(*parts[idx + 2:]) if idx + 2 < len(parts) else Path()
            candidate = current_root / suffix
            if candidate.exists():
                return candidate
    return None


def resolve_job_path(workspace: Path, raw_path: str | Path, manifest_path: Optional[Path] = None) -> Optional[Path]:
    """Resolve a copied job path without trusting stale absolute paths."""
    raw_text = str(raw_path or "").strip()
    if not raw_text:
        return None
    path = Path(raw_text)
    if path.is_absolute():
        if inside_current_job(workspace, path):
            return path
        rebased = rebase_current_job_path(workspace, path)
        if rebased:
            return rebased
        return None

    candidates: List[Path] = [workspace / path, current_input_dir(workspace) / path]
    if manifest_path:
        candidates.extend([manifest_path.parent / path, manifest_path.parent / path.name])
    if str(path).startswith("final/") and manifest_path:
        candidates.append(manifest_path.parent / path.name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def manifest_artifact_path(workspace: Path, manifest_path: Path, artifact: Dict) -> Optional[Path]:
    if not isinstance(artifact, dict):
        return None
    for key in ("path", "relpath_or_hint", "file", "full_path"):
        resolved = resolve_job_path(workspace, artifact.get(key) or "", manifest_path)
        if resolved:
            return resolved
    return None


def get_tool_versions() -> Dict[str, str]:
    """获取工具版本信息"""
    versions = {}
    try:
        r = subprocess.run(["R", "--version"], capture_output=True, text=True, check=False)
        if r.returncode == 0:
            versions["R"] = r.stdout.split("\n")[0]
    except:
        versions["R"] = "not_found"
    
    try:
        import Bio
        versions["biopython"] = Bio.__version__
    except:
        versions["biopython"] = "not_found"
    
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


def read_gene_ids(id_file: Path) -> List[str]:
    """读取基因 ID 列表"""
    ids = []
    with open(id_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                ids.append(line)
    return ids


def open_text_auto(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="ignore")
    return open(path, "r", encoding="utf-8", errors="ignore")


def read_tree_order(tree_file: Path) -> List[str]:
    """从树文件读取叶节点顺序"""
    try:
        from Bio import Phylo
        tree = Phylo.read(tree_file, 'newick')
        tips = [clade.name for clade in tree.get_terminals()]
        return tips if tips else []
    except:
        return []



def should_strip_version(raw_id: str) -> bool:
    lowered = (raw_id or "").lower()
    return not (lowered.startswith("evm.model.") or lowered.startswith("evm.tu."))


def strip_gff_id(raw_id: Optional[str]) -> str:
    if not raw_id:
        return ""
    value = str(raw_id).strip()
    # Tree/Newick labels may replace ':' with '_' (mRNA:HanXR -> mRNA_HanXR).
    # Strip repeated typed wrappers so GFF attributes, FASTA IDs and tree-safe
    # labels all resolve to the same member accession.
    while True:
        updated = re.sub(r"^(gene|transcript|CDS|mRNA)[:_]", "", value, flags=re.IGNORECASE)
        if updated == value:
            break
        value = updated
    value = re.sub(r'_[PT]\d+$', '', value)
    if should_strip_version(value):
        value = re.sub(r'\.\d+$', '', value)
    return value


def id_variants(raw_id: Optional[str]) -> set[str]:
    value = strip_gff_id(raw_id)
    if not value:
        return set()
    variants = {value}
    if should_strip_version(value) and re.search(r'\.\d+$', value):
        variants.add(re.sub(r'\.\d+$', '', value))
    variants.add(re.sub(r'_[PT]\d+$', '', value))
    expanded = set()
    for item in variants:
        if item:
            expanded.update({item, item.upper(), item.lower()})
            expanded.update({f"gene:{item}", f"transcript:{item}", f"CDS:{item}", f"mRNA:{item}"})
    return expanded


def build_target_id_lookup(gene_ids: List[str]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for gid in gene_ids:
        canonical = str(gid).strip()
        if not canonical:
            continue
        for variant in id_variants(canonical):
            lookup.setdefault(variant, canonical)
    return lookup


def canonical_target_id(raw_id: Optional[str], target_lookup: Dict[str, str]) -> Optional[str]:
    for variant in id_variants(raw_id):
        if variant in target_lookup:
            return target_lookup[variant]
    return None


def parse_gff3_attributes(attr: str) -> Dict[str, str]:
    attr_map: Dict[str, str] = {}
    for item in str(attr or "").split(";"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        attr_map[key] = value
    return attr_map


def canonical_from_attr_values(attr_map: Dict[str, str], keys: List[str], target_lookup: Dict[str, str]) -> Optional[str]:
    for key in keys:
        value = attr_map.get(key)
        if not value:
            continue
        for token in str(value).split(","):
            canonical = canonical_target_id(token, target_lookup)
            if canonical:
                return canonical
    return None

def resolve_gff3_path(ws: Path, prefix: str, cli_gff3: Optional[str] = None) -> Path:
    """
    解析 GFF3 文件路径
    
    命名规则: {species_short}_final.gff3 (如 Ft_final.gff3)
    优先级: final/ > input/
    
    Args:
        ws: workspace 目录
        prefix: 分析前缀 (如 Ft.SPL)，从中提取物种简写 Ft
        cli_gff3: 命令行指定的 GFF3 路径（可选）
    
    Returns:
        GFF3 文件路径
    
    Raises:
        FileNotFoundError: 找不到符合命名规范的 GFF3 文件
    """
    # 方式一：命令行指定（如果提供）
    if cli_gff3:
        gff3_path = resolve_job_path(ws, cli_gff3) or Path(cli_gff3)
        if gff3_path.exists():
            print(f"[INFO] Using specified GFF3: {gff3_path}")
            return gff3_path
        else:
            raise FileNotFoundError(f"Specified GFF3 not found: {cli_gff3}")
    
    # 从 prefix 提取物种简写 (Ft.SPL -> Ft)
    species_short = prefix.split('.')[0] if '.' in prefix else prefix
    
    # 标准命名: Ft_final.gff3
    gff3_name = f"{species_short}_final.gff3"
    
    final_dir = ws / "final"
    input_dir = ws / "input"
    
    # 也检查 bbav2/input/{job_id}/ 路径
    job_id = ws.name
    alt_input_dir = ws.parent.parent / "input" / job_id
    
    # 优先级: final/ > workspace/input/ > bbav2/input/{job_id}/
    search_paths = []
    if final_dir.exists():
        search_paths.append(final_dir / gff3_name)
    if input_dir.exists():
        search_paths.append(input_dir / gff3_name)
    if alt_input_dir.exists():
        search_paths.append(alt_input_dir / gff3_name)
    
    # 查找
    for candidate in search_paths:
        if candidate.exists():
            print(f"[INFO] Found GFF3: {candidate}")
            return candidate
    
    # 都找不到，报错
    searched = ", ".join([str(p) for p in search_paths]) if search_paths else "no directories available"
    raise FileNotFoundError(
        f"Cannot find GFF3 file '{gff3_name}' for prefix '{prefix}'. "
        f"Searched in: {searched}. "
        f"Please ensure the file follows naming convention: {{species_short}}_final.gff3 "
        f"or specify --gff3 explicitly."
    )


def find_prepared_gff3(ws: Path, species: str) -> Optional[Path]:
    job_id = ws.name
    candidates = [
        ws / "final" / f"{species}_final.gff3",
        ws / "input" / f"{species}_final.gff3",
        ws.parent.parent / "input" / job_id / f"{species}_final.gff3",
        ws.parent.parent / "input" / job_id / species / f"{species}_final.gff3",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate.absolute()
    return None


def resolve_scope_gff3_paths(scope_data: Dict, ws: Optional[Path] = None) -> List[Tuple[str, Path]]:
    target_species = [str(item).strip() for item in scope_data.get("target_species", []) if str(item).strip()]
    selected_species = [str(item).strip() for item in scope_data.get("selected_species", []) if str(item).strip()]
    scope_mode = str(scope_data.get("scope_mode") or "")
    species_for_structure = target_species
    if not species_for_structure:
        raise FileNotFoundError("Scope file does not define species for structure stage")

    target_set = set(target_species)
    resolved: List[Tuple[str, Path]] = []
    missing: List[str] = []
    files = scope_data.get("files", {})
    for species in species_for_structure:
        selected: Optional[Path] = None
        if ws is not None and species in target_set:
            selected = find_prepared_gff3(ws, species)
        species_files = files.get(species, {})
        if selected is None:
            for record in species_files.get("gff", []):
                path_text = str(record.get("path") or "")
                path = resolve_job_path(ws, path_text) if (ws is not None and path_text) else (Path(path_text) if path_text else None)
                if path and path.exists() and path.stat().st_size > 0:
                    selected = path
                    break
        if selected:
            resolved.append((species, selected.absolute()))
        else:
            missing.append(species)

    if missing:
        raise FileNotFoundError(
            "Missing target GFF3 files in scope for structure stage: " + ", ".join(missing)
        )
    return resolved


def resolve_scope_tree_file(ws: Path, prefix: str, scope_data: Optional[Dict], cli_tree: Optional[str] = None) -> Path:
    final_dir = ws / "final"
    if cli_tree:
        tree_path = resolve_job_path(ws, cli_tree) or Path(cli_tree)
        if tree_path.exists() and tree_path.stat().st_size > 0:
            return tree_path.absolute()
        raise FileNotFoundError(f"Specified tree file not found: {cli_tree}")

    manifest_candidates: List[Path] = []
    scope_id = str((scope_data or {}).get("scope_id") or "").strip()
    if scope_id:
        manifest_candidates.append(final_dir / f"{prefix}.{sanitize_token(scope_id)}.phylogeny.manifest.json")
    else:
        manifest_candidates.append(final_dir / f"{prefix}.phylogeny.manifest.json")

    for manifest_file in manifest_candidates:
        if not manifest_file.exists() or manifest_file.stat().st_size == 0:
            continue
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        tree_path = None
        artifact = (manifest.get("artifacts", {}) or {}).get("tree")
        if isinstance(artifact, dict):
            tree_path = manifest_artifact_path(ws, manifest_file, artifact)
        elif isinstance(artifact, str):
            tree_path = resolve_job_path(ws, artifact, manifest_file)
        if tree_path and tree_path.exists() and tree_path.stat().st_size > 0:
            return tree_path.absolute()

    fallback = final_dir / f"{prefix}.tree.nwk"
    if not scope_id and fallback.exists() and fallback.stat().st_size > 0:
        return fallback.absolute()
    raise FileNotFoundError(f"Tree file not found for prefix {prefix}. Please run phylogeny first or specify --tree.")


def filter_scope_gff3(
    gff3_sources: List[Tuple[str, Path]],
    gene_ids: List[str],
    output_gff3: Path,
    structure_dir: Path,
    prefix: str,
) -> Tuple[bool, Dict]:
    if len(gff3_sources) == 1:
        return filter_gff3(gff3_sources[0][1], gene_ids, output_gff3)

    aggregate = {
        "total_lines": 0,
        "filtered_lines": 0,
        "genes_found": 0,
        "mrna_count": 0,
        "cds_count": 0,
        "utr_count": 0,
        "per_species": {},
    }
    temp_paths: List[Path] = []
    for species, gff3_file in gff3_sources:
        temp_path = structure_dir / f"{prefix}.{species}.structure.filtered.gff3"
        success, stats = filter_gff3(gff3_file, gene_ids, temp_path)
        if not success:
            return False, aggregate
        temp_paths.append(temp_path)
        aggregate["per_species"][species] = stats
        for key in ("total_lines", "filtered_lines", "genes_found", "mrna_count", "cds_count", "utr_count"):
            aggregate[key] += int(stats.get(key, 0))

    with output_gff3.open("w", encoding="utf-8") as out:
        for temp_path in temp_paths:
            with temp_path.open(encoding="utf-8", errors="ignore") as handle:
                for raw in handle:
                    out.write(raw)
    return True, aggregate


def resolve_subfamily_file(ws: Path, prefix: str, cli_subfamily: Optional[str] = None, output_prefix: Optional[str] = None, scope_data: Optional[Dict] = None) -> Optional[Path]:
    """
    解析亚家族文件路径
    
    命名规则: {prefix}.confirmed_subfamily.tsv (如 Ft.SPL.confirmed_subfamily.tsv)
    优先级: final/ > input/
    
    Args:
        ws: workspace 目录
        prefix: 分析前缀 (如 Ft.SPL)
        cli_subfamily: 命令行指定的亚家族文件路径（可选）
    
    Returns:
        亚家族文件路径，或 None（可选）
    """
    # 方式一：命令行指定
    if cli_subfamily:
        if str(cli_subfamily).strip().lower() in {"none", "skip", "disabled"}:
            print("[INFO] Subfamily coloring explicitly disabled")
            return None
        subfamily_path = resolve_job_path(ws, cli_subfamily) or Path(cli_subfamily)
        if subfamily_path.exists():
            print(f"[INFO] Using specified subfamily file: {subfamily_path}")
            return subfamily_path
        else:
            print(f"[WARNING] Specified subfamily file not found: {cli_subfamily}")
            return None
    
    # 自动查找只接受 confirmed_subfamily；prediction/geneID 必须由 --subfamily 显式传入。
    names = []
    bases = []
    for base in [output_prefix, prefix]:
        if base and base not in bases:
            bases.append(base)
    if output_prefix and ".subfamily_evidence" in output_prefix:
        bases.append(output_prefix.replace(".subfamily_evidence", ".subfamily_prediction"))
    scope_mode = str((scope_data or {}).get("scope_mode") or "").strip()
    family = str((scope_data or {}).get("family") or (prefix.split(".")[-1] if "." in prefix else "")).strip()
    targets = [str(x) for x in (scope_data or {}).get("target_species", []) if str(x).strip()]
    refs = [str(x) for x in (scope_data or {}).get("reference_species", []) if str(x).strip()]
    if scope_mode == "target_plus_reference" and len(targets) == 1 and family:
        bases.append(f"{targets[0]}.{family}.target_plus_reference")
        ref_part = "-".join(refs) if refs else ""
        if ref_part:
            bases.append(f"Target.{family}.target-{targets[0]}.ref-{ref_part}.subfamily_prediction")
    seen_bases = set()
    for base in bases:
        if not base or base in seen_bases:
            continue
        seen_bases.add(base)
        names.extend([
            f"{base}.confirmed_subfamily.tsv",
        ])
    subfamily_name = f"{prefix}.confirmed_subfamily.tsv"
    
    final_dir = ws / "final"
    input_dir = ws / "input"
    
    # 也检查 bbav2/input/{job_id}/ 路径（用户上传文件的实际位置）
    job_id = ws.name
    alt_input_dir = ws.parent.parent / "input" / job_id
    
    # 优先级: final/ > workspace/input/ > bbav2/input/{job_id}/
    search_paths = []
    if final_dir.exists():
        search_paths.extend(final_dir / name for name in names)
    if input_dir.exists():
        search_paths.extend(input_dir / name for name in names)
    if alt_input_dir.exists():
        search_paths.extend(alt_input_dir / name for name in names)
    
    for candidate in search_paths:
        if candidate.exists():
            print(f"[INFO] Found subfamily file: {candidate}")
            return candidate
    
    print(f"[INFO] No subfamily file found ({subfamily_name}), will use default coloring")
    return None


def filter_gff3(gff3_file: Path, gene_ids: List[str], output_gff3: Path) -> Tuple[bool, Dict]:
    """
    过滤 GFF3 文件，只保留目标基因。

    ID matching is scope-aware: GFF3 gene/transcript/CDS prefixes, case, version
    suffixes and _P/_T suffixes are normalized back to the requested member IDs.
    NCBI/Gnomon records often use protein IDs as mRNA/CDS IDs while storing the
    transcript accession in Name/transcript_id; those transcript aliases are
    captured so CDS rows are assigned back to the requested XM_* member IDs.
    """
    target_lookup = build_target_id_lookup(gene_ids)
    transcript_to_gene: Dict[str, str] = {}
    stats = {
        "total_lines": 0,
        "filtered_lines": 0,
        "genes_found": set(),
        "mrna_count": 0,
        "cds_count": 0,
        "utr_count": 0
    }

    with open_text_auto(gff3_file) as f_in, open(output_gff3, 'w') as f_out:
        for line in f_in:
            stats["total_lines"] += 1

            if line.startswith('#'):
                continue

            line = re.sub(r'\t(\S+UTR)\t', '\tUTR\t', line, flags=re.IGNORECASE)

            parts = line.strip().split('\t')
            if len(parts) != 9:
                continue

            feat_type = parts[2]

            if feat_type == "gene":
                continue

            attr_map = parse_gff3_attributes(parts[8])
            feat_type_norm = feat_type.lower()
            canonical_id = canonical_from_attr_values(
                attr_map,
                ["ID", "Name", "transcript_id", "gene_id", "gene", "Parent"],
                target_lookup,
            )
            canonical_parent = canonical_from_attr_values(
                attr_map,
                ["Parent", "ID", "Name", "transcript_id", "gene_id", "gene"],
                target_lookup,
            )
            parent_text = attr_map.get("Parent", "")
            for parent_token in str(parent_text).split(","):
                if parent_token in transcript_to_gene:
                    canonical_parent = transcript_to_gene[parent_token]
                    break

            is_target = False

            if feat_type_norm in {"mrna", "transcript"} and canonical_id:
                is_target = True
                stats["genes_found"].add(canonical_id)
                stats["mrna_count"] += 1
                for alias_key in ("ID", "Name", "transcript_id"):
                    alias = attr_map.get(alias_key)
                    if not alias:
                        continue
                    for token in str(alias).split(","):
                        if token:
                            transcript_to_gene[token] = canonical_id
            elif feat_type_norm in {"cds", "utr", "exon"} and canonical_parent:
                is_target = True
                if feat_type_norm == "cds":
                    stats["cds_count"] += 1
                elif feat_type_norm == "utr":
                    stats["utr_count"] += 1

            if is_target:
                f_out.write('\t'.join(parts) + '\n')
                stats["filtered_lines"] += 1

    stats["genes_found"] = len(stats["genes_found"])
    return True, stats

def gff3_to_tsv(
    gff3_file: Path,
    output_tsv: Path,
    tree_order: List[str],
    target_gene_ids: Optional[List[str]] = None,
) -> Tuple[bool, Dict]:
    """将过滤后的 GFF3 转换为 TSV，并把 feature 归一到 scope/tree 成员 ID。"""
    data = []
    stats = {
        "total_features": 0,
        "genes_with_structure": set(),
        "total_cds": 0,
        "total_utr": 0,
        "exon_counts": {},
        "gene_lengths": {}
    }
    target_lookup = build_target_id_lookup(target_gene_ids or tree_order)
    transcript_to_gene: Dict[str, str] = {}
    seen_features: set[str] = set()

    with open_text_auto(gff3_file) as f:
        for line in f:
            parts = line.strip().split('\t')
            if len(parts) != 9:
                continue

            chrom, source, feat_type, start, end, score, strand, phase, attr = parts
            feat_type_norm = feat_type.lower()
            attr_map = parse_gff3_attributes(attr)

            if feat_type_norm in {"mrna", "transcript"}:
                gene_id = canonical_from_attr_values(
                    attr_map,
                    ["ID", "Name", "transcript_id", "geneID", "gene_id", "gene", "Parent"],
                    target_lookup,
                ) if target_lookup else strip_gff_id(attr_map.get("ID") or attr_map.get("Name") or attr_map.get("transcript_id") or attr_map.get("Parent"))
                if gene_id:
                    for alias_key in ("ID", "Name", "transcript_id"):
                        alias = attr_map.get(alias_key)
                        if not alias:
                            continue
                        for token in str(alias).split(","):
                            if token:
                                transcript_to_gene[token] = gene_id
            else:
                gene_id = canonical_from_attr_values(
                    attr_map,
                    ["Parent", "ID", "Name", "transcript_id", "geneID", "gene_id", "gene"],
                    target_lookup,
                ) if target_lookup else strip_gff_id(attr_map.get("Parent") or attr_map.get("ID"))
                parent_text = attr_map.get("Parent", "")
                for parent_token in str(parent_text).split(","):
                    if parent_token in transcript_to_gene:
                        gene_id = transcript_to_gene[parent_token]
                        break

            if not gene_id:
                continue

            start_num = int(start)
            end_num = int(end)
            feature_key = f"{gene_id}|{feat_type}|{start_num}|{end_num}|{strand}|{chrom}"
            if feature_key in seen_features:
                continue
            seen_features.add(feature_key)

            data.append({
                'gene_id': gene_id,
                'feature': feat_type,
                'start': start_num,
                'end': end_num,
                'strand': strand,
                'chrom': chrom
            })

            stats["total_features"] += 1

            if feat_type == "CDS":
                stats["genes_with_structure"].add(gene_id)
                stats["total_cds"] += 1
                stats["exon_counts"][gene_id] = stats["exon_counts"].get(gene_id, 0) + 1
            elif feat_type == "UTR":
                stats["genes_with_structure"].add(gene_id)
                stats["total_utr"] += 1

            if feat_type in {"CDS", "UTR"}:
                if gene_id not in stats["gene_lengths"]:
                    stats["gene_lengths"][gene_id] = {"min": start_num, "max": end_num}
                else:
                    stats["gene_lengths"][gene_id]["min"] = min(stats["gene_lengths"][gene_id]["min"], start_num)
                    stats["gene_lengths"][gene_id]["max"] = max(stats["gene_lengths"][gene_id]["max"], end_num)

    if not data:
        return False, stats

    stats["genes_with_structure"] = len(stats["genes_with_structure"])

    for gene_id, lengths in stats["gene_lengths"].items():
        stats["gene_lengths"][gene_id] = lengths["max"] - lengths["min"] + 1

    gene_list = sorted(set(d['gene_id'] for d in data))

    if tree_order:
        order_dict = {name: i for i, name in enumerate(tree_order)}
        gene_list = sorted(gene_list, key=lambda x: order_dict.get(x, 999999))

    gene_to_y = {g: i+1 for i, g in enumerate(gene_list)}

    with open(output_tsv, 'w') as f:
        f.write('gene_id\tfeature\tstart\tend\tstrand\tchrom\ty_pos\n')
        for d in data:
            y_pos = gene_to_y.get(d['gene_id'], 0)
            f.write(f"{d['gene_id']}\t{d['feature']}\t{d['start']}\t{d['end']}\t{d['strand']}\t{d['chrom']}\t{y_pos}\n")

    return True, stats

def calculate_structure_metrics(stats: Dict, gene_count: int) -> Dict:
    """计算结构相关的 metrics，并记录结构长度 QC。"""
    genes_with_structure = min(int(stats.get("genes_with_structure", 0) or 0), max(gene_count, 0))
    metrics = {
        "genes_total": gene_count,
        "genes_with_structure": genes_with_structure,
        "structure_coverage_rate": round(genes_with_structure / max(gene_count, 1), 4),
        "total_cds_features": stats.get("total_cds", 0),
        "total_utr_features": stats.get("total_utr", 0),
        "avg_exon_count_per_gene": 0.0,
        "avg_gene_length_bp": 0.0,
        "avg_cds_length_bp": 0.0,
        "median_gene_length_bp": 0.0,
        "max_gene_length_bp": 0,
        "long_gene_outlier_threshold_bp": 0,
        "long_gene_outlier_count": 0,
        "top_longest_gene_structures": [],
        "long_gene_outliers": [],
    }
    
    exon_counts = stats.get("exon_counts", {})
    gene_lengths = stats.get("gene_lengths", {})
    
    if exon_counts:
        metrics["avg_exon_count_per_gene"] = round(sum(exon_counts.values()) / len(exon_counts), 2)
    
    if gene_lengths:
        lengths = sorted(int(v) for v in gene_lengths.values() if int(v) > 0)
        metrics["avg_gene_length_bp"] = round(sum(lengths) / len(lengths), 2)
        metrics["avg_cds_length_bp"] = round(metrics["avg_exon_count_per_gene"] * 100, 2)
        mid = len(lengths) // 2
        if len(lengths) % 2:
            median_len = lengths[mid]
        else:
            median_len = (lengths[mid - 1] + lengths[mid]) / 2
        metrics["median_gene_length_bp"] = round(median_len, 2)
        metrics["max_gene_length_bp"] = max(lengths)
        threshold = int(max(10000, median_len * 5))
        metrics["long_gene_outlier_threshold_bp"] = threshold
        ranked = sorted(gene_lengths.items(), key=lambda item: int(item[1]), reverse=True)
        metrics["top_longest_gene_structures"] = [
            {
                "gene_id": gene_id,
                "length_bp": int(length),
                "cds_count": int(exon_counts.get(gene_id, 0) or 0),
            }
            for gene_id, length in ranked[:10]
        ]
        metrics["long_gene_outliers"] = [
            item for item in metrics["top_longest_gene_structures"]
            if int(item.get("length_bp", 0)) >= threshold
        ]
        metrics["long_gene_outlier_count"] = sum(1 for _gene_id, length in gene_lengths.items() if int(length) >= threshold)
    
    return metrics


def run_r_plotting(tree_file: Path, structure_tsv: Path, output_prefix: Path, 
                   rscript_path: Optional[Path] = None, cpu: int = 4,
                   subfamily_file: Optional[Path] = None,
                   project_root: Optional[Path] = None) -> Dict:
    """运行 R 绘图脚本"""
    if rscript_path is None:
        rscript_path = Path(__file__).parent.parent.parent / "scripts" / "plot_structure.R"
    
    if not rscript_path.exists():
        return {"success": False, "error": f"R script not found: {rscript_path}"}
    
    rscript_exe = Path(shutil.which("Rscript") or sys.executable)
    if str(rscript_exe).endswith("python"):
        rscript_exe = Path("Rscript")

    cmd = [
        str(rscript_exe), str(rscript_path),
        "--tree", str(tree_file),
        "--structure", str(structure_tsv),
        "--output", str(output_prefix)
    ]
    
    # 传递项目根目录，让 R 脚本自动推断 job_id 和 subfamily
    if project_root and project_root.exists():
        cmd.extend(["--root", str(project_root)])
    
    # 如果指定了亚家族文件，传递给 R 脚本
    if subfamily_file and subfamily_file.exists():
        cmd.extend(["--subfamily", str(subfamily_file)])
    
    print(f"[CMD] {' '.join(cmd)}")
    
    start_time = datetime.now()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=600)
        end_time = datetime.now()
        
        success = result.returncode == 0
        
        output_files = []
        for ext in ['.png', '.pdf']:
            f = Path(f"{output_prefix}{ext}")
            if f.exists():
                output_files.append(str(f))
        
        return {
            "success": success and len(output_files) > 0,
            "returncode": result.returncode,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-2000:],
            "output_files": output_files,
            "elapsed_sec": (end_time - start_time).total_seconds()
        }
        
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "R plotting timeout (>600s)", "elapsed_sec": 600}
    except Exception as e:
        return {"success": False, "error": str(e), "elapsed_sec": 0}


def generate_report(prefix: str, metrics: Dict, parameters: Dict, 
                   output_file: Path, artifacts: Dict, plot_success: bool) -> None:
    """生成 structure_report.md 报告"""
    report_lines = [
        f"# Structure Analysis Report: {prefix}",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Status:** {'Success' if plot_success else 'Partial Success (Plotting Failed)'}",
        "",
        "## Summary",
        "",
        f"- **Total Genes:** {metrics['genes_total']}",
        f"- **Genes with Structure:** {metrics['genes_with_structure']}",
        f"- **Structure Coverage:** {metrics['structure_coverage_rate']*100:.1f}%",
        f"- **Total CDS Features:** {metrics['total_cds_features']}",
        f"- **Total UTR Features:** {metrics['total_utr_features']}",
        "",
        "## Structure Statistics",
        "",
        f"- **Average Exons per Gene:** {metrics['avg_exon_count_per_gene']}",
        f"- **Average Gene Length:** {metrics['avg_gene_length_bp']:,.0f} bp",
        f"- **Median Gene Length:** {metrics.get('median_gene_length_bp', 0):,.0f} bp",
        f"- **Maximum Gene Length:** {metrics.get('max_gene_length_bp', 0):,.0f} bp",
        f"- **Average CDS Length (est.):** {metrics['avg_cds_length_bp']:,.0f} bp",
        "",
        "## Structure Length QC",
        "",
        f"- **Long-gene outlier threshold:** {metrics.get('long_gene_outlier_threshold_bp', 0):,.0f} bp (max of 10 kb or 5x median gene span)",
        f"- **Long-gene outliers:** {metrics.get('long_gene_outlier_count', 0)}",
        "",
    ]
    top_longest = metrics.get("top_longest_gene_structures") or []
    if top_longest:
        report_lines.extend([
            "| Rank | Gene ID | Structure span (bp) | CDS count |",
            "| --- | --- | ---: | ---: |",
        ])
        for idx, item in enumerate(top_longest[:10], start=1):
            report_lines.append(
                f"| {idx} | `{item.get('gene_id', '')}` | {int(item.get('length_bp', 0)):,} | {int(item.get('cds_count', 0))} |"
            )
        report_lines.append("")
    if metrics.get("long_gene_outliers"):
        report_lines.append("**QC note:** One or more gene structures exceed the long-gene threshold. Inspect the source GFF3 before biological interpretation.")
        report_lines.append("")
    else:
        report_lines.append("**QC note:** No gene structure exceeds the long-gene threshold; visually long tracks are usually due to real intron spacing relative to compact genes.")
        report_lines.append("")
    report_lines.extend([
        "## Parameters",
        "",
        f"- **GFF3 Source:** {parameters.get('gff3_source', 'N/A')}",
        f"- **GFF3 Naming:** {parameters.get('gff3_naming_convention', 'N/A')}",
        f"- **Subfamily File:** {parameters.get('subfamily_file', 'Not provided')}",
        f"- **CPU Cores:** {parameters.get('cpu', 'N/A')}",
        f"- **Tree File:** {parameters.get('tree_file', 'N/A')}",
        f"- **Gene ID File:** {parameters.get('gene_id_file', 'N/A')}",
        "",
        "## Output Files",
        ""
    ])
    
    for key, artifact in artifacts.items():
        if isinstance(artifact, dict):
            report_lines.append(f"- **{key}:** `{artifact.get('path', 'N/A')}` ({artifact.get('type', 'unknown')})")
    
    report_lines.extend([
        "",
        "## Visualization",
        ""
    ])
    
    if plot_success:
        report_lines.append("Gene structure visualization was successfully generated with three panels:")
        report_lines.append("1. **Phylogenetic Tree** - Gene relationships with subfamily coloring")
        report_lines.append("2. **Subfamily Bars** - Visual connection between tree and structure")
        report_lines.append("3. **Gene Structure** - CDS (green) and UTR (orange) architecture")
    else:
        report_lines.append("**Warning:** Gene structure visualization failed. See logs for details.")
    
    report_lines.extend([
        "",
        "---",
        "*Generated by Gene Family Analysis Pipeline - Structure Stage*"
    ])
    
    with open(output_file, 'w') as f:
        f.write('\n'.join(report_lines))
    
    print(f"[INFO] Report generated: {output_file}")


def write_manifest(manifest: Dict, manifest_file: Path) -> None:
    """写入 manifest 文件"""
    with open(manifest_file, 'w') as f:
        json.dump(manifest, f, indent=2)
    print(f"[INFO] Manifest: {manifest_file}")


def write_failure_manifest(workspace: Path, final_dir: Path, prefix: str, 
                          job_id: str, run_id: str, start_time: datetime,
                          error_msg: str, partial_artifacts: Dict = None) -> None:
    """写入失败状态的 manifest"""
    end_time = datetime.now()
    
    manifest = {
        "manifest_version": "1.0",
        "job_id": job_id,
        "stage": "structure",
        "run_id": run_id,
        "prefix": prefix,
        "timestamp": end_time.isoformat(),
        "status": "failed",
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "elapsed_sec": (end_time - start_time).total_seconds(),
        "cmd": [str(Path(__file__).name)],
        "workspace_root": str(workspace.resolve()),
        "workspace": str(final_dir.resolve()),
        "input_dir": str(workspace / "input"),
        "artifacts": partial_artifacts or {},
        "parameters": {
            "stage": "structure",
            "error": error_msg
        },
        "metrics": {
            "error_type": "execution_failed"
        }
    }
    
    manifest_file = final_dir / f"{prefix}.structure.manifest.json"
    write_manifest(manifest, manifest_file)
    print(f"[ERROR] Failure manifest written: {manifest_file}")


def main():
    parser = argparse.ArgumentParser(description='Structure stage - Gene structure visualization')
    parser.add_argument('--prefix', required=True, help='Analysis prefix (e.g., Ft.SPL)')
    parser.add_argument('--workspace', required=True, help='Workspace directory')
    parser.add_argument('--gff3', help='Input GFF3 file (auto-detect if not specified, naming: {species_short}_final.gff3)')
    parser.add_argument('--scope-file', help='Gene-family scope JSON from resolve_scope.py')
    parser.add_argument('--analysis-route', default='', help='Explicit analysis route identity for manifest/status')
    parser.add_argument('--member-set-id', default='', help='Explicit member-set identity for manifest/status')
    parser.add_argument('--tree', help='Phylogeny tree file; auto-detects scoped phylogeny manifest when omitted')
    parser.add_argument('--subfamily', help='Subfamily classification file (explicit path; auto-detect only uses confirmed_subfamily.tsv)')
    parser.add_argument('--cpu', type=int, default=4, help='CPU cores for processing')
    parser.add_argument('--log-file', help='Log file path')
    parser.add_argument('--run-id', help='Run ID (auto-generated if not provided)')
    
    args = parser.parse_args()
    
    run_id = args.run_id or str(uuid.uuid4())[:8]
    job_id = Path(args.workspace).name
    
    start_time = datetime.now()
    ws = Path(args.workspace)
    
    # 1. 创建标准目录结构（契约要求）
    structure_dir = ws / "structure" / "analysis"
    structure_dir.mkdir(parents=True, exist_ok=True)
    final_dir = ws / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    scope_data: Optional[Dict] = None
    scope_file: Optional[Path] = None
    if args.scope_file:
        scope_file = Path(args.scope_file)
        if not scope_file.exists():
            print(f"[ERROR] Scope file not found: {args.scope_file}")
            write_failure_manifest(ws, final_dir, args.prefix, job_id, run_id, start_time, f"Scope file not found: {args.scope_file}")
            sys.exit(1)
        scope_data = read_scope(scope_file)
    scope_id = str((scope_data or {}).get("scope_id") or "").strip()
    output_prefix = user_output_prefix_for(
        "structure",
        args.prefix,
        scope_data or {},
        analysis_route=args.analysis_route,
        member_set_id=args.member_set_id,
    ) if scope_id else args.prefix
    
    # 2. 解析输入文件路径（支持自动查找和命名规范）
    try:
        if scope_data:
            gff3_sources = resolve_scope_gff3_paths(scope_data, ws=ws)
            gff3_file = gff3_sources[0][1]
        else:
            gff3_file = resolve_gff3_path(ws, args.prefix, args.gff3)
            species_short_for_gff = args.prefix.split('.')[0] if '.' in args.prefix else args.prefix
            gff3_sources = [(species_short_for_gff, gff3_file)]
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        write_failure_manifest(ws, final_dir, args.prefix, job_id, run_id, start_time, str(e))
        sys.exit(1)
    
    # 亚家族文件（可选）
    subfamily_file = resolve_subfamily_file(ws, args.prefix, args.subfamily, output_prefix=output_prefix, scope_data=scope_data)
    
    # 其他必需输入
    phase = "subfamily_evidence_required" if args.analysis_route == "subfamily_evidence" else "post_confirm_required"
    try:
        resolved_members = resolve_effective_members(
            workspace=ws,
            final_dir=final_dir,
            input_dir=current_input_dir(ws),
            prefix=args.prefix,
            phase=phase,  # type: ignore[arg-type]
            require="id",
            member_set_id=args.member_set_id,
        )
        gene_id_file = resolved_members.path_id or Path("")
        print(f"[INFO] Gene ID file from {resolved_members.source_kind}: {gene_id_file}")
    except MemberResolutionError as e:
        if args.analysis_route == "subfamily_evidence":
            error_msg = f"Complete identify member review before Structure subfamily evidence: {e}"
        else:
            error_msg = f"Complete subfamily confirmation before regular Structure analysis: {e}"
        print(f"[ERROR] {error_msg}")
        write_failure_manifest(ws, final_dir, args.prefix, job_id, run_id, start_time, error_msg)
        sys.exit(1)
    try:
        tree_file = resolve_scope_tree_file(ws, args.prefix, scope_data, args.tree)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        write_failure_manifest(ws, final_dir, args.prefix, job_id, run_id, start_time, str(e))
        sys.exit(1)
    if scope_data:
        target_species_set = {str(item) for item in scope_data.get("target_species", []) if str(item).strip()}
        tree_ids = []
        for item in read_tree_order(tree_file):
            if not item:
                continue
            if "|" in item:
                species_code, gene_code = item.split("|", 1)
                if target_species_set and species_code not in target_species_set:
                    continue
                tree_ids.append(gene_code)
            else:
                tree_ids.append(item)
        if tree_ids:
            gene_id_file = structure_dir / f"{output_prefix}.scope_members.id"
            with gene_id_file.open("w", encoding="utf-8") as handle:
                for gene_id in tree_ids:
                    handle.write(gene_id + "\n")
    
    # 验证其他输入
    missing = []
    if not gene_id_file.exists():
        missing.append(f"Gene ID file: {gene_id_file}")
    
    if missing:
        error_msg = "Missing required input files:\n" + "\n".join(missing)
        print(f"[ERROR] {error_msg}")
        write_failure_manifest(ws, final_dir, args.prefix, job_id, run_id, start_time, error_msg)
        sys.exit(1)
    
    # 提取物种简写用于报告
    species_short = args.prefix.split('.')[0] if '.' in args.prefix else args.prefix
    gff3_naming = (
        "scope target GFF3: " + ", ".join(f"{species}:{path.name}" for species, path in gff3_sources)
        if scope_data else f"{species_short}_final.gff3"
    )
    
    print(f"[INFO] Structure stage started for {args.prefix}")
    print(f"[INFO] Job ID: {job_id}, Run ID: {run_id}")
    print(f"[INFO] GFF3 naming convention: {gff3_naming}")
    print(f"[INFO] GFF3 source: {gff3_file}")
    if len(gff3_sources) > 1:
        for species, source_path in gff3_sources:
            print(f"[INFO] Scope GFF3 source: {species} -> {source_path}")
    
    # 3. 读取输入数据
    gene_ids = read_gene_ids(gene_id_file)
    print(f"[INFO] {len(gene_ids)} genes to analyze")
    
    tree_order = read_tree_order(tree_file)
    print(f"[INFO] Tree order: {len(tree_order)} tips")
    
    # 4. 过滤 GFF3（中间文件放入 structure/analysis/）
    structure_gff3 = structure_dir / f"{output_prefix}.structure.filtered.gff3"
    filter_success, filter_stats = filter_scope_gff3(gff3_sources, gene_ids, structure_gff3, structure_dir, args.prefix)
    
    if not filter_success:
        error_msg = "Failed to filter GFF3 file"
        print(f"[ERROR] {error_msg}")
        write_failure_manifest(ws, final_dir, args.prefix, job_id, run_id, start_time, error_msg)
        sys.exit(1)
    
    print(f"[INFO] Filtered GFF3: {structure_gff3}")
    print(f"[INFO]  - Total lines: {filter_stats['total_lines']}")
    print(f"[INFO]  - Filtered lines: {filter_stats['filtered_lines']}")
    print(f"[INFO]  - Genes found: {filter_stats['genes_found']}")
    
    # 5. 转换为 TSV（中间文件放入 structure/analysis/）
    structure_tsv = structure_dir / f"{output_prefix}.structure.tsv"
    convert_success, convert_stats = gff3_to_tsv(structure_gff3, structure_tsv, tree_order, gene_ids)
    
    if not convert_success:
        error_msg = "Failed to convert GFF3 to TSV (no valid features found)"
        print(f"[ERROR] {error_msg}")
        write_failure_manifest(ws, final_dir, args.prefix, job_id, run_id, start_time, error_msg)
        sys.exit(1)
    
    print(f"[INFO] Structure TSV: {structure_tsv}")
    print(f"[INFO]  - Genes with structure: {convert_stats['genes_with_structure']}")
    print(f"[INFO]  - Total CDS: {convert_stats['total_cds']}")
    print(f"[INFO]  - Total UTR: {convert_stats['total_utr']}")
    
    # 6. 计算结构 metrics
    structure_metrics = calculate_structure_metrics(convert_stats, len(gene_ids))
    print(f"[INFO] Metrics calculated:")
    for k, v in structure_metrics.items():
        print(f"  - {k}: {v}")
    
    # 7. R 绘图（输出到 final/）
    output_path_prefix = final_dir / f"{output_prefix}.structure"
    project_root = Path(__file__).parent.parent.parent  # bbav5 根目录
    plot_result = run_r_plotting(
        tree_file, 
        structure_tsv, 
        output_path_prefix, 
        cpu=args.cpu,
        subfamily_file=subfamily_file,
        project_root=project_root
    )
    
    plot_success = plot_result.get("success", False)
    if not plot_success:
        print(f"[WARNING] R plotting failed: {plot_result.get('error', plot_result.get('stderr', 'unknown'))}")
    
    print(f"[INFO] Plot files: {plot_result.get('output_files', [])}")
    
    # 8. 准备 artifacts
    end_time = datetime.now()
    artifacts = {}
    
    # 中间文件
    if structure_gff3.exists():
        artifacts["structure_gff3_filtered"] = {
            "artifact": "structure_gff3_filtered",
            "path": str(structure_gff3.relative_to(ws)),
            "full_path": display_path(structure_gff3),
            "type": "gff3",
            "size": structure_gff3.stat().st_size,
            "sha256": compute_file_hash(structure_gff3),
            "description": "Filtered GFF3 with only target genes (intermediate)"
        }
    
    if structure_tsv.exists():
        artifacts["structure_tsv"] = {
            "artifact": "structure_tsv",
            "path": str(structure_tsv.relative_to(ws)),
            "full_path": display_path(structure_tsv),
            "type": "tsv",
            "size": structure_tsv.stat().st_size,
            "description": "Gene structure data in TSV format (intermediate)"
        }
    
    # 最终产物
    for img_file in plot_result.get("output_files", []):
        img_path = Path(img_file)
        if img_path.exists():
            key = f"structure_plot_{img_path.suffix[1:]}"
            artifacts[key] = {
                "artifact": "structure_plot",
                "path": str(img_path.relative_to(ws)),
                "full_path": display_path(img_path),
                "type": "image",
                "format": img_path.suffix[1:],
                "size": img_path.stat().st_size,
                "sha256": compute_file_hash(img_path),
                "description": f"Gene structure visualization ({img_path.suffix[1:].upper()})"
            }
    
    # 9. 生成报告
    report_file = final_dir / f"{output_prefix}.structure_report.md"
    
    parameters = {
        "cpu": args.cpu,
        "prefix": args.prefix,
        "output_prefix": output_prefix,
        "stage": "structure",
        "job_id": job_id,
        "run_id": run_id,
        "gff3_source": str(gff3_file),
        "gff3_sources": [{"species": species, "path": str(path)} for species, path in gff3_sources],
        "gff3_naming_convention": gff3_naming,
        "gff3_source_sha256": compute_file_hash(gff3_file),
        "subfamily_file": str(subfamily_file) if subfamily_file else None,
        "tree_file": str(tree_file),
        "gene_id_file": str(gene_id_file),
        "gene_count": len(gene_ids),
        "tree_tip_count": len(tree_order),
        "Rscript_path": str(Path(__file__).parent.parent.parent / "scripts" / "plot_structure.R"),
        "tool_versions": get_tool_versions(),
        "timestamp_start": start_time.isoformat(),
        "timestamp_end": end_time.isoformat(),
        "workspace_root": display_path(ws),
        "input_dir": str(ws / "input"),
        "structure_analysis_dir": str(structure_dir),
        "final_dir": str(final_dir)
    }
    
    generate_report(output_prefix, structure_metrics, parameters, report_file, artifacts, plot_success)
    
    if report_file.exists():
        artifacts["structure_report"] = {
            "artifact": "structure_report",
            "path": str(report_file.relative_to(ws)),
            "full_path": display_path(report_file),
            "type": "report",
            "format": "markdown",
            "size": report_file.stat().st_size,
            "sha256": compute_file_hash(report_file),
            "description": "Human-readable analysis report"
        }
    
    # 10. 确定状态
    status = "success" if plot_success else "partial_success"
    
    # 11. 构建完整 manifest
    manifest = {
        "manifest_version": "1.0",
        "job_id": job_id,
        "stage": "structure",
        "run_id": run_id,
        "prefix": args.prefix,
        "output_prefix": output_prefix,
        "timestamp": end_time.isoformat(),
        "status": status,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "elapsed_sec": (end_time - start_time).total_seconds(),
        "cmd": [
            str(Path(__file__).name),
            "--prefix", args.prefix,
            "--workspace", str(ws),
            "--gff3", str(gff3_file),
            "--cpu", str(args.cpu),
            "--run-id", run_id
        ] + (["--scope-file", str(scope_file)] if scope_file else []) + (["--subfamily", str(subfamily_file)] if subfamily_file else []),
        "workspace_root": display_path(ws),
        "workspace": display_path(structure_dir),
        "input_dir": str(ws / "input"),
        "artifacts": artifacts,
        "parameters": parameters,
        "metrics": structure_metrics
    }
    if scope_data:
        manifest["scope_id"] = scope_data.get("scope_id", "")
        manifest["scope_mode"] = scope_data.get("scope_mode", "")
        manifest["scope_file"] = str(scope_file)
        manifest["target_species"] = scope_data.get("target_species", [])
        manifest["reference_species"] = scope_data.get("reference_species", [])
        manifest["comparative_species"] = scope_data.get("comparative_species", [])
        manifest["selected_species"] = scope_data.get("selected_species", [])
        manifest["scope"] = {
            "family": scope_data.get("family", ""),
            "prefix": scope_data.get("prefix", ""),
            "scope_id": scope_data.get("scope_id", ""),
            "scope_mode": scope_data.get("scope_mode", ""),
            "target_species": scope_data.get("target_species", []),
            "reference_species": scope_data.get("reference_species", []),
            "comparative_species": scope_data.get("comparative_species", []),
            "selected_species": scope_data.get("selected_species", []),
        }
    context = build_stage_context(
        stage="structure",
        prefix=args.prefix,
        scope_data=scope_data or {},
        scope_file=scope_file or "",
        output_prefix=output_prefix,
        subfamily_file=subfamily_file,
        analysis_route=args.analysis_route,
        member_set_id=args.member_set_id,
    )
    manifest["tree_source"] = str(tree_file)
    manifest["gene_id_file"] = str(gene_id_file)
    manifest["gff3_source"] = str(gff3_file)
    apply_stage_context(manifest, context)
    
    manifest_file = final_dir / f"{output_prefix}.structure.manifest.json"
    write_manifest(manifest, manifest_file)
    if scope_data and scope_data.get("scope_id"):
        write_legacy_manifest_copy(manifest_file, args.prefix, str(scope_data.get("scope_id") or ""), "structure")
    
    # 12. 完成
    if status == "success":
        print(f"[INFO] Structure stage completed successfully")
    else:
        print(f"[WARNING] Structure stage completed with partial success (plotting failed)")
    
    print("\n=== OUTPUT_FILES ===")
    for k, v in artifacts.items():
        if isinstance(v, dict) and v.get("type") == "image":
            print(f"IMAGE: {v['full_path']}")
    
    sys.exit(0 if status == "success" else 1)


if __name__ == "__main__":
    main()
