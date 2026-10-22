"""services/llm.py —— LLM 调用（httpx 裸调 DeepSeek，模式同 paper_writer_service）。

- complete_text(system, user, *, temperature=0.3, max_tokens=8000, timeout=None) -> str
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

import httpx

logger = logging.getLogger(__name__)

DEFAULT_LLM_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_LLM_MODEL = "deepseek-chat"
DEFAULT_LLM_TIMEOUT = 300.0
BACKOFF_SECONDS = (1.5, 3.0, 6.0)
MAX_ATTEMPTS = 4


class LLMError(Exception):
    """LLM 调用失败（重试尽/响应异常）。"""


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
    max_tokens: int,
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
        "max_tokens": max_tokens,
    }
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
    text = _extract_text(payload)
    if not text:
        raise LLMError("LLM 响应缺少 choices[0].message.content")
    return text


async def _complete(
    system: str,
    user: str,
    *,
    temperature: float,
    max_tokens: int,
    json_mode: bool,
    timeout: float | None,
) -> str:
    cfg = _llm_config()
    if not cfg["api_key"]:
        raise LLMError("本机未找到已保存的模型 API Key")
    last_exc: BaseException | None = None
    async with httpx.AsyncClient() as client:
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
                retryable = status is None or status == 429 or status >= 500
                if not retryable or attempt == MAX_ATTEMPTS - 1:
                    raise LLMError(f"LLM 调用失败（HTTP {status or '网络'}）：{exc}") from exc
                await asyncio.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
    raise LLMError(f"LLM 调用失败：{last_exc}")


async def complete_text(
    system: str,
    user: str,
    *,
    temperature: float = 0.3,
    max_tokens: int = 8000,
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
    max_tokens: int = 8000,
    timeout: float | None = None,
) -> dict[str, Any]:
    """JSON 补全（json_object + 一次修复重试）。"""
    text = await _complete(
        system, user, temperature=temperature, max_tokens=max_tokens, json_mode=True,
        timeout=timeout,
    )
    try:
        return json.loads(text)
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
        return json.loads(text2)
    except ValueError:
        repaired = _repair_json(text2)
        return json.loads(repaired)
