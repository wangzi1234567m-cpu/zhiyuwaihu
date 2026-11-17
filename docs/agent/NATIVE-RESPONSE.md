# 原生独立复审返工

2026-11-17。初审针对干净提交ef38aa4，原生审查单ec68cd3141，结论CHANGES_REQUESTED。原结果不覆盖，保留在私人项目审查目录NATIVE-RELEASE-REVIEW.md。

1. **回写挂起（确认并修复）**：新增独立用例在原实现4.2秒超时失败。GLM提案为回写加3秒wait_for，超时进入现有独立重试并保留业务成功事实，不重发execute。
2. **响应丢失后回写换ID（确认并修复）**：新增模拟远端按record_id去重并首轮提交后丢响应的用例，原实现两次ID不同。GLM在首次回写I/O前把完整CallRecord存入Operation.data.writeback_record；后续重试和新Coordinator实例完全复用payload，远端记录数保持1。
3. **版本竞态留下completed消息（复现未成立）**：独立用例在最终enqueue前真实提交申请单revision更新，原实现即通过：没有operation/效果、状态不为ACTION_READY/EXECUTING/CLOSED，busy=false，消息failed。原因是Repository.transaction使用Session.begin，enqueue抛异常会回滚同一事务内的completed/busy/状态更新，外层异常仍能识别当前generation并标记失败。未为这条审查推断改产品行为；保留用例防止将来破坏事务边界。
4. **缓存被跟踪（索引事实纠正）**：git ls-files对backend/.pytest_cache和frontend/tsconfig.tsbuildinfo为空，git check-ignore均命中；这些文件是采集器读取了未跟踪的本机缓存，并不在commit、bundle或源码ZIP内。本轮把缓存保留到工作树.venv审查归档，防止下次采集混入。没有删除历史或隐藏产品源码。

新增3项独立用例原实现2 failed/1 passed，修复后3 passed；完整交付目录155 passed/0 skipped（28.53s）。修复后Chrome三轮和刷新恢复再次通过（2.0s），数据库仍为CLOSED、1效果、1记录。真实GLM理解三轮再次3请求0失败/重试，CLOSED、1效果、1记录，整体3924ms；仍不是语音指标。

实现提案来自配置GLM，主Codex审阅应用并独立复验；新增测试没有降低原有预期。完整原失败/修复证据见私人项目artifacts/review-20261117/，正式代码审查结论以其后重新读取固定源码的原生复审结果为准。

## 第二轮复审与返工

第二轮原生审查单c61d14fe56确认此前两项已修、事务回滚复现正确，另外发现派发后版本混用和并发回写ID问题。

- 三项新增复现：业务execute后申请单升版本仍须按派发版本回写；旧操作缺快照且版本变化必须转人工保留既成效果；两个真实线程、独立Coordinator并发处理同一已成功operation时，远端仍只有一个回写ID。返工前3项全失败。
- GLM修复：enqueue保存case_snapshot；首次回写从快照生成，旧操作无快照则验证版本，不一致转人工且不重发业务；载荷创建和读写均先锁Operation。已回写完成的操作提前返回，迟到回写失败不能覆盖成功标志。
- 返工后6项原生审查回归全部通过；完整干净交付目录158 passed/0 skipped（29.71s）。Chrome三轮与刷新再次通过（2.1s）；真实GLM理解再次3请求0失败/重试，CLOSED、1效果、1记录，整体4053ms。

后端仍遵守原骨架单worker部署要求；这些并发边界回归不等于已交付多worker调度或分布式执行引擎。剩余语音、官方指标、RSI证据和PR条件不变。
