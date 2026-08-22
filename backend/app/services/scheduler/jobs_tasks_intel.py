"""
services/scheduler/jobs_tasks_intel.py — 市场情报 / AI Today / 官号日报 定时任务簇
=============================================================
从 jobs_tasks.py 行为不变搬来的「每日情报/市场/官号」config-gate 任务簇:
V6 Fit 快照 / AI Today 简报 / 竞品雷达 / 市场信号刷新 / 今日热点 / 官号日报 / 官号画质扫描。
jobs_tasks.py 通过 `from .jobs_tasks_intel import (...)` re-export 兜住所有调用点。

红线对齐(与 jobs_tasks.py 原注释同款):走预算闸 + 代理;config-gate 默认 OFF;
绝不写 viltrox_fit_score(快照只读源列不写回)。核心情报 job 回写注册表运行结果。
"""
from __future__ import annotations

import contextvars
import functools
import inspect
import json
from typing import Any, Callable

from app.core.logging import get_logger

logger = get_logger(__name__)

_AI_TODAY_ATTEMPT_KIND = "ai_today_attempt_v1"

# ── 运行记录槽位协议(注册层包装 ↔ 任务体)────────────────────────────────────
# B3:此前只有部分任务在体内显式调 _record_scheduler_run,fit_snapshot 等不记,
# 验收门读注册表 last_run_at 时把"跑过"误判成"从未跑"。统一做法:注册层
# (jobs_registry._RunRecordingRegistration)用 with_scheduler_run_record 包住每个
# add_job 的回调,运行前登记一个槽位,运行后按 ok/error 补记。任务体内既有的显式
# 回写先落库并在槽位标记 recorded(包装不再重复写,保持幂等);config-gate 拒跑
# 标记 skipped(没真跑就不伪造 last_run_at)。本模块是 jobs_tasks 的模块级叶子,
# 槽位放这里可被 jobs_tasks / jobs_registry 两侧无环 import。
_RUN_RECORD_SLOT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "vkpi_scheduler_run_record_slot", default=None
)


def _note_run_record_slot(task_key: str, flag: str) -> None:
    """在当前运行槽位上打标(recorded / skipped);键不匹配或无槽位则无事发生。"""
    slot = _RUN_RECORD_SLOT.get()
    if slot is not None and slot.get("task_key") == str(task_key or "").strip():
        slot[flag] = True


def _gate_result(task_key: str, enabled: bool) -> bool:
    """config-gate 结果透传;拒跑时标记槽位 skipped,让包装层不伪造运行记录。"""
    if not enabled:
        _note_run_record_slot(task_key, "skipped")
    return enabled


