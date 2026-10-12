from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage

from agents.cost_budget import CostBudget
from agents.tracing import TraceRecorder


@pytest.mark.parametrize("usage", [None, {"input_tokens": 10},
    {"input_tokens": 1.5, "output_tokens": 2},
    {"input_tokens": 10, "output_tokens": 2, "total_tokens": 99}])
def test_incomplete_usage_never_releases_reserved_money(tmp_path, usage):
    budget = CostBudget(1, tmp_path / "budget.json")
    recorder = TraceRecorder(10, 10, cost_budget=budget, model="test",
                             input_price_ceiling=1, output_price_ceiling=1, max_output_tokens=32)
    request_id = uuid4()
    recorder.on_chat_model_start({}, [[HumanMessage("hello")]], run_id=request_id)
    reserved = budget.reserved_usd
    message = SimpleNamespace(content="answer", usage_metadata=usage, response_metadata={})
    response = SimpleNamespace(generations=[[SimpleNamespace(message=message)]], llm_output={})
    recorder.on_llm_end(response, run_id=request_id)
    assert budget.reserved_usd == reserved > 0
    assert budget.spent_usd == 0
    assert recorder.metrics.usage_complete is False
    assert recorder.metrics.cost_reported_complete is False


def test_transport_error_and_next_attempt_keep_separate_reservations(tmp_path):
    budget = CostBudget(1, tmp_path / "budget.json")
    recorder = TraceRecorder(10, 10, cost_budget=budget, model="test",
                             input_price_ceiling=1, output_price_ceiling=1, max_output_tokens=32)
    first, second = uuid4(), uuid4()
    recorder.on_chat_model_start({}, [[HumanMessage("hello")]], run_id=first)
    reserved = budget.reserved_usd
    recorder.on_llm_error(TimeoutError("uncertain response"), run_id=first)
    recorder.on_chat_model_start({}, [[HumanMessage("hello")]], run_id=second)
    assert budget.reserved_usd == pytest.approx(reserved * 2)
    assert len(budget.snapshot()["calls"]) == 2
    assert recorder.metrics.cost_reported_complete is False
