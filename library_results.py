"""Cross-survey statistics for one pinned version, scoped to survey ownership."""
from collections import Counter
from copy import deepcopy
from fractions import Fraction

from question_library import LibraryError, get_version, _content
from surveys import _object_id
from survey_results import PAGE_SIZE, _average


def summarize_version(database, version_id, user_id, *, page=1):
    vid = _object_id(version_id)
    if vid is None:
        raise LibraryError("版本不存在。", 404)
    origin_item = database.question_items.find_one({"owner_id": user_id, "original_use.version_id": vid})
    origin = origin_item["original_use"] if origin_item else None
    version_for_origin = database.question_versions.find_one({"_id": vid}) if origin else None
    conditions = [{"questions.source.version_id": vid}]
    if origin:
        conditions.append({"_id": origin["survey_id"]})
    surveys = list(database.surveys.find({"owner_id": user_id, "$or": conditions},
                                         {"title": 1, "questions": 1}))
    ids, titles, fallback = {}, {}, None
    for survey in surveys:
        selected = [q for q in survey["questions"] if q.get("source", {}).get("version_id") == vid]
        if origin and survey["_id"] == origin["survey_id"] and version_for_origin:
            original = next((q for q in survey["questions"] if q["id"] == origin["instance_id"]), None)
            if original and _content(original) == version_for_origin["content"] and original not in selected:
                selected.append(original)
        if not selected:
            continue
        ids[survey["_id"]] = {q["id"] for q in selected}
        titles[survey["_id"]] = survey["title"]
        if selected:
            fallback = selected[0]
    try:
        item, version = get_version(database, vid, user_id)
        question, number = version["content"], version["number"]
        item_name = item["name"]
    except LibraryError:
        if fallback is None:
            raise
        # Revocation must not remove statistics on the user's own frozen survey copy.
        question = deepcopy(fallback)
        metadata = database.question_versions.find_one({"_id": vid}, {"number": 1})
        number = metadata["number"] if metadata else None
        item_name = question["title"]
    counts, total, answered, reached, slots, numeric_sum = Counter(), 0, 0, 0, 0, Fraction(0)
    answers = []
    offset = (page - 1) * PAGE_SIZE
    query = {"survey_id": {"$in": list(ids)}, "state": "submitted"}
    with database.responses.find(query, {"survey_id": 1, "path": 1, "answers": 1,
                                         "submitted_at": 1}).sort([("submitted_at", -1), ("_id", -1)]) as records:
        for record in records:
            total += 1
            valid_ids = ids[record["survey_id"]]
            path = set(record["path"]) & valid_ids
            reached += len(path)
            slots += len(valid_ids)
            for answer in record["answers"]:
                if answer["question_id"] not in path or answer["value"] is None:
                    continue
                value = answer["value"]
                answered += 1
                if question["type"] in ("single_choice", "multiple_choice"):
                    counts.update(set(value) if isinstance(value, list) else [value])
                elif question["input_type"] == "number":
                    numeric_sum += Fraction(str(value))
                if offset < answered <= offset + PAGE_SIZE:
                    answers.append({"value": value, "submitted_at": record["submitted_at"],
                                    "survey_title": titles[record["survey_id"]]})
    options = [{"text": o["text"], "count": counts[o["id"]],
                "percent": format(counts[o["id"]] * 100 / answered, ".1f") if answered else "0.0"}
               for o in question.get("options", [])]
    chart = None
    if options:
        chart = {"kind": question["type"], "series": [{"label": o["text"], "count": o["count"]} for o in options]}
    elif question.get("input_type") == "number":
        alternatives = [{"survey_id": sid, "answers.question_id": {"$in": sorted(qids)},
                         "path": {"$in": sorted(qids)}} for sid, qids in ids.items()]
        bins = []
        if alternatives:
            pipeline = [{"$match": query}, {"$unwind": "$answers"},
                        {"$match": {"$or": alternatives, "answers.value": {"$type": ["int", "long", "double"]},
                                    "$expr": {"$in": ["$answers.question_id", "$path"]}}},
                        {"$bucketAuto": {"groupBy": "$answers.value", "buckets": 10,
                                         "output": {"count": {"$sum": 1}}}}]
            with database.responses.aggregate(pipeline) as cursor:
                bins = list(cursor)
        series = []
        for i, b in enumerate(bins):
            low, high = b["_id"]["min"], b["_id"]["max"]
            label = str(low) if low == high else f"[{low}, {high}{']' if i == len(bins)-1 else ')'}"
            series.append({"label": label, "count": b["count"]})
        chart = {"kind": "number", "series": series}
    return {"question": question, "name": item_name, "number": number,
            "survey_count": len(ids), "submission_count": total, "answered": answered,
            "reached": reached, "blank": reached - answered, "skipped": slots - reached,
            "options": options, "chart": chart,
            "average": _average(numeric_sum, answered) if question.get("input_type") == "number" else None,
            "answers": answers, "has_next": answered > offset + PAGE_SIZE, "offset": offset}
