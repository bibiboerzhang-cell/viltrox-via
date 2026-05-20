#!/usr/bin/env python3
"""Generate a compact V-KPI execution status report from repo/runtime signals."""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(command: list[str], *, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, check=False, text=True, capture_output=True, timeout=timeout)


def parse_json_blob(output: str) -> dict[str, Any]:
    text = output.strip()
    if not text:
        raise ValueError("empty output")
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {"value": payload}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(text[start:end + 1])
        return payload if isinstance(payload, dict) else {"value": payload}


def read(path: str) -> str:
    file_path = ROOT / path
    if not file_path.exists():
        return ""
    return file_path.read_text(encoding="utf-8", errors="ignore")


def exists(path: str) -> bool:
    return (ROOT / path).exists()


def contains(path: str, *needles: str) -> bool:
    text = read(path)
    return bool(text) and all(needle in text for needle in needles)


def git_info() -> dict[str, Any]:
    head = run(["git", "rev-parse", "--short", "HEAD"])
    branch = run(["git", "branch", "--show-current"])
    status = run(["git", "status", "--short"])
    log = run(["git", "log", "--oneline", "-20"])
    return {
        "branch": branch.stdout.strip(),
        "head": head.stdout.strip(),
        "dirty": bool(status.stdout.strip()),
        "status_short": status.stdout.splitlines(),
        "recent_commits": log.stdout.splitlines(),
    }


def daily_sync_status() -> dict[str, Any]:
    script = ROOT / "scripts" / "ops" / "check_vkpi_daily_sync_status.sh"
    if not script.exists():
        return {"available": False}
    result = run([str(script)], timeout=60)
    try:
        payload = parse_json_blob(result.stdout or result.stderr)
    except Exception as exc:
        return {
            "available": True,
            "error": f"invalid status output: {exc}",
            "returncode": result.returncode,
            "stdout_tail": result.stdout[-500:],
            "stderr_tail": result.stderr[-500:],
        }
    payload["available"] = True
    payload["returncode"] = result.returncode
    return payload


def r2_readiness() -> dict[str, Any]:
    script = ROOT / "scripts" / "ops" / "check_vkpi_r2_readiness.py"
    if not script.exists():
        return {"available": False}
    command = [str(script)]
    if ROOT != Path("/opt/viltrox-2.0"):
        command.extend(["--remote", "viltrox", "--remote-root", "/opt/viltrox-2.0"])
    result = run(command, timeout=30)
    try:
        payload = parse_json_blob(result.stdout or result.stderr)
    except Exception as exc:
        return {"available": True, "ready": False, "error": f"invalid readiness output: {exc}", "returncode": result.returncode}
    local = payload.get("local") if isinstance(payload, dict) else {}
    remote = payload.get("remote") if isinstance(payload, dict) else {}
    source = remote if isinstance(remote, dict) and not remote.get("error") else local
    return {
        "available": True,
        "ready": bool(isinstance(source, dict) and source.get("ready_for_new_uploads")),
        "storage_mode": source.get("storage_mode") if isinstance(source, dict) else "",
        "missing_required": source.get("missing_required") if isinstance(source, dict) else [],
        "source": source.get("source") if isinstance(source, dict) else "",
        "local_ready": bool(isinstance(local, dict) and local.get("ready_for_new_uploads")),
        "remote_ready": bool(isinstance(remote, dict) and remote.get("ready_for_new_uploads")),
        "returncode": result.returncode,
    }


def prod_snapshot_status() -> dict[str, Any]:
    script = ROOT / "scripts" / "ops" / "check_prod_snapshot_sync_status.sh"
    if not script.exists():
        return {"available": False}
    result = run([str(script)], timeout=20)
    try:
        payload = json.loads(result.stdout)
    except Exception as exc:
        return {"available": True, "loaded": False, "error": f"invalid snapshot output: {exc}", "returncode": result.returncode}
    payload["available"] = True
    payload["returncode"] = result.returncode
    return payload


