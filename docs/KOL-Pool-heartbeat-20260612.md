# 心跳盘点一页(B)· APScheduler 全部注册 job(2026-06-12,只读)
> 现状:全系统 `ENABLE_SCHEDULER=0`(admin 实测 env / worker 脚本默认)→ **13 个 job 全部死着**。
> 归 Codex 回归单附录;"复活"=开 ENABLE_SCHEDULER 后自动恢复,无需改码。

| # | job id | 节奏 | 死着影响什么 | 随 E6 复活还是删 |
|---|---|---|---|---|
| 1 | verification_scan_check | 5min | 审核队列无人扫,verification 流程停 | 复活(与 E6 无关,开关即活) |
| 2 | cache_cleanup | 30min | 过期缓存堆积(表缓慢膨胀,非紧急) | 复活 |
| 3 | pending_asset_cleanup | 30min | 30 天 stale 上传资产不清理 | 复活 |
| 4 | rate_limit_cleanup | 1h | 限流桶残留(无功能影响) | 复活 |
| 5 | provider_health_check | 5min | provider 健康页数据死 | 复活 |
| 6 | bh_daily_snapshot | 03:00 | B&H 竞品快照停更(launchd 另有 prod-snapshot-sync,二者关系 Codex 核) | 复活(先核与 launchd 任务是否重复) |
| 7 | via_daily_learning | 04:15(条件注册) | Via 学习停 | 复活 |
| 8 | confirm_partial_awards | 10min | **部分奖励 24h 确认卡住(若 rewards 在用=中危)** | 复活(优先核) |
| 9 | vkpi_lineage_snapshot | 1h | 指标血缘快照停更 | 复活 |
| 10 | vkpi_kpi_rollup | 01:20 | **每日 KPI/工作量汇总停——staff KPI 页 stale(团队上线后可感)** | 复活(优先核) |
| 11 | vkpi_alerts | 30min | 停滞工作流告警静默 | 复活 |
| 12 | vkpi_weekly_report | 周一 02:00 | 周报停 | 复活 |
| 13 | vkpi_morning_sync | 08:00(中国时区) | **KOL/channel/product 日同步停 = last_seen_at 停摆主因**;内含 kol_pool_light(另有 allow_qualified 应用层闸) | **E6 真游标落地后复活**(否则伪增量烧配额);其余 stage(channel/product)可先行评估单独放行 |

**删除候选:无**(13 个均有真实职责;唯 #6 需与 launchd 快照任务去重核对)。
**复活路径**:`ENABLE_SCHEDULER=1` 重启 admin(或专跑一个 scheduler 进程)→ 13 个全活;`#13` 的 kol_pool_light 段还需 payload `allow_qualified_kol_refresh=true` 才真跑(双闸设计,E6 前保持关)。

---

# 周脉冲报价一行(C)· 待裁
**qualified 带闸脉冲当前真实作用面 = 25 行**(`vkpi_kol_refresh_tier` 仅 25 warm、last_refresh_at 全 NULL——不是 960;960 是 legacy 全量口径):**~$0.06-0.17/次、<$1.2/周**(YouTube 走官方 Data API 免费配额;Instagram 走 Apify instagram-profile-scraper 牌价 ~$2.3/千结果 × max_posts≤3)。若按 06-04 那种 **legacy 全量 960 行**口径(YT 527 免费 + IG 297×3 结果 + TT 105):**~$2-4/次、$15-30/周**。⚠️ 库内零 actual_cost 记账(Apify 计费在平台侧),以上为牌价推算,**精确值需 Apify 控制台跑一次 25 行小批实测**——待报价裁决。

> 附:E6 未落地前任何脉冲都是伪增量(整列表重拉本地截断)——钱花在重抓上;裁"开"也建议只开 qualified 25 行口径。
