"""件3 · ReplyQueue 评论区销售员 v0 半自动核心逻辑。

链路:
  screen_intents()  从 vkpi_comments 扫购买意向(多语种关键词规则)-> 入 vkpi_reply_queue(status=pending)。
  draft_reply(id)   RAG over 369 SKU(vkpi_products)-> llm_gateway.invoke 生成品牌口吻草稿(check_budget 先行,
                    降级给模板)-> 写 draft_reply + status=drafted。

v0 铁律:不自动发帖,只到 drafted;人工一键复制手动回,再 mark(replied/dismissed)。
纪律:复用既有件(intelligence_rules 的意向词表 + _rule_tags);函数内懒 import 防缺模块炸;
      红线零触 viltrox_fit_score/rule_v0 打分;compat SQL 用 ? 占位。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(value: Any) -> str:
    return str(value or "").strip()


# ── 多语种购买意向关键词(复用 intelligence_rules 的 OPPORTUNITY 词表,再补齐若干语种)──
# 兼容性问句 / 求购 / 型号问询 / 价格哪里买 等口径;命中即视为「值得销售员回」的机会评论。
_EXTRA_OPPORTUNITY_TERMS = (
    # 英语补齐
    "buy", "price", "cost", "how much", "where can i", "in stock", "order",
    "purchase", "coupon", "deal", "sale", "compatible", "compatibility", "fit ", "work with",
    # 西语(样本里有 Costo / Donde vendes / precio)
    "costo", "precio", "comprar", "donde", "dónde", "cuanto", "cuánto", "disponible",
    # 葡语
    "preço", "comprar", "onde", "quanto",
    # 法语
    "prix", "acheter", "où", "combien", "disponible",
    # 德语
    "preis", "kaufen", "wo kann", "verfügbar",
    # 日语
    "いくら", "値段", "購入", "どこで", "買え",
    # 韩语
    "가격", "구매", "어디서", "얼마",
    # 中文补齐
    "多少钱", "价格", "购买", "在哪", "哪里买", "哪买", "有货", "现货", "适配", "兼容", "支持",
)


def _opportunity_terms() -> tuple[str, ...]:
    """懒引 intelligence_rules 的 OPPORTUNITY_TERMS 并叠加本模块补齐词表(去重)。"""
    try:
        from app.domains.comments.intelligence_rules import OPPORTUNITY_TERMS as base
    except Exception:
        base = ()
    seen: dict[str, None] = {}
    for term in (*base, *_EXTRA_OPPORTUNITY_TERMS):
        key = str(term).strip().lower()
        if key:
            seen.setdefault(key, None)
    return tuple(seen.keys())


def _classify_intent(text: str) -> str:
    """返回意向标签:price(价格/购买) / compat(兼容/型号问询) / question(泛问句) / ''(非机会)。

    只用规则,不触 rule_v0 打分,不写 fit_score。
    """
    lowered = _text(text).lower()
    if not lowered:
        return ""
    price_terms = (
        "buy", "price", "cost", "how much", "order", "purchase", "coupon", "deal", "sale",
        "costo", "precio", "comprar", "preço", "prix", "acheter", "preis", "kaufen",
        "在哪", "哪里买", "哪买", "多少钱", "价格", "购买", "有货", "现货",
        "いくら", "値段", "購入", "가격", "구매", "얼마",
    )
    compat_terms = (
        "compatible", "compatibility", "work with", "fit ", "mount",
        "适配", "兼容", "支持", "卡口",
    )
    if any(term in lowered for term in price_terms):
        return "price"
    if any(term in lowered for term in compat_terms):
        return "compat"
    # 命中 opportunity 词表但落不到上两类,或本身是问句 -> question
    if "?" in text or "？" in text:
        return "question"
    if any(term in lowered for term in _opportunity_terms()):
        return "question"
    return ""


# ── 轻量语种判定(vkpi_comments.language_detected 大多为空,这里兜底猜一版)──
def _guess_lang(text: str, hint: str = "") -> str:
    hint = _text(hint).lower()
    if hint:
        return hint[:10]
    s = _text(text)
    if not s:
        return ""
    if re.search(r"[一-鿿]", s):
        return "zh"
    if re.search(r"[぀-ヿ]", s):
        return "ja"
    if re.search(r"[가-힯]", s):
        return "ko"
    if re.search(r"[áéíóúñ¿¡]", s.lower()):
        return "es"
    if re.search(r"[àâçéèêëîïôûù]", s.lower()):
        return "fr"
    if re.search(r"[äöüß]", s.lower()):
        return "de"
    if re.search(r"[ãõáâêô]", s.lower()):
        return "pt"
    return "en"


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row.get(key) if hasattr(row, "get") else row[key]
    except Exception:
        try:
            return dict(row).get(key, default)
        except Exception:
            return default


# ══════════════════════════════════════════════════════════════════════════
# screen_intents:从 vkpi_comments 扫购买意向 -> 入队 pending(幂等)
# ══════════════════════════════════════════════════════════════════════════
def screen_intents(*, limit: int = 500, platform: str = "") -> dict[str, Any]:
    """扫 vkpi_comments 找购买意向评论,入 vkpi_reply_queue(status=pending)。

    幂等:comment_external_id 唯一约束 + ON CONFLICT DO NOTHING;重复筛不会重复入队。
    只读 vkpi_comments,只写 vkpi_reply_queue,红线零触。
    """
    conn = get_conn()
    if not _table_exists("vkpi_reply_queue"):
        return {"ok": False, "reason": "reply_queue_table_missing", "scanned": 0, "enqueued": 0}

    safe_limit = max(1, min(int(limit or 500), 5000))
    where = "WHERE COALESCE(comment_text,'') <> ''"
    params: list[Any] = []
    plat = _text(platform).lower()
    if plat:
        where += " AND LOWER(COALESCE(platform,'')) = ?"
        params.append(plat)
    params.append(safe_limit)

    rows = conn.execute(
        f"""
        SELECT external_comment_id, comment_text, platform, account_id, language_detected
        FROM vkpi_comments
        {where}
        ORDER BY COALESCE(fetched_at, created_at) DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()

    scanned = 0
    enqueued = 0
    matched = 0
    for row in rows:
        scanned += 1
        ext_id = _text(_row_get(row, "external_comment_id"))
        text = _text(_row_get(row, "comment_text"))
        if not ext_id or not text:
            continue
        intent = _classify_intent(text)
        if not intent:
            continue
        matched += 1
        lang = _guess_lang(text, _text(_row_get(row, "language_detected")))
        try:
            kol_pool_id = int(_row_get(row, "account_id") or 0) or None
        except Exception:
            kol_pool_id = None
        cur = conn.execute(
            """
            INSERT INTO vkpi_reply_queue (
              platform, kol_pool_id, comment_external_id, comment_text,
              intent_tag, lang, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            ON CONFLICT (comment_external_id) DO NOTHING
            """,
            (
                _text(_row_get(row, "platform")),
                kol_pool_id,
                ext_id,
                text[:2000],
                intent,
                lang,
                _now_iso(),
                _now_iso(),
            ),
        )
        # rowcount>0 表示真入队(未撞唯一约束);<=0 表示已存在(幂等跳过)。
        try:
            if int(getattr(cur, "rowcount", 0) or 0) > 0:
                enqueued += 1
        except Exception:
            enqueued += 1
    conn.commit()
    return {"ok": True, "scanned": scanned, "matched": matched, "enqueued": enqueued}


