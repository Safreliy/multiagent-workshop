"""Public chat inputs must be checked before starting a DB/LLM job."""
import json
import os
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from presentation import live_demo


@pytest.fixture
def body(monkeypatch):
    monkeypatch.setattr(live_demo, "_load_models", lambda: [{"id": "test-model"}])
    return {"architecture": "board_swarm", "model": "test-model", "baseline_sql": "SELECT 1"}


@pytest.mark.parametrize("sql", [
    "SELECT 1", "SELECT 1; -- pasted from editor", "WITH a AS (SELECT 1 AS x) SELECT x FROM a",
])
def test_pasted_readonly_sql_is_accepted(body, sql):
    body["baseline_sql"] = sql
    assert live_demo._validate_submission(body)[2] == sql


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders", "SELECT 1; SELECT 2", "SELECT * INTO tmp FROM orders",
    "WITH gone AS (DELETE FROM orders RETURNING *) SELECT * FROM gone",
    "SELECT * FROM orders FOR UPDATE", "SELECT pg_sleep(1000)",
])
def test_rejects_mutations_multiple_statements_and_disallowed_functions(body, sql):
    body["baseline_sql"] = sql
    with pytest.raises(ValueError, match="SQL rejected"):
        live_demo._validate_submission(body)


@pytest.mark.parametrize("key,value", [
    ("baseline_sql", " "), ("baseline_sql", None), ("baseline_sql", "x" * 20001),
    ("instruction", []), ("instruction", "x" * 10000), ("model", "not-configured"),
    ("previous_job_id", "../../.env"), ("previous_job_id", "a" * 13),
])
def test_invalid_inputs_fail_before_a_job_is_created(body, key, value):
    body[key] = value
    with pytest.raises(ValueError):
        live_demo._validate_submission(body)


def test_followup_reference_is_an_exact_run_id(body):
    body.update(previous_job_id="012345abcdef", instruction="Try another join order")
    assert live_demo._validate_submission(body)[3] == "012345abcdef"


def test_error_redaction_handles_url_decoded_proxy_password(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "socks5://test-user:p%40ss@127.0.0.1:1234")
    monkeypatch.setenv("BENCHMARK_ADMIN_DSN", "postgresql://test-admin:db-secret@localhost/test")
    text = live_demo._public_text("test-user p@ss p%40ss test-admin db-secret")
    assert "test-user" not in text and "p@ss" not in text and "p%40ss" not in text
    assert "db-secret" not in text and "test-admin" not in text


@pytest.mark.skipif(
    os.getenv("SQL_AGENT_TEST_V2_INTEGRATION") != "1",
    reason="set SQL_AGENT_TEST_V2_INTEGRATION=1 after starting the PostgreSQL fixture",
)
def test_live_worker_accepts_more_than_5000_rows_without_llm(tmp_path, monkeypatch):
    """Exercise the live preflight and exact validator with a large result."""
    import benchmark.runner
    import tools.environment
    from psycopg.conninfo import make_conninfo

    from tools.postgres import PostgresTools

    baseline = "SELECT generate_series(1, 6001) AS n"
    candidate = "SELECT n FROM generate_series(1, 6001) AS src(n)"
    (tmp_path / "request.json").write_text(
        json.dumps({"baseline_sql": baseline}), encoding="utf-8"
    )

    model = SimpleNamespace(
        id="integration-no-llm", base_url="http://127.0.0.1:9/v1",
        api_key="unused", extra_body={}, temperature="task_default",
    )
    monkeypatch.setattr(live_demo, "_model_for_id", lambda _model_id: (model, False))

    class LocalAttempt:
        reference_database = "postgres"
        candidate_database = "postgres"

        def __init__(self, dsn):
            self.dsn = dsn

        def make_reference_tools(self, sql, **kwargs):
            return PostgresTools(dsn=self.dsn, baseline_sql=sql, **kwargs)

        def make_candidate_tools(self, sql, _profile, **kwargs):
            return PostgresTools(dsn=self.dsn, baseline_sql=sql, **kwargs)

    class LocalEnvironment:
        def __init__(self, admin_dsn, _fixture_database):
            self.dsn = make_conninfo(admin_dsn, dbname="postgres")

        def attempt(self):
            return nullcontext(LocalAttempt(self.dsn))

    monkeypatch.setattr(tools.environment, "DatabaseEnvironment", LocalEnvironment)

    def fake_run_one(_architecture, _repetition, db, _task, sql, _config, _directory,
                     *, baseline_db, **_kwargs):
        assert sql.lower() == baseline.lower()
        assert baseline_db.validate_candidate(candidate) == {"ok": True, "correct": True}
        shorter = baseline_db.validate_candidate("SELECT generate_series(1, 6000) AS n")
        assert shorter["ok"] and not shorter["correct"]
        submission = db.submit_candidate(candidate, n=1, warmup=0)
        assert submission["ok"] and submission["correct"]
        return {"status": "completed", "correct": True, "speedup": submission["speedup"]}

    monkeypatch.setattr(benchmark.runner, "run_one", fake_run_one)

    assert live_demo._run_worker("single", model.id, tmp_path) == 0
    assert json.loads((tmp_path / "stage.json").read_text(encoding="utf-8"))["stage"] == "finished"
