# Agent交付与联调入口

2026-11-17。本分支基于团队`aa9b2d1`，整理自GLM实现且经独立复验的源码版本`30ec6e9`。干净交付分支为`codex/wei-agent-delivery-20261117`，保留原开发分支与夜间运行证据；没有合并main。

## 已经能运行的部分

- 沿用FastAPI、PostgreSQL、Next.js、REST/SSE和既有内部端口。Agent生成决策，协调器检查守卫、统一执行工具和回写；没有另建业务Agent服务。
- 结构化意图/槽位理解与原文依据校验、已发布知识约束、身份核对、路由策略、状态转移与业务守卫已接入。
- 驳回幂等按申请单、场景、审核版本和动作绑定，不随联系/提交次数变化；兼容两种已持久化的旧键。派发后结束对话，业务仍继续查证和回写。
- 前端读取后端权威状态，展示模拟业务结果与回写记录，刷新可恢复。
- RSI提供开发集、模拟器、归因、受限候选、gate-v2、采纳/拒绝与版本回滚机制。当前证据不足，保留拒绝采纳结果。

## 独立验证结果

| 检查 | 结果及口径 |
|---|---|
| 后端 | 真实PostgreSQL，152 passed、0 failed、0 skipped；包括11项独立边界回归 |
| 前端 | TypeScript和Next生产构建通过 |
| 浏览器 | 本机Chrome三轮文本、身份、免责声明、模拟处理、结果回写、刷新恢复：1 passed |
| 真实GLM | 修复后3轮、3请求、0失败/0重试，最终CLOSED、1条模拟业务效果和1条记录 |

浏览器使用FakeModel保证可重复；真实GLM验证运行产品Services/协调器和独立PostgreSQL。真实模型仅负责理解，回复是有依据的模板；业务端是合成Mock。上述结果不代表真实京麦或语音验收。五类修复说明见[独立复验](CODEX-RECHECK.md)。

## 启动和演示

先按[README](../../README.md)准备Python3.12、Node22.22以上和自己的PostgreSQL；依赖版本沿用锁文件。迁移、种子与开发启动使用原有`scripts/dev.ps1`，不要重建或清空既有数据库。

```powershell
$env:DATABASE_URL='postgresql+psycopg://YOUR_USER:YOUR_PASSWORD@127.0.0.1:5432/zhiyu'
$env:APP_ENV='development'
$env:PUBLIC_ORIGIN='http://localhost:3000'
$env:ZHIYU_MODEL_MODE='fake'
./scripts/dev.ps1 Migrate
./scripts/dev.ps1 Backend
# 另一终端，项目根目录：
./scripts/dev.ps1 Frontend
```

登录默认演示密码`demo-local-only`，选择合成申请单1，创建会话、开始；依次输入“我是演示商家1”“补齐材料一定必过吗”“明白了”。应完成模拟处理并显示回写结果；刷新仍可查看。

真实GLM使用`ZHIYU_MODEL_MODE=glm`，密钥只从已有`GLM_API_KEY`环境变量读取。端点、模型别名和时间限制以`backend/app/guard.py`中的`load_model_config()`为准；不得把密钥写进仓库。默认fake服务不会自动消费真实模型额度。

## 复验与RSI

数据库回归会清空测试业务表，必须使用独立、名称以`_test`结尾的库，迁移完成后再运行：

```powershell
$env:DATABASE_URL='postgresql+psycopg://YOUR_USER:YOUR_PASSWORD@127.0.0.1:5432/zhiyu_test'
$env:APP_ENV='test'
$env:RUN_DB_TESTS='1'
$env:PYTHONPATH='backend'
$env:ZHIYU_MODEL_MODE='fake'
.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head
.venv/Scripts/python.exe -m pytest backend/tests -q --tb=short
npm run typecheck --prefix frontend
npm run build --prefix frontend
.venv/Scripts/python.exe scripts/rsi_run.py --suite dev --repeats 3 --max-requests 240 --max-real-requests 0
```

上面的RSI命令只做确定性模拟，不调用真实模型。公开分支不包含保留集正文、版本运行账本或夜间原始报告。协调者需从私人交付附件恢复`backend/app/rsi/scenarios/holdout.json`与`holdout-v2.json`后，才能复验gate；这两份历史集合都已暴露，不能重新当成无偏提升证据。缺保留集时gate报缺文件，不会自动通过。不得为通过而降低门槛或清除污染登记。

## 联调分工与剩余事项

魏交付Agent/Policy/Guard/RSI；协调器、公共契约和前端有必要的配套改动，交王、翟审核。AgentPort、BusinessPort签名沿用骨架，公共DTO的新增字段、JSON Schema、OpenAPI及前端类型一起交付，最终兼容性由王审查确认。

- 王：审核协调器、旧键兼容和公共契约；接真实ModelPort、语音和部署环境；联调后决定合并。
- 翟：核对发布知识、申请单与业务Mock/真实端口、前端状态展示；处理资料缺项与冲突。
- 魏：补齐可信预约确认到实际派发链路（目前正常对话的预约仍转人工）、未暴露的新保留评测和真实语音/成本证据。

真实RTC/ASR/TTS、官方沙箱和统一计量资源尚未完成接入，RSI未获采纳。工作台原生审查仍有整目录证据收集限制；本分支不宣称工作台最终accepted。GitHub现有连接对原仓库只有读取权限，远程分支与PR尚未创建。本地可审源码和传输包不能代替远程PR或团队合并审核。
