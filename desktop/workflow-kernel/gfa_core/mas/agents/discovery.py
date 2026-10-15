"""
DiscoveryAgent V1 - CellVoyager风格的科学发现Agent

功能:
1. 分析执行结果，识别异常/新模式
2. 生成科学假设
3. 设计验证实验
4. 迭代优化分析计划

基于CellVoyager论文的REPLAN机制
"""
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict, is_dataclass
from enum import Enum
import json
import asyncio

from .base import AgentMessage, AgentState, BaseAgent
from gfa_core.mas.llm import LLMManager
from gfa_core.mas.dual_kb_qa import query_dual_kb_qa


class DiscoveryType(Enum):
    """发现类型"""
    ANOMALY = "anomaly"           # 异常检测
    PATTERN = "pattern"           # 新模式
    CORRELATION = "correlation"   # 相关性
    HYPOTHESIS = "hypothesis"     # 新假设


@dataclass
class AnomalyDetection:
    """异常检测结果"""
    detected: bool
    description: str
    severity: str  # low, medium, high
    affected_genes: List[str] = field(default_factory=list)
    expected_value: Any = None
    actual_value: Any = None


@dataclass
class GeneratedHypothesis:
    """生成的假设"""
    hypothesis: str
    rationale: str
    confidence: float
    testable: bool
    suggested_experiments: List[str] = field(default_factory=list)
    required_data: List[str] = field(default_factory=list)


@dataclass
class DiscoveryResult:
    """发现结果"""
    discovery_type: DiscoveryType
    description: str
    confidence: float
    hypothesis: Optional[GeneratedHypothesis] = None
    anomalies: List[AnomalyDetection] = field(default_factory=list)
    updated_plan: Optional[Dict] = None
    should_continue: bool = False


