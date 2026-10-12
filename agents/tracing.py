from __future__ import annotations

import json
import math
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from .cost_budget import BudgetExceeded, CostBudget
from .types import RunMetrics

def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump(mode="json"))
        except Exception:  # noqa: BLE001 - trace serialization must never break a run
            return repr(value)
    if hasattr(value, "to_json"):
        try:
            return _jsonable(value.to_json())
        except Exception:  # noqa: BLE001 - trace serialization must never break a run
            return repr(value)
    return repr(value)


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    return json.dumps(_jsonable(content), ensure_ascii=False)


def _request_input_upper_bound(messages: list[list[Any]], kwargs: dict[str, Any]) -> int:
    """Conservative token bound using UTF-8 bytes plus protocol overhead.

    OpenAI-compatible tokenizers operate on byte-backed vocabularies, so charging one
    token per UTF-8 byte is intentionally pessimistic. Tool schemas are included from
    invocation parameters because they are billed input even though they are not chat
    messages.
    """
    invocation = kwargs.get("invocation_params") or {}
    request_extras = {
        key: invocation[key]
        for key in ("tools", "functions", "tool_choice", "response_format", "stop")
        if key in invocation
    }
    payload = {"messages": _jsonable(messages), "request": _jsonable(request_extras)}
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    message_count = sum(len(batch) for batch in messages)
    return len(encoded) + 2_048 + 64 * message_count


def _valid_usage(value: Any, input_key: str, output_key: str) -> tuple[int, int] | None:
    if not isinstance(value, dict) or input_key not in value or output_key not in value:
        return None
    input_tokens, output_tokens = value[input_key], value[output_key]
    if isinstance(input_tokens, bool) or isinstance(output_tokens, bool):
        return None
    if not isinstance(input_tokens, (int, float)) or not isinstance(output_tokens, (int, float)):
        return None
    if (
        not math.isfinite(input_tokens)
        or not math.isfinite(output_tokens)
        or input_tokens < 0
        or output_tokens < 0
        or int(input_tokens) != input_tokens
        or int(output_tokens) != output_tokens
    ):
        return None
    return int(input_tokens), int(output_tokens)


def _reported_cost_usd(response: Any) -> float | None:
    """Return explicit provider-reported cost when present; never infer one here."""
    candidates: list[float] = []

    def inspect_usage(value: Any) -> None:
        if not isinstance(value, dict):
            return
        for key in ("cost", "cost_usd", "total_cost"):
            item = value.get(key)
            if (
                isinstance(item, (int, float))
                and not isinstance(item, bool)
                and math.isfinite(item)
                and item >= 0
            ):
                candidates.append(float(item))

    llm_output = getattr(response, "llm_output", None) or {}
    inspect_usage(llm_output.get("token_usage"))
    inspect_usage(llm_output.get("usage"))
    for batch in getattr(response, "generations", []) or []:
        for generation in batch:
            message = getattr(generation, "message", None)
            metadata = getattr(message, "response_metadata", None) or {}
            inspect_usage(metadata.get("usage"))
            inspect_usage(metadata.get("token_usage"))
    return max(candidates) if candidates else None


