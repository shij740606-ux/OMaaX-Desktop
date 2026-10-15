import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from gfa_core.mas.memory.base import BaseMemory, MemoryEntry
from gfa_core.mas.memory.persistent import PersistentMemory


@dataclass
class SessionData:
    session_id: str
    messages: list[dict] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


class SessionMemory(BaseMemory):
    """SessionMemory with persistent backend"""
    
    def __init__(
        self, 
        max_sessions: int = 1000, 
        default_ttl: int = 3600,
        persistent_storage: Optional[PersistentMemory] = None
    ):
        self._sessions: OrderedDict[str, MemoryEntry[SessionData]] = OrderedDict()
        self._max_sessions = max_sessions
        self._default_ttl = default_ttl
        self._lock = asyncio.Lock()
        
        # 持久化后端
        self._persistent = persistent_storage
        if self._persistent:
            print(f"[SessionMemory] Persistent storage enabled: {self._persistent.storage_dir}")

    async def store(self, key: str, value: Any, ttl: int | None = None) -> None:
        async with self._lock:
            if len(self._sessions) >= self._max_sessions and key not in self._sessions:
                self._sessions.popitem(last=False)

            if isinstance(value, SessionData):
                session_data = value
            else:
                session_data = SessionData(session_id=key, state=value)

            entry = MemoryEntry(
                key=key,
                value=session_data,
                ttl=ttl or self._default_ttl,
            )
            self._sessions[key] = entry
            self._sessions.move_to_end(key)
            
            # 持久化存储
            if self._persistent:
                await self._persistent.store(key, session_data, ttl)

    async def retrieve(self, key: str) -> Any | None:
        async with self._lock:
            # 先查内存
            entry = self._sessions.get(key)
            
            # 内存未命中，查持久化
            if entry is None and self._persistent:
                persistent_data = await self._persistent.retrieve(key)
                if persistent_data:
                    self._sessions[key] = MemoryEntry(
                        key=key,
                        value=persistent_data,
                        ttl=self._default_ttl
                    )
                    entry = self._sessions[key]
            
            if entry is None:
                return None

            if entry.ttl and datetime.now() > entry.created_at + timedelta(seconds=entry.ttl):
                del self._sessions[key]
                if self._persistent:
                    await self._persistent.delete(key)
                return None

            self._sessions.move_to_end(key)
            return entry.value

    async def delete(self, key: str) -> bool:
        async with self._lock:
            deleted = False
            if key in self._sessions:
                del self._sessions[key]
                deleted = True
            if self._persistent:
                persistent_deleted = await self._persistent.delete(key)
                deleted = deleted or persistent_deleted
            return deleted

    async def exists(self, key: str) -> bool:
        async with self._lock:
            # 先查内存
            entry = self._sessions.get(key)
            if entry:
                if entry.ttl and datetime.now() > entry.created_at + timedelta(seconds=entry.ttl):
                    del self._sessions[key]
                    if self._persistent:
                        await self._persistent.delete(key)
                    return False
                return True
            
            # 查持久化
            if self._persistent:
                return await self._persistent.exists(key)
            return False

    async def clear(self) -> None:
        async with self._lock:
            self._sessions.clear()
            if self._persistent:
                await self._persistent.clear()

    async def add_message(self, session_id: str, message: dict) -> None:
        session = await self.retrieve(session_id)
        if session is None:
            session = SessionData(session_id=session_id)

        session.messages.append(message)
        session.updated_at = datetime.now()
        await self.store(session_id, session)

    async def get_messages(self, session_id: str) -> list[dict]:
        session = await self.retrieve(session_id)
        return session.messages if session else []

    async def update_state(self, session_id: str, state: dict[str, Any]) -> None:
        session = await self.retrieve(session_id)
        if session is None:
            session = SessionData(session_id=session_id)

        session.state.update(state)
        session.updated_at = datetime.now()
        await self.store(session_id, session)
