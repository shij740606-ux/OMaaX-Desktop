"""Persistent Phase-B orchestration for the lightweight extension core only."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable, Iterator
from uuid import uuid4

from .confirmation import apply_confirmation_action, create_confirmation, is_candidate_accepted
from .converter import build_execution_facts
from .deepseek_semantics import generate_semantic_candidate
from .evidence import run_raw_baseline
from .process_control import ProcessIdentity, stop_process_group
from .runtime_binding import (
    build_runtime_binding_contract,
    render_runtime_package,
    replay_runtime_binding,
    resolve_runtime_bindings,
)
from .semantic_repair import repair_semantic_candidate


SemanticGenerator = Callable[[dict[str, Any], Path], tuple[dict[str, Any], dict[str, Any]]]
PatchGenerator = Callable[[dict[str, Any], int], tuple[dict[str, Any], dict[str, Any]]]


class LifecycleStateError(ValueError):
    """The requested lifecycle action is not allowed in the current state."""


def _copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        raise ValueError(f"destination already exists: {destination}")
    shutil.copytree(source, destination, symlinks=True)


def _conda_activated_env(runtime_env: str) -> dict[str, str]:
    """镜像标准形态: 无 conda 本体 —— 容器启动环境即该镜像环境的激活态
    (构建期 conda-unpack 已固化前缀; activate.d 效果由镜像 PATH 提供)。

    返回空 dict 表示无需额外激活处理。
    """
    return {}


class LightweightExtensionService:
    """A small state machine that delegates all conversion/runtime facts to Phase A."""

    def __init__(
        self,
        store_root: Path,
        *,
        semantic_generator: SemanticGenerator | None = None,
        patch_generator: PatchGenerator | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        self.store_root = Path(store_root)
        self.extensions_root = self.store_root / "extensions"
        self.semantic_generator = semantic_generator or self._deepseek_candidate
        self.patch_generator = patch_generator
        self.environment = environment

    def extension_dir(self, extension_id: str) -> Path:
        if (
            not extension_id
            or "/" in extension_id
            or "\\" in extension_id
            or extension_id in {".", ".."}
        ):
            raise ValueError("extension_id must be one safe path component")
        return self.extensions_root / extension_id

    def _record_path(self, extension_id: str) -> Path:
        return self.extension_dir(extension_id) / "extension.json"

    @contextmanager
    def _lock(self, extension_id: str) -> Iterator[None]:
        locks = self.store_root / ".locks"
        locks.mkdir(parents=True, exist_ok=True)
        with (locks / f"{extension_id}.lock").open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read_record(self, extension_id: str) -> dict[str, Any]:
        try:
            return json.loads(self._record_path(extension_id).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"extension does not exist: {extension_id}") from exc

    def _write_record(self, extension_id: str, record: dict[str, Any]) -> None:
        path = self._record_path(extension_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".extension-", delete=False
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def get(self, extension_id: str) -> dict[str, Any]:
        return self._read_record(extension_id)

    def _make_env(self, runtime_env: str) -> dict[str, str]:
        """Build a process env for the selected runtime environment.

        镜像标准形态: 每镜像=单一 conda 环境, 容器 PATH 已是该环境 bin,
        故此处仅透传调用方环境并记录运行时环境标识; 不再拼接宿主 conda 前缀。
        """

        env = dict(self.environment or {})
        env["PATH"] = env.get("PATH") or os.environ.get("PATH", "")
        env["CONDA_PREFIX"] = str(Path(sys.executable).resolve().parent.parent)
        env["CONDA_DEFAULT_ENV"] = runtime_env
        # 镜像内无 conda 本体; activate.d 效果由镜像构建期固化(解包即激活态路径)。
        return env

    def _deepseek_candidate(self, facts: dict[str, Any], raw_code: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        return generate_semantic_candidate(
            execution_facts=facts, raw_code=raw_code, environ=self.environment
        )

    @staticmethod
    def _state(record: dict[str, Any], *allowed: str) -> None:
        if record.get("status") not in allowed:
            raise LifecycleStateError(
                f"action requires status in {', '.join(allowed)}, got {record.get('status')}"
            )

    @staticmethod
    def _response(
        record: dict[str, Any], *, token: str, action: str, state: str, run_id: str | None = None
    ) -> dict[str, Any]:
        result = {
            "extension_id": record["extension_id"],
            "operation_token": token,
            "action": action,
            "state": state,
            "status": record["status"],
        }
        if run_id is not None:
            result["run_id"] = run_id
        return result

    def _existing_operation(
        self, record: dict[str, Any], *, token: str, action: str
    ) -> dict[str, Any] | None:
        existing = record.setdefault("operations", {}).get(token)
        if existing is None:
            return None
        if existing.get("action") != action:
            raise ValueError("operation_token is already bound to another action")
        return deepcopy(existing["response"])

    def _short_operation(
        self,
        extension_id: str,
        *,
        token: str,
        action: str,
        mutate: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        if not token.strip():
            raise ValueError("operation_token is required")
        with self._lock(extension_id):
            record = self._read_record(extension_id)
            previous = self._existing_operation(record, token=token, action=action)
            if previous is not None:
                return previous
            mutate(record)
            response = self._response(record, token=token, action=action, state="completed")
            record["operations"][token] = {
                "action": action,
                "state": "completed",
                "response": response,
            }
            self._write_record(extension_id, record)
            return deepcopy(response)

    def _begin_long_operation(
        self,
        extension_id: str,
        *,
        token: str,
        action: str,
        running_status: str,
        allowed: tuple[str, ...],
        run_id: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if not token.strip():
            raise ValueError("operation_token is required")
        with self._lock(extension_id):
            record = self._read_record(extension_id)
            previous = self._existing_operation(record, token=token, action=action)
            if previous is not None:
                return None, previous
            self._state(record, *allowed)
            record["status"] = running_status
            record["active_process"] = None
            response = self._response(
                record, token=token, action=action, state="running", run_id=run_id
            )
            record["operations"][token] = {
                "action": action,
                "state": "running",
                "run_id": run_id,
                "response": response,
            }
            self._write_record(extension_id, record)
            return record, deepcopy(response)

    def _mark_process(
        self,
        extension_id: str,
        *,
        token: str,
        phase: str,
        identity: ProcessIdentity,
    ) -> None:
        stop_after_recording = False
        with self._lock(extension_id):
            record = self._read_record(extension_id)
            operation = record.get("operations", {}).get(token)
            if operation is None or operation.get("state") != "running":
                return
            record["active_process"] = {
                "operation_token": token,
                "phase": phase,
                "identity": dict(identity),
            }
            stop_after_recording = operation.get("stop_requested") is True
            self._write_record(extension_id, record)
        if not stop_after_recording:
            return
        stopped = stop_process_group(identity, grace_seconds=1.0)
        with self._lock(extension_id):
            record = self._read_record(extension_id)
            active = record.get("active_process")
            if isinstance(active, dict) and active.get("operation_token") == token:
                active["stop_result"] = stopped
                self._write_record(extension_id, record)

    def _finish_long_operation(
        self,
        extension_id: str,
        *,
        token: str,
        action: str,
        run_id: str,
        mutate: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        with self._lock(extension_id):
            record = self._read_record(extension_id)
            operation = record.get("operations", {}).get(token)
            if operation is None or operation.get("action") != action:
                raise LifecycleStateError("operation record was lost before completion")
            mutate(record)
            active = record.get("active_process")
            if isinstance(active, dict) and active.get("operation_token") == token:
                record["active_process"] = None
            response = self._response(
                record, token=token, action=action, state="completed", run_id=run_id
            )
            operation.update({"state": "completed", "response": response})
            self._write_record(extension_id, record)
            return deepcopy(response)

    @staticmethod
    def _error(phase: str, error: BaseException | str) -> dict[str, Any]:
        result: dict[str, Any] = {"phase": phase, "message": str(error)}
        metadata = getattr(error, "metadata", None)
        if isinstance(metadata, dict) and metadata:
            result["metadata"] = deepcopy(metadata)
        return result

    def _build_candidate(
        self,
        record: dict[str, Any],
        evidence: dict[str, Any],
        *,
        facts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        root = self.extension_dir(record["extension_id"])
        facts = facts or build_execution_facts(evidence)
        candidate, metadata = self.semantic_generator(facts, root / "raw_code")
        return {
            "baseline": evidence,
            "execution_facts": facts,
            "semantic_candidate": candidate,
            "semantic_metadata": metadata,
            "semantic_repair_history": [],
            "confirmation": create_confirmation(execution_facts=facts, candidate=candidate),
            "runtime_binding_contract": build_runtime_binding_contract(
                facts, candidate, root / "raw_code"
            ),
            "validation_runs": [],
            "reviewed_validation_run_id": None,
            "published_validation_run_id": None,
            "error": None,
            "status": "confirmation_pending",
        }

    def _run_baseline_operation(
        self,
        extension_id: str,
        *,
        token: str,
        action: str,
        allowed: tuple[str, ...],
        reserved: bool = False,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        run_id = run_id or f"baseline_{uuid4().hex}"
        if reserved:
            record = self.get(extension_id)
        else:
            record, existing = self._begin_long_operation(
                extension_id,
                token=token,
                action=action,
                running_status="baseline_running",
                allowed=allowed,
                run_id=run_id,
            )
            if record is None:
                return existing
        root = self.extension_dir(extension_id)
        evidence: dict[str, Any] | None = None
        facts: dict[str, Any] | None = None
        build: dict[str, Any] | None = None
        failure: tuple[str, BaseException | str] | None = None
        try:
            runtime_env = str(record.get("runtime_env") or "test4py311")
            evidence = run_raw_baseline(
                command=record["original_command"],
                raw_code=root / "raw_code",
                raw_data=root / "raw_test_data",
                run_dir=root / "baseline" / run_id,
                env=self._make_env(runtime_env),
                on_process_start=lambda identity: self._mark_process(
                    extension_id, token=token, phase="baseline", identity=identity
                ),
            )
            if not evidence["success"]:
                failure = ("baseline", f"original command returned {evidence['returncode']}")
            else:
                facts = build_execution_facts(evidence)
                build = self._build_candidate(record, evidence, facts=facts)
        except BaseException as exc:
            failure = ("semantic_candidate" if evidence is not None else "baseline", exc)

        def finish(current: dict[str, Any]) -> None:
            if build is not None:
                current.update(build)
                return
            if evidence is not None:
                current["baseline"] = evidence
            if facts is not None:
                current["execution_facts"] = facts
            phase, detail = failure or ("baseline", "unknown build failure")
            current["error"] = self._error(phase, detail)
            current["status"] = "baseline_failed" if phase == "baseline" else "build_failed"

        return self._finish_long_operation(
            extension_id, token=token, action=action, run_id=run_id, mutate=finish
        )

    def upload(
        self,
        *,
        extension_id: str,
        raw_code: Path,
        raw_data: Path,
        original_command: str,
        runtime_env: str = "test4py311",
        operation_token: str,
    ) -> dict[str, Any]:
        raw_code, raw_data = Path(raw_code), Path(raw_data)
        if not isinstance(operation_token, str) or not operation_token.strip():
            raise ValueError("operation_token is required")
        if not raw_code.is_dir() or not raw_data.is_dir():
            raise ValueError("raw_code and raw_data must be directories")
        if not original_command.strip():
            raise ValueError("original_command is required")
        root = self.extension_dir(extension_id)
        run_id = f"baseline_{uuid4().hex}"
        with self._lock(extension_id):
            if self._record_path(extension_id).exists():
                record = self._read_record(extension_id)
                previous = self._existing_operation(
                    record, token=operation_token, action="upload"
                )
                if previous is None:
                    raise LifecycleStateError("extension already exists")
                return previous
            root.mkdir(parents=True, exist_ok=False)
            _copy_tree(raw_code, root / "raw_code")
            _copy_tree(raw_data, root / "raw_test_data")
            self._write_record(
                extension_id,
                {
                    "schema_version": "lightweight-extension-service/v1",
                    "extension_id": extension_id,
                    "original_command": original_command,
                    "runtime_env": runtime_env,
                    "status": "baseline_running",
                    "operations": {
                        operation_token: {
                            "action": "upload",
                            "state": "running",
                            "run_id": run_id,
                            "response": {
                                "extension_id": extension_id,
                                "operation_token": operation_token,
                                "action": "upload",
                                "state": "running",
                                "status": "baseline_running",
                                "run_id": run_id,
                            },
                        }
                    },
                    "active_process": None,
                    "error": None,
                },
            )
        return self._run_baseline_operation(
            extension_id,
            token=operation_token,
            action="upload",
            allowed=("uploaded",),
            reserved=True,
            run_id=run_id,
        )

    def rebuild(self, extension_id: str, *, operation_token: str) -> dict[str, Any]:
        return self._run_baseline_operation(
            extension_id,
            token=operation_token,
            action="rebuild",
            allowed=("uploaded", "rebuild_required", "baseline_failed", "build_failed", "semantic_repair_failed", "validation_failed"),
        )

    def retry_semantic(self, extension_id: str, *, operation_token: str) -> dict[str, Any]:
        if self.get(extension_id).get("status") == "confirmation_pending":
            return self._short_operation(
                extension_id,
                token=operation_token,
                action="retry_semantic",
                mutate=lambda record: self._state(record, "confirmation_pending"),
            )
        run_id = f"semantic_{uuid4().hex}"
        record, existing = self._begin_long_operation(
            extension_id,
            token=operation_token,
            action="retry_semantic",
            running_status="semantic_candidate_running",
            allowed=("build_failed",),
            run_id=run_id,
        )
        if record is None:
            return existing
        evidence = record.get("baseline")
        facts = record.get("execution_facts")
        build: dict[str, Any] | None = None
        failure: BaseException | None = None
        if isinstance(evidence, dict) and not isinstance(facts, dict):
            try:
                facts = build_execution_facts(evidence)
            except BaseException as exc:
                failure = exc
        if not isinstance(evidence, dict) or not isinstance(facts, dict):
            failure = LifecycleStateError(
                "semantic retry requires saved baseline evidence and execution facts"
            )
        elif failure is None:
            try:
                build = self._build_candidate(record, evidence, facts=facts)
            except BaseException as exc:
                failure = exc

        def finish(current: dict[str, Any]) -> None:
            if build is not None:
                current.update(build)
                return
            if isinstance(facts, dict):
                current["execution_facts"] = facts
            current["error"] = self._error(
                "semantic_candidate", failure or "unknown semantic generation failure"
            )
            current["status"] = "build_failed"

        return self._finish_long_operation(
            extension_id,
            token=operation_token,
            action="retry_semantic",
            run_id=run_id,
            mutate=finish,
        )

    def revise(
        self,
        extension_id: str,
        *,
        feedback: str,
        revision_target: str,
        operation_token: str,
    ) -> dict[str, Any]:
        def revise_record(record: dict[str, Any]) -> None:
            self._state(record, "confirmation_pending", "semantic_repair_failed")
            decision = apply_confirmation_action(
                confirmation=record["confirmation"],
                action="revise",
                execution_facts=record["execution_facts"],
                candidate=record["semantic_candidate"],
                feedback=feedback,
                revision_target=revision_target,
            )
            if decision["status"] == "fact_dispute":
                record["confirmation"] = decision
                record["status"] = "rebuild_required"
                return
            try:
                candidate, history = repair_semantic_candidate(
                    execution_facts=record["execution_facts"],
                    candidate=record["semantic_candidate"],
                    user_feedback=feedback,
                    patch_generator=self.patch_generator,
                    raw_code=self.extension_dir(extension_id) / "raw_code",
                    environ=self.environment,
                )
            except BaseException as exc:
                record["status"] = "semantic_repair_failed"
                record["error"] = self._error("semantic_repair", exc)
                return
            if not history or history[-1]["status"] != "success":
                record["semantic_repair_history"] = history
                record["status"] = "semantic_repair_failed"
                record["error"] = self._error("semantic_repair", "semantic repair was rejected")
                return
            record["semantic_candidate"] = candidate
            record["semantic_repair_history"] = history
            record["confirmation"] = create_confirmation(
                execution_facts=record["execution_facts"], candidate=candidate, repair_history=history
            )
            record["runtime_binding_contract"] = build_runtime_binding_contract(
                record["execution_facts"], candidate, self.extension_dir(extension_id) / "raw_code"
            )
            record["error"] = None
            record["status"] = "confirmation_pending"

        return self._short_operation(
            extension_id, token=operation_token, action="revise", mutate=revise_record
        )

    def confirm(self, extension_id: str, *, operation_token: str) -> dict[str, Any]:
        def confirm_record(record: dict[str, Any]) -> None:
            self._state(record, "confirmation_pending")
            confirmation = apply_confirmation_action(
                confirmation=record["confirmation"], action="confirm",
                execution_facts=record["execution_facts"], candidate=record["semantic_candidate"],
            )
            if not is_candidate_accepted(
                confirmation=confirmation,
                execution_facts=record["execution_facts"], candidate=record["semantic_candidate"],
            ):
                raise LifecycleStateError("candidate confirmation is stale")
            record["confirmation"] = confirmation
            record["status"] = "confirmed"

        return self._short_operation(
            extension_id, token=operation_token, action="confirm", mutate=confirm_record
        )

    def _default_inputs(self, record: dict[str, Any]) -> dict[str, Path]:
        data = self.extension_dir(record["extension_id"]) / "raw_test_data"
        return {
            item["path"]: data / item["path"]
            for item in record["runtime_binding_contract"].get("inputs", [])
            if item.get("replaceable")
        }

    @staticmethod
    def _job_path(path: Path, roots: tuple[Path, Path]) -> Path:
        candidate = Path(os.path.abspath(Path(path).expanduser())).resolve()
        resolved_roots = tuple(root.resolve() for root in roots)
        inside = any(candidate.is_relative_to(root) for root in resolved_roots)
        if not candidate.exists():
            raise ValueError(f"runtime input file or directory not found: {candidate}")
        if not (candidate.is_file() or candidate.is_dir()):
            raise ValueError(f"runtime input must be a file or directory: {candidate}")
        if not inside:
            raise ValueError(
                f"runtime input must be a file inside the selected job: {candidate}"
            )
        return candidate

    def _execute(
        self,
        record: dict[str, Any],
        *,
        run_id: str,
        run_dir: Path,
        input_overrides: dict[str, Path],
        parameter_overrides: dict[str, Any] | None = None,
        explicit_prefix: str | None = None,
        operation_token: str,
        phase: str,
    ) -> dict[str, Any]:
        root = self.extension_dir(record["extension_id"])
        resolved = resolve_runtime_bindings(
            record["runtime_binding_contract"], input_overrides, parameter_overrides or {},
            explicit_prefix=explicit_prefix,
        )
        if resolved["status"] != "resolved":
            return {
                "run_id": run_id, "success": False, "contract_passed": False,
                "binding": resolved, "projection": {"files": [], "stdout": {"present": False}},
            }
        rendered = render_runtime_package(
            record["runtime_binding_contract"], root / "raw_code", resolved, run_dir
        )
        runtime_env = str(record.get("runtime_env") or "test4py311")
        result = replay_runtime_binding(
            rendered,
            env=self._make_env(runtime_env),
            on_process_start=lambda identity: self._mark_process(
                record["extension_id"], token=operation_token, phase=phase, identity=identity
            ),
        )
        result["run_id"] = run_id
        result["binding"] = resolved
        result["projection"] = self._project_outputs(run_dir, result)
        return result

    @staticmethod
    def _project_outputs(run_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
        output_root = run_dir / "outputs"
        output_root.mkdir(exist_ok=True)
        files: list[dict[str, Any]] = []
        for item in result.get("outputs", []):
            runtime_path = item["runtime_path"]
            source = run_dir / "work" / runtime_path
            destination = output_root / runtime_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            files.append({**item, "path": runtime_path, "absolute_path": str(destination)})
        stdout_path = Path(result.get("logs", {}).get("stdout", ""))
        stdout = {"present": stdout_path.is_file() and stdout_path.stat().st_size > 0}
        if stdout["present"]:
            stdout.update({"path": str(stdout_path), "text": stdout_path.read_text(encoding="utf-8", errors="replace")})
        return {"files": files, "stdout": stdout}

    def validate(self, extension_id: str, *, operation_token: str) -> dict[str, Any]:
        run_id = f"validation_{uuid4().hex}"
        record, existing = self._begin_long_operation(
            extension_id, token=operation_token, action="validate", running_status="validation_running",
            allowed=("confirmed", "validation_failed"), run_id=run_id,
        )
        if record is None:
            return existing
        result: dict[str, Any]
        try:
            result = self._execute(
                record, run_id=run_id, run_dir=self.extension_dir(extension_id) / "runs" / run_id,
                input_overrides=self._default_inputs(record), operation_token=operation_token, phase="validation",
            )
        except BaseException as exc:
            result = {"run_id": run_id, "success": False, "contract_passed": False, "error": self._error("validation", exc), "projection": {"files": [], "stdout": {"present": False}}}

        def finish(current: dict[str, Any]) -> None:
            current.setdefault("validation_runs", []).append(result)
            current["status"] = "result_review_pending" if result.get("contract_passed") else "validation_failed"
            current["error"] = None if result.get("contract_passed") else result.get("error")

        return self._finish_long_operation(
            extension_id, token=operation_token, action="validate", run_id=run_id, mutate=finish
        )

    def review_result(self, extension_id: str, *, operation_token: str) -> dict[str, Any]:
        def review(record: dict[str, Any]) -> None:
            self._state(record, "result_review_pending")
            run = record["validation_runs"][-1]
            if not run.get("contract_passed"):
                raise LifecycleStateError("only a successful validation run can be reviewed")
            record["reviewed_validation_run_id"] = run["run_id"]
            record["status"] = "reviewed"

        return self._short_operation(extension_id, token=operation_token, action="review_result", mutate=review)

    def publish(self, extension_id: str, *, operation_token: str) -> dict[str, Any]:
        def publish_record(record: dict[str, Any]) -> None:
            self._state(record, "reviewed")
            reviewed = record.get("reviewed_validation_run_id")
            run = next((item for item in record["validation_runs"] if item["run_id"] == reviewed), None)
            if run is None or not run.get("contract_passed"):
                raise LifecycleStateError("publication requires the reviewed successful validation run")
            record["published_validation_run_id"] = reviewed
            record["status"] = "published"

        return self._short_operation(extension_id, token=operation_token, action="publish", mutate=publish_record)

    def help(self, extension_id: str) -> dict[str, Any]:
        record = self.get(extension_id)
        return {
            "extension_id": extension_id, "status": record["status"],
            "function_summary": record.get("semantic_candidate", {}).get("function_summary"),
            "original_command": record["original_command"],
            "inputs": deepcopy(record.get("runtime_binding_contract", {}).get("inputs", [])),
            "parameters": deepcopy(record.get("runtime_binding_contract", {}).get("parameters", [])),
            "outputs": deepcopy(record.get("runtime_binding_contract", {}).get("outputs", {})),
        }

    def run(
        self,
        extension_id: str,
        *,
        input_overrides: dict[str, Path],
        run_job_id: str | None = None,
        input_root: Path | None = None,
        workspace_root: Path | None = None,
        parameter_overrides: dict[str, Any] | None = None,
        explicit_prefix: str | None = None,
        operation_token: str,
    ) -> dict[str, Any]:
        if (
            not run_job_id
            or run_job_id in {".", ".."}
            or "/" in run_job_id
            or "\\" in run_job_id
            or input_root is None
            or workspace_root is None
        ):
            raise ValueError("run_job_id, input_root, and workspace_root are required")
        job_input = Path(os.path.abspath(Path(input_root) / run_job_id))
        job_workspace = Path(os.path.abspath(Path(workspace_root) / run_job_id))
        if not job_input.is_dir() or not job_workspace.is_dir():
            raise ValueError("selected job input and workspace directories must exist")
        checked_inputs = {
            key: self._job_path(Path(value), (job_input, job_workspace))
            for key, value in input_overrides.items()
        }
        run_id = f"runtime_{uuid4().hex}"
        record, existing = self._begin_long_operation(
            extension_id, token=operation_token, action="run", running_status="runtime_running",
            allowed=("published",), run_id=run_id,
        )
        if record is None:
            return existing
        result: dict[str, Any]
        try:
            result = self._execute(
                record, run_id=run_id,
                run_dir=job_workspace / "extension_runs" / extension_id / run_id,
                input_overrides=checked_inputs, parameter_overrides=parameter_overrides,
                explicit_prefix=explicit_prefix, operation_token=operation_token, phase="runtime",
            )
        except BaseException as exc:
            result = {"run_id": run_id, "success": False, "contract_passed": False, "error": self._error("runtime", exc), "projection": {"files": [], "stdout": {"present": False}}}

        def finish(current: dict[str, Any]) -> None:
            current.setdefault("runtime_runs", []).append(result)
            current["status"] = "published"

        return self._finish_long_operation(
            extension_id, token=operation_token, action="run", run_id=run_id, mutate=finish
        )

    def stop(self, extension_id: str, *, operation_token: str) -> dict[str, Any]:
        with self._lock(extension_id):
            record = self._read_record(extension_id)
            active = record.get("active_process")
            operation = record.get("operations", {}).get(operation_token)
            if not isinstance(operation, dict) or operation.get("state") != "running":
                return {"extension_id": extension_id, "operation_token": operation_token, "stopped": False}
            identity = (
                active.get("identity")
                if isinstance(active, dict) and active.get("operation_token") == operation_token
                else None
            )
            operation["stop_requested"] = True
            self._write_record(extension_id, record)
        stopped = (
            stop_process_group(identity, grace_seconds=1.0)
            if isinstance(identity, dict)
            else False
        )
        with self._lock(extension_id):
            record = self._read_record(extension_id)
            active = record.get("active_process")
            if isinstance(active, dict) and active.get("operation_token") == operation_token:
                active["stop_result"] = stopped
                self._write_record(extension_id, record)
            elif not stopped:
                operation = record.get("operations", {}).get(operation_token, {})
                runs = [record.get("baseline", {})]
                runs.extend(record.get("validation_runs", []))
                runs.extend(record.get("runtime_runs", []))
                stopped = bool(
                    operation.get("state") == "completed"
                    and any(
                        isinstance(item, dict)
                        and item.get("returncode", 0) < 0
                        and (
                            operation.get("action") in {"upload", "rebuild"}
                            or item.get("run_id") == operation.get("run_id")
                        )
                        for item in runs
                    )
                )
        return {"extension_id": extension_id, "operation_token": operation_token, "stopped": stopped}
