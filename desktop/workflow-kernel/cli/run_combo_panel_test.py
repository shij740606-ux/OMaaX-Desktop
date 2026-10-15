#!/usr/bin/env python3
"""
Combo Plot Stage - Tree + Motif + Structure + Domain 组合图

将多个分析阶段的结果整合到一张图中：
- Panel A: Phylogenetic tree
- Panel B: Subfamily
- Panel C: Motif
- Panel D: Gene structure
- Panel E: Domain
"""

import os
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.gene_family_scope import read_scope, sanitize_token, scope_hash
from gfa_core.gene_family_stage_context import build_stage_context, user_output_prefix_for, write_legacy_manifest_copy

# 镜像标准形态: 当前解释器即环境解释器; Rscript 由 PATH 查找。
RSCRIPT_PATH = str(shutil.which("Rscript") or "Rscript")
CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
def find_file_by_glob(pattern: Path, name: str) -> Optional[Path]:
    """递归查找文件"""
    matches = list(pattern.rglob(name))
    return matches[0] if matches else None


def find_meme_xml(workspace: Path, prefix: str) -> Optional[Path]:
    """查找 MEME XML 文件"""
    # 优先在 motif/analysis/meme_out/ 中查找
    meme_dir = workspace / "motif" / "analysis" / "meme_out"
    if meme_dir.exists():
        xml_file = meme_dir / "meme.xml"
        if xml_file.exists():
            return xml_file
    
    # 备选：递归搜索
    return find_file_by_glob(workspace, "meme.xml")


def resolve_meme_xml_from_manifest(workspace: Path, motif_data: Dict, prefix: str) -> Optional[Path]:
    artifacts = motif_data.get("artifacts", {}) or {}
    meme_xml = artifacts.get("meme_xml") if isinstance(artifacts, dict) else None
    candidates = []
    if isinstance(meme_xml, dict):
        for key in ("full_path", "path"):
            text = str(meme_xml.get(key) or "").strip()
            if text:
                path = Path(text)
                candidates.append(path if path.is_absolute() else workspace / path)

    meme_results = artifacts.get("meme_results") if isinstance(artifacts, dict) else None
    if isinstance(meme_results, dict):
        for key in ("full_path", "path"):
            text = str(meme_results.get(key) or "").strip()
            if text:
                path = Path(text)
                path = path if path.is_absolute() else workspace / path
                candidates.append(path / "meme.xml")

    motif_txt = artifacts.get("motif_prot_txt") if isinstance(artifacts, dict) else None
    if isinstance(motif_txt, dict):
        text = str(motif_txt.get("full_path") or motif_txt.get("path") or "").strip()
        if text:
            path = Path(text)
            path = path if path.is_absolute() else workspace / path
            candidates.append(path.parent / "meme_out" / "meme.xml")

    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate.resolve()
    return find_meme_xml(workspace, prefix)


