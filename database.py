"""MongoDB 连接和集合初始化。导入模块时不会连接或修改数据库。"""

import os

import click
from flask import current_app, g
from pymongo import MongoClient
from pymongo.errors import CollectionInvalid, PyMongoError

from survey_schema import SURVEY_VALIDATOR, PRE_JUMP_SURVEY_VALIDATOR, PRE_LIBRARY_SURVEY_VALIDATOR
from response_schema import RESPONSE_VALIDATOR
from library_schema import LIBRARY_VALIDATORS


COLLECTION_NAMES = ("users", "surveys", "responses", *LIBRARY_VALIDATORS)
USER_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "username", "password_hash", "created_at"],
        "additionalProperties": False,
        "properties": {
            "_id": {"bsonType": "objectId"},
            "username": {
                "bsonType": "string",
                "pattern": r"\A[a-z0-9_]{3,30}\z",
                "description": "3-30 lowercase ASCII letters, digits or underscores",
            },
            "password_hash": {
                "bsonType": "string",
                "pattern": r"\Ascrypt:[0-9]+:[0-9]+:[0-9]+\$[A-Za-z0-9]+\$[0-9a-f]{128}\z",
                "description": "Werkzeug scrypt password hash, never a plaintext password",
            },
            "created_at": {"bsonType": "date"},
        },
    }
}


def create_client(uri=None):
    return MongoClient(
        uri or os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017"),
        tz_aware=True,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
    )


def get_database(client, name=None):
    return client[name or os.environ.get("MONGODB_DB_NAME", "survey_system")]


def get_request_database():
    """同一网页请求复用连接，请求结束后关闭。"""
    if "mongo_client" not in g:
        g.mongo_client = create_client(current_app.config["MONGODB_URI"])
    return get_database(g.mongo_client, current_app.config["MONGODB_DB_NAME"])


def close_request_client(error=None):
    client = g.pop("mongo_client", None)
    if client is not None:
        client.close()


def initialize_database(database):
    """创建集合和索引；只为无规则的空问卷集合补上校验，不覆盖旧数据。"""
    for name in COLLECTION_NAMES:
        options = {}
        validator = {"users": USER_VALIDATOR, "surveys": SURVEY_VALIDATOR,
                     "responses": RESPONSE_VALIDATOR, **LIBRARY_VALIDATORS}.get(name)
        if validator is not None:
            options = {
                "validator": validator,
                "validationLevel": "strict",
                "validationAction": "error",
            }
        try:
            database.create_collection(name, **options)
        except CollectionInvalid:
            if validator is not None:
                existing = database[name].options()
                if (name == "surveys" and existing.get("validator") in (PRE_JUMP_SURVEY_VALIDATOR, PRE_LIBRARY_SURVEY_VALIDATOR)
                        and existing.get("validationLevel", "strict") == "strict"
                        and existing.get("validationAction", "error") == "error"):
                    database.command("collMod", name, **options)
                    continue
                # 上一步只建立空 surveys 集合，可安全补上本步骤的结构。
                if (name in ("surveys", "responses") and not existing.get("validator")
                        and database[name].find_one({}, {"_id": 1}) is None):
                    database.command("collMod", name, **options)
                    continue
                if (
                    existing.get("validator") != validator
                    or existing.get("validationLevel", "strict") != "strict"
                    or existing.get("validationAction", "error") != "error"
                ):
                    raise ValueError(f"已有 {name} 集合的校验规则不同，需要先确认数据迁移方案。")
    database["users"].create_index("username", unique=True, name="username_unique")
    database["surveys"].create_index(
        [("owner_id", 1), ("created_at", -1), ("_id", -1)],
        name="owner_created_at",
    )

    database.responses.create_index(
        [("survey_id", 1), ("respondent_key", 1)], unique=True,
        partialFilterExpression={"state": "submitted", "allow_repeat": False},
        name="one_submission_per_user",
    )
    database.responses.create_index(
        [("survey_id", 1), ("respondent_key", 1), ("state", 1), ("created_at", -1)],
        name="respondent_progress",
    )
    database.responses.create_index(
        [("survey_id", 1), ("state", 1), ("submitted_at", -1)], name="survey_submissions",
    )


    database.surveys.create_index([("owner_id", 1), ("questions.source.version_id", 1)], name="owner_version_uses")
    database.question_items.create_index([("owner_id", 1), ("created_at", -1)], name="item_owner_created")
    database.question_items.create_index([("shared_with", 1), ("created_at", -1)], name="item_shared_created")
    database.question_versions.create_index([("question_id", 1), ("number", 1)], unique=True, name="question_version_unique")
    database.question_banks.create_index([("owner_id", 1), ("created_at", -1)], name="bank_owner_created")
    database.question_shares.create_index(
        [("version_id", 1), ("requester_id", 1), ("survey_id", 1)], unique=True,
        partialFilterExpression={"status": {"$in": ["pending", "approved"]}}, name="active_survey_share")
    database.question_shares.create_index([("owner_id", 1), ("created_at", -1)], name="share_owner_created")
    database.question_shares.create_index([("requester_id", 1), ("created_at", -1)], name="share_requester_created")


@click.command("init-db")
def init_db_command():
    """创建问卷和题库集合、数据约束和查询索引。"""
    try:
        with create_client() as client:
            client.admin.command("ping")
            database = get_database(client)
            initialize_database(database)
            click.echo(f"数据库 {database.name} 已就绪：{', '.join(COLLECTION_NAMES)}")
    except (PyMongoError, ValueError) as error:
        raise click.ClickException(f"数据库初始化失败：{error}") from error
