"""scripts/ops/model_upgrade_env_preflight.py — env 预检的判定契约。

每个场景在子进程里跑(预检把 env 文件灌进 os.environ 再 import 注册表/网关,
PROVIDER_CONFIG / DEFAULT_VIDEO_GEMINI_MODEL 是 import-time,进程内无法重复求值)。
断言只看 stdout 的键名/模型 id/判定,绝不把值写进断言输出。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops" / "model_upgrade_env_preflight.py"

sys.path.insert(0, str(ROOT / "backend"))

from app.core.model_registry import TASK_MODEL_BINDING, split_binding  # noqa: E402
from app.platform.llm_local_evaluation import LOCAL_EVALUATION_MODEL  # noqa: E402

VIDEO_MODEL = split_binding(TASK_MODEL_BINDING["audit_video_analysis"])[1]
ALL_DEFAULT_BINDINGS = sorted(set(TASK_MODEL_BINDING.values()))


def _happy_env() -> dict[str, str]:
    """Env that pins every task/provider default to the code defaults and acks them all."""

    bindings = set(ALL_DEFAULT_BINDINGS)
    bindings.add("google/gemini-3.5-flash-lite")
    # provider defaults (PROVIDER_CONFIG) are pinned to already-bound models so the ack list stays small
    env = {
        "APIFY_WORKER_GEMINI_MODEL": VIDEO_MODEL,
        "GEMINI_MODEL": VIDEO_MODEL,
        "VKPI_GEMINI_MODEL": VIDEO_MODEL,
        "OPENAI_MODEL": "gpt-5.6-luna",
        "VKPI_OPENAI_MODEL": "gpt-5.6-luna",
        "CLAUDE_MODEL": "claude-sonnet-5",
        "VKPI_CLAUDE_MODEL": "claude-sonnet-5",
        "CLAUDE_HAIKU_MODEL": "claude-sonnet-5",
        "VIA_SUMMARY_PROVIDER": "openai",
        "VIA_SUMMARY_MODEL": "gpt-5.6-luna",
        "GEMINI_FINAL_V1_QA_MODEL": "gemini-3.5-flash-lite",
        "VKPI_GEMINI_MODEL_EXACT": VIDEO_MODEL,
        "LLM_MONTHLY_BUDGET_USD": "2000",
        "VKPI_LLM_READINESS_OPERATOR_ACK": ",".join(sorted(bindings)),
        "GEMINI_API_KEY": "test-placeholder-not-a-real-key",
    }
    return env


def _write_env(tmp_path: Path, values: dict[str, str], name: str = "preflight.env") -> Path:
    path = tmp_path / name
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
    return path


def _run(env_file: Path, *extra: str) -> tuple[int, dict]:
    env = {k: v for k, v in os.environ.items() if not k.endswith(("_MODEL", "_MODELS", "_PROVIDER"))}
    env.pop("VKPI_LLM_READINESS_OPERATOR_ACK", None)
    env["PYTHONPATH"] = str(ROOT / "backend")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(env_file), "--json", *extra],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        timeout=120,
    )
    assert proc.stdout.strip(), proc.stderr[-2000:]
    payload = json.loads(proc.stdout)
    assert payload["values_printed"] is False
    return proc.returncode, payload


def _findings(payload: dict, check: str, status: str | None = None) -> list[dict]:
    return [
        item
        for item in payload["findings"]
        if item["check"] == check and (status is None or item["status"] == status)
    ]


def _assert_no_secret_leak(payload: dict) -> None:
    rendered = json.dumps(payload)
    assert "test-placeholder-not-a-real-key" not in rendered


def test_happy_path_passes(tmp_path: Path) -> None:
    rc, payload = _run(_write_env(tmp_path, _happy_env()))
    _assert_no_secret_leak(payload)
    assert payload["verdict"] == "PASS", payload["findings"]
    assert rc == 0
    assert _findings(payload, "b.video_model_contract", "PASS")
    assert _findings(payload, "c.readiness_ack", "PASS")
    assert _findings(payload, "e.forbidden_runtime_models", "PASS")
    assert "GEMINI_API_KEY" in payload["keys_seen"]


def test_worker_binding_drift_fails_and_pin_flag_downgrades(tmp_path: Path) -> None:
    values = _happy_env()
    values["APIFY_WORKER_GEMINI_MODEL"] = "gemini-2.5-flash"
    values["VKPI_LLM_READINESS_OPERATOR_ACK"] += ",google/gemini-2.5-flash"
    rc, payload = _run(_write_env(tmp_path, values))
    assert rc == 1
    drift = _findings(payload, "b.video_model_contract", "FAIL")
    assert drift and "LOCAL_EVALUATION_MODEL" in drift[0]["detail"]
    assert LOCAL_EVALUATION_MODEL in drift[0]["detail"]

    rc_pin, payload_pin = _run(_write_env(tmp_path, values, "pin.env"), "--allow-worker-pin")
    assert rc_pin == 0, payload_pin["findings"]
    assert _findings(payload_pin, "b.video_model_contract", "WARN")
    assert not _findings(payload_pin, "b.video_model_contract", "FAIL")


def test_unacked_default_binding_fails(tmp_path: Path) -> None:
    values = _happy_env()
    acked = [item for item in values["VKPI_LLM_READINESS_OPERATOR_ACK"].split(",") if item != f"google/{VIDEO_MODEL}"]
    values["VKPI_LLM_READINESS_OPERATOR_ACK"] = ",".join(acked)
    rc, payload = _run(_write_env(tmp_path, values))
    assert rc == 1
    failures = _findings(payload, "c.readiness_ack", "FAIL")
    assert failures
    assert f"google/{VIDEO_MODEL}" in failures[0]["detail"]
    assert "audit_video_analysis" in failures[0]["detail"]


def test_preview_id_in_final_v1_chain_fails(tmp_path: Path) -> None:
    values = _happy_env()
    values["GEMINI_FINAL_V1_MODELS"] = "gemini-3-flash-preview,gemini-2.5-flash"
    rc, payload = _run(_write_env(tmp_path, values))
    assert rc == 1
    failures = _findings(payload, "d.final_v1_chain", "FAIL")
    assert failures and "gemini-3-flash-preview(preview)" in failures[0]["detail"]


@pytest.mark.parametrize("bad_value", ["gemini-3.7-flash", "gemini-flash-latest", "gemini-pro-latest"])
def test_gemini_3_7_and_latest_aliases_are_rejected(tmp_path: Path, bad_value: str) -> None:
    values = _happy_env()
    values["GEMINI_MODEL"] = bad_value
    rc, payload = _run(_write_env(tmp_path, values))
    assert rc == 1
    forbidden = _findings(payload, "e.forbidden_runtime_models", "FAIL")
    assert forbidden and "GEMINI_MODEL~" in forbidden[0]["detail"]
    # gemini-3.7-flash is also unregistered; the *-latest aliases are registered ids but still forbidden at runtime
    if bad_value.startswith("gemini-3.7"):
        assert _findings(payload, "a.registered_models", "FAIL")


def test_missing_budget_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    values = _happy_env()
    values.pop("LLM_MONTHLY_BUDGET_USD")
    rc, payload = _run(_write_env(tmp_path, values))
    assert rc == 0, payload["findings"]
    assert _findings(payload, "f.monthly_budget", "WARN")


def test_extra_env_file_layers_the_ack(tmp_path: Path) -> None:
    values = _happy_env()
    ack = values.pop("VKPI_LLM_READINESS_OPERATOR_ACK")
    base = _write_env(tmp_path, values, "base.env")
    operator = tmp_path / "local_operator_env.sh"
    operator.write_text(f'export VKPI_LLM_READINESS_OPERATOR_ACK="{ack}"\n', encoding="utf-8")
    rc_without, payload_without = _run(base)
    assert rc_without == 1 and _findings(payload_without, "c.readiness_ack", "FAIL")
    rc_with, payload_with = _run(base, "--extra-env-file", str(operator))
    assert rc_with == 0, payload_with["findings"]
    assert payload_with["extra_env_files"] == [str(operator)]
