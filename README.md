# 智语外呼 · 开发与联调骨架 0.3.0

2026-11-24：已整合v0.4 WebRTC实时交互与业务门面，补齐绑定、可信输入登记和主动开场接线。新模式默认关闭；离线验证通过尚不代表真实语音2秒、可信播放或完整语音闭环达标。当前数据流、架构、测试证据、赛题缺口及改进顺序见[双PR整合验收](docs/v04-integration-20261124.md)。

2026-11-17 Agent增量的当前能力、复验结果、启动方式和剩余联调事项见[Agent交付说明](docs/agent/DELIVERY.md)。下文保留原骨架的环境与使用说明。

京东 POP 商户资质审核驳回告知与整改指导的**合成演示骨架**。Next.js＋TypeScript、FastAPI＋Pydantic、PostgreSQL、REST＋SSE。默认FakeModel，不需要云账号，不执行真实外呼。

已有可运行的文本链路、模块替换接口及语音适配代码。本地三轮文本流程、结构化回写、刷新恢复与容器重启持久化已验证；2026年11月21日完成腾讯云联调环境部署。魏的语义Agent与翟的数据／Mock／前端交付已整合，合并后本地验收及待联调项见 [合并记录](docs/pr-integration-20261122.md)；公网服务器仍为此前版本，完整语音链路仍待联调。测试范围与历史记录见 [验证记录](docs/verification.md)。

## 当前状态（2026-11-21）

| 内容 | 实际状态 |
|---|---|
| 公网联调入口 | https://192.144.214.34 ，页面返回200 |
| 后端就绪检查 | https://192.144.214.34/health/ready ，返回`{"status":"ready"}` |
| 部署服务 | PostgreSQL、FastAPI、Next.js、Caddy四个容器运行；仅网关发布80／443 |
| 登录与配置 | 公网使用`STAGING_PASSWORD`；未登录申请单查询返回401，开发默认密码不适用于公网 |
| 自动恢复与证书 | Docker开机启动、容器自动重启已配置；证书续期演练及Caddy重载通过，每天检查两次 |
| 模型与语音 | DeepSeek文本、百炼Qwen TTS各一次独立真实探针通过；声网端到端语音、三轮对话和打断尚未验收 |

当前服务器使用`MODEL_PROVIDER=fake`，`CLOUD_CALLS_ENABLED`、`VOICE_ENABLED`、`VOICE_WRITES_ENABLED`与`ENABLE_FAULTS`均为`false`。访问页面不会自动启用收费模型或语音。公网环境用于联调，队友继续按公共契约独立开发，无须等待正式页面和Agent完成才维护服务器。

公网登录账号为`demo`，密码由部署负责人通过私密渠道提供。不要提交或分享服务器`.env`、SSH私钥、API Key。部署事实记录见 [公网验收证据](docs/public-deployment-evidence.json)，后续操作见 [部署说明](docs/public-deployment.md)。

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

翟雯分支的原件转换、三项授权场景、Mock 故障复现和架构接入补丁见[数据、Mock 与前端交付说明](docs/zhai-rework.md)。

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

CI配置包含PostgreSQL测试和浏览器验收，具体提交是否通过以GitHub Actions对应运行结果为准；本地通过不代表云端CI通过。

## 交付范围

已实现模块替身与连接点、DeepSeek适配、声网接入代码和Qwen TTS协议转换；语义Agent、RSI评测模块与数据／Mock／前端交付已整合；完整联系流程、知识审核管理台及正式发布仍待验收。预约与人工意图先转人工状态，未创建外部工单。取消只停止当前生成，已登记派发的业务操作继续查证。服务器部署通过不代表正式业务或语音验收通过。

不提交.env、真实赛题数据、未脱敏日志、录音和保留评测集。旧套餐方案保留在docs/archive，仅作历史资料。

复验完整数据流：准备专用`_test`数据库、完成迁移和前端构建，并安装Playwright Chromium后，运行 `.venv/Scripts/python.exe scripts/verify_browser_flow.py`。脚本会清空该测试库，自动启动／停止前后端并生成 [数据库事实证据](docs/flow-evidence.json)。

## 语音／真实模型增量（2026-11-18）

声网RTC／RTM语音组件、DeepSeek适配和自定义模型入口已经加入。配置与真实验收状态见 [接入说明](docs/voice-integration.md)。根目录`.env`已在本机生成，账号字段由本人填写；仓库仅提交`.env.example`。检查配置运行 `python scripts/cloud_doctor.py`，不会显示密钥或调用云服务。

公网与声网操作顺序见 [腾讯云部署说明](docs/public-deployment.md)。目标为11月25日前完成联调。当前采用固定公网IP、可信IP证书和独立Caddy，使用`compose.yaml`＋`compose.cloud.yaml`＋`compose.public-ip.yaml`；不混用域名版或Nginx备选配置。公网HTTPS已验证，声网云端调用自研接口仍需真实验收。

本机Docker容器验收已通过，证据见 [Docker验收记录](docs/docker-evidence.json)。DeepSeek和百炼TTS独立探针通过仅证明各自接口可调用，不能证明语音时延、抗噪或打断达标。再次运行真实探针会产生新的供应商请求。

## 队友模块合入后的部署更新

魏鼎承与翟雯继续在各自分支实现约定端口，提交PR后运行类型、业务回归及CI检查。公共DTO或端口发生变化时，同一PR更新Schema、前端生成类型和交付说明；业务模块内部修改不要求改动语音适配层。

由部署负责人构建并上传确认版本的前后端镜像，在服务器`/opt/zhiyu`导入镜像后执行：

```bash
sudo docker load -i zhiyu-images.tar
sudo docker compose -f compose.yaml -f compose.cloud.yaml -f compose.public-ip.yaml config --quiet
sudo docker compose -f compose.yaml -f compose.cloud.yaml -f compose.public-ip.yaml up -d --no-build
sudo docker compose -f compose.yaml -f compose.cloud.yaml -f compose.public-ip.yaml ps
curl --fail --show-error https://192.144.214.34/health/ready
```

随后验证本次变更涉及的真实模块流程。涉及数据库迁移时先备份；保留服务器私有`.env`、证书目录与PostgreSQL数据卷，不用本地开发配置覆盖，不执行`down -v`。修改Compose环境配置后使用上述`up`命令重新创建受影响容器，单独`restart`不会应用新的环境变量。

证书由`infra/renew-ip-cert.sh`经webroot续期并重载Caddy，定时任务在服务器`/etc/cron.d/zhiyu-ip-cert`，日志在`/var/log/zhiyu-ip-cert-renew.log`。当前初次证书到期时间为2026-11-27 17:10 UTC；续期演练已通过，未来正式续期结果以任务日志和实际证书为准。
