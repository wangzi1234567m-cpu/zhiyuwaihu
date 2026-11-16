# 模块契约与对接样例

契约版本 `0.1.0`。所有 `/api/v1` 业务字段由 [contracts.py](contracts/contracts.py) 定义，JSON 使用 snake_case，时间 UTC RFC3339，金额整数最小货币单位。错误采用统一 envelope。未知业务请求字段拒绝；供应商适配器单独接收其扩展字段，不能把未知字段透传到工具。

## 标识和信任边界

| 标识 | 产生方与传递路径 | 生命周期 |
|---|---|---|
| session_id | 服务端 UUID；HTTP path、SSE、声网自定义 LLM URL、所有表 | 一次通话，重连不换 |
| turn_id | 服务端 UUID；语音 provider turn 映射／文本受理后生成；传 Agent、工具、事件 | 一次逻辑用户最终输入；开场有独立非用户轮次 |
| generation_id | 服务端 UUID；一次生成任务；输出、取消请求与事件携带 | 重试或新生成换 ID；不可复用旧 ID 输出 |
| operation_id | 服务端 UUID；预留持久动作时生成；工具请求、订单、查证、回写使用 | 独立于生成任务，跨取消与进程重启 |
| client_event_id | 前端 UUID；文本输入、取消和遥测 | 客户端重试保持不变；仅幂等，不授予业务权限 |
| provider_binding_id／provider_turn_id | 服务端绑定和声网原始 turn | vendor turn 在重建 Agent 时可归零，必须带 binding |
| state_revision | 服务端递增整数，随 snapshot／业务事件 | 业务状态 CAS，拒绝过期命令 |
| seq／event_id | 服务端按 session 递增／UUID | SSE 有序重放；不拿时间戳代替排序 |
| strategy_version_id／trace_id | 会话创建固定版本／服务端创建 trace | 跨所有轮次、工具和评测可追溯 |

浏览器使用同源 HttpOnly Secure SameSite Cookie 绑定的演示访问身份（`owner_id`），首版由受控演示入口发放；所有写请求校验 Origin 和 CSRF token。跨 owner 的 session 返回 404。SSE 使用同源 EventSource 自动带 cookie，禁止在 URL 放 API key。管理 API 额外要求 admin 角色，开发模拟 API 仅 dev/eval 环境启用。

## 外部业务 API

响应包含 `X-Request-ID`，客户端可提交 UUID 格式同名头供追踪，后端验证或重建。创建／启动／结束／取消／评测／版本操作需 `Idempotency-Key`；唯一范围为 `(owner, route, key)`，同键同请求返回原结果，异 payload 409。幂等记录保留至少 7 天；订单幂等另见后文，不随 HTTP 缓存到期失效。

媒体凭证响应是例外：重复创建必须返回同一 session 资源，但可以为它重新签发短期媒体凭证，禁止在通用 request_receipts 明文缓存 token；凭证响应均 `Cache-Control: no-store`。`fixture_id` 仅 dev/eval 或受控管理员演示允许改变；普通 demo 访问强制 success fixture，不能请求注入故障或选择其他客户。

| 方法与路径 | 请求与返回 | 约束／状态码 |
|---|---|---|
| POST `/api/v1/sessions` | CreateSessionRequest → CreateSessionResponse，session＋媒体连接信息 | 201；scenario 固定 `mock_package_change`；默认 voice；text 仅 dev/eval；fixture 仅允许合成 allowlist；版本由服务端选 |
| POST `/api/v1/sessions/{sid}/start` | RevisionRequest → SessionSnapshot | 202；必须先准备 RTC／RTM；客户端 ready 不代表云 ready；连接成功经 snapshot／SSE 确认 |
| GET `/api/v1/sessions/{sid}` | SessionSnapshot | 200；恢复入口，含 `last_event_seq`、报价／订单／回写状态；token 不在 snapshot |
| POST `/api/v1/sessions/{sid}/credentials` | 空 JSON → MediaCredentials | 200；短期续期，检查 owner、session 存活、限速；响应 no-store |
| GET `/api/v1/sessions/{sid}/events` | `Last-Event-ID: seq` 或 `after_seq` → SSE EventEnvelope | 快照 cursor 后重放；过期返回 409 `CURSOR_EXPIRED` 并要求重新 GET snapshot |
| POST `/api/v1/sessions/{sid}/turns` | TextTurnRequest → AcceptedTurn | 202；只在 text 模式；输入接收后由 SSE 推送，不同步返回假 Agent 结果 |
| POST `/api/v1/sessions/{sid}/interruptions` | InterruptRequest → InterruptResponse | 202／200 重放；generation 或 provider turn 二者至少一项，若都存在必须映射一致；旧目标 `applied=false` |
| POST `/api/v1/sessions/{sid}/telemetry` | TelemetryBatch → Receipt | 202；批量最多 50，强限流；服务器覆盖 source/trust，不能更新核验、确认或订单 |
| POST `/api/v1/sessions/{sid}/end` | EndRequest → SessionSnapshot | 202；幂等；停止媒体与新业务，不阻断已派发 operation 查证 |
| GET `/api/v1/sessions/{sid}/operations/{oid}` | OperationView | 200；订单／回写操作同一查询形式；必须属于 sid |
| POST `/api/v1/admin/evaluations` | EvaluationRequest → EvaluationRunView | 202；admin，仅已登记数据集版本，异步离线执行；不在请求内跑评测 |
| GET `/api/v1/admin/evaluations/{rid}` | EvaluationRunView | 200；指标、证据引用、是否满足门槛，不暴露保留集全文 |
| POST `/api/v1/admin/strategy-decisions` | StrategyDecisionRequest → StrategyDecisionView | 201；adopt 检查通过证据与 active CAS；rollback 只回历史已通过版本；reject 保留原因 |