class TraceRecorder(BaseCallbackHandler):
    """Thread-safe LangChain callback that records budgets, usage and trajectory."""

    raise_error = True

    def __init__(
        self,
        max_llm_calls: int,
        max_tool_calls: int,
        trace_path: Path | None = None,
        *,
        cost_budget: CostBudget | None = None,
        model: str = "",
        input_price_ceiling: float | None = None,
        output_price_ceiling: float | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        super().__init__()
        self.max_llm_calls = max_llm_calls
        self.max_tool_calls = max_tool_calls
        self.events: list[dict[str, Any]] = []
        self.metrics = RunMetrics()
        self._lock = threading.RLock()
        self._started = time.perf_counter()
        self._active_workers = 0
        self._active_llm = 0
        self.cost_budget = cost_budget
        self.model = model
        self.input_price_ceiling = input_price_ceiling
        self.output_price_ceiling = output_price_ceiling
        self.max_output_tokens = max_output_tokens
        self._cost_reservations: dict[str, float] = {}
        self.trace_path = trace_path
        if trace_path is not None:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            trace_path.write_text("", encoding="utf-8")

    def record(self, event: str, **payload: Any) -> None:
        with self._lock:
            self.events.append(
                {
                    "sequence": len(self.events) + 1,
                    "timestamp": time.time(),
                    "elapsed_s": time.perf_counter() - self._started,
                    "event": event,
                    **{key: _jsonable(value) for key, value in payload.items()},
                }
            )
            if self.trace_path is not None:
                with self.trace_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(self.events[-1], ensure_ascii=False) + "\n")

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        request_id = str(run_id)
        with self._lock:
            if self.metrics.llm_calls >= self.max_llm_calls:
                self.record("budget_exceeded", budget="llm_calls")
                raise BudgetExceeded(f"LLM call budget ({self.max_llm_calls}) exceeded")
            if self.cost_budget is not None:
                input_bound = _request_input_upper_bound(messages, kwargs)
                reservation = self.cost_budget.reserve(
                    request_id,
                    model=self.model,
                    input_tokens_upper_bound=input_bound,
                    output_tokens_upper_bound=int(self.max_output_tokens or 0),
                    input_price_ceiling=float(self.input_price_ceiling or 0),
                    output_price_ceiling=float(self.output_price_ceiling or 0),
                )
                self._cost_reservations[request_id] = reservation
                self.metrics.cost_reserved_usd += reservation
            self.metrics.llm_calls += 1
            self._active_llm += 1
            self.metrics.max_parallelism = max(self.metrics.max_parallelism, self._active_llm)
        self.record(
            "llm_start",
            run_id=str(run_id),
            model=serialized.get("name") or serialized.get("id"),
            messages=[[_message_text(message) for message in batch] for batch in messages],
            message_objects=[[_jsonable(message) for message in batch] for batch in messages],
            cost_reservation_usd=self._cost_reservations.get(request_id),
        )

    def on_llm_end(self, response: Any, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        input_tokens = output_tokens = total_tokens = 0
        usage_found = False
        usage_valid = True
        outputs: list[str] = []
        output_messages: list[Any] = []
        for batch in getattr(response, "generations", []) or []:
            for generation in batch:
                message = getattr(generation, "message", None)
                if message is not None:
                    outputs.append(_message_text(message))
                    output_messages.append(_jsonable(message))
                    usage = getattr(message, "usage_metadata", None) or {}
                    usage_found = usage_found or bool(usage)
                    parsed = _valid_usage(usage, "input_tokens", "output_tokens")
                    if parsed is None:
                        usage_valid = False
                    else:
                        input_tokens += parsed[0]
                        output_tokens += parsed[1]
                        reported_total = usage.get("total_tokens", sum(parsed))
                        valid_total = not (
                            isinstance(reported_total, bool)
                            or not isinstance(reported_total, (int, float))
                            or not math.isfinite(reported_total)
                            or int(reported_total) != reported_total
                            or reported_total > sum(parsed)
                        )
                        if not valid_total:
                            usage_valid = False
                        total_tokens += int(reported_total or sum(parsed)) if valid_total else sum(parsed)
        if not total_tokens:
            raw_usage = (getattr(response, "llm_output", None) or {}).get("token_usage", {})
            usage_found = usage_found or bool(raw_usage)
            parsed = _valid_usage(raw_usage, "prompt_tokens", "completion_tokens")
            if parsed is not None:
                input_tokens, output_tokens = parsed
                reported_total = raw_usage.get("total_tokens", sum(parsed))
                if (
                    not isinstance(reported_total, bool)
                    and isinstance(reported_total, (int, float))
                    and math.isfinite(reported_total)
                    and int(reported_total) == reported_total
                    and reported_total <= sum(parsed)
                ):
                    total_tokens = int(reported_total or sum(parsed))
                    usage_valid = True
        usage_valid = usage_found and usage_valid
        settlement = None
        request_id = str(run_id)
        if self.cost_budget is not None and usage_valid:
            settlement = self.cost_budget.settle(
                request_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reported_cost_usd=_reported_cost_usd(response),
            )
        with self._lock:
            self.metrics.input_tokens += input_tokens
            self.metrics.output_tokens += output_tokens
            self.metrics.total_tokens += total_tokens or input_tokens + output_tokens
            if not usage_valid:
                self.metrics.usage_complete = False
            if settlement is not None:
                self.metrics.cost_reserved_usd -= float(settlement["reserved_cost_usd"] or 0)
                self.metrics.cost_estimated_usd += float(settlement["estimated_cost_usd"] or 0)
                self.metrics.cost_reported_usd += float(settlement["reported_cost_usd"] or 0)
                self.metrics.cost_accounted_usd += float(settlement["accounted_cost_usd"] or 0)
                if settlement["reported_cost_usd"] is None:
                    self.metrics.cost_reported_complete = False
            elif self.cost_budget is not None:
                self.metrics.cost_reported_complete = False
            self._active_llm = max(0, self._active_llm - 1)
        self.record(
            "llm_end",
            run_id=str(run_id),
            outputs=outputs,
            output_messages=output_messages,
            usage={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens or input_tokens + output_tokens,
            },
            usage_complete=usage_valid,
            cost=settlement,
        )

    def on_llm_error(self, error: BaseException, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        with self._lock:
            self._active_llm = max(0, self._active_llm - 1)
            self.metrics.usage_complete = False
            if self.cost_budget is not None:
                self.metrics.cost_reported_complete = False
        self.record(
            "llm_error",
            run_id=str(run_id),
            error=repr(error),
            reservation_retained_usd=self._cost_reservations.get(str(run_id)),
        )

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        with self._lock:
            if self.metrics.tool_calls >= self.max_tool_calls:
                self.record("budget_exceeded", budget="tool_calls")
                raise BudgetExceeded(f"tool call budget ({self.max_tool_calls}) exceeded")
            self.metrics.tool_calls += 1
        self.record(
            "tool_start",
            run_id=str(run_id),
            name=serialized.get("name"),
            input=kwargs.get("inputs", input_str),
        )

    def on_tool_end(self, output: Any, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        self.record("tool_end", run_id=str(run_id), output=output)

    def on_tool_error(self, error: BaseException, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        self.record("tool_error", run_id=str(run_id), error=repr(error))

    @contextmanager
    def worker(self, name: str, handoff: dict[str, Any]) -> Iterator[None]:
        with self._lock:
            self.metrics.worker_calls += 1
            self._active_workers += 1
            self.metrics.max_parallelism = max(self.metrics.max_parallelism, self._active_workers)
        self.record("worker_start", worker=name, handoff=handoff)
        try:
            yield
        except BaseException as exc:
            self.record("worker_error", worker=name, error=repr(exc))
            raise
        finally:
            self.record("worker_end", worker=name)
            with self._lock:
                self._active_workers -= 1

    def handoff(self, source: str, destination: str, state: dict[str, Any]) -> None:
        with self._lock:
            self.metrics.handoff_calls += 1
            self.metrics.worker_calls += 1
        self.record("handoff", source=source, destination=destination, state=state)

    def database_call(self) -> None:
        with self._lock:
            self.metrics.database_tool_calls += 1

    def model_retry(self, retry_number: int, max_retries: int, error: BaseException) -> None:
        with self._lock:
            self.metrics.model_retries += 1
        self.record(
            "model_retry",
            retry_number=retry_number,
            max_retries=max_retries,
            error_type=type(error).__name__,
            error=str(error),
        )

    def finish(self) -> None:
        self.metrics.wall_clock_s = time.perf_counter() - self._started
        self.record("run_end", metrics=self.metrics.to_dict())

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as stream:
            for event in self.events:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
