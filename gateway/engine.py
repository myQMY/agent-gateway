from __future__ import annotations

"""编排：限流 -> 幂等 -> 缓存 -> 路由/降级 -> 台账。

off 格走完全直通（单上游、无任何网关能力），保证对照实验唯一自变量
就是 gateway_enabled，而不是「顺手也关掉了别的东西」。
"""

from dataclasses import replace

from .components import (
    CACHE_HIT_LATENCY_MS,
    ExactCache,
    FixedWindowLimiter,
    IdempotencyStore,
    Ledger,
    Router,
)
from .config import GatewayConfig
from .core import ChatRequest, Decision, UpstreamReply
from .upstream import FakeUpstream, Upstream


class GatewayEngine:
    def __init__(self, config: GatewayConfig, upstream: Upstream | None = None):
        self.config = config
        self.upstream = upstream or FakeUpstream(config)
        self.cache = ExactCache(config.cache_capacity, config.cache_ttl_requests)
        self.limiter = FixedWindowLimiter(config.rpm_limit, config.window_requests)
        self.idempotency = IdempotencyStore()
        self.router = Router(config, self.upstream)
        self.ledger = Ledger(config)
        self.wall_ms: list[float] = []

    def handle(self, request: ChatRequest) -> Decision:
        if not self.config.gateway_enabled:
            return self._passthrough(request)

        tick = request.index + 1
        reasons: list[str] = []

        if not self.limiter.allow(request.caller, tick):
            return self._finish(
                Decision(
                    status=429, reply=None, index=request.index, caller=request.caller,
                    model=request.model, route="", rate_limited=True,
                    simulated_latency_ms=0.0,
                    reasons=("rate_limit_exceeded",),
                )
            )

        replayed = self.idempotency.get(request.idempotency_key)
        if replayed is not None:
            self.idempotency.mark_blocked()
            return self._finish(
                Decision(
                    status=200, reply=replayed, index=request.index,
                    caller=request.caller, model=request.model,
                    route=replayed.target, idempotent_replay=True, attempts=0,
                    cost_usd=0.0, simulated_latency_ms=CACHE_HIT_LATENCY_MS,
                    reasons=("idempotent_replay",),
                )
            )

        cached = self.cache.get(request.cache_key(), tick)
        if cached is not None:
            return self._finish(
                Decision(
                    status=200, reply=cached, index=request.index,
                    caller=request.caller, model=request.model,
                    route=cached.target, cache_hit=True, attempts=0,
                    cost_usd=0.0, simulated_latency_ms=CACHE_HIT_LATENCY_MS,
                    reasons=("cache_hit",),
                )
            )

        routed = self.router.call(request)
        if routed.reply is None:
            return self._finish(
                Decision(
                    status=502, reply=None, index=request.index,
                    caller=request.caller, model=request.model,
                    route=routed.route, fallback_used=routed.fallback_used,
                    attempts=routed.attempts, cost_usd=0.0,
                    simulated_latency_ms=0.0, reasons=routed.reasons,
                )
            )

        reply = routed.reply
        self.cache.put(request.cache_key(), reply, tick)
        self.idempotency.put(request.idempotency_key, reply)
        return self._finish(
            Decision(
                status=200, reply=reply, index=request.index,
                caller=request.caller, model=request.model, route=routed.route,
                fallback_used=routed.fallback_used, attempts=routed.attempts,
                cost_usd=self.ledger.cost(reply, routed.route),
                simulated_latency_ms=reply.latency_ms,
                reasons=routed.reasons,
            )
        )

    def _passthrough(self, request: ChatRequest) -> Decision:
        reply = self.upstream.complete(request, attempt=0, target=request.model)
        if not reply.ok:
            return self._finish(
                Decision(
                    status=502, reply=None, index=request.index,
                    caller=request.caller, model=request.model, route=request.model,
                    attempts=1, cost_usd=0.0, simulated_latency_ms=reply.latency_ms,
                    reasons=(reply.error_kind,),
                )
            )
        return self._finish(
            Decision(
                status=200, reply=reply, index=request.index,
                caller=request.caller, model=request.model, route=request.model,
                attempts=1, cost_usd=self.ledger.cost(reply, request.model),
                simulated_latency_ms=reply.latency_ms,
            )
        )

    def _finish(self, decision: Decision) -> Decision:
        self.ledger.record(decision.as_row())
        return decision

    def metrics(self) -> dict:
        return {
            "requests": len(self.ledger.spans),
            "cache_hits": self.cache.hits,
            "cache_misses": self.cache.misses,
            "cache_expired": self.cache.expired,
            "cache_evictions": self.cache.evictions,
            "cache_size": len(self.cache),
            "rate_limited": self.limiter.rejected,
            "idempotent_blocked": self.idempotency.blocked,
            "total_cost_usd": round(self.ledger.total_cost(), 8),
        }
