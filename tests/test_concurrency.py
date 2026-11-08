"""W2 并发行为测试：只断言与线程调度无关的不变量，不测吞吐 / 墙钟。"""

import threading
import time
import unittest

from gateway.components import ExactCache, FixedWindowLimiter, IdempotencyStore
from gateway.concurrency import BoundedTaskQueue, ConcurrentGateway, SingleFlight
from gateway.config import GatewayConfig
from gateway.core import ChatRequest, UpstreamReply
from gateway.engine import GatewayEngine
from gateway.upstream import FakeUpstream


def req(index, key="", text=None, caller="a"):
    content = text if text is not None else f"t{index}"
    return ChatRequest(
        model="gateway-primary", messages=(("user", content),),
        idempotency_key=key, caller=caller, index=index,
    )


class CountingUpstream(FakeUpstream):
    def __init__(self, config):
        super().__init__(config)
        self.calls = 0
        self._lock = threading.Lock()

    def complete(self, request, attempt=0, target=None):
        with self._lock:
            self.calls += 1
        return super().complete(request, attempt=attempt, target=target)


class BlockingUpstream(FakeUpstream):
    def __init__(self, config):
        super().__init__(config)
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete(self, request, attempt=0, target=None):
        self.entered.set()
        self.release.wait(timeout=3)
        return super().complete(request, attempt=attempt, target=target)


class TestBoundedQueue(unittest.TestCase):
    def test_full_queue_rejects_and_counts(self):
        q = BoundedTaskQueue(2)
        self.assertTrue(q.try_put("a"))
        self.assertTrue(q.try_put("b"))
        self.assertFalse(q.try_put("c"))
        self.assertEqual((q.submitted, q.rejected), (2, 1))

    def test_capacity_is_at_least_one(self):
        self.assertEqual(BoundedTaskQueue(0).capacity, 1)


class TestSingleFlight(unittest.TestCase):
    def test_exactly_one_leader_when_all_acquire_before_release(self):
        sf = SingleFlight()
        barrier = threading.Barrier(9)
        outcomes = []
        lock = threading.Lock()

        def worker():
            barrier.wait()
            leader, event = sf.acquire("K")
            with lock:
                outcomes.append((leader, event))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        barrier.wait()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(len(outcomes), 8)
        self.assertEqual(sum(1 for leader, _ in outcomes if leader), 1)
        self.assertEqual(sf.waited, 7)
        self.assertEqual(sf.calls, 1)
        sf.release("K")
        self.assertTrue(all(ev.is_set() for leader, ev in outcomes if not leader))

    def test_empty_key_never_blocks(self):
        sf = SingleFlight()
        for _ in range(5):
            leader, event = sf.acquire("")
            self.assertTrue(leader)
            self.assertIsNone(event)
        self.assertEqual(sf.calls, 5)
        self.assertEqual(sf.waited, 0)


