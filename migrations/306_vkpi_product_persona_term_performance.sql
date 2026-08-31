-- 306: persona 知识库回填 —— vkpi_product_persona 加 term_performance_json 列。
-- 跃迁二真杠杆:检索流量九成以上走 persona 路径,persona 行里却没有任何
-- 「这个 SKU 用什么词搜到过人、哪些词已经抓干」的供给侧知识;本列把它补上。
--
-- 来源:vkpi_kol_search_sessions.result_summary_json 里 discovery_term_evidence 键
--   (写端 backend/app/domains/kol/profile_discovery_evidence.py),经
--   backend/app/domains/kol/discovery_term_yield.py 的 per_sku_term_performance
--   按 product_anchor.sku 匹配该 SKU 相关搜索会话后纯 SQL 聚合而成。
-- 口径:载荷 schema=persona_term_performance_v1,含 top_terms(高产词至多 5 条,
--   按合格新人数排序)+ exhausted_terms(已抓干词清单)+ totals;样本不足时
--   low_sample=true 诚实标出,消费端不得把荒样本当结论。配额为零的词产出率记
--   null,「没烧配额」不是「零产出」。
-- 写点:仅 backend/scripts_local/build_product_personas.py 重放路径(--execute 的
--   upsert)顺带回填;无 scheduler、无触发器、零 LLM 参与;回填时刻即同行
--   generated_at。persona 正文各列与本列互不影响。
-- 允许 NULL:NULL = 该 SKU 尚未经历带词效证据的重放,不是空账、更不是零产出。
-- 红线:不含 fit 分、不参与 rule_v0 打分、绝不写 viltrox_fit_score。
ALTER TABLE vkpi_product_persona
  ADD COLUMN IF NOT EXISTS term_performance_json JSONB NULL;

COMMENT ON COLUMN vkpi_product_persona.term_performance_json IS
    'persona 词效知识回填(schema persona_term_performance_v1):该 SKU 相关搜索会话的高产词 top5 与已抓干词清单,discovery_term_yield.per_sku_term_performance 纯 SQL 聚合,仅 build_product_personas.py 重放路径写入;NULL means not yet backfilled, never zero yield;low_sample=true 时样本不足勿当结论;不含 fit 分,不参与 rule_v0。';
