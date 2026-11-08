"""W3 行为测试：负载不变量、trace 只读与确定性、容器清单结构。

不引第三方包；裸标准库下即可全绿。
"""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from gateway.loadgen import (
    SCENARIOS,
    build_payload,
    main as loadgen_main,
    scenario_capacity_gate,
    scenario_rate_limit,
    scenario_single_flight,
)
from gateway.trace import (
    load_run,
    main as trace_main,
    render_html,
    summarize_group,
    write_view,
)

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "experiments/runs"
RUN_ID = "replay-aa1142308d8d"
ARCHIVE_FILES = ("report.md", "config.json", "decisions.jsonl")


def _quiet(call, *args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return call(*args)


class TestLoadgen(unittest.TestCase):
    def test_capacity_gate_is_deterministic(self):
        a = scenario_capacity_gate(6, 4)
        b = scenario_capacity_gate(6, 4)
        self.assertEqual(a, b)
        self.assertEqual(a["admitted"], 6)
        self.assertEqual(a["rejected"], 4)
        self.assertTrue(a["empty_after_release"])
        self.assertEqual(a["readmitted_after_release"], 6)
        self.assertTrue(a["empty_after_second_release"])

    def test_capacity_gate_with_no_overflow(self):
        s = scenario_capacity_gate(5, 0)
        self.assertEqual(s["admitted"], 5)
        self.assertEqual(s["rejected"], 0)

    def test_single_flight_invariants(self):
        s = scenario_single_flight(16, 4, 11)
        self.assertEqual(s["upstream_calls"], 1, "幂等表 + 精确缓存应挡住重复上游调用")
        self.assertEqual(s["decisions"], 16)
        self.assertEqual(s["ledger_spans"], 16)
        self.assertEqual(s["queue_rejected_note"], "scheduling-dependent; reported, never asserted")

    def test_single_flight_opens_a_real_concurrency_window(self):
        s = scenario_single_flight(16, 4, 11)
        self.assertGreater(s["single_flight_waited_observed"], 0,
                           "BlockingUpstream 应让等待分支真的触发")
        self.assertEqual(s["single_flight_waited_note"],
                         "scheduling-dependent; reported, never asserted")

    def test_rate_limit_invariants(self):
        s = scenario_rate_limit(16, 4, 4, 11)
        self.assertEqual(s["status_200"], 4)
        self.assertEqual(s["status_429"], 12)
        self.assertEqual(s["ledger_spans"], 16)

    def test_full_payload_all_checks_pass(self):
        p = build_payload(12, 4, 3, 5, 3, 11, SCENARIOS)
        self.assertTrue(p["all_pass"])
        self.assertEqual(len(p["checks"]), 7)
        self.assertTrue(all(c["pass"] for c in p["checks"]))

    def test_scheduling_dependent_counts_are_never_asserted(self):
        p = build_payload(12, 4, 3, 5, 3, 11, SCENARIOS)
        names = [c["name"] for c in p["checks"]]
        self.assertFalse(any("queue_rejected" in n or "waited" in n for n in names))
        self.assertIn("single_flight_waited_observed", json.dumps(p))
        self.assertIn("queue_rejected_observed", json.dumps(p))

    def test_json_out_writes_only_the_requested_file(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "loadgen.json"
            before = {n: (RUNS / RUN_ID / n).read_bytes() for n in ARCHIVE_FILES}
            rc = _quiet(loadgen_main, ["--n", "8", "--capacity", "4", "--extra", "2",
                                       "--json-out", str(out)])
            self.assertEqual(rc, 0)
            self.assertTrue(out.is_file())
            self.assertTrue(json.loads(out.read_text(encoding="utf-8"))["all_pass"])
            self.assertEqual({n: (RUNS / RUN_ID / n).read_bytes() for n in ARCHIVE_FILES}, before)

    def test_unknown_scenario_is_rejected(self):
        with self.assertRaises(SystemExit):
            loadgen_main(["--scenarios", "nope"])

    def test_invalid_parameters_are_rejected(self):
        for argv in (["--n", "0"], ["--rpm-limit", "999"], ["--capacity", "0"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                loadgen_main(argv)


class TestTrace(unittest.TestCase):
    def test_load_run_reads_full_archive(self):
        data = load_run(RUN_ID, RUNS)
        self.assertEqual(len(data["rows"]), 1200)
        self.assertEqual(data["config"]["seed"], 11)
        self.assertEqual({r["group"] for r in data["rows"]}, {"off", "on"})

    def test_load_run_rejects_path_traversal(self):
        for bad in ("../etc", "sub/dir", "", ".hidden"):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                load_run(bad, RUNS)

    def test_summary_is_recomputed_from_all_rows(self):
        data = load_run(RUN_ID, RUNS)
        off = summarize_group([r for r in data["rows"] if r["group"] == "off"])
        on = summarize_group([r for r in data["rows"] if r["group"] == "on"])
        for s in (off, on):
            self.assertEqual(s["requests"], 600)
            self.assertEqual(s["status_200"] + s["status_429"] + s["status_502"], 600)
        self.assertEqual(off["cache_hit"], 0, "off 格直通，不应有缓存命中")
        self.assertGreater(on["cache_hit"], 0)
        self.assertIn("p95_simulated_ms", on)

    def test_render_is_deterministic(self):
        data = load_run(RUN_ID, RUNS)
        self.assertEqual(render_html(data, 12), render_html(data, 12))
        self.assertNotEqual(render_html(data, 12), render_html(data, 13))

    def test_rows_zero_renders_full_timeline(self):
        data = load_run(RUN_ID, RUNS)
        full = render_html(data, 0)
        part = render_html(data, 5)
        self.assertGreater(len(full), len(part))
        self.assertIn("已展示全量", full)
        self.assertIn("展示前 5 条", part)

    def test_render_labels_simulated_latency(self):
        html = render_html(load_run(RUN_ID, RUNS), 4)
        self.assertIn("<strong>模拟</strong>", html)
        self.assertIn("SHA256 派生的模拟延迟", html)
        self.assertIn("不是新实验", html)

    def test_write_view_is_read_only(self):
        run_dir = RUNS / RUN_ID
        before = {n: (run_dir / n).read_bytes() for n in ARCHIVE_FILES}
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "view.html"
            rendered = write_view(load_run(RUN_ID, RUNS), out, 8)
            self.assertEqual(out.read_text(encoding="utf-8"), rendered)
        self.assertEqual({n: (run_dir / n).read_bytes() for n in ARCHIVE_FILES}, before,
                         "生成视图不得改动归档三件任何一个字节")

    def test_write_view_refuses_archive_directory(self):
        with self.assertRaises(SystemExit):
            write_view(load_run(RUN_ID, RUNS), RUNS / "forbidden.html")

    def test_check_mode_passes_on_committed_view(self):
        rc = _quiet(trace_main, [
            "--run", RUN_ID, "--runs-root", str(RUNS),
            "--out", str(ROOT / "experiments/views" / f"{RUN_ID}.html"), "--check",
        ])
        self.assertEqual(rc, 0)

    def test_check_mode_detects_stale_view(self):
        with tempfile.TemporaryDirectory() as td:
            stale = Path(td) / "stale.html"
            stale.write_text("stale", encoding="utf-8")
            rc = _quiet(trace_main, [
                "--run", RUN_ID, "--runs-root", str(RUNS),
                "--out", str(stale), "--check",
            ])
            self.assertEqual(rc, 1)


class TestContainerManifest(unittest.TestCase):
    def test_all_manifest_files_exist(self):
        for rel in ("Dockerfile", "docker-compose.yml", ".dockerignore",
                    "docs/design-w3.md", f"experiments/views/{RUN_ID}.html"):
            self.assertTrue((ROOT / rel).is_file(), rel)

    def test_verify_command_matches_local_reproduction_path(self):
        c = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("services:", c)
        for cmd in ("python -m unittest", "python -m gateway.bench",
                    "python -m gateway.loadgen", "python -m gateway.trace"):
            self.assertIn(cmd, c, cmd)

    def test_dockerfile_copies_everything_verify_needs(self):
        d = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        for path in ("gateway", "bridge", "tests", "experiments", "docs", "requirements.txt"):
            self.assertIn(path, d)


if __name__ == "__main__":
    unittest.main()
