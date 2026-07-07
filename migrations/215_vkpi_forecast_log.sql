-- 215_vkpi_forecast_log.sql — 预测流水台账表(B3 学习闭环通电 · 刀1)。
-- 背景:performance_forecast 每次出 ready 预测只回给前端不落库,L 轨道「预测 vs 实际」
--   四件套(对答案/带内率/校准/复盘)全部空转。本表是预测流水的唯一真源:
--   forecast_for_kol 出 ready 结果时 best-effort 追加一行(dry_run 可关,失败不影响返回),
--   30 天后由 learning.forecast_feedback.refresh_forecast_outcomes 回查该 KOL 窗口内
--   实际播放中位数填 actual_views/actual_at 并判 outcome,summary 出带内率。
-- outcome 取值:pending(待对答案) / hit_in_band(实际落在 p10..p90 带内) /
--   below(实际低于 p10) / above(实际高于 p90)。
-- context 取值:launchpad(发射台组装) / drawer(KOL 档案抽屉) / sim(模拟推演)。
-- kol_pool_id 口径:vkpi_kol_pool.id,仅做归属线索,不做外键强约束(避免历史脏数据 apply 失败)。
-- additive、幂等(IF NOT EXISTS);注释零 ASCII 问号、零百分号(避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯预测流水表,绝不触 viltrox_fit_score、不碰 rule_v0 打分逻辑。
-- 回滚见 215_vkpi_forecast_log_down.sql(DROP TABLE IF EXISTS)。
--
-- 字段:
--   id              BIGSERIAL 主键        —— 流水自增 id。
--   kol_pool_id     BIGINT NOT NULL       —— 被预测的 KOL(vkpi_kol_pool.id)。
--   sku             TEXT 缺省 ''          —— 预测携带的 SKU(无 SKU 调整时为空串)。
--   p10             BIGINT                —— 期望播放下沿(expected_views_p10,已含 SKU 系数)。
--   p50             BIGINT                —— 期望播放中位(expected_views_p50)。
--   p90             BIGINT                —— 期望播放上沿(expected_views_p90)。
--   engagement_rate DOUBLE PRECISION      —— 预测互动率(逐视频赞评除以播放的中位数)。
--   confidence      TEXT 缺省 ''          —— 预测置信度(low / medium / high)。
--   method          TEXT 缺省 ''          —— 预测方法(如 evidence_quantile_v1,可追溯)。
--   context         TEXT 缺省 'drawer'    —— 调用语境(launchpad / drawer / sim)。
--   created_at      TIMESTAMPTZ 缺省 NOW  —— 预测时刻(对答案窗口的起点)。
--   actual_views    BIGINT                —— 实际播放(回查窗口内新发视频播放中位数)。
--   actual_at       TIMESTAMPTZ           —— 对答案时刻。
--   outcome         TEXT 缺省 'pending'   —— 判定结果(pending / hit_in_band / below / above)。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_forecast_log (
    id              BIGSERIAL PRIMARY KEY,
    kol_pool_id     BIGINT NOT NULL,
    sku             TEXT NOT NULL DEFAULT '',
    p10             BIGINT,
    p50             BIGINT,
    p90             BIGINT,
    engagement_rate DOUBLE PRECISION,
    confidence      TEXT NOT NULL DEFAULT '',
    method          TEXT NOT NULL DEFAULT '',
    context         TEXT NOT NULL DEFAULT 'drawer',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actual_views    BIGINT,
    actual_at       TIMESTAMPTZ,
    outcome         TEXT NOT NULL DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS idx_vkpi_forecast_log_outcome_created
    ON vkpi_forecast_log (outcome, created_at);

CREATE INDEX IF NOT EXISTS idx_vkpi_forecast_log_kol
    ON vkpi_forecast_log (kol_pool_id, created_at DESC);

COMMENT ON TABLE vkpi_forecast_log IS
  'B3 预测流水台账: forecast_for_kol 每次 ready 预测追加一行; 30 天后回查实际播放判 outcome, 供带内率统计与校准; 零触 viltrox_fit_score。';
COMMIT;
