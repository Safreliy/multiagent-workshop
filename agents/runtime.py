from __future__ import annotations

import json
import inspect
import re
import threading
from collections.abc import Sequence
from typing import Any

import openai
from langchain.agents.middleware import wrap_model_call
from langchain_core.tools import BaseTool, StructuredTool

from .tracing import TraceRecorder
from .types import AgentConfig


class ToolRegistry:
    """Adapts PostgresTools to LangChain tools and retains submission artifacts."""

    def __init__(
        self,
        postgres_tools: Any,
        recorder: TraceRecorder,
        additional_tools: Sequence[BaseTool] = (),
    ) -> None:
        self.backend = postgres_tools
        self.recorder = recorder
        self._lock = threading.RLock()
        self.submissions: list[dict[str, Any]] = []
        self.standard_tools = self._build()
        discovered = self._discover_additional_tools()
        self.additional_tools = self._wrap_additional_tools([*discovered, *additional_tools])
        self.tools = [*self.standard_tools, *self.additional_tools]
        self.by_name = {item.name: item for item in self.tools}

    def _discover_additional_tools(self) -> list[BaseTool]:
        """Load optional experiment tools without coupling backends to LangChain."""
        discovered: list[BaseTool] = []
        try:
            inspect.getattr_static(self.backend, "available_tools")
        except AttributeError:
            available = ()
        else:
            available = getattr(self.backend, "available_tools")
        available = available() if callable(available) else available
        standard_names = {tool.name for tool in self.standard_tools}
        for name in available or ():
            if name in standard_names:
                continue
            method = getattr(self.backend, name)
            schema_tool = StructuredTool.from_function(
                method,
                name=name,
                description=method.__doc__ or f"Call the optional backend operation {name}.",
            )

            def invoke_method(_name: str = name, **arguments: Any) -> str:
                return self._call(_name, **arguments)

            discovered.append(
                StructuredTool(
                    name=name,
                    description=schema_tool.description,
                    args_schema=schema_tool.args_schema,
                    func=invoke_method,
                )
            )

        try:
            inspect.getattr_static(self.backend, "additional_agent_tools")
        except AttributeError:
            provider = None
        else:
            provider = getattr(self.backend, "additional_agent_tools")
        if provider is not None:
            tools = provider() if callable(provider) else provider
            discovered.extend(tools or [])
        return discovered

    def _wrap_additional_tools(self, tools: Sequence[BaseTool]) -> list[BaseTool]:
        reserved = {tool.name for tool in self.standard_tools}
        wrapped: list[BaseTool] = []
        for backend_tool in tools:
            if not isinstance(backend_tool, BaseTool):
                raise TypeError("additional_agent_tools must contain LangChain BaseTool instances")
            if backend_tool.name in reserved:
                raise ValueError(f"duplicate agent tool name: {backend_tool.name!r}")
            reserved.add(backend_tool.name)

            def invoke_backend(_tool: BaseTool = backend_tool, **arguments: Any) -> str:
                self.recorder.record(
                    "additional_tool_call",
                    tool=_tool.name,
                    arguments=arguments,
                )
                # Advisory tools may capture plans/statistics internally. Serialize them
                # with database tools so a parallel worker cannot change the environment
                # halfway through that snapshot.
                with self._lock:
                    # The public proxy already owns the callback lifecycle. Explicitly
                    # suppress inherited callbacks on the implementation tool so one
                    # model tool call consumes exactly one shared-budget slot.
                    result = _tool.invoke(arguments, config={"callbacks": []})
                self.recorder.record(
                    "additional_tool_result",
                    tool=_tool.name,
                    result=result,
                )
                if isinstance(result, str):
                    return result
                return json.dumps(result, ensure_ascii=False, default=str)

            wrapped.append(
                StructuredTool(
                    name=backend_tool.name,
                    description=backend_tool.description,
                    args_schema=backend_tool.args_schema,
                    func=invoke_backend,
                )
            )
        return wrapped

    def _call(self, name: str, **arguments: Any) -> str:
        self.recorder.database_call()
        # Parallel tool calls would contaminate timing measurements.
        with self._lock:
            result = getattr(self.backend, name)(**arguments)
        self.recorder.record("database_result", tool=name, arguments=arguments, result=result)
        if name == "submit_candidate":
            with self._lock:
                self.submissions.append({"sql": arguments["sql"], "result": result})
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, default=str)

    def _build(self) -> list[BaseTool]:
        def get_schema() -> str:
            """Return tables, columns, types and constraints for the benchmark database."""
            return self._call("get_schema")

        def get_indexes() -> str:
            """Return existing indexes for diagnosis and permitted tuning decisions."""
            return self._call("get_indexes")

        def get_statistics() -> str:
            """Return PostgreSQL planner statistics and table estimates."""
            return self._call("get_statistics")

        def explain(sql: str) -> str:
            """Run read-only EXPLAIN for a candidate SQL query."""
            return self._call("explain", sql=sql)

        def explain_analyze(sql: str) -> str:
            """Run read-only EXPLAIN ANALYZE for a candidate SQL query."""
            return self._call("explain_analyze", sql=sql)

        def execute(sql: str) -> str:
            """Execute read-only SQL and return a bounded result sample."""
            return self._call("execute", sql=sql)

        def benchmark(sql: str, n: int = 5) -> str:
            """Measure a read-only query repeatedly and return timing statistics."""
            return self._call("benchmark", sql=sql, n=n)

        def submit_candidate(sql: str, n: int = 5) -> str:
            """Deterministically verify equivalence and benchmark the final candidate."""
            return self._call("submit_candidate", sql=sql, n=n)

        descriptions = {
            "get_schema": get_schema.__doc__,
            "get_indexes": get_indexes.__doc__,
            "get_statistics": get_statistics.__doc__,
            "explain": explain.__doc__,
            "explain_analyze": explain_analyze.__doc__,
            "execute": execute.__doc__,
            "benchmark": benchmark.__doc__,
            "submit_candidate": submit_candidate.__doc__,
        }
        functions = [
            get_schema,
            get_indexes,
            get_statistics,
            explain,
            explain_analyze,
            execute,
            benchmark,
            submit_candidate,
        ]
        return [
            StructuredTool.from_function(
                function,
                name=function.__name__,
                description=descriptions[function.__name__] or function.__name__,
            )
            for function in functions
        ]

    def select(self, names: Sequence[str], *, include_additional: bool = False) -> list[BaseTool]:
        selected = [self.by_name[name] for name in names]
        if include_additional:
            selected.extend(self.additional_tools)
        return selected

    @property
    def candidate_sql(self) -> str | None:
        accepted = self._accepted_submission()
        return accepted["sql"] if accepted else None

    @property
    def submission(self) -> Any | None:
        accepted = self._accepted_submission()
        if accepted:
            return accepted["result"]
        return self.submissions[-1]["result"] if self.submissions else None

    def _accepted_submission(self) -> dict[str, Any] | None:
        for submission in reversed(self.submissions):
            result = submission["result"]
            if isinstance(result, dict) and result.get("ok") is True and result.get("correct") is True:
                return submission
        return None


