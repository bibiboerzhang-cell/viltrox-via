"""受众地图 v1(geo_ensemble)—— 多信号融合的受众地理估算,替换「创作者国别代理@100%」假信号。

四路信号(全部纯读已有数据,零新采集、零 LLM、零外调):
  ① comment_language          评论语言分布:读 vkpi_comments.language_detected 列(B1 回填中);
                              列全空就诚实标 signal 缺席,绝不在这里重算文本(口径归 B1)。
  ② commenter_script          评论者 ID/名字的文字系统启发:author_handle + raw owner.full_name,
                              按 unicode 块判 拉丁/西里尔/CJK(汉字/假名/谚文)/阿拉伯 等字系;
                              拉丁字系国别不可分,只报占比、不硬编国家。
  ③ creator_country           创作者自报国别:vkpi_kol_pool.country,降权为弱先验(代理口径)。
  ④ commenter_profile_country 评论者身份缓存 vkpi_commenter_profiles.country(有列就用);
     activity_timezone        评论时间 UTC 小时分布 → 粗时区倾向(纯侧信号,weight=0,不入融合)。

每信号独立出分布 + 权重,融合成 top 国家分布 + confidence:
  high   = 2+ 受众信号 ready 且 top 国家相互印证;
  medium = 单一强受众信号;
  low    = 仅创作者国别代理(或全无)—— 前端必须挂「代理口径」角标。
payload 恒带 geo_source 与 signals_used,绝不把代理口径伪装成受众实测。

compat 约定:SQL 占位符 ?;SQL 禁 percent 字符(不用 LIKE,词表匹配拉回 Python 做);
BOOLEAN 读回可能是 int;JSONB 双态 _loads 容错。函数内懒 import get_conn。
红线:纯读聚合,绝不写库;零触 viltrox_fit_score、不碰 rule_v0;不动既有
audience_stats/ensemble 文件(本模块是独立读端新域)。
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── 信号权重(仅 ready 信号参与,融合时按实际在场重归一)────────────────────
SIGNAL_WEIGHTS: dict[str, float] = {
    "comment_language": 0.35,
    "commenter_script": 0.25,
    "commenter_profile_country": 0.30,
    "creator_country": 0.10,
}
STRONG_SAMPLE_MIN = 5          # 受众信号至少几个样本才算「强」
COMMENT_LIMIT = 1500           # 每 KOL 最多读多少条评论(读端保护)
EVIDENCE_LIMIT = 100

# ── 语言 → 国家先验权重(粗口径:主流市场拆分,总和=1;宁粗勿假)──────────────
LANG_COUNTRY_WEIGHTS: dict[str, tuple[tuple[str, float], ...]] = {
    "en": (("US", 0.50), ("GB", 0.20), ("CA", 0.15), ("AU", 0.15)),
    "es": (("MX", 0.30), ("ES", 0.30), ("AR", 0.20), ("CO", 0.20)),
    "pt": (("BR", 0.80), ("PT", 0.20)),
    "de": (("DE", 0.70), ("AT", 0.15), ("CH", 0.15)),
    "fr": (("FR", 0.70), ("CA", 0.15), ("BE", 0.15)),
    "it": (("IT", 1.0),),
    "id": (("ID", 1.0),),
    "tr": (("TR", 1.0),),
    "nl": (("NL", 0.80), ("BE", 0.20)),
    "pl": (("PL", 1.0),),
    "vi": (("VN", 1.0),),
    "zh": (("CN", 0.60), ("TW", 0.25), ("HK", 0.15)),
    "ja": (("JP", 1.0),),
    "ko": (("KR", 1.0),),
    "ru": (("RU", 0.80), ("UA", 0.20)),
    "ar": (("SA", 0.30), ("EG", 0.25), ("AE", 0.15), ("MA", 0.15), ("JO", 0.15)),
    "th": (("TH", 1.0),),
    "hi": (("IN", 1.0),),
    "he": (("IL", 1.0),),
}

# ── 文字系统 → 国家先验(拉丁不在表内=国别不可分,只报占比)─────────────────
SCRIPT_COUNTRY_WEIGHTS: dict[str, tuple[tuple[str, float], ...]] = {
    "cyrillic": (("RU", 0.70), ("UA", 0.20), ("KZ", 0.10)),
    "arabic": (("SA", 0.30), ("EG", 0.25), ("AE", 0.15), ("MA", 0.15), ("IQ", 0.15)),
    "han": (("CN", 0.60), ("TW", 0.25), ("HK", 0.15)),
    "kana": (("JP", 1.0),),
    "hangul": (("KR", 1.0),),
    "thai": (("TH", 1.0),),
    "devanagari": (("IN", 1.0),),
    "hebrew": (("IL", 1.0),),
    "greek": (("GR", 1.0),),
}
SCRIPT_LABELS: dict[str, str] = {
    "latin": "拉丁", "cyrillic": "西里尔", "arabic": "阿拉伯", "han": "汉字",
    "kana": "日文假名", "hangul": "谚文", "thai": "泰文", "devanagari": "天城文",
    "hebrew": "希伯来", "greek": "希腊",
}
# unicode 块 → 文字系统(非拉丁一眼可判;拉丁含扩展)
_SCRIPT_RANGES: tuple[tuple[str, int, int], ...] = (
    ("han", 0x4E00, 0x9FFF), ("han", 0x3400, 0x4DBF),
    ("kana", 0x3040, 0x30FF), ("kana", 0x31F0, 0x31FF),
    ("hangul", 0xAC00, 0xD7AF), ("hangul", 0x1100, 0x11FF), ("hangul", 0x3130, 0x318F),
    ("cyrillic", 0x0400, 0x052F),
    ("arabic", 0x0600, 0x06FF), ("arabic", 0x0750, 0x077F), ("arabic", 0x08A0, 0x08FF),
    ("arabic", 0xFB50, 0xFDFF), ("arabic", 0xFE70, 0xFEFF),
    ("thai", 0x0E00, 0x0E7F),
    ("devanagari", 0x0900, 0x097F),
    ("hebrew", 0x0590, 0x05FF),
    ("greek", 0x0370, 0x03FF),
    ("latin", 0x0041, 0x005A), ("latin", 0x0061, 0x007A), ("latin", 0x00C0, 0x024F),
)

# ── 创作者国别列的中文名 → ISO2(常见值枚举;两位大写码直接放行;未知保原文)──
CN_NAME_TO_ISO: dict[str, str] = {
    "美国": "US", "英国": "GB", "加拿大": "CA", "德国": "DE", "意大利": "IT",
    "西班牙": "ES", "日本": "JP", "澳大利亚": "AU", "法国": "FR", "泰国": "TH",
    "巴西": "BR", "菲律宾": "PH", "韩国": "KR", "阿联酋": "AE", "越南": "VN",
    "俄罗斯": "RU", "阿根廷": "AR", "瑞典": "SE", "墨西哥": "MX", "哥伦比亚": "CO",
    "瑞士": "CH", "比利时": "BE", "中国": "CN", "中国台湾": "TW", "台湾": "TW",
    "中国香港": "HK", "香港": "HK", "印度": "IN", "印度尼西亚": "ID", "印尼": "ID",
    "荷兰": "NL", "波兰": "PL", "葡萄牙": "PT", "土耳其": "TR", "沙特阿拉伯": "SA",
    "沙特": "SA", "埃及": "EG", "以色列": "IL", "乌克兰": "UA", "奥地利": "AT",
    "挪威": "NO", "丹麦": "DK", "芬兰": "FI", "爱尔兰": "IE", "新西兰": "NZ",
    "新加坡": "SG", "马来西亚": "MY", "希腊": "GR", "捷克": "CZ", "罗马尼亚": "RO",
    "匈牙利": "HU", "南非": "ZA", "尼日利亚": "NG", "摩洛哥": "MA", "智利": "CL",
    "秘鲁": "PE", "巴基斯坦": "PK", "孟加拉国": "BD", "斯洛伐克": "SK", "冰岛": "IS",
    "卡塔尔": "QA", "科威特": "KW", "约旦": "JO", "伊拉克": "IQ", "哈萨克斯坦": "KZ",
    # 真实库里出现过的英文名/城市名/别名(2026-07 盘点)
    "Australia": "AU", "Italy": "IT", "Japan": "JP", "Toronto": "CA", "Vienna": "AT",
    "迪拜": "AE", "阿布扎比": "AE", "伊朗": "IR", "克罗地亚": "HR", "卢森堡": "LU",
    "塞尔维亚": "RS", "拉脱维亚": "LV", "斯洛文尼亚": "SI", "格鲁吉亚": "GE",
    "缅甸": "MM", "阿尔及利亚": "DZ", "黑山共和国": "ME", "黑山": "ME",
}
# 这些值等于「没有国别信息」,直接判空(诚实缺席,不硬编)
_COUNTRY_JUNK: frozenset[str] = frozenset({"全球", "未知", "无符合要求的信息", "无", "N/A", "n/a", "-"})
ISO_TO_CN_NAME: dict[str, str] = {v: k for k, v in reversed(list(CN_NAME_TO_ISO.items()))}


# ── 小工具 ──────────────────────────────────────────────────────────────


def _loads(value: Any) -> Any:
    """JSONB/TEXT 经 compat 层可能回 dict 也可能回 str,双态容错。"""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _country_code(raw: Any) -> str:
    """创作者国别列的值 → ISO2;两位字母码放行大写;中文名查表;废话值判空;
    带注解/多国值取首段再查(真实库有「美国(西语)」「美国/英国/波兰」这类);未知保原样(不硬编)。"""
    text = str(raw or "").strip()
    if not text or text in _COUNTRY_JUNK:
        return ""
    if len(text) == 2 and text.isascii() and text.isalpha():
        return text.upper()
    if text in CN_NAME_TO_ISO:
        return CN_NAME_TO_ISO[text]
    head = text
    for sep in ("\n", "/", "(", "（", ",", "，", " "):
        head = head.split(sep, 1)[0].strip()
    return CN_NAME_TO_ISO.get(head, text) if head else text


def _country_name(code: str) -> str:
    return ISO_TO_CN_NAME.get(code, code)


def classify_script(text: str) -> str:
    """单个名字/ID 的文字系统:数非拉丁字符,>=2 个即判该字系;否则有拉丁判 latin;全无判 unknown。"""
    counts: Counter = Counter()
    for ch in str(text or ""):
        o = ord(ch)
        for script, lo, hi in _SCRIPT_RANGES:
            if lo <= o <= hi:
                counts[script] += 1
                break
    if not counts:
        return "unknown"
    non_latin = [(s, n) for s, n in counts.items() if s != "latin"]
    if non_latin:
        top_script, top_n = max(non_latin, key=lambda kv: kv[1])
        if top_n >= 2:
            return top_script
    return "latin" if counts.get("latin") else "unknown"


def _dist_from_weighted(weighted: dict[str, float]) -> list[dict[str, Any]]:
    """{code: 权重质量} → 归一化 [{code,name,pct}](降序,保留一位小数)。"""
    total = sum(v for v in weighted.values() if v > 0)
    if total <= 0:
        return []
    items = sorted(weighted.items(), key=lambda kv: kv[1], reverse=True)
    return [
        {"code": code, "name": _country_name(code), "pct": round(100.0 * mass / total, 1)}
        for code, mass in items
        if mass > 0
    ]


# ── 评论读取(双桥 + 可选 legacy 桥;全只读)────────────────────────────────


def _load_comment_rows(db: Any, kol_pool_id: int) -> list[dict[str, Any]]:
    """该 KOL 的评论行:account_id 直桥(排除员工官号口径)+ evidence 桥。"""
    fields = "platform, author_handle, author_id, language_detected, created_at, raw_data_json"
    rows = db.execute(
        f"SELECT {fields} FROM vkpi_comments "
        "WHERE account_id=? AND post_table<>'vkpi_employee_channels' LIMIT ?",
        (int(kol_pool_id), COMMENT_LIMIT),
    ).fetchall()
    out = [dict(r) for r in rows]
    if not out:
        ev = db.execute(
            "SELECT id FROM vkpi_kol_video_evidence WHERE kol_pool_id=? LIMIT ?",
            (int(kol_pool_id), EVIDENCE_LIMIT),
        ).fetchall()
        eids = [int(dict(e)["id"]) for e in ev]
        if eids:
            placeholders = ",".join(["?"] * len(eids))
            rows = db.execute(
                f"SELECT {fields} FROM vkpi_comments "
                f"WHERE post_table IN ('evidence','vkpi_kol_video_evidence') AND post_id IN ({placeholders}) LIMIT ?",
                (*eids, COMMENT_LIMIT),
            ).fetchall()
            out = [dict(r) for r in rows]
    return out


def _load_legacy_comment_rows(db: Any, pool_row: dict[str, Any]) -> list[dict[str, Any]]:
    """legacy kol_comments 桥:kol_id 引用主表 kols(id),仅当池行 linked_main_kol_id
    非空才可靠取用;平台需与池行一致(防错挂)。表可能不存在,缺席容错。"""
    linked = pool_row.get("linked_main_kol_id")
    if not linked:
        return []
    try:
        rows = db.execute(
            "SELECT platform, author_handle, created_at FROM kol_comments WHERE kol_id=? LIMIT ?",
            (int(linked), COMMENT_LIMIT),
        ).fetchall()
    except Exception:
        return []
    pool_platform = str(pool_row.get("platform") or "").strip().lower()
    out: list[dict[str, Any]] = []
    for r in rows:
        rec = dict(r)
        if pool_platform and str(rec.get("platform") or "").strip().lower() != pool_platform:
            continue
        rec.setdefault("author_id", None)
        rec.setdefault("language_detected", None)
        rec.setdefault("raw_data_json", None)
        out.append(rec)
    return out


def _display_texts(rec: dict[str, Any]) -> str:
    """评论者可读文本:handle + raw 里白捡的 owner.full_name / displayName(有则并)。"""
    parts = [str(rec.get("author_handle") or "")]
    raw = _loads(rec.get("raw_data_json"))
    if isinstance(raw, dict):
        owner = raw.get("owner")
        if isinstance(owner, dict):
            parts.append(str(owner.get("full_name") or ""))
        for key in ("ownerUsername", "displayName", "authorDisplayName", "nickname", "uniqueId"):
            val = raw.get(key)
            if isinstance(val, str):
                parts.append(val)
    return " ".join(p for p in parts if p)


def _commenter_key(rec: dict[str, Any]) -> str:
    """去重键:author_id 优先,退 author_handle 小写。"""
    aid = str(rec.get("author_id") or "").strip()
    if aid:
        return "id:" + aid
    handle = str(rec.get("author_handle") or "").strip().lower()
    return "h:" + handle if handle else ""


# ── 四路信号(每路独立出分布,诚实缺席)──────────────────────────────────


def _signal_comment_language(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """信号①:vkpi_comments.language_detected 列的语言分布 → 国家先验分布。
    列全空 = B1 回填未到,诚实缺席,绝不在这里重算文本。"""
    sig: dict[str, Any] = {
        "key": "comment_language", "label": "评论语言",
        "weight": SIGNAL_WEIGHTS["comment_language"],
        "status": "absent", "sample_size": 0, "distribution": [], "languages": [],
    }
    if not rows:
        sig["reason"] = "无已入库评论"
        return sig
    langs: Counter = Counter()
    for rec in rows:
        lang = str(rec.get("language_detected") or "").strip().lower()
        if lang and lang != "und":
            langs[lang] += 1
    if not langs:
        sig["reason"] = f"评论已入库 {len(rows)} 条但 language_detected 列全空(B1 回填未到)"
        return sig
    total = sum(langs.values())
    sig["languages"] = [
        {"lang": lang, "pct": round(100.0 * n / total, 1)} for lang, n in langs.most_common(8)
    ]
    weighted: dict[str, float] = {}
    for lang, n in langs.items():
        for code, w in LANG_COUNTRY_WEIGHTS.get(lang, ()):  # 未知语言不硬编国家
            weighted[code] = weighted.get(code, 0.0) + n * w
    sig["sample_size"] = total
    sig["distribution"] = _dist_from_weighted(weighted)[:8]
    if sig["distribution"]:
        sig["status"] = "ready"
    else:
        sig["status"] = "weak"
        sig["reason"] = "已判定语言均无国家先验映射"
    return sig


def _signal_commenter_script(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """信号②:评论者 ID/名字的文字系统启发(去重后每人一票)。拉丁字系国别不可分,
    只报占比;仅非拉丁部分参与国家分布。"""
    sig: dict[str, Any] = {
        "key": "commenter_script", "label": "评论者名字字系",
        "weight": SIGNAL_WEIGHTS["commenter_script"],
        "status": "absent", "sample_size": 0, "distribution": [], "scripts": [],
        "latin_share_pct": None,
    }
    if not rows:
        sig["reason"] = "无已入库评论"
        return sig
    per_commenter: dict[str, Counter] = {}
    for rec in rows:
        key = _commenter_key(rec)
        if not key:
            continue
        script = classify_script(_display_texts(rec))
        per_commenter.setdefault(key, Counter())[script] += 1
    votes: Counter = Counter()
    for counter in per_commenter.values():
        known = Counter({s: n for s, n in counter.items() if s != "unknown"})
        if not known:
            continue
        non_latin = Counter({s: n for s, n in known.items() if s != "latin"})
        votes[max(non_latin, key=non_latin.get) if non_latin else "latin"] += 1
    total = sum(votes.values())
    if total == 0:
        sig["reason"] = "评论者名字全部无法判定字系"
        return sig
    sig["sample_size"] = total
    sig["scripts"] = [
        {"script": s, "label": SCRIPT_LABELS.get(s, s), "pct": round(100.0 * n / total, 1)}
        for s, n in votes.most_common()
    ]
    sig["latin_share_pct"] = round(100.0 * votes.get("latin", 0) / total, 1)
    weighted: dict[str, float] = {}
    for script, n in votes.items():
        for code, w in SCRIPT_COUNTRY_WEIGHTS.get(script, ()):  # latin 不入国家分布
            weighted[code] = weighted.get(code, 0.0) + n * w
    sig["distribution"] = _dist_from_weighted(weighted)[:8]
    if sig["distribution"]:
        sig["status"] = "ready"
    else:
        sig["status"] = "weak"
        sig["reason"] = "评论者名字全为拉丁字系,国别不可分(不硬编)"
    return sig


def _signal_creator_country(pool_row: dict[str, Any]) -> dict[str, Any]:
    """信号③:创作者自报国别(代理口径,弱先验)。这是旧「@100%」假信号的降权归位。"""
    sig: dict[str, Any] = {
        "key": "creator_country", "label": "创作者国别(代理)",
        "weight": SIGNAL_WEIGHTS["creator_country"],
        "status": "absent", "sample_size": 0, "distribution": [], "proxy": True,
    }
    code = _country_code(pool_row.get("country"))
    if not code:
        sig["reason"] = "创作者国别列为空"
        return sig
    sig["status"] = "ready"
    sig["sample_size"] = 1
    sig["distribution"] = [{"code": code, "name": _country_name(code), "pct": 100.0}]
    sig["reason"] = "创作者自报/平台标注国别,只是弱先验,不等于受众所在地"
    return sig


def _signal_commenter_profile_country(db: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """信号④a:评论者身份缓存表 vkpi_commenter_profiles.country(既有推断层缓存,有列就用)。
    author_key 口径:YouTube=authorChannelId(存 author_id);IG/TT=author_handle 小写。"""
    sig: dict[str, Any] = {
        "key": "commenter_profile_country", "label": "评论者档案国别",
        "weight": SIGNAL_WEIGHTS["commenter_profile_country"],
        "status": "absent", "sample_size": 0, "distribution": [], "source_breakdown": {},
    }
    if not rows:
        sig["reason"] = "无已入库评论"
        return sig
    keys_by_platform: dict[str, set[str]] = {}
    for rec in rows:
        platform = str(rec.get("platform") or "").strip().lower()
        if not platform:
            continue
        bucket = keys_by_platform.setdefault(platform, set())
        handle = str(rec.get("author_handle") or "").strip().lower()
        aid = str(rec.get("author_id") or "").strip()
        if handle:
            bucket.add(handle)
        if aid:
            bucket.add(aid)
    votes: Counter = Counter()
    sources: Counter = Counter()
    for platform, keys in keys_by_platform.items():
        key_list = [k for k in keys if k][:800]
        if not key_list:
            continue
        placeholders = ",".join(["?"] * len(key_list))
        try:
            rows2 = db.execute(
                "SELECT author_key, country, country_source FROM vkpi_commenter_profiles "
                f"WHERE platform=? AND author_key IN ({placeholders})",
                (platform, *key_list),
            ).fetchall()
        except Exception:
            continue
        seen: set[str] = set()
        for r in rows2:
            rec2 = dict(r)
            akey = str(rec2.get("author_key") or "")
            code = _country_code(rec2.get("country"))
            # 只吃硬信号:country_source=language 是「评论语言→国家」假口径(en→US 把 79% 观众
            # 算成美国),缓存随刷新逐行自愈前,这里先过滤,不让存量假国家进融合。
            if str(rec2.get("country_source") or "").strip().lower() not in ("declared", "name"):
                continue
            if not code or akey in seen:
                continue
            seen.add(akey)
            votes[code] += 1
            sources[str(rec2.get("country_source") or "unknown")] += 1
    total = sum(votes.values())
    if total == 0:
        sig["reason"] = "评论者在身份缓存表无国别命中"
        return sig
    sig["status"] = "ready"
    sig["sample_size"] = total
    sig["distribution"] = _dist_from_weighted({c: float(n) for c, n in votes.items()})[:8]
    sig["source_breakdown"] = dict(sources)
    return sig


def _signal_activity_timezone(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """信号④b:评论时间 UTC 小时分布 → 粗时区倾向。纯侧信号(weight=0,不入国家融合、
    不计 confidence):活跃时段同时受创作者发布节奏影响,只做展示参考。"""
    sig: dict[str, Any] = {
        "key": "activity_timezone", "label": "评论活跃时区",
        "weight": 0.0, "status": "absent", "sample_size": 0,
        "distribution": [], "peak_hours_utc": [], "region_tilt": "",
    }
    hours: Counter = Counter()
    for rec in rows:
        created = rec.get("created_at")
        hour: int | None = None
        if hasattr(created, "astimezone"):
            try:
                from datetime import timezone as _tz

                hour = created.astimezone(_tz.utc).hour
            except Exception:
                hour = getattr(created, "hour", None)
        elif isinstance(created, str) and len(created) >= 13:
            try:
                hour = int(created[11:13])
            except Exception:
                hour = None
        if hour is not None and 0 <= hour <= 23:
            hours[hour] += 1
    total = sum(hours.values())
    if total == 0:
        sig["reason"] = "评论无可用时间戳"
        return sig
    sig["sample_size"] = total
    sig["peak_hours_utc"] = [h for h, _ in hours.most_common(3)]
    bands = {
        "asia_pacific": sum(n for h, n in hours.items() if 0 <= h < 8),
        "europe_africa": sum(n for h, n in hours.items() if 8 <= h < 16),
        "americas": sum(n for h, n in hours.items() if 16 <= h < 24),
    }
    top_band, top_mass = max(bands.items(), key=lambda kv: kv[1])
    sig["region_tilt"] = top_band if top_mass / total > 0.45 else "flat"
    sig["status"] = "side"
    sig["reason"] = "活跃时段受发布节奏影响,仅粗参考,不参与融合"
    return sig


# ── 融合 + 置信度 ─────────────────────────────────────────────────────────

AUDIENCE_SIGNAL_KEYS = ("comment_language", "commenter_script", "commenter_profile_country")


def _fuse(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ready 信号按权重加权融合(权重按在场信号重归一);weight=0 的侧信号不入。"""
    weighted: dict[str, float] = {}
    total_w = 0.0
    for sig in signals:
        if sig.get("status") != "ready" or not sig.get("distribution"):
            continue
        w = float(sig.get("weight") or 0.0)
        if w <= 0:
            continue
        total_w += w
        for item in sig["distribution"]:
            code = str(item.get("code") or "")
            pct = float(item.get("pct") or 0.0)
            if code:
                weighted[code] = weighted.get(code, 0.0) + w * pct
    if total_w <= 0:
        return []
    return _dist_from_weighted(weighted)[:8]


