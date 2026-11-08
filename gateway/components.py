from __future__ import annotations

"""精确缓存、固定窗口限流、幂等键、路由与降级、成本台账。

W2：每个可变组件自带 RLock，只保护自身数据结构。加锁不改变串行路径的任何
行为，因此 W1 的 off/on 归档逐字节不变；上游调用发生在锁外，I/O 可真正并行。
"""

from collections import OrderedDict
from dataclasses import dataclass
import threading

from .config import GatewayConfig
from .core import ChatRequest, UpstreamReply
from .upstream import FakeUpstream, Upstream

CACHE_HIT_LATENCY_MS = 2.0


class ExactCache:
    """规范化请求 key 的精确缓存 + LRU + 逻辑 TTL。

    TTL 用请求序号作逻辑时钟（而非墙钟），保证回放可复现。
    cache_ttl_requests=0 表示不因 TTL 失效，仍受 capacity 约束。
    """

    def __init__(self, capacity: int, ttl_requests: int):
        self.capacity = max(1, capacity)
        self.ttl_requests = max(0, ttl_requests)
        self._data: OrderedDict[str, tuple[UpstreamReply, int]] = OrderedDict()
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.expired = 0

    def get(self, key: str, tick: int) -> UpstreamReply | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return None
            reply, born = item
            if self.ttl_requests and tick - born >= self.ttl_requests:
                del self._data[key]
                self.expired += 1
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return reply

    def put(self, key: str, reply: UpstreamReply, tick: int) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._data[key] = (reply, tick)
                return
            self._data[key] = (reply, tick)
            while len(self._data) > self.capacity:
                self._data.popitem(last=False)
                self.evictions += 1

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


class FixedWindowLimiter:
    """固定窗口计数。回放里以请求序号划分逻辑窗口，真实部署按秒。"""

    def __init__(self, limit: int, window_requests: int):
        self.limit = max(0, limit)
        self.window_requests = max(1, window_requests)
        self._counts: dict[tuple[str, int], int] = {}
        self._lock = threading.RLock()
        self.rejected = 0

    def allow(self, caller: str, tick: int) -> bool:
        with self._lock:
            if self.limit == 0:
                return True
            window = (tick - 1) // self.window_requests
            key = (caller, window)
            used = self._counts.get(key, 0)
            if used >= self.limit:
                self.rejected += 1
                return False
            self._counts[key] = used + 1
            return True


class IdempotencyStore:
    """同 key 重放首次结果，不重复调用上游、不重复计费。"""

    def __init__(self):
        self._data: dict[str, UpstreamReply] = {}
        self._lock = threading.RLock()
        self.blocked = 0

    def get(self, key: str) -> UpstreamReply | None:
        with self._lock:
            if not key:
                return None
            return self._data.get(key)

    def put(self, key: str, reply: UpstreamReply) -> None:
        with self._lock:
            if key:
                self._data[key] = reply

    def mark_blocked(self) -> None:
        with self._lock:
            self.blocked += 1


@dataclass(frozen=True)
class RouteResult:
    reply: UpstreamReply | None
    route: str
    fallback_used: bool
    attempts: int
    reasons: tuple[str, ...]


class Router:
    def __init__(self, config: GatewayConfig, upstream: Upstream | None = None):
        self.config = config
        self.upstream: Upstream = upstream or FakeUpstream(config)

    def targets(self, model: str) -> tuple[str, ...]:
        return (model,) + tuple(self.config.fallback_models)

    def call(self, request: ChatRequest) -> RouteResult:
        reasons: list[str] = []
        targets = self.targets(request.model)
        for attempt, target in enumerate(targets):
            reply = self.upstream.complete(request, attempt=attempt, target=target)
            if reply.ok:
                return RouteResult(
                    reply=reply,
                    route=target,
                    fallback_used=attempt > 0,
                    attempts=attempt + 1,
                    reasons=tuple(reasons),
                )
            reasons.append(f"{target}:{reply.error_kind}")
        return RouteResult(
            reply=None,
            route=targets[-1],
            fallback_used=True,
            attempts=len(targets),
            reasons=tuple(reasons + ["all_upstreams_failed"]),
        )


class Ledger:
    """成本台账：每次请求记一条可归档的 span。"""

    def __init__(self, config: GatewayConfig):
        self.config = config
        self.spans: list[dict] = []
        self._lock = threading.RLock()

    def cost(self, reply: UpstreamReply | None, target: str) -> float:
        if reply is None:
            return 0.0
        pin, pout = self.config.price_for(target)
        return reply.in_tokens / 1_000_000 * pin + reply.out_tokens / 1_000_000 * pout

    def record(self, row: dict) -> None:
        with self._lock:
            self.spans.append(row)

    def total_cost(self) -> float:
        with self._lock:
            return sum(s["cost_usd"] for s in self.spans)
