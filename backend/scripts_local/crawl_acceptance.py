"""抓取链端到端验收(LE 车道)——给若干条查询跑完整链,逐阶段出「产出 / 原因」表。

回答的是运营那句「搜了没结果,到底断在哪一节」:一条查询走
  ① 会话 → ② 候选召回 → ③ 档案落库 → ④ 派生抓取任务
四节,每节都报**产出多少**和**为什么是这个数**,断在哪一节一眼可见。

默认 dry-run(**零写库、零取数、零花钱**):只读库里已有的会话与派生任务,
把「若 --apply 会发起什么」按批次列出来。加 --apply 才真发起,且必须带 --staff-id
——没有真身份,派生的付费任务在 worker 侧一律因缺授权被拦(本地 9 月 11 条就是这么来的),
脚本**不静默降级**,直接报错退出。

原因归一与只读端点 /api/admin/vkpi/ops/crawl-health 同源(共用
``app.api.routers.vkpi_crawl_health`` 的码表),保证脚本表与运维卡口径逐字一致。

用法(仓库根;必须 .venv 解释器,裸 python3 缺依赖会静默降级):
  ENABLE_SCHEDULER=0 APP_ROLE=admin-web PYTHONPATH=backend \\
      .venv/bin/python backend/scripts_local/crawl_acceptance.py            # dry-run 3 条
  ... crawl_acceptance.py -k 5 --queries recent                             # 复查最近 5 条真实会话
  ... crawl_acceptance.py -k 2 --apply --staff-id 40                        # 真发起(花钱)

红线:不写 viltrox_fit_score;不触 rule_v0;dry-run 绝不写库;裸 print 禁用。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_BACKEND_DIR)
for _path in (_BACKEND_DIR, os.path.join(_REPO_ROOT, "scripts")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from stdout_utils import out, out_json  # noqa: E402

DEFAULT_QUERY_FILE = "scripts/kol_search_60_golden_queries.json"
DEFAULT_BATCH = 3
MAX_BATCH = 10  # 批次硬上限:--apply 一次最多发起这么多条,防手滑烧预算
POLL_INTERVAL_SEC = 10
TERMINAL_STATES: tuple[str, ...] = ("done", "failed", "blocked", "triage")

STAGE_SESSION = "① 会话"
STAGE_RECALL = "② 候选召回"
STAGE_PROFILE = "③ 档案落库"
STAGE_JOBS = "④ 派生抓取"

# stage_funnel 的闸名 → 中文一句(门面中立措辞)。
_GATE_LABELS: dict[str, str] = {
    "row_missing": "库里查不到这个账号",
    "low_reach": "粉丝量低于门槛",
    "unknown_reach": "粉丝量未知",
    "hard_filter": "平台/国家等硬条件不符",
    "excluded_region": "按地区规避排除",
    "no_match_evidence": "没有能证明相关的作品",
}


@dataclass
class StageRow:
    """一节链路的产出与原因。``produced`` 是这一节交给下一节的东西的条数。"""

    stage: str
    produced: int
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryReport:
    query_id: str
    query_text: str
    session_id: int | None
    session_status: str
    mode: str
    stages: list[StageRow] = field(default_factory=list)
    reasons: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _loads(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = _text(raw)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _rows(conn: Any, sql: str, params: tuple[Any, ...]) -> list[Any]:
    """只读投影;失败记一笔并回滚,绝不让一条查询把整轮验收带崩。"""
    try:
        return list(conn.execute(sql, params).fetchall() or [])
    except Exception as exc:  # noqa: BLE001 - 验收脚本要把失败当数据报出来,不是崩掉
        out(f"  ! 查询失败 {type(exc).__name__}: {str(exc)[:160]}")
        rollback = getattr(conn, "rollback", None)
        if callable(rollback):
            try:
                rollback()
            except Exception as rb_exc:  # noqa: BLE001 - 回滚失败只记录
                out(f"  ! 回滚失败 {type(rb_exc).__name__}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# 查询清单
# ─────────────────────────────────────────────────────────────────────────────
def load_recent_queries(conn: Any, limit: int) -> tuple[list[dict[str, Any]], str]:
    """``--queries recent``:拿库里最近 N 条会话的原查询当清单。

    金查询清单在本地往往一条会话都没有(四节全是「无从谈起」),体检不出东西;
    用真发生过的查询才能把四节全部走通。**只读**,不新建任何东西。
    """
    rows = _rows(
        conn,
        "SELECT id, query_text FROM vkpi_kol_search_sessions "
        "WHERE archived_at IS NULL AND query_text <> '' ORDER BY id DESC LIMIT ?",
        (int(limit),),
    )
    seen: list[dict[str, Any]] = []
    for row in rows:
        text = _text(row["query_text"])
        if any(item["query"] == text for item in seen):
            continue
        seen.append({"id": f"session_{int(row['id'])}", "query": text})
    if not seen:
        return seen, "库里没有可复查的历史会话。"
    if len(seen) < len(rows):
        return seen, f"最近 {len(rows)} 条会话里有重复查询,去重后剩 {len(seen)} 条。"
    return seen, ""


def load_queries(path: str, limit: int) -> tuple[list[dict[str, Any]], str]:
    """读金查询清单。文件名写着 60,实际只有 5 条 —— 要的比有的多时如实报,不静默少给。"""
    full = path if os.path.isabs(path) else os.path.join(_REPO_ROOT, path)
    with open(full, encoding="utf-8") as handle:
        data = json.load(handle)
    items = data.get("queries") if isinstance(data, dict) else data
    queries = [item for item in (items or []) if isinstance(item, dict) and _text(item.get("query"))]
    note = ""
    if limit > len(queries):
        note = f"清单里只有 {len(queries)} 条查询,少于请求的 {limit} 条,按实有条数跑。"
    return queries[:limit], note


# ─────────────────────────────────────────────────────────────────────────────
# 四节链路(全部只读)
# ─────────────────────────────────────────────────────────────────────────────
def _latest_session(conn: Any, query_text: str) -> dict[str, Any] | None:
    rows = _rows(
        conn,
        "SELECT id, status, query_text, created_by, result_summary_json, created_at "
        "FROM vkpi_kol_search_sessions WHERE query_text=? AND archived_at IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (query_text,),
    )
    return dict(rows[0]) if rows else None


def _session_by_id(conn: Any, session_id: int) -> dict[str, Any] | None:
    rows = _rows(
        conn,
        "SELECT id, status, query_text, created_by, result_summary_json, created_at "
        "FROM vkpi_kol_search_sessions WHERE id=?",
        (int(session_id),),
    )
    return dict(rows[0]) if rows else None


def stage_session(session: dict[str, Any] | None) -> StageRow:
    if not session:
        return StageRow(STAGE_SESSION, 0, "本地没有这条查询的历史记录(--apply 才会新建)")
    owner = session.get("created_by")
    reason = f"会话 {session['id']} 状态 {_text(session.get('status')) or '未知'}"
    if not owner:
        reason += ";无发起人 —— 派生的付费任务会因缺授权被拦"
    return StageRow(STAGE_SESSION, 1, reason, {"session_id": int(session["id"]), "created_by": owner})


def stage_recall(session: dict[str, Any] | None) -> StageRow:
    """从会话账本读逐闸进入/杀掉数;账本缺席就如实说缺席,不猜。"""
    if not session:
        return StageRow(STAGE_RECALL, 0, "没有会话,这一节无从谈起")
    summary = _loads(session.get("result_summary_json"))
    diagnostics = _loads(summary.get("diagnostics"))
    funnel = _loads(diagnostics.get("stage_funnel"))
    returned = int(diagnostics.get("returned_count") or 0)
    if not funnel:
        empty_reason = _text(diagnostics.get("empty_reason"))
        reason = f"会话没有留下逐闸账本{('(' + empty_reason + ')') if empty_reason else ''}"
        return StageRow(STAGE_RECALL, returned, reason, {"returned_count": returned})
    dropped = {key: int(value or 0) for key, value in _loads(funnel.get("dropped_by_gate")).items()}
    killed = sorted(dropped.items(), key=lambda kv: -kv[1])
    entered = int(funnel.get("entered_reach_gate") or 0)
    survivors = int(funnel.get("survivors") or 0)
    if killed and killed[0][1] > 0:
        gate, count = killed[0]
        reason = f"进入 {entered} 人,最大一刀「{_GATE_LABELS.get(gate, gate)}」杀掉 {count} 人,剩 {survivors}"
    else:
        reason = f"进入 {entered} 人,没有闸大量杀人,剩 {survivors}"
    detail = {"entered": entered, "survivors": survivors, "returned_count": returned, "dropped_by_gate": dropped}
    return StageRow(STAGE_RECALL, returned or survivors, reason, detail)


def stage_profile(conn: Any, session: dict[str, Any] | None) -> StageRow:
    if not session:
        return StageRow(STAGE_PROFILE, 0, "没有会话,这一节无从谈起")
    rows = _rows(
        conn,
        "SELECT status, COUNT(*) AS n, SUM(CASE WHEN kol_pool_id IS NULL THEN 0 ELSE 1 END) AS landed "
        "FROM vkpi_kol_search_session_items WHERE session_id=? GROUP BY 1 ORDER BY 2 DESC",
        (int(session["id"]),),
    )
    if not rows:
        return StageRow(STAGE_PROFILE, 0, "会话下没有候选条目(召回这一节就没交出人)")
    by_status = {_text(row["status"]): int(row["n"] or 0) for row in rows}
    landed = sum(int(row["landed"] or 0) for row in rows)
    reason = f"候选 {sum(by_status.values())} 条,其中 {landed} 条已落到创作者库"
    return StageRow(STAGE_PROFILE, landed, reason, {"by_status": by_status})


def _session_jobs(conn: Any, session_id: int) -> list[Any]:
    return _rows(
        conn,
        "SELECT COALESCE(job_type,'') AS job_type, status, "
        "COALESCE(last_error_category,'') AS last_error_category, "
        "SUBSTR(COALESCE(last_error,''), 1, 600) AS last_error "
        "FROM apify_jobs WHERE payload->>'search_session_id' = ? ORDER BY id DESC LIMIT 2000",
        (str(int(session_id)),),
    )


def stage_jobs(conn: Any, session: dict[str, Any] | None) -> tuple[StageRow, list[dict[str, Any]]]:
    """派生抓取任务:按状态计数 + 原因表(与只读端点同一套码表)。"""
    from app.api.routers.vkpi_crawl_health import crawl_reason, job_label

    if not session:
        return StageRow(STAGE_JOBS, 0, "没有会话,这一节无从谈起"), []
    rows = _session_jobs(conn, int(session["id"]))
    if not rows:
        return (
            StageRow(STAGE_JOBS, 0, "会话没有挂上任何抓取任务(链路在这一节之前就断了)"),
            [],
        )
    by_status: dict[str, int] = {}
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        status = _text(row["status"])
        by_status[status] = by_status.get(status, 0) + 1
        if status not in TERMINAL_STATES or status == "done":
            continue
        reason = crawl_reason(
            last_error_category=row["last_error_category"], last_error=row["last_error"]
        )
        human = str(reason["reason_human"])
        bucket = buckets.setdefault(
            human,
            {"reason_human": human, "category": reason["category"], "count": 0, "job_labels": []},
        )
        bucket["count"] = int(bucket["count"]) + 1
        label = job_label(row["job_type"])
        if label not in bucket["job_labels"]:
            bucket["job_labels"].append(label)
    reasons = sorted(buckets.values(), key=lambda item: -int(item["count"]))
    done = int(by_status.get("done", 0))
    if reasons:
        top = reasons[0]
        reason_text = f"{sum(by_status.values())} 个任务,{done} 个成功;最大失因「{top['reason_human']}」{top['count']} 个"
    else:
        reason_text = f"{sum(by_status.values())} 个任务,{done} 个成功,没有失败"
    return StageRow(STAGE_JOBS, done, reason_text, {"by_status": by_status}), reasons


def inspect_session(conn: Any, item: dict[str, Any], session: dict[str, Any] | None, mode: str) -> QueryReport:
    """一条查询的四节只读体检。session 由调用方给(dry-run 查历史,apply 给新建的那条)。"""
    jobs_row, reasons = stage_jobs(conn, session)
    return QueryReport(
        query_id=_text(item.get("id")) or "(无编号)",
        query_text=_text(item.get("query")),
        session_id=int(session["id"]) if session else None,
        session_status=_text(session.get("status")) if session else "",
        mode=mode,
        stages=[stage_session(session), stage_recall(session), stage_profile(conn, session), jobs_row],
        reasons=reasons,
    )


# ─────────────────────────────────────────────────────────────────────────────
# --apply:真身份 + 真发起 + 轮询到终态
# ─────────────────────────────────────────────────────────────────────────────
def load_staff(conn: Any, staff_id: int) -> dict[str, Any]:
    """真 staff 行;取不到就抛,绝不用 None 身份静默降级(那会让派生任务全被拦)。"""
    from app.core.coerce import _truthy

    rows = _rows(conn, "SELECT * FROM staff WHERE id=?", (int(staff_id),))
    if not rows:
        raise SystemExit(f"[验收] staff_id={staff_id} 不存在;--apply 必须带一个真实在职员工。")
    staff = dict(rows[0])
    if not _truthy(staff.get("active")):
        raise SystemExit(f"[验收] staff_id={staff_id} 已停用;--apply 必须带一个真实在职员工。")
    return staff


def launch(item: dict[str, Any], staff: dict[str, Any]) -> int:
    """走既有写路径发起一条查询,返回新会话 id。脚本不新开任何写口。"""
    from app.domains.kol.profile_discovery import enqueue_smart_search_profile_advance

    body: dict[str, Any] = {"input": _text(item.get("query"))}
    if item.get("market"):
        body["market"] = item["market"]
    if item.get("platforms"):
        body["platforms"] = item["platforms"]
    queued = enqueue_smart_search_profile_advance(
        query_text=_text(item.get("query")), body=body, staff=staff
    )
    session = queued.get("search_session") or {}
    session_id = int(session.get("id") or 0)
    if session_id <= 0:
        raise SystemExit(f"[验收] 发起未返回会话号:{str(queued)[:200]}")
    return session_id


def wait_for_terminal(conn: Any, session_id: int, timeout_sec: int) -> str:
    """轮询到该会话的任务全部终态或超时;返回收尾口径(诚实标注是否超时)。"""
    deadline = time.time() + max(0, timeout_sec)
    pending = -1
    while True:
        rows = _session_jobs(conn, session_id)
        pending = sum(1 for row in rows if _text(row["status"]) not in TERMINAL_STATES)
        if rows and pending == 0:
            return "全部终态"
        if time.time() >= deadline:
            return f"等待超时,仍有 {pending if pending >= 0 else 0} 个未完"
        commit = getattr(conn, "commit", None)
        if callable(commit):
            commit()  # 结束只读事务快照,否则轮询永远看见同一批旧行
        time.sleep(POLL_INTERVAL_SEC)


# ─────────────────────────────────────────────────────────────────────────────
# 渲染
# ─────────────────────────────────────────────────────────────────────────────
def _render_text(reports: list[QueryReport], header: dict[str, Any]) -> None:
    out(f"[{header['mode']}] 查询 {header['query_count']} 条 · 生成于 {header['generated_at']}")
    if header.get("note"):
        out(f"  注:{header['note']}")
    for report in reports:
        out("")
        out(f"── {report.query_id} · {report.query_text[:60]}")
        session_label = report.session_id if report.session_id else "无"
        out(f"   会话={session_label} 状态={report.session_status or '—'}")
        for row in report.stages:
            out(f"   {row.stage:<10} 产出 {row.produced:>4}   {row.reason}")
        for reason in report.reasons[:5]:
            labels = "/".join(reason["job_labels"])
            out(f"      · {reason['count']:>3} {reason['reason_human']}  [{labels}]")
    out("")
    out(f"[{header['mode']}] {header['summary']}")
    if header.get("next_step"):
        out(f"[{header['mode']}] {header['next_step']}")


def build_header(reports: list[QueryReport], *, apply_mode: bool, note: str) -> dict[str, Any]:
    linked = sum(1 for report in reports if report.session_id)
    produced = sum(report.stages[-1].produced for report in reports if report.stages)
    broken = [report.query_id for report in reports if report.stages and report.stages[-1].produced == 0]
    return {
        "mode": "APPLY" if apply_mode else "DRY-RUN",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "query_count": len(reports),
        "sessions_found": linked,
        "jobs_done": produced,
        "queries_without_output": broken,
        "note": note,
        "summary": f"有会话 {linked}/{len(reports)} 条;末节成功产出合计 {produced};零产出查询 {len(broken)} 条",
        "next_step": (
            ""
            if apply_mode
            else "未写库、未取数、未花钱。要真跑请加 --apply --staff-id N(一次最多 "
            f"{MAX_BATCH} 条)。"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取链端到端验收(默认 dry-run,不花钱)")
    parser.add_argument("--queries", default=DEFAULT_QUERY_FILE, help=f"查询清单 JSON(默认 {DEFAULT_QUERY_FILE});写 recent = 复查库里最近的会话")
    parser.add_argument("-k", "--limit", type=int, default=DEFAULT_BATCH, help=f"跑几条(默认 {DEFAULT_BATCH},上限 {MAX_BATCH})")
    parser.add_argument("--apply", action="store_true", help="真发起(会花钱);必须同时给 --staff-id")
    parser.add_argument("--staff-id", type=int, default=None, help="发起人员工号(--apply 必填,缺了派生任务会全被拦)")
    parser.add_argument("--wait-seconds", type=int, default=600, help="--apply 后等待任务收敛的上限秒数(默认 600)")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    return parser.parse_args(argv)


def _resolve_batch(args: argparse.Namespace) -> int:
    requested = max(1, int(args.limit))
    return min(requested, MAX_BATCH)


def _run_dry(conn: Any, queries: list[dict[str, Any]]) -> list[QueryReport]:
    reports: list[QueryReport] = []
    for item in queries:
        session = _latest_session(conn, _text(item.get("query")))
        reports.append(inspect_session(conn, item, session, "dry_run"))
    return reports


def _run_apply(conn: Any, queries: list[dict[str, Any]], args: argparse.Namespace) -> list[QueryReport]:
    staff = load_staff(conn, int(args.staff_id))
    reports: list[QueryReport] = []
    for item in queries:
        session_id = launch(item, staff)
        commit = getattr(conn, "commit", None)
        if callable(commit):
            commit()
        waited = wait_for_terminal(conn, session_id, int(args.wait_seconds))
        report = inspect_session(conn, item, _session_by_id(conn, session_id), "apply")
        report.note = waited
        reports.append(report)
    return reports


def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.apply and not args.staff_id:
        out("[验收] --apply 必须带 --staff-id N:没有真身份,派生的付费任务在 worker 侧会被全部拦下。")
        return 2
    batch = _resolve_batch(args)
    note = ""
    if int(args.limit) > MAX_BATCH:
        note = f"请求 {args.limit} 条超过批次上限,已收到 {MAX_BATCH} 条。"
    recent_mode = _text(args.queries).lower() == "recent"
    if recent_mode and args.apply:
        out("[验收] --queries recent 是复查历史会话的只读口径,不能和 --apply 一起用。")
        return 2
    if not recent_mode:
        queries, file_note = load_queries(args.queries, batch)
        note = (note + " " if note else "") + file_note

    from app.db.connection import db_connection_sync_scope, get_conn

    with db_connection_sync_scope():
        conn = get_conn()
        if recent_mode:
            queries, recent_note = load_recent_queries(conn, batch)
            note = (note + " " if note else "") + recent_note
        if not queries:
            out("[验收] 查询清单为空,无事可做。")
            return 1
        reports = _run_apply(conn, queries, args) if args.apply else _run_dry(conn, queries)
        if not args.apply:
            rollback = getattr(conn, "rollback", None)
            if callable(rollback):
                rollback()  # dry-run 只开过只读事务;显式回滚坐实「零写库」
    header = build_header(reports, apply_mode=bool(args.apply), note=note)
    if args.json:
        out_json({"header": header, "reports": [asdict(report) for report in reports]}, ensure_ascii=False)
    else:
        _render_text(reports, header)
    return 0


if __name__ == "__main__":
    os.chdir(_REPO_ROOT)  # app.core.config 按 cwd 读根 .env
    sys.exit(run(sys.argv[1:]))
