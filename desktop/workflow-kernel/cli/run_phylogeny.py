#!/usr/bin/env python3
"""
Phylogeny阶段：对基因家族成员进行多序列比对和系统发育分析

输入：Identify阶段的final/{prefix}.final_members.pep.fa
输出：
  - final/{prefix}.pep.msa.fa (多序列比对)
  - final/{prefix}.tree.nwk (系统发育树)
  - final/{prefix}.phylogeny_report.md (分析报告)
  - final/{prefix}.phylogeny.manifest.json (机器清单)
"""

import os
import argparse
import json
import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import hashlib
import time
import shutil  # 需要添加这个导入，用于复制文件

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.gene_family_scope import read_scope, sanitize_token

CUSTOM_PHYLOGENY_ROUTES = {"custom_phylogeny", "comparative_phylogeny"}


def route_output_suffix(analysis_route: str) -> str:
    route = str(analysis_route or "").strip()
    if route in CUSTOM_PHYLOGENY_ROUTES:
        return sanitize_token(route)
    return ""


from gfa_core.gene_family_stage_context import apply_stage_context, build_stage_context, user_output_prefix_for, write_legacy_manifest_copy

# Karpathy: 添加conda环境bin目录到PATH
CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")


def resolve_iqtree_executable() -> str:
    """Resolve the IQ-TREE executable used by this Python environment."""
    candidates = [
        Path(sys.executable).resolve().parent / "iqtree3",
        Path(CONDA_BIN) / "iqtree3",
        shutil.which("iqtree3"),
        shutil.which("iqtree2"),
        shutil.which("iqtree"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
    return "iqtree3"


def get_tool_versions() -> Dict[str, str]:
    """获取MAFFT和IQ-TREE版本信息"""
    versions = {}
        
    # MAFFT版本 - 修复版本获取
    try:
        # 尝试不同的方法来获取MAFFT版本
        # MAFFT的--version可能输出到stderr
        result = subprocess.run(
            ["mafft", "--version"], 
            capture_output=True, 
            text=True, 
            check=False,
            timeout=5
        )
        
        # 检查stdout和stderr
        output = result.stdout.strip() or result.stderr.strip()
        
        if output:
            # 提取版本信息
            # MAFFT输出格式可能是:
            # 1. "MAFFT v7.505 (2023/Jul/25)"
            # 2. "v7.505" 
            # 3. 或其他格式
            lines = output.split('\n')
            for line in lines:
                line = line.strip()
                if line:
                    # 查找包含版本信息的行
                    if "MAFFT v" in line:
                        versions["mafft"] = line
                        break
                    elif line.startswith("v") and "." in line:
                        versions["mafft"] = f"MAFFT {line}"
                        break
            
            # 如果没找到特定格式，使用第一行
            if "mafft" not in versions and lines:
                versions["mafft"] = lines[0]
        else:
            versions["mafft"] = "unknown (no output)"
            
    except (FileNotFoundError, subprocess.CalledProcessError):
        versions["mafft"] = "not_found"
    except subprocess.TimeoutExpired:
        versions["mafft"] = "timeout"
    except Exception as e:
        versions["mafft"] = f"error: {str(e)[:50]}"

    # IQ-TREE版本（与实际执行命令保持一致）
    try:
        iqtree_exe = resolve_iqtree_executable()
        result = subprocess.run(
            [iqtree_exe, "--version"],
            capture_output=True, 
            text=True, 
            check=False
        )
        if result.returncode == 0:
            # 提取版本信息
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if "IQ-TREE version" in line or "IQ-TREE" in line:
                    versions["iqtree"] = line.strip()
                    break
            if "iqtree" not in versions:
                versions["iqtree"] = lines[0] if lines else "unknown"
        else:
            versions["iqtree"] = f"error_{result.returncode}"
    except (FileNotFoundError, subprocess.CalledProcessError):
        versions["iqtree"] = "not_found"
    
    return versions


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




def infer_effective_prefix(workspace: Path, requested_prefix: str = "") -> str:
    """Return a stable non-hidden prefix for phylogeny outputs."""
    if requested_prefix:
        return requested_prefix

    final_dir = workspace / "final"

    for pep in sorted(final_dir.glob("Multi.*.final_members.pep.fa")):
        suffix = ".final_members.pep.fa"
        if pep.name.endswith(suffix):
            return pep.name[:-len(suffix)]

    for manifest_path in sorted(final_dir.glob("*.identify.manifest.json")):
        if manifest_path.name.startswith("."):
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            prefix = data.get("prefix")
            if prefix:
                return prefix
            gene_family = data.get("gene_family")
            if gene_family:
                return f"Multi.{gene_family}"
        except Exception:
            continue

    legacy_manifest = final_dir / ".identify.manifest.json"
    if legacy_manifest.exists():
        try:
            data = json.loads(legacy_manifest.read_text(encoding="utf-8"))
            prefix = data.get("prefix")
            if prefix:
                return prefix
            gene_family = data.get("gene_family")
            if gene_family:
                return f"Multi.{gene_family}"
        except Exception:
            pass

    raise FileNotFoundError("Unable to infer prefix: no Multi.*.final_members.pep.fa or identify manifest found")

def validate_identify_output(workspace: Path, prefix: str) -> Dict[str, Any]:
    """
    验证Identify阶段的输出文件并获取必要信息
    
    Args:
        workspace: 工作空间目录（workspace/<job_id>）
        prefix: 分析前缀，如 Ft.SPL
    
    Returns:
        包含输入文件路径和Identify manifest数据的字典
    
    Raises:
        FileNotFoundError: Identify输出不存在
        ValueError: 文件无效或格式错误
    """
    print(f"正在验证Identify阶段输出: {workspace}")
    
    # 1. 查找Identify的manifest
    identify_manifest_path = workspace / "final" / f"{prefix}.identify.manifest.json"
    single_species_protein: Optional[Path] = None
    if not identify_manifest_path.exists() and "." in prefix:
        species, family = prefix.split(".", 1)
        if species not in {"Target", "Multi"} and family:
            candidate_manifest = workspace / "final" / f"Target.{family}.identify.manifest.json"
            species_candidates = [
                workspace / "final" / f"Target.{species}.{family}.identified_members.pep.fa",
                workspace / "final" / f"Target.{species}.{family}.final_members.pep.fa",
                workspace / "identify" / species / "final" / f"{species}.final_gfmembers.pep.fa",
            ]
            single_species_protein = next((p for p in species_candidates if p.exists() and p.stat().st_size > 0), None)
            if candidate_manifest.exists() and single_species_protein:
                identify_manifest_path = candidate_manifest
    if not identify_manifest_path.exists():
        raise FileNotFoundError(f"Identify manifest未找到: {identify_manifest_path}")
    
    print(f"  找到Identify manifest: {identify_manifest_path}")
    
    # 2. 读取Identify manifest
    with open(identify_manifest_path, 'r') as f:
        identify_manifest = json.load(f)
    
    # 3. 验证Identify状态
    if identify_manifest.get("status") != "success":
        raise ValueError(f"Identify阶段未成功完成，状态: {identify_manifest.get('status')}")
    
    # 4. 查找蛋白序列文件
    # artifacts可能是列表（旧格式）或字典（新格式）
    artifacts = identify_manifest.get("artifacts", {})
    protein_artifact = None
    
    if isinstance(artifacts, list):
        # 列表格式：查找artifact为"final_proteins"的项
        for artifact in artifacts:
            if isinstance(artifact, dict) and artifact.get("artifact") == "final_proteins":
                protein_artifact = artifact
                break
    elif isinstance(artifacts, dict):
        # 字典格式：直接查找"final_proteins"键
        protein_artifact = artifacts.get("final_proteins")
    
    if not protein_artifact:
        # 尝试其他可能的键名
        for key in ["identified_members", "final_proteins", "final_members", "proteins"]:
            if key in artifacts:
                protein_artifact = artifacts[key] if isinstance(artifacts, dict) else None
                break
    
    if single_species_protein:
        protein_path = single_species_protein
        protein_artifact = {
            "full_path": str(single_species_protein),
            "artifact": "species_final_members",
        }
    elif not protein_artifact:
        raise ValueError("在Identify manifest中找不到final_proteins artifact")

    # 获取文件路径
    if not single_species_protein:
        protein_path = manifest_artifact_path(workspace, identify_manifest_path, protein_artifact)
        if not protein_path:
            raw_full_path = Path(protein_artifact.get("full_path", ""))
            protein_path = raw_full_path if raw_full_path.exists() and inside_current_job(workspace, raw_full_path) else Path("")
        if not protein_path.exists():
            # 尝试相对路径
            rel_path = protein_artifact.get("path", "")
            if rel_path:
                protein_path = resolve_job_path(workspace, rel_path, identify_manifest_path) or (workspace / "final" / Path(rel_path).name)
    
    if not protein_path.exists():
        raise FileNotFoundError(f"蛋白序列文件未找到: {protein_path}")
    
    # 检查文件是否为空
    if protein_path.stat().st_size == 0:
        raise ValueError(f"蛋白序列文件为空: {protein_path}")
    
    print(f"  找到蛋白序列文件: {protein_path}")
    print(f"  文件大小: {protein_path.stat().st_size} 字节")
    
    return {
        "protein_fasta": protein_path,
        "identify_manifest": identify_manifest,
        "identify_metrics": identify_manifest.get("metrics", {}),
        "identify_parameters": identify_manifest.get("parameters", {})
    }


def read_fasta_records(fasta_file: Path) -> List[Dict[str, str]]:
    records: List[Dict[str, str]] = []
    current_header = ""
    current_seq: List[str] = []
    with fasta_file.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                if current_header:
                    records.append({
                        "id": current_header.split()[0],
                        "header": current_header,
                        "seq": "".join(current_seq),
                    })
                current_header = line[1:].strip()
                current_seq = []
            else:
                current_seq.append(line)
    if current_header:
        records.append({
            "id": current_header.split()[0],
            "header": current_header,
            "seq": "".join(current_seq),
        })
    return records


def write_fasta_records(records: List[Dict[str, str]], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(f">{record['header']}\n")
            seq = record["seq"].replace("*", "")
            for idx in range(0, len(seq), 80):
                handle.write(seq[idx:idx + 80] + "\n")


def display_path(path: Path | str) -> str:
    """Return a stable user-facing absolute path without expanding symlinks."""
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
    input_dir = current_input_dir(workspace)
    by_name_candidates = [
        input_dir / path.name,
        workspace / "final" / path.name,
        workspace / "phylogeny" / "analysis" / path.name,
    ]
    for candidate in by_name_candidates:
        if candidate.exists():
            return candidate
    return None


def resolve_job_path(workspace: Path, raw_path: str | Path, manifest_path: Optional[Path] = None) -> Optional[Path]:
    """Resolve a copied job path without trusting stale absolute full_path values."""
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


def manifest_artifact_path(workspace: Path, manifest_path: Path, artifact: Dict[str, Any]) -> Optional[Path]:
    if not isinstance(artifact, dict):
        return None
    for key in ("path", "relpath_or_hint", "file", "full_path"):
        resolved = resolve_job_path(workspace, artifact.get(key) or "", manifest_path)
        if resolved:
            return resolved
    return None


def rebase_job_path_text(workspace: Path, value: str) -> str:
    """Rebase old job paths embedded in manifest strings or command history."""
    if not value:
        return value
    if value.startswith("/"):
        direct = rebase_current_job_path(workspace, Path(value))
        if direct:
            return display_path(direct)
    if "workspace/" + workspace.name not in value and "input/" + workspace.name not in value:
        return value

    def replace_match(match: re.Match[str]) -> str:
        raw_path = match.group(0)
        trailing = ""
        while raw_path and raw_path[-1] in {",", ";", ")", "]", "}"}:
            trailing = raw_path[-1] + trailing
            raw_path = raw_path[:-1]
        rebased = resolve_job_path(workspace, raw_path)
        return (display_path(rebased) if rebased else raw_path) + trailing

    return re.sub(r"/[^\s\"']*(?:workspace|input)/" + re.escape(workspace.name) + r"/[^\s\"']*", replace_match, value)


def rebase_manifest_payload_paths(workspace: Path, value: Any) -> Any:
    if isinstance(value, dict):
        return {key: rebase_manifest_payload_paths(workspace, item) for key, item in value.items()}
    if isinstance(value, list):
        return [rebase_manifest_payload_paths(workspace, item) for item in value]
    if isinstance(value, str):
        return rebase_job_path_text(workspace, value)
    return value


def first_existing_scope_file(workspace: Path, scope_data: Dict[str, Any], species: str, file_types: List[str]) -> Optional[Path]:
    species_files = scope_data.get("files", {}).get(species, {})
    for file_type in file_types:
        for record in species_files.get(file_type, []) or []:
            path_text = record.get("path") or ""
            if path_text:
                path = resolve_job_path(workspace, path_text) or Path(path_text)
                if path.exists() and path.stat().st_size > 0:
                    return path
    return None


def infer_input_dir(workspace: Path, scope_file: Optional[Path]) -> Path:
    """Resolve the job input directory without requiring the caller to pass it."""
    candidates: List[Path] = []
    if scope_file:
        # Common generated scope path:
        # workspace/<job_id>/final/<prefix>.<scope_id>.scope.json
        try:
            scope_parent = scope_file.parent
            if scope_parent.name == "final":
                job_workspace = scope_parent.parent
                candidates.append(job_workspace.parent.parent / "input" / job_workspace.name)
        except Exception:
            pass
    candidates.extend([
        workspace.parent.parent / "input" / workspace.name,
        workspace / "input",
    ])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def find_species_family_pep(
    workspace: Path,
    input_dir: Path,
    species: str,
    family: str,
    scope_data: Dict[str, Any],
) -> Optional[Path]:
    """Find a known family protein FASTA for reference/comparative species."""
    family_tokens = [family] if family else []
    prefix_tokens = [f"{species}.{token}" for token in family_tokens]
    candidates: List[Path] = []
    for prefix_token in prefix_tokens:
        candidates.extend([
            workspace / "final" / f"{prefix_token}.pep.fa",
            workspace / "final" / f"{prefix_token}.pep.fasta",
            workspace / "final" / f"{prefix_token}.faa",
            workspace / "final" / f"{prefix_token}.sequence_extract.pep.fa",
            workspace / "final" / f"{prefix_token}.sequence_extract.pep.fasta",
            workspace / "final" / f"{prefix_token}.ncbi_fetch.protein.fa",
            workspace / "final" / f"{prefix_token}.ncbi_fetch.protein.fasta",
            input_dir / f"{prefix_token}.pep.fa",
            input_dir / f"{prefix_token}.pep.fasta",
            input_dir / f"{prefix_token}.faa",
            input_dir / f"{prefix_token}.sequence_extract.pep.fa",
            input_dir / f"{prefix_token}.sequence_extract.pep.fasta",
            input_dir / f"{prefix_token}.ncbi_fetch.protein.fa",
            input_dir / f"{prefix_token}.ncbi_fetch.protein.fasta",
            input_dir / species / f"{prefix_token}.pep.fa",
            input_dir / species / f"{prefix_token}.pep.fasta",
            input_dir / species / f"{prefix_token}.faa",
            input_dir / species / f"{prefix_token}.sequence_extract.pep.fa",
            input_dir / species / f"{prefix_token}.sequence_extract.pep.fasta",
            input_dir / species / f"{prefix_token}.ncbi_fetch.protein.fa",
            input_dir / species / f"{prefix_token}.ncbi_fetch.protein.fasta",
        ])

    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    scoped = first_existing_scope_file(workspace, scope_data, species, ["family_pep"])
    if scoped:
        return scoped
    return None


def find_refined_species_members(
    workspace: Path,
    input_dir: Path,
    species: str,
    family: str,
    scope_data: Dict[str, Any],
) -> Optional[Path]:
    """Resolve the target species member FASTA for scoped phylogeny.

    The subfamily evidence route is pre-final and must prefer curated_members.
    Regular gene-family routes remain anchored on final_members.
    """
    family = str(family or "").strip()
    candidates: List[Path] = []
    scope_prefix = str(scope_data.get("prefix") or "").strip()
    analysis_route = str(scope_data.get("analysisRoute") or scope_data.get("analysis_route") or "").strip()
    stems = ["curated_members"] if analysis_route == "subfamily_evidence" else ["final_members"]

    for stem in stems:
        if family:
            candidates.extend([
                workspace / "final" / f"Target.{species}.{family}.{stem}.pep.fa",
                input_dir / "final" / f"Target.{species}.{family}.{stem}.pep.fa",
            ])
        # In custom multi-species scopes the scope prefix can be an aggregate or a
        # stale user-facing file. Prefer the explicit per-species Target artifact
        # above; keep the scope-prefix fallback only after that.
        if scope_prefix and scope_prefix.split(".", 1)[0] == species:
            candidates.append(workspace / "final" / f"{scope_prefix}.{stem}.pep.fa")
            candidates.append(input_dir / "final" / f"{scope_prefix}.{stem}.pep.fa")
        if family:
            candidates.extend([
                workspace / "final" / f"{species}.{family}.{stem}.pep.fa",
                input_dir / "final" / f"{species}.{family}.{stem}.pep.fa",
            ])
        candidates.extend([
            workspace / "final" / f"{species}.{stem}.pep.fa",
            input_dir / "final" / f"{species}.{stem}.pep.fa",
        ])

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.absolute()
        except Exception:
            resolved = candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


def prepare_phylogeny_scope_fasta(
    workspace: Path,
    prefix: str,
    identify_info: Dict[str, Any],
    scope_file: Optional[Path],
    fallback_fasta: Path,
    analysis_route: str = "",
) -> tuple[Path, Dict[str, Any]]:
    if not scope_file:
        records = read_fasta_records(fallback_fasta)
        return fallback_fasta, {
            "enabled": False,
            "input_fasta": display_path(fallback_fasta),
            "sequence_count": len(records),
        }

    scope_data = read_scope(scope_file)
    if analysis_route and not (scope_data.get("analysisRoute") or scope_data.get("analysis_route")):
        scope_data["analysisRoute"] = analysis_route
        scope_data["analysis_route"] = analysis_route
    scope_id = scope_data.get("scope_id", "")
    selected_species = scope_data.get("selected_species") or scope_data.get("target_species") or []
    if not selected_species:
        selected_species = identify_info.get("identify_manifest", {}).get("target_species", [])

    identify_manifest = identify_info.get("identify_manifest", {})
    artifacts = identify_manifest.get("artifacts", {}) or {}
    identify_species = identify_manifest.get("target_species", []) or []
    reference_species = scope_data.get("reference_species") or []
    comparative_species = scope_data.get("comparative_species") or []
    family = str(scope_data.get("family") or prefix.split(".")[-1] or "").strip()
    input_dir = infer_input_dir(workspace, scope_file)
    route = str(analysis_route or scope_data.get("analysisRoute") or scope_data.get("analysis_route") or "").strip()
    target_species_set = {str(item).strip() for item in scope_data.get("target_species") or [] if str(item).strip()}

    records: List[Dict[str, str]] = []
    seen_ids: set[str] = set()
    species_counts: Dict[str, int] = {}
    sources: Dict[str, str] = {}
    missing_species: List[str] = []
    missing_details: Dict[str, List[str]] = {}

    def add_records(species: str, fasta: Path, source: str) -> None:
        added = 0
        for record in read_fasta_records(fasta):
            record_id = record["id"]
            if record_id in seen_ids:
                continue
            seen_ids.add(record_id)
            records.append(record)
            added += 1
        species_counts[species] = species_counts.get(species, 0) + added
        sources[species] = display_path(fasta)
        print(f"  Scope FASTA add {species}: {added} sequences from {source}: {fasta}")

    for species in selected_species:
        refined_fasta = find_refined_species_members(
            workspace=workspace,
            input_dir=input_dir,
            species=species,
            family=family,
            scope_data=scope_data,
        )
        if refined_fasta:
            source_label = "curated_members" if route == "subfamily_evidence" else "final_members"
            add_records(species, refined_fasta, source_label)
            continue

        if route == "subfamily_evidence" and species in target_species_set:
            missing_species.append(species)
            missing_details[species] = [
                str(workspace / "final" / f"Target.{species}.{family}.curated_members.pep.fa"),
                str(input_dir / "final" / f"Target.{species}.{family}.curated_members.pep.fa"),
                str(workspace / "final" / f"{species}.{family}.curated_members.pep.fa"),
                "请先完成 identify 成员初筛确认，生成 curated_members 后再运行亚家族证据进化树。",
            ]
            continue

        artifact = artifacts.get(f"{species}_final_members") if isinstance(artifacts, dict) else None
        artifact_path = manifest_artifact_path(workspace, workspace / "final" / f"{prefix}.identify.manifest.json", artifact) if artifact else None
        if artifact_path and artifact_path.exists() and artifact_path.stat().st_size > 0:
            add_records(species, artifact_path, "identify")
            continue

        if species in identify_species:
            fallback = workspace / "identify" / species / "final" / f"{species}.final_gfmembers.pep.fa"
            if fallback.exists() and fallback.stat().st_size > 0:
                add_records(species, fallback, "identify_fallback")
                continue

        if species in reference_species or species in comparative_species:
            family_file = find_species_family_pep(
                workspace=workspace,
                input_dir=input_dir,
                species=species,
                family=family,
                scope_data=scope_data,
            )
            if family_file:
                add_records(species, family_file, "family_pep")
                continue

        missing_species.append(species)
        missing_details[species] = [
            f"identify artifact: {species}_final_members",
            str(workspace / "identify" / species / "final" / f"{species}.final_gfmembers.pep.fa"),
            str(input_dir / f"{species}.{family}.pep.fa"),
            str(input_dir / f"{species}.{family}.pep.fasta"),
            str(input_dir / species / f"{species}.{family}.pep.fasta"),
            str(workspace / "final" / f"{species}.{family}.pep.fa"),
            str(workspace / "final" / f"{species}.{family}.sequence_extract.pep.fa"),
            str(workspace / "final" / f"{species}.{family}.ncbi_fetch.protein.fa"),
        ]

    if missing_species:
        raise RuntimeError(
            "Phylogeny scope 缺少以下物种的家族蛋白序列: "
            + ",".join(missing_species)
            + "。请先运行对应物种 identify，或提供 Species.Family.pep.fa。"
            + " 已检查路径: "
            + json.dumps(missing_details, ensure_ascii=False)
        )
    if not records:
        raise RuntimeError("Phylogeny scope 没有可用于建树的蛋白序列。")

    output_fasta = workspace / "phylogeny" / "analysis" / f"{prefix}.{sanitize_token(scope_id)}.input.pep.fa"
    write_fasta_records(records, output_fasta)
    return output_fasta, {
        "enabled": True,
        "scope_file": display_path(scope_file),
        "scope_id": scope_id,
        "scope_mode": scope_data.get("scope_mode", ""),
        "family": scope_data.get("family", ""),
        "target_species": scope_data.get("target_species") or [],
        "reference_species": reference_species,
        "comparative_species": comparative_species,
        "selected_species": selected_species,
        "input_fasta": display_path(output_fasta),
        "sequence_count": len(records),
        "species_counts": species_counts,
        "sources": sources,
        "input_dir": display_path(input_dir) if input_dir.exists() else str(input_dir),
    }


def prepare_directories(workspace: Path) -> Dict[str, Path]:
    """创建Phylogeny阶段的标准目录结构"""
    paths = {
        "analysis_dir": workspace / "phylogeny" / "analysis",
        "final_dir": workspace / "final",
        "logs_dir": workspace / "logs",
    }
    
    print("创建目录结构:")
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        print(f"  {name}: {path}")
    
    return paths


def force_option_value(args: list[str], option: str, value: str) -> list[str]:
    normalized: list[str] = []
    skip_next = False
    prefix = f"{option}="
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == option:
            skip_next = True
            continue
        if arg.startswith(prefix):
            continue
        normalized.append(arg)
    normalized.extend([option, value])
    return normalized


def run_mafft_alignment(
    input_fasta: Path,
    output_msa: Path,
    cpu: int = 8,
    mafft_params: Optional[str] = None
) -> Dict[str, Any]:
    """
    运行MAFFT进行多序列比对
    
    Args:
        input_fasta: 输入FASTA文件
        output_msa: 输出MSA文件
        cpu: CPU核心数
        mafft_params: 自定义MAFFT参数
    
    Returns:
        包含命令、返回码、输出和指标的字典
    """
    print(f"运行MAFFT比对: {input_fasta} -> {output_msa}")
    
    if mafft_params:
        params = force_option_value(shlex.split(mafft_params), "--thread", str(cpu))
        cmd = ["mafft"] + params
        print(f"  使用自定义参数: {' '.join(params)}")
    else:
        cmd = ["mafft", "--auto", "--thread", str(cpu)]
        print(f"  使用默认参数: --auto --thread {cpu}")

    cmd.append(str(input_fasta))
    
    # 记录开始时间
    start_time = time.time()
    
    # 执行比对
    try:
        with open(output_msa, "w") as f_out:
            result = subprocess.run(
                cmd, 
                stdout=f_out, 
                stderr=subprocess.PIPE, 
                text=True,
                check=False
            )
        
        elapsed = time.time() - start_time
        
        if result.returncode == 0:
            print(f"  MAFFT成功完成，耗时: {elapsed:.1f}秒")
            
            # 计算MSA统计
            metrics = compute_msa_metrics(output_msa)
            metrics["alignment_time_sec"] = elapsed
            
            return {
                "command": " ".join(cmd),
                "returncode": result.returncode,
                "stderr": result.stderr,
                "metrics": metrics,
                "output_file": output_msa,
                "success": True
            }
        else:
            print(f"  MAFFT失败，返回码: {result.returncode}")
            if result.stderr:
                print(f"  错误信息: {result.stderr[:200]}")
            
            return {
                "command": " ".join(cmd),
                "returncode": result.returncode,
                "stderr": result.stderr,
                "metrics": {},
                "output_file": None,
                "success": False
            }
            
    except Exception as e:
        print(f"  MAFFT执行异常: {e}")
        return {
            "command": " ".join(cmd),
            "returncode": -1,
            "stderr": str(e),
            "metrics": {},
            "output_file": None,
            "success": False
        }


def compute_msa_metrics(msa_file: Path) -> Dict[str, Any]:
    """计算MSA的统计指标"""
    try:
        sequences = []
        current_seq = ""
        current_id = ""
        
        with open(msa_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('>'):
                    if current_id:
                        sequences.append(current_seq)
                    current_id = line[1:].split()[0] if line[1:] else ""
                    current_seq = ""
                elif line:
                    current_seq += line
            
            if current_id:
                sequences.append(current_seq)
        
        if not sequences:
            return {
                "sequences_count": 0,
                "msa_length": 0,
                "gap_percentage": 0.0,
                "min_length": 0,
                "max_length": 0
            }
        
        # 检查序列长度一致性
        lengths = [len(seq) for seq in sequences]
        if len(set(lengths)) > 1:
            print(f"警告: MSA序列长度不一致: {lengths}")
        
        # 计算gap百分比
        seq_count = len(sequences)
        msa_length = max(lengths) if lengths else 0
        total_chars = seq_count * msa_length
        
        if total_chars > 0:
            gap_chars = sum(seq.count('-') + seq.count('.') for seq in sequences)
            gap_percentage = (gap_chars / total_chars * 100)
        else:
            gap_percentage = 0.0
        
        return {
            "sequences_count": seq_count,
            "msa_length": msa_length,
            "gap_percentage": round(gap_percentage, 2),
            "min_length": min(lengths) if lengths else 0,
            "max_length": max(lengths) if lengths else 0
        }
        
    except Exception as e:
        print(f"计算MSA指标时出错: {e}")
        return {
            "sequences_count": 0,
            "msa_length": 0,
            "gap_percentage": 0.0,
            "min_length": 0,
            "max_length": 0
        }


def count_tree_tips(tree_file: Path) -> int:
    try:
        from Bio import Phylo

        tree = Phylo.read(str(tree_file), "newick")
        return len(tree.get_terminals())
    except Exception as exc:
        print(f"计算tree tip数量失败: {exc}")
        return 0

def run_iqtree_tree(
    msa_file: Path,
    output_dir: Path,
    prefix: str,
    cpu: int = 8,
    iqtree_params: Optional[str] = None
) -> Dict[str, Any]:
    """Run IQ-TREE and return the generated tree file."""
    print(f"Run IQ-TREE: {msa_file}")
    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = output_dir / f"{prefix}.iqtree"

    # IQ-TREE reuses checkpoints for the same -pre prefix. In this pipeline the
    # same scope prefix can be rerun after upstream member IDs or sequences are
    # corrected, so stale treefiles must not be accepted as current output.
    for stale in output_dir.glob(f"{prefix}.iqtree*"):
        if stale.is_file():
            try:
                stale.unlink()
            except OSError as exc:
                print(f"  Warning: failed to remove stale IQ-TREE output {stale.name}: {exc}")

    if iqtree_params:
        params = force_option_value(shlex.split(iqtree_params), "-nt", str(cpu))
        cmd = [resolve_iqtree_executable(), "-s", str(msa_file), "-pre", str(base_name)] + params
        print(f"  Custom IQ-TREE params: {' '.join(params)}")
    else:
        cmd = [resolve_iqtree_executable(), "-s", str(msa_file), "-pre", str(base_name), "-m", "MFP", "-bb", "1000", "-nt", str(cpu)]
        print(f"  Default IQ-TREE params: -m MFP -bb 1000 -nt {cpu}")

    start_time = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        elapsed = time.time() - start_time
        stdout = result.stdout or ""
        stderr = result.stderr or ""

        tree_candidates = [
            base_name.with_suffix(".treefile"),
            output_dir / f"{prefix}.iqtree.treefile",
            base_name.with_suffix(".contree"),
            output_dir / f"{prefix}.iqtree.contree",
        ]
        tree_file = next((p for p in tree_candidates if p.exists() and p.stat().st_size > 0), None)
        best_model = extract_best_model(stdout + "\n" + stderr)

        checkpoint_finished = "previous run successfully finished" in (stdout + stderr)
        success = tree_file is not None and (result.returncode == 0 or checkpoint_finished)
        if success:
            print(f"  IQ-TREE completed, elapsed: {elapsed:.1f}s")
        else:
            print(f"  IQ-TREE completed, elapsed: {elapsed:.1f}s")
            if stderr:
                print(f"  Error: {stderr[:200]}")

        return {
            "command": " ".join(cmd),
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "tree_file": tree_file,
            "best_model": best_model,
            "tree_building_time_sec": elapsed,
            "success": success
        }
    except Exception as e:
        print(f"  IQ-TREE exception: {e}")
        return {
            "command": " ".join(cmd),
            "returncode": -1,
            "stdout": "",
            "stderr": str(e),
            "tree_file": None,
            "best_model": "",
            "tree_building_time_sec": 0,
            "success": False
        }

def run_msa_plotting(
    msa_file: Path,
    output_prefix: str,
    method: str = "mafft",
    rscript_path: Optional[Path] = None
) -> Dict[str, Any]:
    """
    运行R脚本绘制多序列比对图
    
    Args:
        msa_file: 比对好的MSA文件
        output_prefix: 输出文件前缀
        method: 比对方法（用于报告）
        rscript_path: R脚本路径，如果None则自动查找
    
    Returns:
        包含绘图结果的字典
    """
    print(f"📊 绘制MSA比对图: {msa_file}")
    
    # 自动查找R脚本
    if rscript_path is None:
        # 尝试多个可能的路径
        script_candidates = [
            Path(__file__).parent.parent / "scripts" / "msa_figure_from_aligned.R",
            Path(__file__).parent.parent.parent / "scripts" / "msa_figure_from_aligned.R",
            Path.cwd() / "scripts" / "msa_figure_from_aligned.R",
        ]
        
        for candidate in script_candidates:
            if candidate.exists():
                rscript_path = candidate
                break
        
        if rscript_path is None:
            # 如果专用脚本不存在，尝试原始脚本
            if method == "mafft":
                rscript_path = Path(__file__).parent.parent / "scripts" / "msa_figure.mafft.R"
            else:
                rscript_path = Path(__file__).parent.parent / "scripts" / "msa_figure.R"
        # ========== 修复点1: 提前定义 output_dir ==========
    output_dir = None
    output_prefix_path = None

    # 确保输出目录存在
    if rscript_path and rscript_path.exists():
        output_prefix_path = Path(output_prefix)
        output_dir = output_prefix_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)
    # ========== 修复结束 ==========
    
    if not rscript_path or not rscript_path.exists():
        return {
            "success": False,
            "error": f"R脚本未找到: {rscript_path}",
            "output_files": []
        }
    
    print(f"  使用R脚本: {rscript_path}")
    
    # 构建R命令
    rscript_exe = Path(shutil.which("Rscript") or "Rscript")
    if not rscript_exe.exists():
        rscript_exe = Path("Rscript")

    cmd = [
        str(rscript_exe),
        str(rscript_path),
        display_path(msa_file),
       # str(output_prefix),
        str(output_prefix_path.name),  # 只传递文件名，不含路径
        "--method", method
    ]
    
    # 记录开始时间
    start_time = time.time()
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=output_dir,  # 关键：在输出目录中运行R脚本
            timeout=600,  # 10分钟超时
            check=False
        )
        
        elapsed = time.time() - start_time
        
        if result.returncode == 0:
            print(f"  ✅ MSA绘图成功，耗时: {elapsed:.1f}秒")
            
            # Collect generated image files without constructing invalid glob patterns.
            output_dir = output_prefix_path.parent
            output_stem = output_prefix_path.name
            output_files = []
            for f in output_dir.iterdir():
                if f.is_file() and f.suffix.lower() in {".png", ".pdf"} and f.name.startswith(output_stem):
                    output_files.append(str(f))
            output_files = sorted(set(output_files))
            
            print(f"  生成 {len(output_files)} 个图片文件在: {output_dir}")
            for i, f in enumerate(output_files[:3], 1):
                print(f"    {i}. {Path(f).name}")


            return {
                "success": True,
                "command": " ".join(cmd),
                "returncode": result.returncode,
                "stdout": result.stdout[-500:],  # 只保留最后500字符
                "stderr": result.stderr[-500:] if result.stderr else "",
                "output_files": output_files,
                "elapsed_sec": elapsed
            }
        else:
            print(f"  ⚠️  MSA绘图失败，返回码: {result.returncode}")
            if result.stderr:
                print(f"  错误信息: {result.stderr[:200]}")
            
            return {
                "success": False,
                "command": " ".join(cmd),
                "returncode": result.returncode,
                "stdout": result.stdout[-500:],
                "stderr": result.stderr[-500:] if result.stderr else "",
                "output_files": [],
                "elapsed_sec": elapsed
            }
            
    except subprocess.TimeoutExpired:
        print(f"  ❌ MSA绘图超时 (>10分钟)")
        return {
            "success": False,
            "error": "timeout",
            "output_files": []
        }
    except Exception as e:
        print(f"  ❌ MSA绘图异常: {e}")
        return {
            "success": False,
            "error": str(e),
            "output_files": []
        }



def run_msa_conserved_region_plotting(
    workspace: Path,
    msa_file: Path,
    prefix: str,
    window_len: int = 60,
    min_nongap: float = 0.6,
    scope_file: str = "",
    analysis_route: str = "",
    member_set_id: str = "",
) -> Dict[str, Any]:
    """Generate an additional automatically selected conserved-region MSA plot."""
    script = PROJECT_ROOT / "cli" / "run_msa_region_plot.py"
    python_exe = Path(sys.executable)
    cmd = [
        str(python_exe),
        str(script),
        "--workspace", display_path(workspace),
        "--prefix", prefix,
        "--msa-file", display_path(msa_file),
        "--auto-conserved",
        "--window-len", str(window_len),
        "--min-nongap", str(min_nongap),
        "--force",
    ]
    if scope_file:
        cmd.extend(["--scope-file", str(scope_file)])
    if analysis_route:
        cmd.extend(["--analysis-route", str(analysis_route)])
    if member_set_id:
        cmd.extend(["--member-set-id", str(member_set_id)])
    start_time = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=900,
            check=False,
        )
        elapsed = time.time() - start_time
        final_dir = workspace / "final"
        output_files = []
        if final_dir.exists():
            for f in final_dir.iterdir():
                if f.is_file() and f.name.startswith(f"{prefix}.msa_conserved_region_"):
                    output_files.append(str(f))
        output_files = sorted(set(output_files))
        success = result.returncode == 0 and bool(output_files)
        return {
            "success": success,
            "command": " ".join(cmd),
            "returncode": result.returncode,
            "stdout": result.stdout[-1000:],
            "stderr": result.stderr[-1000:] if result.stderr else "",
            "output_files": output_files,
            "elapsed_sec": elapsed,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "timeout", "output_files": []}
    except Exception as exc:
        return {"success": False, "error": str(exc), "output_files": []}



def infer_species_family_from_output_prefix(prefix: str) -> tuple[str, str]:
    """Infer Species/Family from output labels such as Lc.SPL.*."""
    parts = [p for p in str(prefix or "").split(".") if p]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "", ""


def find_cross_scope_subfamily_file(final_dir: Path, prefix: str, input_dir: Path) -> Optional[Path]:
    """Resolve confirmed labels produced for a target+reference scope.

    Prediction tables are not auto-detected here. A caller that wants to plot
    unconfirmed prediction labels must pass them explicitly through --subfamily.
    """
    species, family = infer_species_family_from_output_prefix(prefix)
    candidates: list[Path] = []
    if species and family:
        stem = f"{species}.{family}.target_plus_reference"
        candidates.extend([
            final_dir / f"{stem}.confirmed_subfamily.tsv",
            input_dir / f"{stem}.confirmed_subfamily.tsv",
        ])
        for pattern in (
            f"{species}.{family}*target_plus_reference*confirmed_subfamily.tsv",
            f"*target-{species}*target_plus_reference*confirmed_subfamily.tsv",
        ):
            candidates.extend(sorted(final_dir.glob(pattern)))
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


def run_tree_plotting(
    tree_file: Path,
    output_prefix: Optional[str] = None,
    rscript_path: Optional[Path] = None,
    subfamily_file: Optional[Path] = None,
    disable_subfamily_auto: bool = False,
    tree_layout: str = "both",
    label_offset: float = 0.4,
    tip_label_size: float = 3.0,
    branch_color: str = "black",
    branch_width: float = 0.5,
    show_bootstrap: bool = True,
    bootstrap_size: float = 2.0,
    tip_point_size: float = 2.0,
    target_shape: str = "triangle",
    reference_shape: str = "circle",
    gene_shape: str = "circle",
    marker_mode: str = "auto",
    circular_label_offset: float = 0.8,
    circular_label_size: float = 1.8,
    subfamily_arc_enabled: bool = True,
    subfamily_arc_gap: float = 1.2,
    subfamily_arc_label_gap: float = 0.6,
    subfamily_arc_label_size: float = 0.0,
) -> Dict[str, Any]:
    """Run the R tree plotting script and return the concrete files it generated."""
    print(f"Plot tree: {tree_file}")
    tree_layout = (tree_layout or "both").lower()
    if tree_layout not in {"rectangular", "circular", "both"}:
        return {"success": False, "error": f"Invalid tree layout: {tree_layout}", "output_files": []}

    if output_prefix is None:
        output_prefix = tree_file.stem

    prefix = tree_file.stem.replace('.tree', '').replace('.nwk', '')

    if subfamily_file is None and not disable_subfamily_auto:
        final_dir = tree_file.parent
        workspace_dir = final_dir.parent
        job_id = workspace_dir.name
        project_root = workspace_dir.parent.parent
        input_dir = project_root / "input" / job_id
        potential_confirmed_final = final_dir / f"{prefix}.confirmed_subfamily.tsv"
        potential_confirmed_input = input_dir / f"{prefix}.confirmed_subfamily.tsv"
        potential_cross_scope = find_cross_scope_subfamily_file(final_dir, prefix, input_dir)

        print(f"  [DEBUG] job_id: {job_id}")
        print(f"  [DEBUG] project_root: {project_root}")
        print(f"  [DEBUG] input_dir: {input_dir}")
        print(f"  [DEBUG] lookup confirmed final: {potential_confirmed_final} (exists: {potential_confirmed_final.exists()})")
        print(f"  [DEBUG] lookup confirmed input: {potential_confirmed_input} (exists: {potential_confirmed_input.exists()})")
        print(f"  [DEBUG] lookup cross-scope confirmed subfamily: {potential_cross_scope} (exists: {bool(potential_cross_scope and potential_cross_scope.exists())})")

        if potential_confirmed_final.exists():
            subfamily_file = potential_confirmed_final
            print(f"  Using confirmed subfamily from final: {subfamily_file}")
        elif potential_confirmed_input.exists():
            subfamily_file = potential_confirmed_input
            print(f"  Using confirmed subfamily from input: {subfamily_file}")
        elif potential_cross_scope and potential_cross_scope.exists():
            subfamily_file = potential_cross_scope
            print(f"  Using cross-scope confirmed subfamily: {subfamily_file}")
        else:
            print(f"  Confirmed subfamily file not found: {prefix}.confirmed_subfamily.tsv")

    if subfamily_file and subfamily_file.exists():
        script_name = "tree_plot.R"
        use_subfamily = True
    else:
        script_name = "plot_tree.R"
        use_subfamily = False

    if rscript_path is None:
        script_candidates = [
            Path(__file__).parent.parent / "scripts" / script_name,
            Path(__file__).parent.parent.parent / "scripts" / script_name,
            Path.cwd() / "scripts" / script_name,
        ]
        for candidate in script_candidates:
            if candidate.exists():
                rscript_path = candidate
                break

    if not rscript_path or not rscript_path.exists():
        return {"success": False, "error": f"R script not found: {rscript_path}", "output_files": []}

    print(f"  R script: {rscript_path}")
    print(f"  Mode: {'subfamily' if use_subfamily else 'normal'}")
    print(f"  Layout: {tree_layout}")

    tree_dir = tree_file.parent
    style_args_base = [
        str(label_offset),
        str(tip_label_size),
        str(branch_color or "black"),
        str(branch_width),
        "true" if show_bootstrap else "false",
        str(bootstrap_size),
        str(tip_point_size),
    ]
    if use_subfamily:
        subfamily_plot_args = [
            *style_args_base,
            str(target_shape or "triangle"),
            str(reference_shape or "circle"),
            str(gene_shape or target_shape or "triangle"),
            str(circular_label_offset),
            str(circular_label_size),
            str(marker_mode or "auto"),
            "true" if subfamily_arc_enabled else "false",
            str(subfamily_arc_gap),
            str(subfamily_arc_label_gap),
            str(subfamily_arc_label_size if subfamily_arc_label_size and subfamily_arc_label_size > 0 else ""),
        ]
        cmd = ["Rscript", str(rscript_path), str(tree_file.name), display_path(subfamily_file), str(output_prefix), tree_layout, "", "", *subfamily_plot_args]
    else:
        normal_plot_args = [
            *style_args_base,
            str(gene_shape or target_shape or "triangle"),
            str(circular_label_offset),
            str(circular_label_size),
            str(marker_mode or "auto"),
        ]
        cmd = ["Rscript", str(rscript_path), str(tree_file.name), str(tree_dir), tree_layout, str(output_prefix or ""), *normal_plot_args]

    start_time = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=tree_dir, timeout=600, check=False)
        elapsed = time.time() - start_time

        if result.returncode == 0:
            print(f"  Tree plotting completed, elapsed: {elapsed:.1f}s")
            output_files = []
            if use_subfamily:
                expected_names = [
                    f"{output_prefix}.png",
                    f"{output_prefix}.pdf",
                    f"{output_prefix}.circular_tree.png",
                    f"{output_prefix}.circular_tree.pdf",
                ]
                for name in expected_names:
                    f = tree_dir / name
                    if f.exists():
                        output_files.append(str(f))
                        print(f"    输出: {f.name}")
            else:
                normal_stem = str(output_prefix or tree_file.stem)
                expected_names = [
                    f"{normal_stem}.png",
                    f"{normal_stem}.pdf",
                    f"{normal_stem}.circular_tree.png",
                    f"{normal_stem}.circular_tree.pdf",
                ]
                for name in expected_names:
                    f = tree_dir / name
                    if f.exists():
                        output_files.append(str(f))
                        print(f"    输出: {f.name}")
                output_dir = tree_dir / f"{tree_file.stem}_output"
                if output_dir.exists():
                    extra_files = [str(f) for f in output_dir.glob("*") if f.is_file()]
                    output_files.extend(extra_files)
                    print(f"  Generated {len(extra_files)} files in: {output_dir}")
            output_files = sorted(set(output_files))
            return {
                "success": True,
                "command": " ".join(cmd),
                "returncode": result.returncode,
                "stdout": result.stdout[-500:],
                "stderr": result.stderr[-500:] if result.stderr else "",
                "output_files": output_files,
                "output_dir": str(tree_dir),
                "elapsed_sec": elapsed,
                "mode": "subfamily" if use_subfamily else "normal",
                "subfamily_file": str(subfamily_file) if subfamily_file else None,
                "plot_params": {
                    "label_offset": label_offset,
                    "tip_label_size": tip_label_size,
                    "branch_color": branch_color,
                    "branch_width": branch_width,
                    "show_bootstrap": show_bootstrap,
                    "bootstrap_size": bootstrap_size,
                    "tip_point_size": tip_point_size,
                    "target_shape": target_shape,
                    "reference_shape": reference_shape,
                    "gene_shape": gene_shape,
                    "marker_mode": marker_mode,
                    "circular_label_offset": circular_label_offset,
                    "circular_label_size": circular_label_size,
                    "subfamily_arc_enabled": subfamily_arc_enabled,
                    "subfamily_arc_gap": subfamily_arc_gap,
                    "subfamily_arc_label_gap": subfamily_arc_label_gap,
                    "subfamily_arc_label_size": subfamily_arc_label_size,
                }
            }

        print(f"  Tree plotting failed, return code: {result.returncode}")
        if result.stderr:
                print(f"  Error: {stderr[:200]}")
        return {
            "success": False,
            "command": " ".join(cmd),
            "returncode": result.returncode,
            "stdout": result.stdout[-500:],
            "stderr": result.stderr[-500:] if result.stderr else "",
            "output_files": [],
            "elapsed_sec": elapsed
        }
    except subprocess.TimeoutExpired:
        print("  ? ??????? (>10??)")
        return {"success": False, "error": "timeout", "output_files": []}
    except Exception as e:
        print(f"  ? ???????: {e}")
        return {"success": False, "error": str(e), "output_files": []}

