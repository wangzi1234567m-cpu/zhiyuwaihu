# 翟雯交付 · 数据、Mock 与前端

分支 `codex/zhai-mock-ui-main` 从 `main@e381f53341fdf146294781a508f1643ffbbaab54` 创建。数据与 Mock 提交为 `1eec9e0`，共用接口接入补丁为 `12e4410`。两者都可以在本分支内审阅。

## 返修项和验收结果

| 项目 | 实现与复验结果 |
| --- | --- |
| Z1 最终写入 | Mock 在效果事务中锁定当前申请，核对归属、版本和审核结论。登记后改版本或把结论改为 approved、pending，结果为明确失败，新增业务效果为 0。历史成功在版本改变后仍可按照原操作查询。 |
| Z2 模板 | 电话和京麦均从服务器审核任务取值。三场景逐一测试两组槽位值、逐项留空、占位符残留以及固定强调文字。核验前隐藏原因和依据。京麦用已登记书面命令验证到业务事务层；完整自动路由由架构侧接入魏的策略后联调。 |
| Z3 可重跑 | 原件在新目录连续转换、清洗两次得到相同内容。已有不同文件会保留并明确报错。同一审核包重复导入只保留一份，已发布版本不可覆盖。 |
| Z4 隔离 | 高频 QA 的 C8、C11，话术表 B8、特殊场景 B6 均保留来源和问题原因；特殊场景 D13 继续隔离。主表第 28、59 行缺书面正文，保留原行。32 条子表差异保留待审。 |
| Z5 授权链 | 通用授权链引用主表 22、52 行，特殊场景 B26 和书面 B27。书面 B50 单列全球购医药范围。发布校验会拒绝将 B50 用作通用授权链依据。 |
| Z6 标识和查证 | 场景、话术、QA 分别有稳定 ID，按条目种类、工作簿哈希、表、分类或标签、行定位。来源版本变化产生新 ID；迁移时复核旧、新来源对应关系，再生成新发布版本。查证超时或服务失败会保留原操作和幂等键；恢复后可查到真实结果。 |

本地在独立 PostgreSQL 数据库完成迁移后，全量后端 **102 项通过，0 跳过**。原件计数为 68 条主场景（电话 54、京麦 14）、80 条话术、28 条 QA、5 个京麦标记。三个授权场景使用原件审核意见、合成申请与测试身份导入并运行，断言得到 **3 条成功效果、3 条回写**，书面命令为实填正文。读取原工作簿时 openpyxl 提示缺省样式，计数与引用断言均通过。

两项浏览器验收分别覆盖真实本地文本服务的三轮会话、回写与刷新恢复，以及合成快照下的身份隔离、未知结果、响应指标和 RSI 报告对比。TypeScript 类型检查、生产构建通过。第二次导出 Schema、OpenAPI 和前端类型，三份文件内容一致。语音与 TTS 运行的是主干离线回归。

## 从原件复现

在仓库根目录按 README 安装锁定依赖、激活 Python 环境。将下列两个输入变量指向持有的原件和负责人提供的审核意见。

```powershell
$env:PYTHONPATH='backend'
$env:ZHAI_ACCEPTANCE_ZIP='D:/private/智语外呼-赛题说明.zip'
$env:ZHAI_ACCEPTANCE_OPINION='D:/private/授权场景原件审核意见.txt'
$out='private-evidence/rework-v2'
python scripts/convert_private_sources.py $env:ZHAI_ACCEPTANCE_ZIP --output "$out/staging.json"
python scripts/clean_competition.py $env:ZHAI_ACCEPTANCE_ZIP --output "$out/clean.json"
python scripts/prepare_knowledge_review.py "$out/staging.json" --source-opinion $env:ZHAI_ACCEPTANCE_OPINION --release authorization-rework-v2 --output "$out/release-candidate.json"
```

候选包记录委托核对意见、来源和模板，发布签名留空。负责人授权的发布人员完成真实审核记录后，可用私有的 `reviewed-release.json` 先校验再导入：

```powershell
python scripts/publish_reviewed_knowledge.py "$out/staging.json" "$out/reviewed-release.json"
python scripts/publish_reviewed_knowledge.py "$out/staging.json" "$out/reviewed-release.json" --publish
```

设置 `ZHIYU_KNOWLEDGE_VERSION` 为已导入的 release_id，再启动服务，新会话绑定该版本。默认合成知识版本为 `demo-k1`。历史申请若缺少必填审核事实会转人工；种子重跑会保留已有申请。开发环境的路由目录默认读取上述 `clean.json`，可用 `MOCK_CLEAN_CATALOG` 指定其他私有清洗文件。

在 README 的独立 `*_test` 数据库上设置 `APP_ENV=test`、`RUN_DB_TESTS=1` 和对应的 `DATABASE_URL`。保留上面两个原件环境变量，以下命令会迁移并重置**该测试库内的业务数据**，测试用发布身份只用于这次验收。

```powershell
python -m alembic -c backend/alembic.ini upgrade head
python -m pytest backend/tests -q
```

默认三条申请的 UUID 依次为 `00000000-0000-0000-0000-000000000001`、`00000000-0000-0000-0000-000000000002`、`00000000-0000-0000-0000-000000000003`，分别对应授权书缺项、过期、缺链。登录后选择申请，点击开始，回复“我是演示商家1”（按选中的编号调整），确认整改内容后回复“明白了”，页面可查业务操作和回写状态。开发人员可通过 `seed(include_routing_fixtures=True)` 在独立演示库准备第 4 至 7 条书面及未接通任务，供策略联调。

## 给架构和 Agent 的接入点

共用文件的精确改动可用 `git show 12e4410 -- backend/app/coordinator.py backend/app/main.py backend/app/contracts.py` 查看。主干保持 0.3.0 的内部端口签名，页面继续使用原有 VoicePanel、RTM 字幕和 generation/seq 更新逻辑。界面从会话快照读渠道、操作类型、最新指标和核验后的来源。RSI 对比读本地报告。

Agent 和 Guard 应消费同一组已经渲染的 KnowledgeEntry。用会话绑定版本查询条目，按当前路由选取渠道，调用 `available_entries(entries, case)`；空结果转人工。京麦在构造 RejectPayload 前也调用该函数，取渲染后的 approved_text 和对应 entry_id。Mock 最终事务会用当前任务和已审原条目再做校验。完整联系策略接入前，规则试算页仅显示主表基础渠道。

查询故障的可运行样例见 `backend/tests/test_zhai_delivery.py` 中的 `test_reconcile_recovers_original_success_after_revision_change`。先模拟执行成功但丢失响应，再使查询超时或暂时失败，随后恢复并按原 operation_id 查询。手工联调限开发、测试环境下启用 `ENABLE_FAULTS=true` 的演示账户；向 `POST /api/v1/dev/faults` 传入 `{"fault":"query_timeout"}` 或 `{"fault":"query_failure"}`，恢复使用 `{"fault":"none"}`。另有 write_failure、persistent_unknown、lost_response、writeback_failure 故障。
