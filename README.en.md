# Agent Gateway & LLM Middleware (W3)

> These two repositories together form an **evaluation harness / runtime platform,
> not a user-facing Agent application**: the objects being measured and improved are the
> runtime and the evaluation chain themselves, not a conversational product.
> This repository (`agent-gateway`) is the serving layer between an Agent runtime and
> model providers — a single OpenAI-compatible egress. FastAPI is only an HTTP adapter;
> routing, caching, rate limiting, idempotency and cost accounting are all
> zero-third-party-dependency, unit-testable core logic.
> **The evaluation harness `agent-reliability-harness` (P1) is the workload source;
> this is a separate repository with a separate commit history and does not modify any P1 file.**

## 30-Second Overview

| Question | Answer |
| --- | --- |
| What it is | An OpenAI-compatible model-call egress; neither an Agent application nor an evaluation platform |
| What was built | Four capabilities in depth + single-process concurrency queue + complete SSE + optional container replay / load invariants / read-only trace view |
| How it is proven | A serial off/on single-variable archive (three files); concurrency-invariant self-checks; a read-only archive view |
| What it costs | Zero API cost and zero network calls on the default path |
| What can be claimed | "Four decision chains each produce measurable events"; "counters stay correct and backpressure holds under concurrency"; "archives replay deterministically" |
| What cannot be claimed | Production gateway, general-purpose Agent governance, savings in production, real p95, benchmark QPS |

## What It Is

W1 picked four capabilities to build in depth (following the capability boundary of
LiteLLM / Portkey / Cloudflare AI Gateway, deliberately not covering everything):

| Capability | Decision point | Explicitly out of scope |
| --- | --- | --- |
| Model routing + fallback | Fixed-order degrade when the primary upstream fails | Latency-aware routing, active-active |
| Exact cache + LRU/logical TTL | Normalized request key, return on hit | Semantic cache, cross-instance shared cache |
| Fixed-window rate limiting | Per-caller counter, 429 on excess | Distributed limiting, token bucket |
| Idempotency key | Replay the first result for the same key, no double charge | Cross-instance lock, expiry policy |

Cross-cutting: every request records one archivable span (tokens, unit price, simulated
latency, hit chain, failure reason). The default upstream is a **deterministic fake** —
zero API cost, zero network calls, byte-for-byte reproducible.

W2 added three blocks (see [docs/design-w2.md](docs/design-w2.md)):

- **Concurrency / queue**: single-process bounded capacity gate + thread pool +
  single-flight dedup on the idempotency key. Pure standard library, no Redis / MQ / K8s (R6).
  Each of the four shared components carries its own `RLock`.
- **Complete SSE semantics**: role first frame, configurable chunking, tail frame with
  `finish_reason` and `usage`, `[DONE]` terminator, `event: error`, `event: cancelled`.
- **Optional real-provider adapter**: a minimal OpenAI-compatible `chat/completions`
  HTTP upstream (`urllib`), **disabled by default, absent from the baseline, no
  `config_hash` change**; credentials are read only from environment variables.

W3 added three blocks (see [docs/design-w3.md](docs/design-w3.md)):

- **Optional container replay**: `Dockerfile` + `docker-compose.yml` (`gateway` serves,
  `verify` runs offline acceptance). It is **not a reproduction prerequisite** — the
  authoritative path is still bare-Python full tests plus archive re-verification.
  Docker is unavailable on this machine, so `build` / `compose up` were **not executed**;
  only static manifests and contract tests are delivered.
- **Load generator**: `gateway/loadgen.py`, parameterised concurrent load producing only
  scheduling-independent invariants (upstream call count, rate-limit counts, ledger spans,
  gate capacity). It does not measure QPS / p95 / wall-clock time.
- **Read-only trace view**: `gateway/trace.py` generates a self-contained static HTML
  (zero JS, zero external assets) from an archive; the same archive yields byte-identical
  output. An example view is committed to the repository and `--check` re-verifies it.

## Two Disjoint Paths (Most Important)

