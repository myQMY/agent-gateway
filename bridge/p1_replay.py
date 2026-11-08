from __future__ import annotations

"""只读把 P1 任务集作为负载喂给网关回放（零 API 成本）。

不 import P1 代码、不写 P1 目录。路径由 --tasks 显式给出，避免硬编码本机目录。
"""

import argparse
from pathlib import Path

from gateway.config import GatewayConfig
from gateway.replay import run_pair, write_archive


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="用 P1 任务负载跑网关 off/on 对照")
    p.add_argument("--tasks", type=Path, required=True, help="P1 的 tasks/mvp_10.jsonl 路径（只读）")
    p.add_argument("--n", type=int, default=600)
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--repeat-rate", type=float, default=0.35)
    p.add_argument("--failure-rate", type=float, default=0.12)
    p.add_argument("--out", type=Path, default=Path("experiments/runs"))
    args = p.parse_args(argv)

    if not args.tasks.is_file():
        raise SystemExit(f"tasks file not found: {args.tasks}")

    config = GatewayConfig(
        seed=args.seed,
        n_requests=args.n,
        repeat_rate=args.repeat_rate,
        failure_rate=args.failure_rate,
        tasks_name=args.tasks.name,
    )
    result = run_pair(config, args.tasks)
    run_dir = write_archive(result, args.out)
    print(f"config_hash = {result['config_hash']}")
    print(f"source      = {result['source']}")
    print(f"off cost    = {result['off']['summary']['total_cost_usd']}")
    print(f"on  cost    = {result['on']['summary']['total_cost_usd']}")
    print(f"cost_saving = {result['cost_saving']:.4f}  (合成回放内相对量)")
    print(f"archive     = {run_dir.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
