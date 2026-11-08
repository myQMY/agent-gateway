"""W2 真实供应商适配器测试：完全用 mock urlopen，零网络、零密钥。"""

import json
import os
from unittest.mock import patch
from urllib.error import HTTPError, URLError
import unittest

from gateway.components import Router
from gateway.config import GatewayConfig
from gateway.core import ChatRequest
from gateway.real_upstream import RealUpstream, RealUpstreamConfig

CFG = GatewayConfig(failure_rate=0.0)
REAL = RealUpstreamConfig(base_url="https://example.invalid/v1", timeout_s=2.0)


class FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def ok_payload(text="real-reply", tin=11, tout=22):
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": tin, "completion_tokens": tout},
    }


def request(index=0):
    return ChatRequest(
        model="gateway-primary", messages=(("user", "hello"),),
        temperature=0.3, index=index,
    )


class TestEnvSwitch(unittest.TestCase):
    def test_switch_off_returns_none(self):
        with patch.dict(os.environ, {"LLM_BASE_URL": "https://example.invalid"}, clear=True):
            self.assertIsNone(RealUpstreamConfig.from_env())

    def test_switch_on_without_base_url_returns_none(self):
        with patch.dict(os.environ, {"GATEWAY_REAL_UPSTREAM": "1"}, clear=True):
            self.assertIsNone(RealUpstreamConfig.from_env())

    def test_switch_on_reads_all_fields(self):
        env = {
            "GATEWAY_REAL_UPSTREAM": "true",
            "LLM_BASE_URL": "https://example.invalid/v1/",
            "GATEWAY_API_KEY_ENV": "MY_KEY",
            "GATEWAY_UPSTREAM_TIMEOUT": "3.5",
            "GATEWAY_UPSTREAM_PATH": "/v1/chat/completions",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = RealUpstreamConfig.from_env()
        self.assertEqual(cfg.base_url, "https://example.invalid/v1")
        self.assertEqual(cfg.api_key_env, "MY_KEY")
        self.assertEqual(cfg.timeout_s, 3.5)
        self.assertEqual(cfg.path, "/v1/chat/completions")

    def test_bad_timeout_falls_back_to_default(self):
        env = {"GATEWAY_REAL_UPSTREAM": "1", "LLM_BASE_URL": "https://x",
               "GATEWAY_UPSTREAM_TIMEOUT": "not-a-number"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(RealUpstreamConfig.from_env().timeout_s, 10.0)


class TestRealUpstream(unittest.TestCase):
    def test_missing_key_fails_without_network_call(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("gateway.real_upstream.urlopen") as mocked:
            reply = RealUpstream(REAL).complete(request())
            mocked.assert_not_called()
        self.assertFalse(reply.ok)
        self.assertEqual(reply.error_kind, "auth_error")

    def test_success_parses_text_and_usage(self):
        env = {"LLM_API_KEY": "test-key-123"}
        with patch.dict(os.environ, env, clear=True), \
             patch("gateway.real_upstream.urlopen", return_value=FakeResponse(ok_payload())) as mocked:
            reply = RealUpstream(REAL).complete(request(), attempt=0, target="gateway-primary")
        self.assertTrue(reply.ok)
        self.assertEqual(reply.text, "real-reply")
        self.assertEqual((reply.in_tokens, reply.out_tokens), (11, 22))
        self.assertEqual(reply.target, "gateway-primary")
        self.assertEqual(reply.latency_ms, 0.0)  # 真实延迟不写进模拟延迟字段
        wire = mocked.call_args[0][0]
        self.assertEqual(wire.get_method(), "POST")
        self.assertEqual(wire.full_url, "https://example.invalid/v1/chat/completions")
        self.assertEqual(wire.headers["Authorization"], "Bearer test-key-123")
        payload = json.loads(wire.data.decode("utf-8"))
        self.assertEqual(payload["model"], "gateway-primary")
        self.assertEqual(payload["stream"], False)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "hello"}])

    def test_missing_usage_degrades_to_zero(self):
        payload = {"choices": [{"message": {"content": "x"}}]}
        with patch.dict(os.environ, {"LLM_API_KEY": "k"}, clear=True), \
             patch("gateway.real_upstream.urlopen", return_value=FakeResponse(payload)):
            reply = RealUpstream(REAL).complete(request())
        self.assertTrue(reply.ok)
        self.assertEqual((reply.in_tokens, reply.out_tokens), (0, 0))

    def test_http_errors_are_classified(self):
        cases = [
            (401, "auth_error"), (403, "auth_error"),
            (429, "upstream_rate_limit"), (500, "upstream_5xx"),
        ]
        for code, kind in cases:
            with self.subTest(code=code):
                err = HTTPError("https://x", code, "err", {}, None)
                with patch.dict(os.environ, {"LLM_API_KEY": "k"}, clear=True), \
                     patch("gateway.real_upstream.urlopen", side_effect=err):
                    reply = RealUpstream(REAL).complete(request())
                self.assertFalse(reply.ok)
                self.assertEqual(reply.error_kind, kind)

    def test_timeout_and_url_error_map_to_timeout(self):
        for exc in (TimeoutError(), URLError("down")):
            with self.subTest(exc=type(exc).__name__):
                with patch.dict(os.environ, {"LLM_API_KEY": "k"}, clear=True), \
                     patch("gateway.real_upstream.urlopen", side_effect=exc):
                    reply = RealUpstream(REAL).complete(request())
                self.assertEqual(reply.error_kind, "timeout")

    def test_malformed_response_is_invalid_response(self):
        for payload in ({}, {"choices": []}, {"choices": [{"message": {}}]}):
            with self.subTest(payload=payload):
                with patch.dict(os.environ, {"LLM_API_KEY": "k"}, clear=True), \
                     patch("gateway.real_upstream.urlopen",
                           return_value=FakeResponse(payload)):
                    reply = RealUpstream(REAL).complete(request())
                self.assertEqual(reply.error_kind, "invalid_response")

    def test_router_can_use_real_upstream_via_protocol(self):
        with patch.dict(os.environ, {"LLM_API_KEY": "k"}, clear=True), \
             patch("gateway.real_upstream.urlopen", return_value=FakeResponse(ok_payload())):
            result = Router(CFG, upstream=RealUpstream(REAL)).call(request())
        self.assertTrue(result.reply.ok)
        self.assertEqual(result.attempts, 1)
        self.assertFalse(result.fallback_used)

    def test_config_is_independent_of_gateway_config_hash(self):
        before = CFG.config_hash()
        RealUpstreamConfig.from_env()
        RealUpstream(REAL)
        self.assertEqual(CFG.config_hash(), before)
        self.assertEqual(GatewayConfig().config_hash(), "aa1142308d8d")


if __name__ == "__main__":
    unittest.main()
