"""services/rebuttal_store.py —— Rebuttal Phase 1 任务持久化（SQLite，自含连接）。

设计（2026-10-12 定稿）：
- 表 rebuttal_jobs 落在 review_service/review.db（与 journals 同库不同表）；
  本模块自开短连接（低频 CRUD，WAL 下足够），不改既有 app/db.py。
- Job/Comment 全量以 JSON 存 reviewers_json 列（Phase 1 规模 ≤ 数十条，
  整读整写最简单且可测；并发写靠 updated_at + 单进程串行）。
- human_ack 不信前端置位：confirm 端点改完 comment 后由本模块重算——
  所有 comment.human_confirmed 全 true 才置 1（决策 4）。
- 不做 TTL 自动清理（决策 5）；DELETE 端点手动删。
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
CREATE TABLE IF NOT EXISTS rebuttal_jobs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  source_kind TEXT,
  filename TEXT,
  raw_text TEXT,
  template TEXT DEFAULT 'generic',
  reviewers_json TEXT NOT NULL,
  letter_md TEXT,
  validation_json TEXT,
  human_ack INTEGER DEFAULT 0,
  created_at TEXT,
  updated_at TEXT
);
"""


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
    return {
        "id": row["id"],
        "status": row["status"],
        "source": {
            "kind": row["source_kind"] or "text",
            "filename": row["filename"],
            "raw_text": row["raw_text"] or "",
        },
        "template": row["template"] or "generic",
        "reviewers": json.loads(row["reviewers_json"] or "[]"),
        "letter_md": row["letter_md"],
        "validation": json.loads(row["validation_json"]) if row["validation_json"] else None,
        "human_ack": bool(row["human_ack"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def insert_job(job: dict[str, Any]) -> None:
    """写入完整 job（reviewers 等结构由调用方构造好）。"""
    now = job.get("created_at") or _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO rebuttal_jobs (id, status, source_kind, filename, raw_text,"
            " template, reviewers_json, letter_md, validation_json, human_ack,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job["id"], job.get("status") or "ready",
                (job.get("source") or {}).get("kind"),
                (job.get("source") or {}).get("filename"),
                (job.get("source") or {}).get("raw_text"),
                job.get("template") or "generic",
                json.dumps(job.get("reviewers") or [], ensure_ascii=False),
                job.get("letter_md"),
                json.dumps(job.get("validation"), ensure_ascii=False)
                if job.get("validation") is not None else None,
                1 if job.get("human_ack") else 0,
                now, now,
            ),
        )


def get_job(job_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM rebuttal_jobs WHERE id=?", (job_id,)
        ).fetchone()
    return _row_to_job(row) if row else None


def update_job(job_id: str, *, reviewers: list[dict[str, Any]] | None = None,
               letter_md: str | None = None, status: str | None = None) -> dict[str, Any] | None:
    """局部更新；reviewers 变更时重算 human_ack（全勾才 true）。"""
    job = get_job(job_id)
    if job is None:
        return None
    if reviewers is not None:
        job["reviewers"] = reviewers
    if letter_md is not None:
        job["letter_md"] = letter_md
    if status is not None:
        job["status"] = status
    job["human_ack"] = all_comments_confirmed(job["reviewers"])
    job["updated_at"] = _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE rebuttal_jobs SET reviewers_json=?, letter_md=?, status=?,"
            " human_ack=?, updated_at=? WHERE id=?",
            (
                json.dumps(job["reviewers"], ensure_ascii=False),
                job.get("letter_md"),
                job["status"],
                1 if job["human_ack"] else 0,
                job["updated_at"],
                job_id,
            ),
        )
    return job


def delete_job(job_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM rebuttal_jobs WHERE id=?", (job_id,))
    return cur.rowcount > 0


def all_comments(reviewers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in reviewers or []:
        out.extend(r.get("comments") or [])
    return out


def all_comments_confirmed(reviewers: list[dict[str, Any]]) -> bool:
    comments = all_comments(reviewers)
    # 空 job（未解析出任何 comment）不置位 human_ack，避免"全勾"平凡真
    if not comments:
        return False
    return all(bool(c.get("human_confirmed")) for c in comments)