def load_manifest(manifest_path: Path) -> Dict:
    """加载 manifest 文件"""
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def manifest_artifact_path(workspace: Path, manifest_path: Path, artifact: Dict) -> Optional[Path]:
    if not isinstance(artifact, dict):
        return None
    raw = str(artifact.get("full_path") or artifact.get("path") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return path
    candidate = workspace / path
    if candidate.exists():
        return candidate
    return manifest_path.parent / path.name


def materialize_aggregate_subfamily_file(workspace: Path, prefix: str, scope_data: Optional[Dict]) -> Optional[Path]:
    """Build a combined subfamily table from per-target reference-classification manifests."""
    scope_id = str((scope_data or {}).get("scope_id") or "").strip()
    if not scope_id:
        return None
    final_dir = workspace / "final"
    aggregate_manifest = final_dir / f"{prefix}.{sanitize_token(scope_id)}.reference_classification.manifest.json"
    if not aggregate_manifest.exists():
        return None
    try:
        aggregate = load_manifest(aggregate_manifest)
    except Exception:
        return None
    rows = []
    for artifact in (aggregate.get("artifacts") or {}).values():
        child_manifest = manifest_artifact_path(workspace, aggregate_manifest, artifact)
        if not child_manifest or not child_manifest.exists() or not child_manifest.name.endswith(".manifest.json"):
            continue
        try:
            child = load_manifest(child_manifest)
        except Exception:
            continue
        child_artifacts = child.get("artifacts") or {}
        sub_artifact = child_artifacts.get("subfamily_file") or child_artifacts.get("geneID_subfamily_compat")
        sub_path = manifest_artifact_path(workspace, child_manifest, sub_artifact)
        if not sub_path or not sub_path.exists():
            continue
        for raw in sub_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.lower().startswith("gene"):
                continue
            rows.append(line)
    if not rows:
        return None
    out = final_dir / f"{prefix}.{sanitize_token(scope_id)}.reference_classification.subfamily.tsv"
    seen = set()
    with out.open("w", encoding="utf-8") as handle:
        for line in rows:
            key = line.split("\t", 1)[0] if "\t" in line else line.split(None, 1)[0]
            if key in seen:
                continue
            seen.add(key)
            handle.write(line + "\n")
    print(f"[INFO] Materialized aggregate subfamily file: {out}")
    return out


def find_latest_manifest(workspace: Path, stage: str, prefix: str, scope_data: Optional[Dict] = None) -> Optional[Path]:
    """查找最新的 manifest 文件"""
    final_dir = workspace / "final"
    if not final_dir.exists():
        return None

    scope_id = str((scope_data or {}).get("scope_id") or "").strip()
    if scope_data and scope_data.get("scope_id"):
        scoped = final_dir / f"{prefix}.{sanitize_token(scope_id)}.{stage}.manifest.json"
        if scoped.exists():
            return scoped
        compact = final_dir / f"{compact_output_prefix(prefix, scope_data)}.{stage}.manifest.json"
        if compact.exists():
            return compact
    
    pattern = f"*{stage}*.manifest.json"
    candidates = list(final_dir.glob(pattern))
    if scope_id:
        for path in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True):
            if scope_id in path.name:
                return path
        for path in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("stage") == stage and data.get("scope_id") == scope_id:
                return path
    
    # 按修改时间排序，返回最新的
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    return None


def compact_output_prefix(prefix: str, scope_data: Optional[Dict]) -> str:
    """Return a bounded user-facing output prefix.

    Full scope details stay in scope JSON and manifest.  Result filenames should
    not enumerate every target/reference/comparative species.
    """
    if not scope_data:
        return prefix
    family = str(scope_data.get("family") or (prefix.split(".")[-1] if "." in prefix else "")).strip()
    mode = str(scope_data.get("scope_mode") or "").strip()
    targets = [str(x) for x in scope_data.get("target_species") or [] if str(x).strip()]
    scope_id = str(scope_data.get("scope_id") or "").strip()
    if len(targets) == 1 and family:
        return ".".join(x for x in [targets[0], family, mode] if x)
    if mode:
        base = prefix if prefix else f"Target.{family}".strip(".")
        if scope_id:
            return f"{base}.{mode}.h{scope_hash(scope_id, 6)}"
        return f"{base}.{mode}"
    return prefix


def normalize_panels(raw_panels: str) -> List[str]:
    """Normalize the panel selector used by the isolated combo replot test."""
    allowed = ["motif", "structure", "domain"]
    aliases = {
        "gene_structure": "structure",
        "gene-structure": "structure",
        "genestructure": "structure",
        "domains": "domain",
        "motifs": "motif",
        "all": "all",
    }
    selected: List[str] = []
    for token in str(raw_panels or "").replace(";", ",").split(","):
        key = token.strip().lower().replace(" ", "_")
        if not key:
            continue
        key = aliases.get(key, key)
        if key == "all":
            return allowed
        if key not in allowed:
            raise ValueError(f"Unsupported combo panel: {token}. Allowed: {', '.join(allowed)}")
        if key not in selected:
            selected.append(key)
    selected = selected or allowed
    if len(selected) < 2:
        raise ValueError("Combo panel test requires at least two data panels; single-panel views already exist in motif/structure/domain stages.")
    return selected


