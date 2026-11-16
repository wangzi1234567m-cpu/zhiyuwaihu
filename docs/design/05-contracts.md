# 模块契约 0.2.0（完整目标设计）

已实现的0.3.0骨架签名和差异见 [实现交接](../implementation.md) 与 [OpenAPI](contracts/openapi.json)，开发以实现版本为准。

所有ID服务端生成UUID；client_event_id仅用于前端幂等。状态权威在后端。时间UTC RFC3339、预约展示Asia/Shanghai。结构源见 [contracts.py](contracts/contracts.py)，导出的schema供Mock与前端对接。前端正式类型由FastAPI OpenAPI＋openapi-typescript生成，不手工维持三份定义。旧套餐schema已归档，不能继续使用。

## 模块端口

| 提供者→调用者 | 接口 | 输入→输出／约束 |
|---|---|---|
| 王→魏 | ModelPort.stream | 服务端组装messages、工具schema、cancel token→文本／工具建议delta；不执行业务工具 |
| 魏→王 | AgentPort.handle_turn（魏实现） | TurnInput＋服务端SessionContext→AgentDecision；只消费最终ASR文本及领域事件 |
| 翟→魏 | CasePort.get_case | 授权Context＋application_id→ReviewCase；返回既定审核结论和槽位，不允许任意用户查单 |
| 翟→魏 | KnowledgePort.lookup | scenario_id、query、scope、knowledge_version→list[KnowledgeEntry]；只返回approved、范围匹配、无冲突条目和source_ref |
| 魏→王／翟 | Policy.decide_route | ReviewCase＋发布规则→RoutingDecision；纯函数，不走LLM |
| 魏→王 | ToolGuard.authorize | action、server context、decision_id、expected_case_revision→允许或错误；客户端布尔值无效 |
| 翟→魏 | BusinessPort.reject_application | 服务器注入的ActionCommand→OperationResult；写模拟京麦书面意见，不批准注册 |
| 翟→魏 | BusinessPort.create_callback | ActionCommand（引用已验证时间确认记录）→OperationResult |
| 翟→魏 | BusinessPort.create_followup | ActionCommand（payload包含原因、原operation）→OperationResult；人工事件不等于真人已接通 |
| 翟→王／魏 | BusinessPort.query_operation | operation_id／idempotency_key→OperationResult；not_found_yet不能判失败 |
| 翟→魏 | BusinessPort.writeback | ActionCommand＋CallRecord→OperationResult；从事实组装，不由LLM猜成功 |
| 王→各模块 | Repository.commit | expected_revision＋变更＋events→snapshot；状态和事件同事务 |

CasePort读取审核业务主体需要服务端任务授权；允许在通话前由系统读取以判断是否需外呼，但在接听方身份核对前不能把详情发浏览器字幕／语音。身份匹配由本会话核验服务写证据，LLM只提取名称与肯否意图。其他商家信息不可查；首次核验规则以模拟名录匹配为暂定，非真实强身份认证。

## HTTP API

| 方法／路径 | 请求→响应 | 权限与说明 |
|---|---|---|
| POST /api/v1/sessions | CreateSession(application_id, mode)→SessionView＋短期媒体凭证 | 普通演示身份只能访问分配任务；case由后台授权，不接受scenario作为事实 |
| POST /api/v1/sessions/{sid}/start | expected_revision→SessionView | 幂等；RTC/RTM准备后启动云Agent；京麦-only任务返回route而不强制建Agent |
| GET /api/v1/sessions/{sid} | →SessionView | 含route、state、case_revision、last_event_seq、operations；敏感字段按核验结果裁剪 |
| GET /api/v1/sessions/{sid}/events | Last-Event-ID／after_seq→SSE Event | 状态、操作、依据、指标；不重复语音字幕 |
| POST /api/v1/sessions/{sid}/turns | TextTurn→202 turn_id,generation_id | 仅dev/eval text模式；语音输入只走声网入口 |
| POST /api/v1/sessions/{sid}/interruptions | generation_id或provider turn、client_event_id→applied | 校验目标，旧事件不能取消新任务 |
| POST /api/v1/sessions/{sid}/end | reason→SessionView | 停媒体与新操作，已派发业务继续查证 |
| POST /api/v1/sessions/{sid}/telemetry | 时钟域、测量方法、时间点、event_id→202 | 遥测不可更新核验／确认／审核结论 |
| POST /api/v1/sessions/{sid}/credentials | →短期RTC/RTM凭证 | owner及会话存活检查，no-store |
| GET /api/v1/operations/{oid} | →OperationResult | 同owner／申请单权限 |
| POST /api/v1/admin/knowledge-imports | 已上传私有artifact_id、sha256→import_id | 管理员；验证不直接发布 |
| POST /api/v1/admin/knowledge-releases | import_id、review_manifest_hash→release_id | 未审／冲突条目不能自动批准 |
| POST /api/v1/admin/evaluations | baseline,candidate,dataset_version→run_id | 离线执行，不阻塞实时服务 |
| POST /api/v1/admin/strategy-decisions | action,target,expected_active,evidence_run_id→decision | adopt须门槛通过，rollback仅已通过版本 |

