import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from gateway.config import GatewayConfig
from gateway.replay import build_log, harness_log, load_harness_tasks, run_pair

P1_TASKS = Path("D:/文档/课题/agent/项目1/tasks/mvp_10.jsonl")

SAMPLE = [
    {
        "id": "T01",
        "user_utterances": ["我要退 O-100 这笔订单"],
        "script": [
            {"tool": "get_order", "args": {"order_id": "O-100"}},
            {"tool": "refund_order", "args": {"order_id": "O-100"}},
            {"tool": "notify_customer", "args": {}},
        ],
        "policy_checks": ["no_over_refund"],
    },
    {
        "id": "T02",
        "user_utterances": ["查一下我的订单"],
        "script": [{"tool": "list_customer_orders", "args": {}}],
        "policy_checks": [],
    },
]


def _snapshot(directory: Path) -> dict:
    return {
        p.name: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
        for p in sorted(directory.iterdir()) if p.is_file()
    }


class TestHarnessLoad(unittest.TestCase):
    def test_load_tasks_from_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tasks.jsonl"
            path.write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in SAMPLE) + "\n",
                            encoding="utf-8", newline="\n")
            loaded = load_harness_tasks(path)
        self.assertEqual([t["id"] for t in loaded], ["T01", "T02"])

    def test_harness_log_expands_script_steps(self):
        cfg = GatewayConfig(seed=11, n_requests=20, repeat_rate=0.0)
        contents = []
        for task in SAMPLE:
            for step_i, step in enumerate(task["script"]):
                contents.append(f"{task['id']}|{task['user_utterances'][0]}|step{step_i}|{step['tool']}")
        self.assertEqual(len(contents), 4)
        log = harness_log(SAMPLE, cfg)
        self.assertEqual(len(log), 20)
        self.assertIn("T01", log[0].messages[0][1])
        self.assertIn("get_order", log[0].messages[0][1])

    def test_harness_log_is_deterministic(self):
        cfg = GatewayConfig(seed=5, n_requests=30, repeat_rate=0.3)
        self.assertEqual(
            [r.to_dict() for r in harness_log(SAMPLE, cfg)],
            [r.to_dict() for r in harness_log(SAMPLE, cfg)],
        )

    def test_build_log_is_deterministic_for_same_contents(self):
        cfg = GatewayConfig(seed=3, n_requests=25, repeat_rate=0.4)
        a = build_log(["a", "b", "c"], cfg)
        b = build_log(["a", "b", "c"], cfg)
        self.assertEqual([r.to_dict() for r in a], [r.to_dict() for r in b])

    def test_different_load_changes_upstream_distribution(self):
        """换负载形态不能只是换名字：token / 成本 / 延迟分布必须真的不同。"""
        from dataclasses import replace
        cfg = GatewayConfig(seed=11, n_requests=60, repeat_rate=0.2,
                            failure_rate=0.1, tasks_name="alt.jsonl")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "alt.jsonl"
            path.write_text(
                "\n".join(json.dumps(t, ensure_ascii=False) for t in SAMPLE) + "\n",
                encoding="utf-8", newline="\n",
            )
            harness = run_pair(cfg, path)
        synthetic = run_pair(replace(cfg, tasks_name=""))
        self.assertNotEqual(
            harness["off"]["summary"]["total_cost_usd"],
            synthetic["off"]["summary"]["total_cost_usd"],
        )
        self.assertNotEqual(
            harness["on"]["summary"]["mean_ms"],
            synthetic["on"]["summary"]["mean_ms"],
        )


class TestP1ReadOnlyContract(unittest.TestCase):
    @unittest.skipUnless(P1_TASKS.is_file(), "P1 tasks file not present on this machine")
    def test_real_p1_tasks_fields_match_bridge_expectation(self):
        tasks = load_harness_tasks(P1_TASKS)
        self.assertGreaterEqual(len(tasks), 10)
        for key in ("id", "user_utterances", "script"):
            self.assertIn(key, tasks[0])

    @unittest.skipUnless(P1_TASKS.is_file(), "P1 tasks file not present on this machine")
    def test_bridge_never_writes_to_p1_directory(self):
        before = _snapshot(P1_TASKS.parent)
        cfg = GatewayConfig(seed=11, n_requests=60, repeat_rate=0.3,
                            tasks_name=P1_TASKS.name)
        result = run_pair(cfg, P1_TASKS)
        after = _snapshot(P1_TASKS.parent)
        self.assertEqual(before, after)
        self.assertTrue(result["source"].startswith("harness:"))
        self.assertEqual(result["source"], "harness:mvp_10.jsonl")

    @unittest.skipUnless(P1_TASKS.is_file(), "P1 tasks file not present on this machine")
    def test_archive_number_uses_basename_only(self):
        cfg = GatewayConfig(seed=11, n_requests=30, tasks_name=P1_TASKS.name)
        self.assertNotIn(P1_TASKS.parent.as_posix(), cfg.config_hash())
        self.assertEqual(cfg.tasks_name, "mvp_10.jsonl")


if __name__ == "__main__":
    unittest.main()
