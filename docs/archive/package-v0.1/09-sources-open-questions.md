# 来源核对与待确认事项

核查日期：2026-11-16。本轮读取两份附件、检查空工作目录，并查阅声网／Agora、DeepSeek、框架官方资料和 npm／PyPI 元数据。用户已确认尚无真实代码，只做文档；已有部分云资源，信息稍后补充。未申请账号、未向云服务发起通话、未验证性能、未上传 GitHub。

## 附件依据

| 来源 | 定位 | 本设计采用的内容 |
|---|---|---|
| 《赛题规则》PDF，用户文件名 `e8439dbae8450df82d1d0b727b0fe9a9(1).pdf` | p1 封面 | 智语外呼赛题名称与主办企业信息 |
| 同上 | p2–3 考察点及技术创新 | 实时语音、任务 Agent、幂等一致性、日志、可复现和 RSI 方向 |
| 同上 | p4 比赛形式及材料 | 企业赛 3 分钟演示／约 7 分钟答辩；代码、环境、方案、视频、自测及许可证 |
| 同上 | p5 基础完成门槛 | ≥3 轮、P50≤2s、完整任务、无严重合规违规 |
| 同上 | p6 评分及数据边界 | 抗噪、纠错、工具／回写、质检；合成／脱敏数据，不爬取、不真实号码外呼 |
| 同上 | p7–8 资料与安全 | 官方业务包和测量工具后发，资料不得外传，现场禁止私自外网穿透／代理 |
| 《项目信息》DOCX | 「赛题目标」「时间节点」「分工」 | 团队目标、11 月 22 日校赛、三人职责与三阶段安排 |
| 同上 | 「可能的技术路线」「阶段一／二／三」 | 作为参考建议，逐项核对后采纳或纠正，不直接复制代码 |
| 用户本轮共识及答复 | 对话 | 任务范围、负责人、权限与确认规则、无代码阶段、资源部分具备 |

附件保留在用户给定原始路径，不复制整份到未来公开代码库。文档内涉及赛事的内容是对提供 PDF 的项目设计提炼，正式最新通知／官方业务资料到达后需更新需求矩阵。

## 对参考方案的修正

| 参考内容 | 修正及原因 |
|---|---|
| 前端生成 conversation_id，内存 dict 存状态 | 后端生成 session_id，DB 权威，内存仅活动任务；防串会话和重启丢事实 |
| `user_confirmed=true` 随提交请求传入 | 移除公共提交入口；服务端确认记录绑定报价、输入证据、核验和版本 |
| “是本人”即确认／尾号核验 | 使用合成单次挑战；不声称可用于真实身份认证 |
| 后期为了展示技术迁 LangGraph | 不做预定迁移；显式状态机首版足够，框架不是权限保障 |
| 后端 HTTP/WebSocket 接声网表述混杂 | 自定义 LLM 明确为 Chat Completions HTTP/SSE；浏览器 RTC 音频 |
| 只停播放／清空被打断回复 | 生成取消、epoch 隔离、已生成与已播放分开；已执行业务事实不能清空 |
| “首 Token”与“首字音频”混用 | 分模型、用户结束→文本、用户结束→客户端音频三种指标 |
| 为 RSI 直接改工具守门／流程安全规则 | 候选仅改允许话术／Prompt；权限错误修代码并回归 |
| 所有未成功都判失败，按总分自动升级 | 合理拒绝独立；硬安全否决；证据缺失／无提升拒绝采纳 |
| 参考目录及 SDK 代码可直接照抄 | 当前无代码；先定契约；区域、版本、SDK 包名和流式行为逐项验证 |

## 官方资料索引

