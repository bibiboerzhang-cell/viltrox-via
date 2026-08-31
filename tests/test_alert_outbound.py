"""告警出站 + 去重/升级 + 每日摘要 + 陈旧归档(sqlite 夹具,零网络)。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.domains.ops import alert_outbound
from scripts.ops import alerts_digest, archive_stale_alerts

_SCHEMA = """
CREATE TABLE persistent_cache (
    cache_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE vkpi_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_key TEXT NOT NULL UNIQUE,
    severity TEXT NOT NULL DEFAULT 'info',
    status TEXT NOT NULL DEFAULT 'open',
    target_type TEXT NOT NULL DEFAULT '',
    target_id INTEGER,
    staff_id INTEGER,
    title TEXT NOT NULL,
    body TEXT DEFAULT '',
    rule_key TEXT DEFAULT '',
    due_at TEXT,
    resolved_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

FAKE_URL = "https://example.invalid/hooks/secret-token-ZX9QWE"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def db(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    monkeypatch.setattr(alert_outbound, "get_conn", lambda: conn)
    monkeypatch.setattr(alert_outbound, "table_exists", lambda name: name in {"persistent_cache", "vkpi_alerts"})
    for key in (
        alert_outbound.ENV_WEBHOOK_URL, alert_outbound.ENV_WEBHOOK_KIND, alert_outbound.ENV_WEBHOOK_SECRET,
        alert_outbound.ENV_DEDUPE_HOURS, alert_outbound.ENV_ESCALATE_AFTER, alert_outbound.ENV_SILENCE_KEYS,
        alert_outbound.ENV_NOTIFY_RECOVERY,
    ):
        monkeypatch.delenv(key, raising=False)
    yield conn
    conn.close()


class _Capture:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.payloads: list[dict[str, Any]] = []

    def __call__(self, payload: dict[str, Any], timeout_s: float) -> tuple[int, str]:
        self.payloads.append(payload)
        return self.status, "ok"


def _event(**extra: Any) -> dict[str, Any]:
    base = {"key": "health-sentinel", "title": "哨兵 2 项失败", "body": "[a] x\n[b] y", "severity": "danger",
            "alert_key": "health-sentinel-2026-08-22", "consecutive": 1}
    base.update(extra)
    return base


# ── payload 形状 ──


def test_feishu_payload_shape_and_signature() -> None:
    payload = alert_outbound.build_payload("feishu", _event(), secret="s3cret", now_ts=1_700_000_000)
    assert payload["msg_type"] == "text"
    assert "哨兵 2 项失败" in payload["content"]["text"]
    assert "key=health-sentinel" in payload["content"]["text"]
    expected = base64.b64encode(
        hmac.new("1700000000\ns3cret".encode("utf-8"), b"", digestmod=hashlib.sha256).digest()
    ).decode("utf-8")
    assert payload["timestamp"] == "1700000000"
    assert payload["sign"] == expected
    assert "sign" not in alert_outbound.build_payload("feishu", _event())


def test_slack_payload_shape() -> None:
    payload = alert_outbound.build_payload("slack", _event(escalated=True))
    assert payload["text"].startswith("🔴 [升级] ")
    assert payload["blocks"][0]["type"] == "section"
    assert "[a] x" in payload["blocks"][0]["text"]["text"]
    assert payload["blocks"][1]["type"] == "context"


def test_generic_payload_shape_and_unknown_kind_falls_back() -> None:
    payload = alert_outbound.build_payload("nonsense", _event(rule_key="ops.health_sentinel"))
    assert payload["source"] == "vkpi"
    assert payload["event"] == "alert"
    assert payload["severity"] == "danger"
    assert payload["rule_key"] == "ops.health_sentinel"
    assert payload["escalated"] is False
    assert payload["sent_at"].endswith("Z")


# ── 未配置 / 静默 ──


def test_not_configured_is_honest_and_still_counts(db: sqlite3.Connection) -> None:
    capture = _Capture()
    result = alert_outbound.notify(key="k1", title="t", body="b", transport=capture)
    assert result["sent"] is False
    assert result["reason"] == "not_configured"
    assert result["configured"] is False
    assert result["consecutive"] == 1
    assert capture.payloads == []
    assert alert_outbound.outbound_status()["configured"] is False
    assert alert_outbound.load_state("k1")["consecutive"] == 1


def test_silenced_key_does_not_send(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(alert_outbound.ENV_WEBHOOK_URL, FAKE_URL)
    monkeypatch.setenv(alert_outbound.ENV_SILENCE_KEYS, "noisy, k1")
    capture = _Capture()
    assert alert_outbound.notify(key="k1", title="t", transport=capture)["reason"] == "silenced"
    assert capture.payloads == []


# ── 去重窗口 / 指纹 / 升级 ──


def test_dedupe_window_blocks_resend_until_window_passes(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(alert_outbound.ENV_WEBHOOK_URL, FAKE_URL)
    monkeypatch.setenv(alert_outbound.ENV_WEBHOOK_KIND, "slack")
    monkeypatch.setenv(alert_outbound.ENV_DEDUPE_HOURS, "6")
    monkeypatch.setenv(alert_outbound.ENV_ESCALATE_AFTER, "99")
    capture = _Capture()
    t0 = datetime(2026, 8, 22, 1, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(alert_outbound, "_utcnow", lambda: t0)
    first = alert_outbound.notify(key="k1", title="t", body="b", fingerprint="a,b", transport=capture)
    assert first["sent"] is True and first["reason"] == "sent" and first["kind"] == "slack"

    monkeypatch.setattr(alert_outbound, "_utcnow", lambda: t0 + timedelta(hours=2))
    second = alert_outbound.notify(key="k1", title="t", body="b", fingerprint="a,b", transport=capture)
    assert second["sent"] is False and second["reason"] == "deduped"
    assert second["consecutive"] == 2

    monkeypatch.setattr(alert_outbound, "_utcnow", lambda: t0 + timedelta(hours=7))
    third = alert_outbound.notify(key="k1", title="t", body="b", fingerprint="a,b", transport=capture)
    assert third["sent"] is True
    assert len(capture.payloads) == 2


def test_fingerprint_change_bypasses_window(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(alert_outbound.ENV_WEBHOOK_URL, FAKE_URL)
    monkeypatch.setenv(alert_outbound.ENV_ESCALATE_AFTER, "99")
    capture = _Capture()
    assert alert_outbound.notify(key="k1", title="t", fingerprint="a", transport=capture)["sent"] is True
    assert alert_outbound.notify(key="k1", title="t", fingerprint="a", transport=capture)["reason"] == "deduped"
    assert alert_outbound.notify(key="k1", title="t", fingerprint="a,b", transport=capture)["sent"] is True


def test_escalation_after_three_marks_alert_and_breaks_window(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(alert_outbound.ENV_WEBHOOK_URL, FAKE_URL)
    monkeypatch.setenv(alert_outbound.ENV_WEBHOOK_KIND, "feishu")
    now = _iso(datetime.now(timezone.utc))
    db.execute(
        "INSERT INTO vkpi_alerts (alert_key, severity, status, title, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        ("health-sentinel-2026-08-22", "danger", "open", "t", '{"failed_keys": ["a"]}', now, now),
    )
    db.commit()
    capture = _Capture()
    kwargs = {"key": "health-sentinel", "alert_key": "health-sentinel-2026-08-22", "title": "t", "fingerprint": "a", "transport": capture}
    r1 = alert_outbound.notify(**kwargs)
    r2 = alert_outbound.notify(**kwargs)
    r3 = alert_outbound.notify(**kwargs)
    assert (r1["sent"], r2["reason"], r3["sent"]) == (True, "deduped", True)
    assert r3["escalated"] is True and r3["escalated_now"] is True and r3["consecutive"] == 3
    assert "[升级]" in capture.payloads[-1]["content"]["text"]
    meta = db.execute("SELECT metadata_json FROM vkpi_alerts WHERE alert_key=?", ("health-sentinel-2026-08-22",)).fetchone()[0]
    assert '"escalated": true' in meta
    r4 = alert_outbound.notify(**kwargs)
    assert r4["reason"] == "deduped" and r4["escalated"] is True and r4["escalated_now"] is False


def test_clear_resets_counter_and_sends_recovery(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(alert_outbound.ENV_WEBHOOK_URL, FAKE_URL)
    capture = _Capture()
    alert_outbound.notify(key="k1", title="t", transport=capture)
    cleared = alert_outbound.clear(key="k1", title="k1 恢复", transport=capture)
    assert cleared["cleared"] is True and cleared["sent"] is True
    assert capture.payloads[-1]["event"] == "recovery"
    assert alert_outbound.load_state("k1") == {}
    assert alert_outbound.notify(key="k1", title="t", transport=capture)["consecutive"] == 1
    assert alert_outbound.clear(key="never-sent", transport=capture)["reason"] == "no_prior_send"


# ── hardening:URL 绝不进日志/返回值 ──


def test_url_never_leaks_into_logs_or_results(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv(alert_outbound.ENV_WEBHOOK_URL, FAKE_URL)

    def exploding(payload: dict[str, Any], timeout_s: float) -> tuple[int, str]:
        raise ConnectionError(f"cannot reach {FAKE_URL} (host example.invalid)")

    with caplog.at_level(logging.DEBUG, logger="viltrox"):
        result = alert_outbound.notify(key="k1", title="t", transport=exploding)
        bad = alert_outbound.notify(key="k2", title="t", transport=_Capture(status=500))
    assert result["sent"] is False and result["reason"] == "delivery_error"
    assert bad["reason"] == "http_error" and bad["status"] == 500
    blob = caplog.text + repr(result) + repr(bad) + repr(alert_outbound.outbound_status())
    assert FAKE_URL not in blob
    assert "secret-token-ZX9QWE" not in blob
    assert "example.invalid" not in blob
    assert alert_outbound._redact(f"boom {FAKE_URL}") == "boom <webhook-url>"


def test_stateless_failure_notifier_never_touches_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(alert_outbound.ENV_WEBHOOK_URL, FAKE_URL)
    monkeypatch.setattr(alert_outbound, "load_state", lambda key: pytest.fail("stateless path read DB state"))
    monkeypatch.setattr(alert_outbound, "save_state", lambda key, state: pytest.fail("stateless path wrote DB state"))
    capture = _Capture()

    result = alert_outbound.notify_stateless(
        key="systemd-failure:vkpi-sync-daily.service",
        title="daily sync failed",
        body="inspect journal",
        transport=capture,
    )

    assert result == {
        "configured": True,
        "kind": "generic",
        "key": "systemd-failure:vkpi-sync-daily.service",
        "sent": True,
        "reason": "sent",
        "status": 200,
    }
    assert capture.payloads[0]["rule_key"] is None


def test_stateless_failure_notifier_is_honest_when_channel_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(alert_outbound.ENV_WEBHOOK_URL, raising=False)
    capture = _Capture()

    result = alert_outbound.notify_stateless(key="daily-sync-deadman", title="failed", transport=capture)

    assert result == {
        "configured": False,
        "kind": "generic",
        "key": "daily-sync-deadman",
        "sent": False,
        "reason": "not_configured",
    }
    assert capture.payloads == []


def test_stateless_failure_notifier_does_not_leak_url_on_delivery_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(alert_outbound.ENV_WEBHOOK_URL, FAKE_URL)

    def explode(payload: dict[str, Any], timeout_s: float) -> tuple[int, str]:
        raise ConnectionError(f"failed {FAKE_URL}")

    with caplog.at_level(logging.DEBUG, logger="viltrox"):
        result = alert_outbound.notify_stateless(
            key="daily-sync-deadman",
            title="failed",
            transport=explode,
        )

    assert result["reason"] == "delivery_error"
    assert FAKE_URL not in str(result)
    assert FAKE_URL not in caplog.text


# ── 每日摘要 ──


def _seed_alerts(conn: sqlite3.Connection, now: datetime) -> None:
    rows = [
        ("a-danger", "danger", "open", "账本漂移", "ops.health_sentinel", '{"escalated": true}', now - timedelta(days=2), now - timedelta(hours=1), None),
        ("b-warn", "warning", "open", "项目停滞", "project.stalled_review", "{}", now - timedelta(hours=3), now - timedelta(hours=3), None),
        ("c-stale", "info", "open", "老告警", "budget_guard.warning_or_hard_stop", "{}", now - timedelta(days=60), now - timedelta(days=45), None),
        ("d-resolved", "warning", "resolved", "已修", "ops.threshold.failure_rate", "{}", now - timedelta(days=3), now - timedelta(hours=5), now - timedelta(hours=5)),
        ("e-archived", "info", "archived", "归档了", "x", "{}", now - timedelta(days=90), now - timedelta(days=1), None),
    ]
    for key, sev, status, title, rule, meta, created, updated, resolved in rows:
        conn.execute(
            "INSERT INTO vkpi_alerts (alert_key, severity, status, title, rule_key, metadata_json, created_at, updated_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (key, sev, status, title, rule, meta, _iso(created), _iso(updated), _iso(resolved) if resolved else None),
        )
    conn.commit()


def test_digest_collect_and_render(db: sqlite3.Connection) -> None:
    now = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)
    _seed_alerts(db, now)
    digest = alerts_digest.collect_digest(db, now=now, hours=24, limit=10)
    assert digest["open_total"] == 3
    assert digest["open_by_severity"] == {"danger": 1, "warning": 1, "info": 1}
    assert digest["escalated_total"] == 1 and digest["escalated"][0]["alert_key"] == "a-danger"
    assert digest["new_in_window"] == 1
    assert digest["resolved_in_window"] == 1
    assert digest["stale_open_total"] == 1
    assert digest["archived_total"] == 1
    assert digest["top_open"][0]["alert_key"] == "a-danger"
    markdown = alerts_digest.render_markdown(digest)
    assert markdown.startswith("# V-KPI 告警日报 2026-08-22")
    assert "未关闭(open):**3**" in markdown
    assert "已升级(escalated):**1**" in markdown
    assert "近 24h 新增:**1**" in markdown
    assert "近 24h 已解决:**1**" in markdown
    assert "账本漂移 ⬆" in markdown
    assert "ops.health_sentinel: 1" in markdown


def test_digest_renders_empty_state(db: sqlite3.Connection) -> None:
    digest = alerts_digest.collect_digest(db, now=datetime(2026, 8, 22, tzinfo=timezone.utc))
    assert digest["open_total"] == 0
    assert "当前没有 open 告警" in alerts_digest.render_markdown(digest)


def test_digest_script_sends_with_same_day_dedupe(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from app.db import connection as db_connection

    @contextmanager
    def scope(**_kwargs: Any):
        yield db

    monkeypatch.setattr(db_connection, "db_connection_sync_scope", scope)
    monkeypatch.setattr(db_connection, "get_conn", lambda: db)
    monkeypatch.setattr(db_connection, "table_exists", lambda name: name == "vkpi_alerts")
    monkeypatch.setenv(alert_outbound.ENV_WEBHOOK_URL, FAKE_URL)
    monkeypatch.setenv(alert_outbound.ENV_WEBHOOK_KIND, "generic")
    capture = _Capture()
    monkeypatch.setattr(alert_outbound, "_http_transport", capture)
    _seed_alerts(db, datetime.now(timezone.utc))

    assert alerts_digest.run([]) == 0
    first = capsys.readouterr().out
    assert "outbound: kind=generic sent=True reason=sent" in first
    assert capture.payloads[-1]["event"] == "alert" and capture.payloads[-1]["key"] == "alerts-digest"
    assert capture.payloads[-1]["escalated"] is False
    assert alerts_digest.run([]) == 0
    assert "sent=False reason=deduped" in capsys.readouterr().out
    assert alerts_digest.run(["--no-send"]) == 0
    assert "reason=skipped_by_flag" in capsys.readouterr().out
    assert FAKE_URL not in first


# ── 陈旧归档(默认 dry-run)──


def test_archive_stale_alerts_dry_run_then_apply(db: sqlite3.Connection) -> None:
    now = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)
    _seed_alerts(db, now)
    stale = archive_stale_alerts.find_stale_open_alerts(db, now=now, days=30)
    assert [r["alert_key"] for r in stale] == ["c-stale"]
    assert db.execute("SELECT status FROM vkpi_alerts WHERE alert_key='c-stale'").fetchone()[0] == "open"
    assert archive_stale_alerts.archive_alerts(db, stale, now=now, days=30) == 1
    row = db.execute("SELECT status, metadata_json FROM vkpi_alerts WHERE alert_key='c-stale'").fetchone()
    assert row[0] == "archived"
    assert '"archived_reason": "stale_open_no_update"' in row[1]
    assert db.execute("SELECT COUNT(*) FROM vkpi_alerts").fetchone()[0] == 5
    assert archive_stale_alerts.find_stale_open_alerts(db, now=now, days=30) == []


def test_archive_script_defaults_to_dry_run(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from app.db import connection as db_connection

    @contextmanager
    def scope(**_kwargs: Any):
        yield db

    monkeypatch.setattr(db_connection, "db_connection_sync_scope", scope)
    monkeypatch.setattr(db_connection, "get_conn", lambda: db)
    monkeypatch.setattr(db_connection, "table_exists", lambda name: name == "vkpi_alerts")
    monkeypatch.setattr(db_connection, "is_postgres_runtime", lambda: False)
    _seed_alerts(db, datetime.now(timezone.utc))
    assert archive_stale_alerts.run([]) == 0
    text = capsys.readouterr().out
    assert "[dry-run]" in text and "候选 1 条" in text and "c-stale" in text
    assert db.execute("SELECT status FROM vkpi_alerts WHERE alert_key='c-stale'").fetchone()[0] == "open"
    assert archive_stale_alerts.run(["--apply"]) == 0
    assert "已标 archived:1 条" in capsys.readouterr().out
    assert db.execute("SELECT status FROM vkpi_alerts WHERE alert_key='c-stale'").fetchone()[0] == "archived"
