# Excel 清洗与数据设计

数据所有者：翟雯。规则解释和业务映射由翟与魏复核；王审查接口及权限。Excel是来源，版本化JSON是导入包，PostgreSQL是运行时权威；不把Excel或评审批注整段直接塞进Prompt。

## 导入管道

ZIP只读取白名单docx/xlsx，忽略__MACOSX和目录项，限制解压大小并检查路径；保留源文件hash。先扫描sheet、合并范围、有效行和公式，再生成原始行记录，不能用max_row当记录数。

制度主表以《场景汇总》为完整索引，实读D13:D80有68条电话／京麦记录；A2为关单总原则，12行为表头。三级分类只在其所属合并区域展开；非合并空白按明确组边界决定是否填充，不能将话术、备注、关单方式无脑向下填充。保留原值和修订值。子表并非互斥全集，不能用14＋37覆盖68。

1. 标准化空格、换行、全半角；稳定ID不单纯依赖行号，用文件版本＋分类路径生成，并记录跨版本映射。
2. 将话术分为phone、written、internal_review；评审建议只进入review_notes，禁止当客服回答。
3. 占位符逐项命名：company_name、shop_name、brand_name、missing_document、authorization_from/to等。缺值不得播出空【】，改追问或人工。
4. QA场景标签缺失时建立独立qa_id；语义匹配只给候选，保留mapping_status和人工审核人。特殊场景中的“京麦驳回”单独提取channel_override，不进入用户问题字段。
5. 冲突、残句、错配条目进入quarantined；不自动补全文义或选择更严格数字。发票时限依品类、店型等scope区分，无法区分则人工。
6. 校验JSON Schema、必填槽位、引用存在性、唯一ID、记录数与渠道分布。导入暂存表，经审核manifest签出后原子发布。每个会话固定knowledge_version/rule_version，不能热变。

JSON条目以contracts.py的KnowledgeEntry为准：entry_id、knowledge_version、kind(scenario/qa/script/routing_rule)、scenario_id、taxonomy、scope、channel、required_slots、approved_text、source_refs(file_sha256,sheet,row,columns)、status(draft/approved/quarantined)、issue_codes、reviewer。数据库列名可不同，由Repository显式映射。原始授权资料仅私有保存，公开Git仅schema、清洗代码和纯合成fixtures。

## 表与归属

| 表 | 关键字段／约束 | 写入者 |
|---|---|---|
| knowledge_releases | id,manifest_hash,status,created_at；发布不可原地改 | 翟 |
| knowledge_entries | release_id,id,kind,taxonomy,scope,channel,slots,text,source_ref,status；unique(release_id,id) | 翟 |
| knowledge_mappings | source_id,target_id,status,reviewer,confidence；不把confidence当批准 | 翟＋魏 |
| merchants | id,synthetic_name,shop_name,allowed_owner；无真实电话 | 翟 |
| applications | id,merchant_id,revision,scenario_id,review_conclusion,slots,submission_attempt,callback_requested,complaint_risk | 翟Mock |
| call_attempts | id,application_id,case_revision,attempt_no,outcome,contact_verified,explanation_status | 王协调器 |
| sessions | id,owner,application_id,state,revision,epoch,versions,current_generation,media_binding | 王 |
| messages | id,session,turn,generation,role,redacted_text,generated_text,played_text,playback_status | 王 |
| confirmations | id,application_id,kind=callback,window,timezone,evidence_message,revoked_at,expires_at | 魏通过Repository |
| operations | id,application_id,case_revision,kind,key,payload_hash,status,result,next_attempt；unique(key) | 王骨架＋翟工具 |
| callbacks_followups | id,operation_id,window,reason,status；unique(operation_id) | 翟 |
| call_records | application_id,session,revision,outcome,reason_ids,rectification,merchant_questions,source_refs,contact_status,explanation_status,operation_ids,versions | 翟回写 |
| events_metrics | session,seq,type,payload,clock_domain,source；unique(session,seq)，按type分区或索引 | 王 |
| strategy_versions_decisions | 内容hash、parent、评测引用、决策、active指针revision | 魏 |

主表应用ID必须与会话授权绑定，LLM不能修改application_id、review_conclusion或submission_attempt。审核任务在系统侧可读，口头披露受接听方核验约束。主键UUID、时间timestamptz、结构化槽位JSONB。应用更新用revision CAS；业务状态＋事件同事务，网络调用在事务外。幂等驳回键以application_id/case_revision/action唯一化，防止两个session重复执行。

调用日志、确认、操作、订单替代物（驳回记录）保持可追溯；取消不删除。终态回写失败保存pending并恢复。Raw API key、核验原值、联系人全号不记录。合成开发数据默认30天，细粒度遥测7天、SSE24小时；录音默认关闭，启用后私有文件＋元数据关联并按用途删除。官方数据遵守赛期保留和销毁要求，不默认30天外传Langfuse。

需要交付的是可复现转换器＋私有JSON包＋异常报告＋审核manifest＋幂等导入命令；本轮只制定此交付要求，未把全部工作簿自动批准成有效规则。
