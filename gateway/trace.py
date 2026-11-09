from __future__ import annotations

"""W3 trace 回放视图：只读归档三件中的 config.json + decisions.jsonl，
确定性渲染一份自包含静态 HTML（内联 CSS、零 JS、零外链）。

- 不修改归档任何一个字节，不生成新指标，不新增结论；
- 不含时钟 / 随机 / 绝对路径 / 主机名，同归档 -> HTML 逐字节相同；
- 汇总基于全量归档行，时间线只展示前 --rows 条并在页面显式标注；
- p95_ms 一律带「模拟」标签（R4）。

详见 docs/design-w3.md §3。
"""

import argparse
import html
import json
from pathlib import Path
import sys

RUNS_ROOT = Path("experiments/runs")
VIEWS_ROOT = Path("experiments/views")
DEFAULT_TIMELINE_ROWS = 48


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _json(value) -> str:
    return _esc(json.dumps(value, ensure_ascii=False, sort_keys=True))


def load_run(run_id: str, runs_root: Path = RUNS_ROOT) -> dict:
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        raise SystemExit(f"invalid run id: {run_id!r}")
    run_dir = runs_root / run_id
    for name in ("config.json", "decisions.jsonl"):
        if not (run_dir / name).is_file():
            raise SystemExit(f"missing archive file: {run_dir / name}")
    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    rows = []
    for line in (run_dir / "decisions.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return {"run_id": run_id, "config": config, "rows": rows}


def summarize_group(rows: list[dict]) -> dict:
    """全部从归档行复算，不读取 report.md 里的现成数字。"""
    n = len(rows)
    latencies = [float(r["simulated_latency_ms"]) for r in rows]
    ordered = sorted(latencies)
    p95 = ordered[int(0.95 * (len(ordered) - 1))] if ordered else 0.0
    return {
        "requests": n,
        "status_200": sum(1 for r in rows if r["status"] == 200),
        "status_429": sum(1 for r in rows if r["status"] == 429),
        "status_502": sum(1 for r in rows if r["status"] == 502),
        "cache_hit": sum(1 for r in rows if r["cache_hit"]),
        "idempotent_replay": sum(1 for r in rows if r["idempotent_replay"]),
        "rate_limited": sum(1 for r in rows if r["rate_limited"]),
        "fallback_used": sum(1 for r in rows if r["fallback_used"]),
        "total_cost_usd": round(sum(float(r["cost_usd"]) for r in rows), 8),
        "p95_simulated_ms": round(p95, 3),
        "mean_simulated_ms": round(sum(latencies) / n, 3) if n else 0.0,
    }


SUMMARY_LABELS = (
    ("requests", "请求数"),
    ("status_200", "200 响应"),
    ("status_429", "429 限流"),
    ("status_502", "502 失败"),
    ("cache_hit", "缓存命中"),
    ("idempotent_replay", "幂等重放"),
    ("rate_limited", "限流拒绝"),
    ("fallback_used", "降级使用"),
    ("total_cost_usd", "总成本（USD）"),
    ("p95_simulated_ms", "p95（**模拟** ms）"),
    ("mean_simulated_ms", "均值（**模拟** ms）"),
)

CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; background: #f6f7f9; color: #1f2329;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
  font-size: 14px; line-height: 1.6; }
