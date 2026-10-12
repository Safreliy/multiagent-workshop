from __future__ import annotations

import itertools
from collections.abc import Sequence
from typing import Any, ClassVar

import httpx2
import openai
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import PrivateAttr

from agents import AgentConfig, run_architecture
from agents.runtime import ToolRegistry, build_model
from agents.tracing import TraceRecorder
from agents.prompts import STATS_ANALYST_PROMPT
from agents.prompts.system import COMMON


class ScriptedToolModel(BaseChatModel):
    """Small deterministic model that exercises each architecture's control flow."""

    counter: ClassVar[Any] = itertools.count(1)
    _seen: list[tuple[str, int]] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-model"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Any:
        schemas = []
        for item in tools:
            name = item.name if isinstance(item, BaseTool) else item.get("function", {}).get("name")
            schemas.append(name)
        return self.bind(tool_names=schemas)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        *,
        tool_names: list[str] | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        available = set(tool_names or [])
        system_text = str(messages[0].content) if messages else ""
        self._seen.append((system_text, len(messages)))
        if messages and isinstance(messages[-1], ToolMessage):
            message = AIMessage(content='{"status":"done"}')
        elif "ask_auditor" in available:
            message = self._call(
                "ask_auditor",
                {
                    "hypothesis": "audit baseline-equivalent rewrite",
                    "evidence": [],
                    "candidate_sql": "SELECT 1",
                },
            )
        elif "You are the Plan Analyst." in system_text and "transfer_to_query_rewriter" in available:
            message = self._call(
                "transfer_to_query_rewriter",
                {
                    "hypothesis": "candidate is ready for audit",
                    "evidence": ["mock plan"],
                    "candidate_sql": "SELECT 1",
                },
            )
        elif "You are the Query Rewriter." in system_text and "transfer_to_auditor" in available:
            message = self._call(
                "transfer_to_auditor",
                {
                    "hypothesis": "candidate measured",
                    "evidence": ["mock plan", "mock benchmark"],
                    "candidate_sql": "SELECT 1",
                },
            )
        elif "submit_candidate" in available:
            message = self._call("submit_candidate", {"sql": "SELECT 1", "n": 1})
        else:
            message = AIMessage(content='{"status":"done"}')
        message.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _call(self, name: str, args: dict[str, Any]) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": name,
                    "args": args,
                    "id": f"call-{next(self.counter)}",
                    "type": "tool_call",
                }
            ],
        )


class StubPostgresTools:
    def __getattr__(self, name: str) -> Any:
        if name == "submit_candidate":
            return lambda sql, n=5: {"ok": True, "correct": True, "sql": sql, "n": n}
        return lambda **kwargs: {"tool": name, "arguments": kwargs}


class ProfiledPostgresTools(StubPostgresTools):
    available_tools = ("get_schema", "get_capabilities", "create_candidate_index")

    def get_capabilities(self) -> dict[str, Any]:
        """Return the mutations allowed by the active experiment profile."""
        return {"create_index": True}

    def create_candidate_index(self, table: str, columns: list[str]) -> dict[str, Any]:
        """Create a temporary candidate index in the isolated benchmark environment."""
        return {"table": table, "columns": columns}


@pytest.mark.parametrize("architecture", ["single", "orchestrator", "swarm"])
def test_architectures_submit_candidate(architecture: str, tmp_path: Any) -> None:
    model = ScriptedToolModel()
    result = run_architecture(
        architecture,
        "Optimize SELECT 1",
        StubPostgresTools(),
        AgentConfig(chat_model=model, max_llm_calls=12, max_tool_calls=12),
        tmp_path / f"{architecture}.jsonl",
    )

    assert result.status == "completed", result.error
    assert result.candidate_sql == "SELECT 1"
    assert result.metrics.llm_calls == {"single": 2, "orchestrator": 4, "swarm": 4}[architecture]
    assert result.metrics.tool_calls == {"single": 1, "orchestrator": 2, "swarm": 3}[architecture]
    assert result.metrics.database_tool_calls == 1
    assert result.metrics.input_tokens > 0
    assert result.trace_path and result.trace_path.exists()
    if architecture == "orchestrator":
        assert result.metrics.worker_calls == 1
        worker_start = next(event for event in result.trace if event["event"] == "worker_start")
        assert worker_start["handoff"]["task"] == "Optimize SELECT 1"
    if architecture == "swarm":
        assert result.metrics.handoff_calls == 2
        assert result.metrics.worker_calls == 3
        auditor_calls = [count for prompt, count in model._seen if "You are the Auditor." in prompt]
        # System + protocol-valid transfer tool call/result + compact state only.
        assert auditor_calls[0] == 4


