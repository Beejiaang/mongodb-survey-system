# API说明

本文集中说明当前Flask网页接口，与 [系统说明](系统说明.md)、[数据库设计](数据库设计.md) 和 [关键逻辑说明](关键逻辑说明.md) 配套。接口返回HTML页面或重定向，使用普通表单提交，不提供独立JSON REST接口。

## 通用约定

本机基础地址为 `http://127.0.0.1:5000`。survey_id和response_id是MongoDB ObjectId的24位十六进制文本；question_id和选项编号为服务器生成的字符串。下面路径中的尖括号表示变量。

浏览器使用Flask签名会话Cookie维持登录。所有POST都必须带当前会话表单中的csrf_token；先GET表单并保留Cookie，再提交令牌，不能使用其他会话的令牌。表单编码为application/x-www-form-urlencoded，多选以同名字段重复传值。复选框选中值为on，不选时省略。应用请求体上限64 KiB，超限返回413。

未登录访问受保护页面一般302跳转登录。分享入口额外设置next，登录成功回原问卷；next只允许本站固定格式 `/survey/24位十六进制编号`，不会任意跳回其他页面。以下POST成功通常303重定向，表单纠错、规则试算和已经提交的回执有明确例外。

## 全部业务路由

本表覆盖33个业务路径，未列Flask自动生成的HEAD、OPTIONS以及静态资源GET接口。

| 方法 | 路径 | 身份与用途 | 正常结果 |
| --- | --- | --- | --- |
| GET | / | 公开首页 | 未登录200；已登录302到/account |
| GET, POST | /register | 未登录注册 | GET 200；POST成功303到/login，可保留next |
| GET, POST | /login | 未登录登录 | GET 200；POST成功303到/account或合法分享目标 |
| GET | /account | 登录后的本人账号 | 200 |
| POST | /logout | 当前会话，要求CSRF | 清会话，303到首页 |
| GET | /surveys | 当前用户自己的问卷列表 | 200 |
| GET, POST | /surveys/new | 登录用户创建草稿 | GET 200；POST成功303到详情 |
| GET | /surveys/<survey_id> | 问卷创建者查看详情 | 200 |
| GET, POST | /surveys/<survey_id>/questions/new | 问卷创建者；仅草稿可追加 | GET 200；保存成功303到详情；提示按钮200回填表单 |
| GET, POST | /surveys/<survey_id>/questions/<question_id>/jumps | 问卷创建者；仅草稿可配置或试算 | GET/试算200；保存、排序、删除成功303到规则页 |
| POST | /surveys/<survey_id>/questions/<question_id>/layout | 问卷创建者；草稿上移、下移或删除题目，清空全部跳转 | 成功303；过期或已发布409 |
| POST | /surveys/<survey_id>/deadline | 问卷创建者；草稿或已发布 | 保存成功303到详情 |
| POST | /surveys/<survey_id>/publish | 问卷创建者；有效草稿 | 发布成功303到详情 |
| POST | /surveys/<survey_id>/close | 问卷创建者；已发布 | 关闭成功303到详情 |
| GET | /survey/<survey_id> | 登录填写者访问分享入口 | 开放时200；草稿404；已关闭/截止410 |
| POST | /survey/<survey_id>/start | 登录填写者 | 创建或恢复本人草稿，303到填写页 |
| GET, POST | /survey/<survey_id>/fill/<response_id> | 本次回答所属账号 | GET显示当前题、确认页或回执200；保存/返回/首次提交成功303；已提交记录直接显示回执200 |
| GET | /surveys/<survey_id>/results | 问卷创建者 | 整卷统计200 |
| GET | /surveys/<survey_id>/results/questions/<question_id> | 问卷创建者 | 单题统计及填空答案分页200 |
| GET | /library | 本人及获共享题目列表 | GET 200；POST成功303，验证失败400，冲突409，越权404 |
| GET, POST | /library/new | 创建独立题目，复用题型表单 | GET 200；POST成功303，验证失败400，冲突409，越权404 |
| GET | /library/items/<item_id> | 版本详情、选择、差异及历史 | GET 200；POST成功303，验证失败400，冲突409，越权404 |
| GET, POST | /library/items/<item_id>/versions/new | 作者基于指定版本创建新版本 | GET 200；POST成功303，验证失败400，冲突409，越权404 |
| POST | /library/versions/<version_id>/restore | 作者用旧内容生成新版本，记录恢复来源 | GET 200；POST成功303，验证失败400，冲突409，越权404 |
| POST | /library/versions/<version_id>/fork | 接收者派生自己的题目 | GET 200；POST成功303，验证失败400，冲突409，越权404 |
| POST | /library/items/<item_id>/share | 作者定向共享或撤销 | GET 200；POST成功303，验证失败400，冲突409，越权404 |
| POST | /library/versions/<version_id>/use | 将确切版本加入本人草稿 | GET 200；POST成功303，验证失败400，冲突409，越权404 |
| POST | /library/from-survey/<survey_id>/<question_id> | 本人问卷题目保存为常用题，旧卷不变 | GET 200；POST成功303，验证失败400，冲突409，越权404 |
| GET | /library/items/<item_id>/usage | 使用情况，本人问卷详情及作者可见的其他引用名称、编号、状态 | GET 200；POST成功303，验证失败400，冲突409，越权404 |
| GET, POST | /library/banks | 创建和列出本人题库 | GET 200；POST成功303，验证失败400，冲突409，越权404 |
| GET, POST | /library/banks/<bank_id> | 本人题库查看、条目增删、手动更新、名称修改和删除 | GET 200；POST成功303，验证失败400，冲突409，越权404 |
| GET, POST | /library/select/<survey_id> | 本人草稿从题库或可用题目选择版本 | GET 200；POST成功303，验证失败400，冲突409，越权404 |
| GET | /library/versions/<version_id>/results | 当前用户自建问卷中此版本的跨问卷统计 | GET 200；POST成功303，验证失败400，冲突409，越权404 |