不提供通用 `/tools/execute`、`PATCH state` 或客户端直接创建订单的公开入口。前端的“业务成功”只读 `OperationView/SessionSnapshot`。工具结果展示仅含允许字段，不包含挑战码或原始敏感参数。

## SSE 业务事件

```text
id: 18
event: state.changed
data: {"schema_version":"0.1.0","event_id":"00000000-0000-4000-8000-000000000018","session_id":"00000000-0000-4000-8000-000000000001","seq":18,"occurred_at":"2026-11-16T10:00:00Z","state_revision":5,"turn_id":null,"generation_id":null,"operation_id":null,"trace_id":"trace-demo","strategy_version_id":"strategy-baseline-1","source":"server","payload":{"type":"state.changed","business_state":"AWAITING_CONFIRMATION","call_status":"active","reason_code":"QUOTE_READY"}}

```

`event` 值与 `payload.type` 一致。类型为 `state.changed/turn.started/text.delta/tool.finished/order.updated/turn.interrupted/metric.recorded/session.ended`。`text.delta` 仅 text 会话用于字幕；工具／订单事件持久化后发布，客户端以 seq 去重、以 revision 防旧状态覆盖。turn.started 给出内部与 provider turn 映射，前端收到早于映射的供应商事件可先按 vendor turn 缓存，不推测内部 ID。

每 15s 输出注释心跳 `: ping`，心跳不分配 seq。前端断线指数退避 1/2/4/8s 并加抖动；Last-Event-ID 是最后已应用序号。首版一条 SSE 覆盖整个会话。事件保留至少 24h；断点超出窗口先取 snapshot，再从其 cursor 订阅，避免 snapshot 与 SSE 之间丢事件。text delta 也以小块持久化后发布；限制每轮回复长度，避免逐 token 写库。

Native EventSource 的 onerror 不能可靠读取 HTTP 错误正文。前端发生重连错误时用普通 GET snapshot 验证会话／登录状态并取得新 cursor，再创建 EventSource；不要假设 onerror 能解析 409 JSON。第一次连接用 after_seq，自动重连时非空 Last-Event-ID 优先，禁止每次从旧 after_seq 开始。连接建立时先重放 DB，再接实时发布，或持续按 seq 补读，防止两者切换间遗漏事件。

## 声网兼容端点

`POST /integrations/agora/sessions/{sid}/v1/chat/completions`，`Authorization: Bearer <会话专用随机密钥>`。此 Authorization 形式为接入测试目标，须以实际云请求核实 api_key 如何传入；不匹配时只在 adapter 显式映射，不放宽为匿名。服务端校验密钥散列、TTL、绑定 sid／agent、会话状态；供应商身份不会继承浏览器 admin 权限。

外部请求接受 `model/messages/stream`，其中 `model` 应为内部路由名 `zhiyu-agent-v1`，不是让调用方选择任意云模型。`messages` 支持纯文本和 text parts；只接收本版支持的音频转写文本，非文本内容返回 422。`stream=false` 本版返回 400；对未知 vendor 扩展仅记录字段名并丢弃，禁止原样日志记录。`turn_id/timestamp` 原始字段归一化为 ProviderTurnRef，具体嵌套位置作为首日证据固定。重复 provider turn 重放已缓存输出或加入同一生成流，不新建第二个业务动作。