class NoUsageModel(ScriptedToolModel):
    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        result = super()._generate(*args, **kwargs)
        result.generations[0].message.usage_metadata = None
        return result


class PriorToolSwarmModel(ScriptedToolModel):
    _plan_calls: int = PrivateAttr(default=0)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        *,
        tool_names: list[str] | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        system_text = str(messages[0].content) if messages else ""
        if "You are the Plan Analyst." in system_text:
            self._seen.append((system_text, len(messages)))
            self._plan_calls += 1
            if self._plan_calls == 1:
                message = self._call("get_schema", {})
            else:
                message = self._call(
                    "transfer_to_query_rewriter",
                    {
                        "hypothesis": "schema checked",
                        "evidence": ["schema tool completed"],
                        "candidate_sql": "SELECT 1",
                    },
                )
            message.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
            return ChatResult(generations=[ChatGeneration(message=message)])
        return super()._generate(
            messages,
            stop,
            run_manager,
            tool_names=tool_names,
            **kwargs,
        )


class TransientOnceModel(ScriptedToolModel):
    _failures_remaining: int = PrivateAttr(default=1)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self._failures_remaining:
            self._failures_remaining -= 1
            raise openai.APIConnectionError(
                message="transient test failure",
                request=httpx2.Request("POST", "http://model.test/v1/chat/completions"),
            )
        return super()._generate(messages, stop, run_manager, **kwargs)


class InvalidRequestModel(ScriptedToolModel):
    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        raise ValueError("invalid model request")


def test_budget_is_global_across_orchestrator_and_worker() -> None:
    result = run_architecture(
        "orchestrator",
        "Optimize SELECT 1",
        StubPostgresTools(),
        AgentConfig(chat_model=ScriptedToolModel(), max_llm_calls=2, max_tool_calls=12),
    )

    assert result.status == "budget_exceeded"
    assert result.metrics.llm_calls == 2


def test_missing_usage_is_explicit() -> None:
    result = run_architecture(
        "single",
        "Optimize SELECT 1",
        StubPostgresTools(),
        AgentConfig(chat_model=NoUsageModel(), max_llm_calls=4, max_tool_calls=4),
    )

    assert result.status == "completed"
    assert result.metrics.usage_complete is False
    assert result.metrics.total_tokens == 0


def test_swarm_discards_history_before_compact_handoff() -> None:
    model = PriorToolSwarmModel()
    result = run_architecture(
        "swarm",
        "Optimize SELECT 1",
        StubPostgresTools(),
        AgentConfig(chat_model=model, max_llm_calls=8, max_tool_calls=8),
    )

    assert result.status == "completed", result.error
    assert result.metrics.handoff_calls == 2
    auditor_calls = [count for prompt, count in model._seen if "You are the Auditor." in prompt]
    assert auditor_calls[0] == 4


@pytest.mark.parametrize("architecture", ["single", "orchestrator", "swarm"])
def test_transport_failure_is_retried_once(architecture: str) -> None:
    result = run_architecture(
        architecture,
        "Optimize SELECT 1",
        StubPostgresTools(),
        AgentConfig(chat_model=TransientOnceModel(), max_llm_calls=12, max_tool_calls=12),
    )

    assert result.status == "completed", result.error
    assert result.metrics.model_retries == 1
    assert result.metrics.llm_calls == {"single": 3, "orchestrator": 5, "swarm": 5}[architecture]
    assert result.metrics.usage_complete is False
    assert sum(event["event"] == "model_retry" for event in result.trace) == 1


