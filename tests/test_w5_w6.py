"""W5–W6 守门测试：叙事红线是文档，但必须像代码一样可检。

设计依据：docs/design-w5-w6.md
- §4 禁用短语清单由本测试从设计文档直接解析（单一真相来源）；
- §5 反面示例隔离区与否定语境豁免；
- §1 首句定位、§6 三份文档的结构契约。

本轮零行为代码变更，因此这里验证的是「对外说的话有没有越界」，
外加自检「扫描器本身真的能拦住违规文本」，防止守门测试形同虚设。
"""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent
DESIGN = "docs/design-w5-w6.md"
EXTERNAL_DOCS = ("README.en.md", "docs/resume.md", "docs/interview.md")

BANNED_START = "<!-- BANNED-PHRASES-START -->"
BANNED_END = "<!-- BANNED-PHRASES-END -->"
EXAMPLES_START = "<!-- FORBIDDEN-EXAMPLES-START -->"
EXAMPLES_END = "<!-- FORBIDDEN-EXAMPLES-END -->"

# 设计文档 §5.6：否定语境豁免（同一行内出现即跳过该行）。
NEGATIONS = (
    "不是", "并非", "不得", "不能", "不宣称", "不写", "不说", "绝不",
    "未", "非", "无", "不产", "不外推", "不测", "不接", "不改", "没有",
    "not", "never", "no ", "cannot", "does not", "is not", "are not",
    "neither", "nor ", "without", "not done",
)


def text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def banned_phrases():
    """从设计文档 §4 的 HTML 注释对里解析 fenced code block 中的逐行短语。"""
    d = text(DESIGN)
    assert BANNED_START in d and BANNED_END in d, "设计文档缺少禁用短语标记对"
    block = d.split(BANNED_START, 1)[1].split(BANNED_END, 1)[0]
    m = re.search(r"```\s*\n(.*?)\n```", block, re.S)
    assert m, "禁用短语必须放在 fenced code block 内"
    return [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]


def strip_examples(doc):
    """剥离 §5 的反面示例区与所有 ❌ 行（含 '- ❌' 列表项）。"""
    while EXAMPLES_START in doc and EXAMPLES_END in doc:
        head, rest = doc.split(EXAMPLES_START, 1)
        _, tail = rest.split(EXAMPLES_END, 1)
        doc = head + tail
    kept = []
    for line in doc.splitlines():
        stripped = line.strip()
        probe = stripped.lstrip("-*• ").strip()
        if probe.startswith("❌"):
            continue
        kept.append(line)
    return "\n".join(kept)


def find_violations(doc, phrases=None):
    """返回 [(行号, 短语, 行内容)]；扫描前剥离反面示例，按行做否定语境豁免。"""
    phrases = phrases if phrases is not None else banned_phrases()
    body = strip_examples(doc)
    hits = []
    for lineno, line in enumerate(body.splitlines(), 1):
        low = line.lower()
        if any(neg in low for neg in NEGATIONS):
            continue
        for phrase in phrases:
            if phrase.lower() in low:
                hits.append((lineno, phrase, line.strip()))
    return hits


def first_body_block(doc):
    """标题之后、第一个空行之前的连续非空行块；去掉引用符与强调符后折成一行。"""
    lines = doc.splitlines()
    start = None
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        start = i
        break
    if start is None:
        return ""
    block = []
    for line in lines[start:]:
        s = line.strip()
        if not s:
            break
        if s.startswith(">"):
            s = s[1:].strip()
        block.append(s)
    return " ".join(block).replace("**", "").replace("`", "")


def paragraphs(doc):
    """按空行切分段落，用于「关键数字 + 同段边界词」抽查。

    跳过以 '#' 起始的标题段落：访谈文档里的「### C1. 「0.1433 → ...？」」是提问句，
    不是结论断言，边界写在紧随其后的回答段落里（design-w5-w6.md §3）。
    """
    parts = [p.strip() for p in re.split(r"\n\s*\n", strip_examples(doc)) if p.strip()]
    return [p for p in parts if not p.startswith("#")]


class TestBannedPhraseSource(unittest.TestCase):
    """禁用清单本身必须可解析、双语覆盖、无重复。"""

    def test_list_is_parseable_and_bilingual(self):
        phrases = banned_phrases()
        self.assertGreaterEqual(len(phrases), 20)
        for sample in ("智能客服", "通用 Agent 评测平台", "生产级网关",
                       "production-ready gateway", "W4 completed",
                       "W4 验证了网关收益", "verified gateway benefit on real models"):
            self.assertIn(sample, phrases)
        self.assertEqual(len(phrases), len(set(phrases)), "清单里有重复行")

    def test_design_declares_w5_w6_red_lines(self):
        d = text(DESIGN)
        for phrase in ("首句", "W4 口径", "不许说的话", "否定语境豁免",
                       "反面示例", "单一真相来源"):
            self.assertIn(phrase, d)

    def test_every_external_doc_has_isolation_markers(self):
        for rel in EXTERNAL_DOCS:
            d = text(rel)
            with self.subTest(doc=rel):
                self.assertIn(EXAMPLES_START, d, f"{rel} 缺少反面示例起始标记")
                self.assertIn(EXAMPLES_END, d, f"{rel} 缺少反面示例结束标记")
                self.assertEqual(d.count(EXAMPLES_START), d.count(EXAMPLES_END))


