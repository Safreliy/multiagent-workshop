"""Parallel peer investigation with a small, versioned shared artifact board.

This deliberately differs from the handoff swarm: peers run concurrently, can see
each other's published evidence while still active, and share immutable SQL artifacts.
The only synchronized part of SQL exploration is each database call (ToolRegistry).
"""
from __future__ import annotations

import json
import threading
import time
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import wrap_model_call
from langchain_core.tools import BaseTool, StructuredTool

from .orchestrator import ALL_DATABASE_TOOLS
from .prompts import (
    AUDITOR_PROMPT,
    PLAN_ANALYST_PROMPT,
    QUERY_REWRITER_PROMPT,
    STATS_ANALYST_PROMPT,
)
from .runtime import (
    ToolRegistry,
    budget_note,
    final_text,
    invoke_config,
    transport_retry_middleware,
)
from .tracing import BudgetExceeded, TraceRecorder
from .types import AgentConfig


class SharedBoard:
    """Append-only findings and immutable SQL artifacts, with monotone revisions."""

    def __init__(self, recorder: TraceRecorder, path: Path | None) -> None:
        self.recorder = recorder
        self.path = path
        self._lock = threading.RLock()
        self._entries: list[dict[str, Any]] = []
        self._artifacts: dict[str, dict[str, Any]] = {}

    def publish(
        self, author: str, kind: str, summary: str, evidence: list[str] | None = None,
        candidate_sql: str | None = None,
    ) -> dict[str, Any]:
        if kind not in {"hypothesis", "evidence", "candidate", "decision"}:
            raise ValueError("unsupported finding kind")
        if kind == "candidate" and candidate_sql is None:
            raise ValueError("candidate findings require candidate_sql")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 1200:
            raise ValueError("summary must contain 1 to 1200 characters")
        if evidence is None:
            evidence = []
        if not isinstance(evidence, list) or len(evidence) > 8 or any(
            not isinstance(item, str) or len(item) > 600 for item in evidence
        ):
            raise ValueError("evidence must be at most 8 short strings")
        if candidate_sql is not None and (
            not isinstance(candidate_sql, str) or not candidate_sql.strip() or len(candidate_sql) > 50000
        ):
            raise ValueError("candidate_sql must be a nonempty SQL string under 50000 characters")
        with self._lock:
            revision = len(self._entries) + 1
            artifact_id = f"sql-{revision:04d}" if candidate_sql is not None else None
            if artifact_id:
                self._artifacts[artifact_id] = {
                    "id": artifact_id, "revision": revision, "author": author,
                    "sql": candidate_sql,
                }
            entry = {
                "revision": revision, "author": author, "kind": kind,
                "summary": summary.strip(), "evidence": evidence,
                "artifact_id": artifact_id,
            }
            self._entries.append(entry)
            self._persist()
            self.recorder.record("board_post", **entry)
            return deepcopy(entry)

    def read(self, reader: str, since_revision: int = 0) -> dict[str, Any]:
        if not isinstance(since_revision, int) or since_revision < 0:
            raise ValueError("since_revision must be a nonnegative integer")
        with self._lock:
            revision = len(self._entries)
            # Bounded context, but cursor-based reads can collect all posts incrementally.
            entries = [deepcopy(item) for item in self._entries if item["revision"] > since_revision][:24]
            next_revision = entries[-1]["revision"] if entries else since_revision
            self.recorder.record(
                "board_read", revision=revision, reader=reader,
                since_revision=since_revision, returned=len(entries),
            )
            return {"revision": revision, "next_revision": next_revision,
                    "entries": entries, "more": next_revision < revision}

    def artifact(self, artifact_id: str) -> dict[str, Any]:
        with self._lock:
            if artifact_id not in self._artifacts:
                raise ValueError(f"unknown artifact_id: {artifact_id}")
            return deepcopy(self._artifacts[artifact_id])

    def candidate_ids(self) -> list[str]:
        with self._lock:
            return [item["artifact_id"] for item in self._entries if item["artifact_id"]]

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"revision": len(self._entries), "entries": self._entries,
                        "artifacts": self._artifacts}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        for attempt in range(5):
            try:
                temporary.replace(self.path)
                break
            except PermissionError:
                # Windows scanners can hold the previous snapshot briefly.
                if attempt == 4:
                    raise
                time.sleep(0.04 * (attempt + 1))


