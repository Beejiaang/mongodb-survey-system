"""题目配置及单题答案校验；不连接数据库，不执行跳转。"""

from copy import deepcopy
from math import ceil, floor, isfinite
from uuid import uuid4


CHOICE_TYPES = ("single_choice", "multiple_choice")
QUESTION_TYPES = (*CHOICE_TYPES, "fill_blank")


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}必须是非空文本。")
    return value.strip()


def _number(value):
    # bool 在 Python 中属于 int，但不能作为问卷数字；整数需能存入 BSON。
    return (isinstance(value, int) and not isinstance(value, bool)
            and -(2**63) <= value < 2**63) or (
        isinstance(value, float) and isfinite(value)
    )


def _bounds(rules, lower, upper, *, counts=False):
    for key in (lower, upper):
        value = rules[key]
        if value is None:
            continue
        if counts:
            valid = (isinstance(value, int) and not isinstance(value, bool)
                     and 0 <= value < 2**63)
        else:
            valid = _number(value)
        if not valid:
            raise ValueError(f"{key}必须是{'非负整数' if counts else '有限数字'}或 null。")
    low, high = rules[lower], rules[upper]
    if low is not None and high is not None and low > high:
        raise ValueError("最小限制不能大于最大限制。")


def build_question(question_type, title, *, required=False, options=None,
                   input_type=None, validation=None, question_id=None):
    """生成稳定编号和默认配置；重排或编辑时保留已有编号。"""
    question = {
        "id": uuid4().hex if question_id is None else question_id,
        "type": question_type,
        "title": title,
        "required": required,
        "validation": {},
        "jump_rules": [],
    }
    if question_type in CHOICE_TYPES:
        question["options"] = [] if options is None else deepcopy(options)
        if input_type is not None:
            raise ValueError("选择题不能设置填空类型。")
        if question_type == "multiple_choice":
            question["validation"] = {"min_choices": None, "max_choices": None}
    elif question_type == "fill_blank":
        if options is not None:
            raise ValueError("填空题不能设置选项。")
        question["input_type"] = input_type
        if input_type == "text":
            question["validation"] = {"min_length": None, "max_length": None}
        elif input_type == "number":
            question["validation"] = {
                "min_value": None, "max_value": None, "integer_only": False,
            }
    if validation is not None:
        if not isinstance(validation, dict):
            raise ValueError("校验规则必须是对象。")
        question["validation"].update(validation)
    return validate_question(question)


