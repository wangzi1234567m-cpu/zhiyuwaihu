# 给魏鼎承的交付说明

**开工以0.3.0实际接口为准：** [骨架交接](../../implementation.md) 与 [ports.py](../../../backend/app/ports.py)。下文同时包含后续完整模块目标，不代表全部已实现。

你负责从最终ASR文本进入系统之后的理解、槽位、检索策略、业务状态机、工具执行约束、Prompt和RSI。音频识别／播放SDK、声网配置与云链路由王负责，你不重复开发ASR/TTS。

## 交付内容

1. Policy.decide_route纯函数：申请单事实＋关单规则→RoutingDecision。分清重复提交次数和通话尝试次数；回电／投诉优先级与未知分支有表驱动测试。
2. AgentPort：输入TurnInput和服务器Context；识别操作路径、标准、质疑、知识来源、结果预期、拒绝、错人、回电等意图。只用翟提供的已发布规则和QA，不让模型新增审核结论。
3. 状态机与守门：身份核对前不口头披露详情；缺槽位不播空模板；商家拒绝结束；未知／冲突升级。模型提出工具建议，代码检查case_revision、授权、route decision和告知证据后执行。
4. 业务异常：跨轮／跨session幂等、已派发操作不随取消消失、超时原key查证、回写失败单独重试。预约需服务端保存时间确认，改期旧确认失效；后台驳回不要求用户同意购买式确认。
5. RSI：完整Agent模拟器、归因分类、开发集／保留集、候选、确定性断言、gate-v2、采纳拒绝与回滚。禁止优化器自动改权限和官方知识。

## 验收

使用FakeModel＋翟Mock可跑完整流程；切换王的ModelPort后同样运行。提交状态转移表、工具schema、所有分支用例、可复现实验命令和原始脱敏证据。Agent不要保存第二套权威messages；历史由王的协调层注入。不要直接连DB执行SQL或绕过公共Repository／BusinessPort。

先交“授权缺项→身份确认→原因整改→追问不承诺通过→模拟驳回→回写”；并行交错人、拒听、京麦-only、二次未通和投诉例外。对源资料冲突提交issue给翟，不通过Prompt偷偷选答案。每天用真实三模块联调，修复本模块问题。

## 开工前共同确认的契约

本说明定义交付责任；字段和调用规则必须同时遵守 [模块契约](../05-contracts.md)、[公共DTO](../contracts/contracts.py)、[结构使用说明](../contracts/README.md) 和 [合成样例](../contracts/examples.json)。不得根据本说明另创一套同名字段。

- application_id标识申请单，case_revision绑定审核事实；session_id标识通话，turn_id标识输入轮次，generation_id标识可取消生成，operation_id标识独立业务动作。操作不能随生成取消而丢失。
- 所有内部业务端口由王在FastAPI启动时注入；魏调用翟实现的CasePort／KnowledgePort／BusinessPort，不另建模块间HTTP服务。
- 已受理写入超时返回unknown并按原operation查证；取消前未派发不执行，取消后已派发保存真实结果。Mock须区分这两种取消时间。
- 对接修改先改公共契约和样例，经王审核、两位调用方确认后合并；破坏兼容需升版本，不独自更名或改变字段含义。
- 第一次交接附启动命令、固定版本和fixture ID、预期事件／结果、失败与超时复现方法；仅交源文件或截图不算完成。

魏的提供项：AgentPort、Policy、ToolGuard、RSI评测与版本决策。依赖项：王提供ModelPort、历史、取消和Repository；翟提供申请单、规则检索和业务端口。不要同时在Agent和协调器各执行一次proposed_action：魏生成决策与守卫逻辑，王的协调器调用守卫并统一派发BusinessPort、记录结果，再交回Agent生成事实一致的回复。

## 0.3.0骨架已提供

实际接入点和当前签名见 [骨架交接说明](../../implementation.md)、[内部接口](../../../backend/app/ports.py) 和 [公共DTO](../../../backend/app/contracts.py)。以这些实现为准：BusinessPort统一execute/query_operation/writeback；start返回202；本轮预约仅转人工状态。不要照旧表重复开发HTTP工具服务。
