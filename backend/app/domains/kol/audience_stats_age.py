"""受众画像 · 年龄/性别三路融合(从 audience_stats 拆出,行为不变)。

A=Gemini 批推(llm_gateway,预算记账+代理)> B=M3(可选依赖)> C=频道注册年龄弱先验
> D=用户名生日年 > E=头像视觉(audience_avatar_llm);按 conf 加权投票融合,写回 vkpi_commenter_profiles。
monkeypatch 兼容:对 audience_stats.load_avatar_gemini / download_avatar / _age_llm_batches /
_age_avatar_batch / _age_m3_batch 的补丁经 _live() 生效。
红线:rule_v0 兜底文本不当真;绝不写 viltrox_fit_score。
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.domains.kol.audience_stats_age_ensemble import apply_age_signals

from app.core.logging import get_logger
from app.domains.kol.audience_avatar_llm import (  # noqa: F401 — download_avatar/load_avatar_gemini 经 _live() 解析
    avatar_model,
    classify_avatar_batch,
    download_avatar,
    load_avatar_gemini,
)

logger = get_logger(__name__)

AGE_MIN_DETERMINED = 5  # 年龄分布最小判定人数:低于此不出 bins(防 1 人外推 100%)
AGE_BUCKETS = ("0-18", "19-29", "30-39", "40+")
AGE_LLM_BATCH_SIZE = 50   # A 路(Gemini)每次调用批 50 评论者
AGE_LLM_MAX_BATCHES = 4   # 单次刷新 A 路调用上限(成本闸;其余人走 C 路弱先验,下次刷新继续补)
AGE_LLM_DEADLINE_SECONDS = 40.0

_NAME_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _first_name(display_name: str) -> str:
    """展示名/handle -> 首个字母 token(小写,去数字/emoji/分隔符)。取不到返回空串。"""
    text = str(display_name or "").strip().lstrip("@")
    if not text:
        return ""
    # handle 风格 sweetheart_forever35 -> 按 _ . - 切开取首段字母
    for sep in ("_", ".", "-", " "):
        text = text.replace(sep, " ")
    match = _NAME_TOKEN_RE.search(text)
    return match.group(0).lower() if match else ""




def _live(name: str) -> Any:
    """经门面解析协作函数:tests 在 app.domains.kol.audience_stats 上 monkeypatch 的同名符号仍生效。"""
    facade = sys.modules.get("app.domains.kol.audience_stats")
    target = getattr(facade, name, None) if facade is not None else None
    return target if target is not None else globals()[name]


# ── 年龄三路融合(v2)──

_AGE_ALIAS = {
    "0-18": "0-18", "under 18": "0-18", "13-18": "0-18", "<18": "0-18", "<=18": "0-18",
    "19-29": "19-29", "20-29": "19-29", "18-29": "19-29",
    "30-39": "30-39",
    "40+": "40+", "40-49": "40+", "50+": "40+", ">=40": "40+", ">40": "40+",
}


def _normalize_age_bucket(value: Any) -> str:
    return _AGE_ALIAS.get(str(value or "").strip().lower(), "")


def _age_from_channel_created(created_at: Any) -> tuple[str, float]:
    """C 路:注册年龄弱先验(conf .3)。YouTube 开户下限 13 岁 → 最小年龄 = 13 + 账号年龄。

    只有老账号有信息量(新账号不代表年轻人):账号不满 8 年不出信号;
    8 年以上按年龄下界落桶(如 2012 年注册 -> 下界约 27 -> '19-29'/'30-39' 权重上移)。
    """
    text = str(created_at or "").strip()
    if not text:
        return "", 0.0
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return "", 0.0
    years = (datetime.now(timezone.utc) - dt).days / 365.25
    if years < 8:
        return "", 0.0
    min_age = 13 + years
    if min_age >= 40:
        return "40+", 0.3
    if min_age >= 30:
        return "30-39", 0.3
    return "19-29", 0.3


_HANDLE_YEAR_RE = re.compile(r"(?<!\d)(19[5-9]\d|20[01]\d)(?!\d)")


def _age_from_handle(*texts: Any) -> tuple[str, float]:
    """D 路:用户名/显示名里的生日年启发(jake2008 → ~18)。conf 0.4 只作一票,
    数字可能不是生日(型号/纪念年),靠融合层与其它信号互证;不在 12-75 岁范围直接丢弃。"""
    for text in texts:
        m = _HANDLE_YEAR_RE.search(str(text or ""))
        if not m:
            continue
        age = datetime.now(timezone.utc).year - int(m.group(1))
        if not (12 <= age <= 75):
            continue
        if age <= 18:
            return "0-18", 0.4
        if age <= 29:
            return "19-29", 0.4
        if age <= 39:
            return "30-39", 0.4
        return "40+", 0.4
    return "", 0.0


def _fuse_age(signals: list[tuple[str, float]]) -> tuple[str, float]:
    """按 conf 加权投票融合多路年龄信号。

    赢家桶内多信号 noisy-or 合并(相互印证抬置信),再乘赢家得分份额(有分歧降置信)。
    单信号 -> 原 conf;无有效信号 -> ('', 0)。"""
    votes: dict[str, list[float]] = {}
    for bucket, conf in signals or []:
        normalized = _normalize_age_bucket(bucket)
        if normalized and float(conf or 0) > 0:
            votes.setdefault(normalized, []).append(min(0.95, float(conf)))
    if not votes:
        return "", 0.0
    scores = {b: sum(cs) for b, cs in votes.items()}
    winner = max(scores.items(), key=lambda kv: (kv[1], kv[0]))[0]
    total = sum(scores.values())
    disagreement_share = scores[winner] / total if total else 0.0
    agree_miss = 1.0
    for conf in votes[winner]:
        agree_miss *= 1 - conf
    fused = (1 - agree_miss) * disagreement_share
    return winner, round(min(0.9, fused), 2)


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    """LLM 回复里抠 JSON 数组(容忍代码围栏/前后缀文本/被 max_tokens 截断的残缺数组)。

    截断救援:整体 parse 失败时,逐个抢救完整的 {...} 对象(thinking 型模型的思考 token
    会吃掉 maxOutputTokens,尾部截断是常态 —— 抢救到多少算多少,绝不编造)。
    抠不到返回 []。"""
    raw = str(text or "").strip()
    if "```" in raw:
        raw = raw.replace("```json", "```")
        parts = raw.split("```")
        raw = max(parts, key=len)
    start = raw.find("[")
    if start < 0:
        return []
    end = raw.rfind("]")
    if end > start:
        try:
            data = json.loads(raw[start : end + 1])
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        except Exception:
            logger.debug("JSON 数组整体解析失败,退逐对象抠取(best-effort)", exc_info=True)
    # 救援:截断/夹杂散文时逐对象抠(对象内不嵌套,够用)。
    out: list[dict[str, Any]] = []
    for match in re.finditer(r"\{[^{}]*\}", raw[start:]):
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


def _validate_age_llm_batch(value: Any, expected_count: int) -> tuple[bool, str]:
    """Reject partial or malformed model output before it affects aggregate estimates."""
    if not isinstance(value, list):
        return False, "top-level result must be an array"
    if len(value) != expected_count:
        return False, f"expected {expected_count} items, got {len(value)}"
    seen: set[int] = set()
    for item in value:
        if not isinstance(item, dict):
            return False, "every result item must be an object"
        try:
            index = int(item.get("i"))
        except (TypeError, ValueError):
            return False, "result item i must be an integer"
        if not 1 <= index <= expected_count or index in seen:
            return False, "result item i is missing, duplicated, or out of range"
        seen.add(index)
        age = str(item.get("age") or "").strip()
        if age and not _normalize_age_bucket(age):
            return False, f"unsupported age bucket for item {index}"
        if str(item.get("gender") or "").strip().lower() not in ("", "male", "female"):
            return False, f"unsupported gender for item {index}"
        try:
            confidence = float(item.get("conf") or 0.0)
        except (TypeError, ValueError):
            return False, f"invalid confidence for item {index}"
        if not 0.0 <= confidence <= 1.0:
            return False, f"confidence out of range for item {index}"
    return True, ""


def _age_llm_failure_reason(response: dict[str, Any]) -> str:
    statuses = {
        str(item.get("status") or "")
        for item in list(response.get("errors") or [])
        if isinstance(item, dict)
    }
    if "deadline_exceeded" in statuses or str(response.get("reason") or "") == "deadline_exceeded":
        return "provider_timeout"
    if "parse_failure" in statuses or "validation_failure" in statuses:
        return "invalid_json"
    if "empty_response" in statuses:
        return "empty_response"
    return "provider_unavailable"


def _age_llm_batches(
    commenters: list[dict[str, Any]],
    *,
    max_batches: int = AGE_LLM_MAX_BATCHES,
    batch_size: int = AGE_LLM_BATCH_SIZE,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """A 路:Gemini 批推年龄/性别(llm_gateway:预算记账 + 代理 + 结果落 ledger)。

    输入 显示名+bio+评论原文片段(2026-07-02:评论用语/话题/表情是比名字强得多的年龄信号,
    此前只喂名字+bio 导致判定率极低 → 1 人外推 100% 的笑话);头像视觉版留 P2。
    批 50 人/调用,单次刷新最多 max_batches 次。
    红线:rule_v0 兜底文本不当真(model=rule_v0 一律丢弃);失败/超闸跳过不阻断主流程。
    返回 (author_key -> {age_bucket, gender, conf}, stats)。
    """
    if max_batches <= 0 or not commenters:
        return {}, {"status": "skipped", "calls": 0, "batches_ok": 0, "people_in": 0, "people_out": 0}
    try:
        from app.core.model_registry import current_task_model_binding, split_binding
        from app.platform.llm_production import generate_json
    except Exception as exc:
        return {}, {
            "status": "failed",
            "reason": "gateway_unavailable",
            "error_type": type(exc).__name__,
            "calls": 0,
            "batches_ok": 0,
            "people_in": min(len(commenters), max_batches * batch_size),
            "people_out": 0,
        }
    out: dict[str, dict[str, Any]] = {}
    calls = 0
    batches_ok = 0
    people_in = 0
    failures: Counter = Counter()
    batches = [commenters[i : i + batch_size] for i in range(0, len(commenters), batch_size)][:max_batches]
    for batch in batches:
        people_in += len(batch)
        lines = []
        for idx, c in enumerate(batch):
            name = str(c.get("display_name") or "").replace('"', "'")[:60]
            bio = str(c.get("bio") or "").replace("\n", " ").replace('"', "'")[:160]
            comment = str(c.get("comment_text") or "").replace("\n", " ").replace('"', "'")[:150]
            lines.append(f'{idx + 1}. name="{name}" bio="{bio}" comment="{comment}"')
        prompt = (
            "Task: AGGREGATE audience statistics for a marketing dashboard. Below are PUBLIC display names, "
            "public bios and one public comment of anonymous social media commenters. Results are only used "
            "as aggregate percentages (age buckets, gender split); nothing is attributed to any individual.\n"
            "For EACH numbered entry, classify from TEXT STYLE ONLY — name style, emoji, slang vs formal "
            "wording, topics referenced in the comment, stated roles like dad / retired / student / engineer. "
            "Comment language style (teen slang, professional jargon, dated phrasing) is the strongest cue:\n"
            '  "i": entry number, "age": "0-18"|"19-29"|"30-39"|"40+" or "" when no signal,\n'
            '  "gender": "male"|"female" or "" when no signal, "conf": 0.0-1.0.\n'
            "AGE: when there is ANY weak cue (slang vs formal tone, emoji habits, life-stage hints, topics, "
            "name style) give your best-supported bucket with a LOW conf (0.25-0.4) instead of empty — "
            "empty only when truly nothing. Adults discussing pro gear/work are usually 19-29 or 30-39, "
            "not 0-18. GENDER: stay conservative, empty beats a guess. Output STRICTLY one JSON array, "
            "no prose, no markdown fences. Your reply must start with the character [\n\n"
            + "\n".join(lines)
        )
        calls += 1
        try:
            provider, model = split_binding(
                current_task_model_binding().get("kol_audience_analysis") or ""
            )
            raw_response = generate_json(
                prompt,
                provider=provider,
                model=model,
                purpose="vkpi_audience_age_v1",
                max_output_tokens=4000,
                cost_tag="audience_stats",
                validator=lambda value, expected=len(batch): _validate_age_llm_batch(value, expected),
                deadline_seconds=AGE_LLM_DEADLINE_SECONDS,
                metadata={
                    "aggregate_only": True,
                    "batch_size": len(batch),
                    "task_binding": "kol_audience_analysis",
                },
            )
        except Exception as exc:  # noqa: BLE001 - an age estimate must never fail the refresh
            logger.warning("audience age llm batch raised: %s", str(exc)[:150])
            failures["provider_exception"] += 1
            continue
        if not isinstance(raw_response, dict):
            logger.warning("audience age llm batch returned a non-object response")
            failures["invalid_gateway_response"] += 1
            continue
        resp = raw_response
        parsed = resp.get("json") if isinstance(resp, dict) else None
        valid, validation_error = _validate_age_llm_batch(parsed, len(batch))
        if str(resp.get("status") or "") != "success" or not valid:
            reason = _age_llm_failure_reason(resp) if isinstance(resp, dict) else "provider_unavailable"
            if not valid and validation_error and reason == "provider_unavailable":
                reason = "invalid_json"
            failures[reason] += 1
            logger.warning(
                "audience age llm batch unusable: provider=%s status=%s reason=%s",
                resp.get("provider"),
                resp.get("status"),
                reason,
            )
            continue
        batches_ok += 1
        for item in parsed:
            try:
                idx = int(item.get("i")) - 1
            except (TypeError, ValueError):
                continue
            if not (0 <= idx < len(batch)):
                continue
            key = str(batch[idx].get("author_key") or "")
            bucket = _normalize_age_bucket(item.get("age"))
            gender = str(item.get("gender") or "").strip().lower()
            try:
                conf = max(0.0, min(1.0, float(item.get("conf") or 0.0)))
            except (TypeError, ValueError):
                conf = 0.0
            if key and (bucket or gender in ("male", "female")) and conf > 0:
                out[key] = {"age_bucket": bucket, "gender": gender if gender in ("male", "female") else "", "conf": round(conf, 2)}
    status = "ok" if batches_ok == calls else ("partial" if batches_ok else "failed")
    failure_reason = failures.most_common(1)[0][0] if failures else ""
    return out, {
        "status": status,
        "reason": failure_reason,
        "calls": calls,
        "batches_ok": batches_ok,
        "people_in": people_in,
        "people_out": len(out),
        "failure_counts": dict(failures),
    }


AGE_AVATAR_MAX_IMAGES = 120  # E 路(头像视觉)单次刷新最多看多少张(小缩略图,Gemini 视觉便宜但设上限)
AGE_AVATAR_BATCH = 30        # 多图单调用批量


def _age_avatar_batch(commenters: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """E 路:头像视觉判龄/性别(Gemini 多图单调用;Modash 系年龄判定率高的主武器)。

    只看仍无年龄、且带头像 URL 的评论者;下载缩略图(几 KB)内联进一次 generate_content。
    结果只进聚合统计,不归因个人(与 A 路同口径);默认剪影/加载失败逐个跳过。
    """
    need = [
        c for c in commenters
        if not c.get("age_bucket") and str(c.get("avatar_url") or "").startswith("http")
    ][:AGE_AVATAR_MAX_IMAGES]
    if not need:
        return {}, {"status": "no_avatars", "calls": 0, "people_in": 0, "people_out": 0, "download_failed": 0}
    client, genai_types, status = _live("load_avatar_gemini")()
    if status:
        return {}, {"status": status, "calls": 0, "people_in": 0, "people_out": 0, "download_failed": 0}
    model = avatar_model()
    out: dict[str, dict[str, Any]] = {}
    calls = 0
    people_in = 0
    fetched = 0
    download_failed = 0
    for start in range(0, len(need), AGE_AVATAR_BATCH):
        batch = need[start : start + AGE_AVATAR_BATCH]
        images: list[tuple[bytes, str]] = []
        keys: list[str] = []
        for c in batch:
            data, mime = _live("download_avatar")(str(c["avatar_url"]))
            if not data:
                download_failed += 1
                continue
            if len(data) < 300:
                continue  # 空图/默认剪影占位常见极小,跳过
            images.append((data, mime))
            keys.append(str(c.get("author_key") or ""))
            fetched += 1
        if not keys:
            continue
        people_in += len(keys)
        try:
            text = classify_avatar_batch(images, client=client, genai_types=genai_types, model=model)
            calls += 1
        except Exception as exc:
            logger.warning("audience avatar batch failed: %s", str(exc)[:150])
            continue
        for item in _extract_json_array(text):
            try:
                idx = int(item.get("i")) - 1
            except (TypeError, ValueError):
                continue
            if not (0 <= idx < len(keys)):
                continue
            bucket = _normalize_age_bucket(item.get("age"))
            gender = str(item.get("gender") or "").strip().lower()
            try:
                conf = max(0.0, min(0.6, float(item.get("conf") or 0.0)))  # 视觉判龄封顶 .6
            except (TypeError, ValueError):
                conf = 0.0
            if keys[idx] and (bucket or gender in ("male", "female")) and conf > 0:
                out[keys[idx]] = {
                    "age_bucket": bucket,
                    "gender": gender if gender in ("male", "female") else "",
                    "conf": round(conf, 2),
                }
    return out, {"status": "ok" if calls else "no_images", "calls": calls, "people_in": people_in,
                 "images_fetched": fetched, "download_failed": download_failed, "people_out": len(out)}


def _m3_available() -> bool:
    try:
        import m3inference  # noqa: F401

        return True
    except Exception:
        return False


def _age_m3_batch(commenters: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], str]:
    """B 路:M3(可选依赖,文本模式)。未安装诚实返回 unavailable,绝不强装。

    安装法(重依赖含 torch,默认不装):.venv/bin/pip install m3inference
    """
    if not _m3_available():
        return {}, "unavailable"
    try:
        from m3inference import M3Inference  # type: ignore

        m3 = M3Inference(use_full_model=False, use_cuda=False, parallel=False)
        docs = [
            {
                "id": str(c.get("author_key") or ""),
                "name": str(c.get("display_name") or "")[:60],
                "screen_name": _first_name(str(c.get("display_name") or "")) or "user",
                "description": str(c.get("bio") or "")[:200],
                "lang": str(c.get("language") or "un") or "un",
            }
            for c in commenters
            if c.get("author_key")
        ]
        preds = m3.infer(docs) or {}
        bucket_map = {"<=18": "0-18", "19-29": "19-29", "30-39": "30-39", ">=40": "40+"}
        out: dict[str, dict[str, Any]] = {}
        for key, pred in preds.items():
            age = (pred or {}).get("age") or {}
            if not age:
                continue
            top_label, top_p = max(age.items(), key=lambda kv: kv[1])
            bucket = bucket_map.get(str(top_label), "")
            if bucket:
                out[str(key)] = {"age_bucket": bucket, "conf": round(float(top_p), 2)}
        return out, "ok"
    except Exception as exc:
        logger.warning("audience_stats m3 batch failed: %s", exc)
        return {}, f"error: {exc}"[:120]


def _update_age_cache(conn: Any, rows: list[dict[str, Any]]) -> int:
    """把融合后的 age/gender 写回身份缓存(行已由 upsert 保证存在)。"""
    now = _utcnow_iso()
    written = 0
    for rec in rows:
        if not rec.get("platform") or not rec.get("author_key"):
            continue
        age_conf = rec.get("age_conf")
        conn.execute(
            "UPDATE vkpi_commenter_profiles SET age_bucket=?, age_conf=?, gender=?, gender_conf=?, updated_at=? "
            "WHERE platform=? AND author_key=?",
            (
                rec.get("age_bucket") or "",
                float(age_conf) if age_conf is not None else None,
                rec.get("gender") or "",
                float(rec.get("gender_conf") or 0.0),
                now,
                rec["platform"], rec["author_key"],
            ),
        )
        written += 1
    return written


def _age_ensemble(
    conn: Any,
    platform: str,
    profiles: list[dict[str, Any]],
    *,
    llm_max_batches: int = AGE_LLM_MAX_BATCHES,
    allow_avatar_provider: bool = True,
) -> dict[str, Any]:
    """ABC 三路年龄融合,就地更新 profiles 的 age_bucket/age_conf(必要时 gender),写回缓存。

    A=Gemini(只喂尚无缓存年龄、且有名字或 bio 的人;成本闸 llm_max_batches);
    B=M3(装了就用,没装 coverage 标 unavailable);C=频道注册年龄弱先验。
    gender:LLM 输出仅在新 conf 高于现有 gender_conf 时覆盖(人名表 .8 通常保留)。
    """
    # 评论文本也算可推断输入(2026-07-02):很多评论者无 bio,但评论用语本身就是年龄信号。
    need = [
        p for p in profiles
        if not p.get("age_bucket") and (p.get("display_name") or p.get("bio") or p.get("comment_text"))
    ]
    llm_pred, llm_stats = _live("_age_llm_batches")(need, max_batches=llm_max_batches)
    m3_pred, m3_status = _live("_age_m3_batch")(need)
    # E 路:A/B 跑完仍无年龄的人再看头像(视觉最贵放最后,只补漏)。
    still_need = [p for p in need if not llm_pred.get(str(p.get("author_key") or ""), {}).get("age_bucket")
                  and not m3_pred.get(str(p.get("author_key") or ""), {}).get("age_bucket")]
    # The legacy avatar path calls the Gemini SDK directly and therefore lacks
    # the shared budget ledger used by generate_json.  Keep it disabled for
    # provider-fenced MY KOL jobs until a multimodal gateway is available.
    if allow_avatar_provider:
        avatar_pred, avatar_stats = _live("_age_avatar_batch")(still_need)
    else:
        avatar_pred, avatar_stats = {}, {
            "status": "disabled_unbudgeted_provider_path",
            "calls": 0,
            "people_in": len(still_need),
            "people_out": 0,
        }
    counts = {"cached": 0, "llm": 0, "m3": 0, "channel": 0, "handle_year": 0, "avatar": 0, "fused": 0}
    updates: list[dict[str, Any]] = []
    for p in profiles:
        if p.get("age_bucket"):
            counts["cached"] += 1
            continue
        if apply_age_signals(
            p,
            llm_predictions=llm_pred,
            m3_predictions=m3_pred,
            avatar_predictions=avatar_pred,
            counts=counts,
            channel_age=_age_from_channel_created,
            handle_age=_age_from_handle,
            fuse_age=_fuse_age,
        ):
            updates.append(p)
    written = _update_age_cache(conn, updates) if updates else 0
    return {"llm": llm_stats, "m3": m3_status, "avatar": avatar_stats, "counts": counts, "cache_written": written}

