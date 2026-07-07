"""市场之声 → 产品线月报(market_voice v0)—— 用户反馈聚类,反哺产品部(账二第 6 层)。

四块输出(全部纯词表/正则聚合已有数据,零新采集、零 LLM、零写库):
  complaints   抱怨聚类:对焦/画质/做工品控/价格/兼容性固件/体积重量 六类,
               规则 = 「话题词 + 负面线索词」同句命中(宁缺毋滥),每类带原声引文 top3;
  wishlist     愿望清单:「wish / hope / please make / would love / 需要 / 求出」词表命中,
               再抽「想要 X 焦段 / X 卡口」(焦段正则复用 focal_matrix 口径,卡口自带正则);
  gaps         需求空白:声量里提到、但我方 vkpi_products 目录零 SKU 的焦段
               (目录焦段口径守卫复用 focal_matrix 的 _load_products + _build_catalog);
  suggestions  给产品部的建议条目:纯规则从上面三块生成,每条带证据计数 + 引文,
               标 method=lexicon_v0 + confidence(low/medium/high 按证据数),绝不编造。

数据源(全只读,逐源诚实标 status;2026-07 实测:comments 777 行 / reply_queue 132 行 /
bh_reviews 0 行 / brand_signal 0 行 / sentiment_results 0 行——空源如实报 empty 不编):
  vkpi_comments            已入库评论(IG/FB 为主,多英文 → 词表双语);
  vkpi_reply_queue         评论区销售员已筛的意向评论(intent_tag: question/price/compat);
  vkpi_bh_reviews          B&H 用户评论(迁移 207 建表,当前空,表存在才读);
  vkpi_brand_signal        品牌需求信号(当前空,表存在才读);
  vkpi_sentiment_results   情感结果(已建未跑,只报计数不参与聚类)。

month 参数:'YYYY-MM' 筛该自然月(UTC);None = 近 30 天。格式非法抛 ValueError(路由转 400)。

compat 约定:SQL 占位符用 ?;SQL 字符串零字面 percent(不用 LIKE,词表匹配全在 Python);
BOOLEAN 读回可能是 int 1/0;JSONB/时间戳读回可能是 str。函数内懒 import get_conn。
兄弟件契约:focal_matrix 复用一律 try/except ImportError 守卫,失败退回自带精简词表。
红线:纯读聚合,绝不写库;零触 viltrox_fit_score、不碰 rule_v0(本模块不产生任何评分,
「影响评分」永久 NO——输出仅供产品部阅读,不回写任何 KOL/SKU 打分链路)。
"""
from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

VOICE_METHOD = "lexicon_v0"

# ── focal_matrix 口径守卫复用(兄弟件契约:ImportError 降级到自带精简词表)──
try:  # pragma: no cover - import guard
    from app.domains.kol.focal_matrix import (
        PRODUCT_LINES as _FM_PRODUCT_LINES,
        _build_catalog as _fm_build_catalog,
        _extract_focals as _fm_extract_focals,
        _extract_zooms as _fm_extract_zooms,
        _load_products as _fm_load_products,
    )

    _FOCAL_MATRIX_AVAILABLE = True
except ImportError:  # pragma: no cover - 降级路径
    _FM_PRODUCT_LINES = None
    _fm_build_catalog = None
    _fm_extract_focals = None
    _fm_extract_zooms = None
    _fm_load_products = None
    _FOCAL_MATRIX_AVAILABLE = False

# 降级用精简产品线词表(与 focal_matrix.PRODUCT_LINES 同结构,仅在 import 失败时启用)
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
# 降级用焦段正则(与 focal_matrix._FOCAL_MM_RE 同口径)
_FALLBACK_FOCAL_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d)?)\s*mm", re.IGNORECASE)
_FALLBACK_ZOOM_RE = re.compile(r"(?<![\d.])(\d{1,3})\s*-\s*(\d{1,3})\s*mm", re.IGNORECASE)
_FOCAL_MIN, _FOCAL_MAX = 6.0, 800.0

