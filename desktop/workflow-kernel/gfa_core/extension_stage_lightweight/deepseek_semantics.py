"""Generate semantic-only candidates with an OpenAI-compatible DeepSeek API."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from .semantic_model import bind_fact_derived_semantics, validate_semantic_candidate


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
MAX_FILE_BYTES = 32 * 1024
MAX_TOTAL_BYTES = 128 * 1024

SYSTEM_PROMPT = """You describe already-runnable bioinformatics code as a natural-language tool.
Return one strict JSON object and no markdown. You must not return or modify execution facts,
commands, paths, flags, values, observed outputs, or wrapper logic. Reference only fact_id values
present in the supplied execution facts. Provide exactly one entry for every supplied input, parameter, and output fact_id, with no omissions, duplicates, or extras. Fill only function_summary and the allowed semantic fields.
For result_role, final means a user-facing analysis result; intermediate means a temporary or
supporting artifact used to produce or render another result; log means diagnostic execution output.
Use unknown only when the supplied code and execution evidence cannot distinguish these roles.
"""

REPAIR_SYSTEM_PROMPT = """Return only a semantic patch as one strict JSON object and no markdown.
Change only the supplied allowed_targets. Never return or modify commands, paths, flags, values,
observed outputs, code, wrapper logic, or unknown fact_id values.
"""


@dataclass(frozen=True)
class DeepSeekConfig:
    env_file: Path
    api_key: str
    api_key_alias: str
    base_url: str
    model: str


class DeepSeekSemanticError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.metadata = deepcopy(metadata or {})


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip().strip("\"'")
    return values


def load_deepseek_config(
    environ: Mapping[str, str] | None = None,
) -> DeepSeekConfig:
    source = os.environ if environ is None else environ
    configured_path = source.get("BBAV5_PROJECT_ENV_FILE", "").strip()
    if not configured_path:
        raise DeepSeekSemanticError(
            "BBAV5_PROJECT_ENV_FILE is required for DeepSeek semantic conversion",
            code="configuration_error",
        )
    env_file = Path(configured_path).expanduser().resolve()
    if not env_file.is_file():
        raise DeepSeekSemanticError(
            f"Configured project environment file does not exist: {env_file}",
            code="configuration_error",
        )

    values = _read_env_file(env_file)
    api_key_alias = "NEW_API_KEY" if values.get("NEW_API_KEY", "").strip() else "DEEPSEEK_API_KEY"
    api_key = values.get(api_key_alias, "").strip()
    if not api_key:
        raise DeepSeekSemanticError(
            f"DeepSeek API key is missing from configured environment file: {env_file}",
            code="configuration_error",
        )
    base_url = (
        values.get("NEW_API_BASE_URL", "").strip()
        or values.get("DEEPSEEK_BASE_URL", "").strip()
        or DEFAULT_BASE_URL
    )
    model = (
        values.get("NEW_API_MODEL", "").strip()
        or values.get("DEEPSEEK_MODEL", "").strip()
        or DEFAULT_MODEL
    )
    return DeepSeekConfig(
        env_file=env_file,
        api_key=api_key,
        api_key_alias=api_key_alias,
        base_url=base_url,
        model=model,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_code_preview(raw_code: Path) -> dict[str, Any]:
    root = Path(raw_code).resolve()
    files: list[dict[str, Any]] = []
    truncated_files: list[str] = []
    remaining = MAX_TOTAL_BYTES
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        base = {"path": relative, "size": size, "sha256": _sha256(path)}
        with path.open("rb") as handle:
            sample = handle.read(4096)
        try:
            sample.decode("utf-8")
            is_binary = b"\x00" in sample
        except UnicodeDecodeError:
            is_binary = True
        if is_binary:
            files.append({**base, "kind": "binary"})
            continue

        limit = min(MAX_FILE_BYTES, remaining)
        with path.open("rb") as handle:
            content_bytes = handle.read(limit)
        truncated = size > limit
        files.append(
            {
                **base,
                "kind": "text",
                "content": content_bytes.decode("utf-8", errors="replace"),
                "truncated": truncated,
            }
        )
        remaining -= len(content_bytes)
        if truncated:
            truncated_files.append(relative)
    return {"files": files, "truncated_files": truncated_files}


def _default_client(config: DeepSeekConfig) -> Any:
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - environment dependent
        raise DeepSeekSemanticError(
            f"OpenAI-compatible client is unavailable: {exc}",
            code="configuration_error",
        ) from exc
    return OpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=90,
        max_retries=0,
    )


def _metadata(
    config: DeepSeekConfig,
    *,
    attempt: int,
    preview: dict[str, Any],
) -> dict[str, Any]:
    return {
        "env_file": str(config.env_file),
        "base_url": config.base_url,
        "model": config.model,
        "api_key_alias": config.api_key_alias,
        "attempt": attempt,
        "request_id": None,
        "usage": {},
        "raw_response": "",
        "code_preview": {
            "truncated_files": list(preview["truncated_files"]),
            "binary_files": [
                item["path"] for item in preview["files"] if item["kind"] == "binary"
            ],
        },
    }


def _response_usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def generate_semantic_candidate(
    *,
    execution_facts: dict[str, Any],
    raw_code: Path,
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[[DeepSeekConfig], Any] | None = None,
    attempt: int = 1,
) -> tuple[dict[str, Any], dict[str, Any]]:
    config = load_deepseek_config(environ)
    preview = build_code_preview(raw_code)
    metadata = _metadata(config, attempt=attempt, preview=preview)
    payload = {
        "execution_facts": execution_facts,
        "raw_code_preview": preview,
        "required_candidate_shape": {
            "function_summary": "one sentence",
            "inputs": [
                {
                    "fact_id": "existing input fact_id",
                    "role": "semantic role",
                    "description": "plain-language description",
                    "replaceability": (
                        "generated when execution_facts source is generated_input; "
                        "otherwise replaceable or fixed_resource"
                    ),
                }
            ],
            "parameters": [
                {
                    "fact_id": "existing parameter fact_id",
                    "display_name": "human-readable name",
                    "description": "plain-language description",
                }
            ],
            "outputs": [
                {
                    "fact_id": "existing output fact_id",
                    "description": "plain-language description",
                    "result_role": "final, intermediate, log, or unknown",
                }
            ],
        },
    }
    client = (client_factory or _default_client)(config)
    try:
        response = client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                },
            ],
            temperature=0,
            max_tokens=32000,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "enabled"}},
        )
    except DeepSeekSemanticError:
        raise
    except Exception as exc:
        message = str(exc).replace(config.api_key, "[REDACTED]")
        raise DeepSeekSemanticError(
            f"DeepSeek API request failed: {message}",
            code="api_request_failed",
            metadata=metadata,
        ) from exc

    content = (response.choices[0].message.content or "").strip()
    metadata["request_id"] = getattr(response, "id", None)
    metadata["usage"] = _response_usage(response)
    metadata["finish_reason"] = getattr(response.choices[0], "finish_reason", None)
    metadata["raw_response"] = content
    try:
        candidate = json.loads(content)
    except json.JSONDecodeError as exc:
        raise DeepSeekSemanticError(
            f"DeepSeek response is not valid JSON: {exc.msg}",
            code="invalid_json",
            metadata=metadata,
        ) from exc
    if not isinstance(candidate, dict):
        raise DeepSeekSemanticError(
            "DeepSeek response must be one JSON object",
            code="invalid_json",
            metadata=metadata,
        )
    if "facts_sha256" in candidate:
        metadata["discarded_fact_fields"] = ["facts_sha256"]
        candidate.pop("facts_sha256")
    candidate["facts_sha256"] = execution_facts["facts_sha256"]
    candidate, binding_audit = bind_fact_derived_semantics(
        execution_facts, candidate
    )
    if binding_audit:
        metadata["fact_derived_semantic_bindings"] = binding_audit

    validation_errors = validate_semantic_candidate(execution_facts, candidate)
    if validation_errors:
        metadata["validation_errors"] = validation_errors
        metadata["system_bound_candidate"] = deepcopy(candidate)
        raise DeepSeekSemanticError(
            "DeepSeek semantic candidate failed the locked-fact gate",
            code="semantic_candidate_invalid",
            metadata=metadata,
        )
    return candidate, metadata


def generate_semantic_patch(
    *,
    repair_context: dict[str, Any],
    raw_code: Path,
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[[DeepSeekConfig], Any] | None = None,
    attempt: int = 1,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ask DeepSeek for one semantic-only patch; validation stays in the repair gate."""

    config = load_deepseek_config(environ)
    preview = build_code_preview(raw_code)
    metadata = _metadata(config, attempt=attempt, preview=preview)
    payload = {
        **deepcopy(repair_context),
        "raw_code_preview": preview,
        "required_patch_shape": {
            "changes": [
                {
                    "section": "root, inputs, parameters, or outputs",
                    "fact_id": "existing fact_id, or null for root",
                    "fields": {"allowed semantic field": "new string value"},
                }
            ],
        },
    }
    client = (client_factory or _default_client)(config)
    try:
        response = client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": REPAIR_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                },
            ],
            temperature=0,
            max_tokens=6000,
            response_format={"type": "json_object"},
        )
    except DeepSeekSemanticError:
        raise
    except Exception as exc:
        message = str(exc).replace(config.api_key, "[REDACTED]")
        raise DeepSeekSemanticError(
            f"DeepSeek API repair request failed: {message}",
            code="api_request_failed",
            metadata=metadata,
        ) from exc

    content = (response.choices[0].message.content or "").strip()
    metadata["request_id"] = getattr(response, "id", None)
    metadata["usage"] = _response_usage(response)
    metadata["raw_response"] = content
    try:
        patch = json.loads(content)
    except json.JSONDecodeError as exc:
        raise DeepSeekSemanticError(
            f"DeepSeek repair response is not valid JSON: {exc.msg}",
            code="invalid_json",
            metadata=metadata,
        ) from exc
    if not isinstance(patch, dict):
        raise DeepSeekSemanticError(
            "DeepSeek repair response must be one JSON object",
            code="invalid_json",
            metadata=metadata,
        )
    if "facts_sha256" in patch:
        metadata["discarded_fact_fields"] = ["facts_sha256"]
        patch.pop("facts_sha256")
    patch["facts_sha256"] = repair_context["execution_facts"]["facts_sha256"]
    return patch, metadata
