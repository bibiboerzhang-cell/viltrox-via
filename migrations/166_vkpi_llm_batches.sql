-- 166_vkpi_llm_batches.sql (Batch API 台账:异步批量 LLM 提交/回收状态机)
-- 用途:过夜批量任务(content_fit 刷新等)用 Anthropic Message Batches 异步提交——同模型
--       (claude-sonnet-4-6)、输出与同步一致,仅以延迟(SLA≤24h)换 50% 折扣。本表记每个批次的
--       provider batch_id / consumer(注册式 dispatch)/ 请求映射 / 状态;poller(job_llm_batch_poll)
--       轮询回收并交给 consumer 落各自域表。
-- additive/幂等(IF NOT EXISTS);与 KOL 评分域物理隔离:无 viltrox_fit_score / rule_v0 触点。

BEGIN;

CREATE TABLE IF NOT EXISTS vkpi_llm_batches (
    id             BIGSERIAL   PRIMARY KEY,
    batch_id       TEXT        NOT NULL UNIQUE,          -- provider 批次 id(msgbatch_...)
    provider       TEXT        NOT NULL DEFAULT 'anthropic',
    consumer       TEXT        NOT NULL,                 -- 注册表 key,如 'content_fit_v1'
    purpose        TEXT        NOT NULL DEFAULT '',      -- 成本 scope / tag
    status         TEXT        NOT NULL DEFAULT 'in_progress',  -- in_progress|collected|errored|expired
    request_count  INTEGER     NOT NULL DEFAULT 0,
    request_map    JSONB       NOT NULL DEFAULT '{}'::jsonb,    -- {custom_id: meta}(回收时拼信封用)
    result_summary JSONB,                                -- {written,failed,total,tokens,cost_usd}
    error          TEXT        NOT NULL DEFAULT '',
    submitted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    collected_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_vkpi_llm_batches_status
    ON vkpi_llm_batches(status, submitted_at);
CREATE INDEX IF NOT EXISTS idx_vkpi_llm_batches_consumer
    ON vkpi_llm_batches(consumer, status);

COMMENT ON TABLE vkpi_llm_batches IS
    'Batch API 台账:异步批量 LLM 提交/回收状态机(consumer 注册式 dispatch)。省 50% 非实时花费,输出与同步路径一致;仅用于「没人等」的过夜批量,绝不用于交互。';

COMMIT;
