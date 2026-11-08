from __future__ import annotations

"""W2 可选真实供应商适配器（默认关闭、不进基准、零第三方包）。

R3：默认路径全部是确定性 fake upstream。本模块只有在调用方**显式注入**
RealUpstream，或显式设置环境变量开关时才会被使用；replay.py 不引用它，
所有归档数字仍来自 fake。

刻意不采集真实延迟：UpstreamReply.latency_ms 在 W1 里是 SHA256 派生的模拟值，
若把墙钟写进同一字段，就会与归档里的模拟延迟混用（R4）。真实延迟 / 真实账单
需要 W4 另开字段与口径，本轮不做。
"""

from dataclasses import dataclass
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .core import ChatRequest, UpstreamReply

ENV_SWITCH = "GATEWAY_REAL_UPSTREAM"
ENV_BASE_URL = "LLM_BASE_URL"
ENV_KEY_NAME = "GATEWAY_API_KEY_ENV"
ENV_TIMEOUT = "GATEWAY_UPSTREAM_TIMEOUT"
ENV_PATH = "GATEWAY_UPSTREAM_PATH"


@dataclass(frozen=True)
class RealUpstreamConfig:
    """与 GatewayConfig 完全独立：不进 config_hash、不进任何归档。"""

    base_url: str
    api_key_env: str = "LLM_API_KEY"
    timeout_s: float = 10.0
    path: str = "/chat/completions"

    @classmethod
    def from_env(cls) -> RealUpstreamConfig | None:
        """默认返回 None。只有显式打开开关且给出 base_url 才启用。"""
        if os.environ.get(ENV_SWITCH, "").strip().lower() not in {"1", "true", "yes"}:
            return None
        base = os.environ.get(ENV_BASE_URL, "").strip()
        if not base:
            return None
        try:
            timeout = float(os.environ.get(ENV_TIMEOUT, "10"))
        except ValueError:
            timeout = 10.0
        return cls(
            base_url=base.rstrip("/"),
            api_key_env=os.environ.get(ENV_KEY_NAME, "LLM_API_KEY").strip() or "LLM_API_KEY",
            timeout_s=timeout,
            path=os.environ.get(ENV_PATH, "/chat/completions").strip() or "/chat/completions",
        )


class RealUpstream:
    """OpenAI 兼容 chat/completions 的最小上游，错误分类复用既有 fallback 链路。"""

    def __init__(self, config: RealUpstreamConfig):
        self.config = config

    def _failed(self, kind: str, model: str) -> UpstreamReply:
        return UpstreamReply(
            text="", in_tokens=0, out_tokens=0, ok=False,
            error_kind=kind, target=model, latency_ms=0.0,
        )

    def complete(
        self, request: ChatRequest, attempt: int = 0, target: str | None = None
    ) -> UpstreamReply:
        model = target or request.model
        api_key = os.environ.get(self.config.api_key_env, "").strip()
        if not api_key:
            return self._failed("auth_error", model)

        payload = {
            "model": model,
            "messages": [
                {"role": role, "content": content} for role, content in request.messages
            ],
            "temperature": request.temperature,
            "stream": False,
        }
        url = self.config.base_url + self.config.path
        wire = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urlopen(wire, timeout=self.config.timeout_s) as response:
                raw = response.read()
            obj = json.loads(raw.decode("utf-8"))
            text = obj["choices"][0]["message"]["content"]
            usage = obj.get("usage") or {}
            tin = int(usage.get("prompt_tokens") or 0)
            tout = int(usage.get("completion_tokens") or 0)
        except HTTPError as exc:
            if exc.code in (401, 403):
                return self._failed("auth_error", model)
            if exc.code == 429:
                return self._failed("upstream_rate_limit", model)
            return self._failed("upstream_5xx", model)
        except (TimeoutError, URLError):
            return self._failed("timeout", model)
        except (ValueError, KeyError, TypeError, IndexError):
            return self._failed("invalid_response", model)

        return UpstreamReply(
            text=str(text), in_tokens=tin, out_tokens=tout, ok=True,
            target=model, latency_ms=0.0,
        )
