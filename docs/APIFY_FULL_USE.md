# 把 Apify 用透(替代商业达人库,自持省钱)

战略:不买 Modash/HypeAuditor/蝉妈妈,**用 Apify 把发现 + 富集都覆盖**。地基已建,激活只需配 actor env。

## 三块能力 + 激活开关

| 能力 | 模块 | 激活(env) | 默认 |
|---|---|---|---|
| **发现搜索**(关键词→达人) | `discovery/federation._apify_search` | `VKPI_APIFY_SEARCH_ACTOR=streamers/youtube-scraper` | 未设=not_configured(零计费) |
| **KOL 富集**(抓公开档案存证据) | `discovery/apify_enrich.enrich_kol` | `VKPI_APIFY_ENRICH_ENABLED=1` | 未设=disabled(零计费) |
| **评论/视频深析**(已有) | `services/scraping/apify.scrape_*` | `APIFY_TOKEN` + 各平台 actor | 既有管线 |

## 平台 actor(env 可换,默认值在代码)
- YouTube:`streamers/youtube-scraper`(搜索 + 频道)
- TikTok:`APIFY_TIKTOK_ACTOR_ID`(默认 `clockworks/tiktok-scraper`)
- Instagram:`apify/instagram-scraper`
- 抖音:`APIFY_DOUYIN_*_ACTOR_ID`

## 怎么用透(运营动作)
1. 配 `APIFY_TOKEN`(已有)。
2. 发现广度:设 `VKPI_APIFY_SEARCH_ACTOR` → `/kol-pool/discovery/federated-search?q=...` 直接用 Apify 搜真达人入联邦。
3. 数据规模:设 `VKPI_APIFY_ENRICH_ENABLED=1` → `POST /kol-pool/{id}/enrich-via-apify` 抓公开档案存富集证据;可挂 D3 自动轮询批量富集收藏的 KOL。
4. **成本控制**:多个 APIFY_TOKEN 走 [[token_broker]] 轮转;富集只对收藏/高价值 KOL(非全量),避免烧钱。

## 诚实边界
- Apify 给的是**公开数据**(粉丝/播放/互动/视频)——和商业库的"公开层"同源。
- **受众画像/精确刷粉**这类"估算层",Apify 不直接给;若真要,再单独评估买 HypeAuditor(但多为估算,性价比存疑)。
- 富集一律 `confidence` 标注、存为证据,**绝不并入 viltrox_fit_score**。
