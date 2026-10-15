# 基于 MongoDB 的问卷系统

数据科学与工程导论第一次大作业。使用 Python、Flask 和 MongoDB 开发简化版在线问卷系统，支持账号、问卷管理、条件跳转、回答校验和结果统计。

## 当前进度

已实现 Flask 首页、MongoDB 集合初始化和用户数据处理。数据库包含 `users`、`surveys`、`responses`；用户层已支持用户名规范化、密码哈希、保存和查询。注册登录网页、会话管理、问卷和统计功能将在后续步骤实现。

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

浏览器访问 <http://127.0.0.1:5000>，应显示“问卷系统首页”和“欢迎使用问卷系统。”。在终端按 `Ctrl+C` 停止服务。此命令启动本地开发服务器。

首页仅展示欢迎文字；初始化命令及用户数据操作需要 MongoDB 正常运行。结构与处理规则见 [数据库设计](docs/数据库设计.md)。

## 目录结构

```text
.
├── app.py                 # Flask 应用与首页路由
├── database.py            # MongoDB 连接、集合校验和初始化命令
├── users.py               # 用户名、密码哈希及用户保存与查询
├── templates/
│   └── index.html         # 首页模板
├── tests/
│   ├── test_app.py        # 首页自动测试
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

全部测试包括首页、用户名边界及规范化、密码哈希和验证，以及真实 MongoDB 的集合、字段校验、唯一索引和重复初始化。数据库测试会创建名字为 `survey_system_test_<随机编号>` 的临时数据库，并在每项测试后清理，不向业务数据库写测试用户。MongoDB 连接失败会让测试报错。

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