以下是**本项目兼容服务**的响应示例，不是从参考文档拷贝的可运行应用：

```text
data: {"id":"chatcmpl-0001","object":"chat.completion.chunk","created":1794823200,"model":"zhiyu-agent-v1","choices":[{"index":0,"delta":{"role":"assistant","content":"您好，我是智语外呼 AI 模拟客服。"},"finish_reason":null}]}

data: {"id":"chatcmpl-0001","object":"chat.completion.chunk","created":1794823200,"model":"zhiyu-agent-v1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]

```

Content-Type 为 `text/event-stream`，UTF-8，空行分帧，最后发送 `[DONE]`；不向声网输出内部 `state.changed`。供应商 SSE 与浏览器 SSE 是两个独立协议。上游 DeepSeek 适配器要能处理跨网络块拆分的行／JSON／UTF-8，忽略 keepalive，不假设一次网络 read 就是一个 token。

输出首部前鉴权／校验失败返回正常 HTTP 错误。开始 stream 后出错不能再改 HTTP 状态；若能安全输出，发送固定失败说明、finish chunk 和 `[DONE]`，同时记录 `turn.outcome=failed`；不编造 `success`。若 stream 已断，直接关闭并取消可取消任务。断开／中断需要能在停止 token 读取后释放 HTTP 连接。

