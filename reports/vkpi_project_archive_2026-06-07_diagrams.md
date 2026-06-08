# V-KPI 链路图合集（可视化 PNG + Mermaid 源码）

生成日期：2026-06-07

## 系统总架构

![系统总架构](/Users/bibiboer/Documents/V-KPI——marketing/reports/diagrams/01_system_architecture.png)

## KOL Pool 主列表与详情

![KOL Pool 主列表与详情](/Users/bibiboer/Documents/V-KPI——marketing/reports/diagrams/02_kol_pool_detail.png)

## V6 Fit 写口

![V6 Fit 写口](/Users/bibiboer/Documents/V-KPI——marketing/reports/diagrams/03_v6_fit_writes.png)

## final_v1 视频深析

![final_v1 视频深析](/Users/bibiboer/Documents/V-KPI——marketing/reports/diagrams/04_final_v1_video.png)

## Keyframe QA

![Keyframe QA](/Users/bibiboer/Documents/V-KPI——marketing/reports/diagrams/05_keyframe_qa.png)

## Deep Result 沉淀

![Deep Result 沉淀](/Users/bibiboer/Documents/V-KPI——marketing/reports/diagrams/06_deep_result.png)

## URL Deep Crawl

![URL Deep Crawl](/Users/bibiboer/Documents/V-KPI——marketing/reports/diagrams/07_url_deep_crawl.png)

## Product Recall

![Product Recall](/Users/bibiboer/Documents/V-KPI——marketing/reports/diagrams/08_product_recall.png)

## Task Queue 看板

![Task Queue 看板](/Users/bibiboer/Documents/V-KPI——marketing/reports/diagrams/09_task_queue.png)


---

# Mermaid 源码

## 系统总架构

```mermaid
flowchart LR
  UI[V615 React UI] --> API[FastAPI routers]
  API --> Domain[Domain services]
  Domain --> PG[(PostgreSQL)]
  Domain --> Qdrant[(Qdrant vector index)]
  API --> Jobs[(apify_jobs)]
  Worker[apify_jobs_worker] --> Jobs
  Worker --> Gemini[Gemini Flash/Pro]
  Worker --> Cache[(vkpi_analysis_cache)]
  Cache --> Deep[(vkpi_kol_llm_deep_analysis_results)]
  UI --> Deep
```

## KOL Pool 主列表与详情

```mermaid
flowchart TD
  Page[KOLPoolPage] --> ListAPI[GET /kol-pool]
  ListAPI --> Pool[(vkpi_kol_pool)]
  Pool --> Sort[COALESCE(viltrox_fit_score,0) DESC]
  Page --> Drawer[KOLDetailDrawer]
  Drawer --> ItemAPI[GET /kol-pool/{id}]
  Drawer --> D11[GET /dimensions11]
  Drawer --> Deep[GET /llm-deep-analysis]
  Drawer --> Video[KOLVideoAnalysisPanel]
  Video --> Cache[(vkpi_analysis_cache)]
```

## V6 Fit 写口

```mermaid
flowchart TD
  EnrichAPI[POST /kol-pool/{id}/enrich] --> Enrich[pool.enrich_item]
  BatchAPI[POST /kol-pool/batch-enrich] --> Batch[pool.batch_enrich_items]
  WorkerTask[workers/tasks/vkpi.py] --> Enrich
  Daily[daily_sync.py] --> Enrich
  Enrich --> Rule[rule_v0 formula]
  Rule --> Score[(vkpi_kol_pool.viltrox_fit_score)]
  Batch --> Enrich
```

## final_v1 视频深析

```mermaid
flowchart TD
  Button[AI深度分析按钮] --> EnqueueAPI[POST /enqueue-video-analysis]
  EnqueueAPI --> Dedup[ownership + cache/job dedup + budget preflight]
  Dedup --> Job[(apify_jobs queued)]
  Worker[apify_jobs_worker claim SKIP LOCKED] --> Job
  Worker --> Download[resolve/download media via yt-dlp/proxy]
  Download --> Analyzer[gemini_video final_v1]
  Analyzer --> Layers[layer1-6 JSON]
  Layers --> Cache[(vkpi_analysis_cache)]
```

## Keyframe QA

```mermaid
flowchart TD
  FinalV1[(final_v1 cache)] --> Select[select 4-6 frames]
  Select --> Download[download video]
  Download --> FFmpeg[ffmpeg extract frames]
  FFmpeg --> QAModel[QA model checks product/model/brand/competitor/text]
  QAModel --> QAResult[qa_pass/issues/score_correction]
  QAResult --> QACache[(vkpi_analysis_cache derive_method final_v1_keyframe_qa)]
```

## Deep Result 沉淀

```mermaid
flowchart TD
  Cache[(vkpi_analysis_cache final_v1)] --> Script[backfill_kol_llm_deep_analysis_results.py]
  QACache[(keyframe QA cache)] --> Script
  Evidence[(vkpi_kol_video_evidence)] --> Script
  Script --> Extract[marketing_score fallback + recommendations + risk + QA]
  Extract --> Deep[(vkpi_kol_llm_deep_analysis_results)]
  Deep --> Endpoint[GET /llm-deep-analysis]
  Endpoint --> Drawer[LLM深度判断 panel]
```

## URL Deep Crawl

```mermaid
flowchart TD
  Input[粘贴 URL] --> Endpoint[POST /kol-url-deep-crawl]
  Endpoint --> Classify[profile/video/unknown]
  Classify --> Dedup[platform + handle/channel_id dedup]
  Dedup --> DryRun[execute=false preview]
  Dedup --> Execute[execute=true profile flow]
  Execute --> Crawler[youtube/ig/tiktok crawler]
  Crawler --> Writer[profile_basics whitelist writer]
  Writer --> Pool[(vkpi_kol_pool profile fields)]
  Writer --> Run[(vkpi_kol_url_deep_crawl_runs)]
```

## Product Recall

```mermaid
flowchart TD
  Query[35mm use-case query] --> Embed[OpenAI text-embedding-3-small]
  Embed --> Qdrant[(vkpi_kol_profile_index_v1)]
  Qdrant --> Candidates[Top N vector candidates]
  Candidates --> Join[Join index entries profile_type/type_scores]
  Join --> Buckets[creator/reviewer buckets mixed dominant]
  Buckets --> Fuse[0.7 vector + 0.3 type score]
  Fuse --> Panel[ProductRecallPanel 7:3 soft display]
```

## Task Queue 看板

```mermaid
flowchart TD
  Apify[(apify_jobs)] --> QueueAPI[GET /task-queue]
  Ledger[(job_execution_ledger)] --> QueueAPI
  Calls[(vkpi_llm_calls small window)] --> QueueAPI
  QueueAPI --> Map[stage mapping queued/search/thinking/summarizing]
  Map --> Board[TaskProgressBoard]
  Board --> Poll[2.5s poll, pause on hidden, clear on unmount]
```

