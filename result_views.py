"""仅创建者可访问的问卷和单题统计页面。"""

from flask import Blueprint, abort, g, render_template, request

from auth import login_required
from database import get_request_database
from survey_results import PAGE_SIZE, answer_page, owned_results
from survey_views import KINDS, STATUS_LABELS
from surveys import availability


results = Blueprint("results", __name__, url_prefix="/surveys")


def _owned_results(survey_id):
    result = owned_results(get_request_database(), survey_id, g.user["_id"])
    if result is None:
        abort(404)
    return result


@results.get("/<survey_id>/results")
@login_required
def overview(survey_id):
    survey, summary = _owned_results(survey_id)
    return render_template("surveys/results.html", title="统计结果", survey=survey,
                           summary=summary, kinds=KINDS, state=availability(survey),
                           status_labels=STATUS_LABELS, selected=None)


@results.get("/<survey_id>/results/questions/<question_id>")
@login_required
def question(survey_id, question_id):
    survey, summary = _owned_results(survey_id)
    selected = next((row for row in summary["questions"] if row["question"]["id"] == question_id), None)
    if selected is None:
        abort(404)
    raw_page = request.args.get("page", "1")
    if not raw_page.isascii() or not raw_page.isdigit() or len(raw_page) > 7:
        abort(400)
    page = int(raw_page)
    if page < 1:
        abort(400)
    pages = max(1, (selected["answered"] + PAGE_SIZE - 1) // PAGE_SIZE)
    if page > pages or (page != 1 and selected["question"]["type"] != "fill_blank"):
        abort(404)
    answers = answer_page(get_request_database(), survey["_id"], question_id, page) if selected["question"]["type"] == "fill_blank" else None
    return render_template("surveys/results.html", title="单题统计", survey=survey,
                           summary=summary, kinds=KINDS, state=availability(survey),
                           status_labels=STATUS_LABELS, selected=selected, answers=answers)
