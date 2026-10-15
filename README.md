# 基于 MongoDB 的问卷系统

数据科学与工程导论第一次大作业。使用 Python、Flask 和 MongoDB 开发简化版在线问卷系统，支持账号、问卷管理、条件跳转、回答校验和结果统计。

## 当前进度

目前处于项目初始化阶段，已实现 Flask 首页和 Jinja HTML 模板渲染。账号、数据库连接、问卷和统计功能尚未实现。

项目分为两个阶段：先完成初始需求，再在现有代码上实现需求变更。第二阶段需要保留第一阶段的核心结构，并记录数据与功能的兼容性处理。

## 本地运行

开发环境使用 Python 3.12。以下命令适用于 Windows PowerShell，均在本项目根目录执行。

首次准备环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

若本地已经存在可用的 `.venv`，可以直接使用它。

启动应用：

```powershell
.\.venv\Scripts\python.exe -m flask --app app:app run
```

浏览器访问 <http://127.0.0.1:5000>，应显示“问卷系统首页”和“欢迎使用问卷系统。”。在终端按 `Ctrl+C` 停止服务。此命令启动本地开发服务器。

当前首页不访问数据库，因此无需先启动 MongoDB。后续接入数据库时，将补充连接配置和启动说明。

## 目录结构

```text
.
├── app.py                 # Flask 应用与首页路由
├── templates/
│   └── index.html         # 首页模板
├── tests/
│   └── test_app.py        # 首页自动测试
├── docs/
│   ├── 开发计划.md         # 功能范围与开发顺序
│   └── AI使用日志.md       # 真实的 AI 协作记录
├── requirements.txt       # 已安装依赖的固定版本
├── AGENTS.md              # 改动、测试和提交规则
├── .gitignore             # 排除环境、缓存和本地配置
└── .gitattributes         # 统一文本文件换行规则
```

## 第一阶段功能范围

- 用户注册、登录，以及用户之间的问卷权限隔离。
- 问卷创建、发布、关闭、截止时间和分享链接。
- 单选题、多选题、文本填空和数字填空。
- 必填、选项数量、文本长度和数值范围等校验。
- 根据单选、多选或数字回答执行可配置的题目跳转。
- 登录后填写问卷，支持匿名回答设置和重复填写设置。
- 保存每次提交，并按问卷或题目统计回答。

全部业务数据存储在 MongoDB 中。具体设计和接口说明将在功能实现时补充，详细计划见 [开发计划](docs/开发计划.md)。

## 当前验证方法

依赖一致性检查：

```powershell
.\.venv\Scripts\python.exe -m pip check
```

运行全部自动测试（使用 Python 自带的 unittest，无需额外安装测试框架）：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

当前测试验证首页返回 200、响应为 HTML、模板中的标题和欢迎文字正确，并确认标题占位符已被替换。也可以启动应用并在浏览器中检查页面。

当前仅完成首页验证。创建问卷、添加题目、跳转、校验、提交和统计测试将在对应功能完成后添加，并记录步骤、输入、预期输出和实际结果。

## Git 与 GitHub

本目录作为独立 Git 仓库，主分支为 `main`。`.venv`、缓存、本地环境配置和课程原始 Word 文档不纳入版本管理。

本仓库的提交身份使用 GitHub 用户名 `Beejiaang` 和隐私邮箱 `270674908+Beejiaang@users.noreply.github.com`。该设置仅作用于本仓库。

每次改动后，按 [AGENTS.md](AGENTS.md) 更新相关测试，完成全部测试与验证，再创建对应提交：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
git status
git add .
git diff --cached --check
git commit -m "说明本次改动"
```

已创建私有仓库 [Beejiaang/mongodb-survey-system](https://github.com/Beejiaang/mongodb-survey-system)，本地远程名称 `origin` 已关联到该仓库。完成首次提交后，执行：

```powershell
git push -u origin main
```

本地首次提交包含项目初始化文件、协作规则和首页测试；代码尚未上传。本机推送认证尚未验证，首次推送时可能需要登录 GitHub。完成上传后，在项目报告首页填写上面的仓库链接。

## 提交材料

最终提交应包含可运行代码、MongoDB 数据结构说明、配置说明、API 与关键逻辑说明、测试用例和项目完成报告。AI 使用过程见 [AI 使用日志](docs/AI使用日志.md)，后续开发应持续补充真实记录。
