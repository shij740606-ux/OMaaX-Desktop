from enum import Enum
from typing import Any, TypedDict, Union


class WorkflowStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERING = "recovering"


class GraphState(TypedDict):
    session_id: str
    job_id: str  # 当前选中的 job_id，用于 auto_continue
    query: str
    mode: str
    status: str
    user_input: dict[str, Any]
    files: list[Union[str, dict[str, Any]]]
    current_agent: str | None
    current_step: int
    workflow: list[dict[str, Any]]
    knowledge_results: list[dict[str, Any]]
    analysis_plan: dict[str, Any]
    execution_results: list[dict[str, Any]]  # 修复：改为 list 类型，与实际使用一致
    validation_result: dict[str, Any]  # 修复：改为单数形式，与代码一致
    diagnosis_result: dict[str, Any]  # 修复：改为单数形式，与代码一致
    parse_result: dict[str, Any]  # 新增：ResultParser 的解析结果
    plan_result: dict[str, Any]  # 新增：Planning 的计划结果
    intent_result: dict[str, Any]  # 新增：Router 的意图识别结果
    discovery_result: dict[str, Any]  # 新增：Discovery 的发现结果
    preflight_result: dict[str, Any]  # 新增：PreFlight 的验证结果
    final_output: dict[str, Any]
    messages: list[dict[str, str]]
    errors: list[str]
    retry_count: int
    max_retries: int
    iteration_count: int  # 新增：迭代计数
    metadata: dict[str, Any]
    completed_stages: list[str]  # 已完成的阶段列表
    single_stage_mode: bool  # 新增：单阶段模式标记
    target_stage: str  # 新增：目标阶段


def create_initial_state(session_id: str, query: str, files: list[Union[str, dict[str, Any]]] | None = None) -> GraphState:
    return GraphState(
        session_id=session_id,
        job_id="",  # 新增
        query=query,
        mode="faq",
        status="pending",
        user_input={},
        files=files or [],
        current_agent=None,
        current_step=0,
        workflow=[],
        knowledge_results=[],
        analysis_plan={},
        execution_results=[],  # 修复：改为空列表
        validation_result={},  # 修复：改为单数形式
        diagnosis_result={},  # 修复：改为单数形式
        parse_result={},  # 新增
        plan_result={},  # 新增
        intent_result={},  # 新增
        discovery_result={},  # 新增
        preflight_result={},  # 新增
        final_output={},
        messages=[],
        errors=[],
        retry_count=0,
        max_retries=3,
        iteration_count=0,  # 新增
        metadata={},
        completed_stages=[],
        single_stage_mode=False,  # 新增
        target_stage="",  # 新增
    )
