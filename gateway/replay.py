from __future__ import annotations

"""零成本回放基准：合成日志 / P1 任务负载 -> 单变量 off-on 对照 -> 归档三件。

默认不接任何真实供应商。所有数字只在回放内成立，报告必须带边界。
"""

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path
import time
from typing import Iterable

from .config import GatewayConfig
from .core import ChatRequest
from .engine import GatewayEngine
from .upstream import FakeUpstream

CALLERS = ("harness-a", "harness-b", "harness-c")


def _derive(seed: int, index: int, tag: str) -> int:
    raw = f"{seed}|{index}|{tag}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def build_log(contents: list[str], config: GatewayConfig) -> list[ChatRequest]:
    """由内容单元构造请求流，重复位置与幂等键全部由 seed 派生。"""
    assert contents, "contents must not be empty"
    log: list[ChatRequest] = []
    pool: list[ChatRequest] = []
    for i in range(config.n_requests):
        d_rep = _derive(config.seed, i, "repeat")
        if pool and d_rep % 10000 / 10000.0 < config.repeat_rate:
            src = pool[_derive(config.seed, i, "which") % len(pool)]
            use_key = _derive(config.seed, i, "key") % 2 == 0
            log.append(
                replace(
                    src,
                    index=i,
                    caller=CALLERS[_derive(config.seed, i, "caller") % len(CALLERS)],
                    idempotency_key=src.idempotency_key if use_key else "",
                )
            )
            continue
        text = f"{contents[i % len(contents)]}#{i}"
        req = ChatRequest(
            model=config.models[_derive(config.seed, i, "model") % len(config.models)],
            messages=(("user", text),),
            temperature=0.0,
            idempotency_key=f"idem-{i}",
            caller=CALLERS[_derive(config.seed, i, "caller") % len(CALLERS)],
            index=i,
        )
        pool.append(req)
        log.append(req)
    return log


