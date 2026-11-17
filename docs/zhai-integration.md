# 翟雯模块联调说明

以 0.3.0 的 `backend/app/contracts.py`、`ports.py` 和生成的 OpenAPI 为准。

## 起服务和合成用例

按根目录 README 安装前后端依赖。使用空的本地 PostgreSQL 数据库并设置 `DATABASE_URL`、`APP_ENV=development`、`PUBLIC_ORIGIN=http://localhost:3000`，依次运行 `./scripts/dev.ps1 Migrate`、`./scripts/dev.ps1 Backend`，另开终端运行 `./scripts/dev.ps1 Frontend`。迁移脚本也会加载可重复执行的合成种子。页面访问 http://localhost:3000。

合成知识版本为 `demo-k1`，以下申请均由 `demo` 账号操作：

- `00000000-0000-0000-0000-000000000001` 授权书缺项。
- `00000000-0000-0000-0000-000000000002` 授权过期。
- `00000000-0000-0000-0000-000000000003` 授权链不完整。

创建文本会话并开始。输入“我是演示商家N”后才应看到驳回原因和整改要求。继续输入“补齐材料一定必过吗”和“明白了”，等待 `operation.updated` 与通话回写。依据在回写记录中，刷新可重新取回。输入“预约回电”应进入人工处理，现有守卫未派发预约。端口支持服务器已登记的预约和人工跟进命令，预约还要求同会话、同申请版本的有效确认。成功表示生成了模拟操作记录。

诊断时可用 `ENABLE_FAULTS=true` 启动开发后端，通过带登录 Cookie 和 `X-Requested-With: zhiyu` 的 POST `/api/v1/dev/faults` 提交例如 `{"fault":"lost_response"}`。每个写入场景用独立测试数据库或重置专用测试库：

- `write_failure` 持久化失败结果，按原操作查询仍为失败。
- `lost_response` 首次返回 `unknown`，按同一操作查询恢复实际持久化结果。
- `persistent_unknown` 首次返回 `unknown`，查询未找到时仍不得判失败。
- `writeback_failure` 保留等待回写，恢复 `none` 后由协调器重试。
- 取消用会话中断接口。派发前无写入，派发后通过原操作查证。中断文本生成与业务操作分别使用 `generation_id`、`operation_id`。

页面显示的是 0.3.0 文本 SSE 和服务端状态。RTM、媒体凭证、指标与 RSI 对比依赖团队后续接入，京麦-only 路由和二次未通转预约依赖规则和守卫。

## 私有资料

安装转换依赖 `python -m pip install openpyxl==3.1.5`。用两份原始 xlsx 或带白名单文件的原始 ZIP 生成私有暂存包：

```powershell
python scripts/convert_private_sources.py <原始ZIP或两份xlsx> --output <仓库外的私有JSON路径>
```

输出包含逐行原值、展开后的主表分类、来源坐标、渠道分类、独立批注、异常和计数。两个工作簿须匹配版本哈希。暂存包包含原始文字，放在私有目录。列语义、话术用途及场景映射由翟和魏复核，冲突项标记 `quarantined`。

审核清单私有保存，包含 `schema_version`（`0.3.0`）、`release_id`、`staging_sha256`、`reviewers` 和 `entries`。每个条目使用既有 `KnowledgeEntry` DTO，`knowledge_version` 等于 `release_id`。同一发布版本的重复执行幂等。复核后使用受控数据库账号：

```powershell
$env:PYTHONPATH = 'backend'
python scripts/publish_reviewed_knowledge.py <暂存JSON> <审核清单JSON>
python scripts/publish_reviewed_knowledge.py <暂存JSON> <审核清单JSON> --publish
```

发布命令按新知识版本原子入库，无 HTTP 入口。服务端当前固定使用 `demo-k1`，正式版本切换由协调器接入。真实来源包和正式审核清单需由团队提供与复核。
