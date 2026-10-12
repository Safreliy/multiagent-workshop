from __future__ import annotations

import os
from pathlib import Path

import pytest
import psycopg

from tools.environment import DatabaseEnvironment
from tools.postgres import PostgresTools, validate_candidate


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "WITH values_cte AS (SELECT 1 AS n) SELECT n FROM values_cte",
        "SELECT '; DELETE FROM customers' AS harmless_text",
        "SELECT count(*) FROM customers WHERE active",
        "(SELECT 1) UNION ALL (SELECT 2)",
    ],
)
def test_validator_accepts_read_only_queries(sql: str) -> None:
    assert validate_candidate(sql)["valid"] is True


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "SELECT 1; SELECT 2",
        "DELETE FROM customers",
        "WITH removed AS (DELETE FROM customers RETURNING id) SELECT * FROM removed",
        "SELECT * INTO temporary_table FROM customers",
        "SELECT * FROM customers FOR UPDATE",
        "SELECT pg_sleep(10)",
        "SELECT pg_advisory_lock(1)",
        "SELECT set_config('statement_timeout', '0', false)",
        "COPY customers TO STDOUT",
        "SELECT $1",
    ],
)
def test_validator_rejects_unsafe_or_unsupported_queries(sql: str) -> None:
    result = validate_candidate(sql)
    assert result["valid"] is False
    assert result["error"]


def test_constructor_validates_bounds_and_baseline() -> None:
    with pytest.raises(ValueError):
        PostgresTools(baseline_sql="SELECT 1", max_rows=0)
    with pytest.raises(ValueError):
        PostgresTools(baseline_sql="SELECT 1", statement_timeout_ms=99)
    with pytest.raises(ValueError):
        PostgresTools(baseline_sql="DELETE FROM customers")


def test_baseline_is_an_unordered_select() -> None:
    baseline = (Path(__file__).parents[1] / "db" / "baseline.sql").read_text("utf-8")
    validation = validate_candidate(baseline)
    assert validation["valid"] is True
    assert "ORDER BY" not in validation["normalized_sql"].upper()


def test_hint_comments_require_a_hint_profile() -> None:
    hinted = "/*+ SeqScan(customers) */ SELECT count(*) FROM customers"
    assert validate_candidate(hinted)["valid"] is False
    assert validate_candidate(hinted, allow_hints=True)["valid"] is True
    set_hint = "/*+ Set(statement_timeout 0) */ SELECT count(*) FROM customers"
    assert validate_candidate(set_hint, allow_hints=True)["valid"] is False


def test_freedom_profiles_expose_only_allowed_agent_tools() -> None:
    query_only = PostgresTools(baseline_sql="SELECT 1")
    indexes = PostgresTools(baseline_sql="SELECT 1", freedom_profile="indexes")
    hints = PostgresTools(baseline_sql="SELECT 1", freedom_profile="hints")

    assert "create_candidate_index" not in query_only.available_tools
    assert "create_candidate_index" in indexes.available_tools
    assert "create_candidate_index" not in hints.available_tools
    assert validate_candidate("/*+ SeqScan(customers) */ SELECT 1")["valid"] is False
    with pytest.raises(ValueError):
        PostgresTools(baseline_sql="SELECT 1", freedom_profile="unlimited")  # type: ignore[arg-type]


def test_partial_index_allows_typed_literals_but_not_nested_functions() -> None:
    from tools.postgres import CandidateValidationError
    predicate = PostgresTools._predicate_sql("orders", "created_at >= TIMESTAMP '2024-01-01'", {"created_at"})
    assert "2024-01-01" in predicate
    with pytest.raises(CandidateValidationError):
        PostgresTools._predicate_sql("orders", "CAST(pg_sleep(1) AS integer) = 0", {"created_at"})


@pytest.mark.skipif(
    os.getenv("SQL_AGENT_TEST_INTEGRATION") != "1",
    reason="set SQL_AGENT_TEST_INTEGRATION=1 after starting the Docker fixture",
)
def test_fixture_tools_and_equivalence() -> None:
    events: list[dict] = []
    tools = PostgresTools(metrics_hook=events.append, statement_timeout_ms=30_000)

    assert tools.get_schema()["ok"] is True
    assert tools.get_indexes()["ok"] is True
    assert tools.get_statistics()["ok"] is True
    assert tools.explain(tools.baseline_sql)["ok"] is True
    execution = tools.execute("SELECT count(*) AS count FROM customers")
    assert execution["rows"] == [[12000]]

    validation = tools.validate_candidate(tools.baseline_sql)
    assert validation == {"ok": True, "correct": True}

    same = tools.submit_candidate(tools.baseline_sql, n=1, warmup=0)
    assert same["ok"] is True
    assert same["correct"] is True
    assert same["speedup"] > 0

    wrong = tools.submit_candidate("SELECT 1", n=1, warmup=0)
    assert wrong["ok"] is True
    assert wrong["correct"] is False

    invalid = tools.submit_candidate("DELETE FROM customers", n=1, warmup=0)
    assert invalid["ok"] is True
    assert invalid["correct"] is False
    assert invalid["validation_error"] is True
    assert events


