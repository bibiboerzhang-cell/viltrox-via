-- 290_vkpi_recommendation_feedback_search.sql — 学习闭环 L 车道:搜索页反馈写口 + 三条零 LLM cron 闸门种子。
-- 背景:人工操作 = 训练信号。发现墙 / KOL 详情的「有用 / 没用 + 拒绝原因」以前无处落;
--   vkpi_recommendation_feedback.recommendation_id NOT NULL 导致没有推荐行时反馈只能丢弃。
-- 本迁移(全部 additive、幂等;运行器拥有事务,禁止 BEGIN/COMMIT;注释零 ASCII 问号、零百分号):
--   1) recommendation_id 放宽为可空(FK 保留;NULL 不参与 FK 校验),既有读口全部以 int(x or 0) 宽容读回;
--   2) 新增三列:source(写口来源闭集:discovery_wall / kol_detail,空串=旧行)、kol_pool_id(无推荐时的归属键)、
--      reason(拒绝原因闭集:not_relevant / wrong_region / too_small / brand_official / duplicate / other,空串=无);
--   3) 无 recommendation_id 时以 (source, kol_pool_id, COALESCE(staff,0)) 部分唯一索引去重(同人同源同 KOL 只留一行,
--      改判走 UPDATE);有 recommendation_id 的旧口径仍按 recommendation_id x feedback_type 去重(应用层);
--   4) scheduler_tasks 注册三条闸门(默认 enabled=FALSE,运营在 Ops 页显式开):
--      vkpi_forecast_batch_issue(预测批量发射,每日)/ vkpi_weekly_offline_eval(每周一离线评估链)/
--      vkpi_anomaly_sentinel(S 车道哨兵,每 30 分钟)。
-- 红线:零触 viltrox_fit_score、不碰 rule_v0;回滚见 290_vkpi_recommendation_feedback_search_down.sql。

ALTER TABLE vkpi_recommendation_feedback
    ALTER COLUMN recommendation_id DROP NOT NULL;

ALTER TABLE vkpi_recommendation_feedback
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT '';

ALTER TABLE vkpi_recommendation_feedback
    ADD COLUMN IF NOT EXISTS kol_pool_id BIGINT;

ALTER TABLE vkpi_recommendation_feedback
    ADD COLUMN IF NOT EXISTS reason TEXT NOT NULL DEFAULT '';

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_reco_feedback_search_dedupe
    ON vkpi_recommendation_feedback (source, kol_pool_id, COALESCE(created_by_staff_id, 0))
    WHERE source <> '' AND kol_pool_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_vkpi_reco_feedback_pool
    ON vkpi_recommendation_feedback (kol_pool_id, created_at DESC)
    WHERE kol_pool_id IS NOT NULL;

COMMENT ON COLUMN vkpi_recommendation_feedback.source IS
    'Write-port source for search feedback (discovery_wall / kol_detail); empty for legacy recommendation-bound rows';
COMMENT ON COLUMN vkpi_recommendation_feedback.kol_pool_id IS
    'KOL pool id the feedback is about when no recommendation row exists (search feedback dedupe key)';
COMMENT ON COLUMN vkpi_recommendation_feedback.reason IS
    'Closed-set reject reason: not_relevant / wrong_region / too_small / brand_official / duplicate / other; empty = none';

INSERT INTO scheduler_tasks (task_key, label, enabled, risk_level) VALUES
    ('vkpi_forecast_batch_issue', '预测批量发射(每日,MY KOL x 活跃上市 SKU,零 LLM,按 KOL/SKU/日幂等)', FALSE, 'low'),
    ('vkpi_weekly_offline_eval', '离线评估周链(每周一 06:30,core_v1 + 预测回测 + 重排 holdout + 记分卡,纯读断言)', FALSE, 'low'),
    ('vkpi_anomaly_sentinel', '异常哨兵(每 30 分钟,零 LLM,写 vkpi_alerts 按 alert_key 幂等)', FALSE, 'low')
ON CONFLICT (task_key) DO NOTHING;
