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

# 已知可自动升级的上一版：所有 jump_rules 都被要求为空。
# 新版接受上一版全部合法文档，升级只放宽并细化该字段，不修改数据。
PRE_JUMP_SURVEY_VALIDATOR = deepcopy(SURVEY_VALIDATOR)


def _condition(operators, value_schema):
    return {"bsonType": "object", "required": ["operator", "value"],
            "additionalProperties": False,
            "properties": {"operator": {"enum": operators}, "value": value_schema}}


_FINITE_TYPE = {"bsonType": ["int", "long", "double"]}
_CONDITIONS = [
    _condition(["equals"], _TEXT),
    _condition(["contains_all", "equals_set"],
               {"bsonType": "array", "minItems": 1, "uniqueItems": True, "items": _TEXT}),
    None,  # 文本填空仍不支持跳转。
    {"oneOf": [_condition(["eq", "lt", "lte", "gt", "gte"], _FINITE_TYPE),
               _condition(["between"], {"bsonType": "array", "minItems": 2,
                                         "maxItems": 2, "items": _FINITE_TYPE})]},
]
for _variant, _rule_condition in zip(QUESTION_SCHEMA["oneOf"], _CONDITIONS):
    if _rule_condition is not None:
        _variant["properties"]["jump_rules"] = {
            "bsonType": "array", "items": {
                "bsonType": "object", "required": ["condition", "target_question_id"],
                "additionalProperties": False,
                "properties": {"condition": _rule_condition, "target_question_id": _TEXT},
            },
        }

# Phase two provenance is optional; all phase-one documents remain valid.
PRE_LIBRARY_SURVEY_VALIDATOR = deepcopy(SURVEY_VALIDATOR)
SOURCE_SCHEMA = {
    "bsonType": "object", "required": ["question_id", "version_id"],
    "additionalProperties": False,
    "properties": {"question_id": {"bsonType": "objectId"}, "version_id": {"bsonType": "objectId"}},
}
for _variant in QUESTION_SCHEMA["oneOf"]:
    _variant["properties"]["source"] = deepcopy(SOURCE_SCHEMA)
