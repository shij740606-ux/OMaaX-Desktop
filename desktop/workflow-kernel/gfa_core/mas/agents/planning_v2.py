"""
PlanningAgent V2 - LLM驱动的智能任务规划
根据用户意图和系统状态,动态生成最优执行计划

V2.1 更新:
- 集成PreFlight预检验证
- 集成Learning参数推荐
- 输出包含: 预检验证结果、执行计划、推荐参数
"""
import json
import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from .base import AgentMessage, AgentState, BaseAgent, QueryMode
from gfa_core.mas.llm import LLMManager
from gfa_core.mas.stage_catalog import (
    PARALLEL_GROUPS as CATALOG_PARALLEL_GROUPS,
    STAGE_DEPENDENCIES as CATALOG_STAGE_DEPENDENCIES,
    STAGE_INFO as CATALOG_STAGE_INFO,
    complete_dependencies,
    get_unsupported_stages,
    is_stage_executable,
    is_utility_stage,
    is_repair_stage,
)
from gfa_core.mas.utility_tasks import utility_display_name
from gfa_core.mas.artifact_resolver import ArtifactResolver
from gfa_core.project_paths import PROJECT_ROOT, WORKSPACE_ROOT
from gfa_core.mas.context_index import ProjectContextIndex, build_project_context
from gfa_core.mas.stage_capabilities import get_stage_capability
from gfa_core.gene_family_stage_context import enrich_stage_params_with_context
from gfa_core.gene_family_scope import sanitize_token
from gfa_core.gwas_fmt_context import resolve_gwas_fmt_input
from gfa_core.gwas_root_inputs import resolve_root_pair
from gfa_core.mas.subfamily_status import subfamily_state_for_scope
from gfa_core.wgs_upstream_manifest import (
    WgsUpstreamManifestError,
    materialize_wgs_identity_params,
    resolve_unique_wgs_prefix,
)


GF_TARGET_ONLY_STAGE_PARAMS = {"promoter", "synteny", "karyotype", "pep", "tfbs", "ppi"}
GF_REFERENCE_STAGE_PARAMS = {
    "phylogeny",
    "motif",
    "domain",
    "structure",
    "combo",
    "interspecies_synteny",
    "ortholog",
    "kaks",
}
GF_SCOPE_IDENTITY_KEYS = (
    "scopeFile",
    "scope_file",
    "scopeId",
    "scope_id",
    "stageContext",
    "stage_context",
    "memberSetId",
    "member_set_id",
    "output_prefix",
    "legacy_output_prefix",
)
WGS_PREFIX_CONSUMER_STAGES = {
    "wgs_align",
    "wgs_markdup",
    "wgs_bam_qc",
    "wgs_haplotype_calling",
    "wgs_gvcf_combine",
    "wgs_joint_genotyping",
    "wgs_variant_select",
    "wgs_variant_filter",
    "wgs_impute",
    "wgs_maf_missing_filter",
}

_GWAS_SCOPE_ID_PATTERNS = {
    "genotype_scope_id": re.compile(r"^genotype:[0-9a-f]{64}$"),
    "phenotype_scope_id": re.compile(r"^phenotype:[0-9a-f]{64}$"),
    "covariate_scope_id": re.compile(r"^covariate:[0-9a-f]{64}$"),
}


def _gwas_mlm_scopes_from_completed_preflight(
    preflight_status: Dict[str, Any],
    prefix: str,
    workspace: str,
) -> Dict[str, str]:
    """Read exact preflight-approved producer manifests for a completed MLM DAG."""
    requirements = {
        "gwas_fmt": ("genotype_scope_id",),
        "gwas_kinship": ("genotype_scope_id",),
        "gwas_trait": ("phenotype_scope_id",),
        "gwas_cov": ("genotype_scope_id", "covariate_scope_id"),
    }
    final_dir = (Path(workspace) / "final").resolve()
    collected: Dict[str, List[str]] = {}

    for stage_id, scope_keys in requirements.items():
        status = preflight_status.get(stage_id)
        if not isinstance(status, dict) or status.get("status") != "success":
            return {}
        manifest_value = status.get("manifest_path")
        if not manifest_value:
            return {}
        manifest_path = Path(str(manifest_value)).resolve()
        try:
            manifest_path.relative_to(final_dir)
        except ValueError:
            return {}
        if not manifest_path.is_file():
            return {}
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if (
            manifest.get("stage") != stage_id
            or manifest.get("prefix") != prefix
            or manifest.get("status") != "success"
        ):
            return {}
        for scope_key in scope_keys:
            scope_id = str(manifest.get(scope_key) or "").strip()
            if not _GWAS_SCOPE_ID_PATTERNS[scope_key].fullmatch(scope_id):
                return {}
            collected.setdefault(scope_key, []).append(scope_id)

    genotype_scopes = collected.get("genotype_scope_id") or []
    if len(set(genotype_scopes)) != 1:
        return {}
    return {
        "genotypeScopeId": genotype_scopes[0],
        "phenotypeScopeId": collected["phenotype_scope_id"][0],
        "covariateScopeId": collected["covariate_scope_id"][0],
    }


def _confirmation_bound_input_issue(stage_id: str, role: str) -> Optional[Dict[str, Any]]:
    """Describe an external input that is intentionally selected at confirmation time."""
    if stage_id.startswith("pop_") and role == "population_vcf":
        return {
            "severity": "warning", "category": "parameter_confirmation",
            "code": "population_vcf_confirmation_required",
            "message": f"{stage_id} 需要在执行确认弹窗中选择群体 VCF",
            "suggestion": "请确认群体VCF绝对路径；执行时会重新校验文件和科学身份。",
            "auto_fixable": True, "fix_action": "confirm_population_vcf",
        }
    if stage_id != "gwas_fmt" or role != "external_vcf":
        return None
    return {
        "severity": "warning",
        "category": "parameter_confirmation",
        "code": "gwas_external_vcf_confirmation_required",
        "message": "gwas_fmt 需要在执行确认弹窗中选择外部 VCF",
        "suggestion": "请在执行确认弹窗填写外部 VCF 的绝对路径；提交执行时仍会严格校验文件及 VCF 内容。",
        "auto_fixable": True,
        "fix_action": "confirm_external_vcf",
    }


def _as_param_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.replace("，", ",").replace("；", ",").replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = [value]
    return [str(item).strip() for item in raw if str(item).strip()]


COMBO_EVIDENCE_PANELS = ["motif", "structure", "domain"]


def _normalize_combo_panel_list(raw_panels: Any, *, default: Optional[List[str]] = None) -> List[str]:
    aliases = {
        "motifs": "motif",
        "domains": "domain",
        "gene_structure": "structure",
        "gene-structure": "structure",
        "genestructure": "structure",
        "all": "all",
    }
    if isinstance(raw_panels, str):
        raw_items = raw_panels.replace("，", ",").replace("；", ",").replace(";", ",").split(",")
    elif isinstance(raw_panels, (list, tuple, set)):
        raw_items = list(raw_panels)
    else:
        raw_items = []
    panels: List[str] = []
    for item in raw_items:
        key = str(item or "").strip().lower().replace(" ", "_")
        if not key:
            continue
        key = aliases.get(key, key)
        if key == "all":
            return list(COMBO_EVIDENCE_PANELS)
        if key in COMBO_EVIDENCE_PANELS and key not in panels:
            panels.append(key)
    return panels or list(default or [])


def _combo_panels_from_params(params: Dict[str, Any]) -> List[str]:
    return _normalize_combo_panel_list((params or {}).get("comboPanels") or (params or {}).get("combo_panels"))


def _append_combo_for_two_evidence(target_stages: List[str], intent: Dict[str, Any]) -> List[str]:
    evidence = [panel for panel in COMBO_EVIDENCE_PANELS if panel in target_stages]
    if "combo" not in target_stages and len(evidence) >= 2:
        intent.setdefault("params", {})["comboPanels"] = evidence
        return list(dict.fromkeys([*target_stages, "combo"]))
    return target_stages


def _drop_scope_identity_keys(params: Dict[str, Any]) -> None:
    for key in GF_SCOPE_IDENTITY_KEYS:
        params.pop(key, None)


def _subfamily_scope_mode_from_params(params: Dict[str, Any]) -> str:
    refs = _as_param_list(params.get("referenceSpecies") or params.get("reference_species") or params.get("refSpeciesList"))
    comps = _as_param_list(params.get("comparativeSpecies") or params.get("comparative_species"))
    if refs and comps:
        return "target_plus_reference_plus_comparative"
    if refs:
        return "target_plus_reference"
    if comps:
        return "target_plus_comparative"
    return str(params.get("scopeMode") or params.get("scope_mode") or "target_only")


def _normalize_gene_family_stage_params(stage_id: str, stage_params: Dict[str, Any], prefix: str) -> None:
    targets = _as_param_list(stage_params.get("targetSpecies") or stage_params.get("target_species"))
    refs = _as_param_list(stage_params.get("referenceSpecies") or stage_params.get("reference_species") or stage_params.get("refSpeciesList"))
    comps = _as_param_list(stage_params.get("comparativeSpecies") or stage_params.get("comparative_species"))
    family = str(stage_params.get("geneFamily") or stage_params.get("gene_family") or (prefix.split(".")[-1] if "." in str(prefix or "") else "")).strip()
    if targets:
        stage_params["targetSpecies"] = targets
    if len(targets) == 1 and family:
        stage_params["prefix"] = f"{targets[0]}.{family}"

    if stage_id in GF_TARGET_ONLY_STAGE_PARAMS:
        stage_params["scopeMode"] = "target_only"
        stage_params["referenceSpecies"] = []
        stage_params["refSpeciesList"] = []
        stage_params["comparativeSpecies"] = []
        stage_params["analysisRoute"] = "single_species"
        stage_params.pop("analysis_route", None)
        _drop_scope_identity_keys(stage_params)
    elif stage_id in GF_REFERENCE_STAGE_PARAMS and (refs or comps):
        if stage_id == "interspecies_synteny":
            plot_mode = str(stage_params.get("interspeciesPlotMode") or stage_params.get("plotMode") or "both").strip().lower()
            if plot_mode not in {"multi", "pairwise", "both"}:
                plot_mode = "both"
            stage_params["interspeciesPlotMode"] = plot_mode
            stage_params["blastEngine"] = str(stage_params.get("blastEngine") or "diamond").strip().lower() or "diamond"
            ref_side = list(dict.fromkeys([*refs, *comps]))
            primary = refs[0] if refs else ref_side[0]
            comparative = [sp for sp in ref_side if sp != primary]
            if ref_side:
                stage_params["referenceSpecies"] = [primary]
                stage_params["refSpeciesList"] = ref_side
                stage_params["comparativeSpecies"] = comparative
                stage_params["scopeMode"] = "target_plus_reference_plus_comparative" if comparative else "target_plus_reference"
                stage_params["analysisRoute"] = "multi_species"
                _drop_scope_identity_keys(stage_params)
            return
        if refs and comps:
            stage_params["scopeMode"] = "target_plus_reference_plus_comparative"
        elif refs:
            stage_params["scopeMode"] = "target_plus_reference"
        else:
            stage_params["scopeMode"] = "target_plus_comparative"
        stage_params["referenceSpecies"] = refs
        stage_params["refSpeciesList"] = refs
        stage_params["comparativeSpecies"] = comps
        _drop_scope_identity_keys(stage_params)


