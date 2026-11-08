import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace

from gateway.config import GatewayConfig
from gateway.replay import (
    build_log, render_report, run_one, run_pair, synthetic_log, write_archive,
)


def small(**kw):
    base = dict(seed=11, n_requests=120, repeat_rate=0.35,
                failure_rate=0.12, gateway_enabled=True)
    base.update(kw)
    return GatewayConfig(**base)


class TestDeterminism(unittest.TestCase):
    def test_same_config_produces_identical_rows(self):
        a = run_pair(small())
        b = run_pair(small())
        self.assertEqual(a["config_hash"], b["config_hash"])
        self.assertEqual(a["off"]["rows"], b["off"]["rows"])
        self.assertEqual(a["on"]["rows"], b["on"]["rows"])

    def test_synthetic_log_is_byte_stable(self):
        cfg = small()
        a = [r.to_dict() for r in synthetic_log(cfg)]
        b = [r.to_dict() for r in synthetic_log(cfg)]
        self.assertEqual(a, b)

    def test_config_hash_excludes_the_switch(self):
        self.assertEqual(
            small(gateway_enabled=True).config_hash(),
            small(gateway_enabled=False).config_hash(),
        )

    def test_config_hash_changes_with_real_parameters(self):
        self.assertNotEqual(small().config_hash(), small(seed=12).config_hash())
        self.assertNotEqual(small().config_hash(), small(repeat_rate=0.5).config_hash())

    def test_request_content_changes_upstream_derivation(self):
        from gateway.upstream import FakeUpstream
        cfg = small()
        up = FakeUpstream(cfg)
        a = build_log(["content-A"], cfg)[0]
        b = build_log(["content-B"], cfg)[0]
        self.assertNotEqual(up.complete(a).text, up.complete(b).text)

    def test_no_absolute_path_in_config_snapshot(self):
        cfg = small(tasks_name=Path("D:/文档/课题/agent/项目1/tasks/mvp_10.jsonl").name)
        raw = json.dumps(cfg.snapshot(), ensure_ascii=False)
        self.assertNotIn("D:/", raw)
        self.assertNotIn("项目1/tasks", raw)
        self.assertIn("mvp_10.jsonl", raw)


class TestSingleVariableContrast(unittest.TestCase):
    def test_both_cells_share_one_log(self):
        cfg = small()
        log = synthetic_log(cfg)
        off_rows, _, _ = run_one(log, replace(cfg, gateway_enabled=False))
        on_rows, _, _ = run_one(log, replace(cfg, gateway_enabled=True))
        self.assertEqual([r["index"] for r in off_rows], [r["index"] for r in on_rows])
        self.assertEqual([r["caller"] for r in off_rows], [r["caller"] for r in on_rows])
        self.assertEqual([r["model"] for r in off_rows], [r["model"] for r in on_rows])

    def test_off_has_no_gateway_events(self):
        off = run_pair(small())["off"]["summary"]
        for key in ("cache_hit_rate", "idempotent_replays", "rate_limited", "fallback_triggered"):
            self.assertEqual(off[key], 0, key)

    def test_on_shows_all_four_decision_chains(self):
        on = run_pair(small(failure_rate=0.25, rpm_limit=5, window_requests=50))["on"]["summary"]
        self.assertGreater(on["cache_hit_rate"], 0)
        self.assertGreater(on["idempotent_replays"], 0)
        self.assertGreater(on["fallback_triggered"], 0)
        self.assertGreater(on["rate_limited"], 0)
        self.assertGreater(on["status_429"], 0)

    def test_cache_hit_rate_tracks_repeat_rate(self):
        low = run_pair(small(repeat_rate=0.05))["on"]["summary"]["cache_hit_rate"]
        high = run_pair(small(repeat_rate=0.60))["on"]["summary"]["cache_hit_rate"]
        self.assertGreater(high, low)

    def test_cost_saving_formula(self):
        r = run_pair(small())
        off = r["off"]["summary"]["total_cost_usd"]
        on = r["on"]["summary"]["total_cost_usd"]
        if off:
            self.assertAlmostEqual(r["cost_saving"], 1 - on / off, places=5)


class TestReportAndArchive(unittest.TestCase):
    def test_report_carries_boundaries(self):
        text = render_report(run_pair(small()))
        for phrase in ("模拟延迟", "自变量", "不外推真实流量", "零 API 成本", "逐字节可复现"):
            self.assertIn(phrase, text)
        self.assertNotIn("wall_ms", text)
        self.assertNotIn("生产 SLA 达成", text)

    def test_archive_has_three_files_and_all_rows(self):
        cfg = small(n_requests=50)
        result = run_pair(cfg)
        with tempfile.TemporaryDirectory() as td:
            run_dir = write_archive(result, Path(td))
            names = sorted(p.name for p in run_dir.iterdir() if p.is_file())
            self.assertEqual(names, ["config.json", "decisions.jsonl", "report.md"])
            lines = (run_dir / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 100)
            payloads = [json.loads(l) for l in lines]
            self.assertEqual({p["group"] for p in payloads}, {"off", "on"})
            self.assertTrue(all("wall_ms" not in p for p in payloads))
            self.assertEqual(
                (run_dir / "config.json").read_text(encoding="utf-8"),
                json.dumps(result["config"], sort_keys=True, ensure_ascii=False, indent=2) + "\n",
            )

    def test_wall_clock_never_enters_archive(self):
        result = run_pair(small(n_requests=20))
        with tempfile.TemporaryDirectory() as td:
            run_dir = write_archive(result, Path(td))
            blob = "".join(
                p.read_text(encoding="utf-8") for p in run_dir.iterdir() if p.is_file()
            )
            self.assertNotIn("_wall_ms", blob)
            self.assertNotIn("wall_ms", blob)


if __name__ == "__main__":
    unittest.main()
