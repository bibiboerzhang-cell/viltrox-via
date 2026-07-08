-- 227_vkpi_reply_queue_claim.sql — 评论区销售员 ReplyQueue 认领与唯一约束加固(件3 · 车道2)。
-- 背景(修 reply_queue 5 缺陷里的表结构侧):
--   缺陷① 队列全局共享无认领 —— 8 员工同时起草会撞车重复烧 LLM。本迁移加 claimed_by / claimed_at
--          两列作占用锚:域层 draft_reply 生成前原子认领,list_queue 按认领可见性过滤,避免撞车。
--   缺陷⑥ 旧唯一约束 UNIQUE(comment_external_id) 比源表 vkpi_comments 的
--          UNIQUE(platform, external_comment_id) 更强:跨平台同一 external_comment_id 会被误判重复丢评论。
--          本迁移改成 UNIQUE(platform, comment_external_id),与源表口径对齐(先删旧单列约束,再补复合约束)。
--   缺陷⑤ 历史入队把 vkpi_comments.account_id 直接当 kol_pool_id 写,大量值并不在 vkpi_kol_pool
--          (无效归属线索)。本迁移把这些无效值一次性置 NULL;域层 screen_intents 之后只在
--          account_id 真实存在于 vkpi_kol_pool 时才写 kol_pool_id,防再生。
-- additive、幂等:列用 IF NOT EXISTS;约束改动用 DROP IF EXISTS + DO 块 guard;数据清洗只把无效归属线索置空。
-- 注释零 ASCII 问号、零百分号(避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯待办队列表加固,绝不触 viltrox_fit_score、不碰 rule_v0 打分。
-- 回滚见 227_vkpi_reply_queue_claim_down.sql。
BEGIN;

ALTER TABLE vkpi_reply_queue ADD COLUMN IF NOT EXISTS claimed_by BIGINT;
ALTER TABLE vkpi_reply_queue ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;

-- 唯一约束与源表对齐:先删旧的单列约束,再补 (platform, comment_external_id) 复合约束(DO 块 guard 幂等)。
ALTER TABLE vkpi_reply_queue DROP CONSTRAINT IF EXISTS vkpi_reply_queue_comment_uniq;
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'vkpi_reply_queue_platform_comment_uniq'
    ) THEN
        ALTER TABLE vkpi_reply_queue
            ADD CONSTRAINT vkpi_reply_queue_platform_comment_uniq UNIQUE (platform, comment_external_id);
    END IF;
END
$do$;

-- 缺陷⑤ 历史脏数据清洗:把不在 vkpi_kol_pool 的 kol_pool_id 置 NULL(无效归属线索,不是真 KOL)。
UPDATE vkpi_reply_queue
   SET kol_pool_id = NULL
 WHERE kol_pool_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM vkpi_kol_pool kp WHERE kp.id = vkpi_reply_queue.kol_pool_id);

-- 认领可见性过滤按 claimed_by 命中,补一条索引。
CREATE INDEX IF NOT EXISTS idx_vkpi_reply_queue_claimed
    ON vkpi_reply_queue(claimed_by);

COMMENT ON COLUMN vkpi_reply_queue.claimed_by IS
  '认领人 staff.id(件3 车道2 缺陷①): draft_reply 生成前原子占用防 8 员工撞车双烧 LLM; NULL=未认领(池)。';
COMMENT ON COLUMN vkpi_reply_queue.claimed_at IS
  '认领时刻(UTC); 仅首次认领落一次(COALESCE 保留原值)。';

COMMIT;
