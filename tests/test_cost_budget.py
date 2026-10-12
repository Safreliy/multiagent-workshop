from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from agents.cost_budget import BudgetExceeded, CostBudget
from benchmark.config import Model, configure_openrouter_budget
from benchmark.runner import dollar_budget_should_stop


def test_budget_settles_success_at_price_ceiling_and_persists_distinctions(tmp_path):
    path = tmp_path / "budget.json"
    budget = CostBudget(1.0, path)
    reserved = budget.reserve(
        "one",
        model="example/model",
        input_tokens_upper_bound=10_000,
        output_tokens_upper_bound=1_000,
        input_price_ceiling=2.0,
        output_price_ceiling=10.0,
    )
    assert reserved == pytest.approx(0.03)

    settled = budget.settle("one", input_tokens=100, output_tokens=20)
    assert settled["estimated_cost_usd"] == pytest.approx(0.0004)
    assert settled["reported_cost_usd"] is None
    assert budget.spent_usd == pytest.approx(0.0004)
    assert budget.reserved_usd == 0
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["calls"]["one"]["reserved_cost_usd"] == pytest.approx(0.03)
    assert persisted["calls"]["one"]["estimated_cost_usd"] == pytest.approx(0.0004)


def test_unknown_call_cost_retains_reservation_and_concurrent_calls_cannot_overspend(tmp_path):
    budget = CostBudget(0.05, tmp_path / "budget.json")

    def reserve(index):
        try:
            budget.reserve(
                str(index),
                model="example/model",
                input_tokens_upper_bound=10_000,
                output_tokens_upper_bound=10_000,
                input_price_ceiling=1.0,
                output_price_ceiling=1.0,
            )
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = list(pool.map(reserve, range(8)))
    assert sum(accepted) == 2
    assert budget.reserved_usd == pytest.approx(0.04)
    assert budget.available_usd == pytest.approx(0.01)


def test_reservation_bound_anomaly_halts_later_requests(tmp_path):
    budget = CostBudget(1.0, tmp_path / "budget.json")
    budget.reserve(
        "one",
        model="example/model",
        input_tokens_upper_bound=10,
        output_tokens_upper_bound=10,
        input_price_ceiling=1.0,
        output_price_ceiling=1.0,
    )
    budget.settle("one", input_tokens=100, output_tokens=100)
    assert budget.snapshot()["halted"] is True
    assert budget.available_usd == 0
    with pytest.raises(BudgetExceeded, match="halted"):
        budget.reserve(
            "two",
            model="example/model",
            input_tokens_upper_bound=1,
            output_tokens_upper_bound=1,
            input_price_ceiling=1.0,
            output_price_ceiling=1.0,
        )


def test_openrouter_budget_requires_prices_and_sets_provider_guards():
    model = Model(
        "example/model",
        "https://openrouter.ai/api/v1",
        "secret",
        {"provider": {"max_price": {"prompt": 1.0, "completion": 8.0}}},
        input_price=2.0,
        output_price=10.0,
    )
    configure_openrouter_budget(model)
    provider = model.extra_body["provider"]
    assert provider["max_price"] == {"prompt": 1.0, "completion": 8.0, "request": 0.0}
    assert provider["require_parameters"] is True
    assert model.input_price == 1.0 and model.output_price == 8.0

    missing = Model("example/model", "https://openrouter.ai/api/v1", "secret")
    with pytest.raises(ValueError, match="MODEL_PRICES_JSON"):
        configure_openrouter_budget(missing)
    unsupported = Model("example/model", "https://example.test/v1", "secret", input_price=1, output_price=1)
    with pytest.raises(ValueError, match="only the OpenRouter"):
        configure_openrouter_budget(unsupported)


def test_matrix_stop_signal_ignores_ordinary_run_budget_status():
    ordinary_call_limit_state = {"halted": False, "calls": {}}
    assert dollar_budget_should_stop(ordinary_call_limit_state) is False
    assert dollar_budget_should_stop({
        "halted": False,
        "calls": {"x": {"status": "denied"}},
    }) is True
