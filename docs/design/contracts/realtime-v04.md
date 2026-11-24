# PR0：实时交互与业务模块公共契约 0.4.0

状态：接口定义已落地，待双方开发接入；不是实时功能或业务验收完成。代码基线 `8991e677a85e81675da46be1ce850ba1b01491bf`。本轮仅提交契约，不实现Mock、业务适配、媒体路由、数据库迁移或部署。

## 1. 目标与职责

首要目标是减少串行等待：音频输入、模型事件接收、业务处理、音频播放作为独立的有界任务运行。事件接收循环只分发任务，不等待完整业务结果；审核片段就绪后即可进入播放，不等待回写。安全校验和业务因果顺序仍保留。

实时模型已产生合格理解候选时，由业务侧校验复用，不无条件二次模型理解。合同不强制每轮经过独立ASR、Agent模型和TTS；工具等待也不应阻塞接收。模型供应商不支持业务输出门控时，采用确定性播报回退，禁止未审核业务音频直通。

| 责任 | A：实时交互负责人 | B：业务负责人 |
| --- | --- | --- |
| 实现 | 音频、供应商事件、轮次、播放仲裁、打断、迟到过滤 | Coordinator薄门面、权限/版本、业务决策、持久事件、查证/回写 |
| 下一轮替身 | 持续输入/事件、音频消费者、故障注入驱动 | 正常回复、慢业务、超时未知等可配置业务替身 |
| 数据清洗协作 | 消费稳定的知识版本 | 提供审核原因、整改要求、知识来源和批准话术；不是模拟超时/断线的实现方 |
| 共同工作 | 接口变更评审、集成时序、有效答案时延、错误效果检查 | 接口变更评审、集成时序、有效答案时延、错误效果检查 |

这里只使用A/B角色，不假定人员姓名。公共契约由整合人合并；双方后续功能PR引用本版本。协议对象有效不代表鉴权通过。

## 2. 权威文件与兼容性

- DTO源：`backend/app/realtime_contract/dto.py`。
- 五类Python接口及异常：`backend/app/realtime_contract/ports.py`。
- 生成Schema：`realtime-v04.schema.json`；命令 `python scripts/export_realtime_contract.py`，CI使用 `--check`。
- 复用0.3的 `DTO`、`AcceptedTurn`、`Interrupted`、`State`、`OperationResult` 及既有事件payload，避免第二套业务状态。
- 这是进程内协议，不新增HTTP端点，不修改0.3 OpenAPI/TS生成物，不要求前端提交内部可信上下文。后续若暴露网络API，需另定义鉴权入口和生成相应类型。
- 0.4内部信封使用 `contract_version=0.4.0`；原0.3 `schema_version` 不变。未知版本拒绝；字段采用extra=forbid，新增可选字段也需先更新公共契约，再升级双方，不静默忽略。
- 新事件必须持久适配，不把0.4对象直接塞进0.3的Payload联合。原始持久事件通过稳定映射复用；新增片段事件可用独立适配日志，具体存储设计属于B的后续PR。

现有接口适配要求：C1由B封装 `Coordinator.accept()`，不能将expected_state_revision或expected_case_revision直接冒充现有start_revision；两种版本分别重验。C3复用现有取消机制，C4映射现有会话快照，C2映射持久业务事件，C5保留原播放证据守卫。UnderstandingCandidate映射到现有理解结果前必须通过服务端语义校验；不得为了通过旧source枚举伪造供应商来源。source扩展及运行接线属于下一轮功能PR，本轮不修改旧DTO。

## 3. 五类接口

### C1 submit_turn(context, user_input) → AcceptedTurn

`TrustedTurnContext`：owner、session_id、connection_binding、request_id、provider_input_id、expected_state_revision、expected_case_revision、knowledge_version、contract_version。

前三项必须由已认证服务器上下文绑定；request_id用于幂等，provider_input_id绑定供应商输入轮次。两个revision分别表示会话状态版本和申请版本，不可混用。

