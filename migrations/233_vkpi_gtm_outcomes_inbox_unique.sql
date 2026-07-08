-- 233_vkpi_gtm_outcomes_inbox_unique.sql — GTM 结果账并发双裁决防线(闭环波 L2 · 类E 并发竞态)。
-- 背景(修 market_brain/verdict_flow.record_verdict 双插竞态):inbox_id 分支先 SELECT 该 bet
--   有无既有结果行、无则 INSERT 一条 finalized 结果行,这段 select-then-insert 非原子。两个并发裁决
--   同一 bet(库里尚无 outcome 行)会双双读到零行、双双 INSERT,落两条互相矛盾的 finalized 行
--   (一条 validated 一条 failed),污染学习闭环账本。
-- 本迁移加一条按 action_inbox_id 的部分唯一索引(仅 action_inbox_id 非空时约束),
--   让「一个 bet 至多一条结果行」成为库级不变量:第二个并发 INSERT 撞唯一索引被挡,
--   域层配合 ON CONFLICT DO NOTHING 优雅退让(不落第二行、不抛裸库错、诚实回 already_decided)。
--   action_inbox_id 可空(非 bet 关联的结果行不受约束;Postgres 唯一索引对多个 NULL 互不冲突)。
-- additive、幂等(CREATE UNIQUE INDEX IF NOT EXISTS);既有数据已核无重复 action_inbox_id,apply 不阻塞。
-- 注释零 ASCII 疑问号、零 percent 字面量(避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯结果账本加固,零触 viltrox_fit_score、零碰 rule_v0 打分。回滚见 233_vkpi_gtm_outcomes_inbox_unique_down.sql。
BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_gtm_outcomes_inbox
    ON vkpi_gtm_outcomes (action_inbox_id)
    WHERE action_inbox_id IS NOT NULL;

COMMENT ON INDEX uq_vkpi_gtm_outcomes_inbox IS
  '一个 bet(action_inbox_id 非空)至多一条 GTM 结果行(类E 并发双裁决防线): record_verdict 的 select-then-insert 竞态由本部分唯一索引兜底,域层 ON CONFLICT DO NOTHING 优雅退让,确保并发下恰一条 finalized 结果行。';

COMMIT;
