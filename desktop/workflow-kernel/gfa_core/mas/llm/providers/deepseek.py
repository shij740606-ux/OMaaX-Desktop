"""DeepSeek Provider - 使用 DeepSeek OpenAI-compatible API"""
import asyncio
from typing import AsyncIterator, Iterator

from openai import OpenAI

from ..base import BaseLLMProvider, LLMMessage, LLMResponse, LLMToolCall


class DeepSeekProvider(BaseLLMProvider):
    """DeepSeek模型Provider - 通过 DeepSeek OpenAI-compatible API 调用
    
    支持的模型:
    - deepseek-chat: 通用对话模型
    - deepseek-reasoner: 推理模型（适合复杂分析）
    """
    provider_name = "deepseek"
    DEFAULT_BASE_URL = "https://api.deepseek.com"
    
    # 百炼平台模型名称映射
    MODEL_MAPPING = {
        "deepseek-chat": "deepseek-chat",
        "deepseek-reasoner": "deepseek-reasoner",
        "deepseek-v3": "deepseek-chat",
        "deepseek-r1": "deepseek-reasoner",
        "deepseek-v4-flash": "deepseek-v4-flash",
        "deepseek-v4-pro": "deepseek-v4-pro",
    }

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        api_base: str = None,
    ):
        # 映射模型名称
        deepseek_model = self.MODEL_MAPPING.get(model, model)
        base_url = api_base or self.DEFAULT_BASE_URL
        
        super().__init__(
            api_key=api_key,
            api_base=base_url,
            model=deepseek_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def call(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        request_kwargs = {
            "model": self.model,
            "messages": self._build_messages(messages),
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
        }
        tools = kwargs.get("tools")
        if tools is not None:
            request_kwargs["tools"] = tools
        if tools is not None or kwargs.get("disable_thinking", False):
            request_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        tool_choice = kwargs.get("tool_choice")
        if tool_choice is not None:
            request_kwargs["tool_choice"] = tool_choice
        response_format = kwargs.get("response_format")
        if response_format is not None:
            request_kwargs["response_format"] = response_format

        response = self.client.chat.completions.create(**request_kwargs)
        message = response.choices[0].message
        tool_calls = [
            LLMToolCall(
                id=tool_call.id,
                name=tool_call.function.name,
                arguments=tool_call.function.arguments,
            )
            for tool_call in (message.tool_calls or [])
        ]
        return LLMResponse(
            content=message.content or "",
            model=self.model,
            provider=self.provider_name,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            },
            finish_reason=response.choices[0].finish_reason,
            tool_calls=tool_calls,
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
