"""Flask 应用入口；配置可在测试中注入。"""

import os
import secrets
from datetime import timedelta, timezone
from pathlib import Path

from flask import Flask, g, redirect, render_template, url_for
from pymongo.errors import PyMongoError
from werkzeug.exceptions import HTTPException

from auth import auth, csrf_token, load_user, login_required, validate_csrf
from database import close_request_client, init_db_command
from survey_views import surveys, shared_surveys
from response_views import filling
from responses import ResponseError


def create_app(config=None):
    application = Flask(__name__)
    application.config.from_mapping(
        SECRET_KEY=os.environ.get("FLASK_SECRET_KEY"),
        RESPONSE_IDENTITY_KEY=os.environ.get("RESPONSE_IDENTITY_KEY"),
        MONGODB_URI=os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017"),
        MONGODB_DB_NAME=os.environ.get("MONGODB_DB_NAME", "survey_system"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    if config:
        application.config.update(config)
    if not application.config["SECRET_KEY"]:
        # 开发密钥保存在被 Git 忽略的 instance 目录，重启后仍可验证会话。
        key_path = Path(application.instance_path) / "session-key"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with key_path.open("x", encoding="ascii") as key_file:
                key_file.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        application.config["SECRET_KEY"] = key_path.read_text(encoding="ascii").strip()

    if not application.config["RESPONSE_IDENTITY_KEY"]:
        key_path = Path(application.instance_path) / "response-identity-key"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with key_path.open("x", encoding="ascii") as key_file:
                key_file.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        application.config["RESPONSE_IDENTITY_KEY"] = key_path.read_text(encoding="ascii").strip()

    application.cli.add_command(init_db_command)
    application.register_blueprint(auth)
    application.register_blueprint(surveys)
    application.register_blueprint(shared_surveys)
    application.register_blueprint(filling)
    application.before_request(validate_csrf)
    application.before_request(load_user)
    application.teardown_appcontext(close_request_client)
    application.jinja_env.globals["csrf_token"] = csrf_token

    @application.template_filter("beijing_time")
    def beijing_time(value):
        return value.astimezone(timezone(timedelta(hours=8))).strftime("%Y年%m月%d日 %H:%M")

    @application.route("/")
    def index():
        if g.user:
            return redirect(url_for("account"))
        return render_template("index.html", title="问卷系统首页")

    @application.route("/account")
    @login_required
    def account():
        return render_template("account.html", title="我的账号")

    @application.errorhandler(ResponseError)
    def response_error(error):
        return render_template("error.html", title="填写提示", message=str(error)), error.status

    @application.errorhandler(PyMongoError)
    def database_unavailable(error):
        application.logger.error("MongoDB 操作失败：%s", type(error).__name__)
        return render_template(
            "error.html", title="暂时无法连接", message="暂时无法连接数据服务，请稍后再试。"
        ), 503

    @application.errorhandler(HTTPException)
    def http_error(error):
        messages = {
            400: error.description if error.description.startswith("表单") else "请求内容有误，请检查后重试。",
            404: "这个页面还不存在，回到首页继续吧。",
            405: "当前操作不支持这种访问方式。",
            413: "提交的内容过大，请减少输入后重试。",
        }
        response = error.get_response()
        response.set_data(render_template(
            "error.html", title=f"访问提示 · {error.code}",
            message=messages.get(error.code, "暂时无法完成请求，请稍后重试。"),
        ))
        response.content_type = "text/html; charset=utf-8"
        return response

    @application.after_request
    def protect_response(response):
        if response.mimetype == "text/html":
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    return application


app = create_app()
