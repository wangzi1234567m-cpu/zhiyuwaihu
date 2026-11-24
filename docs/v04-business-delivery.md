# V0.4 业务封装交付

分支：`codex/v04-business`；基线：`657310e`；公共进程内协议：0.4.0。
本轮由业务封装方实现，共享文件的局部适配由实时方最终整合。

## 双方共用替身

`app.realtime_business.fixtures.ScenarioBusiness` 实现五个公共接口，
`FakeRealtimeConsumer` 可连接替身或真实 `CoordinatorBusinessFacade`。
固定场景：normal、slow（默认3秒）、timeout（保留unknown操作）、duplicate
（重复请求与事件）、late（失效generation迟到）、interrupt、disconnect
（断线后模拟操作完成且仅一次效果）。所有数据都是合成测试数据。

```powershell
.venv/Scripts/python.exe scripts/demo_realtime_business.py
.venv/Scripts/python.exe -m pytest -c backend/pytest.ini backend/tests/test_realtime_contract.py backend/tests/test_realtime_fixtures.py -q
```

替身内存状态仅用于模块独立开发，不提供进程重启持久保证。
消费者恢复先取快照再订阅快照游标后的事件；历史音频不会自动重播。

## 真实业务封装与接入顺序

1. 迁移至0004，新增 `realtime_events`，不替换原业务事件和账本。
2. 构造现有Coordinator并注入 `CoordinatorBusinessFacade(coordinator)`。
   独立宿主调用facade.startup/shutdown；已有宿主不得重复启动Coordinator，
   应由整合方将队列恢复循环纳入同一生命周期。
3. 完成身份认证后，服务器调用 `facade.bind(owner, session_id)` 生成短期连接绑定。
   旧0.3文本会话先完成原start；本轮未增加网络入口或自动开场协议。
4. 实时入口核对供应商最终输入/音频绑定后调用 `record_input(context, provider_input_id, input)`。
   文本测试入口也须先认证并登记原始输入。此入口只能由服务器调用。
5. 按公共DTO调用submit_turn、subscribe_events、interrupt_generation、get_snapshot、report_playback。

状态和申请版本分别重验；幂等收据先于版本冲突检查。相同供应商输入不能换请求号重复受理。
受理、输入消费记录、待执行任务和原Coordinator轮次同事务提交。
排队任务在启动失败或进程重启后可恢复；运行中断的生成明确失败，不自动重做模型或业务写入。
操作继续使用原Coordinator的持久查证和独立回写。

适配事件与业务提交同事务持久化，独立连续seq。原token增量不转为审核片段，
真实适配首期仅发布完整审核后的一个片段；双段提前消费由替身验证。
单worker，每会话最多两个订阅；持久日志轮询连续补读，无通知丢失窗口。
实时增量落后超过128条断开慢消费者，事件不丢弃；历史完整重放保留。
全局最多32个运行生成任务，调用方仍须限制连接和输入速率。

理解候选使用明确的realtime_candidate来源，校验允许槽位、长度、原文和业务语义，
复用SemanticAgent.decide，不再调用独立理解模型；不支持候选的Agent明确拒绝。
共享DTO增加来源枚举，原Schema/OpenAPI及前端类型需一起生成。

## 播放边界

默认evidence resolver不信任任何客户端回执。只有服务器提供的独立可信证据解析器
返回VerifiedPlaybackProof且全部绑定、版本、完整覆盖匹配，才计入播放进度。
单片段、模型完成、客户端completed均不能单独形成完整告知。
本轮不把新播放摘要接成语音业务写授权；真实媒体证据和写入启用归后续集成验收。

## 验证与限制

七类替身演示通过；契约与替身测试27项通过。后端全量445项通过、0跳过，
前端生成类型及TypeScript检查通过。真实PostgreSQL的三轮闭环、
唯一效果、回写、幂等重试、稳定重放、来源绑定及旧版本拦截已通过。
后续测试结果以本次执行输出为准。使用独立 `_test` 数据库，未访问线上库或云模型。
审核收紧实时告知边界后，业务封装、契约和替身定向复验32项通过，含新增候选复用用例。
数据库集成测试文件为 `backend/tests/test_realtime_business.py`，复用原测试环境准备流程。

本轮未接媒体、未部署、未证明真实时延收益。Docker及部署由实时/整合方处理。
旧模式不bind即保持原行为；回滚时停止新适配入口，保留所有数据与账本。
