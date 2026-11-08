import unittest

from gateway.components import CACHE_HIT_LATENCY_MS, ExactCache, FixedWindowLimiter, IdempotencyStore
from gateway.core import UpstreamReply


def reply(text="x"):
    return UpstreamReply(text, 10, 20, True, target="gateway-primary", latency_ms=50.0)


class TestExactCache(unittest.TestCase):
    def test_miss_then_hit(self):
        c = ExactCache(capacity=8, ttl_requests=0)
        self.assertIsNone(c.get("k", 1))
        c.put("k", reply(), 1)
        self.assertEqual(c.get("k", 2).text, "x")
        self.assertEqual((c.hits, c.misses), (1, 1))

    def test_lru_eviction(self):
        c = ExactCache(capacity=2, ttl_requests=0)
        c.put("a", reply("a"), 1)
        c.put("b", reply("b"), 2)
        c.put("c", reply("c"), 3)
        self.assertIsNone(c.get("a", 4))
        self.assertEqual(c.evictions, 1)
        self.assertEqual(len(c), 2)

    def test_lru_touch_keeps_hot_entry(self):
        c = ExactCache(capacity=2, ttl_requests=0)
        c.put("a", reply("a"), 1)
        c.put("b", reply("b"), 2)
        c.get("a", 3)
        c.put("c", reply("c"), 4)
        self.assertIsNotNone(c.get("a", 5))
        self.assertIsNone(c.get("b", 6))

    def test_logical_ttl_expires(self):
        c = ExactCache(capacity=8, ttl_requests=2)
        c.put("k", reply(), tick=1)
        self.assertIsNone(c.get("k", tick=3))
        self.assertEqual(c.expired, 1)

    def test_ttl_zero_never_expires(self):
        c = ExactCache(capacity=8, ttl_requests=0)
        c.put("k", reply(), tick=1)
        self.assertIsNotNone(c.get("k", tick=9999))

    def test_cache_hit_latency_is_fixed_constant(self):
        self.assertEqual(CACHE_HIT_LATENCY_MS, 2.0)


class TestFixedWindowLimiter(unittest.TestCase):
    def test_zero_limit_allows_everything(self):
        lim = FixedWindowLimiter(limit=0, window_requests=10)
        self.assertTrue(all(lim.allow("a", i) for i in range(1, 101)))
        self.assertEqual(lim.rejected, 0)

    def test_limit_within_window(self):
        lim = FixedWindowLimiter(limit=2, window_requests=10)
        self.assertTrue(lim.allow("a", 1))
        self.assertTrue(lim.allow("a", 2))
        self.assertFalse(lim.allow("a", 3))
        self.assertEqual(lim.rejected, 1)

    def test_window_boundary_resets(self):
        lim = FixedWindowLimiter(limit=2, window_requests=3)
        self.assertTrue(lim.allow("a", 1))
        self.assertTrue(lim.allow("a", 2))
        self.assertFalse(lim.allow("a", 3))
        self.assertTrue(lim.allow("a", 4))

    def test_callers_are_isolated(self):
        lim = FixedWindowLimiter(limit=1, window_requests=100)
        self.assertTrue(lim.allow("a", 1))
        self.assertTrue(lim.allow("b", 1))
        self.assertFalse(lim.allow("a", 2))


class TestIdempotencyStore(unittest.TestCase):
    def test_empty_key_never_stored(self):
        s = IdempotencyStore()
        s.put("", reply())
        self.assertIsNone(s.get(""))

    def test_replay_returns_first_reply(self):
        s = IdempotencyStore()
        s.put("k", reply("first"))
        self.assertEqual(s.get("k").text, "first")
        s.mark_blocked()
        self.assertEqual(s.blocked, 1)

    def test_distinct_keys_do_not_collide(self):
        s = IdempotencyStore()
        s.put("k1", reply("one"))
        self.assertIsNone(s.get("k2"))


if __name__ == "__main__":
    unittest.main()
