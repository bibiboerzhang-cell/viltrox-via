-- 309 down: 回滚 DSAR 公开申请通道的列 / 约束 / 索引。
-- 保守回滚:先把 do_not_contact 工单标记为 rejected(原 CHECK 不认该类型,否则重建约束会失败),
--   再把 request_type 改回 'rectification' 并在 note 留痕;公开来源的行保留(是合规留痕,不删)。
-- 不恢复任何已执行的删除 / 抑制(本就不可逆)。

UPDATE vkpi_dsar_requests
   SET note = CASE WHEN COALESCE(note, '') = '' THEN 'rolled_back_309:do_not_contact'
                   ELSE note || ' | rolled_back_309:do_not_contact' END,
       request_type = 'rectification',
       status = CASE WHEN status IN ('pending','approved','executing') THEN 'rejected' ELSE status END
 WHERE request_type = 'do_not_contact';

ALTER TABLE vkpi_dsar_requests DROP CONSTRAINT IF EXISTS chk_vkpi_dsar_type;
ALTER TABLE vkpi_dsar_requests
  ADD CONSTRAINT chk_vkpi_dsar_type
  CHECK (request_type IN ('erasure','access','rectification'));

ALTER TABLE vkpi_dsar_requests DROP CONSTRAINT IF EXISTS chk_vkpi_dsar_source;

DROP INDEX IF EXISTS idx_vkpi_dsar_source_status;
DROP INDEX IF EXISTS uq_vkpi_dsar_public_ref;

ALTER TABLE vkpi_dsar_requests DROP COLUMN IF EXISTS client_ip_hash;
ALTER TABLE vkpi_dsar_requests DROP COLUMN IF EXISTS suppression_json;
ALTER TABLE vkpi_dsar_requests DROP COLUMN IF EXISTS subject_profile_url;
ALTER TABLE vkpi_dsar_requests DROP COLUMN IF EXISTS requester_message;
ALTER TABLE vkpi_dsar_requests DROP COLUMN IF EXISTS requester_contact;
ALTER TABLE vkpi_dsar_requests DROP COLUMN IF EXISTS public_ref;
ALTER TABLE vkpi_dsar_requests DROP COLUMN IF EXISTS source;

DELETE FROM schema_migrations
 WHERE version_key = '309_vkpi_dsar_public_intake.sql';
