-- 119:产品 persona / 推广方向知识库(地基A:LLM 深度学习每个产品的推广方向)。
-- 一 SKU 一行,1:1 镜像 vkpi_products.sku。本表只存「产品是什么 + 理想创作者人群 + 推广角度」的
-- LLM 生成结构化文本/标签;绝不存 fit 分、不存 KOL↔产品打分(viltrox_fit_score 唯一写点仍是 pool.py enrich_item)。
-- 离线批跑 backend/scripts_local/build_product_personas.py 负责 upsert;搜索 planner 经 helper 只读取本表。
-- 红线:LLM 只产产品 persona 文本/标签;rule_v0 打分公式冻结,本表零参与评分。down 见 119_vkpi_product_persona_down.sql
CREATE TABLE IF NOT EXISTS vkpi_product_persona (
    product_sku TEXT PRIMARY KEY REFERENCES vkpi_products(sku) ON DELETE CASCADE,
    category TEXT,
    what_is TEXT,
    key_specs_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ideal_persona TEXT,
    ideal_creator_types_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    verticals_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    promotion_angles_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    avoid_types_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    model TEXT,
    source TEXT NOT NULL DEFAULT 'llm_persona_v1',
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_product_persona_category
    ON vkpi_product_persona(category);
CREATE INDEX IF NOT EXISTS idx_vkpi_product_persona_generated
    ON vkpi_product_persona(generated_at DESC);

COMMENT ON TABLE vkpi_product_persona IS
    '地基A 产品知识库:每个 SKU 的「产品是什么 / 理想创作者人群 / 推广方向」LLM 生成 persona;不含 fit 分,不参与 rule_v0 打分。';
COMMENT ON COLUMN vkpi_product_persona.key_specs_json IS
    '注入的真实参数快照(焦段/光圈/卡口/价位/专业级别 等);来源 vkpi_products.specs_json + price_usd,非 LLM 杜撰。';
COMMENT ON COLUMN vkpi_product_persona.ideal_persona IS
    '理想创作者人群一段话(LLM 生成的推广方向文本),供搜索 planner 取作 SKU→深度产品分析的人群锚点。';
COMMENT ON COLUMN vkpi_product_persona.promotion_angles_json IS
    '推广方向/卖点角度数组(LLM 标签);服务曝光/市场,不是 KOL 适配打分。';
COMMENT ON COLUMN vkpi_product_persona.avoid_types_json IS
    '明确规避的错配创作者类型数组;仅作负向提示文本,不写入任何评分列。';
