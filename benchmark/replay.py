"""Replay saved final SQL without an agent or LLM calls.

Each saved query is checked against the baseline on a single isolated v2 fixture
clone. Timing uses randomized complete blocks and paired AB/BA measurements so
changes in host load can be seen in the paired baseline samples.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv

from tools.environment import DatabaseEnvironment, DEFAULT_ADMIN_DSN, DEFAULT_FIXTURE_DATABASE


ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "DeepSeek": ROOT / "results/comparison-20260921/runs",
    "Luna": ROOT / "results/luna-proxy-20260921-231311/runs",
    "Sol": ROOT / "results/sol-budget20-20260922-113757/runs",
}
ARCHITECTURES = ("single", "orchestrator", "swarm")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def save_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def percentile(values: list[float], fraction: float) -> float:
    """Linearly interpolate order statistics; no optional numerical dependency."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot summarize an empty sample")
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def describe(values: list[float]) -> dict[str, float | int]:
    if not values:
        raise ValueError("cannot summarize an empty sample")
    return {
        "n": len(values),
        "median": statistics.median(values),
        "p25": percentile(values, 0.25),
        "p75": percentile(values, 0.75),
        "min": min(values),
        "max": max(values),
    }


def load_sources() -> tuple[str, list[dict[str, Any]]]:
    baseline = (ROOT / "db/hard-baseline.sql").read_text(encoding="utf-8")
    expected_hash = sha256(baseline)
    published_manifest = ROOT / "presentation/materials/manifest.json"
    if published_manifest.is_file() and not all(
        (folder / "manifest.json").is_file() for folder in SOURCES.values()
    ):
        manifest = json.loads(published_manifest.read_text(encoding="utf-8"))
        if manifest.get("baseline_sha256") != expected_hash:
            raise RuntimeError("published baseline hash differs")
        candidates = []
        for record in manifest["candidates"]:
            sql_path = (published_manifest.parent / record["source_sql"]).resolve()
            final_sql = sql_path.read_text(encoding="utf-8")
            if sha256(final_sql) != record["sql_sha256"]:
                raise RuntimeError(f"published SQL hash differs: {sql_path}")
            candidates.append({**record, "source_sql": str(sql_path), "sql": final_sql})
        groups: dict[tuple[str, str], int] = defaultdict(int)
        for candidate in candidates:
            groups[(candidate["model"], candidate["architecture"])] += 1
        if len(candidates) != 27 or len(groups) != 9 or set(groups.values()) != {3}:
            raise RuntimeError(f"expected 27 SQL and nine groups of three, got {dict(groups)}")
        return baseline, candidates
    candidates: list[dict[str, Any]] = []
    for model, folder in SOURCES.items():
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("baseline_sha256") != expected_hash:
            raise RuntimeError(f"baseline hash differs in {folder}")
        for path in sorted(folder.glob("run-*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("model") == "~openai/gpt-luna-latest" and model == "DeepSeek":
                continue  # Nine denied API attempts are not candidate SQL.
            if not (record.get("status") == "completed" and record.get("correct") is True):
                raise RuntimeError(f"source run is not a validated success: {path}")
            if record.get("profile") != "query_only" or record.get("advisor") != "off":
                raise RuntimeError(f"source run used a different environment: {path}")
            if record.get("architecture") not in ARCHITECTURES:
                raise RuntimeError(f"unknown architecture in {path}")
            sql_path = path.with_suffix(".sql")
            final_sql = sql_path.read_text(encoding="utf-8")
            if final_sql.strip() != record.get("candidate_sql", "").strip():
                raise RuntimeError(f"SQL file and record differ: {path}")
            candidates.append(
                {
                    "id": f"{model.lower()}-{record['architecture']}-{record['repetition']}",
                    "model": model,
                    "architecture": record["architecture"],
                    "original_repetition": record["repetition"],
                    "original_speedup": record.get("speedup"),
                    "source_record": str(path),
                    "source_sql": str(sql_path),
                    "source_record_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "sql_sha256": sha256(final_sql),
                    "sql": final_sql,
                }
            )
    groups: dict[tuple[str, str], int] = defaultdict(int)
    for candidate in candidates:
        groups[(candidate["model"], candidate["architecture"])] += 1
    if len(candidates) != 27 or len(groups) != 9 or set(groups.values()) != {3}:
        raise RuntimeError(f"expected 27 SQL and nine groups of three, got {dict(groups)}")
    return baseline, candidates


def summarize(candidates: list[dict[str, Any]], samples: list[dict[str, Any]]) -> dict[str, Any]:
    by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in samples:
        if row.get("ok"):
            by_candidate[row["candidate_id"]].append(row)

    per_sql = []
    for candidate in candidates:
        rows = by_candidate[candidate["id"]]
        if not rows:
            continue
        baseline_values = [row["baseline_ms"] for row in rows]
        candidate_values = [row["candidate_ms"] for row in rows]
        paired_values = [row["paired_speedup"] for row in rows]
        per_sql.append(
            {
                "candidate_id": candidate["id"],
                "model": candidate["model"],
                "architecture": candidate["architecture"],
                "original_repetition": candidate["original_repetition"],
                "original_speedup": candidate["original_speedup"],
                "sql_sha256": candidate["sql_sha256"],
                "baseline_ms": describe(baseline_values),
                "candidate_ms": describe(candidate_values),
                "paired_speedup": describe(paired_values),
                "ratio_of_medians": statistics.median(baseline_values)
                / statistics.median(candidate_values),
            }
        )

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in per_sql:
        groups[(row["model"], row["architecture"])].append(row)
    per_group = []
    for (model, architecture), rows in sorted(groups.items()):
        # The three original agent runs, rather than 30 correlated timing pairs,
        # are the independent units for architecture-level comparisons.
        values = [row["paired_speedup"]["median"] for row in rows]
        per_group.append(
            {
                "model": model,
                "architecture": architecture,
                "n_sql": len(rows),
                "median_of_sql_paired_speedups": statistics.median(values),
                "min_sql_paired_speedup": min(values),
                "max_sql_paired_speedup": max(values),
            }
        )
    return {"per_sql": per_sql, "per_group": per_group}


def render_report(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    lines = [
        "# Replayed final SQL: isolated PostgreSQL measurements",
        "",
        f"Measured at: {utc_now()}. Seed: {manifest['seed']}; blocks: {manifest['blocks']}.",
        "",
        "Each of the 27 saved final SQL queries was validated against the baseline on one "
        "fresh clone of the frozen v2 fixture. Each block contains every SQL once "
        "in random order. A pair warms baseline and candidate once each, then times "
        "both with alternating order. Timing is query execution plus full result fetch; "
        "the read-only connection setup and SQL validation are outside the timer. "
        "All SQL runs sequentially with no LLM calls.",
        "",
        "The headline for each architecture is the median of the three independently "
        "generated SQL solutions' median paired speedups. Ten timing pairs per SQL "
        "describe timing variation and are not ten independent agent solutions.",
        "",
        "| Model | Architecture | Valid SQL | Median paired speedup | Range across SQL |",
        "|---|---|---:|---:|---:|",
    ]
    for row in summary["per_group"]:
        lines.append(
            f"| {row['model']} | {row['architecture']} | {row['n_sql']}/3 | "
            f"{row['median_of_sql_paired_speedups']:.3f}× | "
            f"{row['min_sql_paired_speedup']:.3f}–{row['max_sql_paired_speedup']:.3f}× |"
        )
    lines += [
        "",
        "| SQL | Original speedup | Replayed median paired speedup | "
        "Paired IQR | Candidate median ms | Baseline median ms |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in summary["per_sql"]:
        speed = row["paired_speedup"]
        original = row["original_speedup"]
        lines.append(
            f"| {row['candidate_id']} | {original:.3f}× | {speed['median']:.3f}× | "
            f"{speed['p25']:.3f}–{speed['p75']:.3f}× | "
            f"{row['candidate_ms']['median']:.1f} | {row['baseline_ms']['median']:.1f} |"
        )
    lines += [
        "",
        "Raw paired timings and timestamps are in `samples.jsonl`; checksums and "
        "source paths are in `manifest.json`. The spread of baseline timings in each "
        "pair provides a direct view of changing DB or host load. These timings "
        "replay existing SQL, so they do not include agent time or model cost.",
        "",
    ]
    return "\n".join(lines)


def run(output: Path, *, blocks: int, seed: int) -> None:
    if output.exists():
        raise RuntimeError(f"output directory already exists: {output}")
    baseline, candidates = load_sources()
    output.mkdir(parents=True)
    manifest = {
        "created_at": utc_now(),
        "blocks": blocks,
        "seed": seed,
        "baseline_sha256": sha256(baseline),
        "baseline_path": str(ROOT / "db/hard-baseline.sql"),
        "fixture": "sql_agent_fixture_v2",
        "profile": "query_only",
        "statement_timeout_ms": 60_000,
        "warmup_per_query_per_pair": 1,
        "pair_order": "alternating AB/BA across successive pairs",
        "candidates": [{k: v for k, v in row.items() if k != "sql"} for row in candidates],
    }
    save_json(output / "manifest.json", manifest)
    started = time.perf_counter()
    status = {
        "state": "validating",
        "started_at": utc_now(),
        "completed_pairs": 0,
        "expected_pairs": blocks * len(candidates),
        "validated_sql": 0,
        "total_sql": len(candidates),
    }
    save_json(output / "status.json", status)
    samples: list[dict[str, Any]] = []
    environment = DatabaseEnvironment(
        os.getenv("BENCHMARK_ADMIN_DSN", DEFAULT_ADMIN_DSN),
        os.getenv("BENCHMARK_FIXTURE_DATABASE", DEFAULT_FIXTURE_DATABASE),
    )
    try:
        with environment.attempt() as databases:
            # A single clone makes both sides of every pair share physical data,
            # indexes and cache. The other clone is created by the existing safe
            # environment manager but is intentionally unused.
            manifest["measurement_database"] = databases.candidate_database
            manifest["same_clone_for_baseline_and_candidate"] = True
            tools = databases.make_candidate_tools(
                baseline, "query_only", statement_timeout_ms=60_000
            )
            with psycopg.connect(databases.candidate_dsn, connect_timeout=5) as conn:
                manifest["postgres_version"] = conn.execute("SHOW server_version").fetchone()[0]
                manifest["settings"] = {
                    setting: conn.execute("SELECT current_setting(%s)", (setting,)).fetchone()[0]
                    for setting in (
                        "shared_buffers", "work_mem", "max_parallel_workers_per_gather",
                        "effective_cache_size", "jit",
                    )
                }
            save_json(output / "manifest.json", manifest)

            for candidate in candidates:
                result = tools.validate_candidate(candidate["sql"])
                if not (result.get("ok") and result.get("correct")):
                    raise RuntimeError(f"validation failed for {candidate['id']}: {result}")
                status["validated_sql"] += 1
                save_json(output / "status.json", status)

            status["state"] = "measuring"
            save_json(output / "status.json", status)
            rng = random.Random(seed)
            pair_index = 0
            with (output / "samples.jsonl").open("w", encoding="utf-8", buffering=1) as log:
                for block in range(1, blocks + 1):
                    schedule = list(candidates)
                    rng.shuffle(schedule)
                    for candidate in schedule:
                        order = ("baseline", "candidate") if pair_index % 2 == 0 else (
                            "candidate", "baseline"
                        )
                        row: dict[str, Any] = {
                            "block": block,
                            "pair_index": pair_index,
                            "candidate_id": candidate["id"],
                            "model": candidate["model"],
                            "architecture": candidate["architecture"],
                            "started_at": utc_now(),
                            "order": list(order),
                        }
                        try:
                            with tools._readonly_connection() as connection:
                                queries = {"baseline": baseline, "candidate": candidate["sql"]}
                                # Warm-up follows the measured order, giving each SQL
                                # the same cache treatment within the pair.
                                for name in order:
                                    tools._time_query(connection, queries[name])
                                for name in order:
                                    row[f"{name}_ms"] = tools._time_query(
                                        connection, queries[name]
                                    )
                            row["paired_speedup"] = row["baseline_ms"] / row["candidate_ms"]
                            row["ok"] = True
                        except Exception as exc:
                            row["ok"] = False
                            row["error"] = " ".join(str(exc).split())[:500]
                        row["finished_at"] = utc_now()
                        log.write(json.dumps(row, ensure_ascii=False) + "\n")
                        samples.append(row)
                        pair_index += 1
                        status["completed_pairs"] = pair_index
                        status["last_candidate_id"] = candidate["id"]
                        status["last_sample_ok"] = row["ok"]
                        save_json(output / "status.json", status)
                        if not row["ok"]:
                            raise RuntimeError(f"measurement failed for {candidate['id']}: {row['error']}")
                    print(f"block {block}/{blocks} finished at {utc_now()}", flush=True)

        summary = summarize(candidates, samples)
        save_json(output / "summary.json", summary)
        (output / "report.md").write_text(render_report(manifest, summary), encoding="utf-8")
        status.update(
            state="finished",
            finished_at=utc_now(),
            elapsed_s=round(time.perf_counter() - started, 3),
        )
        save_json(output / "status.json", status)
    except Exception as exc:
        status.update(
            state="failed",
            finished_at=utc_now(),
            elapsed_s=round(time.perf_counter() - started, 3),
            error=" ".join(str(exc).split())[:800],
        )
        save_json(output / "status.json", status)
        raise


def main() -> None:
    load_dotenv(ROOT / ".env", encoding="utf-8-sig")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blocks", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.blocks < 1:
        parser.error("--blocks must be positive")
    run(args.output.resolve(), blocks=args.blocks, seed=args.seed)


if __name__ == "__main__":
    main()