# ══════════════════════════════════════════════════════════════════════════
# draft_reply:RAG over 369 SKU -> llm_gateway 生成品牌口吻草稿(预算闸先行,降级模板)
# ══════════════════════════════════════════════════════════════════════════
def _retrieve_skus(text: str, *, top_k: int = 5) -> list[dict[str, Any]]:
    """极简 RAG:按评论文本对 vkpi_products 做关键词命中打分,取 top_k。

    仅只读 vkpi_products;打分只是检索排序,与 viltrox_fit_score 无关(红线零触)。
    """
    conn = get_conn()
    if not _table_exists("vkpi_products"):
        return []
    try:
        rows = conn.execute(
            """
            SELECT sku, model_name, marketing_name, category_main, category_detail,
                   series, mount, price_usd, description
            FROM vkpi_products
            WHERE COALESCE(status,'') <> 'archived'
            LIMIT 500
            """,
        ).fetchall()
    except Exception:
        return []

    lowered = _text(text).lower()
    tokens = [t for t in re.split(r"[^a-z0-9一-鿿]+", lowered) if len(t) >= 2]
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        item = {
            "sku": _text(_row_get(row, "sku")),
            "model_name": _text(_row_get(row, "model_name")),
            "marketing_name": _text(_row_get(row, "marketing_name")),
            "category_main": _text(_row_get(row, "category_main")),
            "category_detail": _text(_row_get(row, "category_detail")),
            "series": _text(_row_get(row, "series")),
            "mount": _text(_row_get(row, "mount")),
            "price_usd": _row_get(row, "price_usd"),
            "description": _text(_row_get(row, "description")),
        }
        haystack = " ".join(
            [
                item["model_name"], item["marketing_name"], item["category_main"],
                item["category_detail"], item["series"], item["mount"], item["description"],
            ]
        ).lower()
        score = 0
        for tok in tokens:
            if tok and tok in haystack:
                score += 1
        # mount 精确命中额外加权(兼容性问句最关心卡口)。
        if item["mount"] and item["mount"].lower() in lowered:
            score += 3
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _score, item in scored[: max(1, int(top_k))]]


