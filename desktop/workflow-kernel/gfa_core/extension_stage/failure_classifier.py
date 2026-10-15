"""Failure taxonomy for extension-stage build and run loops."""

from __future__ import annotations

import json
import re
from typing import Any


def classify_exception(*, phase: str, exc: BaseException | str, raw_response: str = "") -> dict[str, Any]:
    message = str(exc)
    lower = message.lower()
    code = "unknown_failure"
    layer = "extension_stage"
    retryable = True

    if "unterminated string" in lower or "json" in lower and ("decode" in lower or "expecting" in lower):
        code = "build_parse_failure"
        layer = "api_response_format"
    elif "did not contain json" in lower or "malformed json" in lower:
        code = "build_parse_failure"
        layer = "api_response_format"
    elif "unsupported placeholders" in lower or "missing command_template" in lower or "schema" in lower:
        code = "schema_failure"
        layer = "schema_validation"
    elif "code_artifacts" in lower or "generated code" in lower or "base64" in lower:
        code = "code_artifact_failure"
        layer = "api_generated_code"
    elif "insufficient balance" in lower or "402" in lower:
        code = "api_billing_failure"
        layer = "api_transport"
        retryable = False
    elif "timed out" in lower or "timeout" in lower:
        code = "timeout"
        layer = "execution"
    elif "does not exist" in lower or "not found" in lower:
        code = "missing_input_or_file"
        layer = "input_binding"
    elif "empty output" in lower or "0 b" in lower:
        code = "empty_output"
        layer = "artifacts"

    return {
        "phase": phase,
        "code": code,
        "layer": layer,
        "message": message,
        "retryable": retryable,
        "raw_response_preview": _preview(raw_response),
    }


def classify_run(run: dict[str, Any]) -> dict[str, Any]:
    status = str(run.get("status") or "")
    error = str(run.get("error") or "")
    command = str(run.get("command") or "")
    outputs = run.get("outputs") or []
    if status == "success" and not _has_empty_outputs(outputs):
        return {"phase": "run", "code": "success", "layer": "none", "message": "", "retryable": False}
    if _has_empty_outputs(outputs):
        return {
            "phase": "validation" if run.get("validation") else "run",
            "code": "empty_output",
            "layer": "artifacts",
            "message": error or "Extension produced empty output.",
            "retryable": True,
        }
    lower_error = error.lower()
    if "no files were produced under outputs" in lower_error or "required output pattern" in lower_error or "outside outputs" in lower_error:
        return {
            "phase": "validation" if run.get("validation") else "run",
            "code": "output_contract_failed",
            "layer": "artifacts",
            "message": error,
            "retryable": True,
        }
    if "missing command template value" in error.lower():
        return {
            "phase": "validation" if run.get("validation") else "run",
            "code": "schema_failure",
            "layer": "command_rendering",
            "message": error,
            "retryable": True,
        }
    if _looks_shell_wrapper_syntax_error(error):
        return {
            "phase": "validation" if run.get("validation") else "run",
            "code": "shell_wrapper_syntax",
            "layer": "api_generated_code",
            "message": error,
            "retryable": True,
        }
    if "does not exist" in error.lower() or "not found" in error.lower():
        return {
            "phase": "validation" if run.get("validation") else "run",
            "code": "missing_input_or_file",
            "layer": "input_binding",
            "message": error,
            "retryable": True,
        }
    if any(token in command.lower() for token in ("hmmsearch", "hmmbuild", "blastp", "seqtk", "muscle")):
        return {
            "phase": "validation" if run.get("validation") else "run",
            "code": "tool_runtime_failure",
            "layer": "tool_runtime",
            "message": error,
            "retryable": True,
        }
    return {
        "phase": "validation" if run.get("validation") else "run",
        "code": "execution_failed",
        "layer": "execution",
        "message": error,
        "retryable": True,
    }


def _looks_shell_wrapper_syntax_error(error: str) -> bool:
    lower = str(error or "").lower()
    tokens = [
        "missing `]'",
        "missing ']'",
        "unary operator expected",
        "binary operator expected",
        "syntax error",
        "unexpected token",
        "bad substitution",
        "failed no-arg smoke test",
        "malformed shell test",
    ]
    raw = str(error or "")
    return any(token in lower for token in tokens) or '缺少 "]"' in raw or "未预期的记号" in raw


def safe_json_error(exc: BaseException) -> str:
    if isinstance(exc, json.JSONDecodeError):
        return f"Malformed JSON at line {exc.lineno} column {exc.colno}: {exc.msg}"
    return str(exc)


def _has_empty_outputs(outputs: list[dict[str, Any]]) -> bool:
    return bool(outputs) and any(bool(out.get("is_empty")) or int(out.get("size") or 0) == 0 for out in outputs)


def _preview(text: str, limit: int = 1600) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:limit]
