# 给翟雯的交付说明

**开工以0.3.0实际接口为准：** [骨架交接](../../implementation.md) 与 [ports.py](../../../backend/app/ports.py)。下文同时包含后续完整模块目标，不代表全部已实现。

你交付数据与Mock业务系统、规则查询端口、前端三个可独立运行的模块。Agent不直接读取Excel，也不直接访问数据库。

## 交付内容

1. 转换器：输入原始ZIP／xlsx，输出私有versioned JSON、source_manifest、issues、counts、mappings。按合并区填充分类；区分电话、书面、内部批注。主表68条必须保留，包括缺话术行，不静默丢弃。
2. 发布规则包：三场景优先人工复核；每条source_ref可定位文件hash＋sheet＋行；冲突条目quarantined。自动语义映射只能draft。导入幂等，坏包整批拒绝，审核后原子发布，不覆盖进行中会话版本。
3. Mock：合成merchant/application及已有审核结果，支持get_case、lookup、reject_application、create_callback、create_followup、query_operation、writeback。实现协议中的成功／失败／未知／取消，持久化结果，响应丢失后仍能查到操作。
4. 前端：通话开始结束、RTM单源字幕、接听方核验状态、驳回原因／整改／依据、关单渠道、预约／人工事件、实际写操作、指标和RSI对比。未核验不显示申请详情。展示“结果待确认”而不是猜失败。

模拟写接口是FastAPI内部BusinessPort实现，不另起不必要服务。UI独立开发可以用fixture事件，但每天换真实模块联调。管理发布入口需要admin；浏览器不能直接写申请单审核结论或数据库。

## 验收

新环境按README完成转换→校验→导入→启动Mock；可重跑不重复数据。给魏一个固定release_id、三条合成application_id、明确输入输出JSON和所有故障fixture。给王环境配置、迁移、前端连接步骤与异常日志。每条映射由翟与魏核实；王审查敏感字段／权限。

首先完成授权书缺项、授权过期、授权链不完整；再做京麦-only与二次未通预约。不要自行补业务标准，不把400热线做成真实外呼按钮，不导入真实商家手机号。

## 开工前共同确认的契约

本说明定义交付责任；字段和调用规则必须同时遵守 [模块契约](../05-contracts.md)、[公共DTO](../contracts/contracts.py)、[结构使用说明](../contracts/README.md) 和 [合成样例](../contracts/examples.json)。不得根据本说明另创一套同名字段。

- application_id标识申请单，case_revision绑定审核事实；session_id标识通话，turn_id标识输入轮次，generation_id标识可取消生成，operation_id标识独立业务动作。操作不能随生成取消而丢失。
- 所有内部业务端口由王在FastAPI启动时注入；魏调用翟实现的CasePort／KnowledgePort／BusinessPort，不另建模块间HTTP服务。
- 已受理写入超时返回unknown并按原operation查证；取消前未派发不执行，取消后已派发保存真实结果。Mock须区分这两种取消时间。
- 对接修改先改公共契约和样例，经王审核、两位调用方确认后合并；破坏兼容需升版本，不独自更名或改变字段含义。
- 第一次交接附启动命令、固定版本和fixture ID、预期事件／结果、失败与超时复现方法；仅交源文件或截图不算完成。

翟的提供项：CasePort、KnowledgePort、BusinessPort、已审核知识导入包及React前端。依赖项：王提供公共结构、会话HTTP／SSE与媒体凭证；魏提供路由规则解释和工具守卫。前端只接后端API与声网，不直接调用Python业务端口。申请详情和整改内容的展示DTO、媒体凭证DTO及管理台完整OpenAPI尚待王在骨架PR补齐；可先用fixture开发，不能声称这些接口已冻结。

## 0.3.0骨架已提供

实际接入点和当前签名见 [骨架交接说明](../../implementation.md)、[内部接口](../../../backend/app/ports.py) 和 [公共DTO](../../../backend/app/contracts.py)。以这些实现为准：BusinessPort统一execute/query_operation/writeback；start返回202；本轮预约仅转人工状态。不要照旧表重复开发HTTP工具服务。
