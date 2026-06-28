-- 196_vkpi_competitor_signal_expiry.sql — Market Brain 活体化:竞品信号有效期治理。
-- 信号不该永久"新鲜":加 expires_at;到期由 mark_expired 置 review_status='expired',
-- 让市场大脑日报只采纳未过期信号(避免 35 天 stale 当新鲜)。
-- additive、幂等。注释零 ASCII 问号(避 compat 占位符陷阱)。红线:纯信号侧,零触 viltrox_fit_score。
BEGIN;
ALTER TABLE vkpi_competitor_signals ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
-- 既有行回填默认有效期 = created_at + 30 天(单调,只填空)
UPDATE vkpi_competitor_signals
   SET expires_at = created_at + INTERVAL '30 days'
 WHERE expires_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_competitor_signals_expires ON vkpi_competitor_signals(expires_at);
COMMIT;