def _board_tools(board: SharedBoard, role: str) -> list[BaseTool]:
    def read_board(since_revision: int = 0) -> str:
        """Read up to 24 findings; if more, call again with next_revision as cursor."""
        return json.dumps(board.read(role, since_revision), ensure_ascii=False)

    def post_finding(summary: str, evidence: list[str], candidate_sql: str | None = None) -> str:
        """Share a short finding and optional complete SQL; returns immutable artifact ID."""
        return json.dumps(
            board.publish(role, "candidate" if candidate_sql else "evidence", summary,
                          evidence, candidate_sql), ensure_ascii=False,
        )

    def get_artifact(artifact_id: str) -> str:
        """Fetch complete immutable candidate SQL by its ID from the shared board."""
        return json.dumps(board.artifact(artifact_id), ensure_ascii=False)

    return [StructuredTool.from_function(func) for func in
            (read_board, post_finding, get_artifact)]


def _research_tools(registry: ToolRegistry, board: SharedBoard, role: str) -> list[BaseTool]:
    """Same SQL toolkit for peers; successful measurements become shared artifacts."""
    selected = registry.select(
        [name for name in ALL_DATABASE_TOOLS if name != "submit_candidate"],
        include_additional=True,
    )
    benchmark_tool = registry.by_name["benchmark"]

    def measured_benchmark(sql: str, n: int = 5) -> str:
        result = benchmark_tool.invoke({"sql": sql, "n": n}, config={"callbacks": []})
        try:
            parsed = json.loads(result) if isinstance(result, str) else result
        except ValueError:
            parsed = {}
        if isinstance(parsed, dict) and parsed.get("ok") is True:
            median = parsed.get("median_ms", "unknown")
            board.publish(role, "candidate", f"Benchmarked SQL at {median} ms; final equivalence pending",
                          [f"benchmark(n={n}) median_ms={median}"], sql)
        return result

    benchmark_proxy = StructuredTool.from_function(
        measured_benchmark, name="benchmark", description=benchmark_tool.description,
    )
    return [benchmark_proxy if tool.name == "benchmark" else tool for tool in selected]


PARALLEL_ROLES = {
    "plan_analyst": (
        PLAN_ANALYST_PROMPT,
        "Plan bottleneck and join-order hypothesis. Publish observed nodes and row errors.",
    ),
    "stats_analyst": (
        STATS_ANALYST_PROMPT,
        "Selectivity/index/statistics hypothesis. Publish actual evidence, not a guessed speedup.",
    ),
    "query_rewriter": (
        QUERY_REWRITER_PROMPT,
        "Independent query rewrite hypothesis: reduce duplicated work, joins, or aggregation. "
        "Publish the full candidate SQL as an artifact if useful.",
    ),
}

PEER_SUFFIX = """
You are in a parallel shared-board experiment. Other specialists are running now.
Your initial direction is independent; do not wait for other peers to finish. Call
read_board(0) early, and read again after your own investigation to incorporate any
in-flight results. Use post_finding to share useful evidence promptly; publish complete
candidate SQL as an immutable artifact when ready. Your final response is a compact
JSON object with hypothesis, evidence, candidate_sql (nullable). You have the same
database tools as every researcher, but stay focused on your specialty. Do not call submit_candidate.
Board reading and posting are tools and count toward the shared tool budget.
"""

SYNTHESIS_SUFFIX = """
The parallel researchers have finished. Use their versioned shared-board findings and
fetch candidate SQL artifacts by ID. Combine evidence, test only a promising refinement,
and publish the strongest complete SQL. Return compact JSON with hypothesis, evidence,
candidate_sql. The Auditor performs final deterministic validation; do not submit.
"""


