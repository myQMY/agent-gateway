"""W4 工具链守门测试：脚手架必须「备好但不代跑、不污染默认 fake」。

设计依据：docs/design-w4-tooling.md §2 / §3 / §6

重要约束：w4/serve.py 顶层 import gateway.app（依赖 fastapi），裸标准库 Python
跑不了。因此本文件对 serve.py / app.py 一律**只读源码文本**，绝不 import；
唯一被 import 的是纯标准库的 w4.run（用于行为级验证 dry-run 真的不花钱）。

核心两条红线：
- 默认路径仍是 FakeUpstream：gateway/app.py 的模块级 app 保持无参 create_app()；
- 默认不花钱：python -m w4.run 不带 --execute 时不启动服务、不调用 subprocess、
  不发网络请求、不写 P1 目录。
"""

from contextlib import redirect_stdout
import io
import re
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SECRET_RE = re.compile(r"sk-[A-Za-z0-9]{16,}")


def text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def module_level_app_args(src):
    """返回模块级 `app = create_app(...)` 的实参文本；无参返回空串。"""
    m = re.search(r"(?m)^app\s*=\s*create_app\((.*?)\)\s*$", src)
    return m.group(1).strip() if m else None


def toml_value(src, key):
    """取简单 TOML 的 `key = value` 首个值（去引号/去行内注释）。"""
    m = re.search(rf"(?m)^\s*{re.escape(key)}\s*=\s*(.+?)\s*$", src)
    if not m:
        return None
    return m.group(1).split("#", 1)[0].strip().strip('"').strip("'")


class TestDefaultFakeNotPolluted(unittest.TestCase):
    """§2.1：真实上游只能由 w4/serve.py 显式注入，默认 app 保持 fake。"""

    def test_app_module_level_uses_plain_create_app(self):
        self.assertEqual(module_level_app_args(text("gateway/app.py")), "")

    def test_app_never_references_real_upstream_or_switch(self):
        src = text("gateway/app.py")
        self.assertNotIn("RealUpstream", src)
        self.assertNotIn("GATEWAY_REAL_UPSTREAM", src)
        # create_app 仍保留 upstream 形参供注入，但模块级 app 不传它。
        self.assertIn("upstream: Upstream | None = None", src)

    def test_serve_is_the_only_place_injecting_real_upstream(self):
        src = text("w4/serve.py")
        self.assertIn("create_app(upstream=UPSTREAM, concurrent=False)", src)
        self.assertIn("RealUpstream(", src)
        self.assertIn("RealUpstreamConfig(", src)

    def test_serve_self_check_endpoint_never_returns_key_value(self):
        src = text("w4/serve.py")
        self.assertIn('@app.get("/w4/upstream")', src)
        self.assertIn('"kind": "real"', src)
        # 密钥只以「是否存在」的布尔值出现，绝不返回值本身。
        self.assertIn('"api_key_present": bool(os.environ.get(', src)
        block = src.split("return {", 1)[1].split("}", 1)[0]
        self.assertNotRegex(
            block, r'"\w*(?:key|secret|token)\w*"\s*:\s*os\.environ\.get')
        self.assertNotIn("api_key_value", block)

    def test_scanner_would_catch_a_polluted_app(self):
        """自检：若有人把模块级 app 改成注入真实上游，守门确实会拦下。"""
        self.assertEqual(module_level_app_args(text("gateway/app.py")), "")
        polluted = "app = create_app(upstream=RealUpstream(RealUpstreamConfig()), concurrent=True)\n"
        self.assertNotEqual(module_level_app_args(polluted), "")


class TestEnvTemplatesHaveNoSecrets(unittest.TestCase):
    """§4 / §6：仓库里永不出现真实密钥。"""

    def test_env_examples_contain_no_key_material(self):
        for rel in ("w4/on.env.example", "w4/off.env.example",
                    "w4/gw_on.toml", "w4/gw_off.toml"):
            with self.subTest(doc=rel):
                self.assertIsNone(SECRET_RE.search(text(rel)), rel)

    def test_off_template_uses_placeholder(self):
        self.assertIn("REPLACE_ME", text("w4/off.env.example"))

    def test_on_template_points_to_gateway_with_v1(self):
        base = toml_value(text("w4/on.env.example"), "LLM_BASE_URL")
        self.assertIsNotNone(base)
        self.assertTrue(base.rstrip("/").endswith("/v1"), base)
        self.assertIn("127.0.0.1:8000", base)

    def test_off_template_uses_direct_provider_without_v1(self):
        base = toml_value(text("w4/off.env.example"), "LLM_BASE_URL")
        self.assertIsNotNone(base)
        self.assertFalse(base.rstrip("/").endswith("/v1"), base)
        self.assertIn("api.deepseek.com", base)

    def test_gitignore_and_dockerignore_exclude_real_env_and_outputs(self):
        g = text(".gitignore")
        self.assertIn("/w4/*.env", g)
        self.assertIn("/w4/out/", g)
        d = text(".dockerignore")
        self.assertIn("w4/*.env", d)
        self.assertIn("w4/out/", d)

    def test_no_checked_in_real_env_files(self):
        for name in ("on.env", "off.env"):
            with self.subTest(name=name):
                self.assertFalse((ROOT / "w4" / name).exists(),
                                 f"w4/{name} 含真实密钥，绝不能入库")

    def test_no_secret_like_string_in_any_w4_file(self):
        for p in sorted((ROOT / "w4").rglob("*")):
            if p.is_file():
                with self.subTest(file=p.name):
                    self.assertIsNone(SECRET_RE.search(p.read_text(encoding="utf-8")), p.name)


