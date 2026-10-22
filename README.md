# 基于 MongoDB 的问卷系统

当代数据管理系统第一次大作业。使用 Python、Flask 和 MongoDB 开发简化版在线问卷系统，支持账号、问卷管理、条件跳转、回答校验和结果统计。

## 当前进度

原始需求已整理为 [逐项验收清单](docs/需求验收清单.md)。2026年10月20日51项基础业务验收点通过，当次190项自动测试通过；后续增加图表、单选人数及数字格式校验，最新套件含214项Python及16项JavaScript测试，实际结果见 [测试说明](docs/测试说明.md)。基础验收历史证据见 [第一阶段验收报告](docs/第一阶段验收报告.md)。提交文档的四项内容及数据库设计理由已补齐，[项目完成报告](docs/项目完成报告.md)主体已整理，AI协作事实与问题修正已汇总，第六节已包含个人设计理解、实际修改说明和反思。

已实现首页、注册、登录、退出和个人账号页面，以及 MongoDB 集合初始化和用户数据处理。注册写入 `users`，登录使用相同的用户名规则和密码哈希验证。问卷草稿、题目保存到 `surveys`，填写进度和正式回答保存到 `responses`。

已实现问卷创建、自己的问卷列表与详情、逐题添加和保存。创建页提供单选、多选及统一填空入口，填空内选择文字/数字；选择题默认A、B行，点击加号继续添加。复用 [题型与校验说明](docs/题型与校验说明.md) 中的配置校验。页面与接口见 [问卷创建与保存说明](docs/问卷创建与保存说明.md)。发布、截止时间、关闭与分享入口已实现；单题跳转规则配置、优先级调整和试算已实现，详见 [跳转规则说明](docs/跳转规则说明.md)。逐题填写、返回修改、自动跳转、确认提交及答案保存已实现，见 [填写与答案保存说明](docs/填写与答案保存说明.md)。

问卷详情的“查看统计”提供汇总与单题结果：选项计数及占比、文本答案、数字答案和平均值；仅创建者可查看。题目标题中的“请使用阿拉伯数字回答”采用带中文括号的浅绿色标签，便于与正文区分。单选、多选和数字题可在“展示方式”中切换图表或数据表格，图表选择会在本浏览器保留。文本题只显示回答数量，并可进入填写内容列表。详见 [统计结果说明](docs/统计结果说明.md)。

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

创建集合、用户与问卷约束和索引（可重复执行，保留已有数据；从旧版本更新后也执行一次）：

```powershell
.\.venv\Scripts\python.exe -m flask --app app:app init-db
```

启动应用：

```powershell
.\.venv\Scripts\python.exe -m flask --app app:app run
```

浏览器访问 <http://127.0.0.1:5000>，可以进入注册或登录页面。注册成功后跳转登录；登录后进入个人账号页，显示用户名和北京时间的注册时间；右上角可退出。在终端按 `Ctrl+C` 停止服务。此命令启动本地开发服务器。

登录后点击“创建问卷”，保存标题、说明及填写设置，再添加题目；“我的问卷”可以重新打开已保存的草稿。页面只显示当前账号自己的问卷。更新代码后需重启已运行的开发服务。

问卷详情中的“发布与分享”可以设置北京时间截止日期、发布、复制链接和关闭问卷。分享入口要求登录，仅提供退出和开始/继续填写，不预览整卷题目；复制链接支持局域网HTTP的兼容复制与手动提示。到期或关闭后停止接受新提交。详见 [发布与分享说明](docs/发布与分享说明.md)。本机地址仅用于本机访问；跨设备需使用可访问的局域网或部署地址，真实第二设备验证见[测试用例N01](docs/测试用例.md)，目前待执行。

账号操作需要 MongoDB 正常运行。密码仍按原规则要求非空，注册时还需确认密码。用户名去首尾空白并统一小写，密码保留原始大小写和空格。字段设计见 [数据库设计](docs/数据库设计.md)，网页接口见 [账号功能说明](docs/账号功能说明.md)。

登录状态由 Flask 签名会话保存，客户端会话包含用户编号、表单令牌及临时提示，不保存密码或密码哈希。开发环境自动在被 Git 忽略的 `instance/session-key` 中生成并保留随机密钥，也可通过 `FLASK_SECRET_KEY` 环境变量提供稳定密钥。HTTPS 环境可在应用配置中启用 `SESSION_COOKIE_SECURE`，本地 HTTP 开发使用默认关闭值。

## 目录结构