def synthetic_log(config: GatewayConfig) -> list[ChatRequest]:
    contents = [
        f"synthetic-task-{i % 40}:{_derive(config.seed, i, 'synthetic') % 100000}"
        for i in range(max(40, config.n_requests // 4))
    ]
    return build_log(contents, config)


def load_harness_tasks(path: Path) -> list[dict]:
    """只读 P1 任务集。不 import P1 代码、不写 P1 目录。"""
    tasks = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            tasks.append(json.loads(line))
    return tasks


def harness_log(tasks: Iterable[dict], config: GatewayConfig) -> list[ChatRequest]:
    """把 P1 的每条任务按 script 步展开成模型请求流，作为真实负载的形态。"""
    contents: list[str] = []
    for task in tasks:
        tid = str(task.get("id", "unknown"))
        utterances = task.get("user_utterances") or []
        lead = str(utterances[0]) if utterances else tid
        script = task.get("script") or []
        for step_i, step in enumerate(script):
            tool = step.get("tool", "") if isinstance(step, dict) else ""
            contents.append(f"{tid}|{lead}|step{step_i}|{tool}")
    if not contents:
        contents = ["empty-task"]
    return build_log(contents, config)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[int(q * (len(ordered) - 1))]


def summarize(rows: list[dict], engine: GatewayEngine) -> dict:
    n = len(rows)
    latencies = [r["simulated_latency_ms"] for r in rows]
    fallback_triggered = sum(1 for r in rows if r["attempts"] > 1)
    fallback_ok = sum(1 for r in rows if r["attempts"] > 1 and r["status"] == 200)
    return {
        "requests": n,
        "status_200": sum(1 for r in rows if r["status"] == 200),
        "status_429": sum(1 for r in rows if r["status"] == 429),
        "status_502": sum(1 for r in rows if r["status"] == 502),
        "cache_hit_rate": round(sum(1 for r in rows if r["cache_hit"]) / n, 6) if n else 0.0,
        "idempotent_replays": sum(1 for r in rows if r["idempotent_replay"]),
        "rate_limited": sum(1 for r in rows if r["rate_limited"]),
        "fallback_triggered": fallback_triggered,
        "fallback_success_rate": round(fallback_ok / fallback_triggered, 6) if fallback_triggered else 0.0,
        "p95_ms": round(_percentile(latencies, 0.95), 3),
        "mean_ms": round(sum(latencies) / n, 3) if n else 0.0,
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 8),
        "cache_size": engine.metrics()["cache_size"],
    }


def run_one(log: list[ChatRequest], config: GatewayConfig) -> tuple[list[dict], dict, float]:
    engine = GatewayEngine(config)
    started = time.perf_counter()
    rows = [engine.handle(req).as_row() for req in log]
    wall = time.perf_counter() - started
    return rows, summarize(rows, engine), wall


def run_pair(
    config: GatewayConfig, tasks_path: Path | None = None
) -> dict:
    if tasks_path is not None:
        log = harness_log(load_harness_tasks(tasks_path), config)
        source = f"harness:{tasks_path.name}"
    else:
        log = synthetic_log(config)
        source = "synthetic"
    off_rows, off_sum, off_wall = run_one(log, replace(config, gateway_enabled=False))
    on_rows, on_sum, on_wall = run_one(log, replace(config, gateway_enabled=True))
    cost_off = off_sum["total_cost_usd"]
    cost_on = on_sum["total_cost_usd"]
    return {
        "config_hash": config.config_hash(),
        "source": source,
        "config": config.snapshot(),
        "off": {"summary": off_sum, "rows": off_rows},
        "on": {"summary": on_sum, "rows": on_rows},
        "cost_saving": round(1 - cost_on / cost_off, 6) if cost_off else 0.0,
        # 墙钟只作诊断，绝不写进报告或归档行。
        "_wall_ms": {"off": round(off_wall * 1000, 3), "on": round(on_wall * 1000, 3)},
    }


def render_report(result: dict) -> str:
    cfg = result["config"]
    off = result["off"]["summary"]
    on = result["on"]["summary"]
    source_note = (
        "- 负载来自 P1 `tasks/*.jsonl` 的只读桥接（按 script 步展开），不 import P1 代码、不写 P1 目录。"
        if result["source"].startswith("harness:")
        else "- 负载为合成日志，由 SHA256 派生，与 P1 无耦合。"
    )
    lines = [
        f"# 回放基准报告 · {result['config_hash']}",
        "",
        f"- 负载来源：`{result['source']}`（只读 P1 任务集或合成日志，不调用真实 LLM）",
        source_note,
        f"- 请求数：{cfg['n_requests']}　seed：{cfg['seed']}　repeat_rate：{cfg['repeat_rate']}　failure_rate：{cfg['failure_rate']}",
        f"- 限流：rpm_limit={cfg['rpm_limit']} / window_requests={cfg['window_requests']}　缓存：capacity={cfg['cache_capacity']} / ttl_requests={cfg['cache_ttl_requests']}",
        "- **唯一自变量**：`gateway_enabled`（off 完全直通 / on 开启四能力）；两格共用同一份请求流、同一 seed、同一价格表、同一 fake 延迟表。",
        "",
        "## 结果",
        "",
        "| 指标 | off（直通） | on（网关） |",
        "| --- | ---: | ---: |",
    ]
    keys = [
        ("status_200", "200 响应数"), ("status_429", "429 限流数"),
        ("status_502", "502 失败数"), ("cache_hit_rate", "缓存命中率"),
        ("idempotent_replays", "幂等重放数"), ("rate_limited", "限流拒绝数"),
        ("fallback_triggered", "降级触发数"), ("fallback_success_rate", "降级成功率"),
        ("p95_ms", "p95（模拟 ms）"), ("mean_ms", "均值（模拟 ms）"),
        ("total_cost_usd", "总成本（USD）"),
    ]
    for key, label in keys:
        lines.append(f"| {label} | {off[key]} | {on[key]} |")
    lines += [
        f"| **相对成本差** | — | **{result['cost_saving']:.4f}** |",
        "",
        "## 口径边界（必须连结论一起讲）",
        "",
        "- 全部上游是确定性 fake，**零 API 成本、零网络调用**；`p95_ms` / `mean_ms` 是 **SHA256 派生的模拟延迟**，不是网络延迟、不是生产 SLA。",
        f"- 缓存命中率主要由 `repeat_rate={cfg['repeat_rate']}` 决定，它是写进配置的**自变量**，不是优化成果。",
        "- 单价表写死在 `GatewayConfig.prices`；成本差只反映「缓存命中 / 幂等重放不计费」与「降级使用更贵模型」两条机制。",
        "- 失败请求两格都不计费，规则一致；fake 失败按 `failure_rate` 注入，不代表供应商真实可用性。",
        "- 固定窗口以请求序号划分逻辑窗口，窗口边界效应如实存在；`rpm_limit=0` 时不限流。",
        "- 报告与归档不记录墙钟耗时（真实耗时只用于本地诊断，不写入任何归档字段）；归档行 `sort_keys` + UTF-8 + LF，同配置逐字节可复现。",
        "- **不外推真实流量、不宣称生产收益**；真实对照需 W4 挂 P1 运行时 + 真实供应商，本轮未做。",
        "",
        "## 复现",
        "",
        "```bash",
        f"python -m gateway.replay --n {cfg['n_requests']} --seed {cfg['seed']} "
        f"--repeat-rate {cfg['repeat_rate']} --failure-rate {cfg['failure_rate']} "
        f"--rpm-limit {cfg['rpm_limit']} --cache-capacity {cfg['cache_capacity']} "
        f"--cache-ttl {cfg['cache_ttl_requests']}",
        "```",
    ]
    return "\n".join(lines) + "\n"


def write_archive(result: dict, out_root: Path) -> Path:
    run_dir = out_root / f"replay-{result['config_hash']}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.md").write_text(render_report(result), encoding="utf-8", newline="\n")
    (run_dir / "config.json").write_text(
        json.dumps(result["config"], sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    with (run_dir / "decisions.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for group in ("off", "on"):
            for row in result[group]["rows"]:
                payload = {"group": group, **row}
                fh.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")
    return run_dir


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="P4 网关单变量回放基准（零 API 成本）")
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--n", type=int, default=600)
    p.add_argument("--repeat-rate", type=float, default=0.35)
    p.add_argument("--failure-rate", type=float, default=0.12)
    p.add_argument("--rpm-limit", type=int, default=0)
    p.add_argument("--window-requests", type=int, default=100)
    p.add_argument("--cache-capacity", type=int, default=128)
    p.add_argument("--cache-ttl", type=int, default=0)
    p.add_argument("--tasks", type=Path, default=None, help="只读 P1 的 tasks/*.jsonl；不指定则用合成日志")
    p.add_argument("--out", type=Path, default=Path("experiments/runs"))
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = GatewayConfig(
        seed=args.seed, n_requests=args.n, repeat_rate=args.repeat_rate,
        failure_rate=args.failure_rate, rpm_limit=args.rpm_limit,
        window_requests=args.window_requests, cache_capacity=args.cache_capacity,
        cache_ttl_requests=args.cache_ttl,
        tasks_name=args.tasks.name if args.tasks else "",
    )
    result = run_pair(config, args.tasks)
    run_dir = write_archive(result, args.out)
    off, on = result["off"]["summary"], result["on"]["summary"]
    print(f"config_hash = {result['config_hash']}  source = {result['source']}")
    print(f"off: 200={off['status_200']} cost={off['total_cost_usd']} p95={off['p95_ms']}")
    print(f"on : 200={on['status_200']} cost={on['total_cost_usd']} p95={on['p95_ms']} "
          f"cache_hit={on['cache_hit_rate']} idem={on['idempotent_replays']} "
          f"fallback={on['fallback_triggered']} limited={on['rate_limited']}")
    print(f"cost_saving = {result['cost_saving']:.4f}  (合成回放内相对量，非生产收益)")
    print(f"archive = {run_dir.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
