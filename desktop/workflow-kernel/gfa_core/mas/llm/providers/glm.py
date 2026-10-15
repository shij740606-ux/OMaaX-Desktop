"""GLM Provider - 使用阿里云百炼平台"""
import asyncio
from typing import AsyncIterator, Iterator

from openai import OpenAI

from ..base import BaseLLMProvider, LLMMessage, LLMResponse


class GLMProvider(BaseLLMProvider):
    """智谱GLM模型Provider - 通过阿里云百炼平台调用
    
    支持的模型:
    - glm-4: 最强模型，适合复杂分析推理
    - glm-4-plus: 增强版
    - glm-4-flash: 轻量级，速度快
    """
    provider_name = "glm"
    DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    
    # 百炼平台模型名称映射
    MODEL_MAPPING = {
        "glm-4": "glm-4",
        "glm-4-plus": "glm-4-plus",
        "glm-4-flash": "glm-4-flash",
        "glm-5": "glm-4",  # 兼容旧配置
    }

    def __init__(
        self,
        api_key: str,
        model: str = "glm-4",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        # 映射模型名称
        dashscope_model = self.MODEL_MAPPING.get(model, model)
        
        super().__init__(
            api_key=api_key,
            api_base=self.DASHSCOPE_BASE_URL,
            model=dashscope_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.client = OpenAI(api_key=api_key, base_url=self.DASHSCOPE_BASE_URL)

    def call(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=self._build_messages(messages),
            temperature=kwargs.get("temperature", self.temperature),
            max_tokens=kwargs.get("max_tokens", self.max_tokens),
        )
        return LLMResponse(
            content=response.choices[0].message.content,
            model=self.model,
            provider=self.provider_name,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            },
            finish_reason=response.choices[0].finish_reason,
        )

    async def acall(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        return await asyncio.to_thread(self.call, messages, **kwargs)

    def stream(self, messages: list[LLMMessage], **kwargs) -> Iterator[str]:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=self._build_messages(messages),
            temperature=kwargs.get("temperature", self.temperature),
            max_tokens=kwargs.get("max_tokens", self.max_tokens),
            stream=True,
        )
        for chunk in response:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def astream(self, messages: list[LLMMessage], **kwargs) -> AsyncIterator[str]:
        for content in self.stream(messages, **kwargs):
            yield content
