"""Reusable questions, immutable versions, directed sharing and pinned banks."""
from copy import deepcopy
from uuid import uuid4

from bson import ObjectId
from pymongo import ReturnDocument

from jumps import question_revision
from questions import validate_question
from surveys import _object_id, add_question, get_owned_survey, utcnow
from users import find_user_by_username


class LibraryError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _text(value, label, maximum=200, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value.strip()) > maximum:
        raise LibraryError(f"{label}须为{'0' if empty else '1'}～{maximum}字符。")
    return value.strip()


def _user(database, user_id):
    if not isinstance(user_id, ObjectId) or not database.users.find_one({"_id": user_id}, {"_id": 1}):
        raise LibraryError("请重新登录。", 401)


def get_item(database, item_id, user_id, *, owner_only=False):
    oid = _object_id(item_id)
    query = {"_id": oid, "owner_id": user_id} if owner_only else {
        "_id": oid, "$or": [{"owner_id": user_id}, {"shared_with": user_id}]}
    item = database.question_items.find_one(query) if oid else None
    if item is None:
        raise LibraryError("题目不存在或未获授权。", 404)
    return item


def get_version(database, version_id, user_id):
    oid = _object_id(version_id)
    version = database.question_versions.find_one({"_id": oid}) if oid else None
    if version is None:
        raise LibraryError("版本不存在或未获授权。", 404)
    return get_item(database, version["question_id"], user_id), version


def list_items(database, user_id):
    return list(database.question_items.find({"$or": [{"owner_id": user_id}, {"shared_with": user_id}]}).sort([
        ("updated_at", -1), ("_id", -1)]))


def list_versions(database, item_id, user_id):
    item = get_item(database, item_id, user_id)
    return list(database.question_versions.find({"question_id": item["_id"]}).sort("number", -1))


def _content(question):
    if not isinstance(question, dict):
        raise LibraryError("题目配置必须是对象。")
    content = deepcopy(question)
    content.pop("source", None)
    content["jump_rules"] = []
    return validate_question(content)


def create_item(database, owner_id, question, *, name=None, forked_from=None):
    _user(database, owner_id)
    content = _content(question)
    name = _text(content["title"] if name is None else name, "题目名称")
    now = utcnow()
    item_id, version_id = ObjectId(), ObjectId()
    version = {"_id": version_id, "question_id": item_id, "number": 1, "content": content,
               "parent_version_id": forked_from, "previous_version_id": None,
               "restored_from_version_id": None, "change_note": "首次保存" if forked_from is None else "从共享版本派生",
               "created_by": owner_id, "created_at": now}
    # Never leave a current pointer to a version that has not been inserted.
    database.question_versions.insert_one(version)
    database.question_items.insert_one({
        "_id": item_id, "owner_id": owner_id, "name": name,
        "current_version_id": version_id, "current_version_number": 1, "next_version": 1,
        "shared_with": [], "forked_from_version_id": forked_from, "created_at": now, "updated_at": now,
    })
    return item_id


def create_version(database, item_id, owner_id, base_version_id, question=None, *, note="", restore=False):
    item = get_item(database, item_id, owner_id, owner_only=True)
    _, base = get_version(database, base_version_id, owner_id)
    if base["question_id"] != item["_id"]:
        raise LibraryError("来源版本不属于当前题目。")
    note = _text(note, "修改说明", 500, empty=True)
    content = deepcopy(base["content"]) if restore else _content(question)
    allocation = database.question_items.find_one_and_update(
        {"_id": item["_id"], "owner_id": owner_id}, {"$inc": {"next_version": 1}},
        return_document=ReturnDocument.AFTER,
    )
    number, version_id, now = allocation["next_version"], ObjectId(), utcnow()
    database.question_versions.insert_one({
        "_id": version_id, "question_id": item["_id"], "number": number,
        "content": content, "parent_version_id": base["_id"],
        "previous_version_id": allocation["current_version_id"],
        "restored_from_version_id": base["_id"] if restore else None,
        "change_note": note or (f"恢复自v{base['number']}" if restore else "新版本"),
        "created_by": owner_id, "created_at": now,
    })
    # A slower smaller version must not replace a concurrently completed newer default.
    database.question_items.update_one(
        {"_id": item["_id"], "owner_id": owner_id, "current_version_number": {"$lt": number}},
        {"$set": {"current_version_id": version_id, "current_version_number": number, "updated_at": now}},
    )
    return version_id


def fork_version(database, version_id, user_id):
    _, version = get_version(database, version_id, user_id)
    return create_item(database, user_id, version["content"], forked_from=version["_id"])


def save_survey_question(database, survey_id, user_id, question_id):
    survey = get_owned_survey(database, survey_id, user_id)
    question = next((q for q in survey["questions"] if q["id"] == question_id), None) if survey else None
    if question is None:
        raise LibraryError("问卷题目不存在。", 404)
    # Do not rewrite legacy surveys or invalidate their existing response revisions.
    return create_item(database, user_id, question)