def run_combo_plot(
    workspace: Path,
    prefix: str,
    output_prefix: str,
    tree_file: Path,
    subfamily_file: Optional[Path],
    meme_xml: Optional[Path],
    gff3_file: Optional[Path],
    domtblout_file: Optional[Path],
    panels: List[str],
    evalue_cutoff: float = 1e-5,
    coverage_percent: Optional[float] = None,
    rscript: Optional[Path] = None,
) -> Dict:
    """
    运行 R 脚本绘制组合图
    """
    if rscript is None:
        rscript = SCRIPTS_DIR / "combo_tree_motif_structure_domain_panel_test.R"
    
    if not rscript.exists():
        return {
            "success": False,
            "error": f"R script not found: {rscript}"
        }
    
    # 输出文件
    output_pdf = workspace / "final" / f"{output_prefix}.combo_panel_test.pdf"
    
    # 构建命令
    cmd = [
        str(RSCRIPT_PATH), str(rscript),
        str(tree_file),
        str(subfamily_file) if subfamily_file else "NONE",  # 传递 NONE 表示无亚家族
        str(meme_xml) if meme_xml else "NONE",
        str(gff3_file) if gff3_file else "NONE",
        str(domtblout_file) if domtblout_file else "NONE",
        str(evalue_cutoff),  # E-value cutoff
    ]
    
    # 添加 coverage（可选）
    if coverage_percent is not None:
        cmd.append(str(coverage_percent))
    else:
        cmd.append("0")  # 不启用 coverage 过滤
    
    # 添加输出文件和 panel 选择
    cmd.append(str(output_pdf))
    cmd.append(",".join(panels))
    
    print(f"[CMD] {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=300
        )
        
        success = output_pdf.exists()
        
        return {
            "success": success,
            "output_pdf": str(output_pdf) if success else None,
            "output_png": str(output_pdf.with_suffix(".png")) if success else None,
            "returncode": result.returncode,
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-2000:] if result.stderr else "",
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


def promote_scoped_combo_outputs(workspace: Path, prefix: str, scope_id: str, result: Dict) -> Dict:
    """Materialize scoped combo outputs and make them the manifest artifacts.

    The R plotting command writes the short user-facing filename first for
    backwards compatibility. When a scope is active, the manifest should point
    at the scoped copies so strict artifact validation follows the same identity
    as the stage manifest.
    """
    if not scope_id or not result.get("success"):
        return result
    scoped_prefix = f"{prefix}.{sanitize_token(scope_id)}"
    final_dir = workspace / "final"
    promoted = dict(result)
    for key, suffix in (("output_pdf", ".combo_panel_test.pdf"), ("output_png", ".combo_panel_test.png")):
        source_text = promoted.get(key)
        if not source_text:
            continue
        source = Path(source_text)
        if not source.exists():
            continue
        target = final_dir / f"{scoped_prefix}{suffix}"
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        promoted[f"short_{key}"] = str(source)
        promoted[key] = str(target)
    return promoted


