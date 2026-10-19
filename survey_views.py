"""登录用户创建草稿、查看自己的问卷并逐题保存。"""

from uuid import uuid4
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from auth import login_required
from database import get_request_database
from questions import build_question
from jumps import OPERATORS, allowed_operators, next_question_id, question_revision
from surveys import (add_question, create_survey, get_owned_survey, list_owned_surveys,
                     availability, publish_survey, set_survey_deadline, close_survey,
                     get_shared_survey, save_jump_rules)


surveys = Blueprint("surveys", __name__, url_prefix="/surveys")
shared_surveys = Blueprint("shared_surveys", __name__)
BEIJING = timezone(timedelta(hours=8))
KINDS = {"single_choice": "单选题", "multiple_choice": "多选题",
         "text": "文本填空", "number": "数字填空"}
STATUS_LABELS = {"draft": "草稿", "published": "已发布", "closed": "已关闭", "expired": "已截止"}


def _owned(survey_id):
    survey = get_owned_survey(get_request_database(), survey_id, g.user["_id"])
    if survey is None:
        abort(404)
    return survey


def _checkbox(name):
    value = request.form.get(name)
    if value not in (None, "on"):
        raise ValueError("开关设置无效，请重新选择。")
    return value == "on"


def _bound(name, label, *, integer=False):
    raw = request.form.get(name, "").strip()
    if not raw:
        return None
    try:
        if integer:
            return int(raw)
        # 先尝试整数，保留手机号等较大整数的精度。
        try:
            return int(raw)
        except ValueError:
            return float(raw)
    except ValueError as error:
        raise ValueError(f"{label}必须是{'整数' if integer else '数字'}。") from error


def _question_from_form(kind):
    kwargs = {"required": _checkbox("required")}
    if kind in ("single_choice", "multiple_choice"):
        labels = request.form.get("options_text", "").splitlines()
        kwargs["options"] = [{"id": uuid4().hex, "text": label.strip()}
                             for label in labels if label.strip()]
        if kind == "multiple_choice":
            kwargs["validation"] = {
                "min_choices": _bound("min_choices", "最少选择数", integer=True),
                "max_choices": _bound("max_choices", "最多选择数", integer=True),
            }
        question_type = kind
    else:
        question_type = "fill_blank"
        kwargs["input_type"] = kind
        if kind == "text":
            kwargs["validation"] = {
                "min_length": _bound("min_length", "最短长度", integer=True),
                "max_length": _bound("max_length", "最长长度", integer=True),
            }
        else:
            kwargs["validation"] = {
                "min_value": _bound("min_value", "最小值"),
                "max_value": _bound("max_value", "最大值"),
                "integer_only": _checkbox("integer_only"),
            }
    return build_question(question_type, request.form.get("title", ""), **kwargs)


@surveys.get("")
@login_required
def index():
    items = list_owned_surveys(get_request_database(), g.user["_id"])
    return render_template("surveys/list.html", title="我的问卷", surveys=items,
                           status_labels=STATUS_LABELS, availability=availability)


@surveys.route("/new", methods=("GET", "POST"))
@login_required
def new():
    error, status = None, 200
    if request.method == "POST":
        try:
            survey_id = create_survey(
                get_request_database(), g.user["_id"], request.form.get("title", ""),
                description=request.form.get("description", ""),
                is_anonymous=_checkbox("is_anonymous"), allow_repeat=_checkbox("allow_repeat"),
            )
        except ValueError as invalid:
            error, status = str(invalid), 400
        else:
            flash("问卷草稿已保存，可以开始添加题目。", "success")
            return redirect(url_for("surveys.detail", survey_id=survey_id), code=303)
    return render_template("surveys/new.html", title="创建问卷", error=error,
                           values=request.form), status


@surveys.get("/<survey_id>")
@login_required
def detail(survey_id):
    return _render_detail(_owned(survey_id))


