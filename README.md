# 智语外呼 · 商户资审工作台

面向商家资质审核驳回的业务工作台：审核任务、身份核验、实时语音、整改问答、京麦书面驳回和结构化回写。业务渠道由后端 RoutingPolicy 决定，业务事实与事件保存在 PostgreSQL。

## 目录

- `backend/app`：业务规则、知识、数据库、实时语音、工具守卫与 RSI。
- `backend/migrations`：数据库迁移；`backend/tests`：回归测试。
- `frontend`：三栏工作台、字幕、语音及书面处理界面。
- `scripts`：启动、知识导入、契约导出、检查和 RSI 入口。
- `infra`、`compose*.yaml`：容器与 HTTPS 运行配置。
- `docs`：机器契约、知识来源清单和第三方组件许可。

## 数据流与运行边界

麦克风 → WebRTC / aiortc → 原生音频理解 → RealtimeBusinessPort → 身份、版本与知识审核 → 流式 Qwen TTS → 浏览器播放。最终理解与审核消息通过持久事件 / SSE 更新字幕。

京麦任务走书面预览 → 正文哈希与版本校验 → 幂等工具执行 → 结果回写。通话结束不代表业务完成。默认 `VOICE_WRITES_ENABLED=false`，不以浏览器播放或 RTP 发完代替可信告知证明。

当前为单进程异步多任务架构。真实语音配置为 server_vad 400ms / threshold 0.5、Qwen-Audio-3.1-TTS-Flash WebSocket、longanhuan_v3.1。自然停顿、弱网/TURN、物理耳麦和多 worker 尚未完成完整验收。

## 安装与初始化

需要 Python 3.12、Node.js 22.22 以上和 PostgreSQL 18。使用锁文件安装：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.lock -r backend/requirements-rtc.lock
npm ci --prefix frontend
```

新环境可复制 `.env.example` 为 `.env`，填写数据库和供应商配置；已有 `.env` 请继续使用。环境变量优先于配置文件。数据库需事先创建，例如：

```powershell
$env:DATABASE_URL='postgresql+psycopg://zhiyu:local-dev-only@127.0.0.1:5432/zhiyu'
$env:WORKBENCH_DATABASE_URL=$env:DATABASE_URL
$env:PYTHONPATH='backend'
.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head
.venv/Scripts/python.exe -c 'from app.seed import seed_workbench; seed_workbench()'
```

种子数据为合成演示数据。真实语音工作台使用 `workbench-reviewed-20261125-v3`，首次运行还需从团队私有交付包导入已审核知识：

```powershell
.venv/Scripts/python.exe scripts/publish_reviewed_knowledge.py private-evidence/business/staging.json private-evidence/business/reviewed-release-v3-final.json --publish
```

私有审核包不在公开源码中。导入会核对来源哈希、正文、审核范围及版本；同一发布版本不可覆盖修改。

## 启动与使用

已有数据库、已发布 v3 知识及供应商凭据时：

```powershell
./scripts/start_workbench_demo.ps1 -Restart -Build
# 后续无需重建时省略 -Build；回退原 HTTP TTS：
./scripts/start_workbench_demo.ps1 -Restart -Rollback
```

打开 `http://localhost:13000`。本机工作人员账号 `demo`，默认密码 `staff-local-only`，可用 `STAFF_PASSWORD` 覆盖。后端地址 `http://127.0.0.1:18000`。供应商凭据从根 `.env` 或 `VOICE_CONFIG_ENV_FILE` 读取；启动和查看任务不发起云调用，点击“开始通话”后才调用供应商。

选择电话任务，在开发设置中选择“WebRTC 实时语音”，新建匹配的语音会话，允许麦克风后开始通话。例如任务1：

1. “我是演示商家1的联系人。”
2. “补齐材料一定必过吗？”
3. “这个结论的依据是什么？”

播报中可说话或点击“打断播报”。字幕显示服务端最终理解，暂不提供临时识别字串。京麦任务显示书面预览及执行结果。旧语音和文字方式保留在开发设置中。

启动脚本只重启归属已核验的工作台进程，不清空数据库或调用账本。运行限额来自现有配置；不要将供应商凭据、Cookie 密钥、私有知识或数据库提交到 Git。

## 容器运行

```powershell
docker compose up --build -d
```

基础模式是离线合成演示，页面 `http://localhost:3000`，后端 `http://127.0.0.1:8000`。真实语音额外使用 `compose.cloud.yaml`、`compose.realtime.yaml`，并在本地配置 `.env` 中的云调用授权、供应商凭据、申请单白名单及 `REALTIME_SPEECH_BACKEND=qwen_ws`。不要启用语音业务写入。

公网 HTTPS 使用 `compose.public-ip.yaml` 与 `infra/Caddyfile.ip`，需配置有效 IP 证书、PUBLIC_IP、独立密码及 Cookie 密钥。`docker compose down` 停止服务；不要使用 `-v` 删除已有数据卷。

## 检查与契约

先创建并迁移独立的测试数据库；以下命令只应指向 `_test` 库，测试会清空该库的业务表：

```powershell
$env:DATABASE_URL='postgresql+psycopg://zhiyu:local-dev-only@127.0.0.1:5432/zhiyu_test'
$env:APP_ENV='test'
$env:RUN_DB_TESTS='1'
$env:PYTHONPATH='backend'
.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head
.venv/Scripts/python.exe -m pytest backend/tests -q
npm run typecheck --prefix frontend
npm run build --prefix frontend
.venv/Scripts/python.exe scripts/export_realtime_contract.py --check
```

浏览器回归运行 `npm run test:e2e --prefix frontend`，完整工作台回归可使用 `scripts/verify_workbench.py`。

公共 DTO 以 Pydantic 为源，使用 `scripts/export_contracts.py` 和 `npm run types --prefix frontend` 生成契约与类型；实时内部契约由 `scripts/export_realtime_contract.py` 生成。RSI 入口为 `scripts/rsi_run.py`，不会将缺少语音、成本或安全证据的候选自动采纳。

相同发布配置已有固定音频真实 RTC 30轮记录：P50 1774.2ms、P95 1971.3ms、30/30有效。记录不代表物理扬声器时延或全部赛题通过。完整原始证据、录像和清理前源码保存在此前交付包及 Git 历史；本次精简未新增付费云调用。