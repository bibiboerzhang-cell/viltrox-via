"""路线1 · Agent Tool Registry —— Agent 只能调白名单工具,绝不乱跑。

每个工具声明元数据:写库?烧 LLM?成本档?需审批?输入字段?对应既有 domain 端点。
注册表本身**只声明不执行**;真正执行仍由既有 domain 函数 / Action Inbox executor 完成。
红线:写库或烧 LLM 的工具一律 requires_approval=True(走 Action Inbox 人审);零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

# 第一批工具:全部映射到已存在的 domain 能力(端点仅作人话标注,编排器不在此调用)。
TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "search_kol": {
        "name": "搜索 KOL", "writes_db": False, "uses_llm": True, "cost_tier": "low",
        "requires_approval": False, "inputs": ["query"],
        "endpoint": "POST /api/admin/vkpi/kol-smart-search",
    },
    "scan_profile": {
        "name": "抓取 KOL 主页(补全资料)", "writes_db": True, "uses_llm": False, "cost_tier": "medium",
        "requires_approval": True, "inputs": ["kol_pool_id"],
        "endpoint": "action: kol_profile / deep_missing executor",
    },
    "enqueue_video_analysis": {
        "name": "排队视频深析", "writes_db": True, "uses_llm": True, "cost_tier": "high",
        "requires_approval": True, "inputs": ["kol_pool_id"],
        "endpoint": "kol.video_analysis_enqueue",
    },
    "generate_outreach": {
        "name": "生成合作话术 + SOW 草案", "writes_db": False, "uses_llm": True, "cost_tier": "medium",
        "requires_approval": True, "inputs": ["session_id"],
        "endpoint": "POST /api/admin/vkpi/kol-search-sessions/{id}/generate-outreach",
    },
    "create_project_draft": {
        "name": "建项目草案(挂选中 KOL)", "writes_db": True, "uses_llm": False, "cost_tier": "low",
        "requires_approval": True, "inputs": ["session_id"],
        "endpoint": "POST /api/admin/vkpi/kol-search-sessions/{id}/create-project-draft",
    },
    "check_project_observation": {
        "name": "扫已签收开观察窗", "writes_db": True, "uses_llm": False, "cost_tier": "low",
        "requires_approval": True, "inputs": ["project_id"],
        "endpoint": "POST /api/admin/vkpi/projects/observation-windows/scan-delivered",
    },
    "retry_failed_analysis": {
        "name": "重试失败/受阻任务", "writes_db": True, "uses_llm": False, "cost_tier": "low",
        "requires_approval": True, "inputs": ["job_id"],
        "endpoint": "action: failed_retry executor",
    },
    "sync_company_account": {
        "name": "同步公司官方账号", "writes_db": True, "uses_llm": False, "cost_tier": "low",
        "requires_approval": True, "inputs": [],
        "endpoint": "domains.sync.cron channels_sync",
    },
}


def list_tools() -> list[dict[str, Any]]:
    """工具清单(给前端/编排器看的 manifest)。"""
    return [{"tool_id": tid, **meta} for tid, meta in TOOL_REGISTRY.items()]


def get_tool(tool_id: str) -> dict[str, Any] | None:
    return TOOL_REGISTRY.get(str(tool_id or ""))


def validate_inputs(tool_id: str, inputs: dict[str, Any] | None) -> dict[str, Any]:
    """校验输入是否齐(缺字段不抛异常,返回 ok=False + missing)。"""
    tool = get_tool(tool_id)
    if not tool:
        return {"ok": False, "reason": "unknown_tool"}
    given = inputs or {}
    missing = [k for k in tool.get("inputs", []) if k not in given]
    return {"ok": not missing, "missing": missing}
