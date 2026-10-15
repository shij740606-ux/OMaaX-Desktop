"""轻量版拓展分析的自然语言触发解析。

从已发布的轻量拓展记录（store_root/extensions/*/extension.json）中，用 DeepSeek
把自然语言消息解析为一次运行意图：
- 粗筛（_rough_trigger/_rough_candidates）先用关键词判断消息是否可能命中任一已发布拓展，
  未命中时完全不发起 LLM 调用；
- 命中后把候选目录与 job 文件提示交给 LLM，要求返回严格的 JSON resolution；
- 所有外部输入经过 _sanitize_resolution 白名单校验，本模块任何函数都不对外抛异常。

对外入口：resolve_lightweight_extension_intent(message, published_records, ...)。
测试可注入 client_factory(config) 以获得不依赖真实环境的假客户端。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping

PREFILL_THRESHOLD = 0.6
MAX_CATALOG_CHARS = 24000
MAX_JOB_FILE_HINTS = 200

_ENGLISH_STOPWORDS = {"analysis", "data", "file", "run", "input", "output", "files", "using", "tool"}
_CHINESE_STOPWORDS = {"分析", "序列", "文件", "数据", "蛋白", "拓展", "扩展", "工具", "进行", "搜索"}
_STRONG_TRIGGER_TOKENS = ("拓展分析", "扩展分析", "extension")

_RESPONSE_SYSTEM_PROMPT = (
    "你是植物基因组分析平台的自然语言意图解析器。"
    "用户消息会引用一个轻量拓展分析（lightweight extension analysis）。"
    "你必须只返回一个严格 JSON 对象（不要 markdown、不要多余文字），格式：\n"
    '{"extension_id": 字符串或 null, "input_overrides": [{"path": "...", "value": "..."}], '
    '"parameter_overrides": [{"fact_id": "...", "value": "..."}], "explicit_prefix": 字符串或 null, '
    '"confidence": 0到1之间的数字}\n\n'
    "规则：\n"
    "1. extension_id 只能从给定 extensions 列表里选；无法确定时填 null。\n"
    "2. input_overrides 里的 path 只能引用该拓展 inputs[].path；parameter_overrides 里的 fact_id "
    "只能引用该拓展 parameters[].fact_id。\n"
    "3. 参数值只能来自用户消息原文；用户没提到的参数不要出现。\n"
    "4. 用户提到的文件优先与 job_files 匹配，统一写成 \"input/<job_id>/<文件名>\" 或 "
    "\"workspace/<job_id>/<文件名>\" 的 job 相对形式；禁止绝对路径。用户未指明具体文件时，可引用 workspace/<job_id>/extension_runs/<其他拓展>/runtime_*/outputs/ 下已有的上游产物。\n"
    "5. 禁止编造任何目录、路径、参数或数值。\n"
    '6. 输出必须能被 JSON.parse 直接解析。'
)


@dataclass(frozen=True)
class LightweightIntentResolution:
    extension_id: str | None
    extension_name: str | None
    input_overrides: dict[str, str]
    parameter_overrides: dict[str, Any]
    explicit_prefix: str | None
    confidence: float
    prefill: bool
    reason: str | None
    candidates_considered: list[str]


def load_published_lightweight_records(store_root: Path) -> list[dict]:
    """加载 store_root/extensions/*/extension.json 中 status=="published" 的记录；坏文件跳过。"""
    try:
        candidates = sorted(Path(store_root).glob("extensions/*/extension.json"))
    except (OSError, TypeError, ValueError):
        return []
    records: list[dict] = []
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"[LightweightNLIntent] skip bad record {path}: {exc}")
            continue
        if not isinstance(data, dict):
            print(f"[LightweightNLIntent] skip bad record {path}: not an object")
            continue
        if data.get("status") == "published":
            records.append(data)
    return records


def _function_summary_text(record: Mapping[str, Any]) -> str:
    candidate = record.get("semantic_candidate")
    if not isinstance(candidate, Mapping):
        return ""
    return str(candidate.get("function_summary") or "")


