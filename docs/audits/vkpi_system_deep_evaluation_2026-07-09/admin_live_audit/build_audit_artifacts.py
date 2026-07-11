#!/usr/bin/env python3
"""Build a bounded, read-only Admin audit snapshot and an executed notebook.

The script only issues SELECT statements against the local V-KPI database. It
does not contain the web-admin credential and does not perform product writes.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg
from psycopg.rows import dict_row


ROOT = Path(__file__).resolve().parent
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres@127.0.0.1:54329/viltrox2"
)


QUERIES = {
    "daily_channel_metrics": """
        SELECT
          snapshot_date::text AS date,
          COUNT(DISTINCT channel_id)::int AS channels,
          SUM(followers)::bigint AS followers,
          SUM(total_views)::bigint AS total_views,
          SUM(total_likes)::bigint AS total_likes,
          SUM(total_comments)::bigint AS total_comments,
          SUM(views_delta_24h)::bigint AS views_delta_24h,
          ROUND(AVG(engagement_rate)::numeric, 4) AS avg_engagement_rate
        FROM vkpi_channel_metrics
        GROUP BY snapshot_date
        ORDER BY snapshot_date
    """,
    "action_status": """
        SELECT
          status,
          COUNT(*)::int AS count,
          ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_pct
        FROM vkpi_action_inbox
        GROUP BY status
        ORDER BY count DESC
    """,
    "open_alerts": """
        SELECT rule_key, COUNT(*)::int AS count
        FROM vkpi_alerts
        WHERE status = 'open'
        GROUP BY rule_key
        ORDER BY count DESC
    """,
    "project_stages": """
        SELECT stage, COUNT(*)::int AS count
        FROM vkpi_projects
        GROUP BY stage
        ORDER BY count DESC
    """,
    "assignment_stages": """
        SELECT
          stage,
          COUNT(*)::int AS count,
          COUNT(*) FILTER (WHERE is_placeholder_tracking)::int AS placeholder_count
        FROM vkpi_project_kol_assignments
        GROUP BY stage
        ORDER BY count DESC
    """,
    "reply_status": """
        SELECT status, COUNT(*)::int AS count
        FROM vkpi_reply_queue
        GROUP BY status
        ORDER BY count DESC
    """,
    "flatness": """
        WITH ordered AS (
          SELECT
            channel_id,
            snapshot_date,
            total_likes,
            total_comments,
            LAG(total_likes) OVER (
              PARTITION BY channel_id ORDER BY snapshot_date
            ) AS previous_likes,
            LAG(total_comments) OVER (
              PARTITION BY channel_id ORDER BY snapshot_date
            ) AS previous_comments
          FROM vkpi_channel_metrics
        )
        SELECT
          COUNT(*) FILTER (WHERE previous_likes IS NOT NULL)::int AS pairs,
          COUNT(*) FILTER (
            WHERE previous_likes IS NOT NULL AND total_likes = previous_likes
          )::int AS flat_likes,
          ROUND(
            100.0 * COUNT(*) FILTER (
              WHERE previous_likes IS NOT NULL AND total_likes = previous_likes
            ) / NULLIF(COUNT(*) FILTER (WHERE previous_likes IS NOT NULL), 0),
            1
          ) AS flat_likes_pct,
          COUNT(*) FILTER (
            WHERE previous_comments IS NOT NULL
              AND total_comments = previous_comments
          )::int AS flat_comments,
          ROUND(
            100.0 * COUNT(*) FILTER (
              WHERE previous_comments IS NOT NULL
                AND total_comments = previous_comments
            ) / NULLIF(COUNT(*) FILTER (WHERE previous_comments IS NOT NULL), 0),
            1
          ) AS flat_comments_pct
        FROM ordered
    """,
    "headline": """
        SELECT
          (SELECT COUNT(*) FROM vkpi_channel_metrics)::int AS metric_rows,
          (SELECT COUNT(DISTINCT channel_id) FROM vkpi_channel_metrics)::int AS channels,
          (SELECT MAX(snapshot_date)::text FROM vkpi_channel_metrics) AS metric_latest_date,
          (SELECT (CURRENT_DATE - MAX(snapshot_date))::int FROM vkpi_channel_metrics) AS data_age_days,
          (SELECT MAX(report_date)::text FROM vkpi_official_account_daily_report) AS narrative_latest_date,
          (SELECT ROUND(EXTRACT(EPOCH FROM (NOW() - MAX(last_heartbeat_at))) / 86400.0, 1)
             FROM vkpi_worker_heartbeat) AS worker_stale_days,
          (SELECT COUNT(*) FROM vkpi_action_inbox)::int AS actions_total,
          (SELECT COUNT(*) FROM vkpi_action_inbox WHERE status = 'executed')::int AS actions_executed,
          (SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'executed') / NULLIF(COUNT(*), 0), 1)
             FROM vkpi_action_inbox) AS action_execution_pct,
          (SELECT COUNT(*) FROM vkpi_alerts WHERE status = 'open')::int AS open_alerts,
          (SELECT COUNT(*) FROM staff)::int AS staff_total,
          (SELECT COUNT(*) FROM staff WHERE last_active_at IS NULL)::int AS staff_no_last_active,
          (SELECT COUNT(*) FROM vkpi_staff_groups)::int AS staff_groups,
          (SELECT COUNT(*) FROM vkpi_collab_settings)::int AS collab_settings,
          (SELECT COUNT(*) FROM vkpi_gtm_outcomes)::int AS gtm_outcomes,
          (SELECT COUNT(*) FROM vkpi_prediction_evals)::int AS prediction_evals,
          (SELECT COUNT(*) FROM vkpi_agent_outcome_evaluations)::int AS outcome_evaluations,
          (SELECT COUNT(*) FROM vkpi_agent_outcome_evaluations WHERE success)::int AS successful_evaluations,
          (SELECT COUNT(DISTINCT success) FROM vkpi_agent_outcome_evaluations)::int AS distinct_success_labels,
          (SELECT COUNT(*) FROM vkpi_events)::int AS events,
          (SELECT COUNT(*) FROM vkpi_event_tasks)::int AS event_tasks,
          (SELECT COUNT(*) FROM vkpi_event_evidence)::int AS event_evidence,
          (SELECT COUNT(*) FROM vkpi_event_retrospectives)::int AS event_retrospectives,
          (SELECT COUNT(*) FROM vkpi_dealers)::int AS dealers,
          (SELECT COUNT(*) FROM vkpi_shopify_orders)::int AS shopify_orders,
          (SELECT COUNT(*) FROM vkpi_goaffpro_sales)::int AS goaffpro_sales
    """,
}


PAGE_AUDIT = [
    {"area": "Dashboard", "health": 52, "priority": "P0", "finding": "指标可见但新鲜度不足，地图占首屏，Worker 离线", "screenshot": "01-admin-dashboard.png"},
    {"area": "全局搜索 / Intelligent", "health": 30, "priority": "P0", "finding": "复杂经营问题被误判成固定 ROI 排名并返回 0 行", "screenshot": "12-intelligent-qa-result.png"},
    {"area": "Report", "health": 38, "priority": "P0", "finding": "Dashboard 与报告人数、曝光、互动率口径冲突", "screenshot": "03-report-panel.png"},
    {"area": "MY KOL", "health": 45, "priority": "P0", "finding": "20 人、18 账号、717 MY KOL、1 个库对象并存", "screenshot": "04-my-kol-team.png"},
    {"area": "团队管理", "health": 32, "priority": "P0", "finding": "20 人但无活跃/登录时间、0 组、0 协作设置", "screenshot": "05-team-management.png"},
    {"area": "Projects", "health": 58, "priority": "P0", "finding": "阶段可管理，但导入态、占位 tracking 与真实证据混杂", "screenshot": "06-projects.png"},
    {"area": "战略台", "health": 64, "priority": "P1", "finding": "有 SoV 和机会分析，但缺证据到行动的动态路线", "screenshot": "07-strategy-board-full.png"},
    {"area": "GTM Command", "health": 47, "priority": "P0", "finding": "治理信息完整，但 North Star 几乎未推进且结果为 0", "screenshot": "08-gtm-command.png"},
    {"area": "提醒 / 通知", "health": 40, "priority": "P0", "finding": "提醒 0、建议 12、未读 74；模型分裂且有审计噪音", "screenshot": "10-notifications.png"},
    {"area": "KOL Pool", "health": 61, "priority": "P1", "finding": "1222 条供给强，但数据截至 6 月 12 日且大量待补全", "screenshot": "13-kol-pool.png"},
    {"area": "Market Voice", "health": 46, "priority": "P1", "finding": "规则词典有样本，结果与行动/结果没有串联", "screenshot": "15-market-voice.png"},
    {"area": "创意资产库", "health": 20, "priority": "P0", "finding": "实机持续停留在检索中，核心内容空白", "screenshot": "17-creative-library.png"},
    {"area": "Events", "health": 24, "priority": "P0", "finding": "测试数据、NaN、9642 天活动；任务表为 0", "screenshot": "18-events.png"},
    {"area": "Shopify / GOAFFPRO", "health": 28, "priority": "P1", "finding": "迁移尚未连接，订单与联盟销售为 0", "screenshot": "19-shopify.png"},
    {"area": "Dealers", "health": 18, "priority": "P1", "finding": "0 数据且地图瓦片未正常显示", "screenshot": "20-dealers.png"},
    {"area": "回复队列", "health": 29, "priority": "P0", "finding": "132 条中 130 pending，语言/意图识别存在明显误判", "screenshot": "21-reply-queue.png"},
    {"area": "Launchpad", "health": 42, "priority": "P1", "finding": "需先选实体，尚不是全局组合规划器", "screenshot": "22-launchpad.png"},
    {"area": "自治驾照", "health": 44, "priority": "P0", "finding": "治理概念强，但样本 n=2 或 0，暂不应升级自治", "screenshot": "23-autonomy-license.png"},
]


def normalize(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "as_tuple"):
        return float(value)
    return value


def query_snapshot() -> dict:
    datasets: dict[str, list[dict]] = {}
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            for dataset, sql in QUERIES.items():
                cur.execute(sql)
                datasets[dataset] = [
                    {key: normalize(value) for key, value in row.items()}
                    for row in cur.fetchall()
                ]
    datasets["page_audit"] = PAGE_AUDIT
    datasets["system_scores"] = [
        {"dimension": "完整系统成熟度", "score": 56, "assessment": "工程与模块强，闭环弱"},
        {"dimension": "真正认知与监督能力", "score": 38, "assessment": "规划、监督、学习不足"},
        {"dimension": "UI 一致性与顺畅度", "score": 51, "assessment": "跨页口径和负载分裂"},
        {"dimension": "内部系统成长性", "score": 82, "assessment": "垂直数据与治理骨架稀缺"},
        {"dimension": "SaaS 化就绪度", "score": 35, "assessment": "租户、SLO、配置化未成熟"},
    ]
    return {
        "version": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "ready",
        "datasets": datasets,
    }


def write_csvs(snapshot: dict) -> None:
    for name in (
        "daily_channel_metrics",
        "action_status",
        "open_alerts",
        "project_stages",
        "assignment_stages",
        "reply_status",
        "page_audit",
    ):
        rows = snapshot["datasets"][name]
        with (ROOT / f"{name}.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)


def stream_output(text: str) -> dict:
    return {"name": "stdout", "output_type": "stream", "text": text.splitlines(keepends=True)}


def markdown_cell(source: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": source.splitlines(keepends=True)}


def code_cell(source: str, execution_count: int, namespace: dict) -> dict:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exec(compile(source, f"audit_cell_{execution_count}", "exec"), namespace)
    output = buffer.getvalue()
    return {
        "cell_type": "code",
        "execution_count": execution_count,
        "metadata": {},
        "outputs": [stream_output(output)] if output else [],
        "source": source.splitlines(keepends=True),
    }


def build_notebook(snapshot: dict) -> dict:
    namespace: dict = {}
    cells = [
        markdown_cell(
            "# V-KPI Admin 全系统审计：可复跑数据附录\n\n"
            "## tl;dr\n\n"
            "- 系统广度已经足够；当前瓶颈是事实口径、新鲜度、执行转化和结果标签。\n"
            "- 该 notebook 使用本轮只读数据库快照，复核报告里的关键数字。\n"
            "- 浏览器视觉证据位于同目录 PNG，代码基线为 `9bfcd4a944104e6b010a93df50d07ed7e5c44847`。"
        ),
        markdown_cell(
            "## Context & Methods\n\n"
            "本附录把公司账号时序、Action Inbox、开放告警、Projects、回复队列、团队协作和结果标签放到同一个审计快照。"
        ),
        markdown_cell(
            "### Key Assumptions\n\n"
            "- 数据库 `CURRENT_DATE` 使用运行实例时区，因此新鲜度与 America/New_York 可能相差 1 天。\n"
            "- `executed / all actions` 只表示状态转化，不等于业务结果成功。\n"
            "- 页面健康分是基于本轮 Admin 实机、数据可用性和闭环完整性的审计评分，不是用户满意度调查。"
        ),
    ]
    code_sources = [
        "import json\nfrom pathlib import Path\nimport pandas as pd\n\nSNAPSHOT_PATH = Path('audit_snapshot.json')\nsnapshot = json.loads(SNAPSHOT_PATH.read_text(encoding='utf-8'))\ndata = snapshot['datasets']\nprint('datasets:', ', '.join(sorted(data)))\nprint('generatedAt:', snapshot['generatedAt'])",
        "headline = pd.DataFrame(data['headline'])\nprint(headline.to_string(index=False))",
        "flatness = pd.DataFrame(data['flatness'])\nprint(flatness.to_string(index=False))\nprint('Interpretation: likes/comments are unchanged in most consecutive channel snapshots, so apparent daily coverage does not imply fresh interaction data.')",
        "actions = pd.DataFrame(data['action_status'])\nprint(actions.to_string(index=False))\nexecuted = float(actions.loc[actions.status.eq('executed'), 'share_pct'].iloc[0])\nprint(f'Executed share: {executed:.1f}%')",
        "daily = pd.DataFrame(data['daily_channel_metrics'])\ndaily['date'] = pd.to_datetime(daily['date'])\nprint(daily.tail(12)[['date','channels','followers','views_delta_24h','total_likes','total_comments']].to_string(index=False))",
        "pages = pd.DataFrame(data['page_audit']).sort_values(['priority','health'])\nprint(pages[['area','health','priority','finding']].to_string(index=False))",
        "assignments = pd.DataFrame(data['assignment_stages'])\nreplies = pd.DataFrame(data['reply_status'])\nprint('Assignment stages:')\nprint(assignments.to_string(index=False))\nprint('\\nReply queue:')\nprint(replies.to_string(index=False))",
    ]
    old_cwd = Path.cwd()
    os.chdir(ROOT)
    try:
        for count, source in enumerate(code_sources, start=1):
            cells.append(code_cell(source, count, namespace))
    finally:
        os.chdir(old_cwd)
    cells.extend(
        [
            markdown_cell(
                "## Results\n\n"
                "1. 公司账号明细最新到 2026-06-14，而叙事日报标到 2026-06-29，需在 UI 中同时显示 source date 与 report date。\n"
                "2. Action Inbox 只有约 4.5% 进入 executed；130/132 条回复仍在 pending。\n"
                "3. 20 位成员全部缺 last_active_at / last_login_at，且 0 组、0 协作设置，不能据此衡量成员互动。\n"
                "4. 98 个 outcome evaluation 全部 success，标签只有一个取值，不能支撑自治升级。"
            ),
            markdown_cell(
                "## Takeaways\n\n"
                "第一阶段应建设统一事实层和 KOL commitment supervisor；第二阶段再做问数到可追溯报告；第三阶段才扩大自动执行与学习。"
            ),
        ]
    )
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3"},
            "vkpi_audit": {"executed_by": "build_audit_artifacts.py", "read_only": True},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def main() -> None:
    snapshot = query_snapshot()
    (ROOT / "audit_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_csvs(snapshot)
    notebook = build_notebook(snapshot)
    (ROOT / "vkpi_admin_full_system_audit.ipynb").write_text(
        json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "generated_at": snapshot["generatedAt"],
                "datasets": {key: len(value) for key, value in snapshot["datasets"].items()},
                "notebook_cells": len(notebook["cells"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
