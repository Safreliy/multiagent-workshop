"""Exercise the real SDK/callback boundary without any network traffic."""
import asyncio
import json

import httpx2
import openai
import pytest
from langchain_openai import ChatOpenAI

from agents.cost_budget import BudgetExceeded, CostBudget
from agents.runtime import build_model
from agents.tracing import TraceRecorder
from agents.types import AgentConfig


def test_large_tool_schema_is_budgeted_before_sdk_sends_http(tmp_path, monkeypatch):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json={
            "id": "chatcmpl-test", "object": "chat.completion", "created": 1,
            "model": "test-model", "choices": [{"index": 0,
                "message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 2, "total_tokens": 102},
        })

    sync = openai.DefaultHttpxClient(transport=httpx2.MockTransport(respond), trust_env=False)
    asynchronous = openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(respond), trust_env=False)
    def model_factory(**kwargs):
        kwargs["http_client"] = sync
        kwargs["http_async_client"] = asynchronous
        return ChatOpenAI(**kwargs)

    monkeypatch.setattr("langchain_openai.ChatOpenAI", model_factory)
    ledger = CostBudget(0.02, tmp_path / "budget.json")
    recorder = TraceRecorder(10, 10, cost_budget=ledger, model="test-model",
                             input_price_ceiling=1.0, output_price_ceiling=1.0,
                             max_output_tokens=32)
    config = AgentConfig(model="test-model", api_key="fake-key",
                         base_url="https://model.invalid/v1", max_tokens=32,
                         cost_budget=ledger, input_price_ceiling=1.0, output_price_ceiling=1.0)
    try:
        model = build_model(config)
        model.invoke("hello", config={"callbacks": [recorder]})
        assert len(requests) == 1
        assert ledger.spent_usd == pytest.approx(0.000102)
        tool = {"type": "function", "function": {"name": "inspect",
            "description": "Long diagnostic schema " * 3000,
            "parameters": {"type": "object", "properties": {}}}}
        with pytest.raises(BudgetExceeded):
            model.bind_tools([tool]).invoke("hello", config={"callbacks": [recorder]})
        assert len(requests) == 1, "Unaffordable tool schema must never reach the transport"
        assert any(call["status"] == "denied" for call in ledger.snapshot()["calls"].values())
    finally:
        sync.close()
        asyncio.run(asynchronous.aclose())
