-- ============================================================
-- Migration: 001_verification.sql
-- Date: 2026-04-09
-- Purpose: 给 verifications 表加新字段, 支持 "主页 URL + 评论验证" 流程
-- ============================================================

-- 安全策略: 用 ALTER TABLE ADD COLUMN, 不动现有数据
-- 所有新字段都是可选的, 老的 verification 不会受影响

-- ──────────────────────────────────────────────────────────────
-- 检查 verifications 表是否存在 (如果不存在, 先建)
-- ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    code TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    approved_at TEXT,
    approved_by TEXT,
    note TEXT
);

-- ──────────────────────────────────────────────────────────────
-- 新字段 (用 ALTER TABLE)
-- 注意: SQLite 不支持 ADD COLUMN IF NOT EXISTS, 所以下面的 ALTER 
-- 第二次跑会报错. 应用代码里要 catch error.
-- ──────────────────────────────────────────────────────────────

-- 主页 URL (用户输入的, 例 https://www.youtube.com/@bibiboerz6679)
ALTER TABLE verifications ADD COLUMN profile_url TEXT;

-- 基线数据 (用户绑定时, 调 Apify 抓主页拿到的)
ALTER TABLE verifications ADD COLUMN baseline_username TEXT;
ALTER TABLE verifications ADD COLUMN baseline_followers INTEGER;
ALTER TABLE verifications ADD COLUMN baseline_avatar_url TEXT;
ALTER TABLE verifications ADD COLUMN baseline_bio TEXT;
ALTER TABLE verifications ADD COLUMN baseline_data_json TEXT;  -- 完整 JSON

-- GPT 生成的好评 (用户复制粘贴用)
ALTER TABLE verifications ADD COLUMN generated_comment TEXT;

-- 用户点 "I've Posted" 的时间
ALTER TABLE verifications ADD COLUMN posted_at TEXT;

-- 评分系统
ALTER TABLE verifications ADD COLUMN match_score REAL DEFAULT 0;

-- 扫描记录
ALTER TABLE verifications ADD COLUMN scan_count INTEGER DEFAULT 0;
ALTER TABLE verifications ADD COLUMN last_scanned_at TEXT;

-- 找到的评论数据 (匹配成功后写入)
ALTER TABLE verifications ADD COLUMN comment_id TEXT;
ALTER TABLE verifications ADD COLUMN comment_username TEXT;
ALTER TABLE verifications ADD COLUMN comment_text TEXT;
ALTER TABLE verifications ADD COLUMN comment_video_url TEXT;

-- 过期时间 (24h 后, 防长期占用)
ALTER TABLE verifications ADD COLUMN expires_at TEXT;

-- ──────────────────────────────────────────────────────────────
-- 索引 (加速查询)
-- ──────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_verifications_status 
    ON verifications(status);

CREATE INDEX IF NOT EXISTS idx_verifications_user_id 
    ON verifications(user_id);

CREATE INDEX IF NOT EXISTS idx_verifications_platform_status 
    ON verifications(platform, status);

CREATE INDEX IF NOT EXISTS idx_verifications_code 
    ON verifications(code);

CREATE INDEX IF NOT EXISTS idx_verifications_created 
    ON verifications(created_at);

-- ──────────────────────────────────────────────────────────────
-- 完成
-- ──────────────────────────────────────────────────────────────

-- 验证: 查看表结构
-- .schema verifications
