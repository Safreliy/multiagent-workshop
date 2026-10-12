from __future__ import annotations

import itertools
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, ClassVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from pydantic import PrivateAttr

from agents import AgentConfig, run_architecture
from agents.board_swarm import SharedBoard, _research_tools
from agents.runtime import ToolRegistry
from agents.tracing import TraceRecorder


class BoardModel(BaseChatModel):
    counter: ClassVar[Any] = itertools.count(1)
    _barrier: threading.Barrier = PrivateAttr(default_factory=lambda: threading.Barrier(3))
    _plan_posted: threading.Event = PrivateAttr(default_factory=threading.Event)
    _stats_saw_plan: bool = PrivateAttr(default=False)

    @property
    def _llm_type(self) -> str:
        return "board-test"

    def bind_tools(self, tools: list[BaseTool], **kwargs: Any) -> Any:
        return self.bind(tool_names=[tool.name for tool in tools])

    def _call(self, name: str, arguments: dict[str, Any]) -> AIMessage:
        return AIMessage(content="", tool_calls=[{
            "name": name, "args": arguments, "id": f"test-{next(self.counter)}",
            "type": "tool_call",
        }])

    def _generate(self, messages: list[BaseMessage], stop: Any = None,
                  run_manager: Any = None, *, tool_names: list[str] | None = None,
                  **kwargs: Any) -> ChatResult:
        system = str(messages[0].content)
        user = str(messages[1].content)
        last = messages[-1]
        if "You are the Auditor." in system:
            if isinstance(last, ToolMessage) and last.name == "get_artifact":
                message = self._call("submit_candidate", {"sql": "SELECT 1", "n": 1})
            elif isinstance(last, ToolMessage):
                message = AIMessage(content='{"status":"audited"}')
            else:
                message = self._call("get_artifact", {
                    "artifact_id": json.loads(user)["candidate_artifacts"][0],
                })
        elif "candidate_artifacts" in user:
            if isinstance(last, ToolMessage):
                message = AIMessage(content='{"hypothesis":"synthesized","evidence":[],"candidate_sql":"SELECT 1"}')
            else:
                message = self._call("read_board", {"since_revision": 0})
        elif "You are the Plan Analyst." in system:
            if isinstance(last, ToolMessage):
                self._plan_posted.set()
                message = AIMessage(content='{"hypothesis":"plan finding","evidence":[],"candidate_sql":null}')
            else:
                self._barrier.wait(timeout=5)
                message = self._call("post_finding", {"summary": "plan finding", "evidence": ["test"]})
        elif "You are the Stats Analyst." in system:
            if isinstance(last, ToolMessage):
                self._stats_saw_plan = "plan finding" in str(last.content)
                message = AIMessage(content='{"hypothesis":"stats finding","evidence":[],"candidate_sql":null}')
            else:
                self._barrier.wait(timeout=5)
                assert self._plan_posted.wait(timeout=5)
                message = self._call("read_board", {"since_revision": 0})
        else:
            if isinstance(last, ToolMessage):
                message = AIMessage(content='{"hypothesis":"rewrite","evidence":[],"candidate_sql":null}')
            else:
                self._barrier.wait(timeout=5)
                message = self._call("post_finding", {
                    "summary": "rewrite", "evidence": ["tested"], "candidate_sql": "SELECT 1",
                })
        message.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        return ChatResult(generations=[ChatGeneration(message=message)])


class StubTools:
    def __getattr__(self, name: str) -> Any:
        if name == "submit_candidate":
            return lambda sql, n=5: {"ok": True, "correct": True, "sql": sql, "n": n}
        return lambda **arguments: {"tool": name, "arguments": arguments}


class NoisyBoardModel(BoardModel):
    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> ChatResult:
        system = str(messages[0].content)
        user = str(messages[1].content)
        if ("candidate_artifacts" not in user and "You are the Auditor." not in system
                and isinstance(messages[-1], ToolMessage)
                and "synthesis" not in system):
            message = self._call("read_board", {"since_revision": 0})
            message.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
            if "You are the Plan Analyst." in system:
                self._plan_posted.set()
            return ChatResult(generations=[ChatGeneration(message=message)])
        return super()._generate(messages, *args, **kwargs)


class FourthTurnFindingModel(BoardModel):
    _role_turns: dict[str, int] = PrivateAttr(default_factory=dict)
    _turn_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> ChatResult:
        system = str(messages[0].content)
        if "You are the Plan Analyst." in system or "You are the Stats Analyst." in system:
            role = "plan_analyst" if "Plan Analyst" in system else "stats_analyst"
            with self._turn_lock:
                turn = self._role_turns.get(role, 0) + 1
                self._role_turns[role] = turn
            if turn == 1:
                self._barrier.wait(timeout=5)
            if turn < 4:
                message = self._call("read_board", {"since_revision": 0})
            else:
                message = self._call("post_finding", {
                    "summary": f"{role} observation", "evidence": ["fourth-turn evidence"],
                })
            message.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
            return ChatResult(generations=[ChatGeneration(message=message)])
        return super()._generate(messages, *args, **kwargs)