| Path | Entry point | Output | In the archive? |
| --- | --- | --- | --- |
| Serial baseline | `python -m gateway.replay` | off/on single-variable comparison, byte-for-byte reproducible | Yes (three files) |
| Concurrent execution | `gateway.concurrency.ConcurrentGateway` | Concurrency invariants | **No** |

Under concurrency, "who hits the cache first" is decided by thread scheduling, so cost and
hit figures are not reproducible. The concurrent path therefore **does not produce
`cache_hit_rate` / `cost_saving` / `p95_ms`, and is never subtracted from or joined into a
curve with the W1 archive figures**. W1's `GatewayEngine.handle()` is unchanged and
`GatewayConfig` gained no field; both W1 archives remain byte-for-byte identical.

## How to Run

The core baseline, concurrency self-check, load generator, trace view and the full test
suite need **no installed dependencies**:

```bash
python -m unittest discover -s tests -q
python -m gateway.replay --n 600 --seed 11
python -m gateway.bench
python -m gateway.loadgen --n 64
python -m gateway.trace --run replay-aa1142308d8d
python -m gateway.trace --run replay-aa1142308d8d --check
python -m bridge.p1_replay --tasks "D:/文档/课题/agent/项目1/tasks/mvp_10.jsonl" --n 600
```

Only the HTTP service needs FastAPI:

```bash
pip install -r requirements.txt
uvicorn gateway.app:app --port 8000
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gateway-primary","messages":[{"role":"user","content":"hello"}]}'
```

Optional container (requires Docker; not executed this round):

```bash
docker compose up --build -d gateway
docker compose --profile verify run --rm verify
```

Endpoints: `POST /v1/chat/completions` (OpenAI-compatible subset, supports `stream=true`
SSE), `GET /health`, `GET /metrics` (includes concurrency counters), `GET /admin/config`.

## Key Results

`python -m gateway.replay` runs an **off (pure pass-through) / on (all four capabilities)
single-variable comparison**, archived as three files under
`experiments/runs/replay-<config_hash>/` (`report.md` / `config.json` / `decisions.jsonl`).
Cache hits, idempotent replays, rate-limit rejections and fallback triggers are each
counted, and a relative cost difference is reported. **This only shows that the four
decision chains are running and individually measurable; it does not show savings in
production.**

`python -m gateway.bench` verifies concurrency invariants, not performance:
32 concurrent requests sharing one idempotency key → the upstream is called **exactly
once**, with all 32 ledger spans present; 40 concurrent requests at `rpm_limit=3` →
exactly 3 responses with 200 and exactly 37 with 429, with 40 ledger spans.
These counts are independent of thread scheduling and therefore reproducible;
**they are neither QPS nor an SLA, and are not extrapolated to production throughput.**

`python -m gateway.loadgen` generalises this with parameters: same-key concurrency →
exactly one upstream call; at `rpm_limit=3` → exactly 3 with 200 and N−3 with 429;
a pure gate of capacity C → exactly C admitted and M rejected. A `BlockingUpstream`
holds the first upstream call open to **genuinely open the concurrency window** (a W3
fix: the first version had `single_flight_waited` always 0, degrading the self-check
into a serial replay). `queue_rejected` / `waited` depend on scheduling and are
**reported but never asserted**.

`python -m gateway.trace` **recomputes** the off/on summary over the full
`decisions.jsonl` and draws a decision-chain timeline; it adds no metric and changes no
byte of the archive. `--check` verifies byte-identical rendering and compares against
the committed example view.

## Boundaries

- Every default upstream is a deterministic fake; `p95_ms` is a **SHA256-derived
  simulated latency**, neither network latency nor a production SLA. Reports record no
  wall-clock time.
- The cache hit rate is largely determined by `repeat_rate`, which is an **independent
  variable rather than an optimisation result**; the price table is fixed, and the cost
  difference reflects only "hits are not charged" and "fallback uses a pricier model".
- Fake failures are injected via `failure_rate` and say nothing about real provider
  availability; the fixed window has boundary effects. Request content participates in
  upstream derivation, so changing the workload changes the token / latency distribution.
- `config_hash` pins the configuration, not the code; full reproduction requires
  "same config + same git commit".
