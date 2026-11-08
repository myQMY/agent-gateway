import unittest
import warnings

warnings.filterwarnings("ignore", message=".*httpx.*")

try:
    from fastapi.testclient import TestClient
    from gateway.app import create_app
    HAS_FASTAPI = True
except ImportError:  # 核心逻辑测试不依赖 fastapi
    HAS_FASTAPI = False

from gateway.config import GatewayConfig


def body(text="hello", **kw):
    payload = {"model": "gateway-primary", "messages": [{"role": "user", "content": text}]}
    payload.update(kw)
    return payload


@unittest.skipUnless(HAS_FASTAPI, "fastapi not installed (pip install -r requirements.txt)")
class TestHttpLayer(unittest.TestCase):
    def setUp(self):
        self.cfg = GatewayConfig(seed=11, failure_rate=0.0, rpm_limit=0)
        self.client = TestClient(create_app(self.cfg))

    def test_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["config_hash"], self.cfg.config_hash())

    def test_admin_config_exposes_hash(self):
        r = self.client.get("/admin/config")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["config_hash"], self.cfg.config_hash())

    def test_chat_completion_openai_shape(self):
        r = self.client.post("/v1/chat/completions", json=body())
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["object"], "chat.completion")
        self.assertEqual(data["created"], 0)
        self.assertEqual(data["choices"][0]["message"]["role"], "assistant")
        self.assertTrue(data["choices"][0]["message"]["content"].startswith("fake:"))
        self.assertIn("prompt_tokens", data["usage"])
        self.assertIn("cache_hit", data["gateway"])

    def test_created_is_constant_for_reproducibility(self):
        r1 = self.client.post("/v1/chat/completions", json=body("a"))
        r2 = self.client.post("/v1/chat/completions", json=body("b"))
        self.assertEqual(r1.json()["created"], r2.json()["created"])

    def test_missing_messages_returns_400(self):
        r = self.client.post("/v1/chat/completions", json={"model": "m"})
        self.assertEqual(r.status_code, 400)

    def test_cache_hit_on_repeated_request(self):
        self.client.post("/v1/chat/completions", json=body("repeat-me"))
        r = self.client.post("/v1/chat/completions", json=body("repeat-me"))
        self.assertTrue(r.json()["gateway"]["cache_hit"])
        self.assertEqual(r.json()["gateway"]["cost_usd"], 0.0)

    def test_idempotency_key_replays_first_result(self):
        headers = {"Idempotency-Key": "fixed-key"}
        r1 = self.client.post("/v1/chat/completions", json=body("k1"), headers=headers)
        r2 = self.client.post("/v1/chat/completions", json=body("k2"), headers=headers)
        self.assertFalse(r1.json()["gateway"]["idempotent_replay"])
        self.assertTrue(r2.json()["gateway"]["idempotent_replay"])
        self.assertEqual(r1.json()["choices"][0]["message"]["content"],
                         r2.json()["choices"][0]["message"]["content"])

    def test_sse_stream(self):
        r = self.client.post("/v1/chat/completions", json=body("stream-me", stream=True))
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/event-stream", r.headers["content-type"])
        self.assertIn("data: ", r.text)
        self.assertIn("[DONE]", r.text)

    def test_metrics_endpoint(self):
        self.client.post("/v1/chat/completions", json=body("a"))
        self.client.post("/v1/chat/completions", json=body("a"))
        data = self.client.get("/metrics").json()
        self.assertEqual(data["requests"], 2)
        self.assertEqual(data["cache_hits"], 1)

    def test_rate_limit_returns_429(self):
        cfg = GatewayConfig(seed=11, failure_rate=0.0, rpm_limit=1, window_requests=1000)
        client = TestClient(create_app(cfg))
        headers = {"X-Caller": "same"}
        self.assertEqual(client.post("/v1/chat/completions", json=body("a"), headers=headers).status_code, 200)
        r = client.post("/v1/chat/completions", json=body("b"), headers=headers)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()["error"]["type"], "rate_limit_exceeded")


if __name__ == "__main__":
    unittest.main()
