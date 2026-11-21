# 智语外呼 · 文本联调骨架 0.3.0

京东 POP 商户资质审核驳回告知与整改指导的**合成演示骨架**。Next.js＋TypeScript、FastAPI＋Pydantic、PostgreSQL、REST＋SSE。默认FakeModel，不需要云账号，不执行真实外呼。

代码已搭建。本机Windows虚拟化组件已修复，重装Docker Desktop 4.99.0后容器部署通过：PostgreSQL、后端和前端均已启动，三轮浏览器业务流程、回写、刷新恢复及容器重启持久化验证通过。此前58项后端测试的记录及本次容器验收详见 [验证记录](docs/verification.md)。

## 先看哪里

- [当前实现与三人接入说明](docs/implementation.md)：模块入口、0.3契约和未实现范围。
- [完整项目设计](docs/design/README.md)：业务目标、状态机、数据与RSI设计。
- [魏鼎承交付](docs/design/handoffs/wei.md)／[翟雯交付](docs/design/handoffs/zhai.md)。
- [公共DTO](backend/app/contracts.py)、[内部接口](backend/app/ports.py)、[OpenAPI](docs/design/contracts/openapi.json)。

## 当前不需要Docker就能做的事

要求Python 3.12、Node 22.22以上。项目已配置锁文件，不使用latest安装。

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.lock
npm ci --prefix frontend --cache .cache/npm --registry https://registry.npmjs.org
./scripts/dev.ps1 Check
```

本机现有`.venv`使用Python 3.12，不必重复创建。默认检查会明确跳过数据库测试。只看前端页面：

```powershell
./scripts/dev.ps1 Frontend
```

浏览器访问 http://localhost:3000 。无后端／数据库时可以看到登录页面，但不能完成登录或业务流程。这里没有偷偷改成SQLite或浏览器伪造业务成功。

## 不用Docker连接独立PostgreSQL

先准备PostgreSQL 18和空数据库zhiyu；后端不会替你创建数据库。将连接凭证放在本地环境变量，密码特殊字符需URL编码，不提交到Git。

```powershell
$env:DATABASE_URL='postgresql+psycopg://zhiyu:local-dev-only@127.0.0.1:5432/zhiyu'
$env:APP_ENV='development'
$env:PUBLIC_ORIGIN='http://localhost:3000'
./scripts/dev.ps1 Migrate
./scripts/dev.ps1 Backend
```

另外一个终端运行 `./scripts/dev.ps1 Frontend`。后端端口8000，文档 http://localhost:8000/docs ，健康检查 `/health/live` 和 `/health/ready`。后端单worker；迁移／种子可重复运行。

开发登录用户demo，默认密码`demo-local-only`；用DEMO_PASSWORD覆盖。页面选择申请单1、创建会话、开始，然后依次输入：

1. `我是演示商家1`
2. `补齐材料一定必过吗`
3. `明白了`

应看到模拟处理结果及“通话记录已回写”，刷新后恢复。身份核对前不展示具体驳回原因。默认密码和演示登录仅供localhost，不用于公网部署。

## Docker统一启动

```powershell
Copy-Item .env.example .env
# 修改本地配置后启动。不要覆盖已有.env。
docker compose up --build -d
```

浏览器仍访问 http://localhost:3000 。Compose自动执行迁移与种子，数据库使用持久卷。停止使用 `docker compose down`，不要加 `-v`，以免删除数据。

热更新：`docker compose -f compose.yaml -f compose.dev.yaml up --build`。开发前端使用独立node_modules卷，依赖变化后在容器内执行npm ci。

`.env`由Compose读取；本机后端也会加载根目录.env，终端环境变量优先。修改.env后重启后端。

## 类型与测试

```powershell
./scripts/dev.ps1 Types
./scripts/dev.ps1 Check
```

Pydantic为唯一手写结构源。Types导出JSON Schema／OpenAPI，再生成frontend/app/api.generated.ts。不要手改生成文件。

真实数据库测试需要专用数据库，名字以`_test`结尾；**测试会清空其中的业务表**：

```powershell
$env:DATABASE_URL='postgresql+psycopg://zhiyu:local-dev-only@127.0.0.1:5432/zhiyu_test'
$env:APP_ENV='test'
$env:RUN_DB_TESTS='1'
$env:PYTHONPATH='backend'
.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head
.venv/Scripts/python.exe -m pytest backend/tests -q
```

浏览器验收也使用专用测试数据库。先运行 `python scripts/reset_test_db.py`，用该数据库启动后端与前端，再执行：

```powershell
cd frontend
npx playwright install chromium
npm run test:e2e
```

CI提供相同的PostgreSQL测试和浏览器验收。工作流已编写，尚未上传GitHub运行。

## 交付范围

已实现模块替身与连接点，不是完整比赛业务。预约与人工意图先转人工状态，未创建外部工单；语音、真实模型、知识审核管理台和RSI作业未实现。取消只停止当前生成，已登记派发的业务操作继续查证。

不提交.env、真实赛题数据、未脱敏日志、录音和保留评测集。旧套餐方案保留在docs/archive，仅作历史资料。

复验完整数据流：准备专用`_test`数据库、完成迁移和前端构建，并安装Playwright Chromium后，运行 `.venv/Scripts/python.exe scripts/verify_browser_flow.py`。脚本会清空该测试库，自动启动／停止前后端并生成 [数据库事实证据](docs/flow-evidence.json)。

## 语音／真实模型增量（2026-11-18）

声网RTC／RTM语音组件、DeepSeek适配和自定义模型入口已经加入。配置与真实验收状态见 [接入说明](docs/voice-integration.md)。根目录`.env`已在本机生成，账号字段由本人填写；仓库仅提交`.env.example`。检查配置运行 `python scripts/cloud_doctor.py`，不会显示密钥或调用云服务。

公网与声网操作顺序见 [腾讯云部署说明](docs/public-deployment.md)。目标为11月25日前联调。用户已授权停止旧Nginx和3000端口Node服务，以本项目为主；释放80／443／3000后使用独立Caddy与compose.public-ip.yaml。compose.nginx.yaml保留为需要保留旧Nginx时的备选，本轮不混用。所有公网路径都需要实际服务器和声网云端验收。

本机Docker容器验收已通过，证据见 [Docker验收记录](docs/docker-evidence.json)。此前针对旧socket登记的开机改名任务已取消；正常启动无需运行故障修复脚本。固定公网三轮语音、真实模型及TTS接入仍待配置和实测。
