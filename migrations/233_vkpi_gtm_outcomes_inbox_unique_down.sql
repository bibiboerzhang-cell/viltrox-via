-- 233 回滚:撤销 GTM 结果账 action_inbox_id 部分唯一索引(类E 并发双裁决防线)。
-- 注意:回滚后 record_verdict 并发双裁决回到仅靠域层 select-then-insert(非原子),
--   并发下可能重现单 bet 双 finalized 结果行;域层 ON CONFLICT DO NOTHING 无索引可撞时退化为普通 INSERT。
--   若回滚前已积累单 bet 多结果行,本 DROP 无碍(仅去索引,不动数据)。
BEGIN;

DROP INDEX IF EXISTS uq_vkpi_gtm_outcomes_inbox;

COMMIT;