`POST /integrations/agora/webhooks` 校验原始 body 的 HMAC 签名（优先官方 `Agora-Signature-V2`）、noticeId 去重、来源绑定与合理时间窗；轮转旧／新 secret 有界接受。脱敏后的持久 receipt 写入成功才回 200；处理放进进程内恢复扫描，失败可重放。签名校验失败不接受。原始 body 仅在内存验签，不写日志。[官方 Webhook 文档](https://docs.agora.io/en/ai/build/handle-runtime-events/webhooks)

## 内部端口

下列签名是模块边界，首版函数调用不经过 HTTP。实现可放进后续仓库既有目录，不能在每层复制结构定义。

```python
class AgentPort(Protocol):  # 魏实现，王调用
    async def run_turn(self, context: TurnContext, user: TurnInput,
                       cancel: CancellationToken) -> AgentDecision: ...

class ModelPort(Protocol):  # 王实现，魏调用
    def stream(self, request: ModelRequest,
               cancel: CancellationToken) -> AsyncIterator[ModelDelta]: ...

class BusinessPort(Protocol):  # 翟 Mock，未来官方 adapter；只由守门器调用
    async def verify_identity(self, ctx: ToolContext, challenge: str) -> IdentityResult: ...
    async def query_plans(self, ctx: ToolContext, needs: Needs) -> list[Quote]: ...
    async def submit_change(self, ctx: ToolContext, quote: Quote,
                            idempotency_key: str) -> SubmissionResult: ...
    async def query_operation(self, ctx: ToolContext,
                              idempotency_key: str) -> SubmissionResult: ...
    async def writeback(self, ctx: ToolContext, record: BusinessRecord,
                        idempotency_key: str) -> WritebackResult: ...

class StateMachine:  # 魏，纯函数，不做网络或 DB I/O
    def transition(self, snapshot: SessionSnapshot,
                   event: DomainEvent) -> TransitionDecision: ...

class ConfirmationService:  # 魏，王审查；不接受 bool confirmed
    async def validate_and_reserve(self, session_id: UUID,
                                   evidence_message_id: UUID,
                                   expected_revision: int) -> OperationView: ...

class SessionRepository:  # 王定义 UoW，业务负责人实现对应存取
    async def load(self, session_id: UUID, owner_id: str) -> SessionSnapshot: ...
    async def commit(self, expected_revision: int, changes: StateChanges,
                     events: list[EventEnvelope]) -> SessionSnapshot: ...
```

`TurnContext` 由服务器创建，含 sid、turn、generation、epoch、strategy、rule/data/model 版本和 trace；`ToolContext` 再带 operation、verified_subject、deadline 和环境。context 不由模型 JSON 解包。`AgentDecision` 仅包含意图、槽位、建议、回复草案；协调器＋状态机应用事实后才提交。运行时的取消 token、DB session 不序列化到 JSON，也不传前端。

BusinessPort 的结果必须区分 `succeeded/failed/unknown/not_found_yet/definitively_not_submitted`，包含 operation_id、provider_reference、order_id（可空）、error_code、retryable、as_of；工具输入无 `user_confirmed`。Mock 由服务端 fixture 确定结果，不能从用户对话选择故障模式。正式环境不能通过请求临时切换 Mock 成功／失败。

AgentPort.run_turn 返回一个本轮决策，不把未完成的工具参数作为流输出。关键业务轮采用“完成意图／槽位解析→校验执行→事实模板”的短路径；开放式解释轮的 ResponseComposer 直接消费 ModelPort.stream，按完整句缓冲合规检查后输出。`llm_ttft_ms` 在模型 adapter 测，用户首文本在真正放行句子时测，不能把尚未审查的 token 当成用户已收到。

## 超时、取消与幂等

| 调用 | 建议截止时间 | 取消与重试 |
|---|---|---|
| DB 短事务 | 500ms 告警、2s 超时 | 回滚不派发新写入，禁止网络调用持锁 |
| LLM 首有效 content | 2.5s；整轮 8s | 超时取消生成、审核兜底；该值是超时预算，不是 2s 达标声明 |
| 身份／只读业务查询 | 1.5s | 确认可重试错误一次，总预算 3s；中断可取消 |
| 提交／回写 HTTP | 3s | 派发后不可按生成取消；超时 unknown，不盲重试 |
| 声网 join/leave | 5s | 结果不明先查唯一 agent；leave 失败登记回收任务 |
| 浏览器 HTTP 控制 | 5s | 使用同 Idempotency-Key 查询／重试；不创建新操作 |

submit 幂等键为服务器派生 `submit:{session_id}:{quote_id}:{confirmation_id}`。对该 key 建唯一索引，另约束每 session 最多一个未解决／生效 submit。payload hash 绑定主体、报价条款和业务种类。工具端 Mock 和未来沙箱均须支持相同键重放／查证；若官方不支持幂等，需凭业务唯一引用查单，无法保证时禁自动重提并转人工。HTTP exactly-once 不作承诺，保证的是持久操作唯一和可恢复查证。

writeback 键 `writeback:{session_id}:{record_revision}`；内容变更才增加 revision，重试同 revision 不重复登记。业务事实成功后取消生成不会删除对应 operation。查询失败和写入未知不得共用一个笼统 `TOOL_FAILED`。

## 错误约定

```json
{"error":{"code":"CONFIRMATION_STALE","message":"方案已变化，请重新确认。","retryable":false,"request_id":"00000000-0000-4000-8000-000000000099","operation_id":null,"details":{}}}
```

| HTTP | code | 调用方处理 |
|---|---|---|
| 400／422 | INVALID_REQUEST／UNSUPPORTED_MODALITY | 修正输入；422 错误细节不回显核验凭据 |
| 401／403 | AUTH_REQUIRED／NOT_ALLOWED | 重新登录／停止，不自动重试工具 |
| 404 | SESSION_NOT_FOUND | 不区分不存在和无权访问 |
| 409 | STATE_CONFLICT／CONFIRMATION_STALE／IDEMPOTENCY_CONFLICT／CURSOR_EXPIRED | 拉 snapshot 或重新确认；幂等冲突不可换 key 强行重试 |
| 429 | RATE_LIMITED／BUDGET_EXHAUSTED | 按 Retry-After；预算耗尽停止新动作 |
| 503 | UPSTREAM_UNAVAILABLE／PERSISTENCE_UNAVAILABLE | 可取消请求有限重试；订单查 operation |
| 200／202 | operation.status=unknown/reconciling | 已受理操作的状态，不用 HTTP 504 暗示未提交 |

## 类型同步与契约变更

`contracts.py` 为结构源；[export_contracts.py](contracts/export_contracts.py) 导出 JSON Schema 和经验证的样例。实现期 FastAPI 请求／响应直接复用这些类，`app.openapi()` 导出完整路由 OpenAPI。前端执行锁定的 `openapi-typescript` 生成 `api-types.d.ts`；SSE discriminated union 需注册到 OpenAPI components，不能仅生成 HTTP response 类型漏掉事件。

CI 重生成后若 diff 非空则失败；前端 `tsc --noEmit`、后端 schema 校验、Mock fixtures 共用同一契约。样例仅证明结构合法，不能证明权限正确；跨字段权限和状态条件按状态机测试。新增可选字段可小版本演进；删除／改名／改枚举语义必须三人评审、升契约版本并同步 Mock。晚到未知事件保留 raw type 告警，不把 UI 弄崩或自动解释为成功。
