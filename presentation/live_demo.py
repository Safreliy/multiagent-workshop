"""Local-only live presentation server for one real SQL agent run at a time.

Run: uv run python -m presentation.live_demo
Open: http://127.0.0.1:8769/live.html
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
RUNS = ROOT / "results" / "live-demo"
ARCHITECTURES = {"single", "orchestrator", "swarm", "board_swarm"}
MAX_COST_USD = 1.0
MAX_SQL_CHARS = 20_000
MAX_INSTRUCTION_CHARS = 2_000
MAX_LLM_CALLS = 24
MAX_OUTPUT_TOKENS = 4_096
_job_lock = threading.RLock()
_job: dict | None = None


class StopRequested(Exception):
    """The browser asked to stop before the child entered the agent loop."""


def _load_models() -> list[dict]:
    from dotenv import load_dotenv

    from benchmark.config import configure_openrouter_budget, models_from_env
    from urllib.parse import urlparse

    load_dotenv(ROOT / ".env", encoding="utf-8-sig")
    available = []
    for model in models_from_env(include_legacy=True):
        if not model.api_key:
            continue
        priced = urlparse(model.base_url or "").hostname == "openrouter.ai"
        if priced:
            try:
                configure_openrouter_budget(model)
            except ValueError:
                continue
        if any(item["id"] == model.id for item in available):
            continue
        available.append({"id": model.id, "provider": "openrouter" if priced else "direct",
                          "budget_kind": "usd" if priced else "calls",
                          "max_cost_usd": MAX_COST_USD if priced else None,
                          "max_llm_calls": MAX_LLM_CALLS,
                          "max_output_tokens_per_call": MAX_OUTPUT_TOKENS})
    return available


def _model_for_id(model_id: str):
    from benchmark.config import configure_openrouter_budget, models_from_env
    from urllib.parse import urlparse

    models = models_from_env(include_legacy=True)
    model = next((item for item in models if item.id == model_id and item.api_key), None)
    if model is None:
        raise ValueError("Configured model is unavailable")
    priced = urlparse(model.base_url or "").hostname == "openrouter.ai"
    if priced:
        configure_openrouter_budget(model)
    return model, priced


def _validate_submission(body: dict) -> tuple[str, str, str, str | None]:
    from tools.postgres import validate_candidate

    if not isinstance(body, dict):
        raise ValueError("Expected an object")
    architecture, model_id = body.get("architecture"), body.get("model")
    if not isinstance(architecture, str) or architecture not in ARCHITECTURES:
        raise ValueError("Unknown architecture")
    if not isinstance(model_id, str) or model_id not in {m["id"] for m in _load_models()}:
        raise ValueError("Model is unavailable or missing its price ceiling")
    sql, instruction = body.get("baseline_sql"), body.get("instruction", "")
    if not isinstance(sql, str) or not sql.strip() or len(sql) > MAX_SQL_CHARS:
        raise ValueError(f"SQL must contain 1–{MAX_SQL_CHARS} characters")
    if not isinstance(instruction, str) or len(instruction) > MAX_INSTRUCTION_CHARS:
        raise ValueError(f"Instruction must be at most {MAX_INSTRUCTION_CHARS} characters")
    check = validate_candidate(sql)
    if not check["valid"]:
        raise ValueError("SQL rejected: " + check["error"])
    previous = body.get("previous_job_id")
    if previous is not None and (not isinstance(previous, str) or not re.fullmatch(r"[0-9a-f]{12}", previous)):
        raise ValueError("Invalid previous run")
    return architecture, model_id, sql.strip(), previous


def _previous_candidate(previous: str | None, raw_baseline: str) -> str | None:
    """Carry a prior artifact only when the user kept the exact same baseline."""
    if previous is None or not re.fullmatch(r"[0-9a-f]{12}", previous):
        return None
    from tools.postgres import validate_candidate

    folder = RUNS / previous
    try:
        prior_request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
        prior_sql = (folder / "live.sql").read_text(encoding="utf-8")
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if prior_request.get("baseline_sql") != raw_baseline or not validate_candidate(prior_sql)["valid"]:
        return None
    return prior_sql[:MAX_SQL_CHARS]


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.01 * (attempt + 1))


def _public_text(value: object, limit: int = 1800) -> str:
    """Keep credentials out of browser events, including proxy URLs in transport errors."""
    message = str(value)
    secrets = [os.getenv(key, "") for key in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "LLM_API_KEY")]
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "BENCHMARK_ADMIN_DSN"):
        address = os.getenv(key, "")
        if address:
            secrets.append(address)
            try:
                parsed = urlsplit(address)
                for credential in (parsed.username, parsed.password):
                    if credential:
                        secrets.extend((credential, unquote(credential)))
            except ValueError:
                pass
    for secret in sorted(set(s for s in secrets if s), key=len, reverse=True):
        message = message.replace(secret, "[redacted]")
    return message[:limit]


def _run_worker(architecture: str, model_id: str, directory: Path) -> int:
    """Run in a child process so the UI can stop only this job."""
    from dotenv import load_dotenv
    import psycopg
    import yaml

    from agents import AgentConfig
    from agents.cost_budget import CostBudget
    from benchmark.runner import run_one
    from tools.postgres import validate_candidate
    from tools.environment import DatabaseEnvironment, DEFAULT_ADMIN_DSN, DEFAULT_FIXTURE_DATABASE

    load_dotenv(ROOT / ".env", encoding="utf-8-sig")

    def check_stop() -> None:
        if (directory / "stop.requested").exists():
            raise StopRequested

    def stage(name: str, **data: object) -> None:
        _write_json(directory / "stage.json", {"stage": name, **data})

    check_stop()
    stage("connecting")
    model, priced = _model_for_id(model_id)
    task_file = ROOT / "benchmark" / "task_hard.yaml"
    task = yaml.safe_load(task_file.read_text(encoding="utf-8"))
    task["id"] = "live-custom-sql"
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    raw_baseline = request["baseline_sql"]
    baseline_check = validate_candidate(raw_baseline)
    if not baseline_check["valid"]:
        raise ValueError("Stored baseline is not a read-only SELECT")
    # Downstream validators embed the baseline in subqueries; a trailing comment
    # or semicolon from the editor must not swallow their closing parenthesis.
    baseline = baseline_check["normalized_sql"]
    instruction = request.get("instruction", "")
    task["description"] = (
        "Optimize the user's PostgreSQL SELECT for the fixture database. Preserve its exact "
        "unordered multiset result, column order, and types. Do not change data or schema. "
        "Use get_capabilities, the plan, schema, indexes and statistics to investigate. "
        "Submit the best proven candidate with submit_candidate. Fixture equivalence is "
        "not a proof for all future data."
    )
    if instruction:
        task["description"] += "\nUser instruction:\n" + instruction
    prior_sql = _previous_candidate(request.get("previous_job_id"), raw_baseline)
    if prior_sql:
        task["description"] += "\nEarlier submitted candidate to improve if useful:\n```sql\n" + prior_sql + "\n```"
    task["benchmark_samples"] = 3
    task["max_llm_calls"] = min(task["max_llm_calls"], MAX_LLM_CALLS)
    task["max_tool_calls"] = min(task["max_tool_calls"], 48)
    config = AgentConfig(
        model=model.id, base_url=model.base_url, api_key=model.api_key,
        extra_body=model.extra_body,
        temperature=task["temperature"] if model.temperature == "task_default" else model.temperature,
        max_llm_calls=task["max_llm_calls"], max_tool_calls=task["max_tool_calls"],
        request_timeout_s=task["request_timeout_s"], max_tokens=min(task["max_output_tokens"], MAX_OUTPUT_TOKENS),
        max_model_retries=task["max_model_retries"],
        cost_budget=CostBudget(MAX_COST_USD, directory / "budget.json") if priced else None,
        input_price_ceiling=model.input_price if priced else None,
        output_price_ceiling=model.output_price if priced else None,
    )
    config.validate()
    admin_dsn = os.getenv("BENCHMARK_ADMIN_DSN", DEFAULT_ADMIN_DSN)
    # Preflight fails before any paid request and does not touch other containers.
    with psycopg.connect(admin_dsn, connect_timeout=5) as connection:
        connection.execute("SELECT 1").fetchone()
    check_stop()
    environment = DatabaseEnvironment(admin_dsn, os.getenv("BENCHMARK_FIXTURE_DATABASE", DEFAULT_FIXTURE_DATABASE))
    stage("preparing")
    with environment.attempt() as databases:
        _write_json(directory / "databases.json", {
            "reference": databases.reference_database,
            "candidate": databases.candidate_database,
        })
        check_stop()
        db = databases.make_candidate_tools(baseline, "query_only", statement_timeout_ms=task["statement_timeout_ms"])
        reference = databases.make_reference_tools(baseline, statement_timeout_ms=task["statement_timeout_ms"])
        preflight = reference.benchmark(baseline, n=1, warmup=0)
        if not preflight.get("ok"):
            raise RuntimeError(f"Baseline SQL failed: {preflight.get('error')}")
        check_stop()
        stage("agent", baseline_ms=preflight.get("median_ms"))
        record = run_one(
            architecture, 1, db, task, baseline, config, directory,
            baseline_db=reference, prefix="live", model_settings=model,
            metadata={"model": model.id, "profile": "query_only", "advisor": "off", "task_id": task["id"]},
        )
        if record["status"] == "completed" and record["correct"]:
            stage("finished", correct=True, speedup=record.get("speedup"))
        else:
            stage("failed", error=record.get("error") or record.get("validation", {}).get("reason")
                  or "Candidate was not submitted or did not pass validation")
    (directory / "databases.json").unlink(missing_ok=True)
    return 0


def _worker_entry(architecture: str, model_id: str, directory: Path) -> int:
    try:
        return _run_worker(architecture, model_id, directory)
    except StopRequested:
        _write_json(directory / "stage.json", {"stage": "stopped"})
        return 0
    except Exception as exc:  # noqa: BLE001 - present the actual failure in the live UI
        _write_json(directory / "stage.json", {
            "stage": "failed", "error": _public_text(f"{type(exc).__name__}: {exc}", 500),
        })
        return 1


def _safe_event(raw: dict) -> dict:
    """Browser receives a small allowlist, never raw provider metadata or credentials."""
    event = raw.get("event", "")
    value = {"sequence": raw.get("sequence"), "elapsed_s": raw.get("elapsed_s"), "event": event}
    for key in ("name", "worker", "source", "destination", "architecture", "run_id",
                "revision", "author", "reader", "since_revision", "kind", "artifact_id"):
        if key in raw:
            value[key] = _public_text(raw[key], 120) if isinstance(raw[key], str) else raw[key]
    if event == "llm_end":
        value["text"] = _public_text("\n".join(str(s) for s in raw.get("outputs", [])))
        value["usage"] = raw.get("usage", {})
    elif event == "tool_start":
        value["text"] = _public_text(raw.get("input", ""), 900)
    elif event == "tool_end":
        value["text"] = _public_text(raw.get("output", ""), 900)
    elif event in {"handoff", "worker_start"}:
        state = raw.get("state") or raw.get("handoff") or {}
        if isinstance(state, dict):
            value["text"] = _public_text(state.get("hypothesis") or "", 400)
    elif event == "board_post":
        value["text"] = _public_text(raw.get("summary", ""), 500)
    elif event == "board_read":
        value["text"] = f"Revision {raw.get('revision', 0)}"
    elif event in {"run_error", "tool_error", "worker_error", "llm_error"}:
        value["text"] = _public_text(raw.get("error", ""), 500)
    elif event == "run_end":
        metrics = raw.get("metrics") or {}
        value["metrics"] = {key: metrics.get(key) for key in (
            "llm_calls", "tool_calls", "worker_calls", "handoff_calls", "input_tokens", "output_tokens",
            "max_parallelism",
        )}
    return value


def _events(path: Path, after: int) -> list[dict]:
    if not path.exists():
        return []
    events = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue  # a concurrent writer may still be finishing this line
            if isinstance(raw.get("sequence"), int) and raw["sequence"] > after:
                events.append(_safe_event(raw))
    return events[:200]


def _cleanup_clones(directory: Path) -> None:
    """After forcibly terminating our worker, remove only its verified generated clones."""
    metadata = directory / "databases.json"
    if not metadata.exists():
        return
    from dotenv import load_dotenv
    from tools.environment import DatabaseEnvironment, DEFAULT_ADMIN_DSN, _GENERATED_NAME
    import psycopg

    load_dotenv(ROOT / ".env", encoding="utf-8-sig")
    names = json.loads(metadata.read_text(encoding="utf-8"))
    if not isinstance(names, dict) or not all(_GENERATED_NAME.fullmatch(v) for v in names.values()):
        raise RuntimeError("Refusing to clean up unexpected database name")
    environment = DatabaseEnvironment(os.getenv("BENCHMARK_ADMIN_DSN", DEFAULT_ADMIN_DSN))
    with psycopg.connect(environment.admin_dsn, autocommit=True, connect_timeout=5) as connection:
        for name in names.values():
            environment._drop_clone(connection, name)
    metadata.unlink(missing_ok=True)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HERE), **kwargs)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        request_path = urlsplit(self.path).path
        if request_path == "/api/config":
            self._json(200, {"models": _load_models(), "architectures": sorted(ARCHITECTURES),
                             "max_cost_usd": MAX_COST_USD, "max_sql_chars": MAX_SQL_CHARS,
                             "max_instruction_chars": MAX_INSTRUCTION_CHARS})
            return
        if request_path == "/api/sample":
            sample = (ROOT / "db" / "hard-baseline.sql").read_text(encoding="utf-8")
            self._json(200, {"sql": sample})
            return
        if request_path == "/api/schema":
            schema = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
            self._json(200, {"schema": schema})
            return
        if request_path == "/api/artifact":
            from urllib.parse import parse_qs

            artifact_id = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
            if not re.fullmatch(r"sql-[0-9]{4}", artifact_id):
                self._json(400, {"error": "Invalid artifact id"})
                return
            with _job_lock:
                job = _job
                if job is None or job["architecture"] != "board_swarm":
                    self._json(404, {"error": "No board run"})
                    return
                path = job["directory"] / "live.board.json"
                try:
                    board = json.loads(path.read_text(encoding="utf-8"))
                    artifact = board["artifacts"][artifact_id]
                except (FileNotFoundError, KeyError, json.JSONDecodeError):
                    self._json(404, {"error": "Artifact not found"})
                    return
                self._json(200, {"id": artifact_id, "sql": artifact["sql"]})
            return
        if request_path == "/api/state":
            from urllib.parse import parse_qs

            query = parse_qs(urlsplit(self.path).query)
            try:
                after = max(0, int(query.get("after", ["0"])[0]))
            except ValueError:
                self._json(400, {"error": "Invalid event cursor"})
                return
            with _job_lock:
                job = _job
                if job is None:
                    self._json(200, {"job": None, "events": []})
                    return
                directory = job["directory"]
                process = job["process"]
                stage_file = directory / "stage.json"
                try:
                    stage = json.loads(stage_file.read_text(encoding="utf-8"))
                except (FileNotFoundError, json.JSONDecodeError):
                    stage = {"stage": "starting"}
                for key in ("error", "cleanup_error"):
                    if key in stage:
                        stage[key] = _public_text(stage[key], 500)
                if process.poll() not in (None, 0) and stage.get("stage") not in {"failed", "stopped"}:
                    stage = {"stage": "failed", "error": "Worker process exited unexpectedly"}
                record_file = directory / "live.json"
                try:
                    record = json.loads(record_file.read_text(encoding="utf-8"))
                except (FileNotFoundError, json.JSONDecodeError):
                    record = None
                result = None if record is None else {key: record.get(key) for key in (
                    "architecture", "model", "correct", "speedup", "baseline_ms", "candidate_ms",
                    "status", "error", "metrics", "wall_clock_s",
                )}
                if result and result.get("error"):
                    result["error"] = _public_text(result["error"], 500)
                if result and isinstance(result.get("metrics"), dict):
                    metrics = result["metrics"]
                    result["metrics"] = {key: metrics.get(key) for key in (
                        "input_tokens", "output_tokens", "llm_calls", "tool_calls", "worker_calls",
                        "handoff_calls", "cost_accounted_usd", "usage_complete", "max_parallelism",
                    )}
                candidate_file = directory / "live.sql"
                if result and candidate_file.exists():
                    result["candidate_sql"] = candidate_file.read_text(encoding="utf-8")[:50_000]
                try:
                    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
                except (FileNotFoundError, json.JSONDecodeError):
                    request = {}
                self._json(200, {"job": {"id": job["id"], "architecture": job["architecture"],
                                          "model": job["model"], "running": process.poll() is None,
                                          "stage": stage, "result": result,
                                          "baseline_sql": request.get("baseline_sql", ""),
                                          "instruction": request.get("instruction", "")},
                                 "events": _events(directory / "live.jsonl", after)})
            return
        if request_path in {"/", "/live.html", "/live.js", "/live.css"}:
            self.path = "/live.html" if request_path == "/" else request_path
            return super().do_GET()
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        global _job
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            self._json(403, {"error": "Cross-origin requests are disabled"})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self._json(415, {"error": "Expected application/json"})
            return
        if self.path == "/api/start":
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > 100_000:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(size))
                architecture, model_id, baseline_sql, previous = _validate_submission(body)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
                return
            with _job_lock:
                if _job and _job["process"].poll() is None:
                    self._json(409, {"error": "A live run is already in progress"})
                    return
                identifier = uuid.uuid4().hex[:12]
                directory = RUNS / identifier
                directory.mkdir(parents=True, exist_ok=False)
                _write_json(directory / "request.json", {
                    "baseline_sql": baseline_sql, "instruction": body.get("instruction", ""),
                    "previous_job_id": previous,
                })
                log = (directory / "worker.log").open("w", encoding="utf-8")
                process = subprocess.Popen(
                    [sys.executable, "-m", "presentation.live_demo", "--worker", architecture,
                     "--model", model_id, "--output", str(directory)],
                    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                )
                log.close()
                _job = {"id": identifier, "directory": directory, "process": process,
                        "architecture": architecture, "model": model_id}
            self._json(202, {"id": identifier})
            return
        if self.path == "/api/stop":
            with _job_lock:
                if _job is None or _job["process"].poll() is not None:
                    self._json(409, {"error": "No active run"})
                    return
                job = _job
                (job["directory"] / "stop.requested").touch()
                if not (job["directory"] / "databases.json").exists():
                    self._json(202, {"stopping": True})
                    return
                job["process"].terminate()
                try:
                    job["process"].wait(timeout=8)
                except subprocess.TimeoutExpired:
                    job["process"].kill()
                    job["process"].wait(timeout=5)
                try:
                    _cleanup_clones(job["directory"])
                except Exception as exc:  # noqa: BLE001 - report cleanup failure
                    _write_json(job["directory"] / "stage.json", {"stage": "stopped", "cleanup_error": str(exc)})
                else:
                    _write_json(job["directory"] / "stage.json", {"stage": "stopped"})
            self._json(200, {"stopped": True})
            return
        self._json(404, {"error": "Unknown endpoint"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=sorted(ARCHITECTURES))
    parser.add_argument("--model")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--port", type=int, default=8769)
    args = parser.parse_args()
    if args.worker:
        if not args.model or not args.output or not re.fullmatch(r"[0-9a-f]{12}", args.output.name):
            parser.error("Worker requires a valid model and generated output directory")
        return _worker_entry(args.worker, args.model, args.output)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Live SQL agents: http://127.0.0.1:{args.port}/live.html", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