# ── 抱怨聚类词表(话题词,英/中双语;评论库实测多英文)──────────────────
COMPLAINT_CATEGORIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("autofocus", "对焦", (
        "autofocus", "auto focus", "focus", "af speed", "hunting", "focus breathing",
        "对焦", "跑焦", "追焦", "呼吸效应",
    )),
    ("image_quality", "画质", (
        "sharp", "soft", "blurry", "image quality", "chromatic", "fringing", "flare",
        "ghosting", "distortion", "vignett", "bokeh",
        "画质", "锐度", "紫边", "眩光", "畸变", "暗角", "虚化",
    )),
    ("build_quality", "做工/品控", (
        "build quality", "build", "quality control", "loose", "rattle", "wobble",
        "broke", "broken", "stopped working", "dead on arrival", "defect",
        "做工", "品控", "松动", "坏了", "故障",
    )),
    ("price", "价格", (
        "price", "expensive", "overpriced", "cost", "价格", "太贵", "贵了", "性价比",
    )),
    ("compatibility", "兼容性/固件", (
        "compatib", "firmware", "not supported", "support for", "doesn't support",
        "does not support", "l-mount", "l mount", "update", "兼容", "固件", "适配", "卡口",
    )),
    ("size_weight", "体积/重量", (
        "heavy", "bulky", "weight", "huge", "太重", "重量", "笨重", "太大",
    )),
)

# 负面线索词:话题词单独出现常是夸赞(实测 "Beautiful sharp images" 是好评),
# 必须再命中一条负面线索才算抱怨——宁漏勿滥,漏的如实留在桶外不硬贴。
NEGATIVE_CUES: tuple[str, ...] = (
    "not ", "n't", "no ", "never", "issue", "problem", "bad", "poor", "terrible",
    "awful", "disappoint", "unfortunately", "lacks", "lack of", "missing", "cannot",
    "can't", "won't", "wont", "doesn't", "does not", "stopped", "stuck", "slow",
    "broken", "broke", "fail", "hunting", "why is", "why no", "still no", "ever?",
    "any firmware", "could've been", "could have been", "too expensive", "overpriced",
    "too heavy", "too big", "refund", "return it",
    "不行", "不好", "不能", "没法", "太贵", "太重", "太大", "问题", "坏了", "差",
    "失望", "跑焦", "拉胯", "😡", "🤦", "👎",
)

# 愿望词表(任务口径:wish / hope / please make / 需要,加保守变体)
WISH_TERMS: tuple[str, ...] = (
    "wish", "hope", "please make", "please release", "please bring", "would love",
    "would like to see", "we need", "i need", "need a", "make a", "make one",
    "can you make", "should make", "when will", "waiting for",
    "希望", "求出", "出个", "出一个", "什么时候出", "快出", "需要",
)

# 卡口正则(自带口径:focal_matrix 无卡口词表可复用,词边界防 "perfect" 里的 rf 误伤)
_MOUNT_RE = re.compile(
    r"\b(?:"
    r"(?:sony\s*)?e[- ]mount|(?:nikon\s*)?z[- ]mount|(?:canon\s*)?rf[- ]mount|"
    r"(?:canon\s*)?ef[- ]mount|(?:fuji(?:film)?\s*)?x[- ]mount|l[- ]mount|"
    r"sony\s+e\b|nikon\s+z\b|canon\s+rf\b|canon\s+ef\b|fuji(?:film)?\s+x\b|"
    r"m43|micro\s*four\s*thirds|mft"
    r")",
    re.IGNORECASE,
)
_MOUNT_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("E 卡口(Sony)", ("e-mount", "e mount", "sony e")),
    ("Z 卡口(Nikon)", ("z-mount", "z mount", "nikon z")),
    ("RF 卡口(Canon)", ("rf-mount", "rf mount", "canon rf")),
    ("EF 卡口(Canon)", ("ef-mount", "ef mount", "canon ef")),
    ("X 卡口(Fuji)", ("x-mount", "x mount", "fuji x", "fujifilm x")),
    ("L 卡口", ("l-mount", "l mount")),
    ("M43 卡口", ("m43", "micro four thirds", "mft")),
)

_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


# ── 小工具(容错约定与 focal_matrix / comment_intel 同款)────────────────


