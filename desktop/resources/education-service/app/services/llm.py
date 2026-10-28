"""services/llm.py —— LLM 调用（httpx 裸调 DeepSeek，模式同 paper_writer_service）。

- complete_text(system, user, *, temperature=0.3, max_tokens=None, timeout=None) -> str
- complete_json(...)（response_format json_object + 一次 JSON 修复重试）
- 4 次尝试（1.5s / 3s / 6s 指数退避，429 / 5xx / 超时）
- env 键（优先 REVIEW_LLM_*，回退 DOCGEN_LLM_*，再回退内置默认）：
  *_LLM_BASE_URL / *_LLM_API_KEY / *_LLM_MODEL / *_LLM_TIMEOUT
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

DEFAULT_LLM_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_LLM_MODEL = "deepseek-chat"
DEFAULT_LLM_TIMEOUT = 300.0
BACKOFF_SECONDS = (1.5, 3.0, 6.0)
MAX_ATTEMPTS = 4


class LLMError(Exception):
    """LLM 调用失败（重试尽/响应异常）。"""

    def __init__(self, message: str, code: str = "UPSTREAM_DOWN") -> None:
        super().__init__(message)
        self.code = code


def _env(keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        val = os.environ.get(key)
        if val:
            return val
    return default


def _llm_config() -> dict[str, Any]:
    return {
        "base_url": _env(
            ("REVIEW_LLM_BASE_URL", "DOCGEN_LLM_BASE_URL"), DEFAULT_LLM_BASE_URL
        ).rstrip("/"),
        "api_key": _env(("REVIEW_LLM_API_KEY", "DOCGEN_LLM_API_KEY"), ""),
        "model": _env(("REVIEW_LLM_MODEL", "DOCGEN_LLM_MODEL"), DEFAULT_LLM_MODEL),
        "timeout": float(
            _env(("REVIEW_LLM_TIMEOUT", "DOCGEN_LLM_TIMEOUT"), str(DEFAULT_LLM_TIMEOUT))
        ),
    }


def _extract_text(payload: dict[str, Any]) -> str:
    try:
        choices = payload.get("choices") or []
        if choices:
            content = choices[0].get("message", {}).get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    str(c.get("text", "")) if isinstance(c, dict) else str(c) for c in content
                )
    except Exception:
        pass
    return ""


async def _chat_once(
    client: httpx.AsyncClient,
    cfg: dict[str, Any],
    system: str,
    user: str,
    *,
    temperature: float,
    max_tokens: int | None,
    json_mode: bool,
    timeout: float | None,
) -> str:
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    body: dict[str, Any] = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    resp = await client.post(
        f"{cfg['base_url']}/chat/completions",
        headers=headers,
        json=body,
        timeout=timeout if timeout is not None else cfg["timeout"],
    )
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        raise LLMError("LLM 响应形态异常（非 JSON 对象）")
    choices = payload.get("choices") or []
    if choices and isinstance(choices[0], dict) and choices[0].get("finish_reason") == "length":
        raise LLMError("模型输出达到上限，结果不完整。", "OUTPUT_TRUNCATED")
    text = _extract_text(payload)
    if not text:
        raise LLMError("LLM 响应缺少 choices[0].message.content")
    return text


async def _complete(
    system: str,
    user: str,
    *,
    temperature: float,
    max_tokens: int | None,
    json_mode: bool,
    timeout: float | None,
) -> str:
    cfg = _llm_config()
    if not cfg["api_key"]:
        raise LLMError("模型服务尚未连接，请重新打开教育科研页面。", "MODEL_NOT_CONNECTED")
    last_exc: BaseException | None = None
    # The desktop bridge is local. Windows system proxies must not intercept it.
    # Keep remote standalone-service proxy behavior unchanged.
    loopback = urlsplit(cfg["base_url"]).hostname in {"127.0.0.1", "::1", "localhost"}
    async with httpx.AsyncClient(trust_env=not loopback) as client:
        for attempt in range(MAX_ATTEMPTS):
            try:
                return await _chat_once(
                    client, cfg, system, user,
                    temperature=temperature, max_tokens=max_tokens, json_mode=json_mode,
                    timeout=timeout,
                )
            except LLMError:
                raise
            except (httpx.HTTPStatusError, httpx.HTTPError, httpx.TimeoutException) as exc:
                last_exc = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                bridge_code = None
                if getattr(exc, "response", None) is not None:
                    try:
                        bridge_code = exc.response.json().get("error", {}).get("code")
                    except (ValueError, AttributeError):
                        pass
                if status == 401:
                    if bridge_code == "model_credential_rejected":
                        raise LLMError("模型服务拒绝了临时凭据，请联系管理员。", "MODEL_AUTH") from exc
                    raise LLMError("登录已失效，请重新登录后重试。", "AUTH_EXPIRED") from exc
                if status == 403:
                    raise LLMError("当前账号暂不能使用模型服务，请联系管理员。", "MODEL_FORBIDDEN") from exc
                if status in (400, 413):
                    code = "INPUT_TOO_LONG" if status == 413 or bridge_code == "context_length_exceeded" else "MODEL_REQUEST_INVALID"
                    raise LLMError(f"模型服务未接受审稿请求（HTTP {status}）。", code) from exc
                retryable = status is None or status == 429 or status >= 500
                if not retryable or attempt == MAX_ATTEMPTS - 1:
                    if isinstance(exc, httpx.TimeoutException) or status in (408, 504):
                        raise LLMError("审稿请求超时，请稍后重试。", "TIMEOUT") from exc
                    if status == 429:
                        raise LLMError("模型服务请求过于频繁，请稍后重试。", "RATE_LIMIT") from exc
                    if status is None:
                        raise LLMError("无法连接模型服务，请检查网络后重试。", "NETWORK") from exc
                    raise LLMError(f"模型服务暂时不可用（HTTP {status}），请稍后重试。") from exc
                await asyncio.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
    raise LLMError(f"LLM 调用失败：{last_exc}")


async def complete_text(
    system: str,
    user: str,
    *,
    temperature: float = 0.3,
    max_tokens: int | None = None,
    timeout: float | None = None,
) -> str:
    """文本补全。失败抛 LLMError。"""
    return await _complete(
        system, user, temperature=temperature, max_tokens=max_tokens, json_mode=False,
        timeout=timeout,
    )


def _repair_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    if not text:
        raise LLMError("LLM 返回空内容")
    if text.startswith("{"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    raise LLMError("LLM 输出中未找到 JSON 对象")


async def complete_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.3,
    max_tokens: int | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """JSON 补全（json_object + 一次修复重试）。"""
    text = await _complete(
        system, user, temperature=temperature, max_tokens=max_tokens, json_mode=True,
        timeout=timeout,
    )
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass
    text2 = await _complete(
        system + "\n（重要：你的回答必须是单个合法 JSON 对象，不要输出其他任何内容）",
        user,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=True,
        timeout=timeout,
    )
    try:
        parsed = json.loads(_repair_json(text2))
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, LLMError):
        pass
    raise LLMError("模型返回的结果格式异常，请重试。", "MODEL_INVALID_RESPONSE")
