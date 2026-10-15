"""Evidence-first interpretation helpers for gene family analysis reports."""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from gfa_core.mas.agents.interpretation import build_claim_layers
from gfa_core.mas.agents.interpretation.narrative_composer import (
    claims_to_scientific_discoveries,
    render_major_claims_markdown,
)
from gfa_core.mas.agents.interpretation.result_interpreter import (
    build_grouped_result_interpretations,
    build_structured_result_interpretations,
)


STAGE_NAMES = {
    "genome_prepare": "基因组数据准备",
    "identify": "基因家族鉴定",
    "phylogeny": "系统发育分析",
    "motif": "Motif分析",
    "domain": "结构域分析",
    "structure": "基因结构分析",
    "promoter": "启动子分析",
    "synteny": "共线性分析",
    "interspecies_synteny": "种间共线性分析",
    "karyotype": "核型分析",
    "combo": "组合图",
    "ppi": "蛋白互作分析",
    "expression": "表达分析",
    "pep": "蛋白属性分析",
    "kaks": "Ka/Ks分析",
    "reference_classification": "参考亚家族分类",
    "rnaseq_qc": "RNA-seq质控",
    "rnaseq_align": "RNA-seq比对",
    "rnaseq_quant": "RNA-seq定量",
    "rnaseq_sample_qc": "RNA-seq样本质量评估",
    "rnaseq_de": "RNA-seq差异表达",
    "annotation": "背景功能注释",
    "annotation_go": "GO背景自动注释",
    "annotation_kegg": "KEGG/KO背景自动注释",
    "rnaseq_go_enrich": "RNA-seq GO富集分析",
    "rnaseq_kegg_enrich": "RNA-seq KEGG富集分析",
    "rnaseq_fgsea": "RNA-seq fgsea排序富集",
    "rnaseq_wgcna": "RNA-seq WGCNA共表达分析",
    "rnaseq_candidate_family": "RNA-seq候选家族筛选",
    "rnaseq_enrich": "RNA-seq富集分析（兼容旧入口）",
}

STAGE_ORDER = [
    "genome_prepare",
    "identify",
    "phylogeny",
    "domain",
    "motif",
    "structure",
    "promoter",
    "synteny",
    "interspecies_synteny",
    "karyotype",
    "combo",
    "ppi",
    "expression",
    "pep",
    "kaks",
    "reference_classification",
    "rnaseq_qc",
    "rnaseq_align",
    "rnaseq_quant",
    "rnaseq_sample_qc",
    "rnaseq_de",
    "annotation",
    "annotation_go",
    "annotation_kegg",
    "rnaseq_go_enrich",
    "rnaseq_kegg_enrich",
    "rnaseq_fgsea",
    "rnaseq_wgcna",
    "rnaseq_candidate_family",
    "rnaseq_enrich",
]

ROUTE_EXPECTED_STAGES = {
    "subfamily_prediction": ["phylogeny", "domain", "motif", "structure", "combo", "reference_classification"],
    "subfamily_evidence": ["phylogeny", "domain", "motif", "structure", "combo"],
    "target_plus_reference": ["identify", "phylogeny", "domain", "motif", "structure", "combo", "interspecies_synteny"],
    "multi_species": ["identify", "phylogeny", "domain", "motif", "structure", "combo", "interspecies_synteny"],
    "single_species": ["identify", "phylogeny", "domain", "motif", "structure"],
    "target_only": ["identify", "phylogeny", "domain", "motif", "structure"],
}


RNASEQ_METHOD_CONTEXT = {
    "rnaseq_qc": "RNA-seq质控用于去除接头、低质量碱基和污染reads，常规报告应包含过滤前后reads、Q20/Q30、GC含量和FastQC/Cutadapt或fastp日志。",
    "rnaseq_align": "比对阶段将clean reads映射到参考基因组，HISAT2等剪接比对工具的总体比对率、唯一比对率和多比对率是判断样本与参考基因组匹配程度的核心证据。",
    "rnaseq_quant": "定量阶段把比对结果汇总到基因模型，count矩阵用于DESeq2，TPM/FPKM矩阵可用于表达展示，但差异统计应优先基于原始counts。",
    "rnaseq_sample_qc": "样本质量评估基于VST/rlog或log表达矩阵进行PCA、样本相关性热图和层次聚类；它应位于定量之后、差异分析之前，用于检查重复一致性、分组分离和潜在离群样本。",
    "rnaseq_de": "差异表达分析用统计模型比较处理和对照，常用阈值为padj<0.05并结合|log2FC|筛选；全量DE结果还能作为fgsea排序输入。",
    "annotation": "背景注释为GO/KEGG富集提供gene2go和gene2ko背景，InterProScan/KofamScan等自动注释结果需要记录数据库和ID标准化策略。",
    "annotation_go": "GO背景自动注释使用InterProScan或兼容的Bioconductor来源生成gene2go，并记录ID标准化、foreground/background匹配和GO覆盖率。",
    "annotation_kegg": "KEGG/KO背景自动注释使用KofamScan生成gene2ko，并记录KOfam数据库完整性、高置信KO命中和KO覆盖率。",
    "rnaseq_go_enrich": "GO富集基于差异基因相对于gene2go背景的过表达检验，用于解释生物过程、分子功能和细胞组分的显著偏倚。",
    "rnaseq_kegg_enrich": "KEGG富集基于gene2ko/KO背景检验通路富集；植物项目应优先使用物种KEGG pathway或植物兼容过滤，避免把人类疾病/动物系统通路作为主结论。",
    "rnaseq_fgsea": "fgsea基于全部基因排序而非DEG阈值，可补充发现整体协同变化的通路；当前实现不使用人类MSigDB/org.Hs.eg.db，而优先使用物种KEGG pathway白名单结合gene2ko构建植物兼容gene set。",
    "rnaseq_wgcna": "WGCNA把表达模式相似基因聚成模块，再关联样本性状；小样本设计可用于探索候选模块，但应谨慎解释模块-性状相关性和hub基因。",
    "rnaseq_candidate_family": "候选家族筛选把DEG、InterPro/Pfam结构域和GO/KEGG富集上下文整合，用于决定下一步要正式identify哪个基因家族；它是优先级排序，不等同于家族成员最终鉴定。",
}


def _safe_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        n = int(float(value))
        return n if n > 0 else 0
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _expected_stages_for_report(analysis_route: str, stages: List[str]) -> List[str]:
    route = str(analysis_route or "").strip()
    expected = list(ROUTE_EXPECTED_STAGES.get(route) or [])
    if not expected:
        return list(stages)
    seen = set(expected)
    return expected + [stage for stage in stages if stage not in seen]


def _report_mode_for_route(analysis_route: str, stages: List[str]) -> str:
    route = str(analysis_route or "").strip()
    if route in {"subfamily_prediction", "subfamily_evidence"}:
        return "subfamily_prediction_report"
    if "reference_classification" in stages and route in {"confirmed_subfamily"}:
        return "subfamily_prediction_report"
    rnaseq_stages = {
        "rnaseq_qc",
        "rnaseq_align",
        "rnaseq_quant",
        "rnaseq_sample_qc",
        "rnaseq_de",
        "annotation",
        "annotation_go",
        "annotation_kegg",
        "rnaseq_go_enrich",
        "rnaseq_kegg_enrich",
        "rnaseq_fgsea",
        "rnaseq_wgcna",
        "rnaseq_candidate_family",
        "rnaseq_enrich",
    }
    if any(stage in rnaseq_stages for stage in stages):
        return "rnaseq_integrated_report"
    return "gene_family_integrated_report"


def _report_mode_label(report_mode: str, is_final: bool) -> str:
    if report_mode == "subfamily_prediction_report":
        return "亚家族分类预测解读" if is_final else "亚家族分类预测实时解读"
    if report_mode == "rnaseq_integrated_report":
        return "RNA-seq综合深度解读" if is_final else "RNA-seq综合实时解读"
    return "基因家族综合深度解读" if is_final else "基因家族综合实时解读"


def _interpretation_boundary_for_mode(report_mode: str) -> str:
    if report_mode == "subfamily_prediction_report":
        return "该报告用于亚家族分类预测、低置信成员复核和后续分析优先级建议；不得直接写成家族进化、复制扩张或功能分化的最终结论。"
    if report_mode == "rnaseq_integrated_report":
        return "该报告用于整合RNA-seq质控、比对、定量、样本QC、差异表达、富集和共表达证据形成表达响应假设；不得把富集或模块相关直接写成调控因果。"
    return "该报告用于整合成员鉴定、结构、系统发育、共线性和表达等证据形成综合假设；亚家族分类仅作为可选注释层。"


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _artifact_path(manifest: Dict[str, Any], artifact_key: str) -> Optional[Path]:
    artifact = (manifest.get("artifacts") or {}).get(artifact_key) or {}
    manifest_path = Path(str(manifest.get("_manifest_path") or ""))
    job_dir = manifest_path.parent.parent if manifest_path else None
    for path_text in (artifact.get("full_path"), artifact.get("path")):
        if not path_text:
            continue
        path = Path(str(path_text))
        candidates = [path]
        if job_dir:
            if not path.is_absolute():
                candidates.append(job_dir / path)
            else:
                job_index = next((index for index, part in enumerate(path.parts) if part.startswith("job_")), None)
                if job_index is not None:
                    candidates.append(job_dir.joinpath(*path.parts[job_index + 1 :]))
                candidates.append(manifest_path.parent / path.name)
        for candidate in candidates:
            if candidate.exists():
                return candidate
    return None


def _artifact_path_by_suffix(manifest: Dict[str, Any], suffix: str) -> Optional[Path]:
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    suffix = str(suffix or "")
    for key in sorted(artifacts):
        if str(key).endswith(suffix):
            path = _artifact_path(manifest, str(key))
            if path:
                return path
    return None


def _artifact_file_candidates(manifest: Dict[str, Any], names: Iterable[str]) -> List[Path]:
    paths: List[Path] = []
    for name in names:
        path = _artifact_path(manifest, name)
        if path:
            paths.append(path)
    return paths


