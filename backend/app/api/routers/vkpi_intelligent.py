"""件1 · Intelligent 问答(三车道分诊)· 只读加性新路由。

三车道:
  车道①(intent)   —— query_planner.match_intent/resolve_intent 命中即 run(),秒回结构化答案。
  车道②(search)   —— 未命中意图 → unified_search 检索给候选(自有池召回,免费即时)。
  车道③(synth)    —— 需要综合 → llm_gateway.invoke(purpose="vkpi_intelligent_ask");
                      预算键先 check_budget,失败/超时/降级则诚实回退到车道②的检索结果(mode=degraded)。

端点:
  POST /api/admin/vkpi/intelligent/ask          三车道分诊,当日结果缓存(内存 dict + 日期键)。
  GET  /api/admin/vkpi/intelligent/suggestions  当日"异动种"chips(vkpi_alerts 近24h + apify_jobs 近24h)。

铁律:
  - 全部第三方依赖走函数内懒 import,缺模块只降级不炸(never 500)。
  - 零触 viltrox_fit_score / rule_v0 写路径;本路由只读、不拼 SQL、不改任何业务表。
  - compat SQL 用 ? 占位;鉴权 require_tab("vkpi","read")(照抄同目录 router 写法)。
  - 【不改 main.py】挂载行见任务回报 collect_anchors。
"""
from __future__ import annotations

import threading
from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from app.api.dependencies.perms import require_tab


router = APIRouter(prefix="/api/admin/vkpi/intelligent", tags=["vkpi-intelligent"])


# ── 当日结果缓存:内存 dict,键=(date, 归一化问题),换日自然失效(懒清理) ──────────
_ASK_CACHE: dict[str, dict[str, Any]] = {}
_ASK_CACHE_LOCK = threading.Lock()
_ASK_CACHE_MAX = 512  # 软上限,超出清空当日缓存,防内存无限涨(单进程串行足够)。

# LLM 综合调用的预算键与超时(秒)。check_budget 失败即诚实降级,不烧钱。
_SYNTH_BUDGET_SCOPE = "vkpi_intelligent_ask"
_SYNTH_TIMEOUT_S = 30

# 车道①以外的兜底建议问题(缺表 / 无异动时用)。
_DEFAULT_SUGGESTIONS: tuple[str, ...] = (
    "最近哪些 KOL 值得优先跟进?",
    "近30天各地区的表现怎么样?",
    "有哪些待处理的告警?",
    "本周有哪些新达人进池?",
)


def _today_key() -> str:
    return date.today().isoformat()


def _norm_q(question: str) -> str:
    return " ".join(str(question or "").strip().lower().split())


def _cache_get(question: str) -> dict[str, Any] | None:
    key = _today_key() + "::" + _norm_q(question)
    with _ASK_CACHE_LOCK:
        hit = _ASK_CACHE.get(key)
        if hit is None:
            return None
        # 命中拷贝一份并打上 cached=True,避免调用方改到缓存内部对象。
        out = dict(hit)
        out["cached"] = True
        return out


def _cache_put(question: str, payload: dict[str, Any]) -> None:
    key = _today_key() + "::" + _norm_q(question)
    with _ASK_CACHE_LOCK:
        if len(_ASK_CACHE) >= _ASK_CACHE_MAX:
            _ASK_CACHE.clear()
        # 存底本 cached=False;读时再拷贝并翻 True。
        stored = dict(payload)
        stored["cached"] = False
        _ASK_CACHE[key] = stored