class TestTwoCellConfigs(unittest.TestCase):
    """§3：name 不同才生成不同 run_id；compare 空、护栏全关、不写 [seeded]。"""

    def test_names_differ(self):
        on = toml_value(text("w4/gw_on.toml"), "name")
        off = toml_value(text("w4/gw_off.toml"), "name")
        self.assertNotEqual(on, off)
        self.assertEqual(on, "w4_gw_on")
        self.assertEqual(off, "w4_gw_off")

    def test_both_are_openai_mvp10_k3(self):
        for rel in ("w4/gw_on.toml", "w4/gw_off.toml"):
            with self.subTest(doc=rel):
                t = text(rel)
                self.assertEqual(toml_value(t, "backend"), "openai")
                self.assertEqual(toml_value(t, "tasks"), "tasks/mvp_10.jsonl")
                self.assertEqual(toml_value(t, "k"), "3")

    def test_compare_is_empty_in_both(self):
        for rel in ("w4/gw_on.toml", "w4/gw_off.toml"):
            with self.subTest(doc=rel):
                self.assertEqual(toml_value(text(rel), "compare"), "")

    def test_guards_all_off_and_no_seeded_injection(self):
        for rel in ("w4/gw_on.toml", "w4/gw_off.toml"):
            with self.subTest(doc=rel):
                t = text(rel)
                # 注释里会解释「故意不写」，因此只查真正的键/段，不查注释文本。
                body = "\n".join(l for l in t.splitlines()
                                 if l.strip() and not l.strip().startswith("#"))
                self.assertNotRegex(body, r"(?m)^\s*enabled\s*=")
                self.assertNotIn("[seeded]", body)


class TestDryRunNeverSpends(unittest.TestCase):
    """§2.3：默认 dry-run 不启动服务、不建目录、不调用 subprocess、不发网络。"""

    def test_build_command_has_required_flags_and_no_report(self):
        from w4 import run as w4run
        cmd = w4run.build_command("on", 720)
        self.assertEqual(cmd[1:3], ["-m", "eval.run"])
        self.assertIn("--config", cmd)
        self.assertIn("--env-file", cmd)
        self.assertIn("--max-calls", cmd)
        self.assertIn("720", cmd)
        self.assertNotIn("--report", cmd)  # 传了就不写 run_dir 三件留档

    def test_build_command_writes_everything_under_w4_out(self):
        from w4 import run as w4run
        for cell in ("on", "off"):
            cmd = w4run.build_command(cell, 720)
            out = str(w4run.OUT).replace("\\", "/")
            for flag in ("--runs-dir", "--results-dir", "--traces-dir"):
                with self.subTest(cell=cell, flag=flag):
                    value = cmd[cmd.index(flag) + 1].replace("\\", "/")
                    self.assertTrue(value.startswith(out), value)
            self.assertNotIn("项目1", " ".join(cmd))  # 产物路径不落到 P1

    def test_dry_run_does_not_spawn_or_call_network(self):
        from w4 import run as w4run
        with mock.patch.object(w4run, "start_gateway") as sg, \
             mock.patch.object(w4run, "run_cell") as rc, \
             mock.patch.object(w4run.subprocess, "Popen") as popen, \
             mock.patch.object(w4run.subprocess, "run") as srun, \
             mock.patch.object(w4run, "urlopen") as uopen:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = w4run.main([])
        self.assertEqual(code, 0)
        for m, label in ((sg, "start_gateway"), (rc, "run_cell"),
                         (popen, "Popen"), (srun, "subprocess.run"),
                         (uopen, "urlopen")):
            with self.subTest(target=label):
                self.assertFalse(m.called, f"dry-run 竟调用了 {label}")
        self.assertIn("DRY RUN", buf.getvalue())

    def test_dry_run_does_not_create_output_directories(self):
        from w4 import run as w4run
        before = sorted(str(p) for p in w4run.OUT.rglob("*")) if w4run.OUT.exists() else []
        with redirect_stdout(io.StringIO()):
            w4run.main([])
        after = sorted(str(p) for p in w4run.OUT.rglob("*")) if w4run.OUT.exists() else []
        self.assertEqual(after, before)

    def test_execute_aborts_when_preflight_fails_without_spending(self):
        """即使误加 --execute，只要没填好 env 就必须原地返回，绝不启动网关。"""
        from w4 import run as w4run
        with mock.patch.object(w4run, "preflight", return_value=["mock: 缺少 off.env"]), \
             mock.patch.object(w4run, "start_gateway") as sg, \
             mock.patch.object(w4run, "run_cell") as rc, \
             mock.patch.object(w4run.subprocess, "Popen") as popen, \
             mock.patch.object(w4run.subprocess, "run") as srun:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = w4run.main(["--execute"])
        self.assertEqual(code, 2)
        for m, label in ((sg, "start_gateway"), (rc, "run_cell"),
                         (popen, "Popen"), (srun, "subprocess.run")):
            with self.subTest(target=label):
                self.assertFalse(m.called, f"preflight 未通过竟调用了 {label}")
        self.assertIn("preflight 未通过", buf.getvalue())

    def test_execute_is_opt_in_and_prints_budget_cap(self):
        src = text("w4/run.py")
        self.assertIn('"--execute"', src)
        self.assertIn("if not args.execute and not args.serve_only", src)
        self.assertIn("合计调用上限", src)

    def test_preflight_is_local_only(self):
        src = text("w4/run.py")
        # preflight 定义在 wait_gateway 之前；网络调用只应出现在其后的 wait_gateway 内。
        head = src.split("def wait_gateway", 1)[0]
        self.assertNotIn("urlopen(", head)
        self.assertIn("def preflight", head)

    def test_wait_gateway_rejects_fake_upstream(self):
        src = text("w4/run.py")
        self.assertIn('info.get("kind") != "real"', src)
        self.assertIn("gateway.app:app", src)
        self.assertIn("api_key_present", src)


