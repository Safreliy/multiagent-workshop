import json

from presentation import live_demo


def test_followup_uses_previous_candidate_for_unchanged_multiline_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(live_demo, "RUNS", tmp_path)
    previous = tmp_path / "012345abcdef"
    previous.mkdir()
    baseline = "SELECT\n  1 AS answer; -- user comment"
    (previous / "request.json").write_text(
        json.dumps({"baseline_sql": baseline}), encoding="utf-8"
    )
    (previous / "live.sql").write_text(
        "SELECT 1 AS answer", encoding="utf-8"
    )

    assert live_demo._previous_candidate(previous.name, baseline) == "SELECT 1 AS answer"
    assert live_demo._previous_candidate(previous.name, "SELECT 2") is None
    assert live_demo._previous_candidate("../012345abcdef", baseline) is None
