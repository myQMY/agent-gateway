from __future__ import annotations

"""W3 负载生成器：可配参数的并发负载 + **只与调度无关**的不变量自检。

这是 `gateway/bench.py`（W2 固定 4 项自检）的推广，口径完全一致：
- 不测吞吐 / 分位延迟 / 墙钟耗时，源码不 import time、不读时钟；
- 不写归档行、不改 GatewayConfig 字段、不碰 config_hash；
- 并发场景里的 queue_rejected 依赖线程时序，只作观测值输出，**从不作为断言**；
- 背压改用纯闸门场景（不提交线程）验证，保证确定性。

详见 docs/design-w3.md §2。
"""

import argparse
import json
from pathlib import Path
import sys
import threading

from .concurrency import BoundedTaskQueue, ConcurrentGateway
from .config import GatewayConfig
from .core import ChatRequest
from .engine import GatewayEngine
from .bench import CountingUpstream

SCOPE = "concurrency invariants only; not a benchmark; no machine-dependent numbers"
QUEUE_NOTE = "scheduling-dependent; reported, never asserted"
WAITED_NOTE = "scheduling-dependent; reported, never asserted"

SCENARIOS = ("single_flight", "rate_limit", "capacity_gate")


class BlockingUpstream(CountingUpstream):
    """让首个上游调用阻塞到全部请求已提交，真正打开并发窗口。

    否则 leader 可能在其它请求到达前就完成，single-flight 的等待分支一次都不触发，
    「并发自检」就退化成串行回放。Event 只作同步原语（带超时防死锁），
    不读时钟作为指标。
    """

    def __init__(self, config):
        super().__init__(config)
        self._entered = threading.Event()
        self._release = threading.Event()

    def complete(self, request, attempt=0, target=None):
        self._entered.set()
        self._release.wait(timeout=10)
        return super().complete(request, attempt=attempt, target=target)



def make_request(index: int, key: str = "", text: str | None = None) -> ChatRequest:
    return ChatRequest(
        model="gateway-primary",
        messages=(("user", text if text is not None else f"load-{index}"),),
        idempotency_key=key,
        caller="loadgen",
        index=index,
    )


def scenario_single_flight(n: int, workers: int, seed: int) -> dict:
    cfg = GatewayConfig(
        seed=seed, failure_rate=0.0, rpm_limit=0, cache_capacity=max(256, n * 2)
    )
    upstream = BlockingUpstream(cfg)
    engine = GatewayEngine(cfg, upstream=upstream)
    # 容量故意放大到 n 以上：本场景不测背压，避免调度相关的拒绝混入不变量。
    with ConcurrentGateway(engine, workers=workers, queue_capacity=n * 2) as pool:
        futures = pool.submit_many([make_request(i, key="K", text="same") for i in range(n)])
        # 等 leader 真正进入上游调用后再放行，制造出其它请求同时到达的窗口。
        upstream._entered.wait(timeout=10)
        upstream._release.set()
        decisions = pool.collect(futures, timeout_s=30)
        stats = pool.stats()
    return {
        "requests": n,
        "workers": workers,
        "upstream_calls": upstream.calls,
        "expected_upstream_calls": 1,
        "decisions": len(decisions),
        "ledger_spans": len(engine.ledger.spans),
        "single_flight_waited_observed": stats["single_flight_waited"],
        "single_flight_waited_note": WAITED_NOTE,
        "queue_rejected_observed": stats["queue_rejected"],
        "queue_rejected_note": QUEUE_NOTE,
    }


def scenario_rate_limit(n: int, workers: int, limit: int, seed: int) -> dict:
    cfg = GatewayConfig(
        seed=seed, failure_rate=0.0, rpm_limit=limit,
        window_requests=max(1000, n * 10), cache_capacity=max(256, n * 2),
    )
    engine = GatewayEngine(cfg)
    with ConcurrentGateway(engine, workers=workers, queue_capacity=n * 2) as pool:
        decisions = pool.collect(
            pool.submit_many([make_request(i) for i in range(n)]), timeout_s=30
        )
        stats = pool.stats()
    return {
        "requests": n,
        "workers": workers,
        "rpm_limit": limit,
        "status_200": sum(1 for d in decisions if d.status == 200),
        "status_429": sum(1 for d in decisions if d.status == 429),
        "expected_status_200": limit,
        "expected_status_429": n - limit,
        "ledger_spans": len(engine.ledger.spans),
        "queue_rejected_observed": stats["queue_rejected"],
        "queue_rejected_note": QUEUE_NOTE,
    }


