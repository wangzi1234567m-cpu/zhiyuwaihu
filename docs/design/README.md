# 京东 POP 商家资质审核驳回外呼设计

版本 0.2.0，2026-11-16。现已建立0.3.0文本开发骨架，实际实现与差异见[骨架说明](../implementation.md)。以下为完整目标设计。实际范围是入驻、新增品牌、新增类目申请的审核驳回告知与整改指导，系统不自动审核材料、不批准入驻、不替商家注册。

技术主线保持 React＋TypeScript（Next.js）、FastAPI＋Pydantic、PostgreSQL、HTTP REST＋业务 SSE、声网 RTC＋对话式 AI。采用一个业务 Agent 和代码状态机，结构化规则检索优先，不增加微服务或向量数据库。

## 文档入口

- [V0.4 实时交互层交付](../v04-realtime-delivery.md)：可选WebRTC接线、并行任务、流式播报、打断清理、Mock注入和P50低于2秒的验收边界。
- [V0.4 实时交互与业务封装升级契约（双人开发草案）](../v04-realtime-business-contract.md)：并行化目标、五条契约、GitHub分工与验收，以及现有资产保留/清理边界。下列原三人分工保留为历史设计，V0.4分工以新契约双方确认结果为准。
- [需求与验收](01-requirements.md)
- [技术选型](02-decisions.md)
- [架构与语音链路](03-architecture.md)
- [状态机与关单规则](04-state-machine.md)
- [模块契约](05-contracts.md)
- [数据清洗与持久化](06-data.md)
- [协作与每日验收](07-integration.md)
- [指标与 RSI](08-evaluation-rsi.md)
- [来源、冲突与待确认](09-sources-open-questions.md)
- [翟雯交付说明](handoffs/zhai.md)／[魏鼎承交付说明](handoffs/wei.md)

王梓恒负责架构、公共契约、后端会话与模型适配基础、声网音频链路和取消、集成、部署及域名。翟雯负责数据清洗与规则库、Mock 业务系统、前端。魏鼎承负责最终 ASR 文本之后的业务理解、检索策略、Agent、状态机、工具执行约束与 RSI。ASR/TTS 服务接入归王，魏消费文本事件，不再搭建第二套识别服务。

原套餐方案已完整归档至 `docs/archive/package-v0.1`，其中报价、购单、确认套餐及旧结构样例均不再有效。当前业务写操作改为模拟京麦驳回、预约回电、人工事件和通话回写。商家理解整改内容不等于同意购买，也不应成为后台执行既定审核驳回的必要条件。

公共交接资产：[Pydantic结构与使用说明](contracts/README.md)、[合成对接样例](contracts/examples.json)、[JSON Schema](contracts/schema.json)、[本轮校验记录](validation-report.md)。
