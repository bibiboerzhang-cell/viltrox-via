-- 111(2026-06-12,0号 RealER 裁定选项B):影子列三段式 up——
-- real_er=近10条 evidence 实算 ER(Σ(likes+comments)/Σviews);rule_v0 与
-- viltrox_fit_score 全程冻结,本列仅对照展示,绝不进评分。
-- down:ALTER TABLE vkpi_kol_pool DROP COLUMN real_er, DROP COLUMN real_er_sample_n,
--       DROP COLUMN real_er_computed_at, DROP COLUMN real_er_method;
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS real_er NUMERIC(8,4);
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS real_er_sample_n INTEGER;
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS real_er_computed_at TIMESTAMPTZ;
ALTER TABLE vkpi_kol_pool ADD COLUMN IF NOT EXISTS real_er_method TEXT;
