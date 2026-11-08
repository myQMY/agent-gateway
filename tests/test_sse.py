"""W2 SSE 帧测试：纯标准库函数，不需要 fastapi。"""

from dataclasses import replace
import json
import unittest

from gateway.config import GatewayConfig
from gateway.core import Decision, UpstreamReply
from gateway.sse import (
    SSE_HEADERS,
    cancelled_frame,
    completion_id,
    sse_frames,
)

CFG = GatewayConfig()


def make_decision(status=200, text="hello world", reasons=(), **kw):
    reply = (
        UpstreamReply(text, 10, 20, True, target="gateway-primary", latency_ms=50.0)
        if status == 200 else None
    )
    return Decision(
        status=status, reply=reply, index=7, caller="a",
        model="gateway-primary", route="gateway-primary", reasons=reasons, **kw
    )


def frames(decision, **kw):
    return list(sse_frames(decision, CFG, **kw))


def payloads(frame_list):
    out = []
    for frame in frame_list:
        for line in frame.strip().split("\n"):
            if line.startswith("data: ") and line[6:] != "[DONE]":
                out.append(json.loads(line[6:]))
    return out


class TestSSEFrames(unittest.TestCase):
    def test_first_chunk_carries_assistant_role(self):
        ps = payloads(frames(make_decision()))
        self.assertEqual(ps[0]["choices"][0]["delta"]["role"], "assistant")
        self.assertEqual(ps[0]["choices"][0]["delta"]["content"], "")

    def test_content_chunks_restore_original_text(self):
        text = "abcdefghij" * 7
        ps = payloads(frames(make_decision(text=text), chunk_size=4))
        joined = "".join(p["choices"][0]["delta"].get("content", "") for p in ps)
        self.assertEqual(joined, text)

    def test_chunk_size_controls_number_of_content_frames(self):
        text = "a" * 20
        ps = payloads(frames(make_decision(text=text), chunk_size=5))
        content_frames = [p for p in ps if p["choices"][0]["delta"].get("content")]
        self.assertEqual(len(content_frames), 4)

    def test_tail_has_finish_reason_and_usage(self):
        ps = payloads(frames(make_decision()))
        tails = [p for p in ps if p["choices"][0]["finish_reason"] == "stop"]
        self.assertEqual(len(tails), 1)
        self.assertEqual(tails[0]["usage"]["prompt_tokens"], 10)
        self.assertEqual(tails[0]["usage"]["completion_tokens"], 20)
        self.assertEqual(tails[0]["usage"]["total_tokens"], 30)

    def test_stream_ends_with_done(self):
        self.assertEqual(frames(make_decision())[-1], "data: [DONE]\n\n")

    def test_empty_text_still_produces_full_sequence(self):
        fs = frames(make_decision(text=""))
        self.assertEqual(len(payloads(fs)), 3)  # role + one empty content + tail
        self.assertEqual(fs[-1], "data: [DONE]\n\n")

    def test_cache_hit_and_idempotent_replay_still_stream(self):
        for d in (replace(make_decision(), cache_hit=True),
                  replace(make_decision(), idempotent_replay=True)):
            self.assertEqual(frames(d)[-1], "data: [DONE]\n\n")

    def test_bytes_are_deterministic(self):
        a = "".join(frames(make_decision(text="abc")))
        b = "".join(frames(make_decision(text="abc")))
        self.assertEqual(a, b)
        self.assertTrue(all(p["created"] == 0 for p in payloads(frames(make_decision()))))


class TestSSEErrorsAndCancellation(unittest.TestCase):
    def test_non_200_uses_error_event_then_done(self):
        fs = frames(make_decision(status=429, reasons=("rate_limit_exceeded",)))
        self.assertTrue(fs[0].startswith("event: error\n"))
        self.assertIn("rate_limit_exceeded", fs[0])
        self.assertEqual(fs[-1], "data: [DONE]\n\n")

    def test_error_type_reflects_rate_limit(self):
        fs = frames(make_decision(status=429, reasons=("rate_limit_exceeded",),
                                  rate_limited=True))
        body = json.loads(fs[0].split("data: ", 1)[1])
        self.assertEqual(body["error"]["type"], "rate_limit_exceeded")

    def test_upstream_error_has_no_rate_limit_type(self):
        fs = frames(make_decision(status=502, reasons=("upstream_5xx",)))
        body = json.loads(fs[0].split("data: ", 1)[1])
        self.assertEqual(body["error"]["type"], "upstream_error")

    def test_cancellation_emits_cancelled_and_no_done(self):
        fs = frames(make_decision(), is_cancelled=lambda: True)
        self.assertEqual(len(fs), 1)
        self.assertTrue(fs[0].startswith("event: cancelled\n"))
        self.assertNotIn("[DONE]", "".join(fs))

    def test_cancelled_frame_is_exported_for_http_disconnect(self):
        frame = cancelled_frame(make_decision(), CFG)
        self.assertTrue(frame.startswith("event: cancelled\n"))
        self.assertIn("client_cancelled", frame)


class TestSSEMetadata(unittest.TestCase):
    def test_completion_id_is_stable(self):
        self.assertEqual(completion_id(make_decision(), CFG), "gw-7-aa1142308d8d")

    def test_headers_disable_buffering_and_caching(self):
        self.assertEqual(SSE_HEADERS["Cache-Control"], "no-cache")
        self.assertEqual(SSE_HEADERS["X-Accel-Buffering"], "no")


if __name__ == "__main__":
    unittest.main()
