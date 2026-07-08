-- 227 回滚:撤销 ReplyQueue 认领列与复合唯一约束加固,恢复旧的单列唯一约束。
-- 注意:claimed_by / claimed_at 认领信息随列删除;被本迁移置 NULL 的历史 kol_pool_id 无法还原
--   (本就是无效归属线索,回滚不重建);域层在列缺失时不依赖认领,读写降级正常。
-- 恢复单列唯一约束更强:若回滚前已积累跨平台同 external_comment_id 评论,ADD CONSTRAINT 会失败,
--   这是诚实信号(说明确有跨平台重复,应保留复合约束),按需人工去重后再回滚。
BEGIN;

ALTER TABLE vkpi_reply_queue DROP CONSTRAINT IF EXISTS vkpi_reply_queue_platform_comment_uniq;
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'vkpi_reply_queue_comment_uniq'
    ) THEN
        ALTER TABLE vkpi_reply_queue
            ADD CONSTRAINT vkpi_reply_queue_comment_uniq UNIQUE (comment_external_id);
    END IF;
END
$do$;

DROP INDEX IF EXISTS idx_vkpi_reply_queue_claimed;
ALTER TABLE vkpi_reply_queue DROP COLUMN IF EXISTS claimed_at;
ALTER TABLE vkpi_reply_queue DROP COLUMN IF EXISTS claimed_by;

COMMIT;
