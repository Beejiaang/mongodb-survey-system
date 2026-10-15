"""验证当前首页从路由到 HTML 模板的完整响应。"""

import unittest
from unittest.mock import patch

from app import app


class HomepageTests(unittest.TestCase):
    def test_homepage_renders_html_template(self):
        with patch.dict(app.config, TESTING=True):
            with app.test_client() as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        content = response.get_data(as_text=True)
        self.assertIn("<h1>问卷系统首页</h1>", content)
        self.assertIn("欢迎使用问卷系统。", content)
        self.assertNotIn("{{ title }}", content)


if __name__ == "__main__":
    unittest.main()