def _template_reply(text: str, intent: str, skus: list[dict[str, Any]], lang: str) -> str:
    """预算不足或 LLM 降级时的模板草稿(不发帖,人工可编辑后再用)。"""
    names = ", ".join(
        [s.get("marketing_name") or s.get("model_name") or s.get("sku") for s in skus[:3] if s]
    )
    if lang.startswith("zh"):
        base = "感谢关注 Viltrox!"
        if names:
            base += f" 您可以看看 {names}。"
        if intent == "price":
            base += " 具体价格与购买链接可私信我们获取。"
        elif intent == "compat":
            base += " 关于卡口/兼容性,欢迎告诉我们您的机身型号,我们帮您确认。"
        else:
            base += " 有任何问题欢迎随时问我们。"
        return base
    base = "Thanks for your interest in Viltrox!"
    if names:
        base += f" You might like {names}."
    if intent == "price":
        base += " DM us for current pricing and where to buy."
    elif intent == "compat":
        base += " Tell us your camera body and we'll confirm mount compatibility."
    else:
        base += " Happy to answer any questions."
    return base


def _build_prompt(text: str, intent: str, skus: list[dict[str, Any]], lang: str) -> str:
    catalog_lines = []
    for s in skus[:5]:
        price = s.get("price_usd")
        price_str = f" | ${price}" if price not in (None, "") else ""
        catalog_lines.append(
            f"- {s.get('marketing_name') or s.get('model_name')} (SKU {s.get('sku')}, "
            f"{s.get('category_main')}/{s.get('mount')}{price_str}): {s.get('description', '')[:160]}"
        )
    catalog = "\n".join(catalog_lines) if catalog_lines else "(no matching SKU retrieved)"
    lang_hint = {
        "zh": "Reply in Simplified Chinese.",
        "ja": "Reply in Japanese.",
        "ko": "Reply in Korean.",
        "es": "Reply in Spanish.",
        "fr": "Reply in French.",
        "de": "Reply in German.",
        "pt": "Reply in Portuguese.",
    }.get(lang, "Reply in the same language as the comment.")
    return (
        "You are a friendly Viltrox brand social-media rep answering a buyer-intent comment.\n"
        "Write ONE short, warm, non-pushy reply (max 2 sentences). Do not invent prices or specs not in the catalog.\n"
        "If the buyer asks compatibility, ask for their camera body when unsure. Never fabricate stock or discounts.\n"
        f"{lang_hint}\n\n"
        f"Buyer comment ({intent}): {text}\n\n"
        f"Relevant Viltrox catalog:\n{catalog}\n\n"
        "Reply:"
    )