def test_board_concurrent_publication_and_immutable_artifacts(tmp_path: Path) -> None:
    board = SharedBoard(TraceRecorder(20, 20), tmp_path / "run.board.json")
    with ThreadPoolExecutor(max_workers=8) as pool:
        entries = list(pool.map(lambda n: board.publish(f"peer-{n}", "candidate", f"test-{n}",
                                                        candidate_sql=f"SELECT {n}"), range(20)))
    assert sorted(item["revision"] for item in entries) == list(range(1, 21))
    assert len({item["artifact_id"] for item in entries}) == 20
    assert board.artifact("sql-0001")["sql"].startswith("SELECT ")
    assert board.read("late-peer", 18)["entries"][0]["revision"] == 19
    page = board.read("late-peer", 0)
    assert page["next_revision"] == 20
    page["entries"][0]["evidence"].append("mutation")
    assert board.read("late-peer", 0)["entries"][0]["evidence"] == []
    persisted = json.loads((tmp_path / "run.board.json").read_text(encoding="utf-8"))
    assert persisted["revision"] == 20
    assert len(persisted["artifacts"]) == 20


def test_parallel_peers_read_new_findings_and_auditor_submits(tmp_path: Path) -> None:
    model = BoardModel()
    result = run_architecture(
        "board_swarm", "Optimize SELECT 1", StubTools(),
        AgentConfig(chat_model=model, max_llm_calls=18, max_tool_calls=18),
        tmp_path / "trace.jsonl",
    )
    assert result.status == "completed", result.error
    assert result.candidate_sql == "SELECT 1"
    assert model._stats_saw_plan
    assert result.metrics.max_parallelism >= 3
    assert result.metrics.worker_calls == 5
    assert result.metrics.database_tool_calls == 1
    assert result.metrics.handoff_calls == 0
    entries = json.loads((tmp_path / "trace.board.json").read_text(encoding="utf-8"))
    assert any(entry["artifact_id"] for entry in entries["entries"])
    assert any(event["event"] == "board_read" and event["reader"] == "stats_analyst"
               for event in result.trace)
    assert any(event["event"] == "board_post" and event["kind"] == "decision"
               for event in result.trace)


def test_parallel_phase_limits_preserve_budget_for_auditor(tmp_path: Path) -> None:
    result = run_architecture(
        "board_swarm", "Optimize SELECT 1", StubTools(),
        AgentConfig(chat_model=NoisyBoardModel(), max_llm_calls=12, max_tool_calls=24),
        tmp_path / "limit.jsonl",
    )
    assert result.status == "completed", result.error
    assert result.metrics.llm_calls <= 12
    assert result.candidate_sql == "SELECT 1"
    assert sum(event["event"] == "phase_limit" for event in result.trace) == 3


def test_benchmark_automatically_publishes_measured_unvalidated_sql_once(tmp_path: Path) -> None:
    class MeasuredTools(StubTools):
        def benchmark(self, sql: str, n: int = 5) -> dict[str, Any]:
            return {"ok": True, "median_ms": 42.5, "sql": sql, "n": n}

    recorder = TraceRecorder(4, 4)
    registry = ToolRegistry(MeasuredTools(), recorder)
    board = SharedBoard(recorder, tmp_path / "measure.board.json")
    tool = next(item for item in _research_tools(registry, board, "query_rewriter")
                if item.name == "benchmark")
    tool.invoke({"sql": "SELECT 1", "n": 2}, config={"callbacks": [recorder]})
    assert recorder.metrics.tool_calls == 1
    assert recorder.metrics.database_tool_calls == 1
    assert board.artifact("sql-0001")["sql"] == "SELECT 1"
    entry = board.read("stats_analyst")["entries"][0]
    assert entry["kind"] == "candidate" and "equivalence pending" in entry["summary"]


def test_diagnostic_peers_can_post_evidence_on_fourth_turn(tmp_path: Path) -> None:
    result = run_architecture(
        "board_swarm", "Optimize SELECT 1", StubTools(),
        AgentConfig(chat_model=FourthTurnFindingModel(), max_llm_calls=24, max_tool_calls=48),
        tmp_path / "four-turns.jsonl",
    )
    assert result.status == "completed", result.error
    limits = next(event["model_calls"] for event in result.trace
                  if event["event"] == "board_phase_limits")
    assert limits == {"plan_analyst": 4, "stats_analyst": 4, "query_rewriter": 8,
                      "synthesis_rewriter": 3, "auditor": 5}
    posts = [event for event in result.trace if event["event"] == "board_post"
             and event["kind"] == "evidence"]
    assert {event["author"] for event in posts} >= {"plan_analyst", "stats_analyst"}
