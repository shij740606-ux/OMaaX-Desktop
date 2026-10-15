import os
import json
import httpx
import asyncio
from typing import AsyncIterator, Iterator
from ..base import BaseLLMProvider, LLMMessage, LLMResponse


class OllamaProvider(BaseLLMProvider):
    DEFAULT_OLLAMA_HOST = "http://localhost:11434"
    
    def __init__(
        self,
        api_key: str = "",
        model: str = "qwen3:0.6b",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        base_url: str = None,
        **kwargs
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.base_url = base_url or os.environ.get("OLLAMA_HOST", self.DEFAULT_OLLAMA_HOST)
        
    def _get_client(self):
        # Create a new client for each call to avoid event loop issues
        return httpx.Client(base_url=self.base_url, timeout=120.0)
        
    def _convert_messages(self, messages: list[LLMMessage]) -> list[dict]:
        ollama_messages = []
        for msg in messages:
            role = msg.role
            if role == "system":
                role = "system"
            elif role == "user":
                role = "user"
            elif role == "assistant":
                role = "assistant"
            ollama_messages.append({
                "role": role,
                "content": msg.content
            })
        return ollama_messages
    
    def call(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        # Use synchronous httpx.Client
        client = self._get_client()
        temperature = kwargs.get("temperature", self.temperature)
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        
        ollama_messages = self._convert_messages(messages)
        
        payload = {
            "model": self.model,
            "messages": ollama_messages,
            "temperature": temperature,
            "options": {
                "num_predict": max_tokens
            },
            "stream": False
        }
        
        try:
            response = client.post("/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
            
            content = data.get("message", {}).get("content", "")
            return LLMResponse(
                content=content,
                model=self.model,
                provider="ollama",
                usage={
                    "prompt_tokens": data.get("prompt_eval_count", 0),
                    "completion_tokens": data.get("eval_count", 0),
                    "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0)
                }
            )
        except httpx.HTTPStatusError as e:
            raise Exception(f"Ollama API error: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            raise Exception(f"Ollama call failed: {str(e)}")
        finally:
            client.close()
    
    async def acall(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        # For async context, use async client
        async with httpx.AsyncClient(base_url=self.base_url, timeout=120.0) as client:
            temperature = kwargs.get("temperature", self.temperature)
            max_tokens = kwargs.get("max_tokens", self.max_tokens)
            
            ollama_messages = self._convert_messages(messages)
            
            payload = {
                "model": self.model,
                "messages": ollama_messages,
                "temperature": temperature,
                "options": {
                    "num_predict": max_tokens
                },
                "stream": False
            }
            
            try:
                response = await client.post("/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()
                
                content = data.get("message", {}).get("content", "")
                return LLMResponse(
                    content=content,
                    model=self.model,
                    provider="ollama",
                    usage={
                        "prompt_tokens": data.get("prompt_eval_count", 0),
                        "completion_tokens": data.get("eval_count", 0),
                        "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0)
                    }
                )
            except httpx.HTTPStatusError as e:
                raise Exception(f"Ollama API error: {e.response.status_code} - {e.response.text}")
            except Exception as e:
                raise Exception(f"Ollama call failed: {str(e)}")
    
    def stream(self, messages: list[LLMMessage], **kwargs) -> Iterator[str]:
        result = self.call(messages, **kwargs)
        yield result.content
    
    async def astream(self, messages: list[LLMMessage], **kwargs) -> AsyncIterator[str]:
        result = await self.acall(messages, **kwargs)
        yield result.content
