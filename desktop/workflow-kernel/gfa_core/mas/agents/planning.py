"""
PlanningAgent V2 - 增强版任务规划
支持根据用户意图生成合理的分析流程
集成Knowledge Agent获取参数推荐
"""
import json
import re
from typing import Dict, List, Optional
from .base import AgentMessage, AgentState, BaseAgent
from gfa_core.mas.llm import LLMManager


class PlanningAgent(BaseAgent):
    """
    任务规划Agent V2
    
    根据用户意图和可用资源，生成最优的分析执行计划
    """
    name = "planning"
    role = "Workflow Planning Specialist"
    
    # 标准分析流程定义
    STANDARD_FLOWS = {
        "prepare": ["genome_prepare"],
        "basic": ["identify"],
        "identify_refine": ["identify_refine"],
        "phylogeny": ["identify", "phylogeny"],
        "structure": ["identify", "phylogeny", "motif", "domain", "structure"],
        "promoter": ["identify", "phylogeny", "promoter"],
        "synteny": ["identify", "phylogeny", "synteny"],
        "karyotype": ["identify", "phylogeny", "karyotype"],
        "interspecies": ["identify", "phylogeny", "interspecies_synteny"],
        "expression": ["identify", "phylogeny", "expression"],
        "complete": [
            "identify", "phylogeny", "motif", "domain", 
            "structure", "promoter", "synteny", "expression"
        ],
        "rnaseq": [
            "rnaseq_qc",
            "rnaseq_align",
            "rnaseq_quant",
            "rnaseq_sample_qc",
            "rnaseq_de",
            "rnaseq_go_enrich",
            "rnaseq_kegg_enrich",
            "rnaseq_fgsea",
            "rnaseq_wgcna",
        ],
        "evolution": ["identify", "phylogeny", "kaks"],
        "interaction": ["identify", "ppi"]
    }
    
    # 阶段依赖关系
    STAGE_DEPENDENCIES = {
        "genome_prepare": [],
        "identify_refine": [],
        "phylogeny": ["identify"],
        "motif": ["identify"],
        "domain": ["identify"],
        "structure": ["identify"],
        "promoter": ["identify", "phylogeny"],
        "synteny": ["identify"],
        "karyotype": ["identify"],
        "interspecies_synteny": ["identify"],
        "expression": ["identify"],
        "kaks": ["identify", "phylogeny"],
        "ppi": ["identify"],
        "rnaseq_align": ["rnaseq_qc"],
        "rnaseq_quant": ["rnaseq_align"],
        "rnaseq_sample_qc": ["rnaseq_quant"],
        "rnaseq_wgcna": ["rnaseq_sample_qc"],
        "rnaseq_de": ["rnaseq_sample_qc"],
        "rnaseq_go_enrich": ["rnaseq_de"],
        "rnaseq_kegg_enrich": ["rnaseq_de"],
        "rnaseq_fgsea": ["rnaseq_de"],
        "rnaseq_enrich": ["rnaseq_de"],
        "rnaseq_candidate_family": ["rnaseq_de", "rnaseq_go_enrich", "rnaseq_kegg_enrich", "rnaseq_fgsea"]
    }
    
    # 阶段描述
    STAGE_DESCRIPTIONS = {
        "genome_prepare": "数据准备总流程 - 多物种文件整理、GFF/FASTA标准化、最长转录本和参考序列合并",
        "identify_refine": "修订鉴定成员 - 根据人工修订ID列表重建final_members.id和final_members.pep.fa",
        "identify": "基因家族鉴定 - 使用HMM和BLAST识别基因家族成员",
        "phylogeny": "系统发育分析 - 构建进化树和亚家族分类",
        "motif": "Motif分析 - 识别保守的蛋白基序",
        "domain": "结构域分析 - 分析蛋白结构域组成",
        "structure": "结构预测 - 预测蛋白三维结构",
        "promoter": "启动子分析 - 分析顺式作用元件",
        "synteny": "共线性分析 - 分析基因组共线性关系",
        "karyotype": "核型分析 - 生成染色体分布图",
        "interspecies_synteny": "物种间共线性 - 跨物种比较分析",
        "expression": "表达分析 - 分析基因表达模式",
        "kaks": "Ka/Ks分析 - 计算进化速率和选择压力",
        "ppi": "PPI分析 - 蛋白互作网络分析",
        "rnaseq_qc": "RNA-seq质控 - FastQC质量评估",
        "rnaseq_align": "RNA-seq比对 - reads比对到基因组",
        "rnaseq_quant": "RNA-seq定量 - 基因表达量计算",
        "rnaseq_sample_qc": "RNA-seq样本质量评估 - VST、PCA、样本相关性与聚类",
        "rnaseq_wgcna": "RNA-seq WGCNA - 加权共表达网络与模块性状相关性",
        "rnaseq_de": "差异表达 - DESeq2差异分析",
        "rnaseq_go_enrich": "RNA-seq GO富集分析 - 独立使用 gene2go 背景",
        "rnaseq_kegg_enrich": "RNA-seq KEGG富集分析 - 独立使用 gene2ko/KO 背景",
        "rnaseq_fgsea": "RNA-seq fgsea - 基于排序基因和 KEGG 基因集的 GSEA",
        "rnaseq_enrich": "RNA-seq富集分析 - 兼容旧入口；规划中优先使用 GO/KEGG 子阶段"
    }
    
    def __init__(self, llm_manager: LLMManager):
        super().__init__(llm_manager)
    
    async def execute(self, state: AgentState, message: AgentMessage) -> AgentState:
        """
        执行任务规划
        
        根据用户意图生成分析执行计划
        """
        query = message.payload.get("query", "")
        intent = state.results.get("results", {}).get("intent", {}) or state.results.get("intent", {})
        
        # 提取关键信息
        gene_family = intent.get("gene_family", "") or self._extract_gene_family(query)
        species = intent.get("species", "") or self._extract_species(query)
        stage = intent.get("stage", "")
        
        # 确定分析流程
        flow = self._determine_flow(query, stage)
        
        # 生成执行计划
        plan = await self._generate_plan(query, gene_family, species, flow)
        
        # 更新状态
        state.results["task_plan"] = plan
        state.results["planning_info"] = {
            "gene_family": gene_family,
            "species": species,
            "flow_type": flow,
            "total_stages": len(plan.get("stages", []))
        }
        
        state.messages.append({
            "role": "planning",
            "content": f"📋 已生成分析计划: {len(plan.get('stages', []))}个阶段",
        })
        
        return state
    
    def _extract_gene_family(self, query: str) -> str:
        """从查询中提取基因家族名称"""
        # 常见基因家族模式
        patterns = [
            r'(\w+)\s*基因家族',
            r'基因家族\s*(\w+)',
            r'\b(SPL|NAC|WRKY|MYB|bHLH|WD40|C2H2|C3H|NBS|AP2|ARF|AUX|B3|BZIP|C2C2|CCAAT|CO-like|CPP|CSD|CTF|CYP|Dof|E2F|ERF|FAR1|G2-like|GATA|GeBP|GRAS|GRF|HB|HMA|HMG|HRT|HSF|JUMONJI|KNOX|LBD|LFY|LIM|LOB|LSD|MADS|MULE|MYB|NAC|ND|Nin|NLP|NPR|OFP|Orphan|Ots|PHD|PLATZ|PPR|Pseudo|RWP|S1Fa|SAP|SBP|Sigma|SMC|SRS|TALE|TCP|Tify|TRAF|Trihelix|TST|TUB|ULT|VOZ|Whirly|WOX|WRKY|XAL2|YABBY|ZF)\b'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                return match.group(1).upper()
        
        return "SPL"  # 默认
    
    def _extract_species(self, query: str) -> str:
        """从查询中提取物种信息"""
        species_map = {
            'ft': 'Ft', 'tartary': 'Ft', '苦荞': 'Ft',
            'at': 'At', 'arabidopsis': 'At', '拟南芥': 'At',
            'os': 'Os', 'rice': 'Os', '水稻': 'Os',
            'zm': 'Zm', 'maize': 'Zm', '玉米': 'Zm',
            'gm': 'Gm', 'soybean': 'Gm', '大豆': 'Gm',
            'sl': 'Sl', 'tomato': 'Sl', '番茄': 'Sl'
        }
        
        query_lower = query.lower()
        for key, value in species_map.items():
            if key in query_lower:
                return value
        
        return "Ft"  # 默认
    
    def _determine_flow(self, query: str, stage: str) -> str:
        """确定分析流程类型"""
        query_lower = query.lower()
        
        # RNA-seq关键词
        if any(kw in query_lower for kw in ['rnaseq', 'rna-seq', '转录组', '表达谱']):
            return "rnaseq"
        
        # 进化分析关键词
        if any(kw in query_lower for kw in ['kaks', 'ka/ks', '进化', '选择压力']):
            return "evolution"
        
        # PPI关键词
        if any(kw in query_lower for kw in ['ppi', '互作', 'interaction', 'string']):
            return "interaction"
        
        # 根据明确指定的stage
        if stage:
            flow_map = {
                "identify": "basic",
                "phylogeny": "phylogeny",
                "motif": "structure",
                "domain": "structure",
                "structure": "structure",
                "promoter": "promoter",
                "synteny": "synteny",
                "karyotype": "karyotype",
                "interspecies_synteny": "interspecies",
                "expression": "expression",
                "kaks": "evolution",
                "ppi": "interaction"
            }
            return flow_map.get(stage, "complete")
        
        # 完整分析关键词
        if any(kw in query_lower for kw in ['完整', '全套', '全部', 'complete', 'full']):
            return "complete"
        
        return "complete"  # 默认完整分析
    
    async def _generate_plan(self, query: str, gene_family: str, 
                           species: str, flow: str) -> dict:
        """生成详细执行计划"""
        
        # 获取流程阶段列表
        stages = self.STANDARD_FLOWS.get(flow, self.STANDARD_FLOWS["complete"])
        
        # 构建阶段详情
        stage_details = []
        for i, stage_id in enumerate(stages):
            stage_info = {
                "id": stage_id,
                "name": self.STAGE_DESCRIPTIONS.get(stage_id, stage_id),
                "description": self.STAGE_DESCRIPTIONS.get(stage_id, ""),
                "order": i + 1,
                "dependencies": self.STAGE_DEPENDENCIES.get(stage_id, []),
                "params": await self._get_default_params(stage_id, gene_family, species)
            }
            stage_details.append(stage_info)
        
        # 构建执行计划
        plan = {
            "query": query,
            "gene_family": gene_family,
            "species": species,
            "flow_type": flow,
            "prefix": f"{species}.{gene_family}",
            "stages": stage_details,
            "total_stages": len(stages),
            "estimated_time": self._estimate_time(stages),
            "required_inputs": self._get_required_inputs(stages),
            "outputs": self._get_outputs(stages, gene_family, species)
        }
        
        return plan
    
    async def _get_default_params(self, stage_id: str, gene_family: str, species: str) -> dict:
        """获取阶段的默认参数，并尝试从Knowledge Agent获取推荐"""
        default_params = {
            "identify": {"cpu": 8, "dom_i_evalue_max": 0.001, "hmm_cov_min": 0.3, "blast_evalue": "1e-05"},
            "phylogeny": {"cpu": 8, "bootstrap": 1000, "model": "MFP"},
            "motif": {"cpu": 8, "nmotifs": 10, "min_width": 6, "max_width": 200},
            "domain": {"cpu": 8, "e_value": 1e-5},
            "structure": {"cpu": 8},
            "promoter": {"cpu": 8},
            "synteny": {"cpu": 8},
            "karyotype": {"cpu": 8},
            "interspecies_synteny": {"cpu": 8},
            "expression": {"cpu": 8},
            "kaks": {"cpu": 8, "model": "YN00"},
            "ppi": {"species": "ath", "score": 200},
            "rnaseq_qc": {"cpu": 8},
            "rnaseq_align": {"cpu": 8},
            "rnaseq_quant": {"cpu": 8},
            "rnaseq_de": {"cpu": 8},
            "rnaseq_enrich": {"cpu": 8, "top_pathways": 20}
        }
        
        params = default_params.get(stage_id, {"cpu": 8}).copy()
        params["prefix"] = f"{species}.{gene_family}"
        
        # 尝试从 Knowledge Agent 获取参数推荐
        try:
            recommended = await self._get_params_from_knowledge(stage_id, gene_family, species)
            if recommended:
                # 合并推荐参数（推荐参数优先）
                params.update(recommended)
                print(f"[PlanningAgent] 从Knowledge获取到{stage_id}阶段参数推荐")
        except Exception as e:
            print(f"[PlanningAgent] Knowledge参数推荐失败: {e}，使用默认参数")
        
        return params
    
    async def _get_params_from_knowledge(self, stage_id: str, gene_family: str, species: str) -> dict:
        """从Knowledge Agent获取参数推荐"""
        from .knowledge import create_knowledge_agent
        from gfa_core.mas.llm import LLMManager
        
        # 创建 Knowledge Agent
        llm_manager = LLMManager()
        knowledge_agent = create_knowledge_agent(llm_manager)
        
        # 构建查询
        query = f"推荐{stage_id}阶段的分析参数，基因家族：{gene_family}，物种：{species}"
        
        # 构建消息
        from .base import AgentMessage, AgentState
        import uuid
        
        message = AgentMessage(
            sender="planning",
            receiver="knowledge",
            message_type="query",
            payload={"query": query, "mode": "consultation"}
        )
        
        state = AgentState(
            session_id=f"planning_{uuid.uuid4().hex[:8]}",
            results={},
            messages=[]
        )
        
        # 执行查询
        result_state = await knowledge_agent.execute(state, message)
        
        # 解析推荐参数
        answer = result_state.results.get("knowledge_response", "")
        
        # 尝试从回答中提取参数（简化版）
        recommended = {}
        if "cpu" in answer.lower():
            # 提取CPU推荐
            import re
            cpu_match = re.search(r'(\d+)\s*核', answer)
            if cpu_match:
                recommended["cpu"] = int(cpu_match.group(1))
        
        return recommended
    
    def _estimate_time(self, stages: list) -> str:
        """估算总执行时间"""
        time_map = {
            "identify": 5, "phylogeny": 30, "motif": 20, "domain": 15,
            "structure": 30, "promoter": 20, "synteny": 30, "karyotype": 10,
            "interspecies_synteny": 40, "expression": 15, "kaks": 20, "ppi": 10,
            "rnaseq_qc": 30, "rnaseq_align": 60, "rnaseq_quant": 30,
            "rnaseq_sample_qc": 10, "rnaseq_de": 20, "rnaseq_enrich": 15,
            "rnaseq_go_enrich": 15, "rnaseq_kegg_enrich": 15,
            "rnaseq_fgsea": 10, "rnaseq_wgcna": 30
        }
        
        total_minutes = sum(time_map.get(s, 10) for s in stages)
        
        if total_minutes < 60:
            return f"约{total_minutes}分钟"
        else:
            hours = total_minutes // 60
            mins = total_minutes % 60
            return f"约{hours}小时{mins}分钟" if mins > 0 else f"约{hours}小时"
    
    def _get_required_inputs(self, stages: list) -> list:
        """获取所需输入文件"""
        inputs = []
        if "identify" in stages:
            inputs.extend([
                {"name": "HMM模型文件", "type": "hmm", "required": True, "example": "SPL_Ath.hmm"},
                {"name": "蛋白质序列", "type": "fasta", "required": True, "example": "Ft.pep.fasta"}
            ])
        if any(s.startswith("rnaseq") for s in stages):
            inputs.extend([
                {"name": "FASTQ文件", "type": "fastq", "required": True, "example": "*.fastq.gz"},
                {"name": "样本配置", "type": "yaml", "required": True, "example": "samples.yaml"}
            ])
        return inputs
    
    def _get_outputs(self, stages: list, gene_family: str, species: str) -> list:
        """获取预期输出"""
        prefix = f"{species}.{gene_family}"
        outputs = []
        output_map = {
            "identify": [{"name": "最终成员列表", "file": f"{prefix}.final_members.id"}],
            "phylogeny": [{"name": "进化树", "file": f"{prefix}.tree.nwk"}],
            "kaks": [{"name": "Ka/Ks结果", "file": f"{prefix}.kaks_results.csv"}],
            "ppi": [{"name": "PPI网络", "file": f"{prefix}.ppi_network.tsv"}]
        }
        for stage in stages:
            if stage in output_map:
                outputs.extend(output_map[stage])
        return outputs


def create_planning_agent(llm_manager):
    """创建PlanningAgent实例"""
    return PlanningAgent(llm_manager)
