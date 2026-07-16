#!/usr/bin/env python3
"""Run the V-KPI daily incremental sync job.

Default behavior:
- refresh official channels with recent public data only;
- skip legacy KOL pool rows unless an operator explicitly opts in;
- do not call LLM or deep-scan pipelines.
"""
from __future__ import annotations

from stdout_utils import out

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime  # noqa: E402
from app.domains.sync.cron import run_job  # noqa: E402
from app.domains.sync.daily_sync import SyncFailFast, SyncGuardBlocked  # noqa: E402


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_kol_stale_before(raw_value: str = "", stale_days: int = 0, *, now: datetime | None = None) -> str:
    """Return the KOL stale cutoff for periodic qualified refreshes.

    A blank cutoff means qualified catch-up mode, which only selects rows that
    have never been refreshed through vkpi_kol_refresh_tier.
    """
    raw = str(raw_value or "").strip()
    if raw:
        return raw
    days = max(0, int(stale_days or 0))
    if days <= 0:
        return ""
    anchor = now or datetime.now(timezone.utc)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    # +2h 宽限:timer 04:00 开跑、上一轮 04:2x 才刷完,严格 N×24h 会让昨日刷新行今天永不到期
    # → hot 层 92/0 隔日空转 + 哨兵隔日误报断流(2026-07 实测)。宽限吃掉运行时长漂移。
    return (anchor.astimezone(timezone.utc) - timedelta(days=days) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit_event(event: str, **payload: object) -> None:
    out(json.dumps({"event": event, "at": utcnow(), **payload}, ensure_ascii=False, default=str), flush=True)


def result_summary(result: dict[str, object]) -> dict[str, object]:
    inner = result.get("result") if isinstance(result, dict) else {}
    if not isinstance(inner, dict):
        return {}
    official = inner.get("official") if isinstance(inner.get("official"), dict) else {}
    kol = inner.get("kol_pool_light") if isinstance(inner.get("kol_pool_light"), dict) else {}
    return {
        "official_requested": official.get("requested"),
        "official_synced": official.get("synced"),
        "official_failed": official.get("failed"),
        "kol_requested": kol.get("requested"),
        "kol_refreshed": kol.get("refreshed"),
        "kol_partial": kol.get("partial"),
        "kol_errors": kol.get("errors"),
        "started_at": inner.get("started_at"),
        "finished_at": inner.get("finished_at"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V-KPI daily incremental sync")
    parser.add_argument("--dry-run", action="store_true", help="Plan the run without provider calls or DB writes")
    parser.add_argument("--official-max-posts", type=int, default=50, help="Recent posts per official account")
    parser.add_argument("--official-platforms", default="", help="Comma-separated official platforms to run")
    parser.add_argument("--skip-official", action="store_true", help="Skip 18 official-account refresh")
    parser.add_argument("--kol-limit", type=int, default=1200, help="Max KOL pool rows to refresh")
    parser.add_argument("--kol-offset", type=int, default=0, help="Skip the first N selected KOL rows for bounded retries")
    parser.add_argument("--kol-stale-before", default="", help="Only refresh selected KOL rows refreshed before this UTC timestamp")
    parser.add_argument("--kol-stale-days", type=int, default=0, help="Compute --kol-stale-before as now minus N days. Use 1 for daily hot refresh.")
    parser.add_argument("--kol-max-posts", type=int, default=1, help="Latest post sample per KOL pool row")
    parser.add_argument("--kol-error-stop-threshold", type=int, default=3, help="Stop KOL refresh when provider errors reach this count")
    parser.add_argument("--kol-platforms", default="", help="Comma-separated KOL platforms to run")
    parser.add_argument("--kol-refresh-selector", default="qualified", choices=["qualified", "legacy"], help="KOL refresh selector to use when KOL refresh is explicitly included")
    parser.add_argument("--kol-tiers", default="hot", help="Comma-separated refresh tiers for qualified selector")
    parser.add_argument("--kol-source-type", default="legacy_excel_p2d", help="KOL pool source_type scope")
    parser.add_argument("--skip-kol", action="store_true", help="Skip KOL pool lightweight refresh")
    parser.add_argument(
        "--include-legacy-kol",
        action="store_true",
        help="Explicitly run the legacy KOL pool lightweight refresh. Use only for bounded retries until the tier selector replaces it.",
    )
    parser.add_argument(
        "--include-qualified-kol",
        action="store_true",
        help="Explicitly run qualified KOL refresh from vkpi_kol_refresh_tier. Does not enable legacy full-pool refresh.",
    )
    parser.set_defaults(skip_kol=True)
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    kol_selector = "legacy" if args.include_legacy_kol else args.kol_refresh_selector
    payload = {
        "dry_run": bool(args.dry_run),
        "official_max_posts": max(1, min(100, int(args.official_max_posts or 50))),
        "official_platforms": args.official_platforms,
        "skip_official": bool(args.skip_official),
        "kol_limit": max(1, min(1200, int(args.kol_limit or 1200))),
        "kol_offset": max(0, min(5000, int(args.kol_offset or 0))),
        "kol_stale_before": compute_kol_stale_before(args.kol_stale_before, args.kol_stale_days),
        "kol_max_posts": max(1, min(3, int(args.kol_max_posts or 1))),
        "kol_error_stop_threshold": max(0, min(100, int(args.kol_error_stop_threshold or 0))),
        "kol_platforms": args.kol_platforms,
        "kol_refresh_selector": kol_selector,
        "kol_tiers": args.kol_tiers,
        "kol_source_type": args.kol_source_type,
        "skip_kol": bool(args.skip_kol) and not (bool(args.include_legacy_kol) or bool(args.include_qualified_kol)),
        "allow_legacy_kol_full_refresh": bool(args.include_legacy_kol),
        "allow_qualified_kol_refresh": bool(args.include_qualified_kol),
        "staff": {"id": 0, "staff_id": 0, "user_id": 0, "role": "admin", "is_owner": 1},
    }
    try:
        emit_event(
            "cron_daily_sync_started",
            dry_run=payload["dry_run"],
            official_max_posts=payload["official_max_posts"],
            skip_official=payload["skip_official"],
            kol_limit=payload["kol_limit"],
            kol_offset=payload["kol_offset"],
            kol_stale_before=payload["kol_stale_before"],
            kol_max_posts=payload["kol_max_posts"],
            kol_error_stop_threshold=payload["kol_error_stop_threshold"],
            skip_kol=payload["skip_kol"],
            kol_refresh_selector=payload["kol_refresh_selector"],
            kol_tiers=payload["kol_tiers"],
            kol_source_type=payload["kol_source_type"],
        )
        result = await run_job("daily_incremental_sync", payload)
        emit_event("cron_daily_sync_finished", summary=result_summary(result))
        # 同步成功后重建 vkpi_channel_metrics_filled(公司账号 30d 成熟度依赖它;
        # 无定时维护会掉回「累积中」)。只写隔离 filled 表,失败不影响同步退出码。
        try:
            from app.db.connection import get_conn
            from app.domains.channels.metrics_gapfill import backfill_filled_table

            _gap_res = backfill_filled_table(get_conn())
            emit_event("cron_daily_sync_gapfill", summary=_gap_res)
        except Exception as _gap_exc:
            emit_event("cron_daily_sync_gapfill_failed", error=f"{type(_gap_exc).__name__}: {str(_gap_exc)[:200]}")
        # 同步成功后增量维护 KOL 向量召回索引(2026-07-02:索引表空表导致文本搜索
        # 静默归零的事故复盘产物)。expand 只补未入索引的新 KOL(embedding 花费分级别),
        # classify 补分型;子进程跑、双闸超时,失败只记事件不影响同步退出码。
        try:
            import subprocess
            from pathlib import Path

            _repo = str(Path(__file__).resolve().parents[1])
            for _script, _cmd in (
                ("scripts/expand_kol_profile_index.py", "write-and-validate"),
                ("scripts/classify_kol_profile_type.py", "write"),
            ):
                _proc = subprocess.run(
                    [sys.executable, str(Path(_repo) / _script), _cmd],
                    cwd=_repo, capture_output=True, text=True, timeout=1800,
                )
                if _proc.returncode != 0:
                    emit_event(
                        "cron_daily_sync_index_maint_failed",
                        script=_script, code=_proc.returncode, err=str(_proc.stderr)[-300:],
                    )
            emit_event("cron_daily_sync_index_maint_done")
        except Exception as _idx_exc:
            emit_event("cron_daily_sync_index_maint_failed", error=f"{type(_idx_exc).__name__}: {str(_idx_exc)[:200]}")
        out(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        inner = result.get("result") if isinstance(result, dict) else {}
        if isinstance(inner, dict):
            # 退出码与 health 阈值对齐(2026-07-03):此前「任意 1 个官号失败就 exit 2」,
            # 18 官号里 1 个 Apify actor 偶发超时(失败率 0.9%,远低于 10% 阈值)也会把
            # systemd 服务打成 failed 并触发 OnFailure 告警,狼来了掩盖真故障。
            # 现在:blocked_next_run 或失败率超过 health 自己的阈值才 exit 2;
            # 低于阈值的零星失败已在 health/failures 字段里如实记录,退出 0。
            health = inner.get("health") if isinstance(inner.get("health"), dict) else {}
            if bool(health.get("blocked_next_run")):
                return 2
            rate = health.get("failure_rate")
            threshold = health.get("failure_rate_threshold")
            if isinstance(rate, (int, float)) and isinstance(threshold, (int, float)):
                if float(rate) > float(threshold):
                    return 2
                return 0
            # health 块缺失时回退旧口径(任何失败即 2),不放过未知状态
            official = inner.get("official") if isinstance(inner.get("official"), dict) else {}
            kol = inner.get("kol_pool_light") if isinstance(inner.get("kol_pool_light"), dict) else {}
            if int(official.get("failed") or 0) or int(kol.get("errors") or 0):
                return 2
        return 0
    except SyncFailFast as exc:
        emit_event(
            "cron_daily_sync_interrupted",
            exit_code=exc.exit_code,
            run_id=exc.run_id,
            stage=exc.stage,
            summary=exc.summary,
            error=f"{type(exc).__name__}: {str(exc)[:500]}",
        )
        out(json.dumps({
            "job": "daily_incremental_sync",
            "status": "interrupted",
            "exit_code": exc.exit_code,
            "run_id": exc.run_id,
            "stage": exc.stage,
            "summary": exc.summary,
            "error": str(exc),
        }, ensure_ascii=False, default=str, indent=2))
        return exc.exit_code
    except SyncGuardBlocked as exc:
        emit_event(
            "cron_daily_sync_blocked",
            exit_code=exc.exit_code,
            blocking_run_id=exc.blocking_run_id,
            summary=exc.summary,
            error=f"{type(exc).__name__}: {str(exc)[:500]}",
        )
        out(json.dumps({
            "job": "daily_incremental_sync",
            "status": "blocked",
            "exit_code": exc.exit_code,
            "blocking_run_id": exc.blocking_run_id,
            "summary": exc.summary,
            "error": str(exc),
        }, ensure_ascii=False, default=str, indent=2))
        return exc.exit_code
    except Exception as exc:
        emit_event("cron_daily_sync_failed", error=f"{type(exc).__name__}: {str(exc)[:500]}")
        raise
    finally:
        await close_db_runtime()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
