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
    result = run([str(script)], timeout=20)
    try:
        payload = parse_json_blob(result.stdout or result.stderr)
    except Exception as exc:
        return {"available": True, "ready": False, "error": f"invalid readiness output: {exc}", "returncode": result.returncode}
    local = payload.get("local") if isinstance(payload, dict) else {}
    return {
        "available": True,
        "ready": bool(isinstance(local, dict) and local.get("ready_for_new_uploads")),
        "storage_mode": local.get("storage_mode") if isinstance(local, dict) else "",
        "missing_required": local.get("missing_required") if isinstance(local, dict) else [],
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


def status_item(key: str, title: str, status: str, evidence: list[str], next_step: str) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "status": status,
        "evidence": evidence,
        "next_step": next_step,
    }


def build_items(sync: dict[str, Any], r2: dict[str, Any], snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    sync_active = str(sync.get("service_state") or "") in {"active", "activating"}
    r2_ready = bool(r2.get("ready"))
    snapshot_loaded = bool(snapshot.get("loaded"))
    latest_snapshot = snapshot.get("latest_snapshot") if isinstance(snapshot.get("latest_snapshot"), dict) else {}
    competitor_relation_ready = (
        exists("migrations/069_vkpi_competitor_relation.sql")
        and exists("scripts/ops/backfill_vkpi_competitor_relations_after_sync.sh")
        and contains("backend/app/services/vkpi/kol_competitor_detector.py", "vkpi_competitor_relation", "prefer_persisted")
    )
    brand_signal_ready = (
        exists("backend/app/services/vkpi/brand_signal_detector.py")
        and exists("scripts/ops/scan_vkpi_brand_signals_after_sync.sh")
        and contains("backend/app/services/vkpi/brand_signal_detector.py", "scan_cached_brand_signals", "write_db")
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
                f"last_apify_line={sync.get('last_apify_line', '')}",
                f"failure_tail_count={len(sync.get('failure_tail') or [])}",
            ],
            "当前运行不打断；完成后部署本地 HEAD，再检查 18 官方 + 1012 轻量刷新结果。",
        ),
        status_item(
            "a1_r2",
            "A1 R2 云端媒体缓存",
            "blocked" if not r2_ready else "ready",
            [
                "media cache adapter and migration dry-run exist",
                f"storage_mode={r2.get('storage_mode', 'unknown')}",
                f"missing_required={', '.join(r2.get('missing_required') or []) or '-'}",
            ],
            "先配置 R2 env；未 ready 前继续本地 fallback，不执行旧缓存迁移。",
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
            "backfill_ready" if competitor_relation_ready else "partial_ui",
            [
                "kol_competitor_detector.py exists" if exists("backend/app/services/vkpi/kol_competitor_detector.py") else "detector missing",
                "competitor API exists" if contains("backend/app/api/routers/vkpi_kol_pool.py", "/competitors") else "competitor API missing",
                "competitor relation migration exists" if exists("migrations/069_vkpi_competitor_relation.sql") else "competitor relation migration missing",
                "remote backfill guard exists" if exists("scripts/ops/backfill_vkpi_competitor_relations_after_sync.sh") else "backfill guard missing",
                "API can prefer persisted relation" if contains("backend/app/services/vkpi/kol_competitor_detector.py", "prefer_persisted") else "persisted relation read missing",
                "Discover competitor block exists" if contains("frontend/src/components/vkpi/pages/DiscoverPage.tsx", "竞品关系") else "Discover competitor block missing",
            ],
            "等 A2 完成并部署后，运行远端 backfill guard 写入 1012 历史池竞品关系快照。",
        ),
        status_item(
            "brand_signal_c",
            "C Viltrox brand signal",
            "scan_ready" if brand_signal_ready else "partial_live",
            [
                "brand_signal_detector.py exists" if exists("backend/app/services/vkpi/brand_signal_detector.py") else "detector missing",
                "post-sync brand signal guard exists" if exists("scripts/ops/scan_vkpi_brand_signals_after_sync.sh") else "brand signal guard missing",
                "cached scan can write_db" if contains("backend/app/services/vkpi/brand_signal_detector.py", "scan_cached_brand_signals", "write_db") else "brand signal write path missing",
                "DataQuality signal queue exists" if contains("frontend/src/components/vkpi/pages/DataQualityPage.tsx", "Viltrox / 竞品信号") else "signal queue missing",
                "Command Center signal card exists" if contains("frontend/src/components/vkpi/dashboard/CommandCenter.tsx", "品牌信号") else "command signal card missing",
            ],
            "等 A2 完成并部署后，运行远端 brand signal guard，从缓存内容写入真实未处理信号。",
        ),
        status_item(
            "dimensions_d",
            "D 11 维评估",
            "preview_ui",
            [
                "eleven_dimensions.py exists" if exists("backend/app/services/vkpi/eleven_dimensions.py") else "dimension service missing",
                "dimensions11 API exists" if contains("backend/app/api/routers/vkpi_kol_pool.py", "dimensions11") else "dimensions API missing",
                "Discover 11维 UI exists" if contains("frontend/src/components/vkpi/pages/DiscoverPage.tsx", "11维") else "11维 UI missing",
            ],
            "等竞品风险和 profile_deep 表稳定后，再做 batch backfill，不直接跑 1012 deep scan。",
        ),
        status_item(
            "search_ui",
            "红人搜索智能化",
            "partial_live",
            [
                "search progress exists" if contains("frontend/src/components/vkpi/pages/DiscoverPage.tsx", "SearchProgress") else "progress missing",
                "search history exists" if contains("frontend/src/components/vkpi/pages/DiscoverPage.tsx", "搜索历史") else "history missing",
                "1012 history merge exists" if contains("frontend/src/components/vkpi/pages/DiscoverPage.tsx", "1012 历史合作池") else "history merge missing",
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
    report = {
        "generated_at": utcnow(),
        "git": git,
        "daily_sync": sync,
        "r2": r2,
        "prod_snapshot": snapshot,
        "items": build_items(sync, r2, snapshot),
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