def import_version(database, survey_id, user_id, version_id):
    _, version = get_version(database, version_id, user_id)
    survey = get_owned_survey(database, survey_id, user_id)
    if survey is None:
        raise LibraryError("问卷不存在。", 404)
    if survey["status"] != "draft":
        raise LibraryError("只有草稿可以选入题目版本。", 409)
    question = deepcopy(version["content"])
    question["id"] = uuid4().hex
    question["source"] = {"question_id": version["question_id"], "version_id": version["_id"]}
    if not add_question(database, survey["_id"], user_id, question):
        raise LibraryError("问卷已发布或发生变化，请刷新后重试。", 409)
    return question["id"]


def share_item(database, item_id, owner_id, username, *, revoke=False):
    item = get_item(database, item_id, owner_id, owner_only=True)
    try:
        target = find_user_by_username(database, username)
    except ValueError as error:
        raise LibraryError(str(error)) from error
    if target is None or target["_id"] == owner_id:
        raise LibraryError("请选择一个已注册的其他用户。")
    database.question_items.update_one(
        {"_id": item["_id"], "owner_id": owner_id},
        {"$pull" if revoke else "$addToSet": {"shared_with": target["_id"]},
         "$set": {"updated_at": utcnow()}},
    )


def version_difference(before, after):
    def fields(q):
        result = {"题型": q["type"] + (" / " + q["input_type"] if "input_type" in q else ""),
                  "题目内容": q["title"], "是否必答": "是" if q["required"] else "否",
                  "选项": "；".join(o["text"] for o in q.get("options", [])) or "无"}
        labels = {"min_choices": "最少选择数", "max_choices": "最多选择数", "min_length": "最少字符数",
                  "max_length": "最多字符数", "min_value": "最小值", "max_value": "最大值", "integer_only": "必须为整数"}
        for k, v in q["validation"].items():
            result[labels[k]] = "不限" if v is None else ("是" if v is True else "否" if v is False else str(v))
        return result
    left, right = fields(before), fields(after)
    return [{"field": key, "before": left.get(key, "不适用"), "after": right.get(key, "不适用")}
            for key in dict.fromkeys([*left, *right]) if left.get(key) != right.get(key)]


def usage(database, item_id, user_id, version_id=None):
    item = get_item(database, item_id, user_id)
    match = {"source.question_id": item["_id"]}
    if version_id is not None:
        _, version = get_version(database, version_id, user_id)
        if version["question_id"] != item["_id"]:
            raise LibraryError("版本不属于当前题目。")
        match["source.version_id"] = version["_id"]
    query = {"questions": {"$elemMatch": match}}
    own = list(database.surveys.find({**query, "owner_id": user_id},
                                    {"title": 1, "status": 1, "questions": 1}).sort("created_at", -1))
    other_count = database.surveys.count_documents({**query, "owner_id": {"$ne": user_id}}) if item["owner_id"] == user_id else None
    return {"surveys": own, "other_count": other_count}


def create_bank(database, owner_id, name, description=""):
    _user(database, owner_id)
    now = utcnow()
    return database.question_banks.insert_one({"_id": ObjectId(), "owner_id": owner_id,
        "name": _text(name, "题库名称"), "description": _text(description, "说明", 500, empty=True),
        "entries": [], "created_at": now, "updated_at": now}).inserted_id


def get_bank(database, bank_id, owner_id):
    bank = database.question_banks.find_one({"_id": _object_id(bank_id), "owner_id": owner_id})
    if bank is None:
        raise LibraryError("题库不存在。", 404)
    return bank


def bank_revision(bank):
    return question_revision(bank["entries"])


def change_bank(database, bank_id, owner_id, action, version_id, revision):
    bank = get_bank(database, bank_id, owner_id)
    if revision != bank_revision(bank):
        raise LibraryError("题库已变化，请刷新后重试。", 409)
    entries = deepcopy(bank["entries"])
    if action == "remove":
        entries = [e for e in entries if e["version_id"] != _object_id(version_id)]
        if entries == bank["entries"]:
            raise LibraryError("题库条目不存在。", 404)
    elif action == "put":
        _, version = get_version(database, version_id, owner_id)
        entry = {"question_id": version["question_id"], "version_id": version["_id"]}
        entries = [e for e in entries if e["question_id"] != version["question_id"]]
        entries.append(entry)
    else:
        raise LibraryError("题库操作无效。")
    result = database.question_banks.update_one(
        {"_id": bank["_id"], "owner_id": owner_id, "entries": bank["entries"]},
        {"$set": {"entries": entries, "updated_at": utcnow()}},
    )
    if not result.matched_count:
        raise LibraryError("题库已变化，请刷新后重试。", 409)