@pytest.mark.skipif(
    os.getenv("SQL_AGENT_TEST_INTEGRATION") != "1",
    reason="set SQL_AGENT_TEST_INTEGRATION=1 after starting the Docker fixture",
)
def test_equivalence_preserves_bag_cardinality_and_types() -> None:
    tools = PostgresTools(
        baseline_sql="SELECT NULL::integer AS x UNION ALL SELECT NULL::integer",
    )

    assert tools.validate_candidate(
        "SELECT NULL::integer AS x UNION ALL SELECT NULL::integer"
    ) == {"ok": True, "correct": True}
    missing_duplicate = tools.validate_candidate("SELECT NULL::integer AS x")
    assert missing_duplicate["correct"] is False
    assert "missing from candidate" in missing_duplicate["error"]
    different_type = tools.validate_candidate(
        "SELECT NULL::numeric AS x UNION ALL SELECT NULL::numeric"
    )
    assert different_type == {
        "ok": True,
        "correct": False,
        "error": "result column types differ from baseline",
    }


@pytest.mark.skipif(
    os.getenv("SQL_AGENT_TEST_V2_INTEGRATION") != "1",
    reason="set SQL_AGENT_TEST_V2_INTEGRATION=1 after starting docker-compose.v2.yml",
)
def test_v2_clone_isolation_index_actions_and_real_hints() -> None:
    baseline = "SELECT count(*) FROM orders WHERE status = 'completed'"
    environment = DatabaseEnvironment()
    with environment.attempt() as attempt:
        clone_names = (attempt.reference_database, attempt.candidate_database)
        reference = attempt.make_reference_tools(baseline)
        candidate = attempt.make_candidate_tools(baseline, "all", statement_timeout_ms=30_000)

        capabilities = candidate.get_capabilities()
        assert capabilities["ok"] is True
        assert capabilities["pg_hint_plan"]["available"] is True

        lookup = "SELECT * FROM orders AS o WHERE o.id = 1"
        hinted = "/*+ SeqScan(o) */ SELECT * FROM orders AS o WHERE o.id = 1"
        assert candidate.explain(lookup)["plan"]["Plan"]["Node Type"] == "Index Scan"
        hinted_plan = candidate.explain(hinted)["plan"]["Plan"]
        assert hinted_plan["Node Type"] == "Gather"
        assert hinted_plan["Plans"][0]["Node Type"] == "Seq Scan"
        assert reference.explain(hinted)["ok"] is False

        created = candidate.create_candidate_index(
            "orders", ["status", "created_at DESC"], include=["customer_id", "id"],
            predicate="status = 'completed'", fillfactor=95,
        )
        assert created["ok"] is True and created["created"] is True
        assert created["index"].startswith("agent_idx_")
        assert created["storage_bytes"] > 0
        assert not any(
            item["name"].startswith("agent_idx_")
            for item in reference.get_indexes()["indexes"]
        )
        assert any(
            item["name"] == created["index"]
            for item in candidate.get_indexes()["indexes"]
        )
        configured_fixture = candidate.configure_index("orders_customer_created_idx", 80)
        configured_candidate = candidate.configure_index(created["index"], 85)
        assert configured_fixture["ok"] is True
        assert configured_candidate["ok"] is True

        unsafe = candidate.create_candidate_index(
            "orders", ["status"], predicate="EXISTS (SELECT 1 FROM customers)",
        )
        assert unsafe["ok"] is False
        artifacts = candidate.experiment_artifacts()
        assert artifacts["index_build_time_ms"] >= 0
        assert artifacts["created_index_storage_bytes_cumulative"] == created["storage_bytes"]
        assert artifacts["current_candidate_index_storage_bytes"] > 0

        dropped = candidate.drop_candidate_index(created["index"])
        assert dropped["ok"] is True
        final_artifacts = candidate.experiment_artifacts()
        assert final_artifacts["current_candidate_index_storage_bytes"] == 0
        assert final_artifacts["created_index_storage_bytes_cumulative"] == created["storage_bytes"]

    with psycopg.connect(environment.admin_dsn) as admin:
        remaining = admin.execute(
            "SELECT datname FROM pg_database WHERE datname = ANY(%s)",
            (list(clone_names),),
        ).fetchall()
    assert remaining == []
