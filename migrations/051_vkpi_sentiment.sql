-- migrations/051_vkpi_sentiment.sql
-- P1.4: Multi-dimensional sentiment analysis

CREATE TABLE IF NOT EXISTS vkpi_sentiment_results (
  id BIGSERIAL PRIMARY KEY,
  comment_id BIGINT NOT NULL,
  
  -- 3 维结果
  sentiment VARCHAR(20),
  sentiment_confidence NUMERIC(3,2),
  
  emotion VARCHAR(20),
  emotion_confidence NUMERIC(3,2),
  
  brand_attitude VARCHAR(20),
  brand_attitude_confidence NUMERIC(3,2),
  
  -- 元数据
  llm_provider VARCHAR(50),
  llm_model VARCHAR(100),
  prompt_version VARCHAR(20) NOT NULL,
  language_detected VARCHAR(10),
  
  -- 成本追踪
  input_tokens INT DEFAULT 0,
  output_tokens INT DEFAULT 0,
  cost_cents INT DEFAULT 0,
  
  -- 时间
  analyzed_at TIMESTAMPTZ DEFAULT NOW(),
  
  CONSTRAINT vkpi_sentiment_uniq UNIQUE (comment_id, prompt_version)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_sentiment_comment 
  ON vkpi_sentiment_results(comment_id);

CREATE INDEX IF NOT EXISTS idx_vkpi_sentiment_brand 
  ON vkpi_sentiment_results(brand_attitude, analyzed_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_sentiment_negative 
  ON vkpi_sentiment_results(analyzed_at DESC) 
  WHERE sentiment = 'negative';

CREATE INDEX IF NOT EXISTS idx_vkpi_sentiment_hostile 
  ON vkpi_sentiment_results(analyzed_at DESC) 
  WHERE brand_attitude = 'hostile';

-- 关联回 vkpi_comments 的 sentiment_id (P1.3 已建)
-- comments_collector.py 在 P1.4 里加触发逻辑