def _text(value: Any, limit: int = 300) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _int0(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _product_lines() -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    if _FOCAL_MATRIX_AVAILABLE and _FM_PRODUCT_LINES:
        return tuple(_FM_PRODUCT_LINES)
    return _FALLBACK_PRODUCT_LINES


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
            logger.debug("focal_matrix 定焦提取失败,退降级正则(best-effort)", exc_info=True)
    return {d for m in _FALLBACK_FOCAL_RE.findall(blob) if (d := _focal_display(m))}


def _extract_zooms(blob: str) -> set[str]:
    if _FOCAL_MATRIX_AVAILABLE and _fm_extract_zooms is not None:
        try:
            return set(_fm_extract_zooms(blob))
        except Exception:  # noqa: BLE001
            logger.debug("focal_matrix 变焦提取失败,退降级正则(best-effort)", exc_info=True)
    out: set[str] = set()
    for lo, hi in _FALLBACK_ZOOM_RE.findall(blob):
        a, b = _focal_display(lo), _focal_display(hi)
        if a and b:
            out.add(a[:-2] + "-" + b)
    return out


def _focal_mm(display: str) -> float:
    try:
        return float(display[:-2])
    except Exception:
        return 0.0


def _matches_any(lower_text: str, terms: tuple[str, ...]) -> bool:
    return any(term in lower_text for term in terms)


def _parse_ts(value: Any) -> datetime | None:
    """时间戳容错解析(compat 层读回多为 ISO str);解不了返回 None(诚实不猜)。"""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _window(month: str | None) -> tuple[str, str, str]:
    """(since_iso, until_iso, label);month 非法抛 ValueError(路由转 400)。"""
    if month:
        token = str(month).strip()
        if not _MONTH_RE.match(token):
            raise ValueError("month 参数格式应为 YYYY-MM,例如 2026-06")
        year, mon = int(token[:4]), int(token[5:7])
        if not (1 <= mon <= 12):
            raise ValueError("month 参数月份非法(01-12)")
        since = datetime(year, mon, 1, tzinfo=timezone.utc)
        last_day = calendar.monthrange(year, mon)[1]
        until = datetime(year, mon, last_day, tzinfo=timezone.utc) + timedelta(days=1)
        return since.isoformat(), until.isoformat(), token + " 自然月(UTC)"
    until_dt = datetime.now(timezone.utc)
    since_dt = until_dt - timedelta(days=30)
    return since_dt.isoformat(), until_dt.isoformat(), "近 30 天"


# ── 数据装载(全只读;占位符 ?,零字面 percent,词表匹配全在 Python)──────


def _table_present(conn: Any, table: str) -> bool:
    try:
        row = conn.execute("SELECT to_regclass(?) AS t", (table,)).fetchone()
        return bool(dict(row or {}).get("t"))
    except Exception:
        return False


def _count_rows(conn: Any, table: str) -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return _int0(dict(row or {}).get("n"))
    except Exception:
        return 0


def _load_comments(conn: Any, since: str, until: str, limit: int = 5000) -> list[dict[str, Any]]:
    """vkpi_comments → 统一声音 dict;窗口按 COALESCE(created_at, fetched_at)
    (实测 49 行 created_at 为空,fetched_at 兜底,不丢声音)。"""
    rows = conn.execute(
        """
        SELECT id, platform, comment_text, author_handle, likes_count,
               language_detected,
               COALESCE(created_at, fetched_at) AS at_ts
        FROM vkpi_comments
        WHERE comment_text IS NOT NULL AND comment_text <> ''
          AND COALESCE(created_at, fetched_at) >= CAST(? AS TIMESTAMPTZ)
          AND COALESCE(created_at, fetched_at) < CAST(? AS TIMESTAMPTZ)
        ORDER BY likes_count DESC NULLS LAST, id DESC
        LIMIT ?
        """,
        (since, until, int(limit)),
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
            "intent_tag": "",
        })
    return out