def _summary_keywords(record: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """从 function_summary 抽英文 token（len>=3）与中文子串（len>=2），过滤停用词。"""
    summary = _function_summary_text(record).lower()
    english = [
        token for token in re.split(r"[^A-Za-z0-9]+", summary)
        if len(token) >= 3 and token not in _ENGLISH_STOPWORDS
    ]
    chinese_segments = re.findall(r"[一-鿿]+", summary)
    chinese: list[str] = []
    for segment in chinese_segments:
        length = len(segment)
        for size in range(2, min(4, length) + 1):
            for start in range(length - size + 1):
                sub = segment[start:start + size]
                if sub not in _CHINESE_STOPWORDS and sub not in chinese:
                    chinese.append(sub)
    return english, chinese


def _extension_id_tokens(record: Mapping[str, Any]) -> list[str]:
    """extension_id 里的英文 token（如 03___hmm_... → hmm、07_pangene__orthofinder_... → pangene/orthofinder）。"""
    extension_id = str(record.get("extension_id") or "").lower()
    return [
        token for token in re.split(r"[^A-Za-z0-9]+", extension_id)
        if len(token) >= 3 and token not in _ENGLISH_STOPWORDS
    ]


def _parameter_flag_tokens(record: Mapping[str, Any]) -> list[str]:
    """parameters 的 flag 去前导 - 后作为关键词（如 --samples → samples）。"""
    contract = record.get("runtime_binding_contract")
    if not isinstance(contract, Mapping):
        return []
    tokens: list[str] = []
    for item in contract.get("parameters") or []:
        if not isinstance(item, Mapping):
            continue
        flag = str(item.get("flag") or "").strip().lower()
        for token in re.split(r"[^A-Za-z0-9]+", flag):
            if len(token) >= 3 and token not in _ENGLISH_STOPWORDS:
                tokens.append(token)
    return tokens


def _record_keyword_sources(record: Mapping[str, Any]) -> list[str]:
    """一条记录的全部粗筛关键词：summary 英文/中文子串 + extension_id token + 参数 flag。"""
    english, chinese = _summary_keywords(record)
    return english + chinese + _extension_id_tokens(record) + _parameter_flag_tokens(record)


def _rough_candidates(message: str, records: list[dict]) -> list[str]:
    """按关键词粗筛可能命中的记录 id；不含 LLM 调用。

    union 策略：先剔除强触发词（拓展分析/扩展分析/extension）后按关键词源
    匹配子集，命中数降序（同分按 id 排序）；除强触发词外没有任何关键词命中
    （纯「拓展分析」）时才返回全量候选兜底，否则返回 []。
    """
    text = str(message or "").lower()
    stripped = text
    for token in _STRONG_TRIGGER_TOKENS:
        stripped = stripped.replace(token, " ")
    scored: list[tuple[int, str]] = []
    for record in records:
        extension_id = str(record.get("extension_id") or "").strip()
        if not extension_id:
            continue
        hits = sum(1 for keyword in _record_keyword_sources(record) if keyword and keyword in stripped)
        if hits:
            scored.append((hits, extension_id))
    if scored:
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [extension_id for _, extension_id in scored]
    if any(token in text for token in _STRONG_TRIGGER_TOKENS):
        return [str(record.get("extension_id")) for record in records if record.get("extension_id")]
    return []


def compact_extension_name(extension_id: str) -> str:
    """extension_id 的紧凑展示形式：去掉尾缀时间戳（_后 10 位以上数字），再截断 30 字符。"""
    value = str(extension_id or "").strip()
    compact = re.sub(r"_\d{10,}$", "", value)
    if compact:
        return compact[:30]
    return value[:30]


def _rough_trigger(message: str, records: list[dict]) -> bool:
    """消息是否可能命中任一已发布轻量拓展（粗筛布尔版）。"""
    return bool(_rough_candidates(message, records))


def _build_catalog(records: list[dict]) -> dict:
    """构造发给 LLM 的拓展目录；序列化后总长超过 MAX_CATALOG_CHARS 时截断。

    records 顺序即目录顺序（_rough_candidates 已按命中关键词数降序排好）。
    """
    items: list[dict] = []
    seen_ids: set[str] = set()
    total = 0
    truncated = False
    for record in records:
        extension_id = str(record.get("extension_id") or "").strip()
        if not extension_id or extension_id in seen_ids:
            continue
        seen_ids.add(extension_id)
        contract = record.get("runtime_binding_contract")
        if not isinstance(contract, Mapping):
            continue
        inputs = [
            {
                "path": str(item.get("path") or ""),
                "role": str(item.get("role") or ""),
                "description": str(item.get("description") or ""),
            }
            for item in contract.get("inputs") or []
            if isinstance(item, Mapping) and item.get("replaceable")
        ]
        parameters = [
            {
                "fact_id": str(item.get("fact_id") or ""),
                "flag": str(item.get("flag") or ""),
                "display_name": str(item.get("display_name") or ""),
                "description": str(item.get("description") or ""),
            }
            for item in contract.get("parameters") or []
            if isinstance(item, Mapping)
        ]
        entry = {
            "extension_id": extension_id,
            "function_summary": _function_summary_text(record)[:300],
            "inputs": inputs,
            "parameters": parameters,
        }
        size = len(json.dumps(entry, ensure_ascii=False))
        if total + size > MAX_CATALOG_CHARS:
            truncated = True
            break
        items.append(entry)
        total += size
    return {"extensions": items, "truncated": truncated}


def _build_messages(message: str, catalog: Mapping[str, Any], job_file_hints: list[str]) -> list[dict]:
    """构造 chat messages：system 规则 + user 里放消息、目录与 job 文件提示。"""
    return [
        {"role": "system", "content": _RESPONSE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "user_message": str(message or ""),
                    "extensions": catalog.get("extensions") or [],
                    "job_files": list(job_file_hints or []),
                },
                ensure_ascii=False,
            ),
        },
    ]


