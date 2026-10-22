"""services/ppt_store.py —— P5 科研汇报 job 持久化（SQLite，自含连接）。

模式同 rebuttal_store：表 ppt_jobs 落 review_service/review.db（同库不同表），
自开短连接 + WAL；pages/warnings 全量 JSON 列整读整写（单进程串行足够）。
raw_text 只落库不回 API（150K 正文进 job dict 会把 GET 撑爆；重生成场景
才需要它，经 DB 直读）。DDL 按 2026-10-14 决策 3 定稿。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ppt_jobs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  source_kind TEXT,
  filename TEXT,
  raw_text TEXT,
  scenario TEXT,
  minutes INTEGER,
  style_id TEXT,
  pages_json TEXT,
  pptx_path TEXT,
  warnings_json TEXT,
  created_at TEXT,
  updated_at TEXT
);
"""

_STATUS_VALUES = ("outline_ready", "building", "done", "error")
_JSON_COLS = {"pages": "pages_json", "warnings": "warnings_json"}
_SCALAR_COLS = ("status", "scenario", "minutes", "style_id", "pptx_path",
                "source_kind", "filename")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:12]


@contextmanager
def _connect():
    db_path: Path = get_settings().db_path_abs
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def ensure_table() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    status = row["status"]
    pptx_path = row["pptx_path"]
    return {
        "id": row["id"],
        "status": status,
        "source": {
            "kind": row["source_kind"] or "text",
            "filename": row["filename"],
            "text_chars": len(row["raw_text"] or ""),
        },
        "scenario": row["scenario"],
        "minutes": row["minutes"],
        "style_id": row["style_id"],
        "pages": json.loads(row["pages_json"]) if row["pages_json"] else [],
        "warnings": json.loads(row["warnings_json"]) if row["warnings_json"] else [],
        "pptx_ready": bool(pptx_path) and Path(pptx_path).exists(),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def insert_job(job: dict[str, Any], raw_text: str = "") -> None:
    now = job.get("created_at") or _now()
    status = job.get("status") or "outline_ready"
    if status not in _STATUS_VALUES:
        raise ValueError(f"非法 status: {status}")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO ppt_jobs (id, status, source_kind, filename, raw_text,"
            " scenario, minutes, style_id, pages_json, pptx_path, warnings_json,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job["id"], status,
                (job.get("source") or {}).get("kind"),
                (job.get("source") or {}).get("filename"),
                raw_text,
                job.get("scenario"), job.get("minutes"), job.get("style_id"),
                json.dumps(job.get("pages") or [], ensure_ascii=False),
                job.get("pptx_path"),
                json.dumps(job.get("warnings") or [], ensure_ascii=False),
                now, now,
            ),
        )


def get_job(job_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM ppt_jobs WHERE id=?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def update_job(job_id: str, **fields: Any) -> dict[str, Any] | None:
    """局部更新（省略的列不动；pages/warnings 传对象自动序列化）。"""
    sets: list[str] = []
    vals: list[Any] = []
    for key, value in fields.items():
        if key in _JSON_COLS:
            sets.append(f"{_JSON_COLS[key]}=?")
            vals.append(json.dumps(value, ensure_ascii=False) if value is not None else None)
        elif key in _SCALAR_COLS:
            if key == "status" and value not in _STATUS_VALUES:
                raise ValueError(f"非法 status: {value}")
            sets.append(f"{key}=?")
            vals.append(value)
        else:
            raise TypeError(f"update_job 不支持的字段: {key}")
    if not sets:
        return get_job(job_id)
    sets.append("updated_at=?")
    vals.extend([_now(), job_id])
    with _connect() as conn:
        cur = conn.execute(f"UPDATE ppt_jobs SET {', '.join(sets)} WHERE id=?", vals)
        if cur.rowcount == 0:
            return None
    return get_job(job_id)


def delete_job(job_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM ppt_jobs WHERE id=?", (job_id,))
    return cur.rowcount > 0


def get_raw_text(job_id: str) -> str:
    """raw_text 只在 evidence 重验时直读（不进 API job dict，避免 GET 撑爆）。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT raw_text FROM ppt_jobs WHERE id=?", (job_id,)
        ).fetchone()
    return (row["raw_text"] or "") if row else ""