已登录访问注册页跳转/account；登录页跳转合法next或/account。他人的管理页和回答编号均返回404，不能仅凭分享链接查看结果或管理问卷。已提交的本人回执在关闭后仍可访问。

## 账号及创建参数

| 接口 | 字段 | 约束 |
| --- | --- | --- |
| POST /register | username、password、confirm_password | 用户名去首尾空白，3～30位ASCII字母/数字/下划线并小写；非空密码，两次一致 |
| POST /login | username、password | 相同用户名规则；密码原样比对哈希 |
| GET /register、/login | next（可选查询参数） | 仅合法分享路径；提交仍保留在URL查询串 |
| POST /surveys/new | title、description、is_anonymous、allow_repeat | 标题1～200字符；说明最多5000，可空；两个开关选中为on |
| POST /surveys/<survey_id>/deadline | deadline | 北京时间YYYY-MM-DDTHH:MM，必须晚于当前；空串取消限制 |
| POST /surveys/<survey_id>/publish、/close | 除csrf_token外无必填业务字段 | 状态及权限由后端检查 |

不接受请求指定owner_id、创建时间、发布状态或正式提交时间来控制实际存储。GET /surveys按当前账号筛选，按created_at和_id倒序。

## 添加题目参数

GET使用kind查询参数选择题型，缺省single_choice；POST使用隐藏字段kind。正常保存不需要action字段，所有题型必须提交title，可选required=on。

| kind | 专有字段 | 保存类型 |
| --- | --- | --- |
| single_choice | option_text（重复字段），按行提交至少两个选项 | type=single_choice |
| multiple_choice | option_text（重复字段）、min_choices、max_choices | type=multiple_choice |
| text | min_length、max_length | type=fill_blank、input_type=text |
| number | min_value、max_value、integer_only=on | type=fill_blank、input_type=number |

上下限为空表示不限制，包含边界；数量和长度为非负整数。文本长度按Unicode码点计数。最少不能大于最多，多选限制不能超过选项总数；必答配置须有可填写的值。

选择题新表单按A、B、C顺序提交重复字段`option_text`，不允许空白或含换行的选项。`action=add_option`只返回多一行的表单，不保存；兼容旧`options_text`换行字段。填空统一入口，通过`input_type=text/number`选择回答内容，优先于旧`kind=text/number`；只读取所选类型的限制。

文本或数字题提交action=number_hint时，仅返回整理后的数字表单，不写数据库：附加“请使用阿拉伯数字回答”，已有提示不重复；保留标题、必答及已有数字设置，文本长度设置不再适用。还须点击“保存题目”才能追加题目，成功返回303。此动作也要求CSRF、问卷归属及草稿状态。

## 跳转规则参数

所有规则POST带revision（GET页面提供的整卷题目配置摘要）。规则编号index从0开始，仅用于当前版本。

| action | 字段 | 行为 |
| --- | --- | --- |
| add | operator、target，以及下述条件值 | 追加规则，target必须是本问卷后面的题 |
| delete | index | 删除指定规则 |
| up、down | index | 调整优先级，不能超出边界 |
| test | 题型对应模拟答案 | 用已保存规则试算，不写回答或规则，200显示下一题或确认阶段 |

