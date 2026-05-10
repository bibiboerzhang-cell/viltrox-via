-- migrations/050_vkpi_comments.sql
-- P1.3: Comments collection across 5 platforms

-- ─── vkpi_comments ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS vkpi_comments (
  id BIGSERIAL PRIMARY KEY,
  
  -- 来源信息
  account_id BIGINT,
  post_id BIGINT,
  post_table VARCHAR(50) DEFAULT 'industry_posts',
  external_post_id VARCHAR(200),
  platform VARCHAR(50) NOT NULL,
  external_comment_id VARCHAR(200) NOT NULL,
  
  -- 内容
  comment_text TEXT,
  language_detected VARCHAR(10),
  
  -- 作者
  author_handle VARCHAR(200),
  author_id VARCHAR(200),
  is_op BOOLEAN DEFAULT FALSE,
  
  -- 嵌套
  parent_comment_id VARCHAR(200),
  depth SMALLINT DEFAULT 0,
  
  -- 互动
  likes_count INT DEFAULT 0,
  reply_count INT DEFAULT 0,
  
  -- 时间
  created_at TIMESTAMPTZ,
  fetched_at TIMESTAMPTZ DEFAULT NOW(),
  
  -- 关联 (P1.4/P1.5)
  sentiment_id BIGINT,
  pillar_id INT,
  
  -- 原始
  raw_data_json TEXT,
  
  -- 唯一约束 (防重复抓)
  CONSTRAINT vkpi_comments_external_uniq UNIQUE (platform, external_comment_id)
);

-- ─── 索引 ───────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_vkpi_comments_account 
  ON vkpi_comments(account_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_comments_post 
  ON vkpi_comments(post_id, post_table);

CREATE INDEX IF NOT EXISTS idx_vkpi_comments_platform 
  ON vkpi_comments(platform);

CREATE INDEX IF NOT EXISTS idx_vkpi_comments_external_post
  ON vkpi_comments(platform, external_post_id);

-- 部分索引: P1.4 sentiment 队列(只看待处理)
CREATE INDEX IF NOT EXISTS idx_vkpi_comments_sentiment_pending
  ON vkpi_comments(id) WHERE sentiment_id IS NULL;

-- 部分索引: P1.5 pillar 队列
CREATE INDEX IF NOT EXISTS idx_vkpi_comments_pillar_pending
  ON vkpi_comments(id) WHERE pillar_id IS NULL;

-- ─── 评论抓取统计表 (轻量,便于报表) ─────────────────────
CREATE TABLE IF NOT EXISTS vkpi_comments_collection_runs (
  id BIGSERIAL PRIMARY KEY,
  post_id BIGINT,
  post_table VARCHAR(50),
  platform VARCHAR(50) NOT NULL,
  status VARCHAR(20) NOT NULL,        -- ok / fail / skip / not_configured
  fetched_count INT DEFAULT 0,
  new_count INT DEFAULT 0,
  duplicate_count INT DEFAULT 0,
  error_message TEXT,
  cost_cents INT DEFAULT 0,
  
  staff_id INT,
  triggered_by VARCHAR(50),           -- 'manual' / 'scheduled' / 'backfill'
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_comments_runs_platform_time
  ON vkpi_comments_collection_runs(platform, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_comments_runs_post
  ON vkpi_comments_collection_runs(post_id, post_table);
