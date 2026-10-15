"""
LangGraph Orchestrator - bbav5 新版本
基于 LangGraph 的 9-Agent 工作流编排
"""
from typing import Dict, List, Optional, Any, TypedDict, Annotated
from pathlib import Path
from enum import Enum
import asyncio
import json

from langgraph.graph import StateGraph, END

from gfa_core.mas.llm import LLMManager
from gfa_core.mas.agents import (
    RouterAgent, ExecutionAgent,
    KnowledgeAgent, DiagnosisAgent,
    ResultParserAgent, DiscoveryAgent,
    AgentState, AgentMessage, QueryMode
)
from gfa_core.mas.agents.planning_v2 import PlanningAgentV2
from gfa_core.mas.agents.intent_v2 import IntentAgentV2
from gfa_core.mas.tools.stage_tools import create_stage_registry
from gfa_core.project_paths import PROJECT_ROOT


class WorkflowMode(Enum):
    """工作流模式"""
    FAQ = "faq"                    # 知识问答
    CONSULTATION = "consultation"  # 咨询建议
    ANALYSIS = "analysis"          # 分析任务
    INTERPRETATION = "interpretation"  # 结果解读


class GraphState(TypedDict):
    """LangGraph 状态定义"""
    # 基础信息
    session_id: str
    job_id: str
    query: str
    mode: str
    
    # 执行状态
    status: str  # pending, running, completed, failed
    current_agent: str
    current_step: int
    
    # 数据
    user_input: Dict[str, Any]
    files: List[Dict[str, Any]]
    messages: List[Dict]  # 简化消息存储
    
    # Agent 结果
    intent_result: Dict[str, Any]      # IntentAgentV2 结果
    plan_result: Dict[str, Any]        # PlanningAgent 结果
    execution_result: Dict[str, Any]   # ExecutionAgent 结果（单条）
    execution_results: List[Dict]      # ExecutionAgent 结果列表（用于诊断）
    knowledge_result: Dict[str, Any]   # KnowledgeAgent 结果
    diagnosis_result: Dict[str, Any]   # DiagnosisAgent 结果
    validation_result: Dict[str, Any]  # 验证结果（已合并到ResultParser）
    parse_result: Dict[str, Any]       # ResultParserAgent 结果
    learn_result: Dict[str, Any]       # Learning结果（已合并到Knowledge）
    discovery_result: Dict[str, Any]   # DiscoveryAgent 结果
    
    # 输出
    final_output: Dict[str, Any]
    
    # 错误处理
    errors: List[str]
    retry_count: int
    max_retries: int
    last_error: str  # 上次错误信息（用于诊断循环）
    
    # 已完成阶段
    completed_stages: List[str]  # 已完成的阶段列表
    
    # Learning推荐
    learning_recommendations: Dict[str, Any]  # Learning Agent的参数推荐
    
    # 迭代控制
    iteration_count: int         # 当前迭代次数（全局计数）
    max_iterations: int          # 最大迭代次数
    discoveries: List[Dict]      # 发现历史
    
    # 解读模式配置
    interpretation_mode: str     # "quick" (快速), "deep" (深度), "comprehensive" (综合)
    stage_interpretations: Dict  # 各阶段解读结果存储
    
    # 流程控制
    next_node: str               # 下一个节点（用于条件路由）
    knowledge_mode: str          # Knowledge Agent模式（faq/consultation/feasibility/report_enhance）
    pending_confirmation: Dict   # 等待用户确认的状态
    
    # REPLAN控制
    replan_count: int            # REPLAN次数计数
    max_replan: int              # 最大REPLAN次数


