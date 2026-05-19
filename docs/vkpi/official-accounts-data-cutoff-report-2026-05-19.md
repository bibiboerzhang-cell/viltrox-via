# V-KPI 18 个官方账号数据与截止日期评估

生成日期：2026-05-19

本报告基于当前 V-KPI 本地数据库中的官方账号矩阵生成，只读取已有数据，不触发 YouTube/Apify/X/Reddit 等外部 provider 调用。

## 结论摘要

- 当前官方账号数：18 个，覆盖 6 个平台。
- 已有指标合计：posts=12,165，followers=1,174,598，views=367,606,819。
- 指标快照时间范围：2026-05-16 20:46 UTC 至 2026-05-18 14:21 UTC。
- 最近一次账号同步时间：2026-05-18 16:53 UTC。
- 当前样本内容最新发布时间：2026-05-17 04:00 UTC。
- 配置状态非 synced：2 个，分别是 instagram:viltrox.cine、tiktok:viltrox.store2。
- 内容样本超过 14 天未见新内容：4 个，需优先确认是否真实停更或采集未补齐。

## 截止日期判断

这里区分三种日期：

1. `last_sync_at`：账号同步任务最后执行时间。它代表系统何时尝试刷新账号。
2. `metric_captured_at / snapshot_date`：当前指标快照的采集时间/统计日期。它代表粉丝、posts、views 等汇总指标的截止点。
3. `latest_sample_post_date`：当前账号矩阵样本中能看到的最新内容发布时间。它代表内容层可见数据的截止点，但受当前样本上限限制。

当前判断：指标层主要截止在 2026-05-17，少数账号到 2026-05-18；内容层最新样本到 2026-05-17。P4/P6/P10 使用这些数据可以做内部评估，但如果要做对外月报或高精度投放复盘，应该先补一次官方账号 baseline refresh。

## 平台汇总

| 平台 | 账号数 | 已知 Posts | Followers | Views | Views 口径评估 |
| --- | ---: | ---: | ---: | ---: | --- |
| youtube | 1 | 808 | 33,600 | 19,824,454 | 高：YouTube API viewCount 可用 |
| instagram | 6 | 6,353 | 722,860 | 78,693,274 | 中：Reels/视频较可靠，图文不应按播放量理解 |
| tiktok | 5 | 2,225 | 164,509 | 266,161,774 | 高：Apify 返回 playCount 时可用 |
| facebook | 4 | 932 | 245,567 | 2,246,411 | 低：当前 Page/Post actor 多为互动和媒体，Reels 观看需另走路径 |
| reddit | 1 | 1,000 | 4,596 | 0 | 不适用：按社区帖/upvote/comment，不追播放量 |
| x | 1 | 847 | 3,466 | 680,906 | 低：受 token/actor 稳定性和限流影响 |

## 18 个账号明细

| ID | 平台 | 账号 | Handle | 状态 | Posts | Followers | Views | 指标快照 | 最近同步 | 内容样本截止 | 评估 |
| ---: | --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |
| 113 | youtube | Viltrox Official | viltroxofficial | synced | 808 | 33,600 | 19,824,454 | 2026-05-17 | 2026-05-17 | 2026-05-10 | 内容样本 >7d |
| 105 | instagram | Viltrox Cine | viltrox.cine | not_configured | 229 | 34,512 | 278,188 | 2026-05-18 | 2026-05-18 | 2026-04-23 | 配置需确认；内容样本偏旧 >14d |
| 106 | instagram | Viltrox Community | viltroxcommunity | synced | 336 | 68,362 | 4,730,990 | 2026-05-16 | 2026-05-16 | 2026-05-14 | 可用 |
| 104 | instagram | Viltrox Flash | viltrox.flash | synced | 542 | 76,180 | 10,485,500 | 2026-05-16 | 2026-05-16 | 2026-05-16 | 可用 |
| 102 | instagram | VILTROX 𝘖𝘧𝘧𝘪𝘤𝘪𝘢𝘭 𝘈𝘤𝘤𝘰𝘶𝘯𝘵 | viltrox.official | synced | 3,662 | 472,254 | 56,566,878 | 2026-05-16 | 2026-05-16 | 2026-05-16 | 可用 |
| 103 | instagram | Viltrox.USA | viltrox.usa | synced | 1,069 | 64,499 | 5,718,410 | 2026-05-16 | 2026-05-16 | 2026-05-16 | 可用 |
| 107 | instagram | Viltrox_Thailand Official | viltrox_thailand | synced | 515 | 7,053 | 913,308 | 2026-05-16 | 2026-05-16 | 2026-05-11 | 可用 |
| 114 | tiktok | Viltrox | viltrox.global | synced | 1,077 | 129,300 | 218,461,329 | 2026-05-17 | 2026-05-17 | 2026-05-16 | 可用 |
| 115 | tiktok | Viltrox | viltrox.usa | synced | 663 | 22,900 | 10,441,137 | 2026-05-17 | 2026-05-17 | 2026-05-16 | 可用 |
| 116 | tiktok | Viltrox.Flash | viltrox.flash | synced | 325 | 12,100 | 37,139,594 | 2026-05-17 | 2026-05-17 | 2026-05-15 | 可用 |
| 117 | tiktok | Viltrox.gear | viltrox.gear | synced | 80 | 85 | 57,062 | 2026-05-17 | 2026-05-17 | 2026-05-04 | 内容样本 >7d |
| 118 | tiktok | Viltrox.store | viltrox.store2 | not_configured | 80 | 124 | 62,652 | 2026-05-17 | 2026-05-18 | 2026-04-30 | 配置需确认；内容样本偏旧 >14d |
| 111 | facebook | Viltrox.Cine | viltrox.cine | synced | 251 | 12,546 | 15,708 | 2026-05-16 | 2026-05-16 | 2026-03-26 | 内容样本偏旧 >30d；播放量口径弱 |
| 110 | facebook | Viltrox.Flash | viltrox.flash | synced | 250 | 29,078 | 750,769 | 2026-05-16 | 2026-05-16 | 2026-05-16 | 播放量口径弱 |
| 108 | facebook | Viltrox.Official | viltrox.official | synced | 251 | 203,597 | 1,478,294 | 2026-05-16 | 2026-05-16 | 2026-05-16 | 播放量口径弱 |
| 109 | facebook | Viltrox.us | viltrox.usa | synced | 180 | 346 | 1,640 | 2026-05-16 | 2026-05-16 | 2026-04-29 | 内容样本偏旧 >14d；播放量口径弱 |
| 119 | reddit | Viltrox | viltrox_global | synced | 1,000 | 4,596 | 0 | 2026-05-17 | 2026-05-17 | 2026-05-16 | 播放量口径弱 |
| 112 | x | VILTROX | viltroxofficial | synced | 847 | 3,466 | 680,906 | 2026-05-17 | 2026-05-17 | 2026-05-17 | 播放量口径弱 |

