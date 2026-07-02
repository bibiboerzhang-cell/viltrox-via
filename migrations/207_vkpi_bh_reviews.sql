-- 207_vkpi_bh_reviews.sql — B&H 产品用户评论表(竞品口碑数据源)。
-- 背景:付费 search actor(powerai/bhphotovideo-product-search-scraper)抓的产品列表
-- 与库内数据完全重复零增量,用户令 2026-07-02 停掉;换同家 reviews actor
-- (powerai/bhphotovideo-product-reviews-scraper)抓产品页用户评论,喂竞品口碑与市场洞察。
-- 防重键:product_url + author + review_date + body_prefix(正文前 80 字符,由写入层截取);
-- 用普通列而非表达式索引,兼容 compat 层 ON CONFLICT 列推断。
-- additive、幂等(IF NOT EXISTS);注释零 ASCII 问号(避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯数据表,绝不触 viltrox_fit_score、不碰 rule_v0。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_bh_reviews (
    id BIGSERIAL PRIMARY KEY,
    product_url TEXT NOT NULL,
    product_name TEXT NOT NULL DEFAULT '',
    sku TEXT DEFAULT '',
    rating REAL,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    review_date TEXT NOT NULL DEFAULT '',
    body_prefix TEXT NOT NULL DEFAULT '',
    fetched_at TEXT,
    CONSTRAINT uq_vkpi_bh_reviews_dedupe UNIQUE (product_url, author, review_date, body_prefix)
);
CREATE INDEX IF NOT EXISTS idx_vkpi_bh_reviews_product_url ON vkpi_bh_reviews(product_url);
CREATE INDEX IF NOT EXISTS idx_vkpi_bh_reviews_fetched_at ON vkpi_bh_reviews(fetched_at);
COMMIT;
