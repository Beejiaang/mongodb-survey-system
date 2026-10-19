"""服务端填写进度、按路径校验及幂等提交。"""

from hashlib import sha256
import hmac

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from jumps import evaluate_path, question_revision
from questions import validate_answer
from surveys import _object_id, availability, utcnow


class ResponseError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def respondent_key(secret, survey_id, user_id):
    return hmac.new(secret.encode(), f"{survey_id}:{user_id}".encode(), sha256).hexdigest()


def open_survey(database, survey_id):
    survey_id = _object_id(survey_id)
    survey = database.surveys.find_one({"_id": survey_id}) if survey_id else None
    if survey is None or survey["status"] == "draft":
        raise ResponseError("问卷不存在。", 404)
    if availability(survey) != "published":
        raise ResponseError("问卷已关闭或截止，不能继续填写或提交。", 410)
    return survey


def own_response(database, survey_id, response_id, user_id, secret):
    sid, rid = _object_id(survey_id), _object_id(response_id)
    if sid is None or rid is None:
        raise ResponseError("填写记录不存在。", 404)
    record = database.responses.find_one({"_id": rid, "survey_id": sid,
        "respondent_key": respondent_key(secret, sid, user_id)})
    if record is None:
        raise ResponseError("填写记录不存在。", 404)
    return record


def existing_submission(database, survey, key):
    if not survey["allow_repeat"]:
        return database.responses.find_one({"survey_id": survey["_id"],
            "respondent_key": key, "state": "submitted"}, {"_id": 1})
    return None


def start_response(database, survey_id, user_id, secret):
    survey = open_survey(database, survey_id)
    if database.users.find_one({"_id": user_id}, {"_id": 1}) is None:
        raise ResponseError("请重新登录。", 401)
    key = respondent_key(secret, survey["_id"], user_id)
    if existing_submission(database, survey, key):
        raise ResponseError("你已提交过这份问卷，每人只能填写一次。")
    progress = database.responses.find_one({"survey_id": survey["_id"],
        "respondent_key": key, "state": "draft"}, sort=[("created_at", -1)])
    if progress:
        return progress["_id"]
    if not survey["questions"]:
        raise ResponseError("问卷没有可填写的题目。")
    document = {
        "_id": ObjectId(), "survey_id": survey["_id"], "respondent_key": key,
        "user_id": None if survey["is_anonymous"] else user_id,
        "is_anonymous": survey["is_anonymous"], "allow_repeat": survey["allow_repeat"],
        "state": "draft", "question_revision": question_revision(survey["questions"]),
        "version": 0, "answers": [], "path": [],
        "current_question_id": survey["questions"][0]["id"],
        "created_at": utcnow(), "submitted_at": None,
    }
    database.responses.insert_one(document)
    return document["_id"]


def check_progress(survey, record):
    if (record["question_revision"] != question_revision(survey["questions"])
            or record["is_anonymous"] != survey["is_anonymous"]
            or record["allow_repeat"] != survey["allow_repeat"]):
        raise ResponseError("问卷配置已改变，请联系创建者。")


def update_progress(database, survey_id, response_id, user_id, secret, version, action, answer=None):
    record = own_response(database, survey_id, response_id, user_id, secret)
    if record["state"] != "draft":
        raise ResponseError("回答已提交，不能再修改。")
    if type(version) is not int or version != record["version"]:
        raise ResponseError("填写进度已在其他页面改变，请刷新后重试。")
    survey = open_survey(database, survey_id)
    check_progress(survey, record)
    answers = {a["question_id"]: a["value"] for a in record["answers"]}
    route = evaluate_path(survey["questions"], answers)
    current = record["current_question_id"]
    if action == "back":
        index = route["path"].index(current) if current else len(route["path"])
        if index == 0:
            raise ResponseError("已经是第一题。", 400)
        fields = {"current_question_id": route["path"][index - 1]}
    elif action == "next":
        if current is None:
            raise ResponseError("请确认并提交回答。", 400)
        question = next(q for q in survey["questions"] if q["id"] == current)
        answer = validate_answer(question, answer)
        # 修改前题后丢弃所有后续旧答案，重新决定路径，避免隐藏答案进入统计。
        prefix = route["path"][:route["path"].index(current)]
        answers = {qid: answers[qid] for qid in prefix}
        answers[current] = answer
        route = evaluate_path(survey["questions"], answers)
        fields = {"answers": [{"question_id": qid, "value": value}
                               for qid, value in route["answers"].items()],
                  "path": list(route["answers"]), "current_question_id": route["next_question_id"]}
    else:
        raise ResponseError("不支持的操作。", 400)
    result = database.responses.update_one(
        {"_id": record["_id"], "state": "draft", "version": version},
        {"$set": fields, "$inc": {"version": 1}})
    if not result.matched_count:
        raise ResponseError("填写进度已改变，请刷新后重试。")


def submit_response(database, survey_id, response_id, user_id, secret, version):
    record = own_response(database, survey_id, response_id, user_id, secret)
    # 同一记录重复提交返回原编号，即使问卷随后关闭，也不会生成另一份回答。
    if record["state"] == "submitted":
        return record["_id"]
    if type(version) is not int or version != record["version"]:
        raise ResponseError("填写进度已改变，请刷新后重试。")
    survey = open_survey(database, survey_id)
    check_progress(survey, record)
    if record["current_question_id"] is not None:
        raise ResponseError("请先完成逐题填写并进入确认页。", 400)
    route = evaluate_path(survey["questions"],
        {a["question_id"]: a["value"] for a in record["answers"]}, require_complete=True)
    submitted = existing_submission(database, survey, record["respondent_key"])
    if submitted:
        if submitted["_id"] == record["_id"]:
            return record["_id"]
        raise ResponseError("你已提交过这份问卷，每人只能填写一次。")
    # 单文档原子准入：与关闭/改截止操作排序，数据库时间到期即拒绝。
    # 准入成功后允许完成写入，即使创建者随后关闭；失败时原进度仍可重试。
    admitted = database.surveys.find_one_and_update({
        "_id": survey["_id"], "status": "published", "questions": survey["questions"],
        "is_anonymous": record["is_anonymous"], "allow_repeat": record["allow_repeat"],
        "$expr": {"$or": [{"$eq": ["$deadline", None]}, {"$gt": ["$deadline", "$$NOW"]}]},
    }, {"$currentDate": {"updated_at": True}}, return_document=ReturnDocument.AFTER)
    if admitted is None:
        raise ResponseError("问卷已关闭、截止或发生变化，请重新进入。", 410)
    try:
        result = database.responses.update_one(
            {"_id": record["_id"], "state": "draft", "version": version},
            {"$set": {"state": "submitted", "submitted_at": admitted["updated_at"],
                      "answers": [{"question_id": qid, "value": value}
                                  for qid, value in route["answers"].items()],
                      "path": route["path"]}, "$inc": {"version": 1}})
    except DuplicateKeyError as error:
        raise ResponseError("你已提交过这份问卷，每人只能填写一次。") from error
    if not result.matched_count:
        latest = own_response(database, survey_id, response_id, user_id, secret)
        if latest["state"] != "submitted":
            raise ResponseError("填写进度已改变，请刷新后重试。")
    return record["_id"]
