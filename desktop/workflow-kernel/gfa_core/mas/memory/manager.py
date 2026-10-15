from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gfa_core.mas.memory.base import SearchResult
from gfa_core.mas.memory.case import AnalysisCase, CaseMemory
from gfa_core.mas.memory.knowledge import KnowledgeEntry, KnowledgeMemory
from gfa_core.mas.memory.session import SessionData, SessionMemory


@dataclass
class MemoryStats:
    session_count: int = 0
    case_count: int = 0
    knowledge_count: int = 0
    last_updated: datetime = field(default_factory=datetime.now)


class MemoryManager:
    def __init__(
        self,
        session_memory: SessionMemory | None = None,
        case_memory: CaseMemory | None = None,
        knowledge_memory: KnowledgeMemory | None = None,
    ):
        self.session = session_memory or SessionMemory()
        self.case = case_memory or CaseMemory()
        self.knowledge = knowledge_memory or KnowledgeMemory()

    async def create_session(self, session_id: str, initial_state: dict[str, Any] | None = None) -> SessionData:
        session = SessionData(
            session_id=session_id,
            state=initial_state or {},
        )
        await self.session.store(session_id, session)
        return session

    async def get_session(self, session_id: str) -> SessionData | None:
        return await self.session.retrieve(session_id)

    async def add_message_to_session(self, session_id: str, role: str, content: str) -> None:
        await self.session.add_message(session_id, {"role": role, "content": content})

    async def update_session_state(self, session_id: str, state: dict[str, Any]) -> None:
        await self.session.update_state(session_id, state)

    async def create_case(
        self,
        case_id: str,
        user_id: str | None = None,
        gene_family: str | None = None,
        species: str | None = None,
        workflow: list[dict] | None = None,
    ) -> AnalysisCase:
        case = AnalysisCase(
            case_id=case_id,
            user_id=user_id,
            gene_family=gene_family,
            species=species,
            workflow=workflow or [],
        )
        await self.case.store(case_id, case)
        return case

    async def get_case(self, case_id: str) -> AnalysisCase | None:
        return await self.case.retrieve(case_id)

    async def update_case_results(self, case_id: str, results: dict[str, Any]) -> None:
        case = await self.case.retrieve(case_id)
        if case:
            case.results.update(results)
            await self.case.store(case_id, case)

    async def complete_case(self, case_id: str) -> None:
        await self.case.update_status(case_id, "completed")

    async def add_case_feedback(self, case_id: str, feedback: dict[str, Any]) -> None:
        await self.case.add_feedback(case_id, feedback)

    async def add_knowledge(
        self,
        entry_id: str,
        content: str,
        embedding: list[float] | None = None,
        source: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        await self.knowledge.add_entry(
            entry_id=entry_id,
            content=content,
            embedding=embedding,
            source=source,
            category=category,
            tags=tags,
        )

    async def search_knowledge(
        self,
        query_embedding: list[float],
        top_k: int = 10,
    ) -> list[SearchResult]:
        return await self.knowledge.search(query_embedding, top_k)

    async def search_knowledge_by_tags(self, tags: list[str], top_k: int = 10) -> list[SearchResult]:
        return await self.knowledge.search_by_tags(tags, top_k)

    async def extract_case_to_knowledge(self, case_id: str) -> KnowledgeEntry | None:
        case = await self.case.retrieve(case_id)
        if not case or case.status != "completed":
            return None

        entry_id = f"case_{case_id}"
        content = self._extract_knowledge_content(case)

        entry = KnowledgeEntry(
            entry_id=entry_id,
            content=content,
            source=f"case:{case_id}",
            category="workflow_template",
            tags=[case.gene_family, case.species] if case.gene_family and case.species else [],
            metadata={
                "gene_family": case.gene_family,
                "species": case.species,
                "workflow_steps": len(case.workflow),
            },
        )

        await self.knowledge.store(entry_id, entry)
        return entry

    def _extract_knowledge_content(self, case: AnalysisCase) -> str:
        parts = []
        if case.gene_family:
            parts.append(f"Gene Family: {case.gene_family}")
        if case.species:
            parts.append(f"Species: {case.species}")
        if case.workflow:
            parts.append("Workflow:")
            for i, step in enumerate(case.workflow, 1):
                parts.append(f"  {i}. {step.get('tool', 'unknown')}: {step.get('description', '')}")
        return "\n".join(parts)

    async def get_stats(self) -> MemoryStats:
        return MemoryStats(
            session_count=len(self.session._sessions),
            case_count=len(await self.case.list_by_status("completed")),
            knowledge_count=len(self.knowledge._entries),
        )

    async def clear_all(self) -> None:
        await self.session.clear()
        await self.case.clear()
        await self.knowledge.clear()