def _confidence(signals: list[dict[str, Any]]) -> tuple[str, str]:
    """high=2+ 受众信号一致 / medium=单强受众信号 / low=仅创作者国代理(或全无)。"""
    strong = [
        s for s in signals
        if s["key"] in AUDIENCE_SIGNAL_KEYS
        and s.get("status") == "ready"
        and int(s.get("sample_size") or 0) >= STRONG_SAMPLE_MIN
        and s.get("distribution")
    ]
    if len(strong) >= 2:
        tops = [str(s["distribution"][0]["code"]) for s in strong]
        top3s = [{str(i.get("code")) for i in s["distribution"][:3]} for s in strong]
        agree = any(
            tops[a] in top3s[b]
            for a in range(len(strong))
            for b in range(len(strong))
            if a != b
        )
        if agree:
            return "high", f"{len(strong)} 路受众信号相互印证(top 国家一致)"
        return "medium", f"{len(strong)} 路受众信号在场但 top 国家不一致,降为 medium"
    if len(strong) == 1:
        return "medium", f"单一强受众信号:{strong[0]['label']}(样本 {strong[0]['sample_size']})"
    ready_weak = [
        s for s in signals
        if s["key"] in AUDIENCE_SIGNAL_KEYS and s.get("status") == "ready" and s.get("distribution")
    ]
    if ready_weak:
        return "low", "受众信号样本过小,不足以支撑口径"
    return "low", "无受众实测信号,仅创作者国别代理(或全无)"


