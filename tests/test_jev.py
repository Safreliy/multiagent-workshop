from __future__ import annotations

import json
from typing import Any

import httpx

from benchmark.jev import PlanAdvisor


def test_plan_advisor_default_client_honours_proxy_environment(
    tmp_path: Any, monkeypatch: Any
) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "answers": {"preferred_plan": {"type": "choice", "choice": "left"}},
                "usage": {},
            }

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured["client_kwargs"] = kwargs

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def post(self, endpoint: str, **kwargs: Any) -> FakeResponse:
            captured["endpoint"] = endpoint
            return FakeResponse()

    monkeypatch.setattr("benchmark.jev.httpx.Client", FakeClient)
    advisor = PlanAdvisor(
        ReadOnlyPlanDatabase(),
        tmp_path / "jev.jsonl",
        api_key="secret",
    )

    result = advisor.rank_query_plans("SELECT 1", "SELECT 2")

    assert result["ok"] is True
    assert captured["client_kwargs"] == {"timeout": 60, "trust_env": True}


class ReadOnlyPlanDatabase:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def explain(self, sql: str) -> dict[str, Any]:
        self.calls.append(("explain", sql))
        return {"ok": True, "plan": [{"Node Type": "Seq Scan", "Plan Rows": 10}]}

    def get_indexes(self) -> dict[str, Any]:
        self.calls.append(("get_indexes", None))
        return {"ok": True, "indexes": []}

    def get_statistics(self) -> dict[str, Any]:
        self.calls.append(("get_statistics", None))
        return {"ok": True, "statistics": []}

    def get_capabilities(self) -> dict[str, Any]:
        self.calls.append(("get_capabilities", None))
        return {"ok": True, "freedom_profile": "query_only"}


def test_plan_advisor_sends_typed_decision_and_accounts_reads(tmp_path: Any) -> None:
    captured: dict[str, Any] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "answers": {
                    "preferred_plan": {
                        "type": "choice",
                        "choice": "left",
                        "confidence": 0.8,
                        "probabilities": {"left": 0.8, "right": 0.1, "uncertain": 0.1},
                    }
                },
                "usage": {"input_tokens": 321, "output_tokens": 20, "cost": 0.00002},
            },
        )

    database = ReadOnlyPlanDatabase()
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        advisor = PlanAdvisor(database, tmp_path / "nested" / "jev.jsonl", api_key="secret", client=client)
        result = advisor.rank_query_plans("SELECT 1", "SELECT 2", "avoid excess work")

    assert result == {
        "ok": True,
        "advisory_only": True,
        "choice": "left",
        "confidence": 0.8,
        "probabilities": {"left": 0.8, "right": 0.1, "uncertain": 0.1},
    }
    assert captured["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert captured["authorization"] == "Bearer secret"
    assert captured["payload"]["model"] == "typesafe/jev-1.13"
    assert captured["payload"]["questions"]["preferred_plan"]["type"] == "choice"
    assert captured["payload"]["state"]["left"]["sql"] == "SELECT 1"
    assert database.calls == [
        ("explain", "SELECT 1"),
        ("explain", "SELECT 2"),
        ("get_indexes", None),
        ("get_statistics", None),
        ("get_capabilities", None),
    ]
    assert advisor.metrics() == {
        "calls": 1,
        "api_calls": 1,
        "successful_calls": 1,
        "database_reads": 5,
        "input_tokens": 321,
        "output_tokens": 20,
        "cost_usd": 0.00002,
        "wall_clock_s": advisor.metrics()["wall_clock_s"],
    }
    event = json.loads((tmp_path / "nested" / "jev.jsonl").read_text(encoding="utf-8"))
    assert "secret" not in json.dumps(event)


def test_plan_advisor_rejects_invalid_typed_answer_without_leaking_body(tmp_path: Any) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"answers": {"preferred_plan": {"type": "choice", "choice": "fast"}}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        advisor = PlanAdvisor(ReadOnlyPlanDatabase(), tmp_path / "jev.jsonl", api_key="secret", client=client)
        result = advisor.rank_query_plans("SELECT 1", "SELECT 2")

    assert result == {"ok": False, "error": "ValueError: advisor unavailable or invalid input/response"}
    assert advisor.metrics()["successful_calls"] == 0
    assert advisor.metrics()["database_reads"] == 5


def test_plan_advisor_budget_stops_before_database_or_http(tmp_path: Any) -> None:
    database = ReadOnlyPlanDatabase()
    advisor = PlanAdvisor(database, tmp_path / "jev.jsonl", api_key="secret", max_calls=0)

    assert advisor.rank_query_plans("SELECT 1", "SELECT 2") == {
        "ok": False,
        "error": "Advisor call budget exhausted",
    }
    assert database.calls == []
    assert advisor.metrics()["calls"] == 0


def test_plan_advisor_compacts_verbose_statistics_explicitly(tmp_path: Any) -> None:
    class VerboseDatabase(ReadOnlyPlanDatabase):
        freedom_profile = "indexes"
        statement_timeout_ms = 15_000

        def explain(self, sql: str) -> dict[str, Any]:
            self.calls.append(("explain", sql))
            return {
                "ok": True,
                "plan": {"Plan": {"Node Type": "Seq Scan", "Relation Name": "orders"}},
            }

        def get_statistics(self) -> dict[str, Any]:
            self.calls.append(("get_statistics", None))
            columns = []
            for index in range(100):
                columns.append(
                    {
                        "table": "orders",
                        "column": f"column_{index}",
                        "null_fraction": 0,
                        "distinct_estimate": 100,
                        "correlation": 0.5,
                        "most_common_values": "x" * 2_000,
                        "most_common_frequencies": "y" * 2_000,
                        "histogram_bounds": "z" * 2_000,
                    }
                )
            return {"ok": True, "tables": [{"table": "orders"}], "columns": columns}

    captured: dict[str, Any] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "answers": {
                    "preferred_plan": {"type": "choice", "choice": "uncertain"}
                },
                "usage": {"input_tokens": 10, "output_tokens": 1, "cost": 0.0},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        advisor = PlanAdvisor(
            VerboseDatabase(),
            tmp_path / "jev.jsonl",
            api_key="secret",
            client=client,
            max_state_chars=20_000,
        )
        result = advisor.rank_query_plans("SELECT * FROM orders", "SELECT * FROM orders")

    assert result["ok"] is True
    statistics = captured["state"]["statistics"]
    assert statistics["columns_truncated"] is True
    assert statistics["omitted_column_count"] > 0
    assert statistics["columns"][0]["histogram_bounds"]["truncated"] is True
    assert captured["state"]["database_context"]["freedom_profile"] == "indexes"
    assert len(json.dumps(captured["state"])) <= 20_000


def test_plan_advisor_rejects_oversize_plans_without_truncation(tmp_path: Any) -> None:
    class HugePlanDatabase(ReadOnlyPlanDatabase):
        def explain(self, sql: str) -> dict[str, Any]:
            self.calls.append(("explain", sql))
            return {"ok": True, "plan": {"Filter": "x" * 5_000}}

    database = HugePlanDatabase()
    advisor = PlanAdvisor(
        database,
        tmp_path / "jev.jsonl",
        api_key="secret",
        max_state_chars=1_000,
    )

    result = advisor.rank_query_plans("SELECT 1", "SELECT 2")

    assert result["ok"] is False
    assert database.calls == [("explain", "SELECT 1"), ("explain", "SELECT 2")]
    assert advisor.metrics()["database_reads"] == 2