def validate_question(question):
    """返回独立的规范化配置；拒绝未知字段，防止拼错规则后失效。"""
    if not isinstance(question, dict):
        raise ValueError("题目必须是对象。")
    q = deepcopy(question)
    kind = q.get("type")
    if kind not in QUESTION_TYPES:
        raise ValueError("不支持的题型。")
    fields = {"id", "type", "title", "required", "validation", "jump_rules"}
    fields.add("options" if kind in CHOICE_TYPES else "input_type")
    if set(q) != fields:
        raise ValueError("题目字段缺失或包含不支持的字段。")
    q["id"] = _text(q["id"], "题目编号")
    q["title"] = _text(q["title"], "题目标题")
    if type(q["required"]) is not bool:
        raise ValueError("是否必答必须是布尔值。")
    if not isinstance(q["jump_rules"], list) or q["jump_rules"]:
        raise ValueError("当前步骤尚未实现跳转规则，jump_rules 必须为空数组。")
    rules = q["validation"]
    if not isinstance(rules, dict):
        raise ValueError("校验规则必须是对象。")
    expected_rules = set()
    if kind in CHOICE_TYPES:
        options = q["options"]
        if not isinstance(options, list) or len(options) < 2:
            raise ValueError("选择题至少需要两个选项。")
        option_ids = set()
        for option in options:
            if not isinstance(option, dict) or set(option) != {"id", "text"}:
                raise ValueError("每个选项必须包含 id 和 text。")
            option["id"] = _text(option["id"], "选项编号")
            option["text"] = _text(option["text"], "选项内容")
            if option["id"] in option_ids:
                raise ValueError("同一题内的选项编号不能重复。")
            option_ids.add(option["id"])
        if kind == "multiple_choice":
            expected_rules = {"min_choices", "max_choices"}
    elif q["input_type"] == "text":
        expected_rules = {"min_length", "max_length"}
    elif q["input_type"] == "number":
        expected_rules = {"min_value", "max_value", "integer_only"}
    else:
        raise ValueError("填空类型必须是 text 或 number。")
    if set(rules) != expected_rules:
        raise ValueError("校验规则字段与题型不匹配。")
    if kind == "multiple_choice":
        _bounds(rules, "min_choices", "max_choices", counts=True)
        for value in rules.values():
            if value is not None and value > len(q["options"]):
                raise ValueError("选择数量限制不能超过选项总数。")
        if q["required"] and rules["max_choices"] == 0:
            raise ValueError("必答多选题必须允许至少选择一个选项。")
    elif kind == "fill_blank" and q["input_type"] == "text":
        _bounds(rules, "min_length", "max_length", counts=True)
        if q["required"] and rules["max_length"] == 0:
            raise ValueError("必答文本题必须允许至少输入一个字符。")
    elif kind == "fill_blank":
        _bounds(rules, "min_value", "max_value")
        if type(rules["integer_only"]) is not bool:
            raise ValueError("是否必须为整数必须是布尔值。")
        if rules["integer_only"]:
            low, high = rules["min_value"], rules["max_value"]
            if low is not None and high is not None and ceil(low) > floor(high):
                raise ValueError("数字范围内没有可填写的整数。")
    return q


def validate_questions(questions):
    """校验草稿题目列表，保留顺序；空列表允许草稿逐步添加题目。"""
    if not isinstance(questions, list):
        raise ValueError("题目列表必须是数组。")
    result = [validate_question(question) for question in questions]
    if len({q["id"] for q in result}) != len(result):
        raise ValueError("同一问卷内的题目编号不能重复。")
    return result


def validate_answer(question, answer):
    """校验单题答案，返回可保存值；未作答统一为 None。"""
    q = validate_question(question)
    kind, rules = q["type"], q["validation"]
    is_text = kind == "fill_blank" and q["input_type"] == "text"
    empty = answer is None or (
        kind == "multiple_choice" and isinstance(answer, list) and not answer
    ) or (is_text and isinstance(answer, str) and not answer.strip())
    if empty:
        if q["required"]:
            raise ValueError("此题为必答题。")
        return None
    if kind in CHOICE_TYPES:
        valid_ids = {option["id"] for option in q["options"]}
        if kind == "single_choice":
            if not isinstance(answer, str) or answer not in valid_ids:
                raise ValueError("单选题必须选择一个有效选项。")
            return answer
        if not isinstance(answer, list) or any(
            not isinstance(value, str) or value not in valid_ids for value in answer
        ):
            raise ValueError("多选题答案必须是有效选项编号组成的数组。")
        if len(set(answer)) != len(answer):
            raise ValueError("多选题不能重复选择同一选项。")
        value, low, high = len(answer), rules["min_choices"], rules["max_choices"]
    elif is_text:
        if not isinstance(answer, str):
            raise ValueError("文本填空必须填写文本。")
        # 按 Unicode 码点计数，保留原文及非空文本的首尾空格。
        value, low, high = len(answer), rules["min_length"], rules["max_length"]
    else:
        if not _number(answer):
            raise ValueError("数字填空必须填写有效的有限数字。")
        if rules["integer_only"] and isinstance(answer, float) and not answer.is_integer():
            raise ValueError("此题必须填写整数。")
        value, low, high = answer, rules["min_value"], rules["max_value"]
    if low is not None and value < low:
        raise ValueError("答案低于最小限制。")
    if high is not None and value > high:
        raise ValueError("答案超过最大限制。")
    return deepcopy(answer)
