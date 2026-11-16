# 公共结构样例

本目录只有设计阶段的数据结构、生成脚本与合成 JSON 样例，没有 FastAPI 服务／Agent／Mock 实现。运行需 Python 3.12 与 Pydantic 2；本轮在已有 Pydantic 2.14.2 环境验证。

```powershell
python docs/design/contracts/export_contracts.py
```

生成 `shared.schema.json` 和 `examples/`。schema 是 `$defs` 定义集；校验具体文件时选择 `examples/manifest.json` 指定的模型，如 `#/$defs/CreateSessionRequest`，不能对空根 schema 校验后误以为所有实例正确。Pydantic 的跨字段 validator 不一定全部体现在 JSON Schema 中，服务端必须保留校验；前端类型不能代替权限检查。

样例中的 UUID、token、quote_hash、版本和时间都是合成占位，不可用来接通声网。`metric.event.json` 展示超时且 value_ms 为 null，未编造性能成绩。各事件文件是独立结构示例，不是一条真实完整回放；状态变更的实际时序以实现后的 trace 为准。

voice 会话不接受 `text-turn.request.json`；该文件供单独创建 text 模式会话时使用。真实报价 hash 必须由服务端对标准化条款计算，不能采用示例中的重复字符。确认记录只能由服务端产生，不存在接收该结构的公开确认 API。

后续实现：将结构迁到正式公共模块，FastAPI 直接引用；导出 app.openapi() 后通过锁定的 openapi-typescript 生成 TypeScript。别同时维护手写 Python、JSON Schema、TS 三套权威定义。内部端口完整行为见上一级模块契约；运行时协议类型在实现相应端口时按文档添加并共同评审。
