# Replayed final SQL: isolated PostgreSQL measurements

Measured at: 2026-09-22T12:51:18.204778+00:00. Seed: 42; blocks: 10.

Each of the 27 saved final SQL queries was validated against the baseline on one fresh clone of the frozen v2 fixture. Each block contains every SQL once in random order. A pair warms baseline and candidate once each, then times both with alternating order. Timing is query execution plus full result fetch; the read-only connection setup and SQL validation are outside the timer. All SQL runs sequentially with no LLM calls.

The headline for each architecture is the median of the three independently generated SQL solutions' median paired speedups. Ten timing pairs per SQL describe timing variation and are not ten independent agent solutions.

| Model | Architecture | Valid SQL | Median paired speedup | Range across SQL |
|---|---|---:|---:|---:|
| DeepSeek | orchestrator | 3/3 | 1.878× | 1.829–1.885× |
| DeepSeek | single | 3/3 | 1.896× | 1.876–2.691× |
| DeepSeek | swarm | 3/3 | 1.739× | 1.726–2.013× |
| Luna | orchestrator | 3/3 | 2.031× | 1.790–2.092× |
| Luna | single | 3/3 | 1.914× | 1.899–2.041× |
| Luna | swarm | 3/3 | 2.018× | 1.907–2.032× |
| Sol | orchestrator | 3/3 | 2.041× | 1.983–2.116× |
| Sol | single | 3/3 | 2.004× | 1.999–2.019× |
| Sol | swarm | 3/3 | 1.922× | 1.919–2.043× |

| SQL | Original speedup | Replayed median paired speedup | Paired IQR | Candidate median ms | Baseline median ms |
|---|---:|---:|---:|---:|---:|
| deepseek-single-1 | 2.354× | 1.896× | 1.864–1.942× | 1210.2 | 2307.0 |
| deepseek-orchestrator-1 | 2.386× | 1.829× | 1.786–1.873× | 1259.4 | 2263.0 |
| deepseek-swarm-1 | 1.780× | 1.726× | 1.708–1.765× | 1314.5 | 2268.4 |
| deepseek-single-2 | 3.176× | 2.691× | 2.667–2.752× | 841.2 | 2294.8 |
| deepseek-orchestrator-2 | 1.985× | 1.885× | 1.876–1.898× | 1210.9 | 2280.0 |
| deepseek-swarm-2 | 1.726× | 1.739× | 1.722–1.751× | 1307.1 | 2265.4 |
| deepseek-single-3 | 2.174× | 1.876× | 1.849–1.899× | 1213.0 | 2264.1 |
| deepseek-orchestrator-3 | 2.245× | 1.878× | 1.867–1.919× | 1204.3 | 2263.1 |
| deepseek-swarm-3 | 2.389× | 2.013× | 1.978–2.039× | 1122.0 | 2260.9 |
| luna-orchestrator-1 | 2.279× | 2.092× | 2.073–2.113× | 1078.6 | 2262.9 |
| luna-single-1 | 2.150× | 2.041× | 2.017–2.082× | 1114.7 | 2265.8 |
| luna-swarm-1 | 2.502× | 1.907× | 1.890–1.925× | 1184.3 | 2256.6 |
| luna-swarm-2 | 2.454× | 2.032× | 2.015–2.056× | 1119.4 | 2267.4 |
| luna-orchestrator-2 | 2.428× | 2.031× | 2.017–2.054× | 1115.1 | 2267.7 |
| luna-single-2 | 2.281× | 1.914× | 1.891–1.928× | 1183.0 | 2263.5 |
| luna-orchestrator-3 | 2.017× | 1.790× | 1.774–1.803× | 1268.8 | 2273.1 |
| luna-swarm-3 | 2.305× | 2.018× | 1.993–2.030× | 1132.2 | 2266.0 |
| luna-single-3 | 2.141× | 1.899× | 1.890–1.926× | 1190.4 | 2263.0 |
| sol-orchestrator-1 | 2.186× | 1.983× | 1.966–2.006× | 1136.2 | 2241.0 |
| sol-single-1 | 1.999× | 1.999× | 1.976–2.018× | 1131.8 | 2265.4 |
| sol-swarm-1 | 2.672× | 2.043× | 2.005–2.059× | 1122.4 | 2260.8 |
| sol-swarm-2 | 2.451× | 1.922× | 1.897–1.966× | 1190.7 | 2269.1 |
| sol-orchestrator-2 | 1.979× | 2.041× | 2.008–2.152× | 1106.5 | 2241.7 |
| sol-single-2 | 2.693× | 2.019× | 1.974–2.076× | 1131.7 | 2271.9 |
| sol-orchestrator-3 | 1.948× | 2.116× | 2.056–2.152× | 1096.2 | 2299.7 |
| sol-swarm-3 | 1.960× | 1.919× | 1.887–1.948× | 1187.9 | 2265.4 |
| sol-single-3 | 2.077× | 2.004× | 1.977–2.025× | 1124.0 | 2254.4 |

Raw paired timings and timestamps are in `samples.jsonl`; checksums and source paths are in `manifest.json`. The spread of baseline timings in each pair provides a direct view of changing DB or host load. These timings replay existing SQL, so they do not include agent time or model cost.