## 单账号补数建议

| ID | 平台 | Handle | 建议动作 |
| ---: | --- | --- | --- |
| 113 | youtube | viltroxofficial | 补 pageToken 全量分页 |
| 105 | instagram | viltrox.cine | 先确认账号配置；补历史分页，区分 Reels/图文；优先刷新近 30 天内容 |
| 106 | instagram | viltroxcommunity | 补历史分页，区分 Reels/图文 |
| 104 | instagram | viltrox.flash | 补历史分页，区分 Reels/图文 |
| 102 | instagram | viltrox.official | 补历史分页，区分 Reels/图文 |
| 103 | instagram | viltrox.usa | 补历史分页，区分 Reels/图文 |
| 107 | instagram | viltrox_thailand | 补历史分页，区分 Reels/图文 |
| 114 | tiktok | viltrox.global | 保持禁下载视频，补历史分页 |
| 115 | tiktok | viltrox.usa | 保持禁下载视频，补历史分页 |
| 116 | tiktok | viltrox.flash | 保持禁下载视频，补历史分页 |
| 117 | tiktok | viltrox.gear | 保持禁下载视频，补历史分页 |
| 118 | tiktok | viltrox.store2 | 先确认账号配置；保持禁下载视频，补历史分页；优先刷新近 30 天内容 |
| 111 | facebook | viltrox.cine | 补 Reels/视频观看路径；优先刷新近 30 天内容 |
| 110 | facebook | viltrox.flash | 补 Reels/视频观看路径 |
| 108 | facebook | viltrox.official | 补 Reels/视频观看路径 |
| 109 | facebook | viltrox.usa | 补 Reels/视频观看路径；优先刷新近 30 天内容 |
| 119 | reddit | viltrox_global | 按社区互动评估，不看 views |
| 112 | x | viltroxofficial | 先确认稳定 token/actor |

## 主要风险与解释

1. Facebook：当前数据更适合看发帖、互动和媒体存在性，不适合直接拿 views 做平台间横向比较。要看真实视频播放，应补 Reels/视频 actor。
2. Instagram：views 更偏 Reels/视频口径，图文内容不应当被理解为无播放价值。后续需要按 media_type 分层。
3. TikTok：views 口径相对可用，但历史补数要保持 video download disabled，避免成本和存储失控。
4. Reddit：没有播放量概念，应看发帖、upvote/comment 和社区反馈。
5. X：当前可作为辅助信号，进入稳定看板前应确认 token/actor 和限流策略。
6. `not_configured` 的账号并不等于没有历史数据；它表示当前同步配置状态需确认。报告中 IG `viltrox.cine` 和 TikTok `viltrox.store2` 属于这一类。

## 建议下一步

1. 先修正/确认 2 个 `not_configured` 账号。
2. 对 18 个账号跑一次人工确认的官方账号 baseline refresh，禁止页面加载自动触发。
3. YouTube 补 pageToken 分页；Instagram/TikTok 补历史分页；Facebook 单独补 Reels/视频观看路径。
4. 后续报告中保留三类日期：同步时间、指标快照时间、内容样本截止时间，避免把账号快照误当成内容截止。
5. 如果要发给业务同事，只建议使用 PDF 中的汇总和明细，不要把 views 做跨平台绝对排名。

## 数据来源

- `backend/app/services/vkpi/channels.py::official_account_matrix(staff={id:1, role:admin}, limit=50)`
- `backend/app/services/vkpi/channels.py::_latest_official_channel_rows(...)`
- `scripts/vkpi_official_baseline_plan.py --json` 用于确认当前矩阵账号数和 baseline 策略。
- 本报告生成过程不调用 provider，不写数据库。
