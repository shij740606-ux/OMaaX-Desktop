"""app/db.py —— review 服务独立 SQLite（aiosqlite + WAL，无 ORM）：journals 表。

- 单连接 + asyncio 写锁（模式对齐 paper_writer_service/bioflow，但表结构与种子
  逻辑为本模块自研）；路径默认 review_service/review.db（REVIEW_DB_PATH 覆盖）。
- 种子：data/journals/journals.json → journals 表（幂等 upsert，vstore_doc_id 列
  永不被种子覆盖——该列记录 scope_desc 在 vstore 8094 的 document_id）。
- 只在 review_service/ 内使用，不触碰 BFF/生产服务数据库。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

from .config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS journals (
    id                 TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    scope_desc         TEXT NOT NULL,
    impact_factor      REAL,
    review_time_weeks  REAL,
    acceptance_rate    REAL,
    submission_deadline TEXT,
    official_url       TEXT,
    last_updated       TEXT NOT NULL,
    vstore_doc_id      TEXT
);
"""

# 种子 upsert 列（刻意不含 vstore_doc_id：种子刷新不得清掉向量库映射）
_SEED_COLS = (
    "id",
    "name",
    "scope_desc",
    "impact_factor",
    "review_time_weeks",
    "acceptance_rate",
    "submission_deadline",
    "official_url",
    "last_updated",
)
_UPSERT_SQL = (
    "INSERT INTO journals (" + ", ".join(_SEED_COLS) + ") VALUES ("
    + ", ".join("?" for _ in _SEED_COLS) + ") "
    + "ON CONFLICT(id) DO UPDATE SET "
    + ", ".join(f"{c}=excluded.{c}" for c in _SEED_COLS if c != "id")
)


class SeedError(Exception):
    """种子文件缺失/损坏（journals.json 不可读）。"""


class Database:
    """aiosqlite 单连接封装（WAL；写串行化）。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path: Path | None = Path(path) if path is not None else None
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        if self._path is None:
            self._path = get_settings().db_path_abs
        return self._path

    def set_path(self, path: str | Path) -> None:
        """测试注入：在进入 TestClient lifespan 之前预置库路径（须尚未 connect）。"""
        if self._conn is not None:
            raise RuntimeError("Database 已连接，无法再改路径（先 close_db()）")
        self._path = Path(path)

    @property
    def connected(self) -> bool:
        return self._conn is not None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database 未初始化：先 await init_db()")
        return self._conn

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        async with self.conn.execute(sql, tuple(params)) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        async with self.conn.execute(sql, tuple(params)) as cur:
            row = await cur.fetchone()
        return dict(row) if row is not None else None

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        async with self._lock:
            cur = await self.conn.execute(sql, tuple(params))
            await self._conn.commit()  # type: ignore[union-attr]
            return cur.lastrowid or 0

    async def executemany(self, sql: str, seq: list[tuple[Any, ...]]) -> None:
        async with self._lock:
            await self.conn.executemany(sql, seq)
            await self._conn.commit()  # type: ignore[union-attr]


db = Database()


async def init_db(path: str | Path | None = None) -> None:
    """幂等初始化：已连接则跳过（测试可在 lifespan 前 set_path 注入路径）。"""
    if path is not None:
        db.set_path(path)
    if db.connected:
        return
    await db.connect()


async def close_db() -> None:
    await db.close()


def set_path(path: str | Path) -> None:
    """同步注入库路径（测试 fixture 用；须在 init_db 之前调用）。"""
    db.set_path(path)


async def ensure_journals_seeded() -> int:
    """装载种子期刊（幂等）。返回 journals 表行数。"""
    row = await db.fetchone("SELECT COUNT(*) AS c FROM journals")
    if row and int(row["c"]) > 0:
        return int(row["c"])

    seed_file = get_settings().journals_seed_abs
    try:
        payload = json.loads(Path(seed_file).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SeedError(f"期刊种子文件不存在：{seed_file}") from exc
    except (OSError, ValueError) as exc:
        raise SeedError(f"期刊种子文件不可读：{exc}") from exc

    journals = payload.get("journals") if isinstance(payload, dict) else None
    if not isinstance(journals, list) or not journals:
        raise SeedError(f"期刊种子文件缺少 journals 数组：{seed_file}")

    rows: list[tuple[Any, ...]] = []
    for item in journals:
        if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
            continue
        rows.append(tuple(item.get(c) for c in _SEED_COLS))
    if not rows:
        raise SeedError(f"期刊种子文件无有效条目：{seed_file}")

    await db.executemany(_UPSERT_SQL, rows)
    return len(rows)


async def list_journals() -> list[dict[str, Any]]:
    """全部期刊（impact_factor 降序，NULL 靠后）。"""
    return await db.fetchall(
        "SELECT * FROM journals ORDER BY impact_factor DESC, name ASC"
    )


async def get_journal(journal_id: str) -> dict[str, Any] | None:
    return await db.fetchone("SELECT * FROM journals WHERE id = ?", (journal_id,))


async def set_vstore_doc_id(journal_id: str, doc_id: str) -> None:
    """记录 scope_desc 在 vstore 的 document_id（向量白名单检索用）。"""
    await db.execute(
        "UPDATE journals SET vstore_doc_id = ? WHERE id = ?", (doc_id, journal_id)
    )
