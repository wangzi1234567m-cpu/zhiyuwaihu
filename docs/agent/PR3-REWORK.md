# PR #3 返修交付（2026-11-21）

本分支替代已关闭的 PR #3，供团队复核，不自动合并。基线为原 PR 提交
`af883f8404a82ac41166217cd5e9b97f6441b1fb`，已合入 main
`e381f53341fdf146294781a508f1643ffbbaab54`（包含媒体基线
`a64403a63931d5fb6654ed8983c4d7f4dab88adc`）。公共契约仍为 **0.3.0**。
实际返修提交 SHA 在 PR 和交付清单记录，不在自身提交中填写循环引用。

## 责任与返修结果

| 项 | 修改与可核验结果 | 状态 |
|---|---|---|
| W1 模型遗漏例外 | `validate_understanding` 恢复原文明确的投诉、错人、拒绝和回电；Agent 决策前复验，澄清未完成不办理。实际 GlmUnderstanding→Agent→Coordinator→Guard→PostgreSQL Mock 链用 MockTransport 检查 | 魏侧完成 |
| W2 否定回电 | 模型回电意图与规则共用原文否定作用域；保留转折后的新请求、正反问、双重否定和自身状态后的真实请求 | 魏侧完成 |
| W3 采纳门 | 校验版本化 voice/cost 双臂报告：候选 >=10 通/30 轮，P50<=2000ms，失败率不增；真实模型请求数、基线 TTFT/成本、有限数、来源引用、样本去重均必需；成本与 TTFT 分别<=1.1 | 魏侧完成；真实采纳证据未齐 |
| W4 执行事务版本竞态 | 魏侧 case_revision/decision_id/授权/告知守卫保留。独立探针实测排队后升版：守卫拒绝，但 main Mock 仍写入；已成功操作升版后历史查证正常 | **待翟修 Mock 事务后联调，不宣称闭合** |
| W5 主干媒体接线 | 保留 main 的 VoiceGateway、ModelPort、DeepSeek、TTS、鉴权、取消及持久额度；语音写入同时要求开关与可信播放证据；所有显式注入保留 | 接线与离线回归完成；共享文件待王复核 |

`frontend/app/page.tsx`、样式、Next 配置、VoicePanel 和 smoke 用例均按 main 保留；撤回魏原先的页面和私有前端辅助。相对 main，前端仅有**由合并后公共 DTO 自动生成**的 `api.generated.ts` 差异。没有接管翟的页面或 MockBusiness。

`adapters.py` 只合并 Services 装配；MockBusiness 类与 main 一致。
`coordinator.py` 是共享接线：保留魏的状态/路由/幂等/恢复逻辑与 main 的媒体生命周期，删除只适配旧 DemoAgent 的窄状态白名单，继续由显式状态图、动作关系和持久拒绝记录约束。共享差异请王复核。

## 精确反例对照

同一 MockTransport 响应、真实 PostgreSQL 合成业务链，修前为冻结 af883，修后为本分支；**均为零真实云请求**。

| 输入 / 模型响应 | 修前 | 修后 |
|---|---|---|
| “明白了，但我要投诉” / 只返回 acknowledge | CLOSED，1 条驳回效果 | MANUAL_REVIEW，0 operation / 0 effect |
| “明白了” / acknowledge | CLOSED，1 条效果 | CLOSED，1 条效果，正常路径保留 |
| “不用回电，直接说原因”“我不想回电”“我不愿意回电” / request_callback+ask_reason | CALLBACK_PENDING | QA，无回电、无业务效果 |
| 一通一轮、P50=9000ms、失败率=1 的旧式报告 | adopt | refuse_auto_adopt，证据不充分 |

修前新增回归 **14 failed / 59 passed**；其中包括新证据格式正向控制，不能将 14 直接称为 14 个独立产品缺陷。修后完整后端 **377 passed、0 failed、0 skipped**，在新建、迁移至 head 的 `_test` PostgreSQL 库执行。原有测试保留；解析回电用例补真实原文支持，语音旧中断用例用 Event 保持生成中，避免短回复完成造成时序误报，断言未放宽。

