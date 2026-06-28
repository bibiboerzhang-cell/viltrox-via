"""N3 新品 Launch Project(加性).

定义 launch project 的字段 schema(sku / 价格带 / 目标国家 / 核心卖点 /
竞品 / 目标人群 / 验证假设),并提供:

- ``LAUNCH_PROJECT_FIELDS`` / ``launch_project_schema()`` —— 纯描述,不触 DB。
- ``normalize_launch_metadata(...)`` —— 把用户输入清洗成 launch 元数据 dict。
- ``create_launch_project(...)`` —— 复用现有 ``workflow_projects.create_project``,
  打 ``source_type='launch'`` 标 + 把 launch 元数据塞进 ``metadata_json.launch``。
  默认 ``dry_run=True`` 不写库;``dry_run=False`` 才真正落 project。
- ``generate_launch_plan(project_id, ...)`` —— 复用现有
  ``recommendations.new_launch_match`` 出 KOL 候选,并生成「内容验证任务 +
  观察窗口」占位(纯 dict,不写库)。

红线:零触 viltrox_fit_score(只读都不需要),不新增列(全部落 metadata_json),
不碰 main.py / executors / skill_registry / 任何 frontend / 共享 barrel。

本模块为加性新文件:不改任何既有文件,既有 pytest 行为不变。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.projects.workflow_common import utcnow
from app.domains.projects.workflow_projects import create_project

logger = get_logger(__name__)

# launch project 来源标记:写进 vkpi_projects.source_type(既有列,加性复用)。
LAUNCH_SOURCE_TYPE = "launch"
# launch 元数据在 metadata_json 下的命名空间键。
LAUNCH_METADATA_KEY = "launch"

# ---------------------------------------------------------------------------
# Field schema —— 纯描述,前端/校验/文档共用。绝不触 DB。
# ---------------------------------------------------------------------------
# 每个字段:key / label / type / required / repeated(列表) / help。
LAUNCH_PROJECT_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "key": "sku",
        "label": "SKU",
        "type": "string",
        "required": True,
        "repeated": False,
        "help": "新品的产品 SKU(用于复用产品族匹配)。",
    },
    {
        "key": "price_band",
        "label": "价格带",
        "type": "string",
        "required": False,
        "repeated": False,
        "help": "目标零售价格区间,例如 '99-149 USD'。",
    },
    {
        "key": "target_countries",
        "label": "目标国家",
        "type": "string",
        "required": False,
        "repeated": True,
        "help": "主投放国家/地区列表(ISO 国家名或代码)。",
    },
    {
        "key": "selling_points",
        "label": "核心卖点",
        "type": "string",
        "required": False,
        "repeated": True,
        "help": "新品的核心差异化卖点。",
    },
    {
        "key": "competitors",
        "label": "竞品",
        "type": "string",
        "required": False,
        "repeated": True,
        "help": "对标竞品名称/型号。",
    },
    {
        "key": "target_audience",
        "label": "目标人群",
        "type": "string",
        "required": False,
        "repeated": False,
        "help": "目标受众画像描述。",
    },
    {
        "key": "validation_hypotheses",
        "label": "验证假设",
        "type": "string",
        "required": False,
        "repeated": True,
        "help": "本次 launch 想验证的市场/内容假设,驱动内容验证任务。",
    },
)

_REQUIRED_FIELDS: tuple[str, ...] = tuple(f["key"] for f in LAUNCH_PROJECT_FIELDS if f.get("required"))
_REPEATED_FIELDS: frozenset[str] = frozenset(f["key"] for f in LAUNCH_PROJECT_FIELDS if f.get("repeated"))
_SCALAR_FIELDS: frozenset[str] = frozenset(
    f["key"] for f in LAUNCH_PROJECT_FIELDS if not f.get("repeated")
)


def launch_project_schema() -> dict[str, Any]:
    """返回 launch project 字段 schema(纯描述,可直接序列化给前端)。"""
    return {
        "project_type": LAUNCH_SOURCE_TYPE,
        "source_type": LAUNCH_SOURCE_TYPE,
        "metadata_key": LAUNCH_METADATA_KEY,
        "required_fields": list(_REQUIRED_FIELDS),
        "fields": [dict(field) for field in LAUNCH_PROJECT_FIELDS],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        logger.debug("suppressed exception (hardening): best-effort", exc_info=True)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_list(value: Any) -> list[str]:
    """把标量/逗号串/列表统一清洗成去重保序的非空字符串列表。"""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = str(value).split(",")
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _text(item)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def normalize_launch_metadata(body: dict[str, Any]) -> dict[str, Any]:
    """把任意输入清洗成 launch 元数据 dict(只含 schema 定义的字段)。

    支持两种输入形态:扁平(``body['sku']``)或嵌套(``body['launch']['sku']``)。
    缺失 required 字段抛 ``ValueError``。
    """
    nested = body.get(LAUNCH_METADATA_KEY)
    source: dict[str, Any] = nested if isinstance(nested, dict) else dict(body)
    meta: dict[str, Any] = {}
    for field in LAUNCH_PROJECT_FIELDS:
        key = field["key"]
        if key not in source and nested is None and key not in body:
            # 字段未提供:repeated 默认空列表,scalar 默认空串。
            meta[key] = [] if key in _REPEATED_FIELDS else ""
            continue
        value = source.get(key)
        if key in _REPEATED_FIELDS:
            meta[key] = _as_list(value)
        else:
            meta[key] = _text(value)
    missing = [key for key in _REQUIRED_FIELDS if not meta.get(key)]
    if missing:
        raise ValueError(f"launch metadata missing required field(s): {', '.join(missing)}")
    return meta


# ---------------------------------------------------------------------------
# create_launch_project
# ---------------------------------------------------------------------------
def create_launch_project(
    body: dict[str, Any],
    *,
    staff: dict[str, Any] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """创建 launch project。

    - 清洗 launch 元数据 + 校验 required。
    - ``dry_run=True``(默认):不写库,返回将要写入的 payload 预览。
    - ``dry_run=False``:复用 ``workflow_projects.create_project`` 真正落 project,
      ``source_type='launch'``,launch 元数据落 ``metadata_json.launch``。

    红线:零触 viltrox_fit_score;不新增列。
    """
    launch_meta = normalize_launch_metadata(body)
    project_name = _text(body.get("project_name") or body.get("name"))
    if not project_name:
        sku = launch_meta.get("sku") or ""
        project_name = f"Launch {sku}".strip() or "Launch Project"

    existing_meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    metadata = {**existing_meta, LAUNCH_METADATA_KEY: launch_meta, "project_type": LAUNCH_SOURCE_TYPE}

    create_body: dict[str, Any] = {
        "project_name": project_name,
        "product_sku": launch_meta.get("sku") or _text(body.get("product_sku")),
        "product_name": _text(body.get("product_name")),
        "platform": _text(body.get("platform")),
        "marketplace": _text(body.get("marketplace")),
        "stage": _text(body.get("stage")) or "discovery",
        "priority": _text(body.get("priority")) or "normal",
        "source_type": LAUNCH_SOURCE_TYPE,
        "kol_id": body.get("kol_id"),
        "assigned_staff_id": body.get("assigned_staff_id"),
        "note": _text(body.get("note")),
        "metadata": metadata,
    }
    if body.get("project_uid"):
        create_body["project_uid"] = _text(body.get("project_uid"))

    preview = {
        "dry_run": True,
        "would_create": True,
        "project_type": LAUNCH_SOURCE_TYPE,
        "source_type": LAUNCH_SOURCE_TYPE,
        "project_name": project_name,
        "launch": launch_meta,
        "metadata_json_preview": metadata,
    }
    if dry_run:
        return preview

    created = create_project(create_body, staff=staff)
    return {
        "dry_run": False,
        "project_type": LAUNCH_SOURCE_TYPE,
        "source_type": LAUNCH_SOURCE_TYPE,
        "launch": launch_meta,
        **created,
    }


# ---------------------------------------------------------------------------
# generate_launch_plan
# ---------------------------------------------------------------------------
def _load_launch_project(project_id: int) -> dict[str, Any]:
    row = get_conn().execute(
        "SELECT id, project_name, product_sku, source_type, metadata_json FROM vkpi_projects WHERE id=?",
        (int(project_id),),
    ).fetchone()
    if not row:
        raise ValueError(f"launch project not found: {project_id}")
    record = dict(row)
    record["metadata"] = _loads(record.get("metadata_json"))
    return record


def _candidate_preview(
    *,
    product_query: str,
    primary_markets: str,
    candidate_limit: int,
) -> dict[str, Any]:
    """复用现有 new_launch_match 出 KOL 候选(只读 dry-run,不写库)。

    任何前置(memory readiness / budget guard)未就绪时优雅降级:返回空候选 + 原因,
    绝不抛断本函数 —— 内容验证任务/观察窗口占位依然可生成。
    """
    try:
        from app.domains.recommendations.new_launch_match import build_new_launch_match_preview

        preview = build_new_launch_match_preview(
            product_query=product_query,
            limit=int(candidate_limit),
            primary_markets=primary_markets,
            with_llm_reasons=False,
            persist_run=False,
        )
    except Exception as exc:  # readiness/budget/无数据等 —— 降级不阻断
        logger.info("launch plan candidate preview unavailable: %s", exc)
        return {
            "available": False,
            "reason": str(exc),
            "items": [],
            "summary": {},
        }
    items = preview.get("items") or []
    return {
        "available": True,
        "reason": "",
        "items": [
            {
                "rank": item.get("rank"),
                "kol_entity_uid": item.get("kol_entity_uid"),
                "kol_pool_id": item.get("kol_pool_id"),
                "platform": item.get("platform"),
                "handle": item.get("handle"),
                "display_name": item.get("display_name"),
                "country": item.get("country"),
                "score": item.get("score"),
                "review_required": item.get("review_required"),
            }
            for item in items
        ],
        "summary": preview.get("summary") or {},
        "target_family_name": preview.get("target_family_name") or "",
    }


def _content_validation_tasks(launch_meta: dict[str, Any]) -> list[dict[str, Any]]:
    """按验证假设生成内容验证任务占位(纯 dict,不写库)。"""
    hypotheses = launch_meta.get("validation_hypotheses") or []
    if not hypotheses:
        # 无显式假设:用核心卖点兜底成单条通用验证任务。
        selling = launch_meta.get("selling_points") or []
        fallback = selling[0] if selling else (launch_meta.get("sku") or "new product")
        hypotheses = [f"Validate market reception for {fallback}"]
    tasks: list[dict[str, Any]] = []
    for idx, hypothesis in enumerate(hypotheses, start=1):
        tasks.append(
            {
                "task_type": "content_validation",
                "sequence": idx,
                "status": "placeholder",
                "hypothesis": _text(hypothesis),
                "target_audience": launch_meta.get("target_audience") or "",
                "competitors": launch_meta.get("competitors") or [],
            }
        )
    return tasks


def _observation_window_placeholders(launch_meta: dict[str, Any]) -> list[dict[str, Any]]:
    """生成观察窗口占位(纯 dict,不写库)。

    目标国家有几个就生成几个观察窗口占位;无则单条全局占位。
    """
    countries = launch_meta.get("target_countries") or []
    targets = countries or ["global"]
    return [
        {
            "window_type": "launch_observation",
            "status": "placeholder",
            "market": _text(market),
            "duration_days": 30,
        }
        for market in targets
    ]


def generate_launch_plan(
    project_id: int,
    *,
    candidate_limit: int = 20,
) -> dict[str, Any]:
    """为 launch project 生成计划:KOL 候选(复用 new_launch_match)+ 内容验证任务
    占位 + 观察窗口占位。

    纯只读 / 占位生成,不写库,零触 viltrox_fit_score。
    """
    record = _load_launch_project(project_id)
    launch_meta = record.get("metadata", {}).get(LAUNCH_METADATA_KEY)
    if not isinstance(launch_meta, dict) or not launch_meta:
        # 非 launch project 或缺 launch 元数据 —— 用 project 自身字段兜底。
        launch_meta = normalize_launch_metadata(
            {"sku": record.get("product_sku") or record.get("project_name") or f"project-{project_id}"}
        )

    product_query = _text(launch_meta.get("sku")) or _text(record.get("product_sku")) or _text(
        record.get("project_name")
    )
    primary_markets = ",".join(launch_meta.get("target_countries") or [])

    candidates = _candidate_preview(
        product_query=product_query,
        primary_markets=primary_markets,
        candidate_limit=candidate_limit,
    )
    tasks = _content_validation_tasks(launch_meta)
    windows = _observation_window_placeholders(launch_meta)

    return {
        "generated_at": utcnow(),
        "project_id": int(project_id),
        "project_type": LAUNCH_SOURCE_TYPE,
        "product_query": product_query,
        "launch": launch_meta,
        "kol_candidates": candidates,
        "content_validation_tasks": tasks,
        "observation_windows": windows,
        "summary": {
            "candidate_count": len(candidates.get("items") or []),
            "candidates_available": bool(candidates.get("available")),
            "content_task_count": len(tasks),
            "observation_window_count": len(windows),
        },
    }