class TestConcurrentGateway(unittest.TestCase):
    def test_same_idempotency_key_calls_upstream_exactly_once(self):
        cfg = GatewayConfig(failure_rate=0.0, rpm_limit=0, cache_capacity=256)
        upstream = CountingUpstream(cfg)
        engine = GatewayEngine(cfg, upstream=upstream)
        with ConcurrentGateway(engine, workers=8, queue_capacity=256) as pool:
            futures = pool.submit_many([req(i, key="K", text="same") for i in range(32)])
            decisions = pool.collect(futures, timeout_s=10)
        self.assertEqual(len(decisions), 32)
        self.assertEqual(upstream.calls, 1)
        self.assertEqual([d.index for d in decisions], list(range(32)))

    def test_rate_limit_count_is_exactly_the_limit_under_concurrency(self):
        cfg = GatewayConfig(failure_rate=0.0, rpm_limit=3, window_requests=1000)
        engine = GatewayEngine(cfg)
        with ConcurrentGateway(engine, workers=8, queue_capacity=256) as pool:
            futures = pool.submit_many([req(i) for i in range(40)])
            decisions = pool.collect(futures, timeout_s=10)
        self.assertEqual(sum(1 for d in decisions if d.status == 200), 3)
        self.assertEqual(sum(1 for d in decisions if d.status == 429), 37)
        self.assertEqual(len(engine.ledger.spans), 40)
        self.assertEqual([d.index for d in decisions], list(range(40)))

    def test_queue_backpressure_returns_429_without_touching_ledger(self):
        cfg = GatewayConfig(failure_rate=0.0, rpm_limit=0)
        upstream = BlockingUpstream(cfg)
        engine = GatewayEngine(cfg, upstream=upstream)
        pool = ConcurrentGateway(engine, workers=1, queue_capacity=2)
        try:
            first = pool.submit(req(0))
            self.assertTrue(upstream.entered.wait(timeout=3))
            more = [pool.submit(req(i)) for i in range(1, 10)]
            rejected = [
                f for f in more
                if f.done() and f.result().status == 429
                and "queue_full" in f.result().reasons
            ]
            self.assertGreater(len(rejected), 0)
            self.assertGreater(pool.stats()["queue_rejected"], 0)
            self.assertEqual(len(engine.ledger.spans), 0)  # leader 仍阻塞，未写台账
        finally:
            upstream.release.set()
            first.result(timeout=10)
            for f in more:
                f.result(timeout=10)
            pool.shutdown(wait=True)

    def test_stats_report_deterministic_counters(self):
        cfg = GatewayConfig(failure_rate=0.0, rpm_limit=0)
        engine = GatewayEngine(cfg)
        with ConcurrentGateway(engine, workers=4, queue_capacity=64) as pool:
            pool.collect(pool.submit_many([req(i) for i in range(20)]), timeout_s=10)
            deadline = time.monotonic() + 3
            while pool.stats()["completed"] < 20 and time.monotonic() < deadline:
                time.sleep(0.005)
            stats = pool.stats()
        self.assertEqual(stats["workers"], 4)
        self.assertEqual(stats["queue_capacity"], 64)
        self.assertEqual(stats["queue_submitted"], 20)
        self.assertEqual(stats["queue_rejected"], 0)
        self.assertEqual(stats["completed"], 20)

    def test_off_mode_is_still_full_passthrough_under_concurrency(self):
        cfg = GatewayConfig(failure_rate=0.0, gateway_enabled=False, rpm_limit=1)
        engine = GatewayEngine(cfg)
        with ConcurrentGateway(engine, workers=4, queue_capacity=64) as pool:
            decisions = pool.collect(
                pool.submit_many([req(i, key=f"K{i}") for i in range(12)]), timeout_s=10
            )
        self.assertTrue(all(d.status == 200 for d in decisions))
        self.assertFalse(any(d.cache_hit or d.rate_limited for d in decisions))


class TestBenchInvariants(unittest.TestCase):
    def test_bench_invariants_all_pass(self):
        from gateway.bench import (
            check_rate_limit_under_concurrency,
            check_same_key_single_flight,
        )
        same = check_same_key_single_flight(n=16, workers=4)
        limited = check_rate_limit_under_concurrency(n=20, limit=2, workers=4)
        self.assertEqual(same["upstream_calls"], 1)
        self.assertEqual(same["ledger_spans"], same["requests"])
        self.assertEqual(limited["status_200"], limited["rpm_limit"])
        self.assertEqual(limited["ledger_spans"], limited["requests"])

    def test_bench_never_writes_archives(self):
        import inspect
        from gateway import bench
        src = inspect.getsource(bench)
        for forbidden in ("write_archive", "experiments/runs", "perf_counter"):
            self.assertNotIn(forbidden, src)


class TestComponentThreadSafety(unittest.TestCase):
    def test_counters_are_not_lost_under_many_threads(self):
        cache = ExactCache(64, 0)
        limiter = FixedWindowLimiter(0, 100)
        idem = IdempotencyStore()
        sample = UpstreamReply("x", 10, 20, True, target="gateway-primary", latency_ms=1.0)

        def worker(i):
            cache.put(f"k{i % 8}", sample, i)
            cache.get(f"k{i % 8}", i)
            limiter.allow("a", i + 1)
            idem.put(f"x{i % 8}", sample)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(200)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(cache.hits + cache.misses, 200)
        self.assertEqual(len(idem._data), 8)
        self.assertEqual(limiter.rejected, 0)


if __name__ == "__main__":
    unittest.main()
