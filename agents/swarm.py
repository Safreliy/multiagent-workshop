from __future__ import annotations

import json
from typing import Annotated, Any

from langchain.agents import create_agent
from langchain.messages import HumanMessage, RemoveMessage, ToolMessage
from langchain.tools import BaseTool, InjectedToolCallId, tool
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from langgraph_swarm import create_swarm

from .orchestrator import ROLE_SPECS
from .prompts import SWARM_SUFFIX
from .runtime import (
    ToolRegistry,
    budget_note,
    compact_payload,
    final_text,
    invoke_config,
    transport_retry_middleware,
)
from .tracing import TraceRecorder
from .types import AgentConfig

HANDOFF_METADATA_KEY = "__handoff_destination"

# Useful feedback routes without giving every role a quadratic set of handoff schemas.
NEXT_ROLES = {
    "plan_analyst": ("stats_analyst", "query_rewriter"),
    "stats_analyst": ("plan_analyst", "query_rewriter"),
    "query_rewriter": ("plan_analyst", "stats_analyst", "auditor"),
    "auditor": ("query_rewriter",),
}


def _handoff_tool(
    source: str,
    destination: str,
    root_task: str,
    recorder: TraceRecorder,
) -> BaseTool:
    tool_name = f"transfer_to_{destination}"

    @tool(
        tool_name,
        description=(
            f"Transfer control to {destination} with compact state only: task, "
            "hypothesis, evidence, candidate_sql."
        ),
    )
    def transfer(
        hypothesis: str,
        evidence: list[str],
        candidate_sql: str | None = None,
        state: Annotated[Any, InjectedState] = None,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Command:
        payload = compact_payload(root_task, hypothesis, evidence, candidate_sql)
        recorder.handoff(source, destination, payload)
        previous = state.get("messages", []) if isinstance(state, dict) else []
        # Keep the assistant tool call and its response so the next API request is
        # protocol-valid, followed by one compact user state. Older history is replaced
        # by the swarm parent state schema.
        compact_messages: list[Any] = [RemoveMessage(id=REMOVE_ALL_MESSAGES)]
        if previous:
            compact_messages.append(previous[-1])
        compact_messages.extend(
            [
                ToolMessage(
                    content=f"Transferred compact state to {destination}",
                    name=tool_name,
                    tool_call_id=tool_call_id,
                ),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ]
        )
        return Command(
            goto=destination,
            graph=Command.PARENT,
            update={"messages": compact_messages, "active_agent": destination},
        )

    transfer.metadata = {HANDOFF_METADATA_KEY: destination}
    return transfer


def run_swarm(
    task: str,
    model: Any,
    registry: ToolRegistry,
    config: AgentConfig,
    recorder: TraceRecorder,
) -> str:
    agents = []
    for name, (prompt, database_tool_names) in ROLE_SPECS.items():
        transfer_tools = [
            _handoff_tool(name, destination, task, recorder) for destination in NEXT_ROLES[name]
        ]
        agents.append(
            create_agent(
                model=model,
                tools=[
                    *registry.select(database_tool_names, include_additional=True),
                    *transfer_tools,
                ],
                system_prompt=prompt + SWARM_SUFFIX + budget_note(config),
                name=name,
                middleware=[transport_retry_middleware(config, recorder)],
            )
        )

    workflow = create_swarm(agents, default_active_agent="plan_analyst")
    app = workflow.compile(name="sql_optimization_swarm")
    recorder.metrics.worker_calls = 1
    recorder.record(
        "architecture_start",
        architecture="swarm",
        task=task,
        active_agent="plan_analyst",
    )
    state = app.invoke(
        {"messages": [{"role": "user", "content": task}]},
        config=invoke_config(config, recorder),
    )
    return final_text(state)
