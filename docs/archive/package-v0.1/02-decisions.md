# 技术选型与版本基线

本节是工程决策，不是依赖已安装或性能已通过的声明。2026-11-16 的 PyPI／npm 官方元数据快照见 [dependency-research.json](dependency-research.json)。当前无代码和锁文件；实现首日按候选组合执行安装、构建及接口冒烟，提交 `uv.lock`、`package-lock.json` 和镜像摘要后才形成实际基线。

## 技术决策记录

| ADR | 选择与理由 | 替代与成本 | 本版处理 |
|---|---|---|---|
| 01 | Next.js App Router＋TypeScript；符合翟雯分工，公共类型可生成 | Vite SPA 更轻；现阶段没有迁移收益 | 一个页面＋可折叠调试／评测区；RTC 仅 Client Component 动态载入，不在 SSR 初始化 |
| 02 | Python 3.12＋FastAPI＋Pydantic 2；一个进程内模块调用 | 微服务增加部署、网络故障与追踪负担 | 单体、一个 Uvicorn worker；开发接口与管理接口显式隔离 |
| 03 | 显式状态机＋确定性工具权限 | LangGraph 可提供持久化工作流能力，但仍需同样的状态和权限设计 | 首版不用；仅在长任务恢复／复杂分支出现明确收益后另立 ADR，不排期迁移 |
| 04 | 声网 RTC＋对话式 AI Engine Main v2；ASR/TTS 交给云端 | 自建语音链路工作量大；其他 RTC 云需重测接入 | 首日公网验证；RTM 为字幕和实时事件的配套依赖，不是自建消息队列 |
| 05 | DeepSeek `deepseek-flash`，显式关闭 thinking，HTTPX 流式适配 | 较强模型可能增加成本和延迟；备选供应商只通过同一适配层接入 | 默认 `max_tokens=256`；不把 reasoning 内容发送 TTS；模型可配置，不能由浏览器指定 |
| 06 | PostgreSQL 18 系列＋SQLAlchemy＋Alembic | 相比 SQLite 多一个数据库服务和备份任务；换来多写入者、事务锁及接近部署环境的测试 | Compose 一个私有 DB；低并发演示仍保留 DB 唯一约束，不能只依赖 Python 锁 |
| 07 | HTTP 命令＋SSE 业务通知 | WebSocket 要自行管理重连、鉴权及协议；当前上行低频 | 浏览器 HTTP POST 上报中断／遥测即可；不用业务 WS，不转发音频到 FastAPI |
| 08 | DB 事实＋结构化脱敏日志，Langfuse 可选导出 | 自托管 Langfuse 带来 ClickHouse、对象存储及相关基础设施 | 校赛不自托管；外发许可明确时用 Cloud；无 Cloud 仍可复盘与评测 |
| 09 | Promptfoo 调用完整 Agent 模拟器＋确定性断言 | 仅直接评测 LLM 文本不能检验状态机和工具 | 本地离线 CLI，不进入实时链路；不为它增加在线服务 |

