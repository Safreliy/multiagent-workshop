from __future__ import annotations

import os
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .cost_budget import CostBudget

ArchitectureName = Literal["single", "orchestrator", "swarm", "board_swarm"]


@dataclass(slots=True)
class AgentConfig:
    """Shared model and execution limits used by every architecture."""

    model: str = "DeepSeek-V4-Flash-0731"
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False)
    temperature: float | None = 0.0
    max_llm_calls: int = 36
    max_tool_calls: int = 64
    max_model_retries: int = 1
    recursion_limit: int = 80
    request_timeout_s: float = 120.0
    max_tokens: int | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)
    chat_model: Any | None = field(default=None, repr=False)
    cost_budget: CostBudget | None = field(default=None, repr=False)
    input_price_ceiling: float | None = None
    output_price_ceiling: float | None = None

    @classmethod
    def from_env(cls, **overrides: Any) -> AgentConfig:
        values: dict[str, Any] = {
            "model": os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL") or "DeepSeek-V4-Flash-0731",
            "base_url": os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL"),
            "api_key": os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY"),
        }
        values.update(overrides)
        return cls(**values)

    def validate(self) -> None:
        if not self.model:
            raise ValueError("model must be set")
        if self.chat_model is None and not self.api_key:
            raise ValueError("api_key (LLM_API_KEY/OPENAI_API_KEY) must be set")
        if self.max_llm_calls < 1 or self.max_tool_calls < 1:
            raise ValueError("max_llm_calls and max_tool_calls must be positive")
        if self.max_model_retries < 0:
            raise ValueError("max_model_retries must be non-negative")
        if self.recursion_limit < 2:
            raise ValueError("recursion_limit must be at least 2")
        if self.cost_budget is not None:
            if self.max_tokens is None or self.max_tokens < 1:
                raise ValueError("max_tokens must be positive when a USD budget is active")
            if self.input_price_ceiling is None or self.output_price_ceiling is None:
                raise ValueError("explicit input/output price ceilings are required for a USD budget")
            for name, value in (
                ("input_price_ceiling", self.input_price_ceiling),
                ("output_price_ceiling", self.output_price_ceiling),
            ):
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f"{name} must be non-negative")


@dataclass(slots=True)
class RunMetrics:
    wall_clock_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0
    model_retries: int = 0
    tool_calls: int = 0
    database_tool_calls: int = 0
    worker_calls: int = 0
    handoff_calls: int = 0
    max_parallelism: int = 1
    usage_complete: bool = True
    cost_reserved_usd: float = 0.0
    cost_estimated_usd: float = 0.0
    cost_reported_usd: float = 0.0
    cost_reported_complete: bool = True
    cost_accounted_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ArchitectureResult:
    architecture: ArchitectureName
    status: Literal["completed", "failed", "budget_exceeded"]
    candidate_sql: str | None
    submission: Any | None
    final_output: str
    metrics: RunMetrics
    error: str | None = None
    trace_path: Path | None = None
    trace: list[dict[str, Any]] = field(default_factory=list, repr=False)

    @property
    def succeeded(self) -> bool:
        return self.status == "completed" and self.candidate_sql is not None

    def to_dict(self, *, include_trace: bool = False) -> dict[str, Any]:
        result = asdict(self)
        result["trace_path"] = str(self.trace_path) if self.trace_path else None
        if not include_trace:
            result.pop("trace", None)
        return result


@dataclass(slots=True)
class HandoffState:
    """The only state specialists are asked to pass across a handoff."""

    task: str
    hypothesis: str
    evidence: list[str] = field(default_factory=list)
    candidate_sql: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