def _render_detail(survey, error=None, deadline_value=None, status=200):
    if deadline_value is None:
        deadline_value = survey["deadline"].astimezone(BEIJING).strftime("%Y-%m-%dT%H:%M") if survey["deadline"] else ""
    return render_template(
        "surveys/detail.html", title="问卷详情", survey=survey, kinds=KINDS,
        status_labels=STATUS_LABELS, state=availability(survey), error=error,
        deadline_value=deadline_value,
        share_url=url_for("shared_surveys.entry", survey_id=survey["_id"], _external=True),
    ), status


@surveys.post("/<survey_id>/deadline")
@login_required
def deadline(survey_id):
    survey = _owned(survey_id)
    raw = request.form.get("deadline", "").strip()
    try:
        parsed = None
        if raw:
            try:
                parsed = datetime.strptime(raw, "%Y-%m-%dT%H:%M").replace(tzinfo=BEIJING)
            except ValueError as invalid:
                raise ValueError("请输入有效的北京时间，精确到分钟。") from invalid
        saved = set_survey_deadline(get_request_database(), survey["_id"], g.user["_id"], parsed)
    except ValueError as invalid:
        return _render_detail(survey, error=str(invalid), deadline_value=raw, status=400)
    if not saved:
        return _render_detail(_owned(survey_id), error="已关闭的问卷不能修改截止时间。", status=409)
    flash("截止时间已保存。" if parsed else "已取消截止时间限制。", "success")
    return redirect(url_for("surveys.detail", survey_id=survey_id), code=303)


@surveys.post("/<survey_id>/publish")
@login_required
def publish(survey_id):
    survey = _owned(survey_id)
    try:
        saved = publish_survey(get_request_database(), survey["_id"], g.user["_id"])
    except ValueError as invalid:
        return _render_detail(survey, error=str(invalid), status=400)
    if not saved:
        return _render_detail(_owned(survey_id), error="问卷已发布或内容发生变化，请查看后重试。", status=409)
    flash("问卷已发布，可以复制分享链接。", "success")
    return redirect(url_for("surveys.detail", survey_id=survey_id), code=303)


@surveys.post("/<survey_id>/close")
@login_required
def close(survey_id):
    survey = _owned(survey_id)
    if not close_survey(get_request_database(), survey["_id"], g.user["_id"]):
        return _render_detail(survey, error="只有已发布的问卷可以关闭。", status=409)
    flash("问卷已关闭，原分享链接不再开放问卷内容。", "success")
    return redirect(url_for("surveys.detail", survey_id=survey_id), code=303)


@shared_surveys.get("/survey/<survey_id>")
def entry(survey_id):
    survey = get_shared_survey(get_request_database(), survey_id)
    if survey is None:
        abort(404)
    if g.user is None:
        return redirect(url_for("auth.login", next=url_for("shared_surveys.entry", survey_id=survey_id)))
    state = availability(survey)
    return render_template("surveys/shared.html", title="问卷入口", survey=survey,
                           state=state, kinds=KINDS), 410 if state in ("expired", "closed") else 200


@surveys.route("/<survey_id>/questions/new", methods=("GET", "POST"))
@login_required
def new_question(survey_id):
    survey = _owned(survey_id)
    if survey["status"] != "draft":
        return render_template("error.html", title="问卷不可修改",
                               message="只有草稿问卷可以添加题目。"), 409
    kind = request.form.get("kind", "single_choice") if request.method == "POST" else request.args.get("kind", "single_choice")
    if kind not in KINDS:
        abort(400)
    error, status = None, 200
    if request.method == "POST":
        try:
            question = _question_from_form(kind)
            saved = add_question(get_request_database(), survey["_id"], g.user["_id"], question)
        except ValueError as invalid:
            error, status = str(invalid), 400
        else:
            if not saved:
                return render_template("error.html", title="未能保存题目",
                                       message="问卷状态已变化，请返回问卷查看后重试。"), 409
            flash("题目已保存。", "success")
            return redirect(url_for("surveys.detail", survey_id=survey_id), code=303)
    return render_template("surveys/question_new.html", title="添加题目", survey=survey,
                           kind=kind, kinds=KINDS, error=error, values=request.form), status