`BusinessTurnInput`：input_id、最终text、final=true、可选understanding_candidate。候选仅含意图、slots、澄清和否定标志；无verified/source/业务成功/证据消息ID等权限字段。slots仍需长度、允许键和原话依据校验，DTO校验不能替代语义校验。完整输入与供应商事件/音频绑定由A负责，B负责重验其可信来源及语义依据。

临时转录不提交C1，不能推进业务；未来预取使用单独的可取消准备接口，首期不实现。原生理解候选也必须绑定最终输入；不能将模型补写或臆造内容当用户授权证据。

受理顺序：鉴权/绑定 → 读取幂等收据 → 校验版本/输入 → 事务登记轮次与任务 → 提交后启动或唤醒处理 → 返回AcceptedTurn。后续PR需处理提交成功但启动失败的恢复窗口，不能把内存create_task当持久任务保证。

幂等键：(owner, session_id, request_id)。内容摘要覆盖provider_input_id、input_id、text、候选及两个revision/knowledge_version；连接重绑不改变请求内容。相同键/内容返回原AcceptedTurn，先于新版本冲突检查；相同键/不同内容返回REQUEST_CONFLICT。受理不等待模型、工具或音频生成。

同一输入轮次不得换request_id重复受理，B需以session_id/provider_input_id登记唯一绑定。一个会话只允许一个当前可输出generation；新轮次原子失效旧generation，但已派发操作保留。

### C2 subscribe_events(context, subscription) → AsyncIterator[RealtimeBusinessEvent]

`EventSubscription.after_seq` 为排他游标，默认0。事件信封包括event_id、session_id、turn_id、generation_id、operation_id、seq、state_revision、occurred_at、knowledge_version和payload。seq为会话内严格递增的适配事件游标，不假设与原0.3 seq逐条相等。重放必须保留原event_id/seq，映射关系须持久稳定。

| payload | 处理语义 |
| --- | --- |
| turn.started | 复用原受理轮次信息 |
| reply.segment_ready | 已审核语义片段，message/segment/index、原文SHA256、verbatim |
| reply.completed | 已审核答案全部交付，segment_count；不代表播放完成 |
| generation.finished | 生成completed/interrupted/failed；失败不替代操作结果 |
| turn.interrupted | 旧generation失效；不能清除已登记操作 |
| operation.updated | 复用OperationResult，信封operation_id须匹配；未知不能报成功 |
| turn.failed | 受理后的轮次错误；携带ContractError |
| writeback.completed | record_id与record_revision；不代表用户已听到结果 |

所有事件在提交后可见，历史事件不可修改。订阅必须消除先补读后监听的竞争窗口：通知仅用于唤醒，按持久seq连续补读。每次读取重新校验会话权限/连接绑定，撤权后结束。

片段索引在同一message内从0连续增长，重试同一segment_id内容不可变；不做token直通。hash基于精确UTF-8文字，不做标点归一化。前段必须语义独立，后段不能反转已经播出的业务事实。整条答复必须等全文校验时，可只发布一个片段，不伪称流式审核。

默认边界：每会话最多2个订阅，每订阅通知队列128条；满时断开该订阅并报SLOW_CONSUMER，不能丢弃持久事件。活动会话保留完整重放范围；其他保留策略须明确oldest_available_seq。after_seq < oldest_available_seq-1报CURSOR_EXPIRED，超出last_event_seq报CURSOR_AHEAD。取消迭代器释放订阅资源；客户端消费按seq/event_id去重。

### C3 interrupt_generation(context, request) → Interrupted

请求携带request_id与generation_id。幂等键为(owner, session_id, request_id)，摘要包含generation_id；重复返回原结果，异内容冲突。仅当前generation可首次应用；非当前或已结束返回applied=false，不取消当前新轮次。首次成功记录失效与取消事件，重复不增加状态版本。

A先停止本地播放并清队列，再协调服务端与供应商取消。B在耗时处理后、状态提交前重验generation。所有晚到片段按generation过滤；审计及operation.updated仍可保留，不能因旧generation删除操作记录。已派发操作继续查证/回写；取消不是回滚外部业务。

### C4 get_snapshot(context) → BusinessSnapshot