.wrap { max-width: 1120px; margin: 0 auto; padding: 28px 24px 64px; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 30px 0 12px; padding-bottom: 6px;
  border-bottom: 1px solid #e3e6eb; }
.sub { color: #5f6b7a; margin: 0 0 18px; }
.note { background: #fff8e6; border: 1px solid #f0dca6; border-left: 4px solid #d9a441;
  border-radius: 6px; padding: 12px 16px; margin: 16px 0 22px; }
.note ul { margin: 6px 0 0; padding-left: 20px; }
.card { background: #fff; border: 1px solid #e3e6eb; border-radius: 8px; padding: 16px; }
table { width: 100%; border-collapse: collapse; background: #fff; font-size: 13px; }
th, td { border: 1px solid #e6e9ee; padding: 7px 10px; text-align: left; vertical-align: top; }
th { background: #f0f2f5; font-weight: 600; color: #38414d; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }
.small { font-size: 12px; color: #5f6b7a; }
.tag { display: inline-block; border-radius: 4px; padding: 1px 6px; margin: 1px 3px 1px 0;
  font-size: 11px; border: 1px solid #d7dce3; background: #f3f5f8; color: #414b58; }
.tag.hit { background: #e8f4ec; border-color: #b6dcc3; color: #1d6b39; }
.tag.idem { background: #e9eefb; border-color: #bcccf0; color: #2a4d97; }
.tag.limit { background: #fbecea; border-color: #f0c3bd; color: #a3342a; }
.tag.fallback { background: #fdf0e2; border-color: #f0d2a9; color: #925c14; }
.st { font-weight: 600; }
.st.ok { color: #1d6b39; } .st.warn { color: #a3342a; }
.bar { background: #eceff3; border-radius: 3px; height: 9px; width: 100%; overflow: hidden; }
.bar > span { display: block; height: 100%; background: #5a7fc4; }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.kv td:first-child { width: 240px; color: #5f6b7a; }
footer { margin-top: 34px; color: #6b7684; font-size: 12px;
  border-top: 1px solid #e3e6eb; padding-top: 14px; }
code { background: #eef1f5; border-radius: 3px; padding: 1px 5px; }
"""


def _config_table(config: dict) -> str:
    lines = ['<table class="kv"><thead><tr><th>配置字段</th><th>值</th></tr></thead><tbody>']
    for key in sorted(config):
        lines.append(
            f"<tr><td class='mono'>{_esc(key)}</td>"
            f"<td class='mono'>{_json(config[key])}</td></tr>"
        )
    lines.append("</tbody></table>")
    return "\n".join(lines)


def _summary_table(groups: dict[str, dict]) -> str:
    off, on = groups["off"], groups["on"]
    lines = [
        "<table><thead><tr><th>指标（由归档行全量复算）</th>",
        "<th class='num'>off（直通）</th><th class='num'>on（网关）</th></tr></thead><tbody>",
    ]
    for key, label in SUMMARY_LABELS:
        label_html = label.replace("**模拟**", "<strong>模拟</strong>")
        lines.append(
            f"<tr><td>{label_html}</td>"
            f"<td class='num'>{off[key]}</td><td class='num'>{on[key]}</td></tr>"
        )
    lines.append("</tbody></table>")
    return "\n".join(lines)


def _bars(groups: dict[str, dict]) -> str:
    keys = ("cache_hit", "idempotent_replay", "rate_limited", "fallback_used")
    labels = {"cache_hit": "缓存命中", "idempotent_replay": "幂等重放",
              "rate_limited": "限流拒绝", "fallback_used": "降级使用"}
    out = ["<div class='grid'>"]
    for group in ("off", "on"):
        s = groups[group]
        n = s["requests"] or 1
        out.append(f"<div class='card'><strong>{group} 格 · 事件分布</strong>")
        for key in keys:
            pct = round(s[key] / n * 100, 2)
            width = f"{min(100.0, max(0.0, pct)):.2f}%"
            out.append(
                f"<div style='margin-top:10px'><span class='small'>{labels[key]}"
                f"　{s[key]} / {s['requests']}　{pct}%</span>"
                f"<div class='bar'><span style='width:{width}'></span></div></div>"
            )
        out.append("</div>")
    out.append("</div>")
    return "\n".join(out)


def _badges(row: dict) -> str:
    parts = []
    if row["cache_hit"]:
        parts.append("<span class='tag hit'>cache_hit</span>")
    if row["idempotent_replay"]:
        parts.append("<span class='tag idem'>idempotent_replay</span>")
    if row["rate_limited"]:
        parts.append("<span class='tag limit'>rate_limited</span>")
    if row["fallback_used"]:
        parts.append("<span class='tag fallback'>fallback_used</span>")
    if not parts:
        parts.append("<span class='small'>upstream</span>")
    return "".join(parts)


def _timeline(rows: list[dict], group: str, limit: int) -> str:
    subset = [r for r in rows if r["group"] == group]
    subset = sorted(subset, key=lambda r: r["index"])
    total = len(subset)
    shown = subset if limit == 0 else subset[:limit]
    out = [
        f"<h3>{group} 格 · 决策链路时间线</h3>",
        f"<p class='small'>统计基于全量 {total} 条归档行；"
        f"下方时间线展示前 {len(shown)} 条"
        + ("（<code>--rows 0</code> 可展示全量）。" if limit else "（已展示全量）。")
        + "</p>",
        "<table><thead><tr><th class='num'>index</th><th>caller</th><th>status</th>"
        "<th>route</th><th>决策链路</th><th class='num'>attempts</th>"
        "<th class='num'>cost_usd</th><th class='num'>模拟 ms</th><th>reasons</th>"
        "</tr></thead><tbody>",
    ]
    for r in shown:
        status = r["status"]
        cls = "ok" if status == 200 else "warn"
        reasons = ", ".join(r["reasons"]) or "-"
        out.append(
            f"<tr><td class='num mono'>{r['index']}</td>"
            f"<td class='mono'>{_esc(r['caller'])}</td>"
            f"<td><span class='st {cls}'>{status}</span></td>"
            f"<td class='mono'>{_esc(r['route'])}</td>"
            f"<td>{_badges(r)}</td>"
            f"<td class='num'>{r['attempts']}</td>"
            f"<td class='num mono'>{float(r['cost_usd']):.8f}</td>"
            f"<td class='num mono'>{float(r['simulated_latency_ms']):.3f}</td>"
            f"<td class='mono small'>{_esc(reasons)}</td></tr>"
        )
    out.append("</tbody></table>")
    return "\n".join(out)


def render_html(data: dict, timeline_rows: int = DEFAULT_TIMELINE_ROWS) -> str:
    run_id = data["run_id"]
    config = data["config"]
    rows = data["rows"]
    groups = {
        g: summarize_group([r for r in rows if r["group"] == g])
        for g in ("off", "on")
    }
    hash_part = run_id[len("replay-"):] if run_id.startswith("replay-") else run_id
    source = (
        "只读 P1 任务集回放（<code>tasks_name</code> 非空）" if config.get("tasks_name")
        else "合成日志（SHA256 派生）"
    )
    total_rows = len(rows)
    parts = [
        "<!doctype html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Trace 回放视图 · {_esc(run_id)}</title>",
        f"<style>{CSS}</style></head><body><div class='wrap'>",
        f"<h1>Trace 回放视图 · <span class='mono'>{_esc(run_id)}</span></h1>",
        f"<p class='sub'>config_hash <code>{_esc(hash_part)}</code>　"
        f"负载来源：{source}　归档行 {total_rows} 条</p>",
        "<div class='note'><strong>口径边界（必须连视图一起读）</strong><ul>",
        "<li>本页是<strong>已有归档的只读可视化</strong>，不是新实验；所有数字由 "
        "<code>decisions.jsonl</code> 全量复算，不新增任何指标或结论。</li>",
        "<li>默认上游是确定性 fake，<strong>零 API 成本、零网络调用</strong>；"
        "<code>p95</code> / 均值是 <strong>SHA256 派生的模拟延迟</strong>，不是网络延迟、不是生产 SLA。</li>",
        "<li>缓存命中率主要由 <code>repeat_rate</code> 这个自变量决定，不是优化成果；"
        "成本差只在「同一份请求流 + 单变量 <code>gateway_enabled</code>」内成立，不外推生产。</li>",
        "<li>与 P1 的关系是「只读任务集做回放负载」，<strong>不是运行时直连</strong>；"
        "真实链路 W4 两格<strong>已执行一次</strong>（未产收益结论），"
        "本页不产 <code>pass^k</code> 结论。</li>",
        "</ul></div>",
        "<h2>1 · 运行配置</h2>",
        _config_table(config),
        "<h2>2 · off / on 汇总</h2>",
        _summary_table(groups),
        "<h2>3 · 事件分布</h2>",
        _bars(groups),
        "<h2>4 · 决策链路时间线</h2>",
        _timeline(rows, "off", timeline_rows),
        _timeline(rows, "on", timeline_rows),
        "<footer>",
        "只读视图：不修改 <code>report.md</code> / <code>config.json</code> / "
        "<code>decisions.jsonl</code> 任何一个字节；同归档生成结果逐字节相同。<br>",
        f"复现：<code>python -m gateway.trace --run {_esc(run_id)} --rows "
        f"{timeline_rows} --check</code>　"
        "<code>python -m unittest discover -s tests -q</code><br>",
        "阶段 W3 · 设计边界见 <code>docs/design-w3.md</code>。",
        "</footer></div></body></html>",
    ]
    return "\n".join(parts) + "\n"


def default_view_path(run_id: str, views_root: Path = VIEWS_ROOT) -> Path:
    return views_root / f"{run_id}.html"


def write_view(data: dict, out: Path, timeline_rows: int = DEFAULT_TIMELINE_ROWS) -> str:
    norm = out.as_posix().replace("\\", "/")
    if "experiments/runs" in norm:
        raise SystemExit("refusing to write a view into experiments/runs (archives are read-only)")
    rendered = render_html(data, timeline_rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8", newline="\n")
    return rendered


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="W3 trace 回放视图（只读归档，确定性 HTML）")
    p.add_argument("--run", required=True, help="experiments/runs 下的归档目录名")
    p.add_argument("--runs-root", type=Path, default=RUNS_ROOT)
    p.add_argument("--out", type=Path, default=None,
                   help="输出路径，默认 experiments/views/<run>.html")
    p.add_argument("--rows", type=int, default=DEFAULT_TIMELINE_ROWS,
                   help="每格时间线展示条数，0 表示全量")
    p.add_argument("--check", action="store_true",
                   help="只校验确定性并与已提交视图逐字节比对，不写文件")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.rows < 0:
        raise SystemExit("--rows must be >= 0")
    data = load_run(args.run, args.runs_root)
    out = args.out or default_view_path(args.run)
    if args.check:
        first = render_html(data, args.rows)
        second = render_html(data, args.rows)
        deterministic = first == second
        exists = out.is_file()
        matches = (out.read_bytes() == first.encode("utf-8")) if exists else None
        ok = deterministic and (matches is not False)
        print(json.dumps({
            "run_id": args.run,
            "rows": args.rows,
            "deterministic": deterministic,
            "view_exists": exists,
            "matches_committed_view": matches,
            "bytes": len(first.encode("utf-8")),
            "all_pass": ok,
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if ok else 1
    rendered = write_view(data, out, args.rows)
    print(f"view = {out.as_posix()}  bytes = {len(rendered.encode('utf-8'))}  "
          f"rows = {args.rows}  (read-only replay of {len(data['rows'])} archive lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