单选operator=equals，value为一个已有选项编号；多选operator=contains_all或equals_set，value重复传多个编号。数字支持eq、lt、lte、gt、gte、between，使用number字段；between还要求upper。试算使用test_answer（多选重复传值）、test_number或test_text。完整数据结构及例子见 [跳转规则说明](跳转规则说明.md)。

## 填写及提交参数

开始接口只需csrf_token。填写POST需要csrf_token、整数version和action：

| action | answer字段 | 行为 |
| --- | --- | --- |
| next | 单选为选项编号，多选为重复的answer字段，文本为原文，数字为阿拉伯数字文本 | 校验当前题，保存并计算下一题；末题后到确认页 |
| back | 忽略当前输入 | 返回实际路径上一题，不被当前无效数字阻挡 |
| submit | 不接受整卷答案覆盖 | 仅确认页可提交；后端重算路径并校验已有保存答案，再正式提交 |

例如多选表单中 `answer=option_a&answer=option_b` 表示同时选两项。选答空白会按题型规范化为null，必答不允许空；数字支持18、-2.5、1e3，但拒绝全角数字、文字、单位、逗号、下划线、NaN和Infinity，再校验该题的范围和整数要求。纯整数优先解析为64位BSON整数；不是任意精度小数接口。

version每次保存、返回或首次正式提交递增；旧版本不能覆盖新进度。已经提交的同一记录再次访问或POST直接显示原回执，不生成新回答。问卷不允许重复填写时，提交后再次start返回409。

## 统计查询参数与输出

整卷统计显示提交总数、题目数及所有题统计；单题地址显示该题。填空题查询参数page默认为1，必须是最多7位的ASCII正整数，每页最多20条。非法页码400，超过实际页数404；选择题只能使用第1页。

结果只包含本问卷state=submitted的记录和实际path中的有效答案。单选人数按账号去重；份数、选项占比、平均值和图表按 [统计结果说明](统计结果说明.md) 的口径计算。答案列表按submitted_at、_id倒序，不返回用户名、user_id、respondent_key或填写记录编号。切换图表在浏览器本地完成，不发起写入API。

## 状态码与错误

| 状态码 | 典型含义 |
| --- | --- |
| 200 | 页面、表单纠错辅助动作成功、规则试算、已提交回执 |
| 302 | GET导航或未登录跳转 |
| 303 | 注册、登录、保存、发布、关闭、首次提交等成功后的重定向 |
| 400 | 字段、格式、题型、页码、操作参数或CSRF不合法 |
| 401 | 用户名或密码错误；填写业务发现账号已不存在 |
| 404 | 编号无效、不存在、草稿分享入口、无权访问的问卷/回答/结果、越界页 |
| 405 | 使用未支持的方法，如GET /logout、GET发布或关闭 |
| 409 | 重复用户名、非草稿修改、重复发布、旧版本、禁止重复填写等冲突 |
| 410 | 问卷已截止或关闭、正式提交准入时问卷已变化 |
| 413 | 请求体超过64 KiB |
| 503 | MongoDB不可用，页面不展示内部连接细节 |

多个条件同时不满足时，以最先执行的检查为准，例如无效CSRF会在路由业务前返回400。表单型校验错误会回填非密码输入；涉及版本或开放状态的冲突需要刷新并重新进入。

## 第二阶段：题目布局

POST布局接口带revision（当前整卷摘要）和action=up/down/delete。上移、下移保留编号和题目内容；删除移除指定题目。三种成功操作均清空整卷jump_rules。首题上移、末题下移或未知动作400；过期摘要及已发布/关闭409；他人问卷404；缺少CSRF400。原子更新同时匹配旧题目数组、归属和draft状态。

## 第二阶段题目与题库参数

GET版本详情可用version选择版本、compare选择同题比较版本。新版本编辑通过base查询参数指定来源，题目字段沿用原添加题目表单，另有change_note（最多500字符）。没有原地覆盖版本的接口。restore无业务必填字段，始终新建版本并记录恢复源。

share带username和action=share/revoke；use带survey_id；select的POST带version_id，GET可带bank筛选本人题库。from-survey路径指定本人问卷及本地题目编号，保存来源映射而不重写原卷。

题库列表POST带name和description；题库详情POST须带revision及action。put带version_id（同题替换题库条目指定版本），remove带现有version_id，rename带name和description，delete只删除题库容器。过期摘要409，不覆盖他人或新的条目。

跨问卷统计GET可带page（正整数，最多100000），每页20条原文，只统计当前用户自建问卷中的submitted及实际到达题目。相同版本的多次出现按回答次数计数，正式提交总份数不重复；不将按问卷隔离的HMAC计数描述为跨问卷去重人数。授权撤销后本人已导入问卷的统计仍可查看。