def extract_best_model(iqtree_output: str) -> str:
    """从IQ-TREE输出中提取最佳模型"""
    if not iqtree_output:
        return "unknown"
    
    lines = iqtree_output.split('\n')
    for line in lines:
        if "Best-fit model:" in line:
            return line.split("Best-fit model:")[1].strip()
        elif "Model of substitution:" in line:
            return line.split("Model of substitution:")[1].strip()
        elif "Best model:" in line:
            return line.split("Best model:")[1].strip()
    
    return "unknown"


def write_report(
    workspace: Path,
    prefix: str,
    metrics: Dict[str, Any],
    parameters: Dict[str, Any],
    tool_versions: Dict[str, str],
    mafft_result: Dict[str, Any],
    iqtree_result: Dict[str, Any],
    msa_plot_result: Optional[Dict[str, Any]],  # 新增
    tree_plot_result: Optional[Dict[str, Any]],  # 新增
    artifacts: Dict[str, Dict]
) -> Path:
    """写入人可读的分析报告"""
    report_path = workspace / "final" / f"{prefix}.phylogeny_report.md"
    
    print(f"生成分析报告: {report_path}")
    
    with open(report_path, "w") as f:
        f.write(f"# Phylogeny分析报告 - {prefix}\n\n")
        f.write(f"**生成时间**: {datetime.now().isoformat()}\n\n")
        
        f.write("## 1. 分析概述\n")
        f.write(f"- **分析阶段**: Phylogeny\n")
        f.write(f"- **分析状态**: {'成功' if iqtree_result.get('success', False) else '部分成功'}\n")
        f.write(f"- **序列数量**: {metrics.get('sequences_count', 'N/A')}\n")
        f.write(f"- **比对长度**: {metrics.get('msa_length', 'N/A')} 氨基酸\n")
        f.write(f"- **Gap比例**: {metrics.get('gap_percentage', 'N/A')}%\n")
        f.write(f"- **最佳模型**: {iqtree_result.get('best_model', 'N/A')}\n\n")
        
        f.write("## 2. 工具版本\n")
        for tool, version in tool_versions.items():
            f.write(f"- **{tool}**: {version}\n")
        f.write("\n")
        
        f.write("## 3. 执行参数\n")
        for param, value in parameters.items():
            f.write(f"- **{param}**: {value}\n")
        f.write("\n")
        
        f.write("## 4. 执行命令\n")
        f.write("### MAFFT比对\n")
        f.write(f"```bash\n{mafft_result.get('command', 'N/A')}\n```\n\n")
        
        if mafft_result.get('returncode', -1) != 0:
            f.write(f"**返回码**: {mafft_result.get('returncode', 'N/A')}\n")
            if mafft_result.get('stderr'):
                f.write(f"**错误信息**:\n```\n{mafft_result['stderr'][:500]}\n```\n\n")
        
        f.write("### IQ-TREE建树\n")
        f.write(f"```bash\n{iqtree_result.get('command', 'N/A')}\n```\n\n")
        
        if iqtree_result.get('returncode', -1) != 0:
            f.write(f"**返回码**: {iqtree_result.get('returncode', 'N/A')}\n")
            if iqtree_result.get('stderr'):
                f.write(f"**错误信息**:\n```\n{iqtree_result['stderr'][:500]}\n```\n\n")
        ##开始 #  
                # MSA绘图结果
        f.write("### 多序列比对图\n")
        if msa_plot_result and msa_plot_result.get("success", False):
            f.write("✅ **MSA比对图绘制成功**\n")
            f.write(f"- 耗时: {msa_plot_result.get('elapsed_sec', 0):.1f}秒\n")

            # 列出生成的图片
            msa_images = [k for k, v in artifacts.items() if isinstance(v, dict) and v.get("artifact") == "msa_plot"]
            if msa_images:
                f.write("- 生成图片:\n")
                for img_key in msa_images[:5]:  # 最多显示5个
                    img_info = artifacts[img_key]
                    f.write(f"  - `{img_info.get('path', '')}` ({img_info.get('size', 0)}字节)\n")
        else:
            f.write("❌ **MSA比对图绘制失败**\n")
            if msa_plot_result:
                f.write(f"- 错误: {msa_plot_result.get('error', '未知错误')}\n")

        f.write("\n")

        # 进化树绘图结果
        f.write("### 系统发育树图\n")
        if tree_plot_result and tree_plot_result.get("success", False):
            f.write("✅ **进化树图绘制成功**\n")
            f.write(f"- 耗时: {tree_plot_result.get('elapsed_sec', 0):.1f}秒\n")

            # 列出生成的图片
            tree_images = [k for k, v in artifacts.items() if isinstance(v, dict) and v.get("artifact") == "tree_plot"]
            if tree_images:
                f.write("- 生成图片:\n")
                for img_key in tree_images[:5]:  # 最多显示5个
                    img_info = artifacts[img_key]
                    f.write(f"  - `{img_info.get('path', '')}` ({img_info.get('size', 0)}字节)\n")
        else:
            f.write("❌ **进化树图绘制失败**\n")
            if tree_plot_result:
                f.write(f"- 错误: {tree_plot_result.get('error', '未知错误')}\n")

        f.write("\n")
        ###结束


        f.write("## 5. 产出文件\n")
        for artifact_name, artifact_info in artifacts.items():
            if isinstance(artifact_info, dict):
                f.write(f"- **{artifact_name}**: {artifact_info.get('path', 'N/A')}\n")
                if artifact_info.get('size'):
                    f.write(f"  - 大小: {artifact_info['size']} 字节\n")
                if artifact_info.get('sha256'):
                    f.write(f"  - SHA256: {artifact_info['sha256'][:16]}...\n")
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
    identify_inputs: List[Dict],
    status: str = "success",
    scope_info: Optional[Dict[str, Any]] = None,
    output_prefix: Optional[str] = None,
    analysis_route: str = "",
    member_set_id: str = "",
    subfamily_file: str = "",
) -> Path:
    """
    写入符合schema v1的manifest文件
    
    注意：artifacts必须是字典格式，不是列表
    """
    elapsed_sec = (end_time - start_time).total_seconds()
    output_prefix = output_prefix or prefix
    scope_id = str((scope_info or {}).get("scope_id") or "").strip()
    
    manifest_data = {
        # 基础标识字段（必需）
        "manifest_version": "1.0",
        "job_id": job_id,
        "stage": "phylogeny",
        "run_id": run_id,
        "prefix": prefix,
        "output_prefix": output_prefix,
        
        # 状态和时间字段（必需）
        "timestamp": end_time.isoformat(),
        "status": status,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "elapsed_sec": round(elapsed_sec, 2),
        
        # 执行信息（必需）
        "cmd": rebase_manifest_payload_paths(workspace, cmd_history),
        "tool_versions": tool_versions,
        
        # 目录路径（必需）
        "workspace_root": display_path(workspace.parent),
        "workspace": display_path(workspace / "final"),
        "input_dir": display_path(workspace.parent.parent / "input" / job_id),
        
        # 输入文件（必需）
        "inputs": rebase_manifest_payload_paths(workspace, identify_inputs),
        
        # 输出产物（必需）- 必须是字典格式
        "artifacts": artifacts,
        
        # 参数和指标（必需）
        "parameters": rebase_manifest_payload_paths(workspace, parameters),
        "metrics": metrics,
        "scope_id": scope_id,
        "scope": rebase_manifest_payload_paths(workspace, scope_info or {}),
        
        # 溯源信息（可选但推荐）
        "provenance": {
            "derived_from": f"{prefix}.identify.manifest.json",
            "stage_sequence": ["identify", "phylogeny"]
        }
    }
    context = build_stage_context(
        stage="phylogeny",
        prefix=prefix,
        scope_data=scope_info or {},
        scope_file=(scope_info or {}).get("scope_file", ""),
        output_prefix=output_prefix,
        subfamily_file=subfamily_file,
        analysis_route=analysis_route,
        member_set_id=member_set_id,
    )
    apply_stage_context(manifest_data, context)
    
    if scope_id:
        manifest_path = workspace / "final" / f"{output_prefix}.phylogeny.manifest.json"
        manifest_data["scope_manifest"] = display_path(manifest_path)
    else:
        manifest_path = workspace / "final" / f"{prefix}.phylogeny.manifest.json"
    
    print(f"生成Manifest文件: {manifest_path}")
    
    # 确保JSON严格性：无注释，确保ASCII
    with open(manifest_path, "w", encoding='utf-8') as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)
    
    return manifest_path


