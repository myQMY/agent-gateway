"""守门测试：把设计文档里的红线与仓库卫生钉死（口径与 P1 一致）。"""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent
CORE_MODULES = ("config.py", "core.py", "components.py", "engine.py", "upstream.py", "replay.py")


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
                            "__future__", "argparse", "collections", "dataclasses",
                            "hashlib", "json", "pathlib", "time", "typing",
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


if __name__ == "__main__":
    unittest.main()
