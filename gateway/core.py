from __future__ import annotations

"""核心数据结构。所有字段可 JSON 序列化，归档逐字节可复现。"""

from dataclasses import dataclass
import hashlib
import json


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: tuple[tuple[str, str], ...]
    temperature: float = 0.0
    stream: bool = False
    idempotency_key: str = ""
    caller: str = "default"
    index: int = 0

    def normalized(self) -> dict:
        return {
            "model": self.model,
            "messages": [{"role": r, "content": c} for r, c in self.messages],
            "temperature": self.temperature,
        }

    def cache_key(self) -> str:
        raw = json.dumps(
            self.normalized(), sort_keys=True, ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "messages": [{"role": r, "content": c} for r, c in self.messages],
            "temperature": self.temperature,
            "stream": self.stream,
            "idempotency_key": self.idempotency_key,
            "caller": self.caller,
            "index": self.index,
        }


@dataclass(frozen=True)
class UpstreamReply:
    text: str
    in_tokens: int
    out_tokens: int
    ok: bool
    error_kind: str = ""
    target: str = ""
    latency_ms: float = 0.0


@dataclass(frozen=True)
class Decision:
    status: int
    reply: UpstreamReply | None
    index: int
    caller: str
    model: str
    route: str
    cache_hit: bool = False
    idempotent_replay: bool = False
    rate_limited: bool = False
    fallback_used: bool = False
    attempts: int = 0
    cost_usd: float = 0.0
    simulated_latency_ms: float = 0.0
    reasons: tuple[str, ...] = ()

    def as_row(self) -> dict:
        """归档行。刻意不含 wall_ms：墙钟不可复现，不进报告。"""
        return {
            "index": self.index,
            "caller": self.caller,
            "model": self.model,
            "status": self.status,
            "route": self.route,
            "cache_hit": self.cache_hit,
            "idempotent_replay": self.idempotent_replay,
            "rate_limited": self.rate_limited,
            "fallback_used": self.fallback_used,
            "attempts": self.attempts,
            "cost_usd": round(self.cost_usd, 8),
            "simulated_latency_ms": round(self.simulated_latency_ms, 3),
            "reasons": list(self.reasons),
            "in_tokens": self.reply.in_tokens if self.reply else 0,
            "out_tokens": self.reply.out_tokens if self.reply else 0,
        }
