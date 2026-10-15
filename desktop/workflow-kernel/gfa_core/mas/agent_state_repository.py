"""SQLite bootstrap for future transactional agent-state persistence."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from uuid import UUID

from pydantic import ValidationError

from gfa_core.mas.workflow_models import GoalLedger


_SQLITE_BUSY_TIMEOUT_MAX_MS = 2_147_483_647


class AgentStateRepositoryError(RuntimeError):
    """Raised when repository configuration or migration state is invalid."""


class AgentStateConflictError(AgentStateRepositoryError):
    """Raised when a transactional state revision or key conflicts."""


@dataclass(frozen=True)
class _Migration:
    revision: int
    name: str
    statements: tuple[str, ...] = ()


_MIGRATIONS = (
    _Migration(revision=1, name="bootstrap_schema"),
    _Migration(
        revision=2,
        name="conversation_message_schema",
        statements=(
            """
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY
                    CHECK (
                        length(conversation_id) = 36
                        AND substr(conversation_id, 9, 1) = '-'
                        AND substr(conversation_id, 14, 1) = '-'
                        AND substr(conversation_id, 19, 1) = '-'
                        AND substr(conversation_id, 24, 1) = '-'
                        AND length(replace(conversation_id, '-', '')) = 32
                        AND replace(conversation_id, '-', '')
                            NOT GLOB '*[^0-9a-f]*'
                    ),
                principal_id TEXT NOT NULL,
                job_id TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL DEFAULT 0
                    CHECK (
                        typeof(revision) = 'integer'
                        AND revision >= 0
                    ),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE conversation_messages (
                message_id TEXT PRIMARY KEY
                    CHECK (
                        length(message_id) = 36
                        AND substr(message_id, 9, 1) = '-'
                        AND substr(message_id, 14, 1) = '-'
                        AND substr(message_id, 19, 1) = '-'
                        AND substr(message_id, 24, 1) = '-'
                        AND length(replace(message_id, '-', '')) = 32
                        AND replace(message_id, '-', '')
                            NOT GLOB '*[^0-9a-f]*'
                    ),
                conversation_id TEXT NOT NULL,
                revision INTEGER NOT NULL
                    CHECK (
                        typeof(revision) = 'integer'
                        AND revision > 0
                    ),
                role TEXT NOT NULL
                    CHECK (role IN ('user', 'assistant', 'system')),
                content TEXT NOT NULL CHECK (content <> ''),
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(conversation_id)
                    ON DELETE CASCADE,
                UNIQUE (conversation_id, revision)
            )
            """,
        ),
    ),
    _Migration(
        revision=3,
        name="goal_ledger_schema",
        statements=(
            """
            CREATE TABLE conversation_goal_ledgers (
                conversation_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL
                    CHECK (
                        typeof(revision) = 'integer'
                        AND revision >= 0
                    ),
                payload_json TEXT NOT NULL
                    CHECK (
                        typeof(payload_json) = 'text'
                        AND payload_json <> ''
                    ),
                payload_sha256 TEXT NOT NULL
                    CHECK (
                        typeof(payload_sha256) = 'text'
                        AND length(payload_sha256) = 64
                        AND payload_sha256 NOT GLOB '*[^0-9a-f]*'
                    ),
                updated_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(conversation_id)
                    ON DELETE CASCADE
            )
            """,
        ),
    ),
    _Migration(
        revision=4,
        name="conversation_sidebar_state",
        statements=(
            """
            ALTER TABLE conversations
            ADD COLUMN title TEXT NOT NULL DEFAULT ''
            """,
            """
            ALTER TABLE conversations
            ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'archived', 'deleted'))
            """,
        ),
    ),
)


@dataclass(frozen=True)
class ConversationRecord:
    conversation_id: str
    principal_id: str
    job_id: str
    revision: int
    created_at: str
    updated_at: str
    title: str = ""
    status: str = "active"


@dataclass(frozen=True)
class MessageRecord:
    message_id: str
    conversation_id: str
    revision: int
    role: str
    content: str
    created_at: str


_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def require_canonical_uuid_text(value: object) -> str:
    if type(value) is not str:
        raise ValueError("identifier must be canonical UUID text")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise ValueError("identifier must be canonical UUID text") from None
    if str(parsed) != value:
        raise ValueError("identifier must be canonical UUID text")
    return value


def _require_identity(value: object, field_name: str, *, optional: bool = False) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a safe identity")
    if optional and value == "":
        return value
    if value != value.strip() or not _IDENTITY_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a safe identity")
    return value


def _canonical_goal_ledger(ledger: GoalLedger) -> tuple[str, str]:
    payload = json.dumps(
        ledger.model_dump(mode="json", round_trip=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_migration_plan() -> None:
    revisions = [migration.revision for migration in _MIGRATIONS]
    if revisions != list(range(1, len(_MIGRATIONS) + 1)):
        raise AgentStateRepositoryError(
            "database migration revisions must be contiguous from 1"
        )


class SQLiteAgentStateRepository:
    def __init__(
        self,
        database_path: Path,
        *,
        busy_timeout_ms: int = 5000,
    ) -> None:
        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(busy_timeout_ms, int)
            or busy_timeout_ms <= 0
            or busy_timeout_ms > _SQLITE_BUSY_TIMEOUT_MAX_MS
        ):
            raise ValueError(
                "busy_timeout_ms must be a positive SQLite-compatible integer"
            )
        self.database_path = Path(database_path)
        self.busy_timeout_ms = busy_timeout_ms

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            connection.execute("PRAGMA journal_mode = WAL")
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        _validate_migration_plan()
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_state_schema_migrations (
                        revision INTEGER PRIMARY KEY CHECK (revision > 0),
                        name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
                        applied_at TEXT NOT NULL
                    )
                    """
                )
                applied = connection.execute(
                    """
                    SELECT revision, name
                    FROM agent_state_schema_migrations
                    ORDER BY revision
                    """
                ).fetchall()
                expected_prefix = [
                    (migration.revision, migration.name)
                    for migration in _MIGRATIONS[: len(applied)]
                ]
                if applied != expected_prefix:
                    raise AgentStateRepositoryError(
                        "database migration history is not a supported prefix"
                    )
                for migration in _MIGRATIONS[len(applied) :]:
                    for statement in migration.statements:
                        connection.execute(statement)
                    connection.execute(
                        """
                        INSERT INTO agent_state_schema_migrations (
                            revision,
                            name,
                            applied_at
                        )
                        VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                        """,
                        (migration.revision, migration.name),
                    )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def create_conversation(
        self,
        *,
        conversation_id: str,
        principal_id: str,
        job_id: str = "",
        title: str = "",
    ) -> ConversationRecord:
        conversation_id = require_canonical_uuid_text(conversation_id)
        principal_id = _require_identity(principal_id, "principal_id")
        job_id = _require_identity(job_id, "job_id", optional=True)
        if type(title) is not str or len(title) > 200:
            raise ValueError("title must be text up to 200 characters")
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO conversations (
                        conversation_id,
                        principal_id,
                        job_id,
                        revision,
                        created_at,
                        updated_at,
                        title,
                        status
                    )
                    VALUES (
                        ?,
                        ?,
                        ?,
                        0,
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                        ?,
                        'active'
                    )
                    """,
                    (conversation_id, principal_id, job_id, title),
                )
                record = self._get_conversation(connection, conversation_id)
                connection.execute("COMMIT")
            except sqlite3.IntegrityError:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise AgentStateConflictError(
                    "conversation identity conflicts"
                ) from None
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        assert record is not None
        return record

    def get_conversation(
        self,
        conversation_id: str,
    ) -> ConversationRecord | None:
        conversation_id = require_canonical_uuid_text(conversation_id)
        with self.connection() as connection:
            return self._get_conversation(connection, conversation_id)

    def list_conversations(
        self,
        *,
        principal_id: str,
        status: str = "active",
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ConversationRecord, ...]:
        principal_id = _require_identity(principal_id, "principal_id")
        if status not in {"active", "archived", "deleted"}:
            raise ValueError("status must be active, archived, or deleted")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
            or isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
        ):
            raise ValueError("invalid conversation list pagination")
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    conversation_id,
                    principal_id,
                    job_id,
                    revision,
                    created_at,
                    updated_at,
                    title,
                    status
                FROM conversations
                WHERE principal_id = ? AND status = ?
                ORDER BY updated_at DESC, conversation_id
                LIMIT ? OFFSET ?
                """,
                (principal_id, status, limit, offset),
            ).fetchall()
        return tuple(ConversationRecord(*row) for row in rows)

    def update_conversation(
        self,
        *,
        conversation_id: str,
        principal_id: str,
        title: str | None = None,
        job_id: str | None = None,
        status: str | None = None,
    ) -> ConversationRecord:
        conversation_id = require_canonical_uuid_text(conversation_id)
        principal_id = _require_identity(principal_id, "principal_id")
        if title is not None and (type(title) is not str or not title or len(title) > 200):
            raise ValueError("title must be nonempty text up to 200 characters")
        if job_id is not None:
            job_id = _require_identity(job_id, "job_id", optional=True)
        if status is not None and status not in {"active", "archived", "deleted"}:
            raise ValueError("status must be active, archived, or deleted")
        if title is None and job_id is None and status is None:
            raise ValueError("at least one conversation field must be updated")
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE conversations
                    SET
                        title = CASE WHEN ? IS NULL THEN title ELSE ? END,
                        job_id = CASE WHEN ? IS NULL THEN job_id ELSE ? END,
                        status = CASE WHEN ? IS NULL THEN status ELSE ? END,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE conversation_id = ? AND principal_id = ?
                    """,
                    (
                        title,
                        title,
                        job_id,
                        job_id,
                        status,
                        status,
                        conversation_id,
                        principal_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise AgentStateConflictError(
                        "conversation is missing or principal does not own it"
                    )
                record = self._get_conversation(connection, conversation_id)
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        assert record is not None
        return record

    def append_message(
        self,
        *,
        conversation_id: str,
        message_id: str,
        role: str,
        content: str,
        expected_revision: int,
    ) -> MessageRecord:
        conversation_id = require_canonical_uuid_text(conversation_id)
        message_id = require_canonical_uuid_text(message_id)
        if role not in {"user", "assistant", "system"}:
            raise ValueError("role must be user, assistant, or system")
        if type(content) is not str or not content:
            raise ValueError("content must be nonempty verbatim text")
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ValueError("expected_revision must be a nonnegative integer")
        next_revision = expected_revision + 1
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE conversations
                    SET
                        revision = revision + 1,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE conversation_id = ? AND revision = ?
                    """,
                    (conversation_id, expected_revision),
                )
                if cursor.rowcount != 1:
                    raise AgentStateConflictError(
                        "conversation is missing or revision is stale"
                    )
                connection.execute(
                    """
                    INSERT INTO conversation_messages (
                        message_id,
                        conversation_id,
                        revision,
                        role,
                        content,
                        created_at
                    )
                    VALUES (
                        ?,
                        ?,
                        ?,
                        ?,
                        ?,
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    )
                    """,
                    (
                        message_id,
                        conversation_id,
                        next_revision,
                        role,
                        content,
                    ),
                )
                row = connection.execute(
                    """
                    SELECT
                        message_id,
                        conversation_id,
                        revision,
                        role,
                        content,
                        created_at
                    FROM conversation_messages
                    WHERE message_id = ?
                    """,
                    (message_id,),
                ).fetchone()
                connection.execute("COMMIT")
            except sqlite3.IntegrityError:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise AgentStateConflictError(
                    "message identity or revision conflicts"
                ) from None
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        assert row is not None
        return MessageRecord(*row)

    def list_messages(
        self,
        conversation_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[MessageRecord, ...]:
        conversation_id = require_canonical_uuid_text(conversation_id)
        if (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
            or (
                limit is not None
                and (
                    isinstance(limit, bool)
                    or not isinstance(limit, int)
                    or not 1 <= limit <= 500
                )
            )
            or (limit is None and offset != 0)
        ):
            raise ValueError("invalid message pagination")
        with self.connection() as connection:
            query = """
                SELECT
                    message_id,
                    conversation_id,
                    revision,
                    role,
                    content,
                    created_at
                FROM conversation_messages
                WHERE conversation_id = ?
                ORDER BY revision
            """
            parameters: tuple[Any, ...] = (conversation_id,)
            if limit is not None:
                query += " LIMIT ? OFFSET ?"
                parameters += (limit, offset)
            rows = connection.execute(query, parameters).fetchall()
        return tuple(MessageRecord(*row) for row in rows)

    def get_message_summary(self, conversation_id: str) -> tuple[int, str]:
        conversation_id = require_canonical_uuid_text(conversation_id)
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT
                    count(*),
                    COALESCE(
                        (
                            SELECT content
                            FROM conversation_messages
                            WHERE conversation_id = ?
                            ORDER BY revision DESC
                            LIMIT 1
                        ),
                        ''
                    )
                FROM conversation_messages
                WHERE conversation_id = ?
                """,
                (conversation_id, conversation_id),
            ).fetchone()
        assert row is not None
        return int(row[0]), str(row[1])

    def list_recent_messages(
        self,
        conversation_id: str,
        *,
        limit: int = 10,
    ) -> tuple[MessageRecord, ...]:
        conversation_id = require_canonical_uuid_text(conversation_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("recent message limit must be between 1 and 100")
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    message_id,
                    conversation_id,
                    revision,
                    role,
                    content,
                    created_at
                FROM conversation_messages
                WHERE conversation_id = ?
                ORDER BY revision DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
        return tuple(MessageRecord(*row) for row in reversed(rows))

    def put_goal_ledger(
        self,
        ledger: GoalLedger,
        *,
        expected_revision: int | None,
    ) -> GoalLedger:
        if type(ledger) is not GoalLedger:
            raise ValueError("ledger must be an exact GoalLedger")
        declared_fields = set(type(ledger).model_fields)
        if (
            set(ledger.__dict__) != declared_fields
            or ledger.__pydantic_extra__
        ):
            raise ValueError("ledger must be a valid GoalLedger")
        if expected_revision is not None and (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ValueError(
                "expected_revision must be None or a nonnegative integer"
            )
        try:
            ledger = GoalLedger.model_validate(
                ledger.model_dump(mode="python", round_trip=True)
            )
        except (TypeError, ValueError, ValidationError):
            raise ValueError("ledger must be a valid GoalLedger") from None
        conversation_id = require_canonical_uuid_text(ledger.conversation_id)
        required_revision = (
            0 if expected_revision is None else expected_revision + 1
        )
        if ledger.revision != required_revision:
            raise ValueError("goal ledger revision does not match the CAS update")
        payload_json, payload_sha256 = _canonical_goal_ledger(ledger)
        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                if expected_revision is None:
                    connection.execute(
                        """
                        INSERT INTO conversation_goal_ledgers (
                            conversation_id,
                            revision,
                            payload_json,
                            payload_sha256,
                            updated_at
                        )
                        VALUES (
                            ?,
                            ?,
                            ?,
                            ?,
                            strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        )
                        """,
                        (
                            conversation_id,
                            ledger.revision,
                            payload_json,
                            payload_sha256,
                        ),
                    )
                else:
                    cursor = connection.execute(
                        """
                        UPDATE conversation_goal_ledgers
                        SET
                            revision = ?,
                            payload_json = ?,
                            payload_sha256 = ?,
                            updated_at = strftime(
                                '%Y-%m-%dT%H:%M:%fZ',
                                'now'
                            )
                        WHERE conversation_id = ? AND revision = ?
                        """,
                        (
                            ledger.revision,
                            payload_json,
                            payload_sha256,
                            conversation_id,
                            expected_revision,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise AgentStateConflictError(
                            "goal ledger is missing or revision is stale"
                        )
                stored = self._get_goal_ledger(connection, conversation_id)
                connection.execute("COMMIT")
            except sqlite3.IntegrityError:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise AgentStateConflictError(
                    "goal ledger identity conflicts"
                ) from None
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        assert stored is not None
        return stored

    def get_goal_ledger(
        self,
        conversation_id: str,
    ) -> GoalLedger | None:
        conversation_id = require_canonical_uuid_text(conversation_id)
        with self.connection() as connection:
            return self._get_goal_ledger(connection, conversation_id)

    @staticmethod
    def _get_goal_ledger(
        connection: sqlite3.Connection,
        conversation_id: str,
    ) -> GoalLedger | None:
        row = connection.execute(
            """
            SELECT revision, payload_json, payload_sha256
            FROM conversation_goal_ledgers
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()
        if row is None:
            return None
        revision, payload_json, payload_sha256 = row
        if (
            type(revision) is not int
            or type(payload_json) is not str
            or type(payload_sha256) is not str
        ):
            raise AgentStateRepositoryError("goal ledger row storage is invalid")
        actual_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        if actual_sha256 != payload_sha256:
            raise AgentStateRepositoryError("goal ledger payload hash mismatch")
        try:
            ledger = GoalLedger.model_validate_json(payload_json)
        except (TypeError, ValueError, ValidationError):
            raise AgentStateRepositoryError("goal ledger payload is invalid") from None
        if (
            ledger.conversation_id != conversation_id
            or ledger.revision != revision
        ):
            raise AgentStateRepositoryError("goal ledger row identity mismatch")
        canonical_json, canonical_sha256 = _canonical_goal_ledger(ledger)
        if payload_json != canonical_json or payload_sha256 != canonical_sha256:
            raise AgentStateRepositoryError("goal ledger payload is not canonical")
        return ledger

    @staticmethod
    def _get_conversation(
        connection: sqlite3.Connection,
        conversation_id: str,
    ) -> ConversationRecord | None:
        row = connection.execute(
            """
            SELECT
                conversation_id,
                principal_id,
                job_id,
                revision,
                created_at,
                updated_at,
                title,
                status
            FROM conversations
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()
        return ConversationRecord(*row) if row is not None else None

    @staticmethod
    def schema_revision(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT max(revision) FROM agent_state_schema_migrations"
        ).fetchone()
        return int(row[0] or 0)


__all__ = [
    "AgentStateConflictError",
    "AgentStateRepositoryError",
    "ConversationRecord",
    "MessageRecord",
    "SQLiteAgentStateRepository",
    "require_canonical_uuid_text",
]
