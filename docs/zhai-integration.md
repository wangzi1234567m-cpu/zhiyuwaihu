# 翟雯模块联调说明

以 0.3.0 的 `backend/app/contracts.py`、`ports.py` 和生成的 OpenAPI 为准。

## 起服务和合成用例

按根目录 README 安装前后端依赖。使用空的本地 PostgreSQL 数据库并设置 `DATABASE_URL`、`APP_ENV=development`、`PUBLIC_ORIGIN=http://localhost:3000`，依次运行 `./scripts/dev.ps1 Migrate`、`./scripts/dev.ps1 Backend`，另开终端运行 `./scripts/dev.ps1 Frontend`。迁移脚本也会加载可重复执行的合成种子。页面访问 http://localhost:3000。

合成知识版本为 `demo-k1`，演示申请均由 `demo` 账号操作：

- `00000000-0000-0000-0000-000000000001` 授权书缺项。
- `00000000-0000-0000-0000-000000000002` 授权过期。
- `00000000-0000-0000-0000-000000000003` 授权链不完整。

创建文本会话并开始。输入“我是演示商家N”后才应看到驳回原因和整改要求。继续输入“补齐材料一定必过吗”和“明白了”，等待 `operation.updated` 与通话回写。依据在回写记录中，刷新可重新取回。普通回电意图由语义模块处理；创建确认预约需要服务端保存的时间窗和证据。端口支持服务器已登记的预约和人工跟进命令，预约还要求同会话、同申请版本的有效确认。成功表示生成了模拟操作记录。

诊断时可用 `ENABLE_FAULTS=true` 启动开发后端，通过带登录 Cookie 和 `X-Requested-With: zhiyu` 的 POST `/api/v1/dev/faults` 提交例如 `{"fault":"lost_response"}`。每个写入场景用独立测试数据库或重置专用测试库：

- `write_failure` 持久化失败结果，按原操作查询仍为失败。
- `lost_response` 首次返回 `unknown`，按同一操作查询恢复实际持久化结果。
- `persistent_unknown` 首次返回 `unknown`，查询未找到时仍不得判失败。
- `writeback_failure` 保留等待回写，恢复 `none` 后由协调器重试。
- 取消用会话中断接口。派发前无写入，派发后通过原操作查证。中断文本生成与业务操作分别使用 `generation_id`、`operation_id`。

页面显示 0.3.0 文本 SSE 和服务端状态。核验后会在通话期间显示 `knowledge.used` 来源，回写后从记录恢复依据。指标区域消费现有 `metric.recorded` 事件，未收到事件时显示等待状态。浏览器测试用合成事件验证指标、身份隐藏和未知结果的展示。

在 `frontend` 执行 `npm run test:e2e -- fixture.spec.ts` 可单独使用合成 HTTP/SSE 数据验证前端，需启动前端并安装 Playwright Chromium。运行完整 `npm run test:e2e` 前，按根 README 使用专用测试库启动 FastAPI 和前端。后端数据库集成测试包含预约确认缺失、预约响应丢失后的查证、重复派发和人工跟进。

页面消费服务端指标事件；RTM 字幕和语音时延依赖媒体凭证及实际音频链路。

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

发布命令按新知识版本原子入库，无 HTTP 入口。服务端默认使用 `demo-k1`。后端启动时可设置 `ZHIYU_KNOWLEDGE_VERSION` 指向已导入版本。每次创建会话会保存版本，之后的检索和回写沿用该版本。

## 使用清洗后的场景

在仓库根目录执行以下命令。输出保存在本地忽略目录，相同输入可重复运行。也可通过后端环境变量 `MOCK_CLEAN_CATALOG` 指向仓库外的清洗文件。

```powershell
python scripts/clean_competition.py <原始ZIP路径> --output private-evidence/pop-v1-clean.json
./scripts/dev.ps1 Migrate
```

按上述起服务步骤登录。打开「关单规则试算」可选择 68 个主表场景，调整同场景提交次数、未接通次数、回电请求和投诉风险。未安装私有数据的环境会显示四个合成示例。试算只读。主表决定基础渠道，子表差异、残缺答案和三类授权场景跨表候选映射保留在私有 JSON 供复核。受控发布仍使用原暂存包和审核清单。

在「会话工作台」选择合成申请单 4（末位 4）并创建会话，可直接验证京麦模拟驳回、操作结果和书面关单回写。电话核验字段保持未核验，联系结果显示「无需电话核验」。申请单 1 至 3 仍可演示电话文本流程。

### 联调分支与未接通验收

本地已集成魏的 `af883f8`。服务端和前端试算都使用同一个 route-r2 决策表。规则试算不会写入业务系统；在会话工作台创建以下申请时会从 Mock 后端读取已登记的合成联系事实，经过工具守卫后写入数据库。

- 申请单 5（末位 5）：首次提交未接通，模拟京麦驳回，联系结果为未接通。
- 申请单 6（末位 6）：第二次提交且首次未通，创建再次联系跟进事件，记录中保留未接通；没有已确认的预约时间窗。
- 申请单 7（末位 7）：第二次提交且二次外呼仍未通，模拟京麦驳回。
- 上述样例使用 `demo-k1`。切换到仅包含三项授权映射的知识版本时，其余未发布场景转人工。验证业务幂等时应使用同一申请和审核版本，检查只产生一个模拟效果。

### 三项授权知识交接

准备原始 ZIP，然后运行：

```powershell
python scripts/convert_private_sources.py <原始ZIP路径> --output private-evidence/pop-v1-staging.json
python scripts/prepare_knowledge_review.py private-evidence/pop-v1-staging.json --release pop-v1-authorization-r1 --output private-evidence/pop-v1-review-draft.json
```

草稿为授权缺项、授权过期、授权链不完整生成三个稳定的条目 ID，列出主表、电话和书面来源，未填署名或核准正文。与魏逐条核实适用场景、槽位和最终用于合成申请的正文后，另存审核清单。只对核实通过的条目标记 approved，填写实际审核人并处理问题标记。冲突项保留 quarantined。用上文的受控发布命令先校验再导入。向王交接公共 DTO、权限边界和故障复现步骤。

### 查看 RSI 对比

```powershell
python scripts/rsi_run.py --suite dev --out private-evidence/rsi-dev.json
```

在「RSI 评测」中选取报告文件，页面在本机读取基线和候选的通过次数及完成率。gate 报告含 dev 和 holdout 时分开展示；开发集通过率不触发策略采纳。私有报告、原文、凭证均留在 Git 忽略目录。
