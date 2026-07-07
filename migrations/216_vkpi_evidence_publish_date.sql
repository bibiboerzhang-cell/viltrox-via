-- 216_vkpi_evidence_publish_date.sql — evidence 发布日期转正列(F1 算法修复包 · 件⑥)。
-- 背景:brand_pulse / industry_benchmark 等读端一直用
--   COALESCE(posted_at, CAST(to_jsonb(e.*)->>'publish_date' AS DATE)) 逐行挖 jsonb 取发布日期,
--   全表逐行序列化、永远走不了索引。本迁移把该口径转正为真列 published_at_norm TIMESTAMPTZ:
--   posted_at(DATE)优先、publish_date(TIMESTAMPTZ)兜底,一次性回填既有行并建索引。
-- 新行不强制写该列:读端(brand_pulse)保底 COALESCE(published_at_norm, posted_at, publish_date),
--   列为空自动落回旧路径,绝不漏行。
-- publish_date 是后加列,跨环境存在与否有漂移:回填走 DO 块先查 information_schema 再执行,防炸。
-- additive、幂等(IF NOT EXISTS,回填只补 NULL 行);注释零 ASCII 问号、零百分号
--   (避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯日期整备列,绝不触 viltrox_fit_score、不碰 rule_v0 打分逻辑。
-- 回滚见 216_vkpi_evidence_publish_date_down.sql(DROP COLUMN/INDEX IF EXISTS,旧路径列原样保留)。
--
-- 字段:
--   published_at_norm TIMESTAMPTZ —— 发布日期归一列;回填口径 COALESCE(posted_at, publish_date)。
BEGIN;

ALTER TABLE vkpi_kol_video_evidence
    ADD COLUMN IF NOT EXISTS published_at_norm TIMESTAMPTZ;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'vkpi_kol_video_evidence'
          AND column_name = 'publish_date'
    ) THEN
        EXECUTE 'UPDATE vkpi_kol_video_evidence '
             || 'SET published_at_norm = COALESCE(CAST(posted_at AS TIMESTAMPTZ), publish_date) '
             || 'WHERE published_at_norm IS NULL '
             || 'AND (posted_at IS NOT NULL OR publish_date IS NOT NULL)';
    ELSE
        EXECUTE 'UPDATE vkpi_kol_video_evidence '
             || 'SET published_at_norm = CAST(posted_at AS TIMESTAMPTZ) '
             || 'WHERE published_at_norm IS NULL AND posted_at IS NOT NULL';
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_vkpi_evidence_published_at_norm
    ON vkpi_kol_video_evidence (published_at_norm DESC);

COMMENT ON COLUMN vkpi_kol_video_evidence.published_at_norm IS
  'F1 件⑥ 发布日期转正列: 回填口径 COALESCE(posted_at, publish_date); 读端保底 COALESCE 落回旧路径; 不参与 viltrox_fit_score。';

COMMIT;
