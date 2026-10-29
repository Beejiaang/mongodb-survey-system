"""Reusable questions, immutable versions, directed sharing and pinned banks."""
from copy import deepcopy
from uuid import uuid4

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from jumps import question_revision
from questions import validate_question
from surveys import _object_id, add_question, availability, get_owned_survey, utcnow
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


def _grant_query(user_id, *, version_id=None, survey_id=None, user_wide=False):
    query = {"requester_id": user_id, "status": "approved"}
    if version_id is not None:
        query["version_id"] = _object_id(version_id)
    if user_wide:
        query["survey_id"] = None
    elif survey_id is not None:
        query["survey_id"] = {"$in": [None, _object_id(survey_id)]}
    return query


def branch_origin(database, item):
    source = database.question_versions.find_one({"_id": item["forked_from_version_id"]}) if item["forked_from_version_id"] else None
    if source:
        original = database.question_items.find_one({"_id": source["question_id"]})
        if original and original["owner_id"] != item["owner_id"]:
            return original, source
    return None


def require_branch_permission(database, item):
    origin = branch_origin(database, item)
    if origin and not database.question_shares.find_one(_grant_query(
            item["owner_id"], version_id=origin[1]["_id"], user_wide=True)):
        raise LibraryError("修改分支需要来源版本的有效面向用户授权。", 403)


def require_fork_permission(database, version_id, user_id):
    item, version = get_version(database, version_id, user_id)
    if branch_origin(database, item):
        raise LibraryError("请在当前分支内新增版本，不能复制分支或转授。", 403)
    if item["owner_id"] != user_id and not database.question_shares.find_one(
            _grant_query(user_id, version_id=version["_id"], user_wide=True)):
        raise LibraryError("修改分支需要面向用户授权；部分问卷授权仅允许复用原版。", 403)
    return item, version


def get_item(database, item_id, user_id, *, owner_only=False):
    oid = _object_id(item_id)
    item = database.question_items.find_one({"_id": oid}) if oid else None
    if item is None or (item["owner_id"] != user_id and (owner_only or not database.question_shares.find_one({
            "question_id": oid, "requester_id": user_id, "status": "approved"}))):
        raise LibraryError("题目不存在或未获授权。", 404)
    return item


def get_version(database, version_id, user_id, *, survey_id=None):
    oid = _object_id(version_id)
    version = database.question_versions.find_one({"_id": oid}) if oid else None
    if version is None:
        raise LibraryError("版本不存在或未获授权。", 404)
    item = get_item(database, version["question_id"], user_id)
    if item["owner_id"] != user_id:
        query = _grant_query(user_id, version_id=oid, survey_id=survey_id)
        if not database.question_shares.find_one(query):
            raise LibraryError("此版本未获目标问卷的使用授权。", 404)
    return item, version


def list_items(database, user_id, *, survey_id=None):
    query = _grant_query(user_id, survey_id=survey_id)
    ids = database.question_shares.distinct("question_id", query)
    items = list(database.question_items.find({"$or": [{"owner_id": user_id}, {"_id": {"$in": ids}}]}).sort([
        ("updated_at", -1), ("_id", -1)]))
    if survey_id is not None:
        allowed = []
        for item in items:
            try:
                require_branch_permission(database, item)
                allowed.append(item)
            except LibraryError:
                pass
        return allowed
    return items


def list_versions(database, item_id, user_id, *, survey_id=None):
    item = get_item(database, item_id, user_id)
    query = {"question_id": item["_id"]}
    if item["owner_id"] != user_id:
        grants = {**_grant_query(user_id, survey_id=survey_id), "question_id": item["_id"]}
        query["_id"] = {"$in": database.question_shares.distinct("version_id", grants)}
    return list(database.question_versions.find(query).sort("number", -1))


def _content(question):
    if not isinstance(question, dict):
        raise LibraryError("题目配置必须是对象。")
    content = deepcopy(question)
    content.pop("source", None)
    content["jump_rules"] = []
    return validate_question(content)