class PhaseCallLimit(Exception):
    """A specialist consumed its own allocation; other phases retain shared budget."""


def phase_limit(role: str, maximum: int) -> Any:
    used = 0
    lock = threading.Lock()

    @wrap_model_call(name=f"{role}_phase_limit")
    def limit(request: Any, handler: Any) -> Any:
        nonlocal used
        with lock:
            if used >= maximum:
                raise PhaseCallLimit(f"{role} reached its {maximum} model-call phase limit")
            used += 1
            remaining = maximum - used
        note = (
            f"\nLocal phase: {remaining} model responses remain after this one. "
            "Publish a useful finding as soon as evidence is available. "
        )
        if remaining == 0:
            note += (
                "This is your last response in this role. Publish a SQL artifact or finding "
                "now; if you are Auditor, submit_candidate now."
            )
        return handler(request.override(system_prompt=(request.system_prompt or "") + note))

    return limit


def _extract_output(board: SharedBoard, role: str, output: str) -> None:
    """Preserve specialist output even when a model ends without using post_finding."""
    try:
        parsed = json.loads(output.strip().removeprefix("```json").removesuffix("```").strip())
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        evidence = parsed.get("evidence", [])
        if not isinstance(evidence, list):
            evidence = []
        evidence = [str(item)[:600] for item in evidence[:8]]
        candidate = parsed.get("candidate_sql")
        if not isinstance(candidate, str) or not candidate.strip() or len(candidate) > 50000:
            candidate = None
        board.publish(role, "candidate" if candidate else "evidence",
                      str(parsed.get("hypothesis") or "Worker result")[:1200], evidence, candidate)
    elif output.strip():
        board.publish(role, "evidence", output.strip()[:1200])


