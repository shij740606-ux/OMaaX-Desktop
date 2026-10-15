"""
ResultParserAgent V3 - 结果解析、验证与生物解读
自动解析分析结果，验证质量，生成生物解读报告
"""
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from datetime import datetime
from .base import AgentMessage, AgentState, BaseAgent
from gfa_core.mas.llm import LLMManager
from gfa_core.mas.prompts.prompt_builder import build_result_parser_prompt
from gfa_core.mas.llm.model_selector import TaskType, select_model_for_task
from gfa_core.project_paths import WORKSPACE_ROOT

try:
    from gfa_core.gene_family_scope import sanitize_token
except Exception:
    def sanitize_token(value: str) -> str:
        token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
        token = token.strip(".-_")
        return token or "NA"

try:
    from gfa_core.mas.agents.interpretation_evidence import build_cumulative_interpretation
except Exception:
    build_cumulative_interpretation = None


@dataclass
class ValidationResult:
    """验证结果"""
    rule_name: str
    passed: bool
    message: str
    severity: str
    details: Dict = None


class ResultParserAgent(BaseAgent):
    """
    结果解析Agent V3
    
    功能：
    1. 解析manifest.json提取关键信息
    2. 验证分析结果质量
    3. 生成生物解读
    4. 生成Markdown格式综合报告
    """
    name = "result_parser"
    role = "Result Parsing, Validation and Biological Interpretation Specialist"
    
    # 各阶段预期输出文件（从Validator整合）
    EXPECTED_OUTPUTS = {
        "identify": [
            "{prefix}.final_members.id",
            "{prefix}.final_members.pep.fa",
            "{prefix}.identify.manifest.json",
            "{prefix}.identify_report.md"
        ],
        "phylogeny": [
            "{prefix}.tree.nwk",
            "{prefix}.phylogeny.manifest.json",
            "{prefix}.phylogeny_report.md"
        ],
        "pep": [
            "{prefix}.pep.manifest.json",
            "{prefix}.pep_properties.tsv",
            "{prefix}.pep_report.md"
        ],
        "motif": [
            "{prefix}.motif.manifest.json",
            "{prefix}.motif_report.md"
        ],
        "domain": [
            "{prefix}.domain.manifest.json",
            "{prefix}.domain_report.md"
        ],
        "structure": [
            "{prefix}.structure.manifest.json"
        ],
        "promoter": [
            "{prefix}.promoter.manifest.json",
            "{prefix}.promoter_report.md"
        ],
        "synteny": [
            "{prefix}.synteny.manifest.json",
            "{prefix}.synteny_report.md"
        ],
        "interspecies_synteny": [
            "{prefix}.interspecies_synteny.manifest.json",
            "{prefix}.interspecies_synteny_report.md"
        ],
        "karyotype": [
            "{prefix}.karyotype.manifest.json",
            "{prefix}.karyotype.png"
        ],
        "expression": [
            "{prefix}.expression.manifest.json"
        ],
        "kaks": [
            "{prefix}.kaks.manifest.json",
            "{prefix}.kaks_results.csv",
            "{prefix}.kaks_plots.pdf"
        ],
        "ppi": [
            "{prefix}.ppi.manifest.json",
            "{prefix}.ppi_network.tsv"
        ]
    }
    
    # 数据合理性阈值（从Validator整合）
    THRESHOLDS = {
        "identify": {
            "min_genes": 1,
            "max_genes": 1000,
            "max_time": 3600
        },
        "phylogeny": {
            "max_time": 7200
        }
    }
    
    # 已知基因家族规模参考（用于生物解读）
    GENE_FAMILY_REFERENCES = {
        "SPL": {"min": 10, "max": 50, "typical": 20, "description": "SPL转录因子家族"},
        "NAC": {"min": 100, "max": 200, "typical": 150, "description": "NAC转录因子家族"},
        "WRKY": {"min": 30, "max": 100, "typical": 60, "description": "WRKY转录因子家族"},
        "MYB": {"min": 50, "max": 200, "typical": 100, "description": "MYB转录因子家族"},
        "bHLH": {"min": 80, "max": 200, "typical": 120, "description": "bHLH转录因子家族"},
        "NBS": {"min": 50, "max": 500, "typical": 150, "description": "NBS-LRR抗病基因家族"},
        "LEA": {"min": 20, "max": 100, "typical": 50, "description": "LEA蛋白家族"},
        "HSP": {"min": 30, "max": 150, "typical": 80, "description": "热激蛋白家族"},
        "default": {"min": 10, "max": 100, "typical": 30, "description": "一般基因家族"}
    }

    # bbav5 stage id -> prompt_builder stage_examples key（避免所有阶段误用 gene_identification）
    STAGE_TO_PROMPT_KEY = {
        "identify": "gene_identification",
        "phylogeny": "phylogenetic_analysis",
        "pep": "gene_structure",
        "structure": "gene_structure",
        "motif": "motif_domain",
        "domain": "motif_domain",
        "promoter": "promoter_analysis",
        "synteny": "collinearity_duplication",
        "interspecies_synteny": "collinearity_duplication",
        "karyotype": "chromosomal_localization",
        "expression": "expression_profile",
        "kaks": "kaks_analysis",
        "ppi": "ppi_prediction",
        "combo": "comprehensive_visualization",
        "rnaseq_qc": "expression_profile",
        "rnaseq_align": "expression_profile",
        "rnaseq_quant": "expression_profile",
        "rnaseq_de": "expression_profile",
        "rnaseq_go_enrich": "go_kegg_enrichment",
        "rnaseq_kegg_enrich": "go_kegg_enrichment",
        "annotation": "go_kegg_enrichment",
    }
    
    def __init__(self, llm_manager: LLMManager):
        super().__init__(llm_manager)
        self.parsed_results: List[Dict] = []
    
    async def execute(self, state: AgentState, message: AgentMessage) -> AgentState:
        """执行结果解析、验证与解读"""
        print(f"[ResultParserAgent] 开始解析、验证与解读...")
        
        # 获取执行结果
        execution_results = state.results.get("execution_results", [])
        if execution_results:
            last_execution = execution_results[-1] if isinstance(execution_results, list) else {}
            execution_result = last_execution.get("result", {}) if isinstance(last_execution, dict) else {}
            stage = last_execution.get("stage", message.payload.get("stage", "unknown"))
        else:
            execution_result = state.results.get("execution_result", {})
            stage = message.payload.get("stage", "unknown")
        
        prefix = message.payload.get("prefix", "")
        workspace = message.payload.get("workspace", "")
        gene_family = message.payload.get("gene_family", "")
        organism = message.payload.get("organism", "")
        
        if not execution_result:
            state.results["parsed_report"] = {"error": "No execution result to parse"}
            return state
        
        # 1. 解析结果
        parsed = await self._parse_result(stage, execution_result)
        
        # 2. 验证结果
        validation_results = await self._validate_stage(stage, prefix, workspace, execution_result)
        validation_report = self._generate_validation_report(stage, validation_results)
        
        # 3. 生成生物解读
        biological_interpretation = await self._generate_biological_interpretation(stage, parsed, gene_family, organism)
        
        # 4. 生成综合报告
        report = await self._generate_report(stage, parsed, validation_report, biological_interpretation)
        
        # 更新状态
        state.results["parsed_report"] = report
        state.results["parsed_summary"] = parsed.get("summary", {})
        state.results["validation_report"] = validation_report
        state.results["validation_passed"] = validation_report.get("overall_passed", False)
        state.results["biological_interpretation"] = biological_interpretation
        
        state.messages.append({
            "role": "result_parser",
            "content": f"📊 结果解析完成: {report.get('title', stage)}",
        })
        
        return state
    
    def _get_manifest(self, execution_result: Dict) -> Dict:
        """统一获取manifest的方法
        
        优先从manifest_path读取文件，如果不存在则从execution_result获取
        """
        import json
        from pathlib import Path
        
        # 优先从manifest_path读取
        manifest_path = execution_result.get("manifest_path", "")
        if manifest_path:
            try:
                manifest_file = Path(manifest_path)
                if manifest_file.exists():
                    with open(manifest_file, 'r') as f:
                        return json.load(f)
            except Exception as e:
                print(f"[ResultParser] 从文件读取manifest失败: {e}")
        
        # 回退到execution_result中的manifest
        return execution_result.get("manifest") or {}

    def _list_value(self, value: Any) -> List[str]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        if not text:
            return []
        return [item for item in re.split(r"[,;，、\s]+", text) if item]

    def _first_manifest_value(self, manifest: Dict, *keys: str) -> Any:
        params = manifest.get("parameters") or {}
        scope = manifest.get("scope") or {}
        for key in keys:
            for source in (manifest, params, scope):
                if isinstance(source, dict) and source.get(key) not in (None, ""):
                    return source.get(key)
        return ""

    def _scope_context_from_manifest(self, manifest: Dict) -> Dict[str, Any]:
        """Extract explicit family/species scope from a manifest.

        Scope is optional for legacy runs.  When absent, keep prefix as a
        namespace fallback but avoid treating it as a biological species range.
        """
        prefix = str(manifest.get("prefix") or self._first_manifest_value(manifest, "prefix") or "")
        family = self._first_manifest_value(manifest, "family", "gene_family")
        if not family and "." in prefix:
            family = prefix.split(".", 1)[1]

        target_species = self._list_value(self._first_manifest_value(manifest, "target_species", "targetSpecies"))
        reference_species = self._list_value(self._first_manifest_value(manifest, "reference_species", "referenceSpecies"))
        comparative_species = self._list_value(self._first_manifest_value(manifest, "comparative_species", "comparativeSpecies"))
        selected_species = self._list_value(self._first_manifest_value(manifest, "selected_species", "selectedSpecies"))

        context = {
            "prefix": prefix,
            "family": str(family or ""),
            "scope_id": str(self._first_manifest_value(manifest, "scope_id") or ""),
            "scope_file": str(self._first_manifest_value(manifest, "scope_file") or ""),
            "scope_mode": str(self._first_manifest_value(manifest, "scope_mode", "scopeMode") or ""),
            "target_species": target_species,
            "reference_species": reference_species,
            "comparative_species": comparative_species,
            "selected_species": selected_species,
        }
        context["label"] = self._format_scope_label(context, prefix)
        return context

    def _format_scope_label(self, scope: Dict[str, Any], prefix: str = "") -> str:
        family = scope.get("family") or (prefix.split(".", 1)[1] if "." in str(prefix) else "")
        parts = []
        if family:
            parts.append(f"{family}家族")
        if scope.get("target_species"):
            parts.append("目标物种=" + ",".join(scope["target_species"]))
        if scope.get("reference_species"):
            parts.append("参考物种=" + ",".join(scope["reference_species"]))
        if scope.get("comparative_species"):
            parts.append("比较物种=" + ",".join(scope["comparative_species"]))
        if parts:
            return "；".join(parts)
        return prefix or "当前分析范围"
    
    async def _parse_result(self, stage: str, execution_result: Dict) -> Dict:
        """解析执行结果"""
        manifest = self._get_manifest(execution_result)
        
        parsed = {
            "stage": stage,
            "status": execution_result.get("status", "unknown"),
            "execution_time": self._format_duration(manifest.get("elapsed_sec", 0)),
            "timestamp": manifest.get("timestamp", ""),
            "tool_versions": manifest.get("tool_versions", {}),
            "metrics": self._extract_metrics(manifest),
            "artifacts": self._extract_artifacts(manifest),
            "inputs": manifest.get("inputs", []),
            "parameters": manifest.get("parameters", {}),
            "scope": self._scope_context_from_manifest(manifest),
            "summary": {},
            "parsed_data": {}
        }
        
        parsed["summary"] = self._generate_summary(stage, parsed)
        parsed["parsed_data"] = await self._parse_data_files(stage, manifest)
        
        return parsed
    
    async def _validate_stage(self, stage: str, prefix: str, 
                             workspace: str, execution_result: Dict) -> List[ValidationResult]:
        """验证单个阶段"""
        results = []
        
        # 1. 检查执行状态
        status = execution_result.get("status", "unknown")
        results.append(ValidationResult(
            rule_name="execution_status",
            passed=status in ["success", "partial_success"],
            message=f"执行状态: {status}",
            severity="error" if status not in ["success", "partial_success"] else "info",
            details={"status": status}
        ))
        
        if status not in ["success", "partial_success"]:
            return results
        
        # 2. 检查输出文件
        file_results = self._check_output_files(stage, prefix, workspace)
        results.extend(file_results)
        
        # 3. 检查数据合理性
        manifest = self._get_manifest(execution_result)
        data_results = self._check_data_validity(stage, manifest)
        results.extend(data_results)
        
        # 4. 检查执行时间
        time_results = self._check_execution_time(stage, manifest)
        results.extend(time_results)
        
        # 5. 检查manifest完整性
        manifest_results = self._check_manifest_integrity(manifest)
        results.extend(manifest_results)
        
        return results
    
    def _check_output_files(self, stage: str, prefix: str, workspace: str) -> List[ValidationResult]:
        """检查输出文件"""
        results = []
        expected_files = self.EXPECTED_OUTPUTS.get(stage, [])
        final_dir = Path(workspace) / "final" if workspace else Path(".")
        
        missing_files = []
        present_files = []
        
        for file_pattern in expected_files:
            file_name = file_pattern.format(prefix=prefix)
            file_path = final_dir / file_name
            
            if file_path.exists():
                present_files.append({"name": file_name, "size": file_path.stat().st_size})
            else:
                missing_files.append(file_name)
        
        if missing_files:
            results.append(ValidationResult(
                rule_name="required_files",
                passed=False,
                message=f"缺少 {len(missing_files)} 个必需输出文件",
                severity="error",
                details={"missing": missing_files, "present": present_files}
            ))
        else:
            results.append(ValidationResult(
                rule_name="required_files",
                passed=True,
                message=f"所有 {len(expected_files)} 个必需输出文件已生成",
                severity="info",
                details={"present": present_files}
            ))
        
        return results
    
    def _check_data_validity(self, stage: str, manifest: Dict) -> List[ValidationResult]:
        """检查数据合理性"""
        results = []
        metrics = manifest.get("metrics", {})
        thresholds = self.THRESHOLDS.get(stage, {})
        
        if stage == "identify":
            final_members = metrics.get("final_members", 0)
            candidates = metrics.get("candidates", 0)
            
            # 检查最小基因数
            min_genes = thresholds.get("min_genes", 1)
            if final_members < min_genes:
                results.append(ValidationResult(
                    rule_name="min_genes",
                    passed=False,
                    message=f"基因数过少: {final_members} < {min_genes}",
                    severity="warning",
                    details={"final_members": final_members, "min": min_genes}
                ))
            else:
                results.append(ValidationResult(
                    rule_name="min_genes",
                    passed=True,
                    message=f"基因数正常: {final_members}",
                    severity="info",
                    details={"final_members": final_members}
                ))
            
            # 检查最大基因数
            max_genes = thresholds.get("max_genes", 1000)
            if final_members > max_genes:
                results.append(ValidationResult(
                    rule_name="max_genes",
                    passed=False,
                    message=f"基因数过多: {final_members} > {max_genes}",
                    severity="warning",
                    details={"final_members": final_members, "max": max_genes}
                ))
            
            # 检查过滤率
            if candidates > 0:
                filter_rate = (candidates - final_members) / candidates
                if filter_rate > 0.9:
                    results.append(ValidationResult(
                        rule_name="filter_rate",
                        passed=False,
                        message=f"过滤率过高: {filter_rate:.1%}",
                        severity="warning",
                        details={"candidates": candidates, "final": final_members, "filter_rate": filter_rate}
                    ))
                else:
                    results.append(ValidationResult(
                        rule_name="filter_rate",
                        passed=True,
                        message=f"过滤率正常: {filter_rate:.1%}",
                        severity="info",
                        details={"candidates": candidates, "final": final_members, "filter_rate": filter_rate}
                    ))
        
        return results
    
    def _check_execution_time(self, stage: str, manifest: Dict) -> List[ValidationResult]:
        """检查执行时间"""
        results = []
        elapsed = manifest.get("elapsed_sec", 0)
        thresholds = self.THRESHOLDS.get(stage, {})
        max_time = thresholds.get("max_time", 3600)
        
        if elapsed > max_time:
            results.append(ValidationResult(
                rule_name="execution_time",
                passed=False,
                message=f"执行时间过长: {elapsed:.1f}s > {max_time}s",
                severity="warning",
                details={"elapsed": elapsed, "max": max_time}
            ))
        else:
            results.append(ValidationResult(
                rule_name="execution_time",
                passed=True,
                message=f"执行时间正常: {elapsed:.1f}s",
                severity="info",
                details={"elapsed": elapsed}
            ))
        
        return results
    
    def _check_manifest_integrity(self, manifest: Dict) -> List[ValidationResult]:
        """检查manifest完整性"""
        results = []
        
        required_fields = ["manifest_version", "stage", "status", "timestamp"]
        missing_fields = [f for f in required_fields if f not in manifest]
        
        if missing_fields:
            results.append(ValidationResult(
                rule_name="manifest_fields",
                passed=False,
                message=f"Manifest缺少字段: {missing_fields}",
                severity="error",
                details={"missing": missing_fields}
            ))
        else:
            results.append(ValidationResult(
                rule_name="manifest_fields",
                passed=True,
                message="Manifest字段完整",
                severity="info"
            ))
        
        return results
    
    def _generate_validation_report(self, stage: str, results: List[ValidationResult]) -> Dict:
        """生成验证报告"""
        passed_count = sum(1 for r in results if r.passed)
        total_count = len(results)
        
        errors = [r for r in results if not r.passed and r.severity == "error"]
        warnings = [r for r in results if not r.passed and r.severity == "warning"]
        
        overall_passed = len(errors) == 0
        score = (passed_count / total_count * 100) if total_count > 0 else 0
        
        if overall_passed and len(warnings) == 0:
            summary = "✅ 验证通过，所有检查项正常"
        elif overall_passed:
            summary = f"⚠️ 验证通过，但有 {len(warnings)} 个警告"
        else:
            summary = f"❌ 验证失败，发现 {len(errors)} 个错误"
        
        return {
            "stage": stage,
            "overall_passed": overall_passed,
            "score": round(score, 1),
            "summary": summary,
            "total_checks": total_count,
            "passed_checks": passed_count,
            "errors": [{"rule": r.rule_name, "message": r.message, "details": r.details} for r in errors],
            "warnings": [{"rule": r.rule_name, "message": r.message, "details": r.details} for r in warnings],
            "details": [{"rule": r.rule_name, "passed": r.passed, "message": r.message, "severity": r.severity} for r in results]
        }

    def _map_stage_to_prompt_key(self, stage: str) -> str:
        return self.STAGE_TO_PROMPT_KEY.get(stage, "gene_identification")

    def _truncate_text(self, text: Optional[str], max_len: int = 900) -> str:
        if not text or not isinstance(text, str):
            return ""
        t = text.strip()
        if len(t) <= max_len:
            return t
        return t[: max_len - 3] + "..."

    def _artifact_names_summary(self, parsed: Dict, limit: int = 16) -> List[str]:
        arts = parsed.get("artifacts") or []
        out = []
        if isinstance(arts, list):
            for a in arts[:limit]:
                if isinstance(a, dict):
                    out.append(a.get("name") or a.get("file") or "")
        return [x for x in out if x]

    def _build_prompt_metrics_bundle(self, stage: str, parsed: Dict, gene_family: str, organism: str) -> Dict[str, Any]:
        """组装传给 prompt_builder 的定量字段（按阶段尽量填满）。"""
        metrics = parsed.get("metrics") or {}
        summary = parsed.get("summary") or {}
        pdata = parsed.get("parsed_data") or {}
        scope = parsed.get("scope") or {}
        if not gene_family:
            gene_family = scope.get("family") or ""
        if not organism:
            organism = ",".join(scope.get("target_species") or scope.get("selected_species") or [])
        gf_ref = self.GENE_FAMILY_REFERENCES.get(
            (gene_family or "").upper(), self.GENE_FAMILY_REFERENCES["default"]
        )
        base_member = int(metrics.get("final_members") or pdata.get("gene_count") or 0)

        bundle: Dict[str, Any] = {
            "bbav5_stage": stage,
            "gene_family": gene_family,
            "organism": organism,
            "scope": scope,
            "scope_label": scope.get("label", ""),
            "member_count": base_member,
            "reference_count": gf_ref.get("typical", 20),
            "execution_summary_title": summary.get("title"),
            "execution_key_metric": summary.get("key_metric"),
        }

        if stage == "phylogeny":
            tree = pdata.get("tree_newick") or ""
            tips = tree.count(",") + 1 if tree else 0
            bundle["branch_count"] = max(0, tips - 1)
            bundle["member_count"] = tips or base_member

        if stage == "rnaseq_de":
            fam = metrics.get("family_analysis") or {}
            bundle["total_genes"] = int(fam.get("total_family_genes") or base_member or 1)
            bundle["tissue_specific_genes"] = int(metrics.get("de_genes_total") or 0)

        if stage in ("rnaseq_quant", "rnaseq_align", "rnaseq_qc"):
            bundle["total_genes"] = int(metrics.get("final_members") or base_member or 1)

        if stage in ("rnaseq_go_enrich", "rnaseq_kegg_enrich", "annotation"):
            enriched = int(
                metrics.get("kegg_enriched_pathways")
                or metrics.get("go_enriched_terms")
                or metrics.get("enriched_pathway_count")
                or 0
            )
            bundle["enriched_pathway_count"] = enriched
            bundle["de_genes_input"] = int(metrics.get("de_genes_input") or 0)

        return bundle

    def _structured_digest_for_llm(self, stage: str, parsed: Dict) -> Dict[str, Any]:
        """控制体积的结构化摘要，便于模型引用具体指标而非套话。"""
        pdata = parsed.get("parsed_data") or {}
        digest: Dict[str, Any] = {
            "stage": stage,
            "status": parsed.get("status"),
            "execution_time": parsed.get("execution_time"),
            "metrics": parsed.get("metrics") or {},
            "scope": parsed.get("scope") or {},
            "summary": {
                "title": (parsed.get("summary") or {}).get("title"),
                "key_metric": (parsed.get("summary") or {}).get("key_metric"),
                "description": (parsed.get("summary") or {}).get("description"),
            },
            "artifact_keys_sample": self._artifact_names_summary(parsed, 24),
        }
        if pdata.get("gene_count") is not None:
            digest["parsed_gene_count"] = pdata.get("gene_count")
        if pdata.get("tree_newick"):
            digest["tree_newick_excerpt"] = self._truncate_text(pdata.get("tree_newick"), 1200)
        if pdata.get("gene_list"):
            genes = pdata["gene_list"]
            digest["gene_id_sample"] = genes[:12] if isinstance(genes, list) else []
        return digest

    async def _fetch_rag_family_context(self, gene_family: str, stage: str) -> Dict[str, Any]:
        """获取 RAG 结构化家族事实，供结果解读使用。"""
        if not gene_family:
            return {}
        try:
            import aiohttp
            import os

            api_url = os.environ.get("LEGACY_RAG_API_URL", "").rstrip("/")
            if not api_url:
                return {}
            timeout = aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                baseline_resp = await session.get(
                    f"{api_url}/api/v1/rag/family/baseline",
                    params={"family": gene_family},
                )
                baseline = await baseline_resp.json() if baseline_resp.status == 200 else {}

                stage_resp = await session.get(
                    f"{api_url}/api/v1/rag/family/stage-context",
                    params={"family": gene_family, "stage": stage, "limit": 8},
                )
                stage_context = await stage_resp.json() if stage_resp.status == 200 else {}

                pfam_resp = await session.get(
                    f"{api_url}/api/v1/rag/family/pfam",
                    params={"family": gene_family},
                )
                pfam = await pfam_resp.json() if pfam_resp.status == 200 else {}

            return {
                "baseline": baseline.get("baseline") or {},
                "pfam_ids": pfam.get("pfam_ids") or [],
                "pfams": pfam.get("pfams") or [],
                "stage_context": stage_context.get("contexts") or [],
                "available_stages": stage_context.get("available_stages") or [],
                "source": baseline.get("source") or pfam.get("source") or stage_context.get("source") or "",
            }
        except Exception as e:
            print(f"[ResultParser] RAG family context unavailable: {e}")
            return {}
    
    async def _generate_biological_interpretation(self, stage: str, parsed: Dict, gene_family: str, organism: str) -> Dict:
        """生成生物解读（使用Prompt Builder + LLM）"""
        interpretation = {
            "stage": stage,
            "gene_family": gene_family,
            "organism": organism,
            "scope": parsed.get("scope") or {},
            "findings": [],
            "significance": "",
            "recommendations": [],
            "llm_prompt": "",
            "llm_response": ""
        }
        
        prompt_key = self._map_stage_to_prompt_key(stage)
        metrics = parsed.get("metrics", {})
        results = self._build_prompt_metrics_bundle(stage, parsed, gene_family, organism)
        rag_context = await self._fetch_rag_family_context(gene_family, stage)
        rag_baseline = rag_context.get("baseline") or {}
        if rag_baseline.get("median") is not None:
            results["reference_count"] = rag_baseline.get("median")
            results["rag_baseline"] = rag_baseline
        if rag_context.get("pfam_ids"):
            results["rag_pfam_ids"] = rag_context["pfam_ids"]

        ref_typical = self.GENE_FAMILY_REFERENCES.get(
            (gene_family or "").upper(), self.GENE_FAMILY_REFERENCES["default"]
        ).get("typical", 20)
        if rag_baseline.get("median") is not None:
            try:
                ref_typical = float(rag_baseline.get("median"))
            except Exception:
                pass
        mc = int(results.get("member_count") or metrics.get("final_members") or 0)
        user_hypothesis = None
        if prompt_key == "gene_identification" and mc > ref_typical * 1.5:
            user_hypothesis = f"{gene_family}家族在{organism}中成员数偏多，可能存在扩张"
        elif prompt_key == "gene_identification" and mc and mc < ref_typical * 0.5:
            user_hypothesis = f"{gene_family}家族在{organism}中成员数偏少，需审视鉴定阈值或谱系特异性丢失"

        digest = self._structured_digest_for_llm(stage, parsed)
        if rag_context:
            digest["rag_family_context"] = {
                "source": rag_context.get("source"),
                "pfam_ids": rag_context.get("pfam_ids"),
                "baseline": rag_baseline,
                "stage_context": rag_context.get("stage_context"),
            }
        digest_json = json.dumps(digest, ensure_ascii=False, indent=2)

        try:
            prompt = build_result_parser_prompt(
                stage_key=prompt_key,
                results=results,
                user_hypothesis=user_hypothesis
            )
            prompt += (
                "\n\n【本阶段结构化结果摘要】（解读时必须引用其中的具体数字、路径或指标名，禁止空泛陈述；"
                "若无某项数据请明确说明「manifest未提供该字段」；若提供了 rag_family_context，必须说明其证据来源和与本次结果的差异）：\n"
                f"{digest_json}\n"
            )
            interpretation["llm_prompt"] = prompt
        except Exception as e:
            print(f"[ResultParser] 构建Prompt失败: {e}")
            interpretation["llm_prompt"] = ""
            return interpretation
        
        try:
            messages = [
                {"role": "system", "content": (
                    "你是专业的生物信息学分析专家，擅长基因家族与转录组下游分析。"
                    "你必须结合用户给出的 manifest 摘要中的具体数字作答；【解读】不少于3句，需包含至少两类不同指标或产物。"
                )},
                {"role": "user", "content": prompt}
            ]
            
            model_config = select_model_for_task(TaskType.ANALYSIS)
            provider = model_config.get("provider", "ollama")
            model = model_config.get("model", "qwen2.5:14b")
            
            print(f"[ResultParser] 使用模型: {provider}/{model}")
            
            llm_response = await self.llm_manager.acall(
                messages=messages,
                provider=provider,
                model=model,
                temperature=0.7,
                max_tokens=3500
            )
            
            interpretation["llm_response"] = llm_response.content if hasattr(llm_response, 'content') else str(llm_response)
            
            self._parse_llm_response(interpretation["llm_response"], interpretation)
            
        except Exception as e:
            print(f"[ResultParser] LLM调用失败: {e}")
            self._generate_fallback_interpretation(stage, metrics, gene_family, interpretation)
        
        return interpretation
    
    def _parse_llm_response(self, response: str, interpretation: Dict) -> None:
        """解析 LLM 响应，提取结构化数据"""
        # 提取 【解读】
        interpretation_match = re.search(r'【解读】\s*(.+?)(?=【假设验证】|$)', response, re.DOTALL)
        if interpretation_match:
            interpretation["findings"] = [interpretation_match.group(1).strip()]
        
        # 提取 【假设验证】
        hypothesis_match = re.search(r'【假设验证】\s*(.+?)(?=【建议】|$)', response, re.DOTALL)
        if hypothesis_match:
            interpretation["significance"] = hypothesis_match.group(1).strip()
        
        # 提取 【建议】
        recommendation_match = re.search(r'【建议】\s*(.+?)(?=【|$)', response, re.DOTALL)
        if recommendation_match:
            rec_text = recommendation_match.group(1).strip()
            # 分割多条建议
            recs = [r.strip() for r in rec_text.split('\n') if r.strip()]
            interpretation["recommendations"] = recs
    
    def _generate_fallback_interpretation(self, stage: str, metrics: Dict, gene_family: str, interpretation: Dict) -> None:
        """LLM 失败时的 fallback 逻辑"""
        final_members = metrics.get("final_members", 0)
        
        # 获取基因家族参考信息
        gf_ref = self.GENE_FAMILY_REFERENCES.get((gene_family or "").upper(), self.GENE_FAMILY_REFERENCES["default"])
        
        # 生成发现
        if final_members < gf_ref["min"]:
            interpretation["findings"] = [f"鉴定到的基因数量({final_members})低于{gene_family}家族典型范围({gf_ref['min']}-{gf_ref['max']})"]
            interpretation["significance"] = "基因数量偏少，可能存在鉴定参数过于严格或基因丢失的情况"
        elif final_members > gf_ref["max"]:
            interpretation["findings"] = [f"鉴定到的基因数量({final_members})高于{gene_family}家族典型范围({gf_ref['min']}-{gf_ref['max']})"]
            interpretation["significance"] = "基因数量偏多，可能存在基因家族扩张或假阳性"
        else:
            interpretation["findings"] = [f"鉴定到{final_members}个基因，符合{gene_family}家族典型规模"]
            interpretation["significance"] = "基因家族规模正常，建议进行后续分析"
        
        # 生成建议
        interpretation["recommendations"] = [
            f"建议进行系统发育分析，了解{gene_family}家族的进化关系",
            "建议分析基因结构和保守基序",
            "建议研究基因表达模式"
        ]
        
        if stage == "phylogeny":
            interpretation["findings"] = ["系统发育树构建完成"]
            interpretation["significance"] = "进化树揭示了基因家族的进化关系和亚家族分类"
            interpretation["recommendations"] = [
                "建议结合基因结构分析验证亚家族分类",
                "建议进行Ka/Ks分析研究选择压力"
            ]
        elif stage == "rnaseq_de":
            up = metrics.get("de_genes_up", 0)
            down = metrics.get("de_genes_down", 0)
            tot = metrics.get("de_genes_total", up + down)
            fam = metrics.get("family_analysis") or {}
            interpretation["findings"] = [
                f"差异分析得到总计 {tot} 个差异基因（上调 {up}，下调 {down}）。",
                f"家族层面统计：家族基因数 {fam.get('total_family_genes', 'N/A')}，"
                f"其中显著差异家族基因 {fam.get('de_family_genes', 'N/A')}。"
            ]
            interpretation["significance"] = (
                "可将全局差异结果与家族候选基因叠加解读：若家族基因整体稳定而背景基因波动大，提示胁迫响应可能主要由非家族通路驱动。"
            )
            interpretation["recommendations"] = [
                "建议结合 GO/KEGG 富集解释差异基因的通路偏向",
                "建议查看 final 目录中的 DE 结果表与 MA 图核对阈值敏感性",
            ]
        elif stage in ("rnaseq_kegg_enrich", "rnaseq_go_enrich"):
            kp = metrics.get("kegg_enriched_pathways", 0)
            gt = metrics.get("go_enriched_terms", 0)
            di = metrics.get("de_genes_input", 0)
            interpretation["findings"] = [
                f"富集输入基因数约 {di}；KEGG 显著通路 {kp} 条，GO 条目 {gt} 个（manifest 字段）。"
            ]
            interpretation["significance"] = "需结合 padj/qvalue Cutoff 检查是否为单一比较所致偏差；关注顶层通路是否与家族功能一致。"
            interpretation["recommendations"] = [
                "建议阅读 final 目录对应 *_enrich_report.md 中的条目列表",
                "若通路过少，可放宽 cut-off 或检查 ID 映射是否完整",
            ]
    
    async def _generate_report(self, stage: str, parsed: Dict, 
                               validation_report: Dict, biological_interpretation: Dict) -> Dict:
        """生成综合报告"""
        summary = parsed.get("summary", {})
        metrics = parsed.get("metrics", {})
        
        # 构建Markdown报告
        md_lines = [
            f"# {summary.get('title', stage)} 报告",
            "",
            f"**分析阶段**: {stage}",
            f"**执行状态**: {'✅ 成功' if parsed.get('status') in ['success', 'partial_success'] else '❌ 失败'}",
            f"**执行时间**: {parsed.get('execution_time', 'N/A')}",
            "",
            "## 执行摘要",
            "",
            summary.get('key_metric', ''),
            "",
            summary.get('description', ''),
            "",
        ]
        scope = parsed.get("scope") or {}
        scope_lines = self._scope_markdown_lines(scope)
        if scope_lines:
            md_lines.extend(["## 分析范围", "", *scope_lines, ""])
        
        md_lines.extend([
            "## 验证结果",
            "",
            validation_report.get('summary', ''),
            f"**质量评分**: {validation_report.get('score', 0)}分",
            "",
            "## 生物解读",
            "",
        ])
        
        # 添加生物解读
        for finding in biological_interpretation.get("findings", []):
            md_lines.append(f"- {finding}")
        
        md_lines.extend([
            "",
            f"**生物学意义**: {biological_interpretation.get('significance', '')}",
            "",
            "## 后续建议",
            "",
        ])
        
        for rec in biological_interpretation.get("recommendations", []):
            md_lines.append(f"- {rec}")
        
        return {
            "title": summary.get('title', stage),
            "stage": stage,
            "status": parsed.get('status'),
            "markdown": "\n".join(md_lines),
            "validation": validation_report,
            "interpretation": biological_interpretation,
            "metrics": metrics,
            "scope": scope
        }

    def _scope_markdown_lines(self, scope: Dict[str, Any]) -> List[str]:
        if not scope:
            return []
        rows = []
        if scope.get("family"):
            rows.append(f"- **基因家族**: {scope['family']}")
        if scope.get("scope_id"):
            rows.append(f"- **Scope ID**: {scope['scope_id']}")
        if scope.get("scope_mode"):
            rows.append(f"- **范围模式**: {scope['scope_mode']}")
        if scope.get("target_species"):
            rows.append(f"- **目标物种**: {', '.join(scope['target_species'])}")
        if scope.get("reference_species"):
            rows.append(f"- **参考物种**: {', '.join(scope['reference_species'])}")
        if scope.get("comparative_species"):
            rows.append(f"- **比较物种**: {', '.join(scope['comparative_species'])}")
        if scope.get("selected_species"):
            rows.append(f"- **实际参与分析物种**: {', '.join(scope['selected_species'])}")
        return rows
    
    async def _parse_data_files(self, stage: str, manifest: Dict) -> Dict:
        """解析数据文件"""
        parsed_data = {}
        artifacts = manifest.get("artifacts", {})
        
        if stage == "identify":
            members_file = artifacts.get("final_members", {}).get("full_path", "")
            if members_file and Path(members_file).exists():
                parsed_data["gene_list"] = self._parse_fasta_ids(members_file)
                parsed_data["gene_count"] = len(parsed_data["gene_list"])
        
        elif stage == "phylogeny":
            tree_file = artifacts.get("tree", {}).get("full_path", "")
            if tree_file and Path(tree_file).exists():
                parsed_data["tree_newick"] = self._read_text_file(tree_file)
        
        return parsed_data
    
    def _parse_fasta_ids(self, file_path: str) -> List[str]:
        """解析FASTA文件ID"""
        ids = []
        try:
            with open(file_path, 'r') as f:
                for line in f:
                    if line.startswith('>'):
                        id_line = line[1:].strip()
                        gene_id = id_line.split()[0]
                        ids.append(gene_id)
        except Exception as e:
            print(f"[ResultParser] 解析FASTA失败: {e}")
        return ids
    
    def _read_text_file(self, file_path: str) -> str:
        """读取文本文件"""
        try:
            with open(file_path, 'r') as f:
                return f.read()
        except Exception as e:
            print(f"[ResultParser] 读取文件失败: {e}")
            return ""
    
    def _extract_metrics(self, manifest: Dict) -> Dict:
        """提取关键指标"""
        metrics = manifest.get("metrics", {})
        return {
            "total_proteins": metrics.get("total_proteins", 0),
            "final_members": metrics.get("final_members", 0),
            "candidates": metrics.get("candidates", 0),
            "execution_time_sec": manifest.get("elapsed_sec", 0)
        }
    
    def _extract_artifacts(self, manifest: Dict) -> List[Dict]:
        """提取输出文件"""
        artifacts = manifest.get("artifacts", {})
        result = []
        for key, artifact in artifacts.items():
            if isinstance(artifact, dict):
                result.append({
                    "name": key,
                    "file": artifact.get("file", ""),
                    "type": artifact.get("type", "unknown"),
                    "size": artifact.get("size", 0),
                    "path": artifact.get("full_path", "")
                })
        return result
    
    def _generate_summary(self, stage: str, parsed: Dict) -> Dict:
        """生成执行摘要"""
        metrics = parsed.get("metrics", {})
        
        summaries = {
            "identify": {
                "title": "基因家族鉴定",
                "key_metric": f"找到 {metrics.get('final_members', 0)} 个基因成员",
                "description": "使用HMM和BLAST鉴定基因家族成员"
            },
            "phylogeny": {
                "title": "系统发育分析",
                "key_metric": f"构建进化树（{metrics.get('execution_time_sec', 0):.1f}s）",
                "description": "使用MAFFT和IQ-TREE构建系统发育树"
            }
        }
        
        return summaries.get(stage, {
            "title": f"{stage}分析",
            "key_metric": "分析完成",
            "description": "基因家族分析阶段"
        })
    
    def _format_duration(self, seconds: float) -> str:
        """格式化持续时间"""
        if seconds < 60:
            return f"{seconds:.1f}秒"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f}分钟"
        else:
            hours = seconds / 3600
            return f"{hours:.1f}小时"

    # ============ 累积解读功能（新增） ============

    def _discover_scope_id(self, final_dir: Path, prefix: str, completed_stages: List[str]) -> str:
        counts: Dict[str, int] = {}
        for path in sorted(final_dir.glob(f"{prefix}.*.manifest.json")):
            manifest = self._get_manifest({"manifest_path": str(path)})
            scope_id = str(self._first_manifest_value(manifest, "scope_id") or "")
            stage = manifest.get("stage")
            if scope_id and (not completed_stages or stage in completed_stages):
                counts[scope_id] = counts.get(scope_id, 0) + 1
        if not counts:
            return ""
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

    def _canonical_manifest_if_alias(self, path: Path) -> Path | None:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        for key in ("canonical_manifest_path", "canonical_manifest"):
            value = str(manifest.get(key) or "").strip()
            if not value:
                continue
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = path.parent / candidate.name if value.startswith("final/") else path.parent / candidate
            try:
                if candidate.exists() and candidate.resolve() != path.resolve():
                    return candidate
            except OSError:
                continue
        stage = str(manifest.get("stage") or "").strip()
        output_prefix = str(
            manifest.get("output_prefix")
            or manifest.get("parameters", {}).get("output_prefix")
            or manifest.get("stage_context", {}).get("output_prefix")
            or ""
        ).strip()
        if stage and output_prefix:
            candidate = path.parent / f"{output_prefix}.{stage}.manifest.json"
            try:
                if candidate.exists() and candidate.resolve() != path.resolve():
                    return candidate
            except OSError:
                pass
        return None

    def _manifest_path_for_stage(self, final_dir: Path, prefix: str, stage: str, scope_id: str = "") -> Path:
        if scope_id:
            scoped = final_dir / f"{prefix}.{sanitize_token(scope_id)}.{stage}.manifest.json"
            if scoped.exists():
                return self._canonical_manifest_if_alias(scoped) or scoped
        legacy = final_dir / f"{prefix}.{stage}.manifest.json"
        if legacy.exists():
            return self._canonical_manifest_if_alias(legacy) or legacy
        candidates = sorted(final_dir.glob(f"{prefix}.*.{stage}.manifest.json"))
        if candidates:
            return self._canonical_manifest_if_alias(candidates[0]) or candidates[0]
        return legacy

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
        from datetime import datetime
        
        workspace_dir = WORKSPACE_ROOT / job_id / "final"
        scope_id = scope_id or self._discover_scope_id(workspace_dir, prefix, completed_stages)
        if build_cumulative_interpretation is not None:
            try:
                evidence_report = build_cumulative_interpretation(job_id, prefix, completed_stages, workspace_dir, scope_id=scope_id)
                if evidence_report.get("cumulative"):
                    return evidence_report
            except Exception as exc:
                print(f"[ResultParser] 证据驱动累积解读生成失败，回退旧逻辑: {exc}")
        
        # 读取所有已完成阶段的 manifest
        stage_manifests = {}
        stage_interpretations = {}
        
        for stage in completed_stages:
            manifest_file = self._manifest_path_for_stage(workspace_dir, prefix, stage, scope_id)
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
            "scope": self._scope_context_from_manifest(next(iter(stage_manifests.values()), {})),
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
        """生成单阶段解读 - 支持 Stage Examples + Prompt Builder"""
        from datetime import datetime

        metrics = manifest.get("metrics", {})
        status = manifest.get("status", "unknown")
        scope = self._scope_context_from_manifest(manifest)
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

        # 阶段1 (identify) 使用 Stage Examples + Prompt Builder
        if stage == "identify":
            interpretation_data = await self._generate_identify_interpretation_with_template(metrics, prefix, manifest)
            return {
                "stage_name": stage_names.get(stage, stage),
                "stage_id": stage,
                "status": status,
                "key_findings": interpretation_data.get("key_findings", []),
                "interpretation": interpretation_data.get("interpretation", ""),
                "metrics": key_metrics,
                "scope": scope,
                "discovery": interpretation_data.get("discovery"),
                "generated_at": datetime.now().isoformat()
            }

        # 其他阶段使用原有规则生成
        key_findings = self._generate_stage_findings(stage, metrics, scope_label)
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

    async def _generate_identify_interpretation_with_template(self, metrics: Dict, prefix: str, manifest: Dict = None) -> Dict:
        """
        生成阶段1解读 - 简化版本，直接读取参考文件
        """
        member_count = metrics.get("final_members", 0)
        candidates = metrics.get("candidates", 0)
        scope = self._scope_context_from_manifest(manifest or {})
        scope_label = scope.get("label") or prefix
        family = scope.get("family") or (prefix.split(".", 1)[1] if "." in prefix else "")
        
        # 直接从 manifest 读取参考文件计数
        ref_count = 0
        if manifest and "inputs" in manifest:
            for inp in manifest["inputs"]:
                path = inp.get("path", "")
                role = str(inp.get("role") or inp.get("species_role") or "").lower()
                file_scope = str(inp.get("file_scope") or inp.get("scope") or "").lower()
                path_name = Path(path).name
                is_reference_input = role in {"reference", "ref", "seed_reference"} or "known_family" in file_scope
                is_family_file = bool(family and family.lower() in path_name.lower())
                if is_reference_input or is_family_file:
                    full_path = inp.get("full_path", "")
                    try:
                        with open(full_path, 'r') as f:
                            ref_count = sum(1 for line in f if line.startswith('>'))
                    except Exception as e:
                        print(f"[ERROR] 读取参考文件失败: {e}")
                    break
        
        # 生成解读文本
        if ref_count > 0:
            interpretation = f"在{scope_label}中鉴定出{member_count}个基因家族成员，参考物种有{ref_count}个成员。"
        else:
            interpretation = f"在{scope_label}中鉴定出{member_count}个基因家族成员。"
        
        return {
            "key_findings": [f"鉴定出 {member_count} 个基因家族成员"],
            "interpretation": interpretation,
            "discovery": {}
        }

        # 构建 discovery 字段 - 参考模板分析框架，基于真实数据
        member_count = results.get("member_count", 0)
        parts = prefix.split('.')
        species = parts[0] if len(parts) > 0 else "目标物种"
        gene_family = parts[1] if len(parts) > 1 else "该基因家族"

        # 根据模式类型构建不同的 discovery 内容
        if member_count > 50:
            discovery = {
                "pattern_type": "expansion",
                "anomaly": f"通过不同方法（HMMER vs BLAST）鉴定的初始名单可能有细微差别。这些'边缘'基因是否具有非典型结构域？它们可能是假基因，也可能代表了{gene_family}家族一个新的、功能未知的旁系同源分支，值得单独审视。",
                "storyline": f"起点：{gene_family}转录因子家族在{species}中扩张至{member_count}个成员，为功能分化奠定了数量基础。",
                "insight": f"成员鉴定是分析的基石，其首要信息是家族规模。{gene_family}家族在{species}中具有较多成员（{member_count}个），这为后续探究其扩张机制、功能分化及生物学意义奠定了基础。"
            }
        elif member_count < 10:
            discovery = {
                "pattern_type": "contraction",
                "anomaly": f"基因家族的'收缩'或'丢失'是进化的重要方面。{gene_family}家族在{species}中仅{member_count}个成员，需要进一步分析这是否反映了真实的生物学收缩或技术因素。",
                "storyline": f"精简适应：{gene_family}家族在{species}中保持精简的基因组特征。",
                "insight": f"这可能反映了研究对象的偏向性。若需分析此模式，应补充如{species}基因组学特征或特定谱系基因丢失研究的文献。"
            }
        else:
            discovery = {
                "pattern_type": "conservation",
                "anomaly": f"要分析'保守'模式，需要超越单物种鉴定，引入系统的跨物种比较分析。{gene_family}家族在{species}中的{member_count}个成员是否符合该家族在进化上的保守特征？",
                "storyline": f"核心保守：{gene_family}基因家族在进化长河中的稳定传承。",
                "insight": f"真正的'保守'模式需在比较基因组学框架下验证。这指明了未来分析的一个重要方向：与其他物种的{gene_family}家族进行对比。"
            }

        return {
            "key_findings": key_findings,
            "interpretation": interpretation,
            "discovery": discovery
        }

    def _apply_identify_template(self, example: Dict, results: Dict, prefix: str, reference_data: Dict = None) -> str:
        """应用阶段1模板生成解读 - 参考模板分析方法，基于真实数据和参考物种比较"""
        member_count = results.get("member_count", 0)
        candidates = results.get("candidates", 0)
        total_proteins = results.get("total_proteins", 0)

        # 解析 prefix 获取物种和基因家族
        parts = prefix.split('.')
        species = parts[0] if len(parts) > 0 else "目标物种"
        gene_family = parts[1] if len(parts) > 1 else "该基因家族"

        # DEBUG
        print(f"[_apply_identify_template DEBUG] reference_data: {reference_data}")

        # 优先使用实际参考物种数据进行比较（模板逻辑）
        if reference_data and "Ath" in reference_data:
            ref_count = reference_data["Ath"]["count"]
            ref_name = reference_data["Ath"]["name"]
            deviation = member_count - ref_count
            deviation_pct = (deviation / ref_count * 100) if ref_count > 0 else 0

            # 模板逻辑：与参考物种比较判断模式
            if member_count > ref_count * 1.5:  # 超过1.5倍 = 扩张
                interpretation = f"在{prefix}中鉴定出{member_count}个基因家族成员，数量远超{ref_name}（{ref_count}个）。这种显著的扩张（+{deviation_pct:.1f}%）很可能与{species}经历的古多倍化事件（全基因组复制）密切相关，为其复杂的生长发育和信号转导提供了更丰富的遗传材料和功能冗余保障。"
            elif member_count < ref_count * 0.5:  # 少于0.5倍 = 收缩
                interpretation = f"在{prefix}中鉴定出{member_count}个基因家族成员，数量远少于{ref_name}（{ref_count}个）。这种收缩（{deviation_pct:.1f}%）可能表明该家族在{species}中经历了基因丢失或功能简化。"
            else:  # 相近 = 保守
                if abs(deviation_pct) <= 20:
                    interpretation = f"在{prefix}中鉴定出{member_count}个基因家族成员，与{ref_name}（{ref_count}个）数量基本一致。表明该家族在进化过程中保持稳定，核心功能受到纯化选择。"
                elif deviation > 0:
                    interpretation = f"在{prefix}中鉴定出{member_count}个基因家族成员，略多于{ref_name}（{ref_count}个，+{deviation_pct:.1f}%），可能存在轻度扩张。"
                else:
                    interpretation = f"在{prefix}中鉴定出{member_count}个基因家族成员，略少于{ref_name}（{ref_count}个，{deviation_pct:.1f}%），可能存在轻度收缩。"
        else:
            # 没有参考数据时，使用一般性描述（不判断模式）
            interpretation = f"在{prefix}中鉴定出{member_count}个基因家族成员。建议进行跨物种比较分析，以确定该家族在进化上的扩张/保守/收缩模式。"

        return interpretation

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

        elif stage == "synteny":
            blocks = metrics.get("syntenic_blocks", 0)
            if blocks > 0:
                findings.append(f"种内共线性识别出 {blocks} 个共线性区块")

        elif stage == "interspecies_synteny":
            pairs = metrics.get("pairwise_synteny") or {}
            n_pairs = len(pairs) if isinstance(pairs, dict) else 0
            if metrics.get("tree_success"):
                findings.append("多物种系统发育树构建成功")
            if metrics.get("synteny_success"):
                mode = metrics.get("plot_mode") or "both"
                pairwise_count = metrics.get("pairwise_plot_count") or 0
                if mode in {"pairwise", "both"} and pairwise_count:
                    findings.append(f"种间共线性 JCVI 绘图链路已完成，生成 {pairwise_count} 张两两共线性图")
                else:
                    findings.append("种间共线性 JCVI 绘图链路已完成")
            elif n_pairs:
                findings.append(f"完成 {n_pairs} 对物种组合的比对与共线性筛查")

        return findings

    def _generate_stage_interpretation_text(self, stage: str, metrics: Dict, prefix: str) -> str:
        """生成阶段解读文本 - 完整版"""
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
                interpretations.append("基于多物种序列的系统发育为种间共线性比较提供分组依据")
            if metrics.get("synteny_success"):
                plot_mode = metrics.get("plot_mode") or "both"
                pairwise_count = metrics.get("pairwise_plot_count") or 0
                if plot_mode in {"pairwise", "both"} and pairwise_count:
                    interpretations.append(f"物种间共线性分析完成，除多物种总图外还生成了{pairwise_count}张两两比较图")
                else:
                    interpretations.append("物种间共线性分析完成，可用于目标物种与 ref-side 物种的共线性比较")
            else:
                reason = metrics.get("synteny_skipped_reason") or ""
                if reason:
                    interpretations.append(f"种间共线性部分完成或未达标：{reason}")

        return "；".join(interpretations) if interpretations else "分析完成"

    def _generate_stage_connections(self, stage_manifests: Dict, completed_stages: List[str]) -> List[Dict]:
        """生成阶段间关联分析"""
        connections = []

        for i in range(len(completed_stages) - 1):
            from_stage = completed_stages[i]
            to_stage = completed_stages[i + 1]

            from_manifest = stage_manifests.get(from_stage, {})
            to_manifest = stage_manifests.get(to_stage, {})

            from_metrics = from_manifest.get("metrics", {})
            to_metrics = to_manifest.get("metrics", {})

            # 生成关联描述
            connection = self._create_stage_connection(from_stage, to_stage, from_metrics, to_metrics)
            if connection:
                connections.append(connection)

        return connections

    def _create_stage_connection(self, from_stage: str, to_stage: str, from_metrics: Dict, to_metrics: Dict) -> Optional[Dict]:
        """创建两个阶段的关联"""
        stage_names = {
            "identify": "基因家族鉴定",
            "phylogeny": "系统发育分析",
            "motif": "Motif分析",
            "domain": "结构域分析",
            "structure": "基因结构分析",
            "promoter": "启动子分析",
            "synteny": "种内共线性分析",
            "interspecies_synteny": "物种间共线性分析",
        }

        from_name = stage_names.get(from_stage, from_stage)
        to_name = stage_names.get(to_stage, to_stage)

        # identify -> phylogeny
        if from_stage == "identify" and to_stage == "phylogeny":
            final_members = from_metrics.get("final_members", 0)
            seq_count = to_metrics.get("sequences_count", 0)
            return {
                "from": from_stage,
                "to": to_stage,
                "description": f"基于鉴定的{final_members}个基因构建系统发育树",
                "insight": f"进化分析包含{seq_count}条序列，揭示家族进化关系"
            }

        # identify -> structure
        if from_stage == "identify" and to_stage == "structure":
            final_members = from_metrics.get("final_members", 0)
            genes_total = to_metrics.get("genes_total", 0)
            return {
                "from": from_stage,
                "to": to_stage,
                "description": f"为{final_members}个鉴定基因提取结构信息",
                "insight": f"成功获取{genes_total}个基因的结构注释"
            }

        # phylogeny -> motif
        if from_stage == "phylogeny" and to_stage == "motif":
            return {
                "from": from_stage,
                "to": to_stage,
                "description": "基于进化树分组分析保守基序",
                "insight": "motif分析可验证亚家族分类的功能相关性"
            }

        # 默认关联
        return {
            "from": from_stage,
            "to": to_stage,
            "description": f"{from_name}为{to_name}提供数据基础",
            "insight": "连续分析确保数据一致性"
        }

    def _generate_cumulative_biological_story(self, stage_interpretations: Dict, prefix: str) -> str:
        """生成累积生物学故事"""
        stories = []

        # 按阶段顺序构建故事
        stage_order = [
            "identify", "phylogeny", "structure", "motif", "domain", "promoter",
            "synteny", "interspecies_synteny", "karyotype", "expression",
        ]

        for stage in stage_order:
            if stage in stage_interpretations:
                interp = stage_interpretations[stage]
                stage_name = interp.get("stage_name", stage)
                interpretation = interp.get("interpretation", "")
                if interpretation:
                    stories.append(f"【{stage_name}】{interpretation}")

        if not stories:
            return f"{prefix}基因家族分析正在进行中"

        # 添加综合分析
        stories.append(f"\n综合以上分析，{prefix}基因家族的特征已初步揭示。")

        return "\n\n".join(stories)

    def _generate_next_steps(self, completed_stages: List[str], stage_manifests: Dict) -> List[Dict]:
        """生成下一步建议"""
        next_steps = []

        # 定义推荐顺序
        stage_pipeline = [
            "identify", "phylogeny", "structure", "motif", "domain", "promoter",
            "synteny", "interspecies_synteny", "karyotype", "expression", "kaks", "ppi",
        ]

        # 找出未完成的阶段
        completed_set = set(completed_stages)
        for stage in stage_pipeline:
            if stage not in completed_set:
                next_steps.append({
                    "stage": stage,
                    "priority": "high" if len(next_steps) < 2 else "medium",
                    "description": self._get_stage_description(stage),
                    "rationale": self._get_stage_rationale(stage, completed_stages)
                })

        return next_steps[:3]  # 最多返回3个建议

    def _get_stage_description(self, stage: str) -> str:
        """获取阶段描述"""
        descriptions = {
            "identify": "基因家族鉴定",
            "phylogeny": "系统发育分析",
            "structure": "基因结构分析",
            "motif": "Motif分析",
            "domain": "结构域分析",
            "promoter": "启动子分析",
            "synteny": "种内共线性分析",
            "interspecies_synteny": "物种间共线性分析",
            "karyotype": "核型分析",
            "expression": "表达分析",
            "kaks": "Ka/Ks分析",
            "ppi": "蛋白互作分析",
        }
        return descriptions.get(stage, stage)

    def _get_stage_rationale(self, stage: str, completed_stages: List[str]) -> str:
        """获取阶段推荐理由"""
        if stage == "phylogeny" and "identify" in completed_stages:
            return "基于已鉴定的基因家族成员，构建进化树揭示进化关系"
        elif stage == "structure" and "identify" in completed_stages:
            return "分析基因结构特征，验证进化关系"
        elif stage == "promoter" and "structure" in completed_stages:
            return "基于基因结构信息提取启动子序列，分析调控元件"
        elif stage == "motif" and "phylogeny" in completed_stages:
            return "结合进化树分组，分析保守基序特征"
        elif stage == "synteny" and "identify" in completed_stages:
            return "分析基因家族在基因组中的分布和复制模式（种内 MCScanX）"
        elif stage == "interspecies_synteny" and "identify" in completed_stages:
            return "跨物种比较共线性块与基因组演化（需多物种配置与进化树）"
        elif stage == "expression" and "identify" in completed_stages:
            return "研究基因家族的表达模式，揭示功能特征"
        else:
            return f"补充{self._get_stage_description(stage)}，完善研究体系"

    def _interpretation_report_body(self, report: Dict) -> Dict:
        if isinstance(report.get("report"), dict):
            return report["report"]
        return report

    def _interpretation_metadata(self, report: Dict) -> Dict:
        body = self._interpretation_report_body(report)
        return body.get("metadata") or report.get("metadata") or {}

    def _interpretation_identity(self, prefix: str, report: Dict, scope_id: str = "", rnaseq_scope_id: str = "") -> Dict[str, str]:
        metadata = self._interpretation_metadata(report)
        resolved = metadata.get("resolved_identity") or {}
        def meta_value(key: str, fallback: str = "") -> str:
            if key in metadata:
                return str(metadata.get(key) or "").strip()
            return str(fallback or "").strip()
        return {
            "prefix": str(report.get("prefix") or metadata.get("prefix") or resolved.get("prefix") or prefix or "").strip(),
            "scope_id": str(scope_id or report.get("scope_id") or meta_value("scope_id", resolved.get("scope_id"))).strip(),
            "analysis_route": str(report.get("analysis_route") or meta_value("analysis_route", resolved.get("analysis_route"))).strip(),
            "member_set_id": str(report.get("member_set_id") or meta_value("member_set_id", resolved.get("member_set_id"))).strip(),
            "rnaseq_scope_id": str(rnaseq_scope_id or report.get("rnaseq_scope_id") or meta_value("rnaseq_scope_id", resolved.get("rnaseq_scope_id"))).strip(),
        }

    def _interpretation_cache_name(self, identity: Dict[str, str]) -> str:
        prefix = sanitize_token(identity.get("prefix") or "unprefixed")
        rnaseq_scope_id = identity.get("rnaseq_scope_id") or ""
        if rnaseq_scope_id:
            return f"{prefix}.{sanitize_token(rnaseq_scope_id)}.interpretation.json"
        scope_id = identity.get("scope_id") or "unscoped"
        route = identity.get("analysis_route") or "default"
        member = identity.get("member_set_id") or "default_members"
        return f"{prefix}.{sanitize_token(scope_id)}.{sanitize_token(route)}.{sanitize_token(member)}.interpretation.json"

    def _newest_manifest_mtime(self, workspace_dir: Path, report: Dict) -> str:
        body = self._interpretation_report_body(report)
        metadata = body.get("metadata") or {}
        included = set(metadata.get("included_stages") or [])
        paths: List[Path] = []
        stage_status = (((body.get("evidence_bundle") or {}).get("result_agent") or {}).get("stage_status") or [])
        for item in stage_status:
            raw = str(item.get("manifest_path") or "").strip() if isinstance(item, dict) else ""
            if not raw:
                continue
            path = Path(raw)
            if not path.is_absolute():
                path = workspace_dir / path.name if path.name else workspace_dir / raw
            paths.append(path)
        if not paths and included:
            for path in workspace_dir.glob("*.manifest.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if data.get("stage") in included:
                    paths.append(path)
        mtimes = []
        for path in paths:
            try:
                mtimes.append(path.stat().st_mtime)
            except OSError:
                continue
        return datetime.fromtimestamp(max(mtimes)).isoformat() if mtimes else ""

    def _update_interpretation_index(self, workspace_dir: Path, cache_path: Path, identity: Dict[str, str], report: Dict) -> None:
        index_path = workspace_dir / "interpretation.index.json"
        try:
            index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
        except Exception:
            index = {}
        entries = index.get("entries") if isinstance(index.get("entries"), list) else []
        key = "|".join([
            identity.get("prefix", ""),
            identity.get("scope_id", ""),
            identity.get("analysis_route", ""),
            identity.get("member_set_id", ""),
            identity.get("rnaseq_scope_id", ""),
        ])
        body = self._interpretation_report_body(report)
        metadata = body.get("metadata") or {}
        source_summary = body.get("source_summary") or {}
        generated_at = metadata.get("generated_at") or report.get("generated_at") or datetime.now().isoformat()
        included_stages = metadata.get("included_stages") or (report.get("cumulative") or {}).get("stages_completed") or []
        stage_count = metadata.get("stage_count")
        if stage_count is None:
            stage_count = (report.get("cumulative") or {}).get("stage_count", len(included_stages))
        entries = [entry for entry in entries if entry.get("identity_key") != key]
        entries.append({
            "identity_key": key,
            "prefix": identity.get("prefix", ""),
            "scope_id": identity.get("scope_id", ""),
            "analysis_route": identity.get("analysis_route", ""),
            "member_set_id": identity.get("member_set_id", ""),
            "rnaseq_scope_id": identity.get("rnaseq_scope_id", ""),
            "path": f"final/{cache_path.name}",
            "stage_count": stage_count,
            "included_stages": included_stages,
            "generated_at": generated_at,
            "newest_manifest_mtime": self._newest_manifest_mtime(workspace_dir, report),
            "claim_count": source_summary.get("claim_count", len(body.get("claims") or [])),
            "major_claim_count": source_summary.get("major_claim_count", len(body.get("major_claims") or [])),
        })
        index = {
            "version": "1.0",
            "updated_at": datetime.now().isoformat(),
            "entries": entries,
        }
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_interpretation(self, job_id: str, prefix: str, report: Dict, scope_id: str = "", rnaseq_scope_id: str = "") -> bool:
        """保存 scope-aware 解读报告，避免多个 scope 共用 final/interpretation.json。"""
        try:
            workspace_dir = WORKSPACE_ROOT / job_id / "final"
            workspace_dir.mkdir(parents=True, exist_ok=True)
            identity = self._interpretation_identity(prefix, report, scope_id=scope_id, rnaseq_scope_id=rnaseq_scope_id)
            report["identity"] = identity
            report["cache_schema"] = "scope_aware_interpretation_v1"
            interpretation_path = workspace_dir / self._interpretation_cache_name(identity)
            interpretation_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            self._update_interpretation_index(workspace_dir, interpretation_path, identity, report)
            print(f"[ResultParser] scope-aware 解读报告已保存: {interpretation_path}")
            return True
        except Exception as e:
            print(f"[ResultParser] 保存解读报告失败: {e}")
            return False

    def load_interpretation(self, job_id: str, prefix: str = "", scope_id: str = "", analysis_route: str = "", member_set_id: str = "", rnaseq_scope_id: str = "") -> Optional[Dict]:
        """按 identity 从 interpretation.index.json 加载 scope-aware 解读报告。"""
        try:
            workspace_dir = WORKSPACE_ROOT / job_id / "final"
            index_path = workspace_dir / "interpretation.index.json"
            if not index_path.exists():
                return None
            index = json.loads(index_path.read_text(encoding="utf-8"))
            matches = []
            for entry in index.get("entries", []):
                if prefix and entry.get("prefix") != prefix:
                    continue
                if scope_id and entry.get("scope_id") != scope_id:
                    continue
                if analysis_route and entry.get("analysis_route") != analysis_route:
                    continue
                if member_set_id and entry.get("member_set_id") != member_set_id:
                    continue
                if rnaseq_scope_id and entry.get("rnaseq_scope_id") != rnaseq_scope_id:
                    continue
                matches.append(entry)
            if not member_set_id:
                empty_member = [entry for entry in matches if not entry.get("member_set_id")]
                if empty_member:
                    matches = empty_member
            matches = sorted(matches, key=lambda entry: str(entry.get("generated_at") or ""), reverse=True)
            for entry in matches:
                rel = str(entry.get("path") or "")
                path = workspace_dir / Path(rel).name if rel.startswith("final/") else workspace_dir / rel
                if path.exists():
                    return json.loads(path.read_text(encoding="utf-8"))
            return None
        except Exception as e:
            print(f"[ResultParser] 加载解读报告失败: {e}")
            return None


def create_result_parser_agent(llm_manager) -> "ResultParserAgent":
    """创建ResultParserAgent实例"""
    return ResultParserAgent(llm_manager)
