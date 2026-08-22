-- 288: 推荐特征快照 + 影子重排序模型账本(学习闭环 W-L2)。
-- 目的:把「推荐时刻的特征向量 + 基础分 + A/B arm + 影子调整量」冻结成一行,
-- 之后由结果回流(vkpi_recommendation_outcomes)给出标签,周拟合作业只读这张表
-- 产出 rerank_adjustment(影子重排序微调)与 <=3 条理由码。
-- 红线:本迁移零触 viltrox_fit_score / rule_v0 评分公式;快照是派生表,可由引擎重放重建。
-- 注意:注释里禁用 ASCII 问号,避免 compat 适配器把它当占位符。

CREATE TABLE IF NOT EXISTS vkpi_recommendation_feature_snapshot (
    id BIGSERIAL PRIMARY KEY,
    recommendation_id BIGINT NOT NULL
        REFERENCES vkpi_kol_recommendations(id) ON DELETE CASCADE,
    run_id BIGINT,
    kol_pool_id BIGINT
        REFERENCES vkpi_kol_pool(id) ON DELETE SET NULL,
    launch_id BIGINT,
    staff_id BIGINT,
    engine TEXT NOT NULL DEFAULT '',
    arm TEXT NOT NULL DEFAULT 'off',
    feature_keys_version TEXT NOT NULL DEFAULT 'rerank_features_v1',
    feature_vector JSONB NOT NULL DEFAULT '{}'::jsonb,
    base_score NUMERIC(10,3),
    rerank_adjustment NUMERIC(10,3) NOT NULL DEFAULT 0,
    rerank_applied BOOLEAN NOT NULL DEFAULT FALSE,
    rerank_reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    rerank_model_version TEXT NOT NULL DEFAULT '',
    outcome_label SMALLINT,
    outcome_nodes JSONB NOT NULL DEFAULT '[]'::jsonb,
    outcome_labeled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_vkpi_reco_feature_snapshot_rec UNIQUE (recommendation_id),
    CONSTRAINT chk_vkpi_reco_feature_snapshot_arm
        CHECK (arm IN ('off', 'control', 'treatment')),
    CONSTRAINT chk_vkpi_reco_feature_snapshot_label
        CHECK (outcome_label IS NULL OR outcome_label IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_vkpi_reco_feature_snapshot_label
    ON vkpi_recommendation_feature_snapshot(outcome_label, created_at);

CREATE INDEX IF NOT EXISTS idx_vkpi_reco_feature_snapshot_pool
    ON vkpi_recommendation_feature_snapshot(kol_pool_id);

CREATE INDEX IF NOT EXISTS idx_vkpi_reco_feature_snapshot_arm
    ON vkpi_recommendation_feature_snapshot(arm, created_at);

-- 周拟合账本:每次拟合落一行(含「样本不足未激活」的诚实记录);
-- 主引擎只读 activated=TRUE 的最新一行产出影子调整量。
CREATE TABLE IF NOT EXISTS vkpi_recommendation_rerank_model (
    id BIGSERIAL PRIMARY KEY,
    model_version TEXT NOT NULL UNIQUE,
    feature_keys_version TEXT NOT NULL DEFAULT 'rerank_features_v1',
    fitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sample_count INTEGER NOT NULL DEFAULT 0,
    positive_count INTEGER NOT NULL DEFAULT 0,
    negative_count INTEGER NOT NULL DEFAULT 0,
    activated BOOLEAN NOT NULL DEFAULT FALSE,
    activation_rule TEXT NOT NULL DEFAULT '',
    weights JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_reco_rerank_model_active
    ON vkpi_recommendation_rerank_model(activated, fitted_at DESC);

COMMENT ON TABLE vkpi_recommendation_feature_snapshot IS
    'Frozen feature vector per recommendation at recommendation time, with A/B arm, shadow rerank adjustment and outcome label; derived, rebuildable';
COMMENT ON TABLE vkpi_recommendation_rerank_model IS
    'Weekly logistic fit ledger for shadow rerank adjustment (activated only when samples are sufficient); never touches viltrox_fit_score';