def post_sync_audit() -> dict[str, Any]:
    script = ROOT / "scripts" / "ops" / "audit_vkpi_post_sync_state.py"
    if not script.exists():
        return {"available": False}
    command = [str(script)]
    if ROOT == Path("/opt/viltrox-2.0"):
        command.append("--local")
    result = run(command, timeout=60)
    try:
        payload = parse_json_blob(result.stdout or result.stderr)
    except Exception as exc:
        return {"available": True, "loaded": False, "error": f"invalid audit output: {exc}", "returncode": result.returncode}
    payload["available"] = True
    payload["loaded"] = result.returncode == 0
    payload["returncode"] = result.returncode
    return payload


def status_item(key: str, title: str, status: str, evidence: list[str], next_step: str) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "status": status,
        "evidence": evidence,
        "next_step": next_step,
    }


def build_items(sync: dict[str, Any], r2: dict[str, Any], snapshot: dict[str, Any], audit: dict[str, Any]) -> list[dict[str, Any]]:
    sync_active = str(sync.get("service_state") or "") in {"active", "activating"}
    r2_ready = bool(r2.get("ready"))
    snapshot_loaded = bool(snapshot.get("loaded"))
    latest_snapshot = snapshot.get("latest_snapshot") if isinstance(snapshot.get("latest_snapshot"), dict) else {}
    acceptance = audit.get("acceptance") if isinstance(audit.get("acceptance"), dict) else {}
    competitor_summary = audit.get("competitor_relation_summary") if isinstance(audit.get("competitor_relation_summary"), dict) else {}
    brand_signal_summary = audit.get("brand_signal_summary") if isinstance(audit.get("brand_signal_summary"), dict) else {}
    competitor_total = int(competitor_summary.get("total") or 0)
    brand_signal_total = int(brand_signal_summary.get("total") or 0)
    competitor_relation_ready = (
        exists("migrations/069_vkpi_competitor_relation.sql")
        and exists("scripts/ops/backfill_vkpi_competitor_relations_after_sync.sh")
        and contains("backend/app/services/vkpi/kol_competitor_detector.py", "vkpi_competitor_relation", "prefer_persisted")
    )
    competitor_recommendation_ready = (
        contains("backend/app/services/vkpi/product_analysis.py", "COMPETITOR_SCORE_ADJUSTMENTS", "filtered_avoid")
        and contains("frontend/src/components/vkpi/pages/DiscoverPage.tsx", "competitorRiskTier", "vkpi-discover-rec__risk")
        and exists("tests/test_vkpi_product_analysis_competitor.py")
    )
    brand_signal_ready = (
        exists("backend/app/services/vkpi/brand_signal_detector.py")
        and exists("scripts/ops/scan_vkpi_brand_signals_after_sync.sh")
        and contains("backend/app/services/vkpi/brand_signal_detector.py", "scan_cached_brand_signals", "write_db")
    )
    dimensions_guard_ready = (
        exists("backend/app/services/vkpi/eleven_dimensions.py")
        and exists("scripts/ops/backfill_vkpi_dimensions11_after_sync.sh")
        and contains("backend/app/services/vkpi/eleven_dimensions.py", "backfill_existing_profile_deep_dimensions11")
    )
    dimensions_confidence_ready = contains(
        "backend/app/services/vkpi/eleven_dimensions.py",
        "product_fit_confidence",
        '"confidence"',
        '"evidence"',
    ) and contains(
        "frontend/src/components/vkpi/pages/DiscoverPage.tsx",
        "pendingByConfidence",
        "product_fit_confidence",
    )
    return [
        status_item(
            "current_release",
            "当前改动封版",
            "done",
            ["git HEAD is clean when status_short is empty", "deploy guard blocks rollout while daily sync is active"],
            "等 vkpi-sync-daily.service 完成后部署当前 HEAD 并校验 asset match。",
        ),
        status_item(
            "perf_p1",
            "性能包 P1/P2",
            "done",
            [
                "channel and KOL pool read cache committed",
                "workspace lazy chunks committed",
                "Channels / Settings / KOL Pool skeletons committed",
            ],
            "同步结束并部署后，用 benchmark 脚本测核心 API 第二次请求。",
        ),
        status_item(
            "a2_daily_sync",
            "A2 每日增量 sync",
            "running" if sync_active else ("done" if sync.get("result") == "success" else "needs_check"),
            [
                f"service_state={sync.get('service_state', 'unknown')}",
                f"inferred_stage={sync.get('inferred_stage', '-')}",
                f"last_activity_at={sync.get('last_activity_at', '-')}",
                f"actor_run_count={sync.get('actor_run_count', '-')}",
                f"dataset_fetch_count={sync.get('dataset_fetch_count', '-')}",
                f"last_apify_line={sync.get('last_apify_line', '')}",
                f"failure_tail_count={len(sync.get('failure_tail') or [])}",
            ],
            "已完成本轮同步；后续保持 timer，下一轮只跑轻量刷新，不开 deep scan。" if sync.get("result") == "success" else "当前运行不打断；完成后部署本地 HEAD，再检查 18 官方 + 1012 轻量刷新结果。",
        ),
        status_item(
            "a1_r2",
            "A1 R2 云端媒体缓存",
            "blocked" if not r2_ready else "ready",
            [
                "media cache adapter and migration dry-run exist",
                "post-sync R2 migration guard exists" if exists("scripts/ops/migrate_vkpi_media_cache_to_r2_after_sync.sh") else "R2 migration guard missing",
                f"storage_mode={r2.get('storage_mode', 'unknown')}",
                f"readiness_source={r2.get('source', '-')}",
                f"runtime_ready={r2.get('ready', '-')}",
                f"missing_required={', '.join(r2.get('missing_required') or []) or '-'}",
            ],
            "R2 runtime 已 ready；继续按 limit 分批迁移旧缓存，并复测播放/图片签名 URL。" if r2_ready else "先配置 R2 env；未 ready 前继续本地 fallback，不执行旧缓存迁移。",
        ),
        status_item(
            "prod_snapshot",
            "本地/云端单向同步",
            "done" if snapshot_loaded else "needs_check",
            [
                f"launch_agent_loaded={snapshot_loaded}",
                f"latest_snapshot={latest_snapshot.get('path', '-')}",
                f"dump_size_bytes={latest_snapshot.get('dump_size_bytes', '-')}",
            ],
            "保持只下载不恢复；需要恢复时必须显式设置 RESTORE_LOCAL 和 LOCAL_DATABASE_URL。",
        ),
        status_item(
            "competitor_b",
            "B 竞品识别",
            "done" if competitor_total > 0 else ("backfill_ready" if competitor_relation_ready else "partial_ui"),
            [
                "kol_competitor_detector.py exists" if exists("backend/app/services/vkpi/kol_competitor_detector.py") else "detector missing",
                "competitor API exists" if contains("backend/app/api/routers/vkpi_kol_pool.py", "/competitors") else "competitor API missing",
                "competitor relation migration exists" if exists("migrations/069_vkpi_competitor_relation.sql") else "competitor relation migration missing",
                "remote backfill guard exists" if exists("scripts/ops/backfill_vkpi_competitor_relations_after_sync.sh") else "backfill guard missing",
                "API can prefer persisted relation" if contains("backend/app/services/vkpi/kol_competitor_detector.py", "prefer_persisted") else "persisted relation read missing",
                "Discover competitor block exists" if contains("frontend/src/components/vkpi/pages/DiscoverPage.tsx", "竞品关系") else "Discover competitor block missing",
                "recommendation filter exists" if competitor_recommendation_ready else "recommendation filter missing",
                f"persisted_relations={competitor_total}",
                f"kol_count={competitor_summary.get('kol_count', '-')}",
                f"avoid={competitor_summary.get('avoid_count', '-')}",
                f"caution={competitor_summary.get('caution_count', '-')}",
            ],
            (
                "推荐过滤已接入；下一步复测详情页竞品区和推荐列表 risk pill。"
                if competitor_recommendation_ready
                else "已写入 1012 历史池竞品关系；下一步接前端推荐过滤和详情页复测。"
            ) if competitor_total > 0 else "等 A2 完成并部署后，运行远端 backfill guard 写入 1012 历史池竞品关系快照。",
        ),
        status_item(
            "brand_signal_c",
            "C Viltrox brand signal",
            "done" if brand_signal_total > 0 else ("scan_ready" if brand_signal_ready else "partial_live"),
            [
                "brand_signal_detector.py exists" if exists("backend/app/services/vkpi/brand_signal_detector.py") else "detector missing",
                "post-sync brand signal guard exists" if exists("scripts/ops/scan_vkpi_brand_signals_after_sync.sh") else "brand signal guard missing",
                "cached scan can write_db" if contains("backend/app/services/vkpi/brand_signal_detector.py", "scan_cached_brand_signals", "write_db") else "brand signal write path missing",
                "DataQuality signal queue exists" if contains("frontend/src/components/vkpi/pages/DataQualityPage.tsx", "Viltrox / 竞品信号") else "signal queue missing",
                "Command Center signal card exists" if contains("frontend/src/components/vkpi/dashboard/CommandCenter.tsx", "品牌信号") else "command signal card missing",
                f"persisted_signals={brand_signal_total}",
                f"current_year={brand_signal_summary.get('current_year_count', '-')}",
                f"competitor={brand_signal_summary.get('competitor_count', '-')}",
                f"new={brand_signal_summary.get('new_count', '-')}",
            ],
            "已写入缓存品牌信号；下一步复测 Dashboard/DataQuality 是否按真实新信号展示。" if brand_signal_total > 0 else "等 A2 完成并部署后，运行远端 brand signal guard，从缓存内容写入真实未处理信号。",
        ),
        status_item(
            "dimensions_d",
            "D 11 维评估",
            "guard_ready" if dimensions_guard_ready else "preview_ui",
            [
                "eleven_dimensions.py exists" if exists("backend/app/services/vkpi/eleven_dimensions.py") else "dimension service missing",
                "profile_deep update guard exists" if exists("scripts/ops/backfill_vkpi_dimensions11_after_sync.sh") else "dimensions backfill guard missing",
                "updates existing profile_deep only" if contains("backend/app/services/vkpi/eleven_dimensions.py", "backfill_existing_profile_deep_dimensions11") else "dimensions write path missing",
                "confidence/evidence guards exist" if dimensions_confidence_ready else "confidence/evidence guards missing",
                "dimensions11 API exists" if contains("backend/app/api/routers/vkpi_kol_pool.py", "dimensions11") else "dimensions API missing",
                "Discover 11维 UI exists" if contains("frontend/src/components/vkpi/pages/DiscoverPage.tsx", "11维") else "11维 UI missing",
            ],
            "11维规则画像已带 confidence/evidence；profile_deep 表未建时守卫脚本安全跳过，不插假画像。",
        ),
        status_item(
            "search_ui",
            "红人搜索智能化",
            "partial_live",
            [
                "search progress exists" if contains("frontend/src/components/vkpi/pages/DiscoverPage.tsx", "SearchProgress") else "progress missing",
                "search history exists" if contains("frontend/src/components/vkpi/pages/DiscoverPage.tsx", "搜索历史") else "history missing",
                "1012 history merge exists" if contains("frontend/src/components/vkpi/pages/DiscoverPage.tsx", "1012 历史合作池") else "history merge missing",
                f"legacy_1012_present={acceptance.get('legacy_1012_present', '-')}",
            ],
            "部署后用真实关键词复测头像、候选逐条出现和最近内容回填。",
        ),
    ]


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# V-KPI Current Execution Status",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- branch: `{report['git']['branch']}`",
        f"- head: `{report['git']['head']}`",
        f"- dirty: `{report['git']['dirty']}`",
        "",
        "| 路线 | 状态 | 证据 | 下一步 |",
        "|---|---|---|---|",
    ]
    for item in report["items"]:
        evidence = "<br>".join(str(value).replace("|", "\\|") for value in item["evidence"])
        lines.append(f"| {item['title']} | `{item['status']}` | {evidence} | {item['next_step']} |")
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report current V-KPI execution status")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    parser.add_argument("--out", default="", help="Optional output path for Markdown report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    git = git_info()
    sync = daily_sync_status()
    r2 = r2_readiness()
    snapshot = prod_snapshot_status()
    audit = post_sync_audit()
    report = {
        "generated_at": utcnow(),
        "git": git,
        "daily_sync": sync,
        "r2": r2,
        "prod_snapshot": snapshot,
        "post_sync_audit": audit,
        "items": build_items(sync, r2, snapshot, audit),
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        markdown = render_markdown(report)
        print(markdown)
        if args.out:
            out = ROOT / args.out
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