def _sanitize_resolution(raw: Any, catalog: Mapping[str, Any]) -> tuple[dict | None, str | None]:
    """白名单校验 LLM 返回的 resolution；任何违规返回 (None, 原因)。

    返回的 dict 含 extension_id（已从 raw 校验并取出），
    以及 input_overrides/parameter_overrides/explicit_prefix/confidence。
    """
    if not isinstance(raw, dict):
        return None, "LLM 返回的不是 JSON 对象"
    extension_id = raw.get("extension_id")
    if not extension_id or not isinstance(extension_id, str):
        return None, "extension_id 缺失或不是字符串"
    extensions = catalog.get("extensions") or []
    selected = next(
        (item for item in extensions if isinstance(item, Mapping) and item.get("extension_id") == extension_id),
        None,
    )
    if selected is None:
        return None, f"extension_id 不在可选目录中: {extension_id}"
    allowed_paths = {str(item.get("path") or "") for item in selected.get("inputs") or []}
    allowed_fact_ids = {str(item.get("fact_id") or "") for item in selected.get("parameters") or []}

    raw_overrides = raw.get("input_overrides")
    if raw_overrides is not None and not isinstance(raw_overrides, list):
        return None, "input_overrides 必须是数组"
    input_overrides: dict[str, str] = {}
    for item in raw_overrides or []:
        if not isinstance(item, Mapping):
            return None, "input_overrides 元素必须是对象"
        path = item.get("path")
        value = item.get("value")
        if not isinstance(path, str) or path not in allowed_paths:
            return None, f"input_overrides 引用了不存在的 path: {path}"
        if not isinstance(value, str) or not value.strip():
            return None, f"input_overrides[{path}] 的 value 必须是非空字符串"
        input_overrides[path] = value.strip()

    raw_parameters = raw.get("parameter_overrides")
    if raw_parameters is not None and not isinstance(raw_parameters, list):
        return None, "parameter_overrides 必须是数组"
    parameter_overrides: dict[str, Any] = {}
    for item in raw_parameters or []:
        if not isinstance(item, Mapping):
            return None, "parameter_overrides 元素必须是对象"
        fact_id = item.get("fact_id")
        value = item.get("value")
        if not isinstance(fact_id, str) or fact_id not in allowed_fact_ids:
            return None, f"parameter_overrides 引用了不存在的 fact_id: {fact_id}"
        if not isinstance(value, str) or not value.strip():
            return None, f"parameter_overrides[{fact_id}] 的 value 必须是非空字符串"
        parameter_overrides[fact_id] = value.strip()

    explicit_prefix = raw.get("explicit_prefix")
    if explicit_prefix is not None and not isinstance(explicit_prefix, str):
        return None, "explicit_prefix 必须是字符串或 null"
    confidence = raw.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else 0.0
    except (TypeError, ValueError):
        return None, "confidence 不是数字"
    if not (0.0 <= confidence <= 1.0):
        return None, "confidence 超出 [0,1]"
    return (
        {
            "extension_id": extension_id,
            "input_overrides": input_overrides,
            "parameter_overrides": parameter_overrides,
            "explicit_prefix": explicit_prefix.strip() if isinstance(explicit_prefix, str) else None,
            "confidence": confidence,
        },
        None,
    )


