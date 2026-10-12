"""Aggregate real runs, including failures, without an LLM judge."""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path


def median(values):
    valid = [value for value in values if value is not None]
    return statistics.median(valid) if valid else None


def summarize(records: list[dict]) -> list[dict]:
    rows = []
    dimensions = ("model", "profile", "advisor", "task_id", "architecture")
    groups = sorted({tuple(r.get(key, "legacy") for key in dimensions) for r in records})
    for group in groups:
        runs = [r for r in records if tuple(r.get(key, "legacy") for key in dimensions) == group]
        if not runs:
            continue
        correct = [r for r in runs if r.get("correct")]
        rows.append({
            **dict(zip(dimensions, group)),
            "runs": len(runs),
            "correct": len(correct),
            "speedup_median": median([r.get("speedup") for r in correct]),
            "speedup_min": min((r["speedup"] for r in correct), default=None),
            "speedup_max": max((r["speedup"] for r in correct), default=None),
            "wall_s_median": median([r.get("wall_clock_s") for r in runs]),
            "agent_wall_s_median": median([r.get("agent_wall_clock_s") for r in runs]),
            "usage_known_runs": sum(r.get("metrics", {}).get("input_tokens") is not None
                                    and r.get("metrics", {}).get("output_tokens") is not None for r in runs),
            "input_tokens_median": median([r.get("metrics", {}).get("input_tokens") for r in runs]),
            "output_tokens_median": median([r.get("metrics", {}).get("output_tokens") for r in runs]),
            "llm_calls_median": median([r.get("metrics", {}).get("llm_calls") for r in runs]),
            "tool_calls_median": median([r.get("metrics", {}).get("tool_calls") for r in runs]),
            "worker_calls_median": median([r.get("metrics", {}).get("worker_calls") for r in runs]),
            "handoff_calls_median": median([r.get("metrics", {}).get("handoff_calls") for r in runs]),
            "max_parallelism": max((r.get("metrics", {}).get("max_parallelism", 0) for r in runs), default=0),
            "cost_usd_median": median([r.get("cost_usd") for r in runs]),
            "advisor_calls_median": median([r.get("advisor_metrics", {}).get("calls") for r in runs]),
            "advisor_cost_usd_median": median([r.get("advisor_metrics", {}).get("cost_usd") for r in runs]),
            "attempt_with_reset_s_median": median([r.get("attempt_with_reset_s") for r in runs]),
            "index_build_time_ms_median": median([r.get("environment", {}).get("index_build_time_ms") for r in runs]),
            "final_candidate_index_storage_bytes_median": median([r.get("environment", {}).get("current_candidate_index_storage_bytes") for r in runs]),
        })
    return rows


def format_number(value, decimals=1):
    return "—" if value is None else f"{value:.{decimals}f}"


def render_markdown(rows: list[dict]) -> str:
    lines = [
        "| Model / profile / advisor | Architecture | Correct | Speedup median (min–max) | Agent / total s | Tokens in / out | Usage known | LLM | Tools | Workers / handoffs | Parallel max | Main / advisor USD | Index build ms |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        speed = format_number(r["speedup_median"], 2)
        interval = f'{format_number(r["speedup_min"], 2)}–{format_number(r["speedup_max"], 2)}'
        lines.append(
            f'| {r["model"]} / {r["profile"]} / {r["advisor"]} | {r["architecture"]} | {r["correct"]}/{r["runs"]} | {speed} ({interval}) | '
            f'{format_number(r["agent_wall_s_median"])} / {format_number(r["wall_s_median"])} | {format_number(r["input_tokens_median"], 0)} / '
            f'{format_number(r["output_tokens_median"], 0)} | {r["usage_known_runs"]}/{r["runs"]} | {format_number(r["llm_calls_median"])} | '
            f'{format_number(r["tool_calls_median"])} | {format_number(r["worker_calls_median"])} / '
            f'{format_number(r["handoff_calls_median"])} | {r["max_parallelism"]} | '
            f'{format_number(r["cost_usd_median"], 6)} / {format_number(r["advisor_cost_usd_median"], 6)} | '
            f'{format_number(r["index_build_time_ms_median"])} |'
        )
    lines.extend(["", "Speedup includes only correct submitted candidates. Failed attempts remain in call/time statistics.",
                  "Token/cost medians cover only known usage/prices; usage coverage is shown explicitly. Unknown values are —.",
                  "Compare agent time for architecture overhead; total time additionally includes deterministic final evaluation of submitted candidates.",
                  "For v2, baseline is measured on a pristine reference clone; candidate timing includes the final physical changes. Index build time is not included in query speedup.",
                  "Each speedup is the ratio of paired baseline/candidate medians (warm cache). Small samples are descriptive, not evidence of statistical significance."])
    return "\n".join(lines) + "\n"


def write_report(directory: Path, records: list[dict]) -> str:
    rows = summarize(records)
    rendered = render_markdown(rows)
    if any(r.get("budget_cost_basis") == "token_usage_at_price_ceiling" for r in records):
        rendered += (
            "\nFor capped runs the USD column uses provider-reported costs only; unknown costs remain —. "
            "Budget estimates use token usage at configured price ceilings and are not provider invoices. "
            "See budget.json and per-run estimated_cost_usd for reservations and estimated costs. "
            "A budget stop may leave unequal completed repetitions; inspect run statuses before comparing groups.\n"
        )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.md").write_text(rendered, encoding="utf-8")
    (directory / "summary.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    if rows:
        with (directory / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return rendered


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(args.directory.glob("run-*.json"))]
    if not records:
        parser.error("No run-*.json records found")
    print(write_report(args.directory, records))


if __name__ == "__main__":
    main()
