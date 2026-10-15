import asyncio
import csv
import inspect
import io
import json
import math
import re
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from gfa_core.mas.agents.base import AgentMessage, AgentState
from gfa_core.mas.agents.intent_v2 import IntentAgentV2
from gfa_core.mas.agents.interpretation_evidence import build_evidence_report
from gfa_core.mas.context_index import build_project_context
from gfa_core.mas.dual_kb_qa import query_dual_kb_qa
from gfa_core.mas.llm import LLMManager
from gfa_core.mas.mutation_surface import ToolSurfaceEntry
from gfa_core.mas.stage_capabilities import get_stage_capability
from gfa_core.mas.tools.stage_status import SUCCESS_STATUSES
from gfa_core.mas.tools.stage_tools import stage_registry
from gfa_core.mas.workflow_catalog import load_rnaseq_business_context
from gfa_core.mas.workflow_models import GoalLedger


@dataclass(frozen=True)
class _ConversationTool:
    description: str
    args_model: type[BaseModel]
    handler: Callable
    availability: Callable[[], bool] | None
    permission: Literal["read_only", "pure"]
    max_result_chars: int


class ConversationToolRegistry:
    def __init__(self, progress_callback: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._tools: dict[str, _ConversationTool] = {}
        self._permission_guard: Callable[[str], None] | None = None
        self._progress_callback = progress_callback
        self._analysis_handoff_resolver: Callable[..., str] | None = None

    def set_analysis_handoff_resolver(self, resolver: Callable[..., str]) -> None:
        self._analysis_handoff_resolver = resolver

    def resolve_analysis_handoff_request(
        self,
        query: str,
        *,
        workflow_id: str | None = None,
        guidance: bool = False,
        language: str = "zh",
    ) -> str:
        text = str(query or "").strip()
        if self._analysis_handoff_resolver is None:
            return text
        resolved = str(
            self._analysis_handoff_resolver(
                text,
                workflow_id=workflow_id,
                guidance=guidance,
                language=language,
            )
            or ""
        ).strip()
        return resolved or text

    def _notify_progress(self, event: dict[str, Any]) -> None:
        if self._progress_callback is None:
            return
        try:
            self._progress_callback(event)
        except Exception:
            pass

    def set_permission_guard(self, guard: Callable[[str], None]) -> None:
        self._permission_guard = guard

    def register(
        self,
        *,
        name: str,
        description: str,
        args_model: type[BaseModel],
        handler: Callable,
        availability: Callable[[], bool] | None = None,
        permission: Literal["read_only", "pure"],
        max_result_chars: int = 24_000,
    ) -> None:
        if permission not in {"read_only", "pure"}:
            raise ValueError("permission must be 'read_only' or 'pure'")
        if max_result_chars <= 0:
            raise ValueError("max_result_chars must be positive")
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        self._tools[name] = _ConversationTool(
            description=description,
            args_model=args_model,
            handler=handler,
            availability=availability,
            permission=permission,
            max_result_chars=max_result_chars,
        )

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.description,
                    "parameters": tool.args_model.model_json_schema(),
                },
            }
            for name, tool in self._tools.items()
        ]

    def policy_inventory(self) -> tuple[ToolSurfaceEntry, ...]:
        return tuple(
            ToolSurfaceEntry(
                name=name,
                permission=tool.permission,
                handler=f"{tool.handler.__module__}.{tool.handler.__qualname__}",
            )
            for name, tool in sorted(self._tools.items())
        )

    async def dispatch(self, name: str, raw_arguments: str) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return self._error("unknown_tool", f"unknown tool: {name}")
        if self._permission_guard is not None:
            try:
                self._permission_guard(tool.permission)
            except Exception as exc:
                return self._error("evidence_root_read_only", str(exc))
        if tool.availability is not None:
            try:
                available = tool.availability()
            except Exception:
                available = False
            if not available:
                return self._error("tool_unavailable", f"tool unavailable: {name}")

        try:
            arguments = tool.args_model.model_validate_json(raw_arguments or "{}")
        except ValidationError as exc:
            return self._error("invalid_arguments", str(exc))

        self._notify_progress({"stage": "tool_started", "tool": name})
        try:
            result = tool.handler(arguments)
            if inspect.isawaitable(result):
                result = await result
            return self._success(result, tool.max_result_chars)
        except Exception as exc:
            return self._error("tool_error", str(exc))
        finally:
            self._notify_progress({"stage": "tool_completed", "tool": name})

    @staticmethod
    def _error(code: str, message: str) -> str:
        return json.dumps(
            {"ok": False, "error": {"code": code, "message": message}},
            ensure_ascii=False,
        )

    @staticmethod
    def _success(data: Any, max_result_chars: int) -> str:
        data_json = json.dumps(data, ensure_ascii=False)
        if len(data_json) > max_result_chars:
            return json.dumps(
                {
                    "ok": True,
                    "data": data_json[:max_result_chars],
                    "truncated": True,
                },
                ensure_ascii=False,
            )
        return json.dumps({"ok": True, "data": data}, ensure_ascii=False)


def _validate_job_id(value: str) -> str:
    if value and (Path(value).is_absolute() or "/" in value or "\\" in value or value in {".", ".."}):
        raise ValueError("job_id must be a single project job name")
    return value


def _validate_required_job_id(value: str) -> str:
    if not value.strip():
        raise ValueError("job_id is required")
    return _validate_job_id(value)


def _validate_prefix(value: str) -> str:
    if any(token in value for token in ("/", "\\", "..", "*", "?", "[", "]")):
        raise ValueError("prefix must not contain path or glob syntax")
    if value and not re.fullmatch(
        r"(?:Target\.)?[A-Za-z][A-Za-z0-9_-]{0,31}\.[A-Za-z][A-Za-z0-9_-]{0,63}",
        value,
    ):
        raise ValueError("prefix must be a canonical species.family identity")
    return value


def _canonical_species_codes(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(
        dict.fromkeys(
            value
            for value in values
            if isinstance(value, str)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,15}", value)
        )
    )


def _normalize_result_stage(value: str) -> str:
    stage = str(value or "").strip()
    return {
        "differential_expression": "rnaseq_de",
    }.get(stage.lower(), stage)


class InspectProjectContextArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    prefix: str = ""

    _job_id_is_local = field_validator("job_id")(_validate_required_job_id)
    _prefix_is_literal = field_validator("prefix")(_validate_prefix)


class InspectAnalysisResultsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    prefix: str = ""
    stage: str = ""
    scope_id: str = ""
    analysis_route: str = ""
    member_set_id: str = ""

    _job_id_is_local = field_validator("job_id")(_validate_required_job_id)
    _prefix_is_literal = field_validator("prefix")(_validate_prefix)
    _stage_is_canonical = field_validator("stage")(_normalize_result_stage)


class QueryPlantAIRagArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    family: str = ""
    stage: str = ""


class RecognizeAnalysisIntentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    job_id: str = ""
    prefix: str = ""
    language: str = "zh"

    _job_id_is_local = field_validator("job_id")(_validate_job_id)
    _prefix_is_literal = field_validator("prefix")(_validate_prefix)


class RecognizeUtilityIntentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str


class BoundInspectProjectContextArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prefix: str = ""
    _prefix_is_literal = field_validator("prefix")(_validate_prefix)


class BoundInspectAnalysisResultsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prefix: str = ""
    stage: str = ""
    scope_id: str = ""
    analysis_route: str = ""
    member_set_id: str = ""
    _prefix_is_literal = field_validator("prefix")(_validate_prefix)
    _stage_is_canonical = field_validator("stage")(_normalize_result_stage)


class BoundRecognizeAnalysisIntentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    prefix: str = ""
    language: str = "zh"
    _prefix_is_literal = field_validator("prefix")(_validate_prefix)


class InspectJobFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    max_lines: int = Field(default=200, ge=1, le=500)


class ListJobFilesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roots: list[Literal["input", "final", "logs", "workspace"]] = Field(
        default_factory=lambda: ["input", "final", "logs"]
    )
    limit: int = Field(default=100, ge=1, le=200)
    cursor: int = Field(default=0, ge=0)


def normalize_rag_tool_result(raw: Any) -> dict[str, Any]:
    """Expose only truthful RAG availability, success, answer, and citations."""
    data = raw if isinstance(raw, dict) else {}
    request_id = str(data.get("request_id") or f"rag-{uuid.uuid4().hex[:12]}")
    error_code = str(data.get("error_code") or "")
    available = error_code not in {"dual_kb_unavailable", "rag_unreachable"}
    answer = str(data.get("answer") or data.get("response") or "").strip()
    citations: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    raw_sources = data.get("sources") or data.get("citations") or []
    if isinstance(raw_sources, list):
        for source in raw_sources:
            if not isinstance(source, dict):
                continue
            doc_id = str(
                source.get("doc_id")
                or source.get("paper_id")
                or source.get("pubmed_id")
                or source.get("id")
                or ""
            ).strip()
            title = str(
                source.get("title")
                or source.get("paper_title")
                or source.get("source")
                or doc_id
                or ""
            ).strip()
            locator = str(
                source.get("locator")
                or source.get("page")
                or source.get("section")
                or doc_id
                or source.get("target")
                or ""
            ).strip()
            if not title or not locator:
                continue
            url = str(source.get("url") or source.get("doi_url") or "").strip()
            if url:
                parsed = urllib.parse.urlsplit(url)
                if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
                    url = ""
            key = (title, locator, url, doc_id)
            if key in seen:
                continue
            seen.add(key)
            citation = {"title": title, "locator": locator}
            if url:
                citation["url"] = url
            if doc_id:
                citation["doc_id"] = doc_id
            citations.append(citation)

    raw_success = data.get("success") is True
    if not raw_success:
        return {
            "registered": True,
            "available": available,
            "success": False,
            "request_id": request_id,
            "error_code": error_code or "rag_request_failed",
            "answer": "",
            "citations": [],
        }
    if not answer:
        error_code = "rag_empty_answer"
    elif not citations:
        error_code = "rag_no_usable_citations"
    if error_code:
        return {
            "registered": True,
            "available": available,
            "success": False,
            "request_id": request_id,
            "error_code": error_code,
            "answer": "",
            "citations": [],
        }
    return {
        "registered": True,
        "available": True,
        "success": True,
        "request_id": request_id,
        "error_code": "",
        "answer": answer,
        "citations": citations,
    }


_FORBIDDEN_INTENT_KEYS = {
    "benchmarkMode",
    "forceRerun",
    "forceRerunStages",
    "scopeId",
    "scope_id",
    "memberSetId",
    "member_set_id",
}


def _without_forbidden_intent_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_forbidden_intent_keys(item)
            for key, item in value.items()
            if key not in _FORBIDDEN_INTENT_KEYS
        }
    if isinstance(value, list):
        return [_without_forbidden_intent_keys(item) for item in value]
    return value


def _manifest_identity(manifest: dict[str, Any]) -> dict[str, str]:
    def value(key: str) -> str:
        raw = manifest.get(key) or ""
        return raw if isinstance(raw, str) else ""

    return {
        "scope_id": value("scope_id"),
        "analysis_route": value("analysis_route"),
        "member_set_id": value("member_set_id"),
    }


def _canonical_identity_is_complete(identity: dict[str, Any]) -> bool:
    return all(
        _safe_manifest_token(identity.get(key), max_length=512)
        for key in ("prefix", "scope_id", "analysis_route", "member_set_id")
    )


def _result_identity_is_complete(identity: dict[str, Any], *, stage: str) -> bool:
    """Validate identity at the requested stage's actual business boundary."""
    if not all(
        _safe_manifest_token(identity.get(key), max_length=512)
        for key in ("prefix", "scope_id", "analysis_route")
    ):
        return False
    if stage == "identify":
        # Identify creates the candidate set; member_set_id exists only after
        # the user confirms those candidates.
        return identity.get("member_set_id") in {None, ""}
    return _safe_manifest_token(identity.get("member_set_id"), max_length=512)


def _safe_manifest_token(value: Any, *, max_length: int) -> bool:
    allowed_punctuation = "._-:"
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value) <= max_length
        and all(
            char.isascii() and (char.isalnum() or char in allowed_punctuation)
            for char in value
        )
        and not Path(value).is_absolute()
        and "/" not in value
        and "\\" not in value
        and ".." not in value
    )


def _known_result_stage(stage: str) -> bool:
    return (
        stage == "extension_stage"
        or stage_registry.get(stage) is not None
        or get_stage_capability(stage) is not None
    )


