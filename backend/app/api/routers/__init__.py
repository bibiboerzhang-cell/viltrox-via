from app.api.routers import activities, auth, admin, audit, creator, uploads, sse, leaderboard, platform_ingest, media, via, student_identity, vkpi, vkpi_attribution_metrics, vkpi_comment_intelligence, vkpi_audit, vkpi_comments, vkpi_costs, vkpi_dashboard_staff, vkpi_data_quality, vkpi_evidence_assets, vkpi_feedback, vkpi_firewall, vkpi_industry_automation, vkpi_kol_links, vkpi_kol_pool, vkpi_memory, vkpi_operations, vkpi_pillars, vkpi_product_analysis, vkpi_projects, vkpi_reconciliation, vkpi_reports, vkpi_settings, vkpi_sentiment, vkpi_sync, vkpi_weekly_reports, vkpi_workflow_assets

__all__ = ["activities", "auth", "admin", "audit", "creator", "uploads", "sse", "leaderboard", "platform_ingest", "media", "via", "student_identity", "vkpi", "vkpi_attribution_metrics", "vkpi_comment_intelligence", "vkpi_audit", "vkpi_comments", "vkpi_costs", "vkpi_dashboard_staff", "vkpi_data_quality", "vkpi_evidence_assets", "vkpi_feedback", "vkpi_firewall", "vkpi_industry_automation", "vkpi_kol_links", "vkpi_kol_pool", "vkpi_memory", "vkpi_operations", "vkpi_pillars", "vkpi_product_analysis", "vkpi_projects", "vkpi_reconciliation", "vkpi_reports", "vkpi_settings", "vkpi_sentiment", "vkpi_sync", "vkpi_weekly_reports", "vkpi_workflow_assets", "ADMIN_ROUTER_MODULES"]

# ---------------------------------------------------------------------------
# F2 工程税①:vkpi_* 挂载区注册表。
# main.py 的 IS_ADMIN_APP 分支用一个循环按此顺序 importlib + include_router,
# 取代此前百余行手写 import+include。
# 【铁律】顺序 = 2026-07-07 收敛前 main.py 的历史挂载顺序,逐一照抄;
#   新增路由模块 append 到对应主题段落即可,绝不要重排已有项(路由表顺序=对外行为)。
# 收敛验收:改造前后 app.routes 的 (type, path, methods, name) 有序全量对比逐字一致。
# 注:dashboard_account_picker 无 vkpi_ 前缀,但历史上就挂在 vkpi 挂载区中段
#   (vkpi_dashboard_staff 与 vkpi_attribution_metrics 之间),为保持顺序原位保留。
#   vkpi.router 是零路由空壳(P2 裁决)、vkpi_workflow_assets 从未挂载 —— 均不入表。
# ---------------------------------------------------------------------------
ADMIN_ROUTER_MODULES: list[str] = [
    "vkpi_access",
    "vkpi_actions",
    "vkpi_metrics",
    "vkpi_agents",
    "vkpi_skills",
    "vkpi_analytics_export",
    # P1 智能可见周:Intelligent 问答三车道 / 思考流 / 评论区销售员(路由自带 prefix)
    "vkpi_intelligent",
    "vkpi_activity",
    "vkpi_reply_queue",
    # 第2轮 档案工程:招牌画像 / 周期+竞争 / SKU 360°
    "vkpi_signature",
    "vkpi_leadtime",
    "vkpi_sku360",
    # 第3轮 信号聚合层:焦段矩阵 / 品牌脉搏 / 质量分+FTC / 以视频找相似
    "vkpi_focal_matrix",
    "vkpi_brand_pulse",
    "vkpi_brand_safety",  # G4 品牌安全 v0(库内信号)
    "vkpi_authenticity",  # G4 受众真实性信号
    "vkpi_quality_compliance",
    "vkpi_outreach_signals",  # G1 外联三承诺+敢给差评信号
    "vkpi_video_similar",
    # 第4轮 预测+发射台:预测战绩 / 报价库 / 全案组装 / 覆盖组合
    "vkpi_forecast",
    "vkpi_rates",
    "vkpi_launch_assembly",
    "vkpi_roster",
    # 第5轮 自治层:预测台账 / 驾照 / 晨报 / 市场之声
    "vkpi_prediction_ledger",
    "vkpi_autonomy",
    "vkpi_morning_brief",
    "vkpi_market_voice",
    # 第6轮 L轨道+P6:记分卡 / 复盘 / 影子评测 / 创意资产库
    "vkpi_weekly_scorecard",
    "vkpi_miss_review",
    "vkpi_shadow_eval",
    "vkpi_creative_segments",
    # A波 四主线:每日学习 / 预设问题库
    "vkpi_daily_digest",
    "vkpi_canned_queries",
    # C8 Local Worker:注册/租约/校验/看板
    "vkpi_local_workers",
    "vkpi_local_worker_admin",
    "vkpi_local_worker_board",
    # 战略大脑:对照/赛道/模拟/表现
    "vkpi_industry_benchmark",
    "vkpi_category_tracks",
    "vkpi_strategy_sim",
    "vkpi_strategy_performance",
    # B+波 数据地基
    "vkpi_contact_system",
    "vkpi_audience_geo",
    "vkpi_forecast_feedback",
    "vkpi_agent_loop",
    "vkpi_customer_mining",
    # F4 召回三段式:语义召回(embedding→粗排→LLM重排)
    "vkpi_recall",
    # G波 弹药:战绩卡 / 履约漏斗 / 评论区机会 / 长尾波次
    "vkpi_performance_card",
    "vkpi_gifted_funnel",
    "vkpi_comment_opportunities",
    "vkpi_longtail_wave",
    "vkpi_kol_portal",
    "vkpi_dashboard_staff",
    "dashboard_account_picker",
    "vkpi_attribution_metrics",
    "vkpi_audit",
    "vkpi_budgets",
    "vkpi_launch",
    "vkpi_triage",
    "vkpi_comments",
    "vkpi_comment_intelligence",
    "vkpi_costs",
    "vkpi_data_quality",
    "vkpi_evidence_assets",
    "vkpi_events",
    "vkpi_inventory",
    "vkpi_dealers",
    "vkpi_shopify",
    "vkpi_goaffpro",
    "vkpi_staff_groups",
    "vkpi_feedback",
    "vkpi_firewall",
    "vkpi_industry_automation",
    "vkpi_kol_decisions",
    "vkpi_kol_links",
    "vkpi_kol_pool",
    "vkpi_learning",
    "vkpi_memory",
    "vkpi_kol_memory",
    "vkpi_my_kol",
    "vkpi_operating_review",
    "vkpi_operations",
    "vkpi_pillars",
    "vkpi_product_analysis",
    "vkpi_projects",
    "vkpi_reconciliation",
    "vkpi_reports",
    "vkpi_search",
    "vkpi_settings",
    "vkpi_sentiment",
    "vkpi_sync",
    "vkpi_tasks",
    "vkpi_weekly_reports",
]