@surveys.route("/<survey_id>/questions/<question_id>/jumps", methods=("GET", "POST"))
@login_required
def edit_jumps(survey_id, question_id):
    from copy import deepcopy

    survey = _owned(survey_id)
    question = next((q for q in survey["questions"] if q["id"] == question_id), None)
    if question is None:
        abort(404)
    if survey["status"] != "draft":
        return render_template("error.html", title="问卷不可修改", message="只有草稿问卷可以配置跳转。"), 409
    labels = {q["id"]: f"第{i + 1}题：{q['title']}" for i, q in enumerate(survey["questions"])}
    position = next(i for i, q in enumerate(survey["questions"]) if q["id"] == question_id)
    revision = question_revision(survey["questions"])
    error, result, status = None, None, 200
    if request.method == "POST":
        if request.form.get("revision") != revision:
            error, status = "题目或规则已变化，请刷新页面后重新操作。", 409
        else:
            try:
                action = request.form.get("action")
                rules = deepcopy(question["jump_rules"])
                if action == "test":
                    if question["type"] == "single_choice":
                        answer = request.form.get("test_answer") or None
                    elif question["type"] == "multiple_choice":
                        answer = request.form.getlist("test_answer")
                    elif question.get("input_type") == "number":
                        answer = _bound("test_number", "试算答案")
                    else:
                        answer = request.form.get("test_text", "")
                    target = next_question_id(survey["questions"], question_id, answer)
                    result = "下一步：" + (labels[target] if target else "提交确认页")
                else:
                    if action == "add":
                        op = request.form.get("operator", "")
                        if op not in allowed_operators(question):
                            raise ValueError("请选择适合当前题型的条件。")
                        if question["type"] == "single_choice":
                            value = request.form.get("value", "")
                        elif question["type"] == "multiple_choice":
                            value = request.form.getlist("value")
                        else:
                            value = _bound("number", "条件数值")
                            if op == "between":
                                value = [value, _bound("upper", "区间上限")]
                        rules.append({"condition": {"operator": op, "value": value},
                                      "target_question_id": request.form.get("target", "")})
                    elif action in ("delete", "up", "down"):
                        try:
                            index = int(request.form.get("index", ""))
                        except ValueError as invalid:
                            raise ValueError("规则编号无效。") from invalid
                        if not 0 <= index < len(rules):
                            raise ValueError("规则不存在。")
                        if action == "delete":
                            rules.pop(index)
                        else:
                            other = index + (-1 if action == "up" else 1)
                            if not 0 <= other < len(rules):
                                raise ValueError("规则已在边界，无法继续移动。")
                            rules[index], rules[other] = rules[other], rules[index]
                    else:
                        raise ValueError("不支持的操作。")
                    if not save_jump_rules(get_request_database(), survey["_id"], g.user["_id"],
                                           question_id, rules, revision):
                        error, status = "问卷已变化或发布，请刷新后重试。", 409
                    else:
                        flash("跳转规则已保存。", "success")
                        return redirect(url_for("surveys.edit_jumps", survey_id=survey_id,
                                                question_id=question_id), code=303)
            except ValueError as invalid:
                error, status = str(invalid), 400
    option_labels = {o["id"]: o["text"] for o in question.get("options", [])}
    descriptions = []
    for rule in question["jump_rules"]:
        condition = rule["condition"]
        value = condition["value"]
        if question["type"] == "single_choice":
            text = option_labels[value]
        elif question["type"] == "multiple_choice":
            text = "、".join(option_labels[item] for item in value)
        elif isinstance(value, list):
            text = f"{value[0]} ～ {value[1]}"
        else:
            text = str(value)
        descriptions.append(f"{OPERATORS[condition['operator']]}：{text} → {labels[rule['target_question_id']]}")
    return render_template("surveys/jumps.html", title="配置跳转", survey=survey,
                           question=question, targets=survey["questions"][position + 1:],
                           labels=labels, operators=allowed_operators(question), operator_labels=OPERATORS,
                           descriptions=descriptions, revision=revision, values=request.form,
                           error=error, result=result), status