快照包含session_id、原State、state_revision、case_revision、knowledge_version、当前generation、last_event_seq、oldest_available_seq、operations及可选播放摘要。状态和游标必须来自一个一致的已提交视图。

恢复：读取快照S → 从S.last_event_seq订阅增量 → 校验generation/去重 → 恢复状态展示。增量订阅补上快照与订阅之间的事件；不自动重播历史答案、不重新派发操作。快照本身不提供音频恢复，恢复播放需未来单独协议。

首期约束为单进程/单worker、会话内有序提交；进程内通知不承诺跨worker。进程重启后从持久任务/操作记录恢复，不伪称协程存活。快照读取不能泄露未核验申请原因等私密内容，仍受原权限规则约束。

### C5 report_playback(context, report) → PlaybackReportResult

报告包含report_id、generation/message/segment/playback_instance、text_sha256、observation、clock_domain、可选elapsed_ms与evidence_ref。report_id按(owner,session_id,report_id)幂等，相同编号改内容拒绝。完成观察不是业务已告知。

evidence_ref只指向服务端已验证、不可由浏览器创建的证据记录。校验owner/session/binding/generation/message/segment/播放实例/文字hash/完整覆盖与可信完成来源；还要校验中断和版本失效。引用不存在、错绑、旧轮次、覆盖不足均不得授权。

返回observation_recorded、evidence_verified或evidence_rejected及reason_code。interrupted/failed/started不能验证完整播放；客户端completed但无独立可信凭据只能记录观察。DTO状态组合校验并不替代实际证据验证。

**WebRTC方案：** 媒体层记录服务端签发播放实例、审核片段hash及帧发送/取消轨迹；这些仅能证明服务端发送。RTCP、浏览器回执、已发全量PCM不能单独证明用户完整听到。首期独立WebRTC路径如无可验证的可信完成来源，业务已告知保持unknown/partial，保持语音业务写入关闭；可回退现有经验证的声网证据路径。后续需提交明确的信任模型、证据采集/存储适配和攻击/中断验收，才能启用相应业务推进。

整条message完成须reply.completed已到达，segment_count范围内所有片段获得有效证据，无中断/失效；单个segment成功不能直接置message_fully_notified=true。等待反馈不属于业务告知。

## 4. 错误、超时和资源规则

受理前/订阅失败抛 `ContractFailure(ContractError)`；受理后错误发turn.failed。网络入口未来另行映射HTTP/WS错误，当前不新增路由。供应商原始报错、密钥及私密上下文不得进入message。

| 错误码 | 处理 |
| --- | --- |
| UNAUTHORIZED / SESSION_NOT_FOUND / BINDING_EXPIRED | 重新鉴权/查询，不泄露其他owner的会话 |
| CONTRACT_VERSION_UNSUPPORTED / INVALID_INPUT / INPUT_NOT_FINAL | 修正版本或请求，不盲重试 |
| REQUEST_CONFLICT | 同一幂等键内容冲突，修正调用方 |
| REVISION_CONFLICT | 提供可公开current_state_revision，先读快照；不自动复用旧授权 |
| STALE_GENERATION | 停止旧输出，跟随当前generation |
| CURSOR_EXPIRED / CURSOR_AHEAD | 读快照恢复，不从0无限补读 |
| SUBSCRIBER_LIMIT / SLOW_CONSUMER / BUSY | 有界退避或释放旧连接；重试原请求先核对受理状态 |
| BUSINESS_TIMEOUT | 回答任务超时，不能推断业务操作失败 |
| OPERATION_UNKNOWN | 查证原operation，不新建重复操作 |
| SERVICE_UNAVAILABLE / INTERNAL_ERROR | 按retryable与受理状态恢复；禁止盲目重派 |
| PLAYBACK_EVIDENCE_REJECTED | 保持未确认，不以重试客户端回执替代可信证据 |

retryable表示相同语义请求在条件满足后可重试，不授予重派业务的权限。初期建议预算：受理目标100ms（非实测保证）；业务答复等待8秒后发超时；操作时限依既有执行/查证策略独立处理。A的等待反馈阈值可设600ms，真实答复就绪时取消未播放反馈，等待话术不包含业务结论。

