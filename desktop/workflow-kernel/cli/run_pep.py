#!/usr/bin/env python3
"""
PEP stage: parallel protein feature analysis (ExPASy ProtParam + DeepTMHMM + WoLF PSORT).

输入：
  - Identify / phylogeny scope 蛋白序列 FASTA

输出：
  - final/{prefix}.pep_properties.pdf/png
  - final/{prefix}.pep_tmhmm.pdf/png (+ optional summary figures)
  - final/{prefix}.pep_subcellular.pdf/png
  - final/{prefix}.pep_report.md
  - final/{prefix}.pep.manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import re
import subprocess
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.gene_family_scope import read_scope, sanitize_token
from scripts.pep_v2.deeptmhmm import (
    parse_gff3_tm_counts,
    parse_topology_3line,
    run_deeptmhmm,
    write_topology_tables,
)
from scripts.pep_v2.plotting import plot_deeptmhmm, plot_protparam, plot_subcellular_heatmap
from scripts.pep_v2.protparam import fetch_protparam_batch, write_protparam_tables
from scripts.pep_v2.subcellular import parse_wolfpsort_scores, run_wolfpsort, write_subcellular_tables

CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")

PLANT_MPLOC_ENABLED = False


def load_manifest(manifest_file: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def count_fasta_records(fasta_file: Path) -> int:
    count = 0
    with open(fasta_file, encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if line.startswith(">"):
                count += 1
    return count


def ensure_protein_fasta_without_stop_markers(protein_fasta: Path) -> Path:
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
    with open(protein_fasta, "r", encoding="utf-8", errors="ignore") as src, open(
        clean_fasta, "w", encoding="utf-8"
    ) as dst:
        for line in src:
            dst.write(line if line.startswith(">") else line.replace("*", ""))
    print(f"[INFO] Created stop-marker-clean FASTA for PEP: {clean_fasta}")
    return clean_fasta


def resolve_identify_protein_fasta_for_scope(workspace: Path, prefix: str, scope_data: Dict[str, Any]) -> Optional[Path]:
    scope_mode = str((scope_data or {}).get("scope_mode") or "").strip()
    if scope_mode not in {"", "target_only"}:
        return None
    final_dir = workspace / "final"
    family = str((scope_data or {}).get("family") or (prefix.split(".")[-1] if "." in prefix else prefix)).strip()
    targets = [str(x).strip() for x in (scope_data or {}).get("target_species") or [] if str(x).strip()]
    candidates = [final_dir / f"{prefix}.final_members.pep.fa"]
    if len(targets) == 1:
        species = targets[0]
        candidates = [
            workspace / "identify" / species / "final" / f"{species}.final_gfmembers.pep.fa",
            final_dir / f"Target.{species}.{family}.final_members.pep.fa",
            final_dir / f"{species}.{family}.final_members.pep.fa",
            *candidates,
        ]
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return ensure_protein_fasta_without_stop_markers(candidate)
    return None


def resolve_scope_protein_fasta(workspace: Path, prefix: str, scope_data: Dict[str, Any], cli_fasta: Optional[str] = None) -> Path:
    if cli_fasta:
        fasta_path = Path(cli_fasta)
        if fasta_path.exists() and fasta_path.stat().st_size > 0:
            return ensure_protein_fasta_without_stop_markers(fasta_path)
        raise FileNotFoundError(f"Specified protein FASTA not found: {cli_fasta}")

    final_dir = workspace / "final"
    scope_id = str(scope_data.get("scope_id") or "")
    manifest_candidates = []
    if scope_id:
        manifest_candidates.append(final_dir / f"{prefix}.{sanitize_token(scope_id)}.phylogeny.manifest.json")
    manifest_candidates.append(final_dir / f"{prefix}.phylogeny.manifest.json")

    for manifest_file in manifest_candidates:
        if not manifest_file.exists():
            continue
        manifest_data = load_manifest(manifest_file)
        if not manifest_data:
            continue
        fasta_text = manifest_data.get("parameters", {}).get("scope_input_fasta")
        if fasta_text:
            fasta_path = Path(fasta_text)
            if fasta_path.exists() and fasta_path.stat().st_size > 0:
                print(f"[INFO] Found scoped protein FASTA from phylogeny manifest: {fasta_path}")
                return ensure_protein_fasta_without_stop_markers(fasta_path)

    fallback = resolve_identify_protein_fasta_for_scope(workspace, prefix, scope_data)
    if fallback:
        print(f"[INFO] Using target-only Identify protein FASTA: {fallback}")
        return fallback

    raise FileNotFoundError(
        "Cannot find scoped protein FASTA. Run phylogeny with the same scope first, "
        "or provide --fasta explicitly."
    )


def scope_parameters(scope_file: Optional[Path], scope_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not scope_file or not scope_data:
        return {}
    return {
        "scope_file": str(scope_file),
        "scope_id": scope_data.get("scope_id", ""),
        "scope_mode": scope_data.get("scope_mode", ""),
        "target_species": scope_data.get("target_species", []),
        "reference_species": scope_data.get("reference_species", []),
        "comparative_species": scope_data.get("comparative_species", []),
        "selected_species": scope_data.get("selected_species", []),
        "family": scope_data.get("family", ""),
    }


def compute_file_hash(filepath: Path) -> str:
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception:
        return ""


def get_tool_versions() -> Dict[str, str]:
    versions: Dict[str, str] = {}
    try:
        result = subprocess.run(["R", "--version"], capture_output=True, text=True, check=False, timeout=5)
        versions["R"] = result.stdout.split("\n")[0] if result.stdout else "available"
    except Exception:
        versions["R"] = "not_found"
    try:
        biolib_bin = str(shutil.which("biolib") or Path(CONDA_BIN) / "biolib")
        result = subprocess.run([biolib_bin, "--version"], capture_output=True, text=True, check=False, timeout=5)
        versions["biolib"] = "available" if result.returncode == 0 else "not_found"
    except Exception:
        versions["biolib"] = "not_found"
    return versions


def prepare_directories(workspace: Path) -> Dict[str, Path]:
    paths = {
        "analysis_dir": workspace / "pep" / "analysis",
        "final_dir": workspace / "final",
        "logs_dir": workspace / "logs",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def parse_fasta(fasta_file: Path) -> Dict[str, str]:
    sequences: Dict[str, str] = {}
    current_id = None
    current_seq: List[str] = []
    with open(fasta_file, "r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if line.startswith(">"):
                if current_id:
                    sequences[current_id] = "".join(current_seq)
                current_id = line[1:].split()[0]
                current_seq = []
            else:
                current_seq.append(line)
        if current_id:
            sequences[current_id] = "".join(current_seq)
    return sequences


def external_tool_timeout(
    fasta_file: Path,
    seconds_per_sequence: int = 90,
    minimum: int = 300,
    maximum: int = 7200,
) -> int:
    try:
        count = sum(1 for line in open(fasta_file, "r", encoding="utf-8", errors="ignore") if line.startswith(">"))
    except Exception:
        count = 1
    return max(minimum, min(maximum, count * seconds_per_sequence))


def write_clean_fasta(fasta_file: Path, output_file: Path) -> Path:
    """Write DeepTMHMM-friendly FASTA with short IDs and canonical amino acids."""
    allowed = set("ACDEFGHIKLMNPQRSTVWY")
    sequences = parse_fasta(fasta_file)
    with open(output_file, "w", encoding="utf-8") as handle:
        for seq_id, seq in sequences.items():
            clean = "".join(aa for aa in seq.upper() if aa in allowed)
            handle.write(f">{seq_id}\n{clean}\n")
    return output_file


def task_protparam(
    sequences: Dict[str, str],
    analysis_dir: Path,
    final_dir: Path,
    prefix: str,
) -> Dict[str, Any]:
    name = "protparam"
    try:
        print(f"[{name}] ExPASy ProtParam batch ({len(sequences)} sequences)...")
        raw_dir = analysis_dir / "protparam"
        results = fetch_protparam_batch(sequences, raw_dir, delay_sec=1.0)
        ok = [row for row in results if row.get("status") == "success"]
        if not ok:
            return {"name": name, "success": False, "error": "No successful ProtParam responses"}
        full_tsv = write_protparam_tables(results, analysis_dir, final_dir, prefix=prefix)
        plot_pdf = final_dir / f"{prefix}.pep_properties.pdf"
        plot_png = final_dir / f"{prefix}.pep_properties.png"
        plot_protparam(final_dir / "properties_data.tsv", plot_pdf, plot_png, analysis_dir)
        print(f"[{name}] Done: {len(ok)}/{len(results)} sequences")
        return {
            "name": name,
            "success": True,
            "method": "ExPASy ProtParam web POST",
            "source_url": "https://web.expasy.org/protparam/",
            "success_count": len(ok),
            "failed_count": len(results) - len(ok),
            "full_tsv": str(full_tsv),
            "plot_pdf": str(plot_pdf),
            "plot_png": str(plot_png),
        }
    except Exception as exc:
        print(f"[{name}] Failed: {exc}")
        traceback.print_exc()
        return {"name": name, "success": False, "error": str(exc)}


def task_deeptmhmm(fasta_file: Path, analysis_dir: Path, final_dir: Path, prefix: str) -> Dict[str, Any]:
    name = "deeptmhmm"
    try:
        print(f"[{name}] DeepTMHMM...")
        result = run_deeptmhmm(fasta_file, analysis_dir)
        if not result.get("success"):
            return {"name": name, "success": False, "error": str(result.get("stderr") or "DeepTMHMM failed")}
        topology_file = Path(result["topologies"])
        gff3 = analysis_dir / "deeptmhmm_results.gff3"
        if not gff3.exists():
            gff3 = analysis_dir / "TMRs.gff3"
        records = parse_topology_3line(topology_file)
        gff_counts = parse_gff3_tm_counts(gff3)
        segments_tsv, summary_tsv = write_topology_tables(records, gff_counts, analysis_dir)
        strip_pdf = final_dir / f"{prefix}.pep_tmhmm.pdf"
        strip_png = final_dir / f"{prefix}.pep_tmhmm.png"
        summary_pdf = final_dir / f"{prefix}.pep_tmhmm_summary.pdf"
        summary_png = final_dir / f"{prefix}.pep_tmhmm_summary.png"
        plot_deeptmhmm(segments_tsv, summary_tsv, strip_pdf, strip_png, summary_pdf, summary_png, analysis_dir)
        tm_n = sum(1 for record in records if gff_counts.get(record.protein_id, record.tm_count) > 0)
        print(f"[{name}] Done: {len(records)} proteins, TM={tm_n}, GLOB={len(records) - tm_n}")
        return {
            "name": name,
            "success": True,
            "method": result.get("method"),
            "protein_count": len(records),
            "tm_count": tm_n,
            "glob_count": len(records) - tm_n,
            "strip_pdf": str(strip_pdf),
            "strip_png": str(strip_png),
            "summary_pdf": str(summary_pdf),
            "summary_png": str(summary_png),
        }
    except Exception as exc:
        print(f"[{name}] Failed: {exc}")
        traceback.print_exc()
        return {"name": name, "success": False, "error": str(exc)}


def task_subcellular(fasta_file: Path, analysis_dir: Path, final_dir: Path, prefix: str) -> Dict[str, Any]:
    name = "subcellular"
    try:
        print(f"[{name}] WoLF PSORT...")
        wolf_file = analysis_dir / f"{prefix}_wolfpsort.txt"
        result = run_wolfpsort(fasta_file, wolf_file)
        if not result.get("success"):
            return {"name": name, "success": False, "error": str(result.get("stderr") or "WoLF PSORT failed")}
        scores, primary = parse_wolfpsort_scores(wolf_file)
        if not scores:
            return {"name": name, "success": False, "error": "WoLF PSORT output parsed empty"}
        scores_tsv = write_subcellular_tables(scores, primary, analysis_dir, final_dir)
        heatmap_pdf = final_dir / f"{prefix}.pep_subcellular.pdf"
        heatmap_png = final_dir / f"{prefix}.pep_subcellular.png"
        plot_subcellular_heatmap(scores_tsv, heatmap_pdf, heatmap_png, analysis_dir)
        counts: Dict[str, int] = {}
        for loc in primary.values():
            counts[loc] = counts.get(loc, 0) + 1
        print(f"[{name}] Done: {len(scores)} proteins")
        return {
            "name": name,
            "success": True,
            "method": result.get("method"),
            "protein_count": len(scores),
            "subcellular_counts": counts,
            "heatmap_pdf": str(heatmap_pdf),
            "heatmap_png": str(heatmap_png),
            "scores_tsv": str(scores_tsv),
        }
    except Exception as exc:
        print(f"[{name}] Failed: {exc}")
        traceback.print_exc()
        return {"name": name, "success": False, "error": str(exc)}


def write_report(
    workspace: Path,
    prefix: str,
    task_results: List[Dict[str, Any]],
    metrics: Dict[str, Any],
    parameters: Dict[str, Any],
    tool_versions: Dict[str, str],
) -> Path:
    report_path = workspace / "final" / f"{prefix}.pep_report.md"
    lines = [
        f"# 蛋白特征分析报告 - {prefix}",
        f"**生成时间**: {datetime.now().isoformat()}",
        "",
        "## 1. 分析概述",
        "- **分析阶段**: PEP (Protein Features)",
        "- **执行方式**: 并行任务（ProtParam / DeepTMHMM / WoLF PSORT），单任务失败不影响其余任务",
        "- **理化性质**: ExPASy ProtParam 官方网页接口",
        "- **跨膜拓扑**: DeepTMHMM (DTU Health Tech web / BioLib fallback)",
        "- **亚细胞定位**: WoLF PSORT (Plant, wolfpsort.hgc.jp)",
        f"- **Plant-mPLoc**: {'enabled' if PLANT_MPLOC_ENABLED else 'disabled (default)'}",
        "",
    ]
    if metrics.get("protein_count"):
        lines.append(f"- **蛋白数量**: {metrics['protein_count']}")
        lines.append("")

    lines.extend(
        [
            "## 2. 任务状态",
            "| Task | Status | Method | Notes |",
            "|---|---|---|---|",
        ]
    )
    for row in task_results:
        status = "success" if row.get("success") else "failed"
        note = "" if row.get("success") else str(row.get("error", ""))[:120]
        lines.append(f"| {row.get('name')} | {status} | {row.get('method', '')} | {note} |")
    lines.append("")

    subcellular_data = workspace / "final" / "subcellular_data.tsv"
    if subcellular_data.exists() and subcellular_data.stat().st_size > 0:
        lines.extend(["## 3. 亚细胞定位数量", "| Location | Count | Source |", "|---|---:|---|"])
        with open(subcellular_data, "r", encoding="utf-8", errors="ignore") as handle:
            header = handle.readline().strip().split("\t")
            for line in handle:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 2:
                    continue
                row = dict(zip(header, parts))
                lines.append(
                    f"| {row.get('location', '')} | {row.get('count', '')} | {row.get('source', '')} |"
                )
        lines.append("")

    lines.extend(["## 4. 工具版本"])
    for tool, version in tool_versions.items():
        lines.append(f"- **{tool}**: {version}")
    lines.extend(
        [
            "",
            "## 5. 输出文件",
            f"- `{prefix}.pep_properties.pdf/png` - 理化性质分布图 (ExPASy ProtParam)",
            f"- `{prefix}.pep_tmhmm.pdf/png` - DeepTMHMM 拓扑条带图",
            f"- `{prefix}.pep_tmhmm_summary.pdf/png` - DeepTMHMM 汇总图",
            f"- `{prefix}.pep_subcellular.pdf/png` - WoLF PSORT 热图",
            f"- `pep/analysis/{prefix}_properties.tsv` - 理化性质完整表",
            f"- `pep/analysis/protparam/` - ExPASy 原始 HTML",
            "",
            "## Parameters",
            "```json",
            json.dumps(parameters, indent=2, ensure_ascii=False),
            "```",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def write_manifest(
    workspace: Path,
    job_id: str,
    run_id: str,
    prefix: str,
    start_time: datetime,
    end_time: datetime,
    artifacts: Dict,
    metrics: Dict,
    parameters: Dict,
    tool_versions: Dict,
) -> Path:
    elapsed_sec = (end_time - start_time).total_seconds()
    manifest_data = {
        "manifest_version": "1.0",
        "job_id": job_id,
        "stage": "pep",
        "run_id": run_id,
        "prefix": prefix,
        "timestamp": end_time.isoformat(),
        "status": "success" if metrics.get("tasks_success", 0) > 0 else "failed",
        "elapsed_sec": round(elapsed_sec, 2),
        "tool_versions": tool_versions,
        "artifacts": artifacts,
        "parameters": parameters,
        "metrics": metrics,
    }
    final_dir = workspace / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
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

    manifest_path = final_dir / f"{prefix}.pep.manifest.json"
    manifest_path.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")

    if parameters.get("scope_id"):
        scoped_manifest = final_dir / f"{prefix}.{sanitize_token(str(parameters['scope_id']))}.pep.manifest.json"
        manifest_data["scope_manifest"] = str(scoped_manifest.resolve())
        scoped_manifest.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")

    return manifest_path


def collect_artifacts(task_results: List[Dict[str, Any]], prefix: str) -> Dict[str, Any]:
    artifacts: Dict[str, Any] = {}
    for result in task_results:
        if not result.get("success"):
            continue
        name = result.get("name")
        if name == "protparam":
            artifacts["properties_pdf"] = {"path": f"final/{prefix}.pep_properties.pdf"}
            artifacts["properties_png"] = {"path": f"final/{prefix}.pep_properties.png"}
            artifacts["properties_tsv"] = {"path": f"pep/analysis/{prefix}_properties.tsv"}
            artifacts["protparam_full_tsv"] = {"path": "pep/analysis/protparam_full.tsv"}
        elif name == "deeptmhmm":
            artifacts["tmhmm_pdf"] = {"path": f"final/{prefix}.pep_tmhmm.pdf"}
            artifacts["tmhmm_png"] = {"path": f"final/{prefix}.pep_tmhmm.png"}
            artifacts["tmhmm_summary_pdf"] = {"path": f"final/{prefix}.pep_tmhmm_summary.pdf"}
            artifacts["tmhmm_summary_png"] = {"path": f"final/{prefix}.pep_tmhmm_summary.png"}
            artifacts["topologies_3line"] = {"path": "pep/analysis/predicted_topologies.3line"}
        elif name == "subcellular":
            artifacts["subcellular_pdf"] = {"path": f"final/{prefix}.pep_subcellular.pdf"}
            artifacts["subcellular_png"] = {"path": f"final/{prefix}.pep_subcellular.png"}
            artifacts["subcellular_data"] = {"path": "final/subcellular_data.tsv"}
            artifacts["subcellular_scores"] = {"path": "pep/analysis/subcellular_scores.tsv"}
    return artifacts


def main() -> int:
    parser = argparse.ArgumentParser(description="蛋白特征分析 (parallel PEP)")
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--input-dir")
    parser.add_argument("--fasta", help="蛋白FASTA文件；用于显式指定PEP输入")
    parser.add_argument("--scope-file", help="target/reference/comparative scope JSON")
    parser.add_argument("--cpu", type=int, default=8)
    parser.add_argument("--log-file", help="日志文件路径（用于前端兼容）")
    args = parser.parse_args()

    start_time = datetime.now()
    run_id = f"pep_{start_time.strftime('%Y%m%d_%H%M%S')}"
    workspace = Path(args.workspace)
    job_id = workspace.name

    print("=" * 60)
    print("蛋白特征分析 (parallel PEP)")
    print("=" * 60)

    try:
        scope_file = Path(args.scope_file) if args.scope_file else None
        scope_data = None
        if scope_file:
            if not scope_file.exists():
                raise FileNotFoundError(f"Scope file not found: {scope_file}")
            scope_data = read_scope(scope_file)

        paths = prepare_directories(workspace)
        tool_versions = get_tool_versions()

        if scope_data:
            protein_file = resolve_scope_protein_fasta(workspace, args.prefix, scope_data, args.fasta)
        elif args.fasta:
            protein_file = Path(args.fasta)
            if not protein_file.exists() or protein_file.stat().st_size == 0:
                print(f"找不到蛋白文件: {protein_file}")
                return 1
            protein_file = ensure_protein_fasta_without_stop_markers(protein_file)
        else:
            protein_file = workspace / "final" / f"{args.prefix}.final_members.pep.fa"
            if not protein_file.exists() or protein_file.stat().st_size == 0:
                print(f"找不到蛋白文件: {protein_file}")
                return 1
            protein_file = ensure_protein_fasta_without_stop_markers(protein_file)

        sequences = parse_fasta(protein_file)
        print(f"蛋白文件: {protein_file}")
        print(f"包含 {len(sequences)} 个序列")

        task_results: List[Dict[str, Any]] = []
        metrics: Dict[str, Any] = {
            "protein_count": len(sequences),
            "subcellular_method": "WoLF PSORT",
            "plant_mploc_enabled": PLANT_MPLOC_ENABLED,
        }

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(
                    task_protparam, sequences, paths["analysis_dir"], paths["final_dir"], args.prefix
                ): "protparam",
                executor.submit(
                    task_deeptmhmm, protein_file, paths["analysis_dir"], paths["final_dir"], args.prefix
                ): "deeptmhmm",
                executor.submit(
                    task_subcellular, protein_file, paths["analysis_dir"], paths["final_dir"], args.prefix
                ): "subcellular",
            }
            for future in as_completed(futures):
                result = future.result()
                task_results.append(result)
                name = result.get("name")
                metrics[f"{name}_success"] = bool(result.get("success"))
                if not result.get("success"):
                    metrics[f"{name}_error"] = str(result.get("error", ""))[:500]
                    print(f"[WARN] Task '{name}' failed; other tasks continue.")
                elif name == "subcellular" and result.get("subcellular_counts"):
                    metrics["subcellular_counts"] = result["subcellular_counts"]
                    metrics["subcellular_source"] = "WoLF PSORT"
                    metrics["subcellular_prediction_count"] = result.get("protein_count", 0)

        success_count = sum(1 for row in task_results if row.get("success"))
        metrics["tasks_success"] = success_count
        metrics["tasks_total"] = len(task_results)

        parameters = {
            "prefix": args.prefix,
            "cpu": args.cpu,
            "protein_fasta": str(protein_file),
            "protein_count": count_fasta_records(protein_file),
            "runner": "run_pep.py",
        }
        parameters.update(scope_parameters(scope_file, scope_data))

        end_time = datetime.now()
        artifacts = collect_artifacts(task_results, args.prefix)
        report_path = write_report(
            workspace, args.prefix, task_results, metrics, parameters, tool_versions
        )
        artifacts["report"] = {"path": f"final/{args.prefix}.pep_report.md"}

        manifest_path = write_manifest(
            workspace,
            job_id,
            run_id,
            args.prefix,
            start_time,
            end_time,
            artifacts,
            metrics,
            parameters,
            tool_versions,
        )

        print("\n" + "=" * 60)
        print(f"蛋白特征分析完成: {success_count}/{len(task_results)} tasks succeeded")
        print(f"Report: {report_path}")
        print(f"Manifest: {manifest_path}")
        print("=" * 60)
        return 0 if success_count > 0 else 1

    except Exception as exc:
        print(f"\n失败: {exc}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