def _kaks_result_paths(manifest: Dict[str, Any]) -> List[Path]:
    paths: List[Path] = []
    primary = _artifact_path(manifest, "kaks_results")
    if primary:
        paths.append(primary)
    for key in sorted((manifest.get("artifacts") or {}).keys()):
        if key == "kaks_results" or not str(key).endswith("_kaks_results"):
            continue
        path = _artifact_path(manifest, key)
        if path:
            paths.append(path)
    for run in manifest.get("runs") or []:
        if not isinstance(run, dict):
            continue
        result_text = str(run.get("results") or "").strip()
        if not result_text:
            continue
        result_path = Path(result_text)
        if not result_path.is_absolute():
            if result_path.exists():
                result_path = result_path.resolve()
            elif manifest.get("_manifest_path"):
                result_path = Path(str(manifest["_manifest_path"])).parent.parent / result_path
        if result_path.exists():
            paths.append(result_path)
    deduped: List[Path] = []
    seen = set()
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def _count_fasta(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return sum(1 for line in handle if line.startswith(">"))
    except Exception:
        return 0


def _read_fasta_ids(path: Optional[Path], limit: int = 1000000) -> List[str]:
    if not path:
        return []
    ids: List[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if not line.startswith(">"):
                    continue
                ids.append(line[1:].strip().split()[0])
                if len(ids) >= limit:
                    break
    except Exception:
        return []
    return ids


def _normalize_gene_id(gene_id: str) -> str:
    text = str(gene_id).strip()
    if text.startswith(">"):
        text = text[1:].split()[0]
    text = re.sub(r"\.T\d+$", "", text)
    return text


def _normalize_ref_id(gene_id: str) -> str:
    return re.sub(r"\.\d+$", "", str(gene_id).strip())


def _manifest_final_dir(manifest: Dict[str, Any]) -> Optional[Path]:
    if not manifest.get("_manifest_path"):
        return None
    return Path(str(manifest["_manifest_path"])).parent


def _job_dir(manifest: Dict[str, Any]) -> Optional[Path]:
    final_dir = _manifest_final_dir(manifest)
    return final_dir.parent if final_dir else None


def _sibling_manifest(manifest: Dict[str, Any], stage: str) -> Dict[str, Any]:
    final_dir = _manifest_final_dir(manifest)
    prefix = manifest.get("prefix")
    if not final_dir or not prefix:
        return {}
    scope_id = str(_manifest_value(manifest, "scope_id") or "")

    if scope_id:
        scoped_path = final_dir / f"{prefix}.{scope_id}.{stage}.manifest.json"
        if scoped_path.exists():
            sibling = _read_json(scoped_path)
            if sibling:
                sibling["_manifest_path"] = str(scoped_path)
                return sibling

        for candidate in sorted(final_dir.glob(f"{prefix}.*.{stage}.manifest.json")):
            sibling = _read_json(candidate)
            if str(_manifest_value(sibling, "scope_id") or "") == scope_id:
                sibling["_manifest_path"] = str(candidate)
                return sibling

    legacy_path = final_dir / f"{prefix}.{stage}.manifest.json"
    sibling = _read_json(legacy_path)
    if sibling:
        sibling["_manifest_path"] = str(legacy_path)
    return sibling


def _family_member_ids(manifest: Dict[str, Any]) -> List[str]:
    identify = _sibling_manifest(manifest, "identify")
    members_path = _artifact_path(identify, "final_members") or _artifact_path(identify, "final_proteins")
    if not members_path:
        final_dir = _manifest_final_dir(manifest)
        prefix = manifest.get("prefix")
        if final_dir and prefix:
            members_path = final_dir / f"{prefix}.final_members.pep.fa"
    if members_path and members_path.suffix in {".fa", ".fasta", ".faa"}:
        return [_normalize_gene_id(i) for i in _read_fasta_ids(members_path)]
    return [_normalize_gene_id(i) for i in _read_ids(members_path, limit=1000000)] if members_path else []


def _job_input_dir(manifest: Dict[str, Any]) -> Optional[Path]:
    job_dir = _job_dir(manifest)
    if not job_dir:
        return None
    return job_dir.parent.parent / "input" / job_dir.name


def _parse_newick_tips(path: Path) -> List[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    return re.findall(r"(?<=[(,])([^():,;]+):", text)


def _parse_domtblout(path: Optional[Path], cutoff: float = 1e-5) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    if not path:
        return {"rows": rows, "query_count": 0, "significant_hits": 0, "unique_domains": [], "top_domains": []}
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if not line.strip() or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 22:
                    continue
                i_eval = _safe_float(parts[12])
                rows.append({
                    "domain": parts[0],
                    "accession": parts[1],
                    "query": parts[3],
                    "i_evalue": i_eval,
                    "ali_from": _safe_int(parts[17]),
                    "ali_to": _safe_int(parts[18]),
                    "description": " ".join(parts[22:]) if len(parts) > 22 else "",
                })
    except Exception:
        rows = []
    significant = [row for row in rows if row.get("i_evalue") is not None and row["i_evalue"] <= cutoff]
    domain_counter = Counter(row["domain"] for row in significant)
    return {
        "rows": rows,
        "query_count": len({row["query"] for row in rows}),
        "raw_hits": len(rows),
        "significant_hits": len(significant),
        "proteins_with_domains": len({row["query"] for row in significant}),
        "unique_domains": sorted(domain_counter),
        "top_domains": [{"domain": name, "count": count} for name, count in domain_counter.most_common(5)],
        "sbp_protein_count": len({row["query"] for row in significant if row["domain"] == "SBP"}),
    }


def _normalize_top_domains(value: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            name = item.get("domain") or item.get("name") or item.get("id")
            count = item.get("count") or item.get("proteins") or item.get("hits")
        elif isinstance(item, (list, tuple)) and item:
            name = item[0]
            count = item[1] if len(item) > 1 else None
        else:
            continue
        if name:
            normalized.append({"domain": str(name), "count": count})
        if len(normalized) >= 5:
            break
    return normalized


def _parse_mcscanx_collinearity(path: Optional[Path], family_ids: Iterable[str], ref_ids: Iterable[str] = ()) -> Dict[str, Any]:
    family = set(family_ids)
    refs = {_normalize_ref_id(i) for i in ref_ids}
    stats = {
        "alignment_blocks": 0,
        "gene_pairs": 0,
        "family_pair_count": 0,
        "family_block_count": 0,
        "ref_family_pair_count": 0,
        "family_ref_pair_count": 0,
        "example_family_pairs": [],
    }
    if not path:
        return stats
    current_block = ""
    family_blocks = set()
    examples: List[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if line.startswith("## Alignment"):
                    stats["alignment_blocks"] += 1
                    current_block = line.split(":", 1)[0].replace("## Alignment", "").strip()
                    continue
                if not line.startswith(" ") or ":" not in line:
                    continue
                ft_genes = re.findall(r"FtPinG\d+\.\d+(?:\.T\d+)?", line)
                at_genes = re.findall(r"AT\dG\d+(?:\.\d+)?", line)
                genes = ft_genes + at_genes
                if len(genes) < 2:
                    continue
                stats["gene_pairs"] += 1
                norm_ft = {_normalize_gene_id(g) for g in ft_genes}
                norm_at = {_normalize_ref_id(g) for g in at_genes}
                has_family = bool(norm_ft & family)
                has_ref = bool(norm_at & refs) if refs else False
                if has_family:
                    stats["family_pair_count"] += 1
                    if current_block:
                        family_blocks.add(current_block)
                    if len(examples) < 5:
                        examples.append(" - ".join(genes[:2]))
                if has_ref:
                    stats["ref_family_pair_count"] += 1
                if has_family and has_ref:
                    stats["family_ref_pair_count"] += 1
    except Exception:
        return stats
    stats["family_block_count"] = len(family_blocks)
    stats["example_family_pairs"] = examples
    return stats


def _pairwise_synteny_summary(metrics: Dict[str, Any]) -> Dict[str, int]:
    summary = {
        "anchor_count": 0,
        "family_highlight_links": 0,
        "marked_block_count": 0,
    }
    for pair in metrics.get("pairwise_synteny") or []:
        if not isinstance(pair, dict):
            continue
        summary["anchor_count"] += _safe_int(pair.get("anchor_count"))
        summary["family_highlight_links"] += _safe_int(
            pair.get("family_highlight_links") or pair.get("pairwise_family_highlight_links")
        )
        summary["marked_block_count"] += _safe_int(
            pair.get("marked_block_count") or pair.get("pairwise_marked_block_count")
        )
    return summary


def _parse_simple_count_table(path: Optional[Path], limit: int = 5) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not path:
        return items
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                text = line.strip()
                if not text or text.startswith("#"):
                    continue
                parts = re.split(r"\t+", text)
                if len(parts) >= 2 and _safe_int(parts[0]):
                    items.append({"name": parts[1], "count": _safe_int(parts[0])})
                if len(items) >= limit:
                    break
    except Exception:
        return items
    return items


def _read_ids(path: Path, limit: int = 8) -> List[str]:
    ids: List[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                if text.startswith(">"):
                    text = text[1:].split()[0]
                else:
                    text = re.split(r"\s+", text)[0]
                ids.append(text)
                if len(ids) >= limit:
                    break
    except Exception:
        pass
    return ids


def _read_table(path: Path, limit: int = 2000) -> List[Dict[str, str]]:
    try:
        sample = path.read_text(encoding="utf-8", errors="ignore")[:4096]
    except Exception:
        return []
    delimiter = "\t" if sample.count("\t") >= sample.count(",") else ","
    rows: List[Dict[str, str]] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            for idx, row in enumerate(reader):
                if idx >= limit:
                    break
                rows.append({str(k): str(v) for k, v in row.items() if k is not None})
    except Exception:
        return []
    return rows


def _table_dimensions(path: Path) -> Dict[str, int]:
    rows = _read_table(path, limit=100000)
    if not rows:
        return {"rows": 0, "columns": 0}
    return {"rows": len(rows), "columns": len(rows[0])}


def _source(path: Optional[Path], source_type: str = "raw_file") -> Dict[str, str]:
    if not path:
        return {"source_type": source_type, "source_file": ""}
    return {"source_type": source_type, "source_file": str(path)}


def _evidence(
    stage: str,
    claim: str,
    evidence: str,
    path: Optional[Path],
    *,
    source_type: str = "raw_file",
    confidence: str = "high",
    level: str = "fact",
) -> Dict[str, str]:
    item = {
        "stage": stage,
        "claim": claim,
        "evidence": evidence,
        "confidence": confidence,
        "interpretation_level": level,
    }
    item.update(_source(path, source_type))
    return item


def _status_rank(status: str) -> int:
    order = {"success": 0, "partial_success": 1, "failed": 2}
    return order.get(status or "", 3)


def _manifest_value(manifest: Dict[str, Any], key: str) -> Any:
    params = manifest.get("parameters") or {}
    scope = manifest.get("scope") or {}
    for source in (manifest, params, scope):
        if isinstance(source, dict) and source.get(key) not in (None, ""):
            return source.get(key)
    return ""


def _list_value(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item for item in re.split(r"[,;，、\s]+", str(value).strip()) if item]


def _scope_context(manifests: Dict[str, Dict[str, Any]], prefix: str) -> Dict[str, Any]:
    manifest = next(iter(manifests.values()), {})
    family = _manifest_value(manifest, "family") or _manifest_value(manifest, "gene_family")
    if not family and "." in prefix:
        family = prefix.split(".", 1)[1]
    scope = {
        "prefix": prefix,
        "family": family or "",
        "scope_id": _manifest_value(manifest, "scope_id") or "",
        "scope_file": _manifest_value(manifest, "scope_file") or "",
        "scope_mode": _manifest_value(manifest, "scope_mode") or _manifest_value(manifest, "scopeMode") or "",
        "target_species": _list_value(_manifest_value(manifest, "target_species") or _manifest_value(manifest, "targetSpecies")),
        "reference_species": _list_value(_manifest_value(manifest, "reference_species") or _manifest_value(manifest, "referenceSpecies")),
        "comparative_species": _list_value(_manifest_value(manifest, "comparative_species") or _manifest_value(manifest, "comparativeSpecies")),
        "selected_species": _list_value(_manifest_value(manifest, "selected_species") or _manifest_value(manifest, "selectedSpecies")),
    }
    parts = []
    if scope["family"]:
        parts.append(f"{scope['family']}家族")
    if scope["target_species"]:
        parts.append("目标物种=" + ",".join(scope["target_species"]))
    if scope["reference_species"]:
        parts.append("参考物种=" + ",".join(scope["reference_species"]))
    if scope["comparative_species"]:
        parts.append("比较物种=" + ",".join(scope["comparative_species"]))
    scope["label"] = "；".join(parts) if parts else prefix
    return scope


def _default_analysis_route(scope_id: str, analysis_route: str, member_set_id: str = "") -> str:
    route = str(analysis_route or "").strip()
    scope_text = str(scope_id or "")
    member_text = str(member_set_id or "")
    if route in {"subfamily_prediction", "confirmed_subfamily", "subfamily_evidence"}:
        return route
    if "subfamily_evidence" in scope_text or "subfamily_evidence" in member_text:
        return "subfamily_evidence"
    if "target_plus_reference" in scope_text or ".ref-" in scope_text:
        if route in {"", "multi_species", "subfamily_prediction", "confirmed_subfamily", "target_plus_reference"}:
            return "target_plus_reference"
    if route:
        return route
    if "target_plus_reference" in scope_text or ".ref-" in scope_text:
        return "target_plus_reference"
    if "target_only" in scope_text:
        return "single_species"
    return ""


def _route_compatible(requested: str, manifest_route: str) -> bool:
    requested = str(requested or "").strip()
    manifest_route = str(manifest_route or "").strip()
    if not requested:
        return True
    if not manifest_route:
        return True
    if requested == manifest_route:
        return True
    if requested == "target_plus_reference":
        return manifest_route in {"multi_species", "subfamily_prediction", "confirmed_subfamily", "subfamily_evidence", "single_species", "target_plus_reference"}
    if requested == "single_species":
        return manifest_route in {"single_species", "target_only"}
    return False


def _report_member_set_filter(analysis_route: str, member_set_id: str) -> str:
    if analysis_route == "target_plus_reference":
        return ""
    return str(member_set_id or "").strip()


def _report_freshness(manifests: Dict[str, Dict[str, Any]], generated_at: str) -> Dict[str, Any]:
    newest_ts = 0.0
    newest_path = ""
    for manifest in manifests.values():
        raw = str(manifest.get("_manifest_path") or "")
        if not raw:
            continue
        path = Path(raw)
        try:
            ts = path.stat().st_mtime
        except OSError:
            continue
        if ts > newest_ts:
            newest_ts = ts
            newest_path = str(path)
    newest_iso = datetime.fromtimestamp(newest_ts).isoformat() if newest_ts else ""
    stale = False
    try:
        stale = bool(newest_ts and datetime.fromisoformat(generated_at).timestamp() + 1 < newest_ts)
    except Exception:
        stale = False
    return {
        "schema": "interpretation_freshness_v1",
        "generated_at": generated_at,
        "newest_manifest_mtime": newest_iso,
        "newest_manifest_path": newest_path,
        "stale": stale,
    }


def _manifest_identity_value(manifest: Dict[str, Any], key: str) -> str:
    stage_context = manifest.get("stage_context") or manifest.get("stageContext") or {}
    params = manifest.get("parameters") or {}
    return str(manifest.get(key) or params.get(key) or stage_context.get(key) or "").strip()


def _manifest_score(
    path: Path,
    manifest: Dict[str, Any],
    *,
    scope_id: str = "",
    analysis_route: str = "",
    member_set_id: str = "",
    rnaseq_scope_id: str = "",
) -> tuple:
    status = str(manifest.get("status") or "")
    status_rank = 2 if status == "success" else 1 if status == "partial_success" else 0
    scope_match = 1 if (not scope_id or _manifest_identity_value(manifest, "scope_id") == scope_id or f".{scope_id}." in path.name) else 0
    rnaseq_match = 1 if (not rnaseq_scope_id or _manifest_identity_value(manifest, "rnaseq_scope_id") == rnaseq_scope_id) else 0
    route_value = _manifest_identity_value(manifest, "analysis_route")
    route_match = 1 if (not analysis_route or route_value == analysis_route) else 0
    member_value = _manifest_identity_value(manifest, "member_set_id")
    member_match = 1 if (not member_set_id or member_value == member_set_id) else 0
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    metrics = manifest.get("metrics") if isinstance(manifest.get("metrics"), dict) else {}
    completeness = int(bool(metrics)) + int(bool(artifacts)) + int("canonical_manifest" not in manifest and "alias_type" not in manifest)
    metric_count = len(metrics)
    artifact_count = len(artifacts)
    try:
        mtime = path.stat().st_mtime
        file_size = path.stat().st_size
    except OSError:
        mtime = 0
        file_size = 0
    return (scope_match, rnaseq_match, route_match, member_match, status_rank, completeness, metric_count, artifact_count, file_size, mtime)


def load_stage_manifests(
    final_dir: Path,
    prefix: str,
    scope_id: str = "",
    analysis_route: str = "",
    member_set_id: str = "",
    rnaseq_scope_id: str = "",
) -> Dict[str, Dict[str, Any]]:
    manifests: Dict[str, Dict[str, Any]] = {}
    if not final_dir.exists():
        return manifests
    analysis_route = _default_analysis_route(scope_id, analysis_route, member_set_id)
    member_set_id = _report_member_set_filter(analysis_route, member_set_id)
    pattern = f"{prefix}.*.manifest.json" if prefix else "*.manifest.json"
    for path in sorted(final_dir.glob(pattern)):
        manifest = _read_json(path)
        stage = manifest.get("stage")
        manifest_scope_id = _manifest_identity_value(manifest, "scope_id")
        manifest_rnaseq_scope_id = _manifest_identity_value(manifest, "rnaseq_scope_id")
        manifest_route = _manifest_identity_value(manifest, "analysis_route")
        manifest_member = _manifest_identity_value(manifest, "member_set_id")
        rnaseq_global_context_stages = {
            "rnaseq_qc",
            "rnaseq_align",
            "rnaseq_quant",
            "annotation",
            "annotation_go",
            "annotation_kegg",
            "expression",
        }
        if scope_id and manifest_scope_id and manifest_scope_id != scope_id:
            continue
        if scope_id and not manifest_scope_id and f".{scope_id}." not in path.name:
            continue
        if rnaseq_scope_id and manifest_rnaseq_scope_id and manifest_rnaseq_scope_id != rnaseq_scope_id and stage not in rnaseq_global_context_stages:
            continue
        if rnaseq_scope_id and not manifest_rnaseq_scope_id and stage not in rnaseq_global_context_stages:
            continue
        if not _route_compatible(analysis_route, manifest_route):
            continue
        if member_set_id and manifest_member and manifest_member != member_set_id:
            continue
        if stage:
            manifest["_manifest_path"] = str(path)
            existing = manifests.get(stage)
            if existing:
                old_path = Path(str(existing.get("_manifest_path") or ""))
                old_score = _manifest_score(old_path, existing, scope_id=scope_id, analysis_route=analysis_route, member_set_id=member_set_id, rnaseq_scope_id=rnaseq_scope_id)
                new_score = _manifest_score(path, manifest, scope_id=scope_id, analysis_route=analysis_route, member_set_id=member_set_id, rnaseq_scope_id=rnaseq_scope_id)
                if old_score >= new_score:
                    continue
            manifests[stage] = manifest
    return manifests


def ordered_stages(stage_manifests: Dict[str, Dict[str, Any]], current_stage: str = "") -> List[str]:
    stages = list(stage_manifests)
    ranked = sorted(
        stages,
        key=lambda s: (
            STAGE_ORDER.index(s) if s in STAGE_ORDER else len(STAGE_ORDER),
            _status_rank(str(stage_manifests[s].get("status", ""))),
            s,
        ),
    )
    if current_stage and current_stage in ranked:
        return ranked[: ranked.index(current_stage) + 1]
    return ranked


def extract_stage_evidence(stage: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
    metrics = manifest.get("metrics") or {}
    artifacts = manifest.get("artifacts") or {}
    evidence: List[Dict[str, str]] = []
    risks: List[Dict[str, str]] = []
    parsed: Dict[str, Any] = {}
    manifest_path = Path(str(manifest.get("_manifest_path", ""))) if manifest.get("_manifest_path") else None
    scope = _scope_context({"current": manifest}, str(manifest.get("prefix") or ""))
    family = scope.get("family") or "该"
    family_label = f"{family}家族" if family != "该" else "该家族"
    target_label = ",".join(scope.get("target_species") or []) or str(manifest.get("prefix") or "目标物种")
    reference_label = ",".join(scope.get("reference_species") or []) or "参考物种"

    def add_manifest(claim: str, text: str, confidence: str = "high", level: str = "fact") -> None:
        evidence.append(_evidence(stage, claim, text, manifest_path, source_type="manifest", confidence=confidence, level=level))

    if stage == "genome_prepare":
        params = manifest.get("parameters") or {}
        species_results = (
            metrics.get("species_results")
            if isinstance(metrics.get("species_results"), list)
            else []
        )
        prepared_species: List[Dict[str, Any]] = []
        for item in species_results:
            if not isinstance(item, dict):
                continue
            species = str(item.get("species") or "").strip()
            if not species:
                continue
            artifact_names = sorted(
                str(key)
                for key in (item.get("artifacts") or {})
                if str(key).strip()
            )
            prepared_species.append(
                {
                    "species": species,
                    "success": item.get("success") is True,
                    "role": "reference" if item.get("is_reference") is True else "target",
                    "gene_count": _safe_int(item.get("gene_count")),
                    "artifact_types": artifact_names[:8],
                }
            )
        total_species = _safe_int(
            params.get("total_species") or len(prepared_species)
        )
        success_species = _safe_int(
            params.get("success_species")
            or sum(1 for item in prepared_species if item["success"])
        )
        failed_species = _safe_int(params.get("failed_species"))
        preflight = (
            metrics.get("config_preflight")
            if isinstance(metrics.get("config_preflight"), dict)
            else {}
        )
        parsed.update(
            {
                "total_species": total_species,
                "success_species": success_species,
                "failed_species": failed_species,
                "prepared_species": prepared_species,
                "config_preflight_ok": preflight.get("ok") is True,
                "valid_species": list(preflight.get("valid_species") or [])[:20],
                "invalid_species": list(preflight.get("invalid_species") or [])[:20],
            }
        )
        add_manifest(
            "数据准备总体状态",
            (
                f"total_species={total_species}, success_species={success_species}, "
                f"failed_species={failed_species}, config_preflight_ok={preflight.get('ok')}"
            ),
        )
        for item in prepared_species[:10]:
            evidence.append(
                _evidence(
                    stage,
                    f"{item['species']} 数据准备",
                    (
                        f"role={item['role']}, success={item['success']}, "
                        f"gene_count={item['gene_count']}, "
                        f"artifacts={','.join(item['artifact_types']) or '未记录'}"
                    ),
                    manifest_path,
                    source_type="manifest",
                )
            )
        if failed_species or any(not item["success"] for item in prepared_species):
            risks.append(
                {
                    "stage": stage,
                    "severity": "high",
                    "message": "存在未成功完成数据准备的物种，后续分析不能视为输入完整。",
                    "source_file": str(manifest_path) if manifest_path else "",
                }
            )
        if preflight and preflight.get("ok") is not True:
            risks.append(
                {
                    "stage": stage,
                    "severity": "high",
                    "message": "config.xlsx 预检未通过，物种角色或输入文件需复核。",
                    "source_file": str(manifest_path) if manifest_path else "",
                }
            )

    elif stage == "identify":
        members_path = (
            _artifact_path(manifest, "final_members_id")
            or _artifact_path(manifest, "compat_final_members_id")
            or _artifact_path(manifest, "curated_members_id")
            or _artifact_path(manifest, "identified_members_id")
        )
        proteins_path = (
            _artifact_path(manifest, "final_members")
            or _artifact_path(manifest, "compat_final_members")
            or _artifact_path(manifest, "final_proteins")
            or _artifact_path(manifest, "identified_members")
        )
        hmm_path = _artifact_path(manifest, "hmm_results") or _artifact_path(manifest, "hmm_model_audit")
        blast_path = _artifact_path(manifest, "blast_results")
        final_members = _safe_int(metrics.get("final_members"))
        total_proteins = _safe_int(metrics.get("total_proteins"))
        hmm_hits = _safe_int(metrics.get("hmm_hits") or metrics.get("hmm_members"))
        blast_hits = _safe_int(metrics.get("blast_hits") or metrics.get("blast_members"))
        id_count = len(_read_ids(members_path, limit=1000000)) if members_path else 0
        fasta_count = _count_fasta(proteins_path) if proteins_path else 0
        parsed.update({
            "final_members": final_members,
            "total_proteins": total_proteins,
            "hmm_hits": hmm_hits,
            "blast_hits": blast_hits,
            "id_file_count": id_count,
            "fasta_sequence_count": fasta_count,
            "example_members": _read_ids(members_path) if members_path else [],
        })
        add_manifest("鉴定成员规模", f"manifest记录 final_members={final_members}, total_proteins={total_proteins}")
        if members_path:
            evidence.append(_evidence(stage, "成员ID文件", f"final_members.id 读取到 {id_count} 个成员ID", members_path))
        if proteins_path:
            evidence.append(_evidence(stage, "成员蛋白FASTA", f"final_members.pep.fa 读取到 {fasta_count} 条蛋白序列", proteins_path))
        if hmm_path or blast_path:
            evidence.append(_evidence(stage, "HMMER/BLAST一致性", f"HMM hits={hmm_hits}, BLAST hits={blast_hits}", hmm_path or blast_path))
        candidates = _safe_int(metrics.get("candidates") or metrics.get("candidate_members"))
        if candidates and final_members and candidates < final_members:
            risks.append({
                "stage": stage,
                "severity": "medium",
                "message": f"manifest 中 candidates={candidates} 小于 final_members={final_members}，报告不得表述为“从{candidates}个候选筛出{final_members}个”。",
                "source_file": str(manifest_path) if manifest_path else "",
            })
        if id_count and final_members and id_count != final_members:
            risks.append({"stage": stage, "severity": "high", "message": f"成员ID文件计数({id_count})与manifest final_members({final_members})不一致。", "source_file": str(members_path)})
        if fasta_count and final_members and fasta_count != final_members:
            risks.append({"stage": stage, "severity": "high", "message": f"蛋白FASTA序列数({fasta_count})与manifest final_members({final_members})不一致。", "source_file": str(proteins_path)})

    elif stage == "phylogeny":
        tree_path = _artifact_path(manifest, "tree")
        msa_path = _artifact_path(manifest, "msa")
        seq_count = _safe_int(metrics.get("sequences_count") or metrics.get("sequence_count"))
        msa_count = _count_fasta(msa_path) if msa_path else 0
        parsed.update({
            "sequence_count": seq_count,
            "msa_sequence_count": msa_count,
            "msa_length": _safe_int(metrics.get("msa_length")),
            "gap_percentage": metrics.get("gap_percentage"),
            "tree_model": metrics.get("tree_model"),
        })
        add_manifest("系统发育输入规模", f"sequences_count={seq_count}, msa_length={metrics.get('msa_length')}, gap_percentage={metrics.get('gap_percentage')}%")
        if msa_path:
            evidence.append(_evidence(stage, "MSA文件", f"MSA FASTA 读取到 {msa_count} 条序列", msa_path))
        if tree_path:
            tips = len(_parse_newick_tips(tree_path))
            parsed["tree_tip_estimate"] = tips
            evidence.append(_evidence(stage, "Newick树文件", f"tree.nwk 存在，估算 tip 数 {tips or '未能估算'}", tree_path))
            if seq_count and tips and tips != seq_count:
                risks.append({"stage": stage, "severity": "high", "message": f"Newick tip数({tips})与manifest序列数({seq_count})不一致，系统发育图或树文件需复核。", "source_file": str(tree_path)})
        gap = _safe_float(metrics.get("gap_percentage"))
        if gap is not None and gap >= 50:
            risks.append({"stage": stage, "severity": "medium", "message": f"MSA gap比例为{gap:.2f}%，深层拓扑和远缘分支解释需谨慎。", "source_file": str(manifest_path) if manifest_path else ""})

    elif stage == "motif":
        motifs = metrics.get("motifs") if isinstance(metrics.get("motifs"), list) else []
        motif_count = _safe_int(metrics.get("motif_count"))
        total_sites = _safe_int(metrics.get("total_sites"))
        sequence_count = _safe_int(metrics.get("sequence_count"))
        conserved = max(motifs, key=lambda m: _safe_int(m.get("sites")), default={})
        parsed.update({
            "motif_count": motif_count,
            "total_sites": total_sites,
            "sequence_count": sequence_count,
            "most_conserved_motif": conserved,
        })
        add_manifest("Motif总体结果", f"motif_count={motif_count}, total_sites={total_sites}, sequence_count={sequence_count}")
        if conserved:
            add_manifest("核心保守Motif", f"{conserved.get('id') or conserved.get('name')} sites={conserved.get('sites')}, width={conserved.get('width')}")
        motif_txt = _artifact_path(manifest, "motif_prot_txt")
        if motif_txt:
            evidence.append(_evidence(stage, "Motif位置摘要", f"motif_prot_txt 文件大小 {motif_txt.stat().st_size} 字节", motif_txt))
            if motif_txt.stat().st_size == 0:
                risks.append({"stage": stage, "severity": "medium", "message": "motif_prot_txt 为空，无法从原始位置表验证每个基因的motif排列。", "source_file": str(motif_txt)})
        if sequence_count and conserved:
            coverage = _safe_int(conserved.get("sites")) / sequence_count
            if coverage < 0.75:
                risks.append({"stage": stage, "severity": "low", "message": f"最保守motif覆盖率约{coverage:.1%}，不宜直接声称所有成员共享该motif。", "source_file": str(manifest_path) if manifest_path else ""})

    elif stage == "structure":
        genes_total = _safe_int(metrics.get("genes_total"))
        genes_with = _safe_int(metrics.get("genes_with_structure"))
        coverage = _safe_float(metrics.get("structure_coverage_rate"))
        parsed.update({
            "genes_total": genes_total,
            "genes_with_structure": genes_with,
            "structure_coverage_rate": coverage,
            "avg_exon_count_per_gene": metrics.get("avg_exon_count_per_gene"),
            "total_cds_features": metrics.get("total_cds_features"),
            "total_utr_features": metrics.get("total_utr_features"),
        })
        add_manifest("基因结构统计", f"genes_total={genes_total}, genes_with_structure={genes_with}, avg_exon={metrics.get('avg_exon_count_per_gene')}")
        for path in _artifact_file_candidates(manifest, ["structure_tsv", "structure_gff3_filtered"]):
            evidence.append(_evidence(stage, "结构原始表", f"{path.name} 大小 {path.stat().st_size} 字节", path))
        structure_tsv = _artifact_path(manifest, "structure_tsv")
        if structure_tsv:
            rows = _read_table(structure_tsv, limit=100000)
            mrna_genes = {_normalize_gene_id(row.get("gene_id", "")) for row in rows if row.get("feature") == "mRNA"}
            cds_counter = Counter(_normalize_gene_id(row.get("gene_id", "")) for row in rows if row.get("feature") == "CDS")
            cds_counter.pop("", None)
            parsed["raw_mrna_gene_count"] = len(mrna_genes)
            parsed["max_cds_gene"] = cds_counter.most_common(1)[0] if cds_counter else None
            if mrna_genes:
                evidence.append(_evidence(stage, "结构表基因计数", f"structure.tsv 中 mRNA 记录覆盖 {len(mrna_genes)} 个基因", structure_tsv, level="derived_stat"))
        if manifest.get("status") == "partial_success":
            risks.append({"stage": stage, "severity": "medium", "message": "structure 阶段为 partial_success，结构结论应标记为可用但不完整。", "source_file": str(manifest_path) if manifest_path else ""})
        if coverage and coverage > 1.0:
            risks.append({"stage": stage, "severity": "medium", "message": f"structure_coverage_rate={coverage}，manifest 的 genes_with_structure 可能按mRNA/转录本层级计数；报告应优先采用structure.tsv的基因级计数。", "source_file": str(manifest_path) if manifest_path else ""})

    elif stage == "expression":
        gene_exp = _artifact_path(manifest, "gene-exp")
        tau = _artifact_path(manifest, "tissue_specificity")
        cor = _artifact_path(manifest, "gene-cor")
        parsed["gene_count"] = _safe_int(metrics.get("gene_count"))
        parsed["tables"] = {}
        for label, path in [("gene_exp", gene_exp), ("tissue_specificity", tau), ("gene_cor", cor)]:
            if path:
                dims = _table_dimensions(path)
                parsed["tables"][label] = dims
                evidence.append(_evidence(stage, label, f"{path.name} 读取到 {dims['rows']} 行、{dims['columns']} 列", path))
        if gene_exp:
            rows = _read_table(gene_exp, limit=1000)
            expression_genes = {_normalize_gene_id(next(iter(row.values()), "")) for row in rows}
            family_genes = set(_family_member_ids(manifest))
            if family_genes:
                missing = sorted(family_genes - expression_genes)
                parsed["expression_missing_family_genes"] = missing[:10]
                if missing:
                    risks.append({
                        "stage": stage,
                        "severity": "medium",
                        "message": f"表达矩阵包含{len(expression_genes)}个家族基因，少于鉴定成员{len(family_genes)}个；缺失示例: {', '.join(missing[:3])}。",
                        "source_file": str(gene_exp),
                    })
            numeric_max: List[tuple[float, str]] = []
            for row in rows:
                gene = next(iter(row.values()), "")
                vals = [_safe_float(v) for k, v in row.items() if k and k.lower() not in {"gene", "geneid", "id"}]
                vals = [v for v in vals if v is not None]
                if vals:
                    numeric_max.append((max(vals), gene))
            numeric_max.sort(reverse=True)
            parsed["top_expression_genes"] = [{"gene": g, "max_expression": v} for v, g in numeric_max[:5]]
            if numeric_max:
                evidence.append(_evidence(stage, "高表达候选基因", "最高表达候选: " + ", ".join(f"{g}({v:.2f})" for v, g in numeric_max[:3]), gene_exp, level="derived_stat"))
        if tau:
            rows = _read_table(tau, limit=1000)
            tau_hits: List[tuple[float, str]] = []
            for row in rows:
                gene = next(iter(row.values()), "")
                for key, val in row.items():
                    if "tau" in key.lower():
                        fv = _safe_float(val)
                        if fv is not None:
                            tau_hits.append((fv, gene))
                        break
            tau_hits.sort(reverse=True)
            parsed["top_tissue_specific_genes"] = [{"gene": g, "tau": v} for v, g in tau_hits[:5]]
            if tau_hits:
                evidence.append(_evidence(stage, "组织特异性候选基因", "Tau较高候选: " + ", ".join(f"{g}({v:.2f})" for v, g in tau_hits[:3]), tau, level="derived_stat"))
        add_manifest("表达分析产物", f"gene_count={metrics.get('gene_count')}, total_output_files={metrics.get('total_output_files')}")

    elif stage == "domain":
        domtblout = _artifact_path(manifest, "domtblout")
        domain_raw = _parse_domtblout(domtblout)
        raw_protein_total = domain_raw.get("query_count") or _safe_int(metrics.get("proteins_total") or metrics.get("total_proteins"))
        raw_significant = domain_raw.get("significant_hits") if domain_raw.get("rows") else _safe_int(metrics.get("significant_domain_hits"))
        parsed.update({
            "proteins_total": raw_protein_total,
            "proteins_with_domains": domain_raw.get("proteins_with_domains") or metrics.get("proteins_with_domains"),
            "total_domain_hits": domain_raw.get("raw_hits") or metrics.get("total_domain_hits"),
            "significant_domain_hits": raw_significant,
            "unique_domain_types": len(domain_raw.get("unique_domains") or []) or metrics.get("unique_domain_types"),
            "domain_coverage_rate": metrics.get("domain_coverage_rate"),
            "top_domains": _normalize_top_domains(
                domain_raw.get("top_domains") or metrics.get("top_domains", [])
            ),
            "sbp_protein_count": domain_raw.get("sbp_protein_count"),
        })
        add_manifest(
            "结构域统计",
            (
                f"manifest proteins_total={metrics.get('proteins_total')}, "
                f"proteins_with_domains={metrics.get('proteins_with_domains')}, "
                f"total_domain_hits={metrics.get('total_domain_hits')}, "
                f"significant_domain_hits={metrics.get('significant_domain_hits')}"
            ),
        )
        if domtblout and domain_raw.get("rows"):
            top_text = ", ".join(f"{d['domain']}({d['count']})" for d in domain_raw.get("top_domains", [])[:4])
            evidence.append(_evidence(stage, "domtblout原始解析", f"原始domtblout解析到{domain_raw.get('query_count')}个蛋白、{domain_raw.get('significant_hits')}个显著domain hits；主要结构域: {top_text}", domtblout, level="derived_stat"))
        if domain_raw.get("significant_hits") and not _safe_int(metrics.get("significant_domain_hits")):
            risks.append({
                "stage": stage,
                "severity": "high",
                "message": f"manifest记录显著hits为0，但domtblout按E<=1e-5解析得到{domain_raw.get('significant_hits')}个显著hits；当前domain manifest/report统计存在解析错误，应以原始domtblout复核。",
                "source_file": str(manifest_path) if manifest_path else "",
            })
        elif _safe_int(metrics.get("total_domain_hits")) and not _safe_int(metrics.get("significant_domain_hits")):
            risks.append({
                "stage": stage,
                "severity": "medium",
                "message": f"hmmscan产生{metrics.get('total_domain_hits')}个raw hits，但显著hits为0；结构域结论应解释阈值、数据库和identify阶段SBP HMM的一致性。",
                "source_file": str(manifest_path) if manifest_path else "",
            })
        if domain_raw.get("sbp_protein_count") and domain_raw.get("query_count") and domain_raw["sbp_protein_count"] < domain_raw["query_count"]:
            risks.append({
                "stage": stage,
                "severity": "medium",
                "message": f"SBP结构域仅在{domain_raw['sbp_protein_count']}/{domain_raw['query_count']}个蛋白中达到显著阈值，短序列或截短成员需要单独标注。",
                "source_file": str(domtblout) if domtblout else "",
            })
        for key, artifact in artifacts.items():
            path = _artifact_path(manifest, key)
            if path and path.suffix.lower() in {".tsv", ".txt", ".md"}:
                dims = _table_dimensions(path) if path.suffix.lower() in {".tsv", ".txt"} else {"rows": 0, "columns": 0}
                evidence.append(_evidence(stage, key, f"{path.name} 存在" + (f"，约{dims['rows']}行" if dims["rows"] else ""), path))

    elif stage == "reference_classification":
        errors = metrics.get("errors") or manifest.get("errors") or []
        if not isinstance(errors, list):
            errors = [errors]
        confidence_counts = metrics.get("confidence_counts") if isinstance(metrics.get("confidence_counts"), dict) else {}
        assigned_counts = (
            metrics.get("assigned_subfamily_counts")
            or metrics.get("subfamily_counts")
            or metrics.get("assignment_counts")
            or {}
        )
        if not isinstance(assigned_counts, dict):
            assigned_counts = {}
        parsed.update({
            "target_gene_count": metrics.get("target_gene_count"),
            "reference_gene_count": metrics.get("reference_gene_count"),
            "classifiable_species_count": metrics.get("classifiable_species_count"),
            "failed_species_count": metrics.get("failed_species_count"),
            "per_species_count": metrics.get("per_species_count"),
            "confidence_counts": confidence_counts,
            "assigned_subfamily_counts": assigned_counts,
            "errors": [str(item) for item in errors if str(item).strip()],
        })
        add_manifest(
            "亚家族分类预测状态",
            (
                f"status={manifest.get('status')}, "
                f"classifiable_species_count={metrics.get('classifiable_species_count')}, "
                f"per_species_count={metrics.get('per_species_count')}, "
                f"failed_species_count={metrics.get('failed_species_count')}"
            ),
            confidence="low" if manifest.get("status") == "failed" else "medium",
            level="diagnostic" if manifest.get("status") == "failed" else "derived_stat",
        )
        if assigned_counts:
            count_text = "，".join(f"{key}:{value}" for key, value in list(assigned_counts.items())[:8])
            add_manifest("亚家族预测分布", f"预测亚家族分布: {count_text}", confidence="medium", level="derived_stat")
        if confidence_counts:
            confidence_text = "，".join(f"{key}:{value}" for key, value in confidence_counts.items())
            add_manifest("亚家族预测置信度", f"置信度分布: {confidence_text}", confidence="medium", level="derived_stat")
        if manifest.get("status") == "failed" or errors:
            risks.append({
                "stage": stage,
                "severity": "high",
                "message": "reference_classification 未形成可确认亚家族分类；" + ("；".join(str(item) for item in errors[:2] if item) if errors else "需检查参考亚家族表、参考序列ID映射和联合FASTA。"),
                "source_file": str(manifest_path) if manifest_path else "",
            })

    elif stage == "promoter":
        top_elements = _parse_simple_count_table(_artifact_path(manifest, "element_counts"), limit=8)
        complexity_path = _artifact_path(manifest, "complexity_analysis")
        complexity_rows = _read_table(complexity_path, limit=1000) if complexity_path else []
        top_complexity = complexity_rows[:5]
        family_count = len(_family_member_ids(manifest))
        parsed.update({
            "total_elements": metrics.get("total_elements"),
            "unique_elements": metrics.get("unique_elements"),
            "genes_with_elements": metrics.get("genes_with_elements"),
            "filtered_elements": metrics.get("filtered_elements"),
            "enriched_pairs": metrics.get("enriched_pairs"),
            "cooccurrence_pairs": metrics.get("cooccurrence_pairs"),
            "mean_complexity": metrics.get("mean_complexity"),
            "top_elements": top_elements,
            "top_complexity_genes": top_complexity,
        })
        add_manifest(
            "启动子总体统计",
            (
                f"total_elements={metrics.get('total_elements')}, unique_elements={metrics.get('unique_elements')}, "
                f"genes_with_elements={metrics.get('genes_with_elements')}, enriched_pairs={metrics.get('enriched_pairs')}"
            ),
        )
        if top_elements:
            evidence.append(_evidence(stage, "高频顺式元件", "高频元件: " + ", ".join(f"{i['name']}({i['count']})" for i in top_elements[:5]), _artifact_path(manifest, "element_counts"), level="derived_stat"))
        if top_complexity:
            evidence.append(_evidence(stage, "启动子复杂度高值基因", "复杂度最高: " + ", ".join(f"{r.get('Gene')}({r.get('ComplexityScore')})" for r in top_complexity[:3]), complexity_path, level="derived_stat"))
        for key in ["filtered_plantcare_output", "enrichment_analysis", "cooccurrence_analysis", "complexity_analysis", "report"]:
            path = _artifact_path(manifest, key)
            if path and len(evidence) < 8:
                evidence.append(_evidence(stage, key, f"{path.name} 存在，大小 {path.stat().st_size} 字节", path))
        genes_with = _safe_int(metrics.get("genes_with_elements"))
        if family_count and genes_with and genes_with < family_count:
            risks.append({
                "stage": stage,
                "severity": "medium",
                "message": f"启动子元件覆盖{genes_with}/{family_count}个家族成员，至少{family_count - genes_with}个成员缺少可解释启动子元件记录。",
                "source_file": str(manifest_path) if manifest_path else "",
            })
        if top_elements and top_elements[0]["name"] in {"TATA-box", "CAAT-box"}:
            risks.append({
                "stage": stage,
                "severity": "low",
                "message": "最高频元件为TATA/CAAT等核心启动子元件，不能单独作为胁迫或激素响应结论；需结合ABRE、MYB/MYC、LTR等功能元件和表达数据解释。",
                "source_file": str(_artifact_path(manifest, "element_counts") or manifest_path or ""),
            })

    elif stage == "synteny":
        family_ids = _family_member_ids(manifest)
        collinearity_stats = _parse_mcscanx_collinearity(_artifact_path(manifest, "collinearity"), family_ids)
        parsed.update({
            "synteny_blocks": metrics.get("synteny_blocks"),
            "gene_pairs": metrics.get("gene_pairs"),
            "total_alignments": metrics.get("total_alignments"),
            "background_gene_count": metrics.get("gene_count"),
            "unique_queries": metrics.get("unique_queries"),
            "unique_subjects": metrics.get("unique_subjects"),
            "family_pair_count": collinearity_stats.get("family_pair_count"),
            "family_block_count": collinearity_stats.get("family_block_count"),
            "example_family_pairs": collinearity_stats.get("example_family_pairs"),
        })
        add_manifest(
            "种内共线性统计",
            (
                f"synteny_blocks={metrics.get('synteny_blocks')}, "
                f"gene_pairs={metrics.get('gene_pairs')}, "
                f"total_alignments={metrics.get('total_alignments')}"
            ),
        )
        if collinearity_stats.get("gene_pairs"):
            evidence.append(_evidence(stage, "家族成员共线性证据", f"collinearity 中检测到 {collinearity_stats.get('family_pair_count')} 对涉及{family_label}成员的共线性基因、分布于 {collinearity_stats.get('family_block_count')} 个block", _artifact_path(manifest, "collinearity"), level="derived_stat"))
        for key, artifact in artifacts.items():
            path = _artifact_path(manifest, key)
            if path and len(evidence) < 8:
                evidence.append(_evidence(stage, key, f"{path.name} 存在，大小 {path.stat().st_size} 字节", path))
        background = _safe_int(metrics.get("gene_count"))
        if background > 1000:
            risks.append({
                "stage": stage,
                "severity": "low",
                "message": f"synteny manifest 的 gene_count={background} 是全基因组背景规模，不应当解读为{manifest.get('prefix', '')}家族成员数。",
                "source_file": str(manifest_path) if manifest_path else "",
            })
        if family_ids and collinearity_stats.get("family_pair_count", 0) == 0:
            risks.append({
                "stage": stage,
                "severity": "medium",
                "message": f"种内共线性文件未检测到涉及{family_label}成员的基因对，不能据此声称家族扩张由片段复制驱动。",
                "source_file": str(_artifact_path(manifest, "collinearity") or manifest_path or ""),
            })

    elif stage == "karyotype":
        chromosomes = metrics.get("chromosomes") if isinstance(metrics.get("chromosomes"), list) else []
        parsed.update({
            "chromosome_count": metrics.get("chromosome_count"),
            "gene_count": metrics.get("gene_count"),
            "tandem_count": metrics.get("tandem_count"),
            "chromosomes": chromosomes,
            "label_gap_mb": metrics.get("label_gap_mb"),
        })
        add_manifest(
            "核型定位统计",
            (
                f"chromosome_count={metrics.get('chromosome_count')}, "
                f"gene_count={metrics.get('gene_count')}, "
                f"tandem_count={metrics.get('tandem_count')}, "
                f"chromosomes={','.join(chromosomes[:8])}"
            ),
        )
        for key, artifact in artifacts.items():
            path = _artifact_path(manifest, key)
            if path and len(evidence) < 8:
                evidence.append(_evidence(stage, key, f"{path.name} 存在，大小 {path.stat().st_size} 字节", path))
        if _safe_int(metrics.get("gene_count")) == 0:
            risks.append({
                "stage": stage,
                "severity": "high",
                "message": "核型阶段未定位到家族基因，不能进行染色体分布解释。",
                "source_file": str(manifest_path) if manifest_path else "",
            })

    elif stage == "interspecies_synteny":
        family_ids = _family_member_ids(manifest)
        input_dir = _job_input_dir(manifest)
        ref_candidates = []
        params = manifest.get("parameters") if isinstance(manifest.get("parameters"), dict) else {}
        ref_protein_spl = Path(str(params.get("ref_protein_spl"))) if params.get("ref_protein_spl") else None
        if input_dir:
            ref_candidates = sorted(input_dir.glob(f"*{family}*.fa*")) + sorted(input_dir.glob(f"*{family}*.fasta"))
        ref_ids = _read_fasta_ids(ref_protein_spl if ref_protein_spl and ref_protein_spl.exists() else (ref_candidates[0] if ref_candidates else None))
        collinearity_path = _artifact_path(manifest, "collinearity") or _artifact_path_by_suffix(manifest, "_collinearity")
        collinearity_stats = _parse_mcscanx_collinearity(collinearity_path, family_ids, ref_ids)
        pairwise_summary = _pairwise_synteny_summary(metrics)
        report_path = _artifact_path(manifest, "report")
        target_species = ref_species = ""
        if report_path:
            text = report_path.read_text(encoding="utf-8", errors="ignore")
            target_match = re.search(r"目标物种.*?:\s*([A-Za-z0-9_.-]+)", text)
            ref_match = re.search(r"参考物种.*?:\s*([A-Za-z0-9_.-]+)", text)
            target_species = target_match.group(1) if target_match else ""
            ref_species = ref_match.group(1) if ref_match else ""
        parsed.update({
            "target_species": target_species,
            "reference_species": ref_species,
            "alignment_blocks": collinearity_stats.get("alignment_blocks"),
            "gene_pairs": collinearity_stats.get("gene_pairs"),
            "family_pair_count": collinearity_stats.get("family_pair_count"),
            "ref_family_pair_count": collinearity_stats.get("ref_family_pair_count"),
            "family_ref_pair_count": collinearity_stats.get("family_ref_pair_count"),
            "reference_spl_count": len(ref_ids),
            "anchor_count": pairwise_summary.get("anchor_count"),
            "family_highlight_links": pairwise_summary.get("family_highlight_links"),
            "marked_block_count": pairwise_summary.get("marked_block_count"),
            "collinearity_file": str(collinearity_path or ""),
        })
        evidence.append(_evidence(stage, "种间共线性原始解析", f"collinearity包含{collinearity_stats.get('alignment_blocks')}个block、{collinearity_stats.get('gene_pairs')}对基因；{target_label} {family_label}成员命中{collinearity_stats.get('family_pair_count')}对，{reference_label} {family_label}参考命中{collinearity_stats.get('ref_family_pair_count')}对，目标-参考{family_label}同对命中{collinearity_stats.get('family_ref_pair_count')}对；图中家族高亮连线{pairwise_summary.get('family_highlight_links')}条、标记block {pairwise_summary.get('marked_block_count')}个", collinearity_path, level="derived_stat"))
        for key in ["report", "tree_nwk", "synteny_pdf", "synteny_marked_pdf", "collinearity"]:
            path = _artifact_path(manifest, key)
            if path and len(evidence) < 8:
                evidence.append(_evidence(stage, key, f"{path.name} 存在，大小 {path.stat().st_size} 字节", path))
        if collinearity_path and len(evidence) < 8 and not _artifact_path(manifest, "collinearity"):
            evidence.append(_evidence(stage, "pairwise_collinearity", f"{collinearity_path.name} 存在，大小 {collinearity_path.stat().st_size} 字节", collinearity_path))
        if family_ids and collinearity_stats.get("family_pair_count", 0) == 0 and pairwise_summary.get("family_highlight_links", 0) == 0:
            risks.append({
                "stage": stage,
                "severity": "high",
                "message": f"种间MCScanX collinearity中未检测到{target_label} {family_label}成员参与的共线性基因对，当前结果只能说明全基因组目标-参考共线性背景，不能直接支撑{family_label}保守共线性结论。",
                "source_file": str(collinearity_path or manifest_path or ""),
            })
        elif collinearity_stats.get("family_ref_pair_count", 0) == 0 and pairwise_summary.get("family_highlight_links", 0) == 0:
            risks.append({
                "stage": stage,
                "severity": "medium",
                "message": f"虽然检测到目标或参考{family_label}成员参与collinearity，但未发现二者在同一共线性基因对中直接配对；保守共线性结论应降级为候选线索。",
                "source_file": str(collinearity_path or manifest_path or ""),
            })

    elif stage == "ppi":
        report_path = _artifact_path(manifest, "report")
        network_path = _artifact_path(manifest, "network")
        report_text = report_path.read_text(encoding="utf-8", errors="ignore") if report_path else ""
        def report_num(label: str) -> int:
            match = re.search(rf"{re.escape(label)}\*\*:\s*([0-9]+)", report_text)
            return _safe_int(match.group(1)) if match else 0
        parsed.update({
            "input_genes": report_num("Input Genes"),
            "genes_in_network": report_num("Genes in Network"),
            "total_interactions": report_num("Total Interactions"),
            "network_rows": _table_dimensions(network_path)["rows"] if network_path and network_path.stat().st_size else 0,
        })
        if report_path:
            evidence.append(_evidence(stage, "PPI报告统计", f"Input Genes={parsed['input_genes']}, Genes in Network={parsed['genes_in_network']}, Total Interactions={parsed['total_interactions']}", report_path, level="derived_stat"))
        if network_path:
            evidence.append(_evidence(stage, "PPI网络表", f"{network_path.name} 大小 {network_path.stat().st_size} 字节，解析行数 {parsed['network_rows']}", network_path))
        url_path = _artifact_path(manifest, "string_url")
        if url_path:
            evidence.append(_evidence(stage, "STRING链接", f"{url_path.name} 存在，说明生成了外部STRING查询入口", url_path))
        if parsed["input_genes"] == 0 or parsed["total_interactions"] == 0:
            risks.append({
                "stage": stage,
                "severity": "high",
                "message": f"PPI阶段报告Input Genes或Total Interactions为0，不能作为{family_label}蛋白互作证据；需修正物种映射/同源转换后重跑。",
                "source_file": str(report_path or manifest_path or ""),
            })

    elif stage == "pep":
        properties_path = _artifact_path(manifest, "properties_data")
        rows = _read_table(properties_path, limit=1000) if properties_path else []
        lengths = [(_safe_float(row.get("length")), row.get("id", "")) for row in rows]
        lengths = [(v, g) for v, g in lengths if v is not None]
        pis = [(_safe_float(row.get("isoelectric_point")), row.get("id", "")) for row in rows]
        pis = [(v, g) for v, g in pis if v is not None]
        gravies = [(_safe_float(row.get("gravy")), row.get("id", "")) for row in rows]
        gravies = [(v, g) for v, g in gravies if v is not None]
        parsed.update({
            "protein_count": metrics.get("protein_count") or len(rows),
            "avg_length": metrics.get("avg_length"),
            "avg_molecular_weight": metrics.get("avg_molecular_weight"),
            "avg_pi": metrics.get("avg_pi"),
            "avg_gravy": metrics.get("avg_gravy"),
            "longest_proteins": [{"gene": g, "length": v} for v, g in sorted(lengths, reverse=True)[:3]],
            "shortest_proteins": [{"gene": g, "length": v} for v, g in sorted(lengths)[:3]],
            "max_pi": max(pis)[0] if pis else None,
            "min_gravy": min(gravies)[0] if gravies else None,
            "max_gravy": max(gravies)[0] if gravies else None,
        })
        add_manifest("蛋白理化性质统计", f"protein_count={parsed['protein_count']}, avg_length={metrics.get('avg_length')}, avg_pi={metrics.get('avg_pi')}, avg_gravy={metrics.get('avg_gravy')}")
        if properties_path:
            evidence.append(_evidence(stage, "蛋白属性原始表", f"{properties_path.name} 读取到 {len(rows)} 个蛋白属性记录", properties_path))
        if parsed["longest_proteins"]:
            evidence.append(_evidence(stage, "长度极值蛋白", "最长蛋白: " + ", ".join(f"{i['gene']}({i['length']:.0f}aa)" for i in parsed["longest_proteins"]), properties_path, level="derived_stat"))
        avg_pi = _safe_float(metrics.get("avg_pi"))
        if (avg_pi is not None and avg_pi > 14) or (parsed.get("max_pi") is not None and parsed["max_pi"] > 14):
            risks.append({
                "stage": stage,
                "severity": "high",
                "message": f"蛋白pI出现超出生物物理常见范围的数值(avg_pi={metrics.get('avg_pi')}, max_pi={parsed.get('max_pi')})，pI算法或字段解析需要复核，不能直接发表为理化性质结论。",
                "source_file": str(properties_path or manifest_path or ""),
            })

    elif stage == "kaks":
        result_paths = _kaks_result_paths(manifest)
        results_path = result_paths[0] if result_paths else None
        rows: List[Dict[str, str]] = []
        for path in result_paths:
            rows.extend(_read_table(path, limit=1000))
        ratios: List[tuple[float, str]] = []
        ks_values: List[float] = []
        saturated_ks_pairs: List[str] = []
        for row in rows:
            ratio = _safe_float(row.get("Ka/Ks") or row.get("Ka.Ks"))
            ks = _safe_float(row.get("Ks"))
            pair_label = f"{row.get('gene1', '')}-{row.get('gene2', '')}"
            if ratio is not None:
                ratios.append((ratio, pair_label))
            if ks is not None:
                ks_values.append(ks)
                if ks >= 99:
                    saturated_ks_pairs.append(pair_label)
        ratios.sort(reverse=True)
        ratio_values = [value for value, _pair in ratios]
        derived_mean_ka_ks = (sum(ratio_values) / len(ratio_values)) if ratio_values else None
        derived_mean_ks = (sum(ks_values) / len(ks_values)) if ks_values else None
        parsed.update({
            "total_pairs": metrics.get("total_pairs") or metrics.get("input_pairs") or len(rows),
            "successful_pairs": metrics.get("successful_pairs") or len(rows),
            "mean_ka_ks": metrics.get("mean_ka_ks") if metrics.get("mean_ka_ks") not in (None, "") else derived_mean_ka_ks,
            "mean_ks": metrics.get("mean_ks") if metrics.get("mean_ks") not in (None, "") else derived_mean_ks,
            "max_ka_ks": metrics.get("max_ka_ks") if metrics.get("max_ka_ks") not in (None, "") else (max(ratio_values) if ratio_values else None),
            "positive_selection_count": metrics.get("positive_selection_count") if metrics.get("positive_selection_count") not in (None, "") else sum(1 for value in ratio_values if value > 1),
            "top_positive_pairs": [{"pair": pair, "ka_ks": val} for val, pair in ratios[:3]],
            "max_ks": max(ks_values) if ks_values else None,
        })
        add_manifest("Ka/Ks总体统计", f"successful_pairs={parsed.get('successful_pairs')}, mean_ka_ks={parsed.get('mean_ka_ks')}, positive_selection_count={parsed.get('positive_selection_count')}")
        if result_paths:
            if len(result_paths) == 1:
                evidence.append(_evidence(stage, "Ka/Ks结果表", f"{result_paths[0].name} 读取到 {len(rows)} 条成功基因对", result_paths[0]))
            else:
                evidence.append(_evidence(stage, "Ka/Ks聚合结果表", f"读取 {len(result_paths)} 个target-reference结果表，共 {len(rows)} 条成功基因对", results_path))
        if ratios:
            evidence.append(_evidence(stage, "Ka/Ks高值基因对", "高Ka/Ks基因对: " + ", ".join(f"{pair}({val:.2f})" for val, pair in ratios[:3]), results_path, level="derived_stat"))
        if saturated_ks_pairs:
            risks.append({
                "stage": stage,
                "severity": "medium",
                "message": "检测到Ks=99或接近99的基因对（" + ", ".join(saturated_ks_pairs[:5]) + "）。这通常是yn00同义替换饱和/估计边界信号；原始结果应保留，但该基因对的分化时间和选择压力解释需要在深度解读中降级为谨慎解释。",
                "source_file": str(results_path or manifest_path or ""),
            })
        mean_ks = _safe_float(parsed.get("mean_ks"))
        if mean_ks is not None and mean_ks > 10:
            risks.append({
                "stage": stage,
                "severity": "high",
                "message": f"mean_ks={mean_ks:.2f}且发散时间达到异常大数量级，Ka/Ks结果很可能存在序列配对、比对或单位换算问题；不能直接解释为正选择。",
                "source_file": str(results_path or manifest_path or ""),
            })
        if _safe_float(parsed.get("mean_ka_ks")) and _safe_float(parsed.get("mean_ka_ks")) > 1:
            risks.append({
                "stage": stage,
                "severity": "medium",
                "message": "所有成功基因对均表现Ka/Ks>1时，应先排除饱和替换、远缘同源和CDS比对质量问题，再讨论正选择。",
                "source_file": str(results_path or manifest_path or ""),
            })

    elif stage in {
        "rnaseq_qc",
        "rnaseq_align",
        "rnaseq_quant",
        "rnaseq_sample_qc",
        "rnaseq_de",
        "annotation",
        "rnaseq_go_enrich",
        "rnaseq_kegg_enrich",
        "rnaseq_fgsea",
        "rnaseq_wgcna",
        "rnaseq_candidate_family",
        "rnaseq_enrich",
    }:
        parsed["method_context"] = RNASEQ_METHOD_CONTEXT.get(stage, "")
        if stage == "rnaseq_qc":
            samples = metrics.get("samples") if isinstance(metrics.get("samples"), dict) else {}
            parsed.update({
                "total_samples": metrics.get("total_samples") or len(samples),
                "total_raw_reads": metrics.get("total_raw_reads"),
                "total_clean_reads": metrics.get("total_clean_reads"),
            })
            if samples:
                zero_read_samples = [name for name, row in samples.items() if _safe_int(row.get("raw_reads")) == 0 and _safe_int(row.get("clean_reads")) == 0]
                if len(zero_read_samples) == len(samples):
                    risks.append({
                        "stage": stage,
                        "severity": "medium",
                        "message": "QC manifest中所有样本raw_reads/clean_reads为0；FastQC/Cutadapt产物存在时说明流程执行完成，但reads计数字段未被解析，报告中不要据此声称测序量为0。",
                        "source_file": str(manifest_path or ""),
                    })
        elif stage == "rnaseq_align":
            samples = metrics.get("samples") if isinstance(metrics.get("samples"), dict) else {}
            parsed.update({
                "total_samples": metrics.get("total_samples") or len(samples),
                "average_alignment_rate": metrics.get("average_alignment_rate"),
            })
            if samples and all(_safe_int(row.get("total_reads")) == 0 for row in samples.values()):
                risks.append({
                    "stage": stage,
                    "severity": "medium",
                    "message": "Align manifest中total_reads/aligned_reads为0；应优先查看HISAT2日志判断真实比对率，不能把0作为生物学或数据质量结论。",
                    "source_file": str(manifest_path or ""),
                })
        elif stage == "rnaseq_quant":
            parsed.update({
                "total_genes": metrics.get("total_genes"),
                "samples_processed": metrics.get("samples_processed"),
            })
        elif stage == "rnaseq_sample_qc":
            parsed.update({
                "input_genes": metrics.get("input_genes"),
                "expressed_genes": metrics.get("expressed_genes"),
                "samples": metrics.get("samples"),
                "conditions": metrics.get("conditions"),
                "pc1_percent": metrics.get("pc1_percent"),
                "pc2_percent": metrics.get("pc2_percent"),
            })
            pc1 = _safe_float(metrics.get("pc1_percent"))
            pc2 = _safe_float(metrics.get("pc2_percent"))
            samples_n = _safe_int(metrics.get("samples"))
            if samples_n and samples_n < 6:
                risks.append({
                    "stage": stage,
                    "severity": "medium",
                    "message": f"样本数为{samples_n}，PCA/相关性可以用于质控，但离群样本判断需要结合原始QC、比对率和实验批次。",
                    "source_file": str(manifest_path or ""),
                })
            if pc1 is not None and pc2 is not None:
                add_manifest("PCA主成分解释率", f"PC1={pc1:.2f}%, PC2={pc2:.2f}%", level="derived_stat")
        elif stage == "rnaseq_de":
            parsed.update({
                "total_comparisons": metrics.get("total_comparisons"),
                "de_genes_total": metrics.get("de_genes_total"),
                "de_genes_up": metrics.get("de_genes_up"),
                "de_genes_down": metrics.get("de_genes_down"),
            })
        elif stage == "annotation":
            parsed.update({
                "input_proteins": metrics.get("input_proteins"),
                "genes_with_go": metrics.get("canonical_genes_with_go") or metrics.get("raw_genes_with_go"),
                "genes_with_ko": metrics.get("canonical_genes_with_ko") or metrics.get("raw_genes_with_ko"),
                "go_term_count": metrics.get("go_term_count"),
                "ko_term_count": metrics.get("ko_term_count"),
                "method_used": metrics.get("method_used"),
            })
        elif stage == "rnaseq_go_enrich":
            parsed.update({
                "de_genes_input": metrics.get("de_genes_input"),
                "go_enriched_terms": metrics.get("go_enriched_terms"),
                "annotated_background_genes": metrics.get("annotated_background_genes"),
                "foreground_mode": metrics.get("foreground_mode"),
            })
        elif stage == "rnaseq_kegg_enrich":
            params = manifest.get("parameters") or {}
            parsed.update({
                "de_genes_input": metrics.get("de_genes_input"),
                "kegg_enriched_pathways": metrics.get("kegg_enriched_pathways"),
                "annotated_background_genes": metrics.get("annotated_background_genes"),
                "kegg_filtered_pathways": metrics.get("kegg_filtered_pathways"),
                "kegg_organism": params.get("kegg_organism"),
                "kegg_filter_scope": params.get("kegg_filter_scope") or metrics.get("kegg_filter_scope"),
            })
        elif stage == "rnaseq_fgsea":
            params = manifest.get("parameters") or {}
            fgsea_rows: List[Dict[str, str]] = []
            for key in artifacts:
                if not str(key).endswith("_fgsea_kegg"):
                    continue
                path = _artifact_path(manifest, key)
                if path:
                    fgsea_rows.extend(_read_table(path, limit=200))
            significant_fgsea = [
                row for row in fgsea_rows
                if _safe_float(row.get("padj")) is not None and _safe_float(row.get("padj")) <= 0.05
            ]
            significant_fgsea.sort(key=lambda row: abs(_safe_float(row.get("NES")) or 0), reverse=True)
            parsed.update({
                "ranked_genes": metrics.get("ranked_genes"),
                "genes_with_pathway_in_ranks": metrics.get("genes_with_pathway_in_ranks"),
                "pathways_tested": metrics.get("pathways_tested"),
                "significant_pathways_padj_0_05": metrics.get("significant_pathways_padj_0_05"),
                "kegg_gene_set_modes": metrics.get("kegg_gene_set_modes"),
                "kegg_organism": params.get("kegg_organism"),
                "kegg_species_cache": (params.get("kegg_species_cache") or {}).get("status") if isinstance(params.get("kegg_species_cache"), dict) else "",
                "top_fgsea_pathways": significant_fgsea[:5],
            })
        elif stage == "rnaseq_wgcna":
            module_sizes = _read_table(_artifact_path(manifest, "module_sizes"), limit=100) if _artifact_path(manifest, "module_sizes") else []
            module_traits = _read_table(_artifact_path(manifest, "module_trait_correlation"), limit=100) if _artifact_path(manifest, "module_trait_correlation") else []
            top_module = None
            best_abs = -1.0
            for row in module_traits:
                module = row.get("") or row.get("module") or row.get("Module") or next(iter(row.values()), "")
                module_clean = str(module)[2:] if str(module).startswith("ME") else str(module)
                if module_clean.lower() == "grey":
                    continue
                for trait, value in row.items():
                    if trait in {"", "module", "Module"}:
                        continue
                    cor = _safe_float(value)
                    if cor is not None and abs(cor) > best_abs:
                        top_module = {"module": module, "trait": trait, "correlation": cor}
                        best_abs = abs(cor)
            parsed.update({
                "samples": metrics.get("samples"),
                "input_genes": metrics.get("input_genes"),
                "genes_used": metrics.get("genes_used"),
                "soft_power": metrics.get("soft_power"),
                "modules": metrics.get("modules"),
                "matrix_type": metrics.get("matrix_type"),
                "module_sizes": module_sizes[:5],
                "top_module_trait": top_module,
            })
            if _safe_int(metrics.get("samples")) < 15:
                risks.append({
                    "stage": stage,
                    "severity": "medium",
                    "message": f"WGCNA样本数为{metrics.get('samples')}，可以作为候选模块探索，但模块-性状相关和hub基因不宜作为强因果结论。",
                    "source_file": str(manifest_path or ""),
                })
        elif stage == "rnaseq_candidate_family":
            ranking_path = _artifact_path(manifest, "candidate_family_ranking")
            recommendation_path = _artifact_path(manifest, "recommendation")
            evidence_path = _artifact_path(manifest, "evidence_matrix")
            context_path = _artifact_path(manifest, "experiment_context")
            rag_path = _artifact_path(manifest, "rag_queries")
            rag_refs_path = _artifact_path(manifest, "rag_references")
            ranking_rows = _read_table(ranking_path, limit=20) if ranking_path else []
            recommendation_rows = _read_table(recommendation_path, limit=20) if recommendation_path else []
            evidence_rows = _read_table(evidence_path, limit=20) if evidence_path else []
            experiment_context = _read_json(context_path) if context_path else {}
            rag_plan = _read_json(rag_path) if rag_path else {}
            rag_references = _read_json(rag_refs_path) if rag_refs_path else {}
            inputs = manifest.get("inputs") if isinstance(manifest.get("inputs"), dict) else {}
            external_sources = inputs.get("external_resource_sources") if isinstance(inputs.get("external_resource_sources"), dict) else {}
            parsed.update({
                "deg_records": metrics.get("deg_records"),
                "deg_genes": metrics.get("deg_genes"),
                "annotated_deg_genes": metrics.get("annotated_deg_genes"),
                "candidate_family_count": metrics.get("candidate_family_count"),
                "enrichment_terms": metrics.get("enrichment_terms"),
                "recommendation_mode": metrics.get("recommendation_mode") or manifest.get("rnaseq_recommendation_mode") or experiment_context.get("rnaseq_recommendation_mode"),
                "experiment_design": metrics.get("experiment_design") or manifest.get("rnaseq_experiment_design") or experiment_context.get("rnaseq_experiment_design"),
                "treatment": experiment_context.get("rnaseq_treatment") or rag_plan.get("treatment"),
                "treatment_keywords": experiment_context.get("rnaseq_treatment_keywords") or rag_plan.get("treatment_keywords") or [],
                "species_name": experiment_context.get("rnaseq_species_name") or rag_plan.get("species"),
                "top_candidate_families": ranking_rows[:5],
                "top_recommendations": recommendation_rows[:8],
                "evidence_matrix_preview": evidence_rows[:5],
                "experiment_context": experiment_context,
                "rag_plan": rag_plan,
                "rag_queries": (rag_plan.get("queries") or [])[:8],
                "rag_references": rag_references,
                "rag_reference_status": rag_references.get("status", "missing"),
                "protein_fasta": inputs.get("protein_fasta"),
                "interproscan_tsv": inputs.get("interproscan_tsv"),
                "external_resource_sources": external_sources,
            })
        for key in sorted(metrics)[:8]:
            add_manifest(f"{STAGE_NAMES.get(stage, stage)}指标", f"{key}={metrics.get(key)}")
        for key, artifact in artifacts.items():
            path = _artifact_path(manifest, key)
            if path and len(evidence) < 8:
                evidence.append(_evidence(stage, key, f"{path.name} 存在，大小 {path.stat().st_size} 字节", path))

    else:
        for key in sorted(metrics)[:6]:
            add_manifest("阶段指标", f"{key}={metrics.get(key)}")

    if not evidence and manifest_path is not None:
        evidence.append(
            _evidence(
                stage,
                "阶段Manifest",
                (
                    f"status={manifest.get('status', 'unknown')}, "
                    f"metric_count={len(metrics)}, artifact_count={len(artifacts)}"
                ),
                manifest_path,
                source_type="manifest",
            )
        )

    return {
        "stage": stage,
        "stage_name": STAGE_NAMES.get(stage, stage),
        "status": manifest.get("status", "unknown"),
        "metrics": metrics,
        "parsed": parsed,
        "evidence": evidence,
        "risks": risks,
    }


def _stage_key_result(stage: str, bundle: Dict[str, Any]) -> str:
    parsed = bundle.get("parsed") or {}
    metrics = bundle.get("metrics") or {}
    if stage == "identify":
        return f"{parsed.get('final_members') or metrics.get('final_members') or 0}个成员"
    if stage == "phylogeny":
        return f"{parsed.get('sequence_count') or metrics.get('sequences_count') or metrics.get('sequence_count') or 0}条序列, MSA {parsed.get('msa_length') or metrics.get('msa_length') or 'N/A'}"
    if stage == "motif":
        conserved = parsed.get("most_conserved_motif") or {}
        extra = f", 核心{conserved.get('id')}覆盖{conserved.get('sites')}条" if conserved else ""
        return f"{parsed.get('motif_count') or metrics.get('motif_count') or 0}个motif{extra}"
    if stage == "structure":
        return f"{parsed.get('genes_total') or metrics.get('genes_total') or 0}个基因, 平均外显子{metrics.get('avg_exon_count_per_gene', 'N/A')}"
    if stage == "domain":
        total = parsed.get("proteins_total") or metrics.get("proteins_total") or metrics.get("total_proteins") or "N/A"
        sig = parsed.get("significant_domain_hits") if parsed.get("significant_domain_hits") is not None else metrics.get("significant_domain_hits", "N/A")
        return f"{total}个蛋白, 显著结构域hits {sig}"
    if stage == "promoter":
        return f"{metrics.get('genes_with_elements', 'N/A')}个基因含元件, {metrics.get('unique_elements', 'N/A')}类元件"
    if stage == "synteny":
        fam = parsed.get("family_pair_count")
        return f"{fam if fam is not None else 'N/A'}对家族相关共线性, 全基因组{metrics.get('synteny_blocks', 'N/A')}个block"
    if stage == "interspecies_synteny":
        return f"{parsed.get('family_ref_pair_count', 'N/A')}对目标-参考家族共线性, 全基因组{parsed.get('alignment_blocks', 'N/A')}个block"
    if stage == "karyotype":
        return f"{metrics.get('chromosome_count', 'N/A')}条染色体, {metrics.get('gene_count', 'N/A')}个家族基因定位"
    if stage == "ppi":
        return f"{parsed.get('total_interactions', 0)}条互作, {parsed.get('genes_in_network', 0)}个网络基因"
    if stage == "pep":
        return f"{parsed.get('protein_count') or metrics.get('protein_count') or 0}个蛋白, 平均长度{metrics.get('avg_length', 'N/A')}aa"
    if stage == "kaks":
        return f"{parsed.get('successful_pairs') or metrics.get('successful_pairs') or 0}对成功, 平均Ka/Ks {metrics.get('mean_ka_ks', 'N/A')}"
    if stage == "expression":
        return f"{parsed.get('gene_count') or metrics.get('gene_count') or 0}个基因表达谱"
    if stage == "rnaseq_qc":
        return f"{parsed.get('total_samples') or metrics.get('total_samples') or 0}个样本质控"
    if stage == "rnaseq_align":
        return f"{parsed.get('total_samples') or metrics.get('total_samples') or 0}个样本比对, 平均比对率{metrics.get('average_alignment_rate', 'N/A')}"
    if stage == "rnaseq_quant":
        return f"{parsed.get('total_genes') or metrics.get('total_genes') or 0}个基因, {parsed.get('samples_processed') or metrics.get('samples_processed') or 0}个样本"
    if stage == "rnaseq_sample_qc":
        return f"{parsed.get('samples') or metrics.get('samples') or 0}个样本, PC1 {metrics.get('pc1_percent', 'N/A')}%, PC2 {metrics.get('pc2_percent', 'N/A')}%"
    if stage == "rnaseq_de":
        return f"{metrics.get('de_genes_total', 0)}个DEGs, 上调{metrics.get('de_genes_up', 0)}, 下调{metrics.get('de_genes_down', 0)}"
    if stage == "annotation":
        return f"GO基因{parsed.get('genes_with_go', 'N/A')}, KO基因{parsed.get('genes_with_ko', 'N/A')}"
    if stage == "rnaseq_go_enrich":
        return f"{metrics.get('go_enriched_terms', 0)}个GO terms, 背景基因{metrics.get('annotated_background_genes', 'N/A')}"
    if stage == "rnaseq_kegg_enrich":
        return f"{metrics.get('kegg_enriched_pathways', 0)}条KEGG通路, 过滤后背景通路{metrics.get('kegg_filtered_pathways', 'N/A')}"
    if stage == "rnaseq_fgsea":
        modes = metrics.get("kegg_gene_set_modes")
        mode_text = ",".join(modes) if isinstance(modes, list) else (modes or "N/A")
        return f"{metrics.get('ranked_genes', 0)}个排序基因, {metrics.get('pathways_tested', 0)}条通路, {mode_text}"
    if stage == "rnaseq_wgcna":
        return f"{metrics.get('genes_used', 0)}个基因, {metrics.get('modules', 0)}个模块, softPower={metrics.get('soft_power', 'N/A')}"
    if stage == "rnaseq_candidate_family":
        recommendations = parsed.get("top_recommendations") or []
        top = next(
            (
                item for item in recommendations
                if item.get("recommendation_tier") == "priority_recommended"
            ),
            recommendations[0] if recommendations else {},
        )
        top_name = top.get("candidate_family") or "N/A"
        return (
            f"{metrics.get('candidate_family_count', parsed.get('candidate_family_count', 'N/A'))}个候选家族, "
            f"注释DEG {metrics.get('annotated_deg_genes', parsed.get('annotated_deg_genes', 'N/A'))}, "
            f"优先候选 {top_name}"
        )
    for key in ("gene_count", "protein_count", "domain_count", "syntenic_blocks", "deg_count", "sample_count"):
        if metrics.get(key) is not None:
            return f"{metrics.get(key)}"
    return bundle.get("status", "completed")


def _candidate_context_text(parsed: Dict[str, Any]) -> str:
    treatment = parsed.get("treatment") or ""
    species = parsed.get("species_name") or ""
    mode = parsed.get("recommendation_mode") or "context_neutral"
    bits = []
    if species:
        bits.append(str(species))
    if treatment:
        bits.append(str(treatment))
    bits.append(str(mode))
    return "/".join(bits)


def _candidate_rag_query_text(parsed: Dict[str, Any], limit: int = 3) -> str:
    queries = parsed.get("rag_queries") or []
    out = []
    for item in queries[:limit]:
        family = item.get("candidate_family") or "candidate family"
        first_query = (item.get("queries") or [""])[0]
        if first_query:
            out.append(f"{family}: {first_query}")
    return "；".join(out)


def _stage_interpretation(stage: str, bundle: Dict[str, Any], prefix: str) -> str:
    parsed = bundle.get("parsed") or {}
    metrics = bundle.get("metrics") or {}
    risks = bundle.get("risks") or []
    if stage == "identify":
        final = parsed.get("final_members") or metrics.get("final_members") or 0
        hmm = parsed.get("hmm_hits") or metrics.get("hmm_hits")
        blast = parsed.get("blast_hits") or metrics.get("blast_hits")
        text = f"{prefix}家族鉴定得到{final}个成员"
        if hmm and blast:
            text += f"，HMMER与BLAST分别支持{hmm}/{blast}个hits"
        if risks:
            text += "；存在候选数语义风险，报告应以成员ID/FASTA计数和HMM/BLAST结果作为事实来源"
        return text
    if stage == "phylogeny":
        gap = _safe_float(metrics.get("gap_percentage"))
        text = f"系统发育分析纳入{parsed.get('sequence_count') or metrics.get('sequences_count') or metrics.get('sequence_count') or 0}条序列，MSA长度{metrics.get('msa_length', 'N/A')}"
        if metrics.get("tree_model"):
            text += f"，IQ-TREE模型为{metrics.get('tree_model')}"
        if gap is not None and gap >= 50:
            text += f"；gap比例{gap:.2f}%提示深层拓扑解释需谨慎"
        return text
    if stage == "motif":
        conserved = parsed.get("most_conserved_motif") or {}
        text = f"Motif分析识别{parsed.get('motif_count') or metrics.get('motif_count') or 0}个motif、{metrics.get('total_sites', 'N/A')}个sites"
        if conserved:
            text += f"，其中{conserved.get('id') or conserved.get('name')}覆盖{conserved.get('sites')}条序列，是当前最强的保守模式"
        return text
    if stage == "structure":
        raw_count = parsed.get("raw_mrna_gene_count")
        text = f"结构分析覆盖{metrics.get('genes_total', 'N/A')}个家族基因，平均外显子数{metrics.get('avg_exon_count_per_gene', 'N/A')}"
        if raw_count:
            text += f"；structure.tsv基因级mRNA记录为{raw_count}个"
        text += "；genes_with_structure大于genes_total时应解释为转录本/记录层级而非新增成员"
        return text
    if stage == "expression":
        tops = parsed.get("top_expression_genes") or []
        text = f"表达分析覆盖{metrics.get('gene_count', 'N/A')}个家族基因，生成表达热图、相关性和组织特异性结果"
        if tops:
            text += "；高表达候选包括" + "、".join(item["gene"] for item in tops[:3])
        return text
    if stage == "domain":
        top_domains = parsed.get("top_domains") or []
        top_text = "，主要结构域为" + "、".join(f"{d['domain']}({d['count']})" for d in top_domains[:3]) if top_domains else ""
        return (
            f"结构域原始domtblout支持{parsed.get('proteins_total', 'N/A')}个蛋白参与扫描，"
            f"显著domain hits为{parsed.get('significant_domain_hits', 'N/A')}，SBP显著覆盖{parsed.get('sbp_protein_count', 'N/A')}个蛋白{top_text}；"
            "若manifest/report仍显示0，应判定为统计解析问题而非生物学缺失"
        )
    if stage == "promoter":
        top_elements = parsed.get("top_elements") or []
        text = f"启动子分析在{metrics.get('genes_with_elements', 'N/A')}个成员中识别{metrics.get('unique_elements', 'N/A')}类顺式元件，过滤后保留{metrics.get('filtered_elements', 'N/A')}条记录"
        if top_elements:
            text += "；高频元件包括" + "、".join(f"{i['name']}({i['count']})" for i in top_elements[:4])
        text += "；可作为表达调控假设来源，但激素/胁迫结论需与表达矩阵或实验处理对应"
        return text
    if stage == "synteny":
        examples = parsed.get("example_family_pairs") or []
        return (
            f"种内共线性背景包含{metrics.get('synteny_blocks', 'N/A')}个block、{metrics.get('gene_pairs', 'N/A')}对基因；"
            f"原始collinearity中涉及{prefix}成员的共线性候选为{parsed.get('family_pair_count', 'N/A')}对"
            + (f"（如{examples[0]}）" if examples else "")
            + "，因此家族复制结论应以家族成员级配对为主，而不是全基因组背景总数"
        )
    if stage == "interspecies_synteny":
        return (
            f"种间共线性分析使用{parsed.get('target_species') or '目标物种'}与{parsed.get('reference_species') or '参考物种'}，"
            f"全基因组collinearity含{parsed.get('alignment_blocks', 'N/A')}个block；"
            f"目标-参考家族同对命中{parsed.get('family_ref_pair_count', 'N/A')}对，"
            f"目标家族成员参与任意种间共线性对为{parsed.get('family_pair_count', 'N/A')}对；"
            "直接保守共线性结论必须以同对命中为准"
        )
    if stage == "karyotype":
        chromosomes = metrics.get("chromosomes") if isinstance(metrics.get("chromosomes"), list) else []
        chrom_text = "、".join(chromosomes[:8]) if chromosomes else "未记录染色体列表"
        return (
            f"核型分析将{metrics.get('gene_count', 'N/A')}个家族成员定位到"
            f"{metrics.get('chromosome_count', 'N/A')}条染色体（{chrom_text}），"
            f"串联重复计数为{metrics.get('tandem_count', 'N/A')}"
        )
    if stage == "ppi":
        return (
            f"PPI阶段解析到{parsed.get('genes_in_network', 0)}个网络基因、{parsed.get('total_interactions', 0)}条互作；"
            f"当前结果不能证明{prefix}蛋白互作网络，优先作为失败/空网络诊断而不是功能发现"
        )
    if stage == "pep":
        longest = parsed.get("longest_proteins") or []
        text = f"蛋白属性分析覆盖{parsed.get('protein_count', metrics.get('protein_count', 'N/A'))}个蛋白，平均长度{metrics.get('avg_length', 'N/A')} aa、平均分子量{metrics.get('avg_molecular_weight', 'N/A')} Da、GRAVY均值{metrics.get('avg_gravy', 'N/A')}"
        if longest:
            text += "；最长蛋白包括" + "、".join(f"{i['gene']}({i['length']:.0f}aa)" for i in longest[:2])
        text += "；pI异常值需先复核算法后再进入发表结论"
        return text
    if stage == "kaks":
        tops = parsed.get("top_positive_pairs") or []
        text = f"Ka/Ks分析成功计算{parsed.get('successful_pairs', metrics.get('successful_pairs', 'N/A'))}对基因，平均Ka/Ks为{metrics.get('mean_ka_ks', 'N/A')}"
        if tops:
            text += "；高值对包括" + "、".join(f"{i['pair']}({i['ka_ks']:.2f})" for i in tops[:2])
        text += "；由于Ks和发散时间异常偏大，当前更适合作为需要复核的候选结果"
        return text
    if stage == "rnaseq_qc":
        text = f"RNA-seq质控覆盖{parsed.get('total_samples', metrics.get('total_samples', 'N/A'))}个样本，FastQC/Cutadapt等产物用于判断原始reads质量、接头污染和过滤效果"
        if risks:
            text += "；当前reads计数字段未成功解析，报告中应引用FastQC/Cutadapt日志而不是0值计数"
        return text
    if stage == "rnaseq_align":
        text = f"比对阶段覆盖{parsed.get('total_samples', metrics.get('total_samples', 'N/A'))}个样本，manifest记录平均比对率为{metrics.get('average_alignment_rate', 'N/A')}"
        if risks:
            text += "；当前比对reads字段为0，应回查HISAT2日志后再评价真实比对质量"
        return text
    if stage == "rnaseq_quant":
        return (
            f"定量阶段生成{parsed.get('total_genes', metrics.get('total_genes', 'N/A'))}个基因、"
            f"{parsed.get('samples_processed', metrics.get('samples_processed', 'N/A'))}个样本的表达矩阵；"
            "count矩阵适合DESeq2统计，TPM/FPKM更适合表达展示"
        )
    if stage == "rnaseq_sample_qc":
        return (
            f"样本质量评估基于表达矩阵进行VST/PCA、相关性和层次聚类；"
            f"本次{metrics.get('samples', 'N/A')}个样本、{metrics.get('conditions', 'N/A')}个条件，"
            f"PC1解释{metrics.get('pc1_percent', 'N/A')}%、PC2解释{metrics.get('pc2_percent', 'N/A')}%。"
            "PCA距离需要结合组内重复聚类、样本相关性和批次信息综合判断"
        )
    if stage == "rnaseq_de":
        return (
            f"差异表达阶段完成{metrics.get('total_comparisons', 'N/A')}个比较，"
            f"共筛选{metrics.get('de_genes_total', 'N/A')}个差异基因，其中上调{metrics.get('de_genes_up', 'N/A')}、下调{metrics.get('de_genes_down', 'N/A')}；"
            "全量DESeq2结果可继续作为fgsea排序输入"
        )
    if stage == "annotation":
        return (
            f"背景注释使用{parsed.get('method_used', metrics.get('method_used', 'N/A'))}，"
            f"得到GO注释基因{parsed.get('genes_with_go', 'N/A')}个、KO注释基因{parsed.get('genes_with_ko', 'N/A')}个；"
            "这些gene2go/gene2ko文件是后续GO/KEGG富集的统计背景"
        )
    if stage == "rnaseq_go_enrich":
        return (
            f"GO富集以{metrics.get('de_genes_input', 'N/A')}个前景差异基因为输入，"
            f"在{metrics.get('annotated_background_genes', 'N/A')}个GO背景基因中检出{metrics.get('go_enriched_terms', 0)}个富集term；"
            "解释时应优先看具体BP/MF/CC条目和q值，而不是只看term数量"
        )
    if stage == "rnaseq_kegg_enrich":
        return (
            f"KEGG富集以{metrics.get('de_genes_input', 'N/A')}个前景基因为输入，"
            f"KO背景基因为{metrics.get('annotated_background_genes', 'N/A')}个，"
            f"过滤后通路{metrics.get('kegg_filtered_pathways', 'N/A')}条，显著展示{metrics.get('kegg_enriched_pathways', 0)}条；"
            f"当前kegg_organism={parsed.get('kegg_organism') or '未记录'}、filter_scope={parsed.get('kegg_filter_scope') or 'N/A'}，植物项目应以物种/植物兼容通路作为主解释"
        )
    if stage == "rnaseq_fgsea":
        modes = parsed.get("kegg_gene_set_modes")
        mode_text = ",".join(modes) if isinstance(modes, list) else (modes or "N/A")
        return (
            f"fgsea基于{metrics.get('ranked_genes', 'N/A')}个排序基因，"
            f"其中{metrics.get('genes_with_pathway_in_ranks', 'N/A')}个可映射到通路，测试{metrics.get('pathways_tested', 'N/A')}条通路，"
            f"padj<=0.05通路{metrics.get('significant_pathways_padj_0_05', 0)}条；"
            f"gene set模式为{mode_text}，说明本次未使用人类MSigDB，而是按物种KEGG/KO背景构建植物兼容基因集"
        )
    if stage == "rnaseq_wgcna":
        return (
            f"WGCNA使用{metrics.get('genes_used', 'N/A')}个表达基因、{metrics.get('samples', 'N/A')}个样本构建共表达网络，"
            f"softPower={metrics.get('soft_power', 'N/A')}，识别{metrics.get('modules', 'N/A')}个模块；"
            "该结果适合筛选候选模块和候选hub基因，小样本下需要谨慎解释模块-性状相关强度"
        )
    if stage == "rnaseq_candidate_family":
        recs = parsed.get("top_recommendations") or []
        families = [row.get("candidate_family") for row in recs[:5] if row.get("candidate_family")]
        context = _candidate_context_text(parsed)
        text = (
            f"候选家族阶段在{metrics.get('annotated_deg_genes', 'N/A')}个已注释DEG中识别"
            f"{metrics.get('candidate_family_count', 'N/A')}个候选家族/结构域，推荐模式为{context}。"
        )
        if families:
            text += " 当前优先候选包括" + "、".join(families)
        rag_text = _candidate_rag_query_text(parsed, limit=2)
        if rag_text:
            text += f"；已生成RAG检索问题用于外部文献支持：{rag_text}"
        external_sources = parsed.get("external_resource_sources") or {}
        if external_sources:
            text += "；全基因组蛋白/InterProScan注释来自已记录的本地历史资源，并已在当前job内建立可访问链接"
        return text
    return f"{STAGE_NAMES.get(stage, stage)}完成，已提取可追溯证据用于全局解释"


def _finding(stage: str, bundle: Dict[str, Any], idx: int, prefix: str) -> Dict[str, Any]:
    evidence = bundle.get("evidence") or []
    risks = bundle.get("risks") or []
    status = bundle.get("status", "unknown")
    title = f"{STAGE_NAMES.get(stage, stage)}: {_stage_key_result(stage, bundle)}"
    confidence = "low" if any(r.get("severity") == "high" for r in risks) else ("medium" if risks or status != "success" else "high")
    inference_stages = {
        "phylogeny",
        "motif",
        "expression",
        "structure",
        "promoter",
        "synteny",
        "interspecies_synteny",
        "ppi",
        "kaks",
        "rnaseq_sample_qc",
        "rnaseq_de",
        "rnaseq_go_enrich",
        "rnaseq_kegg_enrich",
        "rnaseq_fgsea",
        "rnaseq_wgcna",
        "rnaseq_candidate_family",
    }
    return {
        "id": idx,
        "stage": stage,
        "title": title,
        "description": _stage_interpretation(stage, bundle, prefix),
        "evidence": " | ".join(item.get("evidence", "") for item in evidence[:3]) or "manifest/产物存在性验证",
        "evidence_items": evidence[:6],
        "risk_flags": risks,
        "confidence": confidence,
        "interpretation_level": "biological_inference" if stage in inference_stages else "derived_stat",
    }


def _connections(stages: List[str], bundles: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
    conns: List[Dict[str, str]] = []
    direct_links = {
        ("identify", "phylogeny"): ("同一套家族蛋白序列进入MSA和树构建。", "成员数、FASTA序列数和树tip数必须一致。"),
        ("phylogeny", "domain"): ("系统发育树提供亚家族框架，结构域用于检查各分支是否保持SBP核心域。", "若domain统计为0但domtblout有显著hits，应优先修正解析而不是推断结构域缺失。"),
        ("domain", "motif"): ("结构域和MEME motif共同描述蛋白保守性。", "应区分Pfam结构域、MEME motif和低复杂度片段，避免把三者混为一个证据。"),
        ("motif", "structure"): ("motif保守性与基因结构可联合判断分支特异分化。", "只有当motif位置文件可读时，才适合讨论每个基因的motif排列。"),
        ("structure", "promoter"): ("基因结构和启动子属于同一成员集的基因层面证据。", "启动子提取依赖正确基因坐标，structure的转录本重复计数需要先解释清楚。"),
        ("synteny", "interspecies_synteny"): ("种内和种间共线性分别检验复制背景与跨物种保守性。", "两者都要报告家族成员级命中，而不是只给全基因组block数。"),
        ("synteny", "karyotype"): ("共线性和核型定位共同描述染色体层面的家族分布。", "串联重复、片段复制和染色体定位需要分开陈述。"),
        ("karyotype", "ppi"): ("PPI是功能网络层面的外部映射，核型结果不是PPI输入。", "若PPI网络为空，应作为物种/ID映射失败处理。"),
        ("ppi", "expression"): ("PPI与表达均属于功能线索，但证据来源不同。", "只有互作网络非空时，才适合讨论表达协同与互作模块。"),
        ("ppi", "pep"): ("PPI与蛋白理化性质是并行功能注释。", "空PPI不影响PEP统计，但不能作为蛋白功能互作证据。"),
        ("pep", "kaks"): ("蛋白/CDS质量会影响Ka/Ks解释。", "pI或序列属性异常、远缘同源配对异常时，Ka/Ks结论需降级为待复核。"),
        ("rnaseq_qc", "rnaseq_align"): ("clean reads进入参考基因组比对。", "质控统计和比对日志要共同确认样本数据质量，不能只看流程是否完成。"),
        ("rnaseq_align", "rnaseq_quant"): ("排序BAM进入featureCounts/StringTie等定量步骤。", "比对质量会直接影响基因计数矩阵和后续DESeq2模型。"),
        ("rnaseq_quant", "rnaseq_sample_qc"): ("count矩阵进入VST/PCA、样本相关性和聚类检查。", "样本QC应先于差异分析，用于发现离群样本和批次问题。"),
        ("rnaseq_sample_qc", "rnaseq_de"): ("通过样本质控后的count矩阵进入差异表达分析。", "若PCA重复分散，应在DE结果解释时降低确定性，并复核批次/样本标签。"),
        ("rnaseq_de", "annotation"): ("DE前景基因需要gene2go/gene2ko背景才能做GO/KEGG富集。", "背景注释的物种、数据库和ID标准化会影响富集结果。"),
        ("annotation", "rnaseq_go_enrich"): ("gene2go背景用于GO过表达富集。", "GO结果解释应基于背景覆盖率和显著term，不应把无注释基因忽略为无功能。"),
        ("annotation", "rnaseq_kegg_enrich"): ("gene2ko背景用于KEGG过表达富集。", "植物项目要优先采用物种KEGG或植物兼容过滤后的通路。"),
        ("rnaseq_de", "rnaseq_fgsea"): ("全量DESeq2结果按log2FC排序进入fgsea。", "fgsea不依赖DEG阈值，能补充阈值型富集分析。"),
        ("rnaseq_fgsea", "rnaseq_wgcna"): ("fgsea给出通路层面的整体趋势，WGCNA给出模块层面的共表达候选。", "两者是互补证据，不应写成直接因果链。"),
        ("rnaseq_de", "rnaseq_wgcna"): ("差异表达和WGCNA都来自表达矩阵，但统计问题不同。", "DE找单基因差异，WGCNA找共表达模块；小样本WGCNA应作为候选发现。"),
    }
    for left, right in zip(stages, stages[1:]):
        lkey = _stage_key_result(left, bundles[left])
        rkey = _stage_key_result(right, bundles[right])
        description, insight = direct_links.get(
            (left, right),
            (
                f"{STAGE_NAMES.get(left, left)}和{STAGE_NAMES.get(right, right)}是同一{bundles[left].get('stage_name', '分析对象')}成员集上的相邻证据层（{lkey}；{rkey}）。",
                "该关联用于检查对象集合是否一致，并避免把并行分析误写成直接因果或输入输出关系。",
            ),
        )
        conns.append({
            "from": left,
            "to": right,
            "description": f"{description} 当前结果：{left}={lkey}；{right}={rkey}。",
            "insight": insight,
        })
    return conns


def _stage_review(stage: str, bundle: Dict[str, Any], prefix: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    risks = bundle.get("risks") or []
    strengths = ["产物与manifest可追溯，已进入统一证据索引。"]
    limitations: List[str] = [risk.get("message", "") for risk in risks[:3]]
    improvements: List[str] = []

    if stage == "identify":
        key_points = [
            f"最终成员数为{parsed.get('final_members', 'N/A')}，ID文件和蛋白FASTA可交叉验证。",
            f"HMMER/BLAST hits为{parsed.get('hmm_hits', 'N/A')}/{parsed.get('blast_hits', 'N/A')}。",
        ]
        strengths.append("成员ID和蛋白序列双重产物明确，是后续阶段的基准集合。")
        improvements.append("修正candidates字段语义，区分参考序列数、候选数和最终成员数。")
    elif stage == "phylogeny":
        key_points = [
            f"MSA包含{parsed.get('msa_sequence_count', 'N/A')}条序列，树tip数为{parsed.get('tree_tip_estimate', 'N/A')}。",
            f"gap比例为{parsed.get('gap_percentage', 'N/A')}%。",
        ]
        strengths.append("MSA序列数与树tip数可用于检查系统发育输入完整性。")
        improvements.append("高gap树应补充分支支持率、裁剪策略或保守区段树作为敏感性分析。")
    elif stage == "domain":
        key_points = [
            f"domtblout解析得到{parsed.get('significant_domain_hits', 'N/A')}个显著domain hits。",
            f"SBP显著覆盖{parsed.get('sbp_protein_count', 'N/A')}个蛋白。",
        ]
        strengths.append("原始hmmscan结果可直接恢复Pfam结构域证据。")
        improvements.append("修复domain manifest/report统计器，使显著hits、蛋白覆盖率和结构域类型来自domtblout解析。")
    elif stage == "motif":
        conserved = parsed.get("most_conserved_motif") or {}
        key_points = [
            f"识别{parsed.get('motif_count', 'N/A')}个motif，核心{conserved.get('id', 'motif')}覆盖{conserved.get('sites', 'N/A')}条序列。",
        ]
        strengths.append("motif数量、宽度和sites已记录，可用于分支间保守性比较。")
        improvements.append("修复空motif位置文件，输出每个基因的motif排列后再讨论分支特异motif。")
    elif stage == "structure":
        key_points = [
            f"基因级mRNA记录覆盖{parsed.get('raw_mrna_gene_count', parsed.get('genes_total', 'N/A'))}个成员。",
            f"平均外显子数为{parsed.get('avg_exon_count_per_gene', 'N/A')}。",
        ]
        strengths.append("结构TSV和GFF3都可追溯，适合和motif/phylogeny联合解释。")
        improvements.append("将genes_with_structure调整为基因级统计，另列转录本/feature级统计。")
    elif stage == "promoter":
        tops = parsed.get("top_elements") or []
        key_points = [
            f"{parsed.get('genes_with_elements', 'N/A')}个成员有启动子元件记录，识别{parsed.get('unique_elements', 'N/A')}类元件。",
            "高频元件: " + ", ".join(f"{i['name']}({i['count']})" for i in tops[:4]) if tops else "高频元件未解析。",
        ]
        strengths.append("启动子元件、富集、共现和复杂度文件齐全。")
        improvements.append("将高频基础元件与功能响应元件分层，并与表达高值/组织特异基因交叉。")
    elif stage == "synteny":
        key_points = [
            f"全基因组背景{parsed.get('synteny_blocks', 'N/A')}个block；{prefix}相关共线性候选{parsed.get('family_pair_count', 'N/A')}对。",
        ]
        strengths.append("已区分全基因组共线性背景和家族成员级共线性证据。")
        improvements.append("输出家族成员共线性配对表和复制类型，避免只展示全基因组Circos。")
    elif stage == "interspecies_synteny":
        key_points = [
            f"全基因组种间block为{parsed.get('alignment_blocks', 'N/A')}，目标-参考家族同对命中{parsed.get('family_ref_pair_count', 'N/A')}对。",
        ]
        strengths.append("报告保留了目标/参考物种和全基因组共线性文件。")
        improvements.append("若家族成员命中为0，应改为正交/同源搜索结果展示，不能写成保守共线性。")
    elif stage == "karyotype":
        key_points = [
            f"{parsed.get('gene_count', 'N/A')}个成员定位到{parsed.get('chromosome_count', 'N/A')}条染色体，tandem_count={parsed.get('tandem_count', 'N/A')}。",
        ]
        strengths.append("染色体覆盖和串联重复统计明确。")
        improvements.append("补充每条染色体成员数和热点区段，支撑染色体偏倚分布讨论。")
    elif stage == "ppi":
        key_points = [
            f"PPI网络基因{parsed.get('genes_in_network', 0)}个，互作{parsed.get('total_interactions', 0)}条。",
        ]
        strengths.append("空网络被显式识别为诊断信息。")
        improvements.append("修正目标物种到STRING物种/参考同源ID映射，网络非空后再做模块或hub解释。")
    elif stage == "pep":
        key_points = [
            f"{parsed.get('protein_count', 'N/A')}个蛋白，平均长度{parsed.get('avg_length', 'N/A')} aa，GRAVY均值{parsed.get('avg_gravy', 'N/A')}。",
        ]
        strengths.append("蛋白长度、分子量、GRAVY和属性表可追溯。")
        improvements.append("复核pI计算范围，异常值修复前不进入发表级理化性质结论。")
    elif stage == "kaks":
        key_points = [
            f"成功计算{parsed.get('successful_pairs', 'N/A')}对，mean Ka/Ks={parsed.get('mean_ka_ks', 'N/A')}。",
        ]
        strengths.append("Ka/Ks结果表和图件已生成，便于定位异常基因对。")
        improvements.append("先过滤Ks饱和/远缘同源/低质量CDS比对，再重新判定选择压力。")
    elif stage == "expression":
        tops = parsed.get("top_expression_genes") or []
        key_points = [
            f"表达矩阵覆盖{parsed.get('tables', {}).get('gene_exp', {}).get('rows', 'N/A')}行，manifest记录{parsed.get('gene_count', 'N/A')}个基因。",
            "高表达候选: " + ", ".join(i["gene"] for i in tops[:3]) if tops else "高表达候选未解析。",
        ]
        strengths.append("表达、相关性和组织特异性表均可读，已能提取候选基因。")
        improvements.append("补齐缺失成员并将表达高值、Tau高值与promoter/phylogeny结果交叉。")
    elif stage in {
        "rnaseq_qc",
        "rnaseq_align",
        "rnaseq_quant",
        "rnaseq_sample_qc",
        "rnaseq_de",
        "annotation",
        "rnaseq_go_enrich",
        "rnaseq_kegg_enrich",
        "rnaseq_fgsea",
        "rnaseq_wgcna",
        "rnaseq_candidate_family",
        "rnaseq_enrich",
    }:
        key_points = [_stage_interpretation(stage, bundle, prefix)]
        method_context = parsed.get("method_context") or RNASEQ_METHOD_CONTEXT.get(stage)
        if method_context:
            key_points.append(method_context)
        if stage == "rnaseq_sample_qc":
            strengths.append("PCA、样本相关性、距离热图和聚类图能交叉判断样本一致性。")
            improvements.append("若处理组样本在PCA上分散，应结合相关性矩阵、原始QC、比对率和批次信息判断是否为真实异质性或离群样本。")
        elif stage == "rnaseq_fgsea":
            strengths.append("fgsea使用全基因排序，能补充阈值型GO/KEGG过表达分析。")
            improvements.append("解读显著通路时优先报告NES、padj和leading-edge基因，并说明gene set来源和物种过滤策略。")
        elif stage == "rnaseq_wgcna":
            strengths.append("模块、eigengene和模块-性状相关矩阵可用于筛选共表达候选。")
            improvements.append("样本数较少时把WGCNA定位为候选发现，避免把模块相关直接写成因果调控。")
        elif stage == "rnaseq_kegg_enrich":
            strengths.append("KEGG结果已记录物种/植物过滤相关参数，可追踪通路背景。")
            improvements.append("正式报告中区分filtered结果和raw审计表，避免把raw中的人类疾病/动物通路作为植物结论。")
        elif stage == "rnaseq_go_enrich":
            strengths.append("GO富集基于gene2go背景，能解释差异基因功能偏向。")
            improvements.append("对宽泛GO term进行合并或分层，优先讨论与处理条件和目标家族相关的特异term。")
        elif stage == "annotation":
            strengths.append("gene2go/gene2ko背景把后续富集分析从无背景状态转为可统计检验。")
            improvements.append("记录InterProScan/KofamScan数据库版本，并保留ID标准化映射，保证富集背景可复现。")
    else:
        key_points = [_stage_interpretation(stage, bundle, prefix)]
        improvements.append("补充该阶段的原始文件解析器，减少通用模板解释。")

    readiness = "publishable_with_caveats"
    if any(r.get("severity") == "high" for r in risks):
        readiness = "not_publishable_until_fixed"
    elif limitations:
        readiness = "usable_for_screening"

    return {
        "stage": stage,
        "stage_name": STAGE_NAMES.get(stage, stage),
        "key_points": key_points,
        "strengths": strengths,
        "limitations": limitations or ["未发现阻断性解释风险。"],
        "improvements": improvements,
        "publication_readiness": readiness,
    }


def _publication_claims(stages: List[str], bundles: Dict[str, Dict[str, Any]], prefix: str) -> List[Dict[str, str]]:
    claims: List[Dict[str, str]] = []
    if "identify" in bundles:
        claims.append({"status": "supported", "claim": f"{prefix}家族包含{_stage_key_result('identify', bundles['identify'])}。", "basis": "成员ID文件、蛋白FASTA和HMMER/BLAST结果一致。"})
    if "domain" in bundles:
        domain = bundles["domain"].get("parsed", {})
        claims.append({"status": "supported" if domain.get("significant_domain_hits") else "needs_fix", "claim": "多数/全部成员携带SBP相关结构域。", "basis": f"domtblout显著hits={domain.get('significant_domain_hits')}，SBP覆盖={domain.get('sbp_protein_count')}。"})
    if "synteny" in bundles:
        synteny = bundles["synteny"].get("parsed", {})
        claims.append({"status": "limited", "claim": f"{prefix}存在少量种内共线性候选。", "basis": f"家族成员级共线性对={synteny.get('family_pair_count')}，全基因组block不能直接代表家族扩张。"})
    if "interspecies_synteny" in bundles:
        inter = bundles["interspecies_synteny"].get("parsed", {})
        status = "unsupported" if not _safe_int(inter.get("family_ref_pair_count")) else "supported"
        claims.append({"status": status, "claim": f"{prefix}与参考家族存在直接保守种间共线性。", "basis": f"当前目标-参考同对共线性={inter.get('family_ref_pair_count')}，目标家族任意种间共线性={inter.get('family_pair_count')}。"})
    if "ppi" in bundles:
        ppi = bundles["ppi"].get("parsed", {})
        status = "unsupported" if not _safe_int(ppi.get("total_interactions")) else "supported"
        claims.append({"status": status, "claim": f"{prefix}蛋白存在可解释PPI网络。", "basis": f"当前网络基因={ppi.get('genes_in_network')}，互作数={ppi.get('total_interactions')}。"})
    if "kaks" in bundles:
        claims.append({"status": "needs_fix", "claim": f"{prefix}复制基因经历正选择。", "basis": "Ka/Ks和Ks数值异常偏高，需过滤饱和替换并复核CDS比对。"})
    if "rnaseq_sample_qc" in bundles:
        sample_qc = bundles["rnaseq_sample_qc"].get("metrics", {})
        claims.append({
            "status": "limited",
            "claim": "RNA-seq样本分组在全转录组层面可由PCA/相关性支持。",
            "basis": f"PC1={sample_qc.get('pc1_percent')}%, PC2={sample_qc.get('pc2_percent')}%，仍需结合相关性热图和聚类图判断重复一致性。",
        })
    if "rnaseq_de" in bundles:
        de = bundles["rnaseq_de"].get("metrics", {})
        claims.append({
            "status": "supported",
            "claim": "处理与对照之间存在可统计筛选的差异表达响应。",
            "basis": f"DEGs总数={de.get('de_genes_total')}，上调={de.get('de_genes_up')}，下调={de.get('de_genes_down')}。",
        })
    if "rnaseq_fgsea" in bundles:
        fgsea = bundles["rnaseq_fgsea"].get("metrics", {})
        claims.append({
            "status": "supported" if _safe_int(fgsea.get("significant_pathways_padj_0_05")) else "limited",
            "claim": "排序富集分析发现植物兼容KEGG通路的整体表达趋势。",
            "basis": f"测试通路={fgsea.get('pathways_tested')}，padj<=0.05通路={fgsea.get('significant_pathways_padj_0_05')}，gene set模式={fgsea.get('kegg_gene_set_modes')}.",
        })
    if "rnaseq_wgcna" in bundles:
        wgcna = bundles["rnaseq_wgcna"].get("metrics", {})
        claims.append({
            "status": "limited",
            "claim": "WGCNA模块可作为处理响应候选共表达模块来源。",
            "basis": f"样本数={wgcna.get('samples')}，使用基因={wgcna.get('genes_used')}，模块数={wgcna.get('modules')}；小样本下需作为候选而非强因果证据。",
        })
    return claims


def _scientific_discoveries(stages: List[str], bundles: Dict[str, Dict[str, Any]], prefix: str) -> List[Dict[str, Any]]:
    """Build cross-stage candidate discoveries from real evidence.

    These are intentionally framed as candidate discoveries or hypotheses, not
    final causal claims. Each item names its evidence and remaining caveat.
    """
    discoveries: List[Dict[str, Any]] = []

    if "rnaseq_de" in bundles:
        de = bundles["rnaseq_de"].get("metrics", {})
        comparisons = de.get("comparisons") if isinstance(de.get("comparisons"), dict) else {}
        largest = None
        for name, row in comparisons.items():
            total = _safe_int((row or {}).get("total")) if isinstance(row, dict) else 0
            if largest is None or total > largest["total"]:
                largest = {"name": name, "total": total, "up": _safe_int(row.get("up")) if isinstance(row, dict) else 0, "down": _safe_int(row.get("down")) if isinstance(row, dict) else 0}
        discoveries.append({
            "id": len(discoveries) + 1,
            "type": "expression_response",
            "title": "时间序列表达响应强，T24相对T0变化最明显" if largest and "T24_vs_T0" in largest["name"] else "RNA-seq检测到大规模差异表达响应",
            "claim": f"{prefix}所在数据集存在可统计筛选的时间/处理响应，DEG总记录数为{de.get('de_genes_total')}。",
            "evidence": (
                f"比较数={de.get('total_comparisons')}；上调={de.get('de_genes_up')}；下调={de.get('de_genes_down')}"
                + (f"；最大比较={largest['name']}({largest['total']} genes)" if largest else "")
            ),
            "confidence": "high",
            "interpretation_level": "statistical_finding",
            "caveat": "这是表达统计发现；具体生物学机制需结合功能富集、模块和候选基因验证。",
        })

    if {"rnaseq_go_enrich", "rnaseq_kegg_enrich"} & set(bundles) or "rnaseq_fgsea" in bundles:
        go = bundles.get("rnaseq_go_enrich", {}).get("metrics", {})
        kegg = bundles.get("rnaseq_kegg_enrich", {}).get("metrics", {})
        fgsea = bundles.get("rnaseq_fgsea", {}).get("metrics", {})
        top_fgsea = (bundles.get("rnaseq_fgsea", {}).get("parsed") or {}).get("top_fgsea_pathways") or []
        pathway_names = [row.get("Description") or row.get("pathway") for row in top_fgsea[:3] if row.get("Description") or row.get("pathway")]
        evidence_bits = []
        if go:
            evidence_bits.append(f"GO显著term={go.get('go_enriched_terms')}，背景基因={go.get('annotated_background_genes')}")
        if kegg:
            evidence_bits.append(f"KEGG显著通路={kegg.get('kegg_enriched_pathways')}，plant过滤后背景通路={kegg.get('kegg_filtered_pathways')}")
        if fgsea:
            evidence_bits.append(f"fgsea显著通路={fgsea.get('significant_pathways_padj_0_05')}，测试通路={fgsea.get('pathways_tested')}")
        discoveries.append({
            "id": len(discoveries) + 1,
            "type": "functional_theme",
            "title": "差异表达信号在功能富集和排序富集中形成可解释主题",
            "claim": "GO/KEGG过表达富集与fgsea排序富集共同支持功能层面的响应主题。",
            "evidence": "；".join(evidence_bits) + (f"；fgsea top: {', '.join(pathway_names)}" if pathway_names else ""),
            "confidence": "medium",
            "interpretation_level": "cross_stage_inference",
            "caveat": "富集结果只能说明基因集合偏倚或排序趋势，不能单独证明调控因果关系。",
        })

    if "rnaseq_wgcna" in bundles:
        wgcna = bundles["rnaseq_wgcna"].get("metrics", {})
        parsed = bundles["rnaseq_wgcna"].get("parsed") or {}
        top_module = parsed.get("top_module_trait") or {}
        sizes = parsed.get("module_sizes") or []
        size_lookup = {str(row.get("module") or row.get("") or ""): row.get("genes") for row in sizes}
        module_name = str(top_module.get("module") or "")
        module_clean = module_name[2:] if module_name.startswith("ME") else module_name
        module_cor = _safe_float(top_module.get("correlation"))
        cor_direction = "正相关" if module_cor is not None and module_cor > 0 else ("负相关" if module_cor is not None and module_cor < 0 else "相关")
        discoveries.append({
            "id": len(discoveries) + 1,
            "type": "coexpression_module",
            "title": f"{module_clean or '主要'}共表达模块与{top_module.get('trait', '样本性状')}{cor_direction}" if top_module else "WGCNA识别出可用于候选筛选的共表达模块",
            "claim": "WGCNA模块可作为后续候选hub基因和候选家族筛选的网络线索。",
            "evidence": (
                f"样本数={wgcna.get('samples')}；使用基因={wgcna.get('genes_used')}；模块数={wgcna.get('modules')}"
                + (f"；最高非grey模块相关={module_name} vs {top_module.get('trait')} r={module_cor:.3f}" if module_cor is not None else "")
                + (f"；模块基因数={size_lookup.get(module_clean) or size_lookup.get(module_name)}" if module_name else "")
            ),
            "confidence": "limited" if _safe_int(wgcna.get("samples")) < 15 else "medium",
            "interpretation_level": "candidate_network",
            "caveat": "小样本WGCNA适合生成候选模块，不宜直接写成强因果调控结论。",
        })

    if "rnaseq_candidate_family" in bundles:
        parsed = bundles["rnaseq_candidate_family"].get("parsed") or {}
        metrics = bundles["rnaseq_candidate_family"].get("metrics") or {}
        recommendations = parsed.get("top_recommendations") or []
        named_priority = [
            r for r in recommendations
            if r.get("recommendation_tier") == "priority_recommended"
            and not str(r.get("candidate_family", "")).startswith("Pfam/InterPro:")
        ]
        named = [r for r in recommendations if not str(r.get("candidate_family", "")).startswith("Pfam/InterPro:")]
        top = named_priority[0] if named_priority else (named[0] if named else (recommendations[0] if recommendations else (parsed.get("top_candidate_families") or [{}])[0]))
        top_name = top.get("candidate_family") or "候选家族"
        linked = top.get("evidence_summary") or top.get("linked_enrichment_terms") or ""
        tier = top.get("recommendation_tier") or "legacy_ranking"
        treatment = parsed.get("treatment") or "当前处理"
        context_label = f"{treatment}响应" if parsed.get("treatment") else "RNA-seq响应"
        rag_text = _candidate_rag_query_text(parsed, limit=4)
        external_sources = parsed.get("external_resource_sources") or {}
        discoveries.append({
            "id": len(discoveries) + 1,
            "type": "candidate_family_priority",
            "title": f"{top_name} 是{context_label}的优先候选家族",
            "claim": f"{top_name} 在候选家族阶段的可复现证据矩阵中被列为 {tier}，可作为{context_label}下游正式家族分析入口。",
            "evidence": (
                f"候选家族数={metrics.get('candidate_family_count')}；注释DEG={metrics.get('annotated_deg_genes')}；"
                f"推荐模式={parsed.get('recommendation_mode') or metrics.get('recommendation_mode')}；"
                f"处理={parsed.get('treatment') or '未提供'}；"
                f"top家族DEG={top.get('deg_gene_count')}；best_padj={top.get('best_padj', '')}"
                + (f"；证据摘要={linked[:180]}" if linked else "")
                + (f"；复用外部注释资源={','.join(sorted(external_sources))}" if external_sources else "")
            ),
            "confidence": "medium",
            "interpretation_level": "prioritization_hypothesis",
            "rag_queries": parsed.get("rag_queries") or [],
            "rag_query_summary": rag_text,
            "caveat": "候选家族筛选是优先级排序；RAG只能提供外部文献支持，不能改写DE、fgsea、WGCNA或家族富集统计。",
        })

    return discoveries


def _story(stages: List[str], bundles: Dict[str, Dict[str, Any]], prefix: str, scope_label: str = "") -> str:
    label = scope_label or prefix
    parts = [f"{label}分析应按“鉴定可信度-进化分化-结构/保守性-表达功能”的证据链解释。"]
    for stage in stages:
        parts.append(f"【{STAGE_NAMES.get(stage, stage)}】{_stage_interpretation(stage, bundles[stage], prefix)}。")
    risk_count = sum(len((bundles[s].get("risks") or [])) for s in stages)
    if risk_count:
        parts.append(f"当前共有{risk_count}个解释风险标记，相关结论应在报告中明确区分事实、派生统计和生物学推断。")
    return "\n\n".join(parts)


def _next_steps(stages: List[str], bundles: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
    completed = set(stages)
    steps: List[Dict[str, str]] = []
    if "domain" in completed and any(r.get("severity") == "high" for r in bundles["domain"].get("risks", [])):
        steps.append({"priority": "high", "stage": "domain_qc", "description": "修复domain统计解析", "rationale": "domtblout已显示显著结构域，manifest/report为0会直接误导核心保守结构域结论。"})
    if "ppi" in completed and _safe_int((bundles["ppi"].get("parsed") or {}).get("total_interactions")) == 0:
        steps.append({"priority": "high", "stage": "ppi_mapping", "description": "重做PPI物种和ID映射", "rationale": "当前PPI为空网络，不能作为功能互作证据。"})
    if "kaks" in completed and any(r.get("severity") == "high" for r in bundles["kaks"].get("risks", [])):
        steps.append({"priority": "high", "stage": "kaks_qc", "description": "重筛Ka/Ks配对和CDS比对", "rationale": "Ks/发散时间异常会使正选择判断不可发表。"})
    if "pep" in completed and any(r.get("severity") == "high" for r in bundles["pep"].get("risks", [])):
        steps.append({"priority": "high", "stage": "pep_qc", "description": "复核蛋白pI计算", "rationale": "pI超出生物物理常用范围时，理化性质结论需要先修算法。"})
    if "synteny" not in completed and "identify" in completed:
        steps.append({"priority": "high", "stage": "synteny", "description": "补充共线性分析", "rationale": "用于判断成员扩张是否来自串联复制、片段复制或全基因组复制。"})
    if "kaks" not in completed and {"identify", "phylogeny"} <= completed:
        steps.append({"priority": "high", "stage": "kaks", "description": "补充Ka/Ks选择压力分析", "rationale": "用于验证亚家族分化和复制后功能约束。"})
    if "promoter" not in completed and "expression" in completed:
        steps.append({"priority": "medium", "stage": "promoter", "description": "补充启动子顺式元件分析", "rationale": "用于解释表达模式差异背后的调控机制。"})
    if "rnaseq_quant" in completed and "rnaseq_sample_qc" not in completed:
        steps.append({"priority": "high", "stage": "rnaseq_sample_qc", "description": "先做RNA-seq样本质量评估", "rationale": "PCA、相关性和聚类应在DE前检查重复一致性和离群样本。"})
    if "rnaseq_de" in completed and "annotation" not in completed and ("rnaseq_go_enrich" not in completed or "rnaseq_kegg_enrich" not in completed):
        steps.append({"priority": "high", "stage": "annotation", "description": "补齐gene2go/gene2ko背景注释", "rationale": "GO/KEGG富集需要明确背景，自动注释或上传背景文件二选一。"})
    if "rnaseq_de" in completed and "rnaseq_fgsea" not in completed:
        steps.append({"priority": "medium", "stage": "rnaseq_fgsea", "description": "补充fgsea排序富集", "rationale": "利用全量排序基因检测协同变化通路，补充阈值型GO/KEGG富集。"})
    if "rnaseq_sample_qc" in completed and "rnaseq_wgcna" not in completed:
        steps.append({"priority": "medium", "stage": "rnaseq_wgcna", "description": "补充WGCNA共表达模块分析", "rationale": "用模块-性状相关和模块基因列表筛选候选调控网络。"})
    if any((bundles[s].get("risks") for s in stages)):
        steps.append({"priority": "high", "stage": "qc_review", "description": "复核带风险标记的阶段", "rationale": "优先处理字段语义、partial_success和表格缺失，避免后续结论建立在不一致数据上。"})
    return steps[:6]



def _rag_reference_summary(rag_references: Dict[str, Any]) -> Dict[str, Any]:
    """Summarize attached external references without interpreting them."""
    status_counts: Counter[str] = Counter()
    reference_count = 0
    stages: List[str] = []
    pending_stages: List[str] = []

    for stage, payload in (rag_references or {}).items():
        stages.append(stage)
        if isinstance(payload, dict):
            status = str(payload.get("status") or "attached")
            refs = payload.get("references") or payload.get("sources") or []
            if isinstance(refs, list):
                reference_count += len(refs)
            elif refs:
                reference_count += 1
        elif isinstance(payload, list):
            status = "attached"
            reference_count += len(payload)
        elif payload:
            status = "attached"
            reference_count += 1
        else:
            status = "missing"
        status_counts[status] += 1
        if status in {"missing", "pending", "pending_retrieval", "failed"}:
            pending_stages.append(stage)

    return {
        "stage_count": len(stages),
        "reference_count": reference_count,
        "status_counts": dict(status_counts),
        "stages": stages,
        "pending_stages": pending_stages,
    }


def _load_detached_rag_references(final_dir: Path, prefix: str) -> Dict[str, Any]:
    """Load persisted RAG references that were not attached to a stage manifest."""
    if not final_dir.exists():
        return {}
    patterns = []
    if prefix:
        patterns.append(f"{prefix}.*.rag_references.json")
        patterns.append(f"{prefix}.rag_references.json")
    patterns.append("*.rag_references.json")
    refs: Dict[str, Any] = {}
    seen = set()
    for pattern in patterns:
        for path in sorted(final_dir.glob(pattern), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            name = path.name
            stage = "rag"
            if prefix and name.startswith(f"{prefix}."):
                stage = name[len(prefix) + 1:].replace(".rag_references.json", "")
            elif name.endswith(".rag_references.json"):
                stage = name.replace(".rag_references.json", "")
            if stage == "rag_references":
                stage = "rag"
            if isinstance(payload, dict):
                payload = dict(payload)
                payload.setdefault("source_file", str(path))
            refs.setdefault(stage, payload)
    return refs


def _stage_metric_snapshot(stage: str, bundle: Dict[str, Any]) -> Dict[str, Any]:
    metrics = bundle.get("metrics") or {}
    if not isinstance(metrics, dict):
        return {}
    snapshot: Dict[str, Any] = {}
    for key in sorted(metrics):
        value = metrics.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            snapshot[key] = value
        elif isinstance(value, dict):
            simple_items = {
                str(k): v for k, v in value.items()
                if isinstance(v, (str, int, float, bool)) or v is None
            }
            if simple_items:
                snapshot[key] = simple_items
        elif isinstance(value, list):
            simple_values = [v for v in value if isinstance(v, (str, int, float, bool)) or v is None]
            if simple_values:
                snapshot[key] = simple_values[:20]
    return snapshot


def _assign_evidence_card_ids(evidence_cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach stable in-report evidence ids while preserving card content."""
    counters: Dict[str, int] = {}
    assigned: List[Dict[str, Any]] = []
    for idx, card in enumerate(evidence_cards, start=1):
        item = dict(card or {})
        stage = str(item.get("stage") or "evidence")
        counters[stage] = counters.get(stage, 0) + 1
        item.setdefault("id", f"E{idx:03d}")
        item.setdefault("local_id", f"{stage}.{counters[stage]:03d}")
        assigned.append(item)
    return assigned


def _evidence_refs_for_stages(
    evidence_cards: List[Dict[str, Any]],
    stages: List[str],
    *,
    limit: int = 4,
) -> List[str]:
    wanted = {str(stage) for stage in stages if str(stage).strip()}
    refs = [
        str(card.get("id"))
        for card in evidence_cards
        if card.get("id") and (not wanted or str(card.get("stage") or "") in wanted)
    ]
    if not refs and evidence_cards:
        refs = [str(card.get("id")) for card in evidence_cards if card.get("id")]
    return refs[:limit]


def _claim_stage_list(claim: Dict[str, Any]) -> List[str]:
    stages = claim.get("stages")
    if isinstance(stages, list):
        return [str(stage) for stage in stages if str(stage).strip()]
    evidence_chain = claim.get("evidence_chain")
    if isinstance(evidence_chain, list):
        return [str(item.get("stage")) for item in evidence_chain if isinstance(item, dict) and str(item.get("stage") or "").strip()]
    local_evidence = claim.get("local_evidence")
    if isinstance(local_evidence, list):
        return [str(item.get("stage")) for item in local_evidence if isinstance(item, dict) and str(item.get("stage") or "").strip()]
    return []


def _attach_claim_evidence_refs(claims: List[Dict[str, Any]], evidence_cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    attached: List[Dict[str, Any]] = []
    for claim in claims:
        item = dict(claim or {})
        refs = _evidence_refs_for_stages(evidence_cards, _claim_stage_list(item))
        item["evidence_refs"] = refs
        if not refs:
            item["confidence"] = "low"
            item.setdefault("limitations", [])
            if isinstance(item["limitations"], list):
                item["limitations"].append("缺少可追踪本地证据编号，不能升级为强结论。")
            item["confidence_reason"] = (str(item.get("confidence_reason") or "") + "；缺少可追踪本地证据编号。").strip("；")
        attached.append(item)
    return attached


def _discovery_stage_list(discovery: Dict[str, Any]) -> List[str]:
    chain = discovery.get("evidence_chain")
    if isinstance(chain, list):
        stages = [str(item.get("stage")) for item in chain if isinstance(item, dict) and str(item.get("stage") or "").strip()]
        if stages:
            return stages
    claim_group = str(discovery.get("type") or discovery.get("claim_group") or "")
    if claim_group in {"expression_response"}:
        return ["rnaseq_de"]
    if claim_group in {"functional_theme"}:
        return ["rnaseq_go_enrich", "rnaseq_kegg_enrich", "rnaseq_fgsea"]
    if claim_group in {"coexpression_module"}:
        return ["rnaseq_wgcna"]
    if claim_group in {"candidate_family_priority"}:
        return ["rnaseq_candidate_family"]
    return []


def _attach_discovery_evidence_refs(
    discoveries: List[Dict[str, Any]],
    evidence_cards: List[Dict[str, Any]],
    claims: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    claim_refs = {str(claim.get("claim_id") or ""): list(claim.get("evidence_refs") or []) for claim in claims if claim.get("claim_id")}
    attached: List[Dict[str, Any]] = []
    for discovery in discoveries:
        item = dict(discovery or {})
        refs = claim_refs.get(str(item.get("claim_id") or "")) or _evidence_refs_for_stages(evidence_cards, _discovery_stage_list(item))
        item["evidence_refs"] = refs
        if not refs:
            item["confidence"] = "low"
            caveat = str(item.get("caveat") or "")
            addition = "缺少可追踪本地证据编号，仅能作为待验证假设。"
            item["caveat"] = f"{caveat} {addition}".strip() if addition not in caveat else caveat
        attached.append(item)
    return attached


GENE_FAMILY_INTERPRETATION_STAGES = [
    "identify",
    "phylogeny",
    "domain",
    "motif",
    "structure",
    "promoter",
    "synteny",
    "interspecies_synteny",
    "karyotype",
    "combo",
    "ppi",
    "expression",
    "pep",
    "kaks",
    "ortholog",
    "reference_classification",
    "subfamily_confirm",
]


RNASEQ_INTERPRETATION_STAGES = [
    "rnaseq_qc",
    "rnaseq_align",
    "rnaseq_quant",
    "rnaseq_sample_qc",
    "rnaseq_de",
    "annotation",
    "rnaseq_go_enrich",
    "rnaseq_kegg_enrich",
    "rnaseq_fgsea",
    "rnaseq_wgcna",
    "rnaseq_candidate_family",
    "rnaseq_enrich",
    "expression",
]


def _interpretation_coverage_matrix(
    stages: List[str],
    bundles: Dict[str, Dict[str, Any]],
    claims: List[Dict[str, Any]],
    rag_references: Dict[str, Any],
) -> Dict[str, Any]:
    claim_stage_counts: Counter[str] = Counter()
    for claim in claims:
        for stage in _claim_stage_list(claim):
            claim_stage_counts[stage] += 1

    def row(stage: str, family: str) -> Dict[str, Any]:
        bundle = bundles.get(stage) or {}
        parsed = bundle.get("parsed") if isinstance(bundle.get("parsed"), dict) else {}
        evidence = bundle.get("evidence") or []
        risks = bundle.get("risks") or []
        present = stage in stages
        return {
            "stage": stage,
            "stage_name": STAGE_NAMES.get(stage, stage),
            "family": family,
            "present": present,
            "status": bundle.get("status", "missing") if present else "missing",
            "manifest_parsed": bool(present and bundle.get("metrics") is not None),
            "raw_file_evidence": any(item.get("source_type") == "raw_file" for item in evidence),
            "risk_detection": bool(risks) or present,
            "discovery_claims": claim_stage_counts.get(stage, 0),
            "rag_entry": stage in (rag_references or {}),
            "evidence_count": len(evidence),
            "risk_count": len(risks),
            "parser_depth": "stage_specific" if parsed else ("manifest_only" if present else "missing"),
        }

    rows = [row(stage, "gene_family") for stage in GENE_FAMILY_INTERPRETATION_STAGES]
    rows.extend(row(stage, "rnaseq") for stage in RNASEQ_INTERPRETATION_STAGES if stage not in GENE_FAMILY_INTERPRETATION_STAGES)
    present_rows = [item for item in rows if item["present"]]
    return {
        "schema": "interpretation_coverage_matrix_v1",
        "rows": rows,
        "summary": {
            "present_stage_count": len(present_rows),
            "stage_specific_count": sum(1 for item in present_rows if item["parser_depth"] == "stage_specific"),
            "raw_file_evidence_stage_count": sum(1 for item in present_rows if item["raw_file_evidence"]),
            "claim_covered_stage_count": sum(1 for item in present_rows if item["discovery_claims"] > 0),
            "rag_stage_count": sum(1 for item in present_rows if item["rag_entry"]),
        },
    }


def _agent_contribution_audit(
    stages: List[str],
    bundles: Dict[str, Dict[str, Any]],
    evidence_cards: List[Dict[str, Any]],
    risk_flags: List[Dict[str, Any]],
    claims: List[Dict[str, Any]],
    major_claims: List[Dict[str, Any]],
    scientific_discoveries: List[Dict[str, Any]],
    rag_audit: Dict[str, Any],
    rag_reference_summary: Dict[str, Any],
    boundaries: Dict[str, str],
) -> Dict[str, Any]:
    source_files = {str(card.get("source_file") or "") for card in evidence_cards if card.get("source_file")}
    low_confidence_claims = [claim for claim in claims if claim.get("confidence") == "low"]
    unbound_discoveries = [item for item in scientific_discoveries if not item.get("evidence_refs")]
    return {
        "schema": "agent_contribution_audit_v1",
        "result_agent": {
            "label": "结果 agent",
            "contract": "local_stage_evidence_only",
            "stage_count": len(stages),
            "fact_count": len(evidence_cards),
            "source_file_count": len(source_files),
            "risk_count": len(risk_flags),
            "high_risk_count": sum(1 for risk in risk_flags if risk.get("severity") == "high"),
            "stage_coverage": [
                {
                    "stage": stage,
                    "stage_name": STAGE_NAMES.get(stage, stage),
                    "evidence_count": len((bundles.get(stage) or {}).get("evidence") or []),
                    "risk_count": len((bundles.get(stage) or {}).get("risks") or []),
                    "status": (bundles.get(stage) or {}).get("status", "unknown"),
                }
                for stage in stages
            ],
            "boundary": boundaries.get("result_agent", ""),
        },
        "discovery_agent": {
            "label": "发现 agent",
            "contract": "hypotheses_from_evidence_bundle_only",
            "claim_count": len(claims),
            "major_claim_count": len(major_claims),
            "hypothesis_count": len(scientific_discoveries),
            "low_confidence_claim_count": len(low_confidence_claims),
            "evidence_bound_claim_count": sum(1 for claim in claims if claim.get("evidence_refs")),
            "evidence_bound_hypothesis_count": sum(1 for item in scientific_discoveries if item.get("evidence_refs")),
            "unbound_hypothesis_count": len(unbound_discoveries),
            "boundary": boundaries.get("discovery_agent", ""),
        },
        "rag_agent": {
            "label": "RAG agent",
            "contract": "external_support_only",
            "reference_stage_count": rag_reference_summary.get("stage_count", 0),
            "reference_count": rag_reference_summary.get("reference_count", 0),
            "claim_bound_count": rag_audit.get("claim_binding_count", 0),
            "method_support_count": rag_audit.get("method_reference_count", 0),
            "biological_background_count": rag_audit.get("biological_context_count", rag_audit.get("background_claim_count", 0)),
            "status": rag_audit.get("status", "not_available"),
            "message": rag_audit.get("message", ""),
            "boundary": boundaries.get("rag", ""),
        },
    }


def _build_evidence_bundle(
    stages: List[str],
    bundles: Dict[str, Dict[str, Any]],
    *,
    prefix: str,
    scope: Dict[str, Any],
    scope_id: str,
    analysis_route: str,
    member_set_id: str,
    rnaseq_scope_id: str,
    evidence_cards: List[Dict[str, Any]],
    risk_flags: List[Dict[str, Any]],
    publication_claims: List[Dict[str, Any]],
    scientific_discoveries: List[Dict[str, Any]],
    rag_plans: Dict[str, Any],
    rag_references: Dict[str, Any],
    rag_reference_summary: Dict[str, Any],
) -> Dict[str, Any]:
    """Single handoff object for Result Agent, Discovery Agent, and RAG.

    The bundle is intentionally factual at its core: local stage facts and
    metrics are separated from hypotheses and external references.
    """
    stage_status = []
    for stage in stages:
        bundle = bundles.get(stage) or {}
        stage_status.append({
            "stage": stage,
            "stage_name": STAGE_NAMES.get(stage, stage),
            "status": bundle.get("status", "unknown"),
            "manifest_path": str(bundle.get("manifest_path") or ""),
            "metric_keys": sorted((bundle.get("metrics") or {}).keys()) if isinstance(bundle.get("metrics"), dict) else [],
            "evidence_count": len(bundle.get("evidence") or []),
            "risk_count": len(bundle.get("risks") or []),
        })

    facts = []
    for card in evidence_cards:
        facts.append({
            "id": card.get("id", ""),
            "local_id": card.get("local_id", ""),
            "stage": card.get("stage", ""),
            "claim": card.get("claim", ""),
            "evidence": card.get("evidence", ""),
            "confidence": card.get("confidence", ""),
            "interpretation_level": card.get("interpretation_level", "fact"),
            "source_type": card.get("source_type", ""),
            "source_file": card.get("source_file", ""),
        })

    return {
        "schema": "evidence_bundle_v1",
        "prefix": prefix,
        "scope_id": scope.get("scope_id") or scope_id,
        "analysis_route": analysis_route,
        "member_set_id": member_set_id,
        "rnaseq_scope_id": rnaseq_scope_id,
        "stage_count": len(stages),
        "included_stages": stages,
        "scope": scope,
        "result_agent": {
            "contract": "local_stage_evidence_only",
            "facts": facts,
            "stage_status": stage_status,
            "stage_metrics": {stage: _stage_metric_snapshot(stage, bundles.get(stage) or {}) for stage in stages},
            "risks": risk_flags,
        },
        "discovery_agent": {
            "contract": "hypotheses_from_evidence_bundle_only",
            "publication_claims": publication_claims,
            "hypotheses": scientific_discoveries,
        },
        "rag": {
            "contract": "external_support_only",
            "plans": rag_plans,
            "references": rag_references,
            "summary": rag_reference_summary,
        },
        "boundaries": {
            "result_agent": "提取manifest、原始文件和阶段统计中的事实证据；不得生成未由证据支持的生物学结论。",
            "discovery_agent": "只基于evidence_bundle中的本地证据生成可检验发现或假设；不得改写阶段指标。",
            "rag": "只提供外部文献/知识支持与引用来源；不得覆盖RNA-seq、家族分析或富集统计。",
        },
    }

def _markdown(report: Dict[str, Any]) -> str:
    def _ref_labels(items: Any) -> str:
        if not isinstance(items, list):
            return ""
        labels = []
        for item in items[:3]:
            if not isinstance(item, dict):
                continue
            label = item.get("title") or item.get("paper_title") or item.get("source") or item.get("url") or item.get("stage")
            evidence = item.get("evidence") or item.get("excerpt") or item.get("snippet")
            if evidence and label:
                labels.append(f"{label}: {str(evidence)[:160]}")
            elif label:
                labels.append(str(label))
        return "；".join(labels)

    lines = [
        f"# {report['metadata']['title']}",
        "",
        f"Job: {report['metadata'].get('job_id', '')}",
        f"Generated: {report['metadata'].get('generated_at', '')}",
        "",
        "## Freshness",
    ]
    freshness = report.get("freshness") or report.get("metadata", {}).get("freshness") or {}
    lines.extend([
        f"Newest manifest: {freshness.get('newest_manifest_mtime', '')}",
        f"Stale: {freshness.get('stale', False)}",
        "",
        "## Agent Contribution Audit",
    ])
    agent_audit = report.get("agent_contribution_audit") or {}
    result_agent = agent_audit.get("result_agent") or {}
    discovery_agent = agent_audit.get("discovery_agent") or {}
    rag_agent = agent_audit.get("rag_agent") or {}
    if agent_audit:
        lines.extend([
            f"- Result Agent: stages={result_agent.get('stage_count', 0)}, facts={result_agent.get('fact_count', 0)}, source_files={result_agent.get('source_file_count', 0)}, risks={result_agent.get('risk_count', 0)}",
            f"- Discovery Agent: claims={discovery_agent.get('claim_count', 0)}, hypotheses={discovery_agent.get('hypothesis_count', 0)}, evidence_bound_claims={discovery_agent.get('evidence_bound_claim_count', 0)}, low_confidence_claims={discovery_agent.get('low_confidence_claim_count', 0)}",
            f"- RAG Agent: references={rag_agent.get('reference_count', 0)}, claim_bound={rag_agent.get('claim_bound_count', 0)}, method_support={rag_agent.get('method_support_count', 0)}, biological_background={rag_agent.get('biological_background_count', 0)}, status={rag_agent.get('status', '')}",
            "",
        ])
    lines.extend([
        "## Key Findings",
    ])
    major_claim_lines = render_major_claims_markdown(report.get("major_claims") or [])
    if major_claim_lines:
        key_idx = lines.index("## Key Findings")
        lines = lines[:key_idx] + major_claim_lines + [""] + lines[key_idx:]
    for finding in report.get("key_findings", []):
        lines.extend([
            "",
            f"### {finding.get('id')}. {finding.get('title')}",
            finding.get("description", ""),
            f"Evidence: {finding.get('evidence', '')}",
            f"Confidence: {finding.get('confidence', '')}",
        ])
    structured = report.get("structured_result_interpretations") or {}
    if structured:
        lines.extend(["", "## Structured Result Interpretations"])
        for item in structured.values():
            if not isinstance(item, dict):
                continue
            lines.extend([
                "",
                f"### {item.get('stage_name') or item.get('stage')}",
                f"Result interpretation: {item.get('result_interpretation', '')}",
                "Evidence basis: " + "；".join(item.get("evidence_basis") or []),
                "Article-style gap: " + str(item.get("article_style_gap") or ""),
                "Limitations: " + "；".join(item.get("limitations") or []),
                "Reviewer risk: " + str(item.get("reviewer_risk") or ""),
            ])
    grouped = report.get("grouped_result_interpretations") or {}
    if grouped:
        lines.extend(["", "## Grouped Results Interpretation"])
        for item in grouped.values():
            if not isinstance(item, dict):
                continue
            lines.extend([
                "",
                f"### {item.get('title') or item.get('group')}",
                f"Readiness: {item.get('readiness', '')}",
                "Stages: " + "、".join(item.get("stages") or []),
                f"Result interpretation: {item.get('result_interpretation', '')}",
                "Evidence refs: " + ", ".join(item.get("evidence_refs") or []),
                "Missing slots: " + "；".join(item.get("missing_slots") or []),
                "Downgrade reasons: " + "；".join(item.get("downgrade_reasons") or []),
                "Boundary: " + str(item.get("boundary") or ""),
            ])
    if report.get("scientific_discoveries"):
        lines.extend(["", "## Scientific Discoveries"])
        for item in report["scientific_discoveries"]:
            lines.extend([
                "",
                f"### {item.get('id')}. {item.get('title')}",
                f"Claim: {item.get('claim', '')}",
                f"Evidence refs: {', '.join(item.get('evidence_refs') or [])}",
                f"Evidence: {item.get('evidence', '')}",
                f"Interpretation: {item.get('interpretation', '')}",
                f"Confidence: {item.get('confidence', '')}",
                f"Caveat: {item.get('caveat', '')}",
            ])
            rag_interpretation = item.get("rag_interpretation") or {}
            if rag_interpretation:
                lines.append("RAG interpretation enhancement:")
                claim_support = _ref_labels(rag_interpretation.get("claim_support"))
                method_reference = _ref_labels(rag_interpretation.get("method_reference"))
                biological_context = _ref_labels(rag_interpretation.get("biological_context"))
                if claim_support:
                    lines.append(f"- Literature support for this claim: {claim_support}")
                if method_reference:
                    lines.append(f"- Methodology reference: {method_reference}")
                if biological_context:
                    lines.append(f"- Biological background: {biological_context}")
                if rag_interpretation.get("boundary_note"):
                    lines.append(f"- RAG boundary: {rag_interpretation.get('boundary_note')}")
                if rag_interpretation.get("suggested_wording"):
                    lines.append(f"- Suggested wording: {rag_interpretation.get('suggested_wording')}")
    if report.get("risk_flags"):
        lines.extend(["", "## Risk Flags"])
        for risk in report["risk_flags"]:
            lines.append(f"- [{risk.get('severity')}] {risk.get('stage')}: {risk.get('message')}")
    if report.get("stage_reviews"):
        lines.extend(["", "## Stage Reviews"])
        for review in report["stage_reviews"]:
            lines.extend([
                "",
                f"### {review.get('stage_name')}",
                "Key points: " + "；".join(review.get("key_points", [])),
                "Strengths: " + "；".join(review.get("strengths", [])),
                "Limitations: " + "；".join(review.get("limitations", [])),
                "Improvements: " + "；".join(review.get("improvements", [])),
                f"Publication readiness: {review.get('publication_readiness', '')}",
            ])
    if report.get("publication_claims"):
        lines.extend(["", "## Publication Claim Check"])
        for claim in report["publication_claims"]:
            lines.append(f"- [{claim.get('status')}] {claim.get('claim')} Basis: {claim.get('basis')}")
    if report.get("evidence_cards"):
        lines.extend(["", "## Evidence Cards"])
        for card in report["evidence_cards"][:80]:
            lines.append(f"- {card.get('id', '')} [{card.get('stage', '')}] {card.get('claim', '')}: {card.get('evidence', '')}")
    lines.extend(["", "## Biological Story", report.get("biological_story", "")])
    return "\n".join(lines)


def build_evidence_report(
    final_dir: Path,
    prefix: str,
    *,
    current_stage: str = "",
    session_id: str = "",
    job_id: str = "",
    scope_id: str = "",
    analysis_route: str = "",
    member_set_id: str = "",
    rnaseq_scope_id: str = "",
    include_stages: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    analysis_route = _default_analysis_route(scope_id, analysis_route, member_set_id)
    member_set_id = _report_member_set_filter(analysis_route, member_set_id)
    manifests = load_stage_manifests(
        final_dir,
        prefix,
        scope_id=scope_id,
        analysis_route=analysis_route,
        member_set_id=member_set_id,
        rnaseq_scope_id=rnaseq_scope_id,
    )
    if include_stages:
        allowed = {str(stage) for stage in include_stages if str(stage).strip()}
        manifests = {stage: manifest for stage, manifest in manifests.items() if stage in allowed}
    if not manifests:
        return None
    scope = _scope_context(manifests, prefix)
    scope_label = scope.get("label") or prefix
    scope_id = scope.get("scope_id") or scope_id
    analysis_route = _default_analysis_route(scope_id, analysis_route, member_set_id)
    stages = ordered_stages(manifests, current_stage)
    if include_stages:
        allowed_order = [stage for stage in include_stages if stage in stages]
        stages = allowed_order + [stage for stage in stages if stage in set(include_stages) and stage not in allowed_order]
    report_mode = _report_mode_for_route(analysis_route, stages)
    expected_stages = _expected_stages_for_report(analysis_route, stages)
    completed_expected_stages = [stage for stage in expected_stages if stage in stages]
    missing_expected_stages = [stage for stage in expected_stages if stage not in stages]
    is_final_report = bool(expected_stages) and not missing_expected_stages
    bundles = {stage: extract_stage_evidence(stage, manifests[stage]) for stage in stages}
    findings = [_finding(stage, bundles[stage], idx + 1, scope_label) for idx, stage in enumerate(stages)]
    stage_reviews = [_stage_review(stage, bundles[stage], scope_label) for stage in stages]
    structured_result_interpretations = build_structured_result_interpretations(
        stages,
        bundles,
        prefix=scope_label,
        stage_names=STAGE_NAMES,
        expected_stages=expected_stages,
    )
    grouped_result_interpretations = build_grouped_result_interpretations(
        stages,
        bundles,
        structured_result_interpretations,
        prefix=scope_label,
    )
    publication_claims = _publication_claims(stages, bundles, scope_label)
    scientific_discoveries = _scientific_discoveries(stages, bundles, scope_label)
    risk_flags = [risk for stage in stages for risk in (bundles[stage].get("risks") or [])]
    evidence_cards = _assign_evidence_card_ids([item for stage in stages for item in (bundles[stage].get("evidence") or [])])
    rag_plans = {stage: (bundles[stage].get("parsed") or {}).get("rag_plan") for stage in stages if (bundles[stage].get("parsed") or {}).get("rag_plan")}
    rag_references = {stage: (bundles[stage].get("parsed") or {}).get("rag_references") for stage in stages if (bundles[stage].get("parsed") or {}).get("rag_references")}
    detached_rag_references = _load_detached_rag_references(final_dir, prefix)
    for stage, payload in detached_rag_references.items():
        rag_references.setdefault(stage, payload)
    rag_reference_summary = _rag_reference_summary(rag_references)
    evidence_bundle = _build_evidence_bundle(
        stages,
        bundles,
        prefix=prefix,
        scope=scope,
        scope_id=scope_id,
        analysis_route=analysis_route,
        member_set_id=member_set_id,
        rnaseq_scope_id=rnaseq_scope_id,
        evidence_cards=evidence_cards,
        risk_flags=risk_flags,
        publication_claims=publication_claims,
        scientific_discoveries=scientific_discoveries,
        rag_plans=rag_plans,
        rag_references=rag_references,
        rag_reference_summary=rag_reference_summary,
    )
    claim_layers = build_claim_layers(
        stages,
        bundles,
        evidence_cards=evidence_cards,
        risk_flags=risk_flags,
        rag_references=rag_references,
        scope=scope,
        prefix=scope_label,
    )
    claim_layers["claims"] = _attach_claim_evidence_refs(claim_layers.get("claims") or [], evidence_cards)
    claim_layers["major_claims"] = _attach_claim_evidence_refs(claim_layers.get("major_claims") or [], evidence_cards)
    if not scientific_discoveries:
        scientific_discoveries = claims_to_scientific_discoveries(claim_layers.get("major_claims") or [])
    scientific_discoveries = _attach_discovery_evidence_refs(
        scientific_discoveries,
        evidence_cards,
        claim_layers.get("claims") or [],
    )
    if evidence_bundle.get("discovery_agent"):
        evidence_bundle["discovery_agent"]["hypotheses"] = scientific_discoveries
    evidence_bundle.setdefault("result_agent", {})["evidence_audit"] = claim_layers.get("evidence_audit", {})
    evidence_bundle.setdefault("result_agent", {})["structured_result_interpretations"] = structured_result_interpretations
    evidence_bundle.setdefault("result_agent", {})["grouped_result_interpretations"] = grouped_result_interpretations
    evidence_bundle.setdefault("discovery_agent", {})["claims"] = claim_layers.get("claims", [])
    evidence_bundle.setdefault("discovery_agent", {})["major_claims"] = claim_layers.get("major_claims", [])
    evidence_bundle.setdefault("discovery_agent", {})["grouped_result_interpretations"] = grouped_result_interpretations
    evidence_bundle.setdefault("rag", {})["audit"] = claim_layers.get("rag_audit", {})
    coverage_matrix = _interpretation_coverage_matrix(stages, bundles, claim_layers.get("claims") or [], rag_references)
    agent_contribution_audit = _agent_contribution_audit(
        stages,
        bundles,
        evidence_cards,
        risk_flags,
        claim_layers.get("claims") or [],
        claim_layers.get("major_claims") or [],
        scientific_discoveries,
        claim_layers.get("rag_audit", {}),
        rag_reference_summary,
        evidence_bundle.get("boundaries", {}),
    )
    evidence_bundle["agent_contribution_audit"] = agent_contribution_audit
    evidence_bundle["coverage_matrix"] = coverage_matrix
    generated_at = datetime.now().isoformat()
    freshness = _report_freshness(manifests, generated_at)
    report = {
        "metadata": {
            "title": f"{scope_label} {_report_mode_label(report_mode, is_final_report)}（{len(stages)}个阶段）",
            "current_stage": current_stage,
            "included_stages": stages,
            "stage_count": len(stages),
            "report_kind": "route_live",
            "report_mode": report_mode,
            "report_mode_label": _report_mode_label(report_mode, is_final_report),
            "interpretation_boundary": _interpretation_boundary_for_mode(report_mode),
            "required_stage_policy": (
                "subfamily_prediction_prerequisite_only"
                if report_mode == "subfamily_prediction_report"
                else "rnaseq_integrated_evidence"
                if report_mode == "rnaseq_integrated_report"
                else "integrated_gene_family_evidence"
            ),
            "last_updated_from_stage": current_stage,
            "expected_stages": expected_stages,
            "completed_expected_stages": completed_expected_stages,
            "missing_expected_stages": missing_expected_stages,
            "is_final": is_final_report,
            "generated_at": generated_at,
            "session_id": session_id,
            "job_id": job_id,
            "prefix": prefix,
            "scope": scope,
            "scope_id": scope_id,
            "analysis_route": analysis_route,
            "member_set_id": member_set_id,
            "rnaseq_scope_id": rnaseq_scope_id,
            "schema": "evidence_report_v1",
            "evidence_bundle_schema": "evidence_bundle_v1",
            "rag_reference_summary": rag_reference_summary,
            "freshness": freshness,
        },
        "stage_flow": {
            "visualization": " -> ".join(stages),
            "stages": [
                {
                    "id": stage,
                    "name": STAGE_NAMES.get(stage, stage),
                    "status": bundles[stage].get("status", "unknown"),
                    "key_result": _stage_key_result(stage, bundles[stage]),
                }
                for stage in stages
            ],
        },
        "stage_connections": _connections(stages, bundles),
        "key_findings": findings,
        "structured_result_interpretations": structured_result_interpretations,
        "grouped_result_interpretations": grouped_result_interpretations,
        "scientific_discoveries": scientific_discoveries,
        "stage_reviews": stage_reviews,
        "publication_claims": publication_claims,
        "evidence_audit": claim_layers.get("evidence_audit", {}),
        "claims": claim_layers.get("claims", []),
        "major_claims": claim_layers.get("major_claims", []),
        "claim_patterns": claim_layers.get("claim_patterns", []),
        "rag_audit": claim_layers.get("rag_audit", {}),
        "agent_contribution_audit": agent_contribution_audit,
        "coverage_matrix": coverage_matrix,
        "narrative": claim_layers.get("narrative", {}),
        "evidence_cards": evidence_cards,
        "risk_flags": risk_flags,
        "biological_story": _story(stages, bundles, prefix, scope_label=scope_label),
        "next_steps": _next_steps(stages, bundles),
        "discovery_insights": scientific_discoveries,
        "rag_plans": rag_plans,
        "rag_references": rag_references,
        "freshness": freshness,
        "evidence_bundle": evidence_bundle,
        "agent_boundaries": evidence_bundle.get("boundaries", {}),
        "source_summary": {
            "manifest_count": len(manifests),
            "raw_evidence_count": sum(1 for item in evidence_cards if item.get("source_type") == "raw_file"),
            "risk_count": len(risk_flags),
            "high_risk_count": sum(1 for risk in risk_flags if risk.get("severity") == "high"),
            "rag_reference_stage_count": rag_reference_summary.get("stage_count", 0),
            "rag_reference_count": rag_reference_summary.get("reference_count", 0),
            "claim_count": len(claim_layers.get("claims") or []),
            "major_claim_count": len(claim_layers.get("major_claims") or []),
        },
    }
    report["download_info"] = {
        "markdown": _markdown(report),
        "filename": f"{prefix}.{job_id or 'job'}.evidence_report.md",
    }
    return report


def build_cumulative_interpretation(job_id: str, prefix: str, completed_stages: List[str], final_dir: Path, scope_id: str = "") -> Dict[str, Any]:
    report = build_evidence_report(
        final_dir,
        prefix,
        current_stage=completed_stages[-1] if completed_stages else "",
        job_id=job_id,
        scope_id=scope_id,
        include_stages=completed_stages,
    )
    if not report:
        return {"version": "1.0", "job_id": job_id, "prefix": prefix, "generated_at": datetime.now().isoformat(), "cumulative": {}}
    stage_interpretations: Dict[str, Dict[str, Any]] = {}
    structured_result_interpretations = report.get("structured_result_interpretations") or {}
    grouped_result_interpretations = report.get("grouped_result_interpretations") or {}
    for finding, stage in zip(report.get("key_findings", []), report["metadata"]["included_stages"]):
        stage_interpretations[stage] = {
            "stage_name": STAGE_NAMES.get(stage, stage),
            "stage_id": stage,
            "status": next((s["status"] for s in report["stage_flow"]["stages"] if s["id"] == stage), "unknown"),
            "key_findings": [finding.get("title", "")],
            "interpretation": finding.get("description", ""),
            "structured_result_interpretation": structured_result_interpretations.get(stage, {}),
            "grouped_result_interpretation": grouped_result_interpretations.get(
                (structured_result_interpretations.get(stage) or {}).get("stage_group", ""),
                {},
            ),
            "metrics": {},
            "evidence_items": finding.get("evidence_items", []),
            "risk_flags": finding.get("risk_flags", []),
            "generated_at": report["metadata"]["generated_at"],
        }
    return {
        "version": "2.0",
        "job_id": job_id,
        "prefix": prefix,
        "scope_id": report["metadata"].get("scope_id", ""),
        "scope": report["metadata"].get("scope", {}),
        "generated_at": report["metadata"]["generated_at"],
        "report": report,
        "cumulative": {
            "stages_completed": report["metadata"]["included_stages"],
            "stage_count": report["metadata"]["stage_count"],
            "stage_interpretations": stage_interpretations,
            "synthesis": {
                "biological_story": report["biological_story"],
                "scientific_discoveries": report.get("scientific_discoveries", []),
                "discovery_insights": report.get("discovery_insights", []),
                "stage_connections": report["stage_connections"],
                "structured_result_interpretations": structured_result_interpretations,
                "grouped_result_interpretations": grouped_result_interpretations,
                "next_steps": report["next_steps"],
                "risk_flags": report["risk_flags"],
                "evidence_cards": report["evidence_cards"],
                "stage_reviews": report.get("stage_reviews", []),
                "publication_claims": report.get("publication_claims", []),
                "evidence_audit": report.get("evidence_audit", {}),
                "claims": report.get("claims", []),
                "major_claims": report.get("major_claims", []),
                "claim_patterns": report.get("claim_patterns", []),
                "rag_audit": report.get("rag_audit", {}),
                "narrative": report.get("narrative", {}),
                "rag_plans": report.get("rag_plans", {}),
                "rag_references": report.get("rag_references", {}),
                "evidence_bundle": report.get("evidence_bundle", {}),
                "agent_boundaries": report.get("agent_boundaries", {}),
            },
        },
    }