def create_item(database, owner_id, question, *, name=None, forked_from=None, original_use=None, initial_note=None):
    _user(database, owner_id)
    number = 1
    if forked_from is not None:
        source_item, source = require_fork_permission(database, forked_from, owner_id)
        number = source["number"] + 1
        if source_item["owner_id"] != owner_id and name is None:
            username = database.users.find_one({"_id": owner_id})["username"]
            name = (source_item["name"][:150] + " · " + username + "分支")[:200]
    if isinstance(question, dict) and question.get("source"):
        source_item = get_item(database, question["source"]["question_id"], owner_id, owner_only=True)
        if branch_origin(database, source_item):
            raise LibraryError("修改分支不能另存以移除来源授权。", 403)
    content = _content(question)
    name = _text(content["title"][:200] if name is None else name, "题目名称")
    now = utcnow()
    item_id, version_id = ObjectId(), ObjectId()
    version = {"_id": version_id, "question_id": item_id, "number": number, "content": content,
               "parent_version_id": forked_from, "previous_version_id": None,
               "restored_from_version_id": None, "change_note": initial_note or ("首次保存" if forked_from is None else "从共享版本派生"),
               "created_by": owner_id, "created_at": now}
    # Never leave a current pointer to a version that has not been inserted.
    database.question_versions.insert_one(version)
    database.question_items.insert_one({
        "_id": item_id, "owner_id": owner_id, "name": name,
        "current_version_id": version_id, "current_version_number": number, "next_version": number,
        "shared_with": [], "forked_from_version_id": forked_from, "created_at": now, "updated_at": now,
        "original_use": {**original_use, "version_id": version_id} if original_use else None,
    })
    return item_id


def create_version(database, item_id, owner_id, base_version_id, question=None, *, note="", restore=False):
    item = get_item(database, item_id, owner_id, owner_only=True)
    require_branch_permission(database, item)
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


def fork_version(database, version_id, user_id, question=None, *, note=""):
    _, version = require_fork_permission(database, version_id, user_id)
    note = _text(note, "修改说明", 500, empty=True)
    iid = create_item(database, user_id, version["content"] if question is None else question,
                      forked_from=version["_id"], initial_note=note or None)
    return iid


def save_survey_question(database, survey_id, user_id, question_id):
    survey = get_owned_survey(database, survey_id, user_id)
    question = next((q for q in survey["questions"] if q["id"] == question_id), None) if survey else None
    if question is None:
        raise LibraryError("问卷题目不存在。", 404)
    if question.get("source"):
        source_item = get_item(database, question["source"]["question_id"], user_id, owner_only=True)
        if branch_origin(database, source_item):
            raise LibraryError("修改分支不能另存以移除来源授权。", 403)
    # Do not rewrite legacy surveys or invalidate their existing response revisions.
    return create_item(database, user_id, question,
                       original_use={"survey_id": survey["_id"], "instance_id": question["id"]})


def import_version(database, survey_id, user_id, version_id):
    item, version = get_version(database, version_id, user_id, survey_id=survey_id)
    require_branch_permission(database, item)
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


def replace_survey_version(database, survey_id, user_id, question_id, version_id, revision):
    item, version = get_version(database, version_id, user_id, survey_id=survey_id)
    require_branch_permission(database, item)
    survey = _share_survey(database, survey_id, user_id)
    if question_revision(survey["questions"]) != revision:
        raise LibraryError("问卷已变化，请刷新后重试。", 409)
    questions = deepcopy(survey["questions"])
    old = next((q for q in questions if q["id"] == question_id), None)
    if old is None:
        raise LibraryError("问卷题目不存在。", 404)
    origin = branch_origin(database, item)
    allowed_items = {item["_id"]}
    if origin:
        allowed_items.add(origin[0]["_id"])
    if old.get("source", {}).get("question_id") not in allowed_items:
        raise LibraryError("只能替换当前题目或其原题的引用。")
    replacement = deepcopy(version["content"])
    replacement["id"] = old["id"]
    replacement["source"] = {"question_id": item["_id"], "version_id": version["_id"]}
    questions[questions.index(old)] = replacement
    # Changed options or question types can invalidate conditions elsewhere in the survey.
    for q in questions:
        q["jump_rules"] = []
    result = database.surveys.update_one({"_id": survey["_id"], "owner_id": user_id,
        "status": "draft", "questions": survey["questions"]},
        {"$set": {"questions": questions, "updated_at": utcnow()}})
    if not result.matched_count:
        raise LibraryError("问卷已变化或已发布，请刷新后重试。", 409)


