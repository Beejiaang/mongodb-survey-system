from flask import Flask, render_template

# 创建 Flask 应用
app = Flask(__name__)

# 将首页地址与下面的函数关联
@app.route("/")
def index():
    return render_template(
        "index.html",
        title="问卷系统首页"
    )
