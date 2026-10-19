"""问卷草稿创建、按创建者读取，以及原子追加题目。"""

from datetime import datetime, timezone

from bson import ObjectId

from questions import validate_question


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
