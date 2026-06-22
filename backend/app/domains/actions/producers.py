"""W1 Action Inbox — 9 类建议生产者(纯 SELECT,只产不执行)。

每个 produce_* 函数:跑一条只读查询 → 返回 suggestion dict 列表(给 inbox.py 落库)。
设计原则:
- 完全只读。绝不 INSERT/UPDATE 任何业务表,绝不 SELECT 后回写 viltrox_fit_score。
- 容错隔离:由 inbox.py 用 try/except 包每个 producer;单个查询坏(列名漂移等)→ 该类返回空,
  不拖垮其余 7 类。这里也只引用已在迁移里确认存在的列。
- cutoff 在 Python 算好传参(避 psycopg 的 `%`/INTERVAL 兼容坑)。
- owner_staff_id:项目/活动类按其归属成员解析(成员只看自己的);KOL/任务/库存类=NULL(公司级,仅管理层可见)。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


# 路线0:每类建议的「预计收益(人话)+ 风险等级」默认(producer 可覆盖)。
# 风险=执行该动作的风险(非优先级);收益=做了能带来什么。让每条建议都自带决策四件套。
_CATEGORY_GAIN_RISK: dict[str, tuple[str, str]] = {
    "kol_profile": ("补全资料后可纳入项目 / 提升触达准确度", "low"),
    "deep_missing": ("补深析后可判断合作价值与产品契合度", "low"),
    "failed_retry": ("重试恢复抓取,避免数据断流", "low"),
    "project_observation": ("开观察窗后自动追踪 KOL 发布", "low"),
    "content_candidate": ("确认内容归属后纳入 ROI / 复盘", "low"),
    "retrospective": ("复盘沉淀经验,影响下次推荐", "low"),
    "kol_profile_refresh": ("增量刷新画像,保持数据新鲜", "low"),
    "event_followup": ("回填活动数据后闭环效果评估", "medium"),
    "inventory_low": ("及时补货避免影响寄送 / 活动", "medium"),
    "project_shared_to_you": ("查看共享项目,协作跟进", "low"),
}


# ── suggestion 构造 ───────────────────────────────────────────────────
def make_suggestion(
    *,
    category: str,
    dedupe_key: str,
    title: str,
    detail: str,
    reason: str,
    priority: str = "medium",
    entity_type: str = "",
    entity_id: str = "",
    suggested_endpoint: str = "",
    estimated_cost_cents: int = 0,
    writes_business_data: bool = False,
    uses_llm: bool = False,
    requires_approval: bool = True,
    owner_staff_id: int | None = None,
    payload: dict[str, Any] | None = None,
    expected_gain: str = "",
    risk_level: str = "",
    evidence_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """统一一条建议的结构(字段与 vkpi_action_inbox 列对齐)。touches_v6_fit 永不出现——表 CHECK 兜底。

    路线0:决策四件套 = reason(为什么)+ estimated_cost_cents(成本)+ expected_gain(收益)+
    risk_level(风险)+ evidence_refs(证据来源)。收益/风险缺省按 category 取合理默认;证据缺省
    指向实体本身({type:entity_type,id:entity_id}),让每条建议都可追溯到来源数据。
    """
    gain_default, risk_default = _CATEGORY_GAIN_RISK.get(category, ("", "low"))
    gain = (expected_gain or gain_default).strip()
    rl = (risk_level or risk_default).strip().lower()
    rl = rl if rl in ("low", "medium", "high") else "low"
    refs = list(evidence_refs) if isinstance(evidence_refs, list) else []
    if not refs and entity_type and str(entity_id):
        refs = [{"type": str(entity_type), "id": str(entity_id)}]
    return {
        "category": category,
        "dedupe_key": dedupe_key,
        "title": title,
        "detail": detail,
        "reason": reason,
        "priority": priority if priority in ("high", "medium", "low") else "medium",
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "suggested_endpoint": suggested_endpoint,
        "estimated_cost_cents": max(0, int(estimated_cost_cents)),
        "writes_business_data": bool(writes_business_data),
        "uses_llm": bool(uses_llm),
        # 红线2:烧 LLM 或写业务表的动作一律需人审
        "requires_approval": bool(requires_approval or uses_llm or writes_business_data),
        "owner_staff_id": int(owner_staff_id) if owner_staff_id not in (None, "", 0) else None,
        "payload": payload or {},
        # 路线0 决策四件套(收益/风险/证据)。
        "expected_gain": gain,
        "risk_level": rl,
        "evidence_refs": refs,
    }


def _owner_of_project(row: dict[str, Any]) -> int | None:
    val = row.get("assigned_staff_id") or row.get("created_by_staff_id")
    try:
        return int(val) if val not in (None, "", 0) else None
    except (TypeError, ValueError):
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── 1. kol_profile:KOL 资料缺失,建议补抓 ─────────────────────────────
def produce_kol_profile(conn: Any, *, limit: int = 25) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, pool_uid, platform, handle, display_name, avatar_url, followers, posts_count
        FROM vkpi_kol_pool
        WHERE followers IS NULL OR display_name = '' OR avatar_url = ''
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        missing = [
            name
            for name, val in (("粉丝数", row.get("followers")), ("昵称", row.get("display_name")), ("头像", row.get("avatar_url")))
            if val in (None, "")
        ]
        handle = row.get("handle") or row.get("pool_uid")
        out.append(
            make_suggestion(
                category="kol_profile",
                dedupe_key=f"kol_profile:kol:{row['id']}",
                title=f"补全 KOL 资料 · @{handle}",
                detail=f"{row.get('platform', '')} @{handle} 缺:{'、'.join(missing) or '部分资料'}",
                reason="Profile 字段缺失会拖累分层/召回质量,建议跑一次资料抓取补齐。",
                priority="low",
                entity_type="kol",
                entity_id=row["id"],
                suggested_endpoint="POST /api/admin/vkpi/kol-pool/{id}/refresh-profile",
                writes_business_data=True,  # 写 profile 字段(绝非 viltrox_fit_score)
                uses_llm=False,
                payload={"platform": row.get("platform"), "handle": handle, "missing": missing},
            )
        )
    return out


# ── 2. deep_missing:无深析结果的 KOL,建议跑深析 ─────────────────────
def produce_deep_missing(conn: Any, *, limit: int = 25) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT kp.id, kp.pool_uid, kp.platform, kp.handle
        FROM vkpi_kol_pool kp
        WHERE NOT EXISTS (
            SELECT 1 FROM vkpi_kol_llm_deep_analysis_results d
            WHERE d.kol_pool_id = kp.id AND d.status = 'ready'
        )
        ORDER BY kp.created_at DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        handle = row.get("handle") or row.get("pool_uid")
        out.append(
            make_suggestion(
                category="deep_missing",
                dedupe_key=f"deep_missing:kol:{row['id']}",
                title=f"深度分析待跑 · @{handle}",
                detail=f"{row.get('platform', '')} @{handle} 尚无 ready 的 LLM 深析结果。",
                reason="深析驱动 11 维与内容契合;无深析的 KOL 决策缺据。建议跑一次(烧 LLM,需人审)。",
                priority="medium",
                entity_type="kol",
                entity_id=row["id"],
                suggested_endpoint="POST /api/admin/vkpi/kol-url-deep-crawl",
                estimated_cost_cents=5,
                writes_business_data=True,  # 写 deep_results(非 viltrox_fit_score)
                uses_llm=True,
                payload={"platform": row.get("platform"), "handle": handle},
            )
        )
    return out


# ── 3. failed_retry:近 7 天失败/受阻任务,建议按桶重试 ─────────────
def produce_failed_retry(conn: Any, *, limit: int = 25) -> list[dict[str, Any]]:
    cutoff = _now() - timedelta(days=7)
    rows = conn.execute(
        """
        SELECT id, job_type, status, last_error, last_error_category, attempts, updated_at
        FROM apify_jobs
        WHERE status IN ('failed', 'blocked') AND updated_at >= ?
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (cutoff, int(limit)),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        bucket = row.get("last_error_category") or "unknown"
        err = (row.get("last_error") or "")[:160]
        out.append(
            make_suggestion(
                category="failed_retry",
                dedupe_key=f"failed_retry:job:{row['id']}",
                title=f"失败任务待重试 · {row.get('job_type', 'job')}",
                detail=f"#{row['id']} [{row.get('status')}] 失败桶={bucket} 已试={row.get('attempts', 0)}次。{err}",
                reason=f"任务因『{bucket}』失败/受阻;按失败桶选择重试策略可回收产出。",
                priority="high",
                entity_type="job",
                entity_id=row["id"],
                suggested_endpoint="POST /api/admin/vkpi/jobs/{id}/retry",
                writes_business_data=True,
                uses_llm=False,
                payload={"job_type": row.get("job_type"), "bucket": bucket, "attempts": row.get("attempts")},
            )
        )
    return out


