"""V-KPI Action Inbox domain (W1 · Auto-Ops OS 第一刀, dry-run only).

每天聚合 8 类待办建议(只产不执行、不写业务表、零触 viltrox_fit_score)。
- producers.py: 8 个独立 SELECT 生产者(各自 try/except 容错,坏一个不拖垮整盘)。
- inbox.py: 编排 + 幂等落库 vkpi_action_inbox + run 级 ledger 审计 + scope 读取。
红线:绝不写 viltrox_fit_score / rule_v0(表 CHECK 兜底);成员只看自己的 action;
LLM/写库动作 requires_approval=TRUE;payload 严禁 secret。
"""
