"""新赛道机会分析(category_tracks v0)—— 「下一个该进的品类/焦段赛道是哪」机会矩阵。

赛道 = 品类维(focal_matrix.PRODUCT_LINES 产品线聚类 + anamorphic 专项拆分)
     × 焦段维(vkpi_products 目录焦段 ∪ 声量里提到的焦段)。

每条赛道算四个决定性信号(纯 SQL/词表规则聚合,零 LLM、零新采集、零写库):
  ① 需求声量 demand   评论+意向队列(近 60 天,前/后 30 天环比)+ 视频证据标题
                      (近 180 天,前/后 90 天环比)中该赛道关键词/焦段的提及量;
                      愿望表达(WISH_TERMS 命中)单列并加权(权重常量 v0 待校准);
  ② 我方覆盖 coverage 我方目录该赛道 SKU 数 + 我方内容声量(证据标题命中赛道
                      且命中 Viltrox 词表的视频数);
  ③ 竞品密度 competitors  brand_pulse 同源竞品词表在该赛道证据里的命中分布:
                      top 品牌份额 + HHI;top 份额>=0.6 且样本>=5 判「垄断」;
  ④ 机会分 opportunity = 100 × demand_norm^Wd × (1-coverage_norm)^Ww × openness^Wo
                      (需求高 × 我方弱 × 竞品未垄断;归一化在各维度内做,
                      全部权重常量标「v0 待校准」,公式原文写进 basis)。

输出:top 机会赛道排序(每条带证据:愿望清单原声引文 / 焦段红格=目录零 SKU 或
我方零声量 / 竞品例证)+「不进」清单(需求低或竞品垄断,诚实给出理由)。

可复用资产(兄弟件契约:一律 try/except 守卫 import,失败退自带精简口径,绝不重造):
  focal_matrix   PRODUCT_LINES / _extract_focals / _load_products / _build_catalog / _product_line_of
  market_voice   WISH_TERMS / _MOUNT_RE / _mount_label / _load_comments / _load_intent_queue
                 / _dedupe_docs / _top_quotes
  brand_pulse    _evidence_rows / _competitor_vocab / _viltrox_terms / _matcher

诚实态:一切数字带 basis/method/confidence;数据不足如实回 empty/低置信,
绝不编造市场数据——只用库内真数据,不联网不引外部行情。
compat 约定:SQL 占位符 ?;SQL 字符串零字面 percent;BOOLEAN 宽容判真;
漂移列走 to_jsonb(t.*)->>'col'。函数内懒 import get_conn。
红线:纯读聚合,绝不写库;零触 viltrox_fit_score、不碰 rule_v0;零迁移零 LLM。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.domains.market import category_tracks_report
from app.domains.market.category_tracks_matching import prepare_keyword_groups

logger = get_logger(__name__)
TRACKS_METHOD = "category_tracks_v0"

# ── 窗口常量(决定性;写进 basis)────────────────────────────────────────
COMMENT_WINDOW_DAYS = 60      # 评论/意向声量窗口(前 30 vs 后 30 环比)
EVIDENCE_WINDOW_DAYS = 180    # 视频证据声量窗口(前 90 vs 后 90 环比)

# ── 机会分权重常量(全部 v0 待校准;公式原文写进 basis)─────────────────
W_DEMAND = 1.0        # 需求项指数(v0 待校准)
W_WEAKNESS = 1.0      # 我方弱项指数(v0 待校准)
W_OPENNESS = 1.0      # 竞品开放度指数(v0 待校准)
WISH_BONUS = 2        # 愿望表达在需求声量里的加权倍数(v0 待校准)
SKU_POINT = 1.0       # 每个我方 SKU 折算覆盖点数(v0 待校准)
VOICE_POINT = 1.0     # 每条我方内容声量视频折算覆盖点数(v0 待校准)
OPPORTUNITY_FORMULA = (
    "opportunity = round(100 × demand_norm^{wd} × (1-coverage_norm)^{ww} × openness^{wo});"
    " demand_total = comment_mentions_recent + evidence_mentions_recent + {wb}×wish_count;"
    " coverage_points = {sp}×sku_count + {vp}×our_voice_videos;"
    " demand_norm/coverage_norm = 各自维度内除以最大值;"
    " openness = 1 - top竞品份额(竞品样本<{mc} 时按 1.0 开放处理并标低置信)"
).format(wd=W_DEMAND, ww=W_WEAKNESS, wo=W_OPENNESS, wb=WISH_BONUS,
         sp=SKU_POINT, vp=VOICE_POINT, mc=3)

# ── 判定阈值(决定性;写进 basis)────────────────────────────────────────
MIN_DEMAND_FOR_OPPORTUNITY = 2   # 需求声量低于此 → 「不进:需求低」
MONOPOLY_TOP_SHARE = 0.6         # top 竞品份额 >= 0.6
MONOPOLY_MIN_SAMPLE = 5          # 且竞品命中样本 >= 5 → 「不进:竞品垄断」
MIN_COMP_SAMPLE = 3              # 竞品样本低于此不判集中度(openness=1.0 低置信)
MIN_TREND_SAMPLE = 2             # 环比样本护栏(与 brand_pulse 同款,小样本不硬判)
TOP_OPPORTUNITIES = 10
MAX_NO_GO = 15
QUOTES_PER_TRACK = 3

# ── focal_matrix 口径守卫复用(ImportError 降级到自带精简词表)──────────
try:  # pragma: no cover - import guard
    from app.domains.kol.focal_matrix import (
        PRODUCT_LINES as _FM_PRODUCT_LINES,
        _build_catalog as _fm_build_catalog,
        _extract_focals as _fm_extract_focals,
        _load_products as _fm_load_products,
        _product_line_of as _fm_product_line_of,
    )

    _FOCAL_MATRIX_AVAILABLE = True
except ImportError:  # pragma: no cover - 降级路径
    _FM_PRODUCT_LINES = None
    _fm_build_catalog = None
    _fm_extract_focals = None
    _fm_load_products = None
    _fm_product_line_of = None
    _FOCAL_MATRIX_AVAILABLE = False

# ── market_voice 口径守卫复用 ────────────────────────────────────────────
try:  # pragma: no cover - import guard
    from app.domains.market.market_voice import (
        WISH_TERMS as _MV_WISH_TERMS,
        _MOUNT_RE as _MV_MOUNT_RE,
        _dedupe_docs as _mv_dedupe_docs,
        _load_comments as _mv_load_comments,
        _load_intent_queue as _mv_load_intent_queue,
        _mount_label as _mv_mount_label,
        _top_quotes as _mv_top_quotes,
    )

    _MARKET_VOICE_AVAILABLE = True
except ImportError:  # pragma: no cover - 降级路径
    _MV_WISH_TERMS = None
    _MV_MOUNT_RE = None
    _mv_dedupe_docs = None
    _mv_load_comments = None
    _mv_load_intent_queue = None
    _mv_mount_label = None
    _mv_top_quotes = None
    _MARKET_VOICE_AVAILABLE = False

# ── brand_pulse 口径守卫复用 ─────────────────────────────────────────────
try:  # pragma: no cover - import guard
    from app.domains.market.brand_pulse import (
        _competitor_vocab as _bp_competitor_vocab,
        _evidence_rows as _bp_evidence_rows,
        _matcher as _bp_matcher,
        _viltrox_terms as _bp_viltrox_terms,
    )

    _BRAND_PULSE_AVAILABLE = True
except ImportError:  # pragma: no cover - 降级路径
    _bp_competitor_vocab = None
    _bp_evidence_rows = None
    _bp_matcher = None
    _bp_viltrox_terms = None
    _BRAND_PULSE_AVAILABLE = False

# ── 降级用精简口径(仅在守卫 import 失败时启用,与真源同结构)────────────
_FALLBACK_PRODUCT_LINES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("af_lens", "AF 自动对焦镜头", ("lens", "prime", "bokeh", "autofocus", "定焦", "镜头", "自动对焦")),
    ("mf_lens", "手动/特种镜头", ("manual focus", "manual lens", "手动对焦", "手动镜头")),
    ("cine", "电影镜头", ("cine lens", "cinema lens", "anamorphic", "电影镜")),
    ("lighting", "灯具/闪光", ("light", "lighting", "flash", "led", "灯", "补光")),
    ("adapter", "转接环/增距", ("adapter", "speed booster", "转接", "增距")),
    ("filter", "滤镜", ("filter", "滤镜")),
    ("monitor", "监视器", ("monitor", "监视器")),
    ("battery", "电池/供电", ("battery", "v-mount", "电池")),
    ("accessory", "配件", ("lens hood", "遮光罩", "extension tube")),
)
_FALLBACK_WISH_TERMS: tuple[str, ...] = (
    "wish", "hope", "please make", "please release", "would love", "we need",
    "i need", "need a", "make a", "when will", "waiting for",
    "希望", "求出", "出个", "什么时候出", "快出", "需要",
)
_FALLBACK_FOCAL_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d)?)\s*mm", re.IGNORECASE)
_FOCAL_MIN, _FOCAL_MAX = 6.0, 800.0

# anamorphic 专项赛道(任务点名;cine 线的子集拆分,词表自带)
_ANAMORPHIC_TRACK = ("anamorphic", "变形宽银幕镜头", ("anamorphic", "变形宽银幕", "宽银幕变形"))


# ── 小工具(容错约定与兄弟件同款)────────────────────────────────────────


def _text(value: Any, limit: int = 300) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _int0(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _parse_day(value: Any) -> date | None:
    raw = str(value or "").strip()
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _product_lines() -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    if _FOCAL_MATRIX_AVAILABLE and _FM_PRODUCT_LINES:
        return tuple(_FM_PRODUCT_LINES)
    return _FALLBACK_PRODUCT_LINES


def _wish_terms() -> tuple[str, ...]:
    if _MARKET_VOICE_AVAILABLE and _MV_WISH_TERMS:
        return tuple(_MV_WISH_TERMS)
    return _FALLBACK_WISH_TERMS


def _focal_display(raw: str) -> str | None:
    try:
        value = float(raw)
    except Exception:
        return None
    if not (_FOCAL_MIN <= value <= _FOCAL_MAX):
        return None
    return (str(int(value)) if value == int(value) else str(value)) + "mm"


def _extract_focals(blob: str) -> set[str]:
    if _FOCAL_MATRIX_AVAILABLE and _fm_extract_focals is not None:
        try:
            return set(_fm_extract_focals(blob))
        except Exception:  # noqa: BLE001 - 复用失败退降级正则,不炸报告
            logger.debug("focal_matrix 提取失败,退降级正则(best-effort)", exc_info=True)
    return {d for m in _FALLBACK_FOCAL_RE.findall(blob) if (d := _focal_display(m))}


def _focal_mm(display: str) -> float:
    try:
        return float(display[:-2])
    except Exception:
        return 0.0


def _matches_any(lower_text: str, terms: tuple[str, ...]) -> bool:
    return any(term in lower_text for term in terms)


def _confidence(evidence_count: int) -> str:
    """证据数 → 置信档(与 market_voice 同阈值:>=10 high / >=3 medium / 其余 low)。"""
    if evidence_count >= 10:
        return "high"
    if evidence_count >= 3:
        return "medium"
    return "low"


def _mom(recent: int, prev: int) -> tuple[str, int]:
    """环比:(trend, mom_pct);平滑防零除,小样本(<MIN_TREND_SAMPLE)不硬判升降。"""
    mom_pct = int(round((recent - prev) / max(prev, 1) * 100))
    ratio = (recent + 1.0) / (prev + 1.0)
    if ratio >= 1.5 and recent >= MIN_TREND_SAMPLE:
        return "rising", mom_pct
    if ratio <= 0.67 and prev >= MIN_TREND_SAMPLE:
        return "falling", mom_pct
    return "stable", mom_pct


def _quote(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": _text(doc.get("text"), 220),
        "author": str(doc.get("author") or ""),
        "platform": str(doc.get("platform") or ""),
        "at": str(doc.get("at") or ""),
        "likes": _int0(doc.get("likes")),
        "source": str(doc.get("source") or ""),
    }


def _top_quotes(docs: list[dict[str, Any]], n: int = QUOTES_PER_TRACK) -> list[dict[str, Any]]:
    if _MARKET_VOICE_AVAILABLE and _mv_top_quotes is not None:
        try:
            return _mv_top_quotes(docs, n)
        except Exception:  # noqa: BLE001
            logger.debug("market_voice top_quotes 复用失败,退本地排序(best-effort)", exc_info=True)
    ranked = sorted(docs, key=lambda d: (_int0(d.get("likes")), str(d.get("at") or "")), reverse=True)
    return [_quote(d) for d in ranked[:n]]


# ── 数据装载(全只读;优先守卫复用兄弟件 loader,失败退自带 SQL)────────


def _load_voice_docs(conn: Any, since: str, until: str) -> list[dict[str, Any]]:
    """评论 + 意向队列 → 统一声音 dict(market_voice 同款结构);复用失败退自带 SQL。"""
    if _MARKET_VOICE_AVAILABLE and _mv_load_comments is not None and _mv_load_intent_queue is not None:
        try:
            docs = _mv_load_comments(conn, since, until) + _mv_load_intent_queue(conn, since, until)
            if _mv_dedupe_docs is not None:
                return _mv_dedupe_docs(docs)
            return docs
        except Exception as exc:  # noqa: BLE001 - 复用失败降级,不炸报告
            logger.warning("category_tracks voice-doc reuse failed, fallback: %s", exc)
    rows = conn.execute(
        """
        SELECT id, platform, comment_text, author_handle, likes_count,
               COALESCE(created_at, fetched_at) AS at_ts
        FROM vkpi_comments
        WHERE comment_text IS NOT NULL AND comment_text <> ''
          AND COALESCE(created_at, fetched_at) >= CAST(? AS TIMESTAMPTZ)
          AND COALESCE(created_at, fetched_at) < CAST(? AS TIMESTAMPTZ)
        ORDER BY likes_count DESC NULLS LAST, id DESC
        LIMIT 5000
        """,
        (since, until),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        rec = dict(r)
        text = _text(rec.get("comment_text"), 500)
        if not text:
            continue
        out.append({
            "text": text,
            "lower": text.lower(),
            "author": _text(rec.get("author_handle"), 80),
            "platform": _text(rec.get("platform"), 30),
            "at": str(rec.get("at_ts") or "")[:19],
            "likes": _int0(rec.get("likes_count")),
            "source": "vkpi_comments",
        })
    return out


def _load_evidence(conn: Any, start_day: str) -> list[dict[str, Any]]:
    """全池 active 证据行(标题+日期+播放);复用 brand_pulse._evidence_rows,失败退自带 SQL。"""
    if _BRAND_PULSE_AVAILABLE and _bp_evidence_rows is not None:
        try:
            return _bp_evidence_rows(conn, start_day)
        except Exception as exc:  # noqa: BLE001
            logger.warning("category_tracks evidence reuse failed, fallback: %s", exc)
    rows = conn.execute(
        """
        SELECT
            e.id AS evidence_id,
            e.kol_pool_id,
            e.platform,
            e.content_url,
            e.view_count,
            COALESCE(e.video_title, '') AS video_title,
            to_jsonb(e.*)->>'title' AS title_alt,
            CAST(COALESCE(e.posted_at, CAST(to_jsonb(e.*)->>'publish_date' AS DATE)) AS TEXT) AS pub_day,
            COALESCE(p.display_name, '') AS kol_name
        FROM vkpi_kol_video_evidence e
        LEFT JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE e.is_active IS NOT FALSE
          AND COALESCE(e.posted_at, CAST(to_jsonb(e.*)->>'publish_date' AS DATE)) >= ?
        ORDER BY e.id DESC
        LIMIT 20000
        """,
        (start_day,),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_catalog(conn: Any) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """(catalog三视图, 产品行, basis);优先复用 focal_matrix 真源,失败诚实空。"""
    if _FOCAL_MATRIX_AVAILABLE and _fm_load_products is not None and _fm_build_catalog is not None:
        try:
            products = _fm_load_products(conn)
            return _fm_build_catalog(products), products, "focal_matrix._build_catalog(复用,369 SKU 目录口径)"
        except Exception as exc:  # noqa: BLE001
            logger.warning("category_tracks catalog reuse failed: %s", exc)
    try:
        rows = conn.execute(
            "SELECT sku, series, category_main, category_detail, model_name, marketing_name, price_usd, status FROM vkpi_products",
            (),
        ).fetchall()
        products = [dict(r) for r in rows]
    except Exception:
        return {"focals": {}, "lines": {}, "families": {}}, [], "unavailable(目录读取失败)"
    # 降级:只做粗聚类(焦段提取自 SKU/型号文本;线分类回退 accessory)
    focals: dict[str, dict[str, Any]] = {}
    lines: dict[str, dict[str, Any]] = {}
    for row in products:
        blob = (str(row.get("sku") or "") + " " + str(row.get("model_name") or "")).lower()
        for f in _extract_focals(blob):
            focals.setdefault(f, {"sku_count": 0})["sku_count"] += 1
        lines.setdefault("accessory", {"sku_count": 0, "example": ""})["sku_count"] += 1
    return {"focals": focals, "lines": lines, "families": {}}, products, "fallback_regex(focal_matrix 不可用降级)"


def _competitor_vocab() -> dict[str, dict[str, Any]]:
    if _BRAND_PULSE_AVAILABLE and _bp_competitor_vocab is not None:
        try:
            return _bp_competitor_vocab()
        except Exception:  # noqa: BLE001
            logger.debug("brand_pulse 竞品词表读取失败,退空表(best-effort)", exc_info=True)
    return {}


def _viltrox_terms() -> list[str]:
    if _BRAND_PULSE_AVAILABLE and _bp_viltrox_terms is not None:
        try:
            return list(_bp_viltrox_terms())
        except Exception:  # noqa: BLE001
            logger.debug("brand_pulse viltrox 词表读取失败,退默认词(best-effort)", exc_info=True)
    return ["viltrox"]


def _matcher():
    if _BRAND_PULSE_AVAILABLE and _bp_matcher is not None:
        try:
            return _bp_matcher()
        except Exception:  # noqa: BLE001
            logger.debug("brand_pulse matcher 构造失败,退子串匹配(best-effort)", exc_info=True)
    return lambda text, kw: str(kw or "").lower() in str(text or "").lower()


# ── 赛道定义 ─────────────────────────────────────────────────────────────


def _category_tracks_def() -> list[tuple[str, str, tuple[str, ...]]]:
    """品类维赛道 = focal_matrix 产品线 + anamorphic 专项(cine 子集拆分,响应 note 说明)。"""
    out = [(key, label, tuple(terms)) for key, label, terms in _product_lines()]
    out.append(_ANAMORPHIC_TRACK)
    return out


def _catalog_line_sku_count(catalog: dict[str, Any], products: list[dict[str, Any]], key: str) -> int:
    """品类赛道的我方 SKU 数;anamorphic = cine 线里名称含 anamorphic 的 SKU 数。"""
    if key == "anamorphic":
        count = 0
        for row in products:
            name = (str(row.get("model_name") or "") + " " + str(row.get("marketing_name") or "")).lower()
            line = ""
            if _FOCAL_MATRIX_AVAILABLE and _fm_product_line_of is not None:
                try:
                    line = _fm_product_line_of(row)
                except Exception:  # noqa: BLE001
                    line = ""
            if "anamorphic" in name and (line == "cine" or not line):
                count += 1
        return count
    entry = (catalog.get("lines") or {}).get(key) or {}
    return _int0(entry.get("sku_count"))


# ── 逐赛道四信号计算 ─────────────────────────────────────────────────────


def _prep_docs(docs: list[dict[str, Any]], mid_day: date) -> list[dict[str, Any]]:
    """声音 doc 预处理:焦段集合 + 愿望旗标 + 前/后半窗归属(日期解析不了不进环比)。"""
    wish_terms = _wish_terms()
    out = []
    for d in docs:
        day = _parse_day(d.get("at"))
        half = None if day is None else ("recent" if day >= mid_day else "prev")
        out.append({
            **d,
            "focals": _extract_focals(d["lower"]),
            "is_wish": _matches_any(d["lower"], wish_terms),
            "half": half,
        })
    return out


def _prep_evidence(rows: list[dict[str, Any]], mid_day: date, start_day: date, end_day: date) -> list[dict[str, Any]]:
    """证据行预处理:标题 blob + 焦段集合 + Viltrox 旗标 + 竞品命中品牌集 + 半窗归属。"""
    brand_keywords, viltrox_terms, match = prepare_keyword_groups(
        _competitor_vocab(), _viltrox_terms()
    )
    out = []
    for row in rows:
        day = _parse_day(row.get("pub_day"))
        if day is None or day < start_day or day > end_day:
            continue
        title = _text(row.get("video_title"), 200) or _text(row.get("title_alt"), 200)
        blob = (str(row.get("video_title") or "") + " " + str(row.get("title_alt") or "")).lower()
        if not blob.strip():
            continue
        brand_hits = {
            brand for brand, keywords in brand_keywords.items()
            if any(match(blob, keyword) for keyword in keywords)
        }
        out.append({
            "title": title,
            "blob": blob,
            "focals": _extract_focals(blob),
            "viltrox": any(match(blob, term) for term in viltrox_terms),
            "brands": brand_hits,
            "half": "recent" if day >= mid_day else "prev",
            "view_count": row.get("view_count"),
            "content_url": str(row.get("content_url") or ""),
            "kol_name": str(row.get("kol_name") or ""),
        })
    return out


def _competitor_block(track_evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """③ 竞品密度:该赛道证据里竞品命中分布 → top 份额 + HHI + 垄断判定 + 例证。"""
    brand_counts: dict[str, int] = {}
    brand_example: dict[str, dict[str, Any]] = {}
    for ev in track_evidence:
        for brand in ev["brands"]:
            brand_counts[brand] = brand_counts.get(brand, 0) + 1
            best = brand_example.get(brand)
            if best is None or (_int0(ev.get("view_count")) > _int0(best.get("view_count"))):
                brand_example[brand] = ev
    total = sum(brand_counts.values())
    if total == 0:
        return {
            "status": "empty",
            "reason": "该赛道证据里竞品词表零命中——竞品密度无从计算,openness 按 1.0 开放处理(低置信)。",
            "total_mentions": 0, "brand_count": 0, "top_brand": None,
            "top_share": None, "hhi": None, "monopoly": False, "openness": 1.0,
            "sample_sufficient": False, "example": None,
        }
    top_brand, top_count = max(brand_counts.items(), key=lambda kv: (kv[1], kv[0]))
    top_share = round(top_count / total, 3)
    hhi = round(sum((c / total) ** 2 for c in brand_counts.values()), 3)
    sample_sufficient = total >= MIN_COMP_SAMPLE
    monopoly = sample_sufficient and total >= MONOPOLY_MIN_SAMPLE and top_share >= MONOPOLY_TOP_SHARE
    ex = brand_example.get(top_brand)
    return {
        "status": "ready",
        "total_mentions": total,
        "brand_count": len(brand_counts),
        "top_brand": top_brand,
        "top_share": top_share,
        "hhi": hhi,
        "monopoly": monopoly,
        "openness": round(1.0 - top_share, 3) if sample_sufficient else 1.0,
        "sample_sufficient": sample_sufficient,
        "example": {
            "brand": top_brand,
            "title": (ex or {}).get("title") or "",
            "view_count": (ex or {}).get("view_count"),
            "kol_name": (ex or {}).get("kol_name") or "",
            "content_url": (ex or {}).get("content_url") or "",
        } if ex else None,
    }


def _signals_for(
    track_docs: list[dict[str, Any]],
    track_evidence: list[dict[str, Any]],
    sku_count: int,
) -> dict[str, Any]:
    """①②③ 原始信号(④ 机会分要等全维度归一,主入口再算)。"""
    c_recent = sum(1 for d in track_docs if d["half"] == "recent")
    c_prev = sum(1 for d in track_docs if d["half"] == "prev")
    c_undated = sum(1 for d in track_docs if d["half"] is None)
    e_recent = sum(1 for ev in track_evidence if ev["half"] == "recent")
    e_prev = sum(1 for ev in track_evidence if ev["half"] == "prev")
    wish_docs = [d for d in track_docs if d["is_wish"]]
    c_trend, c_mom = _mom(c_recent, c_prev)
    e_trend, e_mom = _mom(e_recent, e_prev)
    demand_total = c_recent + e_recent + WISH_BONUS * len(wish_docs)
    our_voice = [ev for ev in track_evidence if ev["viltrox"]]
    coverage_points = SKU_POINT * sku_count + VOICE_POINT * len(our_voice)
    return {
        "demand": {
            "total": int(demand_total),
            "comment_mentions": len(track_docs),
            "comment_recent": c_recent,
            "comment_prev": c_prev,
            "comment_undated": c_undated,
            "comment_trend": c_trend,
            "comment_mom_pct": c_mom,
            "evidence_mentions": len(track_evidence),
            "evidence_recent": e_recent,
            "evidence_prev": e_prev,
            "evidence_trend": e_trend,
            "evidence_mom_pct": e_mom,
            "wish_count": len(wish_docs),
            "wish_quotes": _top_quotes(wish_docs),
            "voice_quotes": _top_quotes(track_docs) if not wish_docs else [],
        },
        "coverage": {
            "sku_count": int(sku_count),
            "our_voice_videos": len(our_voice),
            "points": round(coverage_points, 2),
        },
        "competitors": _competitor_block(track_evidence),
        "confidence": _confidence(len(track_docs) + len(track_evidence)),
    }


def _finalize_dimension(items: list[dict[str, Any]]) -> None:
    """④ 机会分:维度内归一(demand/coverage 各除以维度最大值)后按公式打分,原地写回。"""
    max_demand = max((it["demand"]["total"] for it in items), default=0)
    max_coverage = max((it["coverage"]["points"] for it in items), default=0.0)
    for it in items:
        demand_norm = (it["demand"]["total"] / max_demand) if max_demand > 0 else 0.0
        coverage_norm = (it["coverage"]["points"] / max_coverage) if max_coverage > 0 else 0.0
        openness = float(it["competitors"].get("openness") or 0.0)
        score = 100.0 * (demand_norm ** W_DEMAND) * ((1.0 - coverage_norm) ** W_WEAKNESS) * (openness ** W_OPENNESS)
        it["demand"]["norm"] = round(demand_norm, 3)
        it["coverage"]["norm"] = round(coverage_norm, 3)
        it["opportunity"] = {
            "score": int(round(score)),
            "demand_norm": round(demand_norm, 3),
            "weakness": round(1.0 - coverage_norm, 3),
            "openness": round(openness, 3),
            "confidence": it["confidence"],
            "basis": OPPORTUNITY_FORMULA,
        }


def _no_go_reason(item: dict[str, Any]) -> str | None:
    if item["competitors"].get("monopoly"):
        top = item["competitors"]
        return (
            f"竞品垄断:{top.get('top_brand')} 占该赛道竞品声量 "
            f"{round(float(top.get('top_share') or 0) * 100)}%(样本 {top.get('total_mentions')} 条,"
            f"阈值 top份额>={int(MONOPOLY_TOP_SHARE * 100)}% 且样本>={MONOPOLY_MIN_SAMPLE})"
        )
    if item["demand"]["total"] < MIN_DEMAND_FOR_OPPORTUNITY:
        return (
            f"需求低:窗口内需求声量 {item['demand']['total']}"
            f"(评论近半窗 {item['demand']['comment_recent']} + 证据近半窗 {item['demand']['evidence_recent']}"
            f" + 愿望×{WISH_BONUS}),低于阈值 {MIN_DEMAND_FOR_OPPORTUNITY}"
        )
    return None


def _evidence_bundle(item: dict[str, Any]) -> dict[str, Any]:
    """机会条目的三类证据:愿望原声 / 焦段红格(覆盖弱点)/ 竞品例证。诚实缺席不硬编。"""
    cov = item["coverage"]
    red_flags: list[str] = []
    if cov["sku_count"] == 0:
        red_flags.append("目录零 SKU(焦段红格:该赛道我方无产品)")
    if cov["our_voice_videos"] == 0:
        red_flags.append("我方内容零声量(证据标题无 Viltrox×该赛道命中)")
    comp = item["competitors"]
    return {
        "wish_quotes": item["demand"].get("wish_quotes") or [],
        "voice_quotes": item["demand"].get("voice_quotes") or [],
        "coverage_red_flags": red_flags,
        "competitor_example": comp.get("example"),
        "competitor_note": (
            f"top 竞品 {comp.get('top_brand')} 份额 {round(float(comp.get('top_share') or 0) * 100)}%"
            f"(命中 {comp.get('total_mentions')} 条,HHI {comp.get('hhi')})"
            if comp.get("status") == "ready" else str(comp.get("reason") or "")
        ),
    }


# ── 主入口 ──────────────────────────────────────────────────────────────


def tracks(*, conn: Any = None) -> dict[str, Any]:
    """新赛道机会矩阵:品类维 × 焦段维,每赛道四个决定性信号 + top 机会/不进清单。

    纯 SQL/词表规则聚合库内真数据,零 LLM 零外调零写库;一切数字带 basis/method/
    confidence;数据不足诚实 empty/低置信,绝不编造市场数据。
    """
    from app.db.connection import get_conn

    db = conn or get_conn()
    now = datetime.now(timezone.utc)
    today = now.date()

    # 窗口:评论 60 天(前/后 30 环比)、证据 180 天(前/后 90 环比)
    c_since = (now - timedelta(days=COMMENT_WINDOW_DAYS)).isoformat()
    c_until = now.isoformat()
    c_mid = today - timedelta(days=COMMENT_WINDOW_DAYS // 2)
    e_start = today - timedelta(days=EVIDENCE_WINDOW_DAYS - 1)
    e_mid = today - timedelta(days=EVIDENCE_WINDOW_DAYS // 2)

    raw_docs = _load_voice_docs(db, c_since, c_until)
    raw_evidence = _load_evidence(db, e_start.isoformat())
    catalog, products, catalog_basis = _load_catalog(db)

    docs = _prep_docs(raw_docs, c_mid)
    evidence = _prep_evidence(raw_evidence, e_mid, e_start, today)

    windows = {
        "comments": {"days": COMMENT_WINDOW_DAYS, "since": c_since[:10], "until": c_until[:10],
                     "halves": f"前/后 {COMMENT_WINDOW_DAYS // 2} 天环比"},
        "evidence": {"days": EVIDENCE_WINDOW_DAYS, "since": e_start.isoformat(), "until": today.isoformat(),
                     "halves": f"前/后 {EVIDENCE_WINDOW_DAYS // 2} 天环比"},
    }
    sources = {
        "voice_docs": len(docs),
        "evidence_rows": len(evidence),
        "catalog_skus": len(products),
        "catalog_basis": catalog_basis,
        "vocab_reuse": {
            "focal_matrix": _FOCAL_MATRIX_AVAILABLE,
            "market_voice": _MARKET_VOICE_AVAILABLE,
            "brand_pulse": _BRAND_PULSE_AVAILABLE,
        },
    }

    if not docs and not evidence:
        return {
            "status": "empty",
            "reason": "窗口内评论/意向声音与视频证据均为零——四信号无一可算,诚实空态,不编造市场数据。",
            "method": TRACKS_METHOD,
            "windows": windows,
            "sources": sources,
            "generated_at": now.isoformat(),
        }

    category_items, focal_items = category_tracks_report.build_track_dimensions(
        docs,
        evidence,
        catalog,
        products,
        globals(),
    )

    # ── top 机会排序 +「不进」清单(诚实给理由)──
    opportunities, no_go = category_tracks_report.rank_tracks(
        category_items + focal_items,
        globals(),
    )

    # ── 卡口愿望信号(辅助块:wishlist 有 L 卡口这类信号;目录卡口口径未建,不硬算覆盖)──
    mount_signals = category_tracks_report.mount_signals(docs, globals())

    return {
        "status": "ready",
        "method": TRACKS_METHOD,
        "windows": windows,
        "sources": sources,
        "basis": {
            "formula": OPPORTUNITY_FORMULA,
            "weights": {
                "W_DEMAND": W_DEMAND, "W_WEAKNESS": W_WEAKNESS, "W_OPENNESS": W_OPENNESS,
                "WISH_BONUS": WISH_BONUS, "SKU_POINT": SKU_POINT, "VOICE_POINT": VOICE_POINT,
                "note": "全部权重常量为 v0 待校准(未经效果回测,仅保证决定性可复现)",
            },
            "thresholds": {
                "min_demand_for_opportunity": MIN_DEMAND_FOR_OPPORTUNITY,
                "monopoly_top_share": MONOPOLY_TOP_SHARE,
                "monopoly_min_sample": MONOPOLY_MIN_SAMPLE,
                "min_competitor_sample": MIN_COMP_SAMPLE,
            },
            "normalization": "demand_norm/coverage_norm 在各自维度(品类维/焦段维)内除以维度最大值,跨维度分数可比性有限,排序仅供优先级参考",
        },
        "category_tracks": category_items,
        "focal_tracks": focal_items,
        "opportunities": opportunities[:TOP_OPPORTUNITIES],
        "no_go": no_go[:MAX_NO_GO],
        "mount_signals": mount_signals,
        "generated_at": now.isoformat(),
        "note": (
            "口径:纯词表/正则聚合库内真数据(评论+意向队列近 60 天、证据标题近 180 天、"
            "vkpi_products 目录),零 LLM 零联网零外部行情;anamorphic 为 cine 线子集专项拆分,"
            "两赛道计数会重叠;竞品密度基于证据标题词表命中,非市场份额;机会分权重 v0 待校准。"
            "独立展示信号,不参与 V6 Fit 评分,不回写任何评分链路。"
        ),
    }
