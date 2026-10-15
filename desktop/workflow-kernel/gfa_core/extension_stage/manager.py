"""Builder and runner for lightweight extension analysis stages.

The extension stage store keeps reusable converted code and metadata. Real
analysis runs are always materialized under workspace/<job_id>/extension_runs.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from gfa_core.extension_stage.api_converter import ApiConversionError, repair_with_deepseek_api
from gfa_core.extension_stage.api_spec_sanitizer import sanitize_conversion_spec
from gfa_core.extension_stage.agent import build_api_conversion_spec
from gfa_core.extension_stage.artifacts import (
    classify_file,
    execution_lesson_suggestion,
    extension_output_prefix,
    normalize_output_names,
    preview_file,
    run_has_empty_outputs,
    scan_misplaced_outputs,
    scan_outputs,
    validate_output_contract,
)
from gfa_core.extension_stage.code_artifacts import ExtensionCodeArtifactError, save_code_artifacts
from gfa_core.extension_stage.diagnostics import diagnose_extension_run
from gfa_core.extension_stage.executor import ExtensionExecutionError, execute_extension_command, render_command
from gfa_core.extension_stage.failure_classifier import classify_exception, classify_run
from gfa_core.extension_stage.file_resolution import input_path_equivalents
from gfa_core.extension_stage.golden_runner import run_golden_validation
from gfa_core.extension_stage.input_binding import ExtensionInputBindingError, materialize_config_inputs
from gfa_core.extension_stage.lessons import append_lesson, load_recent_lessons
from gfa_core.extension_stage.provenance import inventory_files, write_api_attempt, write_build_record, write_run_record
from gfa_core.extension_stage.publish_gate import missing_parameter_placeholders, publish_checks, unapplied_editable_parameters
from gfa_core.extension_stage.repair_gate import ExtensionRepairGateError, validate_repair_candidate
from gfa_core.extension_stage.runtime_view import runtime_view_for_run_record
from gfa_core.extension_stage.template_gate import (
    LEGACY_TEMPLATE_GATE_STATUS,
    TEMPLATE_GATE_VERSION,
    evaluate_template_gate,
    infer_archetype_id,
    template_gate_publish_status,
)
from gfa_core.extension_stage.template_cases import ARCHETYPE_BY_ID
from gfa_core.extension_stage.wrapper_builder import DeterministicWrapperError, build_deterministic_wrapper_spec
from gfa_core.extension_stage.schema_validator import ExtensionSpecValidationError, validate_conversion_spec
from gfa_core.extension_stage.semantic_validators import summarize_golden_failure
from gfa_core.extension_stage.spec_v2 import infer_analysis_type, infer_tool_family
from gfa_core.extension_stage.tooling_audit import (
    API_ASSIST_TOOL_PROBES,
    repair_field_diff,
    repair_field_snapshot,
    repair_stall_summary,
    tool_capability_report,
)


CODE_SUFFIXES = {".py", ".r", ".R", ".sh", ".pl", ".jl"}
SHELL_REQUIREMENT_IGNORES = {
    "alias",
    "break",
    "case",
    "cd",
    "continue",
    "do",
    "done",
    "echo",
    "else",
    "elif",
    "esac",
    "eval",
    "exec",
    "exit",
    "export",
    "fi",
    "for",
    "function",
    "if",
    "local",
    "printf",
    "pwd",
    "read",
    "return",
    "set",
    "shift",
    "source",
    "then",
    "trap",
    "unset",
    "while",
}


class ExtensionStageError(RuntimeError):
    """Raised for user-facing extension stage failures."""


@dataclass
class ExtensionStageManager:
    project_root: Path
    store_root: Path
    workspace_root: Path
    input_root: Path

    def __post_init__(self) -> None:
        self.project_root = Path(self.project_root).absolute()
        self.store_root = Path(self.store_root).absolute()
        self.workspace_root = Path(self.workspace_root).absolute()
        self.input_root = Path(self.input_root).absolute()
        self.store_root.mkdir(parents=True, exist_ok=True)
        (self.store_root / "success").mkdir(parents=True, exist_ok=True)
        (self.store_root / "failed").mkdir(parents=True, exist_ok=True)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.input_root.mkdir(parents=True, exist_ok=True)

    def create_from_uploads(
        self,
        *,
        display_name: str,
        command: str,
        runtime_env: str = "test4py311",
        code_files: list[tuple[str, bytes]],
        data_files: list[tuple[str, bytes]],
        description: str = "",
    ) -> dict[str, Any]:
        name = _safe_name(display_name)
        if not name:
            raise ExtensionStageError("Extension stage name is required.")
        if not code_files:
            raise ExtensionStageError("Upload at least one code file.")

        extension_id = f"{name}_{_short_id()}"
        root = self.store_root / extension_id
        raw_code = root / "raw_code"
        raw_data = root / "raw_test_data"
        converted_code = root / "converted_code"
        validation = root / "validation"
        for path in (raw_code, raw_data, converted_code, validation):
            path.mkdir(parents=True, exist_ok=True)

        saved_code = _save_uploads(raw_code, code_files)
        saved_data = _save_uploads(raw_data, data_files)
        for path in saved_code:
            dest = converted_code / path.relative_to(raw_code)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)

        conversion = self._convert_uploaded_code(
            display_name=display_name,
            command=command,
            runtime_env=runtime_env,
            raw_code=raw_code,
            raw_data=raw_data,
            converted_code=converted_code,
            saved_code=saved_code,
        )
        inferred_command = conversion["original_command"]
        command_template = conversion["command_template"]
        semantic_command_template = str(conversion.get("semantic_command_template") or (conversion.get("entrypoint") or {}).get("semantic_command_template") or "").strip()
        wrapper_command_template = str(conversion.get("wrapper_command_template") or (conversion.get("entrypoint") or {}).get("wrapper_command_template") or command_template).strip()
        main_code = _infer_main_code(saved_code, inferred_command)
        detected = _merge_requirements(_detect_requirements(saved_code), conversion.get("requirements") or {})
        extension = {
            "schema_version": "extension_stage_v1",
            "extension_id": extension_id,
            "name": name,
            "display_name": display_name.strip() or name,
            "description": description.strip(),
            "created_at": _now(),
            "status": "draft",
            "runtime": {
                "type": "conda",
                "env": runtime_env or "test4py311",
                "language": _infer_language(main_code),
            },
            "paths": {
                "root": str(root),
                "raw_code": "raw_code",
                "raw_test_data": "raw_test_data",
                "converted_code": "converted_code",
            },
            "entrypoint": _entrypoint_with_templates(
                original_command=inferred_command,
                command_template=command_template,
                semantic_command_template=semantic_command_template,
                wrapper_command_template=wrapper_command_template,
            ),
            "command_template": command_template,
            "wrapper_command_template": wrapper_command_template,
            "semantic_command_template": semantic_command_template,
            "template_type": conversion.get("template_type", ""),
            "archetype_id": conversion.get("archetype_id", ""),
            "analysis_type": conversion.get("analysis_type", ""),
            "tool_family": conversion.get("tool_family", ""),
            "converter": conversion.get("converter", {}),
            "input_roles": conversion.get("input_roles") or {},
            "input_bindings": conversion.get("input_bindings") or {},
            "requires_user_confirmation": bool(conversion.get("requires_user_confirmation", False)),
            "conversion_notes": conversion.get("notes", ""),
            "inputs": conversion.get("inputs") or {
                "input_dir": {
                    "type": "directory",
                    "label": "Input directory",
                    "required": True,
                    "description": "Current job input directory or selected input files.",
                }
            },
            "parameters": conversion.get("parameters") or {},
            "outputs": conversion.get("outputs") or {
                "directory": "outputs",
                "patterns": ["*.tsv", "*.csv", "*.txt", "*.md", "*.png", "*.jpg", "*.jpeg", "*.svg", "*.html", "*.pdf"],
            },
            "output_contract": conversion.get("output_contract") or [],
            "comparator_plan": conversion.get("comparator_plan") or {},
            "generated_code_artifacts": conversion.get("generated_code_artifacts") or [],
            "build_record": conversion.get("build_record", ""),
            "build_attempts": conversion.get("build_attempts") or [],
            "requirements": detected,
            "source_files": {
                "code": [str(p.relative_to(root)) for p in saved_code],
                "test_data": [str(p.relative_to(root)) for p in saved_data],
            },
            "build_status": "analyzed",
            "version": 1,
            "uploaded_inputs": _file_inventory(raw_data, root),
            "detected_inputs": _file_inventory(raw_data, root),
            "detected_parameters": _normalize_parameters(conversion.get("parameters") or {}),
            "confirmed_parameters": {},
            "outputs_detected": [],
            "outputs_confirmed": [],
            "validation_runs": [],
            "publish_checks": {},
            "template_gate": {},
            "nl_triggers": [],
        }
        extension["archetype_id"] = extension.get("archetype_id") or infer_archetype_id(extension)
        extension["template_gate"] = {
            "schema_version": TEMPLATE_GATE_VERSION,
            "status": "pending",
            "archetype_id": extension.get("archetype_id", ""),
            "checked_at": "",
            "errors": [],
            "warnings": ["template gate runs after golden validation"],
            "risk_checks": [],
            "gate_version": TEMPLATE_GATE_VERSION,
            "golden_status": "",
        }
        extension["parameters"] = extension["detected_parameters"]
        self._write_extension(root, extension)
        return {"success": True, "extension": extension}

    def _convert_uploaded_code(
        self,
        *,
        display_name: str,
        command: str,
        runtime_env: str,
        raw_code: Path,
        raw_data: Path,
        converted_code: Path,
        saved_code: list[Path],
    ) -> dict[str, Any]:
        mode = os.environ.get("BBAV5_EXTENSION_CONVERTER", "deepseek_api").strip().lower()
        root = raw_code.parent
        if mode in {"", "deepseek", "deepseek_api", "api"}:
            try:
                spec = build_api_conversion_spec(
                    store_root=self.store_root,
                    display_name=display_name,
                    runtime_env=runtime_env,
                    raw_code=raw_code,
                    raw_data=raw_data,
                    converted_code=converted_code,
                )
                spec["command_template"] = self._escape_format_literals(str(spec["command_template"]))
                return spec
            except ApiConversionError as exc:
                raise ExtensionStageError(str(exc)) from exc

        if mode not in {"rule", "rules", "local_rule"}:
            raise ExtensionStageError(f"Unsupported extension converter mode: {mode}")

        inferred_command = command.strip() or _infer_command_from_code(saved_code, raw_code)
        if not inferred_command.strip():
            raise ExtensionStageError("Could not infer a runnable command from uploaded code.")
        return {
            "template_type": "rule_based",
            "original_command": inferred_command,
            "command_template": self._command_to_template(inferred_command, raw_code, raw_data, raw_code),
            "inputs": {},
            "parameters": {},
            "outputs": {},
            "output_contract": [],
            "converter": {"type": "rule", "mode": mode},
        }

    def validate(self, extension_id: str) -> dict[str, Any]:
        extension = self.load(extension_id)
        root = self._extension_root(extension_id)
        run_id = f"validation_{_timestamp()}_{_short_id()}"
        run_dir = root / "validation" / run_id
        input_dir = root / "raw_test_data"
        result = self._execute(extension, run_dir=run_dir, input_dir=input_dir, validation=True)
        if result["status"] == "success":
            golden = run_golden_validation(extension, extension_root=root)
            result["golden_comparison"] = golden
            extension["behavior_baseline"] = golden.get("behavior_baseline") or {}
            extension["golden_comparison"] = golden
            extension["validation_evidence"] = {
                "last_validation_run": result.get("run_id", ""),
                "golden_status": golden.get("status", ""),
                "golden_workspace": golden.get("workspace", ""),
                "checked_at": _now(),
            }
            extension["archetype_id"] = extension.get("archetype_id") or infer_archetype_id(extension)
            gate = evaluate_template_gate(extension)
            extension["template_gate"] = gate
            result["template_gate"] = gate
            if golden.get("status") != "passed":
                diagnosis = summarize_golden_failure(golden)
                result["status"] = "failed"
                result["error"] = diagnosis.get("user_message") or golden.get("error", "golden comparison failed")
                result["diagnosis_report"] = diagnosis
        extension["status"] = "validated" if result["status"] == "success" else "validation_failed"
        extension["build_status"] = "validated" if result["status"] == "success" else "failed"
        extension["last_validation"] = result
        validation_runs = list(extension.get("validation_runs") or [])
        validation_runs.append({"run_id": result.get("run_id"), "status": result.get("status"), "workspace": result.get("workspace"), "elapsed_sec": result.get("elapsed_sec")})
        extension["validation_runs"] = validation_runs[-20:]
        if result["status"] == "success" and not extension.get("outputs_confirmed"):
            extension["outputs_confirmed"] = _mark_primary_outputs(result.get("outputs") or [])
        root = self._relocate_extension_root(root, extension)
        self._write_extension(root, extension)
        return {"success": result["status"] == "success", "extension": extension, "run": result}

    def trial_run(self, extension_id: str) -> dict[str, Any]:
        extension = self.load(extension_id)
        root = self._extension_root(extension_id)
        run_id = f"trial_{_timestamp()}_{_short_id()}"
        run_dir = root / "trial_runs" / run_id
        input_dir = root / "raw_test_data"
        result = self._execute(extension, run_dir=run_dir, input_dir=input_dir, validation=True)
        extension["trial_run"] = result
        extension["outputs_detected"] = _mark_primary_outputs(result.get("outputs") or [])
        confirmed_outputs = extension.get("outputs_confirmed") or []
        confirmed_has_empty = any(int(out.get("size") or 0) == 0 for out in confirmed_outputs if isinstance(out, dict))
        if result["status"] == "success" and (not confirmed_outputs or confirmed_has_empty):
            extension["outputs_confirmed"] = extension["outputs_detected"]
        elif not confirmed_outputs:
            extension["outputs_confirmed"] = extension["outputs_detected"]
        extension["build_status"] = "trial_succeeded" if result["status"] == "success" else "failed"
        extension["status"] = "trial_succeeded" if result["status"] == "success" else "trial_failed"
        root = self._relocate_extension_root(root, extension)
        self._write_extension(root, extension)
        return {"success": result["status"] == "success", "extension": extension, "run": result}

    def confirm(
        self,
        extension_id: str,
        *,
        parameters: dict[str, Any] | None = None,
        outputs: list[dict[str, Any]] | None = None,
        input_bindings: dict[str, Any] | None = None,
        display_name: str = "",
        description: str = "",
        nl_triggers: list[str] | None = None,
    ) -> dict[str, Any]:
        extension = self.load(extension_id)
        root = self._extension_root(extension_id)
        trial_run = extension.get("trial_run") or {}
        last_validation = extension.get("last_validation") or {}
        latest_validated = last_validation.get("status") == "success"
        if trial_run and trial_run.get("status") != "success" and not latest_validated:
            raise ExtensionStageError("Trial run failed; fix inputs or command template before confirming this extension stage.")
        if trial_run and any(int(out.get("size") or 0) == 0 for out in (trial_run.get("outputs") or [])) and not latest_validated:
            raise ExtensionStageError("Trial run produced empty output; fix inputs or command template before confirming this extension stage.")
        if display_name.strip():
            extension["display_name"] = display_name.strip()
        if description.strip():
            extension["description"] = description.strip()
        confirmed_parameters = _normalize_parameters(parameters or extension.get("parameters") or {})
        extension["confirmed_parameters"] = confirmed_parameters
        extension["parameters"] = confirmed_parameters
        if outputs is not None:
            extension["outputs_confirmed"] = _mark_primary_outputs(outputs)
        elif not extension.get("outputs_confirmed"):
            extension["outputs_confirmed"] = _mark_primary_outputs(extension.get("outputs_detected") or [])
        extension["output_contract"] = _merge_output_contract_with_confirmed_outputs(
            extension,
            extension.get("outputs_confirmed") or [],
        )
        extension["input_bindings"] = input_bindings or extension.get("input_bindings") or {}
        extension["nl_triggers"] = [str(x).strip() for x in (nl_triggers or extension.get("nl_triggers") or []) if str(x).strip()]
        extension["build_status"] = "confirmed"
        extension["status"] = "confirmed"
        extension["confirmed_at"] = _now()
        self._write_extension(root, extension)
        return {"success": True, "extension": extension}

    def preview(self, extension_id: str) -> dict[str, Any]:
        extension = self.load(extension_id)
        return {
            "success": True,
            "extension": extension,
            "parameters": extension.get("confirmed_parameters") or extension.get("parameters") or {},
            "outputs": extension.get("outputs_confirmed") or extension.get("outputs_detected") or [],
            "uploaded_inputs": extension.get("uploaded_inputs") or [],
            "trial_run": extension.get("trial_run") or {},
        }

    def validate_parameters(self, extension_id: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        extension = self.load(extension_id)
        root = self._extension_root(extension_id)
        input_dir = root / "raw_test_data"
        run_dir = root / "parameter_preview" / f"preview_{_timestamp()}_{_short_id()}"
        input_files = sorted(p for p in input_dir.rglob("*") if p.is_file())
        merged = _merged_parameter_values(extension, parameters or {})
        command = render_command(extension, run_dir=run_dir, input_dir=input_dir, input_files=input_files, parameters=merged)
        template = (extension.get("entrypoint") or {}).get("command_template", "")
        missing = sorted(missing_parameter_placeholders(template, merged))
        unapplied = sorted(unapplied_editable_parameters(template, extension.get("confirmed_parameters") or extension.get("parameters") or {}))
        return {
            "success": not missing and not unapplied,
            "command": command,
            "parameters": merged,
            "missing_parameters": missing,
            "unapplied_parameters": unapplied,
        }

    def repair(self, extension_id: str, max_attempts: int = 20, auto_validate: bool = True, auto_publish: bool = True) -> dict[str, Any]:
        max_attempts = max(1, min(int(max_attempts or 20), 20))
        extension = self.load(extension_id)
        root = self._extension_root(extension_id)
        last_validation = extension.get("last_validation") or {}
        if not last_validation:
            raise ExtensionStageError("Run validation before repairing this extension stage.")
        if last_validation.get("status") == "success":
            publish_error = ""
            if auto_publish and extension.get("status") != "published":
                try:
                    extension = self.publish(extension_id)["extension"]
                except ExtensionStageError as exc:
                    publish_error = str(exc)
            return {
                "success": True,
                "status": extension.get("status", "validated"),
                "attempts": 0,
                "extension": extension,
                "run": last_validation,
                "repair_history": extension.get("repair_history", []),
                "published": extension.get("status") == "published",
                "message": "Extension stage is already validated; repair is not required." + (f" Publish skipped: {publish_error}" if publish_error else ""),
            }

        raw_code = root / str((extension.get("paths") or {}).get("raw_code") or "raw_code")
        raw_data = root / str((extension.get("paths") or {}).get("raw_test_data") or "raw_test_data")
        repair_dir = root / "repair_history"
        repair_dir.mkdir(parents=True, exist_ok=True)
        attempts = []
        run = last_validation
        success = False
        runtime_env = str((extension.get("runtime") or {}).get("env") or "")
        tool_capabilities = tool_capability_report(runtime_env=runtime_env, tools=API_ASSIST_TOOL_PROBES)

        for attempt in range(1, max_attempts + 1):
            before_template = str((extension.get("entrypoint") or {}).get("command_template") or "")
            before_field_snapshot = repair_field_snapshot(extension)
            validation_context = self._build_repair_validation_context(root, run)
            record: dict[str, Any] = {
                "attempt": attempt,
                "started_at": _now(),
                "status": "repair_failed",
                "before_command_template": before_template,
                "before_field_snapshot": before_field_snapshot,
                "tool_capabilities": tool_capabilities,
                "validation_context": validation_context,
            }
            try:
                spec = repair_with_deepseek_api(
                    extension=extension,
                    raw_code=raw_code,
                    raw_data=raw_data,
                    validation_context=validation_context,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    lessons=load_recent_lessons(self.store_root),
                )
                spec = sanitize_conversion_spec(spec, raw_data=raw_data, source="validation_repair")
                spec = validate_repair_candidate(extension, spec)
                spec = build_deterministic_wrapper_spec(spec, raw_code=raw_code, raw_data=raw_data)
                spec = validate_conversion_spec(spec, require_code_artifacts=True)
                candidate_id = f"candidate_{_timestamp()}_{attempt}_{_short_id()}"
                candidate_dir = root / "repair_candidates" / candidate_id
                candidate_converted_dir = candidate_dir / "converted_code"
                self._copy_tree_contents(raw_code, candidate_converted_dir)
                generated_artifacts = save_code_artifacts(spec, candidate_converted_dir)
                spec["generated_code_artifacts"] = generated_artifacts
                after_template = self._escape_format_literals(str(spec["command_template"]))
                spec["command_template"] = after_template
                candidate_extension = _extension_with_repair_spec(extension, spec)
                contract_diff = _contract_diff(extension, candidate_extension)
                field_diff = repair_field_diff(extension, candidate_extension)
                record.update({
                    "status": "candidate_created",
                    "candidate_id": candidate_id,
                    "candidate_dir": _rel(candidate_dir, root),
                    "after_command_template": after_template,
                    "contract_diff": contract_diff,
                    "field_diff": field_diff,
                    "critical_fields_changed": field_diff.get("critical_fields_changed", []),
                    "repair_notes": spec.get("repair_notes", ""),
                    "fixed_reason": spec.get("fixed_reason", ""),
                    "converter": spec.get("converter", {}),
                    "generated_code_artifacts": spec.get("generated_code_artifacts", []),
                })
                _write_json(candidate_dir / "candidate_spec.json", spec)
                _write_json(candidate_dir / "contract_diff.json", contract_diff)
                if auto_validate:
                    candidate_run_dir = candidate_dir / "validation"
                    input_dir = root / str((extension.get("paths") or {}).get("raw_test_data") or "raw_test_data")
                    run = self._execute(
                        candidate_extension,
                        run_dir=candidate_run_dir,
                        input_dir=input_dir,
                        parameters={},
                        validation=True,
                        converted_code_dir=candidate_converted_dir,
                    )
                    if run.get("status") == "success":
                        golden = run_golden_validation(candidate_extension, extension_root=root, converted_code_dir=candidate_converted_dir)
                        run["golden_comparison"] = golden
                        candidate_extension["golden_comparison"] = golden
                        candidate_extension["archetype_id"] = candidate_extension.get("archetype_id") or infer_archetype_id(candidate_extension)
                        gate = evaluate_template_gate(candidate_extension)
                        candidate_extension["template_gate"] = gate
                        run["template_gate"] = gate
                        if golden.get("status") != "passed":
                            diagnosis = summarize_golden_failure(golden)
                            run["status"] = "failed"
                            run["error"] = diagnosis.get("user_message") or golden.get("error", "golden comparison failed")
                            run["diagnosis_report"] = diagnosis
                    record["validation"] = run
                    record["status"] = "candidate_validated" if run.get("status") == "success" else "candidate_validation_failed"
                    success = run.get("status") == "success"
                    _write_json(candidate_dir / "candidate_run.json", run)
                    if success:
                        promoted_response = self.promote_repair_candidate(extension_id, candidate_id, publish=auto_publish)
                        extension = promoted_response["extension"]
                else:
                    run = {}
                    success = False
            except ApiConversionError as exc:
                record["error"] = str(exc)
                run = _repair_error_run(root, before_template, exc)
            except (ExtensionSpecValidationError, ExtensionCodeArtifactError, ExtensionRepairGateError, DeterministicWrapperError) as exc:
                record["error"] = str(exc)
                run = _repair_error_run(root, before_template, exc)
            except Exception as exc:
                record["error"] = str(exc)
                run = _repair_error_run(root, before_template, exc)

            record["ended_at"] = _now()
            record["repair_stall"] = repair_stall_summary([*attempts, record])
            stalled = bool((record.get("repair_stall") or {}).get("stalled"))
            if stalled and not success:
                record["status"] = "repair_stalled"
            append_lesson(self.store_root, extension, {
                "phase": "repair",
                "lesson_type": record.get("status", "repair_attempt"),
                "attempt": attempt,
                "before_command_template": record.get("before_command_template", ""),
                "after_command_template": record.get("after_command_template", ""),
                "error": record.get("error", ""),
                "fixed_reason": record.get("fixed_reason", ""),
                "repair_notes": record.get("repair_notes", ""),
                "validation_status": (record.get("validation") or {}).get("status", "") if isinstance(record.get("validation"), dict) else "",
            })
            root = self._extension_root(extension_id)
            repair_dir = root / "repair_history"
            repair_dir.mkdir(parents=True, exist_ok=True)
            record_path = repair_dir / f"repair_{_timestamp()}_{attempt}.json"
            record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            history = list(extension.get("repair_history") or [])
            history.append(_rel(record_path, root))
            extension["repair_history"] = history
            extension["last_repair"] = record
            if auto_validate and not success:
                extension["status"] = "validation_failed"
            self._write_extension(root, extension)
            attempts.append(record)
            if success:
                break
            if stalled:
                break

        last_error = ""
        if isinstance(run, dict):
            last_error = str(run.get("error") or "")
        return {
            "success": success,
            "status": extension.get("status", ""),
            "attempts": len(attempts),
            "last_error": last_error,
            "extension": extension,
            "run": run,
            "repair_history": attempts,
            "published": extension.get("status") == "published",
        }

    def repair_from_context(
        self,
        extension_id: str,
        *,
        phase: str,
        run_context: dict[str, Any],
        max_attempts: int = 20,
        auto_publish: bool = True,
    ) -> dict[str, Any]:
        """Repair a failed non-build phase and re-run that phase's gate.

        phase is intentionally narrow: trial repairs rerun only execution on
        raw test data, while validation/publish_gate repairs must pass golden
        validation and template gate before promotion.
        """
        phase = str(phase or "").strip().lower()
        if phase not in {"trial", "validation", "publish_gate"}:
            raise ExtensionStageError(f"Unsupported extension repair phase: {phase}")
        max_attempts = max(1, min(int(max_attempts or 20), 20))
        extension = self.load(extension_id)
        root = self._extension_root(extension_id)
        raw_code = root / str((extension.get("paths") or {}).get("raw_code") or "raw_code")
        raw_data = root / str((extension.get("paths") or {}).get("raw_test_data") or "raw_test_data")
        repair_dir = root / f"{phase}_repair_history"
        repair_dir.mkdir(parents=True, exist_ok=True)
        attempts: list[dict[str, Any]] = []
        run = dict(run_context or {})
        success = False
        runtime_env = str((extension.get("runtime") or {}).get("env") or "")
        tool_capabilities = tool_capability_report(runtime_env=runtime_env, tools=API_ASSIST_TOOL_PROBES)

        for attempt in range(1, max_attempts + 1):
            before_template = str((extension.get("entrypoint") or {}).get("command_template") or "")
            before_field_snapshot = repair_field_snapshot(extension)
            validation_context = self._build_repair_validation_context(root, run)
            validation_context["repair_phase"] = phase
            validation_context["phase_attempt"] = attempt
            record: dict[str, Any] = {
                "attempt": attempt,
                "phase": phase,
                "started_at": _now(),
                "status": "repair_failed",
                "before_command_template": before_template,
                "before_field_snapshot": before_field_snapshot,
                "tool_capabilities": tool_capabilities,
                "validation_context": validation_context,
            }
            try:
                spec = repair_with_deepseek_api(
                    extension=extension,
                    raw_code=raw_code,
                    raw_data=raw_data,
                    validation_context=validation_context,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    lessons=load_recent_lessons(self.store_root),
                )
                spec = sanitize_conversion_spec(spec, raw_data=raw_data, source=f"{phase}_repair")
                spec = validate_repair_candidate(extension, spec)
                spec = build_deterministic_wrapper_spec(spec, raw_code=raw_code, raw_data=raw_data)
                spec = validate_conversion_spec(spec, require_code_artifacts=True)
                candidate_id = f"candidate_{phase}_{_timestamp()}_{attempt}_{_short_id()}"
                candidate_dir = root / "repair_candidates" / candidate_id
                candidate_converted_dir = candidate_dir / "converted_code"
                self._copy_tree_contents(raw_code, candidate_converted_dir)
                generated_artifacts = save_code_artifacts(spec, candidate_converted_dir)
                spec["generated_code_artifacts"] = generated_artifacts
                after_template = self._escape_format_literals(str(spec["command_template"]))
                spec["command_template"] = after_template
                candidate_extension = _extension_with_repair_spec(extension, spec)
                contract_diff = _contract_diff(extension, candidate_extension)
                field_diff = repair_field_diff(extension, candidate_extension)
                record.update({
                    "status": "candidate_created",
                    "candidate_id": candidate_id,
                    "candidate_dir": _rel(candidate_dir, root),
                    "after_command_template": after_template,
                    "contract_diff": contract_diff,
                    "field_diff": field_diff,
                    "critical_fields_changed": field_diff.get("critical_fields_changed", []),
                    "repair_notes": spec.get("repair_notes", ""),
                    "fixed_reason": spec.get("fixed_reason", ""),
                    "converter": spec.get("converter", {}),
                    "generated_code_artifacts": generated_artifacts,
                })
                _write_json(candidate_dir / "candidate_spec.json", spec)
                _write_json(candidate_dir / "contract_diff.json", contract_diff)

                candidate_run_dir = candidate_dir / ("trial" if phase == "trial" else "validation")
                input_dir = root / str((extension.get("paths") or {}).get("raw_test_data") or "raw_test_data")
                run = self._execute(
                    candidate_extension,
                    run_dir=candidate_run_dir,
                    input_dir=input_dir,
                    parameters={},
                    validation=True,
                    converted_code_dir=candidate_converted_dir,
                )
                if phase != "trial" and run.get("status") == "success":
                    golden = run_golden_validation(candidate_extension, extension_root=root, converted_code_dir=candidate_converted_dir)
                    run["golden_comparison"] = golden
                    candidate_extension["golden_comparison"] = golden
                    candidate_extension["archetype_id"] = candidate_extension.get("archetype_id") or infer_archetype_id(candidate_extension)
                    gate = evaluate_template_gate(candidate_extension)
                    candidate_extension["template_gate"] = gate
                    run["template_gate"] = gate
                    if golden.get("status") != "passed":
                        diagnosis = summarize_golden_failure(golden)
                        run["status"] = "failed"
                        run["error"] = diagnosis.get("user_message") or golden.get("error", "golden comparison failed")
                        run["diagnosis_report"] = diagnosis
                record["validation"] = run
                record["status"] = "candidate_validated" if run.get("status") == "success" else "candidate_validation_failed"
                _write_json(candidate_dir / "candidate_run.json", run)
                success = run.get("status") == "success"
                if success:
                    promoted_response = self.promote_repair_candidate(extension_id, candidate_id, publish=(phase != "trial" and auto_publish))
                    extension = promoted_response["extension"]
                    if phase == "trial":
                        extension["trial_run"] = run
                        extension["outputs_detected"] = _mark_primary_outputs(run.get("outputs") or [])
                        if not extension.get("outputs_confirmed"):
                            extension["outputs_confirmed"] = extension["outputs_detected"]
                        extension["status"] = "trial_succeeded"
                        extension["build_status"] = "trial_succeeded"
                        self._write_extension(self._extension_root(extension_id), extension)
            except ApiConversionError as exc:
                record["error"] = str(exc)
                run = _repair_error_run(root, before_template, exc)
            except (ExtensionSpecValidationError, ExtensionCodeArtifactError, ExtensionRepairGateError, DeterministicWrapperError) as exc:
                record["error"] = str(exc)
                run = _repair_error_run(root, before_template, exc)
            except Exception as exc:
                record["error"] = str(exc)
                run = _repair_error_run(root, before_template, exc)

            record["ended_at"] = _now()
            record["repair_stall"] = repair_stall_summary([*attempts, record])
            stalled = bool((record.get("repair_stall") or {}).get("stalled"))
            if stalled and not success:
                record["status"] = "repair_stalled"
            append_lesson(self.store_root, extension, {
                "phase": phase,
                "lesson_type": record.get("status", "repair_attempt"),
                "attempt": attempt,
                "before_command_template": record.get("before_command_template", ""),
                "after_command_template": record.get("after_command_template", ""),
                "error": record.get("error", ""),
                "fixed_reason": record.get("fixed_reason", ""),
                "repair_notes": record.get("repair_notes", ""),
                "validation_status": (record.get("validation") or {}).get("status", "") if isinstance(record.get("validation"), dict) else "",
            })
            root = self._extension_root(extension_id)
            repair_dir = root / f"{phase}_repair_history"
            repair_dir.mkdir(parents=True, exist_ok=True)
            record_path = repair_dir / f"repair_{_timestamp()}_{attempt}.json"
            record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            history_key = f"{phase}_repair_history"
            history = list(extension.get(history_key) or [])
            history.append(_rel(record_path, root))
            extension[history_key] = history
            extension["last_repair"] = record
            if not success:
                extension["status"] = "trial_failed" if phase == "trial" else "validation_failed"
            self._write_extension(self._extension_root(extension_id), extension)
            attempts.append(record)
            if success:
                break
            if stalled:
                break

        last_error = str(run.get("error") or "") if isinstance(run, dict) else ""
        return {
            "success": success,
            "phase": phase,
            "status": extension.get("status", ""),
            "attempts": len(attempts),
            "last_error": last_error,
            "extension": extension,
            "run": run,
            "repair_history": attempts,
            "published": extension.get("status") == "published",
        }

    def list_repair_candidates(self, extension_id: str) -> list[dict[str, Any]]:
        root = self._extension_root(extension_id)
        candidates = []
        for path in sorted((root / "repair_candidates").glob("candidate_*/candidate_spec.json")):
            candidate_dir = path.parent
            record = {
                "candidate_id": candidate_dir.name,
                "candidate_dir": _rel(candidate_dir, root),
                "candidate_spec": _rel(path, root),
                "contract_diff": {},
                "validation": {},
            }
            diff_path = candidate_dir / "contract_diff.json"
            run_path = candidate_dir / "candidate_run.json"
            if diff_path.exists():
                try:
                    record["contract_diff"] = json.loads(diff_path.read_text(encoding="utf-8"))
                except Exception:
                    record["contract_diff"] = {}
            if run_path.exists():
                try:
                    record["validation"] = json.loads(run_path.read_text(encoding="utf-8"))
                except Exception:
                    record["validation"] = {}
            candidates.append(record)
        return candidates

    def promote_repair_candidate(self, extension_id: str, candidate_id: str, publish: bool = False) -> dict[str, Any]:
        extension = self.load(extension_id)
        root = self._extension_root(extension_id)
        candidate_dir = root / "repair_candidates" / _safe_path_token(candidate_id)
        spec_path = candidate_dir / "candidate_spec.json"
        run_path = candidate_dir / "candidate_run.json"
        converted_dir = candidate_dir / "converted_code"
        if not spec_path.exists() or not converted_dir.exists():
            raise ExtensionStageError(f"Repair candidate not found or incomplete: {candidate_id}")
        run = {}
        if run_path.exists():
            run = json.loads(run_path.read_text(encoding="utf-8"))
            if run.get("status") != "success":
                raise ExtensionStageError(f"Repair candidate did not validate successfully: {candidate_id}")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        final_converted_dir = root / str((extension.get("paths") or {}).get("converted_code") or "converted_code")
        if final_converted_dir.exists():
            shutil.rmtree(final_converted_dir)
        shutil.copytree(converted_dir, final_converted_dir)
        promoted = _extension_with_repair_spec(extension, spec)
        promoted["status"] = "validated" if run.get("status") == "success" else "repaired"
        promoted["build_status"] = promoted["status"]
        promoted["repair_count"] = int(promoted.get("repair_count") or 0) + 1
        if run:
            promoted["last_validation"] = run
            if isinstance(run.get("golden_comparison"), dict):
                promoted["golden_comparison"] = run["golden_comparison"]
                promoted["behavior_baseline"] = run["golden_comparison"].get("behavior_baseline") or {}
            if isinstance(run.get("template_gate"), dict):
                promoted["template_gate"] = run["template_gate"]
            promoted["validation_evidence"] = {
                "last_validation_run": run.get("run_id", ""),
                "golden_status": (run.get("golden_comparison") or {}).get("status", ""),
                "golden_workspace": (run.get("golden_comparison") or {}).get("workspace", ""),
                "checked_at": _now(),
            }
        promoted.pop("publish_checks", None)
        promoted["promoted_repair_candidate"] = {
            "candidate_id": candidate_id,
            "promoted_at": _now(),
            "validation_status": run.get("status", ""),
        }
        self._write_extension(root, promoted)
        if publish:
            return self.publish(extension_id)
        return {"success": True, "extension": promoted, "candidate_id": candidate_id}

    def publish(self, extension_id: str) -> dict[str, Any]:
        extension = self.load(extension_id)
        if extension.get("status") != "published" and (extension.get("last_validation") or {}).get("status") == "success":
            extension["archetype_id"] = extension.get("archetype_id") or infer_archetype_id(extension)
            extension["template_gate"] = evaluate_template_gate(extension)
        checks = self._publish_checks(extension)
        if extension.get("status") != "published" and not checks["passed"]:
            extension["publish_checks"] = checks
            root = self._extension_root(extension_id)
            self._write_extension(root, extension)
            raise ExtensionStageError("Publish gate failed: " + "; ".join(checks["errors"]))
        extension["status"] = "published"
        extension["build_status"] = "published"
        extension["publish_checks"] = checks
        extension["published_at"] = _now()
        root = self._relocate_extension_root(self._extension_root(extension_id), extension, force="success")
        self._write_extension(root, extension)
        return {"success": True, "extension": extension}

    def _publish_checks(self, extension: dict[str, Any]) -> dict[str, Any]:
        checks = publish_checks(extension, _merged_parameter_values(extension, {}))
        checks["checked_at"] = _now()
        return checks

    def list_extensions(self, include_drafts: bool = False) -> list[dict[str, Any]]:
        items = []
        for path in self._extension_json_paths():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if include_drafts or data.get("status") == "published":
                if not include_drafts and not _extension_is_runnable_candidate(data):
                    continue
                items.append(_public_extension(data))
        return items

    def load(self, extension_id: str) -> dict[str, Any]:
        root = self._extension_root(extension_id)
        path = root / "extension.json"
        if not path.exists():
            raise ExtensionStageError(f"Extension stage not found: {extension_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def run(
        self,
        *,
        extension_id: str,
        job_id: str,
        input_paths: list[str] | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        extension = self.load(extension_id)
        if extension.get("status") != "published":
            raise ExtensionStageError("Extension stage is not published.")
        if not _extension_is_runnable_candidate(extension):
            raise ExtensionStageError("Extension stage requires spec_v2 migration or publish-gate repair before natural-language/job execution.")
        if not job_id:
            raise ExtensionStageError("job_id is required.")
        workspace = self.workspace_root / _safe_path_token(job_id)
        workspace.mkdir(parents=True, exist_ok=True)
        run_id = f"run_{_timestamp()}_{_short_id()}"
        run_dir = workspace / "extension_runs" / extension_id / run_id
        input_dir = run_dir / "inputs"
        input_dir.mkdir(parents=True, exist_ok=True)
        runtime_parameters = dict(parameters or {})
        explicit_input_paths = [str(x).strip() for x in (input_paths or []) if str(x).strip()]
        if explicit_input_paths:
            runtime_parameters["explicit_input_files"] = [Path(x).name for x in explicit_input_paths]
        self._materialize_inputs(job_id, explicit_input_paths, input_dir)
        if not any(input_dir.iterdir()):
            job_input_dir = self.input_root / _safe_path_token(job_id)
            if job_input_dir.exists():
                self._copy_tree_contents(job_input_dir, input_dir)
        config_bindings = {}
        try:
            config_bindings = materialize_config_inputs(extension, job_id, runtime_parameters, self.input_root, input_dir)
        except ExtensionInputBindingError as exc:
            raise ExtensionStageError(str(exc)) from exc
        if config_bindings:
            runtime_parameters["config_input_bindings"] = config_bindings
            runtime_parameters["binding_report"] = config_bindings
            runtime_parameters["strict_semantic_binding"] = True
        result = self._execute(extension, run_dir=run_dir, input_dir=input_dir, parameters=runtime_parameters, validation=False)
        return {"success": result["status"] == "success", "run": result}

    def list_runs(self, job_id: str = "") -> list[dict[str, Any]]:
        roots: list[Path]
        if job_id:
            roots = [self.workspace_root / _safe_path_token(job_id) / "extension_runs"]
        else:
            roots = sorted(self.workspace_root.glob("*/extension_runs"))
        runs = []
        for root in roots:
            if not root.exists():
                continue
            for path in sorted(root.glob("*/*/extension_run.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    runs.append(
                        runtime_view_for_run_record(
                            data,
                            path,
                            workspace_root=self.workspace_root,
                            input_root=self.input_root,
                        )
                    )
                except Exception:
                    continue
        return runs

    def resolve_run_file(self, job_id: str, run_id: str, rel_path: str) -> Path:
        base = self.workspace_root / _safe_path_token(job_id) / "extension_runs"
        matches = list(base.glob(f"*/{_safe_path_token(run_id)}"))
        if not matches:
            raise ExtensionStageError("Extension run not found.")
        target = (matches[0] / rel_path).resolve()
        if not _is_relative_to(target, matches[0].resolve()):
            raise ExtensionStageError("Invalid extension run file path.")
        if not target.exists() or not target.is_file():
            raise ExtensionStageError("Extension run file not found.")
        return target

    def _build_repair_validation_context(self, root: Path, run: dict[str, Any]) -> dict[str, Any]:
        context = {
            "repair_phase": run.get("repair_phase", ""),
            "status": run.get("status", ""),
            "command": run.get("command", ""),
            "returncode": run.get("returncode"),
            "error": run.get("error", ""),
            "workspace": run.get("workspace", ""),
            "outputs": run.get("outputs", []),
            "golden_comparison": run.get("golden_comparison", {}),
            "template_gate": run.get("template_gate", {}),
            "publish_checks": run.get("publish_checks", {}),
            "diagnosis_report": run.get("diagnosis_report", {}),
        }
        workspace = Path(str(run.get("workspace") or ""))
        logs = run.get("logs") or {}
        if workspace.exists():
            stdout = workspace / str(logs.get("stdout") or "logs/stdout.log")
            stderr = workspace / str(logs.get("stderr") or "logs/stderr.log")
            context["stdout_tail"] = _tail(stdout)
            context["stderr_tail"] = _tail(stderr)
        else:
            context["stdout_tail"] = ""
            context["stderr_tail"] = ""
        converted = root / "converted_code"
        generated_code_files = []
        if converted.exists():
            for path in sorted(p for p in converted.rglob("*") if p.is_file()):
                try:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    content = ""
                generated_code_files.append({
                    "path": _rel(path, converted),
                    "size": path.stat().st_size,
                    "content": content[:12000],
                })
        context["generated_code_files"] = generated_code_files
        context["extension_root"] = str(root)
        return context

    def _extension_root(self, extension_id: str) -> Path:
        token = _safe_path_token(extension_id)
        candidates = [
            self.store_root / token,
            self.store_root / "success" / token,
            self.store_root / "failed" / token,
        ]
        for candidate in candidates:
            if (candidate / "extension.json").exists():
                return candidate
        return candidates[0]

    def _extension_json_paths(self) -> list[Path]:
        paths: list[Path] = []
        for base in [self.store_root, self.store_root / "success", self.store_root / "failed"]:
            if not base.exists():
                continue
            for path in base.glob("*/extension.json"):
                if path.parent.name in {"success", "failed", "_backup", "_repair_lessons"}:
                    continue
                paths.append(path)
        return sorted(paths)

    def _relocate_extension_root(self, root: Path, extension: dict[str, Any], force: str = "") -> Path:
        token = _safe_path_token(extension.get("extension_id") or root.name)
        target_base = self.store_root
        status = str(extension.get("status") or "")
        build_status = str(extension.get("build_status") or "")
        if force == "success" or status == "published":
            target_base = self.store_root / "success"
        elif force == "failed" or status.endswith("_failed") or build_status == "failed":
            target_base = self.store_root / "failed"
        target = target_base / token
        root = Path(root).resolve()
        target = target.resolve()
        if root == target:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return target
        if root.exists():
            shutil.move(str(root), str(target))
        else:
            target.mkdir(parents=True, exist_ok=True)
        return target

    def _write_extension(self, root: Path, extension: dict[str, Any]) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "extension.json").write_text(json.dumps(extension, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (root / "extension.yaml").write_text(_minimal_yaml(extension), encoding="utf-8")

    def _command_to_template(self, command: str, raw_code: Path, raw_data: Path, converted_code: Path) -> str:
        text = command.strip()
        replacements = {
            str(raw_code.resolve()): "{code_dir}",
            str(raw_data.resolve()): "{input_dir}",
            str(raw_code): "{code_dir}",
            str(raw_data): "{input_dir}",
        }
        for source, target in replacements.items():
            text = text.replace(source, target)
        for path in sorted(raw_code.rglob("*")):
            if path.is_file():
                rel = path.relative_to(raw_code)
                text = text.replace(str(path.resolve()), "{code_dir}/" + rel.as_posix())
                text = text.replace(str(path), "{code_dir}/" + rel.as_posix())
                text = re.sub(rf"(?<![\w./-]){re.escape(path.name)}(?![\w./-])", "{code_dir}/" + rel.as_posix(), text)
        for path in sorted(raw_data.rglob("*")):
            if path.is_file():
                rel = path.relative_to(raw_data)
                replacement = "{input_file}" if len([p for p in raw_data.rglob("*") if p.is_file()]) == 1 else "{input_dir}/" + rel.as_posix()
                text = text.replace(str(path.resolve()), replacement)
                text = text.replace(str(path), replacement)
                text = re.sub(rf"(?<![\w./-]){re.escape(path.name)}(?![\w./-])", replacement, text)
                text = re.sub(rf"(?<![\w{{}}./-])(?:(?:\.\./|\./|[A-Za-z0-9_.-]+/)+){re.escape(path.name)}(?![\w./-])", replacement, text)
        text = self._rewrite_output_arguments(text)
        if "{output_dir}" not in text:
            text += " --out {output_dir}"
        return self._escape_format_literals(text)

    def _escape_format_literals(self, command: str) -> str:
        placeholders = ["{code_dir}", "{input_dir}", "{input_file}", "{input_hmm}", "{input_fasta}", "{input_query_fasta}", "{input_reference_fasta}", "{input_pep_fasta}", "{input_cds_fasta}", "{input_id_file}", "{output_dir}", "{work_dir}"]
        protected: dict[str, str] = {}
        text = command
        for index, placeholder in enumerate(placeholders):
            token = f"@@EXTENSION_PLACEHOLDER_{index}@@"
            protected[token] = placeholder
            text = text.replace(placeholder, token)
        for match in re.finditer(r"\{param_[A-Za-z_][A-Za-z0-9_]*\}", text):
            token = f"@@EXTENSION_PARAM_PLACEHOLDER_{len(protected)}@@"
            protected[token] = match.group(0)
            text = text.replace(match.group(0), token)
        text = text.replace("{", "{{").replace("}", "}}")
        for token, placeholder in protected.items():
            text = text.replace(token, placeholder)
        return text

    def _rewrite_output_arguments(self, command: str) -> str:
        output_flags = r"--domtblout|--tblout|--pfamtblout|--aliscoresout|--out(?:put)?|-o|-A"
        output_names: set[str] = set()

        def output_value(value: str) -> str:
            stripped = value.strip("\"'")
            name = Path(stripped.rstrip("/")).name
            if stripped.endswith("/") or name.lower() in {"out", "output", "outputs", "result", "results"}:
                return "{output_dir}"
            if name:
                output_names.add(name)
            return "{output_dir}/" + (name or "output")

        def replace_flag(match: re.Match[str]) -> str:
            return f"{match.group(1)} {output_value(match.group(2))}"

        def replace_redirect(match: re.Match[str]) -> str:
            return f"{match.group(1)} {output_value(match.group(2))}"

        text = re.sub(rf"({output_flags})\s+([^\s]+)", replace_flag, command)
        text = re.sub(r"(>>|>)\s*([^\s|;&]+)", replace_redirect, text)
        for name in sorted(output_names, key=len, reverse=True):
            text = re.sub(rf"(?<![\w./-]){re.escape(name)}(?![\w./-])", "{output_dir}/" + name, text)
        return text

    def _execute(
        self,
        extension: dict[str, Any],
        *,
        run_dir: Path,
        input_dir: Path,
        parameters: dict[str, Any] | None = None,
        validation: bool,
        converted_code_dir: Path | None = None,
    ) -> dict[str, Any]:
        ext_root = self._extension_root(extension["extension_id"])
        converted = converted_code_dir or (ext_root / "converted_code")
        merged_parameters = _merged_parameter_values(extension, parameters or {})
        try:
            execution = execute_extension_command(
                extension,
                run_dir=run_dir,
                input_dir=input_dir,
                converted_code_dir=converted,
                parameters=merged_parameters,
            )
        except ExtensionExecutionError as exc:
            raise ExtensionStageError(str(exc)) from exc

        output_dir = run_dir / "outputs"
        stdout_path = execution["logs"]["stdout_path"]
        stderr_path = execution["logs"]["stderr_path"]
        output_prefix = extension_output_prefix(extension, merged_parameters)
        if output_prefix:
            normalize_output_names(output_dir, output_prefix)
        outputs = scan_outputs(output_dir, run_dir)
        misplaced_outputs = scan_misplaced_outputs(run_dir, output_dir)
        binding_report = merged_parameters.get("binding_report") or merged_parameters.get("config_input_bindings") or {}
        run = {
            "schema_version": "extension_run_v1",
            "spec_version": extension.get("schema_version", "extension_stage_v1"),
            "extension_id": extension["extension_id"],
            "extension_name": extension.get("name", extension["extension_id"]),
            "display_name": extension.get("display_name", extension["extension_id"]),
            "analysis_type": extension.get("analysis_type", ""),
            "tool_family": extension.get("tool_family", ""),
            "archetype_id": extension.get("archetype_id") or infer_archetype_id(extension),
            "template_gate_status": template_gate_publish_status(extension),
            "publish_gate_version": TEMPLATE_GATE_VERSION,
            "run_id": run_dir.name,
            "status": execution["status"],
            "validation": validation,
            "workspace": str(run_dir),
            "command_template": (extension.get("entrypoint") or {}).get("command_template", ""),
            "command": execution["command"],
            "rendered_command": execution["command"],
            "returncode": execution["returncode"],
            "runtime": execution["runtime"],
            "inputs": {"input_dir": str(input_dir)},
            "binding_report": binding_report,
            "intent_slots": merged_parameters.get("intent_slots", {}),
            "parameters": merged_parameters,
            "input_file_checksums": inventory_files(input_dir, relative_to=run_dir),
            "converted_code_checksums": inventory_files(run_dir / "code", relative_to=run_dir),
            "outputs": outputs,
            "output_file_checksums": inventory_files(output_dir, relative_to=run_dir),
            "misplaced_outputs": misplaced_outputs,
            "output_prefix": output_prefix,
            "prefix_consistency": _prefix_consistency_report(execution["command"], output_prefix, outputs),
            "logs": {
                "stdout": _rel(stdout_path, run_dir),
                "stderr": _rel(stderr_path, run_dir),
            },
            "error": execution["error"],
            "started_at": execution["started_at"],
            "ended_at": execution["ended_at"],
            "elapsed_sec": execution["elapsed_sec"],
        }
        if not validation:
            try:
                job_id = run_dir.parents[2].name
                run["job_id"] = job_id
            except Exception:
                pass
        if run["status"] == "success":
            contract_errors = validate_output_contract(run, extension.get("output_contract") or [])
            if contract_errors:
                run["status"] = "failed"
                run["error"] = "; ".join(contract_errors)
        if run["status"] == "success" and run_has_empty_outputs(run):
            empty_names = [str(out.get("name") or out.get("path") or "output") for out in run.get("outputs") or [] if bool(out.get("is_empty")) or int(out.get("size") or 0) == 0]
            run["status"] = "failed"
            run["error"] = "Extension produced empty output file(s): " + ", ".join(empty_names)
        run["failure_classification"] = classify_run(run)
        run["diagnosis_report"] = diagnose_extension_run(run, extension, run_dir=run_dir)
        if run.get("diagnosis_report", {}).get("user_message") and run["status"] != "success":
            run["error"] = run["diagnosis_report"]["user_message"]
        if run["status"] != "success" or run_has_empty_outputs(run):
            append_lesson(self.store_root, extension, {
                "phase": "validation" if validation else "run",
                "lesson_type": run["failure_classification"].get("code") or ("execution_failed" if run["status"] != "success" else "empty_output"),
                "status": run["status"],
                "command": run["command"],
                "error": run["error"],
                "failure_classification": run["failure_classification"],
                "outputs": outputs,
                "parameters": merged_parameters,
                "suggestion": execution_lesson_suggestion(extension, run),
            })
        write_run_record(run_dir, run)
        return run

    def _materialize_inputs(self, job_id: str, input_paths: list[str], input_dir: Path) -> None:
        allowed_roots = [
            (self.input_root / _safe_path_token(job_id)).resolve(),
            (self.workspace_root / _safe_path_token(job_id)).resolve(),
        ]
        for raw in input_paths:
            if not raw:
                continue
            path = Path(raw)
            if not path.is_absolute():
                bases = [self.input_root / job_id, self.workspace_root / job_id, self.project_root]
                candidates = []
                for base in bases:
                    candidates.extend(input_path_equivalents(base / path))
                path = next((p for p in candidates if p.exists()), candidates[0])
            resolved = path.resolve()
            if not any(_is_relative_to(resolved, root) for root in allowed_roots):
                raise ExtensionStageError(f"Input path is outside current job: {raw}")
            if not resolved.exists():
                raise ExtensionStageError(f"Input path does not exist: {raw}")
            if resolved.is_dir():
                self._copy_tree_contents(resolved, input_dir / resolved.name)
            else:
                shutil.copy2(resolved, input_dir / resolved.name)

    def _copy_tree_contents(self, source: Path, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        if not source.exists():
            return
        for item in source.iterdir():
            dest = target / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
            elif item.is_file():
                shutil.copy2(item, dest)





def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _entrypoint_with_templates(
    *,
    original_command: str,
    command_template: str,
    semantic_command_template: str = "",
    wrapper_command_template: str = "",
) -> dict[str, str]:
    entrypoint = {
        "original_command": original_command,
        "command_template": command_template,
    }
    if semantic_command_template:
        entrypoint["semantic_command_template"] = semantic_command_template
    if wrapper_command_template:
        entrypoint["wrapper_command_template"] = wrapper_command_template
    return entrypoint


def _extension_with_repair_spec(extension: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    updated = dict(extension)
    entrypoint = dict(updated.get("entrypoint") or {})
    entrypoint["original_command"] = spec.get("original_command") or entrypoint.get("original_command", "")
    entrypoint["command_template"] = spec.get("command_template") or entrypoint.get("command_template", "")
    spec_entrypoint = spec.get("entrypoint") or {}
    semantic_is_explicit = "semantic_command_template" in spec or "semantic_command_template" in spec_entrypoint
    if semantic_is_explicit:
        semantic_template = str(spec.get("semantic_command_template") if "semantic_command_template" in spec else spec_entrypoint.get("semantic_command_template") or "").strip()
    else:
        semantic_template = str(entrypoint.get("semantic_command_template", "")).strip()
    wrapper_template = str(spec.get("wrapper_command_template") or (spec.get("entrypoint") or {}).get("wrapper_command_template") or entrypoint.get("command_template", "")).strip()
    if semantic_template:
        entrypoint["semantic_command_template"] = semantic_template
        updated["semantic_command_template"] = semantic_template
    elif semantic_is_explicit:
        entrypoint.pop("semantic_command_template", None)
        updated["semantic_command_template"] = ""
    if wrapper_template:
        entrypoint["wrapper_command_template"] = wrapper_template
        updated["wrapper_command_template"] = wrapper_template
    updated["command_template"] = entrypoint["command_template"]
    updated["entrypoint"] = entrypoint
    updated["schema_version"] = spec.get("schema_version", updated.get("schema_version", "extension_stage_v1"))
    updated["analysis_type"] = spec.get("analysis_type", updated.get("analysis_type", ""))
    updated["tool_family"] = spec.get("tool_family", updated.get("tool_family", ""))
    updated["template_type"] = spec.get("template_type", updated.get("template_type", ""))
    updated["archetype_id"] = spec.get("archetype_id") or updated.get("archetype_id") or infer_archetype_id(updated)
    updated["template_gate"] = spec.get("template_gate") or {}
    updated["converter"] = spec.get("converter", updated.get("converter", {}))
    updated["input_roles"] = spec.get("input_roles") or updated.get("input_roles", {})
    updated["input_bindings"] = spec.get("input_bindings") or updated.get("input_bindings", {})
    updated["requires_user_confirmation"] = bool(spec.get("requires_user_confirmation", updated.get("requires_user_confirmation", False)))
    if spec.get("notes"):
        updated["conversion_notes"] = spec.get("notes", "")
    updated["inputs"] = spec.get("inputs") or updated.get("inputs", {})
    updated["parameters"] = spec.get("parameters") or {}
    updated["confirmed_parameters"] = spec.get("parameters") or updated.get("confirmed_parameters", {})
    updated["outputs"] = spec.get("outputs") or updated.get("outputs", {})
    updated["comparator_plan"] = spec.get("comparator_plan") or updated.get("comparator_plan", {})
    if spec.get("output_contract"):
        updated["output_contract"] = spec["output_contract"]
    if spec.get("generated_code_artifacts"):
        updated["generated_code_artifacts"] = spec["generated_code_artifacts"]
    if spec.get("requirements"):
        updated["requirements"] = _merge_requirements(updated.get("requirements") or {}, spec["requirements"])
    return updated


def _contract_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "schema_version",
        "analysis_type",
        "tool_family",
        "archetype_id",
        "template_gate",
        "input_roles",
        "input_bindings",
        "parameters",
        "output_contract",
        "comparator_plan",
        "generated_code_artifacts",
        "requirements",
        "runtime",
    ]
    changes = []
    for key in keys:
        if before.get(key) != after.get(key):
            changes.append({"field": key, "before": before.get(key), "after": after.get(key)})
    before_template = str((before.get("entrypoint") or {}).get("command_template") or "")
    after_template = str((after.get("entrypoint") or {}).get("command_template") or "")
    if before_template != after_template:
        changes.append({"field": "entrypoint.command_template", "before": before_template, "after": after_template})
    before_original = str((before.get("entrypoint") or {}).get("original_command") or before.get("original_command") or "")
    after_original = str((after.get("entrypoint") or {}).get("original_command") or after.get("original_command") or "")
    if before_original != after_original:
        changes.append({"field": "entrypoint.original_command", "before": before_original, "after": after_original})
    return {"schema_version": "extension_repair_contract_diff_v1", "changed_fields": changes, "changed_field_count": len(changes)}

def _infer_command_from_code(paths: list[Path], raw_code: Path) -> str:
    if not paths:
        return ""
    scripts = sorted(paths, key=lambda p: (p.suffix.lower() not in {".sh", ".py", ".r", ".R"}, p.name.lower()))
    main = scripts[0]
    rel = main.relative_to(raw_code).as_posix()
    suffix = main.suffix.lower()
    if suffix == ".sh":
        try:
            lines = []
            for raw in main.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or line.startswith("##"):
                    continue
                lines.append(line)
            if lines:
                return " && ".join(lines)
        except Exception:
            pass
        return f"bash {{code_dir}}/{rel}"
    if suffix == ".py":
        return f"python {{code_dir}}/{rel} --input {{input_dir}} --out {{output_dir}}"
    if suffix in {".r", ".R"}:
        return f"Rscript {{code_dir}}/{rel} --input {{input_dir}} --out {{output_dir}}"
    return f"{{code_dir}}/{rel}"


def _save_uploads(root: Path, uploads: list[tuple[str, bytes]]) -> list[Path]:
    saved = []
    for filename, content in uploads:
        name = _safe_upload_name(filename)
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        if path.suffix.lower() in {".sh", ".bash", ".command"}:
            path.chmod(0o755)
        saved.append(path)
    return saved


def _safe_upload_name(filename: str) -> str:
    name = Path(filename or "uploaded_file").name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "uploaded_file"


def _safe_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip().lower())
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:64]


def _safe_path_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or ""))
    return token.strip("._") or "unknown"


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _now() -> str:
    return datetime.now().isoformat()


def _infer_main_code(files: list[Path], command: str) -> Path:
    for path in files:
        if path.name in command:
            return path
    code_files = [p for p in files if p.suffix in CODE_SUFFIXES]
    return code_files[0] if code_files else files[0]


def _infer_language(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix == ".r":
        return "r"
    if suffix == ".sh":
        return "shell"
    return suffix.lstrip(".") or "unknown"


def _merge_requirements(detected: dict[str, list[str]], api_requirements: dict[str, Any]) -> dict[str, list[str]]:
    merged = {
        "python": set(detected.get("python") or []),
        "r": set(detected.get("r") or []),
        "system": set(detected.get("system") or []),
    }
    if isinstance(api_requirements, dict):
        for key in ("python", "r", "system"):
            values = api_requirements.get(key) or []
            if isinstance(values, str):
                values = [values]
            for value in values:
                text = str(value or "").strip()
                if text:
                    merged.setdefault(key, set()).add(text)
    return {key: sorted(values) for key, values in merged.items()}


def _detect_requirements(files: list[Path]) -> dict[str, list[str]]:
    py: set[str] = set()
    r: set[str] = set()
    system: set[str] = set()
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if path.suffix.lower() == ".py":
            for match in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", text, re.M):
                mod = match.group(1)
                if mod not in {"os", "sys", "re", "json", "csv", "argparse", "pathlib", "math", "statistics", "subprocess", "time", "datetime"}:
                    py.add(mod)
        elif path.suffix.lower() == ".r":
            for match in re.finditer(r"\b(?:library|require)\(([^)]+)\)", text):
                pkg = match.group(1).strip().strip("'\"")
                if pkg:
                    r.add(pkg)
        elif path.suffix.lower() == ".sh":
            system.update(_detect_shell_commands(text))
    return {"python": sorted(py), "r": sorted(r), "system": sorted(system)}


def _detect_shell_commands(text: str) -> set[str]:
    commands: set[str] = set()
    heredoc_until = ""
    for raw in text.splitlines():
        line = raw.strip()
        if heredoc_until:
            if line == heredoc_until:
                heredoc_until = ""
            continue
        if not line or line.startswith("#"):
            continue
        heredoc_match = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
        if heredoc_match:
            heredoc_until = heredoc_match.group(1)
        try:
            parts = shlex.split(line, comments=True)
        except Exception:
            continue
        command = _first_shell_command(parts)
        if command:
            commands.add(command)
    return commands


def _first_shell_command(parts: list[str]) -> str:
    for part in parts:
        token = str(part or "").strip()
        if not token:
            continue
        if token in {"&&", "||", "|", ";", "(", ")"}:
            continue
        if token == ";;" or token.endswith(")"):
            return ""
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
            continue
        command = Path(token).name if "/" in token else token
        if command in SHELL_REQUIREMENT_IGNORES:
            return ""
        if command.startswith("-") or "=" in command or command.startswith("${"):
            return ""
        return command
    return ""


def _file_inventory(root: Path, rel_root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        files.append({
            "name": path.name,
            "path": _rel(path, rel_root),
            "type": classify_file(path),
            "size": path.stat().st_size,
            "mtime": path.stat().st_mtime,
            "preview": preview_file(path),
        })
    return files



def _mark_primary_outputs(outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = [dict(item) for item in outputs]
    primary_set = False
    for item in items:
        item.setdefault("is_empty", int(item.get("size") or 0) == 0)
        item.setdefault("description", "")
        item.setdefault("preview", "")
        if item.get("primary"):
            primary_set = True
    if not primary_set:
        for item in items:
            if not item.get("is_empty"):
                item["primary"] = True
                primary_set = True
                break
    if not primary_set and items:
        items[0]["primary"] = True
    for item in items:
        item.setdefault("primary", False)
    return items


def _merge_output_contract_with_confirmed_outputs(extension: dict[str, Any], outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contract = [dict(item) for item in (extension.get("output_contract") or []) if isinstance(item, dict)]
    archetype = ARCHETYPE_BY_ID.get(infer_archetype_id(extension))
    if not archetype or not outputs:
        return contract

    output_names = [_output_item_name(item) for item in outputs if isinstance(item, dict)]
    output_names = [name for name in output_names if name]
    existing_roles = {str(item.get("semantic_role") or "") for item in contract}
    existing_patterns = [str(item.get("name_pattern") or "") for item in contract]

    for required in archetype.output_contract:
        if not isinstance(required, dict):
            continue
        semantic_role = str(required.get("semantic_role") or "")
        expected_pattern = str(required.get("name_pattern") or "")
        if semantic_role and semantic_role in existing_roles:
            continue
        if expected_pattern and any(_contract_pattern_compatible(expected_pattern, pattern) for pattern in existing_patterns):
            continue
        matched_name = _matched_output_name(expected_pattern, output_names)
        if not matched_name:
            continue
        merged = dict(required)
        merged["name_pattern"] = matched_name
        merged.setdefault("required", True)
        merged.setdefault("allow_empty", False)
        merged.setdefault("required_for_golden", bool(merged.get("required", True)))
        contract.append(merged)
        if semantic_role:
            existing_roles.add(semantic_role)
        existing_patterns.append(matched_name)
    return contract


def _output_item_name(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "").strip()
    if name:
        return name
    path = str(item.get("path") or item.get("relative_path") or "").strip()
    return Path(path).name if path else ""


def _matched_output_name(pattern: str, names: list[str]) -> str:
    if not pattern:
        return ""
    import fnmatch

    for name in names:
        if fnmatch.fnmatch(name, pattern) or _contract_pattern_compatible(pattern, name):
            return name
    return ""


def _contract_pattern_compatible(expected: str, actual: str) -> bool:
    if not expected or not actual:
        return False
    if actual == expected or actual == "*":
        return True
    expected_suffix = expected.lstrip("*")
    actual_suffix = actual.lstrip("*")
    return bool(expected_suffix and actual.endswith(expected_suffix)) or bool(actual_suffix and expected.endswith(actual_suffix))


def _normalize_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for raw_name, raw_value in (parameters or {}).items():
        name = _safe_parameter_name(raw_name)
        if not name:
            continue
        if isinstance(raw_value, dict):
            item = dict(raw_value)
        else:
            item = {"default": raw_value}
        if "default" not in item and "value" in item:
            item["default"] = item.get("value")
        default = item.get("default", "")
        item.setdefault("type", _infer_parameter_type(default))
        item.setdefault("editable", True)
        item.setdefault("description", "")
        item.setdefault("advanced", False)
        item.setdefault("source", "inferred")
        normalized[name] = item
    return normalized


def _infer_parameter_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "float"
    text = str(value)
    if re.fullmatch(r"[-+]?\d+", text):
        return "integer"
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", text):
        return "float"
    return "string"


def _safe_parameter_name(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip())
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return ""
    if text[0].isdigit():
        text = "p_" + text
    if text.startswith("param_"):
        text = text[6:]
    return text


def _merged_parameter_values(extension: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    params = extension.get("confirmed_parameters") or extension.get("parameters") or {}
    for raw_name, spec in params.items():
        name = _safe_parameter_name(raw_name)
        if isinstance(spec, dict):
            merged[name] = spec.get("current_value", spec.get("default", spec.get("value", "")))
        else:
            merged[name] = spec
    for raw_name, value in (overrides or {}).items():
        name = _safe_parameter_name(raw_name)
        if name:
            merged[name] = value
    return merged



def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)



def _prefix_consistency_report(command: str, output_prefix: str, outputs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    report = {"checked": bool(output_prefix), "output_prefix": output_prefix, "issues": []}
    if not output_prefix:
        return report
    command_text = str(command or "")
    output_names = [str(out.get("name") or "") for out in (outputs or [])]
    if "--out-prefix" in command_text:
        match = re.search(r"--out-prefix\s+([^\s;&|]+)", command_text)
        if match:
            rendered = match.group(1).strip("'\"")
            report["command_out_prefix"] = rendered
            if rendered and not any(rendered in name for name in output_names):
                report["issues"].append(f"command --out-prefix {rendered} is not represented in output filenames")
    if output_prefix and output_names and not all(name.startswith(output_prefix + ".") for name in output_names):
        report["issues"].append("some output filenames do not start with the normalized extension output prefix")
    return report

def _repair_error_run(root: Path, command: str, exc: BaseException) -> dict[str, Any]:
    return {
        "status": "failed",
        "command": command,
        "returncode": None,
        "error": str(exc),
        "workspace": str(root),
        "outputs": [],
        "logs": {},
    }


def _tail(path: Path, limit: int = 4000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    return text[-limit:]


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False



def _extension_is_runnable_candidate(extension: dict[str, Any]) -> bool:
    if extension.get("status") != "published":
        return False
    if extension.get("needs_migration"):
        return False
    if not (extension.get("input_roles") or extension.get("input_bindings")) or not extension.get("output_contract"):
        return False
    converter_type = (extension.get("converter") or {}).get("type")
    if converter_type in {"rule", "local_rule"}:
        return False
    try:
        checks = publish_checks(extension, _merged_parameter_values(extension, {}))
    except Exception:
        return False
    return bool(checks.get("passed"))

def _public_extension(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "extension_id": data.get("extension_id"),
        "name": data.get("name"),
        "display_name": data.get("display_name"),
        "description": data.get("description", ""),
        "status": data.get("status"),
        "runtime": data.get("runtime", {}),
        "inputs": data.get("inputs", {}),
        "parameters": data.get("confirmed_parameters") or data.get("parameters", {}),
        "requirements": data.get("requirements", {}),
        "analysis_type": data.get("analysis_type") or infer_analysis_type(data),
        "tool_family": data.get("tool_family") or infer_tool_family(data),
        "archetype_id": data.get("archetype_id") or infer_archetype_id(data),
        "input_roles": data.get("input_roles", {}),
        "output_contract": data.get("output_contract", []),
        "comparator_plan": data.get("comparator_plan", {}),
        "golden_status": (data.get("golden_comparison") or {}).get("status", ""),
        "template_gate_status": template_gate_publish_status(data),
        "template_gate_errors": list(((data.get("template_gate") or {}).get("errors") or [])),
        "template_eligible": bool((data.get("publish_checks") or {}).get("template_eligible", template_gate_publish_status(data) == "passed")),
        "published_runnable": _extension_is_runnable_candidate(data),
        "publish_checks": {
            "passed": bool((data.get("publish_checks") or {}).get("passed", False)),
            "errors": list(((data.get("publish_checks") or {}).get("errors") or [])),
            "template_gate_status": (data.get("publish_checks") or {}).get("template_gate_status", template_gate_publish_status(data)),
            "template_eligible": bool((data.get("publish_checks") or {}).get("template_eligible", template_gate_publish_status(data) == "passed")),
            "published_runnable": bool((data.get("publish_checks") or {}).get("published_runnable", False)),
        },
        "build_status": data.get("build_status", data.get("status", "")),
        "uploaded_inputs": data.get("uploaded_inputs", []),
        "outputs_detected": data.get("outputs_detected", []),
        "outputs_confirmed": data.get("outputs_confirmed", []),
        "nl_triggers": data.get("nl_triggers", []),
        "created_at": data.get("created_at", ""),
        "published_at": data.get("published_at", ""),
    }




def _minimal_yaml(data: dict[str, Any]) -> str:
    lines = [
        f"name: {data.get('name', '')}",
        f"display_name: {data.get('display_name', '')}",
        f"extension_id: {data.get('extension_id', '')}",
        f"status: {data.get('status', '')}",
        "runtime:",
        f"  type: {(data.get('runtime') or {}).get('type', 'conda')}",
        f"  env: {(data.get('runtime') or {}).get('env', 'test4py311')}",
        f"  language: {(data.get('runtime') or {}).get('language', '')}",
        "entrypoint:",
        f"  command_template: {json.dumps(((data.get('entrypoint') or {}).get('command_template', '')), ensure_ascii=False)}",
    ]
    return "\n".join(lines) + "\n"
