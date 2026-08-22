# 镜头出镜证据重扫配方(LENS_EVIDENCE_RESCAN)

对象:派生表 `vkpi_kol_lens_evidence` / 账本 `vkpi_kol_lens_evidence_scan`(迁移 287)。
写入方唯一:`scripts/ops/backfill_lens_evidence.py`。抽取器 `lens_evidence_v2`
+ 别名表 `lens_aliases_2026_08_v1`(`backend/app/domains/products/product_aliases_lens.py`)。

红线:只写上面两张派生表;绝不触 `viltrox_fit_score` / `rule_v0` / KOL 池 / evidence 列;
零 LLM、零外调;prod 一切「看」的动作都在 `/tmp` 里用 `-B` 跑、不写 `.pyc`。

## 0. 先搞清楚「欠产」是什么(2026-08-22 根因)

| 维度 | prod | 隔离库(vkpi_closeout) |
| --- | --- | --- |
| final_v1 ready 行 | 920(2026-06-16 起的 GCE/Vertex 批) | 570(2026-06-02 三 KOL 全量跑) |
| 代码 | 与本地 `c48dd45e5` 三文件 md5 全等 | 同 |
| derive_method / 结果形状 | `video_analysis_final_v1`,layer1 七键齐全 | 同 |
| v1 抽取器产出 | 96 行 / 73 视频;unresolved 26% | 519 行 / 396 视频;unresolved 4% |

结论:**不是** derive_method 名 / 结果形状 / 解析器目录版本不一致——两边代码与数据形状完全相同,
而且两库的 cache_id / target_id 根本不是同一批视频(0 条内容 md5 相同)。欠产 = 语料 + 抽取器三处盲区:

1. **语料构成**(主因,占 841 条空结果里的 ~79%):prod 的 920 条深析多数是 KOL 审查视频,
   散文本身就写「Viltrox 产品未出现 / products are entirely absent」,`empty_result` 是真相不是漏抽。
2. **仅系列提及被丢**:「Viltrox Pro 系列」「Pro/LAB」「Epics」「Air 系列」——旧版把前导系列词
   当噪声剔掉,整条归零。v2 作为 `lens_key=series:xxx`、`v_relevance=likely` 落表(只认出镜类字段,
   裁决 / 钩子里的「推 Pro 系列」不算证据)。
3. **中文卡口 / 斜杠列表 / 别名**:「Z 卡口 85 1.4」「13mm/23mm/27mm/75mm Pro」「DC-X2/X3」
   「唯卓仕AF 85mm F1.8 XF」「Nexus Focus」「Z2」旧版截不出或归不了;v2 先改写卡口短语再截取、
   拆斜杠列表、过口语别名表。

剩下的 unresolved(prod 重放后 20 条非系列行)是散文点名了目录里**没有**的型号
(「45mm T1.5」「35mm T2.1 LAB」「25mm F1.8」「16mm F1.8 Pro」、Sony 变焦被错挂到 Viltrox)——
这是目录缺口 / LLM 误记,按红线保留原文,不杜撰。

## 1. 本地 / 隔离库验证(每次改抽取器或别名表后)

```bash
cd <repo>
PYTHONPATH=backend .venv/bin/python -m pytest tests/test_lens_evidence_resolver.py tests/test_kol_lens_evidence.py -q
PYTHONPATH=.:scripts:backend APP_ROLE=admin-web ENABLE_SCHEDULER=0 \
  .venv/bin/python scripts/ops/backfill_lens_evidence.py --dry-run --force      # 看统计
PYTHONPATH=.:scripts:backend APP_ROLE=admin-web ENABLE_SCHEDULER=0 \
  .venv/bin/python scripts/ops/backfill_lens_evidence.py --apply --force        # 全量重扫(幂等)
PYTHONPATH=.:scripts:backend APP_ROLE=admin-web ENABLE_SCHEDULER=0 \
  .venv/bin/python scripts/ops/backfill_lens_evidence.py --apply                # 再跑一次必须 considered=0
```

看单条:`... backfill_lens_evidence.py --cache-id 23 --cache-id 30`(打印锚点 / 截取 / 归一 / v_relevance
+ 账本旧结果;永远只读)。

## 2. prod 只读 dry-run + 对照(主会话之外任何人都可以做)

prod 现跑的是旧代码时,用「prod 导出文本 → 本地新代码重放」得到新代码在 prod 会抽到什么:

```bash
# ① prod 侧:导出抽取器实际消费的文本(只读;新代码部署后直接用脚本自带的 --trace-out)
ssh viltrox 'cd /tmp && set -a && source /opt/viltrox-2.0/current/.env; set +a; \
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/viltrox-2.0/current:/opt/viltrox-2.0/current/scripts:/opt/viltrox-2.0/current/backend \
  APP_ROLE=admin-web ENABLE_SCHEDULER=0 /opt/viltrox-2.0/current/.venv/bin/python -B \
  /opt/viltrox-2.0/current/scripts/ops/backfill_lens_evidence.py --dry-run --force --trace-out /tmp/lens_trace_prod.json'
scp viltrox:/tmp/lens_trace_prod.json /tmp/lens_trace_prod.json

# ② 本地:重放(不读缓存表,只读本地目录 vkpi_products)
PYTHONPATH=.:scripts:backend APP_ROLE=admin-web ENABLE_SCHEDULER=0 \
  .venv/bin/python scripts/ops/backfill_lens_evidence.py --replay /tmp/lens_trace_prod.json
```

