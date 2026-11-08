from __future__ import annotations

"""W2 SSE 帧生成（纯标准库，不依赖 fastapi）。

OpenAI 兼容 chat/completions 子集的流式帧：role 首帧 -> 内容块 ->
finish_reason/usage 尾帧 -> [DONE]。错误走 event: error，取消走 event: cancelled
且**不发 [DONE]**。所有 JSON 用 sort_keys + UTF-8，保证同输入同字节。
"""

import json
from typing import Callable, Iterator

from .config import GatewayConfig
from .core import Decision

DEFAULT_CHUNK_SIZE = 16

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def completion_id(decision: Decision, config: GatewayConfig) -> str:
    return f"gw-{decision.index}-{config.config_hash()}"


def _json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _chunk(cid: str, model: str, delta: dict, finish_reason=None, usage=None) -> dict:
    payload = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": 0,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


def error_frame(decision: Decision, config: GatewayConfig) -> str:
    payload = {
        "error": {
            "message": ",".join(decision.reasons) or "upstream error",
            "type": "rate_limit_exceeded" if decision.rate_limited else "upstream_error",
        },
        "gateway": {"status": decision.status, "attempts": decision.attempts},
    }
    return "event: error\ndata: " + _json(payload) + "\n\n"


def cancelled_frame(decision: Decision, config: GatewayConfig) -> str:
    payload = {
        "id": completion_id(decision, config),
        "reason": "client_cancelled",
    }
    return "event: cancelled\ndata: " + _json(payload) + "\n\n"


def sse_frames(
    decision: Decision,
    config: GatewayConfig,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    is_cancelled: Callable[[], bool] | None = None,
) -> Iterator[str]:
    """生成完整 SSE 事件序列。取消在每帧前检查，取消后不再发 [DONE]。"""
    cid = completion_id(decision, config)
    model = decision.route

    def should_stop() -> bool:
        return bool(is_cancelled and is_cancelled())

    if decision.status != 200:
        if should_stop():
            yield cancelled_frame(decision, config)
            return
        yield error_frame(decision, config)
        yield "data: [DONE]\n\n"
        return

    reply = decision.reply
    text = reply.text if reply else ""
    tin = reply.in_tokens if reply else 0
    tout = reply.out_tokens if reply else 0
    usage = {
        "prompt_tokens": tin,
        "completion_tokens": tout,
        "total_tokens": tin + tout,
    }
    size = max(1, chunk_size)
    parts = [text[i : i + size] for i in range(0, len(text), size)] or [""]

    first = _chunk(cid, model, {"role": "assistant", "content": ""})
    if should_stop():
        yield cancelled_frame(decision, config)
        return
    yield "data: " + _json(first) + "\n\n"

    for part in parts:
        chunk = _chunk(cid, model, {"content": part})
        if should_stop():
            yield cancelled_frame(decision, config)
            return
        yield "data: " + _json(chunk) + "\n\n"

    tail = _chunk(cid, model, {}, finish_reason="stop", usage=usage)
    if should_stop():
        yield cancelled_frame(decision, config)
        return
    yield "data: " + _json(tail) + "\n\n"
    yield "data: [DONE]\n\n"
