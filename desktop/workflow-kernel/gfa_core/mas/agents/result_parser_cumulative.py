"""
ResultParserAgent 累积解读功能扩展
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

from gfa_core.project_paths import WORKSPACE_ROOT


class CumulativeInterpretationMixin:
    """累积解读功能 Mixin"""

    def _manifest_value(self, manifest: Dict, key: str) -> Any:
        params = manifest.get("parameters") or {}
        scope = manifest.get("scope") or {}
        for source in (manifest, params, scope):
            if isinstance(source, dict) and source.get(key) not in (None, ""):
                return source.get(key)
        return ""

    def _list_value(self, value: Any) -> List[str]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item for item in re.split(r"[,;，、\s]+", str(value).strip()) if item]

    def _scope_context_from_manifest(self, manifest: Dict, prefix: str = "") -> Dict[str, Any]:
        family = self._manifest_value(manifest, "family") or self._manifest_value(manifest, "gene_family")
        if not family and "." in prefix:
            family = prefix.split(".", 1)[1]
        scope = {
            "prefix": prefix,
            "family": family or "",
            "scope_id": self._manifest_value(manifest, "scope_id") or "",
            "scope_mode": self._manifest_value(manifest, "scope_mode") or self._manifest_value(manifest, "scopeMode") or "",
            "target_species": self._list_value(self._manifest_value(manifest, "target_species") or self._manifest_value(manifest, "targetSpecies")),
            "reference_species": self._list_value(self._manifest_value(manifest, "reference_species") or self._manifest_value(manifest, "referenceSpecies")),
            "comparative_species": self._list_value(self._manifest_value(manifest, "comparative_species") or self._manifest_value(manifest, "comparativeSpecies")),
            "selected_species": self._list_value(self._manifest_value(manifest, "selected_species") or self._manifest_value(manifest, "selectedSpecies")),
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

    async def generate_cumulative_report(self, job_id: str, prefix: str,
                                         completed_stages: List[str], scope_id: str = "") -> Dict:
        """
        生成累积解读报告

        Args:
            job_id: 任务ID
            prefix: 前缀（示例：At.WRKY）
            completed_stages: 已完成阶段列表

        Returns:
            累积解读报告
        """
        workspace_dir = WORKSPACE_ROOT / job_id / "final"

        # 读取所有已完成阶段的 manifest
        stage_manifests = {}
        stage_interpretations = {}

        for stage in completed_stages:
            manifest_file = workspace_dir / f"{prefix}.{stage}.manifest.json"
            if scope_id:
                scoped = workspace_dir / f"{prefix}.{scope_id}.{stage}.manifest.json"
                if scoped.exists():
                    manifest_file = scoped
            if manifest_file.exists():
                try:
                    with open(manifest_file, 'r') as f:
                        manifest = json.load(f)
                    stage_manifests[stage] = manifest

                    # 生成单阶段解读
                    stage_interp = await self._generate_stage_interpretation(stage, manifest, prefix)
                    stage_interpretations[stage] = stage_interp
                except Exception as e:
                    print(f"[ResultParser] 读取 {stage} manifest 失败: {e}")
                    continue

        # 生成阶段间关联分析
        stage_connections = self._generate_stage_connections(stage_manifests, completed_stages)

        # 生成生物学故事（综合所有阶段）
        biological_story = self._generate_cumulative_biological_story(stage_interpretations, prefix)

        # 生成下一步建议
        next_steps = self._generate_next_steps(completed_stages, stage_manifests)

        # 构建累积报告
        report = {
            "version": "1.0",
            "job_id": job_id,
            "prefix": prefix,
            "scope_id": scope_id,
            "scope": self._scope_context_from_manifest(next(iter(stage_manifests.values()), {}), prefix),
            "generated_at": datetime.now().isoformat(),
            "cumulative": {
                "stages_completed": completed_stages,
                "stage_count": len(completed_stages),
                "stage_interpretations": stage_interpretations,
                "synthesis": {
                    "biological_story": biological_story,
                    "stage_connections": stage_connections,
                    "next_steps": next_steps
                }
            }
        }

        return report

    async def _generate_stage_interpretation(self, stage: str, manifest: Dict, prefix: str) -> Dict:
        """生成单阶段解读"""
        metrics = manifest.get("metrics", {})
        status = manifest.get("status", "unknown")
        scope = self._scope_context_from_manifest(manifest, prefix)
        scope_label = scope.get("label") or prefix

        # 阶段名称映射
        stage_names = {
            "identify": "基因家族鉴定",
            "phylogeny": "系统发育分析",
            "motif": "Motif分析",
            "domain": "结构域分析",
            "structure": "基因结构分析",
            "promoter": "启动子分析",
            "pep": "蛋白属性分析",
            "synteny": "种内共线性分析",
            "interspecies_synteny": "物种间共线性分析",
            "karyotype": "核型分析",
            "expression": "表达分析",
            "kaks": "Ka/Ks分析",
            "ppi": "蛋白互作分析",
            "summary": "综合分析"
        }

        # 提取关键指标
        key_metrics = self._extract_key_metrics_for_stage(stage, metrics)

        # 生成关键发现
        key_findings = self._generate_stage_findings(stage, metrics, scope_label)

        # 生成解读文本
        interpretation = self._generate_stage_interpretation_text(stage, metrics, scope_label)

        return {
            "stage_name": stage_names.get(stage, stage),
            "stage_id": stage,
            "status": status,
            "key_findings": key_findings,
            "interpretation": interpretation,
            "metrics": key_metrics,
            "scope": scope,
            "generated_at": datetime.now().isoformat()
        }

    def _extract_key_metrics_for_stage(self, stage: str, metrics: Dict) -> Dict:
        """提取阶段关键指标"""
        key_mapping = {
            "identify": ["final_members", "candidates", "total_proteins"],
            "phylogeny": ["sequences_count", "tree_model", "msa_length"],
            "motif": ["motif_count", "motif_types"],
            "domain": ["domain_count", "domain_types"],
            "structure": ["genes_total", "avg_exon_count_per_gene"],
            "promoter": ["genes_with_elements", "total_elements", "unique_elements"],
            "synteny": ["syntenic_blocks", "anchor_pairs"],
            "interspecies_synteny": ["tree_success", "synteny_success", "pairwise_synteny"],
            "expression": ["sample_count", "deg_count"]
        }

        keys = key_mapping.get(stage, [])
        result = {}
        for key in keys:
            if key in metrics:
                result[key] = metrics[key]
        return result

    def _generate_stage_findings(self, stage: str, metrics: Dict, prefix: str) -> List[str]:
        """生成阶段关键发现"""
        findings = []

        if stage == "identify":
            final = metrics.get("final_members", 0)
            if final > 0:
                findings.append(f"鉴定出 {final} 个基因家族成员")
                # 判断家族规模
                if final > 50:
                    findings.append(f"该家族成员数量({final})显著扩张")
                elif final < 10:
                    findings.append(f"该家族成员数量({final})相对较少")

        elif stage == "phylogeny":
            seq_count = metrics.get("sequences_count", 0)
            if seq_count > 0:
                findings.append(f"系统发育分析包含 {seq_count} 条序列")

        elif stage == "promoter":
            genes_with = metrics.get("genes_with_elements", 0)
            total = metrics.get("total_elements", 0)
            if genes_with > 0:
                findings.append(f"{genes_with} 个基因含有顺式作用元件")
                findings.append(f"共识别出 {total} 个调控元件")

        elif stage == "motif":
            motif_count = metrics.get("motif_count", 0)
            if motif_count > 0:
                findings.append(f"识别出 {motif_count} 个保守基序")

        elif stage == "domain":
            domain_count = metrics.get("domain_count", 0)
            if domain_count > 0:
                findings.append(f"识别出 {domain_count} 个功能结构域")

        elif stage == "synteny":
            blocks = metrics.get("syntenic_blocks", 0)
            if blocks > 0:
                findings.append(f"种内共线性识别出 {blocks} 个区块")

        elif stage == "interspecies_synteny":
            pairs = metrics.get("pairwise_synteny") or {}
            n_pairs = len(pairs) if isinstance(pairs, dict) else 0
            if metrics.get("synteny_success"):
                findings.append("物种间共线性主流程已完成")
            elif n_pairs:
                findings.append(f"物种间共线性：已完成 {n_pairs} 对物种组合筛查")

        return findings

    def _generate_stage_interpretation_text(self, stage: str, metrics: Dict, prefix: str) -> str:
        """生成阶段解读文本"""
        interpretations = []

        if stage == "identify":
            final = metrics.get("final_members", 0)
            candidates = metrics.get("candidates", 0)
            if final > 0:
                interpretations.append(f"在{prefix}中鉴定出{final}个基因家族成员")
                if candidates > 0:
                    interpretations.append(f"（从{candidates}个候选基因中筛选）")
                if final > 50:
                    interpretations.append("该家族呈现显著扩张特征，可能与物种特异性的基因组复制事件相关")
                elif final < 10:
                    interpretations.append("该家族成员数量相对较少，可能经历了基因丢失或功能特化")

        elif stage == "phylogeny":
            seq_count = metrics.get("sequences_count", 0)
            if seq_count > 0:
                interpretations.append(f"基于{seq_count}条序列构建的系统发育树揭示了基因家族的进化关系")
                interpretations.append("进化树可用于推断亚家族分类和基因复制事件")

        elif stage == "promoter":
            genes_with = metrics.get("genes_with_elements", 0)
            total = metrics.get("total_elements", 0)
            unique = metrics.get("unique_elements", 0)
            if genes_with > 0:
                interpretations.append(f"在{genes_with}个基因的启动子区域识别出顺式作用元件")
                interpretations.append(f"共发现{total}个元件，涉及{unique}种调控元件类型")
                interpretations.append("这些元件可能参与调控基因家族的时空特异性表达")

        elif stage == "motif":
            motif_count = metrics.get("motif_count", 0)
            if motif_count > 0:
                interpretations.append(f"识别出{motif_count}个保守基序")
                interpretations.append("保守基序可能对应重要的功能结构域或结合位点")

        elif stage == "domain":
            domain_count = metrics.get("domain_count", 0)
            if domain_count > 0:
                interpretations.append(f"识别出{domain_count}个功能结构域")
                interpretations.append("结构域组成反映了基因家族的功能特征")

        elif stage == "structure":
            genes_total = metrics.get("genes_total", 0)
            if genes_total > 0:
                interpretations.append(f"分析了{genes_total}个基因的结构特征")
                interpretations.append("基因结构信息有助于理解进化关系和功能分化")

        elif stage == "synteny":
            blocks = metrics.get("syntenic_blocks", 0)
            if blocks > 0:
                interpretations.append(f"种内分析识别出{blocks}个共线性区块")
                interpretations.append("种内共线性揭示基因组复制与基因保留模式")

        elif stage == "interspecies_synteny":
            if metrics.get("tree_success"):
                interpretations.append("多物种进化树支持后续的种间共线性比较")
            if metrics.get("synteny_success"):
                interpretations.append("物种间共线性分析完成，可衔接 Ka/Ks 等同源推断")
            else:
                reason = metrics.get("synteny_skipped_reason") or ""
                if reason:
                    interpretations.append(f"种间共线性未完全成功：{reason}")

        return "；".join(interpretations) if interpretations else "分析完成"

    # 以下方法与 ResultParserAgent 主类中的实现保持同名占位，便于未来 Mixin 组合；当前仓库实际走的是 result_parser.py 内完整实现。
    def _generate_stage_connections(self, stage_manifests: Dict, completed_stages: List[str]) -> List[Dict]:
        return []

    def _generate_cumulative_biological_story(self, stage_interpretations: Dict, prefix: str) -> str:
        if not stage_interpretations:
            return f"{prefix} 基因家族分析汇总：暂无已完成阶段解读"
        lines = []
        for sid, interp in stage_interpretations.items():
            name = interp.get("stage_name", sid)
            text = interp.get("interpretation") or ""
            if text:
                lines.append(f"【{name}】{text}")
        return "\n\n".join(lines) if lines else f"{prefix} 基因家族分析汇总"

    def _generate_next_steps(self, completed_stages: List[str], stage_manifests: Dict) -> List[Dict]:
        return []
