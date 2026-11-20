# 独立复验：五类缺陷修复记录

后续原生独立复审又完成回写超时、稳定ID、派发版本快照与并发载荷修复，完整回归增至158项；最新说明见[NATIVE-RESPONSE.md](NATIVE-RESPONSE.md)。下文保留初轮152项复验事实。

2026-11-17。初审基线`e48a483`，理解层修复已存入`23de989`，本记录描述其后R1-R4返工。

## 已验证的结果

- `python -m pytest backend/tests -q --tb=short`：**152 passed，0 failed，0 skipped，24.05s**，真实PostgreSQL。
- 原有141项测试保留，新增11项`backend/tests/test_review_regressions.py`随仓库长期运行。
- 新增覆盖：联系次数变化不重复驳回；0.3与夜间两种旧键兼容；旧会话/路由版本及decision_id拒绝；派发后结束继续回写；规范expires_at拒绝；真实GLM适配槽位与高风险意图依据校验。
- `npm run typecheck --prefix frontend`、`npm run build --prefix frontend`均退出0。
- 宿主使用已有继承密钥、已授权GLM端点/模型，修复后跑产品Services+协调器+真PostgreSQL的三轮文本：**3次真实模型理解请求，0失败/0重试；CLOSED；模拟业务效果1条；通话记录1条**。回复采用有据模板，业务工具为合成Mock，未涉及真实京麦或语音。

## 修复与审查分工

业务实现提案由已配置GLM生成，主Codex审阅后应用精确文本替换、执行独立回归。未直接执行模型返回的脚本。原工作台DSH批次因长时间无活动停止；单项提案和原失败证据均保留在用户私人长期项目审查目录。

1. **R1**：驳回业务键去除提交/联系次数。原Application行锁内兼容`application:revision:reject_application`旧基线键及带次数后缀旧键，不改已派发命令、不清库。
2. **R2**：守卫绑定会话、持久化RoutingDecision与command的申请单、审核版本、decision_id和规则。旧版本证据不再自动升级。
3. **R3**：结束交流后，已登记操作的EXECUTING/RECONCILING/WRITEBACK_PENDING状态保留，业务继续查证/回写。
4. **R4**：预约确认使用规范expires_at与带时区now比较，并核对确认、申请单、版本和时间窗。
5. **R5**：真实GLM适配器把原文交给校验门；无原文依据的高风险意图降级，合法槽位保留。

## 对测试的必要校正

原有一项测试强制要求驳回键包含s/c次数，与“同申请单版本不重复办理”的契约冲突。GLM把此断言改为次数变化时驳回键稳定，同时保留/增加动作、申请单、审核版本区分以及跟进次数变化的断言，没有删掉安全覆盖。

独立复现的等待辅助曾假定第二会话必须CLOSED；原项目跨会话重放契约允许MANUAL_REVIEW并链接原成功操作。复验改为允许该既有合法结果，同时要求仅1个已成功operation且数据库效果总数仍为1。原错误实现会生成2次效果，仍会失败。

## 环境与复现

只在本mission自有54329集群运行，使用独立`zhiyu_codex_review_20261117_test`；真实GLM流程用另一个`zhiyu_codex_real_glm_20261117_test`，每次新增合成申请单，不清空上次模型证据。

```powershell
# DATABASE_URL应指向自己的专用*_test库，先完成迁移。
$env:APP_ENV='test'
$env:RUN_DB_TESTS='1'
$env:PYTHONPATH='backend'
$env:ZHIYU_MODEL_MODE='fake'
.venv/Scripts/python.exe -m pytest backend/tests -q --tb=short
npm run typecheck --prefix frontend
npm run build --prefix frontend
```

不设RUN_DB_TESTS时，数据库集成项目仍会按设计skip；这不是152项全通过的口径。

## 仍待交付验收的边界

- 修复后已用本机Chrome、独立测试库复跑完整三轮浏览器流程：1 passed，2.3s，包含结果回写与刷新恢复。GLM修正过时的旧句号断言，同时检查“不能保证通过”与“不能承诺时限”，其余检查保留。默认FakeModel浏览器结果与前述真实GLM服务层验证分别留证。
- 原生OpenRig席位已恢复ready/running，但整项目证据收集仍会遇运行WAL/缓存及材料数量体积限制；本记录不等于工作台最终accepted。
- RSI保留集仍有reject/证据不足，不把安全拒绝改成采纳。
- 真实语音、官方统一指标、远程PR未完成。GitHub连接对原仓库只有pull权限，不应宣称已推送或已开PR。
- 历史Git中曾混入运行数据，发布前需生成基于干净基线的可审交付，不能把大体积运行库历史一起公开推送。

完整原失败日志、GLM提案和宿主实测输出保存在用户私人长期项目`artifacts/review-20261117/`；公开交付只纳入必要的脱敏结果与可运行回归。