class TestScannerIsReal(unittest.TestCase):
    """自检：扫描器必须能拦住正面断言，同时放过否定语境。"""

    def test_positive_claim_is_caught(self):
        fake = "## 结果\n\n这是一个生产级网关，成本下降 30%，上线后能省钱。\n"
        hits = find_violations(fake, ["生产级网关", "上线后能省钱"])
        self.assertGreaterEqual(len(hits), 2)

    def test_negated_context_is_exempt(self):
        fake = "## 边界\n\n这不是生产级网关，也不宣称上线后能省钱。\n"
        self.assertEqual(find_violations(fake, ["生产级网关", "上线后能省钱"]), [])

    def test_isolation_block_is_stripped(self):
        fake = ("## 汇总\n"
                "<!-- FORBIDDEN-EXAMPLES-START -->\n"
                "- ❌ 这是生产级网关\n"
                "<!-- FORBIDDEN-EXAMPLES-END -->\n"
                "正文没有任何越界。\n")
        self.assertEqual(find_violations(fake, ["生产级网关"]), [])

    def test_bullet_cross_mark_is_stripped(self):
        fake = "## 汇总\n\n- ❌ 这是生产级网关\n- ✅ 这是合成回放内的相对量\n"
        self.assertEqual(find_violations(fake, ["生产级网关"]), [])


class TestNoOverclaimInExternalDocs(unittest.TestCase):
    """§4 的核心断言：三份对外文档正文不得出现禁用短语的正面断言。"""

    def test_no_banned_phrase_as_positive_assertion(self):
        phrases = banned_phrases()
        for rel in EXTERNAL_DOCS:
            with self.subTest(doc=rel):
                hits = find_violations(text(rel), phrases)
                self.assertEqual(
                    hits, [],
                    f"{rel} 出现越界断言：" +
                    "; ".join(f"L{n}「{p}」{line[:60]}" for n, p, line in hits[:5]),
                )


class TestPositioningFirstSentence(unittest.TestCase):
    """§1：首句必须亮明「评测装置 / 运行时平台，非面向用户的 Agent 应用」。"""

    def test_resume_first_paragraph(self):
        head = first_body_block(text("docs/resume.md"))
        for phrase in ("评测装置", "运行时平台", "非面向用户的 Agent 应用"):
            self.assertIn(phrase, head, f"简历首段缺少「{phrase}」")

    def test_english_readme_first_paragraph(self):
        head = first_body_block(text("README.en.md"))
        for phrase in ("evaluation harness", "runtime platform",
                       "not a user-facing Agent application"):
            self.assertIn(phrase, head, f"英文 README 首段缺少「{phrase}」")

    def test_positioning_is_not_buried_at_the_end(self):
        # 定位必须出现在首屏，而不是靠全文 grep 在文末凑一句。
        resume = text("docs/resume.md")
        self.assertLess(resume.find("评测装置"), 400)
        en = text("README.en.md")
        self.assertLess(en.find("evaluation harness"), 500)


class TestEnglishReadmeParity(unittest.TestCase):
    """§6：英文 README 与中文结构对齐，不新增承诺。"""

    def test_section_count_matches_chinese(self):
        cn = re.findall(r"^## (.+)$", text("README.md"), re.M)
        en = re.findall(r"^## (.+)$", text("README.en.md"), re.M)
        self.assertEqual(len(cn), 8)
        self.assertEqual(len(en), len(cn), f"中英章节数不符：{cn} vs {en}")

    def test_required_terminology_is_present(self):
        d = text("README.en.md")
        for phrase in ("deterministic fake", "simulated latency",
                       "concurrency invariants", "byte-for-byte",
                       "optional container", "read-only trace view",
                       "Two Disjoint Paths", "Executed once"):
            self.assertIn(phrase, d, f"英文 README 缺少术语「{phrase}」")

    def test_english_readme_links_back_to_chinese(self):
        self.assertIn("README.md", text("README.en.md"))