def _validated_manifest_rows(
    manifests: list[dict[str, Any]],
    *,
    final_dir: Path,
) -> list[dict[str, Any]]:
    try:
        resolved_final = final_dir.resolve(strict=True)
    except OSError:
        return []
    validated = []
    for row in manifests:
        path = Path(str(row.get("path") or ""))
        try:
            resolved_path = path.resolve(strict=True)
            resolved_path.relative_to(resolved_final)
            payload = json.loads(resolved_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        parameters = payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {}
        raw = {
            "prefix": (
                payload.get("prefix")
                or parameters.get("rnaseq_output_prefix")
                or parameters.get("prefix")
            ),
            "stage": payload.get("stage"),
            "scope_id": payload.get("scope_id"),
            "analysis_route": payload.get("analysis_route"),
            "member_set_id": payload.get("member_set_id"),
            "rnaseq_scope_id": (
                payload.get("rnaseq_scope_id")
                or parameters.get("rnaseq_scope_id")
            ),
        }
        identity = {key: raw[key] for key in ("prefix", "scope_id", "analysis_route", "member_set_id")}
        is_rnaseq = str(raw["stage"] or "").startswith("rnaseq_")
        is_shared_prepare = raw["stage"] == "genome_prepare"
        if is_rnaseq:
            if not all(
                _safe_manifest_token(raw.get(key), max_length=512)
                for key in ("prefix", "rnaseq_scope_id")
            ):
                continue
        elif is_shared_prepare:
            if not _safe_manifest_token(raw.get("prefix"), max_length=512):
                continue
        elif not _result_identity_is_complete(identity, stage=str(raw["stage"] or "")):
            continue
        if not _safe_manifest_token(raw["stage"], max_length=128) or not _known_result_stage(raw["stage"]):
            continue
        validated.append({**row, **raw, "status": payload.get("status")})
    return validated


_UNSAFE_METRIC_KEY_TOKENS = {
    "path", "file", "dir", "directory", "warning", "diagnostic", "reason",
    "message", "raw", "error", "exception", "traceback", "log", "stdout",
    "stderr", "command", "detail",
}
_DROP_METRIC = object()


class _ProjectionTooLarge(ValueError):
    pass


def _safe_metric_key(key: Any, value: Any = None) -> bool:
    if not _safe_manifest_token(key, max_length=128):
        return False
    if isinstance(value, (bool, int, float)):
        return True
    tokens = {token for token in key.lower().replace("-", "_").split("_") if token}
    return not bool(tokens & _UNSAFE_METRIC_KEY_TOKENS)


def _safe_metric_value(value: Any, *, depth: int = 0) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _DROP_METRIC
    if isinstance(value, str):
        return _DROP_METRIC
    if depth >= 4:
        return _DROP_METRIC
    if isinstance(value, list):
        if len(value) > 64:
            return _DROP_METRIC
        compact = []
        for item in value:
            safe = _safe_metric_value(item, depth=depth + 1)
            if safe is not _DROP_METRIC:
                compact.append(safe)
        return compact if compact or not value else _DROP_METRIC
    if isinstance(value, dict):
        if len(value) > 128:
            return _DROP_METRIC
        compact = {}
        for key in sorted(value):
            if not _safe_metric_key(key, value[key]):
                continue
            safe = _safe_metric_value(value[key], depth=depth + 1)
            if safe is not _DROP_METRIC:
                compact[key] = safe
        return compact if compact or not value else _DROP_METRIC
    return _DROP_METRIC


def _compact_stage_metrics(metrics_by_stage: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if len(metrics_by_stage) > 16:
        raise _ProjectionTooLarge
    compact: dict[str, dict[str, Any]] = {}
    for stage in sorted(metrics_by_stage):
        safe = _safe_metric_value(metrics_by_stage[stage])
        if not isinstance(safe, dict):
            safe = {}
        compact[stage] = safe
    return compact


def _compact_source_summary(summary: Any) -> dict[str, int]:
    if not isinstance(summary, dict):
        return {}
    allowed = {
        "manifest_count", "raw_evidence_count", "risk_count", "high_risk_count",
        "rag_reference_stage_count", "rag_reference_count", "claim_count",
        "major_claim_count",
    }
    return {
        key: value
        for key, value in sorted(summary.items())
        if key in allowed
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    }


def _compact_result_findings(report: dict[str, Any], *, limit: int = 3) -> list[dict[str, Any]]:
    findings = report.get("key_findings")
    if not isinstance(findings, list):
        return []
    allowed = ("stage", "title", "description", "confidence", "interpretation_level")
    return [
        _selected_fields(item, allowed)
        for item in findings[:limit]
        if isinstance(item, dict)
    ]


def _compact_structured_interpretations(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    interpretations = report.get("structured_result_interpretations")
    if not isinstance(interpretations, dict):
        return {}
    allowed = (
        "stage", "stage_name", "status", "result_interpretation", "limitations",
        "boundary", "interpretation_level",
    )
    return {
        str(stage): _selected_fields(value, allowed)
        for stage, value in list(sorted(interpretations.items()))[:16]
        if _safe_binding_label(stage) and isinstance(value, dict)
    }


def _bounded_tabular_artifact_rows(
    manifest_path: Path,
    *,
    final_dir: Path,
    limit: int = 5,
) -> list[dict[str, str]]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        return []
    resolved_final = final_dir.resolve()
    excluded = {"plot", "raw", "manifest", "command", "environment", "checksum", "report"}
    candidates: list[tuple[str, Path]] = []
    for key, artifact in sorted(artifacts.items()):
        if not isinstance(artifact, dict):
            continue
        raw_path = str(artifact.get("path") or "").strip()
        artifact_type = str(artifact.get("type") or "").strip().lower()
        suffix = Path(raw_path).suffix.lower()
        key_tokens = {token for token in str(key).lower().replace("-", "_").split("_") if token}
        if key_tokens & excluded:
            continue
        if artifact_type not in {"tsv", "csv"} and suffix not in {".tsv", ".txt", ".csv"}:
            continue
        declared = Path(raw_path)
        candidate = declared if declared.is_absolute() else (
            final_dir.parent / declared if declared.parts[:1] == ("final",) else final_dir / declared
        )
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(resolved_final)
        except (OSError, ValueError):
            continue
        candidates.append((str(key), resolved))
    if not candidates:
        return []
    selected = candidates[0][1]
    try:
        with selected.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            sample = handle.read(131_072)
    except OSError:
        return []
    delimiter = "," if selected.suffix.lower() == ".csv" else "\t"
    reader = csv.DictReader(io.StringIO(sample), delimiter=delimiter)
    if not reader.fieldnames or len(reader.fieldnames) > 20:
        return []
    rows: list[dict[str, str]] = []
    for raw_row in reader:
        row = {
            str(key)[:128]: str(value or "")[:512]
            for key, value in list(raw_row.items())[:20]
            if key is not None
        }
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _safe_binding_label(value: Any) -> bool:
    return _safe_manifest_token(value, max_length=128)


def _candidate_identities(
    manifests: list[dict[str, Any]],
    *,
    prefix: str,
    stage: str,
) -> list[dict[str, str]]:
    identities: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for manifest in manifests:
        manifest_prefix = str(manifest.get("prefix") or "")
        if prefix and manifest_prefix != prefix:
            continue
        if stage and str(manifest.get("stage") or "") != stage:
            continue
        identity = _manifest_identity(manifest)
        key = (
            manifest_prefix,
            identity["scope_id"],
            identity["analysis_route"],
            identity["member_set_id"],
        )
        if key in seen:
            continue
        seen.add(key)
        identities.append({"prefix": manifest_prefix, **identity})
    return identities


_INTEGRATED_ROUTE_PRIORITY = {
    "multi_species": 0,
    "single_species": 1,
    "confirmed_subfamily": 2,
    "subfamily_prediction": 3,
    "subfamily_evidence": 4,
}


def _manifest_row_metrics(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics")
    if isinstance(metrics, dict) and metrics:
        return metrics
    try:
        payload = json.loads(Path(str(row.get("path") or "")).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    metrics = payload.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def _integrated_gene_family_rows(
    manifests: list[dict[str, Any]],
    *,
    prefix: str,
) -> tuple[dict[str, dict[str, Any]] | None, str]:
    """Select one evidence-bound successful Manifest per biological stage."""
    rows_by_stage: dict[str, list[dict[str, Any]]] = {}
    for row in manifests:
        stage = str(row.get("stage") or "")
        if (
            row.get("status") not in SUCCESS_STATUSES
            or stage.startswith("rnaseq_")
            or stage == "genome_prepare"
            or str(row.get("prefix") or "") != prefix
        ):
            continue
        rows_by_stage.setdefault(stage, []).append(row)

    selected: dict[str, dict[str, Any]] = {}
    for stage, rows in rows_by_stage.items():
        rows_by_identity: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for row in rows:
            identity = _manifest_identity(row)
            key = (
                identity["scope_id"],
                identity["analysis_route"],
                identity["member_set_id"],
            )
            rows_by_identity.setdefault(key, []).append(row)

        identity_rows: list[dict[str, Any]] = []
        for identity_group in rows_by_identity.values():
            metrics_by_row = [
                (row, _manifest_row_metrics(row)) for row in identity_group
            ]
            for _, left in metrics_by_row:
                for _, right in metrics_by_row:
                    shared_keys = set(left) & set(right)
                    if any(left[key] != right[key] for key in shared_keys):
                        return None, stage
            identity_rows.append(
                sorted(
                    metrics_by_row,
                    key=lambda item: (
                        -len(item[1]),
                        str(item[0].get("path") or ""),
                    ),
                )[0][0]
            )

        best_priority = min(
            _INTEGRATED_ROUTE_PRIORITY.get(str(row.get("analysis_route") or ""), 50)
            for row in identity_rows
        )
        best_rows = [
            row
            for row in identity_rows
            if _INTEGRATED_ROUTE_PRIORITY.get(
                str(row.get("analysis_route") or ""), 50
            )
            == best_priority
        ]
        if len(best_rows) != 1:
            return None, stage
        selected[stage] = best_rows[0]
    return selected, ""


def _selected_fields(item: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {key: item[key] for key in fields if item.get(key) not in (None, "", [], {})}


class _NoCostLLMManager:
    def __init__(self, wrapped: LLMManager):
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def call(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["track_cost"] = False
        return self._wrapped.call(*args, **kwargs)

    async def acall(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["track_cost"] = False
        return await self._wrapped.acall(*args, **kwargs)


def _matching_manifest_rows(
    manifests: list[dict[str, Any]],
    *,
    prefix: str,
    stage: str,
    identity: dict[str, str],
) -> list[dict[str, Any]]:
    return [
        manifest
        for manifest in manifests
        if (not prefix or str(manifest.get("prefix") or "") == prefix)
        and (not stage or str(manifest.get("stage") or "") == stage)
        and _manifest_identity(manifest) == identity
    ]


def _bind_exact_manifest_sources(
    manifests: list[dict[str, Any]],
    *,
    identity: dict[str, str],
    final_dir: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]] | None:
    resolved_final = final_dir.resolve()
    rows_by_stage: dict[str, list[dict[str, Any]]] = {}
    for manifest in manifests:
        rows_by_stage.setdefault(str(manifest.get("stage") or ""), []).append(manifest)

    metrics_by_stage: dict[str, dict[str, Any]] = {}
    bindings: dict[str, dict[str, Any]] = {}
    for stage, rows in rows_by_stage.items():
        if not _safe_binding_label(stage):
            return None
        payloads: list[tuple[Path, dict[str, Any]]] = []
        for row in rows:
            path = Path(str(row.get("path") or ""))
            try:
                resolved_path = path.resolve()
                resolved_path.relative_to(resolved_final)
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                return None
            if _manifest_identity(payload) != identity or str(payload.get("stage") or "") != stage:
                return None
            payloads.append((path, payload))

        metric_variants = {json.dumps(payload.get("metrics") or {}, sort_keys=True) for _, payload in payloads}
        if len(metric_variants) != 1:
            return None
        path, payload = sorted(payloads, key=lambda item: str(item[0]))[0]
        if payload.get("status") not in SUCCESS_STATUSES:
            return None
        metrics_by_stage[stage] = payload.get("metrics") or {}
        bindings[stage] = {
            "manifest_ref": (Path("final") / path.resolve().relative_to(resolved_final)).as_posix(),
            "canonical_identity": dict(identity),
            "stage": stage,
            "status": payload.get("status", ""),
        }
    return metrics_by_stage, bindings


def _report_identity_is_compatible(metadata: dict[str, Any], identity: dict[str, str]) -> bool:
    report_scope = str(metadata.get("scope_id") or "")
    report_route = str(metadata.get("analysis_route") or "")
    report_member = str(metadata.get("member_set_id") or "")
    if report_scope != identity["scope_id"]:
        return False
    if report_route == identity["analysis_route"]:
        return report_member == identity["member_set_id"]
    normalized_target_plus_reference = (
        identity["analysis_route"] == "multi_species"
        and "target_plus_reference" in identity["scope_id"]
        and report_route == "target_plus_reference"
    )
    return normalized_target_plus_reference and report_member in {"", identity["member_set_id"]}


def build_plantai_conversation_tools(
    *,
    llm_manager: LLMManager,
    project_root: Path,
    bound_job_id: str = "",
    goal_ledger: GoalLedger | None = None,
    allow_legacy_manifest_migration: bool = True,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> ConversationToolRegistry:
    project_root = Path(project_root)
    bound_job_id = _validate_job_id(str(bound_job_id or ""))
    registry = ConversationToolRegistry(progress_callback=progress_callback)

    def current_job(args: BaseModel) -> str:
        return bound_job_id or str(getattr(args, "job_id", "") or "")

    context_options = (
        {}
        if allow_legacy_manifest_migration
        else {"allow_legacy_manifest_migration": False}
    )

    async def inspect_project(args: InspectProjectContextArgs) -> dict[str, Any]:
        job_id = current_job(args)
        index = await asyncio.to_thread(
            build_project_context,
            job_id,
            args.prefix,
            project_root,
            **context_options,
        )
        data = index.to_dict()
        scopes = [
            _selected_fields(
                item,
                (
                    "scope_id",
                    "scope_mode",
                    "family",
                    "target_species",
                    "reference_species",
                    "comparative_species",
                    "selected_species",
                ),
            )
            for item in data.get("scope_files", [])
        ]
        for item in scopes:
            for key in (
                "target_species",
                "reference_species",
                "comparative_species",
                "selected_species",
            ):
                if key in item:
                    item[key] = _canonical_species_codes(item[key])
        manifests = [
            _selected_fields(
                item,
                (
                    "stage",
                    "status",
                    "prefix",
                    "scope_id",
                    "analysis_route",
                    "member_set_id",
                    "scope_mode",
                    "family",
                    "target_species",
                    "reference_species",
                    "comparative_species",
                ),
            )
            for item in data.get("manifests", [])
        ]
        for item in manifests:
            for key in (
                "target_species",
                "reference_species",
                "comparative_species",
            ):
                if key in item:
                    item[key] = _canonical_species_codes(item[key])
        stage_status = {
            stage: _selected_fields(status, ("stage", "status"))
            for stage, status in data.get("stage_status", {}).items()
        }
        warnings = [
            _selected_fields(item, ("code", "message", "stage"))
            for item in data.get("warnings", [])
        ]
        return {
            "job_id": data.get("job_id", job_id),
            "family": data.get("family", ""),
            "species_roles": {
                "target": _canonical_species_codes(data.get("target_species", [])),
                "reference": _canonical_species_codes(data.get("reference_species", [])),
                "comparative": _canonical_species_codes(data.get("comparative_species", [])),
            },
            "scopes": scopes,
            "manifests": manifests,
            "stage_status": stage_status,
            "warnings": warnings,
        }

    async def inspect_results(args: InspectAnalysisResultsArgs) -> dict[str, Any]:
        job_id = current_job(args)
        index = await asyncio.to_thread(
            build_project_context,
            job_id,
            args.prefix,
            project_root,
            **context_options,
        )
        index_data = index.to_dict()
        final_dir = project_root / "workspace" / job_id / "final"
        manifests = _validated_manifest_rows(
            index_data.get("manifests", []),
            final_dir=final_dir,
        )
        rnaseq_rows = [
            row
            for row in manifests
            if str(row.get("stage") or "").startswith("rnaseq_")
            and row.get("status") in SUCCESS_STATUSES
            and (not args.stage or row.get("stage") == args.stage)
            and (not args.prefix or row.get("prefix") == args.prefix)
        ]
        if args.stage == "genome_prepare":
            prepare_rows = [
                row
                for row in manifests
                if row.get("stage") == "genome_prepare"
                and row.get("status") in SUCCESS_STATUSES
            ]
            if len(prepare_rows) != 1:
                return {
                    "success": False,
                    "error_code": (
                        "ambiguous_evidence_binding"
                        if prepare_rows
                        else "result_not_found"
                    ),
                    "message": "Genome preparation result is missing or ambiguous.",
                }
            selected = prepare_rows[0]
            effective_prefix = str(selected.get("prefix") or "genome_prepare")
            report = await asyncio.to_thread(
                build_evidence_report,
                final_dir,
                effective_prefix,
                job_id=job_id,
                include_stages=["genome_prepare"],
            )
            if report is None:
                return {
                    "success": False,
                    "error_code": "result_not_found",
                    "message": "No analysis result matched the request.",
                }
            manifest_path = Path(str(selected.get("path") or ""))
            return {
                "projection_version": "compact_v1",
                "success": True,
                "canonical_identity": {"prefix": effective_prefix},
                "metadata": {"prefix": effective_prefix},
                "result_agent": {
                    "stage_metrics": _compact_stage_metrics(
                        {"genome_prepare": dict(selected.get("metrics") or {})}
                    ),
                    "key_findings": _compact_result_findings(report),
                    "structured_result_interpretations": _compact_structured_interpretations(report),
                    "top_results": {},
                },
                "source_bindings": {
                    "genome_prepare": {
                        "manifest_ref": (
                            Path("final")
                            / manifest_path.resolve().relative_to(final_dir.resolve())
                        ).as_posix(),
                        "stage": "genome_prepare",
                        "status": selected.get("status", ""),
                    }
                },
                "source_summary": _compact_source_summary(report.get("source_summary")),
            }
        if not args.stage and rnaseq_rows:
            rnaseq_identities = sorted(
                {
                    (
                        str(row.get("prefix") or ""),
                        str(row.get("rnaseq_scope_id") or ""),
                    )
                    for row in rnaseq_rows
                    if str(row.get("prefix") or "").strip()
                    and str(row.get("rnaseq_scope_id") or "").strip()
                }
            )
            if len(rnaseq_identities) != 1:
                return {
                    "success": False,
                    "error_code": "ambiguous_scope",
                    "message": "Multiple RNA-seq result identities match; provide prefix or stage.",
                    "candidates": [
                        {"prefix": prefix, "rnaseq_scope_id": scope_id}
                        for prefix, scope_id in rnaseq_identities
                    ],
                }
            effective_prefix, rnaseq_scope_id = rnaseq_identities[0]
            selected_rows = [
                row
                for row in rnaseq_rows
                if str(row.get("prefix") or "") == effective_prefix
                and str(row.get("rnaseq_scope_id") or "") == rnaseq_scope_id
            ]
            rows_by_stage: dict[str, list[dict[str, Any]]] = {}
            for row in selected_rows:
                rows_by_stage.setdefault(str(row.get("stage") or ""), []).append(row)
            canonical_rows: dict[str, dict[str, Any]] = {}
            for stage, rows in rows_by_stage.items():
                metric_variants = {
                    json.dumps(row.get("metrics") or {}, sort_keys=True)
                    for row in rows
                }
                if not stage or len(metric_variants) != 1:
                    return {
                        "success": False,
                        "error_code": "ambiguous_evidence_binding",
                        "message": "RNA-seq manifests disagree within one stage identity.",
                    }
                canonical_rows[stage] = sorted(
                    rows, key=lambda row: str(row.get("path") or "")
                )[0]
            report = await asyncio.to_thread(
                build_evidence_report,
                final_dir,
                effective_prefix,
                job_id=job_id,
                rnaseq_scope_id=rnaseq_scope_id,
                include_stages=list(canonical_rows),
            )
            if report is None:
                return {
                    "success": False,
                    "error_code": "result_not_found",
                    "message": "No analysis result matched the request.",
                }
            top_results: dict[str, list[dict[str, str]]] = {}
            source_bindings: dict[str, dict[str, Any]] = {}
            stage_metrics: dict[str, dict[str, Any]] = {}
            for stage, row in canonical_rows.items():
                manifest_path = Path(str(row.get("path") or ""))
                stage_metrics[stage] = dict(row.get("metrics") or {})
                if stage in {
                    "rnaseq_de",
                    "rnaseq_go_enrich",
                    "rnaseq_kegg_enrich",
                }:
                    rows = await asyncio.to_thread(
                        _bounded_tabular_artifact_rows,
                        manifest_path,
                        final_dir=final_dir,
                    )
                    if rows:
                        top_results[stage] = rows
                source_bindings[stage] = {
                    "manifest_ref": (
                        Path("final")
                        / manifest_path.resolve().relative_to(final_dir.resolve())
                    ).as_posix(),
                    "stage": stage,
                    "status": row.get("status", ""),
                }
            projection = {
                "projection_version": "compact_v1",
                "success": True,
                "canonical_identity": {
                    "prefix": effective_prefix,
                    "rnaseq_scope_id": rnaseq_scope_id,
                },
                "metadata": {
                    "prefix": effective_prefix,
                    "rnaseq_scope_id": rnaseq_scope_id,
                },
                "result_agent": {
                    "stage_metrics": _compact_stage_metrics(stage_metrics),
                    "key_findings": _compact_result_findings(report, limit=16),
                    "structured_result_interpretations": _compact_structured_interpretations(report),
                    "top_results": top_results,
                },
                "source_bindings": source_bindings,
                "source_summary": _compact_source_summary(report.get("source_summary")),
            }
            if len(json.dumps(projection, ensure_ascii=False, sort_keys=True)) > 20_000:
                projection["result_agent"]["stage_metrics"] = {}
            if len(json.dumps(projection, ensure_ascii=False, sort_keys=True)) > 20_000:
                return {
                    "success": False,
                    "error_code": "result_payload_too_large",
                    "message": "The compact result exceeds the safe response budget.",
                }
            return projection
        has_exact_identity_selector = any(
            (args.scope_id, args.analysis_route, args.member_set_id)
        )
        if not args.stage and not has_exact_identity_selector:
            effective_prefix = args.prefix
            if not effective_prefix:
                family = str(index_data.get("family") or "").strip()
                target_species = index_data.get("target_species") or []
                expected_prefix = (
                    f"{target_species[0]}.{family}"
                    if len(target_species) == 1 and family
                    else ""
                )
                available_prefixes = {
                    str(row.get("prefix") or "")
                    for row in manifests
                    if row.get("status") in SUCCESS_STATUSES
                    and not str(row.get("stage") or "").startswith("rnaseq_")
                    and str(row.get("stage") or "") != "genome_prepare"
                }
                if expected_prefix in available_prefixes:
                    effective_prefix = expected_prefix
                else:
                    canonical_prefixes = {
                        value
                        for value in available_prefixes
                        if re.fullmatch(
                            r"[A-Za-z][A-Za-z0-9_-]{0,31}\.[A-Za-z][A-Za-z0-9_-]{0,63}",
                            value,
                        )
                    }
                    if len(canonical_prefixes) == 1:
                        effective_prefix = next(iter(canonical_prefixes))
                    elif len(canonical_prefixes) > 1:
                        return {
                            "success": False,
                            "error_code": "ambiguous_prefix",
                            "message": "Multiple gene-family result prefixes match; provide prefix.",
                            "candidates": sorted(canonical_prefixes),
                        }
            integrated_identities = [
                candidate
                for candidate in _candidate_identities(
                    manifests,
                    prefix=effective_prefix,
                    stage="",
                )
                if _canonical_identity_is_complete(candidate)
            ]
            if effective_prefix and len(integrated_identities) > 1:
                integrated_manifests = list(manifests)
                ambiguous_stages: list[str] = []
                while True:
                    canonical_rows, ambiguous_stage = _integrated_gene_family_rows(
                        integrated_manifests,
                        prefix=effective_prefix,
                    )
                    if canonical_rows is not None or not ambiguous_stage:
                        break
                    ambiguous_stages.append(ambiguous_stage)
                    integrated_manifests = [
                        row
                        for row in integrated_manifests
                        if str(row.get("stage") or "") != ambiguous_stage
                    ]
                if canonical_rows is None or (not canonical_rows and ambiguous_stages):
                    return {
                        "success": False,
                        "error_code": "ambiguous_evidence_binding",
                        "message": "Multiple equally valid result identities match one stage.",
                        "stage": ambiguous_stages[0] if ambiguous_stages else ambiguous_stage,
                    }
                if canonical_rows:
                    source_bindings: dict[str, dict[str, Any]] = {}
                    stage_metrics: dict[str, dict[str, Any]] = {}
                    key_findings: list[dict[str, Any]] = []
                    structured_interpretations: dict[str, dict[str, Any]] = {}
                    source_summary: dict[str, int] = {}
                    for stage, row in sorted(canonical_rows.items()):
                        identity = _manifest_identity(row)
                        report = await asyncio.to_thread(
                            build_evidence_report,
                            final_dir,
                            effective_prefix,
                            job_id=job_id,
                            scope_id=identity["scope_id"],
                            analysis_route=identity["analysis_route"],
                            member_set_id=identity["member_set_id"],
                            include_stages=[stage],
                        )
                        if isinstance(report, dict):
                            key_findings.extend(_compact_result_findings(report, limit=1))
                            structured_interpretations.update(
                                _compact_structured_interpretations(report)
                            )
                            for key, value in _compact_source_summary(
                                report.get("source_summary")
                            ).items():
                                source_summary[key] = source_summary.get(key, 0) + value
                        manifest_path = Path(str(row.get("path") or ""))
                        stage_metrics[stage] = dict(_manifest_row_metrics(row))
                        source_bindings[stage] = {
                            "manifest_ref": (
                                Path("final")
                                / manifest_path.resolve().relative_to(final_dir.resolve())
                            ).as_posix(),
                            "stage": stage,
                            "status": row.get("status", ""),
                            "canonical_identity": identity,
                        }
                    projection = {
                        "projection_version": "compact_v1",
                        "success": True,
                        "canonical_identity": {"prefix": effective_prefix},
                        "metadata": {
                            "prefix": effective_prefix,
                            "report_mode": "integrated_gene_family",
                        },
                        "result_agent": {
                            "completed_stages": sorted(canonical_rows),
                            "ambiguous_stages": [
                                {
                                    "stage": stage,
                                    "reason": "multiple_canonical_identities",
                                }
                                for stage in ambiguous_stages
                            ],
                            "stage_metrics": _compact_stage_metrics(stage_metrics),
                            "key_findings": key_findings[:16],
                            "structured_result_interpretations": structured_interpretations,
                        },
                        "source_bindings": source_bindings,
                        "source_summary": source_summary,
                    }
                    if len(json.dumps(projection, ensure_ascii=False, sort_keys=True)) > 20_000:
                        projection["result_agent"]["stage_metrics"] = {}
                    if len(json.dumps(projection, ensure_ascii=False, sort_keys=True)) > 20_000:
                        return {
                            "success": False,
                            "error_code": "result_payload_too_large",
                            "message": "The compact result exceeds the safe response budget.",
                        }
                    return projection
        if args.stage.startswith("rnaseq_"):
            if len(rnaseq_rows) > 1:
                return {
                    "success": False,
                    "error_code": "ambiguous_scope",
                    "message": "Multiple RNA-seq results match; provide prefix.",
                    "candidates": [
                        {
                            "prefix": row["prefix"],
                            "rnaseq_scope_id": row["rnaseq_scope_id"],
                            "stage": row["stage"],
                        }
                        for row in rnaseq_rows
                    ],
                }
            if not rnaseq_rows:
                return {
                    "success": False,
                    "error_code": "result_not_found",
                    "message": "No analysis result matched the request.",
                }
            selected = rnaseq_rows[0]
            effective_prefix = str(selected["prefix"])
            rnaseq_scope_id = str(selected["rnaseq_scope_id"])
            report = await asyncio.to_thread(
                build_evidence_report,
                final_dir,
                effective_prefix,
                job_id=job_id,
                rnaseq_scope_id=rnaseq_scope_id,
                include_stages=[str(selected["stage"])],
            )
            if report is None:
                return {
                    "success": False,
                    "error_code": "result_not_found",
                    "message": "No analysis result matched the request.",
                }
            metrics = dict(selected.get("metrics") or {})
            manifest_path = Path(str(selected["path"]))
            if not metrics:
                metrics = json.loads(manifest_path.read_text(encoding="utf-8")).get("metrics") or {}
            stage = str(selected["stage"])
            top_rows = await asyncio.to_thread(
                _bounded_tabular_artifact_rows,
                manifest_path,
                final_dir=final_dir,
            )
            return {
                "success": True,
                "canonical_identity": {
                    "prefix": effective_prefix,
                    "rnaseq_scope_id": rnaseq_scope_id,
                },
                "metadata": {
                    "prefix": effective_prefix,
                    "rnaseq_scope_id": rnaseq_scope_id,
                },
                "result_agent": {
                    "stage_metrics": _compact_stage_metrics({stage: metrics}),
                    "key_findings": _compact_result_findings(report),
                    "structured_result_interpretations": _compact_structured_interpretations(report),
                    "top_results": {stage: top_rows} if top_rows else {},
                },
                "source_bindings": {
                    stage: {
                        "manifest_ref": (
                            Path("final")
                            / Path(str(selected["path"])).resolve().relative_to(final_dir.resolve())
                        ).as_posix(),
                        "stage": selected["stage"],
                        "status": selected["status"],
                    }
                },
                "source_summary": _compact_source_summary(report.get("source_summary")),
            }
        candidates = _candidate_identities(manifests, prefix=args.prefix, stage=args.stage)
        candidates = [
            candidate
            for candidate in candidates
            if _result_identity_is_complete(candidate, stage=args.stage)
        ]
        requested = {
            "scope_id": args.scope_id,
            "analysis_route": args.analysis_route,
            "member_set_id": args.member_set_id,
        }
        matching = [
            candidate
            for candidate in candidates
            if all(not requested[key] or candidate[key] == requested[key] for key in requested)
        ]
        if len(matching) > 1:
            canonical_identities = {
                (candidate["scope_id"], candidate["analysis_route"], candidate["member_set_id"])
                for candidate in matching
            }
            prefixes = {candidate["prefix"] for candidate in matching}
            ambiguous_prefix = len(canonical_identities) == 1 and len(prefixes) > 1
            return {
                "success": False,
                "error_code": "ambiguous_prefix" if ambiguous_prefix else "ambiguous_scope",
                "message": (
                    "Multiple result prefixes match; provide prefix."
                    if ambiguous_prefix
                    else "Multiple result identities match; provide scope_id, analysis_route, and member_set_id."
                ),
                "candidates": matching,
            }
        if candidates and not matching:
            return {
                "success": False,
                "error_code": "scope_not_found",
                "message": "No result matches the requested identity.",
                "candidates": candidates,
            }

        if not matching:
            return {
                "success": False,
                "error_code": "result_not_found",
                "message": "No analysis result matched the request.",
            }

        selected = matching[0]
        effective_prefix = selected["prefix"]
        identity = {key: selected[key] for key in requested}
        same_scope = [
            {key: candidate[key] for key in requested}
            for candidate in candidates
            if candidate["scope_id"] == identity["scope_id"]
        ]
        if any(candidate != identity for candidate in same_scope):
            return {
                "success": False,
                "error_code": "ambiguous_evidence_binding",
                "message": "Existing evidence loading cannot isolate this member set without mixing identities.",
                "candidates": same_scope,
            }
        source_rows = _matching_manifest_rows(
            manifests,
            prefix=effective_prefix,
            stage=args.stage,
            identity=identity,
        )
        bound_sources = await asyncio.to_thread(
            _bind_exact_manifest_sources,
            source_rows,
            identity=identity,
            final_dir=final_dir,
        )
        if bound_sources is None:
            return {
                "success": False,
                "error_code": "ambiguous_evidence_binding",
                "message": "Exact manifest sources are missing, inconsistent, or disagree on metrics.",
                "canonical_identity": identity,
            }
        bound_metrics, source_bindings = bound_sources
        report = await asyncio.to_thread(
            build_evidence_report,
            final_dir,
            effective_prefix,
            job_id=job_id,
            scope_id=identity["scope_id"],
            analysis_route=identity["analysis_route"],
            member_set_id=identity["member_set_id"],
            include_stages=[args.stage] if args.stage else None,
        )
        if report is None:
            return {
                "success": False,
                "error_code": "result_not_found",
                "message": "No analysis result matched the request.",
                "canonical_identity": identity,
            }
        report_metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
        if not _report_identity_is_compatible(report_metadata, identity):
            return {
                "success": False,
                "error_code": "ambiguous_evidence_binding",
                "message": "The evidence report identity is incompatible with the selected Manifest identity.",
                "canonical_identity": identity,
            }
        try:
            projection = {
                "projection_version": "compact_v1",
                "success": True,
                "canonical_identity": identity,
                "metadata": {"prefix": effective_prefix, **identity},
                "result_agent": {
                    "stage_metrics": _compact_stage_metrics(bound_metrics),
                    "key_findings": _compact_result_findings(report),
                    "structured_result_interpretations": _compact_structured_interpretations(report),
                },
                "source_bindings": source_bindings,
                "source_summary": _compact_source_summary(report.get("source_summary")),
            }
        except _ProjectionTooLarge:
            projection = None
        if (
            projection is None
            or len(json.dumps(projection, ensure_ascii=False, sort_keys=True)) > 20_000
        ):
            return {
                "success": False,
                "error_code": "result_payload_too_large",
                "message": "The compact result exceeds the safe response budget.",
            }
        return projection

    async def query_rag(args: QueryPlantAIRagArgs) -> dict[str, Any]:
        try:
            raw = await asyncio.to_thread(query_dual_kb_qa, args.question, top_k=10)
        except Exception:
            raw = {
                "success": False,
                "error_code": "dual_kb_unavailable",
                "answer": "",
                "sources": [],
            }
        return normalize_rag_tool_result(raw)

    def intent_query_with_confirmed_context(
        query: str,
        *,
        workflow_id: str | None = None,
        guidance: bool = False,
        language: str = "zh",
    ) -> str:
        """Adapt MainAgent memory to the original IntentAgent text contract."""
        text = str(query or "").strip()
        en = language == "en"
        if (
            text.startswith("执行数据准备（genome_prepare）")
            and "当前业务动作已由数据准备契约唯一确定" in text
        ):
            return text
        if goal_ledger is None:
            return text
        terms = goal_ledger.user_terms
        context: list[str] = []
        family = str(terms.get("gene_family") or "").strip()
        if family:
            context.append(f"Gene family: {family}" if en else f"基因家族 {family}")
        for key, label_zh, label_en in (
            ("target_species", "目标物种", "Target species"),
            ("reference_species", "参考物种", "Reference species"),
            ("comparative_species", "比较物种", "Comparative species"),
        ):
            label = label_en if en else label_zh
            values = terms.get(key) or ()
            if isinstance(values, (list, tuple)):
                cleaned = [
                    str(value).strip()
                    for value in values
                    if str(value or "").strip()
                ]
                if cleaned:
                    context.append(f"{label} {','.join(cleaned)}")
        rna_route = str(terms.get("rna_route") or "").strip().lower()
        if workflow_id in {"rnaseq_whole_genome", "rnaseq_family_member"}:
            business_context = load_rnaseq_business_context(workflow_id)
            mode = business_context["analysis_mode"]["mode"]
            experiment_design = business_context["authoritative_inputs"][
                "experiment_design"
            ]
            if mode == "whole_genome":
                enabled_designs = "、".join(business_context["enabled_designs"])
                disabled_designs = "、".join(business_context["disabled_designs"])
                if en:
                    context.append(
                        f"RNA-seq mode: whole genome; {experiment_design} is the authoritative experimental design input; "
                        f"interpretable designs: {enabled_designs}, {disabled_designs} disabled this round; "
                        "design or batch downgrades must not happen silently"
                    )
                else:
                    context.append(
                        f"RNA-seq业务模式 全基因组；{experiment_design} 是实验设计权威输入；"
                        f"可解释设计为 {enabled_designs}，{disabled_designs} 本轮禁用；"
                        "设计或 batch 降级不得静默发生"
                    )
            else:
                if en:
                    context.append(
                        "RNA-seq mode: gene family member; use successful whole-genome differential expression results "
                        "and precisely confirmed gene family members for projection; must not auto-select the first member file, "
                        "must not implicitly re-run differential expression"
                    )
                else:
                    context.append(
                        "RNA-seq业务模式 基因家族成员；使用已成功的全基因组差异表达结果"
                        "和精确确认的基因家族成员进行投影；不得自动选择首个成员文件，"
                        "不得隐式重跑差异表达"
                    )
        elif rna_route == "whole_genome":
            context.append(
                "RNA-seq mode: whole genome" if en else "RNA-seq模式 全基因组"
            )
        elif rna_route == "family_member":
            context.append(
                "RNA-seq mode: gene family member" if en else "RNA-seq模式 基因家族成员"
            )
        job_id = bound_job_id or str(terms.get("active_job_id") or "").strip()
        formal_config_exists = bool(
            job_id and (project_root / "input" / job_id / "config.xlsx").is_file()
        )
        config_confirmed = (
            bool(terms.get("config_confirmed"))
            or "config.xlsx 已确认" in goal_ledger.approved_decisions
            or formal_config_exists
        )
        subfamily_confirm_observed = False
        if job_id:
            final_dir = project_root / "workspace" / job_id / "final"
            for manifest_path in final_dir.glob("*.manifest.json") if final_dir.exists() else ():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                stage = str(manifest.get("stage") or "").strip()
                status = manifest.get("status")
                manifest_prefix = str(manifest.get("prefix") or "").strip()
                family_matches = not family or bool(
                    re.search(
                        rf"(?:^|[.]){re.escape(family)}(?:[.]|$)",
                        manifest_prefix,
                        flags=re.IGNORECASE,
                    )
                )
                if (
                    stage == "subfamily_confirm"
                    and status == "success"
                    and family_matches
                ):
                    subfamily_confirm_observed = True
        genome_prepare_observed = False
        canonical_request = None
        if config_confirmed and not genome_prepare_observed:
            if en:
                context.append(
                    "Formal config.xlsx confirmed; this indicates configuration has been verified, "
                    "not that data preparation is complete. No data preparation completion evidence in current job; "
                    "specific analysis intent and stages still determined by the existing analysis system"
                )
            else:
                context.append(
                    "正式 config.xlsx 已确认；这只表示配置已核对，不等于数据准备已经完成，"
                    "当前 job 尚无数据准备完成证据；具体分析意图和阶段仍由现有分析系统判断"
                )
            if guidance and workflow_id in {
                "data_preparation",
                "gene_family",
                "rnaseq_whole_genome",
                "rnaseq_family_member",
            }:
                contract = load_canonical_workflow_contract("data_preparation")
                transition = next(
                    item
                    for item in contract.transitions
                    if item.from_state_id == "config_confirmed"
                )
                actions = {item.id: item for item in contract.actions}
                target_stage = actions[transition.action_id].target_stage
                if en:
                    canonical_request = (
                        f"Execute data preparation ({target_stage})\n"
                        "Business action uniquely determined by data preparation contract"
                    )
                else:
                    canonical_request = (
                        f"执行数据准备（{target_stage}）\n"
                        "当前业务动作已由数据准备契约唯一确定"
                    )
        if family and subfamily_confirm_observed:
            if en:
                context.append(
                    "Current job's subfamily_confirm completed successfully; "
                    "family members and subfamily classification table confirmed. "
                    "This is a current project state fact; specific analysis intent and stages still determined by the existing analysis system"
                )
            else:
                context.append(
                    "当前 job 的 subfamily_confirm 已成功完成，家族成员和亚家族分类表已确认，"
                    "这是当前项目状态事实；具体分析意图和阶段仍由现有分析系统判断"
                )
        if canonical_request:
            return canonical_request
        if not context:
            return text
        if en:
            return f"{text}\nConfirmed context: {'; '.join(context)}."
        return f"{text}\n已确认上下文：{'；'.join(context)}。"

    registry.set_analysis_handoff_resolver(intent_query_with_confirmed_context)

    async def recognize_intent(args: RecognizeAnalysisIntentArgs) -> dict[str, Any]:
        job_id = current_job(args)
        state = AgentState(
            session_id=f"router:{job_id or 'unbound'}",
            job_id=job_id,
            results={"completed_stages": []},
        )
        message = AgentMessage(
            sender="router",
            receiver="intent_v2",
            message_type="recognize_intent",
            payload={
                "query": intent_query_with_confirmed_context(args.query, language=args.language),
                "job_id": job_id,
                "prefix": args.prefix,
                "workspace": str(project_root / "workspace" / job_id) if job_id else "",
                **(
                    {
                        "goal_context": {
                            "active_goal": goal_ledger.active_goal,
                            "user_terms": goal_ledger.model_dump(
                                mode="json", round_trip=True
                            )["user_terms"],
                        }
                    }
                    if goal_ledger is not None
                    else {}
                ),
            },
        )
        returned_state = await IntentAgentV2(_NoCostLLMManager(llm_manager)).execute(state, message)
        candidate = dict(
            ((returned_state.results.get("results") or {}).get("intent") or {})
        )
        stages = [str(stage) for stage in candidate.get("stages") or ()]
        if any(stage.startswith("rnaseq_") for stage in stages):
            query_lower = str(args.query or "").lower()
            family_member = any(
                token in query_lower
                for token in (
                    "基因家族成员",
                    "家族成员",
                    "family member",
                    "family-member",
                )
            )
            whole_genome = any(
                token in query_lower
                for token in ("全基因组", "whole genome", "whole-genome")
            )
            if family_member or whole_genome:
                rna_route = "family_member" if family_member else "whole_genome"
                foreground_mode = "family" if family_member else "global"
                candidate["rna_route"] = rna_route
                candidate["foreground_mode"] = foreground_mode
                params = dict(candidate.get("params") or {})
                params["analysisRoute"] = rna_route
                params["rnaseqForegroundMode"] = foreground_mode
                candidate["params"] = params
        return _without_forbidden_intent_keys(candidate)

    def recognize_utility(args: RecognizeUtilityIntentArgs) -> dict[str, Any]:
        from gfa_core.mas.utils.intent_parser import parse_sequence_extract_request

        params = parse_sequence_extract_request(args.query)
        if not params:
            return {
                "success": False,
                "error_code": "utility_intent_not_found",
                "message": "No supported utility request was recognized.",
            }
        return {
            "success": True,
            "taskType": "utility",
            "stage": "sequence_extract_by_id",
            "stages": ["sequence_extract_by_id"],
            "utility_tool": (
                "fasta_header_clean"
                if params.get("sequenceExtractCleanHeaders")
                else "sequence_extract_by_id"
            ),
            "params": params,
        }

    def inspect_job_file(args: InspectJobFileArgs) -> dict[str, Any]:
        from gfa_core.mas.job_file_reader import read_job_file_preview

        return read_job_file_preview(
            project_root,
            bound_job_id,
            args.path,
            max_lines=args.max_lines,
        )

    def list_current_job_files(args: ListJobFilesArgs) -> dict[str, Any]:
        from gfa_core.mas.job_file_reader import list_job_files

        return list_job_files(
            project_root,
            bound_job_id,
            roots=tuple(args.roots),
            limit=args.limit,
            cursor=args.cursor,
        )

    registry.register(
        name="inspect_project_context",
        description="Inspect one PlantAI job's project context without changing it.",
        args_model=(BoundInspectProjectContextArgs if bound_job_id else InspectProjectContextArgs),
        handler=inspect_project,
        permission="read_only",
    )
    registry.register(
        name="inspect_analysis_results",
        description="Read evidence from one canonical PlantAI analysis result identity.",
        args_model=(BoundInspectAnalysisResultsArgs if bound_job_id else InspectAnalysisResultsArgs),
        handler=inspect_results,
        permission="read_only",
    )
    registry.register(
        name="query_plantai_rag",
        description="Query PlantAI's existing gene-family knowledge service.",
        args_model=QueryPlantAIRagArgs,
        handler=query_rag,
        permission="read_only",
    )
    registry.register(
        name="recognize_analysis_intent",
        description="Ask IntentAgentV2 for a candidate analysis intent without planning or execution.",
        args_model=(BoundRecognizeAnalysisIntentArgs if bound_job_id else RecognizeAnalysisIntentArgs),
        handler=recognize_intent,
        permission="pure",
    )
    registry.register(
        name="recognize_utility_intent",
        description="Recognize one supported utility request without planning or execution.",
        args_model=RecognizeUtilityIntentArgs,
        handler=recognize_utility,
        permission="pure",
    )
    if bound_job_id:
        registry.register(
            name="list_job_files",
            description=(
                "List real safe file handles in the current PlantAI job. "
                "Use input/final/logs by default; request workspace only when the user asks for intermediates or all files."
            ),
            args_model=ListJobFilesArgs,
            handler=list_current_job_files,
            permission="read_only",
            max_result_chars=70_000,
        )
        registry.register(
            name="inspect_job_file",
            description="Preview one safe text file attached to the current PlantAI job.",
            args_model=InspectJobFileArgs,
            handler=inspect_job_file,
            permission="read_only",
            max_result_chars=70_000,
        )
    return registry
