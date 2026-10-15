from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from gfa_core.project_paths import PROMPTS_DIR
from gfa_core.mas.llm import LLMManager, LLMMessage, LLMResponse


class QueryMode(Enum):
    FAQ = "faq"
    CONSULTATION = "consultation"
    ANALYSIS = "analysis"
    INTERPRETATION = "interpretation"
    FEASIBILITY = "feasibility"  # 新增：可行性研究模式
    REPORT_ENHANCE = "report_enhance"  # 新增：报告增强模式


@dataclass
class AgentState:
    session_id: str
    job_id: str = ""  # 用于查询已完成阶段
    messages: list[dict] = field(default_factory=list)
    current_task: str | None = None
    results: dict[str, Any] = field(default_factory=dict)
    mode: QueryMode = QueryMode.FAQ
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class AgentMessage:
    sender: str
    receiver: str
    message_type: str
    payload: dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.now)


class BaseAgent(ABC):
    name: str
    role: str

    def __init__(self, llm_manager: LLMManager):
        self.llm_manager = llm_manager

    @abstractmethod
    async def execute(self, state: AgentState, message: AgentMessage) -> AgentState:
        pass

    def _call_llm(
        self,
        prompt: str,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        import time
        from gfa_core.mas.cost_tracker import track_cost
        
        # 自动加载并附加系统提示词
        system_prompt = self._build_system_prompt()
        full_prompt = f"""{system_prompt}

---

用户请求: {prompt}

请根据上述系统提示词的指导处理此请求。"""
        messages = [LLMMessage(role="user", content=full_prompt)]
        
        # 记录开始时间
        start_time = time.time()
        
        # 估算输入token数
        input_tokens = len(full_prompt) // 4
        
        # 调用LLM
        response = self.llm_manager.call(messages, provider, model, temperature=temperature, track_cost=False)
        
        # 计算耗时
        duration_ms = int((time.time() - start_time) * 1000)
        
        # 估算输出token数
        output_tokens = len(response.content) // 4 if response.content else 0
        
        # 获取provider和model
        provider_name = provider or getattr(self.llm_manager, '_default_provider', 'glm')
        model_name = model or getattr(self.llm_manager, '_default_model', 'glm-4-flash')
        
        # 记录成本
        try:
            track_cost(
                agent_name=self.name,
                provider=provider_name,
                model=model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration_ms,
                query=prompt[:100],
            )
        except Exception as e:
            print(f"[CostTracker] Failed to track: {e}")
        
        return response

    async def _acall_llm(
        self,
        prompt: str,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        import time
        from gfa_core.mas.cost_tracker import track_cost
        
        # 自动加载并附加系统提示词
        system_prompt = self._build_system_prompt()
        full_prompt = f"""{system_prompt}

---

用户请求: {prompt}

请根据上述系统提示词的指导处理此请求。"""
        messages = [LLMMessage(role="user", content=full_prompt)]
        
        # 记录开始时间
        start_time = time.time()
        
        # 估算输入token数（粗略估算：1token ≈ 4字符）
        input_tokens = len(full_prompt) // 4
        
        # 调用LLM
        response = await self.llm_manager.acall(messages, provider, model, temperature=temperature, track_cost=False)
        
        # 计算耗时
        duration_ms = int((time.time() - start_time) * 1000)
        
        # 估算输出token数
        output_tokens = len(response.content) // 4 if response.content else 0
        
        # 获取provider和model
        provider_name = provider or getattr(self.llm_manager, '_default_provider', 'glm')
        model_name = model or getattr(self.llm_manager, '_default_model', 'glm-4-flash')
        
        # 记录成本
        try:
            track_cost(
                agent_name=self.name,
                provider=provider_name,
                model=model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration_ms,
                query=prompt[:100],
            )
        except Exception as e:
            print(f"[CostTracker] Failed to track: {e}")
        
        return response

    def _build_system_prompt(self) -> str:
        """从文件加载系统提示词，如果不存在则返回默认提示词"""
        # 尝试加载详细提示词文件
        prompt_file = PROMPTS_DIR / f"{self.name}_agent.md"
        
        if prompt_file.exists():
            try:
                with open(prompt_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # 如果文件内容有效，返回文件内容
                    if len(content) > 500:  # 确保不是空文件或简单内容
                        return content
            except Exception as e:
                print(f"[Warning] Failed to load prompt from {prompt_file}: {e}")
        
        # 回退到默认提示词
        return f"You are a {self.role} in a gene family analysis system."
    
    def get_system_prompt(self) -> str:
        """获取系统提示词（供外部调用）"""
        return self._build_system_prompt()
