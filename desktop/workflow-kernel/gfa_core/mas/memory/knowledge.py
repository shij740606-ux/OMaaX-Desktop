from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gfa_core.mas.memory.base import BaseMemory, MemoryEntry, SearchResult


@dataclass
class KnowledgeEntry:
    entry_id: str
    content: str
    embedding: list[float] | None = None
    source: str | None = None
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


class KnowledgeMemory(BaseMemory):
    def __init__(self, embedding_dim: int = 1536):
        self._embedding_dim = embedding_dim
        self._entries: dict[str, MemoryEntry[KnowledgeEntry]] = {}
        self._embeddings: dict[str, list[float]] = {}

    async def store(self, key: str, value: Any, ttl: int | None = None) -> None:
        if isinstance(value, KnowledgeEntry):
            entry = value
        else:
            entry = KnowledgeEntry(entry_id=key, **value)

        memory_entry = MemoryEntry(key=key, value=entry)
        self._entries[key] = memory_entry

        if entry.embedding:
            self._embeddings[key] = entry.embedding

    async def retrieve(self, key: str) -> Any | None:
        entry = self._entries.get(key)
        return entry.value if entry else None

    async def delete(self, key: str) -> bool:
        if key in self._entries:
            del self._entries[key]
            self._embeddings.pop(key, None)
            return True
        return False

    async def exists(self, key: str) -> bool:
        return key in self._entries

    async def clear(self) -> None:
        self._entries.clear()
        self._embeddings.clear()

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        threshold: float = 0.0,
    ) -> list[SearchResult]:
        if not self._embeddings:
            return []

        scores = []
        for key, embedding in self._embeddings.items():
            score = self._cosine_similarity(query_embedding, embedding)
            if score >= threshold:
                scores.append((key, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        top_results = scores[:top_k]

        results = []
        for key, score in top_results:
            entry = self._entries.get(key)
            if entry:
                results.append(SearchResult(
                    key=key,
                    value=entry.value,
                    score=score,
                    metadata=entry.metadata,
                ))

        return results

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        if len(a) != len(b):
            return 0.0

        dot_product = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot_product / (norm_a * norm_b)

    async def search_by_tags(self, tags: list[str], top_k: int = 10) -> list[SearchResult]:
        results = []
        for key, entry in self._entries.items():
            if entry.value.tags and any(tag in entry.value.tags for tag in tags):
                results.append(SearchResult(
                    key=key,
                    value=entry.value,
                    score=1.0,
                    metadata=entry.metadata,
                ))
                if len(results) >= top_k:
                    break

        return results

    async def search_by_category(self, category: str, top_k: int = 10) -> list[SearchResult]:
        results = []
        for key, entry in self._entries.items():
            if entry.value.category == category:
                results.append(SearchResult(
                    key=key,
                    value=entry.value,
                    score=1.0,
                    metadata=entry.metadata,
                ))
                if len(results) >= top_k:
                    break

        return results

    async def add_entry(
        self,
        entry_id: str,
        content: str,
        embedding: list[float] | None = None,
        source: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        entry = KnowledgeEntry(
            entry_id=entry_id,
            content=content,
            embedding=embedding,
            source=source,
            category=category,
            tags=tags or [],
            metadata=metadata or {},
        )
        await self.store(entry_id, entry)
