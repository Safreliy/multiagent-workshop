from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from langchain_core.tools import BaseTool

from .orchestrator import run_orchestrator
from .board_swarm import run_board_swarm
from .runtime import ToolRegistry, build_model
from .single import run_single
from .swarm import run_swarm
from .tracing import BudgetExceeded, TraceRecorder
from .types import AgentConfig, ArchitectureName, ArchitectureResult


def run_architecture(
    architecture: ArchitectureName | str,
    task: str,
    tools: Any,
    config: AgentConfig,
    trace_path: Path | str | None = None,
    *,
    additional_tools: Sequence[BaseTool] = (),
) -> ArchitectureResult:
    """Run one architecture against a PostgresTools-compatible tool provider."""
    config.validate()
    if architecture not in {"single", "orchestrator", "swarm", "board_swarm"}:
        raise ValueError(f"unknown architecture: {architecture!r}")
    architecture = cast(ArchitectureName, architecture)
    output_path = Path(trace_path) if trace_path is not None else None
    recorder = TraceRecorder(
        config.max_llm_calls,
        config.max_tool_calls,
        output_path,
        cost_budget=config.cost_budget,
        model=config.model,
        input_price_ceiling=config.input_price_ceiling,
        output_price_ceiling=config.output_price_ceiling,
        max_output_tokens=config.max_tokens,
    )
    registry = ToolRegistry(tools, recorder, additional_tools)
    final_output = ""
    error: str | None = None
    status: str = "failed"

    try:
        model = build_model(config)
        runners = {
            "single": run_single,
            "orchestrator": run_orchestrator,
            "swarm": run_swarm,
            "board_swarm": run_board_swarm,
        }
        final_output = runners[architecture](task, model, registry, config, recorder)
        status = "completed" if registry.candidate_sql else "failed"
        if not registry.candidate_sql:
            error = "architecture finished without calling submit_candidate"
    except BudgetExceeded as exc:
        status = "budget_exceeded"
        error = str(exc)
        recorder.record("run_error", kind="budget_exceeded", error=error)
    except Exception as exc:  # noqa: BLE001 - architecture errors belong in run results
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        recorder.record("run_error", kind="exception", error=error)
    finally:
        recorder.finish()
        if output_path is not None:
            recorder.write(output_path)

    return ArchitectureResult(
        architecture=architecture,
        status=cast(Any, status),
        candidate_sql=registry.candidate_sql,
        submission=registry.submission,
        final_output=final_output,
        metrics=recorder.metrics,
        error=error,
        trace_path=output_path,
        trace=recorder.events,
    )
