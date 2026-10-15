"""Orchestrator for extension-stage lifecycle and API conversion."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gfa_core.extension_stage.api_converter import ApiConversionError, convert_with_deepseek_api, repair_build_with_deepseek_api
from gfa_core.extension_stage.api_spec_sanitizer import sanitize_conversion_spec
from gfa_core.extension_stage.code_artifacts import ExtensionCodeArtifactError, save_code_artifacts
from gfa_core.extension_stage.failure_classifier import classify_exception
from gfa_core.extension_stage.lessons import append_lesson, load_recent_lessons
from gfa_core.extension_stage.provenance import inventory_files, write_api_attempt, write_build_record
from gfa_core.extension_stage.schema_validator import ExtensionSpecValidationError, validate_conversion_spec
from gfa_core.extension_stage.tooling_audit import API_ASSIST_TOOL_PROBES, repair_field_snapshot, tool_capability_report
from gfa_core.extension_stage.wrapper_builder import DeterministicWrapperError, build_deterministic_wrapper_spec


@dataclass
class ExtensionStageAgent:
    """Lifecycle coordinator used by backend and MAS integration."""

    manager: Any

    def build(self, **kwargs: Any) -> dict[str, Any]:
        return self.manager.create_from_uploads(**kwargs)

    def create_from_uploads(self, **kwargs: Any) -> dict[str, Any]:
        return self.build(**kwargs)

    def trial_run(self, extension_id: str) -> dict[str, Any]:
        return self.manager.trial_run(extension_id)

    def validate(self, extension_id: str) -> dict[str, Any]:
        return self.manager.validate(extension_id)

    def repair(self, extension_id: str, **kwargs: Any) -> dict[str, Any]:
        return self.manager.repair(extension_id, **kwargs)

    def repair_from_context(self, extension_id: str, **kwargs: Any) -> dict[str, Any]:
        return self.manager.repair_from_context(extension_id, **kwargs)

    def publish(self, extension_id: str) -> dict[str, Any]:
        return self.manager.publish(extension_id)

    def run(self, **kwargs: Any) -> dict[str, Any]:
        return self.manager.run(**kwargs)

    def confirm(self, extension_id: str, **kwargs: Any) -> dict[str, Any]:
        return self.manager.confirm(extension_id, **kwargs)

    def list_extensions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.manager.list_extensions(**kwargs)

    def load(self, extension_id: str) -> dict[str, Any]:
        return self.manager.load(extension_id)

    def preview(self, extension_id: str) -> dict[str, Any]:
        return self.manager.preview(extension_id)

    def validate_parameters(self, extension_id: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.manager.validate_parameters(extension_id, parameters)

    def list_repair_candidates(self, extension_id: str) -> list[dict[str, Any]]:
        return self.manager.list_repair_candidates(extension_id)

    def promote_repair_candidate(self, extension_id: str, candidate_id: str, *, publish: bool = False) -> dict[str, Any]:
        return self.manager.promote_repair_candidate(extension_id, candidate_id, publish=publish)

    def list_runs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.manager.list_runs(**kwargs)

    def resolve_run_file(self, *args: Any, **kwargs: Any) -> Path:
        return self.manager.resolve_run_file(*args, **kwargs)


def build_api_conversion_spec(
    *,
    store_root: Path,
    display_name: str,
    runtime_env: str,
    raw_code: Path,
    raw_data: Path,
    converted_code: Path,
) -> dict[str, Any]:
    """Build a validated deterministic-wrapper spec from uploaded code/data."""
    mode = os.environ.get("BBAV5_EXTENSION_CONVERTER", "deepseek_api").strip().lower()
    root = Path(raw_code).parent
    if mode not in {"", "deepseek", "deepseek_api", "api"}:
        raise ValueError(f"build_api_conversion_spec only handles API modes, got {mode}")

    attempts: list[dict[str, Any]] = []
    failure_context: dict[str, Any] = {}
    max_attempts = 20
    tool_capabilities = tool_capability_report(runtime_env=runtime_env, tools=API_ASSIST_TOOL_PROBES)
    for attempt in range(1, max_attempts + 1):
        phase = "initial_conversion" if attempt == 1 and not failure_context else "build_repair"
        record: dict[str, Any] = {
            "attempt": attempt,
            "phase": phase,
            "display_name": display_name,
            "runtime_env": runtime_env,
            "agent": "ExtensionStageAgent",
            "status": "failed",
            "tool_capabilities": tool_capabilities,
        }
        try:
            if phase == "initial_conversion":
                spec = convert_with_deepseek_api(
                    display_name=display_name,
                    raw_code=raw_code,
                    raw_data=raw_data,
                    runtime_env=runtime_env,
                    lessons=load_recent_lessons(store_root),
                )
            else:
                spec = repair_build_with_deepseek_api(
                    display_name=display_name,
                    raw_code=raw_code,
                    raw_data=raw_data,
                    runtime_env=runtime_env,
                    failure_context=failure_context,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    lessons=load_recent_lessons(store_root),
                )
            spec = sanitize_conversion_spec(spec, raw_data=raw_data, source=phase)
            spec = validate_conversion_spec(spec, require_code_artifacts=False)
            spec = build_deterministic_wrapper_spec(spec, raw_code=raw_code, raw_data=raw_data)
            spec = validate_conversion_spec(spec, require_code_artifacts=True)
            generated_artifacts = save_code_artifacts(spec, converted_code)
            spec["generated_code_artifacts"] = generated_artifacts
            spec["agent_record"] = {
                "agent": "ExtensionStageAgent",
                "conversion_mode": "deepseek_api",
                "api_independent": True,
                "codex_manual_spec_edit": False,
                "attempt": attempt,
                "phase": phase,
            }
            record.update({
                "status": "success",
                "converter": spec.get("converter", {}),
                "generated_code_artifacts": generated_artifacts,
                "input_roles": spec.get("input_roles", {}),
                "output_contract": spec.get("output_contract", []),
                "semantic_command_template": spec.get("semantic_command_template", ""),
                "wrapper_command_template": spec.get("wrapper_command_template", spec.get("command_template", "")),
                "sanitizer_actions": spec.get("sanitizer_actions", []),
                "repair_field_snapshot": repair_field_snapshot(spec),
            })
            attempts.append(record)
            write_api_attempt(root, record)
            build_record = {
                "schema_version": "extension_build_v1",
                "status": "conversion_succeeded",
                "agent": "ExtensionStageAgent",
                "display_name": display_name,
                "runtime_env": runtime_env,
                "raw_code_files": inventory_files(raw_code, relative_to=root),
                "raw_test_data_files": inventory_files(raw_data, relative_to=root),
                "api_attempts": attempts,
                "sanitizer_actions": spec.get("sanitizer_actions", []),
                "generated_code_artifacts": generated_artifacts,
            }
            spec["build_attempts"] = attempts
            spec["build_record"] = write_build_record(root, build_record)
            return spec
        except (ApiConversionError, ExtensionSpecValidationError, ExtensionCodeArtifactError, DeterministicWrapperError) as exc:
            raw_response = getattr(exc, "raw_response", "")
            failure_context = classify_exception(phase="build", exc=exc, raw_response=raw_response)
            failure_context["tool_capabilities"] = tool_capabilities
            record.update({"status": "failed", "error": str(exc), "failure": failure_context})
            attempts.append(record)
            write_api_attempt(root, record)
            append_lesson(store_root, {"extension_id": root.name, "display_name": display_name}, {
                "phase": "build",
                "lesson_type": failure_context.get("code", "build_failed"),
                "status": "failed",
                "attempt": attempt,
                "error": str(exc),
                "failure": failure_context,
            })
            if not failure_context.get("retryable", True):
                break
    build_record = {
        "schema_version": "extension_build_v1",
        "status": "conversion_failed",
        "agent": "ExtensionStageAgent",
        "display_name": display_name,
        "runtime_env": runtime_env,
        "raw_code_files": inventory_files(raw_code, relative_to=root),
        "raw_test_data_files": inventory_files(raw_data, relative_to=root),
        "api_attempts": attempts,
        "last_failure": failure_context,
        "tool_capabilities": tool_capabilities,
    }
    write_build_record(root, build_record)
    message = str(failure_context.get("message") or (attempts[-1].get("error") if attempts else "unknown error"))
    raise ApiConversionError("DeepSeek API conversion failed after build repair loop: " + message)
