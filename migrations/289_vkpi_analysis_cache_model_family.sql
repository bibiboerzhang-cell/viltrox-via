-- 289: vkpi_analysis_cache 模型键(优化波 B·C5)。
-- 目的:同一条视频的分析结果要能按「提示版本 + 模型家族」分桶对账(3.6-flash 与 2.5-flash
-- 的 final_v1 不可混评;提示契约升级后旧行要能被识别出来重跑)。只加两列并回填,
-- 唯一键 (target_type, target_id, derive_method) 不动,避免同一目标重复分析。
-- model_family 由 model 列前缀派生:gemini-3.6-flash 归 gemini-3.6,gemini-2.5-flash 归
-- gemini-2.5,gpt-5.5-xxx 归 gpt-5.5,claude-opus-4-1 归 claude-opus-4,其余取首段。
-- 红线:零触 viltrox_fit_score / rule_v0;注释里禁用 ASCII 问号与百分号。

ALTER TABLE vkpi_analysis_cache
    ADD COLUMN IF NOT EXISTS prompt_version TEXT NULL;

ALTER TABLE vkpi_analysis_cache
    ADD COLUMN IF NOT EXISTS model_family TEXT NULL;

UPDATE vkpi_analysis_cache
SET model_family = COALESCE(
    substring(lower(model) from '(gemini-[0-9]+(\.[0-9]+)*)'),
    substring(lower(model) from '(gpt-[0-9]+(\.[0-9]+)*)'),
    substring(lower(model) from '(claude-[a-z]+-[0-9]+)'),
    NULLIF(split_part(lower(model), '-', 1), '')
)
WHERE model_family IS NULL
  AND model IS NOT NULL
  AND model <> '';

-- 历史 final_v1 行统一打上当前提示契约(与 apify_jobs_video_context.FINAL_V1_PROMPT_CONTRACT 同字面)。
UPDATE vkpi_analysis_cache
SET prompt_version = 'final_v1_pure_video_evidence_v2'
WHERE prompt_version IS NULL
  AND derive_method IN ('video_analysis_final_v1', 'video_analysis_final_v1_keyframe_qa');

CREATE INDEX IF NOT EXISTS idx_vkpi_analysis_cache_model_family
    ON vkpi_analysis_cache (model_family, derive_method);

COMMENT ON COLUMN vkpi_analysis_cache.prompt_version IS
  'Prompt contract that produced this row (final_v1 rows carry FINAL_V1_PROMPT_CONTRACT); NULL for legacy or non-prompt writers.';

COMMENT ON COLUMN vkpi_analysis_cache.model_family IS
  'Model family derived from the model column prefix (gemini-3.6, gemini-2.5, gpt-5.5, claude-opus-4); used for per-family reconciliation and re-run selection.';