def build_model(config: AgentConfig) -> Any:
    if config.chat_model is not None:
        return config.chat_model
    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {
        "model": config.model,
        "api_key": config.api_key,
        "timeout": config.request_timeout_s,
        "max_retries": 0,
        # Leave the SDK's httpx transport in charge so it honours the standard
        # HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY environment variables.
        # LangChain still owns and closes both clients normally.
        "http_socket_options": (),
    }
    if config.temperature is not None:
        kwargs["temperature"] = config.temperature
    if config.base_url:
        kwargs["base_url"] = config.base_url
    if config.max_tokens is not None:
        kwargs["max_tokens"] = config.max_tokens
    if config.extra_body:
        kwargs["extra_body"] = config.extra_body
    return ChatOpenAI(**kwargs)


def invoke_config(config: AgentConfig, recorder: TraceRecorder) -> dict[str, Any]:
    return {
        "callbacks": [recorder],
        "recursion_limit": config.recursion_limit,
    }


def budget_note(config: AgentConfig) -> str:
    return (
        f"\nShared hard budget: at most {config.max_llm_calls} total model calls and "
        f"{config.max_tool_calls} total tool calls across this run. Finish and submit "
        "within it."
    )


def transport_retry_middleware(config: AgentConfig, recorder: TraceRecorder) -> Any:
    """Retry only OpenAI transport failures; every attempt remains callback-visible."""

    @wrap_model_call(name="TransportRetryMiddleware")
    def retry_transport(request: Any, handler: Any) -> Any:
        for retry_number in range(config.max_model_retries + 1):
            try:
                remaining_llm = max(0, config.max_llm_calls - recorder.metrics.llm_calls)
                remaining_tools = max(0, config.max_tool_calls - recorder.metrics.tool_calls)
                prompt = request.system_prompt or ""
                live_budget = (
                    "\nLive shared budget before this call: "
                    f"{remaining_llm} model calls (including this one) and "
                    f"{remaining_tools} tool calls remain. "
                    "Checkpoint a promising candidate before either reaches zero."
                )
                return handler(request.override(system_prompt=prompt + live_budget))
            except (openai.APIConnectionError, openai.APITimeoutError) as exc:
                if retry_number >= config.max_model_retries:
                    raise
                recorder.model_retry(
                    retry_number=retry_number + 1,
                    max_retries=config.max_model_retries,
                    error=exc,
                )
        raise AssertionError("unreachable")

    return retry_transport


def final_text(state: Any) -> str:
    if isinstance(state, dict):
        messages = state.get("messages") or []
        if messages:
            content = getattr(messages[-1], "content", messages[-1])
            if isinstance(content, str):
                return content
            return json.dumps(content, ensure_ascii=False, default=str)
    return str(state)


def compact_payload(
    task: str,
    hypothesis: str,
    evidence: list[str],
    candidate_sql: str | None,
) -> dict[str, Any]:
    return {
        "task": task,
        "hypothesis": hypothesis,
        "evidence": evidence,
        "candidate_sql": candidate_sql,
    }


def extract_sql(text: str) -> str | None:
    """Best-effort diagnostic fallback; only submitted SQL is considered successful."""
    fenced = re.findall(r"```(?:sql)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced[-1].strip()
    return None