def audience_geo(kol_pool_id: int, *, conn: Any = None) -> dict[str, Any]:
    """受众地图 v1 主入口:多信号融合的受众地理分布 + 置信度。KOL 不存在抛 LookupError。"""
    from app.db.connection import get_conn

    db = conn or get_conn()
    row = db.execute(
        "SELECT id, platform, handle, country, linked_main_kol_id FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        raise LookupError(f"kol_pool {kol_pool_id} not found")
    pool_row = dict(row)

    rows = _load_comment_rows(db, int(kol_pool_id))
    rows.extend(_load_legacy_comment_rows(db, pool_row))

    signals = [
        _signal_comment_language(rows),
        _signal_commenter_script(rows),
        _signal_commenter_profile_country(db, rows),
        _signal_creator_country(pool_row),
        _signal_activity_timezone(rows),
    ]
    countries = _fuse(signals)
    confidence, confidence_reason = _confidence(signals)

    audience_ready = [
        s["key"] for s in signals
        if s["key"] in AUDIENCE_SIGNAL_KEYS and s.get("status") == "ready"
    ]
    signals_used = [s["key"] for s in signals if s.get("status") == "ready"]
    if audience_ready:
        geo_source = "audience_multi_signal_v1"
    elif any(s["key"] == "creator_country" and s.get("status") == "ready" for s in signals):
        geo_source = "creator_country_proxy"
    else:
        geo_source = "none"

    return {
        "status": "ready",
        "kol_pool_id": int(kol_pool_id),
        "platform": str(pool_row.get("platform") or ""),
        "geo_source": geo_source,
        "proxy_only": not audience_ready,
        "confidence": confidence,
        "confidence_reason": confidence_reason,
        "comment_rows": len(rows),
        "countries": countries,
        "signals": signals,
        "signals_used": signals_used,
        "note": "多信号估算口径(评论语言/名字字系/档案缓存/创作者国别弱先验),非平台官方受众数据",
    }