def share_item(database, item_id, owner_id, username, *, survey_id=None, version_id,
               scope="surveys", survey_ids=None, revoke=False):
    item = get_item(database, item_id, owner_id, owner_only=True)
    if branch_origin(database, item):
        raise LibraryError("修改分支不能转授给其他用户。", 403)
    try:
        target = find_user_by_username(database, username)
    except ValueError as error:
        raise LibraryError(str(error)) from error
    if target is None or target["_id"] == owner_id:
        raise LibraryError("请选择一个已注册的其他用户。")
    _, version = get_version(database, version_id, owner_id)
    if version["question_id"] != item["_id"]:
        raise LibraryError("版本不属于当前题目。")
    targets = _share_targets(database, target["_id"], scope, survey_id, survey_ids, draft_only=not revoke)
    records = []
    if revoke:
        for survey in targets:
            row = database.question_shares.find_one({"version_id": version["_id"], "requester_id": target["_id"],
                "survey_id": survey["_id"] if survey else None, "status": "approved"})
            if row is None:
                raise LibraryError("有效授权不存在。", 404)
            records.append(row)
        ids = [decide_share(database, row["_id"], owner_id, "revoke") for row in records]
    else:
        ids = [_new_share(database, item, version, target["_id"], survey, approved=True) for survey in targets]
    return ids[0] if len(ids) == 1 else ids


def _share_targets(database, requester_id, scope, survey_id=None, survey_ids=None, *, draft_only=True):
    if scope == "user":
        return [None]
    if scope != "surveys":
        raise LibraryError("请选择面向用户或部分问卷。")
    ids = survey_ids if survey_ids is not None else [survey_id]
    if not isinstance(ids, (list, tuple)) or not 1 <= len(ids) <= 50:
        raise LibraryError("部分问卷授权须选择1～50份问卷。")
    objects = [_object_id(sid) for sid in ids]
    if None in objects or len(set(objects)) != len(objects):
        raise LibraryError("问卷编号无效或重复。")
    return [_share_survey(database, sid, requester_id, draft_only=draft_only) for sid in objects]


def _share_survey(database, survey_id, user_id, *, draft_only=True):
    survey = get_owned_survey(database, survey_id, user_id)
    if survey is None:
        raise LibraryError("目标问卷不存在或不属于需求者。", 404)
    if draft_only and survey["status"] != "draft":
        raise LibraryError("只能为草稿问卷申请或开放使用权。", 409)
    return survey


def _new_share(database, item, version, requester_id, survey, *, approved=False, message=""):
    query = {"version_id": version["_id"], "requester_id": requester_id, "survey_id": survey["_id"] if survey else None}
    existing = database.question_shares.find_one({**query, "status": {"$in": ["pending", "approved"]}})
    if existing:
        if approved and existing["status"] == "pending":
            decide_share(database, existing["_id"], item["owner_id"], "approve")
        return existing["_id"]
    now = utcnow()
    record = {"_id": ObjectId(), **query, "question_id": item["_id"], "owner_id": item["owner_id"],
              "status": "approved" if approved else "pending", "origin": "direct" if approved else "request",
              "message": message, "created_at": now, "updated_at": now,
              "decided_at": now if approved else None, "decided_by": item["owner_id"] if approved else None}
    try:
        database.question_shares.insert_one(record)
    except DuplicateKeyError:
        # Concurrent requests share one active record; a direct grant can approve its winner.
        return _new_share(database, item, version, requester_id, survey, approved=approved, message=message)
    return record["_id"]


def request_share(database, item_id, requester_id, survey_id=None, *, version_number=1, message="",
                  scope="surveys", survey_ids=None):
    _user(database, requester_id)
    targets = _share_targets(database, requester_id, scope, survey_id, survey_ids)
    if isinstance(version_number, bool) or not isinstance(version_number, int) or version_number < 1:
        raise LibraryError("版本号须为正整数。")
    message = _text(message, "申请说明", 500, empty=True)
    item = database.question_items.find_one({"_id": _object_id(item_id)})
    version = database.question_versions.find_one({"question_id": _object_id(item_id), "number": version_number})
    if not item or not version or item["owner_id"] == requester_id:
        raise LibraryError("申请的题目版本不存在或属于自己。", 404)
    if branch_origin(database, item):
        raise LibraryError("修改分支不能转授给其他用户。", 403)
    ids = [_new_share(database, item, version, requester_id, survey, message=message) for survey in targets]
    return ids[0] if len(ids) == 1 else ids


def decide_share(database, share_id, owner_id, action):
    states = {"approve": ("pending", "approved"), "reject": ("pending", "rejected"), "revoke": ("approved", "revoked")}
    if action not in states:
        raise LibraryError("审批操作无效。")
    record = database.question_shares.find_one({"_id": _object_id(share_id), "owner_id": owner_id})
    if record is None:
        raise LibraryError("申请或授权不存在。", 404)
    get_item(database, record["question_id"], owner_id, owner_only=True)
    before, after = states[action]
    if after == "approved":
        _user(database, record["requester_id"])
    if after == "approved" and record["survey_id"] is not None:
        _share_survey(database, record["survey_id"], record["requester_id"])
    now = utcnow()
    if not database.question_shares.update_one({"_id": record["_id"], "owner_id": owner_id, "status": before},
            {"$set": {"status": after, "decided_by": owner_id, "decided_at": now, "updated_at": now}}).matched_count:
        raise LibraryError("申请状态已变化，请刷新后重试。", 409)
    return record["_id"]


