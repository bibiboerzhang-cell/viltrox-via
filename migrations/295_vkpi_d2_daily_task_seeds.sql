-- 295: D2 车道三个零 LLM 日任务的 scheduler_tasks 种子(默认 OFF,运维开闸)。
INSERT INTO scheduler_tasks (task_key, label, enabled, risk_level) VALUES
    ('vkpi_pool_raw_fields_backfill', 'KOL 池 raw 字段回填(每日 05:00,500 行,不入队联系方式,零 Apify)', FALSE, 'low'),
    ('vkpi_tracking_auto_enroll', '收藏 KOL 新视频证据自动登记指标追踪(每日 05:20,受 30 美元月闸)', FALSE, 'low'),
    ('vkpi_lens_evidence_backfill', '镜头证据回填(每日 05:40,final_v1 缓存钩子漏扫/抽取器升版兜底)', FALSE, 'low')
ON CONFLICT (task_key) DO NOTHING;
