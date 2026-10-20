"""网页数字输入格式；与填写页的HTML pattern使用同一规则。"""

import re


NUMBER_PATTERN = r"(?:\+|-)?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE](?:\+|-)?[0-9]+)?"


def parse_numeric_input(raw):
    raw = raw.strip()
    if not raw:
        return None
    if re.fullmatch(NUMBER_PATTERN, raw) is None:
        raise ValueError("请使用阿拉伯数字回答（0–9），不要填写文字、全角数字或单位。")
    # 优先保留64位整数精度；有限性、范围及整数限制仍由答案校验处理。
    try:
        return int(raw)
    except ValueError:
        return float(raw)
