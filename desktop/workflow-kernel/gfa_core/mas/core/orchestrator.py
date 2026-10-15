import os
import re
import asyncio
import json
from pathlib import Path
from langgraph.graph import END, StateGraph

from gfa_core.mas.config import config as app_config
from gfa_core.mas.core.state import GraphState
from gfa_core.mas.core.message_bus import MessageBus, AgentMessage, MessageType, get_message_bus
from gfa_core.mas.core.fast_intent import fast_intent_recognition, should_use_fast_intent
from gfa_core.mas.llm import LLMManager
from gfa_core.mas.llm.base import LLMMessage
from gfa_core.mas.memory import MemoryManager
from gfa_core.mas.memory.conversation import ConversationManager
from gfa_core.mas.rag import RAGRetriever
from gfa_core.mas.dual_kb_qa import query_dual_kb_qa
from gfa_core.mas.tools import ToolExecutor, GeneFamilyWorkflow
from gfa_core.mas.tools.stage_tools import StageToolRegistry, create_stage_registry
from gfa_core.mas.agents.diagnosis import DiagnosisAgent
from gfa_core.mas.agents.router import RouterAgent
from gfa_core.mas.agents.planning import PlanningAgent
from gfa_core.mas.agents.planning_v2 import PlanningAgentV2
from gfa_core.mas.agents.intent_v2 import IntentAgentV2
from gfa_core.mas.agents.execution import ExecutionAgent
from gfa_core.project_paths import PROJECT_ROOT, WORKSPACE_ROOT

WORKSPACE_DIR = WORKSPACE_ROOT


