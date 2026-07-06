-- 210_vkpi_reply_queue.sql — 评论区销售员 ReplyQueue v0 半自动待办表(件3)。
-- 背景:从 vkpi_comments 里筛出购买意向评论(兼容性问句/求购/型号问询/价格哪里买等多语种),
-- 落入本表做人工复核队列;draft_reply 走 RAG over 369 SKU + llm_gateway 生成品牌口吻草稿。
-- v0 铁律:不自动发帖,只到 drafted;人工一键复制后手动回,再 mark replied/dismissed。
-- 幂等:comment_external_id 唯一约束防重复筛入队(同一条评论只入队一次)。
-- kol_pool_id 口径:来源评论 vkpi_comments.account_id(compat 视图里等价 kol_pool_id),
--   仅做归属线索,可为空,不做外键强约束(避免历史脏数据 apply 失败)。
-- additive、幂等(IF NOT EXISTS);注释零 ASCII 问号(避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯待办队列表,绝不触 viltrox_fit_score、不碰 rule_v0 打分逻辑。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_reply_queue (
    id BIGSERIAL PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT '',
    kol_pool_id BIGINT,
    comment_external_id TEXT NOT NULL,
    comment_text TEXT DEFAULT '',
    intent_tag TEXT DEFAULT '',
    lang TEXT DEFAULT '',
    draft_reply TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT vkpi_reply_queue_comment_uniq UNIQUE (comment_external_id)
);

-- status 取值:pending(已筛入队待起草) / drafted(草稿已生成待人工回) / replied(已回) / dismissed(忽略)。
CREATE INDEX IF NOT EXISTS idx_vkpi_reply_queue_status
    ON vkpi_reply_queue(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_reply_queue_platform
    ON vkpi_reply_queue(platform, status);
CREATE INDEX IF NOT EXISTS idx_vkpi_reply_queue_kol
    ON vkpi_reply_queue(kol_pool_id);
COMMIT;
