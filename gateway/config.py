from __future__ import annotations

"""配置即自变量：同配置 -> 同 config_hash -> 同回放结果。

刻意不放绝对路径。tasks_path 由 CLI 单独传入，只有 basename 参与 hash，
避免本机路径混入归档编号（这是 P1 审计里记录过的一个真实隐患）。
"""

from dataclasses import asdict, dataclass
import hashlib
import json

PRICE_TABLE: tuple[tuple[str, float, float], ...] = (
    ("gateway-primary", 0.15, 0.60),
    ("gateway-fallback", 0.30, 1.20),
)


@dataclass(frozen=True)
class GatewayConfig:
    seed: int = 11
    n_requests: int = 600
    repeat_rate: float = 0.35
    failure_rate: float = 0.12
    rpm_limit: int = 0
    window_requests: int = 100
    cache_capacity: int = 128
    cache_ttl_requests: int = 0
    gateway_enabled: bool = True
    models: tuple[str, ...] = ("gateway-primary",)
    fallback_models: tuple[str, ...] = ("gateway-fallback",)
    prices: tuple[tuple[str, float, float], ...] = PRICE_TABLE
    tasks_name: str = ""

    def price_for(self, model: str) -> tuple[float, float]:
        for name, pin, pout in self.prices:
            if name == model:
                return pin, pout
        return self.prices[0][1], self.prices[0][2]

    def config_hash(self) -> str:
        """归档编号：排除 gateway_enabled，因为它是两格对照的唯一自变量。"""
        payload = asdict(self)
        payload.pop("gateway_enabled", None)
        raw = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]

    def snapshot(self) -> dict:
        return asdict(self)
