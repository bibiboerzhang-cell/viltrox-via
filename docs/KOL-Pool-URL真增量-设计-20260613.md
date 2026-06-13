# #4 URL 四象限「抓取→结果一条路」真增量 · 实现设计(2026-06-13)

> 来源:4 路并行只读设计 trace(we3hljnd2)。闸B(触 3 爬虫输入契约 + worker + url_deep_crawl 编排)。
> 总策略:**双保险**——since 下推爬虫(省抓取配额)+ 保留 `_filter_incremental_profile_videos:1451` 客户端裁剪(actor 若不支持日期字段则忽略,客户端兜底,功能不破)。
> 红线:viltrox_fit_score 唯一写点 pool.py:892 / rule_v0 / 三循环依赖 全程不碰。游标 = `_profile_incremental_state:1650` 的 `last_video_at`(`_parse_date` 归一 `YYYY-MM-DD`)。

## 四象限期望 vs 现状(已对账)
| 象限 | 期望 | 现状 | 缺口 |
|---|---|---|---|
| 视频·新 | 入库+完整账号分析 | 建档+代表作+history 但 max_posts=3 截断 | P0-3/4 断头 |
| 视频·在库 | 视频分析+账号增量 | 只该视频,**账号侧零动作**(`_execute_existing_creator_video_flow:754`) | **缺口最大** |
| 账号·新 | 完全分析 | profile 仅 3 帖、无 history | P0-3/4 |
| 账号·在库 | 视频分析+账号增量(到上次节点) | 读 cursor 却**不下推爬虫**(`:629`),全量重爬+事后过滤 | **P0-1 假增量** |

## 各爬虫 since 字段支持度(设计实证级)
- **Instagram**:`crawl_channel_profile`(profile-scraper)**无日期字段** → since 非空仅放宽 resultsLimit 12→48 + 客户端裁剪;`crawl_channel_videos`(`apify~instagram-scraper`)**支持 `onlyPostsNewerThan`**(置信度 high)→ 下推。【IG 2 补丁=精确】
- **TikTok**:`clockworks/tiktok-scraper` 支持 **`oldestPostDate`** → 下推(需加 `_normalize_since` 提取 YYYY-MM-DD)。【TT 补丁=散文,实现时手补】
- **YouTube**:API `search` 端点支持 **`publishedAfter`**(RFC3339);有 since 时强制走 search 路径(playlistItems 不支持日期);Apify fallback `oldestPostDate` best-effort。【YT 5 补丁=精确,含 `_since_to_rfc3339` helper】

## 实现步骤(下一刀,逐文件 py_compile + 闸B 报备)
1. **3 爬虫加 `since` keyword-only**(默认空/None,**向后兼容零回归**:21 处现有调用方不传):
   - instagram_crawler.py:167/203(IG 精确补丁)
   - tiktok_crawler.py:51(`_normalize_since`)/166/204/191/224(手补 oldestPostDate)
   - youtube_crawler.py:15(`_since_to_rfc3339`)/261/304/340/373(YT 精确补丁)
2. **url_deep_crawl 编排**(orchestration 6 补丁):
   - `_crawl_profile_basics:1890` 签名加 since,YouTube 分支(:1898/1903)+ 非 YT 分支(:1910)透传(**直接传 since=,不需 `_since_kwargs` 探测——爬虫已同批加 since**)。
   - `_execute_profile_flow:629` 把 `incremental_state["last_video_at"]` 作为 since 传入(治 P0-1)。
   - **断头解除**:`_max_posts:2070` 兜底 3→12;账号 URL 注入 `mode=account_deep`(命中 :1374/:1387 materialize history)。
   - **在库视频接账号增量**【缺口最大】:`_execute_existing_creator_video_flow:754` 该视频 evidence+enqueue 后,读 `_profile_incremental_state` 取 last_video_at,以 since 触发一次轻量账号增量(复用 `_crawl_profile_basics(since=)` + representative/history 只处理 cutoff 之后),**去重排除当前视频**。
3. **闸B safety 块**:`dry_run_url_deep_crawl:191` 的 `provider_calls_performed` 必须回灌「在库视频」分支新增的 profile 爬取(:159-163 当前没把新 provider 调用计入 safety.crawl_performed);`_record_deep_crawl_run` summary 带 `since`/`incremental_mode` 供审计。
4. **验收**:① py_compile 全 4 文件;② dry-run 验一个 actor 的 since 字段真被接受(避免静默全量);③ 真增量烟测(在库账号再抓,确认只补 cutoff 之后 + 配额下降);④ 在库视频再点确认补「账号到上次节点增量」。

## 风险/兜底
- actor 日期字段为推断:若 actor 忽略 since 键 → 客户端 filter 兜底(功能不破,只是不省抓取配额);若 actor 报错未知键 → 多数 Apify actor 忽略未知字段(低风险),dry-run 先验。
- 全程双保险 + 向后兼容(since 空=字节级现状)。
