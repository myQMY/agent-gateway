from __future__ import annotations

"""W4 两格执行编排：默认 dry-run，只有 --execute 才产生真实 API 成本。

设计依据：docs/design-w4-tooling.md
- 默认不启动服务、不建目录、不调用 subprocess、不发起网络请求；
- --execute 的 preflight 全部是本地文件检查 + 一次本地 HTTP GET，零模型调用；
- 产物全部写到 w4/out/，P1 仓库零改动；不 import P1 任何模块；
- 自动起停 w4.serve:app，并在跑 on 格前用 /w4/upstream 拦住「起成 fake 上游」。

用法：
    python -m w4.run                      # 预览两格命令，零成本
    python -m w4.run --execute            # 真跑两格，自动起停网关
    python -m w4.run --cell off --execute # 只跑直连格（不需要网关）
    python -m w4.run --serve-only         # 只起网关，供一次 curl 自检
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
W4 = ROOT / "w4"
OUT = W4 / "out"
DEFAULT_P1_ROOT = ROOT.parent / "项目1"
GATEWAY = "http://127.0.0.1:8000"

CELLS = {
    "on": ("gw_on.toml", "on.env"),
    "off": ("gw_off.toml", "off.env"),
}
REQUIRED_ENV_KEYS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")
PLACEHOLDER_PREFIX = "REPLACE_ME"


def read_env(path: Path) -> dict[str, str]:
    """极简 dotenv 读取；只读文件内容，不把密钥打印出来。"""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def build_command(cell: str, max_calls: int) -> list[str]:
    """拼出 P1 的 eval.run 命令；刻意不传 --report（否则不写 run_dir 三件留档）。"""
    toml, env = CELLS[cell]
    return [
        sys.executable, "-m", "eval.run",
        "--config", str(W4 / toml),
        "--env-file", str(W4 / env),
        "--max-calls", str(max_calls),
        "--runs-dir", str(OUT / f"runs-{cell}"),
        "--results-dir", str(OUT / f"results-{cell}"),
        "--traces-dir", str(OUT / f"traces-{cell}"),
    ]


def preflight(cells: tuple[str, ...], p1_root: Path) -> list[str]:
    """本地检查，零网络请求。返回人类可读的问题清单。"""
    problems: list[str] = []
    if not (p1_root / "eval" / "run.py").is_file():
        problems.append(f"找不到 P1 的 eval/run.py：{p1_root}（用 --p1-root 指定）")
    if not (p1_root / "tasks" / "mvp_10.jsonl").is_file():
        problems.append(f"找不到 P1 任务集：{p1_root / 'tasks' / 'mvp_10.jsonl'}")

    for cell in cells:
        toml, env = CELLS[cell]
        if not (W4 / toml).is_file():
            problems.append(f"缺少配置模板 {W4 / toml}")
        env_path = W4 / env
        if not env_path.is_file():
            problems.append(f"缺少 {env_path.name}：请从 {env}.example 复制后填写")
            continue
        values = read_env(env_path)
        for key in REQUIRED_ENV_KEYS:
            if not values.get(key):
                problems.append(f"{env_path.name} 缺少 {key}")

    off = read_env(W4 / "off.env")
    off_key = off.get("LLM_API_KEY", "")
    if off_key and off_key.startswith(PLACEHOLDER_PREFIX):
        problems.append("off.env 的 LLM_API_KEY 仍是占位符，直连格会鉴权失败")
    if off.get("LLM_BASE_URL", "").rstrip("/").endswith("/v1"):
        problems.append("off.env 的 LLM_BASE_URL 不应带 /v1（DeepSeek 直连路径）")

    on = read_env(W4 / "on.env")
    on_base = on.get("LLM_BASE_URL", "")
    if on_base and not on_base.rstrip("/").endswith("/v1"):
        problems.append(
            "on.env 的 LLM_BASE_URL 必须带 /v1：P1 自拼 /chat/completions，"
            "网关只注册 /v1/chat/completions"
        )
    return problems


def start_gateway() -> subprocess.Popen:
    """起 W4 网关；密钥唯一来源是 w4/off.env，不要求用户另设环境变量。

    旧版要用户既在 off.env 填 key、又在终端 export 一次，两处不一致时
    /w4/upstream 只验「非空」验不出来，上游直接 401、网关映射成 502。
    这里只覆盖子进程 env，绝不打印、不落盘、不写进任何产物。
    """
    env = os.environ.copy()
    off_key = read_env(W4 / "off.env").get("LLM_API_KEY", "").strip()
    if off_key:
        env["LLM_API_KEY"] = off_key
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "w4.serve:app",
         "--port", "8000", "--log-level", "warning"],
        cwd=str(ROOT),
        env=env,
    )


def wait_gateway(proc: subprocess.Popen, timeout_s: float = 20.0) -> dict:
    """零成本自检：确认是真实上游入口且密钥已注入，不是 gateway.app:app 的 fake。"""
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"网关进程提前退出（code={proc.returncode}）。"
                "若报 No module named uvicorn，请用装了 fastapi/uvicorn 的隔离 venv 运行本脚本。"
            )
        try:
            with urlopen(GATEWAY + "/w4/upstream", timeout=1.0) as response:
                info = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 404:
                raise RuntimeError(
                    "8000 端口上没有 /w4/upstream：起的多半是 gateway.app:app（fake 上游）。"
                    "请改用 w4.serve:app，否则 P1 只会拿到 fake: 文本。"
                ) from exc
            last = f"HTTP {exc.code}"
        except (URLError, TimeoutError, ValueError) as exc:
            last = str(exc)
        else:
            if info.get("kind") != "real":
                raise RuntimeError(f"上游类型不是 real：{info.get('kind')!r}")
            if not info.get("api_key_present"):
                raise RuntimeError(
                    "网关进程没读到 LLM_API_KEY；请检查 w4/off.env 是否填了真实密钥"
                    "（--execute 会自动把它注入网关进程，无需另设环境变量）。"
                )
            return info
        time.sleep(0.2)
    raise RuntimeError(f"网关在 {timeout_s:.0f}s 内未就绪：{last}")


def stop_gateway(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def print_gateway_diagnosis() -> None:
    """cell 失败后零成本取回上游失败分类；不发模型请求、不打印密钥。

    502 是 GatewayEngine 把「上游全部失败」映射出来的状态码，P1 的 traceback
    只有 "HTTP Error 502"，看不到真实原因。w4/serve.py 把每次 complete() 失败的
    error_kind 记在内存里，这里读出来，省得用户另开终端手动复现一次付费请求。
    """
    try:
        with urlopen(GATEWAY + "/w4/last-error", timeout=2.0) as response:
            info = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        print(f"  （取网关诊断失败：{exc}；可另开终端查 GET /metrics）")
        return
    errors = info.get("errors") or []
    if not errors:
        print("  网关未记录上游失败；可能是限流(429)或请求解析层，查 GET /metrics")
        return
    print("  网关记录的上游失败分类（零成本，不含密钥与消息内容）：")
    for item in errors[-6:]:
        print(f"    - {item.get('target')}: {item.get('error_kind')} "
              f"(attempt {item.get('attempt')})")


def run_cell(cell: str, p1_root: Path, max_calls: int) -> None:
    cmd = build_command(cell, max_calls)
    print(f"\n=== cell {cell} ===")
    print(shlex.join(cmd), flush=True)
    completed = subprocess.run(cmd, cwd=str(p1_root), check=False)
    if completed.returncode != 0:
        if cell == "on":
            print_gateway_diagnosis()
        raise RuntimeError(f"cell {cell} 退出码 {completed.returncode}，后续格已停止")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m w4.run",
        description="W4 两格执行编排；默认 dry-run，只有 --execute 才产生真实 API 成本",
    )
    parser.add_argument("--execute", action="store_true", help="真正执行（会产生 API 成本）")
    parser.add_argument("--cell", choices=("on", "off", "both"), default="both")
    parser.add_argument("--max-calls", type=int, default=720,
                        help="单次 eval.run 的预算闸门上限；两格合计上限 = 2 × 该值")
    parser.add_argument("--p1-root", type=Path, default=DEFAULT_P1_ROOT)
    parser.add_argument("--serve-only", action="store_true",
                        help="只起网关并保持，供一次 curl 连通性自检；Ctrl+C 结束")
    args = parser.parse_args(argv)

    cells: tuple[str, ...] = ("off", "on") if args.cell == "both" else (args.cell,)
    problems = preflight(cells, args.p1_root)

    if not args.execute and not args.serve_only:
        print("DRY RUN：不启动服务、不发起任何 API 调用、不创建输出目录。\n")
        for cell in cells:
            print(f"=== cell {cell} ===")
            print(shlex.join(build_command(cell, args.max_calls)))
        print("\npreflight（本地文件检查）：")
        print("\n".join(f"  - {p}" for p in problems) if problems else "  - 全部通过")
        print("\n确认无误后加 --execute 才会真正产生成本。")
        return 0

    if problems:
        print("preflight 未通过，已中止（未产生任何 API 调用）：")
        for p in problems:
            print(f"  - {p}")
        return 2

    if args.serve_only:
        print("仅启动网关。另开终端做一次连通性自检（会产生一次真实调用）：")
        url = f"{GATEWAY}/v1/chat/completions"
        body = ('{"model":"deepseek-flash","messages":'
                '[{"role":"user","content":"reply OK"}]}')
        print("  PowerShell（5.1 里 curl 是 Invoke-WebRequest 的别名，别直接写 curl）：")
        print(f'    Invoke-RestMethod -Uri {url} -Method Post '
              f'-ContentType "application/json" -Body \'{body}\'')
        print("  bash：")
        print(f'    curl -s {url} -H "Content-Type: application/json" -d \'{body}\'')
        print("Ctrl+C 结束。")

    print(
        f"\n即将产生真实 API 成本：{len(cells)} 格 × --max-calls {args.max_calls}，"
        f"合计调用上限 {len(cells) * args.max_calls} 次；实际以服务商账单为准。"
    )
    print("P1 判分器不动、不产 pass^k 正向结论；两格数字不相减、不拼曲线、不宣称省钱。\n")

    need_gateway = "on" in cells or args.serve_only
    proc: subprocess.Popen | None = None
    try:
        if need_gateway:
            proc = start_gateway()
            info = wait_gateway(proc)
            print(f"网关自检通过：{info['kind']} @ {info['base_url']}{info['path']}，密钥已注入")
        if args.serve_only:
            proc.wait()
            return 0
        for cell in cells:
            for sub in (f"runs-{cell}", f"results-{cell}", f"traces-{cell}"):
                (OUT / sub).mkdir(parents=True, exist_ok=True)
            run_cell(cell, args.p1_root, args.max_calls)
        print(f"\n{len(cells)} 格完成（{', '.join(cells)}）。产物在 {OUT}。")
        if len(cells) == 2:
            print("请核对 runs-on / runs-off 下两个不同的 run_id。")
        print("提醒：删除 w4/off.env（含真实密钥）；检查 P1 工作区是否仍干净。")
        return 0
    except (RuntimeError, KeyboardInterrupt) as exc:
        print(f"\n已中止：{exc}")
        return 1
    finally:
        stop_gateway(proc)


if __name__ == "__main__":
    raise SystemExit(main())
