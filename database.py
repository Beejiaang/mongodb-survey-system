"""MongoDB 连接和集合初始化。导入模块时不会连接或修改数据库。"""

import os

import click
from pymongo import MongoClient
from pymongo.errors import CollectionInvalid, PyMongoError


COLLECTION_NAMES = ("users", "surveys", "responses")
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


def initialize_database(database):
    """创建集合和唯一索引；可重复执行，不删除已有文档。"""
    for name in COLLECTION_NAMES:
        options = {}
        if name == "users":
            options = {
                "validator": USER_VALIDATOR,
                "validationLevel": "strict",
                "validationAction": "error",
            }
        try:
            database.create_collection(name, **options)
        except CollectionInvalid:
            # 已有用户集合必须符合当前结构，避免静默覆盖旧版规则。
            if name == "users":
                existing = database[name].options()
                if (
                    existing.get("validator") != USER_VALIDATOR
                    or existing.get("validationLevel", "strict") != "strict"
                    or existing.get("validationAction", "error") != "error"
                ):
                    raise ValueError("已有 users 集合的校验规则不同，需要先确认数据迁移方案。")
    database["users"].create_index("username", unique=True, name="username_unique")


@click.command("init-db")
def init_db_command():
    """创建问卷数据库的三个集合和用户约束。"""
    try:
        with create_client() as client:
            client.admin.command("ping")
            database = get_database(client)
            initialize_database(database)
            click.echo(f"数据库 {database.name} 已就绪：{', '.join(COLLECTION_NAMES)}")
    except (PyMongoError, ValueError) as error:
        raise click.ClickException(f"数据库初始化失败：{error}") from error