每会话当前业务generation最多1个；A/B均限制任务和队列，旧任务有界取消，不能无限积压。连接退出释放订阅/媒体任务但不删除持久操作。实际时延与失败率需单独基线实验，DTO测试不能证明100ms或端到端提速。

## 5. 最小调用及时序

```python
from app.realtime_contract.ports import RealtimeBusinessPort
from app.realtime_contract.dto import EventSubscription

# port由B实现并注入；context由服务端认证层创建，不接收模型自由填写。
accepted = await port.submit_turn(context, user_input)
# 长期订阅应由独立任务在提交前建立，或从已知游标补读，不能只等未来通知。
async for event in port.subscribe_events(context, EventSubscription(after_seq=cursor)):
    # 接收循环将审核片段入有界队列，不能await完整播放/业务工具。
    dispatch_without_blocking_receiver(event)
```

正常：最终输入 → 受理 → 后台校验/决策 → 审核片段 → 播放与后续处理重叠 → 答案结束；操作/回写走各自结果事件。

打断：用户新语音 → A立刻静音清队列 → C3失效旧generation → 新轮次 → 旧片段丢弃；旧操作仍查证。

重复：同request_id重试 → 原AcceptedTurn；换内容 → REQUEST_CONFLICT；禁止新建操作。

断线：释放媒体/订阅 → 业务继续记录 → 重新认证/绑定 → C4快照 → C2游标补读；不自动重播或重派。

## 6. 下一轮七类替身场景规格（本轮不实现）

| 场景 | B业务替身/业务侧 | A实时侧验收 |
| --- | --- | --- |
| 正常回复 | 返回审核片段及结束事件 | 只播授权片段，关联generation |
| 慢业务 | 可配置延迟，例如3秒再交付片段 | 持续收音/收事件；等待反馈不抢占答案 |
| 超时 | BUSINESS_TIMEOUT；已派发操作可保持unknown | 兜底不称成功，不重复派发 |
| 重复 | 同请求返回原受理，异内容冲突 | 重复事件不重复播音 |
| 迟到 | 旧generation事件晚于新轮次到达 | 旧答案不播放，操作记录不丢 |
| 打断 | 生成失效，已派发操作继续查证 | 立即停播、清队列，可继续输入 |
| 断线 | 事件可补读，快照与操作结果可恢复 | 重新绑定、按游标恢复，无自动重派 |

正常话术可取已审核知识样例；合成延迟、错误、取消和连接故障属于软件开发/测试，不属于数据清洗。B可实现所有业务替身，A提供消费驱动。数据准备人员负责可追溯案例、字段清洗、知识版本和批准话术。

## 7. 检查、评审与后续合入

无云/无数据库检查：`python scripts/export_realtime_contract.py --check`；`python -m pytest -c backend/pytest.ini backend/tests/test_realtime_contract.py`。Python仅依赖现有Pydantic/pytest，不导入main或数据库。

2026-11-24本地检查：基于上述main起点的隔离工作树中，17项契约测试通过，生成Schema与DTO源一致，差异空白检查通过。覆盖权限字段拒绝、最终输入、版本、原文hash、事件判别、操作ID绑定、播放观察不能自证、快照游标及生成物一致性。未调用云服务或数据库，未运行完整业务回归；没有新增真实时延或媒体验收结果。新增独立GitHub workflow在push/PR检查该契约，远端结果以Actions为准。

双方评审确认：字段映射、幂等及revision、取消与操作边界、事件持久游标、片段审核、WebRTC无证据时禁止写入、worker限制、七类场景。实际实现PR还需行为测试；本轮DTO/Schema测试不等于已实现这些保证。

本次提交main只发布公共契约，不宣称双方已签字或功能验收通过；需要调整时先更新版本/迁移约定，再修改双方实现。

附件通知核对：2026-11-19赛题资料评审通知强调技术路线中立、运行效果、异常处理、可追溯实验及RSI证据。本文100ms/8秒/600ms及资源上限均为团队工程约定，不是通知要求；通知没有要求必须采用WebRTC或取消业务状态机。原PDF不复制入仓库。
