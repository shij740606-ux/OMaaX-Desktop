import json
import sqlite3
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from gfa_core.mas.memory.base import BaseMemory, SearchResult
from gfa_core.project_paths import MEMORY_ROOT


@dataclass
class AnalysisCase:
    case_id: str
    user_id: str | None = None
    gene_family: str | None = None
    species: str | None = None
    workflow: list[dict] = field(default_factory=list)
    results: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    feedback: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class CaseMemory(BaseMemory):
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.environ.get("BBAV5_CASE_DB") or MEMORY_ROOT / "cases.db"
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        print(f"[CaseMemory] SQLite storage: {self._db_path}")

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    gene_family TEXT,
                    species TEXT,
                    workflow TEXT,
                    results TEXT,
                    status TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    completed_at TEXT,
                    feedback TEXT,
                    metadata TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_id ON cases(user_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_gene_family ON cases(gene_family)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status ON cases(status)
            """)

    async def store(self, key: str, value: Any, ttl: int | None = None) -> None:
        if isinstance(value, AnalysisCase):
            case = value
        else:
            case = AnalysisCase(case_id=key, **value)

        case.updated_at = datetime.now()

        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO cases 
                (case_id, user_id, gene_family, species, workflow, results, status,
                 created_at, updated_at, completed_at, feedback, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                case.case_id,
                case.user_id,
                case.gene_family,
                case.species,
                json.dumps(case.workflow),
                json.dumps(case.results),
                case.status,
                case.created_at.isoformat(),
                case.updated_at.isoformat(),
                case.completed_at.isoformat() if case.completed_at else None,
                json.dumps(case.feedback),
                json.dumps(case.metadata),
            ))

    async def retrieve(self, key: str) -> Any | None:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM cases WHERE case_id = ?", (key,)
            )
            row = cursor.fetchone()

            if row is None:
                return None

            return self._row_to_case(row)

    async def delete(self, key: str) -> bool:
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM cases WHERE case_id = ?", (key,)
            )
            return cursor.rowcount > 0

    async def exists(self, key: str) -> bool:
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                "SELECT 1 FROM cases WHERE case_id = ?", (key,)
            )
            return cursor.fetchone() is not None

    async def clear(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM cases")

    def _row_to_case(self, row: sqlite3.Row) -> AnalysisCase:
        return AnalysisCase(
            case_id=row["case_id"],
            user_id=row["user_id"],
            gene_family=row["gene_family"],
            species=row["species"],
            workflow=json.loads(row["workflow"]) if row["workflow"] else [],
            results=json.loads(row["results"]) if row["results"] else {},
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            feedback=json.loads(row["feedback"]) if row["feedback"] else {},
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )

    async def list_by_user(self, user_id: str) -> list[AnalysisCase]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM cases WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,)
            )
            return [self._row_to_case(row) for row in cursor.fetchall()]

    async def list_by_gene_family(self, gene_family: str) -> list[AnalysisCase]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM cases WHERE gene_family = ? ORDER BY created_at DESC",
                (gene_family,)
            )
            return [self._row_to_case(row) for row in cursor.fetchall()]

    async def list_by_status(self, status: str) -> list[AnalysisCase]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM cases WHERE status = ? ORDER BY created_at DESC",
                (status,)
            )
            return [self._row_to_case(row) for row in cursor.fetchall()]

    async def update_status(self, case_id: str, status: str) -> None:
        case = await self.retrieve(case_id)
        if case:
            case.status = status
            if status == "completed":
                case.completed_at = datetime.now()
            await self.store(case_id, case)

    async def add_feedback(self, case_id: str, feedback: dict[str, Any]) -> None:
        case = await self.retrieve(case_id)
        if case:
            case.feedback.update(feedback)
            await self.store(case_id, case)
