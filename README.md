# 基于 MongoDB 的问卷系统

数据科学与工程导论第一次大作业。使用 Python、Flask 和 MongoDB 开发简化版在线问卷系统，支持账号、问卷管理、条件跳转、回答校验和结果统计。

## 当前进度

已实现首页、注册、登录、退出和个人账号页面，以及 MongoDB 集合初始化和用户数据处理。注册写入 `users`，登录使用相同的用户名规则和密码哈希验证。`surveys`、`responses` 已建好，等待后续问卷功能接入。

项目分为两个阶段：先完成初始需求，再在现有代码上实现需求变更。第二阶段需要保留第一阶段的核心结构，并记录数据与功能的兼容性处理。

## 本地运行

开发环境使用 Python 3.12。以下命令适用于 Windows PowerShell，均在本项目根目录执行。

首次准备环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

若本地已经存在可用的 `.venv`，可以直接使用它。

先启动本机 MongoDB。当前开发电脑可使用已有脚本：

```powershell
& 'E:\db\scripts\start-mongod.ps1' -Background
```

该启动脚本是当前电脑的安装辅助文件，不在本仓库中；其他电脑需自行安装并启动 MongoDB，项目已在 MongoDB 8.0.22 验证。

默认连接 `mongodb://127.0.0.1:27017`，数据库名为 `survey_system`。如需调整，在当前终端设置环境变量：

```powershell
$env:MONGODB_URI = "mongodb://127.0.0.1:27017"
$env:MONGODB_DB_NAME = "survey_system"
```

创建集合及用户约束（可重复执行，保留已有数据）：

```powershell
.\.venv\Scripts\python.exe -m flask --app app:app init-db
```

启动应用：

```powershell
.\.venv\Scripts\python.exe -m flask --app app:app run
```

浏览器访问 <http://127.0.0.1:5000>，可以进入注册或登录页面。注册成功后跳转登录；登录后进入个人账号页，显示用户名和北京时间的注册时间；右上角可退出。在终端按 `Ctrl+C` 停止服务。此命令启动本地开发服务器。

账号操作需要 MongoDB 正常运行。密码仍按原规则要求非空，注册时还需确认密码。用户名去首尾空白并统一小写，密码保留原始大小写和空格。字段设计见 [数据库设计](docs/数据库设计.md)，网页接口见 [账号功能说明](docs/账号功能说明.md)。

登录状态由 Flask 签名会话保存，客户端会话包含用户编号、表单令牌及临时提示，不保存密码或密码哈希。开发环境自动在被 Git 忽略的 `instance/session-key` 中生成并保留随机密钥，也可通过 `FLASK_SECRET_KEY` 环境变量提供稳定密钥。HTTPS 环境可在应用配置中启用 `SESSION_COOKIE_SECURE`，本地 HTTP 开发使用默认关闭值。

## 目录结构

```text
.
├── app.py                 # 应用工厂、首页和个人账号页
├── auth.py                # 注册、登录、退出、会话和表单保护
├── database.py            # MongoDB 连接、集合校验和初始化命令
├── users.py               # 用户名、密码哈希及用户保存与查询
├── templates/
│   ├── base.html          # 共用导航和页面布局
│   ├── index.html         # 首页
│   ├── account.html       # 个人账号页
│   ├── error.html         # 访问错误提示
│   └── auth/              # 注册和登录模板
├── static/
│   └── style.css          # 桌面和手机样式
├── tests/
│   ├── test_app.py        # 首页自动测试
│   ├── test_auth.py       # 网页账号流程和会话集成测试
│   ├── test_users.py      # 用户名和密码处理测试
│   └── test_database.py   # 真实 MongoDB 集成测试
├── docs/
│   ├── 开发计划.md         # 功能范围与开发顺序
│   ├── 数据库设计.md       # 集合、用户字段及索引
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

全部测试包括首页、用户名、密码哈希、真实 MongoDB 校验与索引，以及网页注册、重复用户名、密码确认、登录状态、退出、访问限制和表单保护。测试创建名字为 `survey_system_test_<随机编号>` 的临时数据库，并在每项测试后清理，不向业务数据库写测试用户。MongoDB 连接失败会让测试报错。

数据库测试默认连接本机 27017 端口，可以通过 `MONGODB_TEST_URI` 指定另一个测试实例。只运行无需数据库的用户规则测试时，执行 `python -m unittest discover -s tests -p test_users.py -v`。提交前仍需运行完整测试。

创建问卷、添加题目、跳转、答案校验、提交和统计测试将在对应功能完成后添加。当前测试步骤和用例见 [测试说明](docs/测试说明.md)。

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

已创建私有仓库 [Beejiaang/mongodb-survey-system](https://github.com/Beejiaang/mongodb-survey-system)，本地远程名称 `origin` 已关联到该仓库，主分支已跟踪 `origin/main`。本地提交后执行：

```powershell
git push
```

初始提交 `fb87532` 已上传并核对成功。后续每次本地 commit 与上传是两个独立操作；可用 `git status --short --branch` 查看同步状态。项目报告首页使用上面的仓库链接。

## 提交材料

最终提交应包含可运行代码、MongoDB 数据结构说明、配置说明、API 与关键逻辑说明、测试用例和项目完成报告。AI 使用过程见 [AI 使用日志](docs/AI使用日志.md)，后续开发应持续补充真实记录。