def draft_reply(reply_id: int) -> dict[str, Any]:
    """为队列里一条 pending/replied 项生成品牌口吻草稿。

    check_budget 先行:预算不足直接降级模板;LLM 失败也降级模板。写 draft_reply + status=drafted。
    """
    conn = get_conn()
    if not _table_exists("vkpi_reply_queue"):
        return {"ok": False, "reason": "reply_queue_table_missing"}

    row = conn.execute(
        "SELECT id, comment_text, intent_tag, lang, status FROM vkpi_reply_queue WHERE id=?",
        (int(reply_id),),
    ).fetchone()
    if not row:
        return {"ok": False, "reason": "not_found", "id": int(reply_id)}

    text = _text(_row_get(row, "comment_text"))
    intent = _text(_row_get(row, "intent_tag")) or "question"
    lang = _text(_row_get(row, "lang")) or _guess_lang(text)
    skus = _retrieve_skus(text)

    draft = ""
    provider = "template"
    # 预算闸先行(懒 import 防缺模块炸);check_budget 返回 False -> 直接模板降级。
    budget_ok = True
    try:
        from app.domains.costs import budget_guard
        budget_ok = bool(budget_guard.check_budget("comment_reply_draft", 0.01, require_configured=False))
    except Exception:
        budget_ok = True

    if budget_ok:
        try:
            from app.platform import llm_gateway
            result = llm_gateway.invoke(
                _build_prompt(text, intent, skus, lang),
                purpose="comment_reply_draft",
                max_output_tokens=200,
                cost_tag="comment_reply_draft",
                triggered_by="reply_queue.draft_reply",
                metadata={"reply_id": int(reply_id), "intent": intent, "lang": lang},
            )
            if isinstance(result, dict):
                draft = _text(result.get("text") or result.get("output") or result.get("content"))
                provider = _text(result.get("provider")) or "llm"
        except Exception:
            draft = ""

    if not draft:
        draft = _template_reply(text, intent, skus, lang)
        provider = "template"

    conn.execute(
        "UPDATE vkpi_reply_queue SET draft_reply=?, status='drafted', updated_at=? WHERE id=?",
        (draft[:4000], _now_iso(), int(reply_id)),
    )
    conn.commit()
    return {
        "ok": True,
        "id": int(reply_id),
        "status": "drafted",
        "provider": provider,
        "draft_reply": draft,
        "retrieved_skus": [s.get("sku") for s in skus],
    }


# ══════════════════════════════════════════════════════════════════════════
# 列表 / 标记
# ══════════════════════════════════════════════════════════════════════════
_VALID_STATUS = ("pending", "drafted", "replied", "dismissed")


def list_queue(*, status: str = "", platform: str = "", limit: int = 100) -> dict[str, Any]:
    """列出队列项;可按 status / platform 过滤。只读。"""
    conn = get_conn()
    if not _table_exists("vkpi_reply_queue"):
        return {"items": [], "count": 0}
    safe_limit = max(1, min(int(limit or 100), 500))
    where = "WHERE 1=1"
    params: list[Any] = []
    st = _text(status).lower()
    if st in _VALID_STATUS:
        where += " AND status = ?"
        params.append(st)
    plat = _text(platform).lower()
    if plat:
        where += " AND LOWER(COALESCE(platform,'')) = ?"
        params.append(plat)
    params.append(safe_limit)
    rows = conn.execute(
        f"""
        SELECT id, platform, kol_pool_id, comment_external_id, comment_text,
               intent_tag, lang, draft_reply, status, created_at, updated_at
        FROM vkpi_reply_queue
        {where}
        ORDER BY
          CASE status WHEN 'pending' THEN 0 WHEN 'drafted' THEN 1 ELSE 2 END,
          created_at DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    items = []
    for row in rows:
        items.append(
            {
                "id": int(_row_get(row, "id") or 0),
                "platform": _text(_row_get(row, "platform")),
                "kol_pool_id": _row_get(row, "kol_pool_id"),
                "comment_external_id": _text(_row_get(row, "comment_external_id")),
                "comment_text": _text(_row_get(row, "comment_text")),
                "intent_tag": _text(_row_get(row, "intent_tag")),
                "lang": _text(_row_get(row, "lang")),
                "draft_reply": _text(_row_get(row, "draft_reply")),
                "status": _text(_row_get(row, "status")),
                "created_at": _text(_row_get(row, "created_at")),
                "updated_at": _text(_row_get(row, "updated_at")),
            }
        )
    return {"items": items, "count": len(items)}


def mark(reply_id: int, status: str) -> dict[str, Any]:
    """人工标记一条队列项状态(replied / dismissed;也允许回退 pending / drafted)。"""
    st = _text(status).lower()
    if st not in _VALID_STATUS:
        return {"ok": False, "reason": "invalid_status", "allowed": list(_VALID_STATUS)}
    conn = get_conn()
    if not _table_exists("vkpi_reply_queue"):
        return {"ok": False, "reason": "reply_queue_table_missing"}
    row = conn.execute("SELECT id FROM vkpi_reply_queue WHERE id=?", (int(reply_id),)).fetchone()
    if not row:
        return {"ok": False, "reason": "not_found", "id": int(reply_id)}
    conn.execute(
        "UPDATE vkpi_reply_queue SET status=?, updated_at=? WHERE id=?",
        (st, _now_iso(), int(reply_id)),
    )
    conn.commit()
    return {"ok": True, "id": int(reply_id), "status": st}