class TestNumbersCarryBoundaries(unittest.TestCase):
    """§3 抽查：每个关键数字所在的段落必须同时带边界词。"""

    CHECKS = (
        ("0.1433", ("描述性统计", "不外推真实模型", "不是 A/B", "fpm 只控")),
        ("36.2%", ("描述性统计", "不外推真实模型", "最多兑现")),
        ("0.1185", ("不外推生产", "不宣称省钱", "合成回放", "固定价格表")),
        ("612", ("守门测试", "相当比例")),
        ("157", ("skip", "隔离", "守门测试")),
        ("0.00", ("未被检验", "不是护栏", "判分", "H1")),
        ("1/N", ("分辨率", "与 k 无关")),
    )

    def test_key_numbers_never_appear_naked_in_chinese_docs(self):
        for rel in ("docs/resume.md", "docs/interview.md"):
            paras = paragraphs(text(rel))
            for number, bounds in self.CHECKS:
                matching = [p for p in paras if number in p]
                if not matching:
                    continue  # 该文档未引用这个数字，不强制
                for p in matching:
                    with self.subTest(doc=rel, number=number):
                        self.assertTrue(
                            any(b in p for b in bounds),
                            f"{rel} 中「{number}」所在段落缺边界：{p[:100]}",
                        )


class TestW4BoundaryWording(unittest.TestCase):
    """两格真实链路回放已执行一次；所有文档必须带「未产收益结论」的边界。"""

    def test_all_external_docs_carry_the_new_boundary(self):
        for rel, marker in (("README.en.md", "Executed once"),
                            ("docs/resume.md", "已执行一次"),
                            ("docs/interview.md", "已执行一次")):
            with self.subTest(doc=rel):
                d = text(rel)
                self.assertIn("W4", d)
                self.assertIn(marker, d)

    def test_design_and_readme_carry_the_new_boundary(self):
        for rel in ("docs/design.md", "README.md", "docs/design-w5-w6.md",
                    "docs/design-w4-tooling.md", "bridge/README.md", "w4/README.md"):
            with self.subTest(doc=rel):
                self.assertIn("已执行一次", text(rel))

    def test_no_external_doc_claims_a_positive_result(self):
        for rel in EXTERNAL_DOCS:
            with self.subTest(doc=rel):
                self.assertEqual(find_violations(text(rel)), [])

    def test_guard_would_catch_an_overclaim(self):
        fake = "## 结果\n\nW4 验证了网关收益，真实链路证明省钱。\n"
        hits = find_violations(fake, ["W4 验证了网关收益", "真实链路证明省钱"])
        self.assertEqual(len(hits), 2)


class TestIndexContract(unittest.TestCase):
    """§7：首屏必须能找到三份新文档与设计补充文档。"""

    def test_chinese_readme_links_all_new_docs(self):
        r = text("README.md")
        for link in ("README.en.md", "docs/resume.md", "docs/interview.md",
                     "docs/design-w5-w6.md"):
            self.assertIn(link, r, f"中文 README 缺少索引 {link}")

    def test_design_links_w5_w6_supplement(self):
        self.assertIn("docs/design-w5-w6.md", text("docs/design.md"))

    def test_stage_tables_mark_w5_w6_delivered(self):
        for rel in ("docs/design.md", "README.md"):
            with self.subTest(doc=rel):
                d = text(rel)
                row = [ln for ln in d.splitlines()
                       if ln.startswith("| W5–W6")]
                self.assertEqual(len(row), 1)
                self.assertIn("本轮交付", row[0])

    def test_readme_first_screen_still_short(self):
        self.assertLess(len(text("README.md")), 7000)


class TestNoCodeOrArchiveChanged(unittest.TestCase):
    """§8：本轮零行为代码变更，W1/W2/W3 的确定性契约全部保持。"""

    def test_gateway_config_is_still_frozen(self):
        import dataclasses
        from gateway.config import GatewayConfig
        self.assertEqual(len(dataclasses.fields(GatewayConfig)), 13)
        self.assertEqual(GatewayConfig().config_hash(), "aa1142308d8d")

    def test_both_w1_archives_still_reproduce_byte_for_byte(self):
        import json
        import tempfile
        from gateway.config import GatewayConfig
        from gateway.replay import run_pair, write_archive
        p1 = Path("D:/文档/课题/agent/项目1/tasks/mvp_10.jsonl")
        for name in ("replay-aa1142308d8d", "replay-83b590b9e477"):
            run_dir = ROOT / "experiments/runs" / name
            cfg = GatewayConfig(**json.loads(
                (run_dir / "config.json").read_text(encoding="utf-8")))
            tasks = p1 if cfg.tasks_name else None
            with tempfile.TemporaryDirectory() as td:
                out = write_archive(run_pair(cfg, tasks), Path(td))
                for f in ("report.md", "config.json", "decisions.jsonl"):
                    with self.subTest(archive=name, file=f):
                        self.assertEqual((out / f).read_bytes(),
                                         (run_dir / f).read_bytes())


if __name__ == "__main__":
    unittest.main()
