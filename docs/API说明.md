# API说明

本文集中说明当前Flask网页接口，与 [系统说明](系统说明.md)、[数据库设计](数据库设计.md) 和 [关键逻辑说明](关键逻辑说明.md) 配套。接口返回HTML页面或重定向，使用普通表单提交，不提供独立JSON REST接口。

## 通用约定

本机基础地址为 `http://127.0.0.1:5000`。survey_id和response_id是MongoDB ObjectId的24位十六进制文本；question_id和选项编号为服务器生成的字符串。下面路径中的尖括号表示变量。

浏览器使用Flask签名会话Cookie维持登录。所有POST都必须带当前会话表单中的csrf_token；先GET表单并保留Cookie，再提交令牌，不能使用其他会话的令牌。表单编码为application/x-www-form-urlencoded，多选以同名字段重复传值。复选框选中值为on，不选时省略。应用请求体上限64 KiB，超限返回413。

未登录访问受保护页面一般302跳转登录。分享入口额外设置next，登录成功回原问卷；next只允许本站固定格式 `/survey/24位十六进制编号`，不会任意跳回其他页面。以下POST成功通常303重定向，表单纠错、规则试算和已经提交的回执有明确例外。

## 全部业务路由

本表覆盖18个业务路径，未列Flask自动生成的HEAD、OPTIONS以及静态资源GET接口。

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
| POST | /surveys/<survey_id>/deadline | 问卷创建者；草稿或已发布 | 保存成功303到详情 |
| POST | /surveys/<survey_id>/publish | 问卷创建者；有效草稿 | 发布成功303到详情 |
| POST | /surveys/<survey_id>/close | 问卷创建者；已发布 | 关闭成功303到详情 |
| GET | /survey/<survey_id> | 登录填写者访问分享入口 | 开放时200；草稿404；已关闭/截止410 |
| POST | /survey/<survey_id>/start | 登录填写者 | 创建或恢复本人草稿，303到填写页 |
| GET, POST | /survey/<survey_id>/fill/<response_id> | 本次回答所属账号 | GET显示当前题、确认页或回执200；保存/返回/首次提交成功303；已提交记录直接显示回执200 |
| GET | /surveys/<survey_id>/results | 问卷创建者 | 整卷统计200 |
| GET | /surveys/<survey_id>/results/questions/<question_id> | 问卷创建者 | 单题统计及填空答案分页200 |

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
| single_choice | options_text，每行一个选项，至少两个 | type=single_choice |
| multiple_choice | options_text、min_choices、max_choices | type=multiple_choice |
| text | min_length、max_length | type=fill_blank、input_type=text |
| number | min_value、max_value、integer_only=on | type=fill_blank、input_type=number |

上下限为空表示不限制，包含边界；数量和长度为非负整数。文本长度按Unicode码点计数。最少不能大于最多，多选限制不能超过选项总数；必答配置须有可填写的值。

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