| 编号 | 官方来源 | 结论与使用位置 |
|---|---|---|
| S01 | [声网国内自定义大模型](https://doc.shengwang.cn/doc/convoai/restful/user-guides/custom-llm) | HTTP Chat Completions、SSE、自定义认证；架构和兼容端点 |
| S02 | [声网国内 REST 快速开始](https://doc.shengwang.cn/doc/convoai/restful/get-started/quick-start) | 国内 `/cn/` API 路径；账户区域待确认；完整页一次抓取超时，搜索可见官方摘录，实际配置以控制台导出核对 |
| S03 | [声网国内发布说明](https://doc.shengwang.cn/doc/convoai/restful/overview/release-notes) | 检索见 v2.15 2026-11-11；第三方 ASR params 与新旧打断字段存在变更，不能照抄早期教程 |
| S04 | [Agora Main join](https://docs.agora.io/en/api-reference/api-ref/conversational-ai/join) | 国际 Main 配置结构、greeting、custom turn；国内对应字段须核实 |
| S05 | [Agora 自定义 LLM Main](https://docs.agora.io/en/ai/build/custom-model-integration/custom-llm) | 兼容请求、流式返回；示例日志不直接照搬 |
| S06 | [Web toolkit API](https://docs.agora.io/en/api-reference/api-ref/conversational-ai/client-toolkit/web) | 官方工具包名称及 2.9.0 安装说明、interrupt、订阅生命周期 |
| S07 | [实时字幕](https://docs.agora.io/en/ai/build/transcripts) | RTM 字幕更新；避免多来源重复展示 |
| S08 | [运行时事件覆盖](https://docs.agora.io/en/ai/build/handle-runtime-events/get-runtime-events) | 实时打断在客户端路径，Webhook 不等同实时控制 |
| S09 | [Webhook](https://docs.agora.io/en/ai/build/handle-runtime-events/webhooks) | 签名、noticeId、HTTPS；服务端处理与去重 |
| S10 | [Main interrupt](https://docs.agora.io/en/api-reference/api-ref/conversational-ai/interrupt) | 服务端停止指定 Agent；不等于取消自研业务操作 |
| S11 | [DeepSeek 模型清单](https://api-docs.deepseek.com/quick_start/pricing/) | 当前模型名称核对；不使用旧示例默认值，不在本设计冻结价格 |
| S12 | [DeepSeek thinking](https://api-docs.deepseek.com/guides/thinking_mode/) 与 [工具调用](https://api-docs.deepseek.com/guides/tool_calls/) | 低延迟路径显式关闭思考；工具协议和历史必须一致 |
| S13 | [Next.js 安装](https://nextjs.org/docs/app/getting-started/installation) | Node 下限与前端环境，结合 Promptfoo 更高下限选择 |
| S14 | [FastAPI 发布记录](https://fastapi.tiangolo.com/release-notes/) 与 [Pydantic JSON Schema](https://docs.pydantic.dev/latest/concepts/json_schema/) | schema 导出和框架候选；本轮不声称完成应用集成 |
| S15 | [openapi-typescript](https://openapi-ts.dev/introduction) | OpenAPI 3.1→TS；具体 peer 以 npm 元数据为准 |
| S16 | [PostgreSQL 行锁](https://www.postgresql.org/docs/18/explicit-locking.html)、[RLS](https://www.postgresql.org/docs/18/ddl-rowsecurity.html)、[SQLite WAL](https://sqlite.org/wal.html) | 锁／隔离与降级限制 |
| S17 | [Langfuse 基础设施](https://langfuse.com/self-hosting/deployment/infrastructure/clickhouse) | 自托管不只是添加一个应用容器，校赛不引入整套基础设施 |
| S18 | [Promptfoo 自定义 provider](https://www.promptfoo.dev/docs/providers/custom-api/) 与 [断言](https://www.promptfoo.dev/docs/configuration/expected-outputs/) | 评测完整业务链与确定性事实 |
| S19 | [依赖核查快照](dependency-research.json) | 官方 npm／PyPI 的版本、要求和许可证元数据；未等同安装锁文件 |

国际 Main 与 Platform Preview 的页面同时出现在检索结果中。本设计排除 Preview 的 `pipeline/greeting` 配置结构，不把其 event payload 当作 Main 已验证格式。国内外功能发布节奏和 vendor 列表可能不同：框架方向可确定，账号具体参数需要首日真实请求 fixture 才能冻结。

## 集中待确认事项与暂定假设

| ID | 真正待确定的信息 | 暂定假设与不阻塞工作 | 负责人／期限 |
|---|---|---|---|
| Q1 | 已有固定公网服务器／域名／证书的区域和可用时段 | 采用单主机 HTTPS＋Compose；账号未通前做文本／Mock；不依赖现场穿透 | 王，语音接入第一天 |
| Q2 | 声网账号地区、AI Engine／RTM 权限、中文 TTS 供应商和额度；DeepSeek 账号可用模型 | 暂用国内 Engine Main、自定义 LLM、RTM 工具包；具体参数／区域集中在 adapter；只补资源状态，不把密钥写文档 | 王，V01 前 |
| Q3 | 官方业务包、禁语、身份核验方法、订单接口的幂等和查证语义 | 使用明确标识的 mock-v1；不称模拟套餐为官方场景；官方到达后按差异流程更新 | 王对接，魏／翟替换 |
| Q4 | 官方首字测量起止、失败样本口径、现场外部 API 访问安排 | 暂以同客户端输入／输出探针测 E2E，报告不确定度；正式指标待官方重测 | 王，校赛前确认可得信息 |
| Q5 | 第三方云处理／观测平台对官方资料的允许范围 | 只用合成资料；默认关闭 Langfuse Cloud 导出，录音默认关闭；公开仓库不含官方包 | 王，导入官方包前 |

这些问题不阻止文档与模块设计。常规工程选择如 PostgreSQL、SSE、简单状态机已给出建议，不逐项要求用户确认。资源补充后只更新相关 ADR、参数快照与集成任务，不重写全套架构。

## 设计交付验收

本轮可验收项：需求可追源、三人职责明确、模块与部署图、状态／异常／禁止行为、HTTP 与内部端口、标识传递、数据归属、指标口径、RSI 门槛、每日联调计划、可生成公共 Schema 的结构样例。机器校验结果记录在 [validation-report.md](validation-report.md)。

尚未完成且不伪称完成：应用安装锁定、真实云连通、真实语音打断、P50/P95、数据并发恢复测试、业务与 RSI 实验、演示系统。它们是后续实现交付项，不是本轮设计任务已测结果。
