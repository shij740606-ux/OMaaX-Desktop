"""
IntentAgent V2 - LLM驱动的智能意图识别
使用大模型理解用户自然语言，提取分析意图
"""
import json
import re
from typing import Any, Dict, List, Mapping, Optional
from .base import AgentMessage, AgentState, BaseAgent, QueryMode
from gfa_core.mas.llm import LLMManager
from gfa_core.mas.stage_catalog import (
    STAGE_DEPENDENCIES as CATALOG_STAGE_DEPENDENCIES,
    STAGE_INFO as CATALOG_STAGE_INFO,
    complete_dependencies as catalog_complete_dependencies,
)
from gfa_core.mas.utils.intent_parser import (
    extract_negated_target_stages,
    is_gwas_fmt_intent,
    parse_gene_family,
    parse_gene_family_scope_selection,
    resolve_gwas_intent,
    resolve_population_intent,
)


class IntentAgentV2(BaseAgent):
    """
    智能意图识别Agent V2
    
    使用LLM理解用户自然语言，提取：
    - 分析目标（基因家族鉴定、进化分析、结构分析等）
    - 基因家族名称
    - 物种信息
    - 期望的分析流程
    - 特殊要求
    """
    name = "intent_v2"
    role = "Intent Understanding Specialist"
    
    # 所有可能的分析阶段
    ALL_STAGES = list(CATALOG_STAGE_INFO.keys())
    
    # 阶段描述（用于LLM理解）
    STAGE_DESCRIPTIONS = {
        "identify": "基因家族鉴定 - 使用HMM和BLAST从基因组中识别基因家族成员",
        "phylogeny": "系统发育分析 - 构建进化树，分析基因家族的进化关系",
        "reference_classification": "参考亚家族分类 - 根据参考物种已知亚家族标签和联合进化树，为目标物种成员迁移亚家族分类并标注置信度",
        "subfamily_confirm": "确认亚家族分类 - 固化用户审核后的亚家族标签，供后续树图、结构、motif、domain、combo 和 promoter 复用",
        "motif": "Motif分析 - 识别蛋白序列中的保守基序",
        "domain": "结构域分析 - 分析蛋白结构域组成",
        "structure": "基因结构分析 - 分析基因的外显子-内含子结构",
        "promoter": "启动子分析 - 分析顺式作用元件和调控序列",
        "synteny": "种内共线性分析 - 分析基因组内的共线性关系",
        "karyotype": "核型分析 - 分析基因在染色体上的分布",
        "interspecies_synteny": "物种间共线性 - 跨物种比较共线性",
        "expression": "基因家族表达分析 - 基于已完成鉴定和进化树结果，分析基因家族成员的表达模式（热图等）",
        "kaks": "Ka/Ks分析 - 计算进化速率和选择压力",
        "ppi": "PPI分析 - 蛋白互作网络分析",
        "pep": "蛋白特征分析 - 等电点、分子量等理化性质",
        "identify_refine": "修订鉴定成员 - 根据人工修订ID列表重建final_members.id和final_members.pep.fa",
        "genome_prepare": "数据准备总流程 - 多物种文件整理、GFF/FASTA标准化、最长转录本和参考序列合并",
        "prepare_organize": "数据文件整理 - 根据config.xlsx组织文件、标准化命名、生成species_config",
        "prepare_normalize_gff": "GFF ID规范化 - 修订JGI/Ensembl/NCBI GFF中的ID前缀和属性",
        "prepare_longest_transcript": "提取最长转录本 - 按基因选择最长mRNA/CDS并输出过滤GFF和ID表",
        "prepare_extract_pep": "提取PEP - 从genome+GFF使用gffread提取蛋白序列",
        "prepare_extract_cds": "提取CDS - 从genome+GFF使用gffread提取CDS序列",
        "sequence_extract_by_id": "按ID提取序列 - 从用户上传或指定的PEP/CDS/FASTA中提取一个或多个ID对应的序列",
        "prepare_filter_fasta": "按ID提取FASTA - 使用ID列表从CDS/PEP/FASTA中提取子集",
        "prepare_simplify_ncbi": "NCBI ID简化 - 规范化NCBI CDS header并简化GFF属性",
        "rnaseq_qc": "RNA-seq质控 - FastQC质量评估（RNA-seq流程第1步）",
        "wgs_qc": "WGS质控 - fastp paired-end quality control",
        "wgs_index": "WGS参考索引 - samtools、GATK 和 BWA-MEM2 reference index",
        "wgs_align": "WGS序列比对 - 使用BWA-MEM2比对clean reads并生成坐标排序BAM",
        "rnaseq_align": "RNA-seq比对 - reads比对到参考基因组（RNA-seq流程第2步）",
        "rnaseq_quant": "RNA-seq定量 - 基因表达量计算（RNA-seq流程第3步）",
        "rnaseq_de": "RNA-seq差异表达分析 - 从原始FASTQ数据开始，进行质控→比对→定量→差异表达分析（DESeq2）",
        "rnaseq_enrich": "RNA-seq富集分析 - GO/KEGG通路富集分析（RNA-seq流程最后一步）",
        "rnaseq_candidate_family": "RNA-seq候选家族筛选 - 整合DEG、InterPro/Pfam和GO/KEGG富集，输出候选基因家族排名",
        "rnaseq_report": "RNA-seq结果报告 - 从当前成功阶段生成可追溯的正式Markdown报告",
        "enrichment": "富集分析 - 基于基因家族鉴定结果进行GO/KEGG通路富集分析",
        "ncbi_fetch_sequences": "NCBI序列下载 - 根据GeneID、基因名或基因list下载protein/CDS序列"
    }
    
    # 阶段依赖关系
    # 与 PlanningAgentV2 保持一致
    STAGE_DEPENDENCIES = CATALOG_STAGE_DEPENDENCIES
    
    # 推荐流程（当用户提到某个分析时，推荐执行的完整流程）
    RECOMMENDED_FLOWS = {
        "structure": {
            "name": "基因结构分析",
            "description": "完整的基因结构分析流程，包括鉴定、进化树构建和结构可视化",
            "stages": ["identify", "phylogeny", "structure"],
            "optional": ["motif", "domain"]  # 可选的增强分析
        },
        "promoter": {
            "name": "启动子分析",
            "description": "启动子顺式作用元件分析，需要进化树分类",
            "stages": ["identify", "phylogeny", "promoter"],
            "optional": ["motif"]
        },
        "synteny": {
            "name": "共线性分析",
            "description": "基因组内共线性关系分析，需要进化树背景",
            "stages": ["identify", "phylogeny", "synteny"],
            "optional": ["karyotype"]
        },
        "kaks": {
            "name": "进化分析",
            "description": "Ka/Ks进化速率和选择压力分析，需要直系同源基因映射",
            "stages": ["identify", "ortholog", "kaks"],
            "optional": ["interspecies_synteny", "synteny"]
        },
        "complete": {
            "name": "完整分析",
            "description": "基因家族全部分析流程",
            "stages": [
                "identify", "phylogeny", 
                "motif", "domain", "structure", "promoter",
                "synteny", "karyotype", "expression"
            ],
            "optional": ["kaks", "ppi", "combo"]
        },
        "combo": {
            "name": "组合图",
            "description": "Tree + Motif + Structure + Domain 组合可视化",
            "stages": ["identify", "phylogeny", "motif", "structure", "domain", "combo"],
            "optional": []
        },
        "reference_classification": {
            "name": "预测亚家族分类",
            "description": "使用拟南芥等参考物种的已知亚家族标签和联合进化树，为目标物种成员预测亚家族与置信度；完成后前端会弹出亚家族确认界面",
            "stages": ["identify", "phylogeny", "reference_classification"],
            "optional": ["motif", "domain", "structure", "combo"]
        },
        "subfamily_confirm": {
            "name": "确认亚家族分类",
            "description": "在参考亚家族预测结果基础上，由用户结合 tree/structure/motif/domain/combo 证据确认最终亚家族标签",
            "stages": ["subfamily_confirm"],
            "optional": ["motif", "domain", "structure", "combo"]
        },
        "rnaseq_de": {
            "name": "RNA-seq差异表达分析",
            "description": "从原始FASTQ数据开始的完整RNA-seq差异表达分析流程",
            "stages": ["rnaseq_qc", "rnaseq_align", "rnaseq_quant", "rnaseq_de"],
            "optional": []
        },
        "rnaseq_enrich": {
            "name": "RNA-seq富集分析",
            "description": "从原始FASTQ数据开始的完整RNA-seq流程，包括差异表达和富集分析",
            "stages": ["rnaseq_qc", "rnaseq_align", "rnaseq_quant", "rnaseq_de", "rnaseq_enrich"],
            "optional": []
        }
    }
    
    def __init__(self, llm_manager: LLMManager):
        super().__init__(llm_manager)

    @staticmethod
    def _current_request_text(query: str) -> str:
        """Return the user request without Router-appended confirmed context."""
        text = str(query or "")
        boundaries = [
            index
            for marker in ("\n已确认上下文：", "\nConfirmed context:")
            if (index := text.find(marker)) >= 0
        ]
        request = text[:min(boundaries)] if boundaries else text
        return request.strip() or text.strip()

    @staticmethod
    def _apply_structured_goal_context(
        intent: Dict[str, Any],
        goal_context: Mapping[str, Any] | None,
    ) -> Dict[str, Any]:
        if not isinstance(goal_context, Mapping):
            return intent
        terms = goal_context.get("user_terms")
        if not isinstance(terms, Mapping):
            return intent

        def species_list(value: Any) -> List[str]:
            if isinstance(value, str):
                values = value.split(",")
            elif isinstance(value, (list, tuple, set)):
                values = value
            else:
                values = ()
            return list(
                dict.fromkeys(str(item).strip() for item in values if str(item).strip())
            )

        normalized = dict(intent)
        params = dict(normalized.get("params") or {})
        targets = species_list(
            params.get("targetSpecies") or normalized.get("target_species")
        ) or species_list(terms.get("target_species"))
        references = species_list(
            params.get("referenceSpecies") or normalized.get("reference_species")
        ) or species_list(terms.get("reference_species"))
        comparatives = species_list(
            params.get("comparativeSpecies") or normalized.get("comparative_species")
        ) or species_list(terms.get("comparative_species"))
        references = [species for species in references if species not in targets]
        comparatives = [
            species
            for species in comparatives
            if species not in targets and species not in references
        ]
        family = str(
            normalized.get("gene_family") or terms.get("gene_family") or ""
        ).strip()
        scope_mode = (
            "target_plus_reference_plus_comparative"
            if references and comparatives
            else "target_plus_reference"
            if references
            else "target_plus_comparative"
            if comparatives
            else "target_only"
        )
        params.update(
            {
                "targetSpecies": targets,
                "referenceSpecies": references,
                "comparativeSpecies": comparatives,
                "target_species": targets,
                "scopeMode": scope_mode,
            }
        )
        normalized.update(
            {
                "params": params,
                "gene_family": family,
                "target_species": targets,
                "reference_species": references,
                "comparative_species": comparatives,
                "scopeMode": scope_mode,
            }
        )
        if targets:
            normalized["species"] = targets[0] if len(targets) == 1 else "Multi"
        if normalized.get("species") and family:
            normalized["prefix"] = f"{normalized['species']}.{family}"
        return normalized
    
    async def execute(self, state: AgentState, message: AgentMessage) -> AgentState:
        """
        执行意图识别
        
        使用LLM理解用户输入，提取完整意图信息
        """
        query = message.payload.get("query", "")
        request_query = self._current_request_text(query)
        
        # 获取前端传递的 prefix（例如 "At.WRKY"）
        provided_prefix = message.payload.get("prefix", "")
        print(f"[IntentAgentV2] Received prefix: {provided_prefix}")
        
        # 如果提供了 prefix，优先从中解析 species 和 gene_family
        parsed_from_prefix = {}
        if provided_prefix and "." in provided_prefix:
            parts = provided_prefix.split(".", 1)
            parsed_from_prefix = {
                "species": parts[0],
                "gene_family": parts[1]
            }
            print(f"[IntentAgentV2] Parsed from prefix: species={parts[0]}, gene_family={parts[1]}")
        
        # 获取已完成的阶段（从 state.results 中）
        completed_stages = state.results.get("completed_stages", [])
        print(f"[IntentAgentV2] Received completed_stages: {completed_stages}")
        
        # 获取 workspace 和 job_id（用于后续 manifest 检查）
        job_id = message.payload.get("job_id", "")
        workspace = message.payload.get("workspace", "")
        print(f"[IntentAgentV2] Received job_id: {job_id}, workspace: {workspace}")
        
        # ===== 关键修改：使用公共模块的硬编码规则解析意图 =====
        from gfa_core.mas.utils.intent_parser import parse_intent_from_query
        
        request_intent = parse_intent_from_query(request_query)
        context_intent = parse_intent_from_query(query)
        base_intent = dict(request_intent)
        for key in (
            "gene_family", "species", "target_species", "reference_species",
            "comparative_species", "scopeMode", "params", "prefix",
        ):
            if context_intent.get(key):
                base_intent[key] = context_intent[key]
        print(f"[IntentAgentV2] Base intent from parser: {base_intent}")
        
        # 如果硬编码规则解析成功（有明确阶段），直接使用
        if base_intent["stages"] and base_intent["stages"] != ["identify"]:
            print(f"[IntentAgentV2] Using hardcoded rule result")
            # 如果提供了 prefix，优先使用 prefix 解析结果
            final_species = parsed_from_prefix.get("species", base_intent["species"]) if parsed_from_prefix else base_intent["species"]
            final_gene_family = parsed_from_prefix.get("gene_family", base_intent["gene_family"]) if parsed_from_prefix else base_intent["gene_family"]
            
            # 关键修复：展开依赖关系
            original_stages = base_intent["stages"]
            expanded_stages = self._complete_dependencies(original_stages)
            print(f"[IntentAgentV2] Stages expanded: {original_stages} -> {expanded_stages}")
            
            intent_result = {
                "gene_family": final_gene_family,
                "species": final_species,
                "stages": expanded_stages,
                "primary_stage": base_intent["primary_stage"],
                "description": base_intent.get("message") or f"执行{base_intent['primary_stage']}分析",
                "requirements": [],
                "confidence": base_intent["confidence"],
                "task_kind": base_intent.get("task_kind", "pipeline"),
                "taskType": base_intent.get("task_kind", "pipeline"),
            }
            if base_intent.get("utility_tool"):
                intent_result["utility_tool"] = base_intent["utility_tool"]
            if base_intent.get("params"):
                intent_result["params"] = base_intent["params"]
                if base_intent["params"].get("prefix"):
                    intent_result["prefix"] = base_intent["params"]["prefix"]
                if base_intent["params"].get("ncbiSpecies"):
                    intent_result["species"] = base_intent["params"]["ncbiSpecies"]
        else:
            # 硬编码规则未命中，使用 LLM 细化 stages
            print(f"[IntentAgentV2] Hardcoded rule not hit, using LLM")
            intent_result = await self._recognize_intent_with_llm(request_query, completed_stages, provided_prefix)
            excluded_stages = set(extract_negated_target_stages(request_query))
            if excluded_stages:
                intent_result["stages"] = [
                    stage for stage in intent_result.get("stages", [])
                    if stage not in excluded_stages
                ]
                if intent_result.get("primary_stage") in excluded_stages:
                    intent_result["primary_stage"] = intent_result["stages"][0] if intent_result["stages"] else None
                if not intent_result["stages"]:
                    intent_result.update({
                        "description": "未检测到需要执行的正向分析阶段",
                        "needs_clarification": True,
                        "clarification_type": "analysis_stage",
                        "clarification_question": "请说明要执行的具体分析阶段",
                    })
            
            # 关键修复：保护 Parser 已解析的 gene_family/species，不被 LLM 的"未知"覆盖
            if base_intent.get("gene_family") and base_intent["gene_family"] not in ["未知", "待用户指定"]:
                if intent_result.get("gene_family") in ["未知", "待用户指定", ""]:
                    intent_result["gene_family"] = base_intent["gene_family"]
                    print(f"[IntentAgentV2] Protected gene_family from parser: {base_intent['gene_family']}")
            if base_intent.get("species") and base_intent["species"] not in ["未知", "待用户指定"]:
                if intent_result.get("species") in ["未知", "待用户指定", ""]:
                    intent_result["species"] = base_intent["species"]
                    print(f"[IntentAgentV2] Protected species from parser: {base_intent['species']}")
            intent_result.setdefault("task_kind", "pipeline")
            intent_result.setdefault("taskType", intent_result.get("task_kind", "pipeline"))

        intent_result = self._apply_structured_goal_context(
            intent_result,
            message.payload.get("goal_context"),
        )
        
        # 保留原始 stages，不过滤（留给 PlanningAgentV2 处理）
        original_stages = intent_result.get("stages", [])
        print(f"[IntentAgentV2] Final stages: {original_stages}")
        print(f"[IntentAgentV2] Leaving filter to PlanningAgentV2 based on manifest status")
        
        # 添加 job_id 和 workspace 到结果（从 payload 中获取）
        job_id = message.payload.get("job_id", "")
        workspace = message.payload.get("workspace", "")
        
        # 保留原始 stages，不过滤
        intent_result["stages"] = original_stages
        intent_result["all_completed"] = False
        
        # 添加 completed_stages 信息，供 PlanningAgentV2 参考
        intent_result["completed_stages_from_state"] = completed_stages
        
        # 添加 workspace 和 job_id
        intent_result["job_id"] = job_id
        intent_result["workspace"] = workspace
        
        # 更新状态
        if "results" not in state.results:
            state.results["results"] = {}
        state.results["results"]["intent"] = intent_result
        
        # 添加消息
        stages_str = ", ".join(intent_result.get("stages", []))
        state.messages.append({
            "role": "intent",
            "content": f"🎯 意图识别: {intent_result.get('description', '')}\n📋 分析阶段: {stages_str}",
        })
        
        return state
    
    async def _recognize_intent_with_llm(self, query: str, completed_stages: List[str] = None, provided_prefix: str = "") -> Dict[str, Any]:
        """使用LLM识别用户意图
        
        Args:
            query: 用户查询
            completed_stages: 已完成的阶段
            provided_prefix: 前端传递的 prefix（例如 "At.WRKY"）
        """
        completed_stages = completed_stages or []
        
        # 如果提供了 prefix，优先从中解析 species 和 gene_family
        parsed_from_prefix = {}
        if provided_prefix and "." in provided_prefix:
            parts = provided_prefix.split(".", 1)
            parsed_from_prefix = {
                "species": parts[0],
                "gene_family": parts[1]
            }
            print(f"[IntentAgentV2] Prefix override: species={parts[0]}, gene_family={parts[1]}")
        
        # 强制规则匹配：当用户明确提到某个具体分析时，直接使用推荐流程
        query_lower = query.lower()

        gwas = resolve_gwas_intent(query)
        population = resolve_population_intent(query)
        if population:
            return {
                "gene_family": "", "species": "", "stages": population["stages"],
                "primary_stage": population["target"],
                "description": "执行群体遗传分析" if population["full_chain"] else "执行指定群体遗传分析阶段",
                "requirements": ["需要群体VCF"], "confidence": 0.98,
                "task_kind": "pipeline", "taskType": "pipeline",
                "prefix": population["prefix"], "params": population["params"],
            }
        if gwas:
            return {
                "gene_family": "",
                "species": "",
                "stages": gwas["stages"],
                "primary_stage": gwas["target"],
                "description": "执行完整 GWAS 分析链" if gwas["full_chain"] else "执行指定 GWAS 分析阶段及其依赖",
                "requirements": [],
                "confidence": 0.98,
                "task_kind": "pipeline",
                "taskType": "pipeline",
            }
        
        # 核型分析 -> 返回 identify, synteny, karyotype（synteny依赖identify，karyotype依赖synteny）
        if any(kw in query_lower for kw in ["核型", "karyotype", "染色体分布"]):
            print(f"[IntentAgentV2] Force rule matched: karyotype -> ['identify', 'synteny', 'karyotype']")
            return {
                "gene_family": self._extract_gene_family_fallback(query),
                "species": "",
                "stages": ["identify", "synteny", "karyotype"],
                "primary_stage": "karyotype",
                "description": "执行核型分析，需要鉴定、共线性和核型三个阶段",
                "requirements": ["需要基因组文件和基因注释文件"],
                "confidence": 0.95
            }
        
        # 共线性有种内/种间两类。用户只说“共线性”时不要擅自默认，交给前端二选一。
        if any(kw in query_lower for kw in ["种间", "interspecies", "跨物种", "物种间共线性"]) and "核型" not in query_lower and "karyotype" not in query_lower:
            print(f"[IntentAgentV2] Force rule matched: interspecies_synteny -> ['identify', 'interspecies_synteny']")
            return {
                "gene_family": self._extract_gene_family_fallback(query),
                "species": "",
                "stages": ["identify", "interspecies_synteny"],
                "primary_stage": "interspecies_synteny",
                "description": "执行种间共线性分析，需要鉴定和种间共线性阶段",
                "requirements": ["需要待比较物种的蛋白序列、CDS和注释文件"],
                "confidence": 0.95
            }

        if any(kw in query_lower for kw in ["种内共线性", "intraspecies", "mcscanx"]) and "核型" not in query_lower and "karyotype" not in query_lower:
            print(f"[IntentAgentV2] Force rule matched: synteny -> ['identify', 'synteny']")
            return {
                "gene_family": self._extract_gene_family_fallback(query),
                "species": "",
                "stages": ["identify", "synteny"],
                "primary_stage": "synteny",
                "description": "执行种内共线性分析，需要鉴定和种内共线性阶段",
                "requirements": ["需要基因组文件"],
                "confidence": 0.95
            }

        if any(kw in query_lower for kw in ["共线性", "synteny"]) and "核型" not in query_lower and "karyotype" not in query_lower:
            print("[IntentAgentV2] Ambiguous synteny request: requires user clarification")
            return {
                "gene_family": self._extract_gene_family_fallback(query),
                "species": "",
                "stages": [],
                "primary_stage": None,
                "description": "共线性分析包含种内共线性和种间共线性，需要用户选择",
                "requirements": [],
                "confidence": 0.9,
                "needs_clarification": True,
                "clarification_type": "synteny_scope",
                "clarification_question": "请选择要执行哪一种共线性分析",
                "options": [
                    {
                        "id": "synteny",
                        "label": "种内共线性分析",
                        "description": "分析同一物种基因组内的共线性关系",
                        "stages": ["identify", "synteny"]
                    },
                    {
                        "id": "interspecies_synteny",
                        "label": "种间共线性分析",
                        "description": "比较不同物种之间的共线性关系",
                        "stages": ["identify", "interspecies_synteny"]
                    }
                ]
            }
        
        # 进化树分析 -> 只返回 identify, phylogeny
        if any(kw in query_lower for kw in ["进化树", "phylogeny", "系统发育", "进化分析"]):
            print(f"[IntentAgentV2] Force rule matched: phylogeny -> ['identify', 'phylogeny']")
            return {
                "gene_family": self._extract_gene_family_fallback(query),
                "species": "",
                "stages": ["identify", "phylogeny"],
                "primary_stage": "phylogeny",
                "description": "执行进化树分析",
                "requirements": ["需要基因组文件和基因注释文件"],
                "confidence": 0.95
            }
        
        # 蛋白特征分析 (PEP) -> 只返回 pep
        if any(kw in query_lower for kw in ["pep", "蛋白特征", "蛋白性质", "等电点", "分子量"]):
            print(f"[IntentAgentV2] Force rule matched: pep -> ['pep']")
            return {
                "gene_family": self._extract_gene_family_fallback(query),
                "species": "",
                "stages": ["pep"],
                "primary_stage": "pep",
                "description": "执行蛋白特征分析",
                "requirements": ["需要identify阶段的蛋白序列结果"],
                "confidence": 0.95
            }

        if is_gwas_fmt_intent(query):
            return {
                "gene_family": "",
                "species": "",
                "stages": ["gwas_fmt"],
                "primary_stage": "gwas_fmt",
                "description": "使用 TASSEL 排序外部 VCF 并转换为 HapMap",
                "requirements": ["需要显式选择外部 VCF 文件"],
                "confidence": 0.98,
            }

        if any(kw in query_lower for kw in ["背景注释", "功能注释", "基因组注释", "自动注释", "eggnog", "annotation"]):
            print(f"[IntentAgentV2] Force rule matched: annotation -> ['annotation']")
            return {
                "gene_family": self._extract_gene_family_fallback(query),
                "species": "",
                "stages": ["annotation"],
                "primary_stage": "annotation",
                "description": "执行背景注释分析，生成 RNA-seq GO/KEGG 富集所需背景文件",
                "requirements": ["需要目标物种蛋白序列文件"],
                "confidence": 0.95
            }
        
        # RNA-seq 差异表达分析 -> 返回完整 RNA-seq 流程
        # 注意：区分 expression（基因家族表达分析）和 rnaseq_de（RNA-seq差异表达）
        has_rnaseq_marker = any(kw in query_lower for kw in ["rnaseq", "rna-seq", "rna seq", "转录组"])
        explicit_gene_family_context = any(kw in query_lower for kw in ["基因家族", "家族表达", "表达量", "热图", "heatmap"])

        if has_rnaseq_marker and any(
            keyword in query_lower
            for keyword in ["生成结果报告", "打开结果报告", "结果报告", "生成报告", "打开报告"]
        ):
            action = "open" if "打开" in query_lower else "generate"
            return {
                "gene_family": "",
                "species": "",
                "stages": ["rnaseq_report"],
                "primary_stage": "rnaseq_report",
                "description": "打开或生成当前RNA-seq正式结果报告",
                "requirements": ["至少一个成功的RNA-seq阶段manifest"],
                "params": {"rnaseqReportAction": action},
                "confidence": 0.99,
            }

        stage_force_rules = [
            ("rnaseq_candidate_family", ["候选家族", "候选基因家族", "家族排名", "tf注释", "tf family", "tffamily", "pfam注释", "deg注释", "差异基因注释"], "执行RNA-seq候选家族筛选"),
            ("rnaseq_fgsea", ["fgsea", "gsea", "排序富集", "基因集富集"], "执行RNA-seq fgsea排序富集分析"),
            ("rnaseq_wgcna", ["wgcna", "共表达", "加权共表达", "模块性状"], "执行RNA-seq WGCNA共表达分析"),
            ("rnaseq_sample_qc", ["样本质量", "样本质量评估", "样本检测", "sample qc", "sample quality", "pca", "样本相关"], "执行RNA-seq样本质量评估"),
            ("rnaseq_qc", ["质控", "qc", "fastqc", "测序质量"], "执行RNA-seq质控分析"),
            ("rnaseq_quant", ["定量", "quant", "featurecounts", "count矩阵", "计数矩阵"], "执行RNA-seq定量分析"),
            ("rnaseq_align", ["比对", "align", "mapping", "hisat2", "star"], "执行RNA-seq比对分析"),
        ]
        for stage_id, keywords, description in stage_force_rules:
            if any(kw in query_lower for kw in keywords):
                if stage_id == "rnaseq_align" and any(kw in query_lower for kw in ["多序列", "msa", "multiple alignment"]):
                    continue
                return {
                    "gene_family": self._extract_gene_family_fallback(query),
                    "species": "",
                    "stages": [stage_id],
                    "primary_stage": stage_id,
                    "description": description,
                    "requirements": ["需要对应RNA-seq上游产物或原始输入文件"],
                    "confidence": 0.95
                }
        if any(kw in query_lower for kw in ["差异表达", "差异分析", "差异基因", "deg", "deseq2", "edger"]) and not explicit_gene_family_context:
            return {
                "gene_family": self._extract_gene_family_fallback(query),
                "species": "",
                "stages": ["rnaseq_de"],
                "primary_stage": "rnaseq_de",
                "description": "执行RNA-seq差异表达分析",
                "requirements": ["需要RNA-seq定量结果和样本分组配置"],
                "confidence": 0.95
            }

        if any(kw in query_lower for kw in ["富集", "enrich", "go", "kegg", "通路"]):
            if "kegg" in query_lower:
                enrich_stage = "rnaseq_kegg_enrich"
                enrich_type = "kegg"
            elif re.search(r"(^|[^a-z])go([^a-z]|$)", query_lower) or "go富集" in query_lower or "go分析" in query_lower:
                enrich_stage = "rnaseq_go_enrich"
                enrich_type = "go"
            else:
                enrich_stage = "rnaseq_enrich"
                enrich_type = "go"
            print(f"[IntentAgentV2] Force rule matched: {enrich_stage} -> RNA-seq enrichment")
            return {
                "gene_family": self._extract_gene_family_fallback(query),
                "species": "",
                "stages": [enrich_stage],
                "primary_stage": enrich_stage,
                "description": "执行RNA-seq富集分析",
                "requirements": ["需要已完成RNA-seq差异表达结果，并具备gene2go/gene2ko背景或可自动注释"],
                "confidence": 0.95,
                "params": {"enrichType": enrich_type}
            }

        if has_rnaseq_marker and any(kw in query_lower for kw in ["差异", "de", "deg"]):
            print(f"[IntentAgentV2] Force rule matched: rnaseq_de -> full RNA-seq workflow")
            return {
                "gene_family": self._extract_gene_family_fallback(query),
                "species": "",
                "stages": ["rnaseq_qc", "rnaseq_align", "rnaseq_quant", "rnaseq_de"],
                "primary_stage": "rnaseq_de",
                "description": "执行RNA-seq差异表达分析（完整流程：质控→比对→定量→差异表达）",
                "requirements": ["需要RNA-seq原始FASTQ文件和样本分组配置文件"],
                "confidence": 0.95
            }

        if has_rnaseq_marker and any(kw in query_lower for kw in ["定量", "quant", "count"]):
            print(f"[IntentAgentV2] Force rule matched: rnaseq_quant -> qc + align + quant")
            return {
                "gene_family": self._extract_gene_family_fallback(query),
                "species": "",
                "stages": ["rnaseq_qc", "rnaseq_align", "rnaseq_quant"],
                "primary_stage": "rnaseq_quant",
                "description": "执行RNA-seq定量分析（完整流程：质控→比对→定量）",
                "requirements": ["需要RNA-seq原始FASTQ文件和基因组注释文件"],
                "confidence": 0.95
            }

        if has_rnaseq_marker and any(kw in query_lower for kw in ["比对", "align", "mapping"]):
            print(f"[IntentAgentV2] Force rule matched: rnaseq_align -> qc + align")
            return {
                "gene_family": self._extract_gene_family_fallback(query),
                "species": "",
                "stages": ["rnaseq_qc", "rnaseq_align"],
                "primary_stage": "rnaseq_align",
                "description": "执行RNA-seq比对分析（完整流程：质控→比对）",
                "requirements": ["需要RNA-seq原始FASTQ文件和参考基因组"],
                "confidence": 0.95
            }
        
        # RNA-seq 富集分析 -> 返回完整 RNA-seq 流程 + 富集
        if any(kw in query_lower for kw in ["rnaseq富集", "rna-seq富集", "rna富集分析", "rnaseq enrich"]) \
           and any(kw in query_lower for kw in ["rnaseq", "rna-seq", "rna seq"]):
            if "kegg" in query_lower:
                enrich_stage = "rnaseq_kegg_enrich"
                enrich_type = "kegg"
            elif re.search(r"(^|[^a-z])go([^a-z]|$)", query_lower) or "go富集" in query_lower or "go分析" in query_lower:
                enrich_stage = "rnaseq_go_enrich"
                enrich_type = "go"
            else:
                enrich_stage = "rnaseq_enrich"
                enrich_type = "go"
            print(f"[IntentAgentV2] Force rule matched: {enrich_stage} -> full RNA-seq workflow + enrichment")
            return {
                "gene_family": self._extract_gene_family_fallback(query),
                "species": "",
                "stages": ["rnaseq_qc", "rnaseq_align", "rnaseq_quant", "rnaseq_de", enrich_stage],
                "primary_stage": enrich_stage,
                "description": "执行RNA-seq富集分析（完整流程：质控→比对→定量→差异表达→富集）",
                "requirements": ["需要RNA-seq原始FASTQ文件和样本分组配置文件"],
                "confidence": 0.95,
                "params": {"enrichType": enrich_type}
            }
        
        # 构建推荐流程描述
        flows_desc = "\n".join([
            f"- {k}: {v['description']}\n  推荐流程: {' → '.join(v['stages'])}"
            for k, v in self.RECOMMENDED_FLOWS.items()
        ])
        
        # 构建提示词
        system_prompt = """你是一个专业的基因家族分析意图理解专家。
你的任务是理解用户的自然语言输入，提取分析意图并推荐完整的分析流程。

可用的分析阶段：
{stages_desc}

阶段依赖关系：
{dependencies}

推荐分析流程：
{flows_desc}

输出格式（JSON）：
{{
    "gene_family": "基因家族名称，如SPL、NAC等",
    "species": "物种代码，如Ft、At、Os等",
    "stages": ["需要执行的分析阶段列表，按依赖顺序排列"],
    "primary_stage": "用户主要想要的分析阶段",
    "description": "用户意图的简要描述",
    "requirements": ["特殊要求或注意事项"],
    "confidence": 0.95
}}

规则：
1. 根据用户描述自动推断需要的分析阶段
2. 必须考虑阶段依赖关系（如phylogeny需要identify）
3. **重要**: 如果用户明确说"帮我执行核型分析"，只返回 ["identify", "synteny", "karyotype"]（种内核型依赖种内共线性）
4. **重要（共线性二选一）**:
   - 用户明确「种间/物种间/跨物种/interspecies」共线性 → stages 必须包含 **interspecies_synteny**，典型前置为 identify + interspecies_synteny（按依赖展开）
   - 用户明确「种内」或未写明跨物种、仅单一基因组语境 → **synteny**（MCScanX 种内），典型为 identify + synteny
   - 禁止将「种间共线性」规划成仅 synteny
5. **重要**: 如果用户明确说"帮我执行进化树分析"，只返回 ["identify", "phylogeny"]
6. **重要**: 如果用户只提到一个具体分析阶段，只返回该阶段及其直接依赖（不要返回完整流程）
7. 如果用户说"完整分析"或"全套分析"，才返回所有主要阶段
8. **关键区分**: expression（基因家族表达分析）和 rnaseq_de（RNA-seq差异表达）是两种不同的分析：
   - expression: 基于已完成鉴定和进化树结果，分析基因家族成员的表达模式
   - rnaseq_de: 从RNA-seq原始FASTQ数据开始，进行质控→比对→定量→差异表达分析
   如果用户提到"RNA-seq"、"rnaseq"、"转录组测序"等词汇，应该使用RNA-seq流程（rnaseq_qc→rnaseq_align→rnaseq_quant→rnaseq_de）
9. **关键**: 不要过度推断，用户明确提到的分析才是他们想要的
10. **关键**: 返回所有用户请求的阶段，即使它们可能在已完成列表中（后续会根据manifest状态决定是否执行）

参考信息 - 已完成的阶段（供参考，但仍需返回用户请求的阶段）：
{completed_stages}
""".format(
            completed_stages=', '.join(completed_stages) if completed_stages else '无',
            stages_desc="\n".join([f"- {k}: {v}" for k, v in self.STAGE_DESCRIPTIONS.items()]),
            dependencies="\n".join([f"- {k} 需要: {', '.join(v)}" for k, v in self.STAGE_DEPENDENCIES.items()]),
            flows_desc=flows_desc
        )
        
        user_prompt = f"""用户输入: {query}

请识别用户的分析意图，并推荐完整的分析流程。

注意：
- 如果用户只上传了初始数据，还没有执行任何分析，请从identify开始规划
- 如果用户提到某个具体分析（如"基因结构"），请包含该分析需要的所有前置步骤
- 输出JSON格式结果。"""
        
        # 调用LLM
        try:
            from gfa_core.mas.llm.base import LLMMessage
            messages = [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt)
            ]
            response = await self.llm_manager.acall(messages, temperature=0.3)
            
            # 解析LLM输出
            content = response.content if hasattr(response, 'content') else str(response)
            print(f"[IntentAgentV2] LLM raw response: {content[:200]}...")
            
            # 尝试提取JSON（LLM可能返回markdown格式的JSON）
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                content = json_match.group()
            
            intent_data = json.loads(content)
            print(f"[IntentAgentV2] LLM parsed intent: {intent_data}")
            
            # 验证并补充必要字段（传递 provided_prefix）
            intent_data = self._validate_intent(intent_data, query, provided_prefix)
            
            return intent_data
            
        except Exception as e:
            print(f"[IntentAgentV2] LLM识别失败: {e}, 降级到fallback")
            # 降级到规则匹配
            return self._fallback_recognition(query)
    
    def _validate_intent(self, intent_data: Dict, query: str, provided_prefix: str = "") -> Dict:
        """验证并补充意图数据
        
        Args:
            intent_data: LLM 返回的意图数据
            query: 用户查询
            provided_prefix: 前端传递的 prefix（例如 "At.WRKY"）
        """
        # 如果提供了 prefix，优先从中解析 species 和 gene_family
        if provided_prefix and "." in provided_prefix:
            parts = provided_prefix.split(".", 1)
            intent_data["species"] = parts[0]
            intent_data["gene_family"] = parts[1]
            print(f"[IntentAgentV2] Prefix override in validation: species={parts[0]}, gene_family={parts[1]}")
        else:
            # 确保有基因家族；不能用全局默认家族，后续由 config/final 或澄清处理。
            gene_family = intent_data.get("gene_family", "")
            if not gene_family or "未知" in gene_family or "待用户" in gene_family:
                intent_data["gene_family"] = self._extract_gene_family_fallback(query)
            
            # 确保有物种；不能用全局默认物种。
            species = intent_data.get("species", "")
            if not species or "未知" in species or "待用户" in species:
                intent_data["species"] = ""
        
        # 确保有stages
        if not intent_data.get("stages"):
            # 尝试关键词匹配补充（而不是简单默认为identify）
            fallback_stages = self._extract_stages_from_query(query)
            if fallback_stages:
                intent_data["stages"] = fallback_stages
                print(f"[IntentAgentV2] Fallback keyword match: {fallback_stages}")
            else:
                query_lower = query.lower()
                has_rnaseq_keyword = any(
                    kw in query_lower for kw in ["rnaseq", "rna-seq", "rna seq", "转录组", "fastq"]
                )
                intent_data["stages"] = (
                    ["rnaseq_de", "rnaseq_go_enrich", "rnaseq_kegg_enrich", "rnaseq_fgsea", "rnaseq_wgcna"]
                    if has_rnaseq_keyword
                    else []
                )
                if not intent_data["stages"]:
                    intent_data.update(
                        {
                            "description": "未能可靠识别要执行的分析阶段",
                            "needs_clarification": True,
                            "clarification_type": "analysis_stage",
                            "clarification_question": "请说明要执行的具体分析阶段",
                        }
                    )
        
        excluded_stages = set(extract_negated_target_stages(query))
        if excluded_stages:
            intent_data["stages"] = [
                stage for stage in intent_data.get("stages", [])
                if stage not in excluded_stages
            ]
            if intent_data.get("primary_stage") in excluded_stages:
                intent_data["primary_stage"] = intent_data["stages"][0] if intent_data["stages"] else None
            if not intent_data["stages"]:
                intent_data.update({
                    "description": "未检测到需要执行的正向分析阶段",
                    "needs_clarification": True,
                    "clarification_type": "analysis_stage",
                    "clarification_question": "请说明要执行的具体分析阶段",
                })

        # 根据依赖关系补充缺失的阶段（关键修复：确保依赖展开）
        original_stages = intent_data["stages"]
        intent_data["stages"] = self._complete_dependencies(original_stages)
        print(f"[IntentAgentV2] Stages expanded: {original_stages} -> {intent_data['stages']}")
        
        query_lower = query.lower()
        selection = parse_gene_family_scope_selection(query)
        params = intent_data.setdefault("params", {})

        def as_species_list(value: Any) -> List[str]:
            if isinstance(value, str):
                values = [item.strip() for item in value.split(",")]
            elif isinstance(value, (list, tuple, set)):
                values = [str(item).strip() for item in value]
            else:
                values = []
            return list(dict.fromkeys(item for item in values if item))

        target_species = as_species_list(selection.get("targetSpecies"))
        reference_species = as_species_list(selection.get("referenceSpecies"))
        comparative_species = as_species_list(selection.get("comparativeSpecies"))
        has_query_scope = bool(target_species or reference_species or comparative_species)

        prefix_species = provided_prefix.split(".", 1)[0].strip() if provided_prefix and "." in provided_prefix else ""
        if not target_species and prefix_species:
            target_species = [prefix_species]
        if not target_species:
            target_species = as_species_list(params.get("targetSpecies") or intent_data.get("target_species"))
        if not reference_species:
            reference_species = as_species_list(params.get("referenceSpecies") or intent_data.get("reference_species"))
        if not comparative_species:
            comparative_species = as_species_list(params.get("comparativeSpecies") or intent_data.get("comparative_species"))
        if not target_species and not has_query_scope:
            llm_species = str(intent_data.get("species") or "").strip()
            if llm_species and llm_species != "Multi":
                target_species = [llm_species]

        reference_species = [sp for sp in reference_species if sp not in target_species]
        comparative_species = [
            sp for sp in comparative_species
            if sp not in target_species and sp not in reference_species
        ]
        scope_mode = (
            "target_plus_reference_plus_comparative"
            if reference_species and comparative_species
            else "target_plus_reference"
            if reference_species
            else "target_plus_comparative"
            if comparative_species
            else "target_only"
        )

        intent_data["target_species"] = target_species
        intent_data["reference_species"] = reference_species
        intent_data["comparative_species"] = comparative_species
        intent_data["scopeMode"] = scope_mode
        params.update({
            "targetSpecies": target_species,
            "referenceSpecies": reference_species,
            "comparativeSpecies": comparative_species,
            "scopeMode": scope_mode,
            "target_species": target_species,
        })
        explicit_species = bool(target_species)
        if target_species:
            intent_data["species"] = target_species[0] if len(target_species) == 1 else "Multi"
        elif has_query_scope:
            intent_data["species"] = ""
        generic_identify = (
            "identify" in intent_data.get("stages", [])
            and not provided_prefix
            and not explicit_species
            and any(kw in query_lower for kw in ["基因家族成员鉴定", "基因家族鉴定", "identify"])
        )
        if generic_identify and not intent_data.get("species"):
            intent_data["species"] = "Multi"

        # 生成prefix
        if intent_data.get("species") and intent_data.get("gene_family"):
            intent_data["prefix"] = f"{intent_data['species']}.{intent_data['gene_family']}"
        else:
            intent_data["prefix"] = intent_data.get("gene_family", "")
        
        # 确保有描述
        if not intent_data.get("description"):
            stages_str = ", ".join(intent_data["stages"])
            intent_data["description"] = f"执行{intent_data['gene_family']}基因家族的{stages_str}分析"
        
        return intent_data
    
    def _complete_dependencies(self, stages: List[str]) -> List[str]:
        """根据依赖关系补充缺失的阶段"""
        return catalog_complete_dependencies(stages)
    
    def _extract_gene_family_fallback(self, query: str) -> str:
        """降级：从查询中提取基因家族（复用 intent_parser 的过滤逻辑）"""
        result = parse_gene_family(query)
        return result if result else ""
    
    def _extract_stages_from_query(self, query: str) -> List[str]:
        """从查询中通过关键词匹配提取分析阶段"""
        query_lower = query.lower()
        
        stages = []
        if any(kw in query_lower for kw in ["鉴定", "识别", "identify"]):
            stages.append("identify")
        if any(kw in query_lower for kw in ["进化树", "系统发育", "phylogeny", "进化", "tree"]):
            stages.append("phylogeny")
        if any(kw in query_lower for kw in ["确认亚家族", "亚家族确认", "确认亚家族标签", "人工确认亚家族", "subfamily confirm", "confirmed subfamily"]):
            stages.append("subfamily_confirm")
        elif any(kw in query_lower for kw in ["预测亚家族", "亚家族分类", "参考亚家族", "参考分类", "亚家族", "subfamily", "reference classification"]):
            stages.append("reference_classification")
        if any(kw in query_lower for kw in ["motif", "基序", "保守"]):
            stages.append("motif")
        # 注意："结构域" 包含 "结构"，需要先检查 domain，避免同时匹配 structure
        if any(kw in query_lower for kw in ["domain", "结构域", "pfam"]):
            stages.append("domain")
        elif any(kw in query_lower for kw in ["结构", "structure", "外显子", "内含子", "基因结构"]):
            # 使用 elif 确保 "结构域" 不会同时匹配 structure
            stages.append("structure")
        if any(kw in query_lower for kw in ["启动子", "promoter", "顺式作用元件"]):
            stages.append("promoter")
        if any(kw in query_lower for kw in ["ppi", "蛋白互作", "protein interaction", "互作"]):
            stages.append("ppi")
        if any(kw in query_lower for kw in ["kaks", "ka/ks", "选择压力", "dnds", "ks", "ka"]):
            stages.append("kaks")
        # 注意："种间共线性/物种间共线性" 含 "共线性"，必须先匹配种间
        if any(
            kw in query_lower
            for kw in [
                "种间",
                "interspecies",
                "跨物种",
                "种间共线性",
                "物种间共线性",
                "物种间",
            ]
        ):
            stages.append("interspecies_synteny")
        elif any(kw in query_lower for kw in ["种内共线性", "intraspecies"]):
            stages.append("synteny")
        elif any(
            kw in query_lower for kw in ["共线性", "synteny", "mcscanx", "circos", "微共线性"]
        ):
            # 未写明种间/种内时默认走种内 MCScanX（歧义由 clarify / 前端对话框处理）
            stages.append("synteny")
        if any(kw in query_lower for kw in ["核型", "karyotype", "染色体分布", "chromosome", "染色体"]):
            stages.append("karyotype")
        # 注意：区分 expression（基因家族表达分析）和 rnaseq_de（RNA-seq差异表达）
        # expression 关键词：表达分析、expression、expression analysis
        # rnaseq 关键词：rnaseq、rna-seq、转录组测序（这些应该匹配到 rnaseq 流程，不是 expression）
        # 
        # 关键规则：如果查询中包含 RNA-seq 相关词汇，则跳过 expression，使用 RNA-seq 流程
        has_rnaseq_keyword = any(
            kw in query_lower for kw in ["rnaseq", "rna-seq", "rna seq", "转录组", "转录组测序", "fastq"]
        )
        explicit_gene_family_context = any(kw in query_lower for kw in ["基因家族", "家族表达", "表达量", "热图", "heatmap"])
        extraction_context = any(kw in query_lower for kw in ["提取", "extract", "gffread", "筛选", "subseq"])
        
        if not has_rnaseq_keyword:
            # 只有不含 RNA-seq 关键词时，才考虑 expression
            if any(kw in query_lower for kw in ["表达分析", "expression analysis", "基因表达", "热图", "heatmap"]):
                stages.append("expression")
            elif any(kw in query_lower for kw in ["表达", "expression"]):
                stages.append("expression")
        if not extraction_context and any(kw in query_lower for kw in ["pep", "蛋白特征", "理化性质", "蛋白性质", "蛋白理化特性", "蛋白理化特征"]):
            stages.append("pep")
        if any(kw in query_lower for kw in ["combo", "组合", "组合图"]):
            stages.append("combo")
        if any(kw in query_lower for kw in ["背景注释", "功能注释", "基因组注释", "自动注释", "eggnog", "annotation"]):
            stages.append("annotation")
        # MSA（多序列比对）：需要排除 RNA-seq 相关查询
        # "比对" 这个词在 RNA-seq 上下文中通常指 reads 比对，不是多序列比对
        if any(kw in query_lower for kw in ["msa", "多序列比对", "multiple alignment"]):
            stages.append("msa")
        elif any(kw in query_lower for kw in ["比对", "alignment"]):
            # 单独的 "比对" 或 "alignment"：如果有 RNA-seq 关键词，则跳过 MSA
            if not has_rnaseq_keyword:
                stages.append("msa")
        if any(kw in query_lower for kw in ["ortholog", "同源", "同源基因"]):
            stages.append("ortholog")
        # RNA-seq 流程
        # 注意：这些关键词需要包含 RNA-seq 前缀，避免与基因家族分析阶段冲突
        if has_rnaseq_keyword and any(kw in query_lower for kw in ["rnaseq_qc", "rna质控", "rna-seq质控", "质控", "qc"]):
            stages.append("rnaseq_qc")
        if has_rnaseq_keyword and any(kw in query_lower for kw in ["rnaseq_align", "rna比对", "rna-seq比对", "比对", "align", "mapping"]):
            stages.append("rnaseq_align")
        if has_rnaseq_keyword and any(kw in query_lower for kw in ["rnaseq_quant", "rna定量", "rna-seq定量", "定量", "quant", "count"]):
            stages.append("rnaseq_quant")
        # RNA-seq 差异表达：需要明确的 RNA-seq 前缀，或与其他 RNA-seq 关键词一起出现
        # 单独的 "差异表达" 可能指代基因家族表达分析中的差异表达，不一定是 RNA-seq
        # RNA-seq 差异表达和富集也需要 has_rnaseq_keyword
        if has_rnaseq_keyword and any(kw in query_lower for kw in ["rnaseq_de", "rna差异", "rna-seq差异", "de", "deg", "差异基因"]):
            stages.append("rnaseq_de")
        elif has_rnaseq_keyword and "差异表达" in query_lower:
            # "差异表达" + RNA-seq 关键词 = RNA-seq 差异表达
            stages.append("rnaseq_de")
        if not has_rnaseq_keyword:
            if any(kw in query_lower for kw in ["候选家族", "候选基因家族", "家族排名", "tf注释", "tf family", "tffamily", "pfam注释", "deg注释", "差异基因注释"]):
                stages.append("rnaseq_candidate_family")
            if any(kw in query_lower for kw in ["fgsea", "gsea", "排序富集", "基因集富集"]):
                stages.append("rnaseq_fgsea")
            if any(kw in query_lower for kw in ["wgcna", "共表达", "加权共表达", "模块性状"]):
                stages.append("rnaseq_wgcna")
            if any(kw in query_lower for kw in ["样本质量", "样本质量评估", "样本检测", "sample qc", "sample quality", "pca", "样本相关性", "样本聚类"]):
                stages.append("rnaseq_sample_qc")
            if any(kw in query_lower for kw in ["差异表达", "差异分析", "差异基因", "deg", "deseq2", "edger"]) and not explicit_gene_family_context:
                stages.append("rnaseq_de")
            if any(kw in query_lower for kw in ["质控", "qc", "fastqc", "测序质量"]):
                stages.append("rnaseq_qc")
            if any(kw in query_lower for kw in ["定量", "quant", "featurecounts", "count矩阵", "计数矩阵"]):
                stages.append("rnaseq_quant")
            if any(kw in query_lower for kw in ["比对", "align", "mapping", "hisat2", "star"]) and not any(kw in query_lower for kw in ["多序列", "msa", "multiple alignment"]):
                stages.append("rnaseq_align")
        # 注意：区分 RNA-seq 富集 (rnaseq_enrich) 和基因家族富集 (enrichment)
        if has_rnaseq_keyword and any(kw in query_lower for kw in ["rnaseq_enrich", "rna富集", "rna-seq富集"]):
            if "kegg" in query_lower:
                stages.append("rnaseq_kegg_enrich")
            elif re.search(r"(^|[^a-z])go([^a-z]|$)", query_lower) or "go富集" in query_lower or "go分析" in query_lower:
                stages.append("rnaseq_go_enrich")
            else:
                stages.append("rnaseq_enrich")
        # 通用的富集分析：当前标准执行器使用 RNA-seq 富集子阶段
        if any(kw in query_lower for kw in ["富集", "enrichment", "enrich", "go分析", "kegg", "通路分析", "pathway"]):
            if "kegg" in query_lower:
                stages.append("rnaseq_kegg_enrich")
            elif re.search(r"(^|[^a-z])go([^a-z]|$)", query_lower) or "go富集" in query_lower or "go分析" in query_lower:
                stages.append("rnaseq_go_enrich")
            else:
                stages.append("rnaseq_enrich")
        # 基因组准备
        if any(kw in query_lower for kw in ["genome_prepare", "基因组准备", "准备基因组"]):
            stages.append("genome_prepare")
        curated_member_context = (
            any(kw in query_lower for kw in ["修订后的", "修订后", "人工修订", "curated", "edited"])
            and any(kw in query_lower for kw in ["家族id", "成员id", "基因家族id", "member id", "members"])
        )
        if curated_member_context or any(kw in query_lower for kw in ["修订成员", "修订鉴定成员", "修改成员", "删除基因", "删除", "去除基因", "去掉基因", "移除基因", "补充基因", "遗漏基因", "重建蛋白", "人工修订id", "curated members", "refine identify"]):
            stages.append("identify_refine")
        if any(kw in query_lower for kw in ["整理文件", "组织文件", "整理数据", "标准化命名", "重命名文件", "config整理"]):
            stages.append("prepare_organize")
        if any(kw in query_lower for kw in ["修订gff", "规范化gff", "gff id", "gff整理"]):
            stages.append("prepare_normalize_gff")
        if any(kw in query_lower for kw in ["最长转录本", "最长cds", "primary transcript", "longest transcript", "提取最长"]):
            stages.append("prepare_longest_transcript")
        explicit_gff_extract_context = extraction_context and any(
            kw in query_lower for kw in ["gff", "gff3", "gtf", "gffread", "genome", "基因组"]
        )
        if explicit_gff_extract_context and any(kw in query_lower for kw in ["提取pep", "提取蛋白", "提取蛋白序列", "gffread pep", "extract pep"]):
            stages.append("prepare_extract_pep")
        if explicit_gff_extract_context and any(kw in query_lower for kw in ["提取cds", "提取cds序列", "gffread cds", "extract cds"]):
            stages.append("prepare_extract_cds")
        if any(kw in query_lower for kw in ["按id提取", "按id筛选", "提取fasta", "筛选fasta", "subseq"]):
            stages.append("prepare_filter_fasta")
        if any(kw in query_lower for kw in ["ncbi id", "ncbi修订", "ncbi简化", "ncbi header", "简化ncbi"]):
            stages.append("prepare_simplify_ncbi")
        
        if not stages and has_rnaseq_keyword and any(
            kw in query_lower for kw in ["分析", "执行", "运行", "开始", "流程", "pipeline"]
        ):
            stages.extend(["rnaseq_de", "rnaseq_go_enrich", "rnaseq_kegg_enrich", "rnaseq_fgsea", "rnaseq_wgcna"])
        
        if "identify_refine" in stages:
            stages = [stage for stage in stages if stage not in {"identify", "prepare_extract_pep", "prepare_extract_cds"}]
        if any(stage.startswith("rnaseq") for stage in stages):
            stages = [stage for stage in stages if stage not in {"expression", "msa"}]
        excluded_stages = set(extract_negated_target_stages(query))
        stages = [stage for stage in stages if stage not in excluded_stages]
        return list(dict.fromkeys(stages))
    
    def _fallback_recognition(self, query: str) -> Dict[str, Any]:
        """降级：使用规则匹配识别意图"""
        
        # 使用关键词匹配提取阶段
        stages = self._extract_stages_from_query(query)
        
        excluded_stages = set(extract_negated_target_stages(query))
        if not stages:
            query_lower = query.lower()
            has_rnaseq_keyword = any(
                kw in query_lower for kw in ["rnaseq", "rna-seq", "rna seq", "转录组", "fastq"]
            )
            if has_rnaseq_keyword:
                stages = [
                    stage for stage in (
                        "rnaseq_de", "rnaseq_go_enrich", "rnaseq_kegg_enrich",
                        "rnaseq_fgsea", "rnaseq_wgcna",
                    )
                    if stage not in excluded_stages
                ]
        
        # 补充依赖（使用 stage_catalog 的主规划依赖链）
        original_stages = stages.copy()
        stages = self._complete_dependencies(stages)
        print(f"[_fallback_recognition] Stages: {original_stages} -> {stages}")
        
        gene_family = self._extract_gene_family_fallback(query)
        species = "Multi" if any(kw in query.lower() for kw in ["基因家族成员鉴定", "基因家族鉴定", "identify"]) else ""
        
        result = {
            "gene_family": gene_family,
            "species": species,
            "prefix": f"{species}.{gene_family}",
            "stages": stages,
            "description": (
                f"执行{gene_family}基因家族的{', '.join(stages)}分析"
                if stages
                else "未能可靠识别要执行的分析阶段"
            ),
            "requirements": [],
            "confidence": 0.7 if stages else 0.0,
            "task_kind": "pipeline",
            "taskType": "pipeline",
            "mode": "fallback"
        }
        if not stages:
            result.update(
                {
                    "needs_clarification": True,
                    "clarification_type": "analysis_stage",
                    "clarification_question": "请说明要执行的具体分析阶段",
                }
            )
        return result


def create_intent_agent_v2(llm_manager):
    """创建IntentAgentV2实例"""
    return IntentAgentV2(llm_manager)
