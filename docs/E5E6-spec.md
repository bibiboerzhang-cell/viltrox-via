# E5/E6 一页 Spec(2026-06-12,裁决②条件件;施工等窗口纪律)

## E6 · 真 since 游标改造
**游标粒度(a++ 老坑的解)**:`vkpi_kol_pool.last_video_at` 是 **date 粒度**(同日多发漏抓)→ 新列
`last_video_seen_at TIMESTAMPTZ`(**migration 108 三段式**:up 加列 + 以既有 last_video_at 23:59:59 初始化非空行;down 删列;注册随 apply 令)。旧列保留只读不删(报表兼容)。

**五 crawler 签名改动清单**(统一加可选参 `since: datetime | None`,None=现行为):
| crawler | provider 原生参数 | 改动 |
|---|---|---|
| youtube_crawler(官方 Data API) | `publishedAfter`(RFC3339) | search/playlistItems 请求加参;`:249/:292` 两签名 |
| instagram_crawler(Apify profile-scraper) | `onlyPostsNewerThan`(ISO date) | actor input 加键;`:164/:200` |
| bilibili_crawler | API 按 pubdate 倒序 → 本地 since 截断(provider 无参,**如实标注半真游标**) | `:118/:137` 加 early-stop:遇 pubdate<since 停翻页 |
| twitch_crawler | `started_at`(videos API) | `:146/:166` |
| reddit_crawler | listing `before/after` fullname 游标 → 按 created_utc early-stop | `:558/:570` |

**游标推进统一**:成功 sync 后写 `last_video_seen_at = max(本次 materialized posted_at)`(无新内容则写 sync 时刻);
唯一推进点放 `url_deep_crawl._finalize_incremental`(新小函数),daily light refresh 与 execute auto **共用同一推进点**(消灭 a++ 的"两套互不联动")。

**批量工具回显规格**(操作面,闸 E4 语义不变——只做单 KOL/小批,auto-fanout 仍闸):
- 入参过滤器全集:`platform[] / tier[] / stale_before / source_type / limit / offset / dry_run`
- 回显:`requested(命中行数)/ synced / new_items / skipped_by_cursor / provider_calls / errors[≤30] / 每行 {id, handle, since_used, new_count}`
- dry_run 必须返回命中行数与 since 分布,不打 provider。

## E5 · 账号全量同步补建(F4 硬前置)
**job_type 注册三点同步**(架构作战图脆弱点,逐点列出):
1. **worker dispatch**:`apify_jobs_worker.py:3001` if 链新增 `account_full_sync` 早返回分支 → handler
   `_process_account_full_sync`(`db_connection_sync_scope` 包裹,复用 **Stage1 ingest path contract**:
   crawler 分页拉全量(per-platform max 翻页,YT playlistItems/IG resultsLimit 分批)→ `video_evidence` 入库(content_url 去重)
   → 头像/代表作视频写 R2(媒体缓存域既有 `_record_media_cache_asset` 管线)→ 可选按预算入队 final_v1 深析(默认仅代表作 N=3,余下记 evidence 不析——E4 闸)
2. **queue_view**:`_infer_kind/_infer_stage` 加映射 `account_full_sync → 账号全量同步 / search`(抓取段)
3. **TaskProgressBoard**:泳道 stage 已有三道,归"搜索中"道;ETA 由 d8 已加的均时机制自动覆盖
**触发点**:新人 onboarding 尾部(`url_deep_crawl.py:919` 候选池处改为入队 account_full_sync 而非掐死在 max_posts=3)+ Drawer 手动按钮(后续)。
**预算护栏**:单账号 provider 调用上限(默认 ≤10 页)+ 复用 crawl budget 表;LLM 部分零增量(只析代表作,语义同现状)。
**幂等**:evidence 按 content_url 去重;job 按 (kol_pool_id, job_type) active 去重(同 C2 范式)。

## 验收
- E6:同一账号两次 execute,第二次 `provider_calls` 显著下降且 `skipped_by_cursor≈0`(provider 侧已截断);同日多发不漏(timestamptz 粒度)
- E5:新人 URL 走完,evidence 行数 ≈ 账号真实视频数(±分页上限),泳道全程可见;铁律指纹随写库 commit 对账
