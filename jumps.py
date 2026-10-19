"""单题跳转条件、前向路径计算及规则配置版本。"""

from copy import deepcopy
from hashlib import sha256
import json


OPERATORS = {
    "equals": "选择了", "contains_all": "至少包含指定项（可多选其他项）",
    "equals_set": "恰好选择指定项（不能多选或少选）",
    "eq": "等于", "lt": "小于", "lte": "小于等于", "gt": "大于",
    "gte": "大于等于", "between": "处于区间内（包含两端）",
}


def allowed_operators(question):
    if question["type"] == "single_choice":
        return ("equals",)
    if question["type"] == "multiple_choice":
        return ("contains_all", "equals_set")
    if question.get("input_type") == "number":
        return ("eq", "lt", "lte", "gt", "gte", "between")
    return ()


def validate_jump_rules(question):
    """校验条件本身；目标位置由整卷校验检查。"""
    from questions import _number

    rules = question["jump_rules"]
    if not isinstance(rules, list):
        raise ValueError("跳转规则必须是数组。")
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"condition", "target_question_id"}:
            raise ValueError("每条跳转必须包含条件和目标题目编号。")
        target = rule["target_question_id"]
        if not isinstance(target, str) or not target or target != target.strip():
            raise ValueError("目标题目编号无效。")
        condition = rule["condition"]
        if not isinstance(condition, dict) or set(condition) != {"operator", "value"}:
            raise ValueError("条件仅支持一个运算符及其值，不支持组合条件。")
        op, value = condition["operator"], condition["value"]
        if op not in allowed_operators(question):
            raise ValueError("该跳转条件不适用于当前题型。")
        if question["type"] == "single_choice":
            if not isinstance(value, str) or value not in {o["id"] for o in question["options"]}:
                raise ValueError("跳转条件必须引用本题已有选项。")
        elif question["type"] == "multiple_choice":
            ids = {o["id"] for o in question["options"]}
            if (not isinstance(value, list) or not value or
                    any(not isinstance(item, str) or item not in ids for item in value)):
                raise ValueError("请选择至少一个本题已有选项。")
            if len(set(value)) != len(value):
                raise ValueError("跳转条件中的选项不能重复。")
            low, high = question["validation"]["min_choices"], question["validation"]["max_choices"]
            if high is not None and len(value) > high:
                raise ValueError("指定项数量超过本题最多选择数，规则无法触发。")
            if op == "equals_set" and low is not None and len(value) < low:
                raise ValueError("恰好选择的项数低于本题最少选择数，规则无法触发。")
        elif op == "between":
            if (not isinstance(value, list) or len(value) != 2 or
                    not all(_number(item) for item in value) or value[0] > value[1]):
                raise ValueError("区间需要两个有效数字，且下限不能大于上限。")
        elif not _number(value):
            raise ValueError("数字跳转条件必须是有限数字。")


def validate_targets(questions):
    positions = {q["id"]: i for i, q in enumerate(questions)}
    for index, question in enumerate(questions):
        for rule in question["jump_rules"]:
            if positions.get(rule["target_question_id"], -1) <= index:
                raise ValueError("跳转目标必须是本问卷中位于当前题之后的题目。")


def _matches(condition, answer):
    if answer is None:
        return False
    op, value = condition["operator"], condition["value"]
    if op in ("equals", "eq"):
        return answer == value
    if op == "contains_all":
        return set(value).issubset(answer)
    if op == "equals_set":
        return set(value) == set(answer)
    if op == "between":
        return value[0] <= answer <= value[1]
    if op == "lt":
        return answer < value
    if op == "lte":
        return answer <= value
    if op == "gt":
        return answer > value
    return answer >= value  # gte；调用前已校验运算符。


def _next(questions, index, answer):
    for rule in questions[index]["jump_rules"]:
        if _matches(rule["condition"], answer):
            return rule["target_question_id"]
    return questions[index + 1]["id"] if index + 1 < len(questions) else None


def next_question_id(questions, question_id, answer):
    from questions import validate_answer, validate_questions

    questions = validate_questions(questions)
    for index, question in enumerate(questions):
        if question["id"] == question_id:
            return _next(questions, index, validate_answer(question, answer))
    raise ValueError("题目不存在。")


def evaluate_path(questions, answers, *, require_complete=False):
    """重算路径并返回仅途经题目的答案；未回答时停止，None表示主动留空。"""
    from questions import validate_answer, validate_questions

    questions = validate_questions(questions)
    positions = {q["id"]: i for i, q in enumerate(questions)}
    if not isinstance(answers, dict) or any(key not in positions for key in answers):
        raise ValueError("答案必须按本问卷题目编号组织。")
    path, clean = [], {}
    current = questions[0]["id"] if questions else None
    while current is not None:
        path.append(current)
        if current not in answers:
            if require_complete:
                raise ValueError("还有途经的题目未确认作答。")
            break
        index = positions[current]
        clean[current] = validate_answer(questions[index], answers[current])
        current = _next(questions, index, clean[current])
    return {"path": path, "answers": deepcopy(clean), "next_question_id": current,
            "complete": current is None}


def question_revision(questions):
    return sha256(json.dumps(questions, sort_keys=True, ensure_ascii=True,
                             separators=(",", ":")).encode()).hexdigest()
