"""Additive indexes for routing/operation reconciliation (no duplicated state columns).

契约审查（.project-brief/CONTRACT-REVIEW-REQUIRED.md 第 3、4 条）要求：
- 不重复新增 `sessions.state`、`operations.key/status`、`call_records` 等**已存在**的列；
- 路由证据（decision_id / rule_version / evidence / attempt 计数）允许放在
  `sessions.data` JSONB，避免不必要的列迁移。

因此本迁移**不新增任何列**，只补两个能真实被当前查询用到的索引：

1. `ix_operations_active_next_at`
   协调器 reconcile_loop 每 200ms 扫描"未回写且已到重试时间"的操作，目前是全表扫描。
   表达式索引让该轮询命中索引；部分索引条件与 `process_operation` 的筛选一致。

2. `ix_sessions_application_state`
   跨会话幂等/回放（同一 application 的多个 session 共享一个业务效果）按
   `application_id` 检索，`state` 一起过滤可避免大表回表。

两者都用 IF NOT EXISTS 编写，`alembic upgrade head` 重复执行是幂等的。
downgrade 只删除本迁移创建的索引，不触碰任何既有列或数据。
"""
from alembic import op

revision = '0003'
down_revision = '0002'


def upgrade():
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_operations_active_next_at
        ON operations (((data->>'next_at')::double precision))
        WHERE status IN ('pending', 'unknown', 'not_found_yet')
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_sessions_application_state
        ON sessions (application_id, state)
    """)


def downgrade():
    op.execute('DROP INDEX IF EXISTS ix_operations_active_next_at')
    op.execute('DROP INDEX IF EXISTS ix_sessions_application_state')
