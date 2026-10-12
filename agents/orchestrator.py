from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import BaseTool, StructuredTool

from .prompts import (
    AUDITOR_PROMPT,
    ORCHESTRATOR_PROMPT,
    PLAN_ANALYST_PROMPT,
    QUERY_REWRITER_PROMPT,
    STATS_ANALYST_PROMPT,
)
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

ALL_DATABASE_TOOLS = [
    "get_schema",
    "get_indexes",
    "get_statistics",
    "explain",
    "explain_analyze",
    "execute",
    "benchmark",
    "submit_candidate",
]

ROLE_SPECS = {
    "plan_analyst": (PLAN_ANALYST_PROMPT, ALL_DATABASE_TOOLS),
    "query_rewriter": (QUERY_REWRITER_PROMPT, ALL_DATABASE_TOOLS),
    "stats_analyst": (STATS_ANALYST_PROMPT, ALL_DATABASE_TOOLS),
    "auditor": (AUDITOR_PROMPT, ALL_DATABASE_TOOLS),
}


def _make_worker_tool(
    name: str,
    prompt: str,
    root_task: str,
    database_tools: list[BaseTool],
    model: Any,
    config: AgentConfig,
    recorder: TraceRecorder,
) -> BaseTool:
    worker_agent = create_agent(
        model=model,
        tools=database_tools,
        system_prompt=prompt + budget_note(config),
        name=name,
        middleware=[transport_retry_middleware(config, recorder)],
    )

    def delegate(
        hypothesis: str,
        evidence: list[str],
        candidate_sql: str | None = None,
    ) -> str:
        """Delegate a compact optimization state to this specialist."""
        handoff = compact_payload(root_task, hypothesis, evidence, candidate_sql)
        with recorder.worker(name, handoff):
            state = worker_agent.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": json.dumps(handoff, ensure_ascii=False),
                        }
                    ]
                },
                config=invoke_config(config, recorder),
            )
            output = final_text(state)
            recorder.record("worker_output", worker=name, output=output)
            return output

    return StructuredTool.from_function(
        delegate,
        name=f"ask_{name}",
        description=(
            f"Ask the {name.replace('_', ' ')} to work from a compact structured state. "
            "Pass a hypothesis, short evidence strings, and optional candidate_sql. "
            "The immutable original task is attached automatically."
        ),
    )


def run_orchestrator(
    task: str,
    model: Any,
    registry: ToolRegistry,
    config: AgentConfig,
    recorder: TraceRecorder,
) -> str:
    worker_tools = [
        _make_worker_tool(
            name,
            prompt,
            task,
            registry.select(tool_names, include_additional=True),
            model,
            config,
            recorder,
        )
        for name, (prompt, tool_names) in ROLE_SPECS.items()
    ]
    supervisor = create_agent(
        model=model,
        tools=worker_tools,
        system_prompt=ORCHESTRATOR_PROMPT + budget_note(config),
        name="orchestrator",
        middleware=[transport_retry_middleware(config, recorder)],
    )
    recorder.record("architecture_start", architecture="orchestrator", task=task)
    state = supervisor.invoke(
        {"messages": [{"role": "user", "content": task}]},
        config=invoke_config(config, recorder),
    )
    return final_text(state)
