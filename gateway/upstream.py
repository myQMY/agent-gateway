from __future__ import annotations

"""确定性 fake 上游：零 API 成本、零网络、逐字节可复现。

所有随机性都由 SHA256(seed | 请求内容 hash | index | attempt | target | tag) 派生，
不使用 random、不读时钟。latency_ms 是模拟值，不是真实网络延迟。

请求内容参与派生是刻意的：换一份负载（合成日志 vs P1 任务集）必须产生
不同的 token / 延迟 / 失败分布，否则「挂真实负载」只是换个名字的同一组数字。
"""

import hashlib

from .config import GatewayConfig
from .core import ChatRequest, UpstreamReply

ERROR_KINDS = ("timeout", "upstream_5xx", "upstream_rate_limit")


def _derive(seed: int | str, index: int, attempt: int, target: str, tag: str) -> int:
    raw = f"{seed}|{index}|{attempt}|{target}|{tag}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


class FakeUpstream:
    def __init__(self, config: GatewayConfig):
        self.config = config

    def complete(
        self, request: ChatRequest, attempt: int = 0, target: str | None = None
    ) -> UpstreamReply:
        target = target or request.model
        seed = f"{self.config.seed}:{request.cache_key()[:12]}"
        idx = request.index

        # 主上游按 failure_rate 失败；降级上游失败率降为 1/4，且独立派生。
        rate = self.config.failure_rate if attempt == 0 else self.config.failure_rate / 4
        roll = _derive(seed, idx, attempt, target, "fail") % 10000 / 10000.0
        failed = roll < rate

        in_tokens = 24 + _derive(seed, idx, attempt, target, "tin") % 40
        out_tokens = 32 + _derive(seed, idx, attempt, target, "tout") % 64
        base = 60 if attempt > 0 else 40
        span = 200 if attempt > 0 else 160
        latency = float(base + _derive(seed, idx, attempt, target, "lat") % span)

        if failed:
            kind = ERROR_KINDS[_derive(seed, idx, attempt, target, "kind") % len(ERROR_KINDS)]
            return UpstreamReply(
                text="", in_tokens=in_tokens, out_tokens=0, ok=False,
                error_kind=kind, target=target, latency_ms=latency,
            )

        text = (
            f"fake:{target}:{idx}:{attempt}:"
            f"{_derive(seed, idx, attempt, target, 'text') % 1000000}"
        )
        return UpstreamReply(
            text=text, in_tokens=in_tokens, out_tokens=out_tokens, ok=True,
            target=target, latency_ms=latency,
        )
