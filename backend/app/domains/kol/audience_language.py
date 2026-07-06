"""受众语言分布(评论法)—— 自带停用词检测器,零依赖、零外调、可部署任何环境。

背景:「粉丝地理分布」旧数据是"创作者注册国@100%"的假值(kolPoolRuntime.ts:219)。真受众地理
平台不开放,低成本只能估算:抽该账号评论者的评论文本 -> 语言识别 -> 聚合语言分布 -> 推市场区域。
纯 unicode 分不出拉丁语系(英/西/葡/德/法全判 latin),故用**停用词频**判别;CJK/俄/阿/泰走 unicode 块。
数据覆盖现实:目前只有 18 官号有评论;外部 KOL 需先抓评论(run_kol_pool_comments_for_job)。
红线:纯读评论文本做统计,绝不触 viltrox_fit_score。
"""
from __future__ import annotations

import re
from typing import Any

# 各语言高频停用词(小写、去标点后按空格切词匹配)。只需区分主流受众语系,不求学术精确。
_STOPWORDS: dict[str, set[str]] = {
    "en": {"the", "and", "this", "for", "you", "your", "is", "it", "my", "love", "thank", "thanks", "great", "nice", "will", "can", "get", "best", "so", "of", "to", "with", "have", "what", "when", "where", "how", "please", "amazing", "video", "really"},
    "es": {"que", "de", "la", "el", "en", "un", "una", "por", "con", "para", "los", "las", "muy", "es", "gracias", "hola", "bueno", "como", "pero", "esta", "este", "más", "sí", "te", "me", "mi", "tu"},
    "pt": {"que", "de", "não", "você", "com", "para", "uma", "muito", "obrigado", "obrigada", "isso", "bom", "está", "eu", "meu", "minha", "por", "os", "as", "mais", "também", "sim"},
    "de": {"der", "die", "und", "ist", "das", "ich", "nicht", "ein", "eine", "auch", "sehr", "danke", "gut", "mit", "für", "auf", "aber", "was", "wie", "schön", "toll"},
    "fr": {"le", "la", "les", "de", "et", "un", "une", "pour", "pas", "très", "merci", "bonjour", "bien", "je", "tu", "vous", "avec", "mais", "sur", "est", "belle", "beau"},
    "it": {"che", "di", "il", "la", "per", "non", "una", "sono", "molto", "grazie", "bene", "ciao", "questo", "come", "più", "anche", "bello", "bella", "con", "ma"},
    "id": {"yang", "dan", "ini", "itu", "untuk", "saya", "kamu", "tidak", "bisa", "banget", "keren", "mantap", "bagus", "sudah", "juga", "dengan", "apa"},
    "tr": {"bir", "ve", "bu", "çok", "için", "ben", "sen", "güzel", "teşekkür", "evet", "ama", "gibi", "daha", "ne"},
    "nl": {"de", "het", "een", "en", "is", "dat", "ik", "je", "niet", "heel", "mooi", "dank", "wat", "hoe"},
}
# unicode 块 -> 语言(拉丁以外一眼可判)。
_SCRIPT_RANGES = [
    ("zh", 0x4E00, 0x9FFF), ("ja", 0x3040, 0x30FF), ("ko", 0xAC00, 0xD7AF),
    ("ru", 0x0400, 0x04FF), ("ar", 0x0600, 0x06FF), ("th", 0x0E00, 0x0E7F),
    ("he", 0x0590, 0x05FF), ("hi", 0x0900, 0x097F),
]
# 语言 -> 代表市场(粗口径,供"推市场区域")。
LANG_TO_MARKETS: dict[str, list[str]] = {
    "en": ["US", "UK", "AU", "CA"], "es": ["ES", "MX", "AR", "CO"], "pt": ["BR", "PT"],
    "de": ["DE", "AT", "CH"], "fr": ["FR", "CA", "BE"], "it": ["IT"], "id": ["ID"],
    "tr": ["TR"], "nl": ["NL", "BE"], "zh": ["CN", "TW", "HK"], "ja": ["JP"], "ko": ["KR"],
    "ru": ["RU"], "ar": ["SA", "AE", "EG"], "th": ["TH"], "hi": ["IN"], "he": ["IL"],
}
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def detect_lang(text: str) -> str:
    """单条文本语言判别。先看非拉丁 unicode 块,再看拉丁停用词命中;都不中返回 'und'(未定)。"""
    if not text:
        return "und"
    for ch in text:
        o = ord(ch)
        for lang, lo, hi in _SCRIPT_RANGES:
            if lo <= o <= hi:
                return lang
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if not words:
        return "und"
    scores: dict[str, int] = {}
    wl = set(words)
    for lang, sw in _STOPWORDS.items():
        hit = len(wl & sw)
        if hit:
            scores[lang] = hit
    if not scores:
        return "und"
    return max(scores.items(), key=lambda kv: kv[1])[0]