class DiscoveryAgent(BaseAgent):
    """
    科学发现Agent - CellVoyager风格
    
    核心流程:
    1. INSPECT - 检查结果
    2. ANALYZE - 分析异常/模式
    3. HYPOTHESIZE - 生成假设
    4. DESIGN - 设计验证
    5. REPLAN - 更新计划
    """
    name = "discovery"
    role = "Scientific Discovery Assistant"
    
    def __init__(self, llm_manager: LLMManager):
        super().__init__(llm_manager)
        self.discovery_history: List[DiscoveryResult] = []
        self.max_iterations = 3
    
    async def execute(self, state: AgentState, message: AgentMessage) -> AgentState:
        """
        执行发现流程（科研模式：存储解读结果，支持按需生成深度报告）
        
        Args:
            state: 当前状态，包含执行结果
            message: 消息，可能包含特定发现指令
                - mode: "quick" (快速模式，只存储结果) 
                        "deep" (深度模式，立即生成解读)
                        "generate_report" (生成综合报告)
        
        Returns:
            更新后的状态，包含发现结果
        """
        mode = message.payload.get("mode", "quick")  # 默认快速模式
        print(f"[DiscoveryAgent] 开始科学发现流程 (模式: {mode})...")
        
        # 特殊模式：生成综合深度报告
        if mode == "generate_report":
            return await self._generate_comprehensive_report(state, message)
        
        # 1. 获取当前结果（支持多种数据格式）
        execution_results = self._normalize_execution_results(state)
        current_plan = state.results.get("plan", state.results.get("task_plan", {}))
        past_discoveries = state.results.get("discoveries", [])
        
        # 提取基因家族名称
        gene_family = self._extract_gene_family_from_state(state)
        print(f"[DiscoveryAgent] 分析基因家族: {gene_family}")
        
        # 2. 检查异常（使用RAG获取文献基准）
        anomalies = await self._detect_anomalies(execution_results, gene_family)
        print(f"[DiscoveryAgent] 检测到 {len(anomalies)} 个异常")
        
        # 3. 分析模式
        patterns = await self._analyze_patterns(execution_results)
        print(f"[DiscoveryAgent] 识别到 {len(patterns)} 个模式")
        
        # 4. 生成假设（只要有可用执行摘要即调用 LLM，避免「无异常就无话」）
        hypothesis = None
        if execution_results:
            hypothesis = await self._generate_hypothesis(
                anomalies, patterns, execution_results
            )
            if hypothesis:
                print(f"[DiscoveryAgent] 生成假设: {hypothesis.hypothesis[:80]}...")
        
        # 5. 决定是否继续迭代
        should_continue = self._should_continue_iteration(
            anomalies, patterns, hypothesis, len(past_discoveries)
        )
        
        # 6. 更新计划（如需要）
        updated_plan = None
        if should_continue and hypothesis:
            updated_plan = await self._update_plan(current_plan, hypothesis)
            print(f"[DiscoveryAgent] 更新分析计划")
        
        # 7. 构建发现结果
        discovery_result = DiscoveryResult(
            discovery_type=self._determine_discovery_type(anomalies, patterns),
            description=self._build_description(anomalies, patterns, hypothesis),
            confidence=hypothesis.confidence if hypothesis else 0.5,
            hypothesis=hypothesis,
            anomalies=anomalies,
            updated_plan=updated_plan,
            should_continue=should_continue
        )
        
        # 8. 构建完整发现结果（包含原始数据，供后续深度解读使用）
        discovery_data = {
            "type": discovery_result.discovery_type.value,
            "description": discovery_result.description,
            "confidence": discovery_result.confidence,
            "hypothesis": {
                "text": hypothesis.hypothesis if hypothesis else None,
                "rationale": hypothesis.rationale if hypothesis else None,
                "confidence": hypothesis.confidence if hypothesis else None,
                "suggested_experiments": hypothesis.suggested_experiments if hypothesis else []
            },
            "anomalies": [
                {
                    "description": a.description,
                    "severity": a.severity,
                    "affected_genes": a.affected_genes
                }
                for a in anomalies
            ],
            "should_continue": should_continue,
            # 新增：存储原始数据，供深度解读使用
            "raw_data": {
                "execution_results": execution_results,
                "patterns": [p for p in patterns],
                "analysis_timestamp": self._get_timestamp()
            }
        }
        
        # 深度模式：立即生成详细解读
        if mode == "deep":
            deep_interpretation = await self._generate_deep_interpretation(discovery_data)
            discovery_data["deep_interpretation"] = deep_interpretation
            print(f"[DiscoveryAgent] 已生成深度解读")
        
        state.results["discovery_result"] = discovery_data
        
        stage_hint = message.payload.get("stage") or ""
        if not stage_hint:
            pr = message.payload.get("parse_result") or state.results.get("parsed_report") or {}
            if isinstance(pr, dict):
                stage_hint = pr.get("stage", "unknown")

        history_entry = {**discovery_data, "stage": stage_hint}
        self.discovery_history.append(discovery_result)
        past_discoveries.append(history_entry)
        state.results["discoveries"] = past_discoveries
        
        # 添加消息
        state.messages.append({
            "role": "discovery",
            "content": discovery_result.description,
            "metadata": {
                "confidence": discovery_result.confidence,
                "should_continue": should_continue
            }
        })
        
        print(f"[DiscoveryAgent] 发现流程完成，置信度: {discovery_result.confidence:.2f}")
        
        return state
    
    def _normalize_execution_results(self, state: AgentState) -> Dict:
        """
        标准化执行结果格式
        
        支持多种输入格式:
        1. Dict格式: {"identify": {...}, "phylogeny": {...}}
        2. List格式: [{"stage": "identify", ...}, {"stage": "phylogeny", ...}]
        3. 从parsed_report提取
        """
        results = {}
        
        # 尝试从execution_results获取（可能是list或dict）
        execution_results = state.results.get("execution_results", {})
        if isinstance(execution_results, dict):
            results = execution_results
        elif isinstance(execution_results, list):
            # 转换为dict格式
            for item in execution_results:
                if isinstance(item, dict):
                    stage = item.get("stage", "unknown")
                    results[stage] = item
        
        # 尝试从parsed_report获取更详细的数据
        parsed_report = state.results.get("parsed_report", {})
        if parsed_report:
            stage = parsed_report.get("stage", "unknown")
            if stage not in results:
                results[stage] = {}
            # 合并parsed数据
            results[stage].update({
                "metrics": parsed_report.get("metrics", {}),
                "parsed_data": parsed_report.get("parsed_data", {}),
                "status": parsed_report.get("status", "unknown")
            })
        
        # 尝试从单个execution_result获取（最新执行结果）
        execution_result = state.results.get("execution_result", {})
        if execution_result:
            manifest = execution_result.get("manifest", {})
            stage = manifest.get("stage", "unknown")
            if stage not in results:
                results[stage] = {}
            results[stage].update({
                "metrics": manifest.get("metrics", {}),
                "status": execution_result.get("status", "unknown"),
                "manifest": manifest
            })
        
        return results

    def _bucket_metrics(self, bucket: Any) -> Dict[str, Any]:
        if not isinstance(bucket, dict):
            return {}
        m = bucket.get("metrics")
        if isinstance(m, dict) and m:
            return m
        manifest = bucket.get("manifest") or {}
        if isinstance(manifest, dict):
            mm = manifest.get("metrics")
            if isinstance(mm, dict):
                return mm
        return {}

    def _discovery_record_as_dict(self, discovery: Any) -> Dict[str, Any]:
        """兼容历史中的 DiscoveryResult dataclass 与 dict。"""
        if isinstance(discovery, dict):
            return discovery
        if isinstance(discovery, DiscoveryResult):
            hyp = discovery.hypothesis
            hyp_dict = {}
            if hyp is not None:
                hyp_dict = {
                    "text": hyp.hypothesis,
                    "rationale": hyp.rationale,
                    "confidence": hyp.confidence,
                    "suggested_experiments": hyp.suggested_experiments or [],
                }
            return {
                "type": discovery.discovery_type.value,
                "description": discovery.description,
                "confidence": discovery.confidence,
                "should_continue": discovery.should_continue,
                "stage": getattr(discovery, "stage", None),
                "hypothesis": hyp_dict,
                "anomalies": [
                    asdict(a) if is_dataclass(a) else a for a in (discovery.anomalies or [])
                ],
            }
        return {"description": str(discovery), "type": "unknown"}
    
    async def _detect_anomalies(self, results: Dict, gene_family: str = "SPL") -> List[AnomalyDetection]:
        """
        检测异常
        
        检查执行结果中的异常情况:
        - 基因数量异常（过多/过少）- 基于RAG文献数据
        - 表达模式异常
        - 进化关系异常
        """
        anomalies = []
        
        # 检查鉴定结果 - 支持多种数据路径
        identify_data = results.get("identify", {})
        
        # 尝试多种路径获取基因数量
        gene_count = 0
        if "parsed_data" in identify_data:
            gene_count = identify_data["parsed_data"].get("gene_count", 0)
        if gene_count == 0 and "metrics" in identify_data:
            gene_count = identify_data["metrics"].get("final_members", 0)
        if gene_count == 0 and "manifest" in identify_data:
            gene_count = identify_data["manifest"].get("metrics", {}).get("final_members", 0)
        
        # 基因数量异常检测 - 使用RAG获取文献基准
        if gene_count > 0:
            # 查询RAG获取该基因家族的典型规模
            expected_range = await self._get_gene_family_size_from_rag(gene_family)
            min_expected = expected_range.get("min", 5)
            max_expected = expected_range.get("max", 200)
            median_expected = expected_range.get("median", (min_expected + max_expected) // 2)
            
            # 动态阈值：低于最小值50%或高于最大值200%视为异常
            # 例如：文献报道10-30个，低于5个或高于60个视为异常
            lower_threshold = max(3, int(min_expected * 0.5))  # 至少低于3个才触发
            upper_threshold = int(max_expected * 2.0)
            
            if gene_count < lower_threshold:
                anomalies.append(AnomalyDetection(
                    detected=True,
                    description=f"鉴定到的基因数量过少 ({gene_count}个)，远低于文献报道的{min_expected}-{max_expected}个",
                    severity="high",
                    affected_genes=[],
                    expected_value=f"{min_expected}-{max_expected} (中位数: {median_expected})",
                    actual_value=gene_count
                ))
            elif gene_count > upper_threshold:
                anomalies.append(AnomalyDetection(
                    detected=True,
                    description=f"鉴定到的基因数量过多 ({gene_count}个)，远高于文献报道的{min_expected}-{max_expected}个",
                    severity="medium",
                    affected_genes=[],
                    expected_value=f"{min_expected}-{max_expected} (中位数: {median_expected})",
                    actual_value=gene_count
                ))
        
        # 检查表达分析结果
        if "expression" in results:
            expr_data = results["expression"]
            # 检测异常表达模式
            # TODO: 实现具体检测逻辑
        
        return anomalies
    
    async def _get_gene_family_size_from_rag(self, gene_family: str) -> Dict:
        """
        从RAG查询基因家族的典型规模
        
        查询几个模式物种的数据，返回统计范围
        """
        try:
            import aiohttp
            import os
            
            api_url = os.environ.get("LEGACY_RAG_API_URL", "").rstrip("/")
            if api_url:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{api_url}/api/v1/rag/family/baseline",
                        params={"family": gene_family}
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            baseline = data.get("baseline") or {}
                            if baseline.get("count", 0) and baseline.get("min") is not None and baseline.get("max") is not None:
                                return {
                                    "min": int(baseline.get("min")),
                                    "max": int(baseline.get("max")),
                                    "median": baseline.get("median"),
                                    "count": int(baseline.get("count") or 0),
                                    "by_species": baseline.get("by_species") or [],
                                    "source": "rag_family_baseline"
                                }

            # 查询RAG获取基因家族规模信息
            query = f"{gene_family} gene family member count size Arabidopsis rice"
            
            data = await asyncio.to_thread(query_dual_kb_qa, query, top_k=5, use_llm=False)
            sources = data.get("sources", [])

            # 从文献中提取基因数量
            sizes = []
            for source in sources:
                content = str(source.get("content") or source.get("evidence") or "")
                # 尝试提取数字（如 "16 members", "about 20 genes"）
                import re
                # 匹配模式：数字 + members/genes/family
                matches = re.findall(r'(\d+)\s+(?:members?|genes?|family)', content, re.IGNORECASE)
                for match in matches:
                    try:
                        size = int(match)
                        if 1 <= size <= 1000:  # 合理范围
                            sizes.append(size)
                    except:
                        pass

            if sizes:
                sizes.sort()
                return {
                    "min": min(sizes),
                    "max": max(sizes),
                    "median": sizes[len(sizes)//2],
                    "count": len(sizes),
                    "source": "dual_kb_qa"
                }
        except Exception as e:
            print(f"[Discovery] RAG查询失败: {e}")
        
        # 回退到默认阈值（更严格的默认值）
        return {"min": 10, "max": 100, "median": 30, "source": "default"}
    
    def _extract_gene_family_from_state(self, state: AgentState) -> str:
        """从状态中提取基因家族名称"""
        # 尝试从多个来源提取
        intent = state.results.get("results", {}).get("intent", {}) or state.results.get("intent", {})
        if intent and "gene_family" in intent:
            return intent["gene_family"]
        
        # 从user_input提取
        user_input = state.results.get("user_input", {})
        if user_input and "gene_family" in user_input:
            return user_input["gene_family"]
        
        # 从prefix提取（如 "Ft.SPL" -> "SPL"）
        prefix = state.results.get("prefix", "")
        if prefix and "." in prefix:
            return prefix.split(".")[-1]
        
        return "SPL"  # 默认
    
    async def _analyze_patterns(self, results: Dict) -> List[Dict]:
        """
        基于 manifest 常见 metrics 做轻量模式提炼（给假设生成结构化抓手）。
        """
        patterns: List[Dict] = []

        id_b = results.get("identify") or {}
        mid = self._bucket_metrics(id_b)
        gcount = mid.get("final_members") or id_b.get("parsed_data", {}).get("gene_count")
        if gcount:
            patterns.append({
                "type": "gene_family_size_observed",
                "label": "鉴定规模",
                "detail": f"当前鉴定成员数(final_members/gene_count)={gcount}",
            })

        de_b = results.get("rnaseq_de") or {}
        mde = self._bucket_metrics(de_b)
        if mde.get("de_genes_total") is not None:
            tot = int(mde.get("de_genes_total") or 0)
            fam = mde.get("family_analysis") or {}
            dfg = fam.get("de_family_genes")
            tfg = fam.get("total_family_genes")
            patterns.append({
                "type": "rnaseq_de_overview",
                "label": "差异表达规模",
                "detail": (
                    f"全局 DE 基因 {tot}（上/下={mde.get('de_genes_up')}/{mde.get('de_genes_down')}）；"
                    f"家族内 DE {dfg}/{tfg}"
                ),
            })
            if tot >= 80 and dfg == 0:
                patterns.append({
                    "type": "global_de_decoupled_from_family",
                    "label": "家族与全局差异脱节",
                    "detail": "全局大量差异基因但家族层面计数为 0，提示胁迫响应可能主要在非家族基因或其它家族",
                })

        for st in ("rnaseq_kegg_enrich", "rnaseq_go_enrich"):
            eb = results.get(st) or {}
            me = self._bucket_metrics(eb)
            kp = me.get("kegg_enriched_pathways")
            gt = me.get("go_enriched_terms")
            if kp is not None or gt is not None:
                patterns.append({
                    "type": "enrichment_yield",
                    "label": f"{st} 产出",
                    "detail": f"KEGG pathways={kp}, GO terms={gt}, DE_input={me.get('de_genes_input')}",
                })

        phy_b = results.get("phylogeny") or {}
        mp = self._bucket_metrics(phy_b)
        if mp.get("tips") or mp.get("taxa_count"):
            patterns.append({
                "type": "phylogeny_resolution",
                "label": "系统发育规模",
                "detail": f"tips/taxa≈{mp.get('tips') or mp.get('taxa_count')}",
            })

        return patterns
    
    async def _generate_hypothesis(
        self,
        anomalies: List[AnomalyDetection],
        patterns: List[Dict],
        results: Dict
    ) -> GeneratedHypothesis:
        """
        基于异常和模式生成科学假设
        
        使用LLM生成可验证的假设
        """
        # 构建上下文
        context = {
            "anomalies": [
                {"description": a.description, "severity": a.severity}
                for a in anomalies
            ],
            "patterns": patterns,
            "results_summary": self._summarize_results(results)
        }
        
        prompt = f"""基于以下基因家族分析结果（可包含空的 anomalies/patterns），生成科学假设。
若未发现异常，请根据定量指标提出可检验的下一步科学问题，避免泛泛而谈。

分析结果摘要:
{json.dumps(context, ensure_ascii=False, indent=2)}

请生成:
1. 一个具体的、可验证的科学假设
2. 假设的生物学依据
3. 建议的验证实验
4. 需要的额外数据
5. 置信度评估 (0.0-1.0)

以JSON格式返回:
{{
    "hypothesis": "假设描述",
    "rationale": "依据",
    "confidence": 0.8,
    "testable": true,
    "suggested_experiments": ["实验1", "实验2"],
    "required_data": ["数据1", "数据2"]
}}"""
        
        try:
            response = await self._acall_llm(prompt, temperature=0.7)
            content = response.content
            
            # 解析JSON
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            data = json.loads(content.strip())
            
            return GeneratedHypothesis(
                hypothesis=data.get("hypothesis", "无法生成假设"),
                rationale=data.get("rationale", ""),
                confidence=data.get("confidence", 0.5),
                testable=data.get("testable", False),
                suggested_experiments=data.get("suggested_experiments", []),
                required_data=data.get("required_data", [])
            )
        except Exception as e:
            print(f"[DiscoveryAgent] 生成假设失败: {e}")
            return GeneratedHypothesis(
                hypothesis="基于当前结果无法生成明确假设",
                rationale="数据不足或分析结果不明确",
                confidence=0.3,
                testable=False
            )
    
    def _should_continue_iteration(
        self,
        anomalies: List[AnomalyDetection],
        patterns: List[Dict],
        hypothesis: Optional[GeneratedHypothesis],
        iteration_count: int
    ) -> bool:
        """
        决定是否继续迭代
        
        条件:
        1. 检测到重要异常（high或medium严重性）
        2. 生成了高置信度假设
        3. 未达到最大迭代次数
        """
        if iteration_count >= self.max_iterations:
            return False
        
        # 有高严重性异常（必须修复）
        high_severity = any(a.severity == "high" for a in anomalies)
        
        # 有中等严重性异常且没有高置信度假设时也需要迭代
        medium_severity = any(a.severity == "medium" for a in anomalies)
        
        # 有高置信度假设
        high_confidence = hypothesis and hypothesis.confidence > 0.7
        
        # 有值得关注的异常模式（非normal模式）
        significant_patterns = any(
            p.get("type") not in ["normal_gene_family_size"] for p in patterns
        )
        
        return high_severity or (medium_severity and not high_confidence) or significant_patterns
    
    async def _update_plan(
        self,
        current_plan: Dict,
        hypothesis: GeneratedHypothesis,
        learning_agent=None  # 传入Learning Agent获取参数建议
    ) -> Dict:
        """
        基于假设更新分析计划
        
        根据异常类型和假设，返回可执行的分析阶段
        
        返回格式必须与 PlanningAgentV2 一致:
        {
            "stages": [{"id": "identify", "params": {...}}, ...],
            "job_id": "...",
            "prefix": "...",
            "workspace": "..."
        }
        """
        import copy
        
        # 获取当前计划的元信息
        job_id = current_plan.get("job_id", "")
        prefix = current_plan.get("prefix", "")
        workspace = current_plan.get("workspace", "")
        
        # 根据异常/假设确定需要重新执行的阶段
        # 这里是示例逻辑，实际应根据 hypothesis.content 分析
        stages_to_add = []
        
        # 如果假设涉及参数调整，重新执行相关阶段
        if hypothesis and hypothesis.confidence > 0.7:
            # 解析假设中提到的建议
            suggested = hypothesis.suggested_experiments if hypothesis.suggested_experiments else []
            
            # 根据建议映射到可执行的 stage_id
            # 例如: "调整鉴定参数" -> 重新执行 identify
            # 例如: "增加motif数量" -> 重新执行 motif
            stage_mapping = {
                "鉴定": "identify",
                "进化树": "phylogeny",
                "motif": "motif",
                "结构域": "domain",
                "基因结构": "structure",
                "启动子": "promoter",
                "种间共线性": "interspecies_synteny",
                "物种间共线性": "interspecies_synteny",
                "跨物种共线性": "interspecies_synteny",
                "种内共线性": "synteny",
                "共线性": "synteny",
                "表达": "expression",
            }
            
            for suggestion in suggested:
                for keyword, stage_id in stage_mapping.items():
                    if keyword in suggestion:
                        stages_to_add.append({
                            "id": stage_id,
                            "reason": f"基于假设: {hypothesis.hypothesis[:50]}",
                            "params_adjustment": self._extract_param_adjustment(suggestion)
                        })
                        break
        
        # 如果没有特定阶段，默认重新执行 identify（参数调整）
        if not stages_to_add and hypothesis:
            stages_to_add.append({
                "id": "identify",
                "reason": f"基于异常检测重新鉴定",
                "params_adjustment": {"evalue": "放宽到 0.01"}
            })
        
        # 构建新计划
        updated_plan = {
            "stages": stages_to_add,
            "job_id": job_id,
            "prefix": prefix,
            "workspace": workspace,
            "triggered_by_hypothesis": hypothesis.hypothesis if hypothesis else "",
            "iteration_reason": "DiscoveryAgent 建议重新分析"
        }
        
        return updated_plan
    
    def _extract_param_adjustment(self, suggestion: str) -> Dict:
        """从建议中提取参数调整"""
        adjustments = {}
        
        # 简单的关键词匹配
        if "放宽" in suggestion or "增加" in suggestion:
            adjustments["mode"] = "relaxed"
        if "严格" in suggestion or "减少" in suggestion:
            adjustments["mode"] = "strict"
        
        return adjustments
    
    def _determine_discovery_type(
        self,
        anomalies: List[AnomalyDetection],
        patterns: List[Dict]
    ) -> DiscoveryType:
        """确定发现类型"""
        if anomalies:
            return DiscoveryType.ANOMALY
        elif patterns:
            return DiscoveryType.PATTERN
        else:
            return DiscoveryType.HYPOTHESIS
    
    def _build_description(
        self,
        anomalies: List[AnomalyDetection],
        patterns: List[Dict],
        hypothesis: Optional[GeneratedHypothesis]
    ) -> str:
        """构建发现描述"""
        parts = []
        
        if anomalies:
            parts.append(f"检测到{len(anomalies)}个异常:")
            for a in anomalies:
                parts.append(f"  - {a.description} (严重性: {a.severity})")

        if patterns:
            parts.append(f"\n数据模式 ({len(patterns)}):")
            for p in patterns:
                lab = p.get("label") or p.get("type")
                parts.append(f"  - [{lab}] {p.get('detail', '')}")
        
        if hypothesis:
            parts.append(f"\n生成假设: {hypothesis.hypothesis}")
            parts.append(f"置信度: {hypothesis.confidence:.2f}")
        elif not anomalies and not patterns:
            parts.append("当前未发现显著异常或结构化模式；建议查看下游 manifest 是否齐全。")
        
        return "\n".join(parts)
    
    def _summarize_results(self, results: Dict) -> Dict:
        """汇总各阶段关键 metrics，供假设生成使用"""
        summary: Dict[str, Any] = {}

        if "identify" in results:
            mid = self._bucket_metrics(results["identify"])
            summary["identify"] = {
                "final_members": mid.get("final_members"),
            }

        if "phylogeny" in results:
            summary["phylogeny"] = self._bucket_metrics(results["phylogeny"])

        if "rnaseq_de" in results:
            summary["rnaseq_de"] = self._bucket_metrics(results["rnaseq_de"])

        for k in ("rnaseq_quant", "rnaseq_align", "annotation"):
            if k in results:
                summary[k] = self._bucket_metrics(results[k])

        for k in ("rnaseq_go_enrich", "rnaseq_kegg_enrich"):
            if k in results:
                summary[k] = self._bucket_metrics(results[k])

        return summary
    
    async def _generate_deep_interpretation(self, discovery_data: Dict) -> Dict:
        """生成深度解读（科研模式）"""
        stage = discovery_data.get("type", "unknown")
        hypothesis = discovery_data.get("hypothesis", {})
        anomalies = discovery_data.get("anomalies", [])
        raw_compact = json.dumps(
            discovery_data.get("raw_data") or {},
            ensure_ascii=False,
            indent=2,
        )
        if len(raw_compact) > 3200:
            raw_compact = raw_compact[:3200] + "\n…(截断)"

        prompt = f"""你是一个基因家族分析专家。请基于以下发现结果，生成详细的科学解读。

发现类型: {stage}
假设: {hypothesis.get('text', '无')}
假设依据: {hypothesis.get('rationale', '无')}
异常检测: {len(anomalies)} 个异常

原始上下文摘录（patterns + execution_results 摘要）:
{raw_compact}

请生成以下内容的详细解读（JSON格式）:
{{
    "biological_significance": "该发现的生物学意义（2-3句）",
    "mechanism_hypothesis": "可能的分子机制假设",
    "functional_implications": "对基因功能的影响",
    "evolutionary_insights": "进化层面的启示",
    "experimental_suggestions": ["建议的验证实验1", "建议的验证实验2"],
    "literature_context": "与已知研究的关联"
}}

只返回JSON，不要其他内容。"""
        
        try:
            response = await self._acall_llm(prompt, temperature=0.7)
            content = response.content if hasattr(response, 'content') else str(response)
            
            # 提取JSON
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            interpretation = json.loads(content.strip())
            return interpretation
            
        except Exception as e:
            print(f"[DiscoveryAgent] 深度解读生成失败: {e}")
            return {
                "biological_significance": "需要进一步分析",
                "mechanism_hypothesis": "暂不明确",
                "error": str(e)
            }
    
    async def _generate_comprehensive_report(self, state: AgentState, message: AgentMessage) -> AgentState:
        """生成综合深度报告（所有阶段完成后调用，或按需调用）"""
        print(f"[DiscoveryAgent] 生成综合深度报告...")
        
        raw_discoveries = state.results.get("discoveries", [])
        all_discoveries = [self._discovery_record_as_dict(d) for d in raw_discoveries]
        execution_results = state.results.get("execution_results", {})
        
        # 获取当前阶段（如果是按需解读，只解读到当前阶段）
        current_stage = message.payload.get("current_stage", None)
        
        # 过滤发现结果：如果只解读到某个阶段
        if current_stage:
            filtered_discoveries = []
            for discovery in all_discoveries:
                filtered_discoveries.append(discovery)
                # 如果找到当前阶段，停止添加
                if discovery.get("stage") == current_stage:
                    break
            all_discoveries = filtered_discoveries
            print(f"[DiscoveryAgent] 生成阶段性报告，包含 {len(all_discoveries)} 个阶段")
        
        # 构建综合报告
        report_sections = []
        stage_names = []
        
        for i, discovery in enumerate(all_discoveries, 1):
            stage = discovery.get("stage") or discovery.get("type", f"阶段{i}")
            stage_names.append(stage)
            description = discovery.get("description", "")
            
            section = f"## 阶段 {i}: {stage}\n\n{description}\n"
            
            # 如果有深度解读，加入报告
            if "deep_interpretation" in discovery:
                interp = discovery["deep_interpretation"]
                section += f"\n### 科学解读\n"
                section += f"- **生物学意义**: {interp.get('biological_significance', 'N/A')}\n"
                section += f"- **机制假设**: {interp.get('mechanism_hypothesis', 'N/A')}\n"
                section += f"- **功能影响**: {interp.get('functional_implications', 'N/A')}\n"
            
            report_sections.append(section)
        
        # 生成阶段性综合结论（强调阶段间的关联）
        conclusion_prompt = f"""你是一个基因家族分析专家。请基于以下各阶段的分析结果，生成阶段性综合结论。

已完成的阶段: {' → '.join(stage_names)}

各阶段结果:
{chr(10).join([f"阶段 {i+1} ({d.get('stage', d.get('type', 'unknown'))}): {d.get('description', '')[:200]}" for i, d in enumerate(all_discoveries)])}

请重点分析：
1. 各阶段结果的内在关联（如：鉴定到的基因数如何影响进化树构建）
2. 阶段性科学发现（基于目前已完成的工作）
3. 下一步分析的建议（基于当前结果）
4. 潜在的生物学意义

请用中文生成专业、详细的解读。"""
        
        try:
            conclusion_response = await self._acall_llm(conclusion_prompt, temperature=0.7)
            conclusion = conclusion_response.content if hasattr(conclusion_response, 'content') else str(conclusion_response)
        except Exception as e:
            conclusion = f"综合结论生成失败: {e}"
        
        # 组装完整报告
        report_title = f"阶段性综合深度报告（{' → '.join(stage_names)}）" if len(stage_names) < 5 else "基因家族分析综合深度报告"
        
        comprehensive_report = f"""# 🔬 {report_title}

**分析流程**: {' → '.join(stage_names)}

**报告类型**: {'阶段性综合解读' if current_stage else '完整分析报告'}

---

{chr(10).join(report_sections)}

## 📊 阶段性综合结论

{conclusion}

---
*报告生成时间: {self._get_timestamp()}*
"""
        
        # 更新状态
        state.results["comprehensive_report"] = comprehensive_report
        state.results["discovery_result"] = {
            "report_generated": True,
            "sections_count": len(report_sections),
            "stages_included": stage_names,
            "mode": "comprehensive",
            "is_partial": current_stage is not None  # 标记是否为阶段性报告
        }
        
        state.messages.append({
            "role": "discovery",
            "content": f"📊 阶段性综合报告已生成（包含 {len(stage_names)} 个阶段）" if current_stage else "📊 完整综合报告已生成",
            "metadata": {
                "report_length": len(comprehensive_report),
                "stages": stage_names
            }
        })
        
        print(f"[DiscoveryAgent] 综合报告已生成，包含 {len(stage_names)} 个阶段，长度: {len(comprehensive_report)} 字符")
        
        return state
    
    def _get_timestamp(self) -> str:
        """获取当前时间戳"""
        from datetime import datetime
        return datetime.now().isoformat()


# 便捷函数
def create_discovery_agent(llm_manager: LLMManager) -> DiscoveryAgent:
    """创建DiscoveryAgent实例"""
    return DiscoveryAgent(llm_manager)
