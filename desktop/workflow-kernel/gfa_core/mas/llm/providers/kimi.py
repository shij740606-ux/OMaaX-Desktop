"""Kimi Provider - 使用阿里云百炼平台"""
import asyncio
from typing import AsyncIterator, Iterator

from openai import OpenAI

from ..base import BaseLLMProvider, LLMMessage, LLMResponse


class KimiProvider(BaseLLMProvider):
    """Kimi模型Provider - 通过阿里云百炼Coding Plan平台调用
    
    支持的模型:
    - kimi-k2.5: 最强模型，适合复杂分析推理
    - kimi-k2.5-latest: 最新版本
    - kimi-latest: 最新轻量版
    """
    provider_name = "kimi"
    # 阿里云百炼Coding接口
    CODING_BASE_URL = "https://coding.dashscope.aliyuncs.com/v1"
    
    # 百炼平台模型名称映射
    MODEL_MAPPING = {
        "kimi-k2.5": "kimi-k2.5",
        "kimi-k2.5-latest": "kimi-k2.5",
        "kimi-latest": "kimi-k2.5",
        "moonshot-v1-8k": "kimi-k2.5",
        "moonshot-v1-128k": "kimi-k2.5",
    }

    def __init__(
        self,
        api_key: str,
        model: str = "kimi-k2.5",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        api_base: str = None,
    ):
        # 映射模型名称
        coding_model = self.MODEL_MAPPING.get(model, model)
        
        # 使用传入的base_url或默认的Coding接口
        base_url = api_base or self.CODING_BASE_URL
        
        super().__init__(
            api_key=api_key,
            api_base=base_url,
            model=coding_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        print(f"[DEBUG] KimiProvider initialized. api_key_configured={bool(api_key)}, base_url: {base_url}")

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