def language_distribution(comment_texts: list[str]) -> dict[str, Any]:
    """聚合一批评论文本的语言分布。返回 {languages:[{lang,pct}], sample_size, confidence, top_markets}。
    confidence 随样本量与'已判定比例'升。"""
    import collections

    counter: collections.Counter = collections.Counter()
    considered = 0
    for t in comment_texts or []:
        t = (t or "").strip()
        if len(t) < 2:
            continue
        considered += 1
        counter[detect_lang(t)] += 1
    if considered == 0:
        return {"languages": [], "sample_size": 0, "confidence": 0.0, "top_markets": [], "method": "comments_language"}
    determined = considered - counter.get("und", 0)
    langs = [
        {"lang": lang, "pct": round(100 * n / considered)}
        for lang, n in counter.most_common()
        if lang != "und"
    ]
    # confidence:样本 >=200 且已判 >=60% -> 高;逐步降。
    det_ratio = determined / considered if considered else 0
    size_factor = min(1.0, considered / 200.0)
    confidence = round(min(0.85, 0.2 + 0.5 * size_factor + 0.3 * det_ratio), 2)
    top_markets: list[str] = []
    for l in langs[:3]:
        for mk in LANG_TO_MARKETS.get(l["lang"], []):
            if mk not in top_markets:
                top_markets.append(mk)
    return {
        "languages": langs[:8],
        "sample_size": considered,
        "determined_pct": round(100 * det_ratio),
        "confidence": confidence,
        "top_markets": top_markets[:6],
        "method": "comments_language",
        "note": "估算值(评论者语言),非平台官方粉丝数据",
    }


def audience_language_for_kol(kol_pool_id: int, *, conn: Any = None, limit: int = 800) -> dict[str, Any]:
    """取该 KOL 已抓回的评论 -> 语言分布。无评论则 sample_size=0(诚实空,不编造)。
    评论桥:vkpi_comments 经 account_id / evidence。零外调、纯读。"""
    from app.db.connection import get_conn

    db = conn or get_conn()
    # 该 KOL 的评论:优先 account_id=kol_pool_id;否则经其 evidence 的 external_post 关联(尽力而为)。
    rows = db.execute(
        "SELECT comment_text FROM vkpi_comments WHERE account_id=? LIMIT ?",
        (int(kol_pool_id), int(limit)),
    ).fetchall()
    texts = [str(dict(r).get("comment_text") or "") for r in rows]
    if not texts:
        # 兜底:经 evidence 的 content_url 匹配(该 KOL 视频下的评论)。
        ev = db.execute(
            "SELECT id FROM vkpi_kol_video_evidence WHERE kol_pool_id=? LIMIT 50",
            (int(kol_pool_id),),
        ).fetchall()
        eids = [int(dict(e)["id"]) for e in ev]
        if eids:
            placeholders = ",".join(["?"] * len(eids))
            rows = db.execute(
                f"SELECT comment_text FROM vkpi_comments WHERE post_table IN ('evidence', 'vkpi_kol_video_evidence') AND post_id IN ({placeholders}) LIMIT ?",
                (*eids, int(limit)),
            ).fetchall()
            texts = [str(dict(r).get("comment_text") or "") for r in rows]
    dist = language_distribution(texts)
    dist["kol_pool_id"] = int(kol_pool_id)
    return dist


def enqueue_audience_comments_for_high_value(
    *, min_fit: float = 75.0, limit: int | None = 40, staff: Any = None
) -> dict[str, Any]:
    """给「高价值 / AI 看好」的 KOL 抓评论 —— 这样受众语言分布才对你实际评估的人生效。
    高价值口径:viltrox_fit_score >= min_fit 或 已入主表(linked_main_kol_id 非空);且有视频证据、
    且暂无评论(有则跳过,幂等)。逐个走现成 enqueue_kol_pool_comments_job(泳道可见、幂等)。
    读 fit 仅用于筛选,绝不写 viltrox_fit_score。抓取有 Apify 成本,故默认 limit=40、只挑高价值。"""
    from app.db.connection import get_conn
    from app.domains.comments.collector import enqueue_kol_pool_comments_job

    db = get_conn()
    sql = (
        "SELECT DISTINCT p.id, p.viltrox_fit_score FROM vkpi_kol_pool p "
        "JOIN vkpi_kol_video_evidence e ON e.kol_pool_id = p.id AND e.is_active IS NOT FALSE "
        "WHERE (COALESCE(p.viltrox_fit_score,0) >= ? OR p.linked_main_kol_id IS NOT NULL) "
        "ORDER BY p.viltrox_fit_score DESC NULLS LAST"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    ids = [int(dict(r)["id"]) for r in db.execute(sql, (float(min_fit),)).fetchall()]
    enqueued = skipped = has_comments = 0
    for kid in ids:
        if audience_language_for_kol(kid, conn=db).get("sample_size", 0) > 0:
            has_comments += 1
            continue
        try:
            res = enqueue_kol_pool_comments_job(kid, staff=staff)
            if str(res.get("status")) in ("queued", "already_queued"):
                enqueued += 1
            else:
                skipped += 1
        except Exception:
            skipped += 1
    return {
        "status": "done", "candidates": len(ids),
        "enqueued": enqueued, "already_has_comments": has_comments, "skipped": skipped,
    }