class Orchestrator:
    # 双语消息映射表：中文 → English（用于用户可见消息）
    _MSG = {
        # analysis_node — response 消息
        "✅ 所有分析阶段已完成！": "✅ All analysis stages completed!",
        "📋 自动规划剩余分析阶段": "📋 Auto-planned remaining analysis stages",
        "**已完成**": "**Completed**",
        "无": "None",
        "**待执行**": "**Pending**",
        "个阶段)": "stage(s))",
        "请确认是否按顺序执行以上分析阶段？": "Confirm to execute the above analysis stages in order?",
        "我将帮您执行RNA-seq": "I will help you execute RNA-seq",
        "分析。请确认参数后开始。": "analysis. Please confirm parameters to start.",
        "RNA-seq分析将通过前端异步执行。": "RNA-seq analysis will be executed asynchronously via frontend.",
        " 分析。PlanningAgentV2 已根据当前状态生成完整执行计划。": " analysis. PlanningAgentV2 has generated a complete execution plan based on current state.",
        "我将帮您执行": "I will help you execute",
        "分析。": "analysis.",
        "请提供基因家族蛋白序列文件（.fa/.fasta），用于MEME保守基序预测。": "Please provide gene family protein sequence file (.fa/.fasta) for MEME conserved motif prediction.",
        "请提供config.xlsx及基因组/GFF/CDS/PEP等原始文件，用于完整数据准备。": "Please provide config.xlsx and genome/GFF/CDS/PEP files for complete data preparation.",
        "请提供基因家族蛋白序列文件，用于Pfam/InterPro结构域注释。": "Please provide gene family protein sequence file for Pfam/InterPro domain annotation.",
        "请提供基因家族的GFF注释文件和基因组序列。": "Please provide gene family GFF annotation file and genome sequence.",
        "请提供基因家族成员的启动子序列（上游2000bp）或全基因组序列+GFF。": "Please provide gene family promoter sequences (upstream 2000bp) or whole genome + GFF.",
        "请提供物种基因组序列（.fasta）和GFF注释文件。": "Please provide species genome sequence (.fasta) and GFF annotation file.",
        "请提供多个物种的基因组序列和GFF注释文件，以及物种间的同源关系。": "Please provide genome sequences and GFF annotation files for multiple species, along with inter-species orthology.",
        "请提供染色体长度信息和基因位置信息。": "Please provide chromosome length and gene position information.",
        "请提供拟南芥同源基因映射文件（ortholog_pairs.txt，格式：拟南芥ID\t苦荞ID），用于STRING数据库互作预测。如未提供，请先运行同源基因识别分析。": "Please provide Arabidopsis ortholog mapping file (ortholog_pairs.txt, format: AtID\tTargetID) for STRING interaction prediction. If unavailable, run ortholog identification first.",
        "请提供目标物种和参考物种（如拟南芥At）的蛋白序列文件，用于同源基因识别。": "Please provide target and reference species (e.g., Arabidopsis At) protein FASTA for ortholog identification.",
        "请提供物种间同源基因对（ortholog pairs，格式：基因A\t基因B）或共线性分析结果。": "Please provide inter-species ortholog pairs (format: GeneA\tGeneB) or synteny analysis results.",
        "请提供RNA-seq表达矩阵（FPKM/TPM/Count）或原始测序数据。": "Please provide RNA-seq expression matrix (FPKM/TPM/Count) or raw sequencing data.",
        "请提供差异表达基因列表（DEGs）或目标基因集。": "Please provide differentially expressed gene list (DEGs) or target gene set.",
        "请提供基因家族蛋白序列文件，用于理化性质分析。": "Please provide gene family protein sequence file for physicochemical property analysis.",
        "请提供基因家族HMM文件和蛋白序列文件，将自动执行标准11阶段分析。": "Please provide gene family HMM file and protein FASTA; standard 11-stage analysis will be executed automatically.",
        "请提供RNA-seq原始测序数据（FASTQ格式）。": "Please provide RNA-seq raw sequencing data (FASTQ format).",
        "请提供质控后的clean reads（FASTQ格式）和参考基因组。": "Please provide QC-filtered clean reads (FASTQ format) and reference genome.",
        "请提供比对后的BAM文件或参考基因组注释GFF。": "Please provide aligned BAM file or reference genome annotation GFF.",
        "请提供featureCounts计数矩阵和样本分组表，用于VST、PCA、样本相关性和聚类。": "Please provide featureCounts count matrix and sample grouping table for VST, PCA, sample correlation and clustering.",
        "请提供多时相表达矩阵（如不同发育阶段/处理时间点）。": "Please provide multi-temporal expression matrix (e.g., different developmental stages/treatment time points).",
        "请提供VST/log2表达矩阵或count矩阵和样本性状表，用于共表达网络构建。": "Please provide VST/log2 expression matrix or count matrix and sample trait table for co-expression network construction.",
        "请提供差异表达结果和gene2go背景文件，或先执行背景注释。": "Please provide differential expression results and gene2go background file, or run background annotation first.",
        "请提供差异表达结果和gene2ko背景文件，或先执行背景注释。": "Please provide differential expression results and gene2ko background file, or run background annotation first.",
        "请提供差异表达排序统计量和gene2ko/KEGG背景，用于fgsea排序富集。": "Please provide differential expression ranking statistics and gene2ko/KEGG background for fgsea ranked enrichment.",
        "请提供差异表达结果和目标物种蛋白FASTA；若已有InterProScan TSV会优先复用。": "Please provide differential expression results and target species protein FASTA; existing InterProScan TSV will be reused if available.",
        "请提供表达矩阵（样本数≥6），用于共表达网络构建。": "Please provide expression matrix (samples ≥ 6) for co-expression network construction.",
        # knowledge_node — system prompt / response 消息
        "你是BotanicalBaseAgent基因家族分析系统的知识助手。基于提供的文献资料，生成专业、详细的回答。": "You are a knowledge assistant for the BotanicalBaseAgent gene family analysis system. Generate professional, detailed answers based on provided literature.",
        "📚 基于知识库的回答：": "📚 Knowledge base answer:",
        "**参考文献**：": "**References**:",
        "（来源: 本地LLM回答 + RAG辅助检索）": "(Source: local LLM answer + RAG-assisted retrieval)",
        "⚠️ (本地LLM回答，未使用RAG文献检索)": "⚠️ (local LLM answer, RAG retrieval not used)",
        "找到": "Found",
        "条相关文献，正在生成本地LLM回答...": " related references; generating local LLM answer...",
        "未找到相关文献，生成一般回答": "No related references found; generating general answer",
        "你是一个专业的基因家族分析意图识别器。": "You are a professional gene family analysis intent recognizer.",
        "你是一个基因家族分析专家。用户想要进行基因家族分析。": "You are a gene family analysis expert. The user wants to perform gene family analysis.",
    }

    @classmethod
    def _l(cls, zh_text: str, language: str = "zh") -> str:
        """Return language-appropriate text from the bilingual map."""
        if language == "zh":
            return zh_text
        en = cls._MSG.get(zh_text)
        if en is not None:
            return en
        # fallback: try partial match for template strings
        for zh_key, en_val in cls._MSG.items():
            if zh_key in zh_text:
                return zh_text.replace(zh_key, en_val)
        return zh_text
    def __init__(
        self,
        llm_manager: LLMManager,
        memory_manager: MemoryManager,
        papers_dir: str | Path | None = None,
    ):
        self.llm_manager = llm_manager
        self.memory_manager = memory_manager
        
        # 初始化ConversationManager（多会话管理）
        self.conversation_manager = ConversationManager()
        print(f"[Orchestrator] ConversationManager initialized")

        self.rag = RAGRetriever(llm_manager)
        self._rag_indexed = False

        if papers_dir:
            self.papers_dir = Path(papers_dir)
        else:
            self.papers_dir = Path(os.environ.get("PLANTAI_PAPERS_DIR", PROJECT_ROOT / "docs"))

        self.tool_executor = ToolExecutor()
        self.workflow = GeneFamilyWorkflow(self.tool_executor)
        
        # 初始化StageToolRegistry用于执行分析阶段
        self.stage_registry = create_stage_registry()
        
        # 初始化消息总线
        self.message_bus = get_message_bus()
        
        # 初始化DiagnosisAgent用于错误诊断
        self.diagnosis_agent = DiagnosisAgent(llm_manager)
        
        # 初始化RouterAgent用于意图路由
        self.router_agent = RouterAgent(llm_manager)
        print("[Orchestrator] RouterAgent initialized")
        
        # 初始化PlanningAgent用于任务规划
        self.planning_agent = PlanningAgent(llm_manager)
        print("[Orchestrator] PlanningAgent initialized")
        
        # 初始化V2 Agent（LLM驱动）
        self.intent_agent_v2 = IntentAgentV2(llm_manager)
        self.planning_agent_v2 = PlanningAgentV2(llm_manager)
        print("[Orchestrator] V2 Agents initialized")
        
        # 初始化ExecutionAgent用于任务执行
        self.execution_agent = ExecutionAgent(llm_manager, self.stage_registry)
        print("[Orchestrator] ExecutionAgent initialized")

        self.graph = self._build_graph()
        
        # 初始化记忆存储目录
        self.memory_storage_dir = PROJECT_ROOT / "memory_storage"
        self.memory_storage_dir.mkdir(parents=True, exist_ok=True)
        print(f"[Orchestrator] Memory storage: {self.memory_storage_dir}")

    async def ensure_rag_indexed(self) -> None:
        if not self._rag_indexed and self.papers_dir.exists():
            count = await self.rag.aindex_pdf_directory(self.papers_dir)
            self._rag_indexed = True
            print(f"Indexed {count} chunks from {self.papers_dir}")

    def _get_input_dir(self, session_id: str, stage_name: str = "") -> tuple[Path, str]:
        """
        获取输入目录，支持session_id不是job_id的情况
        
        Returns:
            tuple: (input_dir, actual_session_id)
        """
        prefix = f"[{stage_name}]" if stage_name else "[INPUT]"
        input_dir = PROJECT_ROOT / "input" / session_id
        print(f"{prefix} Initial input_dir: {input_dir}, exists: {input_dir.exists()}")
        
        # 如果session_id对应的目录不存在，或者不是job_开头，查找包含配置文件的job目录
        if not input_dir.exists() or not session_id.startswith("job_"):
            job_dirs = sorted([d for d in (PROJECT_ROOT / "input").iterdir() if d.is_dir() and d.name.startswith("job_")], reverse=True)
            print(f"{prefix} Found {len(job_dirs)} job directories")
            
            for job_dir in job_dirs:
                # 优先查找包含samples.yaml或group_sample.yaml的目录
                if (job_dir / "samples.yaml").exists() or (job_dir / "group_sample.yaml").exists():
                    input_dir = job_dir
                    session_id = job_dir.name
                    print(f"{prefix} Selected job dir with config: {job_dir.name}")
                    return input_dir, session_id
            
            # 如果没有找到包含配置文件的目录，使用最新的job目录
            if job_dirs:
                input_dir = job_dirs[0]
                session_id = job_dirs[0].name
                print(f"{prefix} Selected latest job dir: {job_dirs[0].name}")
                return input_dir, session_id
        
        return input_dir, session_id

    def _build_graph(self) -> StateGraph:
        builder = StateGraph(GraphState)
        builder.add_node("router", self._router_node)
        builder.add_node("knowledge", self._knowledge_node)
        builder.add_node("analysis", self._analysis_node)
        builder.set_entry_point("router")

        builder.add_conditional_edges(
            "router",
            self._route_query,
            {
                "knowledge": "knowledge",
                "analysis": "analysis",
            },
        )

        builder.add_edge("knowledge", END)
        builder.add_edge("analysis", END)

        return builder.compile()

    def _route_query(self, state: GraphState) -> str:
        query = state["query"].lower()
        mode = state.get("mode", "faq")
        
        print(f"[_route_query] Query: {query[:50]}..., Mode from state: {mode}")
        
        # 优先使用router节点设置的mode
        print(f"[_route_query] Checking mode: {mode}", flush=True)
        if mode == "analysis":
            print(f"[_route_query] Routing to: analysis (from state)", flush=True)
            return "analysis"
        if mode == "faq":
            print(f"[_route_query] Routing to: knowledge (from state)")
            return "knowledge"
        if mode == "interpretation":
            print(f"[_route_query] Routing to: knowledge (INTERPRETATION mode)")
            return "knowledge"
        
        # 查询已有结果关键词（最高优先级，不触发执行分析）
        # 用户想看"亚家族分类明细"或"成员列表"等，应该走knowledge路由
        query_keywords = [
            "明细", "详情", "分类", "列表", "查看", "显示",
            "亚家族", "成员", "结果查看", "分类明细"
        ]
        for keyword in query_keywords:
            if keyword in query:
                # 确认没有明确的执行意图
                has_exec_intent = any(kw in query for kw in ["执行", "运行", "鉴定", "构建", "帮我"])
                if not has_exec_intent:
                    print(f"[_route_query] Routing to: knowledge (QUERY keyword: {keyword})")
                    return "knowledge"
        
        # 额外的FAQ关键词检测（作为后备）
        faq_keywords = [
            "什么是", "是什么", "有哪些", "作用", "功能", 
            "结构", "特点", "定义", "介绍", "说明",
            "为什么", "如何理解", "什么意思", "概念"
        ]
        
        for keyword in faq_keywords:
            if keyword in query:
                # 检查是否同时包含分析关键词
                has_analysis_intent = any(kw in query for kw in ["帮我", "分析", "鉴定", "构建", "计算", "运行", "执行"])
                if not has_analysis_intent:
                    print(f"[_route_query] Routing to: knowledge (FAQ keyword: {keyword})")
                    return "knowledge"

        analysis_keywords = [
            "鉴定",
            "分析",
            "鉴定基因",
            "基因鉴定",
            "帮我",
            "执行",
            "运行",
            "identify",
            "analyze",
            "analysis",
            "run",
            "execute",
            "找出",
            "发现",
            "寻找",
            "搜索基因",
            "motif",
            "phylogeny",
            "进化树",
            "结构域",
            "domain",
            "启动子",
            "promoter",
            "共线性",
            "synteny",
            "种间",
            "interspecies",
            "核型",
            "karyotype",
            "ppi",
            "蛋白互作",
            "protein-protein",
            "kaks",
            "kaks",
            "ka/ks",
            "选择压力",
            "表达",
            "expression",
            "rna-seq",
            "rnaseq",
            "rna seq",
            "质控",
            "qc",
            "fastqc",
            "比对",
            "mapping",
            "定量",
            "count",
            "fpkm",
            "tpm",
            "差异表达",
            "deg",
            "差异基因",
            "趋势",
            "trend",
            "wgcna",
            "共表达网络",
            "富集",
            "enrichment",
            "go",
            "kegg",
            "pathway",
            "pep",
            "蛋白理化",
            "蛋白特征",
            "氨基酸",
            "分子量",
            "等电点",
            "combo",
            "组合",
            "批量",
            "系统发育",
            "phylogenetic",
            "ml树",
            "nj树",
            "贝叶斯",
            "基序",
            "meme",
            "保守序列",
            "pattern",
            "pfam",
            "interpro",
            "cdd",
            "功能域",
            "外显子",
            "内含子",
            "exon",
            "intron",
            "utr",
            "cds",
            "启动子",
            "promoter",
            "顺式作用",
            "顺式调控",
            "cis-acting",
            "tss",
            "共线性",
            "synteny",
            "segmental",
            "tandem",
            "mcscanx",
            "跨物种",
            "cross-species",
            "染色体",
            "chromosome",
            "核型图",
            "亚细胞定位",
            "信号肽",
            "跨膜",
        ]

        for keyword in analysis_keywords:
            if keyword in query:
                return "analysis"

        return "knowledge"

    async def _router_node(self, state: GraphState) -> dict:
        query = state["query"]
        language = state.get("language", "zh")
        
        # 使用 RouterAgent 进行意图路由
        print(f"[RouterNode] Using RouterAgent to classify query: {query[:50]}...")
        from gfa_core.mas.agents.base import AgentState, AgentMessage as BaseAgentMessage
        
        # 创建 RouterAgent 期望的消息格式
        message = BaseAgentMessage(
            sender="user",
            receiver="router",
            message_type="request",
            payload={"query": query}
        )
        
        # 创建 RouterAgent 期望的状态格式
        from gfa_core.mas.agents.base import QueryMode
        router_state = AgentState(
            session_id=state.get("session_id", "default"),
            results={},
            mode=QueryMode.FAQ  # 默认模式
        )
        
        try:
            router_state = await self.router_agent.execute(router_state, message)
            mode = router_state.mode.value if hasattr(router_state.mode, 'value') else str(router_state.mode)
            print(f"[RouterNode] RouterAgent classified as: {mode}")
        except Exception as e:
            print(f"[RouterNode] RouterAgent failed: {e}, falling back to default")
            # Fallback to simple keyword matching
            if any(kw in query for kw in ["帮我", "分析", "鉴定", "构建", "计算", "运行", "执行"]):
                mode = "analysis"
            else:
                mode = "faq"

        # 更新 state 中的 mode，确保 _route_query 能获取到
        state["mode"] = mode
        # 同时写入文件日志
        with open('/tmp/bbav20311.log', 'a') as f:
            f.write(f"[RouterNode] Set state mode to: {mode}\n")
        print(f"[RouterNode] Set state mode to: {mode}", flush=True)
        
        # 保存到记忆系统
        try:
            await self.memory_manager.add_message_to_session(
                state.get("session_id", "default"),
                "user",
                query
            )
            await self.memory_manager.add_message_to_session(
                state.get("session_id", "default"),
                "router",
                f"Classified as {mode}"
            )
        except Exception as e:
            print(f"[Memory] Failed to save: {e}")
        
        return {
            "current_agent": "router",
            "status": "running",
            "mode": mode,
            "job_id": state.get("job_id"),  # 保留 job_id
            "session_id": state.get("session_id"),  # 保留 session_id
        }

    async def _knowledge_node(self, state: GraphState) -> dict:
        query = state["query"]
        language = state.get("language", "zh")

        try:
            # 优先调用当前 Dual-KB QA 服务
            search_query = query

            # 查询关键词中→英翻译（提高检索命中率）
            import re
            gf_match = re.search(r'([A-Z]{2,})', query.upper())
            gf_name = gf_match.group(1) if gf_match else "SPL"

            # 根据查询类型优化英文关键词
            if '表达' in query or 'expression' in query.lower():
                search_query = f"{gf_name} expression tissue flower development"
            elif '基因家族' in query or '功能' in query:
                search_query = f"{gf_name} SQUAMOSA PROMOTER BINDING PROTEIN function"
            else:
                # 保留原查询，同时添加基因家族名称
                if gf_match:
                    search_query = f"{query} {gf_name}"

            rag_result = await asyncio.to_thread(query_dual_kb_qa, search_query, top_k=5, use_llm=True)
            rag_response = rag_result.get('answer', '')
            sources = rag_result.get('sources', [])

            # 如果 RAG 返回的是简单统计信息，使用本地 LLM 生成完整回答
            if '找到' in rag_response and '条相关文献' in rag_response and len(rag_response) < 200:
                print(f"[KnowledgeNode] RAG returned simple stats, generating full answer with LLM")
                # 构造上下文
                context = "\\n\\n".join([
                    f"【文献{i+1}】{s.get('content', '')[:500]}"
                    for i, s in enumerate(sources[:3])
                ])

                # 使用本地 LLM 生成完整回答
                prompt = f"""你是一个基因家族分析专家。请根据以下检索到的文献内容，回答用户的问题。

用户问题: {query}

文献内容:
{context}

请生成一个专业、详细的回答，包括：
1. **核心概念解释** - 清晰解释相关概念
2. **主要研究发现** - 总结文献中的关键发现
3. **生物学意义** - 说明其在生物学/育种中的应用价值

格式要求：
- 使用 Markdown 语法（加粗、列表等）
- 结构清晰，段落分明
- 关键信息用 **加粗** 标注"""

                messages = [
                    LLMMessage(role="system", content="你是BotanicalBaseAgent基因家族分析系统的知识助手。基于提供的文献资料，生成专业、详细的回答。"),
                    LLMMessage(role="user", content=prompt)
                ]
                llm_response = await self.llm_manager.acall(messages)
                rag_response = llm_response.content

            # 格式化回答，添加文献来源信息
            if sources:
                source_info = "\\n\\n📚 **参考来源**:\\n"
                for i, src in enumerate(sources[:3], 1):
                    metadata = src.get('metadata', {})
                    source_file = metadata.get('source', '').split('/')[-1] if metadata.get('source') else (src.get('paper_id') or src.get('id') or '未知')
                    source_info += f"{i}. {source_file}\\n"
                rag_response += source_info

            return {
                "current_agent": "knowledge",
                "status": "completed",
                "mode": "faq",
                "final_output": {
                    "response": rag_response,
                    "rag_context": f"找到 {len(sources)} 条相关文献",
                    "sources": sources
                },
            }

        except Exception as e:
            print(f"[KnowledgeNode] RAG service error: {e}, using local fallback")
            # 降级：使用本地 RAG 或 LLM
            try:
                await self.ensure_rag_indexed()
                context = ""
                if self.rag.count_chunks() > 0:
                    results = await self.rag.search(query, top_k=3, threshold=0.2)
                    if results:
                        context = self.rag.get_context(results, max_length=2000)

                if context:
                    prompt = f"""你是一个基因家族分析专家。基于以下参考资料回答用户问题。

参考资料:
{context}

用户问题: {query}

请提供简洁、专业的回答。不要提及任何系统内部信息、代码、CLI命令或文件路径。只回答用户的问题本身。"""
                else:
                    prompt = f"""你是一个基因家族分析专家。请回答用户的问题。

用户问题: {query}

请提供简洁、专业的回答。不要提及任何系统内部信息、代码、CLI命令或文件路径。"""

                messages = [
                    LLMMessage(role="system", content="你是BotanicalBaseAgent基因家族分析系统的知识助手。只回答用户问题，不暴露任何系统内部信息。"),
                    LLMMessage(role="user", content=prompt)
                ]
                response = await self.llm_manager.acall(messages)

                return {
                    "current_agent": "knowledge",
                    "status": "completed",
                    "mode": "faq",
                    "final_output": {
                        "response": response.content + "\\n\\n⚠️ (本地LLM回答，未使用RAG文献检索)",
                        "rag_context": context[:500] if context else None,
                    },
                }
            except Exception as e2:
                return {
                    "current_agent": "knowledge",
                    "status": "failed",
                    "mode": "faq",
                    "errors": [str(e), str(e2)],
                    "final_output": {"error": f"RAG服务错误: {e}, 本地降级也失败: {e2}"},
                }

    async def _handle_auto_continue(self, state: GraphState, gene_family: str, species: str) -> dict:
        """处理自动继续执行剩余分析"""
        # 尝试从 state 获取 job_id，如果没有则使用 session_id
        job_id = state.get("job_id") or state.get("session_id", "default")
        import sys
        print(f"[AUTO_CONTINUE] job_id={job_id}, gene_family={gene_family}, species={species}", flush=True)
        print(f"[AUTO_CONTINUE] Full state keys: {list(state.keys())}", flush=True)
        print(f"[AUTO_CONTINUE] state.get('job_id'): {state.get('job_id')}", flush=True)
        print(f"[AUTO_CONTINUE] state.get('session_id'): {state.get('session_id')}", flush=True)
        sys.stdout.flush()
        
        # 查询已完成的 stages
        completed_stages = []
        workspace_dir = WORKSPACE_DIR / job_id
        final_dir = workspace_dir / "final"
        print(f"[AUTO_CONTINUE] Checking final_dir: {final_dir}, exists={final_dir.exists()}")
        
        if final_dir.exists():
            manifest_files = list(final_dir.glob("*.manifest.json"))
            print(f"[AUTO_CONTINUE] Found {len(manifest_files)} manifest files")
            for manifest_file in manifest_files:
                print(f"[AUTO_CONTINUE] Reading {manifest_file.name}")
                try:
                    import json
                    with open(manifest_file, 'r') as f:
                        manifest = json.load(f)
                    print(f"[AUTO_CONTINUE] Manifest: stage={manifest.get('stage')}, status={manifest.get('status')}")
                    if manifest.get("status") == "success":
                        completed_stages.append(manifest.get("stage"))
                except Exception as e:
                    print(f"[AUTO_CONTINUE] Error reading manifest: {e}")
        else:
            print(f"[AUTO_CONTINUE] final_dir does not exist: {final_dir}")
        
        # 定义标准分析流程 (包含所有11个核心阶段 + 扩展阶段 + RNA-seq子阶段)
        # 注意：interspecies_synteny 必须在 kaks 之前，因为 kaks 需要其产生的同源基因对
        standard_flow = [
            "identify",        # 基因识别
            "phylogeny",       # 系统发育
            "motif",           # 保守基序
            "domain",          # 结构域
            "structure",       # 基因结构
            "promoter",        # 启动子分析
            "synteny",         # 共线性
            "karyotype",       # 染色体定位
            "expression",      # 表达分析
            "ppi",             # 蛋白互作
            "pep",             # 多肽分析
            "interspecies_synteny",  # 物种间比较（必须在kaks之前）
            "kaks",            # 进化分析（依赖interspecies_synteny的同源基因对）
            "rnaseq_qc",       # RNA-seq: 质控
            "rnaseq_align",    # RNA-seq: 比对
            "rnaseq_quant",    # RNA-seq: 定量
            "rnaseq_sample_qc", # RNA-seq: 样本质量评估
            "rnaseq_wgcna",     # RNA-seq: 加权共表达
            "rnaseq_de",       # RNA-seq: 差异表达
            "rnaseq_go_enrich", # RNA-seq: GO富集
            "rnaseq_kegg_enrich", # RNA-seq: KEGG富集
            "rnaseq_fgsea",     # RNA-seq: fgsea排序富集
            "rnaseq_candidate_family" # RNA-seq: DEG候选家族筛选
        ]
        
        # 找出剩余 stages
        remaining_stages = [s for s in standard_flow if s not in completed_stages]
        
        if not remaining_stages:
            return {
                "current_agent": "analysis",
                "status": "completed",
                "mode": "analysis",
                "final_output": {
                    "response": self._l("✅ 所有分析阶段已完成！", language) + "\n\n" + self._l("**已完成**", language) + ": " + ", ".join(completed_stages),
                    "analysis_plan": {
                        "gene_family": gene_family,
                        "species": species,
                        "completed_stages": completed_stages,
                        "remaining_stages": []
                    }
                }
            }
        
        # 返回剩余 stages 让用户确认
        stages_text = '\n'.join([f"  {i+1}. {s}" for i, s in enumerate(remaining_stages)])
        return {
            "current_agent": "analysis",
            "status": "completed",
            "mode": "analysis",
            "final_output": {
                "response": f"{self._l('📋 自动规划剩余分析阶段', language)}\n\n{self._l('**已完成**', language)}: {', '.join(completed_stages) if completed_stages else self._l('无', language)}\n\n{self._l('**待执行**', language)} ({len(remaining_stages)}{self._l('个阶段)', language)}:\n{stages_text}\n\n{self._l('请确认是否按顺序执行以上分析阶段？', language)}",
                "analysis_plan": {
                    "gene_family": gene_family,
                    "species": species,
                    "analysis_type": "auto_continue",
                    "completed_stages": completed_stages,
                    "remaining_stages": remaining_stages,
                    "params": {
                        "prefix": f"{species}.{gene_family}",
                        "cpu": 8
                    }
                },
                "intent": {
                    "stage": "auto_continue",
                    "taskType": "batch_analysis",
                    "params": {
                        "stages": remaining_stages,
                        "prefix": f"{species}.{gene_family}",
                        "cpu": 8
                    },
                    "confidence": 0.9
                }
            }
        }

    async def _analysis_node(self, state: GraphState) -> dict:
        query = state["query"]
        files = state.get("files", [])
        language = state.get("language", "zh")
        print(f"[_analysis_node] Entered with query: {query[:50]}...", flush=True)

        try:
            # 第一步：使用LLM进行意图识别，确定分析类型
            from gfa_core.mas.llm.base import LLMMessage
            
            # 首先尝试快速意图识别（无需LLM调用）
            fast_intent = fast_intent_recognition(query)
            print(f"[_analysis_node] fast_intent: {fast_intent}", flush=True)
            
            if fast_intent and fast_intent["confidence"] > 0.7:
                # 使用快速识别的结果
                analysis_type = fast_intent["stage"]
                gene_family = fast_intent["gene_family"]
                species = fast_intent["species"]
                print(f"[FAST INTENT] Using keyword matching: {analysis_type}, {gene_family}, {species}")
                
                # 特殊处理：自动继续执行剩余分析
                if analysis_type == "auto_continue":
                    return await self._handle_auto_continue(state, gene_family, species)
            else:
                # 降级到LLM意图识别
                print(f"[SLOW INTENT] Falling back to LLM intent recognition")
                intent_prompt = """你是一个基因家族分析系统的意图识别器。请分析用户的请求，提取以下信息：

1. 分析类型（必须是以下之一）：
   - identify: 基因家族鉴定（鉴定、识别、找出、identify）
   - genome_prepare: 数据准备总流程（数据准备、基因组准备、genome_prepare）
   - identify: 基因家族鉴定（鉴定、识别、找出、identify）
   - phylogeny: 系统发育分析（进化树、系统发育树、phylogeny、phylogenetic tree、建树、ML树、NJ树、贝叶斯树）
   - motif: Motif分析（motif、基序、保守序列、MEME、序列模式、pattern）
   - domain: 结构域分析（结构域、domain、功能域、Pfam、InterPro、CDD）
   - structure: 基因结构分析（基因结构、structure、外显子内含子、exon-intron、UTR、CDS结构）
   - promoter: 启动子/顺式作用元件分析（启动子、promoter、顺式作用元件、cis-acting element、顺式调控、TSS）
   - synteny: 种内共线性分析（共线性、synteny、segmental duplication、串联重复、tandem、MCScanX，但没有"种间"）
   - interspecies_synteny: 种间共线性分析（种间共线性、interspecies、跨物种、cross-species、多物种共线性）
   - karyotype: 染色体核型分析（核型、karyotype、染色体、核型图、染色体数目、染色体形态）
   - ppi: 蛋白互作分析（PPI、蛋白互作、protein-protein interaction、互作网络、STRING、互作预测）
   - kaks: Ka/Ks选择压力分析（kaks、Ka/Ks、选择压力、dn/ds、进化选择、正选择、负选择）
   - rna_seq_qc: RNA-seq质控（RNA-seq质控、质控分析、QC、FastQC、数据质控、测序质量评估）
   - rna_seq_mapping: RNA-seq比对（RNA-seq比对、reads比对、mapping、序列比对、Hisat2、STAR）
   - rna_seq_quantification: RNA-seq定量（RNA-seq定量、表达定量、reads计数、count、FPKM、TPM、featureCounts）
   - rnaseq_sample_qc: RNA-seq样本质量评估（样本质量、样本检测、PCA、样本相关性、样本聚类）
   - expression: 差异表达分析（expression、表达、差异表达、DEG、差异基因、diff expression、DESeq2、edgeR）
   - trend: 表达趋势分析（trend、趋势分析、表达趋势、时间序列、STEM、表达模式）
   - rnaseq_wgcna: WGCNA分析（WGCNA、加权基因共表达网络、共表达网络、模块分析、hub基因、网络分析）
   - rnaseq_go_enrich: GO富集分析（GO、GO富集、GO term、gene2go）
   - rnaseq_kegg_enrich: KEGG富集分析（KEGG、KEGG富集、pathway、gene2ko）
   - rnaseq_fgsea: fgsea排序富集分析（fgsea、GSEA、排序富集、基因集富集）
   - rnaseq_candidate_family: 候选家族筛选（候选基因家族、TF/Pfam注释、差异基因家族排名）
   - enrichment: 泛富集分析（用户未指定GO或KEGG时，规划为GO与KEGG两个子阶段）
   - pep: 蛋白理化特征分析（PEP、蛋白理化、蛋白理化性质、氨基酸组成、分子量、等电点、pI、疏水性、ProtParam）
   - combo: 组合分析（Combo、组合分析、批量分析、多阶段分析、一键分析、全流程分析）
   - unknown: 无法确定

2. 基因家族名称（如SPL、NAC、WRKY等，大写）

3. 物种（如Ft、At、Os等，大写。注意：PPI分析通常需要"拟南芥"或"At"作为参考物种）

用户请求: {query}

请以JSON格式回复：
{{
    "analysis_type": "分析类型",
    "gene_family": "基因家族",
    "species": "物种"
}}""".format(query=query)

                intent_messages = [
                    LLMMessage(role="system", content="你是一个专业的基因家族分析意图识别器。"),
                    LLMMessage(role="user", content=intent_prompt)
                ]
                
                intent_response = await self.llm_manager.acall(intent_messages, temperature=0.1)
                
                # 解析意图响应
                import json
                import re as re_module
                
                try:
                    # 尝试从响应中提取JSON
                    json_match = re_module.search(r'\{[^}]+\}', intent_response.content)
                    if json_match:
                        intent_data = json.loads(json_match.group())
                        analysis_type = intent_data.get("analysis_type", "identify")
                        gene_family = intent_data.get("gene_family", "")
                        species = intent_data.get("species", "")
                    else:
                        # 降级处理
                        analysis_type = "identify"
                        gene_family = ""
                        species = ""
                except Exception as e:
                    print(f"[ERROR] Failed to parse intent: {e}")
                    analysis_type = "identify"
                    gene_family = "SPL"
                    species = "Ft"
            
            print(f"[DEBUG] Intent analysis: type={analysis_type}, family={gene_family}, species={species}")
            
            # 分析类型名称映射（LLM返回名称 -> pipeline stage名称）
            stage_name_mapping = {
                "interspecies": "interspecies_synteny",
                "rnaseq_qc": "rnaseq_qc",
                "rnaseq_align": "rnaseq_align",
                "rnaseq_quant": "rnaseq_quant",
                "rna_seq_qc": "rnaseq_qc",
                "rna_seq_mapping": "rnaseq_align",
                "rna_seq_quantification": "rnaseq_quant",
                "rnaseq_sample_qc": "rnaseq_sample_qc",
                "rnaseq_wgcna": "rnaseq_wgcna",
                "rnaseq_de": "rnaseq_de",
                "wgcna": "rnaseq_wgcna",
                "rnaseq_go_enrich": "rnaseq_go_enrich",
                "rnaseq_kegg_enrich": "rnaseq_kegg_enrich",
                "rnaseq_fgsea": "rnaseq_fgsea",
                "rnaseq_candidate_family": "rnaseq_candidate_family",
                "rnaseq_enrich": "rnaseq_go_enrich",
                "prepare": "genome_prepare",
            }
            # 转换分析类型名称以匹配pipeline
            if analysis_type in stage_name_mapping:
                analysis_type = stage_name_mapping[analysis_type]
            
            # 如果分析类型是各类分析阶段，但没有相应文件，返回指导信息
            supported_analysis_types = [
                "genome_prepare",
                "identify", "phylogeny", "motif", "domain", "structure", "promoter", "synteny", "interspecies_synteny", 
                "karyotype", "ppi", "kaks", "expression", "enrichment", "pep", "combo",
                "rna_seq_qc", "rna_seq_mapping", "rna_seq_quantification", "rnaseq_sample_qc",
                "rnaseq_wgcna", "rnaseq_go_enrich", "rnaseq_kegg_enrich", "rnaseq_fgsea",
                "rnaseq_candidate_family", "trend", "wgcna"
            ]
            if analysis_type in supported_analysis_types:
                # 根据分析类型生成特定的指导信息
                guidance_map = {
                    "motif": "请提供基因家族蛋白序列文件（.fa/.fasta），用于MEME保守基序预测。",
                    "genome_prepare": "请提供config.xlsx及基因组/GFF/CDS/PEP等原始文件，用于完整数据准备。",
                    "domain": "请提供基因家族蛋白序列文件，用于Pfam/InterPro结构域注释。",
                    "structure": "请提供基因家族的GFF注释文件和基因组序列。",
                    "promoter": "请提供基因家族成员的启动子序列（上游2000bp）或全基因组序列+GFF。",
                    "synteny": "请提供物种基因组序列（.fasta）和GFF注释文件。",
                    "interspecies_synteny": "请提供多个物种的基因组序列和GFF注释文件，以及物种间的同源关系。",
                    "karyotype": "请提供染色体长度信息和基因位置信息。",
                    "ppi": "请提供拟南芥同源基因映射文件（ortholog_pairs.txt，格式：拟南芥ID\t苦荞ID），用于STRING数据库互作预测。如未提供，请先运行同源基因识别分析。",
                    "ortholog": "请提供目标物种和参考物种（如拟南芥At）的蛋白序列文件，用于同源基因识别。",
                    "kaks": "请提供物种间同源基因对（ortholog pairs，格式：基因A\t基因B）或共线性分析结果。",
                    "expression": "请提供RNA-seq表达矩阵（FPKM/TPM/Count）或原始测序数据。",
                    "enrichment": "请提供差异表达基因列表（DEGs）或目标基因集。",
                    "pep": "请提供基因家族蛋白序列文件，用于理化性质分析。",
                    "combo": "请提供基因家族HMM文件和蛋白序列文件，将自动执行标准11阶段分析。",
                    "rna_seq_qc": "请提供RNA-seq原始测序数据（FASTQ格式）。",
                    "rna_seq_mapping": "请提供质控后的clean reads（FASTQ格式）和参考基因组。",
                    "rna_seq_quantification": "请提供比对后的BAM文件或参考基因组注释GFF。",
                    "rnaseq_sample_qc": "请提供featureCounts计数矩阵和样本分组表，用于VST、PCA、样本相关性和聚类。",
                    "trend": "请提供多时相表达矩阵（如不同发育阶段/处理时间点）。",
                    "rnaseq_wgcna": "请提供VST/log2表达矩阵或count矩阵和样本性状表，用于共表达网络构建。",
                    "rnaseq_go_enrich": "请提供差异表达结果和gene2go背景文件，或先执行背景注释。",
                    "rnaseq_kegg_enrich": "请提供差异表达结果和gene2ko背景文件，或先执行背景注释。",
                    "rnaseq_fgsea": "请提供差异表达排序统计量和gene2ko/KEGG背景，用于fgsea排序富集。",
                    "rnaseq_candidate_family": "请提供差异表达结果和目标物种蛋白FASTA；若已有InterProScan TSV会优先复用。",
                    "wgcna": "请提供表达矩阵（样本数≥6），用于共表达网络构建。",
                }
                
                # PPI特殊处理：默认使用拟南芥(At)作为参考
                if analysis_type == "ppi" and (not species or species == "Ft"):
                    species = "At"  # PPI分析默认使用拟南芥作为参考物种
                
                # RNA-seq各阶段分析：返回意图，让用户确认后通过前端执行
                # 注意：RNA-seq分析改为异步模式，通过前端/api/execute执行
                rna_seq_stages = ["rna_seq_qc", "rna_seq_mapping", "rna_seq_quantification", "rnaseq_sample_qc", "rnaseq_wgcna", "rnaseq_go_enrich", "rnaseq_kegg_enrich", "rnaseq_fgsea", "rnaseq_candidate_family"]
                if analysis_type in rna_seq_stages:
                    # 映射到前端stage名称
                    stage_name_map = {
                        "rna_seq_qc": "rnaseq_qc",
                        "rna_seq_mapping": "rnaseq_align",
                        "rna_seq_quantification": "rnaseq_quant",
                        "rnaseq_sample_qc": "rnaseq_sample_qc",
                        "rnaseq_wgcna": "rnaseq_wgcna",
                        "rnaseq_go_enrich": "rnaseq_go_enrich",
                        "rnaseq_kegg_enrich": "rnaseq_kegg_enrich",
                        "rnaseq_fgsea": "rnaseq_fgsea",
                        "rnaseq_candidate_family": "rnaseq_candidate_family",
                    }
                    frontend_stage = stage_name_map.get(analysis_type, analysis_type)
                    
                    return {
                        "current_agent": "analysis",
                        "status": "completed",
                        "mode": "analysis",
                        "final_output": {
                            "response": self._l("我将帮您执行RNA-seq", language) + " " + analysis_type + self._l("分析。请确认参数后开始。", language),
                            "analysis_plan": {
                                "gene_family": gene_family,
                                "species": species,
                                "analysis_type": analysis_type,
                                "stage": frontend_stage,
                                "params": {
                                    "prefix": f"{species}.{gene_family}",
                                    "cpu": 8
                                },
                                "guidance": self._l("RNA-seq分析将通过前端异步执行。", language)
                            }
                        }
                    }
                
                # RNA-seq 差异表达和富集分析：使用 PlanningAgentV2 生成完整计划
                # 让 PlanningAgentV2 检查 final 目录中的 manifest，自动确定需要执行的阶段
                if analysis_type in ["expression", "enrichment"]:
                    # 构建 RNA-seq 阶段列表
                    if analysis_type == "expression":
                        target_stages = ["rnaseq_qc", "rnaseq_align", "rnaseq_quant", "rnaseq_sample_qc", "rnaseq_de"]
                    else:  # enrichment
                        target_stages = ["rnaseq_qc", "rnaseq_align", "rnaseq_quant", "rnaseq_sample_qc", "rnaseq_de", "rnaseq_go_enrich", "rnaseq_kegg_enrich"]
                    
                    # 调用 plan 方法生成完整计划
                    from gfa_core.mas.core.state import GraphState
                    plan_state: GraphState = {
                        "query": f"执行 {gene_family} 基因家族的 RNA-seq {analysis_type} 分析",
                        "session_id": state.get("session_id", ""),
                        "job_id": state.get("job_id", ""),
                        "files": state.get("files", []),
                        "current_agent": "planning",
                        "status": "running",
                        "errors": [],
                        "final_output": {},
                        "history": state.get("history", []),
                        "results": {
                            "intent": {
                                "stage": target_stages[-1],  # 最后一个阶段作为主意图
                                "gene_family": gene_family,
                                "species": species,
                                "prefix": f"{species}.{gene_family}",
                                "description": f"RNA-seq {analysis_type} analysis",
                                "target_stages": target_stages  # 传递完整阶段列表
                            }
                        }
                    }
                    
                    # 调用 plan 方法
                    plan_result = await self.plan(plan_state)
                    
                    if plan_result.get("status") == "completed":
                        return {
                            "current_agent": "analysis",
                            "status": "completed",
                            "mode": "analysis",
                            "final_output": {
                                "response": self._l("我将帮您执行RNA-seq", language) + " " + analysis_type + self._l(" 分析。PlanningAgentV2 已根据当前状态生成完整执行计划。", language),
                                "analysis_plan": plan_result.get("final_output", {}).get("analysis_plan", {})
                            }
                        }
                    else:
                        # plan 失败，返回简单计划
                        return {
                            "current_agent": "analysis",
                            "status": "completed",
                            "mode": "analysis",
                            "final_output": {
                                "response": self._l("我将帮您执行RNA-seq", language) + " " + analysis_type + self._l(" 分析。请确认参数后开始。", language),
                                "analysis_plan": {
                                    "gene_family": gene_family,
                                    "species": species,
                                    "analysis_type": target_stages[-1],
                                    "stage": target_stages[-1],
                                    "stages": target_stages,
                                    "params": {
                                        "prefix": f"{species}.{gene_family}",
                                        "cpu": 8
                                    },
                                    "guidance": self._l("RNA-seq " + analysis_type + " 分析将通过前端异步执行。", language)
                                }
                            }
                        }
                
                # Ka/Ks分析：走通用规划流程（返回analysis_plan让前端执行）
                # 注意：不再直接调用_execute_kaks，而是通过PlanningAgentV2规划完整流程
                guidance = guidance_map.get(analysis_type)
                if guidance:
                    guidance = self._l(guidance, language)
                else:
                    guidance = self._l("请上传进行", language) + analysis_type + self._l("分析所需的文件，或确认使用系统内置数据。", language)

                return {
                    "current_agent": "analysis",
                    "status": "completed",
                    "mode": "analysis",
                    "final_output": {
                        "response": self._l("我将帮您执行", language) + analysis_type + self._l("分析。", language),
                        "analysis_plan": {
                            "gene_family": gene_family,
                            "species": species,
                            "analysis_type": analysis_type,
                            "stage": analysis_type,
                            "params": {
                                "prefix": f"{species}.{gene_family}",
                                "cpu": 8
                            },
                            "guidance": guidance
                        }
                    }
                }
            
            # 基因家族和物种提取逻辑（如果LLM识别结果为空，则从query中提取）
            if not gene_family or gene_family == "SPL":
                # 提取基因家族名称（如SPL、NAC、WRKY等）
                gene_family_match = re.search(
                    r'\b(SPL|NAC|WRKY|MYB|bHLH|WD40|C2H2|C3H|NBS|AP2|ARF|AUX|B3|BZIP|C2C2|CCAAT|CO-like|CPP|CSD|CTF|CYP|Dof|E2F|ERF|FAR1|G2-like|GATA|GeBP|GRAS|GRF|HB|HMA|HMG|HRT|HSF|JUMONJI|KNOX|LBD|LFY|LIM|LOB|LSD|MADS|MULE|MYB|NAC|ND|Nin|NLP|NPR|OFP|Orphan|Ots|PHD|PLATZ|PPR|Pseudo|RWP|S1Fa|SAP|SBP|Sigma|SMC|SRS|TALE|TCP|Tify|TRAF|Trihelix|TST|TUB|ULT|VOZ|Whirly|WOX|WRKY|XAL2|YABBY|ZF)\b', 
                    query, re.IGNORECASE
                )
                
                if not gene_family_match:
                    # 尝试匹配 "X基因家族" 或 "基因家族X" 模式
                    gene_family_match = re.search(r'([A-Z][A-Z0-9]+)\s*基因家族', query, re.IGNORECASE)
                if not gene_family_match:
                    gene_family_match = re.search(r'基因家族\s*([A-Z][A-Z0-9]+)', query, re.IGNORECASE)
                if not gene_family_match:
                    # 最后尝试匹配任意单词+基因家族
                    gene_family_match = re.search(r'(\w+)\s*基因家族', query, re.IGNORECASE)
                
                gene_family = gene_family_match.group(1).upper() if gene_family_match else "SPL"
            
            # 提取物种信息
            if not species or species == "Ft":
                species_match = re.search(r'(Ft|At|Os|Sl|Zm|Gm|Br|Vv|Pt|Sb)\w*', query, re.IGNORECASE)
                species = species_match.group(1).upper() if species_match else "Ft"
            
            file_info_list = []
            for f in files:
                if isinstance(f, dict):
                    file_info_list.append(f)
                elif isinstance(f, str):
                    from pathlib import Path as _Path

                    p = _Path(f)
                    file_info_list.append({"path": f, "name": p.name})

            hmm_file = None
            protein_file = None
            gff_file = None
            reference_file = None

            fasta_files = []

            for file_info in file_info_list:
                file_path = file_info.get("path", "")
                original_name = file_info.get("name", "")
                name_lower = original_name.lower()

                if name_lower.endswith(".hmm"):
                    hmm_file = file_path
                elif (
                    name_lower.endswith(".fasta")
                    or name_lower.endswith(".fa")
                    or name_lower.endswith(".faa")
                ):
                    fasta_files.append({"path": file_path, "name": original_name})
                elif ".gff" in name_lower:
                    gff_file = file_path

            for f in fasta_files:
                fname = f["name"].lower()
                if "pep" in fname or "protein" in fname:
                    protein_file = f["path"]
                elif "ath" in fname or "spl_ath" in fname or "sbp" in fname:
                    reference_file = f["path"]

            if not protein_file and len(fasta_files) > 0:
                for f in fasta_files:
                    if f["path"] != hmm_file and f["path"] != reference_file:
                        protein_file = f["path"]
                        break

            if not reference_file and len(fasta_files) > 1:
                for f in fasta_files:
                    if f["path"] != protein_file and f["path"] != hmm_file:
                        reference_file = f["path"]
            
            # 如果没有HMM文件但有参考序列文件，使用参考序列作为HMM输入（用于测试）
            if not hmm_file and reference_file:
                hmm_file = reference_file

            # 如果没有文件，返回分析计划让用户上传
            if not hmm_file or not protein_file:
                # 使用识别到的 analysis_type，不要硬编码
                stage_display_names = {
                    "identify": "基因家族鉴定",
                    "phylogeny": "系统发育分析",
                    "motif": "Motif分析",
                    "domain": "结构域分析",
                    "structure": "基因结构分析",
                    "promoter": "启动子分析",
                    "synteny": "共线性分析",
                    "interspecies_synteny": "种间共线性分析",
                    "karyotype": "核型分析",
                    "ppi": "蛋白互作分析",
                    "kaks": "Ka/Ks分析",
                    "expression": "表达分析",
                    "enrichment": "富集分析",
                    "pep": "蛋白理化性质分析",
                    "combo": "组合分析"
                }
                stage_display = stage_display_names.get(analysis_type, analysis_type)
                
                analysis_plan = {
                    "gene_family": gene_family,
                    "species": species,
                    "analysis_type": analysis_type,
                    "stage": analysis_type,
                    "params": {
                        "prefix": f"{species}.{gene_family}",
                        "cpu": 8
                    },
                    "tools_needed": ["hmmsearch", "blast"],
                    "guidance": f"请上传以下文件以进行{gene_family}基因家族{stage_display}：\n1. HMM模型文件（如{gene_family}_Ath.hmm）\n2. 目标物种蛋白质序列（如{species}.pep.fasta）\n\n或者使用系统内置的参考数据。"
                }
                
                return {
                    "current_agent": "analysis",
                    "status": "completed",
                    "mode": "analysis",
                    "final_output": {
                        "response": f"""根据您的需求，我为您规划了以下分析方案：

**基因家族**: {gene_family}
**物种**: {species}
**分析阶段**: {stage_display}
**Prefix**: {species}.{gene_family}

**需要的文件**:
- HMM模型文件（如{gene_family}_Ath.hmm）
- 目标物种蛋白质序列（如{species}.pep.fasta）

请上传所需文件，或确认使用系统内置参考数据开始分析。""",
                        "analysis_plan": analysis_plan,
                        "intent": {
                            "stage": analysis_type,
                            "taskType": "complex_analysis",
                            "params": analysis_plan["params"],
                            "confidence": 0.9
                        }
                    },
                }

            if hmm_file and protein_file:
                # 使用StageToolRegistry执行identify阶段
                import asyncio
                
                session_id = state.get("session_id", "default")
                prefix = f"{species}.{gene_family}"
                
                # 准备输入文件
                input_files = {
                    "hmm": hmm_file,
                    "protein": protein_file
                }
                
                # 执行identify阶段
                result = await self.stage_registry.execute(
                    stage_id="identify",
                    job_id=session_id,
                    prefix=prefix,
                    extra_params={"cpu": 8, "dom_i_evalue_max": 0.001, "hmm_cov_min": 0.3, "blast_evalue": "1e-05"},
                    input_files=input_files
                )
                
                if result.get("status") == "success":
                    manifest = result.get("manifest", {})
                    final_members = manifest.get("metrics", {}).get("final_members", 0) if isinstance(manifest, dict) else 0
                    
                    # 发送消息通知其他Agent
                    try:
                        import uuid
                        await self.message_bus.send(AgentMessage(
                            id=str(uuid.uuid4()),
                            sender="analysis",
                            receiver="broadcast",
                            type=MessageType.NOTIFICATION,
                            content={
                                "event": "stage_completed",
                                "stage": "identify",
                                "gene_family": gene_family,
                                "species": species,
                                "final_members": final_members,
                                "workspace": str(WORKSPACE_DIR / session_id)
                            },
                            session_id=session_id
                        ))
                    except Exception as e:
                        print(f"Message bus error: {e}")
                    
                    return {
                        "current_agent": "analysis",
                        "status": "completed",
                        "mode": "analysis",
                        "final_output": {
                            "response": f"""✅ 基因家族鉴定完成！

**鉴定结果**:
- 基因家族: {gene_family}
- 物种: {species}
- 最终成员数: {final_members}
- 工作目录: {WORKSPACE_DIR / session_id}

**输出文件**:
- {prefix}.final_members.id
- {prefix}.final_members.pep.fa
- {prefix}.identify.manifest.json
- {prefix}.identify.report.md

下一步：可以进行进化树构建(phylogeny)、结构域分析(domain)或Motif分析(motif)。
""",
                            "analysis_plan": {
                                "gene_family": gene_family,
                                "species": species,
                                "status": "completed",
                                "workspace": str(WORKSPACE_DIR / session_id),
                                "completed_stage": "identify",
                                "next_stages": ["phylogeny", "motif", "domain", "structure"]
                            },
                            "execution_result": result
                        },
                    }
                else:
                    # 执行失败
                    return {
                        "current_agent": "analysis",
                        "status": "failed",
                        "mode": "analysis",
                        "final_output": {
                            "response": f"❌ 基因家族鉴定失败：{result.get('error', 'Unknown error')}",
                            "execution_result": result
                        },
                    }

            system_prompt = """你是一个基因家族分析专家。用户想要进行基因家族分析。

请分析用户的需求，并提供以下信息（JSON格式）：
{
    "gene_family": "基因家族名称（如SPL, KNOX等）",
    "species": "物种名称（如苦荞、拟南芥等）",
    "analysis_type": "分析类型（identification/phylogeny/expression等）",
    "tools_needed": ["需要的工具列表"],
    "guidance": "给用户的指导建议"
}

如果用户没有提供足够信息，请在guidance中说明需要哪些信息。"""

            messages = [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=query),
            ]

            response = await self.llm_manager.acall(messages, temperature=0.3)

            import json

            try:
                content = response.content
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0]

                analysis_plan = json.loads(content.strip())
            except:
                analysis_plan = {
                    "gene_family": "未知",
                    "species": "未知",
                    "analysis_type": "identification",
                    "tools_needed": ["hmmsearch", "blast"],
                    "guidance": response.content,
                }

            if files:
                analysis_plan["uploaded_files"] = files
                analysis_plan["guidance"] += f"\n\n已检测到您上传了 {len(files)} 个文件。"
                if hmm_file:
                    analysis_plan["guidance"] += "\n- HMM文件已识别，可用于鉴定。"
                if protein_file:
                    analysis_plan["guidance"] += "\n- 蛋白质序列文件已识别。"

            available_tools = self.tool_executor.list_tools()
            tool_info = [
                {
                    "id": t.id,
                    "name": t.name,
                    "category": t.category,
                }
                for t in available_tools
            ]

            return {
                "current_agent": "analysis",
                "status": "completed",
                "mode": "analysis",
                "final_output": {
                    "response": f"""根据您的需求，我为您规划了以下分析方案：

**基因家族**: {analysis_plan.get('gene_family', '未知')}
**物种**: {analysis_plan.get('species', '未知')}
**分析类型**: {analysis_plan.get('analysis_type', 'identification')}

**需要的工具**:
{chr(10).join(['- ' + t for t in analysis_plan.get('tools_needed', [])])}

**指导建议**:
{analysis_plan.get('guidance', '请提供基因家族HMM文件和目标物种蛋白质序列文件。')}

**可用工具列表**:
{chr(10).join(['- ' + t['name'] + ' (' + t['id'] + ')' for t in tool_info])}
""",
                    "analysis_plan": analysis_plan,
                    "available_tools": tool_info,
                },
            }
        except Exception as e:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "errors": [str(e)],
                "final_output": {"error": str(e)},
            }

    async def _execute_rnaseq_qc(self, state: GraphState, gene_family: str, species: str, files: list) -> dict:
        """执行RNA-seq质量控制分析"""
        import asyncio
        import subprocess
        from pathlib import Path
        import json
        
        session_id = state.get("session_id", "default")
        # 确保 prefix 有效
        if not species or species.strip() == "":
            species = "Ft"
        if not gene_family or gene_family.strip() == "":
            gene_family = "SPL"
        prefix = f"{species.strip()}.{gene_family.strip()}"
        print(f"[RNA-QC] prefix: '{prefix}'")
        
        # 确定input目录
        input_dir, session_id = self._get_input_dir(session_id, "RNA-QC")
        
        workspace = WORKSPACE_DIR / session_id
        final_dir = workspace / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"[RNA-QC] Using input directory: {input_dir}")
        print(f"[RNA-QC] Using workspace: {workspace}")
        
        # 如果files为空，从input_dir重新加载
        if not files:
            files = []
            for f in input_dir.iterdir():
                if f.is_file():
                    files.append({"path": str(f), "name": f.name})
            print(f"[RNA-QC] Reloaded files from input_dir: {len(files)} files")
        
        # 查找样本配置文件
        config_file = None
        for candidate in [
            input_dir / "samples.yaml",
            input_dir / "group_sample.yaml",
            input_dir / "config.yaml",
        ]:
            if candidate.exists():
                config_file = candidate
                break
        
        # 查找FASTQ文件（支持两种模式：1. 文件在input_dir中；2. 文件路径在samples.yaml中）
        fastq_files = []
        for f in input_dir.glob("*.fastq.gz"):
            fastq_files.append(f)
        for f in input_dir.glob("*.fq.gz"):
            fastq_files.append(f)
        for f in input_dir.glob("*.fastq"):
            fastq_files.append(f)
        for f in input_dir.glob("*.fq"):
            fastq_files.append(f)
        
        # 如果config_file存在，检查其中的FASTQ路径是否有效
        fastq_paths_valid = False
        if config_file:
            try:
                import yaml
                with open(config_file, 'r') as f:
                    config = yaml.safe_load(f)
                samples = config.get('samples', {})
                for sample_id, paths in samples.items():
                    if isinstance(paths, list) and len(paths) > 0:
                        # 检查至少一个路径存在
                        for path in paths:
                            if Path(path).exists():
                                fastq_paths_valid = True
                                break
                    elif isinstance(paths, str) and Path(paths).exists():
                        fastq_paths_valid = True
                    if fastq_paths_valid:
                        break
                print(f"[RNA-QC] Config file found: {config_file}")
                print(f"[RNA-QC] FASTQ paths in config valid: {fastq_paths_valid}")
            except Exception as e:
                print(f"[RNA-QC] Error checking config: {e}")
        
        # 只要config_file存在且其中路径有效，或者input_dir中有FASTQ文件，就可以继续
        if not config_file:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "mode": "analysis",
                "final_output": {
                    "response": "❌ RNA-seq质控分析缺少必要文件\n\n请确保已上传以下文件:\n- 样本配置文件 (samples.yaml 或 group_sample.yaml)"
                }
            }
        
        if not fastq_files and not fastq_paths_valid:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "mode": "analysis",
                "final_output": {
                    "response": "❌ RNA-seq质控分析找不到FASTQ文件\n\n请确保:\n- FASTQ文件已上传到input目录，或\n- samples.yaml中配置的FASTQ文件路径正确且文件存在"
                }
            }
        
        # 创建输出目录
        qc_dir = workspace / "rnaseq_qc"
        qc_dir.mkdir(parents=True, exist_ok=True)
        
        # 检查是否已有结果文件，如果有则检查是否需要重新执行
        qc_manifest = final_dir / f"{prefix}.rnaseq_qc.manifest.json"
        if qc_manifest.exists():
            # 获取文件修改时间
            import time
            mtime = qc_manifest.stat().st_mtime
            age_hours = (time.time() - mtime) / 3600
            print(f"[RNA-QC] Existing manifest found, age: {age_hours:.1f} hours")
            
            # 如果结果文件较旧（超过24小时），删除旧结果重新执行
            if age_hours > 24:
                print(f"[RNA-QC] Results older than 24 hours, will re-execute")
                # 可选：删除旧结果
                import shutil
                if qc_dir.exists():
                    shutil.rmtree(qc_dir)
                    qc_dir.mkdir(parents=True, exist_ok=True)
        
        # 构建命令
        cmd = [
            sys.executable, str(PROJECT_ROOT / "cli" / "run_rnaseq_qc.py"),
            "-i", str(input_dir),
            "-c", str(config_file) if config_file else str(input_dir / "samples.yaml"),
            "-o", str(qc_dir),
            "-j", session_id,
            "-p", prefix or "analysis",
            "-t", "4"
        ]
        
        # 执行分析（无超时限制，RNA-seq分析可能需要数小时）
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=None,  # 取消超时限制
                cwd=str(PROJECT_ROOT)
            )
            
            # 检查输出
            qc_manifest = final_dir / f"{prefix}.rnaseq_qc.manifest.json"
            print(f"[RNA-QC] Checking manifest: {qc_manifest}")
            print(f"[RNA-QC] Manifest exists: {qc_manifest.exists()}")
            print(f"[RNA-QC] Return code: {result.returncode}")
            print(f"[RNA-QC] stdout: {result.stdout[-500:]}")
            print(f"[RNA-QC] stderr: {result.stderr[-500:]}")
            
            # 检查是否有结果文件生成
            has_results = qc_manifest.exists() or (qc_dir / "fastqc").exists() or (qc_dir / "clean_reads").exists() or (final_dir / f"{prefix}_cutadapt.log").exists()
            print(f"[RNA-QC] has_results: {has_results}")
            print(f"[RNA-QC] qc_manifest exists: {qc_manifest.exists()}")
            print(f"[RNA-QC] qc_dir/fastqc exists: {(qc_dir / 'fastqc').exists()}")
            print(f"[RNA-QC] final_dir/cutadapt.log exists: {(final_dir / f'{prefix}_cutadapt.log').exists()}")
            
            # 检查执行是否真正成功：返回码为0且结果文件存在
            execution_success = (result.returncode == 0) and has_results
            print(f"[RNA-QC] execution_success: {execution_success} (returncode={result.returncode}, has_results={has_results})")
            
            # 只有真正执行成功才返回完成状态
            if execution_success:
                with open(qc_manifest) as f:
                    manifest = json.load(f)
                metrics = manifest.get("metrics", {})
                total_samples = metrics.get("total_samples", 0)
                total_raw = metrics.get("total_raw_reads", 0)
                total_clean = metrics.get("total_clean_reads", 0)
                
                return {
                    "current_agent": "analysis",
                    "status": "completed",
                    "mode": "analysis",
                    "final_output": {
                        "response": f"""✅ RNA-seq质量控制分析完成！

**分析结果**:
- 样本数: {total_samples}
- 原始reads总数: {total_raw:,}
- 过滤后reads总数: {total_clean:,}
- 工作目录: {workspace}

**输出文件**:
- {prefix}.rnaseq_qc_report.md
- {prefix}.rnaseq_qc.manifest.json
- FastQC报告和clean reads

下一步：可以进行RNA-seq比对分析(rna_seq_mapping)。
""",
                        "analysis_plan": {
                            "gene_family": gene_family,
                            "species": species,
                            "analysis_type": "rna_seq_qc",
                            "status": "completed",
                            "workspace": str(workspace),
                            "completed_stage": "rna_seq_qc",
                            "next_stages": ["rna_seq_mapping"]
                        },
                        "execution_result": {"manifest": manifest}
                    }
                }
            else:
                # 执行失败 - 调用DiagnosisAgent诊断
                error_msg = result.stderr[-1000:] if len(result.stderr) > 1000 else result.stderr
                stdout_msg = result.stdout[-1000:] if len(result.stdout) > 1000 else result.stdout
                
                error_info = {
                    "stage": "rnaseq_qc",
                    "message": "RNA-seq质控分析失败",
                    "stderr": error_msg,
                    "stdout": stdout_msg,
                    "params": {"prefix": prefix, "cpu": 4}
                }
                
                full_error = f"{error_msg}\n{stdout_msg}"
                try:
                    diagnosis = await self.diagnosis_agent._diagnose_error(
                        "rnaseq_qc", full_error, error_info
                    )
                    suggestion = diagnosis.get("suggestion", "请检查错误日志")
                    error_type = diagnosis.get("error_type", "unknown")
                    
                    return {
                        "current_agent": "analysis",
                        "status": "failed",
                        "mode": "analysis",
                        "final_output": {
                            "response": f"❌ RNA-seq质控分析失败\n\n**诊断结果**: {error_type}\n\n**建议**: {suggestion}\n\n**错误详情**:\n```\n{error_msg[:500]}\n```",
                            "returncode": result.returncode,
                            "diagnosis": diagnosis
                        }
                    }
                except Exception:
                    return {
                        "current_agent": "analysis",
                        "status": "failed",
                        "mode": "analysis",
                        "final_output": {
                            "response": f"❌ RNA-seq质控分析失败:\n\n```\n{error_msg}\n```",
                            "returncode": result.returncode
                        }
                    }
        except subprocess.TimeoutExpired:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "mode": "analysis",
                "final_output": {"response": "❌ RNA-seq质控分析超时（超过2小时）"}
            }
        except Exception as e:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "mode": "analysis",
                "final_output": {"response": f"❌ RNA-seq质控分析出错: {str(e)}"}
            }

    async def _execute_rnaseq_align(self, state: GraphState, gene_family: str, species: str, files: list) -> dict:
        """执行RNA-seq比对分析"""
        import asyncio
        import subprocess
        from pathlib import Path
        import json
        
        session_id = state.get("session_id", "default")
        # 确保 prefix 有效
        if not species or species.strip() == "":
            species = "Ft"
        if not gene_family or gene_family.strip() == "":
            gene_family = "SPL"
        prefix = f"{species.strip()}.{gene_family.strip()}"
        print(f"[RNA-ALN] prefix: '{prefix}'")
        
        # 确定input目录
        input_dir, session_id = self._get_input_dir(session_id, "RNA-ALN")
        
        workspace = WORKSPACE_DIR / session_id
        final_dir = workspace / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"[RNA-ALN] Using input directory: {input_dir}")
        print(f"[RNA-ALN] Using workspace: {workspace}")
        
        # 如果files为空，从input_dir重新加载
        if not files:
            files = []
            for f in input_dir.iterdir():
                if f.is_file():
                    files.append({"path": str(f), "name": f.name})
            print(f"[RNA-ALN] Reloaded files from input_dir: {len(files)} files")
        
        # 查找HISAT2索引
        hisat2_idx = None
        for candidate in [
            input_dir / "hisat2_index" / f"{species}",
            input_dir / f"{species}.1.ht2",
            PROJECT_ROOT / "data" / "reference" / f"{species}" / "hisat2_index" / f"{species}",
        ]:
            idx_file = candidate if str(candidate).endswith('.ht2') else Path(f"{candidate}.1.ht2")
            if idx_file.exists():
                hisat2_idx = candidate if not str(candidate).endswith('.ht2') else candidate.parent / candidate.stem.rsplit('.', 1)[0]
                break
        
        # 查找样本配置文件
        config_file = None
        for candidate in [
            input_dir / "samples.yaml",
            input_dir / "group_sample.yaml",
        ]:
            if candidate.exists():
                config_file = candidate
                break
        
        if not hisat2_idx:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "mode": "analysis",
                "final_output": {
                    "response": f"❌ RNA-seq比对分析缺少HISAT2索引\n\n请确保已上传或系统已存在以下文件:\n- HISAT2索引文件 ({species}.1.ht2等)\n\n或先运行RNA-seq质控分析生成clean reads。"
                }
            }
        
        # 创建输出目录
        align_dir = workspace / "rnaseq_align"
        align_dir.mkdir(parents=True, exist_ok=True)
        
        # 构建命令
        cmd = [
            sys.executable, str(PROJECT_ROOT / "cli" / "run_rnaseq_align.py"),
            "-i", str(final_dir),  # 从final_dir读取clean reads
            "-x", str(hisat2_idx),
            "-o", str(align_dir),
            "-j", session_id,
            "-p", prefix or "analysis",
            "-t", "4"
        ]
        
        if config_file:
            cmd.extend(["--sample-config", str(config_file)])
        
        # 执行分析
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=7200,
                cwd=str(PROJECT_ROOT)
            )
            
            # 检查输出
            align_manifest = final_dir / f"{prefix or 'analysis'}.rnaseq_align.manifest.json"
            print(f"[RNA-ALN] Checking manifest: {align_manifest}")
            print(f"[RNA-ALN] Manifest exists: {align_manifest.exists()}")
            print(f"[RNA-ALN] Return code: {result.returncode}")
            
            if result.returncode == 0 and align_manifest.exists():
                with open(align_manifest) as f:
                    manifest = json.load(f)
                metrics = manifest.get("metrics", {})
                total_samples = metrics.get("total_samples", 0)
                avg_rate = metrics.get("average_alignment_rate", 0)
                
                return {
                    "current_agent": "analysis",
                    "status": "completed",
                    "mode": "analysis",
                    "final_output": {
                        "response": f"""✅ RNA-seq比对分析完成！

**分析结果**:
- 样本数: {total_samples}
- 平均比对率: {avg_rate:.2f}%
- 工作目录: {workspace}

**输出文件**:
- {prefix}.rnaseq_align_report.md
- {prefix}.rnaseq_align.manifest.json
- 排序后的BAM文件

下一步：可以进行RNA-seq定量分析(rna_seq_quantification)。
""",
                        "analysis_plan": {
                            "gene_family": gene_family,
                            "species": species,
                            "analysis_type": "rna_seq_mapping",
                            "status": "completed",
                            "workspace": str(workspace),
                            "completed_stage": "rna_seq_align",
                            "next_stages": ["rna_seq_quantification"]
                        },
                        "execution_result": {"manifest": manifest}
                    }
                }
            else:
                error_msg = result.stderr[-1000:] if len(result.stderr) > 1000 else result.stderr
                return {
                    "current_agent": "analysis",
                    "status": "failed",
                    "mode": "analysis",
                    "final_output": {
                        "response": f"❌ RNA-seq比对分析失败:\n\n```\n{error_msg}\n```",
                        "returncode": result.returncode
                    }
                }
        except subprocess.TimeoutExpired:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "mode": "analysis",
                "final_output": {"response": "❌ RNA-seq比对分析超时（超过2小时）"}
            }
        except Exception as e:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "mode": "analysis",
                "final_output": {"response": f"❌ RNA-seq比对分析出错: {str(e)}"}
            }

    async def _execute_rnaseq_quant(self, state: GraphState, gene_family: str, species: str, files: list) -> dict:
        """执行RNA-seq定量分析"""
        import asyncio
        import subprocess
        from pathlib import Path
        import json
        
        session_id = state.get("session_id", "default")
        # 确保 prefix 有效
        if not species or species.strip() == "":
            species = "Ft"
        if not gene_family or gene_family.strip() == "":
            gene_family = "SPL"
        prefix = f"{species.strip()}.{gene_family.strip()}"
        print(f"[RNA-QUAN] prefix: '{prefix}'")
        
        # 确定input目录
        input_dir, session_id = self._get_input_dir(session_id, "RNA-QUAN")
        
        workspace = WORKSPACE_DIR / session_id
        final_dir = workspace / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"[RNA-QUAN] Using input directory: {input_dir}")
        print(f"[RNA-QUAN] Using workspace: {workspace}")
        
        # 如果files为空，从input_dir重新加载
        if not files:
            files = []
            for f in input_dir.iterdir():
                if f.is_file():
                    files.append({"path": str(f), "name": f.name})
            print(f"[RNA-QUAN] Reloaded files from input_dir: {len(files)} files")
        
        # 查找GTF/GFF注释文件
        gtf_file = None
        for candidate in [
            input_dir / f"{species}.gtf",
            input_dir / f"{species}.gff",
            input_dir / f"{species}.gff3",
            input_dir / f"{species}_final.gff3",
            PROJECT_ROOT / "data" / "reference" / f"{species}" / f"{species}.gtf",
            PROJECT_ROOT / "data" / "reference" / f"{species}" / f"{species}.gff3",
        ]:
            if candidate.exists():
                gtf_file = candidate
                break
        
        # 查找样本配置文件
        config_file = None
        for candidate in [
            input_dir / "samples.yaml",
            input_dir / "group_sample.yaml",
        ]:
            if candidate.exists():
                config_file = candidate
                break
        
        if not gtf_file:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "mode": "analysis",
                "final_output": {
                    "response": f"❌ RNA-seq定量分析缺少GTF/GFF注释文件\n\n请确保已上传以下文件:\n- 基因组注释文件 ({species}.gtf 或 {species}.gff3)"
                }
            }
        
        # 创建输出目录
        quant_dir = workspace / "rnaseq_quant"
        quant_dir.mkdir(parents=True, exist_ok=True)
        
        # 构建命令
        cmd = [
            sys.executable, str(PROJECT_ROOT / "cli" / "run_rnaseq_quant.py"),
            "-i", str(final_dir),  # 从final_dir读取BAM文件
            "-g", str(gtf_file),
            "-o", str(quant_dir),
            "-p", prefix or "analysis",
            "-j", session_id,
            "-t", "4"
        ]
        
        if config_file:
            cmd.extend(["--sample-config", str(config_file)])
        
        # 执行分析
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=7200,
                cwd=str(PROJECT_ROOT)
            )
            
            # 检查输出
            quant_manifest = final_dir / f"{prefix}.rnaseq_quant.manifest.json"
            print(f"[RNA-QUAN] Checking manifest: {quant_manifest}")
            print(f"[RNA-QUAN] Manifest exists: {quant_manifest.exists()}")
            print(f"[RNA-QUAN] Return code: {result.returncode}")
            
            if result.returncode == 0 and quant_manifest.exists():
                with open(quant_manifest) as f:
                    manifest = json.load(f)
                metrics = manifest.get("metrics", {})
                total_genes = metrics.get("total_genes", 0)
                
                return {
                    "current_agent": "analysis",
                    "status": "completed",
                    "mode": "analysis",
                    "final_output": {
                        "response": f"""✅ RNA-seq定量分析完成！

**分析结果**:
- 定量基因数: {total_genes}
- 工作目录: {workspace}

**输出文件**:
- {prefix}.count_matrix.txt
- {prefix}.fpkm_matrix.txt
- {prefix}.tpm_matrix.txt
- {prefix}.rnaseq_quant_report.md
- {prefix}.rnaseq_quant.manifest.json

下一步：可以进行差异表达分析(expression)。
""",
                        "analysis_plan": {
                            "gene_family": gene_family,
                            "species": species,
                            "analysis_type": "rna_seq_quantification",
                            "status": "completed",
                            "workspace": str(workspace),
                            "completed_stage": "rna_seq_quant",
                            "next_stages": ["expression"]
                        },
                        "execution_result": {"manifest": manifest}
                    }
                }
            else:
                error_msg = result.stderr[-1000:] if len(result.stderr) > 1000 else result.stderr
                return {
                    "current_agent": "analysis",
                    "status": "failed",
                    "mode": "analysis",
                    "final_output": {
                        "response": f"❌ RNA-seq定量分析失败:\n\n```\n{error_msg}\n```",
                        "returncode": result.returncode
                    }
                }
        except subprocess.TimeoutExpired:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "mode": "analysis",
                "final_output": {"response": "❌ RNA-seq定量分析超时（超过2小时）"}
            }
        except Exception as e:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "mode": "analysis",
                "final_output": {"response": f"❌ RNA-seq定量分析出错: {str(e)}"}
            }

    async def _execute_rnaseq_de(self, state: GraphState, gene_family: str, species: str, files: list) -> dict:
        """执行RNA-seq差异表达分析"""
        import asyncio
        import subprocess
        from pathlib import Path
        import json
        
        session_id = state.get("session_id", "default")
        prefix = f"{species}.{gene_family}"
        
        # 确定input目录
        input_dir, session_id = self._get_input_dir(session_id, "RNA-DE")
        
        workspace = WORKSPACE_DIR / session_id
        final_dir = workspace / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"[RNA-DE] Using input directory: {input_dir}")
        print(f"[RNA-DE] Using workspace: {workspace}")
        
        # 查找样本配置文件（必须包含分组和比较信息）
        config_file = None
        for candidate in [
            input_dir / "samples.yaml",
            input_dir / "group_sample.yaml",
        ]:
            if candidate.exists():
                config_file = candidate
                break
        
        # 查找基因家族成员ID文件（可选）
        family_ids_file = None
        for candidate in [
            final_dir / f"{prefix}.final_members.id",
            final_dir / f"{prefix}.geneID.subfamily",
            input_dir / f"{prefix}.final_members.id",
        ]:
            if candidate.exists():
                family_ids_file = candidate
                break
        
        if not config_file:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "mode": "analysis",
                "final_output": {
                    "response": "❌ RNA-seq差异表达分析缺少样本配置文件\n\n请确保已上传以下文件:\n- 样本配置文件 (samples.yaml 或 group_sample.yaml，包含groups和compare字段)"
                }
            }
        
        # 创建输出目录
        de_dir = workspace / "rnaseq_de"
        de_dir.mkdir(parents=True, exist_ok=True)
        
        # 构建命令
        cmd = [
            sys.executable, str(PROJECT_ROOT / "cli" / "run_rnaseq_de.py"),
            "-i", str(final_dir),  # 从final_dir读取count matrix
            "-c", str(config_file),
            "-o", str(de_dir),
            "-p", prefix or "analysis",
            "-j", session_id,
            "-t", "4",
            "--padj", "0.05",
            "--log2fc", "1.0"
        ]
        
        if family_ids_file:
            cmd.extend(["-f", str(family_ids_file), "-n", gene_family])
        
        # 执行分析
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=7200,
                cwd=str(PROJECT_ROOT)
            )
            
            # 检查输出
            de_manifest = final_dir / f"{prefix}.rnaseq_de.manifest.json"
            print(f"[RNA-DE] Checking manifest: {de_manifest}")
            print(f"[RNA-DE] Manifest exists: {de_manifest.exists()}")
            print(f"[RNA-DE] Return code: {result.returncode}")
            
            if result.returncode == 0 and de_manifest.exists():
                with open(de_manifest) as f:
                    manifest = json.load(f)
                metrics = manifest.get("metrics", {})
                total_comparisons = metrics.get("total_comparisons", 0)
                total_de_genes = metrics.get("de_genes_total", 0)
                
                return {
                    "current_agent": "analysis",
                    "status": "completed",
                    "mode": "analysis",
                    "final_output": {
                        "response": f"""✅ RNA-seq差异表达分析完成！

**分析结果**:
- 比较组数: {total_comparisons}
- 差异表达基因总数: {total_de_genes}
- 工作目录: {workspace}

**输出文件**:
- {prefix}.rnaseq_de_report.md
- {prefix}.rnaseq_de.manifest.json
- 各比较组DE结果文件
- MA图和火山图

下一步：可以进行富集分析(enrichment)。
""",
                        "analysis_plan": {
                            "gene_family": gene_family,
                            "species": species,
                            "analysis_type": "expression",
                            "status": "completed",
                            "workspace": str(workspace),
                            "completed_stage": "rnaseq_de",
                            "next_stages": ["enrichment"]
                        },
                        "execution_result": {"manifest": manifest}
                    }
                }
            else:
                error_msg = result.stderr[-1000:] if len(result.stderr) > 1000 else result.stderr
                return {
                    "current_agent": "analysis",
                    "status": "failed",
                    "mode": "analysis",
                    "final_output": {
                        "response": f"❌ RNA-seq差异表达分析失败:\n\n```\n{error_msg}\n```",
                        "returncode": result.returncode
                    }
                }
        except subprocess.TimeoutExpired:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "mode": "analysis",
                "final_output": {"response": "❌ RNA-seq差异表达分析超时（超过2小时）"}
            }
        except Exception as e:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "mode": "analysis",
                "final_output": {"response": f"❌ RNA-seq差异表达分析出错: {str(e)}"}
            }

    async def _execute_rnaseq_enrich(self, state: GraphState, gene_family: str, species: str, files: list) -> dict:
        """执行RNA-seq富集分析"""
        import asyncio
        import subprocess
        from pathlib import Path
        import json
        
        session_id = state.get("session_id", "default")
        prefix = f"{species}.{gene_family}"
        
        # 确定input目录
        input_dir, session_id = self._get_input_dir(session_id, "RNA-ENRICH")
        
        workspace = WORKSPACE_DIR / session_id
        final_dir = workspace / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"[RNA-ENRICH] Using input directory: {input_dir}")
        print(f"[RNA-ENRICH] Using workspace: {workspace}")
        
        # 查找物种代码（默认为ath）
        species_code = species.lower()
        if species_code not in ["ath", "hsa", "mmu", "rno", "dme", "sce"]:
            species_code = "ath"  # 默认使用拟南芥
        
        # 查找gene2go文件（可选，用于非模式物种）
        gene2go_file = None
        for candidate in [
            final_dir / "gene2go.txt",
            input_dir / "gene2go.txt",
        ]:
            if candidate.exists():
                gene2go_file = candidate
                break
        
        # 查找蛋白序列文件（用于eggNOG注释）
        protein_file = None
        for candidate in [
            final_dir / f"{prefix}.final_members.pep.fa",
            input_dir / f"{species}.pep.fasta",
        ]:
            if candidate.exists():
                protein_file = candidate
                break
        
        # 创建输出目录
        enrich_dir = workspace / "rnaseq_enrich"
        enrich_dir.mkdir(parents=True, exist_ok=True)
        
        # 构建命令
        cmd = [
            sys.executable, str(PROJECT_ROOT / "cli" / "run_rnaseq_enrich.py"),
            "-i", str(final_dir),  # 从final_dir读取DE基因
            "-s", species_code,
            "-o", str(enrich_dir),
            "-p", prefix or "analysis",
            "-j", session_id,
            "-t", "4",
            "--pvalue", "0.05",
            "--qvalue", "0.05"
        ]
        
        if gene2go_file:
            cmd.extend(["-g", str(gene2go_file)])
        
        if protein_file:
            cmd.extend(["--protein-fa", str(protein_file), "--auto-annotate"])
        
        # 执行分析
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=7200,
                cwd=str(PROJECT_ROOT)
            )
            
            # 检查输出
            enrich_manifest = final_dir / f"{prefix}.rnaseq_enrich.manifest.json"
            print(f"[RNA-ENRICH] Checking manifest: {enrich_manifest}")
            print(f"[RNA-ENRICH] Manifest exists: {enrich_manifest.exists()}")
            print(f"[RNA-ENRICH] Return code: {result.returncode}")
            
            if result.returncode == 0 and enrich_manifest.exists():
                with open(enrich_manifest) as f:
                    manifest = json.load(f)
                metrics = manifest.get("metrics", {})
                total_comparisons = metrics.get("total_comparisons", 0)
                go_terms = metrics.get("go_enriched_terms", 0)
                kegg_pathways = metrics.get("kegg_enriched_pathways", 0)
                
                return {
                    "current_agent": "analysis",
                    "status": "completed",
                    "mode": "analysis",
                    "final_output": {
                        "response": f"""✅ RNA-seq富集分析完成！

**分析结果**:
- 比较组数: {total_comparisons}
- GO富集条目数: {go_terms}
- KEGG通路数: {kegg_pathways}
- 工作目录: {workspace}

**输出文件**:
- {prefix}.rnaseq_enrich_report.md
- {prefix}.rnaseq_enrich.manifest.json
- GO和KEGG富集结果文件
- 富集图表

RNA-seq分析流程全部完成！
""",
                        "analysis_plan": {
                            "gene_family": gene_family,
                            "species": species,
                            "analysis_type": "enrichment",
                            "status": "completed",
                            "workspace": str(workspace),
                            "completed_stage": "rnaseq_enrich",
                            "next_stages": []
                        },
                        "execution_result": {"manifest": manifest}
                    }
                }
            else:
                error_msg = result.stderr[-1000:] if len(result.stderr) > 1000 else result.stderr
                return {
                    "current_agent": "analysis",
                    "status": "failed",
                    "mode": "analysis",
                    "final_output": {
                        "response": f"❌ RNA-seq富集分析失败:\n\n```\n{error_msg}\n```",
                        "returncode": result.returncode
                    }
                }
        except subprocess.TimeoutExpired:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "mode": "analysis",
                "final_output": {"response": "❌ RNA-seq富集分析超时（超过2小时）"}
            }
        except Exception as e:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "mode": "analysis",
                "final_output": {"response": f"❌ RNA-seq富集分析出错: {str(e)}"}
            }

    async def _execute_kaks(self, state: GraphState, gene_family: str, species: str, files: list) -> dict:
        """执行Ka/Ks分析"""
        import asyncio
        import subprocess
        from pathlib import Path
        
        session_id = state.get("session_id", "default")
        print(f"[KaKs] gene_family: '{gene_family}', species: '{species}'")
        # 确保 prefix 有效
        if not species or species.strip() == "":
            species = "Ft"
        if not gene_family or gene_family.strip() == "":
            gene_family = "SPL"
        prefix = f"{species.strip()}.{gene_family.strip()}"
        print(f"[KaKs] prefix: '{prefix}'")
        
        # 确定input目录
        input_dir, session_id = self._get_input_dir(session_id, "KaKs")
        
        workspace = WORKSPACE_DIR / session_id
        final_dir = workspace / "final"
        
        print(f"[KaKs] Using input directory: {input_dir}")
        print(f"[KaKs] Using workspace: {workspace}")
        
        # 查找输入文件
        cds1_file = None  # Ft CDS
        cds2_file = None  # Ath CDS
        pep1_file = None  # Ft PEP
        pep2_file = None  # Ath PEP
        pairs_file = None  # Ortholog pairs
        gff1_file = None   # Ft GFF
        genome1_file = None  # Ft Genome
        
        # 首先从files中识别文件
        for f in files:
            if isinstance(f, dict):
                file_path = Path(f.get("path", ""))
                file_name = f.get("name", "").lower()
            else:
                file_path = Path(f)
                file_name = file_path.name.lower()
            
            if not file_path.exists():
                continue
                
            # 识别文件类型
            if "cds" in file_name and "ft" in file_name:
                cds1_file = file_path
            elif "cds" in file_name and "ath" in file_name:
                cds2_file = file_path
            elif "pep" in file_name and "ft" in file_name:
                pep1_file = file_path
            elif "pep" in file_name and "ath" in file_name:
                pep2_file = file_path
            elif "ortholog" in file_name or "pairs" in file_name:
                pairs_file = file_path
            elif file_name.endswith("_final.gff3") and "ft" in file_name:
                gff1_file = file_path
            elif file_name.endswith(".gff3") and "ft" in file_name:
                gff1_file = file_path
            elif file_name.endswith(".fasta") and "genome" in file_name and "ft" in file_name:
                genome1_file = file_path
        
        # 在input目录中查找缺失的文件
        if not cds1_file:
            for candidate in [input_dir / "Ft.cds.fasta", input_dir / f"{species}.cds.fasta"]:
                if candidate.exists():
                    cds1_file = candidate
                    break
        if not cds2_file:
            for candidate in [input_dir / "Ath.cds.fasta", input_dir / "ath.cds.fasta"]:
                if candidate.exists():
                    cds2_file = candidate
                    break
            # 如果找不到，尝试从系统默认数据复制到input_dir
            if not cds2_file:
                default_ath_cds = PROJECT_ROOT / "input" / "test_ncbi" / "Ath.cds.fasta"
                if default_ath_cds.exists():
                    import shutil
                    cds2_file = input_dir / "Ath.cds.fasta"
                    shutil.copy2(default_ath_cds, cds2_file)
                    print(f"[KaKs] Copied default Ath CDS to {cds2_file}")
                    print(f"[KaKs] Copied default Ath CDS from {default_ath_cds}")
        if not pep1_file:
            for candidate in [input_dir / "Ft.pep.fasta", input_dir / f"{species}.pep.fasta"]:
                if candidate.exists():
                    pep1_file = candidate
                    break
        if not pep2_file:
            for candidate in [input_dir / "Ath.pep.fasta", input_dir / "ath.pep.fasta"]:
                if candidate.exists():
                    pep2_file = candidate
                    break
        if not pairs_file:
            for candidate in [
                input_dir / "ortholog_pairs.txt",
                workspace / "kaks_analysis" / "ortholog_pairs.txt",
                workspace / "interspecies_synteny" / "analysis" / "Ft.Ath.anchors"
            ]:
                if candidate.exists():
                    pairs_file = candidate
                    break
        if not gff1_file:
            for candidate in [input_dir / "Ft_final.gff3", input_dir / f"{species}_final.gff3"]:
                if candidate.exists():
                    gff1_file = candidate
                    break
        if not genome1_file:
            for candidate in [input_dir / "Ft.genome.fasta", input_dir / f"{species}.genome.fasta"]:
                if candidate.exists():
                    genome1_file = candidate
                    break
        
        # 如果没有CDS文件但有GFF+Genome，尝试提取
        if not cds1_file and gff1_file and genome1_file:
            cds1_extracted = input_dir / "Ft.cds.fasta"
            try:
                cmd = [
                    "gffread", "-x", str(cds1_extracted), "-g", str(genome1_file), str(gff1_file)
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if cds1_extracted.exists() and cds1_extracted.stat().st_size > 0:
                    cds1_file = cds1_extracted
            except Exception as e:
                print(f"[KaKs] Failed to extract CDS1: {e}")
        
        # 检查必要文件
        missing = []
        if not cds1_file:
            missing.append("Ft CDS (cds1)")
        if not cds2_file:
            missing.append("Ath CDS (cds2)")
        if not pep1_file:
            missing.append("Ft PEP (pep1)")
        if not pep2_file:
            missing.append("Ath PEP (pep2)")
        if not pairs_file:
            missing.append("Ortholog pairs")
        
        if missing:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "mode": "analysis",
                "final_output": {
                    "response": f"❌ Ka/Ks分析缺少必要文件: {', '.join(missing)}\n\n请确保已上传或系统已生成以下文件:\n- Ft.cds.fasta 或 Ft_final.gff3 + Ft.genome.fasta\n- Ath.cds.fasta\n- Ft.pep.fasta\n- Ath.pep.fasta\n- ortholog_pairs.txt (或先运行种间共线性分析)",
                    "missing_files": missing
                }
            }
        
        # 创建kaks输出目录
        kaks_dir = workspace / "kaks"
        kaks_dir.mkdir(parents=True, exist_ok=True)
        
        # 查找基因家族成员列表（用于过滤pairs）
        gene_list1_file = None  # Ft基因家族成员
        gene_list2_file = None  # Ath基因家族成员
        
        print(f"[KaKs] Looking for gene lists in final_dir: {final_dir}")
        print(f"[KaKs] Looking for gene lists in input_dir: {input_dir}")
        print(f"[KaKs] prefix: {prefix}")
        
        # 查找Ft基因家族成员列表 - 优先从final_dir查找
        gene_list1_candidates = [
            final_dir / f"{prefix}.final_members.id",
            final_dir / f"{prefix}.geneID.subfamily",
            input_dir / f"{prefix}.final_members.id",
            input_dir / f"{prefix}.geneID.subfamily",
        ]
        for candidate in gene_list1_candidates:
            print(f"[KaKs] Checking Ft gene list candidate: {candidate}, exists: {candidate.exists()}")
            if candidate.exists():
                gene_list1_file = candidate
                print(f"[KaKs] Found Ft gene list: {gene_list1_file}")
                break
        
        # 查找Ath基因家族成员列表 - 优先从input_dir查找
        gene_list2_candidates = [
            input_dir / "SPL_Ath.subfamily",
            final_dir / "SPL_Ath.subfamily",
            input_dir / "Ath.geneID.subfamily",
        ]
        for candidate in gene_list2_candidates:
            print(f"[KaKs] Checking Ath gene list candidate: {candidate}, exists: {candidate.exists()}")
            if candidate.exists():
                gene_list2_file = candidate
                print(f"[KaKs] Found Ath gene list: {gene_list2_file}")
                break
        
        print(f"[KaKs] gene_list1_file: {gene_list1_file}")
        print(f"[KaKs] gene_list2_file: {gene_list2_file}")
        
        # 构建命令
        cmd = [
            sys.executable, str(PROJECT_ROOT / "cli" / "run_kaks_v2.py"),
            "--output", str(kaks_dir),
            "--prefix-name", prefix,
            "--cds1", str(cds1_file),
            "--cds2", str(cds2_file),
            "--pep1", str(pep1_file),
            "--pep2", str(pep2_file),
            "--pairs", str(pairs_file),
            "--neutral-rate", "7.08e-9"
        ]
        
        # 添加基因家族过滤参数（如果找到）
        if gene_list1_file:
            cmd.extend(["--gene-list1", str(gene_list1_file)])
        if gene_list2_file:
            cmd.extend(["--gene-list2", str(gene_list2_file)])
        
        # 执行分析
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,
                cwd=str(PROJECT_ROOT)
            )
            
            # 检查输出
            final_dir = workspace / "final"
            kaks_manifest = final_dir / f"{prefix}.kaks.manifest.json"
            print(f"[KaKs] Checking manifest: {kaks_manifest}")
            print(f"[KaKs] Manifest exists: {kaks_manifest.exists()}")
            print(f"[KaKs] Return code: {result.returncode}")
            print(f"[KaKs] stdout: {result.stdout[-500:]}")
            print(f"[KaKs] stderr: {result.stderr[-500:]}")
            
            # 检查是否有结果文件生成
            kaks_results = list(final_dir.glob(f"{prefix}.kaks_results.csv"))
            has_results = len(kaks_results) > 0
            print(f"[KaKs] Results files found: {kaks_results}")
            
            if result.returncode == 0 and (kaks_manifest.exists() or has_results):
                manifest = None
                if kaks_manifest.exists():
                    with open(kaks_manifest) as f:
                        manifest = json.load(f)
                    metrics = manifest.get("metrics", {})
                    successful_pairs = metrics.get("successful_pairs", 0)
                    mean_kaks = metrics.get("mean_ka_ks", 0)
                else:
                    # 从CSV文件读取结果
                    successful_pairs = 0
                    mean_kaks = 0
                    if kaks_results:
                        import pandas as pd
                        df = pd.read_csv(kaks_results[0])
                        successful_pairs = len(df)
                        mean_kaks = df['Ka/Ks'].mean() if 'Ka/Ks' in df.columns else 0
                
                return {
                    "current_agent": "analysis",
                    "status": "completed",
                    "mode": "analysis",
                    "final_output": {
                        "response": f"""✅ Ka/Ks分析完成！

**分析结果**:
- 成功分析的基因对: {successful_pairs}
- 平均Ka/Ks比值: {mean_kaks:.4f}
- 工作目录: {workspace}

**输出文件**:
- {prefix}.kaks_results.csv
- {prefix}.kaks_plots.pdf
- {prefix}.kaks.manifest.json
""",
                        "analysis_plan": {
                            "gene_family": gene_family,
                            "species": species,
                            "analysis_type": "kaks",
                            "status": "completed",
                            "workspace": str(workspace),
                            "completed_stage": "kaks",
                        },
                        "execution_result": {"manifest": manifest} if manifest else {}
                    }
                }
            else:
                # 执行失败 - 调用DiagnosisAgent诊断
                error_msg = result.stderr[-1000:] if len(result.stderr) > 1000 else result.stderr
                stdout_msg = result.stdout[-1000:] if len(result.stdout) > 1000 else result.stdout
                
                # 构建错误信息
                error_info = {
                    "stage": "kaks",
                    "message": "Ka/Ks分析失败",
                    "stderr": error_msg,
                    "stdout": stdout_msg,
                    "params": {
                        "prefix": prefix,
                        "cds1": str(cds1_file) if cds1_file else None,
                        "cds2": str(cds2_file) if cds2_file else None,
                        "pep1": str(pep1_file) if pep1_file else None,
                        "pep2": str(pep2_file) if pep2_file else None,
                        "pairs": str(pairs_file) if pairs_file else None,
                    }
                }
                
                # 调用DiagnosisAgent诊断
                full_error = f"{error_msg}\n{stdout_msg}"
                try:
                    diagnosis = await self.diagnosis_agent._diagnose_error(
                        "kaks", full_error, error_info
                    )
                    suggestion = diagnosis.get("suggestion", "请检查错误日志")
                    error_type = diagnosis.get("error_type", "unknown")
                    
                    return {
                        "current_agent": "analysis",
                        "status": "failed",
                        "mode": "analysis",
                        "final_output": {
                            "response": f"❌ Ka/Ks分析失败\n\n**诊断结果**: {error_type}\n\n**建议**: {suggestion}\n\n**错误详情**:\n```\n{error_msg[:500]}\n```",
                            "returncode": result.returncode,
                            "diagnosis": diagnosis
                        }
                    }
                except Exception as diag_e:
                    # 诊断失败，返回原始错误
                    return {
                        "current_agent": "analysis",
                        "status": "failed",
                        "mode": "analysis",
                        "final_output": {
                            "response": f"❌ Ka/Ks分析失败:\n\n```\n{error_msg}\n```",
                            "returncode": result.returncode
                        }
                    }
        except subprocess.TimeoutExpired:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "mode": "analysis",
                "final_output": {
                    "response": "❌ Ka/Ks分析超时（超过1小时）"
                }
            }
        except Exception as e:
            return {
                "current_agent": "analysis",
                "status": "failed",
                "mode": "analysis",
                "final_output": {
                    "response": f"❌ Ka/Ks分析出错: {str(e)}"
                }
            }

    async def run(self, state: GraphState) -> GraphState:
        return await self.graph.ainvoke(state)

    def run_sync(self, state: GraphState) -> GraphState:
        return self.graph.invoke(state)
    
    async def plan(self, state: GraphState) -> dict:
        """
        生成分析计划，不实际执行（V2版本 - 使用LLM驱动）
        
        用于前端展示规划结果，让用户确认后再执行
        """
        try:
            query = state.get("query", "")
            session_id = state.get("session_id", "")
            
            from gfa_core.mas.agents.base import AgentState as V2AgentState, AgentMessage as V2AgentMessage, QueryMode
            
            # Step 1: 使用 IntentAgentV2 识别意图（如果前端已提供则跳过）
            # 检查是否已有前端传来的 intent（避免重复调用）
            existing_intent = state.get("results", {}).get("intent", {})
            if existing_intent and existing_intent.get("stages"):
                # 前端已提供 intent，直接使用
                intent = existing_intent
                print(f"[Orchestrator.plan] Step 1: 使用前端提供的 intent（跳过重复调用）")
                print(f"[Orchestrator.plan] Intent: {intent.get('stages', [])}")
            else:
                # 需要调用 IntentAgentV2 识别意图
                print("[Orchestrator.plan] Step 1: Intent recognition with LLM...")
                intent_message = V2AgentMessage(
                    sender="user",
                    receiver="intent_v2",
                    message_type="request",
                    payload={"query": query}
                )
                
                intent_state = V2AgentState(
                    session_id=session_id,
                    results=state.get("results", {}),
                    mode=QueryMode.ANALYSIS
                )
                
                intent_result_state = await self.intent_agent_v2.execute(intent_state, intent_message)
                intent = intent_result_state.results.get("results", {}).get("intent", {})
            
            if "results" not in state:
                state["results"] = {}
            state["results"]["intent"] = intent
            
            print(f"[Orchestrator.plan] Intent recognized: {intent.get('description', '')}")
            print(f"[Orchestrator.plan] Stages: {intent.get('stages', [])}")
            
            # Step 2: 使用 PlanningAgentV2 生成执行计划
            print("[Orchestrator.plan] Step 2: Planning with LLM...")
            completed_stages = state.get("results", {}).get("completed_stages", [])
            
            planning_message = V2AgentMessage(
                sender="user",
                receiver="planning_v2",
                message_type="request",
                payload={"query": query}
            )
            
            planning_state = V2AgentState(
                session_id=session_id,
                job_id=state.get("job_id", ""),  # 传递 job_id 用于 manifest 查询
                results=state.get("results", {}),
                mode=QueryMode.ANALYSIS
            )
            
            planning_result_state = await self.planning_agent_v2.execute(planning_state, planning_message)
            task_plan = planning_result_state.results.get("task_plan", {})
            
            print(f"[Orchestrator.plan] Plan generated: {task_plan.get('description', '')}")
            
            # 检查 PreFlight 验证是否失败
            if not task_plan.get("valid", True):
                error_msg = task_plan.get("error", "预检验证失败")
                print(f"[Orchestrator.plan] PreFlight validation failed: {error_msg}")
                return {
                    "status": "error",
                    "mode": "orchestrator_v2",
                    "error": error_msg,
                    "final_output": {
                        "response": f"❌ 无法生成执行计划: {error_msg}",
                        "error": error_msg,
                        "analysis_plan": None,
                        "intent": intent
                    }
                }
            
            # 构建返回结果
            stages = task_plan.get("stages", [])
            
            return {
                "status": "completed",
                "mode": "orchestrator_v2",
                "final_output": {
                    "analysis_plan": {
                        "analysis_type": "gene_family",
                        "stages": stages,
                        "stages_detail": [
                            {
                                "id": s.get("id"),
                                "name": s.get("name"),
                                "order": s.get("order"),
                                "dependencies": s.get("dependencies", []),
                                "can_parallel": s.get("can_parallel", False),
                                "estimated_time": s.get("estimated_time", 10),
                                "params": s.get("params", {})
                            }
                            for s in stages
                        ],
                        "total_stages": len(stages),
                        "params": {
                            "gene_family": intent.get("gene_family", ""),
                            "species": intent.get("species", ""),
                            "prefix": intent.get("prefix", ""),
                        },
                        "estimated_time": task_plan.get("estimated_total_time", "未知"),
                        "description": task_plan.get("description", ""),
                        "execution_strategy": task_plan.get("execution_strategy", "parallel"),
                        "max_parallel": task_plan.get("max_parallel", 4),
                        "parallel_groups": task_plan.get("parallel_groups", [])
                    },
                    "intent": intent
                }
            }
        except Exception as e:
            import traceback
            print(f"[Orchestrator.plan] Error: {e}")
            traceback.print_exc()
            # 降级到旧版本
            return await self._plan_legacy(state)
    
    async def _plan_legacy(self, state: GraphState) -> dict:
        """降级：使用旧版PlanningAgent"""
        try:
            query = state.get("query", "")
            session_id = state.get("session_id", "")
            
            from gfa_core.mas.agents.base import AgentState, AgentMessage as BaseAgentMessage, QueryMode
            
            intent = state.get("results", {}).get("intent", {})
            if not intent:
                intent = self._parse_intent_from_query(query)
                if "results" not in state:
                    state["results"] = {}
                state["results"]["intent"] = intent
            
            message = BaseAgentMessage(
                sender="user",
                receiver="planning",
                message_type="request",
                payload={"query": query}
            )
            
            agent_state = AgentState(
                session_id=session_id,
                results=state.get("results", {}),
                mode=QueryMode.ANALYSIS
            )
            
            result_state = await self.planning_agent.execute(agent_state, message)
            task_plan = result_state.results.get("task_plan", {})
            planning_info = result_state.results.get("planning_info", {})
            
            return {
                "status": "completed",
                "mode": "legacy",
                "final_output": {
                    "analysis_plan": {
                        "analysis_type": task_plan.get("flow_type", "gene_family"),
                        "stages": task_plan.get("stages", []),
                        "params": {
                            "gene_family": planning_info.get("gene_family", ""),
                            "species": planning_info.get("species", ""),
                        },
                        "estimated_time": task_plan.get("estimated_time", "未知"),
                        "description": task_plan.get("description", "")
                    }
                }
            }
        except Exception as e:
            return {
                "status": "failed",
                "final_output": {"error": str(e)}
            }
    
    def _parse_intent_from_query(self, query: str) -> dict:
        """从查询中解析意图"""
        query_lower = query.lower()
        
        # 提取基因家族
        gene_family = ""
        patterns = [
            r'(\w+)\s*基因家族',
            r'基因家族\s*(\w+)',
        ]
        for pattern in patterns:
            import re
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                gene_family = match.group(1).upper()
                break
        
        if not gene_family:
            # 检查常见基因家族名称
            common_families = ['SPL', 'NAC', 'WRKY', 'MYB', 'bHLH', 'WD40']
            for fam in common_families:
                if fam.lower() in query_lower:
                    gene_family = fam
                    break
        
        # 提取物种
        species = ""
        if 'ft' in query_lower or 'tartary' in query_lower or '苦荞' in query:
            species = "Ft"
        elif 'at' in query_lower or 'arabidopsis' in query_lower or '拟南芥' in query:
            species = "At"
        elif 'os' in query_lower or 'rice' in query_lower or '水稻' in query:
            species = "Os"
        
        # 确定分析阶段
        stage = ""
        if 'identify' in query_lower or '鉴定' in query:
            stage = "identify"
        elif 'phylogeny' in query_lower or '进化' in query or '树' in query:
            stage = "phylogeny"
        elif 'motif' in query_lower or '基序' in query:
            stage = "motif"
        elif 'domain' in query_lower or '结构域' in query:
            stage = "domain"
        elif 'expression' in query_lower or '表达' in query:
            stage = "expression"
        # 种间共线性优先：中文「共线性」会命中「种间共线性」「物种间共线性」
        elif (
            'interspecies' in query_lower
            or '种间' in query
            or '跨物种' in query
            or '物种间共线性' in query
        ):
            stage = "interspecies_synteny"
        elif 'synteny' in query_lower or '共线性' in query:
            stage = "synteny"
        
        return {
            "gene_family": gene_family or "SPL",
            "species": species or "Ft",
            "stage": stage or "identify",
            "task_type": "analysis"
        }
