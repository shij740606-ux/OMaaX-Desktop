#!/usr/bin/env python3
"""
Motif stage: MEME motif 鉴定、分析和可视化
包含：MEME 鉴定 -> 信息汇总 -> SeqLogo 绘图 -> 树+Motif 组合图
符合 workspace_contract_v1.md 和 manifest_v1.schema.json 契约
"""

import os
import argparse
import json
import subprocess
import sys
import re
import uuid
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import hashlib
import xml.etree.ElementTree as ET

PROJECT_ROOT = Path(__file__).absolute().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.gene_family_scope import read_scope, sanitize_token
from gfa_core.gene_family_stage_context import apply_stage_context, build_stage_context, user_output_prefix_for, write_legacy_manifest_copy
from gfa_core.mas.effective_members import MemberResolutionError, resolve_effective_members


CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")

PROJECT_MPI_MEME_PREFIX = PROJECT_ROOT / ".local" / "meme-4.11.2-mpi"
PROJECT_MPI_PREFIX = PROJECT_ROOT / ".tmp_meme_mpi"


def display_path(path: Path | str) -> str:
    """Return the user-facing symlink path instead of resolving data-root links."""
    return str(Path(path).absolute())


def current_input_dir(workspace: Path) -> Path:
    return workspace.parent.parent / "input" / workspace.name


def workspace_root_dir(workspace: Path) -> Path:
    return workspace.parent


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
    """Resolve a manifest path without trusting stale absolute full_path values."""
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

    candidates: List[Path] = [workspace / path]
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


def meme_runtime_env() -> Tuple[Dict[str, str], Dict[str, object]]:
    """Build a MEME runtime environment from the active conda installation.

    The MEME 4.11.2 package on this machine has build-time /opt paths baked into
    parts of the suite.  MEME itself honors MEME_ETC_DIR and MEME_BIN_DIR, so set
    them explicitly to the active conda prefix and keep the standard XML/TXT
    output path instead of switching the pipeline to text-only mode.
    """
    env = os.environ.copy()
    configured_meme = env.get("PLANTAI_MEME_BIN", "").strip()
    project_mpi_meme = PROJECT_MPI_MEME_PREFIX / "bin" / "meme"
    if configured_meme and Path(configured_meme).exists():
        meme_path = Path(configured_meme)
    elif project_mpi_meme.exists():
        meme_path = project_mpi_meme
    else:
        meme_path = Path(shutil.which("meme") or str(Path(CONDA_BIN) / "meme"))
    meme_path = meme_path.resolve()
    meme_bin_dir = meme_path.parent
    conda_prefix = meme_bin_dir.parent
    meme_etc_dir = conda_prefix / "etc"
    prior30 = meme_etc_dir / "prior30.plib"
    env["MEME_BIN_DIR"] = str(meme_bin_dir)
    env["MEME_ETC_DIR"] = str(meme_etc_dir)
    env.setdefault("MEME_TEMP_DIR", os.environ.get("TMPDIR", "/tmp"))
    env["PATH"] = str(meme_bin_dir) + ":" + env.get("PATH", "")
    using_project_mpi_meme = False
    try:
        using_project_mpi_meme = meme_path.is_relative_to(PROJECT_MPI_MEME_PREFIX.resolve())
    except OSError:
        using_project_mpi_meme = False
    if using_project_mpi_meme and PROJECT_MPI_PREFIX.exists():
        env["OPAL_PREFIX"] = str(PROJECT_MPI_PREFIX)
        mpi_lib = str(PROJECT_MPI_PREFIX / "lib")
        env["LD_LIBRARY_PATH"] = mpi_lib + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    info: Dict[str, object] = {
        "meme_bin": str(meme_path),
        "meme_bin_dir": str(meme_bin_dir),
        "meme_etc_dir": str(meme_etc_dir),
        "prior30_plib": str(prior30),
        "prior30_exists": prior30.exists(),
        "using_project_mpi_meme": using_project_mpi_meme,
        "mpi_prefix": str(PROJECT_MPI_PREFIX) if using_project_mpi_meme else None,
        "mpi_prefix_exists": PROJECT_MPI_PREFIX.exists() if using_project_mpi_meme else None,
    }
    return env, info


def get_tool_versions() -> Dict[str, str]:
    """获取工具版本信息"""
    versions = {}
    
    # R 版本
    try:
        r = subprocess.run(["R", "--version"], capture_output=True, text=True, check=False)
        if r.returncode == 0:
            versions["R"] = r.stdout.split("\n")[0]
    except:
        versions["R"] = "not_found"
    
    # Biopython
    try:
        import Bio
        versions["biopython"] = Bio.__version__
    except:
        versions["biopython"] = "not_found"
    
    # MEME 版本
    try:
        meme_env, meme_env_info = meme_runtime_env()
        result = subprocess.run([str(meme_env_info["meme_bin"]), "-version"], capture_output=True, text=True, check=False, env=meme_env)
        if result.returncode == 0:
            versions["meme"] = result.stdout.strip()
    except:
        versions["meme"] = "not_found"
    
    versions["motif_parser"] = "converted_python:get_meme_pos.py"
    return versions


def prepare_directories(workspace: Path) -> Dict[str, Path]:
    """创建Motif阶段的标准目录结构"""
    paths = {
        "analysis_dir": workspace / "motif" / "analysis",
        "meme_out_dir": workspace / "motif" / "analysis" / "meme_out",
        "final_dir": workspace / "final",
        "logs_dir": workspace / "logs",
    }
    
    print("创建目录结构:")
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        print(f"  {name}: {path}")
    
    return paths
def check_meme_available() -> Tuple[bool, str]:
    """检查 MEME 是否可用"""
    try:
        meme_env, meme_env_info = meme_runtime_env()
        meme_path = str(meme_env_info["meme_bin"])
        if not Path(meme_path).exists():
            return False, f"MEME not found: {meme_path}"
        version_result = subprocess.run(
            [meme_path, "-version"],
            capture_output=True,
            text=True,
            check=False,
            env=meme_env,
        )
        version = version_result.stdout.strip() if version_result.returncode == 0 else "unknown"
        if version_result.returncode != 0:
            detail = (version_result.stderr or version_result.stdout or "").strip()
            return False, f"MEME found at {meme_path}, but failed to start: {detail[-500:]}"
        return True, f"MEME found at {meme_path}, version: {version}"
    except Exception as e:
        return False, f"Error checking MEME: {str(e)}"

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