依据：[Next.js 安装要求](https://nextjs.org/docs/app/getting-started/installation)、[FastAPI 发布记录](https://fastapi.tiangolo.com/release-notes/)、[DeepSeek 模型清单](https://api-docs.deepseek.com/quick_start/pricing/)、[DeepSeek thinking 开关](https://api-docs.deepseek.com/guides/thinking_mode/)。DeepSeek 当前文档列出 `deepseek-flash` 与 `deepseek-v4-pro`，旧文档的 `deepseek-chat` 不作为新设计默认值。别名仍可能滚动升级，每次实验记录请求名称、实际响应 model 和日期，不能承诺冻结云模型权重。

## 候选依赖组合

| 组件 | 设计候选 | 核查与约束 |
|---|---|---|
| Python | 3.12 系列 | 满足本次 FastAPI、SQLAlchemy 元数据中的 Python ≥3.11；容器补丁版与摘要在建骨架时固定 |
| Node.js | 22 系列且 ≥22.22.0 | Next.js 要求 ≥20.9，但候选 Promptfoo 要求更高，统一按较高要求 |
| Next.js／React／React DOM | 16.4.6／19.3.1／19.3.1 | npm 元数据依赖范围匹配；实际构建仍待验收 |
| TypeScript | **5.9.3** | 虽然查询到 latest=7.0.2，`openapi-typescript@7.13.0` 的 peer 为 `^5.x`，不盲目安装 latest |
| 声网 RTC Web | `agora-rtc-sdk-ng@4.24.9` | npm 最新候选；官方发布页快照可滞后，最终以安装包和实测为准 |
| 声网 Signaling | `agora-rtm@2.3.0` | peer 要求 RTC `^4.24.3`，与上述候选匹配；不同时安装另一个包名的 RTM 实例 |
| 声网客户端工具包 | `agora-agent-client-toolkit@2.9.0` | 官方 Web API 安装页指定；已核对 peer 为 `agora-rtm>=2.0.0`、RTC `>=4.23.4`、Node≥20；先用 TS 核心包，React 封装不是必须 |
| FastAPI／Pydantic | 0.163.1／2.14.3 | PyPI 候选；本轮契约示例用环境已有 Pydantic 2.14.2 验证，不能称候选组合已经集成通过 |
| Uvicorn／HTTPX | 0.54.0／0.28.1 | 后端服务与取消友好的异步 HTTP；复用连接池，不每 token 建连接 |
| SQLAlchemy／Alembic／Psycopg | 2.1.8／1.20.1／3.3.9 | DB 驱动、迁移；SQLAlchemy async session 每请求／任务独立，禁止并发共享 |
| openapi-typescript | 7.13.0 | 生成前端类型；类型检查不等于运行时请求校验 |
| Promptfoo | 0.125.1 | 离线开发依赖；锁定并提交可复现实验配置 |
| Langfuse Python SDK | 5.2.0，仅可选 | 不能照搬旧 SDK 示例；先验证最小 trace 导出，无账号不阻塞核心 |

工具包依据：[声网 Web toolkit API](https://docs.agora.io/en/api-reference/api-ref/conversational-ai/client-toolkit/web)。`agora-conversational-ai` 这个猜测包名查询失败，不使用。初查的 `agora-rtm-sdk` 虽可查询到，但工具包声明 peer 为 `agora-rtm`，因此候选修正为后者，避免安装成功却缺少 peer 或出现两套 RTM。首次实现严格区分 Engine Main 的 `join` 配置与 Platform Preview 的 `create-agent` 配置，禁止混用字段。

本轮不生成假锁文件。安装后检查依赖冲突、许可证、浏览器 SDK build、三轮语音与取消；失败时选同一大版本内已验证组合并记录原因。11 月 20 日后冻结，不临赛全量升级。

语音供应商暂定：国内 ASR 用默认凤鸣 `fengming`、语言 `zh-CN`；TTS 优先微软中文声音，区域／voice_name 从已有账号的可用清单选择并记录。微软 TTS 需对应凭证，若团队已有声网支持的其他中文 TTS（如 MiniMax），只替换语音配置并重测，不改业务架构。声网国内发布说明确认默认凤鸣与微软 TTS 支持；第三方 ASR 需补齐 params，不能把国际文档的 `ares` 默认值直接套到国内。未拿到资源清单前不捏造可用音色或已开通额度。[国内发布说明](https://doc.shengwang.cn/doc/convoai/restful/overview/release-notes)

## PostgreSQL 与 SQLite 的明确取舍

首版建议 PostgreSQL：三人共享联调环境、操作幂等、后台查证与日志写入会并行，数据库锁与唯一约束能直接支持设计。演示主机建议起点 2 vCPU／4GB 内存，无 GPU；这是团队容量预算，需实际压测。只在私有网络暴露 DB，挂载持久卷，每日备份并做一次恢复测试。

数据库不是数据隔离方案。每次读取按 `owner_id/session_id` 限定，浏览器不接数据库；模型不能传任意 customer_id，工具从服务端验证记录取得主体。数据库账号最小权限，评测使用独立数据库，迁移账号与应用账号分开。PostgreSQL 的行级安全需要明确启用和策略配置，不能假设默认存在。[PostgreSQL RLS](https://www.postgresql.org/docs/18/ddl-rowsecurity.html)

如果 11 月 17 日 PostgreSQL 部署仍阻塞，可在王记录 ADR 修订后暂用 SQLite，本轮不要求用户再次审批常规工程选择。收益：无 DB 服务、单文件启动快。限制：本地磁盘、单后端进程、单写入者；不能把文件放网络共享盘。使用外键约束、短事务、busy timeout、WAL 和数据库级 CAS，最多 3 路内部测试，不声称支持横向扩展。[SQLite WAL](https://sqlite.org/wal.html)

影响：测试必须覆盖 SQLite 真实事务；后续迁 PostgreSQL 要迁 schema／JSON／时间类型、唯一约束和数据，再复测锁、恢复、幂等和订单查证。SQLAlchemy 不能消除两种数据库的语义差异。本设计的 `FOR UPDATE` 主路径必须在 SQLite 改为短 `BEGIN IMMEDIATE`／条件更新，网络调用不可放锁内。不同时双写两种数据库。

## 延迟与费用控制

语音链路最多一个 LLM 意图／工具建议调用；有工具事实后的关键回复优先由审核模板生成，避免每轮再等第二次 LLM。非关键开放式回答可按句缓冲校验再流式发送。价格、可办理范围、身份通过和订单成功话术只能从代码／工具事实生成，不能先播“办理成功”再执行。

成本按实际账单记录：RTC 计费时长＋AI Engine／ASR／TTS 费用＋LLM 输入输出 token＋公网主机／DB／可选观测费用。不同供应商可能含打包计费，不能重复相加。默认单通话 10 分钟、每用户 1 通、全环境 3 通、单用户每分钟 30 次控制请求、离线评测并发 1。配额告警 80%、达到已配置预算停止新通话／新评测，已提交订单查证与回写继续；金额由用户资源信息确定，不虚构免费额度。

## 许可证与资料边界

FastAPI／Pydantic／Next.js／React／SQLAlchemy／Alembic／Promptfoo 等本次元数据显示 MIT；TypeScript 为 Apache-2.0，HTTPX／Uvicorn 为 BSD-3-Clause，Psycopg 为 LGPL-3.0-only。声网 RTM 元数据只写 `Apache`，正式清单须查具体包 LICENSE，不能直接推断版本。SDK 开源许可证不等于云服务使用条款。DeepSeek 本版使用云 API，不声称自托管模型，也不把云 API 条款等同于模型权重许可证。

正式交付清单应包含实际锁定版本、来源、LICENSE 文件／条款链接与用途，保留 NOTICE。Langfuse Cloud／自托管区别参见 [官方基础设施说明](https://langfuse.com/self-hosting/deployment/infrastructure/clickhouse)。官方业务资料和未公开评测数据禁止公开上传；合成数据也先脱敏再导出。
