import unittest
from dataclasses import replace

from gateway.config import GatewayConfig
from gateway.core import ChatRequest, UpstreamReply
from gateway.engine import GatewayEngine
from gateway.upstream import FakeUpstream


class AlwaysFailUpstream(FakeUpstream):
    def complete(self, request, attempt=0, target=None):
        return UpstreamReply("", 10, 0, False, error_kind="upstream_5xx",
                             target=target or request.model, latency_ms=10.0)


class CountingUpstream(FakeUpstream):
    def __init__(self, config):
        super().__init__(config)
        self.calls = 0

    def complete(self, request, attempt=0, target=None):
        self.calls += 1
        return super().complete(request, attempt=attempt, target=target)


def req(index=0, key="", model="gateway-primary", text="hello"):
    return ChatRequest(model=model, messages=(("user", text),),
                       idempotency_key=key, caller="a", index=index)


class TestRouterAndFallback(unittest.TestCase):
    def test_primary_success_has_no_fallback(self):
        cfg = GatewayConfig(failure_rate=0.0)
        result = GatewayEngine(cfg).router.call(req())
        self.assertTrue(result.reply.ok)
        self.assertEqual(result.attempts, 1)
        self.assertFalse(result.fallback_used)

    def test_all_upstreams_fail_returns_none(self):
        cfg = GatewayConfig()
        eng = GatewayEngine(cfg, upstream=AlwaysFailUpstream(cfg))
        result = eng.router.call(req())
        self.assertIsNone(result.reply)
        self.assertIn("all_upstreams_failed", result.reasons)
        self.assertEqual(result.attempts, 2)

    def test_failure_rate_produces_fallback_events(self):
        cfg = GatewayConfig(seed=11, n_requests=200, failure_rate=0.9, repeat_rate=0.0)
        eng = GatewayEngine(cfg)
        for i in range(200):
            eng.handle(req(index=i, text=f"t{i}"))
        rows = eng.ledger.spans
        self.assertTrue(any(r["fallback_used"] for r in rows))


class TestEngineChain(unittest.TestCase):
    def test_idempotent_replay_does_not_call_upstream_again(self):
        cfg = GatewayConfig(failure_rate=0.0)
        upstream = CountingUpstream(cfg)
        eng = GatewayEngine(cfg, upstream=upstream)
        first = eng.handle(req(index=0, key="K"))
        second = eng.handle(req(index=1, key="K"))
        self.assertFalse(first.idempotent_replay)
        self.assertTrue(second.idempotent_replay)
        self.assertEqual(second.cost_usd, 0.0)
        self.assertEqual(upstream.calls, 1)
        self.assertEqual(eng.metrics()["idempotent_blocked"], 1)

    def test_cache_hit_precedes_upstream(self):
        cfg = GatewayConfig(failure_rate=0.0)
        upstream = CountingUpstream(cfg)
        eng = GatewayEngine(cfg, upstream=upstream)
        eng.handle(req(index=0, text="same"))
        second = eng.handle(req(index=1, text="same"))
        self.assertTrue(second.cache_hit)
        self.assertEqual(second.cost_usd, 0.0)
        self.assertEqual(upstream.calls, 1)

    def test_rate_limit_precedes_everything(self):
        cfg = GatewayConfig(failure_rate=0.0, rpm_limit=1, window_requests=1000)
        upstream = CountingUpstream(cfg)
        eng = GatewayEngine(cfg, upstream=upstream)
        first = eng.handle(req(index=0))
        second = eng.handle(req(index=1))
        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 429)
        self.assertTrue(second.rate_limited)
        self.assertEqual(upstream.calls, 1)

    def test_off_mode_is_full_passthrough(self):
        cfg = GatewayConfig(failure_rate=0.0, gateway_enabled=False,
                            rpm_limit=1, window_requests=1000)
        eng = GatewayEngine(cfg)
        for i in range(5):
            d = eng.handle(req(index=i, key=f"K{i}", text=f"t{i}"))
            self.assertEqual(d.status, 200)
        rows = eng.ledger.spans
        self.assertFalse(any(r["cache_hit"] for r in rows))
        self.assertFalse(any(r["idempotent_replay"] for r in rows))
        self.assertFalse(any(r["rate_limited"] for r in rows))
        self.assertFalse(any(r["fallback_used"] for r in rows))

    def test_metrics_counters_are_consistent(self):
        cfg = GatewayConfig(seed=7, failure_rate=0.2)
        eng = GatewayEngine(cfg)
        for i in range(50):
            eng.handle(req(index=i, key=f"K{i}" if i % 3 == 0 else "",
                           text=f"t{i % 10}"))
        m = eng.metrics()
        self.assertEqual(m["requests"], 50)
        self.assertEqual(m["cache_hits"] + m["cache_misses"], 50 - m["rate_limited"] - m["idempotent_blocked"])
        self.assertAlmostEqual(m["total_cost_usd"], eng.ledger.total_cost(), places=6)


if __name__ == "__main__":
    unittest.main()
