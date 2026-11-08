"""守门测试：把设计文档里的红线与仓库卫生钉死（口径与 P1 一致）。"""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent
CORE_MODULES = (
    "config.py", "core.py", "components.py", "engine.py", "upstream.py",
    "replay.py", "concurrency.py", "sse.py", "real_upstream.py", "bench.py",
)


def text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestDesignRedLines(unittest.TestCase):
    def test_design_declares_all_six_red_lines(self):
        d = text("docs/design.md")
        for tag in ("R1", "R2", "R3", "R4", "R5", "R6"):
            self.assertIn(tag, d)

    def test_design_states_no_p1_modification(self):
        d = text("docs/design.md")
        self.assertIn("不修改 P1 仓库任何文件", d)
        self.assertIn("判分器", d)
        self.assertIn("mvp_10/mvp_30.jsonl", d)

    def test_design_forbids_overclaiming(self):
        d = text("docs/design.md")
        for phrase in ("不是生产级网关", "确定性模拟值", "不许说的话", "未做"):
            self.assertIn(phrase, d)

    def test_design_marks_later_stages_as_not_done(self):
        d = text("docs/design.md")
        self.assertIn("本轮交付", d)
        self.assertGreaterEqual(d.count("未做"), 3)


class TestZeroDependencyCore(unittest.TestCase):
    def test_core_modules_never_import_fastapi(self):
        for name in CORE_MODULES:
            src = text(f"gateway/{name}")
            self.assertNotRegex(src, r"^\s*(from|import)\s+fastapi", name)
            self.assertNotRegex(src, r"^\s*(from|import)\s+uvicorn", name)

    def test_only_app_py_needs_third_party(self):
        for name in CORE_MODULES:
            src = text(f"gateway/{name}")
            imports = re.findall(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", src, re.M)
            external = [m.split(".")[0] for m in imports
                        if m.split(".")[0] not in {
                            "__future__", "argparse", "atexit", "collections",
                            "concurrent", "dataclasses", "hashlib", "json", "os",
                            "pathlib", "queue", "sys", "threading", "time",
                            "typing", "urllib",
                        } and not m.startswith(".")]
            self.assertEqual(external, [], f"{name} imports third-party: {external}")

    def test_requirements_lists_only_http_layer_deps(self):
        lines = [l.strip() for l in text("requirements.txt").splitlines()
                 if l.strip() and not l.startswith("#")]
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(">" in l for l in lines))


class TestRepoHygiene(unittest.TestCase):
    def test_gitignore_excludes_run_artifacts_and_secrets(self):
        g = text(".gitignore")
        for rule in ("/traces/", "/results/", ".env", "__pycache__/"):
            self.assertIn(rule, g)
        self.assertNotIn("/experiments/runs/", g)

    def test_env_example_has_no_real_key(self):
        e = text(".env.example")
        self.assertNotRegex(e, r"sk-[A-Za-z0-9]{16,}")
        self.assertIn("W1 默认不接真实供应商", e)

    def test_license_present(self):
        self.assertIn("MIT License", text("LICENSE"))

    def test_no_secret_like_string_in_tracked_sources(self):
        for p in sorted((ROOT / "gateway").glob("*.py")) + sorted((ROOT / "bridge").glob("*.py")):
            self.assertNotRegex(p.read_text(encoding="utf-8"), r"sk-[A-Za-z0-9]{16,}")

    def test_readme_first_screen_is_short(self):
        r = text("README.md")
        self.assertLess(len(r), 7000)
        for section in ("是什么", "怎么跑", "边界", "证据索引"):
            self.assertIn(section, r)


class TestW2Contract(unittest.TestCase):
    """W2 的红线：并发与真实适配器不得污染 W1 的逐字节可复现归档。"""

    def test_gateway_config_fields_and_hash_are_w1_frozen(self):
        import dataclasses
        from gateway.config import GatewayConfig
        names = [f.name for f in dataclasses.fields(GatewayConfig)]
        self.assertEqual(names, [
            "seed", "n_requests", "repeat_rate", "failure_rate", "rpm_limit",
            "window_requests", "cache_capacity", "cache_ttl_requests",
            "gateway_enabled", "models", "fallback_models", "prices", "tasks_name",
        ])
        self.assertEqual(GatewayConfig().config_hash(), "aa1142308d8d")

    def test_w1_synthetic_archive_reproduces_byte_for_byte(self):
        """W2 改了组件与引擎注解，但 W1 归档三件必须逐字节不变。"""
        import json
        import tempfile
        from pathlib import Path
        from gateway.config import GatewayConfig
        from gateway.replay import run_pair, write_archive
        run_dir = ROOT / "experiments/runs" / "replay-aa1142308d8d"
        cfg = GatewayConfig(**json.loads((run_dir / "config.json").read_text(encoding="utf-8")))
        with tempfile.TemporaryDirectory() as td:
            out = write_archive(run_pair(cfg), Path(td))
            for name in ("report.md", "config.json", "decisions.jsonl"):
                self.assertEqual((out / name).read_bytes(),
                                 (run_dir / name).read_bytes(), name)

    def test_replay_never_imports_w2_modules(self):
        import re
        src = text("gateway/replay.py")
        imported = [m.split(".")[-1] for m in
                    re.findall(r"^\s*(?:from|import)\s+([\w.]+)", src, re.M)]
        for name in ("concurrency", "sse", "real_upstream"):
            self.assertNotIn(name, imported)
        self.assertNotIn("ConcurrentGateway", src)

    def test_concurrency_never_writes_archives(self):
        src = text("gateway/concurrency.py")
        for name in ("write_archive", "experiments/runs", "config_hash"):
            self.assertNotIn(name, src)

    def test_no_distributed_infra_imports(self):
        for rel in [f"gateway/{n}" for n in CORE_MODULES]:
            low = text(rel).lower()
            for banned in ("import redis", "import celery", "import kafka", "import kubernetes"):
                self.assertNotIn(banned, low, rel)

    def test_design_w2_declares_two_separate_paths(self):
        d = text("docs/design-w2.md")
        for phrase in ("互不相交", "不产出", "默认关闭", "R6", "single-flight"):
            self.assertIn(phrase, d)

    def test_real_upstream_is_disabled_by_default(self):
        import os
        from unittest.mock import patch
        from gateway.real_upstream import RealUpstreamConfig
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(RealUpstreamConfig.from_env())
        with patch.dict(os.environ, {
            "GATEWAY_REAL_UPSTREAM": "1", "LLM_BASE_URL": "https://example.invalid/v1",
        }, clear=True):
            cfg = RealUpstreamConfig.from_env()
            self.assertIsNotNone(cfg)
            self.assertEqual(cfg.base_url, "https://example.invalid/v1")


if __name__ == "__main__":
    unittest.main()