独立复审曾对首轮 4fdd1fe 提出一项高优先级问题：两臂内部唯一，但语音 sample_ids 可跨 baseline/candidate 复用。补充“全部复用”和“部分复用”两个反例在首轮代码上均失败；GLM 补丁新增跨臂集合交集拒绝，证据不独立时 refuse_auto_adopt。来源引用仍可指向包含两组测量的同一份报告，不据此要求重复采集文件。全量在此补丁后重新运行；首次复审的 CHANGES_REQUESTED 保留，不冒称初次通过。

新回归在 `backend/tests/test_pr3_model_safety.py`、`test_pr3_gate_evidence.py`、`test_pr3_shared_contract.py`。语音、DeepSeek、TTS、持久额度的 main 测试一并运行。Schema、OpenAPI、TypeScript 重新生成；类型检查、生产构建通过。浏览器结果及源码哈希见交付证据。

## 生产模型与授权知识边界

- 默认保持 main 的 `MODEL_PROVIDER=fake`、云调用关闭、`VOICE_WRITES_ENABLED=false`。生产模型切换继续使用 main 的 `fake/deepseek`；工作台 GLM 仅用于开发实现提案，旧 `ZHIYU_MODEL_MODE=glm` 不自动接管生产。结构化理解默认规则实现；`GlmUnderstanding` 仍支持显式注入和 MockTransport 回归。若要新增生产结构化 ModelPort，先与王确认契约，不把开发网关硬接成团队模型。
- 授权缺项需公司/品牌/缺项，过期需公司/品牌，链条需公司/品牌/上下游；只消费翟已审核、已实填正文。当前无独立模板渲染器，仍含 `{brand_name}` 等占位符时转人工，不播空模板、不自建第二套渲染器。保留既有 `{company}` 包装，不编造有效期、保证、审核结论。
- 链条一般话术审核意见指向“驳回话术表 B27”，不能借用 B50 发票替代（全球购医药）。本轮只记录交接，不修改/发布官方知识。身份前不披露详情，演示与正式业务明确区分。
- 返修材料未提供原始 Excel 或所提及的 `private-evidence/authorization-review.json`；没有声称重新审核原件。相关知识由翟核实并发布后再联调。

## RSI 证据和复现

开发集 24 场景×3 次×2 版本：baseline **69/72**，candidate **72/72**；228/400 为 fake 逻辑请求，真实模型请求 **0**，超时/拒绝 **0**，生成器只读 dev。报告包含候选/基线内容 hash、预算与读取记录。没有创建、读取新的无偏保留集，也未修改 active_version。

gate 输入版本为 `gate-v2-evidence-1`。voice 的 baseline/candidate 必须包含 calls、turns、failed_calls、p50_ms、sample_ids；cost 双臂包含 real_model_requests、model_ttft_p50_ms、total_cost，并指定 cost_unit。两者需 source 与非空 source_refs。缺字段、非法值、缺基线、样本不足 -> refuse_auto_adopt；完整测量超阈值 -> reject；硬安全失败始终 reject。CAS/拒绝历史/仅回滚已验证版本的限制保留。

来源字段是提交方声明，**不构成来源真实性证明**；真实生产者及原始测量由协调者审核，不能把测试中的合成 `real_voice` 字段或 fabricated hash 当真实证据。当前没有满足采纳条件的真实证据，故不自动采纳。

```powershell
# 先准备自己的 *_test PostgreSQL，安装锁定依赖，保持云调用关闭。
$env:RUN_DB_TESTS='1'
$env:MODEL_PROVIDER='fake'
$env:CLOUD_CALLS_ENABLED='false'
$env:VOICE_ENABLED='false'
$env:VOICE_WRITES_ENABLED='false'
python -m alembic -c backend/alembic.ini upgrade head
python -m pytest backend/tests -q --tb=short
python scripts/export_contracts.py
npm --prefix frontend run types
npm --prefix frontend run typecheck
npm --prefix frontend run build
python scripts/rsi_run.py --suite dev --repeats 3 --max-requests 400 --max-real-requests 0 --out YOUR_REPORT.json
# W4 是独立跨模块验收，当前预期非零；会重置该专用测试库的合成数据。
python scripts/probe_pr3_w4.py
```

交付完整性不等于整场验收：W4 外部事务修复、官方知识发布、真实媒体测量与无偏保留集仍未完成。请按上述边界审查本分支。
