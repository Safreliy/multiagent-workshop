"""Run the same SQL optimization task across three agent architectures."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import time
import itertools
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from benchmark.report import write_report
from benchmark.config import configure_openrouter_budget, models_from_env, names

ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURES = ("single", "orchestrator", "swarm", "board_swarm")


def save_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def dollar_budget_should_stop(snapshot):
    """Stop only for a dollar denial/anomaly, not ordinary per-run call limits."""
    return bool(
        snapshot
        and (
            snapshot.get("halted")
            or any(call.get("status") == "denied" for call in snapshot.get("calls", {}).values())
        )
    )


def schedule(architectures, runs, seed):
    """Randomized complete blocks; each repetition contains every requested mode."""
    rng = random.Random(seed)
    ordered = []
    for repetition in range(1, runs + 1):
        block = list(architectures)
        rng.shuffle(block)
        ordered.extend((architecture, repetition) for architecture in block)
    return ordered


def paired_benchmark(db, baseline, candidate, samples=5, warmup=1, baseline_db=None):
    """Alternate order within pairs, measuring query execution plus full fetch."""
    baseline_db = baseline_db or db
    for _ in range(warmup):
        baseline_db.benchmark(baseline, n=1, warmup=0)
        db.benchmark(candidate, n=1, warmup=0)
    timings = {"baseline": [], "candidate": []}
    for index in range(samples):
        pair = [("baseline", baseline), ("candidate", candidate)]
        if index % 2:
            pair.reverse()
        for name, sql in pair:
            measurement = (baseline_db if name == "baseline" else db).benchmark(sql, n=1, warmup=0)
            if measurement.get("ok") is False:
                raise RuntimeError(f"{name} benchmark failed: {measurement.get('error')}")
            timings[name].append(measurement["median_ms"])
    baseline_ms = statistics.median(timings["baseline"])
    candidate_ms = statistics.median(timings["candidate"])
    return {
        "baseline_ms": baseline_ms,
        "candidate_ms": candidate_ms,
        "speedup": baseline_ms / candidate_ms if candidate_ms > 0 else None,
        "baseline_samples_ms": timings["baseline"],
        "candidate_samples_ms": timings["candidate"],
    }


def estimated_cost(metrics):
    prices = [os.getenv("INPUT_PRICE_PER_MILLION"), os.getenv("OUTPUT_PRICE_PER_MILLION")]
    if not all(prices) or metrics.get("usage_complete") is False:
        return None
    if metrics.get("input_tokens") is None or metrics.get("output_tokens") is None:
        return None
    return (metrics["input_tokens"] * float(prices[0]) + metrics["output_tokens"] * float(prices[1])) / 1_000_000


def run_one(architecture, repetition, db, task, baseline, config, directory, *,
            baseline_db=None, prefix=None, metadata=None, advisor=None, model_settings=None):
    from agents import run_architecture

    prefix = prefix or f"run-{repetition:02d}-{architecture}"
    started = time.perf_counter()
    record = {"architecture": architecture, "repetition": repetition, "correct": False,
              "speedup": None, "status": "failed", "started_at": datetime.now(timezone.utc).isoformat()}
    record.update(metadata or {})
    try:
        prompt = task["description"] + "\n\nBaseline SQL:\n```sql\n" + baseline + "\n```"
        if metadata:
            prompt += "\nExperiment profile: " + metadata["profile"] + ". Use get_capabilities to inspect permitted changes."
        if advisor:
            prompt += "\nrank_query_plans provides optional Jev advice on what to benchmark; it cannot certify correctness or speedup."
        result = run_architecture(architecture, prompt, db, config, trace_path=directory / f"{prefix}.jsonl",
                                  additional_tools=[advisor.tool()] if advisor else [])
        record.update(result.to_dict())
        record["agent_wall_clock_s"] = result.metrics.wall_clock_s
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            record["metrics"]["observed_" + name] = record["metrics"].get(name)
        if record.get("metrics", {}).get("usage_complete") is False:
            for name in ("input_tokens", "output_tokens", "total_tokens"):
                record["metrics"][name] = None
        if result.candidate_sql:
            (directory / f"{prefix}.sql").write_text(result.candidate_sql, encoding="utf-8")
            validation = db.validate_candidate(result.candidate_sql)
            record["validation"] = validation
            if validation.get("correct"):
                record.update(paired_benchmark(db, baseline, result.candidate_sql,
                                              samples=task["benchmark_samples"], warmup=task["warmup"],
                                              baseline_db=baseline_db))
                record["correct"] = True
        else:
            record["validation"] = {"correct": False, "reason": "No submitted candidate"}
    except Exception as error:
        record["error"] = f"{type(error).__name__}: {error}"
        record["status"] = "failed"
    metrics = record.get("metrics", {})
    if config.cost_budget is not None:
        record["cost_usd"] = (
            metrics.get("cost_reported_usd")
            if metrics.get("cost_reported_complete") and metrics.get("llm_calls", 0) > 0
            else None
        )
        record["reported_cost_usd"] = record["cost_usd"]
        record["estimated_cost_usd"] = metrics.get("cost_estimated_usd", 0)
        record["reserved_cost_usd"] = metrics.get("cost_reserved_usd", 0)
        record["accounted_cost_usd"] = metrics.get("cost_accounted_usd", 0)
        record["cost_basis"] = "provider_reported"
        record["budget_cost_basis"] = "token_usage_at_price_ceiling"
    elif model_settings and model_settings.input_price is not None and model_settings.output_price is not None and metrics.get("usage_complete"):
        record["cost_usd"] = (metrics["input_tokens"] * model_settings.input_price + metrics["output_tokens"] * model_settings.output_price) / 1_000_000
    else:
        record["cost_usd"] = None
    if advisor:
        record["advisor_metrics"] = advisor.metrics()
    if hasattr(db, "experiment_artifacts"):
        record["environment"] = db.experiment_artifacts()
    record["wall_clock_s"] = time.perf_counter() - started
    save_json(directory / f"{prefix}.json", record)
    return record


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    modes = result.add_mutually_exclusive_group()
    modes.add_argument("--architecture", choices=ARCHITECTURES)
    modes.add_argument("--all", action="store_true", help="Run all architectures")
    result.add_argument("--runs", type=int, default=3)
    result.add_argument("--seed", type=int, default=42, help="Schedule shuffle seed (not a guarantee of LLM determinism)")
    result.add_argument("--task", type=Path, default=ROOT / "benchmark/task_hard.yaml")
    result.add_argument("--profiles", default="query_only", help="Comma-separated query_only,indexes,hints,all")
    result.add_argument("--advisors", default="off", help="off, jev, or off,jev for an ablation")
    result.add_argument("--include-legacy-model", action="store_true",
                        help="Also run OPENAI_MODEL/OPENAI_BASE_URL alongside OPENROUTER_MODELS")
    result.add_argument("--legacy-only-model", action="store_true",
                        help="Run only OPENAI_MODEL/OPENAI_BASE_URL, excluding OPENROUTER_MODELS")
    result.add_argument("--dry-run", action="store_true", help="Show experiment matrix without database or API calls")
    result.add_argument("--output", type=Path, help="New experiment directory; existing runs are never overwritten")
    result.add_argument("--no-db-start", action="store_true", help="Use already running PostgreSQL")
    result.add_argument("--db-only", action="store_true", help="Start PostgreSQL and exit without calling an LLM")
    result.add_argument("--max-llm-calls", type=int)
    result.add_argument("--max-tool-calls", type=int)
    result.add_argument("--max-output-tokens", type=int, help="Maximum generated tokens per LLM response")
    result.add_argument(
        "--max-cost-usd",
        type=float,
        help="Shared fail-closed USD ceiling for the complete matrix (or BENCHMARK_MAX_COST_USD)",
    )
    result.add_argument("--no-cost-budget", action="store_true",
                        help="Ignore BENCHMARK_MAX_COST_USD for a model without supported USD pricing")
    return result


def main():
    cli = parser()
    args = cli.parse_args()
    if args.runs < 1:
        cli.error("--runs must be positive")
    if args.include_legacy_model and args.legacy_only_model:
        cli.error("--include-legacy-model and --legacy-only-model are mutually exclusive")
    if args.max_output_tokens is not None and args.max_output_tokens < 1:
        cli.error("--max-output-tokens must be positive")
    if args.no_cost_budget and args.max_cost_usd is not None:
        cli.error("--no-cost-budget conflicts with --max-cost-usd")
    load_dotenv(ROOT / ".env", encoding="utf-8-sig")
    from agents import AgentConfig
    from agents.cost_budget import CostBudget
    from tools.environment import DatabaseEnvironment, DEFAULT_ADMIN_DSN, DEFAULT_FIXTURE_DATABASE
    from benchmark.jev import PlanAdvisor

    task = yaml.safe_load(args.task.read_text(encoding="utf-8"))
    baseline = (args.task.parent / task["baseline_file"]).resolve().read_text(encoding="utf-8")
    profiles, advisors = names(args.profiles), names(args.advisors)
    if not profiles or set(profiles) - {"query_only", "indexes", "hints", "all"}:
        cli.error("Unknown or empty profiles")
    if not advisors or set(advisors) - {"off", "jev"}:
        cli.error("Unknown or empty advisors")
    models = models_from_env(include_legacy=args.include_legacy_model or args.legacy_only_model)
    if args.legacy_only_model:
        models = models[-1:]
    raw_max_cost = None if args.no_cost_budget else args.max_cost_usd
    if raw_max_cost is None and not args.no_cost_budget and os.getenv("BENCHMARK_MAX_COST_USD"):
        try:
            raw_max_cost = float(os.environ["BENCHMARK_MAX_COST_USD"])
        except ValueError:
            cli.error("BENCHMARK_MAX_COST_USD must be a number")
    if raw_max_cost is not None:
        if not (raw_max_cost > 0 and raw_max_cost < float("inf")):
            cli.error("--max-cost-usd must be a positive finite number")
        if "jev" in advisors:
            cli.error("Jev advisor is unsupported with --max-cost-usd because its alpha endpoint cannot be safely pre-reserved")
        try:
            for model in models:
                configure_openrouter_budget(model)
        except ValueError as error:
            cli.error(str(error))
    architectures = [args.architecture] if args.architecture else ARCHITECTURES
    cells = list(itertools.product(range(len(models)), profiles, advisors, architectures))
    ordered = schedule(cells, args.runs, args.seed)
    if args.dry_run:
        print(json.dumps({"attempts": len(ordered), "runs_per_cell": args.runs,
                          "models": [m.public() for m in models], "profiles": profiles,
                          "advisors": advisors, "architectures": architectures, "task": task["id"],
                          "max_cost_usd": raw_max_cost}, indent=2))
        return 0
    directory = args.output or ROOT / "results" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if directory.exists() and any(directory.iterdir()):
        cli.error("--output must be an empty/new directory")
    directory.mkdir(parents=True, exist_ok=True)
    cost_budget = CostBudget(raw_max_cost, directory / "budget.json") if raw_max_cost is not None else None
    if not args.no_db_start:
        subprocess.run(["docker", "compose", "-p", "sql-agent-scaling-v2", "-f",
                        str(ROOT / "db/docker-compose.v2.yml"), "up", "-d", "--build", "--wait",
                        "--wait-timeout", "900"], check=True)
    if args.db_only:
        print("PostgreSQL v2 with pg_hint_plan is ready on port 55433.")
        return 0
    if "jev" in advisors and not os.getenv("OPENROUTER_API_KEY"):
        cli.error("OPENROUTER_API_KEY is required for --advisors jev")
    for name in ("max_llm_calls", "max_tool_calls"):
        if getattr(args, name) is not None:
            task[name] = getattr(args, name)
    if args.max_output_tokens is not None:
        task["max_output_tokens"] = args.max_output_tokens
    configs = []
    for model in models:
        resolved_temperature = (
            task["temperature"] if model.temperature == "task_default" else model.temperature
        )
        config = AgentConfig(model=model.id, base_url=model.base_url, api_key=model.api_key,
                             extra_body=model.extra_body, temperature=resolved_temperature,
                             max_llm_calls=task["max_llm_calls"], max_tool_calls=task["max_tool_calls"],
                             request_timeout_s=task["request_timeout_s"], max_tokens=task.get("max_output_tokens", 8192),
                             max_model_retries=task.get("max_model_retries", 1),
                             cost_budget=cost_budget, input_price_ceiling=model.input_price,
                             output_price_ceiling=model.output_price)
        config.validate()
        configs.append(config)
    environment = DatabaseEnvironment(os.getenv("BENCHMARK_ADMIN_DSN", DEFAULT_ADMIN_DSN),
                                      os.getenv("BENCHMARK_FIXTURE_DATABASE", DEFAULT_FIXTURE_DATABASE))
    manifest = {
        "task": task, "baseline_sha256": hashlib.sha256(baseline.encode()).hexdigest(),
        "models": [m.public() for m in models], "temperature": task["temperature"],
        "resolved_temperatures": {
            m.id: task["temperature"] if m.temperature == "task_default" else m.temperature
            for m in models
        },
        "schedule_seed": args.seed, "schedule": ordered, "python": sys.version,
        "platform": platform.platform(), "profiles": profiles, "advisors": advisors,
        "advisor_model": os.getenv("JEV_MODEL", "typesafe/jev-1.13"),
        "packages": {p: importlib.metadata.version(p) for p in
                     ["langchain", "langchain-openai", "langgraph", "langgraph-swarm", "psycopg"]},
        "source_hashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for folder in ("agents", "tools", "benchmark") for p in (ROOT / folder).rglob("*.py")},
        "lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        "fixture_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "db").glob("*.sql")},
        "environment_hashes": {name: hashlib.sha256((ROOT / "db" / name).read_bytes()).hexdigest()
                               for name in ("docker-compose.v2.yml", "Dockerfile.v2")},
        "baseline_policy": "pristine reference clone vs tuned candidate clone; alternating warm-cache measurements",
        "max_cost_usd": raw_max_cost,
        "cost_policy": (
            "pre-request conservative reservations and successful token usage at configured price ceilings"
            if cost_budget is not None else None
        ),
    }
    save_json(directory / "manifest.json", manifest)
    (directory / "baseline.sql").write_text(baseline, encoding="utf-8")
    records = []
    stopped_early = False
    for number, ((model_index, profile, advisor_name, architecture), repetition) in enumerate(ordered, 1):
        model, config = models[model_index], configs[model_index]
        prefix = f"run-{number:04d}-{architecture}"
        print(f"[{number}/{len(ordered)}] {model.id} {architecture} {profile} advisor={advisor_name} repetition={repetition}", flush=True)
        attempt_started = time.perf_counter()
        with environment.attempt() as databases:
            db = databases.make_candidate_tools(baseline, profile, statement_timeout_ms=task["statement_timeout_ms"])
            reference = databases.make_reference_tools(baseline, statement_timeout_ms=task["statement_timeout_ms"])
            preflight = reference.benchmark(baseline, n=1, warmup=0)
            if not preflight.get("ok"):
                raise RuntimeError(f"Baseline preflight failed: {preflight.get('error')}")
            capabilities = db.get_capabilities()
            if not capabilities.get("ok") or capabilities.get("configuration_error"):
                raise RuntimeError(f"Profile preflight failed: {capabilities}")
            if number == 1:
                manifest.update(database=reference.execute("SELECT version() AS version"),
                                database_schema=reference.get_schema(), database_indexes=reference.get_indexes(),
                                database_statistics=reference.get_statistics(), initial_baseline=preflight)
                manifest["database_capabilities"] = capabilities
                save_json(directory / "manifest.json", manifest)
            advisor = PlanAdvisor(db, directory / f"{prefix}.advisor.jsonl") if advisor_name == "jev" else None
            record = run_one(architecture, repetition, db, task, baseline, config, directory,
                             baseline_db=reference, prefix=prefix, advisor=advisor, model_settings=model,
                             metadata={"model": model.id, "profile": profile, "advisor": advisor_name, "task_id": task["id"]})
        record["attempt_with_reset_s"] = time.perf_counter() - attempt_started
        save_json(directory / f"{prefix}.json", record)
        records.append(record)
        print(f"  correct={record['correct']} status={record['status']} speedup={record['speedup']}", flush=True)
        write_report(directory, records)
        budget_state = cost_budget.snapshot() if cost_budget is not None else None
        if dollar_budget_should_stop(budget_state):
            manifest["stopped_early"] = {
                "reason": "USD budget could not safely reserve another request",
                "after_attempt": number,
                "budget": budget_state,
            }
            save_json(directory / "manifest.json", manifest)
            stopped_early = True
            break
    print(write_report(directory, records))
    print(f"Artifacts: {directory.resolve()}")
    return 0 if not stopped_early and all(r["correct"] for r in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
