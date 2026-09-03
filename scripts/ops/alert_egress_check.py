#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""告警出站自检:读 VKPI_ALERT_WEBHOOK_URL 发一条测试告警,验证对端返回 2xx。

公测缺口 O2(2026-09-02):告警到人的唯一通道是 VKPI_ALERT_WEBHOOK_URL,prod 是否配置仓内无任何证据。
本脚本在目标机上跑一次即可给出结论;零数据库依赖(只走 app.core.stateless_alert,纯标准库)。

诚实 by design:
  - URL / 签名密钥永不打印、永不进日志、永不进返回值;只报 configured/kind/signed 布尔与 HTTP 状态。
  - 退出码:0 = 已发送且 2xx;1 = 发送失败(非 2xx / 网络错误);2 = 未配置或 URL 不是合法 https;
    3 = key 被 VKPI_ALERT_SILENCE_KEYS 静默(通道存在但此 key 不出站)。
  - --dry-run 只做配置检查不出站(退出码 0/2)。

用法(prod 需先 source 服务 env,见 handoff):
  PYTHONPATH=backend .venv/bin/python scripts/ops/alert_egress_check.py            # 发送 + 人类可读
  PYTHONPATH=backend .venv/bin/python scripts/ops/alert_egress_check.py --json     # 机器可读
  PYTHONPATH=backend .venv/bin/python scripts/ops/alert_egress_check.py --dry-run  # 只查配置
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
for _extra in (REPO / "scripts", REPO / "backend"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from stdout_utils import out, out_json  # noqa: E402

from app.core import stateless_alert  # noqa: E402

ALERT_KEY = "egress-check"
EXIT_SENT = 0
EXIT_FAILED = 1
EXIT_NOT_CONFIGURED = 2
EXIT_SILENCED = 3


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send one test alert through VKPI_ALERT_WEBHOOK_URL and verify 2xx.")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--dry-run", action="store_true", help="only report configuration; do not send")
    parser.add_argument("--severity", default="info", choices=["info", "warning", "danger"])
    return parser.parse_args(argv)


def _body_text() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"host={socket.gethostname()} env={os.environ.get('ENVIRONMENT', 'local')} at={stamp}\n"
        "这是 alert_egress_check 发出的测试告警;收到即说明告警通道可达,无需处理。"
    )


def _exit_code(result: dict[str, Any]) -> int:
    if result.get("sent"):
        return EXIT_SENT
    reason = str(result.get("reason") or "")
    if reason == "not_configured":
        return EXIT_NOT_CONFIGURED
    if reason == "silenced":
        return EXIT_SILENCED
    return EXIT_FAILED


def run(argv: list[str] | None = None, *, transport: stateless_alert.Transport | None = None) -> int:
    """Return the process exit code; ``transport`` is injectable for tests."""
    args = _parse_args(argv)
    status = stateless_alert.outbound_status()
    report: dict[str, Any] = {"check": ALERT_KEY, **status, "silenced": ALERT_KEY in stateless_alert.silenced_keys()}
    if args.dry_run or not status["configured"]:
        report["sent"] = False
        report["reason"] = "dry_run" if status["configured"] else "not_configured"
    else:
        result = stateless_alert.notify_stateless(
            key=ALERT_KEY,
            title="V-KPI 告警通道自检",
            body=_body_text(),
            severity=args.severity,
            transport=transport,
        )
        report.update({k: v for k, v in result.items() if k in {"sent", "reason", "status", "error"}})
    code = EXIT_SENT if args.dry_run and status["configured"] else _exit_code(report)
    report["exit_code"] = code
    if args.json:
        out_json(report, ensure_ascii=False)
    else:
        _emit_human(report)
    return code


def _emit_human(report: dict[str, Any]) -> None:
    out(f"alert egress check: configured={report['configured']} kind={report['kind']} signed={report['signed']}")
    if report["silenced"]:
        out(f"note: key '{ALERT_KEY}' is listed in VKPI_ALERT_SILENCE_KEYS; the channel exists but this key will not send")
    if report.get("reason") == "not_configured":
        out("result: NOT CONFIGURED — VKPI_ALERT_WEBHOOK_URL is empty or not a plain https URL (exit 2)")
        return
    if report.get("reason") == "dry_run":
        out("result: dry-run only, nothing sent (exit 0)")
        return
    status_text = f" http_status={report['status']}" if report.get("status") is not None else ""
    verdict = "SENT (2xx)" if report.get("sent") else f"FAILED reason={report.get('reason')}"
    out(f"result: {verdict}{status_text} (exit {report['exit_code']})")


if __name__ == "__main__":
    sys.exit(run())
