"""只读统计与按题分页答案；返回值不包含填写者身份。"""

from decimal import Decimal, localcontext
from fractions import Fraction

from surveys import get_owned_survey


PAGE_SIZE = 20


def _average(total, count):
    if not count:
        return None
    value = total / count
    # 先用分数累加，避免大数溢出和浮点误差，再按两位小数展示。
    with localcontext() as context:
        context.prec = max(50, len(str(abs(value.numerator))) + len(str(value.denominator)) + 10)
        average = Decimal(value.numerator) / Decimal(value.denominator)
        text = format(average, ".2f")
    return "0.00" if text == "-0.00" else text


def summarize(database, survey):
    questions = []
    by_id, sums = {}, {}
    for number, question in enumerate(survey["questions"], 1):
        row = {"question": question, "number": number, "answered": 0, "reached": 0,
               "blank": 0, "skipped": 0, "options": [], "average": None}
        row["options"] = [{"id": o["id"], "text": o["text"], "count": 0, "percent": "0.0"}
                          for o in question.get("options", [])]
        questions.append(row)
        by_id[question["id"]] = row
        sums[question["id"]] = Fraction(0)
    total = 0
    # 从一次游标读取中计算总数和各题统计，内存不随填写份数增长。
    with database.responses.find(
        {"survey_id": survey["_id"], "state": "submitted"},
        {"_id": 0, "answers": 1, "path": 1},
    ).batch_size(128) as records:
        for record in records:
            total += 1
            path = set(record["path"])
            for qid in path:
                if qid in by_id:
                    by_id[qid]["reached"] += 1
            for answer in record["answers"]:
                qid, value = answer["question_id"], answer["value"]
                if qid not in by_id or qid not in path or value is None:
                    continue
                row = by_id[qid]
                row["answered"] += 1
                question = row["question"]
                if question["type"] in ("single_choice", "multiple_choice"):
                    chosen = set(value) if isinstance(value, list) else {value}
                    for option in row["options"]:
                        option["count"] += option["id"] in chosen
                elif question["input_type"] == "number":
                    sums[qid] += Fraction(str(value))
    for row in questions:
        row["skipped"] = total - row["reached"]
        row["blank"] = row["reached"] - row["answered"]
        for option in row["options"]:
            option["percent"] = format(option["count"] * 100 / row["answered"], ".1f") if row["answered"] else "0.0"
        if row["question"].get("input_type") == "number":
            row["average"] = _average(sums[row["question"]["id"]], row["answered"])
    return {"total": total, "questions": questions}


def answer_page(database, survey_id, question_id, page):
    """填空答案独立分页，只返回值和提交时间，按时间及编号稳定倒序。"""
    pipeline = [
        {"$match": {"survey_id": survey_id, "state": "submitted", "path": question_id}},
        {"$sort": {"submitted_at": -1, "_id": -1}},
        {"$unwind": "$answers"},
        {"$match": {"answers.question_id": question_id, "answers.value": {"$ne": None}}},
        {"$skip": (page - 1) * PAGE_SIZE}, {"$limit": PAGE_SIZE + 1},
        {"$project": {"_id": 0, "value": "$answers.value", "submitted_at": 1}},
    ]
    with database.responses.aggregate(pipeline) as cursor:
        items = list(cursor)
    return {"items": items[:PAGE_SIZE], "has_next": len(items) > PAGE_SIZE,
            "page": page, "offset": (page - 1) * PAGE_SIZE}


def owned_results(database, survey_id, owner_id):
    survey = get_owned_survey(database, survey_id, owner_id)
    if survey is None:
        return None
    return survey, summarize(database, survey)