class LangGraphOrchestrator:
    """
    LangGraph 编排器 - 支持13种流程
    
    支持的流程：
    1. 正常流程：Router→Intent→Planning→Execution→ResultParser→Discovery→Learning
    2. FAQ流程：Router→Knowledge→END
    3. CONSULTATION流程：Router→Knowledge→END
    4. FEASIBILITY流程：Router→Knowledge→END
    5. REPORT_ENHANCE流程：Router→Knowledge→END
    6. 失败+自动修复：Execution→Diagnosis→Execution（循环，最多3次）
    7. 失败+不可修复：Execution→Diagnosis→END
    8. REPLAN流程：Discovery→Planning（循环，最多3次）
    
    全局状态跟踪：
    - iteration_count: 当前迭代次数
    - max_iterations: 3
    - last_error: 上次错误信息
    - replan_count: REPLAN次数
    - max_replan: 3
    """
    
    def __init__(self, llm_manager: LLMManager):
        self.llm_manager = llm_manager
        
        # 创建 StageToolRegistry 用于执行分析任务
        self.stage_registry = create_stage_registry()
        
        # 初始化 9 个 Agent
        self.router_agent = RouterAgent(llm_manager)
        self.intent_agent_v2 = IntentAgentV2(llm_manager)  # 添加 IntentAgentV2
        self.planning_agent = PlanningAgentV2(llm_manager)  # 使用 V2 版本（已合并PreFlight）
        self.execution_agent = ExecutionAgent(llm_manager, self.stage_registry)
        self.knowledge_agent = KnowledgeAgent(llm_manager)  # 已合并Learning
        self.diagnosis_agent = DiagnosisAgent(llm_manager)
        self.result_parser_agent = ResultParserAgent(llm_manager)  # 已合并Validator+Analysis
        self.discovery_agent = DiscoveryAgent(llm_manager)
        
        # 构建图
        self.graph = self._build_graph()
        print("[LangGraphOrchestrator] 7个Agent初始化完成（合并后）")
    
    def _build_graph(self) -> StateGraph:
        """构建 LangGraph 工作流 - 支持13种流程"""
        
        # 创建状态图
        workflow = StateGraph(GraphState)
        
        # ========== 添加所有节点 ==========
        workflow.add_node("router", self._router_node)
        workflow.add_node("intent", self._intent_node)  # 新增：独立Intent节点
        workflow.add_node("knowledge", self._knowledge_node)
        workflow.add_node("learning_recommend", self._learning_recommend_node)
        workflow.add_node("planning", self._planning_node)
        workflow.add_node("preflight", self._preflight_node)
        workflow.add_node("execution", self._execution_node)
        workflow.add_node("validator", self._validator_node)
        workflow.add_node("diagnosis", self._diagnosis_node)
        workflow.add_node("result_parser", self._result_parser_node)
        workflow.add_node("discovery", self._discovery_node)
        workflow.add_node("learning", self._learning_node)
        
        # 设置入口点
        workflow.set_entry_point("router")
        
        # ========== Router 条件分支 ==========
        # 根据mode决定流程分支
        workflow.add_conditional_edges(
            "router",
            self._route_decision,
            {
                "knowledge": "knowledge",           # FAQ/CONSULTATION/INTERPRETATION
                "feasibility": "knowledge",         # FEASIBILITY（特殊knowledge_mode）
                "report_enhance": "knowledge",      # REPORT_ENHANCE（特殊knowledge_mode）
                "intent": "intent",                 # ANALYSIS模式 -> Intent -> Planning流程
                "end": END
            }
        )
        
        # ========== Intent 节点 ==========
        # Intent -> Learning推荐（ANALYSIS模式）
        workflow.add_edge("intent", "learning_recommend")
        
        # ========== Learning推荐节点 ==========
        workflow.add_edge("learning_recommend", "planning")
        
        # ========== Knowledge 节点（多种模式） ==========
        # Knowledge -> END（所有Knowledge模式都直接结束）
        workflow.add_edge("knowledge", END)
        
        # ========== Planning 节点 ==========
        workflow.add_edge("planning", "preflight")
        
        # ========== PreFlight 节点 ==========
        workflow.add_edge("preflight", "execution")
        
        # ========== Execution 节点 ==========
        workflow.add_conditional_edges(
            "execution",
            self._execution_decision,
            {
                "diagnosis": "diagnosis",
                "result_parser": "result_parser",
            }
        )
        
        # ========== ResultParser 节点 ==========
        workflow.add_edge("result_parser", "validator")
        
        # ========== Validator 条件分支 ==========
        # 无论验证成功或失败，都先到 Discovery 进行科学发现
        # Discovery 会根据结果生成假设或建议
        workflow.add_edge("validator", "discovery")
        
        # ========== Diagnosis 条件分支（修复循环） ==========
        workflow.add_conditional_edges(
            "diagnosis",
            self._diagnosis_decision,
            {
                "retry": "execution",           # 自动修复 -> 重试Execution（循环）
                "waiting_confirmation": END,    # 等待用户确认
                "skip": "discovery",            # 跳过错误 -> Discovery
                "fail": END                     # 无法修复 -> 结束
            }
        )
        
        # ========== Discovery 条件分支（REPLAN循环） ==========
        # Discovery 后，根据验证结果决定下一步
        workflow.add_conditional_edges(
            "discovery",
            self._discovery_after_validation_decision,
            {
                "diagnosis": "diagnosis",   # 验证失败 -> Diagnosis（修复）
                "learning": "learning",     # 验证成功 -> Learning（完成）
                "replan": "planning",       # 需要重新规划 -> Planning（循环）
                "end": END                  # 强制结束
            }
        )
        
        # ========== Learning 节点 ==========
        workflow.add_edge("learning", END)
        
        return workflow.compile()
    
    async def run(self, state: GraphState) -> GraphState:
        """运行工作流"""
        return await self.graph.ainvoke(state)
    
    # ============ 节点函数 ============
    
    async def _router_node(self, state: GraphState) -> GraphState:
        """Router Agent 节点 - 基础意图分类
        
        流程1-5: Router根据mode决定分支
        - FAQ/CONSULTATION/INTERPRETATION -> Knowledge
        - FEASIBILITY -> Knowledge (特殊模式)
        - REPORT_ENHANCE -> Knowledge (特殊模式)
        - ANALYSIS -> Intent -> Planning流程
        """
        print(f"[Router] 处理查询: {state['query'][:50]}...")
        
        # 使用 RouterAgent 进行基础分类
        router_message = AgentMessage(
            sender="user",
            receiver="router",
            message_type="classify",
            payload={"query": state["query"]}
        )
        
        agent_state = self._to_agent_state(state)
        router_result = await self.router_agent.execute(agent_state, router_message)
        
        # 更新基础状态
        state["mode"] = router_result.mode.value
        state["current_agent"] = "router"
        
        # 设置下一个节点
        mode_value = router_result.mode.value
        if mode_value == "analysis":
            # 流程1: 正常分析流程 -> Intent节点
            state["next_node"] = "intent"
        elif mode_value in ["faq", "consultation", "interpretation"]:
            # 流程2-3: FAQ/CONSULTATION流程 -> Knowledge
            state["next_node"] = "knowledge"
            state["knowledge_mode"] = mode_value
        elif mode_value == "feasibility":
            # 流程4: FEASIBILITY流程 -> Knowledge (特殊处理)
            state["next_node"] = "knowledge"
            state["knowledge_mode"] = "feasibility"
        elif mode_value == "report_enhance":
            # 流程5: REPORT_ENHANCE流程 -> Knowledge (特殊处理)
            state["next_node"] = "knowledge"
            state["knowledge_mode"] = "report_enhance"
        else:
            state["next_node"] = "end"
            
        print(f"[Router] Mode: {mode_value}, Next: {state['next_node']}")
        return state
    
    async def _intent_node(self, state: GraphState) -> GraphState:
        """Intent Agent 节点 - 详细意图识别
        
        流程1: Router→Intent→Planning→Execution→ResultParser→Discovery→Learning
        """
        print(f"[Intent] 详细意图识别: {state['query'][:50]}...")
        print(f"[Intent] completed_stages in state: {state.get('completed_stages', [])}")
        
        # 检查是否为单stage模式
        user_input = state.get("user_input", {})
        single_stage_mode = user_input.get("stage") is not None
        
        job_id = state.get("job_id", "")
        workspace = str(PROJECT_ROOT / "workspace" / job_id) if job_id else ""
        
        if single_stage_mode:
            # 单stage模式：直接使用指定的stage
            print(f"[Intent] 单stage模式: target={user_input.get('stage')}")
            intent_details = {
                "stages": [user_input.get("stage")],
                "gene_family": user_input.get("prefix", "").split(".")[-1] if "." in user_input.get("prefix", "") else "",
                "species": user_input.get("prefix", "").split(".")[0] if "." in user_input.get("prefix", "") else "",
                "prefix": user_input.get("prefix", ""),
                "params": user_input.get("params", {}) or {},
                "single_stage_mode": True,
                "job_id": job_id,
                "workspace": workspace
            }
        else:
            # 正常模式：使用IntentAgentV2识别意图
            user_prefix = user_input.get("prefix", "")
            
            intent_message = AgentMessage(
                sender="router",
                receiver="intent_v2",
                message_type="request",
                payload={
                    "query": state["query"],
                    "job_id": job_id,
                    "workspace": workspace,
                    "completed_stages": state.get("completed_stages", []),
                    "prefix": user_prefix
                }
            )
            
            agent_state = self._to_agent_state(state)
            intent_result_state = await self.intent_agent_v2.execute(agent_state, intent_message)
            intent_details = intent_result_state.results.get("results", {}).get("intent", {})
            
            # 确保包含必要字段
            intent_details["job_id"] = job_id
            intent_details["workspace"] = workspace
        
        print(f"[Intent] Result: {intent_details}")
        
        # 保存意图结果
        state["intent_result"] = {
            "mode": "analysis",
            **intent_details
        }
        state["current_agent"] = "intent"
        
        return state
    
    async def _knowledge_node(self, state: GraphState) -> GraphState:
        """Knowledge Agent 节点 - 知识检索（支持多种功能模式）"""
        print(f"[Knowledge] 检索知识: {state['query'][:50]}...")
        
        # 根据Router设置的mode选择功能
        knowledge_mode = state.get("knowledge_mode", "rag")
        
        # 构建payload
        payload = {"query": state["query"], "mode": knowledge_mode}
        
        # 如果是报告增强模式，传递分析结果
        if knowledge_mode == "report_enhance":
            payload["analysis_results"] = state.get("parse_result", {})
        
        message = AgentMessage(
            sender="router",
            receiver="knowledge",
            message_type="query",
            payload=payload
        )
        
        agent_state = self._to_agent_state(state)
        result_state = await self.knowledge_agent.execute(agent_state, message)
        
        # 根据模式处理结果
        if knowledge_mode == "feasibility":
            state["knowledge_result"] = {
                "answer": result_state.results.get("knowledge_response", ""),
                "plan": result_state.results.get("feasibility_plan", {}),
                "sources": result_state.results.get("knowledge_sources", [])
            }
            state["final_output"] = {
                "response": result_state.results.get("knowledge_response", ""),
                "plan": result_state.results.get("feasibility_plan", {}),
                "sources": result_state.results.get("knowledge_sources", [])
            }
        elif knowledge_mode == "report_enhance":
            state["knowledge_result"] = {
                "answer": result_state.results.get("knowledge_response", ""),
                "report": result_state.results.get("enhanced_report", {}),
                "sources": result_state.results.get("knowledge_sources", [])
            }
            state["final_output"] = {
                "response": result_state.results.get("knowledge_response", ""),
                "report": result_state.results.get("enhanced_report", {}),
                "sources": result_state.results.get("knowledge_sources", [])
            }
        else:
            state["knowledge_result"] = {
                "answer": result_state.results.get("knowledge_response", ""),
                "sources": result_state.results.get("knowledge_sources", [])
            }
            state["final_output"] = {
                "response": result_state.results.get("knowledge_response", ""),
                "sources": result_state.results.get("knowledge_sources", [])
            }
        
        state["current_agent"] = "knowledge"
        state["status"] = "completed"
        
        return state
    
    def _query_completed_stages_from_manifest(self, job_id: str) -> List[str]:
        """从统一阶段状态模块查询已完成阶段。"""
        from gfa_core.mas.tools.stage_status import list_completed_stages

        completed_stages = list_completed_stages(job_id=job_id)
        print(f"[DEBUG] Unified completed_stages for job_id={job_id}: {completed_stages}")
        return completed_stages
    
    async def _learning_recommend_node(self, state: GraphState) -> GraphState:
        """Learning Agent 节点 - 在Planning前获取参数建议"""
        print(f"[LearningRecommend] 获取参数推荐...")
        
        # 获取目标阶段
        intent = state.get("intent_result", {})
        target_stages = intent.get("stages", ["identify"])
        
        # 为每个阶段获取推荐
        recommendations = {}
        for stage in target_stages:
            # 调用Knowledge Agent获取参数推荐
            message = AgentMessage(
                sender="router",
                receiver="knowledge",
                message_type="recommend",
                payload={"mode": "recommend_params", "stage": stage}
            )
            
            agent_state = self._to_agent_state(state)
            result_state = await self.knowledge_agent.execute(agent_state, message)
            
            # 获取推荐结果
            param_rec = result_state.results.get("param_recommendations", {})
            if isinstance(param_rec, dict) and param_rec:
                recommendations[stage] = param_rec
            else:
                # 使用默认参数
                recommendations[stage] = {
                    "confidence": "low",
                    "params": {"cpu": 8},
                    "message": "使用默认参数"
                }
        
        state["learning_recommendations"] = recommendations
        
        # 记录日志
        for stage, rec in recommendations.items():
            confidence = rec.get("confidence", "low")
            if confidence == "high":
                print(f"[LearningRecommend] ✅ {stage}: 高置信度推荐")
            elif confidence == "medium":
                print(f"[LearningRecommend] ⚠️ {stage}: 中置信度推荐")
            else:
                print(f"[LearningRecommend] ℹ️ {stage}: 使用默认参数")
        
        state["current_agent"] = "learning_recommend"
        
        return state
    
    async def _planning_node(self, state: GraphState) -> GraphState:
        """Planning Agent 节点 - 任务规划"""
        print(f"[Planning] 生成分析计划...")
        
        # ===== 关键：重新查询 completed_stages =====
        job_id = state.get("job_id")
        if job_id:
            completed_stages = self._query_completed_stages_from_manifest(job_id)
            state["completed_stages"] = completed_stages
            print(f"[Planning] Refreshed completed_stages from manifest: {completed_stages}")
        else:
            print(f"[Planning] No job_id, using state completed_stages: {state.get('completed_stages', [])}")
        
        # 传递Learning推荐参数给Planning
        learning_recommendations = state.get("learning_recommendations", {})
        if learning_recommendations:
            print(f"[Planning] 接收Learning推荐: {learning_recommendations.get('message', '')}")
        
        message = AgentMessage(
            sender="router",
            receiver="planning",
            message_type="plan",
            payload={
                "query": state["query"],
                "intent": state.get("intent_result", {}),
                "learning_recommendations": learning_recommendations  # 传递Learning推荐
            }
        )
        
        agent_state = self._to_agent_state(state)
        result_state = await self.planning_agent.execute(agent_state, message)
        
        task_plan = result_state.results.get("task_plan", {})
        state["plan_result"] = task_plan
        
        # 检查Planning是否失败（PreFlight验证失败等）
        if not task_plan.get("valid", True):
            error_msg = task_plan.get("error", "规划失败")
            print(f"[Planning] 规划失败: {error_msg}")
            state["final_output"] = {
                "response": f"❌ {error_msg}",
                "status": "failed",
                "error": error_msg
            }
            state["current_agent"] = "planning"
            return state
        
        state["current_agent"] = "planning"
        
        return state
    
    async def _preflight_node(self, state: GraphState) -> GraphState:
        """PreFlight Agent 节点 - 执行前验证"""
        print(f"[PreFlight] 执行前验证...")
        
        # 从 plan_result 提取信息
        plan = state.get("plan_result", {})
        job_id = state.get("job_id", "")
        
        # 从 intent 或 plan 中提取 prefix
        intent = state.get("intent_result", {})
        prefix = intent.get("prefix") or plan.get("prefix", "")
        
        message = AgentMessage(
            sender="planning",
            receiver="preflight",
            message_type="validate",
            payload={
                "plan": plan,
                "job_id": job_id,
                "prefix": prefix
            }
        )
        
        # PreFlight功能已合并到Planning Agent，跳过独立的PreFlight节点
        # 直接使用Planning中的预检验证结果
        plan_result = state.get("plan_result", {})
        # 注意：验证失败时PlanningAgentV2使用"preflight_result"键，成功时用"preflight_validation"
        preflight_result = plan_result.get("preflight_validation") or plan_result.get("preflight_result", {})
        state["preflight_result"] = preflight_result
        
        # 如果验证失败，直接返回错误，不继续执行
        # 检查plan_result.valid（PlanningAgentV2设置的）或preflight_result.valid
        plan_valid = plan_result.get("valid", True)
        preflight_valid = preflight_result.get("valid", True)
        if not plan_valid or not preflight_valid:
            error_msg = plan_result.get("error") or preflight_result.get("error", "执行前验证失败")
            issues = preflight_result.get("issues", [])
            # 提取第一个错误的详细信息
            detail_msg = issues[0].get("message", error_msg) if issues else error_msg
            
            state["errors"].append({
                "agent": "preflight",
                "message": detail_msg,
                "issues": issues
            })
            print(f"[PreFlight] 验证失败: {len(issues)} 个问题")
            
            # 设置最终输出，直接返回错误
            state["final_output"] = {
                "response": f"❌ {detail_msg}",
                "status": "failed",
                "error": detail_msg,
                "issues": issues
            }
            state["current_agent"] = "preflight"
            state["execution_status"] = "error"
            state["execution_error"] = detail_msg
            return state
        else:
            print(f"[PreFlight] 验证通过（使用Planning中的预检验证结果）")
        
        state["current_agent"] = "preflight"
        
        return state
    
    async def _execution_node(self, state: GraphState) -> GraphState:
        """Execution Agent 节点 - 任务执行"""
        print(f"[Execution] 执行分析任务...")
        
        # 检查前置节点是否已经失败（PreFlight验证失败）
        if state.get("execution_status") == "error":
            error_msg = state.get("execution_error", "前置验证失败")
            print(f"[Execution] ❌ 前置验证失败，跳过执行: {error_msg}")
            state["final_output"] = {
                "response": f"❌ {error_msg}",
                "status": "failed",
                "error": error_msg
            }
            return state
        
        message = AgentMessage(
            sender="planning",
            receiver="execution",
            message_type="execute",
            payload={"plan": state.get("plan_result", {})}
        )
        
        agent_state = self._to_agent_state(state)
        result_state = await self.execution_agent.execute(agent_state, message)
        
        execution_results = result_state.results.get("execution_results", [])
        execution_status = result_state.results.get("execution_status")
        execution_error = result_state.results.get("execution_error")
        print(f"[Execution] DEBUG: execution_results from agent: {len(execution_results)} items")
        print(f"[Execution] DEBUG: execution_status: {execution_status}, execution_error: {execution_error}")
        state["execution_results"] = execution_results  # 使用复数形式，与_diagnosis_node一致
        if execution_status:
            state["execution_status"] = execution_status
        if execution_error:
            state["execution_error"] = execution_error
        print(f"[Execution] DEBUG: state['execution_results'] set: {len(state.get('execution_results', []))} items")
        
        # 记录执行历史到Learning Agent
        for exec_result in execution_results:
            stage = exec_result.get("stage", "unknown")
            result = exec_result.get("result", {})
            
            # 获取执行参数（从plan_result中）
            plan = state.get("plan_result", {})
            stage_params = {}
            for s in plan.get("stages", []):
                if s.get("id") == stage:
                    stage_params = s.get("params", {})
                    break
            
            # 调用Learning记录执行历史
            learning_message = AgentMessage(
                sender="execution",
                receiver="learning",
                message_type="record",
                payload={
                    "stage": stage,
                    "prefix": plan.get("prefix", ""),
                    "params": stage_params,
                    "status": result.get("status", "unknown"),
                    "elapsed_sec": result.get("elapsed_sec", 0),
                    "final_members": result.get("metrics", {}).get("final_members", 0)
                }
            )
            
            try:
                learning_state = self._to_agent_state(state)
                await self.knowledge_agent.execute(learning_state, learning_message)
                print(f"[Execution] ✅ Learning记录已保存: {stage}")
            except Exception as e:
                print(f"[Execution] ⚠️ Learning记录失败: {e}")
        
        state["current_agent"] = "execution"
        # 产物校验：任务运行状态不能替代阶段 manifest/artifacts。
        if execution_status != "error":
            from gfa_core.mas.tools.stage_identity import get_stage_status_for_identity, resolve_stage_identity

            plan = state.get("plan_result", {})
            planned_stage_items = [s for s in plan.get("stages", []) if isinstance(s, dict) and s.get("id")]
            planned_stage_ids = [s.get("id") for s in planned_stage_items]
            if not planned_stage_ids:
                user_input = state.get("user_input") or {}
                if user_input.get("stage"):
                    planned_stage_ids = [user_input["stage"]]
                else:
                    planned_stage_ids = [
                        s for s in (state.get("intent_result", {}).get("stages", []) or [])
                        if isinstance(s, str)
                    ]

            job_id = state.get("job_id", "")
            prefix = (state.get("user_input") or {}).get("prefix") or plan.get("prefix", "")
            invalid_reports = []
            if job_id and prefix:
                stage_params_by_id = {
                    str(item.get("id")): (item.get("params") or {})
                    for item in planned_stage_items
                    if isinstance(item, dict)
                }
                for stage_id in planned_stage_ids:
                    params = dict(stage_params_by_id.get(stage_id) or {})
                    stage_prefix = params.get("prefix") or prefix
                    identity = resolve_stage_identity(
                        stage=stage_id,
                        prefix=stage_prefix,
                        params={**params, "prefix": stage_prefix},
                        job_id=job_id,
                    )
                    report = get_stage_status_for_identity(job_id=job_id, identity=identity, strict_identity=True)
                    if not report.completed:
                        invalid_reports.append(report.to_dict())

            if invalid_reports:
                execution_status = "error"
                first = invalid_reports[0]
                detail = "; ".join(first.get("errors") or first.get("missing_artifacts") or [])
                execution_error = f"阶段产物校验失败: {first['stage']} ({detail or first['status']})"
                state["execution_status"] = execution_status
                state["execution_error"] = execution_error
                state.setdefault("errors", []).append(execution_error)
                state["final_output"] = {
                    "response": f"❌ {execution_error}",
                    "status": "failed",
                    "error": execution_error,
                    "stage_status": invalid_reports,
                }
                print(f"[Execution] ❌ 产物校验失败: {invalid_reports}")

        # 【关键修复】根据执行状态设置 status，而不是总是 "completed"
        if execution_status == "error":
            state["status"] = "failed"
            print(f"[Execution] ❌ 执行失败，设置 status=failed")
        else:
            state["status"] = "completed"
            print(f"[Execution] ✅ 执行完成，设置 status=completed")
        
        return state
    
    async def _validator_node(self, state: GraphState) -> GraphState:
        """Validator Agent 节点 - 结果验证（优化版：复用ResultParser的解析结果）"""
        print(f"[Validator] 验证结果（复用ResultParser解析结果）...")
        
        # 从 state 中获取 parse_result（ResultParser已解析的结果）
        parse_result = state.get("parse_result", {})
        
        # 优先使用 execution_results（因为LangGraph state传递问题，parse_result可能为空）
        execution_results_list = state.get("execution_results", [])
        
        # 如果 execution_results 为空，尝试从文件系统重新加载
        if isinstance(execution_results_list, list) and len(execution_results_list) == 0:
            print(f"[Validator] ⚠️ execution_results 为空，尝试从manifest重新加载")
            job_id = state.get("job_id", "")
            prefix = (state.get("user_input") or {}).get("prefix", "")
            if job_id and prefix:
                try:
                    from pathlib import Path
                    import json
                    workspace_final = PROJECT_ROOT / "workspace" / job_id / "final"
                    if workspace_final.exists():
                        for manifest_file in workspace_final.glob(f"{prefix}.*.manifest.json"):
                            try:
                                with open(manifest_file, 'r') as f:
                                    manifest = json.load(f)
                                stage = manifest.get("stage", "unknown")
                                execution_results_list.append({
                                    "stage": stage,
                                    "result": {
                                        "status": manifest.get("status", "success"),
                                        "manifest": manifest,
                                        "manifest_path": str(manifest_file),
                                        "message": f"✅ {stage.capitalize()} 完成（从manifest加载）"
                                    }
                                })
                                print(f"[Validator] 加载manifest: {manifest_file.name}")
                            except Exception as e:
                                print(f"[Validator] 加载manifest失败: {e}")
                        # 更新state
                        state["execution_results"] = execution_results_list
                except Exception as e:
                    print(f"[Validator] 扫描manifest目录失败: {e}")
            
            # 如果仍然没有execution_results，才跳过验证
            if len(execution_results_list) == 0:
                print(f"[Validator] ✅ 没有执行任何阶段，跳过验证")
                state["validation_result"] = {
                    "overall_passed": True,
                    "summary": "✅ 没有需要验证的阶段",
                    "stage": "none"
                }
                state["current_agent"] = "validator"
                return state
        
        # 使用 execution_results 进行验证
        print(f"[Validator] ✅ 使用execution_results进行验证")
        last_execution = execution_results_list[-1] if execution_results_list else {}
        execution_result = last_execution.get("result", {}) if isinstance(last_execution, dict) else {}
        current_stage = last_execution.get("stage", "unknown") if isinstance(last_execution, dict) else "unknown"
        
        # 获取 prefix 和 workspace
