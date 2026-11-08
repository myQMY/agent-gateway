"""守门测试：把设计文档里的红线与仓库卫生钉死（口径与 P1 一致）。"""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent
CORE_MODULES = (
    "config.py", "core.py", "components.py", "engine.py", "upstream.py",
    "replay.py", "concurrency.py", "sse.py", "real_upstream.py", "bench.py",
    "loadgen.py", "trace.py",
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
                            "concurrent", "dataclasses", "hashlib", "html", "json", "os",
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


class TestW3Contract(unittest.TestCase):
    """W3 红线：容器是可选便利、负载不产性能数字、trace 只读归档。"""

    def test_design_w3_declares_the_three_boundaries(self):
        d = text("docs/design-w3.md")
        for phrase in ("可选便利", "R6", "只读", "不变量", "未实跑", "scheduling-dependent"):
            self.assertIn(phrase, d)

    def test_compose_has_no_distributed_infra(self):
        # 注释里会写明「不引入 redis」等口径，只检查真正的清单内容。
        lines = [l for l in text("docker-compose.yml").splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        c = "\n".join(lines).lower()
        for banned in ("redis", "postgres", "kafka", "kubernetes", "mysql", "celery"):
            self.assertNotIn(banned, c)
        for required in ("profiles", "verify", "gateway.trace --run", "unittest"):
            self.assertIn(required, c)

    def test_dockerfile_only_installs_existing_requirements(self):
        d = text("Dockerfile")
        self.assertIn("python:3.13-slim", d)
        self.assertIn("pip install --no-cache-dir -r requirements.txt", d)
        for banned in ("redis", "apt-get", "curl"):
            self.assertNotIn(banned, d.lower())
        lines = [l.strip() for l in text("requirements.txt").splitlines()
                 if l.strip() and not l.startswith("#")]
        self.assertEqual(len(lines), 3)

    def test_dockerignore_excludes_secrets(self):
        d = text(".dockerignore")
        for item in (".env", ".git", "__pycache__/", ".workbuddy/"):
            self.assertIn(item, d)

    def test_docker_is_optional_not_a_prerequisite(self):
        r = text("README.md")
        self.assertIn("python -m unittest discover -s tests -q", r)
        self.assertIn("可选", r)
        d = text("docs/design-w3.md")
        self.assertIn("主复现路径", d)

    def test_loadgen_source_never_reads_a_clock_as_metric(self):
        src = text("gateway/loadgen.py")
        # docstring 里写了「源码不 import time」，因此只检查真实的 import 语句。
        self.assertNotRegex(src, r"(?m)^\s*(?:from|import)\s+time\b")
        low = src.lower()
        for banned in ("perf_counter", "time.time", "qps", "p95", "wall_ms",
                       "throughput", "elapsed"):
            self.assertNotIn(banned, low, banned)

    def test_loadgen_payload_has_no_performance_fields(self):
        import json
        from gateway.loadgen import build_payload
        payload = build_payload(8, 2, 2, 4, 2, 11, ("capacity_gate", "rate_limit"))
        dumped = json.dumps(payload).lower()
        for banned in ("qps", "rps", "p95", "wall_ms", "elapsed", "throughput", "latency"):
            self.assertNotIn(banned, dumped, banned)
        self.assertTrue(payload["all_pass"])

    def test_loadgen_refuses_to_write_into_archives(self):
        from gateway.loadgen import main
        with self.assertRaises(SystemExit):
            main(["--n", "4", "--json-out", "experiments/runs/forbidden.json"])

    def test_trace_source_never_writes_archives(self):
        src = text("gateway/trace.py")
        for banned in ("write_archive", "shutil", "unlink(", "rename("):
            self.assertNotIn(banned, src)
        self.assertIn("refusing to write a view into experiments/runs", src)

    def test_example_view_is_committed_and_byte_reproducible(self):
        from gateway.trace import load_run, render_html
        data = load_run("replay-aa1142308d8d", ROOT / "experiments/runs")
        expected = render_html(data).encode("utf-8")
        view = ROOT / "experiments/views/replay-aa1142308d8d.html"
        self.assertTrue(view.is_file(), "示例视图必须随仓库入库")
        self.assertEqual(view.read_bytes(), expected)

    def test_trace_view_has_no_clock_or_local_paths(self):
        view = (ROOT / "experiments/views/replay-aa1142308d8d.html").read_text(encoding="utf-8")
        for banned in ("Generated at", "D:/", "C:\\", "localhost", "<script", "http://", "https://"):
            self.assertNotIn(banned, view, banned)

    def test_w3_added_no_gateway_config_field(self):
        import dataclasses
        from gateway.config import GatewayConfig
        self.assertEqual(GatewayConfig().config_hash(), "aa1142308d8d")
        self.assertEqual(len(dataclasses.fields(GatewayConfig)), 13)


if __name__ == "__main__":
    unittest.main()
