from datetime import datetime
from pathlib import Path

from gfa_core.mas.agents.conversation_tools import (
    ConversationToolRegistry,
    build_plantai_conversation_tools,
)
from gfa_core.mas.agents.router_runtime import RouterRuntime
from gfa_core.mas.llm import LLMManager
from gfa_core.project_paths import PROJECT_ROOT

from .base import AgentMessage, AgentState, BaseAgent, QueryMode


class RouterAgent(BaseAgent):
    name = "router"
    role = "Intent Router"

    _LEGACY_MODES = {
        "reply": QueryMode.FAQ,
        "clarify": QueryMode.CONSULTATION,
        "show_result": QueryMode.INTERPRETATION,
        "propose_analysis": QueryMode.ANALYSIS,
        "propose_utility": QueryMode.CONSULTATION,
    }

    def __init__(
        self,
        llm_manager: LLMManager,
        tools: ConversationToolRegistry | None = None,
        project_root: Path = PROJECT_ROOT,
    ) -> None:
        super().__init__(llm_manager)
        self.tools = tools
        if self.tools is None:
            self.tools = build_plantai_conversation_tools(
                llm_manager=llm_manager,
                project_root=Path(project_root),
            )
        self.runtime = RouterRuntime(llm_manager, self.tools)

    async def execute(self, state: AgentState, message: AgentMessage) -> AgentState:
        payload = message.payload
        if "history" in payload:
            history = list(payload.get("history") or [])
        else:
            history = [
                {"role": item.get("role"), "content": item.get("content", "")}
                for item in state.messages
                if isinstance(item, dict) and item.get("role") in {"user", "assistant"}
            ][-10:]
        runtime_context = {
            "query": str(payload.get("query") or ""),
            "history": history,
            "session_id": str(payload.get("session_id") or state.session_id),
            "job_id": str(payload.get("job_id") or state.job_id),
            "language": str(payload.get("language") or "zh"),
        }
        if "goal_ledger" in payload:
            runtime_context["goal_ledger"] = payload.get("goal_ledger")
        outcome = await self.runtime.run(
            **runtime_context,
        )

        state.results["router_outcome"] = outcome.model_dump()
        state.mode = (
            self._LEGACY_MODES.get(outcome.action, QueryMode.FAQ)
            if outcome.status == "ok"
            else QueryMode.FAQ
        )
        state.updated_at = datetime.now()
        state.messages.append(
            {
                "role": "router",
                "content": f"status={outcome.status}; action={outcome.action or 'none'}",
            }
        )
        return state
