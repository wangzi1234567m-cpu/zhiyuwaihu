# V0.4 业务侧并行验收

日期：2026-11-24；分支：codex/v04-business。

结论：异步业务等待与受理、事件消费、取消、快照已解耦；整个系统并行升级尚未验收完成。
本轮使用真实Coordinator、PostgreSQL和Mock账本，以可控异步闸门验证先后关系，
不以create_task或接口存在作为通过依据。没有调用真实语音、模型或Docker。

## 新增因果验收

`backend/tests/test_realtime_business.py`：

- `test_parallel_accept_event_receive_and_interrupt_during_held_understanding`：
  固定挂起业务理解；submit_turn已返回，turn.started已被消费，独立接收循环完成10次输入心跳；
  原理解仍未释放时打断成功，下一轮已经回答。释放旧理解并让其无视取消返回，旧generation不输出审核片段。
- `test_parallel_reply_delivery_before_held_operation_and_writeback`：
  固定挂起模拟操作；消费者已收到审核片段及reply.completed，快照和取消仍可调用；
  放行操作但继续挂起回写，消费者已收到succeeded，数据库只有一个效果；
  断线后放行回写，恢复到CLOSED且仍只有一个操作。

加上七类替身相关验收，本次运行17项通过。双段提前消费、重复事件去重、迟到过滤
由test_realtime_fixtures.py验证；真实业务首期全文审核后仅交付一个完整片段。
此前后端全量445项通过，前端类型检查通过；本次新增用例后未重跑完整后端。

## 尚存串行点及验收边界

1. Coordinator和facade中的SQLAlchemy事务、Case/Knowledge查询仍是同步调用，
   慢数据库会占用asyncio事件循环。本轮闸门证明异步上游等待可并行，不能证明慢数据库无阻塞。
   后续应在明确事务边界下采用异步数据库端口或受控专用执行线程，并做慢查询注入验收。
2. reconcile_loop仍逐个await process_operation；一个操作慢会推迟其他会话操作查证。
   应补有界任务调度及每operation独占，验证两个独立会话中慢操作不阻塞另一操作；
   不能直接无界gather，也不能同一operation被重复派发。
3. 实时媒体接收、播放队列、背压、真实打断停播属于A侧；本轮独立心跳只是替代接收任务，
   不代表真实音频或供应商事件循环验收通过。
4. 已验证答案交付不等回写；没有证明真实已审核答案的多段生成收益。
   理解候选复用已有定向用例，但真实供应商候选/媒体绑定仍待接线。
5. 没有相同条件旧/新版本的有效答案音频延迟、失败率和成本对照，不能宣称性能达标。

因此本轮可交付的是业务侧异步解耦结构及可复现证据，仍需处理上述阻塞点并联合A侧做系统验收。
