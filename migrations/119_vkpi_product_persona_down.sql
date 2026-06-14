-- 119 down:产品 persona 知识库回滚。
-- DROP TABLE 仅删 LLM 生成的 persona 派生数据(可由 build_product_personas.py 幂等重建,无不可逆损失);
-- 不触碰 vkpi_products 本体、不触碰任何 fit/评分列。
DROP INDEX IF EXISTS idx_vkpi_product_persona_generated;
DROP INDEX IF EXISTS idx_vkpi_product_persona_category;
DROP TABLE IF EXISTS vkpi_product_persona;