def _answer(
    *,
    answer: str,
    mode: str,
    evidence: list[dict[str, Any]] | None = None,
    actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """统一答案结构:{answer, mode, evidence, actions, cached}。"""
    return {
        "answer": str(answer or ""),
        "mode": mode,
        "evidence": list(evidence or []),
        "actions": list(actions or []),
        "cached": False,
    }


# ── 车道① intent:命中白名单意图 → query_planner.run() 秒回结构化 ────────────────
def _try_intent(question: str) -> dict[str, Any] | None:
    """命中返回统一答案;未命中返回 None;任何异常 → None(降级到车道②)。"""
    try:
        from app.domains.analytics import query_planner  # 懒 import 防缺模块炸
        from app.db.connection import get_conn
    except Exception:
        return None
    try:
        intent = query_planner.resolve_intent(question, None)
        if intent is None:
            return None
        result = query_planner.run(get_conn(), question=question)
    except Exception:
        return None
    if not result or result.get("intent") is None:
        return None

    rows = result.get("rows") or []
    columns = result.get("columns") or []
    title = result.get("title") or result.get("intent")
    # 结论加粗由前端渲染;这里给一句人话总结 + 结构化证据。
    summary = f"命中「{title}」:返回 {len(rows)} 行。"
    evidence = [{
        "kind": "intent_result",
        "intent": result.get("intent"),
        "title": title,
        "columns": columns,
        "rows": rows[:50],  # 防超大回包;完整数据走「问数」页
        "sql_explain": result.get("sql_explain", ""),
    }]
    # route 用 cockpit nav key(前端按 key 切页,不是 URL);dataQuery=问数页。
    actions = [{"label": "打开问数页查看完整结果", "route": "dataQuery"}]
    return _answer(answer=summary, mode="intent", evidence=evidence, actions=actions)


# ── 车道② search:未命中意图 → unified_search 检索候选(免费即时) ────────────────
def _try_search(question: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    """始终返回一个答案(哪怕空结果),作为车道③失败时的诚实兜底。"""
    results: list[dict[str, Any]] = []
    provider_status: dict[str, Any] = {}
    try:
        from app.domains.kol.unified_search import unified_search  # 懒 import
        found = unified_search(question, include_external=False, limit=20, staff=staff)
        if found.get("status") == "ok":
            results = found.get("results", []) or []
            provider_status = found.get("provider_status", {}) or {}
    except Exception:
        results = []

    n = len(results)
    if n > 0:
        summary = f"未命中固定问法,已按关键词检索到 {n} 个候选。"
    else:
        summary = "未命中固定问法,当前池内没有匹配的候选。"
    evidence = [{
        "kind": "search_results",
        "count": n,
        "results": results[:20],
        "provider_status": provider_status,
    }]
    # route 用 cockpit nav key;kol-pool=KOL 池页。
    actions = [{"label": "去 KOL 池检索", "route": "kol-pool"}]
    return _answer(answer=summary, mode="search", evidence=evidence, actions=actions)


# ── 车道③ synth:需要综合 → llm_gateway.invoke;失败/超时/降级 → 车道② ──────────
def _needs_synth(question: str) -> bool:
    """粗判是否需要综合(开放式提问)。保守触发,省钱优先;不确定就走检索。"""
    q = _norm_q(question)
    if not q:
        return False
    triggers = ("为什么", "怎么", "如何", "分析", "总结", "对比", "建议", "评估",
                "why", "how", "analyze", "summarize", "compare", "recommend")
    return any(t in q for t in triggers)


def _try_synth(question: str, search_fallback: dict[str, Any]) -> dict[str, Any]:
    """综合车道:预算闸 → invoke(30s)→ 成功取 text;失败/降级回退检索结果(mode=degraded)。"""
    # 预算闸:失败即诚实降级到检索,不发起 LLM 调用。
    try:
        from app.domains.costs.budget_guard import check_budget  # 懒 import
        allowed = bool(check_budget(_SYNTH_BUDGET_SCOPE, 0))
    except Exception:
        allowed = False
    if not allowed:
        degraded = dict(search_fallback)
        degraded["mode"] = "degraded"
        degraded["answer"] = "综合分析预算未就绪,已回退到检索结果。" + degraded.get("answer", "")
        return degraded

    # 30s 超时包裹 invoke;线程内跑,主线程等结果或超时降级。
    box: dict[str, Any] = {}

    def _call() -> None:
        try:
            from app.platform.llm_gateway import invoke  # 懒 import
            prompt = (
                "你是 V-KPI 营销智能助手。基于以下用户问题,给出简明、可执行的中文结论"
                "(先给一句结论,再给 2-3 条要点)。若信息不足,直说需要补什么数据。\n\n"
                f"用户问题:{question}\n"
            )
            box["result"] = invoke(
                prompt,
                purpose="vkpi_intelligent_ask",
                max_output_tokens=800,
                cost_tag=_SYNTH_BUDGET_SCOPE,
            )
        except Exception as exc:  # 缺模块/网络/代理任何异常都降级,不炸
            box["error"] = str(exc)

    worker = threading.Thread(target=_call, daemon=True)
    worker.start()
    worker.join(timeout=_SYNTH_TIMEOUT_S)

    result = box.get("result")
    text = str((result or {}).get("text") or "").strip() if isinstance(result, dict) else ""
    provider = str((result or {}).get("provider") or "") if isinstance(result, dict) else ""
    # invoke 成功且非 rule_v0 兜底且有正文 → 综合答案;否则诚实降级到检索。
    if text and provider and provider != "rule_v0":
        evidence = list(search_fallback.get("evidence") or [])
        evidence.append({"kind": "synth", "provider": provider, "model": (result or {}).get("model", "")})
        return _answer(
            answer=text,
            mode="synth",
            evidence=evidence,
            actions=list(search_fallback.get("actions") or []),
        )

    degraded = dict(search_fallback)
    degraded["mode"] = "degraded"
    reason = "超时" if worker.is_alive() else ("降级" if provider == "rule_v0" else "不可用")
    degraded["answer"] = f"综合分析{reason},已回退到检索结果。" + degraded.get("answer", "")
    return degraded


@router.post("/ask")
def intelligent_ask(
    payload: dict[str, Any] = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """三车道分诊问答。请求体:{question}。返回 {answer, mode, evidence, actions, cached}。"""
    question = str(payload.get("question") or "").strip()
    if not question:
        return _answer(answer="请输入一个问题。", mode="degraded")

    # 当日缓存命中直接回(cached=True)。
    cached = _cache_get(question)
    if cached is not None:
        return cached

    # 车道①:意图命中秒回。
    intent_ans = _try_intent(question)
    if intent_ans is not None:
        _cache_put(question, intent_ans)
        return intent_ans

    # 车道②:检索(始终有结果,作为③的兜底底本)。
    search_ans = _try_search(question, staff if isinstance(staff, dict) else None)

    # 车道③:仅在开放式提问时才尝试综合;否则直接回检索。
    if _needs_synth(question):
        final_ans = _try_synth(question, search_ans)
    else:
        final_ans = search_ans

    _cache_put(question, final_ans)
    return final_ans


# ── /suggestions:当日"异动种"chips(vkpi_alerts 近24h + apify_jobs 近24h) ─────────
def _recent_alert_seeds() -> list[str]:
    """近24h open 告警标题 → 建议问题;缺表/异常返回空。"""
    try:
        from app.db.connection import get_conn, table_exists  # 懒 import
    except Exception:
        return []
    if not table_exists("vkpi_alerts"):
        return []
    seeds: list[str] = []
    try:
        rows = get_conn().execute(
            "SELECT title, severity FROM vkpi_alerts "
            "WHERE status = 'open' AND created_at >= NOW() - INTERVAL '24 hours' "
            "ORDER BY created_at DESC LIMIT 3",
            (),
        ).fetchall()
        for row in rows or []:
            d = dict(row)
            title = str(d.get("title") or "").strip()
            if title:
                seeds.append(f"告警「{title}」是什么原因,该怎么处理?")
    except Exception:
        return []
    return seeds


def _recent_apify_seeds() -> list[str]:
    """近24h 完成的 apify_jobs 量 → 一条汇总建议;缺表/异常返回空。"""
    try:
        from app.db.connection import get_conn, table_exists  # 懒 import
    except Exception:
        return []
    if not table_exists("apify_jobs"):
        return []
    try:
        row = get_conn().execute(
            "SELECT COUNT(*) AS n FROM apify_jobs "
            "WHERE status = 'done' AND created_at >= NOW() - INTERVAL '24 hours'",
            (),
        ).fetchone()
        n = int(dict(row).get("n") or 0) if row else 0
    except Exception:
        return []
    if n > 0:
        return [f"近24小时完成了 {n} 个抓取任务,有哪些新发现值得跟进?"]
    return []


@router.get("/suggestions")
def intelligent_suggestions(
    limit: int = Query(default=6, ge=3, le=6),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """当日建议问题 chips。异动种优先,不足用内置默认集补齐到 3-6 条。"""
    del staff
    seeds: list[str] = []
    seeds.extend(_recent_alert_seeds())
    seeds.extend(_recent_apify_seeds())

    # 去重(保序)+ 用默认集补齐到至少 3 条、至多 limit 条。
    ordered: list[str] = []
    seen: set[str] = set()
    for s in seeds + list(_DEFAULT_SUGGESTIONS):
        key = s.strip()
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
        if len(ordered) >= limit:
            break

    return {"suggestions": ordered[:limit], "source": "seeds" if seeds else "default"}
