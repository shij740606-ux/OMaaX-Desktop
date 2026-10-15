"""Semantic interpretation of golden comparison reports."""

from __future__ import annotations

from typing import Any


def summarize_golden_failure(golden: dict[str, Any]) -> dict[str, Any]:
    """Convert a golden comparison report into user-facing diagnostics."""

    if not golden:
        return {"blocking": True, "code": "missing_golden_evidence", "user_message": "缺少 original vs converted 结果链验证证据。"}
    if golden.get("status") == "passed":
        return {"blocking": False, "code": "golden_passed", "user_message": ""}
    comparison = golden.get("comparison") or {}
    failures = comparison.get("failures") or []
    if not failures:
        return {
            "blocking": True,
            "code": "golden_validation_failed",
            "user_message": str(golden.get("error") or "原始代码和 API 转写结果链验证失败。"),
            "evidence": {"golden": _compact(golden)},
        }
    first = failures[0]
    reason = str(first.get("reason") or "output_mismatch")
    comparator = str(first.get("comparator") or "")
    metrics = first.get("metrics") or {}
    if reason == "fasta_header_lost_or_missing":
        message = (
            "FASTA 结果链不一致：原始输出有 "
            f"{metrics.get('original_headers', 0)} 个 header，API 转写输出有 "
            f"{metrics.get('converted_headers', 0)} 个 header。疑似 wrapper 改坏了 ID/header 处理逻辑。"
        )
        code = "fasta_header_lost"
    elif reason == "record_set_changed" and comparator == "bed_record_set":
        message = "BED 坐标结果与原始代码不一致；通常是 awk 列号、阈值或坐标转换被 API 转写改坏。"
        code = "bed_record_set_changed"
    elif reason.startswith("hmm_profile"):
        message = "HMM profile 与原始代码重建结果不一致；需要检查上游提取、比对或 hmmbuild 输入是否被改变。"
        code = "hmm_profile_changed"
    elif reason == "missing_converted_output":
        message = "API 转写后缺少原始代码产生的输出文件；可能漏跑步骤或输出目录/文件名映射错误。"
        code = "missing_converted_output"
    else:
        message = f"原始代码输出与 API 转写输出不一致：{reason}。"
        code = "golden_output_mismatch"
    return {
        "blocking": True,
        "code": code,
        "user_message": message,
        "evidence": {
            "failure": first,
            "summary": comparison.get("summary", {}),
            "golden_workspace": golden.get("workspace", ""),
        },
    }


def _compact(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": value.get("status"),
        "run_id": value.get("run_id"),
        "workspace": value.get("workspace"),
        "error": value.get("error", ""),
    }