def run_board_swarm(
    task: str, model: Any, registry: ToolRegistry, config: AgentConfig,
    recorder: TraceRecorder,
) -> str:
    board_path = recorder.trace_path.with_suffix(".board.json") if recorder.trace_path else None
    board = SharedBoard(recorder, board_path)
    recorder.record("architecture_start", architecture="board_swarm", task=task)
    if config.max_llm_calls < 6:
        raise BudgetExceeded("board_swarm requires at least six model calls across five phases")
    # Rewrite needs more turns to materialize SQL; reserve independent audit capacity.
    audit_limit = max(3 if config.max_llm_calls >= 10 else 2,
                      round(config.max_llm_calls * 5 / 24))
    synthesis_limit = max(1, round(config.max_llm_calls * 3 / 24))
    diagnostic_limit = max(1, round(config.max_llm_calls * 4 / 24))
    rewrite_limit = config.max_llm_calls - audit_limit - synthesis_limit - 2 * diagnostic_limit
    limits = {
        "plan_analyst": diagnostic_limit,
        "stats_analyst": diagnostic_limit,
        "query_rewriter": rewrite_limit,
    }
    recorder.record("board_phase_limits", model_calls={**limits,
                    "synthesis_rewriter": synthesis_limit, "auditor": audit_limit})
    for role, (_, direction) in PARALLEL_ROLES.items():
        board.publish(role, "hypothesis", direction)

    def run_peer(role: str) -> str:
        prompt, direction = PARALLEL_ROLES[role]
        agent = create_agent(
            model=model,
            tools=[*_research_tools(registry, board, role), *_board_tools(board, role)],
            system_prompt=(prompt + PEER_SUFFIX + budget_note(config) +
                           f"\nYou have at most {limits[role]} local model turns, including "
                           "responses after tool calls. Post evidence before your last turn."),
            name=role,
            middleware=[transport_retry_middleware(config, recorder), phase_limit(role, limits[role])],
        )
        handoff = {"task": task, "hypothesis": direction, "evidence": [], "candidate_sql": None}
        with recorder.worker(role, handoff):
            output = final_text(agent.invoke(
                {"messages": [{"role": "user", "content": json.dumps(handoff, ensure_ascii=False)}]},
                config=invoke_config(config, recorder),
            ))
            _extract_output(board, role, output)
            return output

    failures: list[Exception] = []
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="board-swarm") as pool:
        futures = {pool.submit(run_peer, role): role for role in PARALLEL_ROLES}
        for future in as_completed(futures):
            try:
                future.result()
            except PhaseCallLimit as error:
                recorder.record("phase_limit", worker=futures[future], error=str(error))
            except Exception as error:  # Preserve partial board and allow peers to finish.
                failures.append(error)
                recorder.record("peer_error", worker=futures[future], error=repr(error))
    if any(isinstance(error, BudgetExceeded) for error in failures):
        raise next(error for error in failures if isinstance(error, BudgetExceeded))
    if failures and len(failures) == len(PARALLEL_ROLES):
        raise failures[0]

    candidate_ids = board.candidate_ids()

    # Synthesis is deliberately separate from the parallel discovery phase. It sees
    # the board, not three full peer conversations, and may refine candidates.
    rewriter = create_agent(
        model=model,
        tools=[*_research_tools(registry, board, "synthesis_rewriter"),
               *_board_tools(board, "synthesis_rewriter")],
        system_prompt=(QUERY_REWRITER_PROMPT + SYNTHESIS_SUFFIX + budget_note(config) +
                       f"\nYou have at most {synthesis_limit} local model turns. "
                       "Publish the best candidate before your last turn."),
        name="synthesis_rewriter",
        middleware=[transport_retry_middleware(config, recorder),
                    phase_limit("synthesis_rewriter", synthesis_limit)],
    )
    compact = board.read("synthesis_rewriter")
    try:
        with recorder.worker("synthesis_rewriter", {"board_revision": compact["revision"]}):
            result = rewriter.invoke(
                {"messages": [{"role": "user", "content": json.dumps({
                    "task": task, "board": compact, "candidate_artifacts": candidate_ids,
                    "instruction": "Read candidate artifacts, combine independent findings, publish your strongest SQL. "
                                   "Finish promptly so auditor can validate.",
                }, ensure_ascii=False)}]}, config=invoke_config(config, recorder),
            )
            _extract_output(board, "synthesis_rewriter", final_text(result))
    except PhaseCallLimit as error:
        recorder.record("phase_limit", worker="synthesis_rewriter", error=str(error))

    if not board.candidate_ids():
        recorder.record("board_no_candidate", revision=board.read("runtime")["revision"])
        return "No candidate SQL was published by the parallel investigators or synthesis."

    final_board = board.read("auditor")
    auditor = create_agent(
        model=model,
        tools=[*registry.select(ALL_DATABASE_TOOLS, include_additional=True),
               *_board_tools(board, "auditor")],
        system_prompt=(AUDITOR_PROMPT + "\nUse read_board and get_artifact; submit the most "
                       "promising published SQL and report actual measured validation. "
                       "Other peers cannot submit." + budget_note(config) +
                       f"\nYou have at most {audit_limit} local model turns. "
                       "Fetch and submit a candidate early."),
        name="auditor", middleware=[transport_retry_middleware(config, recorder),
                                    phase_limit("auditor", audit_limit)],
    )
    try:
        with recorder.worker("auditor", {"board_revision": final_board["revision"]}):
            result = auditor.invoke(
                {"messages": [{"role": "user", "content": json.dumps({
                    "task": task, "board": final_board, "candidate_artifacts": board.candidate_ids(),
                    "instruction": "Fetch SQL artifact then submit_candidate; choose only measured correct SQL.",
                }, ensure_ascii=False)}]}, config=invoke_config(config, recorder),
            )
            output = final_text(result)
            board.publish("auditor", "decision", output[:1200] or "Auditor finished")
            return output
    except PhaseCallLimit as error:
        recorder.record("phase_limit", worker="auditor", error=str(error))
        if registry.candidate_sql:
            board.publish("auditor", "decision", "Accepted submission is already recorded")
            return "Auditor reached its phase limit after an accepted deterministic submission."
        raise
