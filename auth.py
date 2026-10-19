"""注册、登录、退出和表单保护。"""

from functools import wraps
import secrets
import re

from bson import ObjectId
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from pymongo.errors import DuplicateKeyError
from werkzeug.security import generate_password_hash

from database import get_request_database
from users import create_user, find_user_by_username, verify_user_password


auth = Blueprint("auth", __name__)
_DUMMY_USER = {"password_hash": generate_password_hash(secrets.token_urlsafe(32), method="scrypt")}


def login_destination():
    """仅允许本站固定格式的问卷地址，防止登录后跳转到外部网站。"""
    target = request.args.get("next", "")
    return target if re.fullmatch(r"/survey/[0-9a-fA-F]{24}", target) else url_for("account")


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def validate_csrf():
    if request.method == "POST":
        expected = session.get("csrf_token")
        actual = request.form.get("csrf_token", "")
        if not isinstance(expected, str) or not secrets.compare_digest(
            expected.encode("utf-8"), actual.encode("utf-8")
        ):
            abort(400, description="表单已失效，请重新打开页面后再提交。")


def load_user():
    g.user = None
    if request.endpoint == "static":
        return
    user_id = session.get("user_id")
    if user_id is None:
        return
    if not isinstance(user_id, str) or not ObjectId.is_valid(user_id):
        session.pop("user_id", None)
        return
    g.user = get_request_database()["users"].find_one(
        {"_id": ObjectId(user_id)}, {"password_hash": 0}
    )
    if g.user is None:
        session.pop("user_id", None)


def login_required(view):
    @wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        return view(**kwargs)
    return wrapped


@auth.route("/register", methods=("GET", "POST"))
def register():
    if g.user:
        return redirect(url_for("account"))
    username = ""
    error = None
    status = 200
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        confirmation = request.form.get("confirm_password", "")
        if password != confirmation:
            error, status = "两次输入的密码不一致。", 400
        else:
            try:
                create_user(get_request_database(), username, password)
            except ValueError as validation_error:
                error, status = str(validation_error), 400
            except DuplicateKeyError:
                error, status = "这个用户名已被使用，请换一个。", 409
            else:
                flash("注册成功，请使用新账号登录。", "success")
                target = login_destination()
                return redirect(url_for("auth.login", **({"next": target} if target != url_for("account") else {})), code=303)
    return render_template(
        "auth/register.html", title="创建账号", username=username, error=error
    ), status


@auth.route("/login", methods=("GET", "POST"))
def login():
    if g.user:
        return redirect(login_destination())
    username = ""
    error = None
    status = 200
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        try:
            user = find_user_by_username(get_request_database(), username)
        except ValueError as validation_error:
            error, status = str(validation_error), 400
        else:
            password_matches = verify_user_password(user or _DUMMY_USER, password)
            if user is None or not password_matches:
                error, status = "用户名或密码错误。", 401
            else:
                session.clear()
                session["user_id"] = str(user["_id"])
                csrf_token()
                return redirect(login_destination(), code=303)
    return render_template("auth/login.html", title="登录", username=username, error=error), status


@auth.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"), code=303)
