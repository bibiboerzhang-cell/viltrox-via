-- 110: PV-3 例外遮蔽标记(默认全可见;restricted=true 仅 ADMIN 可见)
-- 择薄说明:metadata_json 承载需 scope SQL 做 JSON 提取,独立布尔列过滤最薄且可索引。
ALTER TABLE vkpi_projects ADD COLUMN IF NOT EXISTS restricted BOOLEAN NOT NULL DEFAULT FALSE;