def resolve_lightweight_extension_intent(
    message: str,
    published_records: list[dict],
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[[Any], Any] | None = None,
    job_file_hints: list[str] | None = None,
) -> LightweightIntentResolution:
    """把自然语言消息解析为轻量拓展运行意图；绝不抛异常。

    client_factory(config) 注入时即使 config 为 None 也会被调用，由工厂自行容错
    （测试用它注入不依赖 config 的假客户端）。
    """
    candidates = _rough_candidates(message, published_records)
    if not published_records:
        return LightweightIntentResolution(None, None, {}, {}, None, 0.0, False, "no_published_records", [])
    if not candidates:
        return LightweightIntentResolution(None, None, {}, {}, None, 0.0, False, "no_trigger", [])
    candidate_ids = set(candidates)
    ordered_records = sorted(
        [record for record in published_records if str(record.get("extension_id") or "") in candidate_ids],
        key=lambda record: candidates.index(str(record.get("extension_id") or "")),
    )
    catalog = _build_catalog(ordered_records)
    if not catalog["extensions"]:
        return LightweightIntentResolution(None, None, {}, {}, None, 0.0, False, "no_trigger", candidates)
    hints = list(job_file_hints or [])[:MAX_JOB_FILE_HINTS]
    try:
        from .deepseek_semantics import DeepSeekSemanticError, load_deepseek_config

        config = None
        try:
            config = load_deepseek_config(environ if environ is not None else os.environ)
        except DeepSeekSemanticError:
            config = None
        if client_factory is not None:
            client = client_factory(config)
        else:
            from .deepseek_semantics import _default_client

            if config is None:
                raise DeepSeekSemanticError("no deepseek config for default client", code="configuration_error")
            client = _default_client(config)
        model = getattr(config, "model", None) if config is not None else None
        content = None
        for attempt in range(2):
            response = client.chat.completions.create(
                model=model or "deepseek-chat",
                messages=_build_messages(message, catalog, hints),
                temperature=0,
                response_format={"type": "json_object"},
                max_tokens=4000,
            )
            content = response.choices[0].message.content if response.choices else None
            if content and str(content).strip():
                break
        if not content or not str(content).strip():
            raise DeepSeekSemanticError("empty LLM response after retries", code="empty_response")
        sanitized, reason = _sanitize_resolution(json.loads(content), catalog)
        if sanitized is None:
            return LightweightIntentResolution(
                None, None, {}, {}, None, 0.0, False, f"invalid_resolution: {reason}", candidates
            )
    except Exception as exc:
        print(f"[LightweightNLIntent] resolve failed: {exc}")
        return LightweightIntentResolution(
            None, None, {}, {}, None, 0.0, False, "llm_unavailable_or_invalid", candidates
        )

    extension_id = sanitized["extension_id"]
    # P2-4: extension_name 用 extension_id 的紧凑形式（去尾缀时间戳），不再用英文摘要句。
    extension_name = compact_extension_name(extension_id)
    reason = "ok"
    if catalog.get("truncated"):
        reason = "ok; 候选过多已截断，请补充工具名"
    return LightweightIntentResolution(
        extension_id=extension_id,
        extension_name=extension_name,
        input_overrides=sanitized["input_overrides"],
        parameter_overrides=sanitized["parameter_overrides"],
        explicit_prefix=sanitized["explicit_prefix"],
        confidence=sanitized["confidence"],
        prefill=bool(
            sanitized["confidence"] >= PREFILL_THRESHOLD
            and (sanitized["input_overrides"] or sanitized["parameter_overrides"])
        ),
        reason=reason,
        candidates_considered=candidates,
    )