上行写请求使用Idempotency-Key，相同key且payload不同返回409。业务动作不提供前端直接执行入口；前端管理台也不能绕过工具守门。

声网入口为 `/integrations/agora/sessions/{sid}/v1/chat/completions`，会话专用服务端密钥鉴权，接受兼容messages、stream=true，输出`chat.completion.chunk`和`data: [DONE]`。仅取本轮user输入；真实provider_turn字段位置在接入实测固定。自定义入口与浏览器SSE协议独立，绝不向TTS发送工具JSON。Webhook验签、noticeId去重后入库，不用它代替实时取消。

## 事件、错误、超时

Event包含schema_version、event_id、session_id、turn_id、generation_id、operation_id、seq、state_revision、occurred_at、strategy_version、knowledge_version及payload。payload.type为唯一事件判别字段；SSE event字段取同值，不再维护第二个JSON顶层type。枚举为state.changed、turn.started、operation.updated、knowledge.used、text.delta、turn.interrupted、metric.recorded、session.ended。payload按type判别，详见schema。

业务事件持久化后发送；按seq去重重放，15秒SSE心跳不分配seq，保留24小时。cursor失效返回409；EventSource错误后用普通GET取snapshot和新cursor。新snapshot后的事件不能在重放／实时切换间漏掉。generation失效的文本丢弃，但旧operation的真实结果仍更新对应业务记录。

统一错误：`{code,message,retryable,request_id,operation_id}`。401鉴权、403禁止、404跨owner或无资源、409状态/版本/幂等冲突、422参数、429限流、503上游/存储故障。提交已受理但结果未知返回202＋status=unknown，不用504误导为未执行。

预算：只读工具1.5秒，可重试一次，总计3秒；模型首content 2.5秒、整轮8秒，超时取消并审核话术兜底；写操作3秒，超时以原key在1/3/10秒查证，30秒仍未知建立人工事件。回写单独重试，DB失败不允许派发新写。幂等覆盖重启，不能只用内存dict。

预约明确确认由服务器记录evidence_message_id、窗口、时区、application_id、case_revision、expires_at；商家改期旧记录失效。后台驳回不复用此同意模型：它用审核结论＋关单规则＋合法告知/例外证据授权。默认不提供真实京麦接口，Mock同样执行这些约束。

具体请求与事件可见 [可验证示例](contracts/examples.json) 和 [结构使用说明](contracts/README.md)。前端每次重连先取SessionView，将last_event_seq作为SSE游标；连接建立期间新增事件仍按数据库序号补发。SessionContext只由后端组装，不能作为浏览器输入。

内部约定：异步I/O端口使用async；Policy为同步纯函数；AgentPort.handle_turn(input, context, history, cancel)返回AgentDecision，流式文本由协调器绑定generation后发送。history为只读已去重消息；cancel由王提供，魏在模型读取、工具调用前后检查。KnowledgePort.lookup(scenario_id, query, scope, knowledge_version)返回list[KnowledgeEntry]；CasePort.get_case(context, application_id)返回ReviewCase。BusinessPort写动作接收ActionCommand；writeback额外接收CallRecord。操作错误统一ApiError；可变业务结果统一OperationResult。Repository负责事务与CAS，模块不分享裸数据库连接。
