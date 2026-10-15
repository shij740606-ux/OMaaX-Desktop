import os
import time
from typing import AsyncIterator, Iterator

from .base import BaseLLMProvider, LLMMessage, LLMResponse
from .providers.deepseek import DeepSeekProvider
from .providers.glm import GLMProvider
from .providers.minimax import MiniMaxProvider
from .providers.ollama import OllamaProvider


class LLMManager:
    PROVIDERS = {
        "glm": GLMProvider,
        "minimax": MiniMaxProvider,
        "deepseek": DeepSeekProvider,
        "ollama": OllamaProvider,
    }

    DEFAULT_MODELS = {
        "glm": "glm-4-flash",
        "minimax": "abab6.5s-chat",
        "deepseek": "deepseek-v4-flash",
        "ollama": "qwen3:0.6b",
    }

    def __init__(self, config: dict):
        self.config = config
        self._providers: dict[str, BaseLLMProvider] = {}
        self._available_providers: list[str] | None = None
        self._default_provider = config.get("default", {}).get("provider", "glm")
        self._default_model = config.get("default", {}).get("model", "glm-4-flash")
        self._default_temperature = config.get("default", {}).get("temperature", 0.7)
        self._default_max_tokens = config.get("default", {}).get("max_tokens", 4096)

    def _get_api_key(self, env_key: str) -> str | None:
        provider_key = ""
        if env_key:
            provider_key = self.config.get(env_key.lower(), "") or os.environ.get(env_key, "")
        if provider_key:
            return provider_key

        # Backward-compatible fallback for OpenAI-compatible providers.
        openai_key = self.config.get("openai_api_key", "") or os.environ.get("OPENAI_API_KEY", "")
        if openai_key:
            return openai_key

        return ""

    def _check_ollama_available(self) -> bool:
        try:
            import requests
            response = requests.get("http://localhost:11434/api/tags", timeout=3)
            return response.status_code == 200
        except Exception:
            return False

    def _get_available_providers(self) -> list[str]:
        if self._available_providers is not None:
            return self._available_providers

        available = []
        providers_config = self.config.get("providers", {})
        
        # 检查是否有阿里云百炼的openai_api_key
        openai_key = self.config.get("openai_api_key", "")
        
        for name, cfg in providers_config.items():
            if name == "ollama":
                if self._check_ollama_available():
                    available.append(name)
            else:
                env_key = cfg.get("env_key", "")
                if self._get_api_key(env_key) or openai_key:
                    available.append(name)

        self._available_providers = available
        return available

    def get_provider(
        self,
        provider_name: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        allow_fallback: bool = True,
    ) -> BaseLLMProvider:
        provider_name = provider_name or self._default_provider
        model = model or self._default_model
        temperature = temperature or self._default_temperature
        max_tokens = max_tokens or self._default_max_tokens

        cache_key = f"{provider_name}:{model}"
        # 强制重新创建provider，避免缓存问题
        if cache_key in self._providers:
            del self._providers[cache_key]

        provider_config = self.config.get("providers", {}).get(provider_name, {})
        env_key = provider_config.get("env_key", "")
        api_key = self._get_api_key(env_key)

        if not api_key:
            if allow_fallback:
                available = self._get_available_providers()
                if available:
                    provider_name = available[0]
                    model = self.DEFAULT_MODELS.get(provider_name, "")
                    provider_config = self.config.get("providers", {}).get(provider_name, {})
                    env_key = provider_config.get("env_key", "")
                    api_key = self._get_api_key(env_key)
                    cache_key = f"{provider_name}:{model}"
                    if cache_key in self._providers:
                        return self._providers[cache_key]
            else:
                raise ValueError(f"API key not found: {env_key}")

        if not api_key and provider_name != "ollama":
            raise ValueError(
                "No LLM API key configured. Please set at least one of: "
                "ZHIPUAI_API_KEY, MOONSHOT_API_KEY, MINIMAX_API_KEY, DEEPSEEK_API_KEY"
            )

        provider_class = self.PROVIDERS.get(provider_name)
        if not provider_class:
            raise ValueError(f"Unknown provider: {provider_name}")

        provider_kwargs = {
            "api_key": api_key,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        provider_base_url = provider_config.get("api_base") or self.config.get("openai_base_url")
        if provider_base_url:
            import inspect

            signature = inspect.signature(provider_class.__init__)
            if "api_base" in signature.parameters:
                provider_kwargs["api_base"] = provider_base_url

        provider = provider_class(**provider_kwargs)
        self._providers[cache_key] = provider
        return provider

    def get_available_providers_info(self) -> list[dict]:
        available = self._get_available_providers()
        result = []
        for name in available:
            cfg = self.config.get("providers", {}).get(name, {})
            result.append({
                "name": name,
                "display_name": cfg.get("name", name),
                "default_model": self.DEFAULT_MODELS.get(name, ""),
                "models": [m.get("name") for m in cfg.get("models", [])],
            })
        return result

    def _track_llm_cost(self, response: LLMResponse, messages: list[LLMMessage], duration_ms: int, agent_name: str = "LLMManager") -> None:
        try:
            from gfa_core.mas.cost_tracker import track_cost

            usage = response.usage or {}
            input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            if not input_tokens and messages:
                input_tokens = sum(len(m.content or "") for m in messages) // 4
            if not output_tokens and response.content:
                output_tokens = len(response.content) // 4
            track_cost(
                agent_name=agent_name,
                provider=response.provider or self._default_provider,
                model=response.model or self._default_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration_ms,
                query=(messages[-1].content if messages else "")[:100],
            )
        except Exception as exc:
            print(f"[CostTracker] LLMManager track failed: {exc}")

    def call(
        self,
        messages: list[LLMMessage],
        provider: str | None = None,
        model: str | None = None,
        allow_fallback: bool = True,
        **kwargs,
    ) -> LLMResponse:
        track_usage = bool(kwargs.pop("track_cost", True))
        track_agent_name = str(kwargs.pop("track_agent_name", "LLMManager") or "LLMManager")
        p = self.get_provider(provider, model, allow_fallback=allow_fallback)
        start_time = time.time()
        response = p.call(messages, **kwargs)
        if track_usage:
            self._track_llm_cost(response, messages, int((time.time() - start_time) * 1000), track_agent_name)
        return response

    async def acall(
        self,
        messages: list[LLMMessage],
        provider: str | None = None,
        model: str | None = None,
        allow_fallback: bool = True,
        **kwargs,
    ) -> LLMResponse:
        track_usage = bool(kwargs.pop("track_cost", True))
        track_agent_name = str(kwargs.pop("track_agent_name", "LLMManager") or "LLMManager")
        p = self.get_provider(provider, model, allow_fallback=allow_fallback)
        start_time = time.time()
        response = await p.acall(messages, **kwargs)
        if track_usage:
            self._track_llm_cost(response, messages, int((time.time() - start_time) * 1000), track_agent_name)
        return response

    def stream(
        self,
        messages: list[LLMMessage],
        provider: str | None = None,
        model: str | None = None,
        allow_fallback: bool = True,
        **kwargs,
    ) -> Iterator[str]:
        p = self.get_provider(provider, model, allow_fallback=allow_fallback)
        return p.stream(messages, **kwargs)

    async def astream(
        self,
        messages: list[LLMMessage],
        provider: str | None = None,
        model: str | None = None,
        allow_fallback: bool = True,
        **kwargs,
    ) -> AsyncIterator[str]:
        p = self.get_provider(provider, model, allow_fallback=allow_fallback)
        async for chunk in p.astream(messages, **kwargs):
            yield chunk
