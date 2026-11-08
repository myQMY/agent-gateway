from __future__ import annotations

"""FastAPI 薄适配层：核心逻辑零依赖，本文件是唯一需要 fastapi 的地方。

W2：默认把请求提交到单进程有界队列 + 线程池（ConcurrentGateway），
SSE 帧生成在 gateway/sse.py（纯标准库），本文件只做 StreamingResponse 包装、
断线检测与 lifespan 生命周期管理。并发池不产出任何 off/on 对照数字。
"""

import asyncio
from contextlib import asynccontextmanager
import itertools

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .concurrency import ConcurrentGateway
from .config import GatewayConfig
from .core import ChatRequest, Decision
from .engine import GatewayEngine
from .sse import (
    DEFAULT_CHUNK_SIZE,
    SSE_HEADERS,
    cancelled_frame,
    completion_id as _completion_id,
    sse_frames,
)
from .upstream import Upstream


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


def create_app(
    config: GatewayConfig | None = None,
    engine: GatewayEngine | None = None,
    upstream: Upstream | None = None,
    concurrent: bool = True,
    workers: int = 4,
    queue_capacity: int = 64,
) -> FastAPI:
    cfg = config or GatewayConfig()
    eng = engine or GatewayEngine(cfg, upstream=upstream)
    pool = ConcurrentGateway(eng, workers=workers, queue_capacity=queue_capacity) if concurrent else None
    index_counter = itertools.count()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if app.state.pool is not None:
            app.state.pool.shutdown(wait=True)

    app = FastAPI(
        title="Agent Gateway (W2)",
        version="0.2.0",
        description="OpenAI 兼容子集 + 路由/缓存/限流/幂等 + 单进程并发队列；默认确定性 fake 上游",
        lifespan=lifespan,
    )
    app.state.config = cfg
    app.state.engine = eng
    app.state.pool = pool

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "config_hash": cfg.config_hash(), "concurrent": pool is not None}

    @app.get("/admin/config")
    async def admin_config() -> dict:
        return {"config_hash": cfg.config_hash(), "config": cfg.snapshot()}

    @app.get("/metrics")
    async def metrics() -> dict:
        data = eng.metrics()
        if pool is not None:
            data["concurrency"] = pool.stats()
        return data

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
            index=next(index_counter),
        )
        if pool is not None:
            decision = await asyncio.wrap_future(pool.submit(req))
        else:
            decision = eng.handle(req)

        if decision.status != 200 and not req.stream:
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
            async def stream():
                for frame in sse_frames(decision, cfg, chunk_size=DEFAULT_CHUNK_SIZE):
                    if await request.is_disconnected():
                        yield cancelled_frame(decision, cfg)
                        return
                    yield frame
            return StreamingResponse(
                stream(), media_type="text/event-stream", headers=SSE_HEADERS
            )
        return JSONResponse(_openai_shape(decision, cfg))

    return app


app = create_app()
