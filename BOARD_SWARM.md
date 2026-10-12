# Parallel shared-board swarm

`board_swarm` is a fourth architecture, separate from the existing sequential
LangGraph handoff `swarm`. The Plan Analyst, Stats Analyst and Query Rewriter
investigate different hypotheses concurrently. They all have the same SQL
diagnostic toolkit (and the configured optional profile tools), except that
only the final Auditor can call `submit_candidate`.

The agents can read current findings through `read_board(since_revision)`, post
short evidence and complete candidate SQL through `post_finding(...)`, and fetch
an immutable SQL artifact through `get_artifact(artifact_id)`. Each post receives
a monotonically increasing revision. A SQL artifact is identified as `sql-0004`
and retains the full query separately from bounded board summaries. A reader
can page through new posts using `next_revision`; concurrent posts remain visible
while other researchers are still running. The runtime saves the append-only
board at `<trace-stem>.board.json`, and the full trajectory at `<trace>.jsonl`.
Each successful `benchmark` call also automatically posts its measured SQL as
an artifact labeled **not yet equivalence-validated**, even if a researcher
reaches its own model-call limit before writing a final answer.

Once the three investigations finish, a synthesis Rewriter receives the board,
fetches any useful SQL artifacts, and can publish a combined rewrite. The
Auditor sees the same board and verifies a selected SQL with the existing
`submit_candidate` tool. Final scoring still independently checks exact fixture
result equality and pairs repeated baseline/candidate measurements. Each run
gets disposable database clones. Individual SQL tool calls share a lock so
benchmarks cannot overlap other calls in the candidate database; LLM calls and
reasoning can overlap. Index-changing profiles should be treated separately
because concurrent index proposals can still influence subsequent plans.

The shared trace callback enforces global model/tool caps across threads. The
first pilot allocated 5 calls to each researcher, 4 to synthesis and 5 to
audit. The second allocated Plan 3, Stats 3, independent Rewrite 8,
Synthesis 4, Auditor 6. Both allocations exhausted diagnostics before those
peers published findings. The current, locally tested code allocates
**4 / 4 / 8 / 3 / 5** and tells each agent its own remaining turns, prompting
early publication. This final adjustment has not been tried against DeepSeek.
Actual provider transport retries still consume the shared global cap.
`max_parallelism` counts active workers or LLM
calls; `handoff_calls` remains zero. Board-specific trace events are
`board_post` (`revision`, `author`, `kind`, `summary`, `evidence`, optional
`artifact_id`) and `board_read` (`revision`, `reader`, `since_revision`,
`returned`), plus `board_phase_limits`. SQL text is in the board artifact file and ordinary tool trace,
not copied into every board summary.

To reproduce the first DeepSeek query-only pilot from the repository root with
the fixture PostgreSQL already running:

```powershell
uv run benchmark --architecture board_swarm --legacy-only-model --no-cost-budget --runs 1 --profiles query_only --advisors off --max-llm-calls 24 --max-tool-calls 48 --max-output-tokens 4096 --no-db-start --output results/my-board-swarm-pilot
```

`--legacy-only-model` uses the `.env` `OPENAI_*` endpoint rather than including
the OpenRouter model list. The legacy DeepSeek endpoint has no configured USD
price ceiling here, so `--no-cost-budget` explicitly ignores the inherited
`BENCHMARK_MAX_COST_USD`. The 4096-token limit is **per response**, not a total
token cap. The `--output` directory must be new or empty. This one exploratory
run is not a fair nine-cell architecture/model comparison; compare distributions
over repeated runs on the same machine and physical DB state.

The first exploratory pilot (`results/board-swarm-deepseek-pilot-20260922`)
finished unsuccessfully: 15 model calls, 24 tool calls, 3 concurrent agents,
333.4 seconds of agent time, and **no submitted SQL**. The independent Rewriter
spent its fifth allotted model call measuring/executing a candidate and reached
its phase cap before publishing it. Its source at launch is preserved in
`source_snapshot/`, with all 21 Python-file hashes verified against that run's
manifest. This failed attempt is retained, not filtered from experiment cost.

The second local pilot (`results/board-swarm-deepseek-pilot2-20260922`) produced
one accepted SQL with **1.9815×** speedup in the independent five-pair final
measurement (baseline median 3929.914 ms, candidate 1983.322 ms). Its agent
used 21 LLM calls, 32 tool calls (25 database calls), 372,579 input tokens and
16,047 output tokens in 581.3 s; total validation and agent time was 628.3 s,
or 651.0 s including environment reset. Maximum simultaneous workers: 3.
The measured speedup is local to this pilot's machine and cache/load conditions;
it is not interchangeable with the earlier server matrix. Model prices for this
legacy endpoint are unknown, so USD cost is not reported.

The actual second-run board had three seeded hypotheses, two benchmarked
independent SQL artifacts, one synthesized SQL artifact, one synthesis finding
and the Auditor's decision (8 revisions). Plan and Stats hit their three-call
caps before publishing evidence. Their initial `read_board` calls happened
before later findings appeared, so this specific run demonstrates parallel
research and synthesis over shared SQL, **not** in-flight cross-peer uptake of
new Plan/Stats findings. The latter behavior is covered by the deterministic
concurrency test. Both pilots' `source_snapshot/` directories contain all 21
Python sources with SHA-256 matching their launch manifests. No generated
`sqlbench_` database clones remained after the pilots.