def _load_intent_queue(conn: Any, since: str, until: str, limit: int = 2000) -> list[dict[str, Any]]:
    """vkpi_reply_queue(评论区销售员已筛意向)→ 统一声音 dict;窗口按入队时间。"""
    rows = conn.execute(
        """
        SELECT id, platform, comment_text, intent_tag, lang, created_at
        FROM vkpi_reply_queue
        WHERE comment_text IS NOT NULL AND comment_text <> ''
          AND created_at >= CAST(? AS TIMESTAMPTZ)
          AND created_at < CAST(? AS TIMESTAMPTZ)
        ORDER BY id DESC
        LIMIT ?
        """,
        (since, until, int(limit)),
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
            "author": "",
            "platform": _text(rec.get("platform"), 30),
            "at": str(rec.get("created_at") or "")[:19],
            "likes": 0,
            "source": "vkpi_reply_queue",
            "intent_tag": _text(rec.get("intent_tag"), 30),
        })
    return out


def _load_bh_reviews(conn: Any, since: str, until: str, limit: int = 2000) -> tuple[list[dict[str, Any]], bool]:
    """vkpi_bh_reviews → 统一声音 dict;表不存在 (rows=[], present=False)。
    review_date/fetched_at 是 TEXT,窗口过滤在 Python 做;两列都解析不了的行保留
    (口碑数据宁可多看,响应 note 里说明口径)。当前真库 0 行 → 诚实空。"""
    if not _table_present(conn, "vkpi_bh_reviews"):
        return [], False
    rows = conn.execute(
        """
        SELECT id, product_name, sku, rating, title, body, author, review_date, fetched_at
        FROM vkpi_bh_reviews
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    since_dt, until_dt = _parse_ts(since), _parse_ts(until)
    out: list[dict[str, Any]] = []
    for r in rows:
        rec = dict(r)
        body = _text(rec.get("body"), 500)
        title = _text(rec.get("title"), 120)
        text = (title + " — " + body).strip(" —") if (title or body) else ""
        if not text:
            continue
        ts = _parse_ts(rec.get("review_date")) or _parse_ts(rec.get("fetched_at"))
        if ts is not None and since_dt is not None and until_dt is not None:
            if not (since_dt <= ts < until_dt):
                continue
        out.append({
            "text": text,
            "lower": text.lower(),
            "author": _text(rec.get("author"), 80),
            "platform": "bh_photo",
            "at": str(rec.get("review_date") or rec.get("fetched_at") or "")[:19],
            "likes": 0,
            "source": "vkpi_bh_reviews",
            "intent_tag": "",
            "rating": rec.get("rating"),
            "product_name": _text(rec.get("product_name"), 120),
        })
    return out, True


def _load_brand_signals(conn: Any, since: str, until: str, limit: int = 500) -> tuple[list[dict[str, Any]], bool]:
    """vkpi_brand_signal 需求信号(表存在才读;当前真库 0 行 → 诚实空)。"""
    if not _table_present(conn, "vkpi_brand_signal"):
        return [], False
    rows = conn.execute(
        """
        SELECT signal_type, brand_name, brand_role, signal_strength, post_url, detected_at
        FROM vkpi_brand_signal
        WHERE detected_at >= CAST(? AS TIMESTAMPTZ)
          AND detected_at < CAST(? AS TIMESTAMPTZ)
        ORDER BY detected_at DESC
        LIMIT ?
        """,
        (since, until, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows], True


def _catalog_focals(conn: Any) -> tuple[set[str], str]:
    """我方目录焦段集合;优先守卫复用 focal_matrix 的目录三视图,失败退自带提取。"""
    if _FOCAL_MATRIX_AVAILABLE and _fm_load_products is not None and _fm_build_catalog is not None:
        try:
            catalog = _fm_build_catalog(_fm_load_products(conn))
            return set(catalog.get("focals") or {}), "focal_matrix._build_catalog(复用)"
        except Exception as exc:  # noqa: BLE001 - 复用失败降级,不炸报告
            logger.warning("market_voice catalog reuse failed, fallback: %s", exc)
    focals: set[str] = set()
    try:
        rows = conn.execute("SELECT sku, model_name FROM vkpi_products", ()).fetchall()
        for r in rows:
            rec = dict(r)
            blob = (str(rec.get("sku") or "") + " " + str(rec.get("model_name") or "")).lower()
            focals |= _extract_focals(blob)
    except Exception:
        return set(), "unavailable"
    return focals, "fallback_regex(自带精简口径)"


# ── 聚类器(纯 Python 词表规则)──────────────────────────────────────────


def _dedupe_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """跨源去重:vkpi_reply_queue 本就筛自 vkpi_comments,同一条原声可能两源都出现;
    按小写正文前 160 字符去重,保留先入列的(comments 源在前,带作者/赞数,信息更全)。"""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for d in docs:
        key = d["lower"][:160]
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _quote(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": _text(doc.get("text"), 220),
        "author": doc.get("author") or "",
        "platform": doc.get("platform") or "",
        "at": doc.get("at") or "",
        "likes": _int0(doc.get("likes")),
        "source": doc.get("source") or "",
    }


def _top_quotes(docs: list[dict[str, Any]], n: int = 3) -> list[dict[str, Any]]:
    """引文排序:赞多优先,同赞按时间新的优先(at 是 ISO 前缀串,字典序即时间序)。"""
    ranked = sorted(docs, key=lambda d: (_int0(d.get("likes")), str(d.get("at") or "")), reverse=True)
    return [_quote(d) for d in ranked[:n]]


def _cluster_complaints(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """抱怨聚类:话题词 + 负面线索词双命中;每类原声引文 top3。"""
    categories: list[dict[str, Any]] = []
    matched_ids: set[int] = set()
    for key, label, topic_terms in COMPLAINT_CATEGORIES:
        hits = [
            d for d in docs
            if _matches_any(d["lower"], topic_terms) and _matches_any(d["lower"], NEGATIVE_CUES)
        ]
        matched_ids.update(id(d) for d in hits)
        categories.append({
            "key": key,
            "label": label,
            "count": len(hits),
            "quotes": _top_quotes(hits, 3),
        })
    categories.sort(key=lambda c: -c["count"])
    total = len(matched_ids)
    if total == 0:
        return {
            "status": "empty",
            "reason": f"窗口内 {len(docs)} 条声音无一同时命中「话题词+负面线索」——词表法宁缺毋滥,不硬贴抱怨标签。",
            "categories": categories,
            "total_matched": 0,
        }
    return {"status": "ready", "total_matched": total, "categories": categories}


def _mount_label(fragment: str) -> str:
    low = fragment.lower().replace("—", "-").strip()
    low = " ".join(low.split())
    for label, aliases in _MOUNT_LABELS:
        if any(a in low for a in aliases):
            return label
    return fragment.strip()


def _cluster_wishlist(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """愿望聚类:wish 词表命中 → 全量清单 + 焦段愿望桶 + 卡口愿望桶(焦段口径复用 focal_matrix)。"""
    wish_docs = [d for d in docs if _matches_any(d["lower"], WISH_TERMS)]
    if not wish_docs:
        return {
            "status": "empty",
            "reason": f"窗口内 {len(docs)} 条声音没命中愿望词表(wish/hope/please make/需要…)——诚实空,不编需求。",
            "total": 0,
            "focal_requests": [],
            "mount_requests": [],
            "items": [],
        }
    focal_buckets: dict[str, list[dict[str, Any]]] = {}
    mount_buckets: dict[str, list[dict[str, Any]]] = {}
    zoom_buckets: dict[str, list[dict[str, Any]]] = {}
    for d in wish_docs:
        zooms = _extract_zooms(d["lower"])
        focals = _extract_focals(d["lower"])
        # 变焦愿望(如 24-70mm)单独成桶,端点焦段不再重复计入定焦愿望
        zoom_endpoints: set[str] = set()
        for z in zooms:
            zoom_buckets.setdefault(z, []).append(d)
            lo, hi = z.split("-", 1)
            zoom_endpoints.add(lo + "mm")
            zoom_endpoints.add(hi)
        for f in focals - zoom_endpoints:
            focal_buckets.setdefault(f, []).append(d)
        for m in _MOUNT_RE.findall(d["lower"]):
            label = _mount_label(m if isinstance(m, str) else str(m))
            if label:
                mount_buckets.setdefault(label, []).append(d)
    focal_requests = [
        {"focal": f, "mm": _focal_mm(f), "count": len(ds), "quotes": _top_quotes(ds, 3)}
        for f, ds in focal_buckets.items()
    ]
    focal_requests.sort(key=lambda x: (-x["count"], x["mm"]))
    zoom_requests = [
        {"range_mm": z, "count": len(ds), "quotes": _top_quotes(ds, 3)}
        for z, ds in zoom_buckets.items()
    ]
    zoom_requests.sort(key=lambda x: -x["count"])
    mount_requests = [
        {"mount": mnt, "count": len(ds), "quotes": _top_quotes(ds, 3)}
        for mnt, ds in mount_buckets.items()
    ]
    mount_requests.sort(key=lambda x: -x["count"])
    return {
        "status": "ready",
        "total": len(wish_docs),
        "focal_requests": focal_requests,
        "zoom_requests": zoom_requests,
        "mount_requests": mount_requests,
        "items": _top_quotes(wish_docs, 10),
    }


def _cluster_gaps(
    docs: list[dict[str, Any]],
    wishlist: dict[str, Any],
    catalog_focals: set[str],
    catalog_basis: str,
) -> dict[str, Any]:
    """需求空白:全量声音里提到的焦段 vs 我方目录焦段(第 3 轮 gaps 思路:有声量+目录空白)。"""
    if not catalog_focals:
        return {
            "status": "empty",
            "reason": "vkpi_products 目录焦段读取失败/为空,空白判断没有基准,诚实不出 gaps。",
            "items": [],
        }
    voice_focals: dict[str, list[dict[str, Any]]] = {}
    for d in docs:
        zooms = _extract_zooms(d["lower"])
        zoom_endpoints: set[str] = set()
        for z in zooms:
            lo, hi = z.split("-", 1)
            zoom_endpoints.add(lo + "mm")
            zoom_endpoints.add(hi)
        for f in _extract_focals(d["lower"]) - zoom_endpoints:
            voice_focals.setdefault(f, []).append(d)
    wish_counts = {
        item["focal"]: _int0(item.get("count"))
        for item in (wishlist.get("focal_requests") or [])
    }
    items = [
        {
            "focal": f,
            "mm": _focal_mm(f),
            "voice_count": len(ds),
            "wish_count": wish_counts.get(f, 0),
            "in_catalog": False,
            "quotes": _top_quotes(ds, 3),
        }
        for f, ds in voice_focals.items()
        if f not in catalog_focals
    ]
    items.sort(key=lambda x: (-x["voice_count"], -x["wish_count"], x["mm"]))
    if not items:
        return {
            "status": "empty",
            "reason": (
                f"窗口内声音提到的焦段({len(voice_focals)} 个)全部在我方目录内({len(catalog_focals)} 个目录焦段)"
                "——本窗口无目录空白焦段声量。"
            ),
            "items": [],
            "catalog_focal_count": len(catalog_focals),
            "catalog_basis": catalog_basis,
        }
    return {
        "status": "ready",
        "items": items[:20],
        "voiced_focal_count": len(voice_focals),
        "catalog_focal_count": len(catalog_focals),
        "catalog_basis": catalog_basis,
    }


def _bucket_product_lines(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按产品线分桶(词表口径守卫复用 focal_matrix.PRODUCT_LINES)。"""
    out: list[dict[str, Any]] = []
    for key, label, terms in _product_lines():
        hits = [d for d in docs if _matches_any(d["lower"], tuple(terms))]
        if key == "af_lens":
            # focal_matrix 同口径:提到具体焦段即视作镜头内容
            extra = [d for d in docs if d not in hits and _extract_focals(d["lower"])]
            hits = hits + extra
        out.append({
            "key": key,
            "label": label,
            "count": len(hits),
            "example": _top_quotes(hits, 1),
        })
    out.sort(key=lambda x: -x["count"])
    return out


def _confidence(evidence_count: int) -> str:
    if evidence_count >= 10:
        return "high"
    if evidence_count >= 3:
        return "medium"
    return "low"


def _build_suggestions(
    complaints: dict[str, Any],
    wishlist: dict[str, Any],
    gaps: dict[str, Any],
) -> dict[str, Any]:
    """给产品部的建议条目:纯规则从三块聚合生成(零 LLM);每条带证据计数+引文+置信档。
    本轮只建判定与展示,不触发任何自动执行动作。"""
    items: list[dict[str, Any]] = []

    # 规则 1:目录空白焦段有愿望声量 → 新品建议
    for gap in (gaps.get("items") or []):
        if _int0(gap.get("wish_count")) >= 1:
            n = _int0(gap.get("voice_count"))
            items.append({
                "kind": "new_product",
                "title": f"新品评估:{gap['focal']} 焦段(目录空白 + 用户点名想要)",
                "detail": f"窗口内 {n} 条声音提到 {gap['focal']},其中 {gap['wish_count']} 条是愿望表达;我方目录该焦段零 SKU。",
                "evidence_count": n,
                "confidence": _confidence(n),
                "quotes": (gap.get("quotes") or [])[:3],
                "method": VOICE_METHOD,
            })
        elif _int0(gap.get("voice_count")) >= 3:
            n = _int0(gap.get("voice_count"))
            items.append({
                "kind": "watch",
                "title": f"声量关注:{gap['focal']} 焦段(目录空白,有讨论声量)",
                "detail": f"窗口内 {n} 条声音提到 {gap['focal']}(非明确愿望),我方目录该焦段零 SKU,建议持续观察。",
                "evidence_count": n,
                "confidence": _confidence(n),
                "quotes": (gap.get("quotes") or [])[:3],
                "method": VOICE_METHOD,
            })

    # 规则 2:目录内焦段的明确愿望 → 迭代建议(如「please make 16mm 1.8 mark II」)
    gap_focals = {g.get("focal") for g in (gaps.get("items") or [])}
    for req in (wishlist.get("focal_requests") or []):
        if req.get("focal") in gap_focals:
            continue  # 已被规则 1 覆盖
        n = _int0(req.get("count"))
        if n >= 1:
            items.append({
                "kind": "iteration",
                "title": f"迭代评估:{req['focal']} 焦段(目录已有,用户点名想要新版本/新卡口)",
                "detail": f"窗口内 {n} 条愿望表达点名 {req['focal']};我方目录已有该焦段,建议评估 II 代或补卡口。",
                "evidence_count": n,
                "confidence": _confidence(n),
                "quotes": (req.get("quotes") or [])[:3],
                "method": VOICE_METHOD,
            })

    # 规则 3:卡口愿望 → 卡口扩展建议
    for req in (wishlist.get("mount_requests") or []):
        n = _int0(req.get("count"))
        if n >= 1:
            items.append({
                "kind": "mount_expansion",
                "title": f"卡口扩展评估:{req['mount']}",
                "detail": f"窗口内 {n} 条愿望表达提到 {req['mount']}。",
                "evidence_count": n,
                "confidence": _confidence(n),
                "quotes": (req.get("quotes") or [])[:3],
                "method": VOICE_METHOD,
            })

    # 规则 4:抱怨类计数达阈值(>=2)→ 改进建议
    for cat in (complaints.get("categories") or []):
        n = _int0(cat.get("count"))
        if n >= 2:
            items.append({
                "kind": "improvement",
                "title": f"改进关注:{cat['label']}(窗口内 {n} 条抱怨)",
                "detail": f"「{cat['label']}」类抱怨 {n} 条(话题词+负面线索双命中),建议产品部复核原声。",
                "evidence_count": n,
                "confidence": _confidence(n),
                "quotes": (cat.get("quotes") or [])[:3],
                "method": VOICE_METHOD,
            })

    items.sort(key=lambda x: -_int0(x.get("evidence_count")))
    if not items:
        return {
            "status": "empty",
            "reason": "窗口内没有任何聚类过阈值(愿望>=1 / 抱怨>=2)——样本不足时诚实不出建议,不编。",
            "items": [],
        }
    return {"status": "ready", "items": items[:20]}


# ── 主入口 ──────────────────────────────────────────────────────────────


def voice_report(month: str | None = None, *, conn: Any = None) -> dict[str, Any]:
    """市场之声月报:评论库 + 意向队列 + B&H 口碑 + 品牌需求信号 → 产品部四块聚合。

    month='YYYY-MM' 筛该自然月(UTC);None = 近 30 天。格式非法抛 ValueError。
    纯读、零 LLM、零外调;每个数据源逐一标 status,空源如实报 empty。
    """
    from app.db.connection import get_conn

    since, until, window_label = _window(month)
    db = conn or get_conn()

    comments = _load_comments(db, since, until)
    intents = _load_intent_queue(db, since, until)
    bh_rows, bh_present = _load_bh_reviews(db, since, until)
    signals, signal_present = _load_brand_signals(db, since, until)
    sentiment_total = _count_rows(db, "vkpi_sentiment_results") if _table_present(db, "vkpi_sentiment_results") else 0

    docs = _dedupe_docs(comments + intents + bh_rows)

    sources = {
        "comments": {
            "table": "vkpi_comments",
            "status": "ready" if comments else "empty",
            "count": len(comments),
        },
        "intent_queue": {
            "table": "vkpi_reply_queue",
            "status": "ready" if intents else "empty",
            "count": len(intents),
            "note": "评论区销售员已筛意向(intent_tag: question/price/compat);窗口按入队时间。",
        },
        "bh_reviews": {
            "table": "vkpi_bh_reviews",
            "status": ("ready" if bh_rows else "empty") if bh_present else "absent",
            "count": len(bh_rows),
            "note": "" if bh_rows else "B&H reviews 表已建(迁移 207)但当前无入库评论——等 reviews actor 喂数,不编。",
        },
        "brand_signal": {
            "table": "vkpi_brand_signal",
            "status": ("ready" if signals else "empty") if signal_present else "absent",
            "count": len(signals),
            "note": "" if signals else "品牌需求信号表当前窗口零行——检测器未产出,如实空。",
        },
        "sentiment": {
            "table": "vkpi_sentiment_results",
            "status": "ready" if sentiment_total else "empty",
            "count": sentiment_total,
            "note": "" if sentiment_total else "情感分析结果表 0 行(管线已建未跑),本报告不含 LLM 情感维度。",
        },
    }

    if not docs:
        return {
            "status": "empty",
            "reason": f"窗口「{window_label}」内三路声音源(评论/意向队列/B&H)均为空——诚实空报告,不编数据。",
            "month": month or None,
            "window": {"since": since, "until": until, "label": window_label},
            "sources": sources,
            "method": VOICE_METHOD,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    complaints = _cluster_complaints(docs)
    wishlist = _cluster_wishlist(docs)
    catalog_focals, catalog_basis = _catalog_focals(db)
    gaps = _cluster_gaps(docs, wishlist, catalog_focals, catalog_basis)
    suggestions = _build_suggestions(complaints, wishlist, gaps)
    line_buckets = _bucket_product_lines(docs)

    # 需求信号(表当前 0 行;有数时按 signal_type 计数展示,纯计数不解读)
    signal_counts: dict[str, int] = {}
    for s in signals:
        st = _text(dict(s).get("signal_type"), 60) or "unknown"
        signal_counts[st] = signal_counts.get(st, 0) + 1

    return {
        "status": "ready",
        "month": month or None,
        "window": {"since": since, "until": until, "label": window_label},
        "sources": sources,
        "sample_size": len(docs),
        "dedup_removed": len(comments) + len(intents) + len(bh_rows) - len(docs),
        "complaints": complaints,
        "wishlist": wishlist,
        "gaps": gaps,
        "suggestions": suggestions,
        "buckets": {
            "product_lines": line_buckets,
            "product_line_basis": (
                "focal_matrix.PRODUCT_LINES(复用)" if _FOCAL_MATRIX_AVAILABLE else "自带精简词表(focal_matrix 不可用降级)"
            ),
        },
        "brand_signal_counts": signal_counts,
        "method": VOICE_METHOD,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "口径:纯词表/正则聚合已有声音数据(双语词表,评论库实测多英文),零 LLM 零新采集;"
            "抱怨=话题词+负面线索双命中,愿望=wish 词表命中,空白=声量焦段减目录焦段;"
            "建议为规则生成(method=lexicon_v0),供产品部人工复核,本模块只做判定与展示、"
            "不执行任何外部动作。独立展示信号,不参与 V6 Fit 评分。"
        ),
    }