```text
.
├── app.py                 # 应用工厂、首页和个人账号页
├── auth.py                # 注册、登录、退出、会话和表单保护
├── database.py            # MongoDB 连接、集合校验和初始化命令
├── users.py               # 用户名、密码哈希及用户保存与查询
├── questions.py           # 题目配置和单题答案校验
├── numeric_input.py       # 网页阿拉伯数字格式解析，与浏览器规则共用
├── jumps.py               # 单题条件、前向跳转、路径计算
├── responses.py           # 填写进度、匿名标识、幂等提交
├── response_schema.py     # 回答结构约束与状态规则
├── response_views.py      # 逐题填写、确认与本人回执
├── survey_results.py      # 正式回答汇总、平均值和分页答案
├── result_views.py        # 创建者统计页面与权限检查
├── surveys.py             # 问卷草稿保存、所有权查询和原子追加题目
├── survey_schema.py       # 问卷及嵌入题目的 MongoDB 校验
├── survey_views.py        # 我的问卷、创建、详情及添加题目路由
├── templates/
│   ├── base.html          # 共用导航和页面布局
│   ├── index.html         # 首页
│   ├── account.html       # 个人账号页
│   ├── error.html         # 访问错误提示
│   ├── auth/              # 注册和登录模板
│   └── surveys/           # 问卷列表、创建、详情及题目表单
├── static/
│   ├── style.css          # 桌面和手机样式
│   ├── share.js           # 复制分享链接
│   └── result-charts.js   # 本地SVG图表、表格切换及选择记忆
├── tests/
│   ├── test_app.py        # 首页自动测试
│   ├── test_auth.py       # 网页账号流程和会话集成测试
│   ├── test_users.py      # 用户名和密码处理测试
│   ├── test_questions.py  # 题型配置、答案和边界测试
│   ├── test_surveys.py    # 问卷数据层、归属和并发测试
│   ├── test_survey_routes.py # 问卷页面与真实数据库集成测试
│   ├── test_database.py   # 真实 MongoDB 集成测试
│   ├── test_publishing.py # 发布、截止、关闭、分享与登录回跳
│   ├── test_jumps.py      # 跳转配置、路径与兼容性
│   ├── test_responses.py  # 填写保存、提交、匿名与并发
│   ├── test_results.py    # 统计、人数、精度、权限与图表数据
│   ├── test_numeric_input.py # 阿拉伯数字格式及边界
│   ├── test_acceptance.py # 从注册创建到填写统计的整链验收
│   ├── test_documentation.py # 文档与路由、字段、索引、示例、链接一致性
│   └── result-charts.test.cjs # JavaScript图表测试
├── docs/
│   ├── 系统说明.md         # 提交文档阅读入口、使用流程与系统结构
│   ├── 数据库设计.md       # 三集合字段、索引、取舍与兼容性
│   ├── API说明.md          # 全部业务接口、字段、权限与错误
│   ├── 关键逻辑说明.md     # 校验、跳转、保存、并发与统计
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

全部业务数据存储在 MongoDB 中。功能、设计与接口的统一阅读入口见 [系统说明](docs/系统说明.md)，详细计划见 [开发计划](docs/开发计划.md)。

## 当前验证方法

依赖一致性检查：

```powershell
.\.venv\Scripts\python.exe -m pip check
```

运行全部自动测试（使用 Python 自带的 unittest，无需额外安装测试框架）：

```powershell
.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -v
& 'C:\Program Files\nodejs\node.exe' --test tests\result-charts.test.cjs tests\browser-behavior.test.cjs
```

全部测试包括首页、用户名、密码哈希、真实 MongoDB 校验与索引，以及网页注册、重复用户名、密码确认、登录状态、退出、访问限制和表单保护。测试创建名字为 `survey_system_test_<随机编号>` 的临时数据库，并在每项测试后清理，不向业务数据库写测试用户。MongoDB 连接失败会让测试报错。

数据库测试默认连接本机 27017 端口，可以通过 `MONGODB_TEST_URI` 指定另一个测试实例。只运行无需数据库的用户规则测试时，执行 `python -m unittest discover -s tests -p test_users.py -v`。提交前仍需运行完整测试。

题型配置、单题答案校验、问卷创建页面、题目保存、所有权隔离及并发追加已有测试。`tests/test_publishing.py` 另覆盖发布、截止边界、关闭、分享与登录回跳。单独运行题型单元测试可执行 `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_questions.py -v`。`tests/test_jumps.py` 覆盖跳转语义、路径、配置页面和旧结构升级；`tests/test_responses.py` 覆盖填写、提交、匿名及并发防重复；`tests/test_numeric_input.py` 覆盖阿拉伯数字格式；`tests/test_results.py` 覆盖统计口径、单选人数去重、精度、分页和结果权限。`tests/test_acceptance.py` 补充注册、配置、填写与统计的完整链路验收；`tests/test_documentation.py` 核对文档的接口、字段、索引、示例与本地链接。当前Python完整套件214项，另有16项JavaScript图表与分享测试；按原文六类要求和额外功能整理的55组场景（54组自动场景及1组待执行跨设备手工场景）见 [测试用例与验收结果](docs/测试用例.md)，历史记录见 [测试说明](docs/测试说明.md)。

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

仓库已设为公开 [Beejiaang/mongodb-survey-system](https://github.com/Beejiaang/mongodb-survey-system)，本地远程名称 `origin` 已关联到该仓库，主分支已跟踪 `origin/main`。本地提交后执行：

```powershell
git push
```

初始提交 `fb87532` 已上传并核对成功。后续每次本地 commit 与上传是两个独立操作；可用 `git status --short --branch` 查看同步状态。项目报告首页使用上面的仓库链接。

## 提交材料

最终提交应包含可运行代码、MongoDB 数据结构说明、配置说明、API 与关键逻辑说明、测试用例和项目完成报告。AI 使用过程见 [AI 使用日志](docs/AI使用日志.md)，后续开发应持续补充真实记录。

“七、提交内容／2.文档”的四份材料已整理为 [系统说明](docs/系统说明.md)、[数据库设计](docs/数据库设计.md)、[API说明](docs/API说明.md)、[关键逻辑说明](docs/关键逻辑说明.md)。四份文档可从系统说明进入，也可单独阅读。[项目完成报告](docs/项目完成报告.md)全文和[AI使用日志](docs/AI使用日志.md)均已上传，可直接在GitHub查看。N01真实跨设备测试仍待执行。