def main():
    """主函数：执行Phylogeny分析流程"""
    parser = argparse.ArgumentParser(
        description="Phylogeny阶段：多序列比对和系统发育分析",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python run_phylogeny.py --prefix Ft.SPL --workspace /path/to/workspace --cpu 4
  
输入要求：
  - Identify阶段必须已成功运行
  - final/{prefix}.final_members.pep.fa 必须存在
  
输出产物：
  - final/{prefix}.pep.msa.fa (多序列比对)
  - final/{prefix}.tree.nwk (系统发育树)
  - final/{prefix}.phylogeny_report.md (分析报告)
  - final/{prefix}.phylogeny.manifest.json (机器清单)
        """
    )
    
    parser.add_argument("--prefix", required=True, 
                       help="分析前缀，格式：物种.家族（如 Ft.SPL）")
    parser.add_argument("--workspace", required=True,
                       help="工作空间目录（workspace/<job_id>/)")
    parser.add_argument("--cpu", type=int, default=8,
                       help="CPU核心数（默认：8）")
    parser.add_argument("--proteins", "--protein-fasta",
                       dest="proteins",
                       help="Protein FASTA override; defaults to Identify manifest final_members protein FASTA")
    parser.add_argument("--scope-file",
                       help="Scope JSON; when provided, build phylogeny input FASTA from selected species")
    parser.add_argument("--analysis-route", default="", help="Explicit analysis route identity for manifest/status")
    parser.add_argument("--member-set-id", default="", help="Explicit member-set identity for manifest/status")
    parser.add_argument("--subfamily", default="", help="Optional subfamily table for tree plotting")
    parser.add_argument("--mafft-params",
                       help="MAFFT参数覆盖（默认：--auto --thread {cpu}）")
    parser.add_argument("--iqtree-params",
                       help="IQ-TREE参数覆盖（默认：-m MFP -bb 1000 -nt {cpu}；-nt 会被 --cpu 覆盖）")
    parser.add_argument("--tree-layout", choices=["rectangular", "circular", "both"], default="both",
                       help="进化树绘图布局：rectangular、circular 或 both（默认：both）")
    parser.add_argument("--tree-subfamily-arc-enabled", choices=["true", "false"], default="true",
                       help="圈图是否显示亚家族外圈弧线（默认：true）")
    parser.add_argument("--tree-subfamily-arc-gap", type=float, default=1.2,
                       help="圈图亚家族弧线相对基因ID外边界的额外距离（默认：1.2）")
    parser.add_argument("--tree-subfamily-arc-label-gap", type=float, default=0.6,
                       help="圈图亚家族名称相对弧线的额外距离（默认：0.6）")
    parser.add_argument("--tree-subfamily-arc-label-size", type=float, default=0.0,
                       help="圈图亚家族名称字号；0 表示自动（默认：0）")
    parser.add_argument("--log-file",
                       help="日志文件路径（默认：workspace/logs/{timestamp}_phylogeny.log）")
    
    args = parser.parse_args()
    
    # 记录开始时间
    start_time = datetime.now()
    run_id = f"phylogeny_{start_time.strftime('%Y%m%d_%H%M%S_%f')[:-3]}"
    
    # 设置工作空间
    workspace = Path(args.workspace)
    job_id = workspace.name  # workspace/<job_id>中的job_id
    effective_prefix = infer_effective_prefix(workspace, args.prefix)
    
    print("=" * 60)
    print(f"🚀 启动Phylogeny分析")
    print("=" * 60)
    print(f"分析前缀: {args.prefix}")
    print(f"工作空间: {workspace}")
    print(f"任务ID: {job_id}")
    print(f"运行ID: {run_id}")
    print(f"CPU核心: {args.cpu}")
    print(f"树图布局: {args.tree_layout}")
    
    try:
        # 1. 准备目录结构
        print("\n📁 准备目录结构...")
        paths = prepare_directories(workspace)
        
        # 2. 获取工具版本
        print("\n🔧 检查工具版本...")
        tool_versions = get_tool_versions()
        print(f"   MAFFT: {tool_versions.get('mafft', 'unknown')}")
        print(f"   IQ-TREE: {tool_versions.get('iqtree', 'unknown')}")
        
        # 3. 验证Identify输出
        print("\n🔍 验证Identify阶段输出...")
        # OMaaX: an explicit protein input does not require an Identify audit record.
        identify_info = {"protein_fasta": Path(args.proteins), "identify_manifest": {}, "identify_metrics": {}, "identify_parameters": {}} if args.proteins else validate_identify_output(workspace, effective_prefix)
        protein_fasta = identify_info["protein_fasta"]
        scope_path = Path(args.scope_file).absolute() if args.scope_file else None
        if scope_path and not scope_path.exists():
            raise FileNotFoundError(f"Scope file not found: {scope_path}")
        scope_info = {
            "enabled": False,
            "input_fasta": display_path(protein_fasta),
            "sequence_count": len(read_fasta_records(protein_fasta)),
        }
        if args.proteins:
            protein_override = resolve_job_path(workspace, args.proteins) or Path(args.proteins)
            if not protein_override.exists():
                raise FileNotFoundError(f"Specified protein FASTA not found: {protein_override}")
            if protein_override.stat().st_size == 0:
                raise ValueError(f"Specified protein FASTA is empty: {protein_override}")
            protein_fasta = protein_override
            print(f"  使用指定蛋白序列文件: {protein_fasta}")
            scope_info = {
                "enabled": bool(scope_path),
                "scope_file": str(scope_path) if scope_path else "",
                "input_fasta": display_path(protein_fasta),
                "sequence_count": len(read_fasta_records(protein_fasta)),
                "note": "--proteins override was used; scope selected species were not used to build FASTA",
            }
        elif scope_path:
            print("\n🧭 准备Phylogeny scope输入FASTA...")
            protein_fasta, scope_info = prepare_phylogeny_scope_fasta(
                workspace=workspace,
                prefix=effective_prefix,
                identify_info=identify_info,
                scope_file=scope_path,
                fallback_fasta=protein_fasta,
                analysis_route=args.analysis_route,
            )
            print(f"  Scope输入FASTA: {protein_fasta}")
            print(f"  Scope序列数: {scope_info.get('sequence_count')}")
        output_prefix_token = effective_prefix
        if scope_info.get("scope_id"):
            output_prefix_token = user_output_prefix_for(
                "phylogeny",
                effective_prefix,
                scope_info,
                analysis_route=args.analysis_route,
                member_set_id=args.member_set_id,
            )
        print(f"  输出前缀: {output_prefix_token}")
        
        # 准备Identify的inputs信息
        identify_inputs = []
        if "inputs" in identify_info["identify_manifest"]:
            identify_inputs = identify_info["identify_manifest"]["inputs"]
        
        # 4. 运行MAFFT比对
        print("\n🔄 运行MAFFT多序列比对...")
        msa_intermediate = paths["analysis_dir"] / f"{output_prefix_token}.pep.mafft.msa.fa"
        mafft_result = run_mafft_alignment(
            input_fasta=protein_fasta,
            output_msa=msa_intermediate,
            cpu=args.cpu,
            mafft_params=args.mafft_params
        )
        
        if not mafft_result.get("success", False):
            print("❌ MAFFT比对失败")
            raise RuntimeError(f"MAFFT失败: {mafft_result.get('stderr', '未知错误')}")
        
        msa_metrics = mafft_result.get("metrics", {})
        print(f"  比对成功: {msa_metrics.get('sequences_count', '未知')} 条序列")
        
        # 5. 复制MSA到final目录
        msa_final = paths["final_dir"] / f"{output_prefix_token}.pep.msa.fa"
        msa_final.write_bytes(msa_intermediate.read_bytes())
        print(f"  MSA保存到: {msa_final}")
        
        # 6. 运行IQ-TREE建树
        print("\n🌳 运行IQ-TREE构建系统发育树...")
        iqtree_result = run_iqtree_tree(
            msa_file= paths["final_dir"] / f"{output_prefix_token}.pep.msa.fa",
            output_dir=paths["analysis_dir"],
            prefix=output_prefix_token,
            cpu=args.cpu,
            iqtree_params=args.iqtree_params
        )
        
        # 7. 复制树文件到final目录（即使部分成功也尝试）
        tree_final = None
        if iqtree_result.get("tree_file") and iqtree_result["tree_file"].exists():
            tree_final = paths["final_dir"] / f"{output_prefix_token}.tree.nwk"
            tree_final.write_bytes(iqtree_result["tree_file"].read_bytes())
            print(f"  树文件保存到: {tree_final}")
        elif iqtree_result.get("success", False):
            # 尝试查找其他可能的树文件
            tree_files = list(paths["analysis_dir"].glob(f"{output_prefix_token}.iqtree.*"))
            tree_files = [f for f in tree_files if f.suffix in ['.treefile', '.nwk', '.tre']]
            if tree_files:
                tree_final = paths["final_dir"] / f"{output_prefix_token}.tree.nwk"
                tree_final.write_bytes(tree_files[0].read_bytes())
                print(f"  树文件保存到: {tree_final}")
            else:
                print("⚠️  警告: 未找到树文件，但IQ-TREE报告成功")


          # ========== 8. 绘制MSA比对图 ==========
        msa_plot_result = None
        msa_conserved_region_result = None
        tree_plot_result = None
        
        # 确保MSA文件路径正确
        msa_final = paths["final_dir"] / f"{output_prefix_token}.pep.msa.fa"
        
        if msa_final.exists():
            print("\n" + "="*60)
            print("📸 开始绘制MSA比对图...")
            msa_plot_result = run_msa_plotting(
                msa_file=msa_final,
                output_prefix=paths["final_dir"] / f"{output_prefix_token}.msa_plot",
                method="mafft"
            )
            
            if msa_plot_result and msa_plot_result.get("success", False):
                print(f"✅ MSA比对图绘制完成")
            else:
                print(f"⚠️  MSA比对图绘制失败")

            print("\n📌 自动筛选保守MSA区域并绘图...")
            msa_conserved_region_result = run_msa_conserved_region_plotting(
                workspace=workspace,
                msa_file=msa_final,
                prefix=output_prefix_token,
                window_len=60,
                min_nongap=0.6,
                scope_file=scope_info.get("scope_file", ""),
                analysis_route=args.analysis_route,
                member_set_id=args.member_set_id,
            )
            if msa_conserved_region_result and msa_conserved_region_result.get("success", False):
                print("✅ 保守区MSA图绘制完成")
            else:
                print("⚠️  保守区MSA图绘制失败")
        
        # ========== 9. 绘制进化树图 ==========
        if tree_final and tree_final.exists():
            print("\n" + "="*60)
            print("🌳 开始绘制进化树图...")
            subfamily_arg = str(args.subfamily or "").strip()
            subfamily_disabled = subfamily_arg.lower() in {"none", "skip", "disabled"}
            subfamily_override = None if (not subfamily_arg or subfamily_disabled) else (resolve_job_path(workspace, subfamily_arg) or Path(subfamily_arg)).absolute()
            tree_plot_result = run_tree_plotting(
                tree_file=tree_final,
                output_prefix=f"{output_prefix_token}.tree",
                tree_layout=args.tree_layout,
                subfamily_file=subfamily_override,
                disable_subfamily_auto=subfamily_disabled,
                subfamily_arc_enabled=str(args.tree_subfamily_arc_enabled).lower() != "false",
                subfamily_arc_gap=args.tree_subfamily_arc_gap,
                subfamily_arc_label_gap=args.tree_subfamily_arc_label_gap,
                subfamily_arc_label_size=args.tree_subfamily_arc_label_size,
            )
            
            if tree_plot_result and tree_plot_result.get("success", False):
                print(f"✅ 进化树图绘制完成")
            else:
                print(f"⚠️  进化树图绘制失败")
       


       # 8. 确定分析状态
        status = "success"
        if not iqtree_result.get("success", False):
            if mafft_result.get("success", False):
                status = "partial_success"
                print("⚠️  分析部分成功: MAFFT成功但IQ-TREE失败")
            else:
                status = "failed"
        
        # 9. 准备artifacts信息（字典格式）
        artifacts = {}
        
        if msa_final.exists():
            artifacts["msa"] = {
                "artifact": "msa",
                "path": f"final/{output_prefix_token}.pep.msa.fa",
                "full_path": display_path(msa_final),
                "type": "fasta",
                "size": msa_final.stat().st_size,
                "sha256": compute_file_hash(msa_final),
                "mtime": msa_final.stat().st_mtime
            }
        
        if tree_final and tree_final.exists():
            artifacts["tree"] = {
                "artifact": "tree",
                "path": f"final/{output_prefix_token}.tree.nwk",
                "full_path": display_path(tree_final),
                "type": "newick",
                "size": tree_final.stat().st_size,
                "sha256": compute_file_hash(tree_final),
                "mtime": tree_final.stat().st_mtime
            }
       
                # 添加MSA绘图文件到artifacts
        if msa_plot_result and msa_plot_result.get("success", False):
            
            # for i, img_file in enumerate(msa_plot_result.get("output_files", []), 1):
            #     img_path = Path(img_file)
            #     if img_path.exists():
            png_added = False
            for img_file in msa_plot_result.get("output_files", []):
                img_path = Path(img_file)
                if not img_path.exists():
                    continue

                # 只处理 PNG 图片用于 Gallery 显示
                if img_path.suffix.lower() == ".png":
                    artifact_key = f"msa_plot_{img_path.stem}"
                    artifacts[artifact_key] = {
                        "artifact": "msa_plot",
                        "path": str(img_path.relative_to(workspace)),
                        "full_path": display_path(img_path),
                        "type": "image",  # 只有 PNG 才是 image
                        "format": "png",
                        "size": img_path.stat().st_size,
                        "sha256": compute_file_hash(img_path),
                        "caption": "MSA Alignment"
                    }
                    png_added = True
                    print(f"[DEBUG] Added MSA PNG artifact: {img_path.name}")
                
                # PDF 作为 document 类型，不进入 Gallery
                elif img_path.suffix.lower() == ".pdf":
                    artifact_key = f"msa_plot_{img_path.stem}_pdf"
                    artifacts[artifact_key] = {
                        "artifact": "msa_plot_pdf",
                        "path": str(img_path.relative_to(workspace)),
                        "full_path": display_path(img_path),
                        "type": "document",  # 不是 image！
                        "format": "pdf",
                        "size": img_path.stat().st_size,
                        "sha256": compute_file_hash(img_path)
                    }
                    print(f"[DEBUG] Added MSA PDF artifact: {img_path.name}")
            
            if not png_added:
                print(f"[WARN] No MSA PNG file found in output_files")
                    # artifact_key = f"msa_plot_{i}"
                    # if img_path.suffix == ".png":
                    #     artifact_key += "_png"
                    # elif img_path.suffix == ".pdf":
                    #     artifact_key += "_pdf"
                    
                    # artifacts[artifact_key] = {
                    #     "artifact": "msa_plot",
                    #     "path": str(img_path.relative_to(workspace)),
                    #     "full_path": str(img_path),
                    #     "type": "image",
                    #     "format": img_path.suffix[1:],  # 去掉点号
                    #     "size": img_path.stat().st_size,
                    #     "sha256": compute_file_hash(img_path)
                    # }
        
        if msa_conserved_region_result and msa_conserved_region_result.get("success", False):
            for output_file in msa_conserved_region_result.get("output_files", []):
                conserved_path = Path(output_file)
                if not conserved_path.exists():
                    continue
                suffix = conserved_path.suffix.lower()
                if suffix == ".png":
                    key = f"msa_conserved_region_plot_{conserved_path.stem}"
                    artifacts[key] = {
                        "artifact": "msa_conserved_region_plot",
                        "path": str(conserved_path.relative_to(workspace)),
                        "full_path": display_path(conserved_path),
                        "type": "image",
                        "format": "png",
                        "size": conserved_path.stat().st_size,
                        "sha256": compute_file_hash(conserved_path),
                        "caption": "Automatically selected conserved MSA region",
                    }
                elif suffix == ".pdf":
                    key = f"msa_conserved_region_plot_{conserved_path.stem}_pdf"
                    artifacts[key] = {
                        "artifact": "msa_conserved_region_plot_pdf",
                        "path": str(conserved_path.relative_to(workspace)),
                        "full_path": display_path(conserved_path),
                        "type": "document",
                        "format": "pdf",
                        "size": conserved_path.stat().st_size,
                        "sha256": compute_file_hash(conserved_path),
                    }
                elif suffix in {".tsv", ".fa", ".json"}:
                    key = f"msa_conserved_region_{conserved_path.stem}_{suffix[1:]}"
                    artifacts[key] = {
                        "artifact": "msa_conserved_region_file",
                        "path": str(conserved_path.relative_to(workspace)),
                        "full_path": display_path(conserved_path),
                        "type": "file",
                        "format": suffix[1:],
                        "size": conserved_path.stat().st_size,
                        "sha256": compute_file_hash(conserved_path),
                    }

        # Add tree plot files returned by run_tree_plotting only.
        if tree_plot_result and tree_plot_result.get("success", False):
            for output_file in tree_plot_result.get("output_files", []):
                plot_path = Path(output_file)
                if not plot_path.exists():
                    continue
                if plot_path.suffix.lower() == ".png":
                    key = f"tree_plot_{plot_path.stem}"
                    artifacts[key] = {
                        "artifact": "tree_plot",
                        "path": str(plot_path.relative_to(workspace)),
                        "full_path": display_path(plot_path),
                        "type": "image",
                        "format": "png",
                        "size": plot_path.stat().st_size,
                        "sha256": compute_file_hash(plot_path),
                        "caption": "Tree Plot"
                    }
                elif plot_path.suffix.lower() == ".pdf":
                    key = f"tree_plot_{plot_path.stem}_pdf"
                    artifacts[key] = {
                        "artifact": "tree_plot_pdf",
                        "path": str(plot_path.relative_to(workspace)),
                        "full_path": display_path(plot_path),
                        "type": "document",
                        "format": "pdf",
                        "size": plot_path.stat().st_size,
                        "sha256": compute_file_hash(plot_path)
                    }
            if tree_plot_result.get("output_dir"):
                output_dir = Path(tree_plot_result["output_dir"])
                if output_dir.exists():
                    artifacts["tree_plot_output_dir"] = {
                        "artifact": "tree_plot_directory",
                        "path": str(output_dir.relative_to(workspace)),
                        "full_path": display_path(output_dir),
                        "type": "directory",
                        "file_count": len(list(output_dir.glob("*")))
                    }

        # 10. ??metrics
        combined_metrics = {}
        if msa_metrics:
            combined_metrics.update(msa_metrics)
        
        if iqtree_result.get("best_model"):
            combined_metrics["tree_model"] = iqtree_result["best_model"]
        
        if mafft_result.get("metrics", {}).get("alignment_time_sec"):
            combined_metrics["alignment_time_sec"] = mafft_result["metrics"]["alignment_time_sec"]
        
        if iqtree_result.get("tree_building_time_sec"):
            combined_metrics["tree_building_time_sec"] = iqtree_result["tree_building_time_sec"]
        if tree_final and tree_final.exists():
            combined_metrics["tree_tip_count"] = count_tree_tips(tree_final)
        
        # 添加绘图相关metrics
        if msa_plot_result:
            combined_metrics["msa_plotting_success"] = msa_plot_result.get("success", False)
            if msa_plot_result.get("elapsed_sec"):
                combined_metrics["msa_plotting_time_sec"] = msa_plot_result["elapsed_sec"]

        if msa_conserved_region_result:
            combined_metrics["msa_conserved_region_success"] = msa_conserved_region_result.get("success", False)
            if msa_conserved_region_result.get("elapsed_sec"):
                combined_metrics["msa_conserved_region_time_sec"] = msa_conserved_region_result["elapsed_sec"]
        
        if tree_plot_result:
            combined_metrics["tree_plotting_success"] = tree_plot_result.get("success", False)
            if tree_plot_result.get("elapsed_sec"):
                combined_metrics["tree_plotting_time_sec"] = tree_plot_result["elapsed_sec"]
            if tree_plot_result.get("subfamily_file"):
                combined_metrics["tree_subfamily_file"] = tree_plot_result["subfamily_file"]
                combined_metrics["tree_plot_mode"] = tree_plot_result.get("mode", "")
        combined_metrics["scope_sequence_count"] = scope_info.get("sequence_count")
        if scope_info.get("species_counts"):
            combined_metrics["scope_species_counts"] = scope_info.get("species_counts")


        # 11. 准备parameters
        parameters = {
            "cpu": args.cpu,
            "prefix": effective_prefix,
            "output_prefix": output_prefix_token,
            "mafft_params": args.mafft_params or "--auto --thread {cpu}",
            "iqtree_params": args.iqtree_params or f"-m MFP -bb 1000 -nt {args.cpu}",
            "tree_layout": args.tree_layout,
            "tree_subfamily_arc_enabled": str(args.tree_subfamily_arc_enabled).lower() != "false",
            "tree_subfamily_arc_gap": args.tree_subfamily_arc_gap,
            "tree_subfamily_arc_label_gap": args.tree_subfamily_arc_label_gap,
            "tree_subfamily_arc_label_size": args.tree_subfamily_arc_label_size,
            "mafft_version": tool_versions.get("mafft", "unknown"),
            "iqtree_version": tool_versions.get("iqtree", "unknown"),
            "stage": "phylogeny",
            "job_id": job_id,
            "run_id": run_id,
            "manifest_version": "1.0",
            "status": status,
            "scope_id": scope_info.get("scope_id", ""),
            "scope_file": scope_info.get("scope_file", ""),
            "scope_input_fasta": scope_info.get("input_fasta", ""),
            "tree_subfamily_file": tree_plot_result.get("subfamily_file") if tree_plot_result else "",
            "tree_subfamily_disabled": bool(locals().get("subfamily_disabled", False)),
            "tree_plot_mode": tree_plot_result.get("mode") if tree_plot_result else "",
        }
        
        # 12. 记录命令历史
        cmd_history = []
        if mafft_result.get("command"):
            cmd_history.append(mafft_result["command"])
        if iqtree_result.get("command"):
            cmd_history.append(iqtree_result["command"])
        
        # 13. 写入报告
        print("\n📝 生成分析报告...")
        report_path = write_report(
            workspace=workspace,
            prefix=output_prefix_token,
            metrics=combined_metrics,
            parameters=parameters,
            tool_versions=tool_versions,
            mafft_result=mafft_result,
            iqtree_result=iqtree_result,
            msa_plot_result=msa_plot_result,  # 新增
            tree_plot_result=tree_plot_result,  # 新增
            artifacts=artifacts
        )
        
        # 添加报告到artifacts
        if report_path.exists():
            artifacts["report"] = {
                "artifact": "report",
                "path": f"final/{output_prefix_token}.phylogeny_report.md",
                "full_path": display_path(report_path),
                "type": "markdown",
                "size": report_path.stat().st_size,
                "sha256": compute_file_hash(report_path)
            }
        
        # 14. 写入manifest
        print("\n📋 生成Manifest文件...")
        end_time = datetime.now()
        manifest_path = write_manifest(
            workspace=workspace,
            job_id=job_id,
            run_id=run_id,
            prefix=effective_prefix,
            start_time=start_time,
            end_time=end_time,
            artifacts=artifacts,
            metrics=combined_metrics,
            parameters=parameters,
            tool_versions=tool_versions,
            cmd_history=cmd_history,
            identify_inputs=identify_inputs,
            status=status,
            scope_info=scope_info,
            output_prefix=output_prefix_token,
            analysis_route=args.analysis_route,
            member_set_id=args.member_set_id,
            subfamily_file=tree_plot_result.get("subfamily_file") if tree_plot_result else "",
        )
        
        # 添加manifest自身到artifacts
        if manifest_path.exists():
            artifacts["manifest"] = {
                "artifact": "manifest",
                "path": str(manifest_path.relative_to(workspace)),
                "full_path": display_path(manifest_path),
                "type": "json",
                "size": manifest_path.stat().st_size,
                "sha256": compute_file_hash(manifest_path)
            }
        
        # 15. 更新manifest包含完整的artifacts
        if manifest_path.exists():
            with open(manifest_path, 'r') as f:
                manifest_data = json.load(f)
            manifest_data["artifacts"] = artifacts
            with open(manifest_path, 'w') as f:
                json.dump(manifest_data, f, indent=2, ensure_ascii=False)
            if scope_info.get("scope_id"):
                legacy_manifest = write_legacy_manifest_copy(manifest_path, effective_prefix, str(scope_info.get("scope_id") or ""), "phylogeny")
                if legacy_manifest:
                    print(f"   Legacy scoped manifest: {legacy_manifest}")
        
        # 16. 输出成功信息
        elapsed = (end_time - start_time).total_seconds()
        print("\n" + "=" * 60)
        print(f"✅ Phylogeny分析完成!")
        print(f"   总耗时: {elapsed:.1f} 秒")
        print(f"   状态: {status}")
        print(f"   Manifest: {manifest_path}")
        print(f"   报告: {report_path}")
        
        if msa_final.exists():
            print(f"   MSA: {msa_final}")
        
        if tree_final and tree_final.exists():
            print(f"   系统发育树: {tree_final}")

         # 添加绘图文件信息
        if msa_plot_result and msa_plot_result.get("success", False):
            output_files = msa_plot_result.get("output_files", [])
            print(f"   MSA比对图: {len(output_files)}个文件")
            for i, f in enumerate(output_files[:3], 1):  # 最多显示3个
                print(f"      {i}. {Path(f).name}")
        
        if tree_plot_result and tree_plot_result.get("success", False):
            output_files = tree_plot_result.get("output_files", [])
            print(f"   进化树图: {len(output_files)}个文件")
            for i, f in enumerate(output_files[:3], 1):  # 最多显示3个
                print(f"      {i}. {Path(f).name}")


        print("=" * 60)
        
        # 根据状态返回退出码
        if status == "success":
            sys.exit(0)
        elif status == "partial_success":
            sys.exit(1)  # 部分成功返回1
        else:
            sys.exit(2)  # 失败返回2
        
    except Exception as e:
        # 记录结束时间
        end_time = datetime.now()
        
        print(f"\n❌ Phylogeny分析失败: {e}")
        import traceback
        traceback.print_exc()
        
        try:
            # 创建基本的artifacts（可能部分成功）
            artifacts = {}
            failure_scope_info = locals().get("scope_info")
            if not failure_scope_info and args.scope_file:
                failure_scope_info = {
                    "enabled": True,
                    "scope_file": display_path(args.scope_file),
                }
                try:
                    scope_data = json.loads(Path(args.scope_file).read_text(encoding="utf-8"))
                    failure_scope_info.update({
                        "scope_id": scope_data.get("scope_id", ""),
                        "scope_mode": scope_data.get("scope_mode", ""),
                        "target_species": scope_data.get("target_species", []),
                        "reference_species": scope_data.get("reference_species", []),
                        "comparative_species": scope_data.get("comparative_species", []),
                        "selected_species": scope_data.get("selected_species", []),
                    })
                except Exception:
                    pass
            
            # 尝试写入失败manifest
            manifest_path = write_manifest(
                workspace=workspace,
                job_id=job_id,
                run_id=run_id,
                prefix=effective_prefix,
                start_time=start_time,
                end_time=end_time,
                artifacts=artifacts,
                metrics={},
                parameters={
                    "cpu": args.cpu, 
                    "prefix": effective_prefix,
                    "error": str(e)[:200],
                    "subfamily_file": "" if str(args.subfamily or "").strip().lower() in {"none", "skip", "disabled"} else (args.subfamily or "")
                },
                tool_versions=tool_versions if 'tool_versions' in locals() else {},
                cmd_history=[],
                identify_inputs=identify_inputs if 'identify_inputs' in locals() else [],
                status="failed",
                scope_info=failure_scope_info,
                output_prefix=locals().get("output_prefix_token", effective_prefix),
                analysis_route=args.analysis_route,
                member_set_id=args.member_set_id,
                subfamily_file="",
            )
            
            print(f"   失败Manifest已写入: {manifest_path}")
            
        except Exception as manifest_error:
            print(f"   写入失败Manifest时出错: {manifest_error}")
        
        sys.exit(2)


if __name__ == "__main__":
    main()
