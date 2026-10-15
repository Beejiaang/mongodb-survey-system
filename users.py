"""用户文档构建和查询；注册与登录共同使用用户名规范化函数。"""

import re
from datetime import datetime, timezone

from bson import ObjectId
from werkzeug.security import check_password_hash, generate_password_hash


def normalize_username(value):
    if not isinstance(value, str):
        raise ValueError("用户名必须是字符串。")
    username = value.strip()
    # 先校验 ASCII，再转小写，避免某些 Unicode 字母转换后混入用户名。
    if re.fullmatch(r"[A-Za-z0-9_]{3,30}", username) is None:
        raise ValueError("用户名须为 3～30 个英文字母、数字或下划线。")
    return username.lower()


def build_user_document(username, password):
    normalized_username = normalize_username(username)
    if not isinstance(password, str) or not password:
        raise ValueError("密码必须是非空字符串。")
    return {
        "_id": ObjectId(),
        "username": normalized_username,
        "password_hash": generate_password_hash(password, method="scrypt"),
        "created_at": datetime.now(timezone.utc),
    }


def create_user(database, username, password):
    """保存用户并返回编号；重复用户名由唯一索引拒绝。"""
    document = build_user_document(username, password)
    return database["users"].insert_one(document).inserted_id


def find_user_by_username(database, username):
    return database["users"].find_one({"username": normalize_username(username)})


def verify_user_password(user, password):
    if not isinstance(user, dict) or not isinstance(password, str) or not password:
        return False
    password_hash = user.get("password_hash")
    if not isinstance(password_hash, str):
        return False
    try:
        return check_password_hash(password_hash, password)
    except (ValueError, TypeError):
        return False
