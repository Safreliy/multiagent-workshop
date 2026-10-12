"""Checks for the statistical unit used by the SQL replay report."""

import json
import sys

import pytest

from benchmark import replay
from benchmark.replay import describe, summarize


def test_published_candidates_and_samples_reproduce_saved_summary() -> None:
    baseline, candidates = replay.load_sources()
    assert baseline and len(candidates) == 27
    materials = replay.ROOT / "presentation/materials"
    samples = [json.loads(line) for line in (materials / "samples.jsonl").read_text(encoding="utf-8").splitlines()]
    expected = json.loads((materials / "summary.json").read_text(encoding="utf-8"))
    assert replay.summarize(candidates, samples) == expected


def test_main_loads_local_dotenv_without_overriding_shell(monkeypatch, tmp_path) -> None:
    (tmp_path / ".env").write_text("BENCHMARK_ADMIN_DSN=postgresql://postgres@localhost:15433/postgres\n", encoding="utf-8")
    monkeypatch.setattr(replay, "ROOT", tmp_path)
    monkeypatch.delenv("BENCHMARK_ADMIN_DSN", raising=False)
    monkeypatch.setattr(sys, "argv", ["replay", "--output", str(tmp_path / "output")])
    observed = []
    monkeypatch.setattr(replay, "run", lambda *args, **kwargs: observed.append(replay.os.getenv("BENCHMARK_ADMIN_DSN")))
    replay.main()
    assert observed == ["postgresql://postgres@localhost:15433/postgres"]
    monkeypatch.setenv("BENCHMARK_ADMIN_DSN", "postgresql://postgres@localhost:15434/postgres")
    replay.main()
    assert observed[-1] == "postgresql://postgres@localhost:15434/postgres"


def test_run_uses_configured_database_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BENCHMARK_ADMIN_DSN", "postgresql://postgres@localhost:15433/postgres")
    monkeypatch.setenv("BENCHMARK_FIXTURE_DATABASE", "sql_agent_fixture_v2")
    monkeypatch.setattr(replay, "load_sources", lambda: ("SELECT 1", []))
    observed = []

    class OfflineEnvironment:
        def __init__(self, *args):
            observed.append(args)

        def attempt(self):
            raise RuntimeError("offline validation")

    monkeypatch.setattr(replay, "DatabaseEnvironment", OfflineEnvironment)
    with pytest.raises(RuntimeError, match="offline validation"):
        replay.run(tmp_path / "output", blocks=1, seed=42)
    assert observed == [("postgresql://postgres@localhost:15433/postgres", "sql_agent_fixture_v2")]


def test_describe_uses_interpolated_quartiles() -> None:
    assert describe([4.0, 1.0, 3.0, 2.0]) == {
        "n": 4,
        "median": 2.5,
        "p25": 1.75,
        "p75": 3.25,
        "min": 1.0,
        "max": 4.0,
    }


def test_group_median_uses_three_sql_solutions_not_timing_pair_pool() -> None:
    candidates = [
        {
            "id": f"model-single-{repetition}",
            "model": "Model",
            "architecture": "single",
            "original_repetition": repetition,
            "original_speedup": None,
            "sql_sha256": str(repetition),
        }
        for repetition in (1, 2, 3)
    ]
    samples = [
        {"ok": True, "candidate_id": "model-single-1", "baseline_ms": 100, "candidate_ms": 100, "paired_speedup": 1},
        {"ok": True, "candidate_id": "model-single-2", "baseline_ms": 300, "candidate_ms": 100, "paired_speedup": 3},
        {"ok": True, "candidate_id": "model-single-3", "baseline_ms": 500, "candidate_ms": 100, "paired_speedup": 5},
    ]
    result = summarize(candidates, samples)
    assert result["per_group"] == [
        {
            "model": "Model",
            "architecture": "single",
            "n_sql": 3,
            "median_of_sql_paired_speedups": 3,
            "min_sql_paired_speedup": 1,
            "max_sql_paired_speedup": 5,
        }
    ]
