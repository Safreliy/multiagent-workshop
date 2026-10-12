from __future__ import annotations

from typing import Any

from langchain.agents import create_agent

from .prompts import SINGLE_PROMPT
from .runtime import (
    ToolRegistry,
    budget_note,
    final_text,
    invoke_config,
    transport_retry_middleware,
)
from .tracing import TraceRecorder
from .types import AgentConfig


def run_single(
    task: str,
    model: Any,
    registry: ToolRegistry,
    config: AgentConfig,
    recorder: TraceRecorder,
) -> str:
    agent = create_agent(
        model=model,
        tools=registry.tools,
        system_prompt=SINGLE_PROMPT + budget_note(config),
        name="single_agent",
        middleware=[transport_retry_middleware(config, recorder)],
    )
    recorder.record("architecture_start", architecture="single", task=task)
    result = agent.invoke(
        {"messages": [{"role": "user", "content": task}]},
        config=invoke_config(config, recorder),
    )
    return final_text(result)