@dataclass
class PreFlightIssue:
    """验证问题"""
    severity: str  # error, warning, info
    category: str  # input, dependency, resource, parameter
    message: str
    suggestion: str
    auto_fixable: bool = False
    fix_action: Optional[str] = None


@dataclass
class StageStatus:
    """阶段状态"""
    stage: str
    status: str  # success, partial_success, failed, not_found
    manifest_path: Optional[Path] = None
    output_files: List[str] = field(default_factory=list)
    missing_outputs: List[str] = field(default_factory=list)


class PlanningAgentV2(BaseAgent):
    """
    智能任务规划Agent V2
    
    功能:
    1. 根据用户意图生成分析流程
    2. 考虑已完成的阶段,避免重复执行
    3. 智能调度并行/串行执行
    4. 生成详细的执行计划(含参数,预估时间)
    5. 集成PreFlight预检验证
    6. 集成Learning参数推荐
    """
    name = "planning_v2"
    role = "Intelligent Workflow Planner"
    
    # 各阶段预期输入文件 (来自PreFlight)
    REQUIRED_INPUTS = {
        "gwas_fmt": ["external_vcf"],
        # HMM is optional at preflight level: identify can use uploaded HMM,
        # --required-pfams, or family knowledge to build/query Pfam HMMs.
        "identify": ["protein"],
        "identify_refine": ["id", "protein"],
        "prepare_organize": [],
        "prepare_normalize_gff": ["gff"],
        "prepare_longest_transcript": ["gff"],
        "prepare_extract_pep": ["gff", "genome"],
        "prepare_extract_cds": ["gff", "genome"],
        "sequence_extract_by_id": [],
        "prepare_filter_fasta": ["protein"],
        "prepare_simplify_ncbi": ["gff"],
        "phylogeny": ["protein"],
        "reference_classification": ["protein"],
        "motif": ["protein"],
        "domain": ["protein"],
        "structure": ["protein", "gff"],
        "promoter": ["gff", "genome"],
        "synteny": ["gff", "genome"],
        # 种间共线性真实执行依赖全基因组蛋白和 GFF 坐标。
        # genome FASTA 只在需要从 GFF 提取 pep/cds 时才需要，不应阻止已有 pep+gff 的任务。
        "interspecies_synteny": ["protein", "gff"],
        "karyotype": [],
        "pep": ["protein"],
        # Expression has an interactive data-source choice in the confirmation
        # dialog: upload matrix/sample.txt, or provide a GEO accession for
        # backend download. Do not block planning before that dialog is shown;
        # run_stage still validates the selected source before execution.
        "expression": [],
        "rnaseq_qc": ["fastq", "sample_config"],
        "wgs_qc": ["wgs_sample_config"],
        "wgs_index": ["wgs_reference_fasta"],
        "wgs_align": [],
        "wgs_markdup": [],
        "wgs_bam_qc": [],
        "wgs_haplotype_calling": [],
        "wgs_gvcf_combine": [],
        "wgs_joint_genotyping": [],
        "wgs_variant_select": [],
        "wgs_variant_filter": [],
        "wgs_impute": [],
        "wgs_maf_missing_filter": [],
        "rnaseq_align": ["genome_or_hisat2_index", "sample_config"],
        "rnaseq_quant": ["gff", "sample_config"],
        "rnaseq_de": ["sample_config"],
        "annotation": ["protein"],
        "rnaseq_go_enrich": [],
        "rnaseq_kegg_enrich": [],
        "rnaseq_enrich": [],
        "rnaseq_fgsea": [],
        "rnaseq_wgcna": [],
        "rnaseq_candidate_family": [],
        "rnaseq_report": [],
    }
    
    # 各阶段依赖 (来自PreFlight)
    STAGE_DEPENDENCIES = CATALOG_STAGE_DEPENDENCIES
    
    # Learning默认参数
    DEFAULT_LEARNING_PARAMS = {
        "identify": {
            "cpu": 8,
            "dom_i_evalue_max": 0.001,
            "hmm_cov_min": 0.3,
            "blast_evalue": "1e-05",
            "blast_database_role": "target_proteome",
            "blast_query_role": "reference_family_pep",
            "blast_candidate_id_column": "sseqid"
        },
        "phylogeny": {
            "cpu": 8,
            "bootstrap": 1000,
            "model": "MFP"
        }
    }
    
    # 阶段基础信息
    STAGE_INFO = CATALOG_STAGE_INFO
    
    # 依赖关系
    DEPENDENCIES = CATALOG_STAGE_DEPENDENCIES
    
    # 可并行执行的阶段组
    PARALLEL_GROUPS = CATALOG_PARALLEL_GROUPS
    
    def __init__(self, llm_manager: LLMManager, 
                 project_root: str = str(PROJECT_ROOT),
                 learning_history_file: str = str(PROJECT_ROOT / "gfa_core" / "mas" / "learning_history.json")):
        super().__init__(llm_manager)
        self.project_root = Path(project_root)
        self.workspace_root = self.project_root / "workspace"
        self.input_root = self.project_root / "input"
        self.learning_history_file = Path(learning_history_file)
        self.execution_history: List[Dict] = []
        self.param_stats: Dict[str, Dict] = {}
        self._load_learning_history()
    
    def _load_learning_history(self):
        """加载Learning历史记录"""
        if self.learning_history_file.exists():
            try:
                with open(self.learning_history_file) as f:
                    data = json.load(f)
                    self.execution_history = data.get("executions", [])
                    self.param_stats = data.get("param_stats", {})
            except Exception as e:
                print(f"[PlanningAgentV2] 加载学习历史失败: {e}")
    
    async def execute(self, state: AgentState, message: AgentMessage) -> AgentState:
        """
        执行智能任务规划 (V2.1 - 集成PreFlight和Learning)
        
        流程:
        1. 预检验证 (PreFlight)
        2. 获取Learning参数推荐
        3. 生成执行计划
        4. 如果验证失败,直接返回不生成计划
        """
        query = message.payload.get("query", "")
        # 优先从 results["results"]["intent"] 获取，回退到 results["intent"]
        intent = state.results.get("results", {}).get("intent", {}) or state.results.get("intent", {})
        completed_stages = state.results.get("completed_stages", [])
        
        print(f"[PlanningAgentV2] completed_stages: {completed_stages}")
        print(f"[PlanningAgentV2] intent stages: {intent.get('stages', 'NOT FOUND')}")
        
        # 从 message.payload 获取 job_id（优先），然后回退到 state 和 intent
        job_id = message.payload.get("job_id", "")
        if not job_id:
            job_id = state.job_id if hasattr(state, 'job_id') else ""
        if not job_id:
            job_id = intent.get("job_id", "")
        if not job_id:
            job_id = state.session_id
        workspace = intent.get("workspace", "")
        prefix = intent.get("prefix") or ""
        print(f"[PlanningAgentV2] job_id from payload/state: {job_id}, prefix: {prefix}")
        intent_params = intent.get("params") if isinstance(intent.get("params"), dict) else {}
        benchmark_mode = bool(intent.get("benchmarkMode") or intent_params.get("benchmarkMode"))
        
        # ===== 步骤1: PreFlight预检验证 =====
        if benchmark_mode:
            print(f"[PlanningAgentV2] benchmarkMode=True, skip PreFlight file validation for planning evaluation")
            preflight_result = {
                "valid": True,
                "issues": [],
                "stage_status": {},
                "benchmark_mode": True,
            }
        else:
            print(f"[PlanningAgentV2] 开始PreFlight预检验证...")
            preflight_result = await self._run_preflight_validation(intent, job_id, prefix, workspace)
        state.results["preflight_validation"] = preflight_result
        
        # 如果验证失败且有严重错误,直接返回不生成计划
        if not preflight_result["valid"]:
            errors = [i for i in preflight_result["issues"] if i["severity"] == "error"]
            if errors:
                error_msg = "; ".join([e["message"] for e in errors])
                print(f"[PlanningAgentV2] PreFlight验证失败: {error_msg}")
                state.messages.append({
                    "role": "planning",
                    "content": f"❌ 预检验证失败: {error_msg}",
                })
                state.results["task_plan"] = {
                    "valid": False,
                    "error": error_msg,
                    "preflight_result": preflight_result,
                    "stages": [],
                    "learning_recommendations": {}
                }
                return state
        
        # ===== 步骤2: 获取Learning参数推荐 =====
        requested_stages = list(intent.get("stages", ["identify"]) or ["identify"])
        if "enrichment" in requested_stages:
            requested_stages = [stage for stage in requested_stages if stage != "enrichment"]
            query_lower = (query or "").lower()
            if "kegg" in query_lower or "通路" in query_lower or "pathway" in query_lower:
                requested_stages.append("rnaseq_kegg_enrich")
            elif "go" in query_lower or "go富集" in query_lower or "go分析" in query_lower:
                requested_stages.append("rnaseq_go_enrich")
            else:
                requested_stages.extend(["rnaseq_go_enrich", "rnaseq_kegg_enrich"])

        target_stages = complete_dependencies(requested_stages)
        learning_recommendations = await self._get_learning_recommendations(target_stages)
        state.results["learning_recommendations"] = learning_recommendations
        
        # ===== 步骤3: 生成执行计划 =====
        plan = await self._generate_plan_with_llm(
            query=query,
            intent=intent,
            completed_stages=completed_stages,
            job_id=job_id,
            workspace=workspace,
            learning_recommendations=learning_recommendations,
            preflight_result=preflight_result
        )
        
        # 更新状态 - 包含所有三部分，同时提供扁平化的stages字段
        state.results["task_plan"] = {
            "valid": True,
            "preflight_validation": preflight_result,
            "execution_plan": plan,
            "learning_recommendations": learning_recommendations,
            # 扁平化字段，方便前端直接访问
            "stages": plan.get("stages", []),
            "total_stages": plan.get("total_stages", 0),
            "prefix": plan.get("prefix", ""),
            "gene_family": plan.get("gene_family", ""),
            "species": plan.get("species", ""),
            "estimated_total_time": plan.get("estimated_total_time", "")
        }
        
        # 添加消息
        total_time = sum(s.get("estimated_time", 10) for s in plan.get("stages", []))
        warning_count = len([i for i in preflight_result["issues"] if i["severity"] == "warning"])
        info_count = len([i for i in preflight_result["issues"] if i["severity"] == "info"])
        
        status_msg = f"✅ 预检验证通过"
        if warning_count > 0:
            status_msg += f" ({warning_count}个警告)"
        status_msg += f" | 已生成执行计划: {len(plan.get('stages', []))}个阶段, 预估{total_time}分钟"
        if learning_recommendations:
            status_msg += f" | 已应用{len(learning_recommendations)}个阶段的学习推荐参数"
        
        state.messages.append({
            "role": "planning",
            "content": status_msg,
        })
        
        return state
    
    # ==================== PreFlight 验证方法 ====================
    
    async def _run_preflight_validation(self, intent: Dict, job_id: str, prefix: str, workspace: str) -> Dict:
        """
        执行PreFlight预检验证
        
        Returns:
            验证结果字典,包含valid, issues, stage_status等
        """
        issues = []
        stage_status = {}
        
        workspace_path = Path(workspace) if workspace else (self.workspace_root / job_id if job_id else self.workspace_root)
        input_dir = self.input_root / job_id if job_id else self.input_root
        target_stages = complete_dependencies(intent.get("stages", ["identify"]))
        for stage_id in reversed(target_stages):
            if stage_id not in WGS_PREFIX_CONSUMER_STAGES:
                continue
            try:
                prefix = resolve_unique_wgs_prefix(
                    stage=stage_id,
                    job_id=job_id,
                    params=intent.get("params", {}),
                    workspace=workspace_path,
                )
                intent["prefix"] = prefix
            except WgsUpstreamManifestError as exc:
                if str(exc) == f"no complete WGS authority prefix found for {stage_id}":
                    continue
                issues.append({
                    "severity": "error",
                    "category": "input",
                    "code": "invalid_wgs_manifest_prefix",
                    "message": str(exc),
                    "suggestion": "确认该任务只有一套完整的 WGS 上游 Manifest",
                    "auto_fixable": False,
                })
            break
        context_index = None
        if job_id:
            try:
                context_index = build_project_context(job_id=job_id, prefix=prefix, project_root=self.project_root)
            except Exception as exc:
                issues.append({
                    "severity": "warning",
                    "category": "context",
                    "message": f"项目上下文索引构建失败: {exc}",
                    "suggestion": "将退回旧版文件检查逻辑；如重复误报缺输入，请检查 job 目录结构和 manifest",
                    "auto_fixable": False,
                })
        
        for stage_id in target_stages:
            print(f"[PreFlight] 验证阶段: {stage_id}")

            if not is_stage_executable(stage_id):
                issues.append({
                    "severity": "error",
                    "category": "unsupported",
                    "message": f"阶段 {stage_id} 暂未接入标准执行器",
                    "suggestion": "请选择已接入执行器的分析阶段，或先为该阶段补充 pipeline_backend.run_stage 分支和标准 manifest",
                    "auto_fixable": False
                })
                continue
            
            # 1. 先检查已有输出(manifest)。若阶段已完成，直接跳过输入/依赖校验，
            # 避免“已完成阶段却因缺输入被误报失败”的口径冲突。
            status = self._check_stage_status_preflight(stage_id, prefix, workspace_path)
            stage_status[stage_id] = status
            
            if status.status in ["success", "partial_success"]:
                print(f"[PreFlight] 阶段 {stage_id} 已完成 ({status.status})")
                issues.append({
                    "severity": "info",
                    "category": "dependency",
                    "message": f"阶段 {stage_id} 已完成",
                    "suggestion": "该阶段已完成,可跳过执行",
                    "auto_fixable": True,
                    "fix_action": "skip_stage"
                })
                continue

            # 2. 阶段未完成时再检查依赖和输入
            dep_issues = self._check_dependencies(stage_id, prefix, workspace_path)
            issues.extend(dep_issues)

            input_issues = self._check_input_files(
                stage_id,
                input_dir,
                workspace_path,
                prefix,
                context_index,
                planned_stages=target_stages,
            )
            issues.extend(input_issues)
            if stage_id == "gwas_fmt":
                for issue in input_issues:
                    resolution = issue.get("resolver") if isinstance(issue, dict) else None
                    if not isinstance(resolution, dict):
                        continue
                    decision = (resolution.get("decisions") or {}).get("external_vcf") or {}
                    intent_params = intent.setdefault("params", {})
                    if decision.get("resolved") and decision.get("path"):
                        intent_params["gwasVcf"] = decision["path"]
                    candidates = resolution.get("candidates") or []
                    if candidates:
                        intent_params["gwasVcfCandidates"] = candidates
                    break
        
        # 4. 检查资源
        resource_issues = self._check_resources()
        issues.extend(resource_issues)

        deduped_issues = []
        seen_issues = set()
        for issue in issues:
            issue_key = (
                issue.get("severity"),
                issue.get("category"),
                issue.get("message"),
            )
            if issue_key in seen_issues:
                continue
            seen_issues.add(issue_key)
            deduped_issues.append(issue)
        issues = deduped_issues
        
        # 判断整体有效性
        errors = [i for i in issues if i["severity"] == "error"]
        warnings = [i for i in issues if i["severity"] == "warning"]
        
        valid = len(errors) == 0
        
        return {
            "valid": valid,
            "issues": issues,
            "stage_status": {
                stage: {
                    "status": status.status,
                    "manifest_path": str(status.manifest_path) if status.manifest_path else None,
                    "output_files": status.output_files,
                    "missing_outputs": status.missing_outputs
                }
                for stage, status in stage_status.items()
            },
            "stage_status_summary": {
                stage: {
                    "status": status.status,
                    "manifest_path": str(status.manifest_path) if status.manifest_path else None,
                    "artifact_count": len(status.output_files or []),
                    "missing_count": len(status.missing_outputs or []),
                }
                for stage, status in stage_status.items()
            },
            "summary": {
                "total_stages": len(target_stages),
                "errors": len(errors),
                "warnings": len(warnings),
                "completed_stages": sum(1 for s in stage_status.values() if s.status == "success")
            }
        }
    
    def _check_dependencies(self, stage_id: str, prefix: str, workspace: Path) -> List[Dict]:
        """检查依赖阶段"""
        issues = []
        deps = self.STAGE_DEPENDENCIES.get(stage_id, [])
        
        for dep in deps:
            dep_status = self._check_stage_status_preflight(dep, prefix, workspace)
            if dep_status.status not in ["success", "partial_success"]:
                # 将依赖未完成从 error 改为 warning
                # 因为 _generate_plan_with_llm 会自动添加依赖阶段
                issues.append({
                    "severity": "warning",  # 改为 warning 而不是 error
                    "category": "dependency",
                    "message": f"依赖阶段 {dep} 未完成 (状态: {dep_status.status})",
                    "suggestion": f"将自动添加 {dep} 阶段到执行计划",
                    "auto_fixable": True,  # 可自动修复
                    "fix_action": "add_dependency"
                })
        
        return issues

    def _semantic_species_for_need(
        self,
        need: Any,
        fallback_species: str,
        context_index: ProjectContextIndex,
    ) -> str:
        roles = set(getattr(need, "source_roles", []) or [])
        if "reference" in roles:
            refs = context_index.reference_species or []
            return refs[0] if len(refs) == 1 else ""
        if "comparative" in roles:
            comps = context_index.comparative_species or []
            return comps[0] if len(comps) == 1 else ""
        return fallback_species
    
    def _check_input_files(
        self,
        stage_id: str,
        input_dir: Path,
        workspace: Path,
        prefix: str,
        context_index: Optional[ProjectContextIndex] = None,
        planned_stages: Optional[List[str]] = None,
    ) -> List[Dict]:
        """检查输入文件"""
        issues = []
        if stage_id == "gwas_fmt":
            project_root = Path(getattr(self, "project_root", input_dir.parent.parent))
            resolution = resolve_gwas_fmt_input(
                job_id=workspace.name,
                prefix=prefix,
                params={},
                project_root=project_root,
            )
            decision = (resolution.get("decisions") or {}).get("external_vcf") or {}
            if resolution.get("valid") and decision.get("resolved"):
                return [{
                    "severity": "info",
                    "category": "input_resolved",
                    "code": "gwas_external_vcf_resolved",
                    "message": "gwas_fmt 输入 external_vcf 已解析",
                    "suggestion": decision.get("path", ""),
                    "auto_fixable": False,
                    "resolver": resolution,
                }]
            issue = dict((resolution.get("issues") or [{}])[0])
            issue.setdefault("severity", "warning" if resolution.get("needs_confirmation") else "error")
            issue.setdefault("category", "parameter_confirmation" if resolution.get("needs_confirmation") else "input")
            issue["suggestion"] = (
                "请选择一个有效 VCF" if resolution.get("needs_confirmation")
                else "请上传有效 VCF，或修复权威 Manifest 后重试"
            )
            issue["auto_fixable"] = bool(resolution.get("needs_confirmation"))
            issue["fix_action"] = "select_external_vcf" if resolution.get("needs_confirmation") else None
            issue["resolver"] = resolution
            return [issue]
        if stage_id == "gwas_cov":
            project_root = Path(getattr(self, "project_root", input_dir.parent.parent))
            resolution = resolve_root_pair(
                project_root=project_root,
                job_id=workspace.name,
                first_role="admixture_q",
                second_role="sample_fam",
            )
            if resolution.get("valid"):
                return [
                    {
                        "severity": "info",
                        "category": "input_resolved",
                        "code": "gwas_cov_root_input_resolved",
                        "role": role,
                        "message": f"gwas_cov 输入 {role} 已解析",
                        "suggestion": str((resolution.get(decision_key) or {}).get("path") or ""),
                        "auto_fixable": False,
                        "resolver": resolution,
                    }
                    for role, decision_key in (("admixture_q", "first"), ("sample_fam", "second"))
                ]
            needs_confirmation = bool(resolution.get("needs_confirmation"))
            return [
                {
                    **dict(issue),
                    "severity": "warning" if needs_confirmation else "error",
                    "category": "parameter_confirmation" if needs_confirmation else "input",
                    "suggestion": (
                        "请选择唯一且同源的 Q/FAM 输入对"
                        if needs_confirmation
                        else "请修复 Q/FAM 权威记录或提供可审计的基因型 lineage"
                    ),
                    "auto_fixable": needs_confirmation,
                    "fix_action": "select_gwas_cov_root_pair" if needs_confirmation else None,
                    "needs_confirmation": needs_confirmation,
                    "resolver": resolution,
                }
                for issue in (resolution.get("issues") or [])
            ]
        if stage_id == "wgs_align":
            planned_stage_set = set(planned_stages or [])
            planned_upstreams = [
                upstream for upstream in ("wgs_qc", "wgs_index")
                if upstream in planned_stage_set
            ]
            if planned_upstreams:
                return [{
                    "severity": "warning",
                    "category": "dependency",
                    "code": "wgs_upstream_will_be_rebuilt",
                    "message": (
                        "wgs_align 的上游 authority 将由本次计划重建: "
                        + ", ".join(planned_upstreams)
                    ),
                    "suggestion": "先执行上游 WGS 阶段，再重新绑定其 Manifest",
                    "auto_fixable": True,
                    "fix_action": "add_dependency",
                }]
            try:
                materialize_wgs_identity_params(
                    stage="wgs_align",
                    job_id=workspace.name,
                    prefix=prefix,
                    params={},
                    workspace=workspace,
                )
            except WgsUpstreamManifestError as exc:
                return [{
                    "severity": "error",
                    "category": "input",
                    "code": "invalid_wgs_manifest_identity",
                    "message": str(exc),
                    "suggestion": "修复 wgs_qc 或 wgs_index 权威 Manifest 后重试",
                    "auto_fixable": False,
                }]
            return [{
                "severity": "info",
                "category": "input_resolved",
                "code": "wgs_align_manifest_inputs_resolved",
                "message": "wgs_align 输入已从权威 Manifest 解析",
                "auto_fixable": False,
            }]
        planned_stage_set = set(planned_stages or [])
        capability = get_stage_capability(stage_id)
        if capability and context_index:
            resolver = ArtifactResolver(context_index)
            species = self._species_from_prefix_or_context(prefix, context_index)
            family = self._family_from_prefix_or_context(prefix, context_index)
            for need in capability.consumes:
                if not need.required:
                    continue
                need_species = self._semantic_species_for_need(need, species, context_index)
                decision = resolver.resolve_need(stage_id, need, species=need_species, family=family)
                if decision and decision.resolved:
                    if self._semantic_target_selection_needed(stage_id, need.role, species, decision):
                        issues.append({
                            "severity": "warning",
                            "category": "scope_selection",
                            "message": f"{stage_id} 检测到多个可用目标物种蛋白，需要确认目标物种",
                            "suggestion": "请在执行确认弹窗中选择 target species；系统不应在 Multi prefix 下静默任选一个物种。",
                            "auto_fixable": True,
                            "fix_action": "select_target_species",
                            "resolver": decision.to_dict(),
                        })
                        continue
                    issues.append({
                        "severity": "info",
                        "category": "input_resolved",
                        "message": f"{stage_id} 输入 {need.role} 已解析",
                        "suggestion": decision.selected.path if decision.selected else "",
                        "auto_fixable": False,
                        "resolver": decision.to_dict(),
                    })
                    continue
                confirmation_issue = _confirmation_bound_input_issue(stage_id, need.role)
                if confirmation_issue:
                    confirmation_issue["resolver"] = decision.to_dict() if decision else None
                    issues.append(confirmation_issue)
                    continue
                producer = self._semantic_input_producer(need.role)
                if producer and producer != stage_id and producer in planned_stage_set:
                    issues.append({
                        "severity": "warning",
                        "category": "dependency",
                        "message": f"{stage_id} 所需输入 {need.role} ({need.kind}) 将由上游阶段 {producer} 生成",
                        "suggestion": f"规划器会先执行 {producer}，再执行 {stage_id}。",
                        "auto_fixable": True,
                        "fix_action": "add_dependency",
                        "resolver": decision.to_dict() if decision else None,
                    })
                    continue
                issues.append({
                    "severity": "error",
                    "category": "input",
                    "message": f"缺少 {stage_id} 所需输入: {need.role} ({need.kind})",
                    "suggestion": self._semantic_input_suggestion(stage_id, need.role, need.kind, decision),
                    "auto_fixable": self._semantic_input_auto_fixable(need.role),
                    "fix_action": "artifact_resolve_or_prepare" if self._semantic_input_auto_fixable(need.role) else None,
                    "resolver": decision.to_dict() if decision else None,
                })
            return issues

        required = self.REQUIRED_INPUTS.get(stage_id, [])
        
        for file_type in required:
            confirmation_issue = _confirmation_bound_input_issue(stage_id, file_type)
            if confirmation_issue:
                issues.append(confirmation_issue)
                continue
            found = self._find_input_file(input_dir, file_type, workspace, prefix)
            if not found:
                issues.append({
                    "severity": "error",  # 改为 error，阻止执行
                    "category": "input",
                    "message": f"缺少 {self._format_file_type_label(file_type)} 类型输入文件",
                    "suggestion": self._input_suggestion(file_type),
                    "auto_fixable": False
                })
        
        return issues
    
    def _find_input_file(self, input_dir: Path, file_type: str, workspace: Path | None = None, prefix: str = "") -> bool:
        """查找输入文件"""
        patterns = {
            "hmm": ["*.hmm", "*.hmmprofile"],
            "protein": ["*.faa", "*.fa", "*.fasta", "*.pep", "*.pep.fa", "*.pep.fasta", "*.protein.fa", "*.protein.fasta", "*.sequence_extract.pep.fa", "*.sequence_extract.pep.fasta", "*.ncbi_fetch.protein.fa"],
            "gene_list": ["*.gene", "*.txt", "*.list", "*.identified_members.id", "*.final_members.id"],
            "genome": ["*.fna", "*.genome", "*genome*.fa", "*genome*.fasta", "*genome*.fna"],
            "genome_or_hisat2_index": ["*genome*.fa", "*genome*.fasta", "*genome*.fna", "*.genome.*.ht2", "*.1.ht2"],
            "gff": ["*.gff", "*.gff3", "*.gtf"],
            "blast": ["*.blast", "*.blastp"],
            "expression_matrix": [
                "*.fpkm_matrix.txt", "*_fpkm_matrix.txt", "*.fpkm.txt", "*_fpkm.txt",
                "*.salmon.tpm_matrix.txt", "*.kallisto.tpm_matrix.txt", "*.rsem.tpm_matrix.txt",
                "*.tpm_matrix.txt", "*_tpm_matrix.txt", "*.tpm.txt", "*_tpm.txt",
            ],
            "expression_gene_id": ["*geneID*", "*.identified_members.id", "*.final_members.id"],
            "sample_txt": ["sample.txt"],
            "sample_info": ["sample.txt", "*.sample", "*.info"],
            "sample_config": ["samples.yaml", "*.yaml", "*.yml"],
            "fastq": ["*.fastq", "*.fastq.gz", "*.fq", "*.fq.gz"],
            "bam": ["*.bam", "*.sam"],
            "counts": ["*.counts", "*.txt", "*.tsv"],
            "de_results": ["*.de", "*.csv", "*.tsv"]
        }

        search_dirs = [input_dir]
        if workspace:
            search_dirs.extend([workspace / "final", workspace])

        for pattern in patterns.get(file_type, [f"*{file_type}*"]):
            for search_dir in search_dirs:
                if search_dir.exists() and list(search_dir.glob(pattern)):
                    return True
        if file_type == "gene_list" and workspace and prefix:
            if (workspace / "final" / f"{prefix}.identified_members.id").exists() or (workspace / "final" / f"{prefix}.final_members.id").exists():
                return True
        return False

    def _species_from_prefix_or_context(self, prefix: str, context: ProjectContextIndex) -> str:
        if prefix and "." in prefix:
            species = prefix.split(".", 1)[0].strip()
            if species and species.lower() not in {"multi", "target", "ref", "reference", "comparative"}:
                return species
        if context.target_species:
            # Multiple target species are a scope-selection situation, not a
            # license to silently choose the first proteome during preflight.
            return context.target_species[0] if len(context.target_species) == 1 else ""
        return ""

    def _family_from_prefix_or_context(self, prefix: str, context: ProjectContextIndex) -> str:
        if prefix and "." in prefix:
            family = prefix.split(".", 1)[1].strip()
            if family:
                return family
        return context.family or ""

    def _semantic_input_auto_fixable(self, role: str) -> bool:
        return role in {
            "target_proteome",
            "reference_family_pep",
            "family_members_pep",
            "family_members_id",
            "target_gff",
            "target_genome",
            "stage_manifests",
        }

    def _semantic_input_producer(self, role: str) -> str:
        producers = {
            "tree": "phylogeny",
            "family_members_pep": "identify",
            "family_members_id": "identify",
            "gene_subfamily": "reference_classification",
            "target_proteome": "genome_prepare",
            "target_gff": "genome_prepare",
            "target_genome": "genome_prepare",
            "prepared_protein": "genome_prepare",
            "prepared_cds": "genome_prepare",
            "prepared_gff": "genome_prepare",
            "sorted_vcf": "gwas_fmt",
            "kinship_matrix": "gwas_kinship",
            "trait_table": "gwas_trait",
            "covariate_table": "gwas_cov",
            "mlm_pvalues": "gwas_mlm",
            "cmlm_pvalues": "gwas_mlm",
            "emmax_pvalues": "gwas_emmax",
            "wgs_sample_config": "wgs_qc",
            "clean_reads_r1": "wgs_qc",
            "clean_reads_r2": "wgs_qc",
            "reference_fasta": "wgs_index",
            "reference_fai": "wgs_index",
            "reference_dict": "wgs_index",
            "bwa_mem2_index_component": "wgs_index",
            "sorted_bam": "wgs_align",
            "sorted_bam_index": "wgs_align",
            "markdup_bam": "wgs_markdup",
            "markdup_bam_index": "wgs_markdup",
            "bam_flagstat": "wgs_bam_qc",
            "bam_depth_summary": "wgs_bam_qc",
            "bam_qc_summary": "wgs_bam_qc",
            "bam_qc_report": "wgs_bam_qc",
            "gvcf": "wgs_haplotype_calling",
            "gvcf_index": "wgs_haplotype_calling",
            "combined_gvcf": "wgs_gvcf_combine",
            "combined_gvcf_index": "wgs_gvcf_combine",
            "joint_vcf": "wgs_joint_genotyping",
            "joint_vcf_index": "wgs_joint_genotyping",
            "selected_snp_vcf": "wgs_variant_select",
            "selected_snp_vcf_index": "wgs_variant_select",
            "selected_indel_vcf": "wgs_variant_select",
            "selected_indel_vcf_index": "wgs_variant_select",
            "pass_snp_vcf": "wgs_variant_filter",
            "pass_snp_vcf_index": "wgs_variant_filter",
            "imputed_snp_vcf": "wgs_impute",
            "imputed_snp_vcf_index": "wgs_impute",
        }
        return producers.get(role, "")

    def _semantic_input_suggestion(self, stage: str, role: str, kind: str, decision: Any = None) -> str:
        suggestions = {
            "target_proteome": "请在 config.xlsx 中配置目标物种 pep，或上传目标物种蛋白 FASTA；若只有 genome+gff，请先执行数据准备。",
            "reference_family_pep": "请上传或下载参考家族蛋白，并命名/软链接为 {Species}.{Family}.pep.fa。",
            "family_members_pep": "请先完成 identify，或确认 final/{prefix}.final_members.pep.fa / scoped phylogeny 输入存在。",
            "family_members_id": "请先完成 identify，或提供 final_members.id。",
            "target_gff": "请在 config.xlsx 中配置目标物种 GFF/GFF3，或先执行数据准备生成 {Species}_final.gff3。",
            "target_genome": "请在 config.xlsx 中配置目标物种 genome FASTA，或先执行数据准备生成 {Species}.genome.fasta。",
            "plant_tf_motif_database": "请确认项目 data/tfbs/jaspar2026/combined/JASPAR2026_CORE_plants_non-redundant.meme 存在，或配置 PlantTFDB/JASPAR MEME 资源。",
            "tree": "请先完成 phylogeny，或提供 final/{prefix}.tree.nwk。",
            "reference_subfamily": "请上传参考物种亚家族标签文件，或在 input/final 中放置可识别的 subfamily 文件。",
            "pfam_database": "请确认项目 data/hmm/Pfam-A.hmm 及 hmmpress 索引存在。",
            "stage_manifests": "请至少先完成一个分析阶段，或修复 final 中已有产物对应的 manifest。",
        }
        base = suggestions.get(role, f"请提供 {stage} 阶段所需的 {kind} 输入。")
        if decision and getattr(decision, "candidates", None):
            tried = [c.path for c in decision.candidates[:3]]
            if tried:
                base += " 已尝试: " + "; ".join(tried)
        return base

    def _semantic_target_selection_needed(self, stage: str, role: str, species: str, decision: Any) -> bool:
        if stage != "identify" or role != "target_proteome" or species:
            return False
        species_tokens = set()
        for candidate in getattr(decision, "candidates", []) or []:
            if not getattr(candidate, "exists", False):
                continue
            name = Path(candidate.path).name
            if any(token in name.lower() for token in ["ref", "final_members", "final_gfmembers"]):
                continue
            token = name.split(".", 1)[0].split("_", 1)[0]
            if token:
                species_tokens.add(token)
        return len(species_tokens) > 1

    def _format_file_type_label(self, file_type: str) -> str:
        labels = {
            "expression_matrix": "表达矩阵，Expression(使用FPKM) 优先使用 *.fpkm_matrix.txt；没有 FPKM 时才 fallback 到 TPM",
            "expression_gene_id": "基因ID映射文件 (*geneID*)",
            "sample_txt": "样本信息文件 (sample.txt)",
            "sample_config": "RNA-seq样本配置文件 (samples.yaml)",
            "genome_or_hisat2_index": "基因组FASTA或HISAT2索引",
            "gff": "基因组注释文件 (GFF/GFF3/GTF)",
            "genome": "基因组FASTA",
            "fastq": "FASTQ",
        }
        return labels.get(file_type, file_type)

    def _input_suggestion(self, file_type: str) -> str:
        suggestions = {
            "expression_matrix": "请上传表达矩阵文件；Expression(使用FPKM) 优先提供 *.fpkm_matrix.txt，若没有再使用 TPM fallback",
            "expression_gene_id": "请上传 *geneID* 文件，或先完成能生成 geneID 的上游阶段",
            "sample_txt": "请上传 sample.txt 到 input/<job_id> 目录",
            "sample_config": "请上传 samples.yaml 到 input/<job_id> 目录，并填写每个样本或 rnaseq.library_strandness 的链特异性",
            "genome_or_hisat2_index": "请上传基因组 FASTA，或提供 *.genome.*.ht2 / *.1.ht2 HISAT2 索引文件",
            "gff": "请上传 GFF/GFF3/GTF 注释文件到 input/<job_id> 目录",
            "genome": "请上传基因组 FASTA 文件到 input/<job_id> 目录",
            "fastq": "请上传 RNA-seq FASTQ 文件到 input/<job_id> 目录",
        }
        return suggestions.get(file_type, f"请准备 {file_type} 文件并上传到 input 目录")
    
    def _single_species_identify_status(self, stage_id: str, prefix: str, workspace: Path) -> Optional[StageStatus]:
        """Treat species-specific member files from a multi-target identify run as completed identify input."""
        if stage_id != "identify" or not prefix or "." not in prefix:
            return None
        species, family = prefix.split(".", 1)
        if not species or species in {"Target", "Multi"} or not family:
            return None
        final_dir = workspace / "final"
        pep = final_dir / f"Target.{species}.{family}.final_members.pep.fa"
        ids = final_dir / f"Target.{species}.{family}.final_members.id"
        if not (pep.exists() and pep.stat().st_size > 0 and ids.exists() and ids.stat().st_size > 0):
            fallback_pep = workspace / "identify" / species / "final" / f"{species}.final_gfmembers.pep.fa"
            fallback_ids = workspace / "identify" / species / "final" / f"{species}.final_gfmembers.id"
            if fallback_pep.exists() and fallback_pep.stat().st_size > 0 and fallback_ids.exists() and fallback_ids.stat().st_size > 0:
                pep, ids = fallback_pep, fallback_ids
            else:
                return None
        manifest = final_dir / f"Target.{family}.identify.manifest.json"
        return StageStatus(
            stage=stage_id,
            status="success",
            manifest_path=manifest if manifest.exists() else None,
            output_files=[str(pep), str(ids)],
            missing_outputs=[],
        )

    def _check_stage_status_preflight(self, stage_id: str, prefix: str, workspace: Path, stage_params: Optional[Dict[str, Any]] = None) -> StageStatus:
        """检查阶段执行状态(PreFlight版本)，以统一 manifest/artifacts 校验为准。"""
        single_species_identify = self._single_species_identify_status(stage_id, prefix, workspace)
        if single_species_identify:
            return single_species_identify
        try:
            if stage_params:
                from gfa_core.mas.tools.stage_identity import resolve_stage_identity, get_stage_status_for_identity

                identity = resolve_stage_identity(
                    stage=stage_id,
                    prefix=prefix,
                    params=stage_params,
                    job_id=workspace.name,
                    workspace_root=workspace.parent,
                )
                report = get_stage_status_for_identity(
                    job_id=workspace.name,
                    identity=identity,
                    workspace_root=workspace.parent,
                    strict_identity=True,
                )
            else:
                from gfa_core.mas.tools.stage_status import get_stage_status

                report = get_stage_status(
                    job_id=workspace.name,
                    prefix=prefix,
                    stage=stage_id,
                    workspace_root=workspace.parent,
                )
            output_files = []
            if report.manifest:
                artifacts = report.manifest.get("artifacts", {}) or {}
                if isinstance(artifacts, dict):
                    output_files = list(artifacts.keys())
                elif isinstance(artifacts, list):
                    output_files = [
                        str(item.get("artifact") or item.get("name") or idx)
                        for idx, item in enumerate(artifacts)
                        if isinstance(item, dict)
                    ]

            return StageStatus(
                stage=stage_id,
                status=report.manifest_status if report.completed else report.status,
                manifest_path=report.manifest_path if report.manifest_path and report.manifest_path.exists() else None,
                output_files=output_files,
                missing_outputs=report.missing_artifacts + report.errors,
            )
        except Exception as e:
            print(f"[PlanningAgentV2] 统一阶段状态检查失败: {e}")
            return StageStatus(
                stage=stage_id,
                status="not_found",
                manifest_path=None,
                output_files=[],
                missing_outputs=[str(e)]
            )

    def _semantic_upstream_stages(
        self,
        stage_id: str,
        prefix: str,
        job_id: str,
        intent_params: Dict[str, Any],
    ) -> List[str]:
        """Infer upstream stages from unresolved semantic artifacts.

        Manifest success alone is not enough for scoped workflows.  For example,
        reference_classification may require a tree for the current
        target/reference scope even when an older phylogeny manifest exists.
        """
        capability = get_stage_capability(stage_id)
        if not capability or not job_id:
            return []

        role_to_stage = {
            "tree": "phylogeny",
            "family_members_pep": "identify",
            "family_members_id": "identify",
            "target_proteome": "genome_prepare",
            "target_gff": "genome_prepare",
            "target_genome": "genome_prepare",
        }
        try:
            context = build_project_context(job_id=job_id, prefix=prefix, project_root=self.project_root)
            resolver = ArtifactResolver(context)
        except Exception as exc:
            print(f"[PlanningAgentV2] semantic context unavailable for {stage_id}: {exc}")
            return []

        family = str(
            intent_params.get("geneFamily")
            or intent_params.get("family")
            or context.family
            or (prefix.split(".")[-1] if "." in prefix else "")
        ).strip()
        target_species = intent_params.get("targetSpecies") or intent_params.get("target_species") or context.target_species or []
        if isinstance(target_species, str):
            target_species = [item.strip() for item in target_species.replace("，", ",").split(",") if item.strip()]
        species = target_species[0] if len(target_species) == 1 else ""
        if not species and "." in prefix:
            prefix_species = prefix.split(".", 1)[0]
            if prefix_species.lower() not in {"multi", "target"}:
                species = prefix_species

        missing_upstream: List[str] = []
        for need in capability.consumes:
            if not need.required:
                continue
            need_species = self._semantic_species_for_need(need, species, context)
            decision = resolver.resolve_need(stage_id, need, species=need_species, family=family)
            if decision and decision.resolved:
                continue
            upstream = role_to_stage.get(need.role)
            if upstream and upstream != stage_id and upstream not in missing_upstream:
                missing_upstream.append(upstream)
                print(
                    f"[PlanningAgentV2] semantic dependency for {stage_id}: "
                    f"missing {need.role} -> add {upstream}"
                )
        return missing_upstream
    
    def _check_resources(self) -> List[Dict]:
        """检查系统资源"""
        issues = []
        
        # CPU 检查
        cpu_count = os.cpu_count() or 1
        if cpu_count < 4:
            issues.append({
                "severity": "warning",
                "category": "resource",
                "message": f"CPU 核数较少: {cpu_count}",
                "suggestion": "建议至少 4 核 CPU",
                "auto_fixable": False
            })
        
        # 磁盘空间检查
        try:
            mem_info = shutil.disk_usage("/")
            mem_gb = mem_info.free / (1024**3)
            if mem_gb < 2:
                issues.append({
                    "severity": "warning",
                    "category": "resource",
                    "message": f"磁盘空间不足: {mem_gb:.1f} GB",
                    "suggestion": "建议至少 10 GB 磁盘空间",
                    "auto_fixable": False
                })
        except:
            pass
        
        return issues
    
    # ==================== Learning 参数推荐方法 ====================
    
    async def _get_learning_recommendations(self, target_stages: List[str]) -> Dict[str, Dict]:
        """
        获取Learning参数推荐
        
        Args:
            target_stages: 目标阶段列表
            
        Returns:
            各阶段的参数推荐字典
        """
        recommendations = {}
        
        for stage in target_stages:
            # 从历史记录中获取推荐参数
            stage_history = [h for h in self.execution_history 
                            if h.get("stage") == stage and h.get("success")]
            
            if len(stage_history) >= 3:
                # 有足够历史数据，分析最优参数
                params = self._analyze_best_params(stage, stage_history)
                recommendations[stage] = {
                    "confidence": "high" if len(stage_history) >= 10 else "medium",
                    "params": params,
                    "message": f"基于 {len(stage_history)} 次成功执行推荐"
                }
            else:
                # 使用默认参数
                recommendations[stage] = {
                    "confidence": "low",
                    "params": self.DEFAULT_LEARNING_PARAMS.get(stage, {}),
                    "message": "历史数据不足，使用默认参数"
                }
        
        return recommendations
    
    def _analyze_best_params(self, stage: str, stage_history: List[Dict]) -> Dict:
        """分析最优参数"""
        # 简单实现：返回最常用的参数组合
        if not stage_history:
            return self.DEFAULT_LEARNING_PARAMS.get(stage, {})
        
        # 取最近一次成功的参数
        latest = stage_history[-1]
        return latest.get("params", self.DEFAULT_LEARNING_PARAMS.get(stage, {}))
    
    async def _generate_plan_with_llm(
        self, 
        query: str, 
        intent: Dict,
        completed_stages: List[str],
        job_id: str = "",
        workspace: str = "",
        learning_recommendations: Dict = None,
        preflight_result: Dict = None
    ) -> Dict[str, Any]:
        """使用LLM生成执行计划"""
        
        target_stages = complete_dependencies(intent.get("stages", ["identify"]))
        target_stages = _append_combo_for_two_evidence(target_stages, intent)
        task_kind = intent.get("task_kind") or intent.get("taskType") or "pipeline"
        gene_family = intent.get("gene_family", "")
        species = intent.get("species", "")
        prefix = intent.get("prefix") or (f"{species}.{gene_family}" if species and gene_family else "")
        if self._is_reference_classification_evidence_plan(query, target_stages, intent):
            target_stages = self._normalize_reference_classification_evidence_plan(target_stages, intent)
        forced_rerun_stages: set[str] = set()
        direct_combo_query = (
            "combo" in target_stages
            and "reference_classification" not in target_stages
            and ("combo" in (query or "").lower() or "组合图" in (query or ""))
        )
        if direct_combo_query and job_id:
            target_stages, forced_rerun_stages = self._normalize_direct_combo_with_subfamily_gate(
                query=query,
                target_stages=target_stages,
                intent=intent,
                prefix=prefix,
                workspace=Path(workspace),
            )

        is_rnaseq_plan = bool(target_stages) and all(stage.startswith("rnaseq") for stage in target_stages)
        is_utility_plan = task_kind == "utility" or all(is_utility_stage(stage) for stage in target_stages)
        is_repair_plan = task_kind == "repair" or all(is_repair_stage(stage) for stage in target_stages)

        unsupported_stages = get_unsupported_stages(target_stages)
        if unsupported_stages:
            return {
                "prefix": prefix,
                "gene_family": gene_family,
                "species": species,
                "stages": [],
                "total_stages": 0,
                "estimated_time": "0分钟",
                "description": f"以下阶段暂未接入标准执行器: {', '.join(unsupported_stages)}",
                "parallel_groups": [],
                "valid": False,
                "error": f"Unsupported stages: {', '.join(unsupported_stages)}",
                "unsupported_stages": unsupported_stages,
            }
        
        # 使用传入的 workspace 和 job_id
        if job_id and workspace:
            workspace = f"{workspace}"
        elif not workspace:
            workspace = str(WORKSPACE_ROOT / job_id) if job_id else str(WORKSPACE_ROOT)
        
        preflight_status = (preflight_result or {}).get("stage_status") or {}
        planning_status_params = {
            stage_name: self._stage_params_for_planning_status(stage_name, prefix, intent.get("params", {}) or {})
            for stage_name in target_stages
        }

        def status_summary(stage_name: str, status_obj) -> str:
            manifest_path = getattr(status_obj, "manifest_path", None)
            manifest_name = Path(str(manifest_path)).name if manifest_path else "None"
            output_count = len(getattr(status_obj, "output_files", []) or [])
            missing_count = len(getattr(status_obj, "missing_outputs", []) or [])
            return (
                f"status={getattr(status_obj, 'status', 'unknown')} "
                f"artifacts={output_count} missing={missing_count} manifest={manifest_name}"
            )

        def status_from_preflight(stage_name: str):
            cached = preflight_status.get(stage_name)
            if not isinstance(cached, dict):
                return None
            return StageStatus(
                stage=stage_name,
                status=str(cached.get("status") or "not_found"),
                manifest_path=Path(cached["manifest_path"]) if cached.get("manifest_path") else None,
                output_files=list(cached.get("output_files") or []),
                missing_outputs=list(cached.get("missing_outputs") or []),
            )

        def get_planning_status(stage_name: str):
            stage_params = planning_status_params.get(stage_name) or {}
            has_wgs_identity = stage_name.startswith("wgs_") and any(
                str(stage_params.get(key) or "").strip()
                for key in (
                    "wgsSampleSetId",
                    "wgs_sample_set_id",
                    "wgsReferenceId",
                    "wgs_reference_id",
                    "wgsAnalysisId",
                    "wgs_analysis_id",
                )
            )
            has_identity = bool(
                stage_params.get("scopeId")
                or stage_params.get("scope_id")
                or stage_params.get("analysisRoute")
                or stage_params.get("analysis_route")
                or has_wgs_identity
            )
            if stage_name.startswith("wgs_"):
                return self._check_stage_status_preflight(
                    stage_name,
                    prefix,
                    Path(workspace),
                    stage_params,
                ), "identity" if has_identity else "manifest"
            if has_identity:
                return self._check_stage_status_preflight(stage_name, prefix, Path(workspace), stage_params), "identity"
            cached_status = status_from_preflight(stage_name)
            if cached_status is not None:
                return cached_status, "preflight"
            return self._check_stage_status_preflight(stage_name, prefix, Path(workspace)), "manifest"

        # ===== 关键修改：基于 manifest 文件状态过滤阶段 =====
        remaining_stages = []
        for stage in target_stages:
            if is_utility_plan or (is_repair_plan and is_repair_stage(stage)):
                remaining_stages.append(stage)
                continue
            status, status_source = get_planning_status(stage)
            print(f"[PlanningAgentV2] Stage {stage} status from {status_source}: {status_summary(stage, status)}")
            
            if stage in forced_rerun_stages:
                print(f"[PlanningAgentV2] Stage {stage} forced by combo/subfamily gate, will re-execute")
                remaining_stages.append(stage)
            elif status.status == "success":
                # 成功完成，跳过
                print(f"[PlanningAgentV2] Stage {stage} already completed (success), skipping")
                continue
            elif status.status == "partial_success":
                # 部分成功，询问用户或继续（这里选择继续，让用户决定）
                print(f"[PlanningAgentV2] Stage {stage} partially completed, will re-execute")
                remaining_stages.append(stage)
            elif status.status == "failed":
                # 失败，重新执行
                print(f"[PlanningAgentV2] Stage {stage} failed previously, will re-execute")
                remaining_stages.append(stage)
            else:
                # not_found 或其他，执行
                remaining_stages.append(stage)
        
        requested_stage_set = {str(stage).strip() for stage in (intent.get("stages") or []) if str(stage).strip()}
        if requested_stage_set and remaining_stages and not any(stage in requested_stage_set for stage in remaining_stages):
            requested_statuses = {stage: get_planning_status(stage)[0] for stage in requested_stage_set}
            if all(status.status == "success" for status in requested_statuses.values()):
                print(
                    "[PlanningAgentV2] Requested stages already completed; dropping dependency-only plan: "
                    f"requested={sorted(requested_stage_set)}, dependencies={remaining_stages}"
                )
                remaining_stages = []

        # 回退：如果没有获取到 workspace，使用旧的 completed_stages 逻辑
        if not remaining_stages and not job_id:
            print(f"[PlanningAgentV2] Falling back to completed_stages logic")
            remaining_stages = [s for s in target_stages if s not in completed_stages]
        
        # 如果没有剩余阶段,返回空计划
        if not remaining_stages:
            return {
                "prefix": prefix,
                "gene_family": gene_family,
                "species": species,
                "stages": [],
                "total_stages": 0,
                "estimated_time": "0分钟",
                "description": "所有阶段已完成",
                "parallel_groups": []
            }
        
        semantic_deps_by_stage = {
            stage: self._semantic_upstream_stages(stage, prefix, job_id, intent.get("params", {}) or {})
            for stage in remaining_stages
        }

        # 检查依赖阶段是否已完成，如果已完成则不添加到计划
        # 【关键修复】使用 manifest 检查依赖阶段状态，而不是依赖 completed_stages
        stages_with_deps = []
        for stage in remaining_stages:
            deps = []
            for dep in self.DEPENDENCIES.get(stage, []) + semantic_deps_by_stage.get(stage, []):
                if dep not in deps:
                    deps.append(dep)
            # 只添加未完成的依赖阶段（通过 manifest 检查）
            for dep in deps:
                dep_status, dep_status_source = get_planning_status(dep)
                force_dep = dep in semantic_deps_by_stage.get(stage, [])
                if (force_dep or dep_status.status != "success") and dep not in stages_with_deps and dep not in remaining_stages:
                    stages_with_deps.append(dep)
                    reason = "semantic artifact missing" if force_dep else f"status: {dep_status.status}"
                    print(f"[PlanningAgentV2] 添加依赖阶段到计划: {dep} ({reason})")
                elif dep_status.status == "success":
                    print(f"[PlanningAgentV2] 依赖阶段已完成，跳过: {dep} ({dep_status_source}, {status_summary(dep, dep_status)})")
            stages_with_deps.append(stage)
        
        # 生成默认阶段配置（传入Learning推荐参数）
        stages = self._generate_default_stages(
            stages_with_deps,
            prefix,
            learning_recommendations,
            intent.get("params", {}),
        )
        completed_mlm_scopes = _gwas_mlm_scopes_from_completed_preflight(
            preflight_status,
            prefix,
            workspace,
        )
        if completed_mlm_scopes:
            for stage in stages:
                if stage.get("id") == "gwas_mlm":
                    stage.setdefault("params", {}).update(completed_mlm_scopes)
        if (intent.get("params", {}) or {}).get("subfamilyPipeline"):
            self._apply_reference_classification_evidence_stage_details(stages, intent.get("params", {}))
        if forced_rerun_stages:
            self._apply_direct_combo_subfamily_stage_details(stages, intent.get("params", {}), forced_rerun_stages)
        
        total_time = sum(s["estimated_time"] for s in stages)
        
        estimated_time = f"{total_time}分钟"

        if is_utility_plan:
            description = "执行工具型任务：" + "、".join(
                utility_display_name(stage, intent.get("params", {})) for stage in remaining_stages
            )
        elif is_repair_plan:
            description = f"执行受控修复任务：{', '.join(remaining_stages)}"
        elif is_rnaseq_plan:
            description = f"执行RNA-seq的{', '.join(remaining_stages)}分析"
            if "rnaseq_de" in remaining_stages:
                description += "；差异分析成功后会自动触发 Expression(使用FPKM) 绘图"
        else:
            description = f"执行{gene_family}基因家族的{', '.join(remaining_stages)}分析"

        return {
            "prefix": prefix,
            "gene_family": gene_family,
            "species": species,
            "task_kind": "utility" if is_utility_plan else "repair" if is_repair_plan else "pipeline",
            "stages": stages,
            "stages_detail": stages,
            "total_stages": len(stages),
            "execution_strategy": "single" if is_utility_plan or is_repair_plan else "parallel",
            "max_parallel": 1 if is_utility_plan or is_repair_plan else 4,
            "estimated_time": estimated_time,
            "estimated_total_time": estimated_time,
            "params": {
                "prefix": prefix,
                "cpu": max((s.get("params", {}).get("cpu", 8) for s in stages), default=8),
                **(intent.get("params", {}) or {}),
            },
            "description": description,
            "parallel_groups": []
        }
    

    def _is_reference_classification_evidence_plan(self, query: str, target_stages: List[str], intent: Dict[str, Any]) -> bool:
        if "reference_classification" not in target_stages:
            return False
        params = intent.get("params", {}) or {}
        if params.get("minimalReferenceClassification") or params.get("classificationOnly"):
            return False
        text = str(query or "").lower()
        refs = _as_param_list(params.get("referenceSpecies") or params.get("reference_species") or params.get("refSpeciesList"))
        return bool(refs or "亚家族" in str(query or "") or "subfamily" in text or "reference classification" in text)

    def _normalize_reference_classification_evidence_plan(self, target_stages: List[str], intent: Dict[str, Any]) -> List[str]:
        chain = ["identify", "phylogeny", "motif", "domain", "structure", "combo", "reference_classification"]
        present = set(target_stages) | set(chain)
        ordered = [stage for stage in chain if stage in present]
        ordered.extend(stage for stage in target_stages if stage not in ordered)
        params = intent.setdefault("params", {})
        params["subfamilyPipeline"] = True
        params["referenceClassificationRequiresEvidence"] = True
        params.setdefault("scopeMode", "target_plus_reference")
        intent["stages"] = ordered
        print(f"[PlanningAgentV2] reference_classification expanded to explicit evidence DAG: {ordered}")
        return ordered

    def _stage_params_for_planning_status(self, stage_id: str, prefix: str, intent_params: Dict[str, Any]) -> Dict[str, Any]:
        params = dict(intent_params or {})
        params.setdefault("prefix", prefix)
        if params.get("subfamilyPipeline"):
            params["scopeMode"] = _subfamily_scope_mode_from_params(params)
            comps_for_subfamily = _as_param_list(params.get("comparativeSpecies") or params.get("comparative_species"))
            if comps_for_subfamily:
                params["comparativeSpecies"] = comps_for_subfamily
                params["comparative_species"] = comps_for_subfamily
            elif "comparative" not in str(params.get("scopeMode") or ""):
                params["comparativeSpecies"] = []
                params["comparative_species"] = []
            if stage_id in {"phylogeny", "motif", "domain", "structure", "combo"}:
                params.pop("memberSetId", None)
                params.pop("member_set_id", None)
                params["analysisRoute"] = "subfamily_evidence"
                params["analysis_route"] = "subfamily_evidence"
            elif stage_id == "reference_classification":
                params.pop("memberSetId", None)
                params.pop("member_set_id", None)
                params["analysisRoute"] = "subfamily_prediction"
                params["analysis_route"] = "subfamily_prediction"
            elif stage_id == "identify":
                params["scopeMode"] = "target_only"
        enriched = enrich_stage_params_with_context(stage_id, params.get("prefix") or prefix, params)
        if params.get("subfamilyPipeline") and stage_id == "reference_classification":
            scope_id = str(enriched.get("scopeId") or enriched.get("scope_id") or "").strip()
            if scope_id:
                member_set = f"{sanitize_token(scope_id)}:subfamily_prediction:all_members"
                enriched["memberSetId"] = member_set
                enriched["member_set_id"] = member_set
                if isinstance(enriched.get("stageContext"), dict):
                    enriched["stageContext"]["member_set_id"] = member_set
                if isinstance(enriched.get("stage_context"), dict):
                    enriched["stage_context"]["member_set_id"] = member_set
        return enriched

    def _apply_reference_classification_evidence_stage_details(
        self,
        stages: List[Dict[str, Any]],
        intent_params: Dict[str, Any],
    ) -> None:
        evidence_stages = {"phylogeny", "motif", "domain", "structure", "combo"}
        for stage in stages:
            stage_id = stage.get("id")
            params = dict(intent_params or {})
            params.update(stage.get("params") or {})
            params["subfamilyPipeline"] = True
            params["referenceClassificationRequiresEvidence"] = True
            if stage_id in evidence_stages:
                params.pop("memberSetId", None)
                params.pop("member_set_id", None)
                params["analysisRoute"] = "subfamily_evidence"
                params["analysis_route"] = "subfamily_evidence"
                params["scopeMode"] = _subfamily_scope_mode_from_params(params)
                comps_for_subfamily = _as_param_list(params.get("comparativeSpecies") or params.get("comparative_species"))
                params["comparativeSpecies"] = comps_for_subfamily
                params["comparative_species"] = comps_for_subfamily
                if stage_id == "phylogeny":
                    stage["name"] = "亚家族证据系统发育分析"
                    stage["dependencies"] = ["identify"]
                elif stage_id in {"motif", "domain", "structure"}:
                    stage["dependencies"] = ["phylogeny"]
                elif stage_id == "combo":
                    stage["name"] = "亚家族证据组合图"
                    stage["dependencies"] = ["motif", "domain", "structure"]
            elif stage_id == "reference_classification":
                params.pop("memberSetId", None)
                params.pop("member_set_id", None)
                params["analysisRoute"] = "subfamily_prediction"
                params["analysis_route"] = "subfamily_prediction"
                params["scopeMode"] = _subfamily_scope_mode_from_params(params)
                comps_for_subfamily = _as_param_list(params.get("comparativeSpecies") or params.get("comparative_species"))
                params["comparativeSpecies"] = comps_for_subfamily
                params["comparative_species"] = comps_for_subfamily
                stage["name"] = "参考亚家族分类"
                stage["dependencies"] = ["combo"]
            elif stage_id == "identify":
                params["scopeMode"] = "target_only"
            enriched = enrich_stage_params_with_context(stage_id, params.get("prefix") or intent_params.get("prefix") or "", params)
            if stage_id == "reference_classification":
                scope_id = str(enriched.get("scopeId") or enriched.get("scope_id") or "").strip()
                if scope_id:
                    member_set = f"{sanitize_token(scope_id)}:subfamily_prediction:all_members"
                    enriched["memberSetId"] = member_set
                    enriched["member_set_id"] = member_set
                    if isinstance(enriched.get("stageContext"), dict):
                        enriched["stageContext"]["member_set_id"] = member_set
                    if isinstance(enriched.get("stage_context"), dict):
                        enriched["stage_context"]["member_set_id"] = member_set
            stage["params"] = enriched

    def _split_species_values(self, value: Any) -> List[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.replace("，", ",").replace(";", ",").split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    def _skip_subfamily_labels_query(self, query: str) -> bool:
        text = str(query or "").lower()
        return any(token in text for token in [
            "不需要亚家族", "不用亚家族", "不标注亚家族", "跳过亚家族", "普通进化树",
            "without subfamily", "skip subfamily", "plain phylogeny",
        ])

    def _combo_species_family(self, intent: Dict[str, Any], prefix: str) -> tuple[List[str], str]:
        params = intent.get("params", {}) or {}
        targets = self._split_species_values(params.get("targetSpecies") or intent.get("target_species"))
        family = str(params.get("geneFamily") or intent.get("gene_family") or "").strip()
        if not family and prefix and "." in prefix:
            family = prefix.split(".", 1)[1].strip()
        if not targets and prefix and "." in prefix:
            species = prefix.split(".", 1)[0].strip()
            if species and species not in {"Target", "Multi"}:
                targets = [species]
        return targets, family

    def _combo_target_member_ready(self, final_dir: Path, species: str, family: str) -> bool:
        if not species or not family:
            return False
        pep = final_dir / f"Target.{species}.{family}.final_members.pep.fa"
        ids = final_dir / f"Target.{species}.{family}.final_members.id"
        return pep.exists() and pep.stat().st_size > 0 and ids.exists() and ids.stat().st_size > 0

    def _combo_confirmed_file(self, final_dir: Path, input_dir: Path, species: str, family: str) -> str:
        for path in (
            final_dir / f"{species}.{family}.confirmed_subfamily.tsv",
            final_dir / f"{species}.{family}.target_plus_reference.confirmed_subfamily.tsv",
            input_dir / f"{species}.{family}.confirmed_subfamily.tsv",
            input_dir / f"{species}.{family}.target_plus_reference.confirmed_subfamily.tsv",
        ):
            if path.exists() and path.stat().st_size > 0:
                return str(path)
        return ""

    def _combo_prediction_file(self, final_dir: Path, species: str, family: str) -> str:
        candidates = [
            final_dir / f"{species}.{family}.reference_classification.subfamily.tsv",
            final_dir / f"{species}.{family}.target_plus_reference.reference_classification.subfamily.tsv",
            final_dir / f"{species}.{family}.{family}.target-{species}.ref-At.target_plus_reference.reference_classification.subfamily.tsv",
        ]
        for pattern in (f"{species}.{family}*.reference_classification.subfamily.tsv", f"*target-{species}*.reference_classification.subfamily.tsv"):
            candidates.extend(sorted(final_dir.glob(pattern)))
        seen = set()
        for path in candidates:
            if path in seen:
                continue
            seen.add(path)
            if path.exists() and path.stat().st_size > 0:
                return str(path)
        return ""

    def _completed_combo_evidence_panels(self, workspace: Path, prefix: str) -> List[str]:
        final_dir = workspace / "final"
        if not final_dir.exists():
            return []
        completed: List[str] = []
        for panel in COMBO_EVIDENCE_PANELS:
            candidates = []
            if prefix:
                candidates.append(final_dir / f"{prefix}.{panel}.manifest.json")
            candidates.extend(sorted(final_dir.glob(f"*.{panel}.manifest.json")))
            if any(path.exists() and path.stat().st_size > 0 for path in candidates):
                completed.append(panel)
        return completed if len(completed) >= 2 else []

    def _resolve_direct_combo_panels(self, target_stages: List[str], intent: Dict[str, Any], prefix: str, workspace: Path) -> List[str]:
        params = intent.get("params", {}) or {}
        explicit = _combo_panels_from_params(params)
        if explicit:
            return explicit
        completed = self._completed_combo_evidence_panels(workspace, prefix)
        if completed:
            return completed
        planned = [panel for panel in COMBO_EVIDENCE_PANELS if panel in target_stages]
        return planned if len(planned) >= 2 else list(COMBO_EVIDENCE_PANELS)


    def _normalize_direct_combo_with_subfamily_gate(
        self,
        query: str,
        target_stages: List[str],
        intent: Dict[str, Any],
        prefix: str,
        workspace: Path,
    ) -> tuple[List[str], set[str]]:
        targets, family = self._combo_species_family(intent, prefix)
        selected_combo_panels = self._resolve_direct_combo_panels(target_stages, intent, prefix, workspace)
        if not targets or not family or self._skip_subfamily_labels_query(query):
            combo_chain = ["phylogeny", *selected_combo_panels, "combo"]
            intent.setdefault("params", {})["comboPanels"] = list(selected_combo_panels)
            return combo_chain, set(combo_chain)

        comparative_species = _as_param_list(
            intent.get("comparative_species")
            or intent.get("params", {}).get("comparativeSpecies")
            or []
        )
        label_state = subfamily_state_for_scope(
            workspace.parent,
            self.input_root,
            workspace.name,
            family,
            target_species=targets,
            reference_species=_as_param_list(intent.get("reference_species") or intent.get("params", {}).get("referenceSpecies")),
            comparative_species=comparative_species,
        )
        statuses = set((label_state.get("statuses") or {}).values())
        member_ready = "missing_identify" not in statuses

        combo_chain: List[str] = []
        if not member_ready:
            combo_chain.append("identify")
        downstream_chain = ["phylogeny", *selected_combo_panels, "combo"]
        if statuses & {"missing_identify", "not_classified", "confirmed_mismatch"}:
            combo_chain.extend(downstream_chain)
            combo_chain.append("reference_classification")
            combo_chain.append("subfamily_confirm")
        else:
            if statuses & {"predicted_unconfirmed"}:
                combo_chain.append("subfamily_confirm")
            combo_chain.extend(downstream_chain)
        combo_chain = list(dict.fromkeys(combo_chain))

        params = intent.setdefault("params", {})
        params["targetSpecies"] = targets
        params["geneFamily"] = family
        params["forceRerunStages"] = combo_chain
        params["comboPanels"] = list(selected_combo_panels)
        params["requiresConfirmedSubfamily"] = True
        params["subfamilyState"] = {
            **label_state,
            "statuses": sorted(statuses),
            "targetSpecies": targets,
            "family": family,
        }
        intent["stages"] = combo_chain
        intent["target_species"] = targets
        intent["gene_family"] = family
        print(f"[PlanningAgentV2] direct combo normalized with subfamily gate: {combo_chain}, statuses={sorted(statuses)}")
        return combo_chain, set(combo_chain)

    def _apply_direct_combo_subfamily_stage_details(
        self,
        stages: List[Dict[str, Any]],
        intent_params: Dict[str, Any],
        forced_rerun_stages: set[str],
    ) -> None:
        if not forced_rerun_stages:
            return
        has_reference_classification = any(stage.get("id") == "reference_classification" for stage in stages)
        labels_required = bool(intent_params.get("requiresConfirmedSubfamily"))
        for stage in stages:
            stage_id = stage.get("id")
            params = stage.setdefault("params", {})
            if stage_id == "reference_classification":
                stage["name"] = "单目标+参考亚家族分类与证据图"
                stage["dependencies"] = ["combo"] if any(item.get("id") == "combo" for item in stages) else ["phylogeny"]
                params.update({
                    "analysisRoute": "subfamily_prediction",
                    "subfamilyPipeline": True,
                    "perTargetEvidenceOnly": True,
                })
            elif stage_id == "subfamily_confirm":
                stage["name"] = "亚家族标签确认"
                stage["dependencies"] = ["reference_classification"] if has_reference_classification else []
                params.update({
                    "analysisRoute": "subfamily_prediction",
                    "interactiveSubfamilyReview": True,
                    "subfamilyConfirmSpecies": intent_params.get("targetSpecies") or [],
                    "geneFamily": intent_params.get("geneFamily") or params.get("geneFamily") or "",
                })
            elif stage_id == "phylogeny":
                stage["dependencies"] = ["identify"] if any(item.get("id") == "identify" for item in stages) else []
            elif stage_id in {"motif", "domain", "structure"}:
                stage["dependencies"] = ["phylogeny"]
            elif stage_id == "combo":
                combo_panels = _combo_panels_from_params(intent_params) or list(COMBO_EVIDENCE_PANELS)
                stage["dependencies"] = list(combo_panels)
                params["comboPanels"] = list(combo_panels)
            if stage_id in forced_rerun_stages:
                params["forceRerun"] = True
                params["forceRerunStages"] = sorted(forced_rerun_stages)
            if labels_required and stage_id in {"phylogeny", "motif", "domain", "structure", "combo"}:
                params["requiresConfirmedSubfamily"] = True
                params["subfamilyLabelSource"] = "confirmed_or_uploaded_subfamily"

    def _generate_default_stages(
        self, 
        stage_ids: List[str], 
        prefix: str,
        learning_recommendations: Dict = None,
        intent_params: Dict = None,
    ) -> List[Dict]:
        """生成默认阶段配置（支持Learning推荐参数）"""
        stages = []
        
        # 获取Learning推荐的参数
        recommended_params = {}
        if learning_recommendations and learning_recommendations.get("confidence") in ["high", "medium"]:
            recommended_params = learning_recommendations.get("params", {})
            print(f"[PlanningAgentV2] 应用Learning推荐参数: {recommended_params}")
        
        for i, stage_id in enumerate(stage_ids):
            info = self.STAGE_INFO.get(stage_id, {"name": stage_id, "estimated_time": 10, "cpu": 8})
            deps = self.DEPENDENCIES.get(stage_id, [])
            display_name = info["name"]
            if is_utility_stage(stage_id):
                display_name = utility_display_name(stage_id, intent_params)
            
            # 判断是否可以并行
            can_parallel = (len(deps) > 0 and i > 0) or stage_id.startswith("pop_")
            parallel_group = None
            
            for group in self.PARALLEL_GROUPS:
                if stage_id in group:
                    parallel_group = f"group_{self.PARALLEL_GROUPS.index(group)}"
                    break
            
            # 基础参数
            stage_params = {
                "prefix": prefix,
                "cpu": info["cpu"]
            }

            if intent_params:
                for key in (
                    "geneFamily",
                    "scopeMode",
                    "targetSpecies",
                    "referenceSpecies",
                    "comparativeSpecies",
                    "refSpeciesList",
                    "blastEngine",
                    "interspeciesPlotMode",
                    "analysisRoute",
                    "memberSetId",
                ):
                    if intent_params.get(key):
                        stage_params[key] = intent_params[key]

            if stage_id in {"ncbi_fetch_sequences", "sequence_extract_by_id"} and intent_params:
                stage_params.update(intent_params)
                stage_params["cpu"] = min(int(stage_params.get("cpu") or info["cpu"] or 1), 1)

            if stage_id == "gwas_fmt" and intent_params:
                for key in (
                    "gwasVcf",
                    "gwasVcfCandidates",
                    "tasselPipeline",
                    "tasselXmx",
                    "gwasUnsupportedMarkerPolicy",
                ):
                    if intent_params.get(key) not in (None, "", []):
                        stage_params[key] = intent_params[key]

            if stage_id.startswith("pop_"):
                population_defaults = {
                    "populationVcf": "", "populationSampleGroup": "",
                    "popKMin": 2, "popKMax": 10, "popCvFolds": 5,
                    "popRandomSeed": 20260723, "popGwasK": 0,
                    "popChromosomePolicy": "all_vcf_contigs", "popChromosomes": "",
                }
                stage_params.update(population_defaults)
                if intent_params:
                    for key in population_defaults:
                        if intent_params.get(key) not in (None, "", []):
                            stage_params[key] = intent_params[key]

            if stage_id in {"ortholog", "ppi", "kaks"} and stage_params.get("refSpeciesList") and not stage_params.get("referenceSpecies"):
                stage_params["referenceSpecies"] = stage_params.get("refSpeciesList")
            if stage_id == "ppi":
                stage_params.setdefault("ppiSpecies", (intent_params or {}).get("ppiSpecies") or (intent_params or {}).get("ppi_species") or "auto")
                stage_params.setdefault("ppiScore", (intent_params or {}).get("ppiScore") or 200)
            if stage_id == "interspecies_synteny":
                stage_params.setdefault("blastEngine", (intent_params or {}).get("blastEngine") or "diamond")
                stage_params.setdefault("interspeciesPlotMode", (intent_params or {}).get("interspeciesPlotMode") or "both")

            _normalize_gene_family_stage_params(stage_id, stage_params, prefix)
            stage_params = enrich_stage_params_with_context(stage_id, stage_params.get("prefix") or prefix, stage_params)
            
            # 融入Learning推荐参数（如果有）
            if stage_id in recommended_params:
                stage_params.update(recommended_params[stage_id])
                print(f"[PlanningAgentV2] Stage {stage_id} 使用推荐参数: {recommended_params[stage_id]}")
            
            if stage_id == "combo":
                combo_panels = _combo_panels_from_params(stage_params) or [panel for panel in COMBO_EVIDENCE_PANELS if panel in stage_ids]
                if len(combo_panels) >= 2:
                    deps = list(combo_panels)
                    stage_params["comboPanels"] = list(combo_panels)

            stage = {
                "id": stage_id,
                "name": display_name,
                "task_kind": "utility" if is_utility_stage(stage_id) else "repair" if is_repair_stage(stage_id) else "pipeline",
                "order": i + 1,
                "dependencies": deps,
                "can_parallel": can_parallel,
                "parallel_group": parallel_group,
                "params": stage_params,
                "estimated_time": info["estimated_time"]
            }
            stages.append(stage)
        
        return stages


def create_planning_agent_v2(llm_manager):
    """创建PlanningAgentV2实例"""
    return PlanningAgentV2(llm_manager)