def with_scheduler_run_record(task_key: str, func: Callable[..., Any]) -> Callable[..., Any]:
    """把一个 APScheduler 回调包成"运行前登记槽位、运行后统一回写注册表"。

    - 正常返回 → record_run(ok=True);抛异常 → record_run(ok=False, error) 后原样 re-raise。
    - 任务体内已显式回写(槽位 recorded)或 config-gate 拒跑(槽位 skipped)→ 不再写。
    - 协程/同步函数分别包装,保持 APScheduler 的 iscoroutinefunction 判定不变;幂等(重复包装返回原函数)。
    """
    if getattr(func, "__vkpi_scheduler_run_record__", False):
        return func
    key = str(task_key or "").strip()

    def _finish(slot: dict[str, Any], *, ok: bool, error: str = "") -> None:
        if not key or slot.get("recorded") or slot.get("skipped"):
            return
        _record_scheduler_run(key, ok=ok, error=error)

    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def _async_recorded(*args: Any, **kwargs: Any) -> Any:
            slot: dict[str, Any] = {"task_key": key}
            token = _RUN_RECORD_SLOT.set(slot)
            try:
                result = await func(*args, **kwargs)
            except BaseException as exc:
                _finish(slot, ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
                raise
            else:
                _finish(slot, ok=True)
                return result
            finally:
                _RUN_RECORD_SLOT.reset(token)

        wrapped: Callable[..., Any] = _async_recorded
    else:
        @functools.wraps(func)
        def _sync_recorded(*args: Any, **kwargs: Any) -> Any:
            slot: dict[str, Any] = {"task_key": key}
            token = _RUN_RECORD_SLOT.set(slot)
            try:
                result = func(*args, **kwargs)
            except BaseException as exc:
                _finish(slot, ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
                raise
            else:
                _finish(slot, ok=True)
                return result
            finally:
                _RUN_RECORD_SLOT.reset(token)

        wrapped = _sync_recorded

    setattr(wrapped, "__vkpi_scheduler_run_record__", True)
    setattr(wrapped, "__vkpi_scheduler_run_record_key__", key)
    return wrapped


def _attempt_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _ai_today_attempt_error(payload: dict[str, Any]) -> str:
    """Serialize a bounded, non-secret failure receipt into scheduler_tasks.last_error."""
    provenance = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
    raw_attempts = provenance.get("attempts") if isinstance(provenance.get("attempts"), list) else []
    attempts = [item for item in raw_attempts if isinstance(item, dict)]
    latest_provider_attempt = attempts[-1] if attempts else {}
    providers_attempted: list[str] = []
    for attempt in attempts:
        provider = _attempt_text(attempt.get("provider"), 40)
        if provider and provider not in providers_attempted:
            providers_attempted.append(provider)

    # A failed fallback can leave provenance.provider/model pointing at an earlier
    # Gemini response. The last concrete provider attempt is the truthful latest
    # attempt for operations and UI diagnostics.
    provider = _attempt_text(
        latest_provider_attempt.get("provider") or provenance.get("provider") or "unknown",
        40,
    )
    model = _attempt_text(
        latest_provider_attempt.get("model") or provenance.get("model") or payload.get("model"),
        96,
    )
    receipt: dict[str, Any] = {
        "kind": _AI_TODAY_ATTEMPT_KIND,
        "status": _attempt_text(
            payload.get("result_status") or payload.get("contract_status") or payload.get("status") or "failed",
            40,
        ),
        "reason": _attempt_text(
            payload.get("error") or payload.get("reason") or "ai_today_not_ready",
            140,
        ),
        "provider": provider or "unknown",
        "provider_status": _attempt_text(latest_provider_attempt.get("status"), 60),
        "generation_status": _attempt_text(provenance.get("status"), 60),
        "model": model,
        "providers_attempted": providers_attempted[:4],
        "generated_at": _attempt_text(payload.get("generated_at"), 40),
    }
    receipt = {key: value for key, value in receipt.items() if value not in ("", [], None)}
    encoded = json.dumps(receipt, ensure_ascii=True, separators=(",", ":"))
    if len(encoded) <= 480:
        return encoded

    # scheduler_registry caps last_error at 500 characters. Drop optional fields
    # before shortening the reason so the stored JSON always remains parseable.
    receipt.pop("model", None)
    receipt.pop("providers_attempted", None)
    encoded = json.dumps(receipt, ensure_ascii=True, separators=(",", ":"))
    if len(encoded) <= 480:
        return encoded
    receipt["reason"] = _attempt_text(receipt.get("reason"), 60)
    return json.dumps(receipt, ensure_ascii=True, separators=(",", ":"))


def _scheduler_task_enabled(task_key: str) -> bool:
    from .jobs_tasks import _scheduler_task_enabled as _impl

    return _impl(task_key)


def _record_scheduler_run(task_key: str, *, ok: bool, error: str = "") -> None:
    try:
        from .jobs_tasks import _record_scheduler_run as _impl

        _impl(task_key, ok=ok, error=error)
    except Exception:
        logger.debug("scheduler.intel_record_run_failed", extra={"task": task_key}, exc_info=True)


def _record_scheduler_result(task_key: str, result: Any) -> None:
    payload = result if isinstance(result, dict) else {}
    status = str(payload.get("status") or "").strip().lower()
    ok = status == "ok"
    error = ""
    if not ok:
        if task_key == "vkpi_ai_today_hot":
            error = _ai_today_attempt_error(payload)
        else:
            error = str(payload.get("error") or payload.get("reason") or f"status={status or 'missing'}")[:240]
    _record_scheduler_run(task_key, ok=ok, error=error)


async def job_vkpi_fit_snapshot():
    """V6 Fit Top 每日快照:只读 vkpi_kol_pool.viltrox_fit_score/followers → 历史表,供 Top Movers diff。
    红线安全:绝不写回源列(指纹不变),零 LLM/provider。config-gate(scheduler_tasks.vkpi_fit_snapshot)。"""
    from .jobs_tasks import _scheduler_task_enabled

    if not _scheduler_task_enabled("vkpi_fit_snapshot"):
        return
    try:
        import asyncio
        from app.domains.dashboard import fit_snapshot

        result = await asyncio.to_thread(fit_snapshot.capture_daily_snapshot)
        logger.info("scheduler.vkpi_fit_snapshot", extra={"result": result})
    except Exception:
        logger.exception("scheduler.vkpi_fit_snapshot_failed")


def _run_brief_agent_daily() -> dict:
    """AI Today 简报 Agent:确定性重建候选汇总(零 LLM/provider/写库),写 runtime/ops 供 dashboard 读。"""
    import json as _json
    from pathlib import Path

    from app.domains.intelligence import brief_use_case

    report = brief_use_case.build_brief_agent_v0(
        kol_pool_ids="",
        ops_dir="runtime/ops",
        limit=8,
        min_evidence_refs=3,
        ref_limit=8,
        claim_limit=12,
        use_latest_recommendation_artifact=False,  # 每天从真实 evidence 重建(确定性),取最新
    )
    out = Path("runtime/ops")
    out.mkdir(parents=True, exist_ok=True)
    (out / "scheduler-p7-83-brief-agent-v0.json").write_text(
        _json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return {
        "passed": bool(report.get("passed")),
        "items": int(report.get("brief_item_count") or len(report.get("items") or [])),
    }


async def job_vkpi_brief_agent():
    """AI Today 简报 Agent 每日刷新。确定性、零 LLM/provider/写库。config-gate(scheduler_tasks.vkpi_brief_agent)。"""
    from .jobs_tasks import _scheduler_task_enabled

    if not _scheduler_task_enabled("vkpi_brief_agent"):
        return
    try:
        import asyncio

        result = await asyncio.to_thread(_run_brief_agent_daily)
        logger.info("scheduler.vkpi_brief_agent", extra={"result": result})
    except Exception:
        logger.exception("scheduler.vkpi_brief_agent_failed")


async def job_vkpi_competitor_radar():
    """竞品新品雷达(每早·Gemini+Google 接地):查海外竞品新镜头/相机发布 + 对 Viltrox 影响。
    红线:走预算闸(cron:competitor_radar)+ 代理;一天一次。config-gate(scheduler_tasks.vkpi_competitor_radar)。"""
    if not _scheduler_task_enabled("vkpi_competitor_radar"):
        return
    try:
        import asyncio
        from app.domains.market import competitor_radar

        result = await asyncio.to_thread(competitor_radar.generate_competitor_radar)
        logger.info("scheduler.vkpi_competitor_radar", extra={"result": result})
        _record_scheduler_result("vkpi_competitor_radar", result)
    except Exception as exc:
        logger.exception("scheduler.vkpi_competitor_radar_failed")
        _record_scheduler_run("vkpi_competitor_radar", ok=False, error=str(exc)[:240])


async def job_vkpi_market_signal_refresh():
    """Signals & Alerts 每日刷新(竞品新品 + Reddit/Google News 热度):allowlisted 有界抓取,零 LLM/零 DB 写。
    竞品入库仍走人工审核闸(本 job 不 promote)。config-gate(scheduler_tasks.vkpi_market_signal_refresh)。"""
    if not _scheduler_task_enabled("vkpi_market_signal_refresh"):
        return
    try:
        import asyncio
        from app.domains.market import signal_refresh

        result = await asyncio.to_thread(signal_refresh.refresh_external_signals)
        logger.info("scheduler.vkpi_market_signal_refresh", extra={"result": result})
        _record_scheduler_result("vkpi_market_signal_refresh", result)
    except Exception as exc:
        logger.exception("scheduler.vkpi_market_signal_refresh_failed")
        _record_scheduler_run("vkpi_market_signal_refresh", ok=False, error=str(exc)[:240])


async def job_vkpi_ai_today_hot():
    """AI Today 今日热点(每早8点中国时区):LLM 据真实行业热点生成拍摄方案+话题。
    红线:走预算闸(cron:ai_today_hot 硬上限)+ claude 代理;一天一次。config-gate(scheduler_tasks.vkpi_ai_today_hot)。"""
    if not _scheduler_task_enabled("vkpi_ai_today_hot"):
        return
    try:
        import asyncio
        from app.domains.market import ai_today

        result = await asyncio.to_thread(ai_today.generate_ai_today_hot)
        logger.info("scheduler.vkpi_ai_today_hot", extra={"result": result})
        _record_scheduler_result("vkpi_ai_today_hot", result)
    except Exception as exc:
        logger.exception("scheduler.vkpi_ai_today_hot_failed")
        _record_scheduler_run("vkpi_ai_today_hot", ok=False, error=str(exc)[:240])


async def job_vkpi_official_daily_report(round_key: str = "daily"):
    """每日官号分析报告(每天2轮:中国早8/美西早6):逐 18 官号 LLM 合成
    播放/评论/画面质量/数据趋势/提升建议。config-gate(scheduler_tasks.vkpi_official_daily_report);
    走预算闸 cron:official_daily_report(硬上限$4/日)+ claude 代理。"""
    from .jobs_tasks import _scheduler_task_enabled

    if not _scheduler_task_enabled("vkpi_official_daily_report"):
        return
    try:
        import asyncio
        from app.domains.channels import official_daily_report

        result = await asyncio.to_thread(
            official_daily_report.generate_official_daily_reports, round_key=round_key
        )
        logger.info(
            "scheduler.vkpi_official_daily_report",
            extra={"round": round_key, **{k: result.get(k) for k in ("ok", "skipped", "blocked", "failed")}},
        )
        # 消盲区:回写注册表 last_run_at——此前不回写,停摆 17 天时注册表谎报"从未跑过",排查被带偏。
        from .jobs_tasks import _record_scheduler_run

        _ok = int(result.get("ok") or 0)
        _blocked = int(result.get("blocked") or 0)
        _record_scheduler_run(
            "vkpi_official_daily_report",
            ok=_ok > 0,
            error="" if _ok > 0 else f"ok=0 blocked={_blocked}(预算闸?)",
        )
    except Exception:
        logger.exception("scheduler.vkpi_official_daily_report_failed")
        try:
            from .jobs_tasks import _record_scheduler_run

            _record_scheduler_run("vkpi_official_daily_report", ok=False, error="exception(见日志)")
        except Exception:
            logger.debug("scheduler run 兜底记录失败(best-effort)", exc_info=True)


async def job_market_voice_alerts():
    """市场之声「声量告警」(V0f):近 8h 窗按抱怨类别统计负面提及(lexicon_v0 口径复用,
    官号帖 post_table='vkpi_employee_channels' 评论权重×2),加权分 ≥3 触发 →
    推「今日该做什么」(vkpi_action_inbox,同类别同日 dedupe_key 幂等;纯提醒不做铃铛)。
    零 LLM/零外调/零成本;唯一写=action inbox 自身台账;零触 viltrox_fit_score / rule_v0。

    config-gate(scheduler_tasks.market_voice_alerts):注册表无此行/未开 → 默认关不跑。
    开启方式(用户动作,默认不烧、不惊扰):
      INSERT INTO scheduler_tasks (task_key, label, enabled, risk_level)
      VALUES ('market_voice_alerts', '市场之声声量告警(8h窗×抱怨类别,官号×2,推今日该做什么)', TRUE, 'low')
      ON CONFLICT (task_key) DO UPDATE SET enabled = TRUE;
    (本地/测试可 env OPS_SCHEDULER_FORCE_ENABLE=1 整体强开。)"""
    if not _scheduler_task_enabled("market_voice_alerts"):
        return
    try:
        import asyncio
        from app.domains.market import voice_alerts

        result = await asyncio.to_thread(voice_alerts.push_voice_alerts)
        logger.info(
            "scheduler.market_voice_alerts",
            extra={k: result.get(k) for k in ("scanned", "triggered", "pushed", "triggered_categories")},
        )
        _record_scheduler_result("market_voice_alerts", result)
    except Exception as exc:
        logger.exception("scheduler.market_voice_alerts_failed")
        _record_scheduler_run("market_voice_alerts", ok=False, error=str(exc)[:240])


async def job_vkpi_market_listening_daily():
    """市场监听每日采集(迸发⑤开闸刀):Reddit 公开 JSON(免费)+ X Apify actor(封顶),
    帖级实体落 vkpi_market_sources/vkpi_market_mentions,喂 Dashboard「近期市场热词」。

    双闸叠加,默认全关不烧钱:
      1) env 闸:VKPI_FORUM_COLLECT_ENABLED / VKPI_X_COLLECT_ENABLED(缺省/0 →
         collect_* 各自返回 disabled,零网络零写库;两闸独立,可只开 Reddit);
      2) config-gate(scheduler_tasks.vkpi_market_listening):注册表无此行/未开 → 不跑。
    成本口径(2026-07-16 实测结算):Reddit 免费(公开 JSON 限速 >= 2 秒/请求;
    IP 被挡时经 VKPI_FORUM_APIFY_FALLBACK_ENABLED 显式放行单 run 批量兜底,
    计时计费 600s 封顶 ≈ $0.26/run);X 走 call_apify_actor(provider:apify
    预算预检 + 记账),单轮 maxItems=60(实测 $2.56/1k → ≈$0.15/天 ≈ $4.6/月)。
    合计月成本 ≈ $4.6(免费路通)~ $12(兜底常开),贴着 ~$10/月 授权跑。
    零触 viltrox_fit_score / rule_v0;落表幂等(platform+source_ref 去重)。

    开启方式(运营显式执行 SQL,或 Ops 设置页开关):
      INSERT INTO scheduler_tasks (task_key, label, enabled, risk_level)
      VALUES ('vkpi_market_listening', '市场监听每日采集(Reddit 免费 + X Apify 封顶)', TRUE, 'low')
      ON CONFLICT (task_key) DO UPDATE SET enabled = TRUE;
    关闸:UPDATE scheduler_tasks SET enabled = FALSE WHERE task_key = 'vkpi_market_listening';
    """
    if not _scheduler_task_enabled("vkpi_market_listening"):
        return
    try:
        import asyncio

        from app.domains.comments import market_listening

        forum = await asyncio.to_thread(market_listening.collect_forum_listening)
        # X 单轮 60 条:实测结算 $2.56/1k,60 条 ≈ $0.15/天,守住月授权(见 docstring 成本口径)
        x = await asyncio.to_thread(lambda: market_listening.collect_x_listening(max_items=60))
        summary_keys = ("status", "fetched", "new_sources", "new_mentions", "skipped_existing", "network_calls", "reason")
        result = {
            "forum": {k: forum.get(k) for k in summary_keys if forum.get(k) is not None},
            "x": {k: x.get(k) for k in summary_keys if x.get(k) is not None},
        }
        logger.info("scheduler.vkpi_market_listening", extra={"result": result})
        failed = [
            f"{name}={part.get('status')}:{str(part.get('reason') or '')[:80]}"
            for name, part in (("forum", forum), ("x", x))
            if str(part.get("status") or "") in {"error", "blocked"}
        ]
        _record_scheduler_run(
            "vkpi_market_listening",
            ok=not failed,
            error="; ".join(failed)[:240],
        )
    except Exception as exc:
        logger.exception("scheduler.vkpi_market_listening_failed")
        _record_scheduler_run("vkpi_market_listening", ok=False, error=str(exc)[:240])


async def job_sentiment_annotate():
    """V0g 评论情绪批注(打包 LLM):sentiment_id IS NULL 的 vkpi_comments 每轮批注 ≤200 条
    (env VKPI_SENTIMENT_ANNOTATE_MAX_PER_RUN 可调),一次调用打包 40 条省 token,
    写 vkpi_sentiment_results + 回填 sentiment_id。走 llm_gateway(预算闸+代理+台账);
    gateway 被闸(fallback_to_rule)→ 立即停跑不落库。零触 viltrox_fit_score / rule_v0。

    config-gate:scheduler_tasks.vkpi_sentiment_annotate —— **默认关**(注册表无此行 → 不跑)。
    开启方式(运营显式执行 SQL,或 Ops 设置页开关):
      INSERT INTO scheduler_tasks (task_key, label, enabled, risk_level)
      VALUES ('vkpi_sentiment_annotate', '评论情绪批注(LLM 打包)', TRUE, 'high')
      ON CONFLICT (task_key) DO UPDATE SET enabled = TRUE;
    关闸:UPDATE scheduler_tasks SET enabled = FALSE WHERE task_key = 'vkpi_sentiment_annotate';

    成本口径(模型走 model_registry.TASK_MODEL_BINDING['vkpi_sentiment_annotate'] 注册表绑定,
    单价以 model_pricing 目录为准;全量 12,703 条 pending、pack=40 → ceil(12703/40)=318 次调用;
    input ≈ 318×(~180 头部 + 40×~50 评论) ≈ 0.69M tok,output ≈ 318×(40×40+60) ≈ 0.53M tok):
      - 按绑定模型现价换算即得全量成本(flash 档约数美元量级)
    单 run 硬上限 200 条 = 5 次调用,成本可忽略;每日一跑清完全量 ≈ 64 天,或临时调高 env 一把梭。
    """
    if not _scheduler_task_enabled("vkpi_sentiment_annotate"):
        return
    try:
        import asyncio

        from app.domains.market import sentiment_annotate

        result = await asyncio.to_thread(
            sentiment_annotate.annotate_batch,
            sentiment_annotate.run_hard_cap(),
            dry_run=False,
        )
        logger.info(
            "scheduler.vkpi_sentiment_annotate",
            extra={
                "result": {
                    k: result.get(k)
                    for k in ("selected", "annotated", "linked_from_cache", "skipped_unparsed", "halted_reason")
                }
            },
        )
        halted = str(result.get("halted_reason") or "")
        _record_scheduler_run("vkpi_sentiment_annotate", ok=not halted, error=halted[:240])
    except Exception as exc:
        logger.exception("scheduler.vkpi_sentiment_annotate_failed")
        _record_scheduler_run("vkpi_sentiment_annotate", ok=False, error=str(exc)[:240])


async def job_market_mention_sentiment():
    """mentions 情感批注(打包 LLM,2026-07-19 挂账刀③):vkpi_market_mentions.sentiment
    空的行每轮批注 ≤200 条,直写自带 sentiment 列(score/aspects 进 metadata_json)。
    与 job_sentiment_annotate 同一套打包管线/停跑纪律,共用模型绑定,独立 cost_scope。

    config-gate:scheduler_tasks.vkpi_market_mention_sentiment —— **默认关**。开启:
      INSERT INTO scheduler_tasks (task_key, label, enabled, risk_level)
      VALUES ('vkpi_market_mention_sentiment', 'mentions 情感批注(LLM 打包)', TRUE, 'high')
      ON CONFLICT (task_key) DO UPDATE SET enabled = TRUE;
    成本:存量 267 行 pack=40 ≈ 7 次调用,gemini-flash 全量 <$0.01;单 run 上限 200。
    """
    if not _scheduler_task_enabled("vkpi_market_mention_sentiment"):
        return
    try:
        import asyncio

        from app.domains.market import mention_sentiment_annotate

        result = await asyncio.to_thread(
            mention_sentiment_annotate.annotate_mentions_batch,
            mention_sentiment_annotate.run_hard_cap(),
            dry_run=False,
        )
        logger.info(
            "scheduler.vkpi_market_mention_sentiment",
            extra={
                "result": {
                    k: result.get(k)
                    for k in ("selected", "annotated", "skipped_unparsed", "halted_reason")
                }
            },
        )
        halted = str(result.get("halted_reason") or "")
        _record_scheduler_run("vkpi_market_mention_sentiment", ok=not halted, error=halted[:240])
    except Exception as exc:
        logger.exception("scheduler.vkpi_market_mention_sentiment_failed")
        _record_scheduler_run("vkpi_market_mention_sentiment", ok=False, error=str(exc)[:240])


async def job_vkpi_official_visual_scan():
    """官号视频画质分析(增量):每轮跑少量未分析的官号视频(Gemini final_v1 → content_quality_score),
    fit-safe 落 vkpi_official_post_visual,不进 kol_pool。config-gate(scheduler_tasks.vkpi_official_visual_scan);
    走预算闸 cron:official_visual。每轮限量防超时/控成本,幂等可续。"""
    from .jobs_tasks import _scheduler_task_enabled

    if not _scheduler_task_enabled("vkpi_official_visual_scan"):
        return
    try:
        from .jobs_tasks import _enqueue_provider_job

        task_id = await _enqueue_provider_job(
            "official_visual_scan",
            {"max_total": 4, "requested_by": "scheduler"},
            lock_key="official_visual_scan:scheduled",
            timeout_seconds=3600,
        )
        logger.info("scheduler.vkpi_official_visual_scan_queued", extra={"job_id": task_id})
    except Exception:
        logger.exception("scheduler.vkpi_official_visual_scan_failed")
