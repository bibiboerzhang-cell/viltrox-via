"""Stable human-readable reporting for the promotion-plan ETL."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from stdout_utils import out

if __package__:
    from .etl_excel_to_vkpi_core import AssignmentPlan, ExcelRow, project_name, text
else:  # pragma: no cover - exercised by the legacy script entry point
    from etl_excel_to_vkpi_core import AssignmentPlan, ExcelRow, project_name, text


def top_unmatched(
    unmatched: list[dict[str, Any]], limit: int = 30
) -> list[tuple[str, str, int]]:
    grouped: Counter[tuple[str, str]] = Counter(
        (item["name"], item["sheet"]) for item in unmatched
    )
    return [
        (name, sheet, count)
        for (name, sheet), count in grouped.most_common(limit)
    ]


def top_fuzzy_medium(
    fuzzy_medium: list[dict[str, Any]], limit: int = 20
) -> list[dict[str, Any]]:
    return sorted(fuzzy_medium, key=lambda item: item["score"])[:limit]


def print_report(
    *,
    skipped: list[str],
    empty_products: list[str],
    rows_by_sheet: dict[str, list[ExcelRow]],
    projects: list[dict[str, Any]],
    match_report: dict[str, Any],
    assignments: list[AssignmentPlan],
    evidence: list[dict[str, Any]],
    evidence_stats: Counter,
    existing_evidence_urls: set[str],
    active_pool_ids: set[int],
    pool_details: dict[int, dict[str, Any]],
    mode: str,
) -> None:
    stats: Counter = match_report["stats"]
    new_pool_plans: list[dict[str, Any]] = match_report["new_pool_plans"]
    evidence_pool_ids = {row["kol_pool_id"] for row in evidence}
    published_pool_ids = {
        plan.kol_pool_id
        for plan in assignments
        if plan.stage in {"content_posted", "reviewed"}
    }
    need_scrape_pool_ids = published_pool_ids - evidence_pool_ids
    placeholder_count = sum(
        1 for plan in assignments if plan.is_placeholder_tracking
    )
    active_roster = len(evidence_pool_ids)

    evidence_by_source = Counter(row["source"] for row in evidence)
    new_evidence = [
        row for row in evidence if row["content_url"] not in existing_evidence_urls
    ]
    new_evidence_by_type = Counter(row["evidence_type"] for row in new_evidence)
    new_active_pool_ids = {
        int(row["kol_pool_id"])
        for row in new_evidence
        if int(row["kol_pool_id"]) > 0
        and int(row["kol_pool_id"]) not in active_pool_ids
    }
    new_media_articles_by_pool: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in new_evidence:
        pool_id = int(row["kol_pool_id"])
        if pool_id in new_active_pool_ids and row["evidence_type"] == "media_article":
            new_media_articles_by_pool[pool_id].append(row)
    out("=" * 60)
    out(f"ETL {'Commit' if mode == 'commit' else 'Dry-Run'} 报告 (V2)")
    out("=" * 60)
    out("\n[Projects]")
    out(f"  {len(rows_by_sheet)} sheet -> {len(projects)} project")
    out(f"  跳过 sheet: {len(skipped)}")
    if empty_products:
        out(f"  空产品 sheet: {len(empty_products)} 个 ({', '.join(empty_products)})")

    out("\n[KOL 匹配]")
    out(f"  exact:           {stats['exact']}")
    out(f"  fuzzy_high:      {stats['fuzzy_high']}")
    out(
        f"  fuzzy_medium:    {stats['fuzzy_medium']} "
        f"(全部 reject: {stats['fuzzy_medium_rejected']})"
    )
    out(f"  unmatched (将新建 pool):  {stats['unmatched_new_pool_rows']}")
    out(f"  unmatched (WORKSHOP 跳过): {stats['unmatched_workshop_skipped']}")

    out("\n[匹配失败 top 30 (Excel 名)]")
    unmatched_top = top_unmatched(match_report["unmatched"])
    if unmatched_top:
        for index, (name, sheet, count) in enumerate(unmatched_top, 1):
            out(
                f"  {index}. {name}  "
                f"(项目: {project_name(sheet)}, sheet出现 {count} 次)"
            )
    else:
        out("  无")

    out("\n[fuzzy_medium top 20]")
    fuzzy_top = top_fuzzy_medium(match_report["fuzzy_medium"])
    if fuzzy_top:
        for index, item in enumerate(fuzzy_top, 1):
            out(
                f"  {index}. Excel: \"{item['name']}\" -> Pool: \"{item['matched_via']}\" "
                f"(score={item['score']:.1f}, 项目: {project_name(item['sheet'])}, "
                f"row={item['row']})"
            )
    else:
        out("  无")

    out("\n[新建 pool]")
    out(f"  将新建 vkpi_kol_pool 记录: {len(new_pool_plans)}")
    out(f"  覆盖 unmatched 行数: {stats['unmatched_new_pool_rows']}")
    out(f"  - account_type=kol:     {stats['new_pool_kol']}")
    out(f"  - account_type=media:   {stats['new_pool_media']}")
    out(f"  - account_type=company: {stats['new_pool_company']}")

    out("\n[Assignments]")
    out(
        f"  合并前: {stats['exact']} + {stats['unmatched_new_pool_rows']} = "
        f"{stats['exact'] + stats['fuzzy_high'] + stats['unmatched_new_pool_rows']}"
    )
    out(
        f"  合并后: {match_report['merged_count']} "
        f"(合并 {match_report['duplicate_extra_rows']} 条, "
        f"{match_report['duplicate_groups']} 组)"
    )
    out(f"  placeholder UPS 单号: {placeholder_count} 条")

    out("\n[Video Evidence]")
    out(f"  回片链接 -> valid evidence:        {evidence_by_source['excel_huipian']}")
    out(
        f"  回片链接 -> blacklisted:           "
        f"{evidence_stats[('回片链接', 'blacklisted')]}"
    )
    out(
        f"  回片链接 -> unknown_domain:        "
        f"{evidence_stats[('回片链接', 'unknown_domain')]}"
    )
    out(
        f"  回片链接 -> media_domain:          "
        f"{evidence_stats[('回片链接', 'media_domain')]}"
    )
    out(
        f"  内容发布链接 -> valid evidence:     "
        f"{evidence_by_source['excel_published']}"
    )
    out(
        f"  内容发布链接 -> blacklisted:        "
        f"{evidence_stats[('内容发布链接', 'blacklisted')]}"
    )
    out(
        f"  内容发布链接 -> unknown_domain:     "
        f"{evidence_stats[('内容发布链接', 'unknown_domain')]}"
    )
    out(
        f"  内容发布链接 -> media_domain:       "
        f"{evidence_stats[('内容发布链接', 'media_domain')]}"
    )
    out(f"  合计 valid evidence URL:           {len(evidence)}")

    out("\n[Fix 1 增量预测]")
    out(f"  video evidence 新增:        {new_evidence_by_type['video']}")
    out(f"  media_article 新增:         {new_evidence_by_type['media_article']}")
    out(f"  预计新增 active KOL:        {len(new_active_pool_ids)}")
    new_media_articles = [
        row for row in new_evidence if row["evidence_type"] == "media_article"
    ]
    if new_media_articles:
        out("\n[media_article 新增候选样本 top 5]")
        for index, row in enumerate(new_media_articles[:5], 1):
            out(f"  {index}. {row['content_url']} ({row['source_ref']})")

    if new_active_pool_ids:
        out(f"\n[新 active KOL 候选 {len(new_active_pool_ids)} 人]")
        out("display_name | dashboard_account_type | tier | 新增 media_article 数 | 样本 1 URL")
        for pool_id in sorted(
            new_active_pool_ids,
            key=lambda value: text(
                pool_details.get(value, {}).get("display_name")
                or pool_details.get(value, {}).get("handle")
            ).lower(),
        ):
            detail = pool_details.get(pool_id, {})
            articles = new_media_articles_by_pool.get(pool_id, [])
            display_name = (
                text(detail.get("display_name"))
                or text(detail.get("handle"))
                or str(pool_id)
            )
            account_type = text(detail.get("dashboard_account_type")) or "-"
            tier = text(detail.get("dashboard_tier")) or "-"
            sample_url = articles[0]["content_url"] if articles else "-"
            out(
                f"{display_name} | {account_type} | {tier} | "
                f"{len(articles)} | {sample_url}"
            )

    out("\n[needs_scrape]")
    out(f"  已合作但无视频证据: {len(need_scrape_pool_ids)} 个 KOL")

    out("\n" + "=" * 60)
    out("预测最终 KPI:")
    out(f"  vkpi_projects 新增/更新: {len(projects)}")
    out(f"  vkpi_kol_pool 新增: {len(new_pool_plans)}")
    out(f"  vkpi_project_kol_assignments 新增/更新: {len(assignments)}")
    out(f"  vkpi_kol_video_evidence 新增: {len(evidence)}")
    out(f"  has_video_evidence=TRUE: {active_roster} (= Active Roster)")
    out(f"  needs_scrape=TRUE:        {len(need_scrape_pool_ids)}")
    out("=" * 60)
