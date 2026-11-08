from __future__ import annotations

"""FastAPI 薄适配层：核心逻辑零依赖，本文件是唯一需要 fastapi 的地方。

W1 范围：OpenAI 兼容 chat/completions 子集（非流式 + SSE）、health、metrics、config。
单进程、非并发安全；并发与队列属 W2，本轮不实现、不宣称。
"""

import json
from typing import Iterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import GatewayConfig
from .core import ChatRequest, Decision
from .engine import GatewayEngine


def _completion_id(decision: Decision, config: GatewayConfig) -> str:
    return f"gw-{decision.index}-{config.config_hash()}"


def _openai_shape(decision: Decision, config: GatewayConfig) -> dict:
    reply = decision.reply
    tin = reply.in_tokens if reply else 0
    tout = reply.out_tokens if reply else 0
    return {
        "id": _completion_id(decision, config),
        "object": "chat.completion",
        "created": 0,
        "model": decision.route,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": reply.text if reply else ""},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": tin,
            "completion_tokens": tout,
            "total_tokens": tin + tout,
        },
        "gateway": {
            "cache_hit": decision.cache_hit,
            "idempotent_replay": decision.idempotent_replay,
            "fallback_used": decision.fallback_used,
            "attempts": decision.attempts,
            "cost_usd": round(decision.cost_usd, 8),
            "simulated_latency_ms": decision.simulated_latency_ms,
            "reasons": list(decision.reasons),
        },
    }


def _sse(decision: Decision, config: GatewayConfig) -> Iterator[str]:
    text = decision.reply.text if decision.reply else ""
    parts = [text[i : i + 16] for i in range(0, len(text), 16)] or [""]
    for part in parts:
        chunk = {
            "id": _completion_id(decision, config),
            "object": "chat.completion.chunk",
            "created": 0,
            "model": decision.route,
            "choices": [{"index": 0, "delta": {"content": part}, "finish_reason": None}],
        }
        yield "data: " + json.dumps(chunk, sort_keys=True, ensure_ascii=False) + "\n\n"
    tail = {
        "id": _completion_id(decision, config),
        "object": "chat.completion.chunk",
        "created": 0,
        "model": decision.route,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield "data: " + json.dumps(tail, sort_keys=True, ensure_ascii=False) + "\n\n"
    yield "data: [DONE]\n\n"


def create_app(
    config: GatewayConfig | None = None, engine: GatewayEngine | None = None
) -> FastAPI:
    cfg = config or GatewayConfig()
    eng = engine or GatewayEngine(cfg)
    app = FastAPI(
        title="Agent Gateway (W1 skeleton)",
        version="0.1.0",
        description="OpenAI 兼容子集 + 路由/缓存/限流/幂等；默认确定性 fake 上游",
    )
    app.state.config = cfg
    app.state.engine = eng

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "config_hash": cfg.config_hash()}

    @app.get("/admin/config")
    async def admin_config() -> dict:
        return {"config_hash": cfg.config_hash(), "config": cfg.snapshot()}

    @app.get("/metrics")
    async def metrics() -> dict:
        return eng.metrics()

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        raw_messages = body.get("messages") or []
        messages = tuple(
            (str(m.get("role", "user")), str(m.get("content", "")))
            for m in raw_messages
            if isinstance(m, dict)
        )
        if not messages:
            return JSONResponse(
                {"error": {"message": "messages is required", "type": "invalid_request"}},
                status_code=400,
            )
        req = ChatRequest(
            model=str(body.get("model") or cfg.models[0]),
            messages=messages,
            temperature=float(body.get("temperature", 0.0)),
            stream=bool(body.get("stream", False)),
            idempotency_key=request.headers.get("idempotency-key", "") or "",
            caller=request.headers.get("x-caller", "default") or "default",
            index=len(eng.ledger.spans),
        )
        decision = eng.handle(req)
        if decision.status != 200:
            return JSONResponse(
                {
                    "error": {
                        "message": ",".join(decision.reasons) or "upstream error",
                        "type": "rate_limit_exceeded" if decision.rate_limited else "upstream_error",
                    },
                    "gateway": {
                        "rate_limited": decision.rate_limited,
                        "attempts": decision.attempts,
                    },
                },
                status_code=decision.status,
            )
        if req.stream:
            return StreamingResponse(_sse(decision, cfg), media_type="text/event-stream")
        return JSONResponse(_openai_shape(decision, cfg))

    return app


app = create_app()