def scenario_capacity_gate(capacity: int, extra: int) -> dict:
    """纯闸门、不提交线程：成功 / 拒绝数完全由容量这个自变量决定。"""
    gate = BoundedTaskQueue(capacity)
    attempts = capacity + extra
    admitted = sum(1 for _ in range(attempts) if gate.try_put())
    rejected = gate.rejected
    for _ in range(admitted):
        gate.task_done()
    empty_after_release = gate.empty()
    readmitted = sum(1 for _ in range(capacity) if gate.try_put())
    for _ in range(readmitted):
        gate.task_done()
    return {
        "capacity": capacity,
        "attempts": attempts,
        "admitted": admitted,
        "rejected": rejected,
        "expected_admitted": capacity,
        "expected_rejected": extra,
        "empty_after_release": empty_after_release,
        "readmitted_after_release": readmitted,
        "empty_after_second_release": gate.empty(),
    }


def build_payload(n: int, workers: int, limit: int, capacity: int,
                  extra: int, seed: int, selected: tuple[str, ...]) -> dict:
    scenarios: dict[str, dict] = {}
    if "single_flight" in selected:
        scenarios["single_flight"] = scenario_single_flight(n, workers, seed)
    if "rate_limit" in selected:
        scenarios["rate_limit"] = scenario_rate_limit(n, workers, limit, seed)
    if "capacity_gate" in selected:
        scenarios["capacity_gate"] = scenario_capacity_gate(capacity, extra)

    checks: list[dict] = []
    if "single_flight" in scenarios:
        s = scenarios["single_flight"]
        checks.append({"name": "same_key_upstream_called_once",
                       "pass": s["upstream_calls"] == 1 and s["decisions"] == n})
        checks.append({"name": "same_key_ledger_not_lost",
                       "pass": s["ledger_spans"] == n})
    if "rate_limit" in scenarios:
        s = scenarios["rate_limit"]
        checks.append({"name": "rate_limit_exactly_the_limit",
                       "pass": s["status_200"] == limit and s["status_429"] == n - limit})
        checks.append({"name": "rate_limit_ledger_not_lost",
                       "pass": s["ledger_spans"] == n})
    if "capacity_gate" in scenarios:
        s = scenarios["capacity_gate"]
        checks.append({"name": "capacity_gate_admitted_exactly_capacity",
                       "pass": s["admitted"] == capacity})
        checks.append({"name": "capacity_gate_rejected_exactly_extra",
                       "pass": s["rejected"] == extra})
        checks.append({"name": "capacity_gate_reusable_after_release",
                       "pass": s["empty_after_release"]
                       and s["readmitted_after_release"] == capacity
                       and s["empty_after_second_release"]})

    return {
        "scope": SCOPE,
        "parameters": {
            "n": n, "workers": workers, "rpm_limit": limit,
            "queue_capacity": capacity, "extra": extra, "seed": seed,
            "scenarios": list(selected),
        },
        "scenarios": scenarios,
        "checks": checks,
        "all_pass": all(c["pass"] for c in checks) and bool(checks),
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="W3 并发负载生成器（只验证不变量，不是性能基准）"
    )
    p.add_argument("--n", type=int, default=64, help="每个并发场景的请求数")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--rpm-limit", type=int, default=3)
    p.add_argument("--capacity", type=int, default=16, help="纯闸门场景的容量")
    p.add_argument("--extra", type=int, default=8, help="纯闸门场景超出容量的提交数")
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--scenarios", type=str, default=",".join(SCENARIOS))
    p.add_argument("--json-out", type=Path, default=None,
                   help="可选输出路径；禁止指向 experiments/runs（归档区只读）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.n < 1 or args.workers < 1 or args.capacity < 1 or args.extra < 0:
        raise SystemExit("n / workers / capacity must be >= 1; extra >= 0")
    if args.rpm_limit < 0 or args.rpm_limit > args.n:
        raise SystemExit("rpm_limit must be within [0, n]")
    selected = tuple(s.strip() for s in args.scenarios.split(",") if s.strip())
    unknown = [s for s in selected if s not in SCENARIOS]
    if unknown or not selected:
        raise SystemExit(f"unknown scenarios: {unknown or selected}")
    if args.json_out is not None:
        norm = args.json_out.as_posix().replace("\\", "/")
        if "experiments/runs" in norm:
            raise SystemExit("refusing to write into experiments/runs (archives are read-only)")

    payload = build_payload(
        args.n, args.workers, args.rpm_limit, args.capacity,
        args.extra, args.seed, selected,
    )
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8", newline="\n")
    return 0 if payload["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
