-- 309: DSAR 公开申请通道(公测阻断 B:L3 / BK-08)——vkpi_dsar_requests 加「公开表单来源」列并放宽工单类型。
--
-- 背景:117 建的 vkpi_dsar_requests 只服务员工手工开单(requested_by_staff_id),零路由调用方;
--   隐私页必须能指向一个可用的删除 / 勿联系通道,公开表单落本表 pending 行,员工在
--   /api/admin/vkpi/dsar/requests 审批 → erase_subject(既有级联删除)/ 抑制通道(既有 HMAC 指纹台账)。
-- 口径:
--   * source        = 'staff'(既有行默认)| 'public_form'(公开表单);
--   * public_ref    = 给申请人引用的回执号(DSAR-xxxxxxxx),部分唯一索引;
--   * requester_contact = 申请人自报的回复邮箱(需要它才能答复主体;只存本列,响应 / 日志一律不回显);
--   * requester_message = 申请人留言(≤2000 字,写入前截断);
--   * subject_profile_url = 申请人提供的主页链接(员工核对身份用);
--   * suppression_json  = 勿联系申请的抑制结果摘要(只有状态码,不含明文联系方式);
--   * client_ip_hash    = 提交端 IP 的截短 HMAC(限流 / 滥用追溯,不存原 IP);
--   * request_type 放宽为含 'do_not_contact'(勿联系:不删档案,只进抑制台账)。
-- 红线:只加列 / 改 CHECK;不 DELETE、不 UPDATE 既有行;不碰 viltrox_fit_score / rule_v0。
-- 回滚见 309_vkpi_dsar_public_intake_down.sql。

ALTER TABLE vkpi_dsar_requests
  ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'staff';
ALTER TABLE vkpi_dsar_requests
  ADD COLUMN IF NOT EXISTS public_ref TEXT;
ALTER TABLE vkpi_dsar_requests
  ADD COLUMN IF NOT EXISTS requester_contact TEXT NOT NULL DEFAULT '';
ALTER TABLE vkpi_dsar_requests
  ADD COLUMN IF NOT EXISTS requester_message TEXT NOT NULL DEFAULT '';
ALTER TABLE vkpi_dsar_requests
  ADD COLUMN IF NOT EXISTS subject_profile_url TEXT NOT NULL DEFAULT '';
ALTER TABLE vkpi_dsar_requests
  ADD COLUMN IF NOT EXISTS suppression_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE vkpi_dsar_requests
  ADD COLUMN IF NOT EXISTS client_ip_hash TEXT NOT NULL DEFAULT '';

ALTER TABLE vkpi_dsar_requests DROP CONSTRAINT IF EXISTS chk_vkpi_dsar_type;
ALTER TABLE vkpi_dsar_requests
  ADD CONSTRAINT chk_vkpi_dsar_type
  CHECK (request_type IN ('erasure','access','rectification','do_not_contact'));

ALTER TABLE vkpi_dsar_requests DROP CONSTRAINT IF EXISTS chk_vkpi_dsar_source;
ALTER TABLE vkpi_dsar_requests
  ADD CONSTRAINT chk_vkpi_dsar_source
  CHECK (source IN ('staff','public_form'));

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_dsar_public_ref
  ON vkpi_dsar_requests(public_ref)
  WHERE public_ref IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_vkpi_dsar_source_status
  ON vkpi_dsar_requests(source, status, created_at DESC);

COMMENT ON COLUMN vkpi_dsar_requests.source IS
    'staff = 员工手工开单;public_form = /api/public/dsar/requests 公开表单(309)';
COMMENT ON COLUMN vkpi_dsar_requests.public_ref IS
    '申请人回执号 DSAR-xxxxxxxx(309);申请人凭此号跟进,响应只回本号不回联系方式';
COMMENT ON COLUMN vkpi_dsar_requests.requester_contact IS
    '申请人自报回复邮箱(309):仅用于答复主体;API 列表口只回脱敏形态,日志绝不记录';
COMMENT ON COLUMN vkpi_dsar_requests.suppression_json IS
    '勿联系申请的抑制结果摘要(309):{status, channel, reason};不含明文联系方式';
