"""件②:制作周期 + 竞业监控 — 纯聚合已有履约链/深析产物(零新采集/零 LLM/零写库)。

production_leadtime(kol_pool_id)
  「签收→发布」历史间隔:
  - 主链 = 观察窗口(迁移 128,vkpi_project_content_observation_windows):
    签收锚 = metadata_json.delivered_at(开窗时由 open_window_for_delivered 落下),
    缺则用 starts_at - window_offset_days[0](默认 7 天)反推;
    发布锚 = 内容帖子候选(迁移 129,vkpi_project_content_posts)published_at,
    配对优先级:matched_content_post_id 直连 > 同 assignment_id > 同项目同 kol_pool_id;
    间隔必须落在 [0, MAX_LEADTIME_DAYS] 才算有效样本(本地历史回填贴常见
    published < delivered,如实计入 diagnostics.pairs_out_of_range,绝不硬凑)。
  - 副链 = 该 KOL(经 vkpi_kol_pool.linked_main_kol_id → vkpi_projects.kol_id)名下
    项目的阶段事件 received → published(vkpi_project_stage_events);
    同项目已被主链取样则跳过(主链锚点更精确)。
  - 样本 <1 → status='empty' + diagnostics 诚实说明看了多少窗口/贴子/无效对。

competing_activity(kol_pool_id, window_days=90)
  近 window_days 天该 KOL 内容中的竞品露出:
  - 主源 = vkpi_analysis_cache 已 ready 的 video_analysis_final_v1 产物
    raw_gemini_video.competitor_mentions(与 layer1_visual_content.competitor_presence
    同源同结构,取并集去重),时间锚 = vkpi_kol_video_evidence.posted_at;
    posted_at 未知的露出进 undated_items 桶(诚实不冒充「近 90 天」)。
  - 副源 = vkpi_brand_signal 按 post_url ↔ evidence.content_url 对齐(表可能不存在
    或为空,防御读,贡献零行是诚实结果)。
  - 品牌归一复用 competitor_exposure 的词表助手(懒 import + 兜底原文),
    'viltrox' 自家品牌不算竞业。

红线:全程纯只读 SELECT;绝不触 viltrox_fit_score / rule_v0 / 归属判定;
compat SQL 一律 ? 占位;重依赖(get_conn / 兄弟模块)函数内懒 import。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 有效制作周期上限:签收后超过这个天数才发布,视为与该次履约无关(不算样本)。
MAX_LEADTIME_DAYS = 180
# 展示样本条数上限(median 用全量样本算,列表只回给前端前 N 条)。
MAX_SAMPLES_RETURNED = 20
# 竞业监控单 KOL 最多扫的深析视频数(全视频跑单号 ~250 条,300 足够)。
MAX_SCAN_ROWS = 300
# 竞业条目返回上限(近窗 + 无日期各自截断,防超长 payload)。
MAX_ITEMS_RETURNED = 50


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _loads(value: Any) -> Any:
    """jsonb/TEXT 列经 compat 读回可能是 str 也可能已是 list/dict,两头兼容。"""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _parse_ts(value: Any) -> datetime | None:
    """把 compat 读回的时间(ISO 字符串/datetime/date/None)统一成 aware UTC datetime。

    兼容 '2026-06-17 18:07:56+00:00'(metadata 里 str(datetime) 的写法)、
    '2026-07-05T02:37:20Z'(compat 序列化)、'2026-06-28'(DATE 列)。
    解析失败诚实返回 None,绝不猜。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(" ", "T", 1).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    raw = ordered[mid] if n % 2 == 1 else (ordered[mid - 1] + ordered[mid]) / 2.0
    return round(raw, 1)


