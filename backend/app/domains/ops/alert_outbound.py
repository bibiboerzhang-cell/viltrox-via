"""告警出站(webhook)+ 去重/静默窗口 + 升级标记 — 让 vkpi_alerts 不再只写库不出声。

诚实 by design:
  - 出站 URL 只从 env ``VKPI_ALERT_WEBHOOK_URL`` 读;**绝不**写进日志、返回值、vkpi_alerts、
    persistent_cache 或任何仓库文件。所有日志/返回值只带 ``kind`` 与 ``configured`` 布尔。
  - 未配置 URL 时不报错:返回 ``{"sent": False, "reason": "not_configured"}``,调用方照常落库。
  - payload 形状按 ``VKPI_ALERT_WEBHOOK_KIND`` = feishu | slack | generic(缺省 generic)。
    飞书自定义机器人可选签名:``VKPI_ALERT_WEBHOOK_SECRET``(timestamp + HMAC-SHA256,官方算法)。
  - 去重/静默:同 ``key`` 在 ``VKPI_ALERT_DEDUPE_HOURS``(默认 6h)内不重发;fingerprint 变化
    (例如失败项集合变了)视为新情况放行。静默名单 ``VKPI_ALERT_SILENCE_KEYS``(逗号分隔)整体不出站。
  - 升级:同 key 连续触发达 ``VKPI_ALERT_ESCALATE_AFTER``(默认 3)次 → 标 escalated,
    升级那一刻无视静默窗口补发一条;同时把 vkpi_alerts 对应行 metadata_json 打上 escalated。
    clear(key)(告警恢复)把连续计数归零。
  - 状态存 persistent_cache(既有 KV,零新表零迁移),键 ``vkpi:alert_outbound:state:<key>``。

写入面:persistent_cache(自己的 state 键)+ vkpi_alerts.metadata_json(仅 escalated 标记)。
零触 viltrox_fit_score / rule_v0;不执行任何运维动作。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

ENV_WEBHOOK_URL = "VKPI_ALERT_WEBHOOK_URL"
ENV_WEBHOOK_KIND = "VKPI_ALERT_WEBHOOK_KIND"
ENV_WEBHOOK_SECRET = "VKPI_ALERT_WEBHOOK_SECRET"
ENV_WEBHOOK_TIMEOUT_S = "VKPI_ALERT_WEBHOOK_TIMEOUT_S"
ENV_DEDUPE_HOURS = "VKPI_ALERT_DEDUPE_HOURS"
ENV_ESCALATE_AFTER = "VKPI_ALERT_ESCALATE_AFTER"
ENV_SILENCE_KEYS = "VKPI_ALERT_SILENCE_KEYS"
ENV_NOTIFY_RECOVERY = "VKPI_ALERT_NOTIFY_RECOVERY"

KINDS = ("feishu", "slack", "generic")
_DEFAULT_KIND = "generic"
_DEFAULT_DEDUPE_HOURS = 6.0
_DEFAULT_ESCALATE_AFTER = 3
_DEFAULT_TIMEOUT_S = 5.0
_STATE_KEY_PREFIX = "vkpi:alert_outbound:state:"
_STATE_TTL_DAYS = 30
_TEXT_LIMIT = 3500

# 透传签名:transport(payload_dict, timeout_s) -> (http_status:int, reason:str)。
Transport = Callable[[dict[str, Any], float], tuple[int, str]]


# ──────────────────────────────────────────────
# 配置(纯读 env;URL 永不外泄)
# ──────────────────────────────────────────────


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name, "").strip()
        return float(raw) if raw else float(default)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.environ.get(name, "").strip()
        return int(raw) if raw else int(default)
    except (TypeError, ValueError):
        return int(default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _webhook_url() -> str:
    return os.environ.get(ENV_WEBHOOK_URL, "").strip()


def webhook_kind() -> str:
    raw = os.environ.get(ENV_WEBHOOK_KIND, "").strip().lower()
    return raw if raw in KINDS else _DEFAULT_KIND


def dedupe_window() -> timedelta:
    return timedelta(hours=max(0.0, _env_float(ENV_DEDUPE_HOURS, _DEFAULT_DEDUPE_HOURS)))


def escalate_after() -> int:
    return max(1, _env_int(ENV_ESCALATE_AFTER, _DEFAULT_ESCALATE_AFTER))


def silenced_keys() -> frozenset[str]:
    raw = os.environ.get(ENV_SILENCE_KEYS, "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def outbound_status() -> dict[str, Any]:
    """给设置页/哨兵结果用的诚实状态:只有 configured + kind,永不带 URL。"""
    return {
        "configured": bool(_webhook_url()),
        "kind": webhook_kind(),
        "dedupe_hours": dedupe_window().total_seconds() / 3600.0,
        "escalate_after": escalate_after(),
        "signed": bool(os.environ.get(ENV_WEBHOOK_SECRET, "").strip()),
    }


# ──────────────────────────────────────────────
# 小工具
# ──────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _clip(text: Any, limit: int = _TEXT_LIMIT) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _redact(message: str) -> str:
    """异常文本里若夹带 URL/host,整体打码——日志永不泄露出站地址。"""
    url = _webhook_url()
    text = str(message or "")
    if url:
        text = text.replace(url, "<webhook-url>")
        host = re.sub(r"^[a-z]+://", "", url).split("/", 1)[0]
        if host:
            text = text.replace(host, "<webhook-host>")
    return re.sub(r"https?://\S+", "<url>", text)[:300]


_SEVERITY_ICON = {"danger": "🔴", "warning": "🟠", "info": "🔵"}


def _headline(event: dict[str, Any]) -> str:
    icon = _SEVERITY_ICON.get(str(event.get("severity") or "info"), "🔵")
    prefix = "[升级] " if event.get("escalated") else ""
    if event.get("event") == "recovery":
        icon, prefix = "🟢", "[恢复] "
    return f"{icon} {prefix}{event.get('title') or event.get('key') or 'vkpi alert'}"


# ──────────────────────────────────────────────
# payload 形状(纯函数,可测)
# ──────────────────────────────────────────────


def build_payload(kind: str, event: dict[str, Any], *, secret: str = "", now_ts: int | None = None) -> dict[str, Any]:
    """按渠道拼 payload。event 至少含 key/title/body/severity;可含 escalated/consecutive/alert_key/rule_key。"""
    kind = kind if kind in KINDS else _DEFAULT_KIND
    headline = _headline(event)
    body = _clip(event.get("body"))
    meta_line = f"key={event.get('key')} severity={event.get('severity') or 'info'}"
    if event.get("consecutive"):
        meta_line += f" consecutive={int(event.get('consecutive') or 0)}"
    if event.get("alert_key"):
        meta_line += f" alert={event.get('alert_key')}"
    text = f"{headline}\n{body}\n{meta_line}".strip()
    if kind == "feishu":
        payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
        if secret:
            ts = int(now_ts if now_ts is not None else time.time())
            string_to_sign = f"{ts}\n{secret}"
            digest = hmac.new(string_to_sign.encode("utf-8"), b"", digestmod=hashlib.sha256).digest()
            payload["timestamp"] = str(ts)
            payload["sign"] = base64.b64encode(digest).decode("utf-8")
        return payload
    if kind == "slack":
        return {
            "text": headline,
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*{headline}*\n{body}"}},
                {"type": "context", "elements": [{"type": "mrkdwn", "text": meta_line}]},
            ],
        }
    return {
        "source": "vkpi",
        "event": str(event.get("event") or "alert"),
        "key": str(event.get("key") or ""),
        "alert_key": event.get("alert_key"),
        "rule_key": event.get("rule_key"),
        "severity": str(event.get("severity") or "info"),
        "title": str(event.get("title") or ""),
        "body": body,
        "escalated": bool(event.get("escalated")),
        "consecutive": int(event.get("consecutive") or 0),
        "sent_at": _iso(_utcnow()),
    }


# ──────────────────────────────────────────────
# 传输(默认 urllib;测试注入 fake)
# ──────────────────────────────────────────────


def _http_transport(payload: dict[str, Any], timeout_s: float) -> tuple[int, str]:
    url = _webhook_url()
    data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    req = urllib_request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    with urllib_request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - URL 仅来自 env
        return int(getattr(resp, "status", 200) or 200), "ok"


def _deliver(payload: dict[str, Any], transport: Transport | None) -> dict[str, Any]:
    timeout_s = max(1.0, _env_float(ENV_WEBHOOK_TIMEOUT_S, _DEFAULT_TIMEOUT_S))
    send = transport or _http_transport
    try:
        status, reason = send(payload, timeout_s)
    except urllib_error.HTTPError as exc:
        logger.warning("alert_outbound: webhook http error status=%s", getattr(exc, "code", "?"))
        return {"sent": False, "reason": "http_error", "status": int(getattr(exc, "code", 0) or 0)}
    except Exception as exc:
        logger.warning("alert_outbound: webhook delivery failed %s: %s", type(exc).__name__, _redact(str(exc)))
        return {"sent": False, "reason": "delivery_error", "error": type(exc).__name__}
    if 200 <= int(status) < 300:
        return {"sent": True, "reason": "sent", "status": int(status)}
    logger.warning("alert_outbound: webhook non-2xx status=%s reason=%s", status, _redact(reason))
    return {"sent": False, "reason": "http_error", "status": int(status)}


# ──────────────────────────────────────────────
# 去重状态(persistent_cache)
# ──────────────────────────────────────────────


def _state_key(key: str) -> str:
    return _STATE_KEY_PREFIX + str(key or "").strip()


def load_state(key: str) -> dict[str, Any]:
    if not table_exists("persistent_cache"):
        return {}
    try:
        row = get_conn().execute(
            "SELECT value_json FROM persistent_cache WHERE cache_key=?", (_state_key(key),)
        ).fetchone()
    except Exception:
        logger.warning("alert_outbound: state read failed", exc_info=True)
        return {}
    if not row:
        return {}
    try:
        payload = json.loads(dict(row).get("value_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_state(key: str, state: dict[str, Any]) -> bool:
    if not table_exists("persistent_cache"):
        logger.warning("alert_outbound: persistent_cache missing, dedupe state not persisted")
        return False
    now = _utcnow()
    try:
        conn = get_conn()
        conn.execute("DELETE FROM persistent_cache WHERE cache_key=?", (_state_key(key),))
        conn.execute(
            "INSERT INTO persistent_cache (cache_key, value_json, expires_at, created_at) VALUES (?,?,?,?)",
            (
                _state_key(key),
                json.dumps(state, ensure_ascii=False, default=str),
                _iso(now + timedelta(days=_STATE_TTL_DAYS)),
                _iso(now),
            ),
        )
        conn.commit()
        return True
    except Exception:
        logger.warning("alert_outbound: state write failed", exc_info=True)
        return False


def clear_state(key: str) -> bool:
    if not table_exists("persistent_cache"):
        return False
    try:
        conn = get_conn()
        conn.execute("DELETE FROM persistent_cache WHERE cache_key=?", (_state_key(key),))
        conn.commit()
        return True
    except Exception:
        logger.warning("alert_outbound: state clear failed", exc_info=True)
        return False


def _mark_alert_escalated(alert_key: str, consecutive: int) -> bool:
    """vkpi_alerts 对应行 metadata_json 打 escalated 标(只改元数据,不改状态/严重度)。"""
    if not alert_key or not table_exists("vkpi_alerts"):
        return False
    try:
        conn = get_conn()
        row = conn.execute("SELECT metadata_json FROM vkpi_alerts WHERE alert_key=?", (alert_key,)).fetchone()
        if not row:
            return False
        try:
            meta = json.loads(dict(row).get("metadata_json") or "{}")
        except (TypeError, ValueError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        now = _iso(_utcnow())
        meta.update({"escalated": True, "escalated_at": meta.get("escalated_at") or now, "consecutive": int(consecutive)})
        conn.execute(
            "UPDATE vkpi_alerts SET metadata_json=?, updated_at=? WHERE alert_key=?",
            (json.dumps(meta, ensure_ascii=False, default=str), now, alert_key),
        )
        conn.commit()
        return True
    except Exception:
        logger.warning("alert_outbound: escalated mark failed", exc_info=True)
        return False


# ──────────────────────────────────────────────
# 对外入口
# ──────────────────────────────────────────────


def notify(
    *,
    key: str,
    title: str,
    body: str = "",
    severity: str = "warning",
    alert_key: str | None = None,
    rule_key: str | None = None,
    fingerprint: str | None = None,
    escalate: bool = True,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """同 key 的一次「告警仍在」触发:计数 → 升级判定 → 去重窗口 → 出站。

    返回永不含 URL:{sent, reason, configured, kind, consecutive, escalated, escalated_now}。
    reason ∈ not_configured | silenced | deduped | sent | http_error | delivery_error。
    """
    key = str(key or "").strip()
    now = _utcnow()
    state = load_state(key)
    consecutive = int(state.get("consecutive") or 0) + 1
    threshold = escalate_after() if escalate else 0
    escalated_now = bool(threshold) and consecutive == threshold
    escalated = bool(threshold) and (bool(state.get("escalated")) or consecutive >= threshold)
    base = {"configured": bool(_webhook_url()), "kind": webhook_kind(), "consecutive": consecutive,
            "escalated": escalated, "escalated_now": escalated_now, "key": key}
    if escalated_now and alert_key:
        _mark_alert_escalated(alert_key, consecutive)

    new_state = {
        **state,
        "consecutive": consecutive,
        "escalated": escalated,
        "last_seen_at": _iso(now),
        "last_fingerprint": fingerprint if fingerprint is not None else state.get("last_fingerprint"),
        "alert_key": alert_key or state.get("alert_key"),
    }
    if not base["configured"]:
        save_state(key, new_state)
        return {**base, "sent": False, "reason": "not_configured"}
    if key in silenced_keys():
        save_state(key, new_state)
        return {**base, "sent": False, "reason": "silenced"}

    last_sent = _parse_dt(state.get("last_sent_at"))
    inside_window = last_sent is not None and (now - last_sent) < dedupe_window()
    same_fingerprint = fingerprint is None or fingerprint == state.get("last_fingerprint")
    if inside_window and same_fingerprint and not escalated_now:
        save_state(key, new_state)
        return {**base, "sent": False, "reason": "deduped", "last_sent_at": state.get("last_sent_at")}

    event = {"event": "alert", "key": key, "alert_key": alert_key, "rule_key": rule_key, "severity": severity,
             "title": title, "body": body, "escalated": escalated, "consecutive": consecutive}
    payload = build_payload(webhook_kind(), event, secret=os.environ.get(ENV_WEBHOOK_SECRET, "").strip())
    delivered = _deliver(payload, transport)
    if delivered.get("sent"):
        new_state["last_sent_at"] = _iso(now)
        new_state["sent_count"] = int(state.get("sent_count") or 0) + 1
    save_state(key, new_state)
    logger.info("alert_outbound notify key=%s kind=%s sent=%s reason=%s consecutive=%s escalated=%s",
                key, base["kind"], delivered.get("sent"), delivered.get("reason"), consecutive, escalated)
    return {**base, **delivered}


def clear(*, key: str, title: str = "", body: str = "", transport: Transport | None = None) -> dict[str, Any]:
    """告警恢复:连续计数归零;此前出站过且 VKPI_ALERT_NOTIFY_RECOVERY(默认开)→ 补发一条恢复。"""
    key = str(key or "").strip()
    state = load_state(key)
    had_sent = bool(state.get("last_sent_at"))
    result: dict[str, Any] = {"key": key, "cleared": bool(state), "configured": bool(_webhook_url()),
                              "kind": webhook_kind(), "sent": False, "reason": "no_prior_send"}
    if had_sent and _webhook_url() and _env_bool(ENV_NOTIFY_RECOVERY, True) and key not in silenced_keys():
        event = {"event": "recovery", "key": key, "alert_key": state.get("alert_key"), "severity": "info",
                 "title": title or f"{key} 已恢复", "body": body, "consecutive": 0}
        payload = build_payload(webhook_kind(), event, secret=os.environ.get(ENV_WEBHOOK_SECRET, "").strip())
        result.update(_deliver(payload, transport))
    elif not _webhook_url():
        result["reason"] = "not_configured"
    clear_state(key)
    return result


def send_digest(*, markdown: str, title: str, day: str, transport: Transport | None = None) -> dict[str, Any]:
    """每日摘要出站:key 固定 alerts-digest,fingerprint=日期 → 同日重跑去重,隔日自然放行。"""
    return notify(key="alerts-digest", title=title, body=markdown, severity="info",
                  rule_key="ops.alerts_digest", fingerprint=str(day), escalate=False, transport=transport)


__all__ = [
    "KINDS", "Transport", "build_payload", "clear", "clear_state", "dedupe_window", "escalate_after",
    "load_state", "notify", "outbound_status", "save_state", "send_digest", "silenced_keys", "webhook_kind",
]
