"""
KnowledgeAgent V2 - 增强版知识检索
集成RAG，支持文献问答和引用
整合Learning模块，支持参数推荐和历史分析
"""
import json
import re
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from collections import defaultdict
from datetime import datetime, timedelta
from .base import AgentMessage, AgentState, BaseAgent
from gfa_core.mas.llm import LLMManager
from gfa_core.mas.dual_kb_qa import query_dual_kb_qa


class LearningModule:
    """
    Learning模块 - 作为KnowledgeAgent的子模块
    功能：
    1. 记录执行历史
    2. 分析最优参数组合
    3. 为用户推荐参数
    4. 自动调参建议
    """
    
    # 参数推荐默认值
    DEFAULT_PARAMS = {
        "identify": {
            "cpu": 8,
            "dom_i_evalue_max": 0.001,
            "hmm_cov_min": 0.3,
            "blast_evalue": "1e-05"
        },
        "phylogeny": {
            "cpu": 8,
            "bootstrap": 1000,
            "model": "MFP"
        }
    }
    
    def __init__(self, history_file: str = None):
        if history_file is None:
            # 默认保存到与knowledge.py同目录下的learning_data.json
            import os
            current_dir = os.path.dirname(os.path.abspath(__file__))
            self.history_file = Path(current_dir) / "learning_data.json"
        else:
            self.history_file = Path(history_file)
        self.execution_history: List[Dict] = []
        self.param_stats: Dict[str, Dict] = defaultdict(lambda: defaultdict(list))
        self._load_history()
    
    def _load_history(self):
        """加载历史记录"""
        if self.history_file.exists():
            try:
                with open(self.history_file) as f:
                    content = f.read().strip()
                    if not content:
                        # 文件为空，初始化空数据
                        self.execution_history = []
                        self.param_stats = defaultdict(lambda: defaultdict(list))
                        return
                    data = json.loads(content)
                    self.execution_history = data.get("executions", [])
                    self.param_stats = defaultdict(lambda: defaultdict(list), 
                                                   data.get("param_stats", {}))
            except Exception as e:
                print(f"[LearningModule] 加载学习历史失败: {e}")
                # 初始化空数据
                self.execution_history = []
                self.param_stats = defaultdict(lambda: defaultdict(list))
    
    def _save_history(self):
        """保存历史记录"""
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_file, 'w') as f:
                json.dump({
                    "executions": self.execution_history[-1000:],  # 保留最近1000条
                    "param_stats": dict(self.param_stats),
                    "last_updated": datetime.now().isoformat()
                }, f, indent=2)
        except Exception as e:
            print(f"[LearningModule] 保存学习历史失败: {e}")
    
    def record_execution(self, data: Dict) -> Dict:
        """记录执行历史"""
        record = {
            "timestamp": datetime.now().isoformat(),
            "stage": data.get("stage", "unknown"),
            "prefix": data.get("prefix", ""),
            "params": data.get("params", {}),
            "status": data.get("status", "unknown"),
            "elapsed_sec": data.get("elapsed_sec", 0),
            "final_members": data.get("final_members", 0),
            "success": data.get("status") == "success"
        }
        
        self.execution_history.append(record)
        
        # 更新参数统计
        stage = record["stage"]
        for param, value in record["params"].items():
            if isinstance(value, (int, float)):
                self.param_stats[stage][param].append({
                    "value": value,
                    "elapsed": record["elapsed_sec"],
                    "success": record["success"]
                })
        
        # 保存历史
        self._save_history()
        
        return {
            "success": True,
            "message": "执行记录已保存",
            "record_id": len(self.execution_history)
        }
    
    def recommend_params(self, stage: str) -> Dict:
        """推荐最优参数"""
        recommendations = {}
        
        # 获取该阶段的历史数据
        stage_history = [h for h in self.execution_history 
                        if h.get("stage") == stage and h.get("success")]
        
        if len(stage_history) < 3:
            # 数据不足，返回默认值
            return {
                "stage": stage,
                "confidence": "low",
                "message": "历史数据不足，使用默认参数",
                "params": self.DEFAULT_PARAMS.get(stage, {}),
                "history_count": len(stage_history)
            }
        
        # 分析各参数的最优值
        for param, records in self.param_stats[stage].items():
            if len(records) < 3:
                continue
            
            # 按成功率排序
            success_records = [r for r in records if r["success"]]
            if not success_records:
                continue
            
            # 找到执行时间最短的参数值
            best = min(success_records, key=lambda x: x["elapsed"])
            recommendations[param] = {
                "value": best["value"],
                "avg_time": sum(r["elapsed"] for r in success_records) / len(success_records),
                "success_rate": len(success_records) / len(records)
            }
        
        # 构建推荐参数
        recommended_params = {}
        for param, info in recommendations.items():
            recommended_params[param] = info["value"]
        
        # 补充缺失参数为默认值
        defaults = self.DEFAULT_PARAMS.get(stage, {})
        for param, value in defaults.items():
            if param not in recommended_params:
                recommended_params[param] = value
        
        return {
            "stage": stage,
            "confidence": "high" if len(stage_history) >= 10 else "medium",
            "message": f"基于 {len(stage_history)} 次成功执行推荐",
            "params": recommended_params,
            "details": recommendations,
            "history_count": len(stage_history)
        }
    
    def analyze_history(self) -> Dict:
        """分析历史数据"""
        if not self.execution_history:
            return {
                "total_executions": 0,
                "message": "暂无历史数据"
            }
        
        total = len(self.execution_history)
        successful = sum(1 for h in self.execution_history if h.get("success"))
        failed = total - successful
        
        # 按阶段统计
        stage_stats = defaultdict(lambda: {"total": 0, "success": 0})
        for h in self.execution_history:
            stage = h.get("stage", "unknown")
            stage_stats[stage]["total"] += 1
            if h.get("success"):
                stage_stats[stage]["success"] += 1
        
        # 计算平均执行时间
        avg_times = {}
        for stage in stage_stats:
            stage_records = [h for h in self.execution_history 
                           if h.get("stage") == stage and h.get("success")]
            if stage_records:
                avg_times[stage] = sum(h.get("elapsed_sec", 0) for h in stage_records) / len(stage_records)
        
        # 最近7天统计
        week_ago = datetime.now() - timedelta(days=7)
        recent = [h for h in self.execution_history 
                 if datetime.fromisoformat(h.get("timestamp", "2000-01-01")) > week_ago]
        
        return {
            "total_executions": total,
            "successful": successful,
            "failed": failed,
            "success_rate": round(successful / total * 100, 1) if total > 0 else 0,
            "stage_stats": dict(stage_stats),
            "avg_execution_time": avg_times,
            "recent_7_days": len(recent),
            "last_updated": datetime.now().isoformat()
        }
    
    def get_param_trends(self, stage: str, param: str) -> List[Dict]:
        """获取参数趋势"""
        records = self.param_stats.get(stage, {}).get(param, [])
        
        # 按时间排序
        sorted_records = sorted(records, key=lambda x: x.get("timestamp", ""))
        
        return [
            {
                "value": r["value"],
                "elapsed": r["elapsed"],
                "success": r["success"]
            }
            for r in sorted_records[-50:]  # 最近50条
        ]
    
    def clear_history(self):
        """清空历史"""
        self.execution_history = []
        self.param_stats = defaultdict(lambda: defaultdict(list))
        self._save_history()


