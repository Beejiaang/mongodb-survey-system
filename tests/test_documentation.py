"""提交文档与实际路由、schema、索引和示例保持一致。"""

import json
from pathlib import Path
import re
import unittest
from unittest.mock import MagicMock
from urllib.parse import unquote, urlsplit

from app import create_app
from database import COLLECTION_NAMES, USER_VALIDATOR, initialize_database
from questions import validate_question
from response_schema import RESPONSE_VALIDATOR
from survey_schema import SURVEY_VALIDATOR


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


class DocumentationTests(unittest.TestCase):
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
                   "responses": RESPONSE_VALIDATOR["$and"][0]["$jsonSchema"]}
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
        for name, index, keys in re.findall(r"^\| (users|surveys|responses) \| ([a-z_]+) \| ([^|]+) \|", source, re.M):
            documented[name, index] = [(field, 1 if direction == "↑" else -1)
                                       for field, direction in re.findall(r"([a-z_]+)([↑↓])", keys)]
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
