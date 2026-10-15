"""
成本追踪系统 - 监控Token消耗和API调用成本
参考Claude Code的cost-tracker.ts实现
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from threading import Lock

from gfa_core.project_paths import PROJECT_ROOT


@dataclass
class CostEntry:
    """单次调用成本记录"""
    timestamp: str
    agent_name: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost: float  # 美元
    duration_ms: int
    query_preview: str  # 查询前50字符


@dataclass
class AgentCostStats:
    """Agent成本统计"""
    agent_name: str
    call_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    avg_cost_per_call: float = 0.0
    last_call: Optional[str] = None


class CostTracker:
    """
    Token成本追踪器
    
    功能:
    1. 记录每次LLM调用的token消耗
    2. 按Agent统计成本
    3. 按时间段统计成本
    4. 生成成本报告
    5. 提供优化建议
    """
    
    # Token价格 (美元/1K tokens) - 阿里云百炼
    TOKEN_PRICES = {
        "glm-4-flash": {"input": 0.0001, "output": 0.0001},
        "moonshot-v1-8k": {"input": 0.00015, "output": 0.00015},
        "abab6.5s-chat": {"input": 0.0001, "output": 0.0001},
        "deepseek-chat": {"input": 0.0001, "output": 0.0002},
        "qwen3:0.6b": {"input": 0.0, "output": 0.0},  # 本地免费
        "deterministic-estimate": {"input": 0.0, "output": 0.0},  # 本地规则/无LLM交互估算
    }
    
    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or PROJECT_ROOT / "cost_logs"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        
        self._entries: List[CostEntry] = []
        self._lock = Lock()
        self._daily_file = self.storage_dir / f"cost_{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        
        # 加载今日已有记录
        self._load_today_entries()
    
    def _load_today_entries(self):
        """加载今日成本记录"""
        if self._daily_file.exists():
            with open(self._daily_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            data = json.loads(line)
                            self._entries.append(CostEntry(**data))
                        except:
                            pass
    
    def _estimate_cost(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
        """估算成本"""
        prices = self.TOKEN_PRICES.get(model, {"input": 0.0001, "output": 0.0001})
        
        input_cost = (input_tokens / 1000) * prices["input"]
        output_cost = (output_tokens / 1000) * prices["output"]
        
        return round(input_cost + output_cost, 6)
    
    def track(
        self,
        agent_name: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: int,
        query: str = "",
    ) -> CostEntry:
        """
        记录一次LLM调用成本
        
        Args:
            agent_name: Agent名称
            provider: 提供商(glm/kimi/deepseek等)
            model: 模型名称
            input_tokens: 输入token数
            output_tokens: 输出token数
            duration_ms: 调用耗时(毫秒)
            query: 查询内容(用于记录)
        """
        entry = CostEntry(
            timestamp=datetime.now().isoformat(),
            agent_name=agent_name,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            estimated_cost=self._estimate_cost(provider, model, input_tokens, output_tokens),
            duration_ms=duration_ms,
            query_preview=query[:50] if query else "",
        )
        
        with self._lock:
            self._entries.append(entry)
            
            # 写入文件
            with open(self._daily_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    "timestamp": entry.timestamp,
                    "agent_name": entry.agent_name,
                    "provider": entry.provider,
                    "model": entry.model,
                    "input_tokens": entry.input_tokens,
                    "output_tokens": entry.output_tokens,
                    "total_tokens": entry.total_tokens,
                    "estimated_cost": entry.estimated_cost,
                    "duration_ms": entry.duration_ms,
                    "query_preview": entry.query_preview,
                }, ensure_ascii=False) + '\n')
        
        return entry
    
    def get_agent_stats(self, agent_name: Optional[str] = None) -> Dict[str, AgentCostStats]:
        """获取Agent成本统计"""
        stats: Dict[str, AgentCostStats] = {}
        
        for entry in self._entries:
            if agent_name and entry.agent_name != agent_name:
                continue
            
            if entry.agent_name not in stats:
                stats[entry.agent_name] = AgentCostStats(agent_name=entry.agent_name)
            
            s = stats[entry.agent_name]
            s.call_count += 1
            s.total_input_tokens += entry.input_tokens
            s.total_output_tokens += entry.output_tokens
            s.total_tokens += entry.total_tokens
            s.total_cost += entry.estimated_cost
            s.last_call = entry.timestamp
        
        # 计算平均值
        for s in stats.values():
            if s.call_count > 0:
                s.avg_cost_per_call = round(s.total_cost / s.call_count, 6)
        
        return stats
    
    def get_summary(self) -> dict:
        """获取成本摘要"""
        if not self._entries:
            return {
                "total_calls": 0,
                "total_tokens": 0,
                "total_cost": 0.0,
                "agents": {},
            }
        
        total_calls = len(self._entries)
        total_tokens = sum(e.total_tokens for e in self._entries)
        total_cost = sum(e.estimated_cost for e in self._entries)
        
        agent_stats = self.get_agent_stats()
        
        return {
            "total_calls": total_calls,
            "total_tokens": total_tokens,
            "total_cost": round(total_cost, 6),
            "avg_cost_per_call": round(total_cost / total_calls, 6) if total_calls > 0 else 0,
            "avg_tokens_per_call": total_tokens // total_calls if total_calls > 0 else 0,
            "agents": {
                name: {
                    "calls": s.call_count,
                    "tokens": s.total_tokens,
                    "cost": round(s.total_cost, 6),
                    "avg_cost": s.avg_cost_per_call,
                }
                for name, s in agent_stats.items()
            },
        }
    
    def generate_report(self) -> str:
        """生成成本报告"""
        summary = self.get_summary()
        
        lines = [
            "=" * 60,
            "BBAV20311 成本追踪报告",
            "=" * 60,
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"数据日期: {datetime.now().strftime('%Y-%m-%d')}",
            "",
            "【总体统计】",
            f"  总调用次数: {summary['total_calls']}",
            f"  总Token数: {summary['total_tokens']:,}",
            f"  总成本: ${summary['total_cost']:.6f}",
            f"  平均每次成本: ${summary['avg_cost_per_call']:.6f}",
            f"  平均每次Token: {summary['avg_tokens_per_call']:,}",
            "",
            "【Agent统计】",
        ]
        
        for agent_name, stats in summary['agents'].items():
            lines.extend([
                f"  {agent_name}:",
                f"    调用: {stats['calls']}",
                f"    Token: {stats['tokens']:,}",
                f"    成本: ${stats['cost']:.6f}",
                f"    平均: ${stats['avg_cost']:.6f}/call",
            ])
        
        lines.extend([
            "",
            "=" * 60,
        ])
        
        return '\n'.join(lines)
    
    def get_optimization_suggestions(self) -> List[str]:
        """获取优化建议"""
        suggestions = []
        stats = self.get_agent_stats()
        
        # 找出高消耗的Agent
        high_cost_agents = [
            (name, s) for name, s in stats.items()
            if s.total_cost > 0.01  # 超过1美分
        ]
        
        if high_cost_agents:
            suggestions.append(f"高消耗Agent: {', '.join(name for name, _ in high_cost_agents)}")
            suggestions.append("建议: 考虑缓存常用查询结果")
        
        # 检查token效率
        for name, s in stats.items():
            if s.avg_cost_per_call > 0.005:  # 单次超过0.5美分
                suggestions.append(f"{name}单次成本较高(${s.avg_cost_per_call:.6f})，建议优化提示词长度")
        
        if not suggestions:
            suggestions.append("当前成本在合理范围内，无需特别优化")
        
        return suggestions


# 全局成本追踪器实例
_cost_tracker: Optional[CostTracker] = None


def get_cost_tracker() -> CostTracker:
    """获取全局成本追踪器"""
    global _cost_tracker
    if _cost_tracker is None:
        _cost_tracker = CostTracker()
    return _cost_tracker


def track_cost(
    agent_name: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    duration_ms: int,
    query: str = "",
):
    """便捷函数：记录成本"""
    tracker = get_cost_tracker()
    return tracker.track(agent_name, provider, model, input_tokens, output_tokens, duration_ms, query)


def print_cost_report():
    """便捷函数：打印成本报告"""
    tracker = get_cost_tracker()
    print(tracker.generate_report())
    
    suggestions = tracker.get_optimization_suggestions()
    if suggestions:
        print("\n【优化建议】")
        for s in suggestions:
            print(f"  • {s}")