class TestBridgeReadmeCorrections(unittest.TestCase):
    """§0 的三处代码级事实必须固化在文档里，避免再让用户照错步骤跑。"""

    def test_url_correction_is_documented(self):
        d = text("bridge/README.md")
        self.assertIn("必须带 `/v1`", d)
        self.assertIn("/chat/completions", d)
        self.assertIn("/v1/chat/completions", d)

    def test_env_file_only_correction_is_documented(self):
        d = text("bridge/README.md")
        self.assertIn("--env-file", d)
        self.assertIn("不读 `export`", d)

    def test_app_not_injecting_is_documented(self):
        d = text("bridge/README.md")
        self.assertIn("app = create_app()", d)
        self.assertIn("GATEWAY_REAL_UPSTREAM", d)
        self.assertIn("w4/serve.py", d)

    def test_bridge_links_to_the_tooling(self):
        d = text("bridge/README.md")
        self.assertIn("w4/README.md", d)
        self.assertIn("docs/design-w4-tooling.md", d)
        self.assertIn("w4.run", d)


class TestStageTablesAndFrozenContracts(unittest.TestCase):
    """§5 / §6：W4 依旧标未做；确定性契约不变。"""

    def test_w4_still_marked_pending_in_stage_tables(self):
        for rel in ("README.md", "docs/design.md"):
            with self.subTest(doc=rel):
                row = [ln for ln in text(rel).splitlines() if ln.startswith("| W4")]
                self.assertEqual(len(row), 1, f"{rel} 的 W4 阶段行缺失或重复")
                self.assertIn("未做", row[0])

    def test_w4_docs_never_claim_it_was_done(self):
        for rel in ("w4/README.md", "docs/design-w4-tooling.md", "w4/__init__.py"):
            with self.subTest(doc=rel):
                self.assertIn("未做", text(rel))

    def test_new_docs_are_indexed(self):
        r = text("README.md")
        self.assertIn("docs/design-w4-tooling.md", r)
        self.assertIn("w4/README.md", r)
        self.assertIn("docs/design-w4-tooling.md", text("docs/design.md"))

    def test_gateway_config_is_still_w1_frozen(self):
        import dataclasses
        from gateway.config import GatewayConfig
        self.assertEqual(len(dataclasses.fields(GatewayConfig)), 13)
        self.assertEqual(GatewayConfig().config_hash(), "aa1142308d8d")

    def test_default_app_import_stays_fake_only(self):
        """裸标准库不能 import app.py，但至少确认它未被本轮改动波及。"""
        src = text("gateway/app.py")
        self.assertIn("from .upstream import Upstream", src)
        self.assertNotIn("from .real_upstream import", src)
        self.assertIn("app = create_app()", src)


if __name__ == "__main__":
    unittest.main()
