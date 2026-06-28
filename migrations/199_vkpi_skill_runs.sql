-- 199_vkpi_skill_runs.sql — Skill Registry 运行账本(Marketing Brain 路线1·Skill Registry 做实)。
-- tool_registry 已声明 8 个工具的元数据,但没有 run/eval/feedback 落库。本表补上:
-- 每次 skill 运行落一行 —— 用了哪个 skill/version、什么 model/prompt_version、喂了什么 context、
-- 产出/成本/延迟,以及事后的人评分(human_score)、业务结果(business_result)、是否被采纳(accepted)。
-- 有了它才能统计 acceptance / cost / latency,做 eval 与 feedback 回路。
-- additive、幂等(IF NOT EXISTS);注释零 ASCII 问号(避 compat 占位符炸 apply 的陷阱)。
-- 红线:本表纯运行账本,绝不触 viltrox_fit_score(唯一合法写点 rule_v0 pool.py)。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_skill_runs (
    id                 BIGSERIAL   PRIMARY KEY,
    skill_name         TEXT        NOT NULL,
    skill_version      TEXT        NOT NULL DEFAULT 'v1',
    input_schema       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    model_used         TEXT,
    prompt_version     TEXT,
    retrieved_context  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    output             JSONB       NOT NULL DEFAULT '{}'::jsonb,
    cost_cents         INTEGER     NOT NULL DEFAULT 0,
    latency_ms         INTEGER     NOT NULL DEFAULT 0,
    human_score        DOUBLE PRECISION,
    business_result    TEXT,
    accepted           BOOLEAN,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_skill_runs_name ON vkpi_skill_runs(skill_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_skill_runs_accepted ON vkpi_skill_runs(skill_name, accepted);
COMMIT;
