"""Lightweight resource gate and task ledger for local stage execution.

The project runs on one workstation, so a small file-backed ledger is enough to
coordinate FastAPI and Celery worker processes without adding another service.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import fcntl


TERMINAL_STATES = {"success", "completed", "failed", "failure", "cancelled", "revoked"}
ACTIVE_STATES = {"waiting_resource", "running"}


def _project_root() -> Path:
    return Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[2])).resolve()


def _state_dir() -> Path:
    path = Path(os.environ.get("BBAV5_RESOURCE_STATE_DIR", _project_root() / "workspace" / "resource_governor"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path() -> Path:
    return _state_dir() / "state.json"


def _config_path() -> Path:
    return _state_dir() / "config.json"


def _lock_path() -> Path:
    return _state_dir() / "state.lock"


def _now() -> str:
    return datetime.now().isoformat()


DEFAULT_RESOURCE_CONFIG = {
    "total_cpu": 20,
    "max_running_tasks": 11,
    "gene_family_project_max_cpu": 20,
    "rnaseq_project_max_cpu": 20,
    "rnaseq_qc_sample_parallel_jobs": 2,
    "rnaseq_align_sample_parallel_jobs": 2,
    "stage_cpu_overrides": {},
}


def _sanitize_stage_cpu_overrides(value: Any) -> dict[str, dict[str, int]]:
    if not isinstance(value, dict):
        return {}
    sanitized: dict[str, dict[str, int]] = {}
    for stage, item in value.items():
        if not isinstance(stage, str) or not isinstance(item, dict):
            continue
        try:
            max_cpu = int(item.get("max_cpu", item.get("default_cpu", 1)))
            default_cpu = int(item.get("default_cpu", max_cpu))
        except (TypeError, ValueError):
            continue
        max_cpu = max(4, max_cpu)
        default_cpu = max(4, default_cpu)
        sanitized[stage] = {"default_cpu": default_cpu, "max_cpu": max_cpu}
    return sanitized


def _read_config_unlocked() -> dict[str, Any]:
    config = dict(DEFAULT_RESOURCE_CONFIG)
    config["total_cpu"] = int(os.environ.get("BBAV5_RESOURCE_TOTAL_CPU", config["total_cpu"]))
    config["max_running_tasks"] = int(os.environ.get("BBAV5_RESOURCE_MAX_RUNNING_TASKS", config["max_running_tasks"]))
    config["rnaseq_project_max_cpu"] = int(os.environ.get("BBAV5_RNASEQ_PROJECT_MAX_CPU", config["rnaseq_project_max_cpu"]))
    path = _config_path()
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                config.update({k: v for k, v in saved.items() if k in config})
        except Exception:
            pass
    for key, default in DEFAULT_RESOURCE_CONFIG.items():
        if key == "stage_cpu_overrides":
            config[key] = _sanitize_stage_cpu_overrides(config.get(key, default))
            continue
        if isinstance(default, str):
            config[key] = str(config.get(key, default) or "").strip()
            continue
        try:
            config[key] = max(1, int(config.get(key, default)))
        except (TypeError, ValueError):
            config[key] = default
    total = max(1, int(config.get("total_cpu", DEFAULT_RESOURCE_CONFIG["total_cpu"])))
    config["total_cpu"] = total
    config["gene_family_project_max_cpu"] = max(1, min(total, int(config.get("gene_family_project_max_cpu", total))))
    config["rnaseq_project_max_cpu"] = max(1, min(total, int(config.get("rnaseq_project_max_cpu", total))))
    return config


def get_resource_config() -> dict[str, Any]:
    return _read_config_unlocked()


def update_resource_config(updates: dict[str, Any]) -> dict[str, Any]:
    allowed = set(DEFAULT_RESOURCE_CONFIG)
    current = _read_config_unlocked()
    for key, value in (updates or {}).items():
        if key not in allowed:
            continue
        if value is None or value == "":
            continue
        if key == "stage_cpu_overrides":
            current[key] = _sanitize_stage_cpu_overrides(value)
        elif isinstance(DEFAULT_RESOURCE_CONFIG.get(key), str):
            current[key] = str(value or "").strip()
        else:
            current[key] = int(value)
    total = max(1, int(current.get("total_cpu", DEFAULT_RESOURCE_CONFIG["total_cpu"])))
    current["total_cpu"] = total
    current["max_running_tasks"] = max(1, int(current.get("max_running_tasks", DEFAULT_RESOURCE_CONFIG["max_running_tasks"])))
    current["gene_family_project_max_cpu"] = max(1, min(total, int(current.get("gene_family_project_max_cpu", total))))
    current["rnaseq_project_max_cpu"] = max(1, min(total, int(current.get("rnaseq_project_max_cpu", total))))
    current["rnaseq_qc_sample_parallel_jobs"] = max(1, int(current.get("rnaseq_qc_sample_parallel_jobs", 2)))
    current["rnaseq_align_sample_parallel_jobs"] = max(1, int(current.get("rnaseq_align_sample_parallel_jobs", 2)))
    current["stage_cpu_overrides"] = _sanitize_stage_cpu_overrides(current.get("stage_cpu_overrides", {}))
    path = _config_path()
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def _total_cpu() -> int:
    return max(1, int(_read_config_unlocked().get("total_cpu", 20)))


def _max_running_tasks() -> int:
    return max(1, int(_read_config_unlocked().get("max_running_tasks", 11)))


def _pid_alive(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    return value > 0 and Path(f"/proc/{value}").exists()


def _total_memory_gb() -> float:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / 1024 / 1024, 2)
    except Exception:
        pass
    return 0.0


def _gpu_summary() -> list[dict[str, Any]]:
    try:
        import subprocess
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        gpus = []
        for idx, line in enumerate(result.stdout.splitlines()):
            if not line.strip():
                continue
            parts = [part.strip() for part in line.split(",")]
            gpus.append({
                "index": idx,
                "name": parts[0] if parts else "unknown",
                "memory_mb": int(float(parts[1])) if len(parts) > 1 and parts[1] else 0,
            })
        return gpus
    except Exception:
        return []


def detect_hardware() -> dict[str, Any]:
    """Return local hardware facts used for RNA-seq resource planning."""
    total_cpu = os.cpu_count() or _total_cpu()
    configured_cpu = _total_cpu()
    memory_gb = _total_memory_gb()
    return {
        "cpu_count": int(total_cpu),
        "configured_total_cpu": int(configured_cpu),
        "memory_gb": memory_gb,
        "gpus": _gpu_summary(),
    }


def rnaseq_project_cpu_limit(requested_cpu: int | None = None) -> int:
    """Return the CPU budget for one RNA-seq project within the global governor."""
    config = _read_config_unlocked()
    configured_total = int(config.get("total_cpu", _total_cpu()))
    project_limit = int(config.get("rnaseq_project_max_cpu", configured_total))
    if requested_cpu:
        try:
            project_limit = min(project_limit, int(requested_cpu))
        except (TypeError, ValueError):
            pass
    return max(1, min(project_limit, configured_total))


def recommend_rnaseq_parallelism(
    total_cpu: int | None = None,
    sample_parallel_jobs: int | None = None,
    threads_per_sample: int | None = None,
    sample_count: int | None = None,
    stage: str = "rnaseq_align",
) -> dict[str, int]:
    """Compute per-project RNA-seq sample/thread settings."""
    cpu_total = max(1, int(total_cpu or rnaseq_project_cpu_limit()))
    if sample_parallel_jobs is None or int(sample_parallel_jobs or 0) < 1:
        jobs = int(_read_config_unlocked().get("rnaseq_qc_sample_parallel_jobs" if stage == "rnaseq_qc" else "rnaseq_align_sample_parallel_jobs", 2)) if stage in {"rnaseq_qc", "rnaseq_align"} else 1
    else:
        jobs = max(1, int(sample_parallel_jobs or 1))
    if sample_count:
        jobs = min(jobs, max(1, int(sample_count)))
    if threads_per_sample is None or int(threads_per_sample) < 1:
        threads = max(1, cpu_total // jobs)
    else:
        threads = max(1, int(threads_per_sample))
    if jobs * threads > cpu_total:
        threads = max(1, cpu_total // jobs)
    effective_cpu = max(1, jobs * threads)
    return {
        "total_cpu": effective_cpu,
        "project_cpu_limit": cpu_total,
        "sample_parallel_jobs": jobs,
        "threads_per_sample": threads,
    }


def _empty_state() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": _now(),
        "limits": {
            "total_cpu": _total_cpu(),
            "max_running_tasks": _max_running_tasks(),
            "hardware": detect_hardware(),
            "config": get_resource_config(),
        },
        "tasks": {},
    }


@contextmanager
def _locked_state():
    lock_file = _lock_path()
    state_file = _state_path()
    with lock_file.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            if state_file.exists():
                try:
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                except Exception:
                    state = _empty_state()
            else:
                state = _empty_state()
            if not isinstance(state, dict):
                state = _empty_state()
            state.setdefault("tasks", {})
            state["limits"] = {
                "total_cpu": _total_cpu(),
                "max_running_tasks": _max_running_tasks(),
                "hardware": detect_hardware(),
                "config": get_resource_config(),
            }
            _cleanup_stale_locked(state)
            yield state
            state["updated_at"] = _now()
            tmp = state_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(state_file)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _cleanup_stale_locked(state: dict[str, Any]) -> None:
    tasks = state.setdefault("tasks", {})
    for task_id, task in list(tasks.items()):
        status = str(task.get("status") or "").lower()
        if status in TERMINAL_STATES:
            continue
        if status == "running" and task.get("pid") and not _pid_alive(task.get("pid")):
            task["status"] = "failed"
            task["ended_at"] = task.get("ended_at") or _now()
            task["message"] = "worker process disappeared; resource lease released"
            task["cpu"] = 0
        elif status == "waiting_resource" and task.get("pid") and not _pid_alive(task.get("pid")):
            task["status"] = "cancelled"
            task["ended_at"] = task.get("ended_at") or _now()
            task["message"] = "waiting worker process disappeared"


def _usage_locked(state: dict[str, Any]) -> dict[str, Any]:
    used_cpu = 0
    running = 0
    waiting = 0
    by_class: dict[str, int] = {}
    for task in state.get("tasks", {}).values():
        status = str(task.get("status") or "").lower()
        if status == "running":
            running += 1
            cpu = int(task.get("cpu") or 0)
            used_cpu += cpu
            cls = str(task.get("resource_class") or "unknown")
            by_class[cls] = by_class.get(cls, 0) + cpu
        elif status == "waiting_resource":
            waiting += 1
    limits = state.get("limits", {})
    total_cpu = int(limits.get("total_cpu") or _total_cpu())
    return {
        "used_cpu": used_cpu,
        "available_cpu": max(0, total_cpu - used_cpu),
        "running_tasks": running,
        "waiting_tasks": waiting,
        "by_resource_class_cpu": by_class,
        "limits": limits,
    }


def record_task(
    *,
    task_id: str,
    stage: str,
    job_id: str,
    prefix: str = "",
    status: str = "queued",
    cpu: int = 0,
    queue: str = "",
    resource_class: str = "",
    concurrency_group: str = "",
    celery_task_id: str = "",
    message: str = "",
) -> dict[str, Any]:
    with _locked_state() as state:
        task = state["tasks"].setdefault(task_id, {})
        task.update(
            {
                "task_id": task_id,
                "stage": stage,
                "job_id": job_id,
                "prefix": prefix,
                "status": status,
                "cpu": int(cpu or 0) if status == "running" else 0,
                "requested_cpu": int(cpu or 0),
                "queue": queue,
                "resource_class": resource_class,
                "concurrency_group": concurrency_group,
                "celery_task_id": celery_task_id or task.get("celery_task_id", ""),
                "pid": os.getpid() if status in ACTIVE_STATES else task.get("pid"),
                "message": message,
                "updated_at": _now(),
            }
        )
        task.setdefault("created_at", _now())
        if status == "running":
            task["started_at"] = task.get("started_at") or _now()
        if status in TERMINAL_STATES:
            task["ended_at"] = task.get("ended_at") or _now()
            task["cpu"] = 0
        return {"task": dict(task), "usage": _usage_locked(state)}


def try_acquire(
    *,
    task_id: str,
    stage: str,
    job_id: str,
    prefix: str,
    cpu: int,
    queue: str = "",
    resource_class: str = "",
    concurrency_group: str = "",
    celery_task_id: str = "",
) -> dict[str, Any]:
    cpu = max(1, int(cpu or 1))
    with _locked_state() as state:
        tasks = state.setdefault("tasks", {})
        existing = tasks.get(task_id, {})
        if str(existing.get("status") or "").lower() == "running":
            return {"granted": True, "task": dict(existing), "usage": _usage_locked(state)}

        usage = _usage_locked(state)
        limits = state.get("limits", {})
        total_cpu = int(limits.get("total_cpu") or _total_cpu())
        max_tasks = int(limits.get("max_running_tasks") or _max_running_tasks())
        config = get_resource_config()
        project_limit_key = "rnaseq_project_max_cpu" if resource_class == "rnaseq" else "gene_family_project_max_cpu"
        project_limit = max(1, int(config.get(project_limit_key, total_cpu)))
        project_used_cpu = 0
        group_running = False
        for existing_task in tasks.values():
            if str(existing_task.get("status") or "").lower() != "running":
                continue
            if (
                concurrency_group
                and str(existing_task.get("concurrency_group") or "") == concurrency_group
                and str(existing_task.get("task_id") or "") != task_id
            ):
                group_running = True
            if str(existing_task.get("job_id") or "") != str(job_id):
                continue
            existing_class = str(existing_task.get("resource_class") or "")
            existing_key = "rnaseq_project_max_cpu" if existing_class == "rnaseq" else "gene_family_project_max_cpu"
            if existing_key == project_limit_key:
                project_used_cpu += int(existing_task.get("cpu") or 0)
        project_has_cpu = True
        # CPU token caps are disabled; max_running_tasks still prevents unbounded task fan-out.
        if usage["running_tasks"] < max_tasks and project_has_cpu and not group_running:
            task = tasks.setdefault(task_id, {})
            task.update(
                {
                    "task_id": task_id,
                    "stage": stage,
                    "job_id": job_id,
                    "prefix": prefix,
                    "status": "running",
                    "cpu": cpu,
                    "requested_cpu": cpu,
                    "queue": queue,
                    "resource_class": resource_class,
                    "concurrency_group": concurrency_group,
                    "celery_task_id": celery_task_id or task.get("celery_task_id", ""),
                    "pid": os.getpid(),
                    "started_at": task.get("started_at") or _now(),
                    "updated_at": _now(),
                    "message": "resource lease granted",
                }
            )
            return {"granted": True, "task": dict(task), "usage": _usage_locked(state)}

        task = tasks.setdefault(task_id, {})
        task.update(
            {
                "task_id": task_id,
                "stage": stage,
                "job_id": job_id,
                "prefix": prefix,
                "status": "waiting_resource",
                "cpu": 0,
                "requested_cpu": cpu,
                "queue": queue,
                "resource_class": resource_class,
                "concurrency_group": concurrency_group,
                "celery_task_id": celery_task_id or task.get("celery_task_id", ""),
                "pid": os.getpid(),
                "updated_at": _now(),
                "message": f"waiting for {cpu} CPU token(s)",
            }
        )
        usage = _usage_locked(state)
        return {
            "granted": False,
            "task": dict(task),
            "usage": usage,
            "reason": (
                f"等待互斥资源组 {concurrency_group}（容量 1）"
                if group_running else (
                    f"资源不足：需要 CPU {cpu}，当前可用 CPU {usage['available_cpu']}，"
                    f"运行任务 {usage['running_tasks']}/{max_tasks}，"
                    f"项目已用 CPU {project_used_cpu}/{project_limit}"
                )
            ),
        }


def release(task_id: str, status: str = "completed", message: str = "") -> dict[str, Any]:
    with _locked_state() as state:
        task = state.setdefault("tasks", {}).setdefault(task_id, {"task_id": task_id})
        task["status"] = status
        task["cpu"] = 0
        task["ended_at"] = task.get("ended_at") or _now()
        task["updated_at"] = _now()
        if message:
            task["message"] = message
        return {"task": dict(task), "usage": _usage_locked(state)}


def snapshot(include_terminal: bool = False) -> dict[str, Any]:
    with _locked_state() as state:
        tasks = []
        for task in state.get("tasks", {}).values():
            status = str(task.get("status") or "").lower()
            if include_terminal or status not in TERMINAL_STATES:
                tasks.append(dict(task))
        tasks.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return {
            "state_path": str(_state_path()),
            "updated_at": state.get("updated_at"),
            "limits": state.get("limits", {}),
            "usage": _usage_locked(state),
            "tasks": tasks,
        }


def get_task(task_id: str) -> dict[str, Any] | None:
    with _locked_state() as state:
        task = state.get("tasks", {}).get(task_id)
        if not task:
            return None
        return {"task": dict(task), "usage": _usage_locked(state)}


@dataclass
class ResourceLease:
    task_id: str
    stage: str
    job_id: str
    prefix: str
    cpu: int
    queue: str = ""
    resource_class: str = ""
    concurrency_group: str = ""
    celery_task_id: str = ""
    poll_seconds: float = 5.0

    def wait(self, update_state=None) -> dict[str, Any]:
        while True:
            result = try_acquire(
                task_id=self.task_id,
                stage=self.stage,
                job_id=self.job_id,
                prefix=self.prefix,
                cpu=self.cpu,
                queue=self.queue,
                resource_class=self.resource_class,
                concurrency_group=self.concurrency_group,
                celery_task_id=self.celery_task_id,
            )
            if result.get("granted"):
                return result
            if update_state:
                update_state(result)
            time.sleep(self.poll_seconds)

    def release(self, status: str = "completed", message: str = "") -> dict[str, Any]:
        return release(self.task_id, status=status, message=message)
