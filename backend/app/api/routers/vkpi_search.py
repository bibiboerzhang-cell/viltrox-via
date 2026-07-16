"""V-KPI deterministic natural search routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.domains.search import natural_search


router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-search"])

logger = get_logger(__name__)

GLOBAL_SEARCH_QUERY_MAX_LENGTH = 80
GLOBAL_SEARCH_LIMIT_DEFAULT = 5
GLOBAL_SEARCH_LIMIT_MAX = 20
_EVENT_SCOPE_FETCH_MULTIPLIER = 5
_PG_TRGM_CAPABILITY: bool | None = None


def _escape_like(value: str) -> str:
    """Treat user-entered LIKE metacharacters as text, not scan wildcards."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _postgres_ranked_search_sql(
    *,
    table: str,
    select_columns: str,
    text_expressions: tuple[str, ...],
    document_expression: str,
    stable_order: str,
    scope_predicate: str = "",
    hidden_columns: str = "",
    use_trigram: bool,
) -> str:
    """Build the fixed Postgres exact/prefix/FTS/trigram retrieval contract."""
    exact_predicate = " OR ".join(
        f"{expression} = input.exact_q" for expression in text_expressions
    )
    prefix_predicate = " OR ".join(
        f"{expression} LIKE input.prefix_q ESCAPE '\\'"
        for expression in text_expressions
    )
    fts_expression = f"TO_TSVECTOR('simple'::regconfig, {document_expression})"
    tolerant_predicate = ""
    if use_trigram:
        tolerant_predicate = (
            f" OR {document_expression} LIKE input.like_q ESCAPE '\\'"
            f" OR {document_expression} %% input.exact_q"
        )
    trigram_rank = (
        f"SIMILARITY({document_expression}, input.exact_q)" if use_trigram else "0.0"
    )
    scope_clause = f" AND ({scope_predicate})" if scope_predicate else ""
    ranked_hidden_columns = f", {hidden_columns}" if hidden_columns else ""
    return f"""
        WITH search_input AS (
            SELECT
                ?::text AS exact_q,
                ?::text AS prefix_q,
                ?::text AS like_q,
                WEBSEARCH_TO_TSQUERY('simple'::regconfig, ?) AS tsq
        ),
        ranked AS (
            SELECT
                {select_columns}{ranked_hidden_columns},
                CASE
                    WHEN ({exact_predicate}) THEN 0
                    WHEN ({prefix_predicate}) THEN 1
                    WHEN {fts_expression} @@ input.tsq THEN 2
                    ELSE 3
                END AS _match_tier,
                TS_RANK_CD({fts_expression}, input.tsq) AS _fts_rank,
                {trigram_rank} AS _trigram_rank
            FROM {table}
            CROSS JOIN search_input AS input
            WHERE (
                {exact_predicate}
                OR {prefix_predicate}
                OR {fts_expression} @@ input.tsq
                {tolerant_predicate}
            ){scope_clause}
        )
        SELECT {select_columns}
        FROM ranked
        ORDER BY
            _match_tier ASC,
            CASE WHEN _match_tier = 2 THEN _fts_rank ELSE 0 END DESC,
            CASE WHEN _match_tier = 3 THEN _trigram_rank ELSE 0 END DESC,
            {stable_order}
        LIMIT ?
    """


def _postgres_search_params(keyword: str, limit: int) -> tuple:
    escaped = _escape_like(keyword)
    return (keyword, f"{escaped}%", f"%{escaped}%", keyword, limit)


def _pg_trgm_available(conn) -> bool:
    global _PG_TRGM_CAPABILITY
    if _PG_TRGM_CAPABILITY is not None:
        return _PG_TRGM_CAPABILITY
    try:
        row = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') AS available"
        ).fetchone()
        _PG_TRGM_CAPABILITY = bool(row and row["available"])
    except Exception:
        logger.debug("global_search pg_trgm probe failed; using FTS/LIKE", exc_info=True)
        _PG_TRGM_CAPABILITY = False
    return _PG_TRGM_CAPABILITY


