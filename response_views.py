"""分享入口后的逐题填写、确认和本人提交回执。"""

from flask import Blueprint, current_app, g, redirect, render_template, request, url_for

from auth import login_required
from database import get_request_database
from numeric_input import NUMBER_PATTERN, parse_numeric_input
from responses import (ResponseError, own_response, open_survey, check_progress,
                       start_response, update_progress, submit_response)


filling = Blueprint("filling", __name__)


def identity_secret():
    return current_app.config["RESPONSE_IDENTITY_KEY"]


def _url(survey_id, response_id):
    return url_for("filling.fill", survey_id=survey_id, response_id=response_id)


def parse_answer(question):
    values = request.form.getlist("answer")
    if question["type"] == "multiple_choice":
        return values
    if len(values) > 1:
        raise ValueError("本题只能填写一个答案。")
    raw = values[0] if values else ""
    if question["type"] == "single_choice":
        return raw or None
    if question.get("input_type") == "number":
        return parse_numeric_input(raw)
    return raw


@filling.post("/survey/<survey_id>/start")
@login_required
def start(survey_id):
    rid = start_response(get_request_database(), survey_id, g.user["_id"], identity_secret())
    return redirect(_url(survey_id, rid), code=303)


@filling.route("/survey/<survey_id>/fill/<response_id>", methods=("GET", "POST"))
@login_required
def fill(survey_id, response_id):
    db = get_request_database()
    record = own_response(db, survey_id, response_id, g.user["_id"], identity_secret())
    if record["state"] == "submitted":
        return render_template("surveys/receipt.html", title="提交成功", record=record)
    survey = open_survey(db, survey_id)
    check_progress(survey, record)
    question = next((q for q in survey["questions"] if q["id"] == record["current_question_id"]), None)
    error, status = None, 200
    if request.method == "POST":
        try:
            try:
                version = int(request.form.get("version", ""))
            except ValueError as invalid:
                raise ResponseError("填写版本无效，请刷新页面。", 400) from invalid
            action = request.form.get("action")
            if action == "submit":
                submit_response(db, survey_id, response_id, g.user["_id"], identity_secret(), version)
            else:
                answer = parse_answer(question) if action == "next" and question else None
                update_progress(db, survey_id, response_id, g.user["_id"], identity_secret(), version, action, answer)
            return redirect(_url(survey_id, response_id), code=303)
        except ResponseError:
            raise
        except ValueError as invalid:
            error, status = str(invalid), 400
    saved = {a["question_id"]: a["value"] for a in record["answers"]}
    value = saved.get(question["id"]) if question else None
    selected = value if isinstance(value, list) else [value] if value is not None else []
    if error:
        selected = request.form.getlist("answer")
        value = request.form.get("answer", "")
    review = []
    for item in record["answers"]:
        q = next(q for q in survey["questions"] if q["id"] == item["question_id"])
        labels = {o["id"]: o["text"] for o in q.get("options", [])}
        answer = item["value"]
        if answer is None:
            text = "未填写（选答）"
        elif isinstance(answer, list):
            text = "、".join(labels[x] for x in answer)
        else:
            text = labels.get(answer, str(answer))
        review.append({"title": q["title"], "answer": text})
    number = next((i + 1 for i, q in enumerate(survey["questions"]) if question and q["id"] == question["id"]), None)
    can_back = bool(record["answers"]) and (question is None or question["id"] != survey["questions"][0]["id"])
    return render_template("surveys/fill.html", title="填写问卷", survey=survey, record=record,
        question=question, number=number, value=value, selected=selected, can_back=can_back,
        review=review, error=error, number_pattern=NUMBER_PATTERN), status
