# 架构主干接入说明

本次主干更新提供声网 RTC/RTM、自定义模型入口、DeepSeek 流式适配、Qwen TTS 转换、云调用额度限制和部署配置。默认保持离线测试；这些适配器的存在不代表真实云端三轮语音已经验收。

## 开发基线

两位成员重新交付时，先获取最新 `origin/main`，将架构更新合入各自分支，再运行完整测试。不要用旧文件整体覆盖新主干。提交说明记录实际合入的 main SHA、自己的修复 SHA、契约版本和验证结果。公开生成类型仍由 `backend/app/contracts.py` 和 OpenAPI 生成。

本次没有合并 PR #3 或 PR #4。现有 `DemoAgent` 和基础 Mock 是接入骨架，两条 PR 中的语义 Agent、RSI、清洗和扩展 Mock 仍按各自返修说明交付。

## 修改职责

| 负责人 | 主责 |
|---|---|
| 王／架构集成 | 公共 DTO、ports、Services 装配、协调器、HTTP/SSE、媒体入口和最终集成 |
| 翟 | 数据转换与知识包、Case/Knowledge/Business 适配器、Mock 的最终事务校验、前端整合 |
| 魏 | 理解层、Agent、路由 Policy、ToolGuard、RSI 候选与采纳门 |

共享缺陷“排队后审核版本变化仍执行旧驳回”由翟负责 Mock 效果事务，魏验证上层守卫，协调器冲突由架构侧统一整合。模板命名与渲染由翟提供一个实现，魏消费其结果和缺槽位状态，不另建第二套数据模型。

## 必须保留的接入能力

- 保留 `Services` 的 model/voice/repository 等依赖注入和现有媒体生命周期调用。语义 Agent 接入不能删除声网与 TTS 的启动、停止、取消和鉴权路径。
- 模型供应商通过 `MODEL_PROVIDER` 等现有配置选择。默认 `fake`；DeepSeek 使用现有适配器、超时和持久调用限额。需要新增结构化理解端口时先协调公共接口，不直接把团队主干换成个人工作台的固定模型地址。
- `generation_id` 用于取消文本生成，`operation_id` 用于业务事实恢复。取消生成不能抹掉已提交业务结果。
- 保留 `VOICE_WRITES_ENABLED=false` 和可信告知完成校验。RTM 字幕、浏览器遥测或模型自述不构成已播放、已告知或已授权业务事实。
- 保留 `turn.started` 对 busy/generation 的更新，以及快照应用前的 session/seq 校验，避免旧快照覆盖新状态。
- 保留 `MODEL_PROVIDER`、`VOICE_ENABLED`、`CLOUD_CALLS_ENABLED`、`FREE_QUOTA_CONFIRMED` 等离线开关，以及自动测试的 MockTransport。日常回归不调用真实云服务。
- `.env`、供应商凭据、证书私钥、录音、原始赛题和私有保留集不进入 Git；`.env.example` 只放占位值。

## 重新交付的检查

在自己的独立 PostgreSQL `_test` 数据库迁移并运行后端全量测试；生成 Schema/OpenAPI/TypeScript 后确认没有漂移；完成前端类型和生产构建、浏览器文本闭环。修改语音公共边界时，现有语音、TTS、取消、鉴权和额度模拟测试也必须通过。

测试数量可能随新增回归增加，不能用固定通过数量替代对已有测试的保留。真实语音、正式知识、官方业务系统和无偏保留集的验收结果单独记录，不用 fake 结果代替。
