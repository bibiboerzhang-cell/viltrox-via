-- Manual product/SKU associations for tracked MY KOL video evidence.
--
-- One video may cover more than one product, so project_id on the evidence
-- row is deliberately not reused as product truth.
-- Integration prerequisite: mainline migration 283 owns the content-metric
-- snapshot ledger consumed by the refresh worker.  This DDL has no snapshot
-- FK, but release ordering must still apply 283 before this slice.

CREATE TABLE IF NOT EXISTS vkpi_kol_video_product_links (
    id BIGSERIAL PRIMARY KEY,
    evidence_id BIGINT NOT NULL
        REFERENCES vkpi_kol_video_evidence(id) ON DELETE CASCADE,
    product_sku TEXT NOT NULL
        REFERENCES vkpi_products(sku) ON UPDATE CASCADE ON DELETE RESTRICT,
    relation_type TEXT NOT NULL DEFAULT 'manual',
    source TEXT NOT NULL DEFAULT 'my_kol_video_tracking',
    confidence NUMERIC(4,3) NOT NULL DEFAULT 1.000,
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_vkpi_kol_video_product_links_relation_type
        CHECK (relation_type IN ('manual', 'detected', 'confirmed')),
    CONSTRAINT chk_vkpi_kol_video_product_links_confidence
        CHECK (confidence >= 0 AND confidence <= 1),
    CONSTRAINT uq_vkpi_kol_video_product_links_identity
        UNIQUE (evidence_id, product_sku, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_video_product_links_sku
    ON vkpi_kol_video_product_links(product_sku, evidence_id);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_video_product_links_evidence
    ON vkpi_kol_video_product_links(evidence_id, id);

COMMENT ON TABLE vkpi_kol_video_product_links IS
    'Auditable many-to-many product links for tracked KOL video evidence';
