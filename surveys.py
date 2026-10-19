"""问卷草稿创建、按创建者读取，以及原子追加题目。"""

from datetime import datetime, timezone

from bson import ObjectId

from questions import validate_question, validate_questions


def _object_id(value):
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    return None


def build_survey_document(owner_id, title, description="", is_anonymous=False,
                          allow_repeat=False):
    """创建完整草稿文档；身份和初始状态由后端设置。"""
    if not isinstance(owner_id, ObjectId):
        raise ValueError("问卷创建者必须是有效的用户编号。")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("问卷标题不能为空。")
    title = title.strip()
    if len(title) > 200:
        raise ValueError("问卷标题不能超过 200 个字符。")
    if not isinstance(description, str):
        raise ValueError("问卷说明必须是文本。")
    description = description.strip()
    if len(description) > 5000:
        raise ValueError("问卷说明不能超过 5000 个字符。")
    if type(is_anonymous) is not bool or type(allow_repeat) is not bool:
        raise ValueError("匿名和重复提交设置必须是布尔值。")
    now = datetime.now(timezone.utc)
    return {
        "_id": ObjectId(),
        "owner_id": owner_id,
        "title": title,
        "description": description,
        "status": "draft",
        "is_anonymous": is_anonymous,
        "allow_repeat": allow_repeat,
        "questions": [],
        "created_at": now,
        "updated_at": now,
        "published_at": None,
        "deadline": None,
    }


def create_survey(database, owner_id, title, description="", is_anonymous=False,
                  allow_repeat=False):
    document = build_survey_document(
        owner_id, title, description, is_anonymous, allow_repeat,
    )
    if database.users.find_one({"_id": owner_id}, {"_id": 1}) is None:
        raise ValueError("问卷创建者不存在，请重新登录。")
    return database.surveys.insert_one(document).inserted_id


def get_owned_survey(database, survey_id, owner_id):
    survey_id, owner_id = _object_id(survey_id), _object_id(owner_id)
    if survey_id is None or owner_id is None:
        return None
    return database.surveys.find_one({"_id": survey_id, "owner_id": owner_id})


def list_owned_surveys(database, owner_id):
    owner_id = _object_id(owner_id)
    if owner_id is None:
        return []
    # 列表只需要题目数量，不加载每道题的选项和校验配置。
    projection = {
        "owner_id": 1, "title": 1, "description": 1, "status": 1,
        "is_anonymous": 1, "allow_repeat": 1, "questions.id": 1,
        "created_at": 1, "updated_at": 1, "published_at": 1, "deadline": 1,
    }
    return list(database.surveys.find({"owner_id": owner_id}, projection).sort([
        ("created_at", -1), ("_id", -1),
    ]))


def add_question(database, survey_id, owner_id, question):
    """单条更新同时检查所有权、草稿状态和编号唯一性，防止并发覆盖。"""
    question = validate_question(question)
    survey_id, owner_id = _object_id(survey_id), _object_id(owner_id)
    if survey_id is None or owner_id is None:
        return False
    result = database.surveys.update_one(
        {
            "_id": survey_id,
            "owner_id": owner_id,
            "status": "draft",
            "questions.id": {"$ne": question["id"]},
        },
        {
            "$push": {"questions": question},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
    )
    return result.modified_count == 1


def utcnow():
    """与 BSON 日期一致，按毫秒比较截止边界。"""
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def availability(survey, now=None):
    """截止状态实时推导，不依赖定时任务，也不改写文档状态。"""
    if survey["status"] != "published":
        return survey["status"]
    if survey["deadline"] is not None and survey["deadline"] <= (now or utcnow()):
        return "expired"
    return "published"


def set_survey_deadline(database, survey_id, owner_id, deadline):
    survey_id, owner_id = _object_id(survey_id), _object_id(owner_id)
    if survey_id is None or owner_id is None:
        return False
    now = utcnow()
    if deadline is not None:
        if not isinstance(deadline, datetime) or deadline.utcoffset() is None:
            raise ValueError("截止时间必须包含时区。")
        try:
            deadline = deadline.astimezone(timezone.utc)
        except (ValueError, OverflowError) as invalid:
            raise ValueError("截止时间超出有效范围。") from invalid
        deadline = deadline.replace(microsecond=deadline.microsecond // 1000 * 1000)
        if deadline <= now:
            raise ValueError("截止时间必须晚于当前时间。")
    result = database.surveys.update_one(
        {"_id": survey_id, "owner_id": owner_id, "status": {"$in": ["draft", "published"]}},
        {"$set": {"deadline": deadline, "updated_at": now}},
    )
    # 重复保存同一时间也算成功。
    return result.matched_count == 1


def publish_survey(database, survey_id, owner_id):
    survey = get_owned_survey(database, survey_id, owner_id)
    if survey is None or survey["status"] != "draft":
        return False
    if not validate_questions(survey["questions"]):
        raise ValueError("请至少添加一道有效题目后再发布。")
    now = utcnow()
    if survey["deadline"] is not None and survey["deadline"] <= now:
        raise ValueError("截止时间已过，请先修改截止时间。")
    # 发布的必须是刚刚校验过的题目与截止时间，避免并发修改绕过检查。
    result = database.surveys.update_one(
        {"_id": survey["_id"], "owner_id": survey["owner_id"], "status": "draft",
         "questions": survey["questions"], "deadline": survey["deadline"]},
        {"$set": {"status": "published", "published_at": now, "updated_at": now}},
    )
    return result.modified_count == 1


def close_survey(database, survey_id, owner_id):
    survey_id, owner_id = _object_id(survey_id), _object_id(owner_id)
    if survey_id is None or owner_id is None:
        return False
    result = database.surveys.update_one(
        {"_id": survey_id, "owner_id": owner_id, "status": "published"},
        {"$set": {"status": "closed", "updated_at": utcnow()}},
    )
    return result.modified_count == 1


def get_shared_survey(database, survey_id):
    survey_id = _object_id(survey_id)
    if survey_id is None:
        return None
    return database.surveys.find_one(
        {"_id": survey_id, "status": {"$in": ["published", "closed"]}},
        {"owner_id": 0},
    )
