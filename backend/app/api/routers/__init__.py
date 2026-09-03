"""Router package registry.

Submodules intentionally remain lazy.  Python's package import semantics keep
``from app.api.routers import <submodule>`` compatible without importing every
router when callers only need the registry below.
"""

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
    # 私有 Marketing Advisor:会话持久化 / 用户确认记忆 / 业务动作仅草稿。
    "vkpi_marketing_advisor",
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
    # GTM-1 总脑:纯读 GTM Plan Preview / summary+规则库
    "vkpi_market_brain",
    "vkpi_market_brain_summary",
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
    "vkpi_event_radar",
    "vkpi_source_passports",
    "vkpi_inventory",
    "vkpi_dealers",
    "vkpi_dealer_location_verification",
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
    "vkpi_lens_insights",
    "vkpi_my_kol",
    # C5 观察清单:MY KOL 分组进度总览两 GET(纯读零 provider,append-only,2026-08-23)
    "vkpi_my_kol_watchlist",
    # 波 D·B:一键数据关注 POST + 按产品聚合播放总览 GET(写口全复用追踪围栏,append-only,2026-08-23)
    "vkpi_my_kol_sku_play",
    # 内容墙「去查最新内容」:报价 GET(纯读)+ 派活 POST(复用既有账号取数入队器,
    # 报价指纹绑定确认框数字,append-only,2026-08-25)
    "vkpi_my_kol_wall_fetch",
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
    # U1 会呼吸的指挥室:顶栏全局任务进度中心(纯读聚合,2026-07-07,append-only)
    "vkpi_progress_center",
    # U3 会呼吸的指挥室:GTM 90 天北极星三表盘(纯读真库现查,表缺诚实 0,append-only)
    "vkpi_northstar",
    # W1 闭环波:bet materialize / 裁决流 / 三窗对答案 / 权重回流(append-only)
    "vkpi_gtm_materialize",
    "vkpi_gtm_verdicts",
    "vkpi_gtm_windows",
    "vkpi_gtm_weights",
    # GTM-2 E3:评论细粒度情绪+渴望密度 KPI(纯词表零 LLM,append-only,2026-07-07)
    "vkpi_fine_emotion",
    # GTM-2 E2:内容记分卡三平台北极星换轴判档(growth_playbook 消费,纯读,append-only)
    "vkpi_content_scorecard",
    # GTM-2 E4:规则库回归验证+校准报告(growth_playbook×自有已析视频,纯读,append-only)
    "vkpi_rule_validation",
    # GTM-2 E1:情绪标签体系——词表回打 emotion_tags_v1 + KOL 情绪画像(零 LLM 零重析,append-only)
    "vkpi_emotion_tags",
    # CB4 Channel Brain:Dealer geo/category 适配评分 v0(纯读 vkpi_dealers,0 行诚实 data_missing,append-only,2026-07-07)
    "vkpi_dealer_scoring",
    # CB3 Channel Brain:独立站/Shopify 承接建议 Conversion Readiness Actions(纯读 checklist,本地 0 订单诚实 data_missing,append-only,2026-07-07)
    "vkpi_indie_site",
    # CB1 Channel Brain:官号内容计划器 Owned Media Planner(纯读 vkpi_channel_post_metrics,词表法零 LLM,append-only,2026-07-07)
    "vkpi_official_planner",
    # CB2 Channel Brain:渠道组合器 Channel Mix Optimizer(Binet-Field 60/40 跨渠道预算分配,KOL 复用 strategy_sim,Dealer 0 行诚实 data_missing,纯读,append-only,2026-07-07)
    "vkpi_channel_mix",
    # 市场之声反馈流:vkpi_comments 分页原声 feed(身份 kol/owned/user 三路 JOIN,纯读零 LLM,append-only,2026-07-11)
    "vkpi_market_voice_feed",
    # 挂账迸发①:板块 KPI 按日时序统一端点 board-series(8 板 sparkline/环比真数据,纯读零 LLM,append-only,2026-07-12)
    "vkpi_board_series",
    # 顶栏 Ask P1:$SKU/镜头直达候选 catalog/suggest(纯读三列,零 LLM,append-only,2026-08-22)
    "vkpi_catalog_suggest",
    # 学习闭环 L 车道:搜索页反馈写口 search-feedback(有用/没用+拒绝原因闭集,幂等落 recommendation_feedback,2026-08-23)
    "vkpi_recommendations",
    # 公测 L-legal-dsar:法务页 SPA 分发(/legal /privacy /terms)+ 公开 DSAR 表单(限流 5/h/IP)
    # + 员工审批口 /api/admin/vkpi/dsar(erasure→既有 erase_subject;do_not_contact→抑制台账;append-only,2026-09-02)
    "dsar_public",
    # 公测 LE-crawl-health:近 N 天「任务类型 × 状态 × 原因」汇总 + 完成率
    # (纯 SELECT 投影,零写库零取数;原因走 last_error 稳定码表,
    #  不依赖对最大失败桶恒 NULL 的 last_error_category;append-only,2026-09-03)
    "vkpi_crawl_health",
]