# 获取 prefix：优先从 execution_result.manifest 获取 prefix（因为 state.user_input 可能没有更新）
        manifest = execution_result.get("manifest") or {}
        manifest_prefix = manifest.get("prefix")
        if manifest_prefix:
            prefix = manifest_prefix
        else:
            # fallback 到 state.user_input 或 state.prefix
            prefix = (state.get("user_input") or {}).get("prefix") or state.get("prefix", "")
        job_id = state.get("job_id", "")
        workspace = str(PROJECT_ROOT / "workspace" / job_id) if job_id else ""
        
        print(f"[Validator] Validating stage: {current_stage}, prefix: {prefix}, workspace: {workspace}")
        
        message = AgentMessage(
            sender="result_parser",  # 改为从result_parser接收
            receiver="validator",
            message_type="validate",
            payload={
                "execution_result": execution_result,
                "stage": current_stage,
                "prefix": prefix,
                "workspace": workspace,
                "parse_result": parse_result  # 传递完整的解析结果
            }
        )
        
        # Validator功能已合并到ResultParser Agent
        # 直接使用ResultParser中的验证结果
        parse_result = state.get("parse_result", {})
        validation_report = parse_result.get("validation", {})
        
        state["validation_result"] = validation_report
        state["current_agent"] = "validator"
        
        print(f"[Validator] 使用ResultParser的验证结果: passed={validation_report.get('overall_passed', False)}")
        
        return state
    
    async def _diagnosis_node(self, state: GraphState) -> GraphState:
        """Diagnosis Agent 节点 - 错误诊断"""
        print(f"[Diagnosis] 诊断错误...")
        print(f"[Diagnosis] DEBUG: state keys: {list(state.keys())}")
        print(f"[Diagnosis] DEBUG: execution_results: {state.get('execution_results', 'NOT FOUND')}")
        
        # 获取最后一个失败的执行结果
        execution_results = state.get("execution_results", [])
        print(f"[Diagnosis] DEBUG: execution_results type: {type(execution_results)}, len: {len(execution_results)}")
        last_execution = execution_results[-1] if execution_results else {}
        print(f"[Diagnosis] DEBUG: last_execution: {last_execution}")
        fallback_stage = state.get("current_stage", "unknown")
        plan_stages = state.get("plan_result", {}).get("stages", [])
        if fallback_stage == "unknown" and isinstance(plan_stages, list) and plan_stages:
            first_stage = plan_stages[0]
            fallback_stage = first_stage.get("id", "unknown") if isinstance(first_stage, dict) else str(first_stage)
        
        # 构建错误信息payload
        state_error = state.get("execution_error") or state.get("last_error") or ""
        error_info = last_execution.get("error_details", {}) if isinstance(last_execution, dict) else {}
        if not isinstance(error_info, dict):
            error_info = {"message": str(error_info)}
        if state_error and not error_info.get("message"):
            error_info["message"] = state_error
        if state_error and not error_info.get("error"):
            error_info["error"] = state_error
        error_info.setdefault(
            "stage",
            last_execution.get("stage", fallback_stage) if isinstance(last_execution, dict) else fallback_stage,
        )

        error_payload = {
            "validation_result": state.get("validation_result", {}),
            "execution_result": last_execution.get("result", {}) if isinstance(last_execution, dict) else {},
            "error": error_info,
            "execution_error": state_error,
            "stage": error_info.get("stage", "unknown"),
            "job_id": state.get("job_id", ""),
            "prefix": (state.get("user_input") or {}).get("prefix") or state.get("plan_result", {}).get("prefix", ""),
        }
        
        print(f"[Diagnosis] 错误信息: {error_payload.get('error', {})}")
        
        message = AgentMessage(
            sender="validator",
            receiver="diagnosis",
            message_type="diagnose",
            payload=error_payload
        )
        
        agent_state = self._to_agent_state(state)
        result_state = await self.diagnosis_agent.execute(agent_state, message)
        
        state["diagnosis_result"] = result_state.results.get("diagnosis", {})
        if state.get("execution_status") == "error":
            final_output = state.get("final_output") or {}
            final_output["diagnosis"] = state["diagnosis_result"]
            final_output.setdefault("status", "failed")
            final_output.setdefault("error", state_error)
            state["final_output"] = final_output
        state["current_agent"] = "diagnosis"
        retry_count = state.get("retry_count", 0)
        state["retry_count"] = (retry_count if retry_count is not None else 0) + 1
        
        return state
    
    async def _result_parser_node(self, state: GraphState) -> GraphState:
        """ResultParser Agent 节点 - 结果解析"""
        print(f"[ResultParser] 解析结果...")
        
        # 检查execution是否失败
        execution_status = state.get("execution_status")
        execution_error = state.get("execution_error")
        if execution_status == "error":
            print(f"[ResultParser] ❌ Execution阶段失败: {execution_error}")
            state["final_output"] = {
                "response": f"❌ 分析执行失败: {execution_error}",
                "status": "failed",
                "error": execution_error
            }
            state["current_agent"] = "result_parser"
            return state
        
        # 获取最后一个执行结果
        execution_results = state.get("execution_results", [])
        
        # 如果 execution_results 为空，尝试从文件系统重新加载（Celery多进程问题）
        if not execution_results:
            print(f"[ResultParser] ⚠️ execution_results 为空，尝试从manifest重新加载")
            job_id = state.get("job_id", "")
            prefix = (state.get("user_input") or {}).get("prefix", "")
            if job_id and prefix:
                try:
                    from pathlib import Path
                    import json
                    workspace = PROJECT_ROOT / "workspace" / job_id / "final"
                    if workspace.exists():
                        for manifest_file in workspace.glob(f"{prefix}.*.manifest.json"):
                            try:
                                with open(manifest_file, 'r') as f:
                                    manifest = json.load(f)
                                stage = manifest.get("stage", "unknown")
                                execution_results.append({
                                    "stage": stage,
                                    "result": {
                                        "status": manifest.get("status", "success"),
                                        "manifest": manifest,
                                        "manifest_path": str(manifest_file),
                                        "message": f"✅ {stage.capitalize()} 完成（从manifest加载）"
                                    }
                                })
                                print(f"[ResultParser] 加载manifest: {manifest_file.name}")
                            except Exception as e:
                                print(f"[ResultParser] 加载manifest失败: {e}")
                        # 更新state
                        state["execution_results"] = execution_results
                except Exception as e:
                    print(f"[ResultParser] 扫描manifest目录失败: {e}")
        
        last_execution = execution_results[-1] if execution_results else {}
        
        # 获取 prefix 和 workspace
        intent = state.get("intent_result", {})
        prefix = intent.get("prefix", "")
        job_id = state.get("job_id", "")
        workspace = str(PROJECT_ROOT / "workspace" / job_id) if job_id else ""
        
        message = AgentMessage(
            sender="validator",
            receiver="result_parser",
            message_type="parse",
            payload={
                "execution_result": last_execution.get("result", {}) if isinstance(last_execution, dict) else {},
                "stage": last_execution.get("stage", "unknown"),
                "prefix": prefix,
                "workspace": workspace,
                "gene_family": intent.get("gene_family", ""),
                "organism": intent.get("species", "")
            }
        )
        
        agent_state = self._to_agent_state(state)
        result_state = await self.result_parser_agent.execute(agent_state, message)
        
        state["parse_result"] = result_state.results.get("parsed_report", {})
        state["current_agent"] = "result_parser"
        
        return state
    
    async def _discovery_node(self, state: GraphState) -> GraphState:
        """Discovery Agent 节点 - 科学发现（支持科研模式和阶段性解读）"""
        print(f"[Discovery] 分析科学发现...")
        
        # 检查验证结果
        validation_result = state.get("validation_result", {})
        validation_passed = validation_result.get("overall_passed", False)
        
        # 如果验证失败，生成修复建议
        if not validation_passed:
            print(f"[Discovery] 验证失败，生成修复建议...")
            # 获取错误信息
            errors = validation_result.get("errors", [])
            error_message = errors[0] if errors else "验证失败"
            
            # 生成修复建议
            discovery_result = {
                "type": "fix_recommendation",
                "description": f"验证失败: {error_message}",
                "confidence": 0.8,
                "should_continue": False,
                "updated_plan": None,
                "recommendations": [
                    "检查输入数据",
                    "调整参数",
                    "重新执行"
                ]
            }
            
            state["discovery_result"] = discovery_result
            state["messages"].append({
                "role": "discovery",
                "content": f"🔍 科学发现: 验证失败，建议修复",
                "metadata": {
                    "confidence": 0.8,
                    "should_continue": False
                }
            })
            
            return state
        
        # 检查是否是按需解读请求
        on_demand_interpret = state.get("on_demand_interpret", False)
        current_stage = state.get("current_stage", None)
        
        # 根据解读模式选择处理方式
        interpretation_mode = state.get("interpretation_mode", "comprehensive")
        
        # 获取最后一个执行结果（统一使用execution_results）
        execution_results = state.get("execution_results", [])
        last_execution = execution_results[-1] if execution_results else {}
        execution_result = last_execution.get("result", {}) if isinstance(last_execution, dict) else {}
        
        # 如果是按需解读请求
        if on_demand_interpret and current_stage:
            print(f"[Discovery] 处理按需阶段性解读请求: {current_stage}")
            message = AgentMessage(
                sender="result_parser",
                receiver="discovery",
                message_type="discover",
                payload={
                    "mode": "generate_report",
                    "current_stage": current_stage,  # 指定当前阶段，生成阶段性报告
                    "stage": last_execution.get("stage"),
                    "parse_result": state.get("parse_result", {}),
                    "execution_result": execution_result
                }
            )
        elif self._is_final_stage(state) and interpretation_mode == "comprehensive":
            # 最后阶段，生成完整综合报告
            message = AgentMessage(
                sender="result_parser",
                receiver="discovery",
                message_type="discover",
                payload={
                    "mode": "generate_report",
                    "stage": last_execution.get("stage"),
                    "parse_result": state.get("parse_result", {}),
                    "execution_result": execution_result
                }
            )
        else:
            # 快速模式：存储结果，不立即生成深度解读
            message = AgentMessage(
                sender="result_parser",
                receiver="discovery",
                message_type="discover",
                payload={
                    "mode": "quick",  # 默认快速模式，存储结果
                    "stage": last_execution.get("stage"),
                    "parse_result": state.get("parse_result", {}),
                    "execution_result": execution_result
                }
            )
        
        agent_state = self._to_agent_state(state)
        result_state = await self.discovery_agent.execute(agent_state, message)
        
        # 获取Discovery结果
        discovery_result = result_state.results.get("discovery_result", {})
        state["discovery_result"] = discovery_result
        
        # 存储综合报告（如果生成）
        if "comprehensive_report" in result_state.results:
            state["comprehensive_report"] = result_state.results["comprehensive_report"]
            print(f"[Discovery] 综合报告已保存")
        
        # 存储阶段解读结果
        stage = state.get("current_stage", "unknown")
        stage_interpretations = state.get("stage_interpretations", {})
        if stage not in stage_interpretations:
            stage_interpretations[stage] = []
        stage_interpretations[stage].append(discovery_result)
        state["stage_interpretations"] = stage_interpretations
        
        # 如果Discovery建议继续迭代，调用Learning获取参数建议
        if discovery_result.get("should_continue") and discovery_result.get("updated_plan"):
            print(f"[Discovery] 建议继续迭代，调用Learning获取参数建议...")
            
            # 获取需要重新执行的阶段
            updated_plan = discovery_result.get("updated_plan", {})
            stages = updated_plan.get("stages", [])
            
            if stages:
                first_stage = stages[0].get("id", "identify")
                
                # 调用Knowledge.Learning获取参数推荐
                learning_message = AgentMessage(
                    sender="discovery",
                    receiver="knowledge",
                    message_type="recommend",
                    payload={"mode": "recommend_params", "stage": first_stage}
                )
                
                learning_state = self._to_agent_state(state)
                learning_result = await self.knowledge_agent.execute(learning_state, learning_message)
                
                knowledge_response = learning_result.results.get("knowledge_response", {})
                recommendations = knowledge_response if isinstance(knowledge_response, dict) else {}
                
                # 将Learning推荐融入updated_plan
                if recommendations.get("confidence") in ["high", "medium"]:
                    recommended_params = recommendations.get("params", {})
                    if first_stage in recommended_params:
                        for stage_config in stages:
                            if stage_config.get("id") == first_stage:
                                stage_config["params"] = stage_config.get("params", {})
                                stage_config["params"].update(recommended_params[first_stage])
                                stage_config["learning_recommended"] = True
                                print(f"[Discovery] Stage {first_stage} 应用Learning推荐参数: {recommended_params[first_stage]}")
                                break
                        
                        # 更新discovery_result中的updated_plan
                        discovery_result["updated_plan"] = updated_plan
                        state["discovery_result"] = discovery_result
        
        state["current_agent"] = "discovery"
        
        return state
    
    async def _learning_node(self, state: GraphState) -> GraphState:
        """Learning Agent 节点 - 学习优化"""
        print(f"[Learning] 学习优化...")
        
        # 获取最后一个执行结果（统一使用execution_results）
        execution_results = state.get("execution_results", [])
        last_execution = execution_results[-1] if execution_results else {}
        execution_result = last_execution.get("result", {}) if isinstance(last_execution, dict) else {}
        
        message = AgentMessage(
            sender="discovery",
            receiver="knowledge",
            message_type="learn",
            payload={
                "mode": "record_execution",
                "execution_result": execution_result,
                "validation_result": state.get("validation_result", {})
            }
        )
        
        agent_state = self._to_agent_state(state)
        result_state = await self.knowledge_agent.execute(agent_state, message)
        
        knowledge_response = result_state.results.get("knowledge_response", {})
        state["learn_result"] = knowledge_response if isinstance(knowledge_response, dict) else {}
        state["final_output"] = {
            "execution": execution_result,
            "validation": state.get("validation_result", {}),
            "parse": state.get("parse_result", {}),
            "discovery": state.get("discovery_result", {}),
            "learn": state.get("learn_result", {})
        }
        state["current_agent"] = "learning"
        # 【关键修复】如果执行失败，不要覆盖 status
        if state.get("execution_status") == "error":
            state["status"] = "failed"
            print(f"[Learning] ❌ 执行失败，保持 status=failed")
        else:
            state["status"] = "completed"
            print(f"[Learning] ✅ 执行完成，设置 status=completed")
        
        return state
    
    def _is_final_stage(self, state: GraphState) -> bool:
        """判断是否为最后一个分析阶段"""
        plan = state.get("plan_result", {})
        stages = plan.get("stages", [])
        completed = state.get("completed_stages", [])
        
        # 如果所有计划阶段都已完成，则是最后阶段
        if stages and completed:
            stage_ids = [s.get("id") for s in stages]
            return all(s in completed for s in stage_ids)
        return False
    
    # ============ 条件边函数 ============
    
    def _route_decision(self, state: GraphState) -> str:
        """Router 决策 - 决定流向
        
        根据Router设置的next_node决定流程分支：
        - "knowledge": FAQ/CONSULTATION/INTERPRETATION流程
        - "feasibility": FEASIBILITY流程
        - "report_enhance": REPORT_ENHANCE流程
        - "intent": ANALYSIS流程 -> Intent -> Planning -> Execution
        - "end": 结束
        """
        # 优先使用 _router_node 设置的 next_node
        next_node = state.get("next_node")
        if next_node:
            print(f"[_route_decision] Using next_node from state: {next_node}")
            return next_node
        
        # 回退到 mode 检查（兼容旧代码）
        mode = state.get("mode", "faq")
        print(f"[_route_decision] Falling back to mode: {mode}")
        
        if mode in ["faq", "consultation", "interpretation"]:
            return "knowledge"
        elif mode == "analysis":
            return "intent"  # ANALYSIS模式现在先到Intent节点
        elif mode == "feasibility":
            return "feasibility"
        elif mode == "report_enhance":
            return "report_enhance"
        else:
            return "end"
    
    def _validate_decision(self, state: GraphState) -> str:
        """Validator 决策 - 验证结果
        
        流程6-7: Execution→Validator→[Discovery/Diagnosis]
        - success: 验证通过 -> Discovery
        - failure: 验证失败 -> Diagnosis（进入修复循环）
        """
        validation = state.get("validation_result", {})
        
        if validation.get("overall_passed", False):
            print(f"[_validate_decision] Validation passed -> Discovery")
            return "success"
        else:
            print(f"[_validate_decision] Validation failed -> Diagnosis")
            # 记录错误信息用于诊断
            errors = validation.get("errors", [])
            if errors:
                state["last_error"] = errors[0] if isinstance(errors[0], str) else str(errors[0])
            return "failure"
    
    def _diagnosis_decision(self, state: GraphState) -> str:
        """Diagnosis 决策 - 修复循环控制
        
        流程6: 失败+自动修复（最多3次）
        Execution→Diagnosis→Execution（循环）
        
        流程7: 失败+不可修复
        Execution→Diagnosis→END
        
        Returns:
            "retry": 自动修复后重试Execution（流程6）
            "waiting_confirmation": 等待用户确认
            "skip": 跳过错误继续到Discovery
            "fail": 达到最大重试次数，失败结束（流程7）
        """
        # 获取全局迭代计数
        iteration_count = state.get("iteration_count", 0) or 0
        max_iterations = state.get("max_iterations", 3) or 3
        
        # 检查是否有等待用户确认的状态
        pending_confirmation = state.get("pending_confirmation")
        if pending_confirmation and pending_confirmation.get("status") == "waiting":
            from datetime import datetime
            deadline = pending_confirmation.get("deadline")
            if deadline:
                try:
                    deadline_dt = datetime.fromisoformat(deadline)
                    if datetime.now() > deadline_dt:
                        print(f"[Diagnosis Decision] 用户确认超时，自动应用修复")
                        pending_confirmation["status"] = "auto_applied"
                        state["pending_confirmation"] = pending_confirmation
                        # 增加全局迭代计数
                        state["iteration_count"] = iteration_count + 1
                        return "retry"
                except:
                    pass
            
            print(f"[Diagnosis Decision] 等待用户确认修复方案...")
            state["status"] = "waiting_confirmation"
            return "waiting_confirmation"
        
        # 检查用户是否已确认
        if pending_confirmation and pending_confirmation.get("status") == "confirmed":
            print(f"[Diagnosis Decision] 用户已确认修复方案，继续重试")
            # 增加全局迭代计数
            state["iteration_count"] = state.get("iteration_count", 0) + 1
            return "retry"
        
        # 默认返回失败（让LangGraph路由到END）
        print(f"[Diagnosis Decision] 无法自动修复，返回失败")
        return "fail"

    def _execution_decision(self, state: GraphState) -> str:
        """Route failed execution/preflight to diagnosis before final parsing."""
        if state.get("execution_status") == "error":
            print("[Execution Decision] execution_status=error -> diagnosis")
            return "diagnosis"
        return "result_parser"
    
    def _discovery_decision(self, state: GraphState) -> str:
        """Discovery 决策 - REPLAN循环控制
        
        流程8: Discovery检测到异常+REPLAN
        Discovery→Planning（循环，最多3次）
        
        流程9: REPLAN达到上限
        Discovery→END
        
        Returns:
            "planning": 需要重新规划（REPLAN）
            "end": 正常结束或达到上限
        """
        discovery_result = state.get("discovery_result", {})
        replan_count = state.get("replan_count", 0)
        max_replan = state.get("max_replan", 3)
        
        # 检查是否需要REPLAN
        should_continue = discovery_result.get("should_continue", False)
        
        if should_continue and replan_count < max_replan:
            print(f"[Discovery Decision] REPLAN {replan_count + 1}/{max_replan}")
            state["replan_count"] = replan_count + 1
            return "planning"
        elif should_continue and replan_count >= max_replan:
            print(f"[Discovery Decision] 达到最大REPLAN次数({max_replan})，强制结束")
            return "end"
        else:
            print("[Discovery Decision] 无需REPLAN，正常结束")
            return "end"
    
    def _discovery_after_validation_decision(self, state: GraphState) -> str:
        """Discovery后决策 - 根据验证结果决定下一步
        
        流程:
        - 验证失败 -> Diagnosis（修复）
        - 验证成功 -> Learning（完成）
        - 需要REPLAN -> Planning（循环）
        """
        # 获取验证结果
        validation_result = state.get("validation_result", {})
        validation_passed = validation_result.get("overall_passed", False)
        
        # 获取Discovery结果
        discovery_result = state.get("discovery_result", {})
        should_continue = discovery_result.get("should_continue", False)
        replan_count = state.get("replan_count", 0)
        max_replan = state.get("max_replan", 3)
        
        # 如果验证失败，进入Diagnosis
        if not validation_passed:
            print(f"[DiscoveryAfterValidation] 验证失败，进入Diagnosis")
            return "diagnosis"
        
        # 如果Discovery建议继续（REPLAN）
        if should_continue and replan_count < max_replan:
            print(f"[DiscoveryAfterValidation] REPLAN {replan_count + 1}/{max_replan}")
            state["replan_count"] = replan_count + 1
            return "replan"
        elif should_continue and replan_count >= max_replan:
            print(f"[DiscoveryAfterValidation] 达到最大REPLAN次数({max_replan})，强制结束")
            return "end"
        
        # 验证成功，进入Learning
        print(f"[DiscoveryAfterValidation] 验证成功，进入Learning")
        return "learning"
    
    # ============ 辅助函数 ============
    
    def _to_agent_state(self, state: GraphState) -> AgentState:
        """将 GraphState 转换为 AgentState"""
        print(f"[_to_agent_state] completed_stages in state: {state.get('completed_stages', 'NOT FOUND')}")
        
        # 获取 execution_results（应该是列表格式）
        execution_result = state.get("execution_results", [])
        # 确保是列表格式
        if not isinstance(execution_result, list):
            execution_result = []
        
        return AgentState(
            session_id=state.get("session_id", ""),
            messages=state.get("messages", []),
            current_task=state.get("current_agent"),
            results={
                "intent": state.get("intent_result", {}),
                "task_plan": state.get("plan_result", {}),
                "execution_results": execution_result,
                "validation_report": state.get("validation_result", {}),
                "diagnosis": state.get("diagnosis_result", {}),
                "parsed_report": state.get("parse_result", {}),
                "discovery_result": state.get("discovery_result", {}),
                "recommendations": state.get("learn_result", {}),
                "job_id": state.get("job_id", ""),  # 传递 job_id 用于文件路径
                "prefix": (state.get("user_input") or {}).get("prefix", ""),  # 传递 prefix
                "completed_stages": state.get("completed_stages", []),  # 传递已完成阶段
            "learning_recommendations": state.get("learning_recommendations", {}),  # 传递Learning推荐
            }
        )
    
    def create_initial_state(
        self,
        session_id: str,
        query: str,
        job_id: str = "",
        files: Optional[List[Dict]] = None,
        prefix: str = ""  # 前端传递的 prefix（例如 "At.WRKY"）
    ) -> GraphState:
        """创建初始状态"""
        return {
            "session_id": session_id,
            "job_id": job_id,
            "query": query,
            "mode": "faq",
            "status": "pending",
            "current_agent": None,
            "current_step": 0,
            "user_input": {"prefix": prefix} if prefix else {},  # 存储前端传递的 prefix
            "files": files or [],
            "messages": [],
            "intent_result": {},
            "plan_result": {},
            "execution_result": {},
            "knowledge_result": {},
            "diagnosis_result": {},
            "validation_result": {},
            "parse_result": {},
            "learn_result": {},
            "discovery_result": {},
            "final_output": {},
            "errors": [],
            "retry_count": 0,
            "max_retries": 3,
            "completed_stages": [],
            "learning_recommendations": {},  # Learning参数推荐
            "iteration_count": 0,
            "max_iterations": 3,
            "discoveries": [],
            "interpretation_mode": "comprehensive",  # 默认综合模式
            "stage_interpretations": {},  # 各阶段解读结果
        }
