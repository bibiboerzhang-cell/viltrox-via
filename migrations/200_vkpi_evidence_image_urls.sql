-- 200_vkpi_evidence_image_urls.sql — 给视频证据表加「轮播图 URL 列表」列。
-- 背景:IG 的 /p/ 既可能是单视频/reel,也可能是多图轮播(Sidecar)或纯图片。识别分流后
-- (见 evidence_type=image),图文/轮播帖不进视频深析,但要把多张图留住、前端展示成图片轮播。
-- 此前 evidence 表只有单张 thumbnail_url,存不下整组轮播图,故加此列。
-- additive、幂等(IF NOT EXISTS)。注释零 ASCII 问号(避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯展示数据,绝不触 viltrox_fit_score。
BEGIN;
-- 用 TEXT 存 JSON 数组串(读时 json.loads),避开 compat 连接层对 jsonb 直插字符串的 cast 问题。
ALTER TABLE vkpi_kol_video_evidence ADD COLUMN IF NOT EXISTS image_urls TEXT;
COMMIT;