def main():
    parser = argparse.ArgumentParser(
        description="Combo Plot: Tree + Motif + Structure + Domain"
    )
    parser.add_argument("--prefix", required=True, help="Analysis prefix (e.g., Ft.SPL)")
    parser.add_argument("--workspace", required=True, help="Workspace directory")
    parser.add_argument("--evalue", type=float, default=1e-5, help="E-value cutoff")
    parser.add_argument("--coverage", type=float, default=None, help="Coverage percent (0-100)")
    parser.add_argument("--subfamily", type=str, default=None, help="Subfamily file path")
    parser.add_argument("--scope-file", type=str, default=None, help="Gene-family scope JSON from resolve_scope.py")
    parser.add_argument("--analysis-route", type=str, default="", help="Analysis route identity, e.g. single_species/subfamily_prediction")
    parser.add_argument("--member-set-id", type=str, default="", help="Member set identity for scoped outputs")
    parser.add_argument("--cpu", type=int, default=8, help="CPU cores (unused, for compatibility)")
    parser.add_argument("--run-id", type=str, default=None, help="Run ID")
    parser.add_argument("--replot", action="store_true", help="Replot even if exists")
    parser.add_argument("--panels", type=str, default="motif,structure,domain", help="Comma-separated panels to show; choose at least two from motif,structure,domain")
    
    args = parser.parse_args()
    
    workspace = Path(args.workspace)
    prefix = args.prefix
    try:
        panels = normalize_panels(args.panels)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)
    scope_data: Optional[Dict] = None
    scope_file: Optional[Path] = None
    if args.scope_file:
        scope_file = Path(args.scope_file)
        if not scope_file.exists():
            print(f"[ERROR] Scope file not found: {args.scope_file}")
            sys.exit(1)
        scope_data = read_scope(scope_file)
    
    print(f"[INFO] Combo panel test stage for {prefix}")
    print(f"[INFO] Workspace: {workspace}")
    print(f"[INFO] Panels: {', '.join(panels)}")
    scope_id = str((scope_data or {}).get("scope_id") or "").strip()
    output_prefix = user_output_prefix_for(
        "combo",
        prefix,
        scope_data or {},
        analysis_route=args.analysis_route,
        member_set_id=args.member_set_id,
    ) if scope_id else prefix
    if output_prefix != prefix:
        print(f"[INFO] Output prefix: {output_prefix}")
    
    # 1. 查找 tree 文件
    phylo_manifest = find_latest_manifest(workspace, "phylogeny", prefix, scope_data)
    if not phylo_manifest:
        print("[ERROR] Phylogeny manifest not found")
        sys.exit(1)
    
    phylo_data = load_manifest(phylo_manifest)
    tree_file = Path(phylo_data["artifacts"]["tree"]["full_path"])
    print(f"[INFO] Tree file: {tree_file}")
    
    # 2. 查找 subfamily 文件
    if args.subfamily:
        subfamily_file = Path(args.subfamily)
    else:
        # 自动查找只接受 confirmed_subfamily；prediction/geneID 必须由 --subfamily 显式传入。
        confirmed_name = f"{prefix}.confirmed_subfamily.tsv"
        scoped_confirmed_name = f"{output_prefix}.confirmed_subfamily.tsv"
        search_paths = [
            workspace / "final" / scoped_confirmed_name,
            workspace / "final" / confirmed_name,
            workspace / "input" / confirmed_name,
            workspace.parent.parent / "input" / workspace.name / confirmed_name,
        ]
        if scope_data and scope_data.get("scope_mode") in {"target_only", "target_plus_reference"}:
            family = str(scope_data.get("family") or (prefix.split(".")[-1] if "." in prefix else "")).strip()
            targets = [str(x) for x in scope_data.get("target_species") or [] if str(x).strip()]
            refs = [str(x) for x in scope_data.get("reference_species") or [] if str(x).strip()]
            if len(targets) == 1 and family:
                cross_prefix = f"{targets[0]}.{family}.target_plus_reference"
                scoped_prediction = None
                if refs:
                    scoped_prediction = f"Target.{family}.target-{targets[0]}.ref-{'-'.join(refs)}.subfamily_prediction"
                extra_paths = [
                    workspace / "final" / f"{cross_prefix}.confirmed_subfamily.tsv",
                ]
                if scoped_prediction:
                    extra_paths.extend([
                        workspace / "final" / f"{scoped_prediction}.confirmed_subfamily.tsv",
                    ])
                search_paths[0:0] = extra_paths
        subfamily_file = None
        for p in search_paths:
            if p.exists():
                subfamily_file = p
                break
        # Do not materialize aggregate prediction labels during automatic lookup.
        # Unconfirmed prediction tables require an explicit --subfamily path.
    
    if not subfamily_file or not subfamily_file.exists():
        print("[INFO] Subfamily file not found, will run without subfamily coloring")
        subfamily_file = None
    else:
        print(f"[INFO] Subfamily file: {subfamily_file}")
    
    # 3. 查找 panel 需要的输入文件
    meme_xml: Optional[Path] = None
    if "motif" in panels:
        motif_manifest = find_latest_manifest(workspace, "motif", prefix, scope_data)
        if not motif_manifest:
            print("[ERROR] Motif manifest not found")
            sys.exit(1)
        motif_data = load_manifest(motif_manifest)
        meme_xml = resolve_meme_xml_from_manifest(workspace, motif_data, prefix)
        if not meme_xml or not meme_xml.exists():
            print("[ERROR] MEME XML not found")
            sys.exit(1)
        print(f"[INFO] MEME XML: {meme_xml}")

    gff3_file: Optional[Path] = None
    if "structure" in panels:
        struct_manifest = find_latest_manifest(workspace, "structure", prefix, scope_data)
        if not struct_manifest:
            print("[ERROR] Structure manifest not found")
            sys.exit(1)
        struct_data = load_manifest(struct_manifest)
        gff3_file = Path(struct_data["artifacts"]["structure_gff3_filtered"]["full_path"])
        print(f"[INFO] GFF3 file: {gff3_file}")

    domtblout_file: Optional[Path] = None
    if "domain" in panels:
        domain_manifest = find_latest_manifest(workspace, "domain", prefix, scope_data)
        if not domain_manifest:
            print("[ERROR] Domain manifest not found")
            sys.exit(1)
        domain_data = load_manifest(domain_manifest)
        domtblout_file = Path(domain_data["artifacts"]["domtblout"]["full_path"])
        print(f"[INFO] domtblout file: {domtblout_file}")
    
    # 6. 运行组合图
    result = run_combo_plot(
        workspace=workspace,
        prefix=prefix,
        output_prefix=output_prefix,
        tree_file=tree_file,
        subfamily_file=subfamily_file,
        meme_xml=meme_xml,
        gff3_file=gff3_file,
        domtblout_file=domtblout_file,
        panels=panels,
        evalue_cutoff=args.evalue,
        coverage_percent=args.coverage,
    )
    result = promote_scoped_combo_outputs(workspace, prefix, scope_id, result)
    
    if result.get("success"):
        print(f"[OK] Combo plot generated: {result['output_pdf']}")
        
        # 检查 PNG 是否存在
        png_path = result.get("output_png")
        png_exists = png_path and Path(png_path).exists() if png_path else False
        
        # 生成 manifest
        artifacts = {
            "combo_plot_pdf": {
                "artifact": "combo_plot",
                "path": str(Path(result["output_pdf"]).relative_to(workspace)),
                "full_path": result["output_pdf"],
                "type": "image",
                "format": "pdf",
                "description": f"Tree + {' + '.join(p.title() for p in panels)} combo panel test visualization"
            }
        }
        
        if png_exists:
            artifacts["combo_plot_png"] = {
                "artifact": "combo_plot",
                "path": str(Path(png_path).relative_to(workspace)),
                "full_path": png_path,
                "type": "image",
                "format": "png",
                "description": f"Tree + {' + '.join(p.title() for p in panels)} combo panel test visualization (PNG)"
            }
        
        stage_context = build_stage_context(
            stage="combo",
            prefix=prefix,
            scope_data=scope_data or {},
            scope_file=str(scope_file or ""),
            output_prefix=output_prefix,
            subfamily_file=str(subfamily_file or ""),
            analysis_route=args.analysis_route or "",
            member_set_id=args.member_set_id or "",
        ).to_dict()
        manifest = {
            "manifest_version": "1.0",
            "job_id": workspace.name,
            "stage": "combo",
            "run_id": args.run_id or "unknown",
            "prefix": prefix,
            "output_prefix": output_prefix,
            "status": "success",
            "analysis_route": stage_context.get("analysis_route", ""),
            "result_group": stage_context.get("result_group", ""),
            "member_set_id": stage_context.get("member_set_id", ""),
            "stage_context": stage_context,
            "artifacts": artifacts,
            "parameters": {
                "evalue": args.evalue,
                "coverage": args.coverage,
                "output_prefix": output_prefix,
                "subfamily_file": str(subfamily_file),
                "tree_file": str(tree_file),
                "meme_xml": str(meme_xml or ""),
                "gff3_file": str(gff3_file or ""),
                "domtblout_file": str(domtblout_file or ""),
                "panels": panels,
                "analysis_route": stage_context.get("analysis_route", ""),
                "member_set_id": stage_context.get("member_set_id", ""),
            }
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
        
        manifest_path = workspace / "final" / f"{output_prefix}.combo_panel_test.manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
        print(f"[INFO] Manifest: {manifest_path}")
        if scope_data and scope_data.get("scope_id"):
            scoped_manifest = write_legacy_manifest_copy(manifest_path, prefix, str(scope_data.get("scope_id") or ""), "combo_panel_test")
            if scoped_manifest:
                print(f"[INFO] Legacy scoped manifest: {scoped_manifest}")
        
    else:
        print(f"[ERROR] Combo plot failed: {result.get('error', result.get('stderr', 'unknown'))}")
        if result.get("stdout"):
            print(result["stdout"])
        sys.exit(1)


if __name__ == "__main__":
    main()
