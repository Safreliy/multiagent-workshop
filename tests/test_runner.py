from benchmark.report import summarize
from benchmark.runner import paired_benchmark, parser, schedule


def test_board_swarm_pilot_cli_is_single_cell_with_bounded_outputs():
    args = parser().parse_args([
        "--architecture", "board_swarm", "--legacy-only-model", "--no-cost-budget",
        "--runs", "1", "--max-llm-calls", "24", "--max-tool-calls", "48",
        "--max-output-tokens", "4096",
    ])
    assert args.architecture == "board_swarm"
    assert args.legacy_only_model and args.no_cost_budget
    assert (args.runs, args.max_llm_calls, args.max_tool_calls, args.max_output_tokens) == (
        1, 24, 48, 4096,
    )


def test_schedule_has_complete_randomized_blocks():
    modes = ["single", "orchestrator", "swarm"]
    ordered = schedule(modes, 5, 42)
    assert ordered == schedule(modes, 5, 42)
    for repetition in range(1, 6):
        assert sorted(mode for mode, r in ordered if r == repetition) == sorted(modes)


def test_paired_benchmark_alternates_and_uses_medians():
    class Database:
        def __init__(self):
            self.calls = []
            self.values = {"base": iter([99, 6, 100, 9]), "fast": iter([99, 2, 80, 3])}

        def benchmark(self, sql, n, warmup):
            assert n == 1 and warmup == 0
            self.calls.append(sql)
            return {"median_ms": next(self.values[sql])}

    db = Database()
    result = paired_benchmark(db, "base", "fast", samples=3, warmup=1)
    assert result["speedup"] == 3
    assert db.calls == ["base", "fast", "base", "fast", "fast", "base", "base", "fast"]


def test_report_does_not_drop_failed_runs_from_resource_metrics():
    records = [
        {"architecture": "single", "correct": True, "speedup": 2, "wall_clock_s": 2,
         "metrics": {"llm_calls": 2}},
        {"architecture": "single", "correct": False, "speedup": None, "wall_clock_s": 8,
         "metrics": {"llm_calls": 10}},
    ]
    row = summarize(records)[0]
    assert row["correct"] == 1 and row["runs"] == 2
    assert row["speedup_median"] == 2
    assert row["llm_calls_median"] == 6
    assert row["wall_s_median"] == 5
    assert row["cost_usd_median"] is None


def test_reference_baseline_is_not_measured_on_tuned_database():
    class Database:
        def __init__(self, permitted, value):
            self.permitted, self.value = permitted, value

        def benchmark(self, sql, **kwargs):
            assert sql == self.permitted
            return {"median_ms": self.value}

    result = paired_benchmark(Database("fast", 2), "base", "fast", samples=3,
                              baseline_db=Database("base", 10))
    assert result["speedup"] == 5


def test_report_keeps_experiment_cells_separate():
    records = [{"architecture": "single", "model": model, "profile": profile, "advisor": advisor,
                "correct": True, "speedup": 2} for model in ("a", "b")
               for profile in ("query_only", "indexes") for advisor in ("off", "jev")]
    assert len(summarize(records)) == 8


def test_openrouter_model_config_does_not_publish_secrets(monkeypatch):
    from benchmark.config import models_from_env
    monkeypatch.setenv("OPENROUTER_MODELS", '["a/model", "b/model", "a/model"]')
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-test-key")
    monkeypatch.setenv("OPENROUTER_PROVIDER", '{"allow_fallbacks":false}')
    models = models_from_env()
    assert len(models) == 2
    assert models[0].extra_body == {"provider": {"allow_fallbacks": False}}
    assert "secret-test-key" not in str([m.public() for m in models])
    assert "secret-test-key" not in repr(models)


def test_openrouter_model_temperature_can_be_omitted(monkeypatch):
    from benchmark.config import models_from_env
    monkeypatch.setenv("OPENROUTER_MODELS", "router/luna")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    monkeypatch.setenv("MODEL_TEMPERATURES_JSON", '{"router/luna":null}')

    assert models_from_env()[0].temperature is None


def test_openrouter_matrix_can_include_legacy_endpoint(monkeypatch):
    from benchmark.config import models_from_env
    monkeypatch.setenv("OPENROUTER_MODELS", "router/model")
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-secret")
    monkeypatch.setenv("OPENAI_MODEL", "legacy-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://legacy.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "legacy-secret")

    assert [model.id for model in models_from_env()] == ["router/model"]
    mixed = models_from_env(include_legacy=True)
    assert [model.id for model in mixed] == ["router/model", "legacy-model"]
    assert mixed[0].api_key == "router-secret"
    assert mixed[1].api_key == "legacy-secret"
    assert "secret" not in str([model.public() for model in mixed])


def test_include_legacy_without_openrouter_list_does_not_duplicate(monkeypatch):
    from benchmark.config import models_from_env
    monkeypatch.delenv("OPENROUTER_MODELS", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "legacy-model")
    assert [model.id for model in models_from_env(include_legacy=True)] == ["legacy-model"]


def test_final_evaluation_uses_final_state_and_pristine_reference(monkeypatch, tmp_path):
    import agents
    from agents.types import ArchitectureResult, RunMetrics, AgentConfig
    from benchmark.runner import run_one

    result = ArchitectureResult("single", "completed", "SELECT 1", {"correct": True}, "done",
                                RunMetrics(input_tokens=10, output_tokens=5))
    monkeypatch.setattr(agents, "run_architecture", lambda *args, **kwargs: result)

    class Candidate:
        def validate_candidate(self, sql):
            return {"correct": True, "ok": True}

        def benchmark(self, sql, **kwargs):
            assert sql == "SELECT 1"
            return {"median_ms": 2}

        def experiment_artifacts(self):
            return {"actions": [{"action": "create_index"}]}

    class Reference:
        def benchmark(self, sql, **kwargs):
            assert sql == "SELECT 1 AS baseline"
            return {"median_ms": 10}

    record = run_one("single", 1, Candidate(), {"description": "task", "benchmark_samples": 3, "warmup": 1},
                     "SELECT 1 AS baseline", AgentConfig(), tmp_path, baseline_db=Reference(),
                     metadata={"model": "test", "profile": "indexes", "advisor": "off"})
    assert record["correct"] and record["speedup"] == 5
    assert record["environment"]["actions"] == [{"action": "create_index"}]
    assert (tmp_path / "run-01-single.json").exists()

    # An agent's earlier accepted submission must never bypass the final-state check.
    monkeypatch.setattr(Candidate, "validate_candidate", lambda *args: {"correct": False})
    record = run_one("single", 2, Candidate(), {"description": "task", "benchmark_samples": 3, "warmup": 1},
                     "SELECT 1 AS baseline", AgentConfig(), tmp_path, baseline_db=Reference())
    assert record["correct"] is False and record["speedup"] is None
