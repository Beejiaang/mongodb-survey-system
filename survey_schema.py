"""问卷及嵌入题目的 MongoDB 结构校验；跨字段业务约束由应用检查。"""

from copy import deepcopy


_TEXT = {"bsonType": "string", "minLength": 1, "pattern": r"\S"}
_COUNT = {"bsonType": ["int", "long", "null"], "minimum": 0}
_NUMBER = {"bsonType": ["int", "long", "double", "null"]}
_OPTION = {
    "bsonType": "object",
    "required": ["id", "text"],
    "additionalProperties": False,
    "properties": {"id": _TEXT, "text": _TEXT},
}


def _question_schema(kind, rules, input_type=None):
    properties = {
        "id": _TEXT,
        "type": {"enum": [kind]},
        "title": _TEXT,
        "required": {"bsonType": "bool"},
        "validation": {
            "bsonType": "object",
            "additionalProperties": False,
            "properties": rules,
        },
        "jump_rules": {"bsonType": "array", "maxItems": 0},
    }
    if rules:
        properties["validation"]["required"] = list(rules)
    if input_type is None:
        properties["options"] = {
            "bsonType": "array", "minItems": 2, "items": _OPTION,
        }
    else:
        properties["input_type"] = {"enum": [input_type]}
    return {
        "bsonType": "object",
        "required": list(properties),
        "additionalProperties": False,
        "properties": deepcopy(properties),
    }


QUESTION_SCHEMA = {
    "oneOf": [
        _question_schema("single_choice", {}),
        _question_schema("multiple_choice", {
            "min_choices": _COUNT, "max_choices": _COUNT,
        }),
        _question_schema("fill_blank", {
            "min_length": _COUNT, "max_length": _COUNT,
        }, "text"),
        _question_schema("fill_blank", {
            "min_value": _NUMBER, "max_value": _NUMBER,
            "integer_only": {"bsonType": "bool"},
        }, "number"),
    ],
}

_SURVEY_PROPERTIES = {
    "_id": {"bsonType": "objectId"},
    "owner_id": {"bsonType": "objectId"},
    "title": {**_TEXT, "maxLength": 200},
    "description": {"bsonType": "string", "maxLength": 5000},
    "status": {"enum": ["draft", "published", "closed"]},
    "is_anonymous": {"bsonType": "bool"},
    "allow_repeat": {"bsonType": "bool"},
    "questions": {"bsonType": "array", "items": QUESTION_SCHEMA},
    "created_at": {"bsonType": "date"},
    "updated_at": {"bsonType": "date"},
    "published_at": {"bsonType": ["date", "null"]},
    "deadline": {"bsonType": ["date", "null"]},
}

SURVEY_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": list(_SURVEY_PROPERTIES),
        "additionalProperties": False,
        "properties": _SURVEY_PROPERTIES,
    },
}
