"""Thin asynchronous HTTP boundary for the lightweight extension service."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from gfa_core.extension_stage.raw_package_normalizer import normalize_raw_package

from .service import LightweightExtensionService
from . import upstream


UPLOAD_CHUNK_BYTES = 1024 * 1024


def _safe_component(value: str, *, name: str) -> str:
    value = str(value or "").strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"{name} must be one safe path component")
    return value


def _relative_path(value: str) -> Path:
    path = Path(value or "")
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("uploaded filename must be a safe relative path")
    return path


async def _copy_upload_file(upload: UploadFile, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
            handle.write(chunk)


def _server_data_files(value: str | None) -> list[tuple[Path, Path]]:
    files: list[tuple[Path, Path]] = []
    for raw in (value or "").splitlines():
        text = raw.strip()
        if not text:
            continue
        source = Path(text).expanduser()
        if not source.is_absolute():
            raise ValueError(f"测试数据路径必须是绝对路径：{text}")
        if not source.exists():
            raise ValueError(f"测试数据路径不存在：{text}")
        if source.is_file():
            files.append((Path(source.name), source.resolve()))
            continue
        if not source.is_dir():
            raise ValueError(f"测试数据路径不是普通文件或目录：{text}")
        files.extend(
            (item.relative_to(source.parent), item.resolve())
            for item in sorted(source.rglob("*"))
            if item.is_file()
        )
    return files


def _path_from_env(value: str, *, project_root: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else project_root / path


@dataclass(frozen=True)
class LightweightApiSettings:
    project_root: Path
    store_root: Path
    input_root: Path
    workspace_root: Path

    @classmethod
    def from_environment(
        cls, *, environ: dict[str, str] | None = None, project_root: Path | None = None
    ) -> "LightweightApiSettings":
        env = os.environ if environ is None else environ
        default_root = Path(__file__).resolve().parents[2]
        root_value = str(
            env.get("BBAV5_PROJECT_ROOT") or env.get("PROJECT_ROOT") or project_root or default_root
        )
        root = _path_from_env(root_value, project_root=default_root).resolve()
        return cls(
            project_root=root,
            store_root=_path_from_env(
                str(
                    env.get("BBAV5_LIGHTWEIGHT_EXTENSION_STORE")
                    or ".runtime/lightweight-extension/store"
                ),
                project_root=root,
            ),
            input_root=_path_from_env(
                str(env.get("BBAV5_LIGHTWEIGHT_INPUT") or "input"), project_root=root
            ),
            workspace_root=_path_from_env(
                str(env.get("BBAV5_LIGHTWEIGHT_WORKSPACE") or "workspace"), project_root=root
            ),
        )


def _runtime_input_path(
    settings: LightweightApiSettings, run_job_id: str, value: str
) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    parts = path.parts
    if len(parts) >= 3 and parts[1] == run_job_id:
        if parts[0] == "input":
            return settings.input_root / run_job_id / Path(*parts[2:])
        if parts[0] == "workspace":
            return settings.workspace_root / run_job_id / Path(*parts[2:])
    return path


class _TokenRequest(BaseModel):
    operation_token: str


class _ReviseRequest(_TokenRequest):
    feedback: str
    revision_target: str


class _RunRequest(_TokenRequest):
    run_job_id: str
    input_overrides: dict[str, str] = Field(default_factory=dict)
    parameter_overrides: dict[str, Any] = Field(default_factory=dict)
    explicit_prefix: str | None = None


class _IntentRequest(BaseModel):
    extension_id: str | None = None
    message: str = ""


def _receipt_path(settings: LightweightApiSettings, token: str) -> Path:
    return settings.store_root / "operations" / f"{_safe_component(token, name='operation_token')}.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


@contextmanager
def _token_lock(settings: LightweightApiSettings, token: str):
    token = _safe_component(token, name="operation_token")
    lock_path = settings.store_root / "operations" / ".locks" / f"{token}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".operation-", delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _reserve_operation_locked(
    settings: LightweightApiSettings, *, extension_id: str, action: str, token: str
) -> tuple[dict[str, Any], bool]:
    path = _receipt_path(settings, token)
    existing = _read_json(path)
    if existing is not None:
        if existing.get("extension_id") != extension_id or existing.get("action") != action:
            raise ValueError("operation_token is already bound to another action")
        return existing, False
    receipt = _queued_receipt(extension_id=extension_id, action=action, token=token)
    _write_json(path, receipt)
    return receipt, True


def _queued_receipt(*, extension_id: str, action: str, token: str) -> dict[str, Any]:
    return {
        "operation_id": token,
        "operation_token": token,
        "extension_id": extension_id,
        "action": action,
        "state": "queued",
        "status": "queued",
    }


def _reserve_operation(
    settings: LightweightApiSettings, *, extension_id: str, action: str, token: str
) -> tuple[dict[str, Any], bool]:
    with _token_lock(settings, token):
        return _reserve_operation_locked(
            settings, extension_id=extension_id, action=action, token=token
        )


def _set_receipt_locked(
    settings: LightweightApiSettings, token: str, **changes: Any
) -> dict[str, Any]:
    path = _receipt_path(settings, token)
    receipt = _read_json(path)
    if receipt is None:
        raise ValueError("operation does not exist")
    receipt.update(changes)
    _write_json(path, receipt)
    return receipt


def _set_receipt(settings: LightweightApiSettings, token: str, **changes: Any) -> dict[str, Any]:
    with _token_lock(settings, token):
        return _set_receipt_locked(settings, token, **changes)


def _service_for(settings: LightweightApiSettings) -> LightweightExtensionService:
    return LightweightExtensionService(settings.store_root)


def _logs_for(record: dict[str, Any], token: str) -> list[str]:
    operation = (record.get("operations") or {}).get(token) or {}
    run_id = operation.get("run_id")
    baseline_action = str(operation.get("action") or "") in {"upload", "rebuild"}
    candidates: list[dict[str, Any]] = []
    baseline = record.get("baseline")
    if (
        baseline_action
        and isinstance(baseline, dict)
        and Path(str(baseline.get("run_dir") or "")).name == run_id
    ):
        candidates.append(baseline)
    else:
        candidates.extend(item for item in record.get("validation_runs", []) if isinstance(item, dict))
        candidates.extend(item for item in record.get("runtime_runs", []) if isinstance(item, dict))
    logs: set[str] = set()
    for item in candidates:
        if run_id and not baseline_action and item.get("run_id") != run_id:
            continue
        for value in (item.get("logs") or {}).values():
            if value:
                logs.add(str(value))
    return sorted(logs)


def _operation_run(record: dict[str, Any], operation: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    run_id = operation.get("run_id") or (operation.get("response") or {}).get("run_id")
    action = str(operation.get("action") or "")
    if action in {"upload", "rebuild"}:
        baseline = record.get("baseline")
        if not isinstance(baseline, dict) or Path(str(baseline.get("run_dir") or "")).name != run_id:
            return None, "baseline"
        return baseline, "baseline"
    collection_name = "runtime_runs" if action == "run" else "validation_runs"
    for run in record.get(collection_name, []):
        if isinstance(run, dict) and run.get("run_id") == run_id:
            return run, "runtime" if action == "run" else "validation"
    return None, "runtime" if action == "run" else "validation"


def _missing_output_dir_hint(record: dict[str, Any], token: str) -> str | None:
    """当 stderr 提示输出目录不存在时，给出更明确的提示。"""
    for log_path in _logs_for(record, token):
        try:
            text = Path(log_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "No such file or directory" in text and "outputs/" in text:
            return (
                "输出目录不存在：工具把输出写入 outputs/ 子目录但未创建该目录"
                "（hmmsearch/diamond 等不会自动建目录），请在 run.sh 开头添加 mkdir -p 对应目录"
            )
    return None


def _run_error(record: dict[str, Any], token: str, run: dict[str, Any], phase: str) -> dict[str, Any]:
    details = run.get("error") if isinstance(run.get("error"), dict) else {}
    binding = run.get("binding") if isinstance(run.get("binding"), dict) else {}
    binding_error = binding.get("error") if isinstance(binding.get("error"), dict) else {}
    hint = _missing_output_dir_hint(record, token)
    message = (
        hint
        or details.get("message")
        or binding_error.get("message")
        or ("runtime binding was not resolved" if binding.get("status") != "resolved" else "execution did not satisfy the output contract")
    )
    return _error(str(details.get("phase") or phase), message, logs=_logs_for(record, token))


def _current_operation_error(
    service: LightweightExtensionService, *, extension_id: str, token: str, result: dict[str, Any]
) -> dict[str, Any] | None:
    record = service.get(extension_id)
    operation = (record.get("operations") or {}).get(token)
    error = record.get("error")
    if not isinstance(operation, dict) or not isinstance(error, dict):
        return None
    operation_status = (operation.get("response") or {}).get("status") or record.get("status")
    if operation_status != result.get("status"):
        return None
    return _error(
        str(error.get("phase") or operation.get("action") or "operation"),
        str(error.get("message") or "operation failed"),
        logs=_logs_for(record, token),
    )


def _operation_view(
    settings: LightweightApiSettings, *, token: str, service_factory: Callable[[LightweightApiSettings], LightweightExtensionService]
) -> dict[str, Any]:
    receipt = _read_json(_receipt_path(settings, token))
    if receipt is None:
        raise ValueError("operation does not exist")
    result = dict(receipt)
    try:
        record = service_factory(settings).get(str(receipt["extension_id"]))
    except ValueError:
        return result
    operation = (record.get("operations") or {}).get(token)
    if isinstance(operation, dict):
        result.update(operation.get("response") or {})
        result["state"] = operation.get("state", result.get("state"))
        result["status"] = (
            (operation.get("response") or {}).get("status")
            or record.get("status")
            or result.get("status")
        )
        result["extension_status"] = record.get("status")
        run, phase = _operation_run(record, operation)
        if isinstance(run, dict) and (
            run.get("success") is False or run.get("contract_passed") is False
        ):
            result["outcome"] = "failed"
            result["error"] = _run_error(record, token, run, phase)
            result["logs"] = result["error"]["logs"]
        elif isinstance(run, dict):
            result["outcome"] = "succeeded"
        elif result.get("error"):
            result["outcome"] = "failed"
            result["logs"] = list((result.get("error") or {}).get("logs") or [])
        elif operation.get("action") not in {"upload", "rebuild"} and record.get("error"):
            result["outcome"] = "failed"
            result["error"] = record["error"]
            result["logs"] = _logs_for(record, token)
    return result


def _error(phase: str, exc: BaseException | str, *, logs: list[str] | None = None) -> dict[str, Any]:
    return {"phase": phase, "message": str(exc), "logs": logs or []}


def _http_error(status_code: int, phase: str, exc: BaseException | str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=_error(phase, exc))


def execute_queued_operation(payload: dict[str, Any]) -> dict[str, Any]:
    """Worker entrypoint. It intentionally owns no Celery configuration."""

    settings = LightweightApiSettings.from_environment()
    action = str(payload["action"])
    runtime_env = str(payload.get("runtime_env", "test4py311"))
    extension_id = _safe_component(str(payload["extension_id"]), name="extension_id")
    token = _safe_component(str(payload["operation_token"]), name="operation_token")
    _set_receipt(settings, token, state="running", status="running")
    service = _service_for(settings)
    try:
        if action == "upload":
            stage = settings.store_root / "staging" / token
            result = service.upload(
                extension_id=extension_id,
                raw_code=stage / "raw_code",
                raw_data=stage / "raw_test_data",
                original_command=str(payload["original_command"]),
                runtime_env=runtime_env,
                operation_token=token,
            )
        elif action == "rebuild":
            result = service.rebuild(extension_id, operation_token=token)
        elif action == "retry_semantic":
            result = service.retry_semantic(extension_id, operation_token=token)
        elif action == "revise":
            result = service.revise(
                extension_id,
                feedback=str(payload["feedback"]),
                revision_target=str(payload["revision_target"]),
                operation_token=token,
            )
        elif action == "confirm":
            result = service.confirm(extension_id, operation_token=token)
        elif action == "validate":
            result = service.validate(extension_id, operation_token=token)
        elif action == "review":
            result = service.review_result(extension_id, operation_token=token)
        elif action == "publish":
            result = service.publish(extension_id, operation_token=token)
        elif action == "run":
            result = service.run(
                extension_id,
                input_overrides={
                    key: _runtime_input_path(settings, str(payload["run_job_id"]), value)
                    for key, value in payload.get("input_overrides", {}).items()
                },
                run_job_id=str(payload["run_job_id"]),
                input_root=settings.input_root,
                workspace_root=settings.workspace_root,
                parameter_overrides=payload.get("parameter_overrides") or {},
                explicit_prefix=payload.get("explicit_prefix"),
                operation_token=token,
            )
        else:
            raise ValueError(f"unsupported lightweight operation: {action}")
    except BaseException as exc:
        receipt = _set_receipt(
            settings, token, state="failed", status="failed", error=_error(action, exc)
        )
        return receipt
    changes: dict[str, Any] = {
        "state": "completed",
        "status": result.get("status"),
        "result": result,
    }
    try:
        current_error = _current_operation_error(
            service, extension_id=extension_id, token=token, result=result
        )
    except ValueError:
        current_error = None
    if current_error is not None:
        changes["error"] = current_error
    receipt = _set_receipt(settings, token, **changes)
    return receipt


def _default_enqueue(payload: dict[str, Any]) -> str:
    from tasks import execute_lightweight_extension_operation

    return str(execute_lightweight_extension_operation.apply_async(args=[payload]).id)


def create_lightweight_extension_router(
    *,
    settings: LightweightApiSettings | None = None,
    service_factory: Callable[[LightweightApiSettings], LightweightExtensionService] = _service_for,
    enqueue: Callable[[dict[str, Any]], str] = _default_enqueue,
) -> APIRouter:
    settings = settings or LightweightApiSettings.from_environment()
    router = APIRouter(prefix="/api/lightweight-extension-stages", tags=["lightweight-extension-stages"])

    def submit(extension_id: str, action: str, token: str, **payload: Any) -> dict[str, Any]:
        extension_id = _safe_component(extension_id, name="extension_id")
        token = _safe_component(token, name="operation_token")
        receipt, created = _reserve_operation(
            settings, extension_id=extension_id, action=action, token=token
        )
        if created:
            try:
                celery_task_id = enqueue(
                    {
                        "extension_id": extension_id,
                        "action": action,
                        "operation_token": token,
                        **payload,
                    }
                )
            except BaseException as exc:
                receipt = _set_receipt(
                    settings, token, state="failed", status="failed", error=_error("queue", exc)
                )
            else:
                receipt = _set_receipt(settings, token, celery_task_id=celery_task_id)
        return receipt

    @router.post("/upload", status_code=202)
    async def upload(
        extension_id: str = Form(...),
        original_command: str | None = Form(None),
        operation_token: str = Form(...),
        raw_code_files: list[UploadFile] | None = File(None),
        raw_data_files: list[UploadFile] | None = File(None),
        raw_data_paths: str | None = Form(None),
        runtime_env: str = Form("test4py311"),
    ) -> dict[str, Any]:
        temporary_stage: Path | None = None
        try:
            token = _safe_component(operation_token, name="operation_token")
            safe_id = _safe_component(extension_id, name="extension_id")
            staged_files = [
                ("raw_code", _relative_path(item.filename or ""), item)
                for item in (raw_code_files or [])
            ] + [
                ("raw_test_data", _relative_path(item.filename or ""), item)
                for item in (raw_data_files or [])
            ]
            server_data_files = _server_data_files(raw_data_paths)
            data_destinations = {
                relative.as_posix()
                for kind, relative, _item in staged_files
                if kind == "raw_test_data"
            }
            for relative, _source in server_data_files:
                key = relative.as_posix()
                if key in data_destinations:
                    raise ValueError(f"测试数据文件名冲突：{key}")
                data_destinations.add(key)
            if not data_destinations:
                raise ValueError("请上传测试数据或填写测试数据服务器路径")
            staging_root = settings.store_root / "staging"
            staging_root.mkdir(parents=True, exist_ok=True)
            temporary_stage = Path(
                tempfile.mkdtemp(prefix=f".{token}-{uuid4().hex}-", dir=staging_root)
            )
            raw_code = temporary_stage / "raw_code"
            raw_data = temporary_stage / "raw_test_data"
            raw_code.mkdir()
            raw_data.mkdir()
            for kind, relative, item in staged_files:
                destination = temporary_stage / kind / relative
                await _copy_upload_file(item, destination)
            for relative, source in server_data_files:
                destination = raw_data / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(source)
            normalized_entry = normalize_raw_package(
                explicit_command=original_command or "",
                raw_code=raw_code,
                raw_data=raw_data,
                saved_code=sorted(path for path in raw_code.rglob("*") if path.is_file()),
            )
            if normalized_entry["requires_user_confirmation"]:
                warnings = "; ".join(normalized_entry["normalization_warnings"])
                scripts = ", ".join(normalized_entry["detected_scripts"])
                detail = warnings or "uploaded code has no unambiguous entry command"
                if scripts:
                    detail += f"; detected scripts: {scripts}"
                raise ValueError(f"{detail}; provide original_command")
            normalized_command = str(normalized_entry["entry_command"]).strip()
            with _token_lock(settings, token):
                existing = _read_json(_receipt_path(settings, token))
                if existing is not None:
                    if existing.get("extension_id") != safe_id or existing.get("action") != "upload":
                        raise ValueError("operation_token is already bound to another action")
                    receipt = existing
                else:
                    stage = staging_root / token
                    if stage.exists():
                        shutil.rmtree(stage)
                    temporary_stage.replace(stage)
                    temporary_stage = None
                    receipt = _queued_receipt(extension_id=safe_id, action="upload", token=token)
                    _write_json(_receipt_path(settings, token), receipt)
                    try:
                        celery_task_id = enqueue(
                            {
                                "extension_id": safe_id,
                                "action": "upload",
                                "operation_token": token,
                                "original_command": normalized_command,
                                "runtime_env": runtime_env,
                            }
                        )
                    except BaseException as exc:
                        receipt = _set_receipt_locked(
                            settings, token, state="failed", status="failed", error=_error("queue", exc)
                        )
                    else:
                        receipt = _set_receipt_locked(settings, token, celery_task_id=celery_task_id)
            return receipt
        except ValueError as exc:
            raise _http_error(400, "upload", exc) from exc
        finally:
            if temporary_stage is not None:
                shutil.rmtree(temporary_stage, ignore_errors=True)

    @router.get("/")
    async def list_extensions() -> dict[str, Any]:
        extensions: list[dict[str, str]] = []
        for record_path in sorted((settings.store_root / "extensions").glob("*/extension.json")):
            record = _read_json(record_path)
            if isinstance(record, dict) and record.get("extension_id"):
                extensions.append(
                    {
                        "extension_id": str(record["extension_id"]),
                        "status": str(record.get("status") or ""),
                    }
                )
        return {"extensions": extensions}

    @router.post("/{extension_id}/rebuild", status_code=202)
    async def rebuild(extension_id: str, req: _TokenRequest) -> dict[str, Any]:
        return _submit_response(submit, extension_id, "rebuild", req.operation_token)

    @router.post("/{extension_id}/retry-semantic", status_code=202)
    async def retry_semantic(extension_id: str, req: _TokenRequest) -> dict[str, Any]:
        return _submit_response(
            submit, extension_id, "retry_semantic", req.operation_token
        )

    @router.post("/{extension_id}/revise", status_code=202)
    async def revise(extension_id: str, req: _ReviseRequest) -> dict[str, Any]:
        return _submit_response(
            submit, extension_id, "revise", req.operation_token,
            feedback=req.feedback, revision_target=req.revision_target,
        )

    @router.post("/{extension_id}/confirm", status_code=202)
    async def confirm(extension_id: str, req: _TokenRequest) -> dict[str, Any]:
        return _submit_response(submit, extension_id, "confirm", req.operation_token)

    @router.post("/{extension_id}/validate", status_code=202)
    async def validate(extension_id: str, req: _TokenRequest) -> dict[str, Any]:
        return _submit_response(submit, extension_id, "validate", req.operation_token)

    @router.post("/{extension_id}/review", status_code=202)
    async def review(extension_id: str, req: _TokenRequest) -> dict[str, Any]:
        return _submit_response(submit, extension_id, "review", req.operation_token)

    @router.post("/{extension_id}/publish", status_code=202)
    async def publish(extension_id: str, req: _TokenRequest) -> dict[str, Any]:
        return _submit_response(submit, extension_id, "publish", req.operation_token)

    @router.post("/{extension_id}/run", status_code=202)
    async def run(extension_id: str, req: _RunRequest) -> dict[str, Any]:
        return _submit_response(
            submit, extension_id, "run", req.operation_token, run_job_id=req.run_job_id,
            input_overrides=req.input_overrides, parameter_overrides=req.parameter_overrides,
            explicit_prefix=req.explicit_prefix,
        )

    @router.get("/{extension_id}/help")
    async def help(extension_id: str) -> dict[str, Any]:
        try:
            return service_factory(settings).help(_safe_component(extension_id, name="extension_id"))
        except ValueError as exc:
            raise _http_error(404, "help", exc) from exc
    @router.get("/{extension_id}/upstream-candidates")
    async def upstream_candidates(extension_id: str, job_id: str = "") -> dict[str, Any]:
        """同 job 内其他轻量拓展成功运行产物对契约输入槽的候选（供运行向导预填）。"""
        try:
            extension_id = _safe_component(extension_id, name="extension_id")
            help_payload = service_factory(settings).help(extension_id)
        except ValueError as exc:
            raise _http_error(404, "upstream-candidates", exc) from exc
        job_id = str(job_id or "").strip()
        base = {"extension_id": extension_id, "job_id": job_id, "candidates": {}, "warnings": []}
        if not job_id or job_id in {".", ".."} or "/" in job_id or "\\" in job_id:
            return base
        try:
            record = service_factory(settings).get(extension_id)
            contract = record.get("runtime_binding_contract") or {}
            patchable_paths = {
                str(target.get("binding_key") or "")[len("path:") :]
                for target in contract.get("patch_targets") or []
                if isinstance(target, dict)
                and str(target.get("binding_key") or "").startswith("path:")
            }
            selected = upstream.resolve_upstream_slots(
                settings.workspace_root,
                job_id,
                help_payload.get("inputs") or [],
                exclude_extension_id=extension_id,
                patchable_paths=patchable_paths,
            )
            job_root = settings.workspace_root / job_id
            candidates: dict[str, Any] = {}
            for slot, candidate in selected.items():
                try:
                    relative = candidate.path.relative_to(job_root).as_posix()
                except ValueError:
                    try:
                        relative = candidate.path.resolve().relative_to(job_root.resolve()).as_posix()
                    except ValueError:
                        continue
                candidates[slot] = {
                    "path": f"workspace/{job_id}/{relative}",
                    "source_extension_id": candidate.source_extension_id,
                    "run_id": candidate.run_id,
                    "is_dir": candidate.is_dir,
                }
            base["candidates"] = candidates
            return base
        except OSError:
            return base

    @router.post("/intent")
    async def intent(req: _IntentRequest) -> dict[str, Any]:
        if not req.extension_id:
            return {
                "intent": req.message,
                "usage": "上传可执行代码、测试数据和原始命令，核对识别出的输入与参数；验证结果后授权发布。发布后可用自然语言指定已发布拓展分析、输入文件和原命令已有参数来运行，也可随时询问某个工具的帮助。",
            }
        try:
            return {"intent": req.message, "help": service_factory(settings).help(req.extension_id)}
        except ValueError as exc:
            raise _http_error(404, "intent", exc) from exc

    @router.get("/operations/{operation_token}")
    async def operation(operation_token: str) -> dict[str, Any]:
        try:
            return _operation_view(settings, token=_safe_component(operation_token, name="operation_token"), service_factory=service_factory)
        except ValueError as exc:
            raise _http_error(404, "operation", exc) from exc

    @router.post("/{extension_id}/operations/{operation_token}/stop")
    async def stop(extension_id: str, operation_token: str) -> dict[str, Any]:
        try:
            result = service_factory(settings).stop(
                _safe_component(extension_id, name="extension_id"),
                operation_token=_safe_component(operation_token, name="operation_token"),
            )
            return result
        except ValueError as exc:
            raise _http_error(400, "stop", exc) from exc

    @router.get("/{extension_id}/results")
    async def results(extension_id: str) -> dict[str, Any]:
        try:
            record = service_factory(settings).get(_safe_component(extension_id, name="extension_id"))
            return {
                "extension_id": extension_id,
                "validation_runs": record.get("validation_runs", []),
                "runtime_runs": record.get("runtime_runs", []),
            }
        except ValueError as exc:
            raise _http_error(404, "results", exc) from exc

    @router.get("/{extension_id}/files")
    async def result_file(extension_id: str, run_id: str, path: str):
        try:
            if _relative_path(path).as_posix() != path:
                raise ValueError("path must be a safe relative output path")
            safe_id = _safe_component(extension_id, name="extension_id")
            safe_run = _safe_component(run_id, name="run_id")
            record = service_factory(settings).get(safe_id)
            run = next((item for item in record.get("validation_runs", []) if item.get("run_id") == safe_run), None)
            kind = "validation"
            if run is None:
                run = next((item for item in record.get("runtime_runs", []) if item.get("run_id") == safe_run), None)
                kind = "runtime"
            if not isinstance(run, dict):
                raise ValueError("run does not exist")
            selected = next(
                (item for item in (run.get("projection") or {}).get("files", []) if item.get("path") == path),
                None,
            )
            if not isinstance(selected, dict):
                raise ValueError("file is not a listed result output")
            output = Path(str(selected.get("absolute_path") or "")).resolve()
            if kind == "validation":
                output_root = (
                    settings.store_root / "extensions" / safe_id / "runs" / safe_run / "outputs"
                ).resolve()
            else:
                relative = output.relative_to(settings.workspace_root.resolve())
                if len(relative.parts) < 6:
                    raise ValueError("runtime result file is outside its run output directory")
                job_id, marker, found_id, found_run, outputs, *suffix = relative.parts
                if (
                    marker != "extension_runs"
                    or found_id != safe_id
                    or found_run != safe_run
                    or outputs != "outputs"
                    or not suffix
                ):
                    raise ValueError("runtime result file is outside its run output directory")
                output_root = (
                    settings.workspace_root
                    / _safe_component(job_id, name="run_job_id")
                    / marker
                    / safe_id
                    / safe_run
                    / outputs
                ).resolve()
            if output != output_root / path or not output.is_file():
                raise ValueError("result file does not exist")
            return FileResponse(str(output), filename=output.name)
        except ValueError as exc:
            raise _http_error(400, "files", exc) from exc

    return router


def _submit_response(
    submit: Callable[..., dict[str, Any]], extension_id: str, action: str, token: str, **payload: Any
) -> dict[str, Any]:
    try:
        return submit(extension_id, action, token, **payload)
    except ValueError as exc:
        raise _http_error(400, action, exc) from exc