def test_transport_retry_still_obeys_global_llm_budget() -> None:
    result = run_architecture(
        "single",
        "Optimize SELECT 1",
        StubPostgresTools(),
        AgentConfig(chat_model=TransientOnceModel(), max_llm_calls=1, max_tool_calls=4),
    )

    assert result.status == "budget_exceeded"
    assert result.metrics.llm_calls == 1
    assert result.metrics.model_retries == 1


def test_non_transport_model_error_is_not_retried() -> None:
    result = run_architecture(
        "single",
        "Optimize SELECT 1",
        StubPostgresTools(),
        AgentConfig(chat_model=InvalidRequestModel(), max_llm_calls=4, max_tool_calls=4),
    )

    assert result.status == "failed"
    assert result.metrics.llm_calls == 1
    assert result.metrics.model_retries == 0


def test_registry_discovers_profile_tools_without_langchain_backend_dependency() -> None:
    recorder = TraceRecorder(max_llm_calls=4, max_tool_calls=4)
    registry = ToolRegistry(ProfiledPostgresTools(), recorder)

    assert {tool.name for tool in registry.additional_tools} == {
        "get_capabilities",
        "create_candidate_index",
    }
    result = registry.by_name["create_candidate_index"].invoke(
        {"table": "orders", "columns": ["customer_id", "created_at"]},
        config={"callbacks": [recorder]},
    )

    assert '"table": "orders"' in result
    assert recorder.metrics.tool_calls == 1
    assert recorder.metrics.database_tool_calls == 1


def test_registry_accepts_external_plan_ranker_without_counting_it_as_database() -> None:
    def rank_query_plans(plan_a: str, plan_b: str) -> dict[str, Any]:
        """Rank two plans with an auxiliary model."""
        return {"preferred": "a", "plans": [plan_a, plan_b]}

    ranker = StructuredTool.from_function(rank_query_plans)
    recorder = TraceRecorder(max_llm_calls=4, max_tool_calls=4)
    registry = ToolRegistry(StubPostgresTools(), recorder, [ranker])

    result = registry.by_name["rank_query_plans"].invoke(
        {"plan_a": "A", "plan_b": "B"},
        config={"callbacks": [recorder]},
    )

    assert '"preferred": "a"' in result
    assert recorder.metrics.tool_calls == 1
    assert recorder.metrics.database_tool_calls == 0
    assert any(event["event"] == "additional_tool_result" for event in recorder.events)


def test_openrouter_extra_body_is_forwarded_to_chat_model(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)
    model = build_model(
        AgentConfig(
            model="openrouter/example",
            api_key="test-key",
            extra_body={"provider": {"order": ["example"]}},
        )
    )

    assert isinstance(model, FakeChatOpenAI)
    assert captured["extra_body"] == {"provider": {"order": ["example"]}}


def test_chat_model_preserves_standard_proxy_environment(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured["model_kwargs"] = kwargs

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)

    build_model(AgentConfig(model="openrouter/example", api_key="test-key"))

    assert captured["model_kwargs"]["http_socket_options"] == ()
    assert "http_client" not in captured["model_kwargs"]
    assert "http_async_client" not in captured["model_kwargs"]


def test_chat_model_can_omit_unsupported_temperature(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)
    build_model(AgentConfig(model="openrouter/example", api_key="test-key", temperature=None))

    assert "temperature" not in captured


def test_prompts_defer_environment_mutations_to_profile_capabilities() -> None:
    common = " ".join(COMMON.split())
    stats = " ".join(STATS_ANALYST_PROMPT.split())
    assert "must never change data, schema" not in common
    assert "only through explicit tools" in common
    assert "get_capabilities" in common
    assert "candidate indexes only through the dedicated tools" in stats


def test_specialists_do_not_echo_the_immutable_task_in_outputs() -> None:
    from agents.prompts import (
        AUDITOR_PROMPT,
        PLAN_ANALYST_PROMPT,
        QUERY_REWRITER_PROMPT,
        STATS_ANALYST_PROMPT,
    )

    for prompt in (
        PLAN_ANALYST_PROMPT,
        QUERY_REWRITER_PROMPT,
        STATS_ANALYST_PROMPT,
        AUDITOR_PROMPT,
    ):
        prompt = " ".join(prompt.split())
        assert "Do not copy the supplied task" in prompt
        assert "keys task, hypothesis" not in prompt
