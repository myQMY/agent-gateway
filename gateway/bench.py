from __future__ import annotations

"""W2 并发不变量自检（不是性能基准）。

只验证与线程调度无关的三条不变量，不测 QPS / p95 / 墙钟，不写任何归档文件。
串行 off/on 对照仍由 `python -m gateway.replay` 产出，两条路径互不混用。
"""

import json
import sys
import threading

from .concurrency import ConcurrentGateway
from .config import GatewayConfig
from .core import ChatRequest
from .engine import GatewayEngine
from .upstream import FakeUpstream


class CountingUpstream(FakeUpstream):
    def __init__(self, config):
        super().__init__(config)
        self.calls = 0
        self._lock = threading.Lock()

    def complete(self, request, attempt=0, target=None):
        with self._lock:
            self.calls += 1
        return super().complete(request, attempt=attempt, target=target)


def _request(index: int, key: str = "", text: str | None = None) -> ChatRequest:
    return ChatRequest(
        model="gateway-primary",
        messages=(("user", text if text is not None else f"t{index}"),),
        idempotency_key=key, caller="bench", index=index,
    )


def check_same_key_single_flight(n: int = 32, workers: int = 8) -> dict:
    cfg = GatewayConfig(failure_rate=0.0, rpm_limit=0, cache_capacity=256)
    upstream = CountingUpstream(cfg)
    engine = GatewayEngine(cfg, upstream=upstream)
    with ConcurrentGateway(engine, workers=workers, queue_capacity=256) as pool:
        decisions = pool.collect(
            pool.submit_many([_request(i, key="K", text="same") for i in range(n)])
        )
    return {
        "requests": n,
        "upstream_calls": upstream.calls,
        "expected_upstream_calls": 1,
        "decisions": len(decisions),
        "ledger_spans": len(engine.ledger.spans),
    }


def check_rate_limit_under_concurrency(n: int = 40, limit: int = 3,
                                       workers: int = 8) -> dict:
    cfg = GatewayConfig(failure_rate=0.0, rpm_limit=limit, window_requests=1000)
    engine = GatewayEngine(cfg)
    with ConcurrentGateway(engine, workers=workers, queue_capacity=256) as pool:
        decisions = pool.collect(pool.submit_many([_request(i) for i in range(n)]))
    ok = sum(1 for d in decisions if d.status == 200)
    limited = sum(1 for d in decisions if d.status == 429)
    return {
        "requests": n,
        "rpm_limit": limit,
        "status_200": ok,
        "status_429": limited,
        "ledger_spans": len(engine.ledger.spans),
        "expected_status_200": limit,
    }


def main(argv: list[str] | None = None) -> int:
    same = check_same_key_single_flight()
    limited = check_rate_limit_under_concurrency()
    checks = [
        ("same_key_upstream_called_once",
         same["upstream_calls"] == 1 and same["decisions"] == same["requests"]),
        ("same_key_ledger_not_lost",
         same["ledger_spans"] == same["requests"]),
        ("rate_limit_exactly_the_limit",
         limited["status_200"] == limited["rpm_limit"]
         and limited["status_429"] == limited["requests"] - limited["rpm_limit"]),
        ("rate_limit_ledger_not_lost",
         limited["ledger_spans"] == limited["requests"]),
    ]
    payload = {
        "scope": "concurrency invariants only; not a benchmark, not an off/on contrast",
        "same_key_single_flight": same,
        "rate_limit_under_concurrency": limited,
        "checks": [{"name": name, "pass": ok} for name, ok in checks],
        "all_pass": all(ok for _, ok in checks),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