def count_fasta_records(fasta_file: Path) -> int:
    count = 0
    with open(fasta_file, encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if line.startswith(">"):
                count += 1
    return count


def ensure_protein_fasta_without_stop_markers(protein_fasta: Path) -> Path:
    """Use a job-local derived FASTA without '*' stop markers when needed."""
    has_stop = False
    with open(protein_fasta, encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.startswith(">") and "*" in line:
                has_stop = True
                break
    if not has_stop:
        return protein_fasta
    clean_name = re.sub(r"(\.fa(?:sta)?)$", r".clean\1", protein_fasta.name, flags=re.IGNORECASE)
    if clean_name == protein_fasta.name:
        clean_name = protein_fasta.name + ".clean.fa"
    clean_fasta = protein_fasta.with_name(clean_name)
    if clean_fasta.exists() and clean_fasta.stat().st_mtime >= protein_fasta.stat().st_mtime:
        return clean_fasta
    with open(protein_fasta, "r", encoding="utf-8", errors="ignore") as src, open(clean_fasta, "w", encoding="utf-8") as dst:
        for line in src:
            dst.write(line if line.startswith(">") else line.replace("*", ""))
    print(f"[INFO] Created stop-marker-clean FASTA for MEME: {clean_fasta}")
    return clean_fasta


def resolve_scope_protein_fasta(ws: Path, prefix: str, scope_data: Dict, cli_fasta: Optional[str] = None) -> Path:
    if cli_fasta:
        fasta_path = resolve_job_path(ws, cli_fasta) or Path(cli_fasta)
        if fasta_path.exists() and fasta_path.stat().st_size > 0:
            return ensure_protein_fasta_without_stop_markers(fasta_path)
        raise FileNotFoundError(f"Specified protein FASTA not found: {cli_fasta}")

    final_dir = ws / "final"
    scope_id = str(scope_data.get("scope_id") or "")
    manifest_candidates = []
    if scope_id:
        manifest_candidates.append(final_dir / f"{prefix}.{sanitize_token(scope_id)}.phylogeny.manifest.json")
    else:
        manifest_candidates.append(final_dir / f"{prefix}.phylogeny.manifest.json")

    for manifest_file in manifest_candidates:
        if not manifest_file.exists():
            continue
        manifest_data = load_manifest(manifest_file)
        if not manifest_data:
            continue
        params = manifest_data.get("parameters", {})
        fasta_text = params.get("scope_input_fasta")
        if fasta_text:
            fasta_path = resolve_job_path(ws, fasta_text, manifest_file)
            if fasta_path and fasta_path.exists() and fasta_path.stat().st_size > 0:
                print(f"[INFO] Found scoped protein FASTA from phylogeny manifest: {fasta_path}")
                return ensure_protein_fasta_without_stop_markers(fasta_path)

    raise FileNotFoundError(
        "Cannot find scoped protein FASTA. Run phylogeny with the same scope first, "
        "or provide --fasta explicitly."
    )


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
        manifest_data = load_manifest(manifest_file)
        if not manifest_data:
            continue
        artifact = (manifest_data.get("artifacts", {}) or {}).get("tree")
        tree_path = None
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


def load_manifest(manifest_file: Path) -> Optional[Dict]:
    """加载 manifest 文件"""
    try:
        with open(manifest_file, 'r') as f:
            return json.load(f)
    except:
        return None


def resolve_protein_fasta(
    ws: Path,
    prefix: str,
    cli_fasta: Optional[str] = None,
    identify_manifest: Optional[Path] = None,
    analysis_route: str = "",
    member_set_id: str = "",
) -> Path:
    """
    解析蛋白序列文件路径
    
    命名规则: final/{prefix}.final_members.pep.fa
    来源: Identify 阶段产物
    """
    # 方式一：命令行指定
    if cli_fasta:
        fasta_path = resolve_job_path(ws, cli_fasta) or Path(cli_fasta)
        if fasta_path.exists():
            print(f"[INFO] Using specified protein FASTA: {fasta_path}")
            return ensure_protein_fasta_without_stop_markers(fasta_path)
        else:
            raise FileNotFoundError(f"Specified protein FASTA not found: {cli_fasta}")

    phase = "subfamily_evidence_required" if analysis_route == "subfamily_evidence" else "post_confirm_required"
    try:
        resolved = resolve_effective_members(
            workspace=ws,
            final_dir=ws / "final",
            input_dir=current_input_dir(ws),
            prefix=prefix,
            phase=phase,  # type: ignore[arg-type]
            require="pep",
            member_set_id=member_set_id,
        )
        if resolved.path_pep:
            print(f"[INFO] Found protein FASTA from {resolved.source_kind}: {resolved.path_pep}")
            return ensure_protein_fasta_without_stop_markers(resolved.path_pep)
    except MemberResolutionError as exc:
        if analysis_route == "subfamily_evidence":
            raise FileNotFoundError(f"Complete identify member review before Motif subfamily evidence: {exc}") from exc
        raise FileNotFoundError(f"Complete subfamily confirmation before regular Motif analysis: {exc}") from exc
    
    # 方式二：从 Identify manifest 的 artifacts 获取（最可靠）
    if identify_manifest and identify_manifest.exists():
        manifest_data = load_manifest(identify_manifest)
        if manifest_data and "artifacts" in manifest_data:
            artifacts = manifest_data["artifacts"]
            # 尝试获取 final_proteins 路径
            if "final_proteins" in artifacts:
                protein_path = manifest_artifact_path(ws, identify_manifest, artifacts["final_proteins"])
                if protein_path and protein_path.exists():
                    print(f"[INFO] Found protein FASTA from Identify manifest: {protein_path}")
                    return ensure_protein_fasta_without_stop_markers(protein_path)
            # 备选：尝试从 artifacts 中找到 pep.fa 文件
            for key, artifact in artifacts.items():
                if isinstance(artifact, dict) and artifact.get("type") == "fasta":
                    protein_path = manifest_artifact_path(ws, identify_manifest, artifact)
                    raw_text = " ".join(str(artifact.get(name) or "") for name in ("path", "relpath_or_hint", "file", "full_path"))
                    if protein_path and (".pep." in str(protein_path) or ".pep." in raw_text):
                        if protein_path.exists():
                            print(f"[INFO] Found protein FASTA from manifest artifact '{key}': {protein_path}")
                            return ensure_protein_fasta_without_stop_markers(protein_path)
    
    # 方式三：标准路径查找
    final_dir = ws / "final"
    candidates = [final_dir / f"{prefix}.final_members.pep.fa"]
    if "." in prefix:
        species, family = prefix.split(".", 1)
        if species not in {"Target", "Multi", "Reference", "Comparative"} and family:
            candidates.append(final_dir / f"Target.{species}.{family}.final_members.pep.fa")
    for fasta_path in candidates:
        if fasta_path.exists() and fasta_path.stat().st_size > 0:
            print(f"[INFO] Found protein FASTA: {fasta_path}")
            return ensure_protein_fasta_without_stop_markers(fasta_path)
    
    raise FileNotFoundError(
        "Cannot find protein FASTA in final/: "
        + ", ".join(path.name for path in candidates)
        + ". Please ensure Identify stage completed successfully."
    )


def resolve_subfamily_file(ws: Path, prefix: str, cli_subfamily: Optional[str] = None, output_prefix: Optional[str] = None) -> Optional[Path]:
    """
    解析亚家族文件路径
    
    命名规则: {prefix}.confirmed_subfamily.tsv
    优先级: final/ > input/ > root_input/
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
    for base in [output_prefix, prefix]:
        if not base or base in names:
            continue
        names.extend([
            f"{base}.confirmed_subfamily.tsv",
        ])
    subfamily_name = f"{prefix}.confirmed_subfamily.tsv"
    
    final_dir = ws / "final"
    input_dir = ws / "input"
    
    # 根目录 input（如 /home/userlab/.../bba/input/<job_id>/）
    root_input_dir = ws.parent.parent / "input" / ws.name
    
    # 优先级: final/ > input/ > root_input/
    search_paths = []
    if final_dir.exists():
        search_paths.extend(final_dir / name for name in names)
    if input_dir.exists():
        search_paths.extend(input_dir / name for name in names)
    if root_input_dir.exists():
        search_paths.extend(root_input_dir / name for name in names)
    
    # 调试输出
    print(f"[DEBUG] Looking for subfamily file: {subfamily_name}")
    for candidate in search_paths:
        print(f"[DEBUG] Checking: {candidate} -> exists: {candidate.exists()}")
        if candidate.exists():
            print(f"[INFO] Found subfamily file: {candidate}")
            return candidate
    
    print(f"[INFO] No subfamily file found ({subfamily_name}), will use default coloring")
    return None


def run_meme(fasta_file: Path, output_dir: Path, prefix: str, 
             nmotifs: int = 10, minw: int = 6, maxw: int = 200,
             cpu: int = 4, try_parallel: bool = True, skip_existing: bool = False) -> Dict:
    """运行 MEME 进行 motif 鉴定"""
    meme_out_dir = output_dir / "meme_out"
    meme_env, meme_env_info = meme_runtime_env()
    signature = {
        "fasta": display_path(fasta_file),
        "fasta_sha256": compute_file_hash(fasta_file),
        "nmotifs": nmotifs,
        "minw": minw,
        "maxw": maxw,
        "mode": "anr",
        "seq_type": "protein",
        "meme_etc_dir": meme_env_info.get("meme_etc_dir"),
    }
    signature_file = meme_out_dir / "run_signature.json"
    
    # 检查是否已有结果
    xml_file = meme_out_dir / "meme.xml"
    txt_file = meme_out_dir / "meme.txt"
    if xml_file.exists() and txt_file.exists() and xml_file.stat().st_size > 0 and txt_file.stat().st_size > 0:
        existing_signature = None
        if signature_file.exists():
            try:
                existing_signature = json.loads(signature_file.read_text(encoding="utf-8"))
            except Exception:
                existing_signature = None
        if existing_signature == signature:
            print(f"[INFO] Found existing MEME results with matching signature: {xml_file}")
            return {
                "success": True,
                "meme_out_dir": str(meme_out_dir),
                "xml_file": str(xml_file),
                "txt_file": str(txt_file),
                "existing": True,
                "returncode": 0,
                "postprocess_warning": False,
                "meme_env": meme_env_info,
            }
        if skip_existing:
            print(f"[INFO] Reusing existing MEME results because --skip-meme was set: {xml_file}")
            return {
                "success": True,
                "meme_out_dir": str(meme_out_dir),
                "xml_file": str(xml_file),
                "txt_file": str(txt_file),
                "existing": True,
                "returncode": 0,
                "postprocess_warning": False,
                "meme_env": meme_env_info,
                "signature_mismatch_reused": True,
            }
        print(f"[INFO] Existing MEME results do not match current input/parameters, rerunning: {meme_out_dir}")
        shutil.rmtree(meme_out_dir)
    
    print(f"[INFO] Running MEME motif prediction...")
    print(f"  - Input: {fasta_file}")
    print(f"  - Output: {meme_out_dir}")
    print(f"  - Parameters: nmotifs={nmotifs}, minw={minw}, maxw={maxw}, cpu={cpu}")
    print(f"  - MEME_ETC_DIR: {meme_env_info.get('meme_etc_dir')}")

    if not meme_env_info.get("prior30_exists"):
        return {
            "success": False,
            "error": f"MEME prior library not found: {meme_env_info.get('prior30_plib')}",
            "meme_env": meme_env_info,
            "meme_out_dir": str(meme_out_dir),
        }
    
    # 构建基础命令（不含 -p）
    base_cmd = [
        "meme",
        str(fasta_file),
        "-mod", "anr",
        "-protein",
        "-nmotifs", str(nmotifs),
        "-minw", str(minw),
        "-maxw", str(maxw),
        "-maxsize", "1000000",  # 增加最大序列数限制，支持大数据集
        "-oc", str(meme_out_dir),
        "-nostatus"
    ]
    
    # 如果尝试并行且 cpu > 1，添加 -p 参数
    cmd = base_cmd.copy()
    if try_parallel and cpu > 1:
        cmd.extend(["-p", str(cpu)])
    
    print(f"[CMD] {' '.join(cmd)}")
    
    start_time = datetime.now()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            env=meme_env,
            timeout=3600
        )
        end_time = datetime.now()
        
        # 检查是否是并行不支持的错误
        if result.returncode != 0 and "Parallel MEME not configured" in result.stderr:
            if try_parallel and cpu > 1:
                print(f"[WARNING] Parallel MEME not configured, retrying with single thread...")
                # 递归调用，不使用并行
                return run_meme(fasta_file, output_dir, prefix, nmotifs, minw, maxw, cpu=1, try_parallel=False)
        
        # 打印输出
        print(f"[MEME return code: {result.returncode}]")
        if result.stdout:
            print(f"[MEME stdout]\n{result.stdout[-5000:]}")
        if result.stderr:
            print(f"[MEME stderr]\n{result.stderr[-5000:]}")
        
        # 检查关键输出文件
        xml_file = meme_out_dir / "meme.xml"
        txt_file = meme_out_dir / "meme.txt"
        
        success = (
            xml_file.exists()
            and txt_file.exists()
            and xml_file.stat().st_size > 0
            and txt_file.stat().st_size > 0
        )
        
        if not success:
            error_msg = f"MEME failed: exit code {result.returncode}"
            if not xml_file.exists():
                error_msg += ", meme.xml not found"
            if not txt_file.exists():
                error_msg += ", meme.txt not found"
            
            return {
                "success": False,
                "returncode": result.returncode,
                "stdout": result.stdout[-5000:],
                "stderr": result.stderr[-5000:],
                "error": error_msg,
                "meme_env": meme_env_info,
                "meme_out_dir": str(meme_out_dir)
            }
        if result.returncode != 0:
            print("[WARNING] MEME returned non-zero after producing meme.xml and meme.txt; treating as post-processing warning")
        signature_file.write_text(json.dumps(signature, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        
        return {
            "success": True,
            "meme_out_dir": str(meme_out_dir),
            "xml_file": str(xml_file),
            "txt_file": str(txt_file),
            "existing": False,
            "elapsed_sec": (end_time - start_time).total_seconds(),
            "returncode": result.returncode,
            "postprocess_warning": result.returncode != 0,
            "stdout": result.stdout[-5000:],
            "stderr": result.stderr[-5000:],
            "meme_env": meme_env_info,
        }
        
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "MEME timeout (>3600s)"}
    except Exception as e:
        import traceback
        return {
            "success": False, 
            "error": str(e),
            "traceback": traceback.format_exc()
        }

def run_meme_summary(meme_txt: Path, prefix: str, output_dir: Path,
                     parser_script: Optional[Path] = None) -> Dict:
    """
    运行 Perl 脚本汇总 MEME 信息
    
    生成: {prefix}.motif_prot.txt (注意: prefix 中的 . 替换为 _)
    """
    if parser_script is None:
        parser_script = Path(__file__).parent.parent.parent / "scripts" / "get_meme_pos.py"
    
    if not parser_script.exists():
        return {"success": False, "error": f"MEME parser script not found: {parser_script}"}
    
    # 输出文件名: Ft_SPL.motif_prot.txt
    safe_prefix = prefix.replace(".", "_")
    output_name = f"{safe_prefix}.motif_prot.txt"
    output_file = output_dir / output_name
    
    # 检查是否已有结果
    if output_file.exists() and output_file.stat().st_size > 0:
        print(f"[INFO] Found existing motif summary: {output_file}")
        return {
            "success": True,
            "output_file": str(output_file),
            "existing": True
        }
    
    cmd = [
        sys.executable, str(parser_script),
        display_path(meme_txt),
        safe_prefix
    ]
    
    print(f"[CMD] {' '.join(cmd)}")
    print(f"[INFO] Working dir: {output_dir}")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            cwd=str(output_dir),
            timeout=60
        )
        
        success = (
            result.returncode == 0
            and output_file.exists()
            and output_file.stat().st_size > 0
        )
        
        return {
            "success": success,
            "output_file": str(output_file) if success else None,
            "returncode": result.returncode,
            "stdout": result.stdout[-1000:],
            "stderr": result.stderr[-1000:]
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


def run_seqlogo_plot(motif_prot_txt: Path, prefix: str, output_dir: Path,
                    rscript: Optional[Path] = None) -> Dict:
    """
    运行 R 脚本绘制 motif SeqLogo 图
    
    注意：draw_motif_seqlogo.R 输出到 meme_out/ 目录
    """
    if rscript is None:
        rscript = Path(__file__).parent.parent.parent / "scripts" / "draw_motif_seqlogo.R"
    
    if not rscript.exists():
        return {"success": False, "error": f"R script not found: {rscript}"}
    
    # SeqLogo 输出在 meme_out/ 目录
    meme_out_dir = output_dir / "meme_out"
    safe_prefix = prefix.replace(".", "_")
    
    # 检查是否已有结果（MEME 标准输出常见为 EPS；PNG 依赖 gs/HTML 后处理）
    seqlogo_files = list(meme_out_dir.glob("logo*.png")) + list(meme_out_dir.glob("logo*.eps"))
    if seqlogo_files:
        print(f"[INFO] Found existing SeqLogo plots in meme_out/: {len(seqlogo_files)} files")
        return {
            "success": True,
            "meme_out_dir": str(meme_out_dir),
            "logo_count": len(seqlogo_files),
            "png_count": len(list(meme_out_dir.glob("logo*.png"))),
            "eps_count": len(list(meme_out_dir.glob("logo*.eps"))),
            "existing": True
        }
    
    rscript_exe = Path(shutil.which("Rscript") or "Rscript")
    if not rscript_exe.exists():
        rscript_exe = Path("Rscript")

    cmd = [
        str(rscript_exe), str(rscript),
        str(motif_prot_txt),
        str(output_dir / f"{safe_prefix}_motif")
    ]
    
    print(f"[CMD] {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=300
        )
        
        # 检查 meme_out/ 中是否生成了 logo 文件
        seqlogo_files = list(meme_out_dir.glob("logo*.png")) + list(meme_out_dir.glob("logo*.eps"))
        success = len(seqlogo_files) > 0
        
        if success:
            print(f"[INFO] SeqLogo generated: {len(seqlogo_files)} motif logos in meme_out/")
        elif result.stderr:
            print(f"[WARNING] SeqLogo stderr: {result.stderr[-1000:]}")
        
        return {
            "success": success,
            "meme_out_dir": str(meme_out_dir) if success else None,
            "logo_count": len(seqlogo_files),
            "png_count": len(list(meme_out_dir.glob("logo*.png"))),
            "eps_count": len(list(meme_out_dir.glob("logo*.eps"))),
            "returncode": result.returncode,
            "stdout": result.stdout[-1000:] if result.stdout else "",
            "stderr": result.stderr[-1000:] if result.stderr else ""
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}

def run_tree_motif_plot(tree_file: Path, subfamily_file: Optional[Path],
                       meme_xml: Path, output_prefix: Path,
                       rscript: Optional[Path] = None) -> Dict:
    """
    运行 R 脚本绘制树+Motif 组合图
    """
    # 尝试多个可能的脚本名
    script_names = ["tree_motif.R", "plot_motif.R"]
    
    if rscript is None:
        scripts_dir = Path(__file__).parent.parent.parent / "scripts"
        for name in script_names:
            candidate = scripts_dir / name
            if candidate.exists():
                rscript = candidate
                print(f"[INFO] Using R script: {rscript}")
                break
    
    if rscript is None or not rscript.exists():
        return {"success": False, "error": f"R script not found. Tried: {script_names}"}
    
    rscript_exe = Path(shutil.which("Rscript") or "Rscript")
    if not rscript_exe.exists():
        rscript_exe = Path("Rscript")

    cmd = [
        str(rscript_exe), str(rscript),
        str(tree_file),
        str(subfamily_file) if subfamily_file else "",
        str(meme_xml),
        str(output_prefix)
    ]
    
    print(f"[CMD] {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=600
        )
        
        success = (result.returncode == 0)
        
        # 检查输出文件
        output_pdf = Path(f"{output_prefix}.tree_motif.pdf")
        output_png = Path(f"{output_prefix}.tree_motif.png")
        
        output_files = []
        if output_pdf.exists():
            output_files.append(str(output_pdf))
        if output_png.exists():
            output_files.append(str(output_png))
        
        return {
            "success": success and len(output_files) > 0,
            "output_files": output_files,
            "returncode": result.returncode,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-2000:]
        }
        
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "R plotting timeout (>600s)"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def parse_meme_xml_basic(meme_xml: Path) -> Dict:
    """解析 MEME XML 获取基本统计信息"""
    try:
        tree = ET.parse(meme_xml)
        root = tree.getroot()
        
        sequences = root.findall(".//sequence")
        seq_count = len(sequences)
        
        motifs = root.findall(".//motif")
        motif_count = len(motifs)
        
        motif_stats = []
        for motif in motifs:
            motif_id = motif.get("id", "unknown")
            motif_name = motif.get("name", motif_id)
            width = int(motif.get("width", 0))
            
            sites = motif.findall(".//contributing_site")
            site_count = len(sites)
            
            motif_stats.append({
                "id": motif_id,
                "name": motif_name,
                "width": width,
                "sites": site_count
            })
        
        avg_width = sum(m["width"] for m in motif_stats) / max(len(motif_stats), 1)
        
        return {
            "sequence_count": seq_count,
            "motif_count": motif_count,
            "motifs": motif_stats,
            "avg_motif_width": round(avg_width, 2),
            "total_sites": sum(m["sites"] for m in motif_stats)
        }
        
    except Exception as e:
        print(f"[WARNING] Failed to parse MEME XML for stats: {e}")
        return {
            "sequence_count": 0,
            "motif_count": 0,
            "motifs": [],
            "avg_motif_width": 0,
            "total_sites": 0,
            "error": str(e)
        }


def generate_report(prefix: str, metrics: Dict, parameters: Dict, 
                   output_file: Path, artifacts: Dict, stage_status: Dict) -> None:
    """生成 motif_report.md 报告"""
    
    all_success = all(stage_status.values())
    any_success = any(stage_status.values())
    
    if all_success:
        status_str = "Success"
    elif any_success:
        status_str = "Partial Success"
    else:
        status_str = "Failed"
    
    report_lines = [
        f"# Motif Analysis Report: {prefix}",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Status:** {status_str}",
        "",
        "## Pipeline Stages",
        ""
    ]
    
    for stage, success in stage_status.items():
        icon = "✅" if success else "❌"
        report_lines.append(f"- {icon} **{stage}**: {'Success' if success else 'Failed'}")
    
    report_lines.extend([
        "",
        "## Summary",
        "",
        f"- **Total Sequences:** {metrics['sequence_count']}",
        f"- **Total Motifs:** {metrics['motif_count']}",
        f"- **Total Motif Sites:** {metrics['total_sites']}",
        f"- **Average Motif Width:** {metrics['avg_motif_width']:.1f} aa",
        "",
        "## Motif Details",
        ""
    ])
    
    if metrics.get("motifs"):
        report_lines.append("| Motif ID | Name | Width (aa) | Sites |")
        report_lines.append("|----------|------|------------|-------|")
        for m in metrics["motifs"]:
            report_lines.append(f"| {m['id']} | {m['name']} | {m['width']} | {m['sites']} |")
        report_lines.append("")
    
    report_lines.extend([
        "## Parameters",
        "",
        f"- **Protein FASTA:** {parameters.get('protein_fasta', 'N/A')}",
        f"- **MEME Parameters:** nmotifs={parameters.get('meme_nmotifs', 10)}, "
        f"minw={parameters.get('meme_minw', 6)}, maxw={parameters.get('meme_maxw', 200)}",
        f"- **CPU Cores:** {parameters.get('cpu', 'N/A')}",
        f"- **Subfamily File:** {parameters.get('subfamily_file', 'Not provided')}",
        f"- **Tree File:** {parameters.get('tree_file', 'N/A')}",
        "",
        "## Output Files",
        ""
    ])
    
    for key, artifact in artifacts.items():
        if isinstance(artifact, dict):
            report_lines.append(f"- **{key}:** `{artifact.get('path', 'N/A')}`")
    
    report_lines.extend([
        "",
        "## Visualization",
        ""
    ])
    
    if stage_status.get("seqlogo", False):
        report_lines.append("- **SeqLogo Plots**: Motif sequence logos generated")
    if stage_status.get("tree_motif", False):
        report_lines.append("- **Tree+Motif Plot**: Combined phylogenetic tree and motif architecture")
    
    if not any([stage_status.get("seqlogo"), stage_status.get("tree_motif")]):
        report_lines.append("**Warning:** No visualizations generated. See logs for details.")
    
    report_lines.extend([
        "",
        "---",
        "*Generated by Gene Family Analysis Pipeline - Motif Stage*"
    ])
    
    with open(output_file, 'w') as f:
        f.write('\n'.join(report_lines))
    
    print(f"[INFO] Report generated: {output_file}")


def write_manifest(manifest: Dict, manifest_file: Path) -> None:
    """写入 manifest 文件"""
    with open(manifest_file, 'w') as f:
        json.dump(manifest, f, indent=2)
    print(f"[INFO] Manifest: {manifest_file}")


def quarantine_stale_unscoped_failure(final_dir: Path, prefix: str, stage: str, canonical_manifest: Path) -> Optional[Path]:
    """Keep but hide a stale unscoped failed manifest after a scoped success."""
    stale = final_dir / f"{prefix}.{stage}.manifest.json"
    if not stale.exists() or stale.absolute() == canonical_manifest.absolute():
        return None
    try:
        data = json.loads(stale.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("status") != "failed":
        return None
    if data.get("scope_id") or (data.get("stage_context") or {}).get("scope_id"):
        return None
    target = final_dir / f"{prefix}.{stage}.stale_failed.{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    stale.replace(target)
    print(f"[INFO] Quarantined stale unscoped failed manifest: {target}")
    return target


def write_failure_manifest(workspace: Path, final_dir: Path, prefix: str, 
                           job_id: str, run_id: str, start_time: datetime,
                          error_msg: str, partial_artifacts: Dict = None,
                          scope_data: Optional[Dict] = None,
                          scope_file: Optional[Path | str] = None,
                          output_prefix: Optional[str] = None,
                          analysis_route: str = "",
                          member_set_id: str = "",
                          subfamily_file: Optional[Path | str] = None,
                          extra_parameters: Optional[Dict] = None) -> None:
    """写入失败状态的 manifest"""
    end_time = datetime.now()
    output_prefix = output_prefix or prefix
    parameters = {
        "stage": "motif",
        "prefix": prefix,
        "output_prefix": output_prefix,
        "job_id": job_id,
        "run_id": run_id,
        "error": error_msg,
    }
    if extra_parameters:
        parameters.update(extra_parameters)
    
    manifest = {
        "manifest_version": "1.0",
        "job_id": job_id,
        "stage": "motif",
        "run_id": run_id,
        "prefix": prefix,
        "output_prefix": output_prefix,
        "timestamp": end_time.isoformat(),
        "status": "failed",
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "elapsed_sec": (end_time - start_time).total_seconds(),
        "cmd": [str(Path(__file__).name)],
        "workspace_root": display_path(workspace_root_dir(workspace)),
        "workspace": display_path(workspace),
        "input_dir": display_path(current_input_dir(workspace)),
        "artifacts": partial_artifacts or {},
        "parameters": parameters,
        "metrics": {
            "error_type": "execution_failed"
        }
    }
    if scope_data:
        manifest["scope_id"] = scope_data.get("scope_id", "")
        manifest["scope_mode"] = scope_data.get("scope_mode", "")
        manifest["scope_file"] = str(scope_file) if scope_file else ""
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
        stage="motif",
        prefix=prefix,
        scope_data=scope_data or {},
        scope_file=scope_file or "",
        output_prefix=output_prefix,
        subfamily_file=subfamily_file,
        analysis_route=analysis_route,
        member_set_id=member_set_id,
    )
    apply_stage_context(manifest, context)
    
    manifest_file = final_dir / f"{output_prefix}.motif.manifest.json"
    write_manifest(manifest, manifest_file)
    if scope_data and scope_data.get("scope_id"):
        write_legacy_manifest_copy(manifest_file, prefix, str(scope_data.get("scope_id") or ""), "motif")
    print(f"[ERROR] Failure manifest written: {manifest_file}")


def main():
    parser = argparse.ArgumentParser(description='Motif stage - MEME motif prediction and visualization')
    parser.add_argument('--prefix', help='Analysis prefix (e.g., Ft.SPL)')
    parser.add_argument('--workspace', help='Workspace directory (e.g., /path/to/workspace/<job_id>)')
    parser.add_argument('--manifest', help='Identify or Phylogeny stage manifest file (alternative to --prefix and --workspace)')
    parser.add_argument('--fasta', help='Protein FASTA file (auto-detect if not specified)')
    parser.add_argument('--scope-file', help='Gene-family scope JSON from resolve_scope.py')
    parser.add_argument('--analysis-route', default='', help='Explicit analysis route identity for manifest/status')
    parser.add_argument('--member-set-id', default='', help='Explicit member-set identity for manifest/status')
    parser.add_argument('--tree', help='Phylogeny tree file; auto-detects scoped phylogeny manifest when omitted')
    parser.add_argument('--subfamily', help='Subfamily classification file (explicit path; auto-detect only uses confirmed_subfamily.tsv)')
    parser.add_argument('--nmotifs', type=int, default=10, help='Number of motifs to find (default: 10)')
    parser.add_argument('--minw', type=int, default=6, help='Minimum motif width (default: 6)')
    parser.add_argument('--maxw', type=int, default=200, help='Maximum motif width (default: 200)')
    parser.add_argument('--cpu', type=int, default=4, help='CPU cores for processing')
    parser.add_argument('--skip-meme', action='store_true', help='Skip MEME if results exist')
  
    parser.add_argument('--log-file', help='Log file path')
    parser.add_argument('--run-id', help='Run ID (auto-generated if not provided)')
    
    args = parser.parse_args()
 
    # 检查 MEME 是否可用
    meme_available, meme_info = check_meme_available()
    print(f"[INFO] {meme_info}")
    if not meme_available:
        print(f"[ERROR] MEME is required but not available. Please install MEME Suite.")
        # 创建临时目录用于写入失败 manifest
        temp_ws = Path(args.workspace) if args.workspace else Path(".")
        temp_final = temp_ws / "final"
        temp_final.mkdir(parents=True, exist_ok=True)
        write_failure_manifest(temp_ws, temp_final, args.prefix or "unknown", 
                              "unknown", str(uuid.uuid4())[:8], 
                              datetime.now(), "MEME not found in PATH")
        sys.exit(1) 

    # 处理 --manifest 参数（与 backend 兼容）
    identify_manifest_file = None
    
    if args.manifest:
        manifest_file = Path(args.manifest)
        if not manifest_file.exists():
            print(f"[ERROR] Manifest file not found: {manifest_file}")
            sys.exit(1)
        
        # 从 manifest 提取信息
        manifest_data = load_manifest(manifest_file)
        if not manifest_data:
            print(f"[ERROR] Failed to load manifest: {manifest_file}")
            sys.exit(1)
        
        # 获取 prefix
        if not args.prefix:
            args.prefix = manifest_data.get("prefix")
            if not args.prefix:
                print(f"[ERROR] Cannot determine prefix from manifest")
                sys.exit(1)
        
        # 获取 workspace - 从 manifest 文件路径推断（更可靠）
        # manifest 路径: .../workspace/<job_id>/final/<prefix>.xxx.manifest.json
        if not args.workspace:
            # 从 manifest 文件的父目录的父目录获取 workspace（即 <job_id> 目录）
            inferred_workspace = manifest_file.parent.parent
            if inferred_workspace.exists() and (inferred_workspace / "final").exists():
                args.workspace = str(inferred_workspace)
                print(f"[INFO] Inferred workspace from manifest path: {args.workspace}")
            else:
                # 备选：使用 manifest 中的 workspace_root（如果它包含 final/）
                workspace_root = manifest_data.get("workspace_root")
                if workspace_root:
                    ws_root_path = Path(workspace_root)
                    # 检查 workspace_root 是否是具体的 job 目录（包含 final/）
                    if (ws_root_path / "final").exists():
                        args.workspace = workspace_root
                    else:
                        # workspace_root 可能是上级目录，尝试构建完整路径
                        job_id = manifest_data.get("job_id")
                        if job_id:
                            full_ws = ws_root_path / job_id
                            if (full_ws / "final").exists():
                                args.workspace = str(full_ws)
        
        # 记录 identify manifest 路径（用于后续查找蛋白文件）
        # 如果传入的是 phylogeny manifest，找到对应的 identify manifest
        identify_manifest_file = manifest_file.parent / f"{args.prefix}.identify.manifest.json"
        if not identify_manifest_file.exists():
            identify_manifest_file = manifest_file  # 回退到传入的 manifest
        
        print(f"[INFO] Loaded from manifest: prefix={args.prefix}, workspace={args.workspace}")
    
    # 验证必需参数
    if not args.prefix or not args.workspace:
        parser.print_help()
        print("\n[ERROR] Either --manifest or both --prefix and --workspace are required")
        sys.exit(1)
    
    # 验证 workspace 目录存在且包含 final/
    ws = Path(args.workspace)
    final_dir_test = ws / "final"
    if not final_dir_test.exists():
        print(f"[ERROR] Invalid workspace directory: {args.workspace}")
        print(f"[ERROR] Expected 'final/' subdirectory not found")
        sys.exit(1)
    
    run_id = args.run_id or str(uuid.uuid4())[:8]
    job_id = ws.name
    
    start_time = datetime.now()
    
    # 1. 创建标准目录结构
    motif_dir = ws / "motif" / "analysis"
    motif_dir.mkdir(parents=True, exist_ok=True)
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
        "motif",
        args.prefix,
        scope_data or {},
        analysis_route=args.analysis_route,
        member_set_id=args.member_set_id,
    ) if scope_id else args.prefix
    
    # 2. 解析输入文件（传入 identify_manifest_file 以优先从 manifest 获取蛋白路径）
    try:
        if scope_data:
            protein_fasta = resolve_scope_protein_fasta(ws, args.prefix, scope_data, args.fasta)
        else:
            protein_fasta = resolve_protein_fasta(
                ws,
                args.prefix,
                args.fasta,
                identify_manifest_file,
                args.analysis_route,
                args.member_set_id,
            )
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        write_failure_manifest(
            ws, final_dir, args.prefix, job_id, run_id, start_time, str(e),
            scope_data=scope_data,
            scope_file=scope_file,
            output_prefix=output_prefix,
            analysis_route=args.analysis_route,
            member_set_id=args.member_set_id,
        )
        sys.exit(1)
    
    subfamily_file = resolve_subfamily_file(ws, args.prefix, args.subfamily, output_prefix=output_prefix)
    protein_record_count = count_fasta_records(protein_fasta)
    
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
            raise FileNotFoundError(f"Complete identify member review before Motif subfamily evidence: {e}") from e
        raise FileNotFoundError(f"Complete subfamily confirmation before regular Motif analysis: {e}") from e
    if scope_data:
        gene_id_file = motif_dir / f"{output_prefix}.scope_members.id"
        with gene_id_file.open("w", encoding="utf-8") as handle:
            with protein_fasta.open(encoding="utf-8", errors="ignore") as fasta_handle:
                for raw in fasta_handle:
                    if raw.startswith(">"):
                        handle.write(raw[1:].split(None, 1)[0].strip() + "\n")
    try:
        tree_file = resolve_scope_tree_file(ws, args.prefix, scope_data, args.tree)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        write_failure_manifest(
            ws, final_dir, args.prefix, job_id, run_id, start_time, str(e),
            scope_data=scope_data,
            scope_file=scope_file,
            output_prefix=output_prefix,
            analysis_route=args.analysis_route,
            member_set_id=args.member_set_id,
            subfamily_file=subfamily_file,
        )
        sys.exit(1)
    
    missing = []
    if not gene_id_file.exists():
        missing.append(f"Gene ID file: {gene_id_file}")
    
    if missing:
        error_msg = "Missing required input files:\n" + "\n".join(missing)
        print(f"[ERROR] {error_msg}")
        write_failure_manifest(
            ws, final_dir, args.prefix, job_id, run_id, start_time, error_msg,
            scope_data=scope_data,
            scope_file=scope_file,
            output_prefix=output_prefix,
            analysis_route=args.analysis_route,
            member_set_id=args.member_set_id,
            subfamily_file=subfamily_file,
        )
        sys.exit(1)
    
    print(f"[INFO] Motif stage started for {args.prefix}")
    print(f"[INFO] Job ID: {job_id}, Run ID: {run_id}")
    print(f"[INFO] Protein FASTA: {protein_fasta}")
    
    # 记录各阶段状态
    stage_status = {
        "meme": False,
        "summary": False,
        "seqlogo": False,
        "tree_motif": False
    }
    
    artifacts = {}
    
    # ========== 阶段 1: MEME 鉴定 ==========
    print("\n[STAGE 1/4] MEME motif prediction...")
    
    meme_result = run_meme(
        protein_fasta,
        motif_dir,
        args.prefix,
        nmotifs=args.nmotifs,
        minw=args.minw,
        maxw=args.maxw,
        cpu=args.cpu,
        skip_existing=args.skip_meme,
    )
    
        
    if not meme_result.get("success"):
        error_msg = meme_result.get('error', 'unknown error')
        print(f"[ERROR] MEME failed: {error_msg}")
        
        # 如果有 stdout/stderr，保存到日志文件
        if meme_result.get('stdout') or meme_result.get('stderr'):
            log_file = motif_dir / f"{args.prefix}.meme_error.log"
            with open(log_file, 'w') as f:
                f.write(f"Error: {error_msg}\n\n")
                f.write(f"STDOUT:\n{meme_result.get('stdout', 'N/A')}\n\n")
                f.write(f"STDERR:\n{meme_result.get('stderr', 'N/A')}\n")
            print(f"[INFO] MEME error log saved to: {log_file}")
        
        write_failure_manifest(
            ws, final_dir, args.prefix, job_id, run_id, start_time, error_msg,
            scope_data=scope_data,
            scope_file=scope_file,
            output_prefix=output_prefix,
            analysis_route=args.analysis_route,
            member_set_id=args.member_set_id,
            subfamily_file=subfamily_file,
            extra_parameters={
                "meme_returncode": meme_result.get("returncode"),
                "meme_env": meme_result.get("meme_env"),
            },
        )
        sys.exit(1)    

    stage_status["meme"] = True
    meme_xml = Path(meme_result["xml_file"])
    meme_txt = Path(meme_result["txt_file"])
    stable_meme_xml = final_dir / f"{output_prefix}.meme.xml"
    stable_meme_txt = final_dir / f"{output_prefix}.meme.txt"
    shutil.copy2(meme_xml, stable_meme_xml)
    shutil.copy2(meme_txt, stable_meme_txt)
    
    if meme_result.get("existing"):
        artifacts["meme_results"] = {
            "artifact": "meme_results",
            "path": str(motif_dir.relative_to(ws) / "meme_out"),
            "full_path": str(meme_result["meme_out_dir"]),
            "type": "directory",
            "description": "MEME output directory (existing)"
        }
    
    meme_stats = parse_meme_xml_basic(meme_xml)
    meme_stats["meme_returncode"] = meme_result.get("returncode", 0)
    meme_stats["meme_postprocess_warning"] = bool(meme_result.get("postprocess_warning"))
    print(f"[INFO] MEME results: {meme_stats['motif_count']} motifs, "
          f"{meme_stats['total_sites']} sites")
    artifacts["meme_xml"] = {
        "artifact": "meme_xml",
        "path": str(stable_meme_xml.relative_to(ws)),
        "full_path": display_path(stable_meme_xml),
        "type": "xml",
        "size": stable_meme_xml.stat().st_size,
        "sha256": compute_file_hash(stable_meme_xml),
        "description": "Stable scoped MEME XML output used by downstream combo plotting"
    }
    artifacts["meme_txt"] = {
        "artifact": "meme_txt",
        "path": str(stable_meme_txt.relative_to(ws)),
        "full_path": display_path(stable_meme_txt),
        "type": "txt",
        "size": stable_meme_txt.stat().st_size,
        "sha256": compute_file_hash(stable_meme_txt),
        "description": "Stable scoped MEME text output"
    }
    artifacts["meme_results"] = {
        "artifact": "meme_results",
        "path": str(Path(meme_result["meme_out_dir"]).relative_to(ws)),
        "full_path": display_path(meme_result["meme_out_dir"]),
        "type": "directory",
        "description": "MEME output directory"
    }
    
    # ========== 阶段 2: 信息汇总 ==========
    print("\n[STAGE 2/4] Summarizing MEME results...")
    if not meme_result.get("existing"):
        stale_summary = motif_dir / f"{output_prefix.replace('.', '_')}.motif_prot.txt"
        if stale_summary.exists():
            stale_summary.unlink()
    
    summary_result = run_meme_summary(
        meme_txt,
        output_prefix,
        motif_dir
    )
    
    if summary_result.get("success"):
        stage_status["summary"] = True
        motif_prot_txt = Path(summary_result["output_file"])
        
        artifacts["motif_prot_txt"] = {
            "artifact": "motif_prot_txt",
            "path": str(motif_prot_txt.relative_to(ws)),
            "full_path": display_path(motif_prot_txt),
            "type": "txt",
            "size": motif_prot_txt.stat().st_size,
            "sha256": compute_file_hash(motif_prot_txt),
            "description": "MEME motif positions summary"
        }
    else:
        print(f"[WARNING] MEME summary failed: {summary_result.get('error', 'unknown')}")
    
    # ========== 阶段 3: SeqLogo 绘图 ==========
    print("\n[STAGE 3/4] Drawing motif SeqLogo plots...")
    
    if summary_result.get("success"):
        seqlogo_result = run_seqlogo_plot(
            motif_prot_txt,
            args.prefix,
            motif_dir
        )
       


        if seqlogo_result.get("success"):
            stage_status["seqlogo"] = True
            
            # SeqLogo 文件在 meme_out/ 目录
            meme_out_dir = Path(seqlogo_result["meme_out_dir"])
            
            # 复制所有 logo 文件到 final/
            logo_png_files = list(meme_out_dir.glob("logo*.png"))
            logo_eps_files = list(meme_out_dir.glob("logo*.eps"))
            
            if logo_png_files or logo_eps_files:
                # 创建目标目录
                dst_dir = final_dir / f"{output_prefix}_motif_logos"
                dst_dir.mkdir(exist_ok=True)
                
                # 复制所有 logo 文件
                for logo_file in sorted(logo_png_files):
                    dst_file = dst_dir / logo_file.name
                    shutil.copy(logo_file, dst_file)
                
                # 同时复制 EPS 版本（如果有）
                for logo_file in sorted(logo_eps_files):
                    dst_file = dst_dir / logo_file.name
                    shutil.copy(logo_file, dst_file)
                
                print(f"[INFO] Copied {len(logo_png_files) + len(logo_eps_files)} motif logos to: {dst_dir}")
                
                artifacts["motif_seqlogo_dir"] = {
                    "artifact": "motif_seqlogo",
                    "path": str(dst_dir.relative_to(ws)),
                    "full_path": display_path(dst_dir),
                    "type": "directory",
                    "description": f"Motif sequence logos ({len(logo_png_files)} PNG, {len(logo_eps_files)} EPS)"
                }
            else:
                print(f"[WARNING] No logo files found in {meme_out_dir}")
        else:
            print(f"[WARNING] SeqLogo plotting failed: {seqlogo_result.get('error', 'unknown')}")
    else:
        print("[WARNING] Skipping SeqLogo (summary failed)")
    
    # ========== 阶段 4: 树+Motif 组合图 ==========
    print("\n[STAGE 4/4] Drawing tree+motif combined plot...")
    
    tree_motif_prefix = final_dir / f"{output_prefix}.motif"
    
    tree_motif_result = run_tree_motif_plot(
        tree_file,
        subfamily_file,
        meme_xml,
        tree_motif_prefix
    )
    
    if tree_motif_result.get("success"):
        stage_status["tree_motif"] = True
        
        # 复制并重命名
        for img_file in tree_motif_result.get("output_files", []):
            src = Path(img_file)
            if src.suffix == '.pdf':
                dst = final_dir / f"{output_prefix}.motif.pdf"
            elif src.suffix == '.png':
                dst = final_dir / f"{output_prefix}.motif.png"
            else:
                continue
            
            shutil.copy(src, dst)
            
            key = f"motif_plot_{dst.suffix[1:]}"
            artifacts[key] = {
                "artifact": "motif_plot",
                "path": str(dst.relative_to(ws)),
                "full_path": display_path(dst),
                "type": "image",
                "format": dst.suffix[1:],
                "size": dst.stat().st_size,
                "sha256": compute_file_hash(dst),
                "description": f"Tree+motif combined visualization ({dst.suffix[1:].upper()})"
            }
    else:
        print(f"[WARNING] Tree+motif plot failed: {tree_motif_result.get('error', 'unknown')}")
    
    # ========== 生成报告和 Manifest ==========
    print("\n[FINAL] Generating report and manifest...")
    
    end_time = datetime.now()
    
    all_success = all(stage_status.values())
    any_success = any(stage_status.values())
    
    if all_success:
        status = "success"
    elif any_success:
        status = "partial_success"
    else:
        status = "failed"
    
    # 生成报告
    report_file = final_dir / f"{output_prefix}.motif_report.md"
    
    parameters = {
        "cpu": args.cpu,
        "prefix": args.prefix,
        "output_prefix": output_prefix,
        "stage": "motif",
        "job_id": job_id,
        "run_id": run_id,
        "protein_fasta": str(protein_fasta),
        "protein_fasta_sha256": compute_file_hash(protein_fasta),
        "meme_nmotifs": args.nmotifs,
        "meme_minw": args.minw,
        "meme_maxw": args.maxw,
        "meme_returncode": meme_result.get("returncode", 0),
        "meme_postprocess_warning": bool(meme_result.get("postprocess_warning")),
        "meme_signature_mismatch_reused": bool(meme_result.get("signature_mismatch_reused")),
        "meme_env": meme_result.get("meme_env"),
        "subfamily_file": str(subfamily_file) if subfamily_file else None,
        "tree_file": str(tree_file),
        "gene_id_file": str(gene_id_file),
        "gene_count": len(read_gene_ids(gene_id_file)),
        "protein_record_count": protein_record_count,
        "tool_versions": get_tool_versions(),
        "timestamp_start": start_time.isoformat(),
        "timestamp_end": end_time.isoformat(),
        "workspace_root": display_path(ws),
        "input_dir": str(ws / "input"),
        "motif_analysis_dir": str(motif_dir),
        "final_dir": str(final_dir),
        "stage_status": stage_status
    }
    if meme_result.get("postprocess_warning"):
        parameters["meme_stderr_summary"] = str(meme_result.get("stderr") or "")[-2000:]
    if scope_data:
        parameters["scope_file"] = str(scope_file)
        parameters["scope_id"] = scope_data.get("scope_id", "")
        parameters["scope_mode"] = scope_data.get("scope_mode", "")
        parameters["target_species"] = scope_data.get("target_species", [])
        parameters["reference_species"] = scope_data.get("reference_species", [])
        parameters["comparative_species"] = scope_data.get("comparative_species", [])
        parameters["selected_species"] = scope_data.get("selected_species", [])
    
    generate_report(output_prefix, meme_stats, parameters, report_file, artifacts, stage_status)
    
    if report_file.exists():
        artifacts["motif_report"] = {
            "artifact": "motif_report",
            "path": str(report_file.relative_to(ws)),
            "full_path": display_path(report_file),
            "type": "report",
            "format": "markdown",
            "size": report_file.stat().st_size,
            "sha256": compute_file_hash(report_file),
            "description": "Human-readable analysis report"
        }
    
    # 构建 manifest
    manifest = {
        "manifest_version": "1.0",
        "job_id": job_id,
        "stage": "motif",
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
            "--nmotifs", str(args.nmotifs),
            "--minw", str(args.minw),
            "--maxw", str(args.maxw),
            "--cpu", str(args.cpu),
            "--run-id", run_id
        ] + (["--scope-file", str(scope_file)] if scope_file else []),
        "workspace_root": display_path(workspace_root_dir(ws)),
        "workspace": display_path(ws),
        "input_dir": display_path(current_input_dir(ws)),
        "artifacts": artifacts,
        "parameters": parameters,
        "metrics": meme_stats
    }
    manifest["metrics"]["protein_record_count"] = protein_record_count
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
        stage="motif",
        prefix=args.prefix,
        scope_data=scope_data or {},
        scope_file=scope_file or "",
        output_prefix=output_prefix,
        subfamily_file=subfamily_file,
        analysis_route=args.analysis_route,
        member_set_id=args.member_set_id,
    )
    manifest["tree_source"] = str(tree_file)
    manifest["protein_fasta"] = str(protein_fasta)
    manifest["gene_id_file"] = str(gene_id_file)
    apply_stage_context(manifest, context)
    
    manifest_file = final_dir / f"{output_prefix}.motif.manifest.json"
    write_manifest(manifest, manifest_file)
    if scope_data and scope_data.get("scope_id"):
        write_legacy_manifest_copy(manifest_file, args.prefix, str(scope_data.get("scope_id") or ""), "motif")
        if status == "success":
            quarantine_stale_unscoped_failure(final_dir, args.prefix, "motif", manifest_file)
    
    # 完成
    print(f"\n{'='*50}")
    print(f"Motif stage completed with status: {status}")
    print(f"Stages: MEME={stage_status['meme']}, Summary={stage_status['summary']}, "
          f"SeqLogo={stage_status['seqlogo']}, TreeMotif={stage_status['tree_motif']}")
    print(f"{'='*50}")
    
    print("\n=== OUTPUT_FILES ===")
    for k, v in artifacts.items():
        if isinstance(v, dict) and v.get("type") == "image":
            print(f"IMAGE: {v['full_path']}")
    
    sys.exit(0 if status in ["success", "partial_success"] else 1)


if __name__ == "__main__":
    main()
