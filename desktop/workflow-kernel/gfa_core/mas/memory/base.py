from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class MemoryEntry(Generic[T]):
    key: str
    value: T
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    ttl: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    key: str
    value: Any
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseMemory(ABC):
    @abstractmethod
    async def store(self, key: str, value: Any, ttl: int | None = None) -> None:
        pass

    @abstractmethod
    async def retrieve(self, key: str) -> Any | None:
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        pass

    @abstractmethod
    async def clear(self) -> None:
        pass
