"""公测演示种子(SH-03 / T 车道 8 个空端点)——默认 dry-run,--apply 才写库,--purge 清掉自己种的行。

所有行都带「演示」标记(三层):
  1. 标题 / 名称前缀「[演示]」——任何板块渲染标题就能看到标签;
  2. 有 JSON 列的表写 {"is_demo": true, "demo": true, "demo_seed": "beta_demo_v1"}
     (与 domains/intelligence 的 json_nonproduction_guard 口径一致:业务真相聚合自动排除);
  3. 稳定自然键(handle / uid / source_ref / template_key 都带 beta_demo)→ 幂等 + 可整批 purge。

8 个空端点 → 落点:
  events.candidate-staging       vkpi_dealer_event_candidates 2 行(pending / gate blocked)
  shopify.gmv                    刻意不种:GMV 只认 HMAC 核验的供应商订单快照(domains/business_truth),
                                 演示数据不伪造供应商核验 → 保持诚实空态(skipped_by_design)
  intelligent.ask-video-26mm-evo vkpi_kol_pool 1 位演示创作者 + vkpi_kol_video_evidence 2 条「26mm EVO」
  intelligent.ask-project-search vkpi_projects 1 个「[演示] Viltrox …」项目
  intelligent.ask-weekly-market  vkpi_comments 3 条近 3 天提及 Viltrox 的公开评论(演示)
  launchpad.publish-approvals    vkpi_publish_approvals 2 条 pending
  reports.history                vkpi_report_runs 1 条 ready 周报
  reports.weekly-read            vkpi_weekly_reports 1 条(source_data_status=partial,诚实标演示)

用法(仓库根;必须 .venv 解释器):
  PYTHONPATH=backend .venv/bin/python backend/scripts_local/seed_beta_demo.py                 # dry-run
  PYTHONPATH=backend .venv/bin/python backend/scripts_local/seed_beta_demo.py --apply         # 写库
  PYTHONPATH=backend .venv/bin/python backend/scripts_local/seed_beta_demo.py --purge --apply # 清演示行
  可选:--staff-id N(周报 / 项目 / 报告归到该员工,让 own-only 视角也看得到)、--org-id 1、--json
红线:不写 viltrox_fit_score;不触 rule_v0;不碰真实数据行(只按自己的自然键增删);裸 print 禁用。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_BACKEND_DIR)
for _path in (_BACKEND_DIR, os.path.join(_REPO_ROOT, "scripts")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from stdout_utils import out, out_json  # noqa: E402

SEED_TAG = "beta_demo_v1"
DEMO_PREFIX = "[演示]"
DEMO_JSON = json.dumps({"is_demo": True, "demo": True, "demo_seed": SEED_TAG}, ensure_ascii=False)
DEMO_HANDLE = "viltrox_beta_demo_creator"
DEMO_POOL_UID = "pool-beta-demo-0001"
DEMO_PROJECT_UID = "proj-beta-demo-0001"
DEMO_REPORT_UID = "report-beta-demo-0001"
DEMO_REGISTRY_ID = "beta_demo_seed_registry"
DEMO_SOURCE_TABLE = "beta_demo_seed"
DEMO_TEMPLATE_KEY = "beta_demo"
DEMO_DOMAIN = "https://demo.viltrox.invalid"
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass
class SeedContext:
    now: datetime
    pg: bool
    staff_id: int | None = None
    org_id: int = 1
    apply: bool = False


@dataclass
class Plan:
    board: str
    endpoint: str
    table: str
    key: str
    action: str  # insert | exists | deleted | absent | skipped_by_design | table_absent
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# 小工具
# ─────────────────────────────────────────────────────────────────────────────
def _ts(ctx: SeedContext, value: datetime) -> Any:
    return value if ctx.pg else value.strftime(_TS_FMT)


def _day(ctx: SeedContext, value: date) -> Any:
    return value if ctx.pg else value.isoformat()


def _exists(conn: Any, sql: str, params: tuple[Any, ...]) -> bool:
    return conn.execute(sql, params).fetchone() is not None


def _table_exists(conn: Any, table: str) -> bool:
    try:
        from app.db.connection import table_exists

        return bool(table_exists(table))
    except Exception:
        try:
            conn.execute(f"SELECT 1 FROM {table} LIMIT 0")
            return True
        except Exception:
            _rollback(conn)
            return False


def _has_column(conn: Any, table: str, column: str) -> bool:
    try:
        conn.execute(f"SELECT {column} FROM {table} LIMIT 0")
        return True
    except Exception:
        _rollback(conn)
        return False


def _rollback(conn: Any) -> None:
    try:
        conn.rollback()
    except Exception:
        return


def _ensure_row(
    conn: Any,
    ctx: SeedContext,
    plan: Plan,
    *,
    exists_sql: str,
    exists_params: tuple[Any, ...],
    insert_sql: str,
    insert_params: tuple[Any, ...],
) -> Plan:
    """幂等落一行:自然键已存在 → exists;否则 dry-run 只报 insert,--apply 真插。"""
    if _exists(conn, exists_sql, exists_params):
        plan.action = "exists"
        return plan
    plan.action = "insert"
    if ctx.apply:
        conn.execute(insert_sql, insert_params)
    return plan


def _purge_rows(conn: Any, ctx: SeedContext, plan: Plan, *, count_sql: str, delete_sql: str, params: tuple[Any, ...]) -> Plan:
    row = conn.execute(count_sql, params).fetchone()
    count = int((dict(row) if hasattr(row, "keys") else {"n": row[0] if row else 0}).get("n") or 0) if row is not None else 0
    plan.extra["rows"] = count
    plan.action = "deleted" if count else "absent"
    if ctx.apply and count:
        conn.execute(delete_sql, params)
    return plan


def _last_week(now: datetime) -> tuple[date, date]:
    today = now.date()
    this_monday = today - timedelta(days=today.weekday())
    start = this_monday - timedelta(days=7)
    return start, start + timedelta(days=6)


# ─────────────────────────────────────────────────────────────────────────────
# 逐板块种子
# ─────────────────────────────────────────────────────────────────────────────
def seed_event_candidates(conn: Any, ctx: SeedContext) -> list[Plan]:
    table = "vkpi_dealer_event_candidates"
    board, endpoint = "Events", "events.candidate-staging"
    if not _table_exists(conn, table):
        return [Plan(board, endpoint, table, "*", "table_absent", "迁移 257 未应用")]
    plans: list[Plan] = []
    samples = (
        (1, "PhotoPlus Expo 2026 · New York(示例活动)", "2026-10-22"),
        (2, "Bild Expo 2026 · Berlin(示例活动)", "2026-11-05"),
    )
    for n, title, starts_on in samples:
        cand_id = "cand_" + hashlib.md5(f"{SEED_TAG}:event:{n}".encode("utf-8")).hexdigest()
        payload = {
            "is_demo": True, "demo": True, "demo_seed": SEED_TAG,
            "title": f"{DEMO_PREFIX} {title}", "starts_on": starts_on,
            "summary": "公测演示数据:活动候选仅用于展示审核队列形态,不代表真实活动或 Viltrox 参展。",
        }
        payload_json = json.dumps(payload, ensure_ascii=False)
        plan = Plan(board, endpoint, table, cand_id, "insert")
        # candidate_payload_json 是 JSONB 列:PG 下 text 参数不会隐式转 jsonb,必须显式 ?::jsonb
        # (与 domains/source_passport_store 同款);sqlite 兼容层没有 jsonb,保留裸 ?。
        payload_slot = "?::jsonb" if ctx.pg else "?"
        plans.append(_ensure_row(
            conn, ctx, plan,
            exists_sql=f"SELECT 1 FROM {table} WHERE organization_id=? AND id=?",
            exists_params=(int(ctx.org_id), cand_id),
            insert_sql=(
                f"INSERT INTO {table} (organization_id, id, candidate_type, source_registry_id, source_entity_key, "
                "source_url, stable_org_key, stable_location_key, content_sha256, candidate_payload_json, "
                "review_status, promotion_gate_status, claim_status, created_at, updated_at) "
                f"VALUES (?,?,?,?,?,?,?,?,?,{payload_slot},?,?,?,?,?)"
            ),
            insert_params=(
                int(ctx.org_id), cand_id, "event_opportunity", DEMO_REGISTRY_ID, f"beta_demo:event:{n}",
                f"{DEMO_DOMAIN}/events/{n}", "", "", hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                payload_json, "pending", "blocked", "descriptive_only", _ts(ctx, ctx.now), _ts(ctx, ctx.now),
            ),
        ))
    return plans


def seed_shopify_gmv(conn: Any, ctx: SeedContext) -> list[Plan]:
    del conn, ctx
    return [Plan(
        "Shopify", "shopify.gmv", "vkpi_shopify_orders", "*", "skipped_by_design",
        "GMV 只认 HMAC 核验的供应商订单快照(domains/business_truth.verified_shopify_attribution_sql);"
        "演示数据不伪造供应商核验,保持诚实空态「待接入」。",
    )]


def _seed_demo_kol(conn: Any, ctx: SeedContext) -> tuple[Plan, int | None]:
    table = "vkpi_kol_pool"
    plan = Plan("KOL Pool", "intelligent.ask-video-26mm-evo", table, f"youtube/{DEMO_HANDLE}", "insert")
    row = conn.execute(f"SELECT id FROM {table} WHERE platform=? AND handle=?", ("youtube", DEMO_HANDLE)).fetchone()
    if row is not None:
        plan.action = "exists"
        return plan, int(dict(row).get("id") if hasattr(row, "keys") else row[0])
    if not ctx.apply:
        return plan, None
    cursor = conn.execute(
        f"INSERT INTO {table} (pool_uid, platform, handle, profile_url, display_name, bio, country, language, "
        "followers, avg_views, primary_topic, sync_status, source_type, source_ref, raw_platform_data, "
        "last_seen_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        (
            DEMO_POOL_UID, "youtube", DEMO_HANDLE, f"{DEMO_DOMAIN}/youtube/{DEMO_HANDLE}",
            f"{DEMO_PREFIX} Demo Lens Reviewer", "[演示数据] 公测演示用创作者档案,非真实账号。",
            "US", "en", 48200, 12600, "camera_lens_review", "imported", "demo_seed", SEED_TAG, DEMO_JSON,
            _ts(ctx, ctx.now), _ts(ctx, ctx.now), _ts(ctx, ctx.now),
        ),
    )
    inserted = cursor.fetchone()
    return plan, int(dict(inserted).get("id") if hasattr(inserted, "keys") else inserted[0])


def seed_video_topic(conn: Any, ctx: SeedContext) -> list[Plan]:
    board, endpoint = "Intelligent", "intelligent.ask-video-26mm-evo"
    if not _table_exists(conn, "vkpi_kol_pool") or not _table_exists(conn, "vkpi_kol_video_evidence"):
        return [Plan(board, endpoint, "vkpi_kol_video_evidence", "*", "table_absent")]
    kol_plan, kol_id = _seed_demo_kol(conn, ctx)
    plans = [kol_plan]
    has_title = _has_column(conn, "vkpi_kol_video_evidence", "title")
    videos = (
        ("26mm-evo-review", "Viltrox AF 26mm f/1.7 EVO review — the tiny wide prime", 2, 18400),
        ("26mm-evo-street", "26mm EVO vs 27mm f/1.2: street photography test (Viltrox)", 5, 9600),
    )
    for slug, title, days_ago, views in videos:
        url = f"{DEMO_DOMAIN}/video/{slug}"
        plan = Plan(board, endpoint, "vkpi_kol_video_evidence", url, "insert")
        if _exists(conn, "SELECT 1 FROM vkpi_kol_video_evidence WHERE content_url=?", (url,)):
            plan.action = "exists"
        elif ctx.apply and kol_id is not None:
            columns = "kol_pool_id, content_url, platform, video_title, posted_at, view_count, like_count, comment_count, source, source_ref, confidence, is_active"
            params: list[Any] = [
                int(kol_id), url, "youtube", f"{DEMO_PREFIX} {title}", _day(ctx, (ctx.now - timedelta(days=days_ago)).date()),
                views, int(views * 0.06), int(views * 0.004), "demo_seed", SEED_TAG, "low", True,
            ]
            if has_title:
                columns += ", title"
                params.append(f"{DEMO_PREFIX} {title}")
            conn.execute(
                f"INSERT INTO vkpi_kol_video_evidence ({columns}) VALUES ({','.join('?' for _ in params)})",
                tuple(params),
            )
        plans.append(plan)
    return plans


def seed_project(conn: Any, ctx: SeedContext) -> list[Plan]:
    table = "vkpi_projects"
    board, endpoint = "Projects", "intelligent.ask-project-search"
    if not _table_exists(conn, table):
        return [Plan(board, endpoint, table, "*", "table_absent")]
    columns = [
        "project_uid", "project_name", "assigned_staff_id", "created_by_staff_id", "product_sku", "product_name",
        "platform", "stage", "stage_status", "priority", "source_type", "metadata_json", "started_at", "last_activity_at",
    ]
    params: list[Any] = [
        DEMO_PROJECT_UID, f"{DEMO_PREFIX} Viltrox AF 26mm EVO 上市 · KOL 种草(示例项目)", ctx.staff_id, ctx.staff_id,
        "AF-26MM-F1.7-EVO", "Viltrox AF 26mm f/1.7 EVO", "youtube", "discovery", "active", "normal", "demo_seed",
        DEMO_JSON, _ts(ctx, ctx.now), _ts(ctx, ctx.now),
    ]
    if _has_column(conn, table, "is_public"):
        columns.append("is_public")
        params.append(True)
    plan = Plan(board, endpoint, table, DEMO_PROJECT_UID, "insert")
    return [_ensure_row(
        conn, ctx, plan,
        exists_sql=f"SELECT 1 FROM {table} WHERE project_uid=?",
        exists_params=(DEMO_PROJECT_UID,),
        insert_sql=f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({','.join('?' for _ in params)})",
        insert_params=tuple(params),
    )]


def seed_weekly_comments(conn: Any, ctx: SeedContext) -> list[Plan]:
    table = "vkpi_comments"
    board, endpoint = "市场之声", "intelligent.ask-weekly-market"
    if not _table_exists(conn, table):
        return [Plan(board, endpoint, table, "*", "table_absent")]
    samples = (
        (1, "Just got the Viltrox 26mm EVO — autofocus is snappy and the size is perfect for street.", 1, 42),
        (2, "Viltrox keeps shipping good glass at this price. Curious how the 26mm EVO handles flare.", 2, 17),
        (3, "唯卓仕这颗 26mm EVO 体积真小,想看夜景视频测试。", 3, 9),
    )
    plans: list[Plan] = []
    for n, text, days_ago, likes in samples:
        external_id = f"beta_demo_c{n}"
        plan = Plan(board, endpoint, table, f"youtube/{external_id}", "insert")
        plans.append(_ensure_row(
            conn, ctx, plan,
            exists_sql=f"SELECT 1 FROM {table} WHERE platform=? AND external_comment_id=?",
            exists_params=("youtube", external_id),
            insert_sql=(
                f"INSERT INTO {table} (post_table, external_post_id, platform, external_comment_id, comment_text, "
                "language_detected, author_handle, likes_count, reply_count, created_at, fetched_at, raw_data_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            ),
            insert_params=(
                DEMO_SOURCE_TABLE, "beta_demo_post_1", "youtube", external_id, f"{DEMO_PREFIX} {text}",
                "zh" if n == 3 else "en", f"demo_viewer_{n}", likes, 0,
                _ts(ctx, ctx.now - timedelta(days=days_ago)), _ts(ctx, ctx.now), DEMO_JSON,
            ),
        ))
    return plans


def seed_publish_approvals(conn: Any, ctx: SeedContext) -> list[Plan]:
    table = "vkpi_publish_approvals"
    board, endpoint = "LaunchPad", "launchpad.publish-approvals"
    if not _table_exists(conn, table):
        return [Plan(board, endpoint, table, "*", "table_absent", "迁移 173 未应用")]
    samples = (
        ("demo-1", "youtube", "26mm EVO 开箱短片 · 待审批(示例)"),
        ("demo-2", "instagram", "26mm EVO 街拍样片九宫格 · 待审批(示例)"),
    )
    plans: list[Plan] = []
    for source_id, platform, title in samples:
        plan = Plan(board, endpoint, table, f"{DEMO_SOURCE_TABLE}/{source_id}", "insert")
        plans.append(_ensure_row(
            conn, ctx, plan,
            exists_sql=f"SELECT 1 FROM {table} WHERE source_table=? AND source_id=?",
            exists_params=(DEMO_SOURCE_TABLE, source_id),
            insert_sql=(
                f"INSERT INTO {table} (source_table, source_id, platform, account_handle, title, status, note, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)"
            ),
            insert_params=(
                DEMO_SOURCE_TABLE, source_id, platform, DEMO_HANDLE, f"{DEMO_PREFIX} {title}", "pending",
                f"is_demo=true demo_seed={SEED_TAG}", _ts(ctx, ctx.now), _ts(ctx, ctx.now),
            ),
        ))
    return plans


def seed_report_run(conn: Any, ctx: SeedContext) -> list[Plan]:
    table = "vkpi_report_runs"
    board, endpoint = "Reports", "reports.history"
    if not _table_exists(conn, table):
        return [Plan(board, endpoint, table, "*", "table_absent")]
    start, end = _last_week(ctx.now)
    period_start = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    period_end = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)
    plan = Plan(board, endpoint, table, DEMO_REPORT_UID, "insert")
    return [_ensure_row(
        conn, ctx, plan,
        exists_sql=f"SELECT 1 FROM {table} WHERE report_uid=?",
        exists_params=(DEMO_REPORT_UID,),
        insert_sql=(
            f"INSERT INTO {table} (report_uid, report_type, period_start, period_end, scope_type, scope_id, "
            "triggered_by_staff_id, triggered_at, status, summary_text, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        ),
        insert_params=(
            DEMO_REPORT_UID, "weekly", _ts(ctx, period_start), _ts(ctx, period_end), "all", None, ctx.staff_id,
            _ts(ctx, ctx.now), "ready",
            f"{DEMO_PREFIX} 本周 KOL 触达 3 人、待审批内容 2 条、演示项目 1 个;GMV 未接入(示例摘要,非真实业务数据)。",
            DEMO_JSON,
        ),
    )]


def seed_weekly_report(conn: Any, ctx: SeedContext) -> list[Plan]:
    table = "vkpi_weekly_reports"
    board, endpoint = "Reports", "reports.weekly-read"
    if not _table_exists(conn, table):
        return [Plan(board, endpoint, table, "*", "table_absent")]
    start, end = _last_week(ctx.now)
    columns = ["staff_id", "layer", "template_key", "period_start", "period_end", "title", "body_md", "llm_provider", "status", "generated_at"]
    body = "\n".join((
        f"# {DEMO_PREFIX} 本周周报(示例)",
        "",
        "> 本报告为公测演示数据,内容由种子脚本生成,不代表真实业务表现。",
        "",
        "## 本周要点",
        "- 新增演示创作者 1 位,26mm EVO 相关视频 2 条(演示)",
        "- 待审批发布内容 2 条(演示)",
        "- 市场之声:3 条演示评论提及 Viltrox 26mm EVO",
        "",
        "## 数据来源",
        "演示种子 `seed_beta_demo.py`(is_demo=true),真实接入后本报告会被自动生成的周报替代。",
    ))
    params: list[Any] = [ctx.staff_id, 1, DEMO_TEMPLATE_KEY, _day(ctx, start), _day(ctx, end), f"{DEMO_PREFIX} 本周周报(示例)", body, "demo_seed", "draft", _ts(ctx, ctx.now)]
    if _has_column(conn, table, "source_data_status"):
        columns += ["source_data_status", "source_count", "source_is_partial"]
        params += ["partial", 1, True]
    plan = Plan(board, endpoint, table, f"{DEMO_TEMPLATE_KEY}/{start.isoformat()}", "insert")
    return [_ensure_row(
        conn, ctx, plan,
        exists_sql=f"SELECT 1 FROM {table} WHERE template_key=? AND period_start=? AND COALESCE(staff_id, 0)=?",
        exists_params=(DEMO_TEMPLATE_KEY, _day(ctx, start), int(ctx.staff_id or 0)),
        insert_sql=f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({','.join('?' for _ in params)})",
        insert_params=tuple(params),
    )]


SEEDERS = (
    seed_event_candidates,
    seed_shopify_gmv,
    seed_video_topic,
    seed_project,
    seed_weekly_comments,
    seed_publish_approvals,
    seed_report_run,
    seed_weekly_report,
)


# ─────────────────────────────────────────────────────────────────────────────
# purge(只按自己的自然键删)
# ─────────────────────────────────────────────────────────────────────────────
PURGE_TARGETS: tuple[tuple[str, str, str, tuple[Any, ...]], ...] = (
    ("Events", "vkpi_dealer_event_candidates", "source_registry_id=?", (DEMO_REGISTRY_ID,)),
    ("Intelligent", "vkpi_kol_video_evidence", "source=? AND source_ref=?", ("demo_seed", SEED_TAG)),
    ("KOL Pool", "vkpi_kol_pool", "source_type=? AND handle=?", ("demo_seed", DEMO_HANDLE)),
    ("Projects", "vkpi_projects", "project_uid=?", (DEMO_PROJECT_UID,)),
    ("市场之声", "vkpi_comments", "post_table=? AND external_comment_id LIKE ?", (DEMO_SOURCE_TABLE, "beta_demo_c%")),
    ("LaunchPad", "vkpi_publish_approvals", "source_table=?", (DEMO_SOURCE_TABLE,)),
    ("Reports", "vkpi_report_runs", "report_uid=?", (DEMO_REPORT_UID,)),
    ("Reports", "vkpi_weekly_reports", "template_key=?", (DEMO_TEMPLATE_KEY,)),
)


def purge_all(conn: Any, ctx: SeedContext) -> list[Plan]:
    plans: list[Plan] = []
    for board, table, where, params in PURGE_TARGETS:
        plan = Plan(board, "purge", table, where, "absent")
        if not _table_exists(conn, table):
            plan.action = "table_absent"
            plans.append(plan)
            continue
        plans.append(_purge_rows(
            conn, ctx, plan,
            count_sql=f"SELECT COUNT(*) AS n FROM {table} WHERE {where}",
            delete_sql=f"DELETE FROM {table} WHERE {where}",
            params=params,
        ))
    return plans


def seed_all(conn: Any, ctx: SeedContext) -> list[Plan]:
    plans: list[Plan] = []
    for seeder in SEEDERS:
        plans.extend(seeder(conn, ctx))
    return plans


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="公测演示种子(默认 dry-run)")
    parser.add_argument("--apply", action="store_true", help="真写库(缺省只报计划)")
    parser.add_argument("--purge", action="store_true", help="清掉本脚本种下的演示行(仍需 --apply 才真删)")
    parser.add_argument("--staff-id", type=int, default=None, help="周报/项目/报告归属员工(own-only 视角可见)")
    parser.add_argument("--org-id", type=int, default=1, help="活动候选的 organization_id(默认 1)")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出计划")
    return parser.parse_args(argv)


def _summarize(plans: list[Plan]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for plan in plans:
        summary[plan.action] = summary.get(plan.action, 0) + 1
    return summary


def _render(plans: list[Plan], ctx: SeedContext, *, purge: bool, as_json: bool) -> None:
    mode = ("PURGE" if purge else "SEED") + ("/APPLY" if ctx.apply else "/DRY-RUN")
    if as_json:
        out_json({"mode": mode, "seed_tag": SEED_TAG, "summary": _summarize(plans), "plans": [asdict(p) for p in plans]}, ensure_ascii=False)
        return
    out(f"[{mode}] seed_tag={SEED_TAG} staff_id={ctx.staff_id} org_id={ctx.org_id}")
    for plan in plans:
        suffix = f" — {plan.detail}" if plan.detail else ""
        rows = f" rows={plan.extra['rows']}" if "rows" in plan.extra else ""
        out(f"  {plan.action:<18} {plan.board:<10} {plan.endpoint:<32} {plan.table}:{plan.key}{rows}{suffix}")
    out(f"[{mode}] summary={_summarize(plans)}")
    if not ctx.apply:
        out(f"[{mode}] 未写库。确认后加 --apply 执行。")


def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from app.db.connection import db_connection_sync_scope, get_conn, is_postgres_runtime

    with db_connection_sync_scope():
        conn = get_conn()
        ctx = SeedContext(now=datetime.now(timezone.utc), pg=bool(is_postgres_runtime()), staff_id=args.staff_id, org_id=int(args.org_id), apply=bool(args.apply))
        try:
            plans = purge_all(conn, ctx) if args.purge else seed_all(conn, ctx)
            if ctx.apply:
                conn.commit()
            else:
                _rollback(conn)
        except Exception:
            _rollback(conn)
            raise
    _render(plans, ctx, purge=bool(args.purge), as_json=bool(args.json))
    return 0


if __name__ == "__main__":
    os.chdir(_REPO_ROOT)  # app.core.config 按 cwd 读根 .env
    sys.exit(run(sys.argv[1:]))
