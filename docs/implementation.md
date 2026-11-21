# 0.3.0 骨架实现与交接

本轮实现文本联调骨架。设计0.2描述完整目标，当前实际字段／方法以 `backend/app/contracts.py`、`backend/app/ports.py` 和生成的 OpenAPI 为准。该实现不是完整比赛业务。

## 模块接入

| 负责人 | 替换／扩展位置 | 接入方式 |
|---|---|---|
| 王梓恒 | coordinator、repository、main、config；ModelPort、VoiceGateway | create_app注入Services；保持路由、事件及权威历史一致 |
| 魏鼎承 | DemoAgent、Policy、ToolGuard | 消费TurnInput、Context、只读历史、申请及已审知识；返回AgentDecision，不自行执行工具或写SQL |
| 翟雯 | DatabaseCases、DatabaseKnowledge、MockBusiness、seed；frontend | 查询及业务端口由Services注入；前端使用生成类型，只连HTTP/SSE |

Services构造参数支持agent/model/cases/knowledge/business/policy/guard/voice/repository替换。默认本地适配器，不动态加载用户提供的Python路径。示例：`create_app(Services(agent=YourAgent(), business=YourBusiness()))`。接口参数和返回类型见ports.py。

ModelPort接收ModelRequest（messages与fixture_response），FakeModel仅输出后者；未来模型接入需单独验证事实约束和延迟，当前不接受真实云模型配置。

当前业务写端口收敛为execute(ActionCommand)、query_operation(ActionCommand)、writeback(CallRecord)。命令payload区分动作，不另外实现四份重复HTTP接口。Mock仅执行模拟驳回；预约／人工工单保留类型但尚未实现真正写入，相关意图转人工状态。正式扩展由魏补守卫、翟补工具，两人同时更新测试。

Repository提供事务边界，协调器使用read/transaction。协调器目前了解公共ORM以减少首版抽象；Agent不允许拿裸SQL。业务适配器各自维护数据访问。首版必须单worker运行。

## 实际行为与契约变化

- 版本0.3.0：增加业务展示、消息、生成完成、登录、受理响应及模型请求DTO；AgentDecision增加action_payload。
- start返回202 AcceptedTurn；状态由GET及SSE查询。start/create使用Idempotency-Key，文本／中断使用client_event_id，end为自然幂等。相同key不同参数409。
- 会话固定demo-p1/demo-k1/demo-r1及case_revision。服务端精确匹配“我是演示商家N”后核验成功；这仅是合成测试规则，不是现实身份认证。
- “明白了”只触发演示流程继续，驳回权限来自既定审核事实、核验和告知证据，不是购买同意。
- 取消不撤销已派发操作。外部调用前持久化unknown；重启后查证，不盲目重发。六轮未确定转人工状态，不伪称已有真人接管或外部工单。
- 回写失败保留WRITEBACK_PENDING并单独重试。业务账本按申请单、审核版本、动作唯一；跨session不创建第二份效果，第二会话显示已有操作并转人工。
- 文本playback_status为not_applicable，没有虚构语音延迟。
- SSE暂保留全部合成事件，尚未实施24小时清理；清理前必须补过期游标检测。客户端通过快照重连、按seq去重。
- 尚未实现录音、媒体凭证、真实声网Chat Completions、LLM调用、指标遥测API、管理发布API和RSI作业；语音未配置返回503。

## 安全与数据

默认仅绑定localhost，通过演示密码获取HttpOnly Cookie。会话、任务、操作及SSE按owner校验；仅有demo和other演示用户，other无申请单。生产禁用开发登录，当前没有生产登录实现，不应直接公网部署。

写请求要求X-Requested-With: zhiyu，浏览器Origin须匹配PUBLIC_ORIGIN。API日志只写方法、状态、request_id。输入中的11至18位连续号码在存储前替换；这不是通用PII识别器，当前仅允许合成数据。

故障注入默认关闭。ENABLE_FAULTS=true且APP_ENV=development/test时注册开发端点，支持write_failure、lost_response、persistent_unknown、writeback_failure；取消通过真实中断API触发。生产不暴露开关。

## 验收边界

集成测试仅用真实PostgreSQL，默认显式跳过。只有RUN_DB_TESTS=1且数据库名以_test结尾才允许清空测试表。CI运行迁移、数据库测试、类型生成差异、前端构建和浏览器三轮流程。

本机Windows虚拟化组件已修复，Docker Desktop重装至4.99.0后Compose部署通过；数据库和浏览器文本全链路、容器重启后的持久化均已验证。消息采用持久化position排序，SSE代理关闭压缩以确保实时推送。具体通过项与待验证项见 [验证记录](verification.md)。

## 2026-11-18语音／模型增量

原“尚未实现”列表记录0.3.0初始状态。现已加入可配置DeepSeekModel、AgoraVoiceGateway及VoicePanel；增量接口、已实现与待实测范围统一见 [语音接入说明](voice-integration.md)。业务Agent／Case／Knowledge／Business端口签名保留，VoiceGateway增加生命周期方法，ModelRequest仅增加可选字段。旧文本流程继续作为回归门槛，语音业务写入默认禁用。
