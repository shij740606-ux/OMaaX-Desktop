"""轻量版拓展分析的"上游样本继承"发现。

从 workspace/<job>/extension_runs/<ext>/runtime_*/outputs/ 枚举同 job 内其他轻量拓展的
成功运行产物，为运行向导的空输入槽提供保守的候选填充。

- 只读、不抛异常：任何解析失败都降级为无候选；
- 只认轻量拓展的 runtime_* 运行目录（标准拓展的 run_* 无 runtime_execution_result.json，
  天然被成功判定排除）；
- 匹配保守：basename 精确匹配优先，role 关键词必须唯一命中才采纳，否则留空让用户手填。

对外入口：resolve_upstream_slots(workspace_root, job_id, contract_inputs, ...)。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Any, Mapping

MAX_UPSTREAM_RUNS = 100

_DIRECTORY_HINT_TOKENS = ("directory", " dir", "folder", "bundle")

_ROLE_STOPWORDS = {
    "sample", "samples", "manifest", "with", "file", "files", "path", "paths",
    "tab", "separated", "containing", "accession", "accessions", "genome",
    "gff3", "protein", "cds", "peptide", "table", "list", "lists", "data",
    "input", "output", "analysis", "columns", "column", "including", "that",
    "from", "for", "and", "the",
}


@dataclass(frozen=True)
class UpstreamCandidate:
    path: Path          # 词法路径（workspace/<job>/extension_runs/... 下，不 resolve）
    relative: str       # 相对 outputs 目录的 posix 路径
    source_extension_id: str
    run_id: str
    is_dir: bool
    run_time: float     # run 目录 mtime，排序键


def _run_result(run_dir: Path) -> dict[str, Any] | None:
    try:
        path = run_dir / "runtime_execution_result.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _run_success(run_dir: Path) -> bool:
    result = _run_result(run_dir)
    return bool(result) and result.get("success") is True


def _run_time(run_dir: Path) -> float:
    try:
        return run_dir.stat().st_mtime
    except OSError:
        return 0.0


def collect_job_upstream_outputs(
    workspace_root: Path, job_id: str, *, exclude_extension_id: str = ""
) -> list[UpstreamCandidate]:
    """枚举同 job 下其他轻量拓展的成功运行产物（outputs/**）。任何失败返回空列表。"""
    try:
        extension_runs = Path(workspace_root) / job_id / "extension_runs"
        if not extension_runs.is_dir():
            return []
        pool: list[UpstreamCandidate] = []
        for ext_dir in sorted(extension_runs.iterdir()):
            if not ext_dir.is_dir() or ext_dir.name == exclude_extension_id:
                continue
            for run_dir in sorted(ext_dir.glob("runtime_*")):
                if len(pool) >= MAX_UPSTREAM_RUNS:
                    return pool
                if not run_dir.is_dir() or not _run_success(run_dir):
                    continue
                outputs = run_dir / "outputs"
                if not outputs.is_dir():
                    continue
                run_time = _run_time(run_dir)
                for path in outputs.rglob("*"):
                    try:
                        if path.is_symlink():
                            continue
                        if path.is_file() or path.is_dir():
                            pool.append(
                                UpstreamCandidate(
                                    path=path,
                                    relative=path.relative_to(outputs).as_posix(),
                                    source_extension_id=ext_dir.name,
                                    run_id=run_dir.name,
                                    is_dir=path.is_dir(),
                                    run_time=run_time,
                                )
                            )
                    except OSError:
                        continue
        return pool
    except OSError:
        return []


def _slot_directory_like(slot: Mapping[str, Any]) -> bool:
    text = str(slot.get("path") or "")
    if text.endswith("/"):
        return True
    for field in ("role", "description"):
        value = str(slot.get(field) or "").lower()
        if any(token in value for token in _DIRECTORY_HINT_TOKENS):
            return True
    return False


def _role_tokens(role: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-zA-Z0-9]{4,}", role)
        if token not in _ROLE_STOPWORDS
    ]


def _match_slot(
    slot: Mapping[str, Any],
    pool: list[UpstreamCandidate],
    patchable_paths: set[str] | None,
) -> list[UpstreamCandidate]:
    """保守匹配一个输入槽：命中即停，匹配不到返回空列表（不猜、不报错）。

    目录候选只进目录槽（_slot_directory_like 判定）；文件槽只认文件候选。
    精确同名匹配永远安全（不改名就不触发脚本替换），始终生效；
    role 模糊匹配会把上游产物改成槽位名，只对可补丁（patch_targets 里声明过
    path:<槽位path>）的槽位启用，否则留空让用户手填同名文件。
    """
    name = Path(str(slot.get("path") or "")).name.lower()
    if not name:
        return []
    wants_dir = _slot_directory_like(slot)
    typed = [candidate for candidate in pool if candidate.is_dir is wants_dir]
    exact = [candidate for candidate in typed if candidate.path.name.lower() == name]
    if exact:
        return exact
    if patchable_paths is not None and str(slot.get("path") or "") not in patchable_paths:
        return []
    tokens = _role_tokens(str(slot.get("role") or ""))
    if not tokens:
        return []
    hits = typed
    for token in tokens:
        hits = [candidate for candidate in hits if token in candidate.path.name.lower()]
    return hits


def _candidate_rank(candidate: UpstreamCandidate) -> tuple[float, int, str]:
    return (-candidate.run_time, len(Path(candidate.relative).parts), candidate.relative.lower())


def find_upstream_candidates(
    workspace_root: Path,
    job_id: str,
    contract_inputs: list[Mapping[str, Any]],
    *,
    exclude_extension_id: str = "",
    patchable_paths: set[str] | None = None,
) -> dict[str, list[UpstreamCandidate]]:
    """为每个可替换输入槽收集上游候选（key = 契约 path）。

    patchable_paths 为契约 patch_targets 中 path:<槽位path> 的集合；传入时
    role 模糊匹配只对可补丁槽位启用（见 _match_slot）。
    """
    pool = collect_job_upstream_outputs(
        workspace_root, job_id, exclude_extension_id=exclude_extension_id
    )
    result: dict[str, list[UpstreamCandidate]] = {}
    for item in contract_inputs:
        if not isinstance(item, Mapping) or not item.get("replaceable"):
            continue
        path = str(item.get("path") or "")
        if not path:
            continue
        matched = _match_slot(item, pool, patchable_paths)
        if matched:
            result[path] = matched
    return result


def select_upstream_for_slots(
    candidates: dict[str, list[UpstreamCandidate]],
    contract_inputs: list[Mapping[str, Any]],
) -> dict[str, UpstreamCandidate]:
    """每槽收敛一个候选：最新 run 优先、outputs 相对路径最短；每个候选只能被一个槽占用。"""
    selected: dict[str, UpstreamCandidate] = {}
    used: set[Path] = set()
    for item in contract_inputs:
        if not isinstance(item, Mapping) or not item.get("replaceable"):
            continue
        path = str(item.get("path") or "")
        if not path or path in selected:
            continue
        for candidate in sorted(candidates.get(path) or [], key=_candidate_rank):
            if candidate.path in used:
                continue
            selected[path] = candidate
            used.add(candidate.path)
            break
    return selected


def resolve_upstream_slots(
    workspace_root: Path,
    job_id: str,
    contract_inputs: list[Mapping[str, Any]],
    *,
    exclude_extension_id: str = "",
    patchable_paths: set[str] | None = None,
) -> dict[str, UpstreamCandidate]:
    """一步：枚举上游成功产物 → 槽位保守匹配 → 收敛为每槽一个候选。"""
    return select_upstream_for_slots(
        find_upstream_candidates(
            workspace_root,
            job_id,
            contract_inputs,
            exclude_extension_id=exclude_extension_id,
            patchable_paths=patchable_paths,
        ),
        contract_inputs,
    )