def _pool_exists(conn: Any, kol_pool_id: int) -> bool:
    row = conn.execute("SELECT id FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    return bool(row)


# ── 制作周期(签收→发布) ─────────────────────────────────────────


def _window_delivered_at(win: dict[str, Any]) -> datetime | None:
    """一条观察窗口的「签收」时间:metadata.delivered_at 优先,缺则 starts_at 反推。"""
    meta = _loads(win.get("metadata_json")) or {}
    delivered = _parse_ts(meta.get("delivered_at")) if isinstance(meta, dict) else None
    if delivered is not None:
        return delivered
    starts = _parse_ts(win.get("starts_at"))
    if starts is None:
        return None
    offsets = meta.get("window_offset_days") if isinstance(meta, dict) else None
    try:
        offset0 = int(offsets[0]) if isinstance(offsets, (list, tuple)) and offsets else 7
    except (TypeError, ValueError):
        offset0 = 7
    return starts - timedelta(days=offset0)


def _pick_post_for_window(
    win: dict[str, Any],
    delivered: datetime,
    posts: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, int]:
    """为一条窗口挑发布贴:返回 (贴, 无效对计数)。

    优先级:matched_content_post_id 直连 > 同 assignment > 同项目同 KOL;
    同级里取 delivered 之后最早发布的一条;间隔出 [0, MAX] 的配对只计数不取样。
    """
    matched_id = win.get("matched_content_post_id")
    win_assignment = win.get("assignment_id")
    win_project = win.get("project_id")
    win_kol = win.get("kol_pool_id")

    def _rank(post: dict[str, Any]) -> int | None:
        if matched_id is not None and post.get("id") == matched_id:
            return 0
        if win_assignment is not None and post.get("assignment_id") == win_assignment:
            return 1
        if post.get("project_id") == win_project and post.get("kol_pool_id") == win_kol:
            return 2
        return None

    best: tuple[int, datetime, dict[str, Any]] | None = None
    out_of_range = 0
    for post in posts:
        rank = _rank(post)
        if rank is None:
            continue
        published = _parse_ts(post.get("published_at"))
        if published is None:
            continue  # 贴在但没发布时间:无法构成间隔,不计入无效对(是缺数据不是坏配对)
        days = (published - delivered).total_seconds() / 86400.0
        if days < 0 or days > MAX_LEADTIME_DAYS:
            out_of_range += 1
            continue
        if best is None or (rank, published) < (best[0], best[1]):
            best = (rank, published, post)
    return (best[2] if best else None), out_of_range


def production_leadtime(kol_pool_id: int) -> dict[str, Any]:
    """签收→发布历史间隔聚合。见模块 docstring;样本 <1 诚实 empty。"""
    from app.db.connection import get_conn

    kol_pool_id = int(kol_pool_id)
    conn = get_conn()
    if not _pool_exists(conn, kol_pool_id):
        raise LookupError(f"kol_pool {kol_pool_id} not found")

    # 主链:该 KOL 的观察窗口(任何 status 都可能已有内容贴;签收锚在 metadata)。
    windows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, project_id, assignment_id, kol_pool_id, starts_at, status,
                   matched_content_post_id, metadata_json
            FROM vkpi_project_content_observation_windows
            WHERE kol_pool_id = ?
            ORDER BY id DESC
            LIMIT 200
            """,
            (kol_pool_id,),
        ).fetchall()
    ]

    # 候选发布贴:本 KOL 名下 + 窗口直连/同派单的贴,一把查回内存配对。
    matched_ids = [w["matched_content_post_id"] for w in windows if w.get("matched_content_post_id") is not None]
    assignment_ids = [w["assignment_id"] for w in windows if w.get("assignment_id") is not None]
    clauses = ["kol_pool_id = ?"]
    params: list[Any] = [kol_pool_id]
    if matched_ids:
        clauses.append("id IN (" + ",".join("?" for _ in matched_ids) + ")")
        params.extend(int(i) for i in matched_ids)
    if assignment_ids:
        clauses.append("assignment_id IN (" + ",".join("?" for _ in assignment_ids) + ")")
        params.extend(int(i) for i in assignment_ids)
    posts = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, project_id, assignment_id, kol_pool_id, published_at, status, content_url
            FROM vkpi_project_content_posts
            WHERE """
            + " OR ".join(clauses)
            + """
            ORDER BY id DESC
            LIMIT 500
            """,
            tuple(params),
        ).fetchall()
    ]

    samples: list[dict[str, Any]] = []
    pairs_out_of_range = 0
    projects_sampled: set[Any] = set()
    for win in windows:
        delivered = _window_delivered_at(win)
        if delivered is None:
            continue
        post, invalid = _pick_post_for_window(win, delivered, posts)
        pairs_out_of_range += invalid
        if post is None:
            continue
        published = _parse_ts(post.get("published_at"))
        if published is None:
            continue
        days = round((published - delivered).total_seconds() / 86400.0, 1)
        samples.append(
            {
                "source": "observation_window",
                "window_id": win.get("id"),
                "project_id": win.get("project_id"),
                "assignment_id": win.get("assignment_id"),
                "delivered_at": _iso(delivered),
                "published_at": _iso(published),
                "days": days,
                "post_status": post.get("status"),
                "content_url": post.get("content_url"),
            }
        )
        if win.get("project_id") is not None:
            projects_sampled.add(win.get("project_id"))

    # 副链:该 KOL 名下项目的阶段事件 received→published(项目级,粒度粗于窗口链)。
    stage_rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT se.project_id, se.to_stage, se.effective_at
            FROM vkpi_project_stage_events se
            WHERE se.to_stage IN ('received', 'published')
              AND se.project_id IN (
                    SELECT pr.id
                    FROM vkpi_projects pr
                    JOIN vkpi_kol_pool kp ON kp.linked_main_kol_id = pr.kol_id
                    WHERE kp.id = ?
              )
            ORDER BY se.effective_at ASC
            LIMIT 400
            """,
            (kol_pool_id,),
        ).fetchall()
    ]
    stage_by_project: dict[Any, dict[str, datetime]] = {}
    for row in stage_rows:
        ts = _parse_ts(row.get("effective_at"))
        if ts is None:
            continue
        slot = stage_by_project.setdefault(row["project_id"], {})
        stage = str(row.get("to_stage") or "")
        # ORDER BY effective_at ASC:received 取最早;published 取 received 之后最早
        if stage == "received" and "received" not in slot:
            slot["received"] = ts
        elif stage == "published" and "published" not in slot and "received" in slot and ts >= slot["received"]:
            slot["published"] = ts
    stage_projects_seen = len(stage_by_project)
    for project_id, slot in stage_by_project.items():
        if project_id in projects_sampled:
            continue  # 主链已取样,窗口锚点更精确
        received, published = slot.get("received"), slot.get("published")
        if received is None or published is None:
            continue
        days = round((published - received).total_seconds() / 86400.0, 1)
        if days < 0 or days > MAX_LEADTIME_DAYS:
            pairs_out_of_range += 1
            continue
        samples.append(
            {
                "source": "stage_events",
                "project_id": project_id,
                "assignment_id": None,
                "delivered_at": _iso(received),
                "published_at": _iso(published),
                "days": days,
                "post_status": None,
                "content_url": None,
            }
        )

    samples.sort(key=lambda s: str(s.get("delivered_at") or ""), reverse=True)
    day_values = [float(s["days"]) for s in samples]
    status = "ok" if samples else "empty"
    return {
        "kol_pool_id": kol_pool_id,
        "status": status,
        "median_days": _median(day_values),
        "sample_count": len(samples),
        "samples": samples[:MAX_SAMPLES_RETURNED],
        "diagnostics": {
            "windows_seen": len(windows),
            "posts_seen": len(posts),
            "pairs_out_of_range": pairs_out_of_range,
            "stage_projects_seen": stage_projects_seen,
            "max_leadtime_days": MAX_LEADTIME_DAYS,
        },
        "note": (
            "签收→发布历史间隔:观察窗口链(签收锚=开窗 delivered_at)+ 项目阶段事件 received→published;"
            "无有效样本=履约链尚无「签收后发布」的完整记录(诚实空态,非系统故障)。"
        ),
        "computed_at": _utcnow_iso(),
        "provider_calls": False,
        "llm_calls": False,
    }


# ── 竞业监控(近 90 天竞品露出) ──────────────────────────────────


def _brand_helpers() -> tuple[Any, Any]:
    """复用 competitor_exposure 的品牌词表归一;拿不到就退化为原文直传(仍诚实可用)。"""
    try:
        from app.domains.kol import competitor_exposure as ce

        known = ce._known_brand_lookup()  # noqa: SLF001 — 同包兄弟模块,词表逻辑单一真源

        def canon(raw: Any) -> str:
            return ce._canon_brand(raw, known)  # noqa: SLF001

        def display(key: str) -> str:
            return ce._display_brand(key)  # noqa: SLF001

        return canon, display
    except Exception:  # noqa: BLE001 — 词表助手挂了不该拖垮竞业监控
        def canon(raw: Any) -> str:
            return str(raw or "").strip().lower()

        def display(key: str) -> str:
            return key.title() if key else key

        return canon, display


def _mention_entries(raw: Any) -> list[dict[str, Any]]:
    """competitor_mentions / competitor_presence → [{brand, scene, risk}](两键结构同源)。"""
    parsed = _loads(raw)
    if not isinstance(parsed, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in parsed:
        if isinstance(item, dict):
            brand = item.get("brand")
            if str(brand or "").strip():
                entries.append(
                    {
                        "brand": str(brand).strip(),
                        "scene": str(item.get("scene") or "").strip()[:200],
                        "risk": str(item.get("risk") or "").strip()[:200],
                    }
                )
        elif str(item or "").strip():
            entries.append({"brand": str(item).strip(), "scene": "", "risk": ""})
    return entries


def competing_activity(kol_pool_id: int, window_days: int = 90) -> dict[str, Any]:
    """近 window_days 天该 KOL 内容中的竞品露出。见模块 docstring;无露出诚实 empty。"""
    from app.db.connection import get_conn, table_exists

    kol_pool_id = int(kol_pool_id)
    window_days = max(7, min(365, int(window_days or 90)))
    conn = get_conn()
    if not _pool_exists(conn, kol_pool_id):
        raise LookupError(f"kol_pool {kol_pool_id} not found")

    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=window_days)
    cutoff_date = cutoff_dt.strftime("%Y-%m-%d")
    canon, display = _brand_helpers()

    # 主源:final_v1 深析产物的竞品提及(posted_at 缺失的行也取回,进 undated 桶)。
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT e.id AS evidence_id, e.content_url, e.video_title, e.platform, e.posted_at,
                   ac.result #> '{raw_gemini_video,competitor_mentions}' AS competitor_mentions,
                   ac.result #> '{layer1_visual_content,competitor_presence}' AS competitor_presence
            FROM vkpi_analysis_cache ac
            JOIN vkpi_kol_video_evidence e ON e.id = CAST(ac.target_id AS BIGINT)
            WHERE ac.target_type = 'video'
              AND ac.derive_method = 'video_analysis_final_v1'
              AND ac.status = 'ready'
              AND e.kol_pool_id = ?
              AND e.is_active IS NOT FALSE
              AND (e.posted_at IS NULL OR e.posted_at >= CAST(? AS DATE))
            ORDER BY e.id DESC
            LIMIT ?
            """,
            (kol_pool_id, cutoff_date, int(MAX_SCAN_ROWS)),
        ).fetchall()
    ]

    items: list[dict[str, Any]] = []
    undated_items: list[dict[str, Any]] = []
    brand_counts: dict[str, int] = {}
    seen: set[tuple[Any, str]] = set()  # (evidence_id/url, brand_key) 去重

    def _push(brand_key: str, when: datetime | None, entry: dict[str, Any], ref: dict[str, Any], source: str) -> None:
        brand_name = display(brand_key)
        record = {
            "brand": brand_name,
            "when": _iso(when) if when else None,
            "scene": entry.get("scene") or "",
            "risk": entry.get("risk") or "",
            "evidence_ref": ref,
            "source": source,
        }
        brand_counts[brand_name] = brand_counts.get(brand_name, 0) + 1
        (items if when is not None else undated_items).append(record)

    for row in rows:
        posted = _parse_ts(row.get("posted_at"))
        if posted is not None and posted < cutoff_dt:
            continue  # DATE 粒度边界:CAST 过滤后再按精确 cutoff 校一遍
        # 两个字段同源同结构,取并集按品牌去重(同视频同品牌只记一次)
        entries = _mention_entries(row.get("competitor_mentions")) + _mention_entries(row.get("competitor_presence"))
        ref = {
            "evidence_id": row.get("evidence_id"),
            "content_url": row.get("content_url"),
            "title": str(row.get("video_title") or "")[:160],
            "platform": row.get("platform"),
        }
        for entry in entries:
            key = canon(entry["brand"])
            if not key or key == "viltrox":
                continue  # 自家品牌不算竞业
            dedupe = (row.get("evidence_id"), key)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            _push(key, posted, entry, ref, "final_v1")

    # 副源:vkpi_brand_signal 按 post_url 对齐该 KOL 的 evidence(表可能不存在/为空,防御读)。
    brand_signal_rows = 0
    try:
        if table_exists("vkpi_brand_signal"):
            signal_rows = conn.execute(
                """
                SELECT bs.brand_name, bs.published_at, bs.post_url, bs.platform,
                       bs.signal_type, bs.brand_role
                FROM vkpi_brand_signal bs
                JOIN vkpi_kol_video_evidence e
                  ON e.content_url = bs.post_url AND e.kol_pool_id = ?
                WHERE e.is_active IS NOT FALSE
                ORDER BY bs.id DESC
                LIMIT 100
                """,
                (kol_pool_id,),
            ).fetchall()
            for r in signal_rows:
                row = dict(r)
                brand_signal_rows += 1
                key = canon(row.get("brand_name"))
                if not key or key == "viltrox":
                    continue
                published = _parse_ts(row.get("published_at"))
                if published is not None and published < cutoff_dt:
                    continue
                dedupe = (row.get("post_url"), key)
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                _push(
                    key,
                    published,
                    {"scene": str(row.get("signal_type") or ""), "risk": str(row.get("brand_role") or "")},
                    {"evidence_id": None, "content_url": row.get("post_url"), "title": "", "platform": row.get("platform")},
                    "brand_signal",
                )
    except Exception:  # noqa: BLE001 — 副源挂了不拖垮主源结果(增益信号,非阻塞)
        logger.debug("brand_signal 副源读取失败,跳过增益信号(best-effort)", exc_info=True)

    items.sort(key=lambda it: str(it.get("when") or ""), reverse=True)
    brands = sorted(
        ({"brand": name, "count": count} for name, count in brand_counts.items()),
        key=lambda b: (-int(b["count"]), str(b["brand"])),
    )
    status = "ok" if (items or undated_items) else "empty"
    return {
        "kol_pool_id": kol_pool_id,
        "status": status,
        "window_days": window_days,
        "items": items[:MAX_ITEMS_RETURNED],
        "undated_items": undated_items[:MAX_ITEMS_RETURNED],
        "brands": brands[:12],
        "scanned_videos": len(rows),
        "brand_signal_rows": brand_signal_rows,
        "note": (
            f"近 {window_days} 天竞品露出:纯读 final_v1 深析产物 competitor_mentions/competitor_presence"
            "(视频×品牌去重)+ brand_signal 按 URL 对齐;发布日期未知的露出单列 undated_items,不冒充近窗。"
        ),
        "computed_at": _utcnow_iso(),
        "provider_calls": False,
        "llm_calls": False,
    }
