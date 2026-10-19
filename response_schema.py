"""填写进度与正式回答共用 responses 集合。"""

_TEXT = {"bsonType": "string", "minLength": 1}
_PROPERTIES = {
    "_id": {"bsonType": "objectId"},
    "survey_id": {"bsonType": "objectId"},
    "respondent_key": {"bsonType": "string", "pattern": "^[0-9a-f]{64}$"},
    "user_id": {"bsonType": ["objectId", "null"]},
    "is_anonymous": {"bsonType": "bool"},
    "allow_repeat": {"bsonType": "bool"},
    "state": {"enum": ["draft", "submitted"]},
    "question_revision": {"bsonType": "string", "pattern": "^[0-9a-f]{64}$"},
    "version": {"bsonType": ["int", "long"], "minimum": 0},
    "answers": {"bsonType": "array", "items": {
        "bsonType": "object", "required": ["question_id", "value"], "additionalProperties": False,
        "properties": {"question_id": _TEXT, "value": {"oneOf": [
            {"bsonType": ["string", "int", "long", "double", "null"]},
            {"bsonType": "array", "uniqueItems": True, "items": _TEXT}
        ]}}
    }},
    "path": {"bsonType": "array", "uniqueItems": True, "items": _TEXT},
    "current_question_id": {"bsonType": ["string", "null"]},
    "created_at": {"bsonType": "date"},
    "submitted_at": {"bsonType": ["date", "null"]},
}
RESPONSE_VALIDATOR = {"$and": [
    {"$jsonSchema": {"bsonType": "object", "required": list(_PROPERTIES),
                     "additionalProperties": False, "properties": _PROPERTIES}},
    {"$or": [{"is_anonymous": True, "user_id": {"$type": "null"}},
             {"is_anonymous": False, "user_id": {"$type": "objectId"}}]},
    {"$or": [{"state": "draft", "submitted_at": {"$type": "null"}},
             {"state": "submitted", "submitted_at": {"$type": "date"},
              "current_question_id": {"$type": "null"}}]},
]}