@dataclass
class RetrievedDocument:
    """检索到的文档"""
    content: str
    source: str
    score: float
    metadata: Dict


class KnowledgeAgent(BaseAgent):
    """
    知识Agent V2
    
    功能：
    1. RAG检索增强问答
    2. 文献引用和来源标注
    3. 多轮对话上下文保持
    4. 知识库实时更新
    """
    name = "knowledge"
    role = "Knowledge Retrieval Specialist"
    
    def __init__(self, llm_manager: LLMManager, rag_retriever=None, 
                 learning_history_file: str = None):
        super().__init__(llm_manager)
        self.rag_retriever = rag_retriever
        self.conversation_history: List[Dict] = []
        self.max_history = 10
        # 初始化Learning模块
        self.learning_module = LearningModule(history_file=learning_history_file)
    
    async def execute(self, state: AgentState, message: AgentMessage) -> AgentState:
        """
        执行知识检索（增强版：支持多种功能模式）
        
        Args:
            state: 当前状态
            message: 包含查询的消息
        
        Returns:
            更新后的状态
        """
        query = message.payload.get("query", "")
        mode = message.payload.get("mode", "rag")  # rag, direct, hybrid, feasibility, report_enhance, recommend_params, analyze_history
        
        # 根据功能模式选择处理策略
        if mode == "recommend_params":
            # Learning功能：推荐参数
            stage = message.payload.get("stage", "identify")
            response = await self._recommend_params_response(stage)
        elif mode == "analyze_history":
            # Learning功能：分析历史
            response = await self._analyze_history_response()
        elif mode == "feasibility":
            # 功能3：分析方案生成（可行性研究）
            response = await self._generate_analysis_plan(query, state)
        elif mode == "report_enhance":
            # 功能4：报告增强
            analysis_results = message.payload.get("analysis_results", {})
            response = await self._enhance_report(query, analysis_results, state)
        elif mode == "rag":
            response = await self._rag_qa(query, state)
        elif mode == "direct":
            response = await self._direct_qa(query)
        else:  # hybrid
            response = await self._hybrid_qa(query, state)
        
        # 更新状态
        state.results["knowledge_response"] = response["answer"]
        state.results["knowledge_sources"] = response.get("sources", [])
        state.results["knowledge_mode"] = response.get("mode", mode)
        if response.get("knowledge_graph"):
            state.results["knowledge_graph"] = response["knowledge_graph"]
        if response.get("kg_entities") is not None:
            state.results["kg_entities"] = response["kg_entities"]
        
        # 如果是可行性研究或报告增强，保存额外结果
        if mode == "feasibility":
            state.results["feasibility_plan"] = response.get("plan", {})
        elif mode == "report_enhance":
            state.results["enhanced_report"] = response.get("report", {})
        elif mode == "recommend_params":
            state.results["param_recommendations"] = response.get("recommendations", {})
        elif mode == "analyze_history":
            state.results["history_analysis"] = response.get("analysis", {})
        
        # 记录对话历史
        self._add_to_history(query, response["answer"])
        
        state.messages.append({
            "role": "knowledge",
            "content": response["answer"],
            "metadata": {
                "sources": response.get("sources", []),
                "mode": mode
            }
        })
        
        return state
    
    async def _rag_qa(self, query: str, state: AgentState) -> Dict:
        """
        RAG问答 - 直接返回gene-family-mas-v2的原始回答
        
        优化：对于"有哪些物种"类问题，直接查询物种API返回完整列表，不走RAG+LLM
        优化：对于 Pfam 号类问题，直接使用结构化 family facts，避免把低分向量 chunk 当作引用展示。
        """
        try:
            import aiohttp
            import re
            
            # 检测是否是"有哪些物种"类查询
            is_species_list_query = self._is_species_list_query(query)
            
            # 如果是物种列表查询，直接返回物种API的结果（快速路径）
            if is_species_list_query:
                gene_family = self._extract_gene_family(query)
                species_list_data = await self._fetch_species_list(gene_family)
                if species_list_data and species_list_data.get("species"):
                    answer = self._format_species_list_answer(species_list_data, query)
                    return {
                        "answer": answer,
                        "sources": [],
                        "mode": "species_list",
                        "knowledge_graph": None,
                        "kg_entities": [],
                    }
            
            if self._is_pfam_query(query):
                gene_family = self._extract_gene_family(query)
                pfam_data = await self._fetch_family_pfam(gene_family)
                if pfam_data and pfam_data.get("pfam_ids"):
                    return {
                        "answer": self._format_family_pfam_answer(pfam_data),
                        "sources": self._format_family_pfam_sources(pfam_data),
                        "mode": "family_pfam",
                        "knowledge_graph": None,
                        "kg_entities": [],
                    }

            # 其他查询走当前 Dual-KB QA 服务
            data = await asyncio.to_thread(query_dual_kb_qa, query, top_k=15, use_llm=True)
            sources = data.get("sources", [])
            answer = data.get("answer", "")

            if not answer and not sources:
                return {
                    "answer": "未找到相关文献。",
                    "sources": [],
                    "mode": "dual_kb_qa",
                    "knowledge_graph": None,
                    "kg_entities": [],
                }

            return {
                "answer": answer,
                "sources": sources,
                "mode": data.get("mode", "dual_kb_qa"),
                "knowledge_graph": data.get("knowledge_graph"),
                "kg_entities": data.get("kg_entities") or [],
            }
        except Exception as e:
            return {
                "answer": f"Dual-KB QA 查询失败: {str(e)}。请确保 7902 Dual-KB QA 服务已启动。",
                "sources": [],
                "mode": "dual_kb_qa",
                "knowledge_graph": None,
                "kg_entities": [],
            }
    
    def _is_species_list_query(self, query: str) -> bool:
        """检测是否是查询物种列表的问题"""
        patterns = [
            r"有哪些物种",
            r"哪些物种",
            r"什么物种",
            r"哪些.*做过.*分析",
            r"哪些.*研究过",
            r".*物种.*列表",
        ]
        query_lower = query.lower()
        return any(re.search(p, query_lower) for p in patterns)

    def _is_pfam_query(self, query: str) -> bool:
        """检测是否是查询基因家族 Pfam 编号的问题。"""
        q = (query or "").lower()
        return any(token in q for token in ["pfam", "pfam号", "pfam编号", "pfam id", "pfam accession", "结构域编号"])
    
    def _extract_gene_family(self, query: str) -> str:
        """从查询中提取基因家族名称"""
        # 常见基因家族模式
        gf_patterns = [
            r"(SPL|NAC|WRKY|MYB|bHLH|AP2|ERF|bZIP|Dof|SBP)[^a-zA-Z]",
            r"(\w+).*基因家族",
            r"(\w+).*gene family",
        ]
        query_upper = query.upper()
        for pattern in gf_patterns:
            match = re.search(pattern, query_upper)
            if match:
                return match.group(1)
        return "SPL"  # 默认返回SPL

    async def _fetch_family_pfam(self, gene_family: str) -> dict:
        """从 gene-family-mas-v2 结构化 family facts 获取 Pfam 证据。"""
        try:
            import aiohttp
            import os

            api_url = os.environ.get("LEGACY_RAG_API_URL", "").rstrip("/")
            if not api_url:
                return {}
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{api_url}/api/v1/rag/family/pfam",
                    params={"family": gene_family}
                ) as response:
                    if response.status == 200:
                        return await response.json()
        except Exception as e:
            print(f"[KnowledgeAgent] 获取 family Pfam 证据失败: {e}")
        return {}

    def _format_family_pfam_answer(self, pfam_data: dict) -> str:
        family = pfam_data.get("family") or "该基因家族"
        pfam_ids = pfam_data.get("pfam_ids") or []
        if not pfam_ids:
            return f"未在结构化文献证据中找到 {family} 家族的 Pfam 号。"
        return f"{family} 家族的 Pfam 号是：**{', '.join(pfam_ids)}**。"

    def _format_family_pfam_sources(self, pfam_data: dict) -> list:
        """把 Pfam family facts 转成前端可显示的文章级引用。"""
        family = pfam_data.get("family") or ""
        confidence = pfam_data.get("confidence") or ""
        sources = []
        seen = set()
        for item in pfam_data.get("pfams") or []:
            pfam_id = item.get("pfam_id") or ""
            domain_name = item.get("domain_name") or ""
            support_count = item.get("support_count") or 0
            item_confidence = item.get("confidence") or confidence
            evidence_items = item.get("evidence") or []
            paper_evidence = [ev for ev in evidence_items if ev.get("paper_id")]
            candidates = paper_evidence or evidence_items
            for idx, ev in enumerate(candidates):
                paper_id = ev.get("paper_id") or ""
                source_path = ev.get("source_path") or ""
                key = paper_id or source_path
                if not key or key in seen:
                    continue
                seen.add(key)
                section = ev.get("section") or "Methods"
                title = f"{paper_id} 方法学 Pfam 证据" if paper_id else f"{family} {pfam_id} Pfam 证据"
                sources.append({
                    "doc_id": f"{paper_id or family}_{pfam_id}_family_pfam_{len(sources) + 1}",
                    "content": ev.get("evidence") or "",
                    "metadata": {
                        "version": "v15",
                        "type": "family_pfam",
                        "title": title,
                        "paper": paper_id,
                        "paper_id": paper_id,
                        "pmid": ev.get("pmid") or "",
                        "section": section,
                        "family": family,
                        "pfam_id": pfam_id,
                        "domain": domain_name,
                        "confidence": item_confidence,
                        "support_count": support_count,
                        "source_path": source_path,
                    },
                    "pmid": ev.get("pmid") or "",
                })
        return sources
    
    async def _fetch_species_list(self, gene_family: str) -> dict:
        """从API获取物种列表"""
        try:
            import aiohttp
            api_url = os.environ.get("LEGACY_RAG_API_URL", "").rstrip("/")
            if not api_url:
                return None
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{api_url}/api/v1/species/list?gene_family={gene_family}"
                ) as response:
                    if response.status == 200:
                        return await response.json()
        except Exception as e:
            print(f"[KnowledgeAgent] 获取物种列表失败: {e}")
        return None
    
    def _enhance_species_list_answer(self, original_answer: str, species_data: dict, query: str) -> str:
        """增强物种列表回答，确保包含所有物种"""
        if not species_data or "species" not in species_data:
            return original_answer
        
        species_list = species_data["species"]
        gene_family = species_data.get("gene_family", "SPL")
        total_species = species_data.get("total_species", len(species_list))
        
        # 构建物种列表文本
        species_text = []
        for sp in species_list:
            name = f"{sp.get('scientific_name', 'N/A')}"
            cn_name = sp.get('common_name_cn', '')
            en_name = sp.get('common_name_en', '')
            if cn_name:
                name += f" ({cn_name}"
                if en_name:
                    name += f", {en_name}"
                name += ")"
            species_text.append(f"  - {name}")
        
        # 构建增强后的回答
        enhanced = f"""## {gene_family}基因家族已分析的物种（共{total_species}种）

根据基因家族-物种索引，已对以下物种开展{gene_family}基因家族的全基因组鉴定与分析：

{chr(10).join(species_text)}

### 详细信息

"""
        
        # 附加原始回答（去除可能重复的物种列表部分）
        # 提取原始回答中的详细分析内容
        if original_answer and len(original_answer) > 100:
            # 尝试提取原始回答中的分析内容（通常在"2. 主要研究发现"之后）
            enhanced += original_answer
        else:
            enhanced += f"\n以上物种均已通过{gene_family}基因家族分析，涵盖模式植物、粮食作物、油料作物、蔬菜作物和果树等多个类别。"
        
        return enhanced
    
    def _format_species_list_answer(self, species_data: dict, query: str) -> str:
        """格式化物种列表回答（直接返回，不经过LLM）"""
        if not species_data or "species" not in species_data:
            return "未找到相关物种信息。"
        
        species_list = species_data["species"]
        gene_family = species_data.get("gene_family", "SPL")
        total_species = species_data.get("total_species", len(species_list))
        
        # 构建物种列表表格
        lines = [
            f"## {gene_family}基因家族已分析的物种（共{total_species}种）",
            "",
            "根据基因家族-物种索引，已对以下物种开展全基因组鉴定与分析：",
            "",
            "| 物种 | 学名 | 比较物种 | 关系数 | 实体数 |",
            "|:---|:---|:---|:---:|:---:|"
        ]
        
        for sp in species_list:
            name = sp.get('common_name_cn', 'N/A')
            scientific = sp.get('scientific_name', 'N/A')
            # 显示完整的比较物种列表
            comparison = ', '.join(sp.get('comparison_species', []))
            relations = sp.get('relations_count', 0)
            entities = sp.get('entities_count', 0)
            lines.append(f"| {name} | {scientific} | {comparison} | {relations} | {entities} |")
        
        lines.extend([
            "",
            "### 说明",
            "",
            f"- **{gene_family}基因家族**：SQUAMOSA启动子结合蛋白样基因家族",
            "- **研究覆盖**：木本经济作物、油料作物、蔬菜作物、药用植物及模式植物",
            "- **比较分析**：所有研究均以拟南芥和水稻作为核心参照物种",
            "",
            "如需了解某个物种的详细分析结果，请直接提问，如'苦荞SPL基因家族功能'。"
        ])
        
        return '\n'.join(lines)
    
    async def _direct_qa(self, query: str) -> Dict:
        """直接问答（不使用RAG）"""
        # 检查LLM是否可用
        if self.llm_manager is None:
            return {
                "answer": f"【知识问答模式】\n\n您的问题: {query}\n\n注意: LLM服务未配置，无法生成智能回答。请配置API密钥。",
                "sources": [],
                "mode": "direct",
                "note": "LLM not configured"
            }
        
        prompt = f"""你是一个基因家族分析专家。请回答用户的问题。

问题: {query}

请提供准确、专业的回答。如果问题涉及特定文献或数据，请说明需要查阅相关资料。"""
        
        try:
            response = await self._acall_llm(prompt, temperature=0.7)
            return {
                "answer": response.content,
                "sources": [],
                "mode": "direct"
            }
        except Exception as e:
            return {
                "answer": f"生成回答失败: {str(e)}",
                "sources": [],
                "error": str(e)
            }
    
    async def _hybrid_qa(self, query: str, state: AgentState) -> Dict:
        """
        混合模式问答
        
        先尝试RAG，如果检索不到再使用直接问答
        """
        rag_result = await self._rag_qa(query, state)
        
        # 如果RAG没有检索到内容，使用直接问答
        if not rag_result.get("sources"):
            direct_result = await self._direct_qa(query)
            direct_result["answer"] = f"【未检索到相关文献】\n\n{direct_result['answer']}"
            return direct_result
        
        return rag_result
    
    def _build_context(self, documents: List[RetrievedDocument]) -> str:
        """构建检索上下文"""
        context_parts = []
        for i, doc in enumerate(documents):
            context_parts.append(
                f"[文献{i+1}] 来源: {doc.source}\n"
                f"内容: {doc.content}\n"
            )
        return "\n---\n".join(context_parts)
    
    async def _generate_rag_response(self, query: str, context: str) -> str:
        """生成RAG回答"""
        prompt = f"""基于以下文献资料，回答用户关于基因家族分析的问题。

文献资料:
{context}

用户问题: {query}

要求:
1. 基于提供的文献资料回答
2. 如果文献中没有相关信息，明确说明
3. 回答要专业、准确
4. 在回答中标注引用来源，如[文献1]、[文献2]

回答:"""
        
        response = await self._acall_llm(prompt, temperature=0.5)
        return response.content
    
    def _add_citations(self, answer: str, sources: List[Dict]) -> str:
        """添加引用来源列表"""
        if not sources:
            return answer
        
        citations = "\n\n📚 **参考来源**:\n"
        for source in sources:
            citations += f"[{source['index']}] {source['source']} (相关度: {source['score']})\n"
        
        return answer + citations
    
    def _add_to_history(self, query: str, answer: str):
        """添加到对话历史"""
        self.conversation_history.append({
            "query": query,
            "answer": answer,
            "timestamp": self._get_timestamp()
        })
        
        # 限制历史大小
        if len(self.conversation_history) > self.max_history:
            self.conversation_history = self.conversation_history[-self.max_history:]
    
    def _get_timestamp(self) -> str:
        """获取当前时间戳"""
        from datetime import datetime
        return datetime.now().isoformat()
    
    async def _generate_analysis_plan(self, query: str, state: AgentState) -> Dict:
        """
        功能3：生成分析方案（可行性研究）
        
        1. 检索物种数据资源（基因组、注释、CDS、PEP）
        2. 检索可用组学数据（转录组等）
        3. 判断本地/外部数据源
        4. 生成完整分析方案
        """
        print(f"[KnowledgeAgent] 生成分析方案: {query[:50]}...")
        
        # 提取物种和基因家族信息
        species, gene_family = self._extract_species_gene_family(query)
        
        # 1. 检索RAG知识库中的相关文献
        literature_check = await self._check_literature(species, gene_family)
        
        # 2. 生成分析方案
        plan = await self._create_analysis_plan(species, gene_family, literature_check)
        
        # 3. 生成自然语言回答
        answer = self._format_plan_answer(plan)
        
        return {
            "answer": answer,
            "plan": plan,
            "sources": literature_check.get("sources", []),
            "mode": "feasibility"
        }
    
    def _extract_species_gene_family(self, query: str) -> tuple:
        """从查询中提取物种和基因家族"""
        import re
        
        # 尝试匹配常见的基因家族名称
        gf_patterns = [r'([A-Z]{2,4})基因家族', r'([A-Z]{2,4})家族', r'([A-Z]{2,4})基因']
        gene_family = None
        for pattern in gf_patterns:
            match = re.search(pattern, query.upper())
            if match:
                gene_family = match.group(1)
                break
        
        # 尝试匹配物种
        species_patterns = {
            '荔枝': 'Lc', '龙眼': 'Ls', '拟南芥': 'At', '水稻': 'Os',
            '玉米': 'Zm', '大豆': 'Gm', '番茄': 'Sl', 'Ft': 'Ft'
        }
        species = 'Ft'  # 默认使用苦荞
        for cn, code in species_patterns.items():
            if cn in query:
                species = code
                break
        
        return species, gene_family or "SPL"
    
    async def _check_literature(self, species: str, gene_family: str) -> Dict:
        """检索相关文献"""
        try:
            query = f"{species} {gene_family} gene family"
            data = await asyncio.to_thread(query_dual_kb_qa, query, top_k=3, use_llm=False)
            sources = data.get("sources", [])

            # 分析文献
            has_genome = any("genome" in str(s.get("content") or s.get("evidence") or "").lower() for s in sources)
            has_transcriptome = any(
                "rna" in str(s.get("content") or s.get("evidence") or "").lower()
                or "transcript" in str(s.get("content") or s.get("evidence") or "").lower()
                for s in sources
            )

            return {
                "has_literature": len(sources) > 0,
                "paper_count": len(sources),
                "has_genome": has_genome,
                "has_transcriptome": has_transcriptome,
                "sources": sources
            }
        except Exception as e:
            print(f"[KnowledgeAgent] 文献检索失败: {e}")
        
        return {
            "has_literature": False,
            "paper_count": 0,
            "has_genome": False,
            "has_transcriptome": False,
            "sources": []
        }
    
    async def _create_analysis_plan(self, species: str, gene_family: str, 
                                   literature_check: Dict) -> Dict:
        """创建分析方案"""
        
        # 基础分析阶段（所有物种都可以做）
        base_stages = [
            {"id": "identify", "name": "基因家族鉴定", "priority": "required", 
             "description": "使用HMM和BLAST从基因组中识别基因家族成员", "estimated_time": "5分钟"},
            {"id": "phylogeny", "name": "系统发育分析", "priority": "required",
             "description": "构建进化树，分析基因家族的进化关系", "estimated_time": "30分钟"},
            {"id": "motif", "name": "Motif分析", "priority": "recommended",
             "description": "识别蛋白序列中的保守基序", "estimated_time": "20分钟"},
            {"id": "domain", "name": "结构域分析", "priority": "recommended",
             "description": "分析蛋白结构域组成", "estimated_time": "15分钟"},
            {"id": "structure", "name": "基因结构分析", "priority": "recommended",
             "description": "分析基因的外显子-内含子结构", "estimated_time": "10分钟"},
        ]
        
        # 可选分析阶段（取决于数据可用性）
        optional_stages = []
        
        if literature_check.get("has_genome"):
            optional_stages.extend([
                {"id": "synteny", "name": "共线性分析", "priority": "optional",
                 "description": "分析基因组内的共线性关系", "estimated_time": "30分钟"},
                {"id": "karyotype", "name": "核型分析", "priority": "optional",
                 "description": "分析基因在染色体上的分布", "estimated_time": "10分钟"},
            ])
        
        if literature_check.get("has_transcriptome"):
            optional_stages.extend([
                {"id": "expression", "name": "表达分析", "priority": "optional",
                 "description": "分析基因表达模式", "estimated_time": "15分钟"},
            ])
        
        # 判断可行性
        feasibility = "high" if literature_check.get("has_genome") else "medium"
        
        return {
            "species": species,
            "gene_family": gene_family,
            "feasibility": feasibility,
            "literature_support": literature_check.get("paper_count", 0),
            "base_stages": base_stages,
            "optional_stages": optional_stages,
            "total_estimated_time": "约2-3小时（基础分析）",
            "requirements": [
                "基因组文件（FASTA格式）",
                "基因注释文件（GFF格式）",
                "蛋白序列文件（可选，用于验证）"
            ],
            "recommendations": [
                "建议从基因家族鉴定开始",
                "如果基因组数据完整，可以进行共线性分析",
                "如果有转录组数据，建议增加表达分析"
            ]
        }
    
    def _format_plan_answer(self, plan: Dict) -> str:
        """格式化方案为自然语言回答"""
        
        feasibility_text = {
            "high": "✅ 可行性高",
            "medium": "⚠️ 可行性中等",
            "low": "❌ 可行性较低"
        }.get(plan.get("feasibility", "medium"), "⚠️ 可行性待评估")
        
        answer = f"""## {plan['species']} {plan['gene_family']} 基因家族分析方案

### {feasibility_text}

**文献支持**: 找到 {plan.get('literature_support', 0)} 篇相关文献

### 📋 推荐分析流程

**基础分析（必做）**:
"""
        
        for stage in plan.get("base_stages", []):
            priority_emoji = "🔴" if stage["priority"] == "required" else "🟡"
            answer += f"\n{priority_emoji} **{stage['name']}** ({stage['estimated_time']})\n"
            answer += f"   - {stage['description']}\n"
        
        if plan.get("optional_stages"):
            answer += "\n**扩展分析（可选）**:\n"
            for stage in plan.get("optional_stages", []):
                answer += f"\n🟢 **{stage['name']}** ({stage['estimated_time']})\n"
                answer += f"   - {stage['description']}\n"
        
        answer += f"\n### ⏱️ 预估时间\n{plan.get('total_estimated_time', '未知')}\n"
        
        answer += "\n### 📦 需要的数据\n"
        for req in plan.get("requirements", []):
            answer += f"- {req}\n"
        
        answer += "\n### 💡 建议\n"
        for rec in plan.get("recommendations", []):
            answer += f"- {rec}\n"
        
        answer += "\n是否开始执行此方案？"
        
        return answer
    
    async def _enhance_report(self, query: str, analysis_results: Dict, state: AgentState) -> Dict:
        """
        功能4：报告增强
        
        1. 查询基因功能注释
        2. 查询通路信息
        3. 对比文献结果
        4. 生成结构化报告
        """
        print(f"[KnowledgeAgent] 增强报告: {query[:50]}...")
        
        # 1. 提取分析结果中的关键信息
        stage = analysis_results.get("stage", "unknown")
        metrics = analysis_results.get("metrics", {})
        
        # 2. 检索相关文献
        related_literature = await self._find_related_literature(analysis_results)
        
        # 3. 生成生物学解释
        biological_insights = await self._generate_biological_insights(
            stage, metrics, related_literature
        )
        
        # 4. 构建增强报告
        report = {
            "original_results": analysis_results,
            "literature_comparison": related_literature,
            "biological_insights": biological_insights,
            "recommendations": self._generate_recommendations(stage, metrics),
            "formatted_report": self._format_enhanced_report(
                analysis_results, biological_insights, related_literature
            )
        }
        
        return {
            "answer": report["formatted_report"],
            "report": report,
            "sources": related_literature.get("sources", []),
            "mode": "report_enhance"
        }
    
    async def _find_related_literature(self, analysis_results: Dict) -> Dict:
        """查找相关文献进行对比"""
        try:
            # 构建查询
            stage = analysis_results.get("stage", "")
            gene_count = analysis_results.get("metrics", {}).get("final_members", 0)
            
            query = f"{stage} gene family analysis {gene_count} members"
            data = await asyncio.to_thread(query_dual_kb_qa, query, top_k=3, use_llm=False)
            sources = data.get("sources", [])

            # 提取对比信息
            comparisons = []
            for s in sources:
                content = str(s.get("content") or s.get("evidence") or "")
                # 尝试提取基因数量
                import re
                numbers = re.findall(r'(\d+)\s*members', content.lower())
                if numbers:
                    comparisons.append({
                        "source": s.get("metadata", {}).get("source", "Unknown") if isinstance(s.get("metadata"), dict) else s.get("source", "Unknown"),
                        "reported_count": int(numbers[0]),
                        "context": content[:200]
                    })

            return {
                "has_comparison": len(comparisons) > 0,
                "comparisons": comparisons,
                "sources": sources
            }
        except Exception as e:
            print(f"[KnowledgeAgent] 文献对比失败: {e}")
        
        return {
            "has_comparison": False,
            "comparisons": [],
            "sources": []
        }
    
    async def _generate_biological_insights(self, stage: str, metrics: Dict, 
                                           literature: Dict) -> Dict:
        """生成生物学洞察"""
        insights = {
            "stage": stage,
            "key_findings": [],
            "biological_significance": "",
            "comparison_with_literature": ""
        }
        
        if stage == "identify":
            gene_count = metrics.get("final_members", 0)
            
            # 基因数量解读
            if gene_count < 10:
                insights["key_findings"].append("基因家族规模较小，可能是物种特异性扩张较少")
            elif gene_count > 50:
                insights["key_findings"].append("基因家族规模较大，可能存在显著扩张")
            else:
                insights["key_findings"].append("基因家族规模适中，符合典型基因家族特征")
            
            # 生物学意义
            insights["biological_significance"] = (
                f"该基因家族包含 {gene_count} 个成员，"
                "可能在生长发育、逆境响应等生物学过程中发挥重要作用。"
                "建议进一步分析其表达模式和功能分化。"
            )
            
            # 与文献对比
            if literature.get("has_comparison"):
                comparisons = literature.get("comparisons", [])
                if comparisons:
                    reported = comparisons[0].get("reported_count", 0)
                    diff = gene_count - reported
                    if abs(diff) < 5:
                        insights["comparison_with_literature"] = (
                            f"与文献报道的 {reported} 个成员相近，"
                            "结果具有可重复性。"
                        )
                    elif diff > 0:
                        insights["comparison_with_literature"] = (
                            f"比文献报道的 {reported} 个成员多 {diff} 个，"
                            "可能存在物种特异性扩张。"
                        )
                    else:
                        insights["comparison_with_literature"] = (
                            f"比文献报道的 {reported} 个成员少 {abs(diff)} 个，"
                            "可能存在基因丢失或鉴定参数差异。"
                        )
        
        elif stage == "phylogeny":
            insights["key_findings"].append("进化树构建完成，可进一步分析亚家族分化")
            insights["biological_significance"] = (
                "系统发育分析揭示了基因家族的进化关系，"
                "有助于理解基因复制事件和功能分化。"
            )
        
        return insights
    
    def _generate_recommendations(self, stage: str, metrics: Dict) -> List[str]:
        """生成后续建议"""
        recommendations = []
        
        if stage == "identify":
            gene_count = metrics.get("final_members", 0)
            
            if gene_count < 5:
                recommendations.append("基因数量较少，建议放宽鉴定参数重新分析")
            elif gene_count > 100:
                recommendations.append("基因数量较多，建议检查是否有假阳性")
            
            recommendations.append("建议进行表达分析，验证基因功能")
            recommendations.append("建议进行启动子分析，了解调控机制")
        
        elif stage == "phylogeny":
            recommendations.append("建议结合基因结构分析，验证亚家族分类")
            recommendations.append("建议进行Ka/Ks分析，研究选择压力")
        
        return recommendations
    
    def _format_enhanced_report(self, analysis_results: Dict, 
                                insights: Dict, literature: Dict) -> str:
        """格式化增强报告"""
        stage = analysis_results.get("stage", "unknown")
        metrics = analysis_results.get("metrics", {})
        
        report = f"""# 📊 {stage} 分析报告（增强版）

## 🔬 关键发现

"""
        
        for finding in insights.get("key_findings", []):
            report += f"- {finding}\n"
        
        report += f"\n## 🧬 生物学意义\n\n{insights.get('biological_significance', '暂无')}\n"
        
        if insights.get("comparison_with_literature"):
            report += f"\n## 📚 文献对比\n\n{insights['comparison_with_literature']}\n"
        
        report += "\n## 📈 详细指标\n\n"
        for key, value in metrics.items():
            report += f"- **{key}**: {value}\n"
        
        recommendations = self._generate_recommendations(stage, metrics)
        
        if recommendations:
            report += "\n## 💡 后续建议\n\n"
            for rec in recommendations:
                report += f"- {rec}\n"
        
        if literature.get("sources"):
            report += "\n## 📚 参考文献\n\n"
            for i, source in enumerate(literature.get("sources", [])[:3], 1):
                report += f"[{i}] {source.get('metadata', {}).get('source', 'Unknown')}\n"
        
        return report
    
    def get_conversation_history(self) -> List[Dict]:
        """获取对话历史"""
        return self.conversation_history
    
    async def index_documents(self, documents: List[Dict]) -> Dict:
        """
        索引文档到RAG
        
        Args:
            documents: 文档列表，每项包含content和metadata
        
        Returns:
            索引结果
        """
        if self.rag_retriever is None:
            return {"success": False, "message": "RAG检索器未初始化"}
        
        try:
            count = await self.rag_retriever.index_documents(documents)
            return {
                "success": True,
                "message": f"成功索引 {count} 个文档",
                "count": count
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"索引失败: {str(e)}",
                "error": str(e)
            }
    
    # ========== Learning模块方法 ==========
    
    async def _recommend_params_response(self, stage: str) -> Dict:
        """
        推荐参数响应（Learning模块功能）
        
        Args:
            stage: 分析阶段 (identify, phylogeny等)
        
        Returns:
            包含推荐参数和格式化回答的字典
        """
        # 调用Learning模块获取推荐
        recommendations = self.learning_module.recommend_params(stage)
        
        # 格式化回答
        confidence_emoji = {
            "high": "🟢",
            "medium": "🟡",
            "low": "🔴"
        }.get(recommendations.get("confidence", "low"), "⚪")
        
        answer = f"""## 📊 {stage} 阶段参数推荐

{confidence_emoji} **置信度**: {recommendations.get("confidence", "unknown").upper()}
**依据**: {recommendations.get("message", "")}
**历史记录数**: {recommendations.get("history_count", 0)}

### 推荐参数

"""
        
        params = recommendations.get("params", {})
        if params:
            for param, value in params.items():
                # 获取详细信息（如果有）
                details = recommendations.get("details", {}).get(param, {})
                if details:
                    answer += f"- **{param}**: `{value}`"
                    if "avg_time" in details:
                        answer += f" (平均耗时: {details['avg_time']:.1f}s)"
                    if "success_rate" in details:
                        answer += f" (成功率: {details['success_rate']*100:.1f}%)"
                    answer += "\n"
                else:
                    answer += f"- **{param}**: `{value}`\n"
        else:
            answer += "暂无推荐参数\n"
        
        return {
            "answer": answer,
            "recommendations": recommendations,
            "sources": [],
            "mode": "recommend_params"
        }
    
    async def _analyze_history_response(self) -> Dict:
        """
        分析历史响应（Learning模块功能）
        
        Returns:
            包含历史分析结果和格式化回答的字典
        """
        # 调用Learning模块获取分析
        analysis = self.learning_module.analyze_history()
        
        # 格式化回答
        answer = f"""## 📈 执行历史分析

### 总体统计

- **总执行次数**: {analysis.get("total_executions", 0)}
- **成功**: {analysis.get("successful", 0)}
- **失败**: {analysis.get("failed", 0)}
- **成功率**: {analysis.get("success_rate", 0)}%
- **最近7天**: {analysis.get("recent_7_days", 0)} 次执行

### 各阶段统计

"""
        
        stage_stats = analysis.get("stage_stats", {})
        if stage_stats:
            for stage, stats in stage_stats.items():
                success_rate = (stats["success"] / stats["total"] * 100) if stats["total"] > 0 else 0
                answer += f"- **{stage}**: {stats['success']}/{stats['total']} (成功率: {success_rate:.1f}%)\n"
        else:
            answer += "暂无阶段统计数据\n"
        
        answer += "\n### 平均执行时间\n\n"
        
        avg_times = analysis.get("avg_execution_time", {})
        if avg_times:
            for stage, avg_time in avg_times.items():
                answer += f"- **{stage}**: {avg_time:.1f} 秒\n"
        else:
            answer += "暂无执行时间数据\n"
        
        answer += f"\n*最后更新: {analysis.get('last_updated', '未知')}*"
        
        return {
            "answer": answer,
            "analysis": analysis,
            "sources": [],
            "mode": "analyze_history"
        }
    
    def record_execution(self, data: Dict) -> Dict:
        """
        记录执行历史（Learning模块代理方法）
        
        Args:
            data: 执行数据
        
        Returns:
            记录结果
        """
        return self.learning_module.record_execution(data)
    
    def get_learning_history_file(self) -> str:
        """获取Learning模块历史文件路径"""
        return str(self.learning_module.history_file)


# 便捷函数
def create_knowledge_agent(llm_manager: LLMManager, rag_retriever=None, 
                          learning_history_file: str = None) -> KnowledgeAgent:
    """创建KnowledgeAgent实例"""
    return KnowledgeAgent(llm_manager, rag_retriever, learning_history_file)
