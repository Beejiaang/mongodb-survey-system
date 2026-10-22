"""提交文档与实际路由、schema、索引和示例保持一致。"""

import ast
import json
from pathlib import Path
import re
import unittest
from unittest.mock import MagicMock
from urllib.parse import unquote, urlsplit

from app import create_app
from database import COLLECTION_NAMES, USER_VALIDATOR, initialize_database
from jumps import allowed_operators
from questions import validate_question
from response_schema import RESPONSE_VALIDATOR
from survey_schema import SURVEY_VALIDATOR
from library_schema import LIBRARY_VALIDATORS


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


class DocumentationTests(unittest.TestCase):
    def test_completion_report_covers_assignment_and_personal_ai_review(self):
        source = (DOCS / "项目完成报告.md").read_text(encoding="utf-8")
        self.assertIn("课程：当代数据管理系统｜", source)
        self.assertIn("当代数据管理系统第一次大作业。", (ROOT / "README.md").read_text(encoding="utf-8"))
        required = ["一、项目目标", "二、系统设计", "三、MongoDB设计", "四、跳转设计",
                    "五、校验设计", "六、AI使用过程", "七、测试结果", "八、遇到的问题", "九、如何解决"]
        for heading in required:
            with self.subTest(section=heading):
                match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", source, re.M | re.S)
                self.assertIsNotNone(match)
                self.assertGreater(len(match.group(1).strip()), 100, "必需章节不能只有占位标题")
        for topic in ("AI帮了什么", "AI做错了什么", "自己设计了什么", "自己改了什么"):
            self.assertIn(topic, source)
        for evidence in ("测试用例.md", "AI使用日志.md", "关键逻辑说明.md", "数据库设计.md"):
            self.assertIn(f"]({evidence})", source)
        numeric_row = re.search(r"^\| 数字填空 \| ([a-z_、]+) \|", source, re.M)
        self.assertIsNotNone(numeric_row)
        self.assertEqual(tuple(numeric_row.group(1).split("、")),
                         allowed_operators({"type": "fill_blank", "input_type": "number"}))
        catalog = (DOCS / "测试用例.md").read_text(encoding="utf-8")
        case_count = len(re.findall(r"^\| [CQJVRSEFAN]\d{2} \|", catalog, re.M))
        report_count = re.search(r"当前(\d+)组场景", source)
        readme_count = re.search(r"整理的(\d+)组场景", (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIsNotNone(report_count)
        self.assertIsNotNone(readme_count)
        self.assertEqual(int(report_count.group(1)), case_count, "报告中的场景数未随用例同步")
        self.assertEqual(int(readme_count.group(1)), case_count, "README中的场景数未随用例同步")

    def test_test_case_catalog_has_complete_rows_and_valid_references(self):
        source = (DOCS / "测试用例.md").read_text(encoding="utf-8")
        rows = re.findall(r"^\| ([CQJVRSEFAN]\d{2}) \| (.+) \|$", source, re.M)
        expected = {f"{prefix}{n:02}" for prefix, count in
                    {"C": 4, "Q": 4, "J": 6, "V": 6, "R": 6, "S": 7,
                     "E": 14, "F": 6, "A": 1, "N": 1}.items() for n in range(1, count + 1)}
        self.assertEqual({row[0] for row in rows}, expected)
        self.assertEqual(len(rows), len(expected), "用例编号重复")
        methods = set()
        for path in (ROOT / "tests").glob("test_*.py"):
            for cls in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(cls, ast.ClassDef):
                    methods.update(f"{path.stem}.{cls.name}.{method.name}" for method in cls.body
                                   if isinstance(method, ast.FunctionDef) and method.name.startswith("test_"))
        js_names = set()
        for path in (ROOT / "tests").glob("*.test.cjs"):
            js_names.update(re.findall(r"test\('([^']+)'", path.read_text(encoding="utf-8")))
        for case_id, content in rows:
            with self.subTest(case=case_id):
                columns = [part.strip() for part in content.split(" | ")]
                self.assertEqual(len(columns), 4, "必须记录步骤/输入、预期、实际结果、测试定位")
                self.assertTrue(all(columns))
                self.assertIn("①", columns[0], "需要有序可复现步骤")
                if case_id.startswith("N"):
                    self.assertIn("通过（实际样本的核心流程）", columns[2])
                    self.assertIn("原计划负例未执行", columns[2])
                    evidence = json.loads((DOCS / "验收证据" / "N01-跨设备实测.json").read_text(encoding="utf-8"))
                    self.assertEqual(evidence["case"], case_id)
                    self.assertEqual(evidence["summary"]["total"], len(evidence["submissions"]))
                    self.assertEqual(len({item["receipt_id"] for item in evidence["submissions"]}), 2)
                    self.assertEqual({item["account"] for item in evidence["submissions"]}, {"ceshi", "ceshi2"})
                    self.assertTrue(all(item["state"] == "submitted" and item["anonymous_user_id_is_null"]
                                        and item["path_length"] == 4 for item in evidence["submissions"]))
                    checks = evidence["post_close_server_checks"]
                    self.assertEqual(checks["entry_get_http"], 410)
                    self.assertEqual(checks["start_post_http_with_valid_csrf"], 410)
                    self.assertEqual(checks["creator_stats_http"], 200)
                    self.assertEqual(checks["records_before_and_after"], [2, 2])
                    self.assertTrue(evidence["physical_phone_evidence"]["confirmed"])
                    self.assertTrue(evidence["not_executed_or_not_collected"])
                    for step in ("另一台设备", "数据库", "统计"):
                        self.assertIn(step, columns[0])
                    self.assertIn("手工", columns[-1])
                elif case_id.startswith("F"):
                    self.assertTrue(set(columns[-1].split("；")) <= js_names, "JS测试引用失效")
                else:
                    references = re.findall(r"`(test_\w+\.\w+\.test_\w+)`", columns[-1])
                    self.assertTrue(references, "缺少明确的Python测试定位")
                    self.assertTrue(set(references) <= methods, "Python测试引用失效")

        phase_two = (DOCS / "第二阶段测试用例.md").read_text(encoding="utf-8")
        phase_rows = re.findall(r"^\| (P2-\d{2}) \| (.+) \|$", phase_two, re.M)
        self.assertEqual([r[0] for r in phase_rows], [f"P2-{n:02}" for n in range(1, 40)])
        documented_methods = set()
        for _, content in phase_rows:
            columns = content.split(" | ")
            self.assertEqual(len(columns), 4)
            self.assertIn("①", columns[0])
            self.assertIn("通过", columns[2])
            documented_methods.update(re.findall(r"`(test_\w+\.\w+\.test_\w+)`", columns[3]))
        phase_prefixes = ("test_layout.", "test_library.", "test_library_web.",
                          "test_library_results.", "test_phase2_acceptance.")
        self.assertEqual(documented_methods, {m for m in methods if m.startswith(phase_prefixes)})
        report = (DOCS / "第二阶段项目完成报告.md").read_text(encoding="utf-8")
        self.assertIn(f"完整Python套件{len(methods)}项通过", report)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"第二阶段当前完整套件{len(methods)}项Python", readme)

    def test_all_business_routes_and_methods_are_documented(self):
        source = (DOCS / "API说明.md").read_text(encoding="utf-8")
        documented = set()
        for methods, path in re.findall(r"^\| (GET(?:, POST)?|POST) \| (/[^|]*?) \|", source, re.M):
            for method in methods.split(", "):
                documented.add((method, path.strip()))
        app = create_app({"TESTING": True, "SECRET_KEY": "documentation-test",
                          "RESPONSE_IDENTITY_KEY": "documentation-test"})
        actual = {(method, rule.rule) for rule in app.url_map.iter_rules() if rule.endpoint != "static"
                  for method in rule.methods - {"HEAD", "OPTIONS"}}
        self.assertTrue(documented, "API总表未解析出接口")
        self.assertEqual(documented, actual, "文档缺少/多写接口或方法")

    def test_collection_field_tables_match_current_validators(self):
        source = (DOCS / "数据库设计.md").read_text(encoding="utf-8")
        schemas = {"users": USER_VALIDATOR["$jsonSchema"],
                   "surveys": SURVEY_VALIDATOR["$jsonSchema"],
                   "responses": RESPONSE_VALIDATOR["$and"][0]["$jsonSchema"],
                   **{name: validator["$jsonSchema"] for name, validator in LIBRARY_VALIDATORS.items()}}
        for name, schema in schemas.items():
            with self.subTest(collection=name):
                section = re.search(rf"^## {name} 字段\n(.*?)(?=^## |\Z)", source, re.M | re.S)
                self.assertIsNotNone(section)
                fields = re.findall(r"^\| ([a-z_]+) \|", section.group(1), re.M)
                self.assertEqual(len(fields), len(set(fields)), "字段表重复")
                self.assertEqual(set(fields), set(schema["properties"]))
                self.assertEqual(set(fields), set(schema["required"]))

    def test_documented_index_names_and_order_match_initializer(self):
        source = (DOCS / "数据库设计.md").read_text(encoding="utf-8")
        documented = {}
        for name, index, keys in re.findall(r"^\| (users|surveys|responses|question_items|question_versions|question_banks) \| ([a-z_]+) \| ([^|]+) \|", source, re.M):
            documented[name, index] = [(field, 1 if direction == "↑" else -1)
                                       for field, direction in re.findall(r"([a-z_.]+)([↑↓])", keys)]
        # 捕获真实初始化函数声明的索引，不连接或修改业务数据库。
        database = MagicMock()
        database.__getitem__.side_effect = lambda name: getattr(database, name)
        initialize_database(database)
        actual = {}
        for name in COLLECTION_NAMES:
            for call in getattr(database, name).create_index.call_args_list:
                keys = call.args[0]
                actual[name, call.kwargs["name"]] = [(keys, 1)] if isinstance(keys, str) else keys
        self.assertTrue(documented)
        self.assertEqual(documented, actual)

    def test_database_question_example_passes_real_configuration_validation(self):
        source = (DOCS / "数据库设计.md").read_text(encoding="utf-8")
        examples = re.findall(r"```json\n(.*?)\n```", source, re.S)
        self.assertTrue(examples)
        for example in examples:
            question = json.loads(example)
            self.assertEqual(validate_question(question), question)

    def test_local_markdown_links_resolve_within_submission(self):
        files = [ROOT / "README.md", *DOCS.rglob("*.md")]
        for path in files:
            source = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
            for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", source):
                target = target.strip().strip("<>")
                parts = urlsplit(target)
                if parts.scheme or not parts.path:
                    continue
                with self.subTest(file=path.relative_to(ROOT), target=target):
                    resolved = (path.parent / unquote(parts.path)).resolve()
                    self.assertTrue(resolved.is_relative_to(ROOT), "提交文档不应依赖仓库外本地文件")
                    self.assertTrue(resolved.exists(), "本地文档或代码链接失效")
