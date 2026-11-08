import json
import unittest

from gateway.core import ChatRequest, Decision, UpstreamReply


def req(**kw):
    base = {
        "model": "gateway-primary",
        "messages": (("user", "hello"),),
        "temperature": 0.0,
    }
    base.update(kw)
    return ChatRequest(**base)


class TestChatRequest(unittest.TestCase):
    def test_cache_key_is_deterministic(self):
        self.assertEqual(req().cache_key(), req().cache_key())
        self.assertEqual(len(req().cache_key()), 64)

    def test_cache_key_ignores_transport_fields(self):
        a = req(stream=False, idempotency_key="k1", caller="a", index=1)
        b = req(stream=True, idempotency_key="k2", caller="b", index=99)
        self.assertEqual(a.cache_key(), b.cache_key())

    def test_cache_key_depends_on_normalized_content(self):
        a = req()
        self.assertNotEqual(a.cache_key(), req(model="gateway-fallback").cache_key())
        self.assertNotEqual(a.cache_key(), req(messages=(("user", "world"),)).cache_key())
        self.assertNotEqual(a.cache_key(), req(temperature=0.7).cache_key())

    def test_cache_key_is_json_key_order_independent(self):
        raw = '{"temperature": 0.0, "model": "gateway-primary", "messages": [{"content": "hello", "role": "user"}]}'
        payload = json.loads(raw)
        r = ChatRequest(
            model=payload["model"],
            messages=tuple((m["role"], m["content"]) for m in payload["messages"]),
            temperature=payload["temperature"],
        )
        self.assertEqual(r.cache_key(), req().cache_key())

    def test_to_dict_is_json_serializable(self):
        json.dumps(req().to_dict(), sort_keys=True, ensure_ascii=False)


class TestDecisionRow(unittest.TestCase):
    def test_as_row_has_no_wall_clock_field(self):
        d = Decision(
            status=200,
            reply=UpstreamReply("x", 10, 20, True, target="gateway-primary", latency_ms=50.0),
            index=0, caller="a", model="gateway-primary", route="gateway-primary",
            cost_usd=0.0001, simulated_latency_ms=50.0,
        )
        row = d.as_row()
        self.assertNotIn("wall_ms", row)
        self.assertNotIn("timestamp", row)
        self.assertNotIn("created_at", row)
        self.assertEqual(row["in_tokens"], 10)
        self.assertEqual(row["out_tokens"], 20)

    def test_as_row_is_byte_stable(self):
        d = Decision(status=429, reply=None, index=1, caller="a", model="m", route="")
        a = json.dumps(d.as_row(), sort_keys=True, ensure_ascii=False)
        b = json.dumps(Decision(status=429, reply=None, index=1, caller="a", model="m", route="").as_row(), sort_keys=True, ensure_ascii=False)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
