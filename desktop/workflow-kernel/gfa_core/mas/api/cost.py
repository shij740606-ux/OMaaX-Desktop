"""
成本追踪API端点
"""

from fastapi import APIRouter
from gfa_core.mas.cost_tracker import get_cost_tracker

router = APIRouter(prefix="/cost", tags=["cost"])


@router.get("/summary")
async def get_cost_summary():
    """获取成本摘要"""
    tracker = get_cost_tracker()
    return tracker.get_summary()


@router.get("/report")
async def get_cost_report():
    """获取成本报告（文本格式）"""
    tracker = get_cost_tracker()
    return {"report": tracker.generate_report()}


@router.get("/agents/{agent_name}")
async def get_agent_cost(agent_name: str):
    """获取特定Agent的成本统计"""
    tracker = get_cost_tracker()
    stats = tracker.get_agent_stats(agent_name)
    if agent_name in stats:
        s = stats[agent_name]
        return {
            "agent_name": s.agent_name,
            "call_count": s.call_count,
            "total_tokens": s.total_tokens,
            "total_cost": s.total_cost,
            "avg_cost_per_call": s.avg_cost_per_call,
            "last_call": s.last_call,
        }
    return {"error": "Agent not found"}


@router.get("/suggestions")
async def get_optimization_suggestions():
    """获取优化建议"""
    tracker = get_cost_tracker()
    return {"suggestions": tracker.get_optimization_suggestions()}
