from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Iterator


@dataclass
class LLMToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class LLMMessage:
    role: str
    content: str = ""
    name: str = ""
    tool_call_id: str = ""
    tool_calls: list[LLMToolCall] = field(default_factory=list)


@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    usage: dict
    finish_reason: str = "stop"
    tool_calls: list[LLMToolCall] = field(default_factory=list)


class BaseLLMProvider(ABC):
    provider_name: str

    def __init__(
        self,
        api_key: str,
        api_base: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        self.api_key = api_key
        self.api_base = api_base
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    @abstractmethod
    def call(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        pass

    @abstractmethod
    async def acall(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        pass

    @abstractmethod
    def stream(self, messages: list[LLMMessage], **kwargs) -> Iterator[str]:
        pass

    @abstractmethod
    async def astream(self, messages: list[LLMMessage], **kwargs) -> AsyncIterator[str]:
        pass

    def _build_messages(self, messages: list[LLMMessage]) -> list[dict]:
        result = []
        for message in messages:
            item = {"role": message.role, "content": message.content}
            if message.name:
                item["name"] = message.name
            if message.tool_call_id:
                item["tool_call_id"] = message.tool_call_id
            if message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.name,
                            "arguments": tool_call.arguments,
                        },
                    }
                    for tool_call in message.tool_calls
                ]
            result.append(item)
        return result
