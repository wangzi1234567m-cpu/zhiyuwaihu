# 技术选型

用户提出的主体技术合适，保留 Next.js 作为 React 框架，不为新业务迁移前端。RTC、麦克风和播放仅在 Client Component 初始化。后端采用单 FastAPI 进程模块化单体，Pydantic 2 作为结构唯一源，PostgreSQL 保存权威状态。首版一个 Uvicorn worker，避免内存取消注册表跨进程失效。

| 组件 | 决策及取舍 |
|---|---|
| 前端 | React＋TypeScript＋Next.js；不用 Next.js API 再复制业务权限层 |
| 后端 | Python 3.12、FastAPI、Pydantic 2、HTTPX；函数接口连接业务模块 |
| 数据 | PostgreSQL 18、SQLAlchemy、Alembic；JSON 是导入／导出包，不是运行时另一套权威数据库 |
| 检索 | 场景ID＋三级分类＋适用范围精确筛选，再用关键词／同义词匹配 QA；68条规模无需独立向量服务。默认全文检索不等于中文分词问题已解决 |
| Agent | 一个业务Agent＋纯Python状态机＋工具守门；LangGraph无明确收益，首版不引入 |
| 语音 | 声网RTC音频、RTM字幕事件、云ASR/TTS；自研后端提供Chat Completions HTTP/SSE |
| 模型 | 保留DeepSeek非思考模式候选，经ModelPort调用；输出必须有业务依据；性能与账号可用性实测后锁定 |
| 评测 | pytest检验代码与事实，Promptfoo驱动完整文本Agent；Langfuse仅可选脱敏异步观测 |

现有依赖核查快照保留在 [依赖记录](../archive/package-v0.1/dependency-research.json)。它不是已安装锁文件。沿用候选 Next 16.4.6、React 19.3.1、TS 5.9.3、FastAPI 0.163.1、Pydantic 2.14.3；TS 版本为匹配 openapi-typescript 7.13.0 的 peer 范围。Node 22≥22.22.0。声网 RTC 4.24.9、agora-rtm 2.3.0、agora-agent-client-toolkit 2.9.0 仍须真实环境验证。不得用 latest 自动漂移或把旧API示例当已验证实现。

PostgreSQL部署多一个容器，但支持并发事务和唯一约束；应用仍必须校验session所有者、申请单归属、操作权限。SQLite不作为本次正式路径。单机反向代理＋Next.js＋FastAPI＋PostgreSQL即可，不引入Redis、消息队列、多Agent。离线评测单独进程运行，不能占实时请求。

官方核对：[声网自定义LLM](https://doc.shengwang.cn/doc/convoai/restful/user-guides/custom-llm)、[Next.js客户端边界](https://nextjs.org/docs/app/getting-started/server-and-client-components)、[PostgreSQL检索](https://www.postgresql.org/docs/current/textsearch.html)。中文业务答案以提供资料为准，不用外网搜索替代待审规则。