def share_records(database, user_id):
    rows = list(database.question_shares.find({"$or": [{"owner_id": user_id}, {"requester_id": user_id}]}).sort([
        ("created_at", -1), ("_id", -1)]))
    for row in rows:
        row["requester"] = database.users.find_one({"_id": row["requester_id"]}, {"username": 1})
        row["author"] = database.users.find_one({"_id": row["owner_id"]}, {"username": 1})
        row["survey"] = database.surveys.find_one({"_id": row["survey_id"], "owner_id": row["requester_id"]}, {"title": 1}) if row["survey_id"] is not None else None
        row["version"] = database.question_versions.find_one({"_id": row["version_id"]}, {"number": 1})
    return rows


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
    branches = _derived_item_ids(database, item, match.get("source.version_id"))
    query = {"questions": {"$elemMatch": match}}
    if branches:
        query = {"$or": [query, {"questions.source.question_id": {"$in": branches}}]}
    original = item["original_use"]
    if original and (version_id is None or original["version_id"] == _object_id(version_id)):
        old = database.surveys.find_one({"_id": original["survey_id"]})
        instance = next((q for q in old["questions"] if q["id"] == original["instance_id"]), None) if old else None
        version = database.question_versions.find_one({"_id": original["version_id"]})
        if instance and version and _content(instance) == version["content"]:
            query = {"$or": [query, {"_id": original["survey_id"]}]}
    own = list(database.surveys.find({**query, "owner_id": user_id},
                                    {"title": 1, "status": 1, "deadline": 1, "questions": 1}).sort("created_at", -1))
    others = list(database.surveys.find({**query, "owner_id": {"$ne": user_id}},
                                       {"title": 1, "status": 1, "deadline": 1}).sort("created_at", -1)) if item["owner_id"] == user_id else []
    return {"surveys": own, "other_surveys": others,
            "other_count": len(others) if item["owner_id"] == user_id else None}


def _derived_item_ids(database, item, version_id=None):
    versions = [version_id] if version_id else database.question_versions.distinct("_id", {"question_id": item["_id"]})
    return database.question_items.distinct("_id", {"forked_from_version_id": {"$in": versions}})


def preview_referenced_survey(database, item_id, user_id, survey_id):
    """Authors may read an actual reference only while its survey is open."""
    item = get_item(database, item_id, user_id, owner_only=True)
    survey = database.surveys.find_one({"_id": _object_id(survey_id)})
    if survey is None:
        raise LibraryError("引用问卷不存在或不可访问。", 404)
    related_ids = {item["_id"], *_derived_item_ids(database, item)}
    referenced = any(q.get("source", {}).get("question_id") in related_ids for q in survey["questions"])
    original = item["original_use"]
    if not referenced and original and original["survey_id"] == survey["_id"]:
        instance = next((q for q in survey["questions"] if q["id"] == original["instance_id"]), None)
        version = database.question_versions.find_one({"_id": original["version_id"]})
        referenced = bool(instance and version and _content(instance) == version["content"])
    if not referenced or survey["status"] == "draft":
        raise LibraryError("引用问卷不存在或不可访问。", 404)
    if availability(survey) != "published":
        raise LibraryError("引用问卷已截止或关闭，不能查看内容。", 410)
    return survey


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
    return question_revision([bank["name"], bank["description"], bank["entries"]])


def change_bank(database, bank_id, owner_id, action, version_id, revision, *, name=None, description=""):
    bank = get_bank(database, bank_id, owner_id)
    if revision != bank_revision(bank):
        raise LibraryError("题库已变化，请刷新后重试。", 409)
    if action in ("rename", "delete"):
        query = {"_id": bank["_id"], "owner_id": owner_id, "entries": bank["entries"],
                 "name": bank["name"], "description": bank["description"]}
        if action == "delete":
            changed = database.question_banks.delete_one(query).deleted_count
        else:
            changed = database.question_banks.update_one(query, {"$set": {
                "name": _text(name, "题库名称"), "description": _text(description, "说明", 500, empty=True),
                "updated_at": utcnow()}}).matched_count
        if not changed:
            raise LibraryError("题库已变化，请刷新后重试。", 409)
        return
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
