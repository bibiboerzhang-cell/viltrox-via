-- 镜头出镜证据(派生表):从 final_v1 深析缓存散文里抽出的 Viltrox 产品提及,
-- 经 vkpi_products 目录归一(sku / family / unresolved),供 MY KOL「镜头出镜」
-- 模块与 KOL 详情「用过的镜头」纯读聚合。写入方只有回填脚本
-- scripts/ops/backfill_lens_evidence.py(幂等,按 cache_id + mention_norm 去重)。
-- 本表零 LLM、零 provider,绝不触 viltrox_fit_score / rule_v0。

CREATE TABLE IF NOT EXISTS vkpi_kol_lens_evidence (
    id BIGSERIAL PRIMARY KEY,
    cache_id BIGINT NOT NULL
        REFERENCES vkpi_analysis_cache(id) ON DELETE CASCADE,
    evidence_id BIGINT
        REFERENCES vkpi_kol_video_evidence(id) ON DELETE CASCADE,
    kol_pool_id BIGINT
        REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
    mention_text TEXT NOT NULL,
    mention_norm TEXT NOT NULL,
    resolution TEXT NOT NULL,
    product_sku TEXT,
    lens_key TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    category_main TEXT NOT NULL DEFAULT '',
    candidate_skus JSONB NOT NULL DEFAULT '[]'::jsonb,
    modalities JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    mention_count INTEGER NOT NULL DEFAULT 1,
    extractor_version TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_vkpi_kol_lens_evidence_cache_mention UNIQUE (cache_id, mention_norm),
    CONSTRAINT chk_vkpi_kol_lens_evidence_resolution
        CHECK (resolution IN ('sku', 'family', 'unresolved')),
    CONSTRAINT chk_vkpi_kol_lens_evidence_mention_count CHECK (mention_count >= 1)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_lens_evidence_kol
    ON vkpi_kol_lens_evidence(kol_pool_id, lens_key);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_lens_evidence_lens
    ON vkpi_kol_lens_evidence(lens_key, resolution);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_lens_evidence_sku
    ON vkpi_kol_lens_evidence(product_sku);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_lens_evidence_evidence
    ON vkpi_kol_lens_evidence(evidence_id);

-- 扫描账本:每条 final_v1 缓存扫过一次就记一行(含零提及的),回填脚本据此增量
-- (缓存 updated_at 变了或抽取器版本升级才重扫),聚合端点据此给出诚实覆盖率。
CREATE TABLE IF NOT EXISTS vkpi_kol_lens_evidence_scan (
    cache_id BIGINT PRIMARY KEY
        REFERENCES vkpi_analysis_cache(id) ON DELETE CASCADE,
    evidence_id BIGINT
        REFERENCES vkpi_kol_video_evidence(id) ON DELETE CASCADE,
    kol_pool_id BIGINT
        REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
    extractor_version TEXT NOT NULL DEFAULT '',
    cache_updated_at TIMESTAMPTZ,
    mention_rows INTEGER NOT NULL DEFAULT 0,
    scan_status TEXT NOT NULL DEFAULT 'scanned',
    scanned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_vkpi_kol_lens_evidence_scan_status
        CHECK (scan_status IN ('scanned', 'no_evidence', 'empty_result'))
);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_lens_evidence_scan_kol
    ON vkpi_kol_lens_evidence_scan(kol_pool_id);

COMMENT ON TABLE vkpi_kol_lens_evidence IS
    'Viltrox product mentions extracted from final_v1 analysis prose and resolved against vkpi_products; derived, rebuildable';
COMMENT ON TABLE vkpi_kol_lens_evidence_scan IS
    'Per-cache scan ledger for lens evidence extraction (coverage and incremental backfill)';
