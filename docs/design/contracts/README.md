# 公共结构 0.3.0

V0.4新增进程内[实时交互与业务公共契约](realtime-v04.md)，DTO源位于 `backend/app/realtime_contract/`，生成物为 [realtime-v04.schema.json](realtime-v04.schema.json)。本轮只交付接口定义，不实现替身或运行接线；原0.3 API与下述生成流程保持不变。

唯一手写源已迁移到 [backend/app/contracts.py](../../../backend/app/contracts.py)。本目录contracts.py仅为兼容导入入口；生成字段与实际签名以新源和 [OpenAPI](openapi.json) 为准。运行 `python scripts/export_contracts.py` 和 `npm run types --prefix frontend` 同步前端类型。

# 0.2设计说明（历史语义，差异见骨架交接）

`contracts.py` 是唯一手写公共 DTO 源；不是后端、数据库模型或鉴权器。运行 `python docs/design/contracts/export_and_validate.py`（Pydantic 2）生成 [schema.json](schema.json) 与 [examples.json](examples.json)。合成案例 ID、版本、场景代码不是官方数据映射。

Schema 的 `$defs` 包含全部对象；调用方选择对应模型的 `$ref`，不要把整个定义集合当成一个请求。事件类型由 `payload.type` 判别，SSE 的 `event:` 也取这个值；SSE `id:` 取单会话 seq，JSON 中 event_id 是持久化UUID。HTTP错误响应使用ApiError。

正式应用的路由应直接引用这些 DTO；前端用 FastAPI 导出的 OpenAPI 通过 openapi-typescript 生成类型。生成产物入库，CI重新生成后检查差异。离线 Mock 在未有 FastAPI 前可直接用JSON Schema验证，不手抄字段。AgentDecision 是代码Agent返回的提议，协调器仍检查合法转移；不得让模型自己写权威state。

ActionCommand只能由服务端守门后构造。confirmation_ref的存在只满足结构约束；还必须查询数据库验证接听方、申请单、版本、时间窗、过期／撤销及最终输入证据。拒绝客户端布尔字段不会自动实现全部业务安全。

所有示例共享application=1、session=4、turn=5、generation=6、operation=10的UUID尾号，用于串联模块。unknown操作保留原operation和幂等key，不能当失败重新提交。预约示例仅验证DTO，不是已获真实商户授权。

TextTurn、StartSession、InterruptRequest 已提供公共定义。媒体凭证和管理台DTO由王梓恒在基础骨架PR中补充OpenAPI；当前核心契约支持文本Agent、Mock端口和业务状态展示并行开工。provider turn字段必须通过声网云端实测固定，不能假造已经贯通。

ActionCommand.payload 按 type 判别：驳回包含依据ID、原因与书面意见；预约引用确认记录，由工具从服务器读取时间窗；人工跟进包含原因及原操作ID；回写引用已持久化CallRecord及其版本。BusinessPort.writeback(command, record)须核对两处ID／版本一致。未接通的系统二次跟进使用create_followup，不虚构商家确认记录。

## 语音增量（2026-11-18）

本轮保留原有业务端口和必填字段，新增语音媒体／生命周期／观测DTO，SessionView增加默认text的mode，ModelRequest增加可选相关ID及allow_rephrase，Capabilities.model允许deepseek。Event.text.delta允许voice_transport；语音UI不把该流当字幕。实际接口签名及验收状态见 [语音接入交接](../../voice-integration.md)。

Qwen TTS增量：新增会话绑定的`POST /integrations/agora/sessions/{sid}/v1/audio/speech`，仅供声网调用，不供浏览器提交业务。供应商协议结构唯一手写源为`backend/app/tts.py::SpeechRequest`，其JSON Schema自动进入OpenAPI；输出24kHz单声道PCM16。model／voice由服务端固定，鉴权与LLM入口分离，允许starting状态的主动开场，拒绝结束／过期会话；不会改变Agent或BusinessPort签名。详细错误与配置见接入交接。