# ── 4. project_observation:已签收满 7 天但无观察窗口,建议开窗 ──────
def produce_project_observation(conn: Any, *, limit: int = 25) -> list[dict[str, Any]]:
    cutoff = _now() - timedelta(days=7)
    rows = conn.execute(
        """
        SELECT s.id AS shipment_id, s.project_id, s.delivered_at,
               p.project_uid, p.project_name, p.assigned_staff_id, p.created_by_staff_id
        FROM vkpi_shipments s
        JOIN vkpi_projects p ON p.id = s.project_id
        WHERE s.delivered_at IS NOT NULL AND s.delivered_at < ?
          AND NOT EXISTS (
              SELECT 1 FROM vkpi_project_content_observation_windows w
              WHERE w.project_id = s.project_id AND w.status NOT IN ('closed', 'content_missing')
          )
        ORDER BY s.delivered_at ASC
        LIMIT ?
        """,
        (cutoff, int(limit)),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        name = row.get("project_name") or row.get("project_uid")
        out.append(
            make_suggestion(
                category="project_observation",
                dedupe_key=f"project_observation:project:{row['project_id']}",
                title=f"开内容观察窗 · {name}",
                detail=f"项目「{name}」样品已签收(>7天)但还没开内容观察窗口。",
                reason="签收后应进入内容观察期以抓发布证据;此项已逾期未开窗。",
                priority="medium",
                entity_type="project",
                entity_id=row["project_id"],
                suggested_endpoint="POST /api/admin/vkpi/projects/observation-scan",
                writes_business_data=True,  # 建观察窗(非业务裁决)
                uses_llm=False,
                owner_staff_id=_owner_of_project(row),
                payload={"shipment_id": row.get("shipment_id"), "project_uid": row.get("project_uid")},
            )
        )
    return out


# ── 5. content_candidate:命中内容候选待人工确认 ──────────────────────
def produce_content_candidate(conn: Any, *, limit: int = 25) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT pcp.id, pcp.project_id, pcp.kol_pool_id, pcp.content_url, pcp.platform, pcp.match_confidence,
               p.project_name, p.project_uid, p.assigned_staff_id, p.created_by_staff_id
        FROM vkpi_project_content_posts pcp
        JOIN vkpi_projects p ON p.id = pcp.project_id
        WHERE pcp.status = 'candidate'
        ORDER BY pcp.match_confidence DESC, pcp.created_at DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        name = row.get("project_name") or row.get("project_uid")
        conf = row.get("match_confidence")
        try:
            conf_pct = f"{float(conf) * 100:.0f}%" if conf is not None else "—"
        except (TypeError, ValueError):
            conf_pct = "—"
        out.append(
            make_suggestion(
                category="content_candidate",
                dedupe_key=f"content_candidate:post:{row['id']}",
                title=f"内容候选待确认 · {name}",
                detail=f"项目「{name}」匹配到 {row.get('platform', '')} 内容(置信 {conf_pct}),待人工确认是否计入履约。",
                reason="自动匹配的发布内容需人工确认后才计入履约,避免误判。",
                priority="medium",
                entity_type="project",
                entity_id=row["project_id"],
                suggested_endpoint="PATCH /api/admin/vkpi/projects/content-posts/{id}",
                writes_business_data=True,
                uses_llm=False,
                owner_staff_id=_owner_of_project(row),
                payload={"post_id": row.get("id"), "content_url": row.get("content_url"), "confidence": conf},
            )
        )
    return out


# ── 6. retrospective:已完成项目但无复盘,建议生成复盘 ────────────────
def produce_retrospective(conn: Any, *, limit: int = 25) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.id, p.project_uid, p.project_name, p.stage, p.assigned_staff_id, p.created_by_staff_id, p.closed_at
        FROM vkpi_projects p
        WHERE p.stage IN ('completed', 'closed')
          AND NOT EXISTS (
              SELECT 1 FROM apify_jobs j
              WHERE j.job_type = 'project_retrospective_aggregate'
                AND j.payload->>'target_type' = 'project'
                AND j.payload->>'target_id' = CAST(p.id AS TEXT)
                AND j.status IN ('done', 'queued', 'running')
          )
        ORDER BY p.closed_at DESC NULLS LAST
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        name = row.get("project_name") or row.get("project_uid")
        out.append(
            make_suggestion(
                category="retrospective",
                dedupe_key=f"retrospective:project:{row['id']}",
                title=f"生成项目复盘 · {name}",
                detail=f"项目「{name}」已结项({row.get('stage')})但还没跑复盘聚合。",
                reason="结项项目应沉淀复盘(聚合已 ready 的 final_v1 分析);此项尚无复盘任务。",
                priority="low",
                entity_type="project",
                entity_id=row["id"],
                suggested_endpoint="POST /api/admin/vkpi/projects/{id}/retrospective",
                estimated_cost_cents=3,
                writes_business_data=True,
                uses_llm=True,
                owner_staff_id=_owner_of_project(row),
                payload={"project_uid": row.get("project_uid"), "stage": row.get("stage")},
            )
        )
    return out


# ── 7. event_followup:已结束活动但 ROI/线索/复盘未填,提醒收尾 ───────
def produce_event_followup(conn: Any, *, limit: int = 25) -> list[dict[str, Any]]:
    cutoff = _now() - timedelta(days=90)
    rows = conn.execute(
        """
        SELECT id, title, status, end_date, roi, leads, retrospective, owner_id
        FROM vkpi_events
        WHERE status = 'completed' AND end_date >= ?
          AND (roi IS NULL OR leads IS NULL OR retrospective = '' OR retrospective IS NULL)
        ORDER BY end_date DESC
        LIMIT ?
        """,
        (cutoff.date(), int(limit)),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        missing = [
            name
            for name, val in (("ROI", row.get("roi")), ("线索数", row.get("leads")), ("复盘", row.get("retrospective")))
            if val in (None, "")
        ]
        owner = row.get("owner_id")
        out.append(
            make_suggestion(
                category="event_followup",
                dedupe_key=f"event_followup:event:{row['id']}",
                title=f"活动收尾待补 · {row.get('title')}",
                detail=f"活动「{row.get('title')}」已结束,待补:{'、'.join(missing) or '收尾数据'}。",
                reason="活动结束后应回填 ROI/线索/复盘以闭环效果评估;此活动尚缺。",
                priority="medium",
                entity_type="event",
                entity_id=row["id"],
                suggested_endpoint="PATCH /api/admin/vkpi/events/{id}",
                writes_business_data=False,  # 不臆造业务数值;执行=受理+留痕(真值仍人工回填)
                uses_llm=False,
                requires_approval=True,  # R6:走 approve→execute 人审闸,执行=受理并留痕
                owner_staff_id=int(owner) if owner not in (None, "", 0) else None,
                payload={"missing": missing, "end_date": str(row.get("end_date"))},
            )
        )
    return out


# ── 8. inventory_low:库存低于阈值,提醒补货 ───────────────────────────
def produce_inventory_low(conn: Any, *, threshold: int = 5, limit: int = 25) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, sku, name, category, qty
        FROM vkpi_inventory
        WHERE qty < ? AND is_sample = FALSE
        ORDER BY qty ASC
        LIMIT ?
        """,
        (int(threshold), int(limit)),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        qty = row.get("qty") or 0
        out.append(
            make_suggestion(
                category="inventory_low",
                dedupe_key=f"inventory_low:inventory:{row['id']}",
                title=f"库存偏低 · {row.get('name')}",
                detail=f"{row.get('category', '')} {row.get('name')}(SKU {row.get('sku')})当前仅 {qty} 件。",
                reason="可用库存低于补货阈值,建议补货以免影响寄送/活动。",
                priority="high" if int(qty) <= 0 else "medium",
                entity_type="inventory",
                entity_id=row["id"],
                suggested_endpoint="PATCH /api/admin/vkpi/inventory/{id}",
                writes_business_data=False,  # 不自动改 qty/下单;执行=受理+留痕(补货仍人工)
                uses_llm=False,
                requires_approval=True,  # R6:走 approve→execute 人审闸,执行=受理并留痕
                payload={"sku": row.get("sku"), "qty": qty, "threshold": int(threshold)},
            )
        )
    return out


# ── 9. project_shared_to_you:项目被显式共享给成员,提醒查看/协作 ──────
def produce_shared_to_you(conn: Any, *, limit: int = 25) -> list[dict[str, Any]]:
    """W4 第 9 类(2026-06-14):为「被分享给我」的项目产纯提醒建议。

    主源 = vkpi_project_members(131,live 成员关系),JOIN vkpi_projects 取项目名。
    不用 share_audit(137 是 append-only 含 revoke 的审计日志,会把已撤销的也算进来)。
    排除「被分享成员本人就是负责/创建人」的自分享噪音。owner_staff_id=被分享成员——
    成员只看自己的(inbox.py own gate 据此过滤)。纯提醒:不写业务表、不烧 LLM、无端点。
    """
    rows = conn.execute(
        """
        SELECT m.project_id, m.staff_id AS member_staff_id, m.role, m.created_at AS shared_at,
               p.project_uid, p.project_name
        FROM vkpi_project_members m
        JOIN vkpi_projects p ON p.id = m.project_id
        WHERE m.staff_id IS NOT NULL
          AND m.staff_id <> COALESCE(p.assigned_staff_id, 0)
          AND m.staff_id <> COALESCE(p.created_by_staff_id, 0)
        ORDER BY m.created_at DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        name = row.get("project_name") or row.get("project_uid")
        role = str(row.get("role") or "viewer").strip().lower()
        editable = "可编辑" if role == "editor" else "只读"
        out.append(
            make_suggestion(
                category="project_shared_to_you",
                dedupe_key=f"shared_to_you:project:{row['project_id']}:{row['member_staff_id']}",
                title=f"项目已共享给你 · {name}",
                detail=f"「{name}」以 {role}({editable})共享给你。",
                reason="此项目被显式共享给你,可在项目跟进里查看/协作。",
                priority="low",
                entity_type="project",
                entity_id=row["project_id"],
                suggested_endpoint="",  # 纯提醒,不给执行端点
                estimated_cost_cents=0,
                writes_business_data=False,  # 硬约束:不写业务表
                uses_llm=False,
                requires_approval=False,  # 纯提醒
                owner_staff_id=row["member_staff_id"],  # 必填=被分享成员
                payload={
                    "project_uid": row.get("project_uid"),
                    "role": role,
                    "shared_at": str(row.get("shared_at")),
                },
            )
        )
    return out


# 编排器按此顺序遍历;键即 category,值即 producer 可调用对象。
PRODUCERS: dict[str, Any] = {
    "kol_profile": produce_kol_profile,
    "deep_missing": produce_deep_missing,
    "failed_retry": produce_failed_retry,
    "project_observation": produce_project_observation,
    "content_candidate": produce_content_candidate,
    "retrospective": produce_retrospective,
    "event_followup": produce_event_followup,
    "inventory_low": produce_inventory_low,
    "project_shared_to_you": produce_shared_to_you,
}