@router.get("/search")
def vkpi_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    del staff
    try:
        return natural_search.search(q, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/global-search")
def vkpi_global_search(
    q: str = Query(..., min_length=1, max_length=GLOBAL_SEARCH_QUERY_MAX_LENGTH),
    limit: int = Query(
        default=GLOBAL_SEARCH_LIMIT_DEFAULT,
        ge=1,
        le=GLOBAL_SEARCH_LIMIT_MAX,
    ),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """顶栏全局搜索(X3):一个关键词同时搜 KOL(名字/handle)/项目(名字)/活动(名字)。

    轻端点定位:三表各取 limit 条(默认 5,上限 20),只回名字/id 级轻字段,不回敏感数据
    (联系方式/预算/评分等一概不带)。可见范围口径:登录 + vkpi:read 即可搜
    ——管理层全量;非管理层套 C3 轻隔离口径(X4-MEDIUM 修复 2026-07-03):
    KOL 限本人可见集(收藏∪项目合作∪共享∪认领)、项目限本人参与集、活动限本人在队。
    """
    _role = str(staff.get("role") or "").strip().lower()
    _is_manager = int(staff.get("is_owner") or 0) == 1 or _role in {
        "admin", "manager", "lead", "marketing_lead", "marketing_manager", "marketing-manager"
    }
    _me = 0 if _is_manager else int(staff.get("staff_id") or 0)
    keyword = str(q or "").strip().lower()
    if not keyword:
        raise HTTPException(status_code=400, detail="q 不能为空")
    if len(keyword) > GLOBAL_SEARCH_QUERY_MAX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"q 最长 {GLOBAL_SEARCH_QUERY_MAX_LENGTH} 个字符",
        )
    try:
        result_limit = max(1, min(int(limit), GLOBAL_SEARCH_LIMIT_MAX))
    except (TypeError, ValueError):
        result_limit = GLOBAL_SEARCH_LIMIT_DEFAULT
    # 懒 import:线上旧布局下给已存在文件顶层新增 import 有风险,函数内引入最稳。
    from app.db.connection import get_conn, is_postgres_runtime

    conn = get_conn()
    escaped_keyword = _escape_like(keyword)
    like = f"%{escaped_keyword}%"
    postgres_runtime = is_postgres_runtime()
    use_trigram = postgres_runtime and _pg_trgm_available(conn)

    def _rows(
        sql: str,
        params: tuple,
        fallback: tuple[str, tuple] | None = None,
        fill_limit: int = 0,
    ) -> list[dict]:
        # 单表查询挂了(迁移未跑/列缺失)不拖垮整个搜索,失败该组回空。
        try:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        except Exception:
            if fallback is None:
                logger.warning("global_search 子查询失败", exc_info=True)
                return []
            logger.warning("global_search 优化查询失败,回退 LIKE", exc_info=True)
            fallback_sql, fallback_params = fallback
            try:
                return [
                    dict(r)
                    for r in conn.execute(fallback_sql, fallback_params).fetchall()
                ]
            except Exception:
                logger.warning("global_search LIKE 回退失败", exc_info=True)
                return []
        if fallback is None or fill_limit <= 0 or len(rows) >= fill_limit:
            return rows
        fallback_sql, fallback_params = fallback
        try:
            fallback_rows = [
                dict(r)
                for r in conn.execute(fallback_sql, fallback_params).fetchall()
            ]
        except Exception:
            logger.warning("global_search LIKE 补位失败", exc_info=True)
            return rows
        seen_ids = {row.get("id") for row in rows}
        for row in fallback_rows:
            if row.get("id") in seen_ids:
                continue
            rows.append(row)
            seen_ids.add(row.get("id"))
            if len(rows) >= fill_limit:
                break
        return rows

    # 非管理层的可见集 SQL(服务端从鉴权 staff 推导,绝不信客户端;识别不出身份=只回空,诚实)。
    _kol_scope_sql = ""
    _proj_scope_sql = ""
    if not _is_manager:
        if _me <= 0:
            return {"q": keyword, "kols": [], "projects": [], "events": []}
        try:
            from app.domains.dashboard.kol_distribution import _staff_visible_kols_sql
            from app.domains.dashboard.summary_scope import _actor_projects_sql

            _kol_scope_sql = f" AND id IN ({_staff_visible_kols_sql(_me)})"
            _proj_scope_sql = f" AND id IN ({_actor_projects_sql(_me)})"
        except Exception:
            # 旧布局缺可见集模块时宁可回空,不泄露全量(与 C3 隔离口径一致)。
            return {"q": keyword, "kols": [], "projects": [], "events": []}

    # KOL:名字/handle 命中,粉丝多的排前(COALESCE 兜 NULL,跨库可移植)。
    kol_fallback_sql = f"""
        SELECT id, platform, handle, display_name, avatar_url, followers
        FROM vkpi_kol_pool
        WHERE (LOWER(COALESCE(display_name, '')) LIKE ? ESCAPE '\\'
           OR LOWER(COALESCE(handle, '')) LIKE ? ESCAPE '\\'){_kol_scope_sql}
        ORDER BY COALESCE(followers, 0) DESC, id DESC
        LIMIT ?
    """
    kol_fallback = (kol_fallback_sql, (like, like, result_limit))
    if postgres_runtime:
        kol_sql = _postgres_ranked_search_sql(
            table="vkpi_kol_pool",
            select_columns="id, platform, handle, display_name, avatar_url, followers",
            text_expressions=(
                "LOWER(COALESCE(display_name, ''))",
                "LOWER(COALESCE(handle, ''))",
            ),
            document_expression=(
                "LOWER(COALESCE(display_name, '') || ' ' || COALESCE(handle, ''))"
            ),
            stable_order="COALESCE(followers, 0) DESC, id DESC",
            scope_predicate=_kol_scope_sql.removeprefix(" AND "),
            use_trigram=use_trigram,
        )
        kols = _rows(
            kol_sql,
            _postgres_search_params(keyword, result_limit),
            kol_fallback,
            fill_limit=0 if use_trigram else result_limit,
        )
    else:
        kols = _rows(*kol_fallback)

    # 项目:按名字命中,新项目排前。
    project_fallback_sql = f"""
        SELECT id, project_uid, project_name, stage, stage_status, platform
        FROM vkpi_projects
        WHERE LOWER(COALESCE(project_name, '')) LIKE ? ESCAPE '\\'{_proj_scope_sql}
        ORDER BY id DESC
        LIMIT ?
    """
    project_fallback = (project_fallback_sql, (like, result_limit))
    if postgres_runtime:
        project_sql = _postgres_ranked_search_sql(
            table="vkpi_projects",
            select_columns="id, project_uid, project_name, stage, stage_status, platform",
            text_expressions=("LOWER(COALESCE(project_name, ''))",),
            document_expression="LOWER(COALESCE(project_name, ''))",
            stable_order="id DESC",
            scope_predicate=_proj_scope_sql.removeprefix(" AND "),
            use_trigram=use_trigram,
        )
        projects = _rows(
            project_sql,
            _postgres_search_params(keyword, result_limit),
            project_fallback,
            fill_limit=0 if use_trigram else result_limit,
        )
    else:
        projects = _rows(*project_fallback)

    # 活动:按标题命中,近期活动排前;非管理层多取 5 倍后按 team_ids 在队成员过滤
    # (jsonb 语义在 compat 问号占位下不便直查,量小后过滤等价且稳)。
    event_query_limit = (
        min(result_limit * _EVENT_SCOPE_FETCH_MULTIPLIER, GLOBAL_SEARCH_LIMIT_MAX * 5)
        if not _is_manager
        else result_limit
    )
    event_fallback_sql = f"""
        SELECT id, title, status, start_date, end_date{", team_ids" if not _is_manager else ""}
        FROM vkpi_events
        WHERE LOWER(COALESCE(title, '')) LIKE ? ESCAPE '\\'
        ORDER BY start_date DESC, created_at DESC, id DESC
        LIMIT ?
    """
    event_fallback = (event_fallback_sql, (like, event_query_limit))
    if postgres_runtime:
        event_select = (
            "id, title, status, start_date, end_date, team_ids"
            if not _is_manager
            else "id, title, status, start_date, end_date"
        )
        event_sql = _postgres_ranked_search_sql(
            table="vkpi_events",
            select_columns=event_select,
            text_expressions=("LOWER(COALESCE(title, ''))",),
            document_expression="LOWER(COALESCE(title, ''))",
            stable_order="start_date DESC, _search_created_at DESC, id DESC",
            hidden_columns="created_at AS _search_created_at",
            use_trigram=use_trigram,
        )
        events = _rows(
            event_sql,
            _postgres_search_params(keyword, event_query_limit),
            event_fallback,
            fill_limit=0 if use_trigram else event_query_limit,
        )
    else:
        events = _rows(*event_fallback)
    if not _is_manager:
        import json as _json

        def _in_team(row: dict) -> bool:
            raw = row.pop("team_ids", None)
            try:
                ids = raw if isinstance(raw, list) else _json.loads(raw or "[]")
                return any(int(x) == _me for x in ids if str(x).strip().lstrip("-").isdigit())
            except Exception:
                return False

        events = [r for r in events if _in_team(r)][:result_limit]
    # DATE 列诚实转 ISO 串(不让序列化差异漏到前端)。
    for row in events:
        for key in ("start_date", "end_date"):
            if row.get(key) is not None:
                row[key] = str(row[key])
    return {"q": keyword, "kols": kols, "projects": projects, "events": events}