- It is **not a general-purpose Agent evaluation platform and does not accept arbitrary
  external Agents**; only a subset of OpenAI-compatible `chat/completions` is implemented,
  and it is not a production gateway. SSE has no `tool_calls` deltas or multiple choices.
- Concurrency is **within a single process**: no distributed limiting or cross-instance
  cache. Concurrent cache misses may call the upstream twice (only the idempotency key
  gets single-flight). Ledger write order depends on scheduling, so no archive rows are
  produced.
- The real-provider adapter is disabled by default; when enabled it **does not collect
  real latency** (to avoid mixing with the simulated-latency field) and is not written
  into any existing report. Its relationship with P1 remains "read-only task set as a
  replay workload", **not a runtime direct connection**.
- The W3 container is **optional convenience only**: no Redis / Postgres / MQ / K8s, the
  image installs only the three existing packages, and no replicas are started. Docker is
  unavailable on this machine, so build / compose were **not executed** — only static
  manifests and contract tests are delivered.
- `loadgen` is a **concurrency-invariant self-check, not a performance benchmark**: no
  QPS / p95 / wall clock, no archive rows, and no subtraction from W1 figures. The trace
  view is a **read-only visualisation of an archive**, neither live tracing nor an
  observability platform; the timeline shows the first 48 rows per cell by default while
  statistics are computed over all 1200 rows.
- W4 (direct connection to the P1 runtime) would produce only real-chain observable
  events and real cost / latency, **no positive `pass^k` conclusion**. It is **not done**.

<!-- FORBIDDEN-EXAMPLES-START -->
Do not say (kept here so the contract test can strip it):

- ❌ "A production-ready LLM gateway that governs any Agent"
- ❌ "Cost fell by XX% in production"
- ❌ "Real p95 = XX ms"
- ❌ "Benchmarked QPS = XX"
- ❌ "Deployed with Kubernetes / W4 completed / Docker verified"
- ❌ "Cache hit rate is an optimisation result"
- ✅ "Within one fixed synthetic replay, toggling the four capabilities produces
     measurable events on each of the four decision chains and a relative cost difference"
<!-- FORBIDDEN-EXAMPLES-END -->

## Evidence Index

- `docs/design.md` — W1 red lines R1–R6, four capabilities, metric definitions, forbidden claims
- `docs/design-w2.md` — W2 scope, the two-path trade-off, concurrency model, lifecycle fix
- `docs/design-w3.md` — W3 boundaries, concurrency-window fix, container-not-executed note
- `docs/design-w5-w6.md` — W5–W6 narrative red lines and the machine-readable banned-phrase list
- `docs/design-w4-tooling.md` — W4 tooling design, three step corrections, two-cell config, metric caveats
- `w4/README.md` — W4 execution checklist (scaffold ready, experiment still not done; the user fills the key and runs it)
- `docs/resume.md` — resume bullets with verifiable numbers and boundaries (Chinese)
- `docs/interview.md` — grouped interview Q&A with evidence links (Chinese)
- `bridge/README.md` — how to attach the P1 workload, W4 steps and red lines
- `experiments/runs/` — replay archives (three files each, **committed**, inspectable after clone)
- `experiments/views/replay-aa1142308d8d.html` — example trace view (byte-reproducible)
- `tests/` — 213 unittest cases (23 W5–W6 narrative contract tests, 33 W4 tooling contract tests); the core has zero dependencies, `test_app.py` runs only when fastapi is installed

## Stages

| Stage | Content | Status |
| --- | --- | --- |
| W1 | Design frozen, four-capability core, replay baseline, thin FastAPI adapter, tests | Delivered |
| W2 | Concurrency / capacity gate / single-flight, complete SSE semantics, optional real-provider adapter | Delivered |
| W3 | Optional container replay, load invariants, read-only trace view | Delivered |
| W4 | Attach the P1 harness runtime directly (requires the user's own API budget and execution) | Not done |
| W5–W6 | English README, resume bullets, interview Q&A | **Delivered this round** |

One commit per stage, stoppable at any point, no automatic scope expansion. License: MIT.
Chinese version: [README.md](README.md).
