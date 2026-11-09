from __future__ import annotations

"""W4 专用 uvicorn 入口：显式注入真实上游，默认 fake 路径保持不受影响。

设计依据：docs/design-w4-tooling.md §2.1 / §2.2

为什么必须单独开一个入口：gateway/app.py 末尾的 `app = create_app()` 从不传
upstream，RealUpstreamConfig.from_env() 的 GATEWAY_REAL_UPSTREAM 开关在生产路径
没有任何调用点。直接 `uvicorn gateway.app:app` 起服务只会返回 fake: 文本。
本文件不修改 gateway/app.py —— 一旦让模块级 app 自动读开关，本机任意一次
普通启动都可能静默变成真实计费路径，R3「默认全 fake、零 API 成本」就失守了。

启动（真实密钥只放在进程环境变量里，不落盘、不入库）：

    PowerShell:  $env:LLM_API_KEY="你的真实key"
    bash:        export LLM_API_KEY="你的真实key"
    uvicorn w4.serve:app --port 8000

正常执行请走 `python -m w4.run --execute`：它会从 w4/off.env 把同一个 key
注入本进程，不要求用户手工设置上面那行环境变量。

GET /w4/upstream 只返回上游类型与「密钥是否存在」的布尔值，不返回密钥值，
供 w4/run.py 在跑 on 格之前做零成本自检。
GET /w4/last-error 只返回上游失败分类（error_kind 枚举），供 502 事后诊断，
不含消息内容、不含密钥、不发起任何模型调用。
"""

import os

from gateway.app import create_app
from gateway.real_upstream import RealUpstream, RealUpstreamConfig

BASE_URL = os.environ.get("W4_UPSTREAM_BASE_URL", "https://api.deepseek.com").rstrip("/")

REAL_UPSTREAM = RealUpstream(
    RealUpstreamConfig(
        base_url=BASE_URL,
        api_key_env="LLM_API_KEY",
        timeout_s=30.0,
        path="/chat/completions",
    )
)


class RecordingUpstream:
    """代理真实上游，只把失败分类记在内存里，供 502 事后诊断。

    不记录 messages、不记录密钥、不记录响应正文；error_kind 只是
    auth_error / upstream_rate_limit / upstream_5xx / timeout / invalid_response
    这类枚举值。转发行为与 RealUpstream 完全一致，不改 gateway/ 任何代码。
    """

    def __init__(self, inner):
        self.inner = inner
        self.errors: list[dict] = []

    def complete(self, request, attempt=0, target=None):
        reply = self.inner.complete(request, attempt=attempt, target=target)
        if not reply.ok:
            self.errors.append({
                "target": target or request.model,
                "error_kind": reply.error_kind,
                "attempt": attempt,
            })
            if len(self.errors) > 50:
                del self.errors[:-50]
        return reply


UPSTREAM = RecordingUpstream(REAL_UPSTREAM)

# concurrent=False：W2 的并发路径与 W1 串行 off/on 归档互不相交，W4 是串行评测。
app = create_app(upstream=UPSTREAM, concurrent=False)


@app.get("/w4/upstream")
def w4_upstream() -> dict:
    """零成本自检端点：确认本次服务确实是真实上游且密钥已注入。"""
    return {
        "kind": "real",
        "base_url": REAL_UPSTREAM.config.base_url,
        "path": REAL_UPSTREAM.config.path,
        "api_key_env": REAL_UPSTREAM.config.api_key_env,
        "api_key_present": bool(os.environ.get(REAL_UPSTREAM.config.api_key_env, "").strip()),
    }


@app.get("/w4/last-error")
def w4_last_error() -> dict:
    """零成本诊断：最近的上游失败分类，不含任何密钥或消息内容。"""
    return {"count": len(UPSTREAM.errors), "errors": list(UPSTREAM.errors[-10:])}
