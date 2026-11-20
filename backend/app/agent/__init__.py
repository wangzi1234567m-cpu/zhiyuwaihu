"""Agent 包：基于结构化理解的 AgentPort 实现（生产主链）。

`SemanticAgent` 是唯一的生产 Agent 实现：
- 理解来自注入的 `UnderstandingPort`（规则替身 或 真实 GLM，见 `app.semantic`）；
  FakeModel/fixture_response 路径完整保留供可重复测试（`app.adapters.FakeModel`
  仍负责"把已校验回复分片流出"，与理解解耦）。
- Agent **不**连数据库、不写 `verified`、不自行派发工具、不保存第二套权威历史；
  历史、上下文、知识由协调器注入；dispatch 只由协调器在守卫后执行。
- 依据选择按 scenario/version/scope/approved/no-conflict；缺槽位不播空模板；
  不拿 knowledge[0] 应付全部问题；冲突转人工。
"""
from .semantic_agent import SemanticAgent, select_grounding

__all__ = ['SemanticAgent', 'select_grounding']