重放报告里看:`would_write_rows`、`unresolved_pct` / `unresolved_pct_excl_series`、`by_v_relevance`、
`videos_by_v_relevance`、`scan_transitions`(`empty_result->scanned` = 新代码多抽到的视频)、
`gained_samples`(逐条核)。

单条跨库对照:两侧各跑 `--cache-id N --trace-out side.json`,再在任一侧 `--cache-id N --diff other.json`;
`different_cache_content` 表示两库根本不是同一条(本次 prod 与隔离库就是这种情况)。

## 3. prod 重扫(留给主会话;部署新代码之后)

```bash
ssh viltrox
cd /tmp && set -a && source /opt/viltrox-2.0/current/.env; set +a
export PYTHONDONTWRITEBYTECODE=1 APP_ROLE=admin-web ENABLE_SCHEDULER=0
export PYTHONPATH=/opt/viltrox-2.0/current:/opt/viltrox-2.0/current/scripts:/opt/viltrox-2.0/current/backend
PY=/opt/viltrox-2.0/current/.venv/bin/python
SCRIPT=/opt/viltrox-2.0/current/scripts/ops/backfill_lens_evidence.py

$PY -B $SCRIPT --dry-run --force                 # ① 先看:extractor_version 必须是 lens_evidence_v2
$PY -B $SCRIPT --apply --force                   # ② 全量重扫(派生表以本次抽取为准,旧行自动删)
$PY -B $SCRIPT --apply                           # ③ 幂等验收:cache_rows_considered 必须 = 0
```

不需要 `--force` 的情形:只是有新深析缓存进来(账本按缓存 updated_at + 抽取器版本自动判增量)。
抽取器版本升级后,旧账本行 `extractor_version != lens_evidence_v2` 会被自动视为需重扫,
`--force` 只是把「缓存没变、版本也没变」的行也一起重扫(幂等:UPSERT + 删掉本次没再出现的行)。

## 4. 验收 SQL(prod / 隔离库通用;`?` 占位是 compat 方言,psql 里直接写字面值)

```sql
-- 账本全部升到 v2,且没有 unscanned
SELECT extractor_version, scan_status, COUNT(*) FROM vkpi_kol_lens_evidence_scan GROUP BY 1, 2 ORDER BY 1, 2;
SELECT COUNT(*) AS unscanned
FROM vkpi_analysis_cache c LEFT JOIN vkpi_kol_lens_evidence_scan s ON s.cache_id = c.id
WHERE c.derive_method = 'video_analysis_final_v1' AND c.status = 'ready' AND s.cache_id IS NULL;

-- 归一分布 + unresolved 比例(含 / 不含仅系列)
SELECT resolution, COUNT(*) FROM vkpi_kol_lens_evidence GROUP BY 1;
SELECT COUNT(*) FILTER (WHERE lens_key LIKE 'series:%') AS series_only,
       COUNT(*) FILTER (WHERE resolution = 'unresolved' AND lens_key NOT LIKE 'series:%') AS unresolved_models,
       COUNT(*) AS total
FROM vkpi_kol_lens_evidence;

-- unresolved 原文榜(= 目录缺口 / 别名表候选;绝不在表里手改 SKU)
SELECT mention_text, COUNT(*) FROM vkpi_kol_lens_evidence
WHERE resolution = 'unresolved' AND lens_key NOT LIKE 'series:%' GROUP BY 1 ORDER BY 2 DESC LIMIT 30;

-- 派生表行数与账本一致
SELECT SUM(mention_rows) AS ledger_rows, (SELECT COUNT(*) FROM vkpi_kol_lens_evidence) AS table_rows
FROM vkpi_kol_lens_evidence_scan;
```

端点验收:`GET /api/admin/vkpi/lens-insights/summary?scope=all` 的 `summary.v_relevance_rows` /
`summary.v_relevance_videos`(confirmed / likely / none)与 `coverage.videos_without_products`
应与上面 SQL 对上;`GET /api/admin/vkpi/lens-insights/kol/{id}` 的 `videos[]` 每条带
`cache_id` + `v_relevance` + `lenses`,是内容墙接真源的链接。

## 5. 2026-08-22 实跑记录

| 库 | 行数 | sku / family / unresolved | 仅系列 | unresolved% (含 / 不含系列) | 视频 confirmed / likely / none |
| --- | --- | --- | --- | --- | --- |
| 隔离库(已 apply --force,二次 apply considered=0) | 584 | 152 / 361 / 71 | 55 | 12.2 / 3.0 | 317 / 88 / 165 |
| prod(只读重放,未写) | 133(would_write) | 25 / 58 / 50 | 30 | 37.6 / 19.4 | 56 / 36 / 828 |

prod 账本迁移(重放):`empty_result->scanned` 19、`no_evidence->scanned` 4、`scanned->scanned` 69、
其余 828 条仍是真·零提及。

## 6. 别名表维护

`backend/app/domains/products/product_aliases_lens.py` 三张表(LENS_ALIASES / MOUNT_PHRASES /
SERIES_MARKERS)。加一条别名的规矩:canonical 必须是目录里真实存在的家族 / 型号写法;
`tests/test_lens_evidence_resolver.py::test_alias_table_every_row_lands_on_catalog_family` 会逐条过目录夹具,
写错直接红。没把握的说法(目录里没有的型号)不要进表——留在 unresolved 榜上当目录缺口看。
改了别名表或抽取器要同步升 `EXTRACTOR_VERSION` / `ALIAS_TABLE_VERSION`,账本才会自动判定需重扫。
