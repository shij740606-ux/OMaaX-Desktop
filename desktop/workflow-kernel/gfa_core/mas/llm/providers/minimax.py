"""MiniMax Provider - 使用阿里云百炼平台"""
import asyncio
from typing import AsyncIterator, Iterator

from openai import OpenAI

from ..base import BaseLLMProvider, LLMMessage, LLMResponse


class MiniMaxProvider(BaseLLMProvider):
    """MiniMax模型Provider - 通过阿里云百炼平台调用
    
    支持的模型:
    - minimax-text-01: 文本模型，适合知识查询
    - minimax-speech-01: 语音模型（暂不支持）
    """
    provider_name = "minimax"
    DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    
    # 百炼平台模型名称映射
    MODEL_MAPPING = {
        "minimax-text-01": "minimax-text-01",
        "abab6.5s-chat": "minimax-text-01",
        "abab6.5-chat": "minimax-text-01",
    }

    def __init__(
        self,
        api_key: str,
        model: str = "minimax-text-01",
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
