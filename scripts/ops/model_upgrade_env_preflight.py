#!/usr/bin/env python3
"""模型升级 env 预检:读一个 env 文件,只打印键名 / 模型 id / 判定,绝不打印任何值。

为什么要有它(2026-08 模型升级刀):线上 .env 不随部署 rsync、由人手改;readiness 闸
fail-closed——默认绑定没进 VKPI_LLM_READINESS_OPERATOR_ACK 又没签名证据,每一次默认
Gemini/Claude/OpenAI 调用都会静默降级 rule_v0。重启 admin-web / 16 条 worker 车道 /
scheduler 之前,对它们各自真正加载的 env 文件跑一遍本脚本,非零退出 = 不许重启。

断言(任一 FAIL 即退出码 1):
  (a) 文件里每个 *_MODEL / *_MODEL_EXACT 键的值都在 model_registry.AVAILABLE_MODELS 且有定价
      (嵌入模型键 VIA_EMBEDDING_MODEL / VIA_BGE_MODEL 默认豁免,--ignore-key 可加);
  (b) 生效的 APIFY_WORKER_GEMINI_MODEL(core/gemini_models.DEFAULT_VIDEO_GEMINI_MODEL)
      == model_registry.TASK_MODEL_BINDING['audit_video_analysis'] 模型后缀
      == platform/llm_local_evaluation.LOCAL_EVALUATION_MODEL
      (--allow-worker-pin:prod 用 env 钉回旧模型的回滚场景,后两项降为 WARN);
  (c) env 生效后 current_task_model_binding() 的每个绑定 + 三家 provider 默认链模型
      都在 VKPI_LLM_READINESS_OPERATOR_ACK 内,或 VKPI_LLM_READINESS_EVIDENCE_JSON
      里有签名证据(production_ready);
  (d) GEMINI_FINAL_V1_MODELS 不含 preview id、每项已注册;
  (e) 任何值都不含 gemini-3.7 / gemini-flash-latest / gemini-pro-latest 作为运行模型。
附加 WARN:LLM_MONTHLY_BUDGET_USD 缺失或 ≤0(历史头号地雷:缺失 → 默认 0 → 全挡)。

用法:
  .venv/bin/python scripts/ops/model_upgrade_env_preflight.py .env --extra-env-file runtime/local_operator_env.sh
  .venv/bin/python scripts/ops/model_upgrade_env_preflight.py /tmp/prod.env \
      --extra-env-file /tmp/prod-lane-overrides.env --allow-worker-pin --json

--extra-env-file 按顺序叠加(后者覆盖前者),用来模拟 systemd 多个 EnvironmentFile /
runtime_env.sh 先后 source 的真实叠加;prod 的 lane-overrides.env 只认 9 个数字键,
模型 id 不能放那里(deploy_local_to_cloud.sh 校验并整文件覆盖)。

实现说明:本进程是一次性的——先设 VKPI_SKIP_DOTENV=1(仓库 .env 不得污染对 prod 副本的判定),
清掉进程里继承的 *_MODEL / *_PROVIDER / readiness 键,再把文件内容整体灌入 os.environ,
然后才 import 注册表 / 网关(PROVIDER_CONFIG / DEFAULT_VIDEO_GEMINI_MODEL 都是 import-time)。
密钥值只进本进程内存,永不进 stdout / stderr / 日志。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FORBIDDEN_RUNTIME_MODEL_MARKERS: tuple[str, ...] = (
    "gemini-3.7",
    "gemini-flash-latest",
    "gemini-pro-latest",
)
PREVIEW_MARKER = "preview"
DEFAULT_IGNORED_MODEL_KEYS: frozenset[str] = frozenset({"VIA_EMBEDDING_MODEL", "VIA_BGE_MODEL"})
VIDEO_TASK = "audit_video_analysis"
WORKER_MODEL_KEY = "APIFY_WORKER_GEMINI_MODEL"
FINAL_V1_CHAIN_KEY = "GEMINI_FINAL_V1_MODELS"
JUDGE_MODEL_KEY = "GEMINI_FINAL_V1_QA_MODEL"
ACK_KEY = "VKPI_LLM_READINESS_OPERATOR_ACK"
BUDGET_KEY = "LLM_MONTHLY_BUDGET_USD"
# 进程继承的这些键会改变判定,灌文件前先清掉(只清键,不读值)。
_PURGE_SUFFIXES = ("_MODEL", "_MODELS", "_PROVIDER", "_MODEL_EXACT")
_PURGE_KEYS = frozenset(
    {
        ACK_KEY,
        "VKPI_LLM_READINESS_EVIDENCE_JSON",
        "LLM_PRIMARY_PROVIDER",
        "VKPI_LLM_GATEWAY_FORCE_OFFLINE",
        BUDGET_KEY,
    }
)


@dataclass(frozen=True)
class Finding:
    status: str  # PASS / WARN / FAIL
    check: str
    detail: str

    def render(self) -> str:
        return f"[{self.status}] {self.check}: {self.detail}"


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines (``export`` prefix, quotes, comments tolerated). Values stay in memory only."""

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _purge_inherited_environment() -> None:
    for key in list(os.environ):
        if key in _PURGE_KEYS or key.endswith(_PURGE_SUFFIXES):
            os.environ.pop(key, None)


def apply_env(values: Mapping[str, str]) -> None:
    """One-shot: make the file the only source of truth for model / readiness keys."""

    os.environ["VKPI_SKIP_DOTENV"] = "1"
    _purge_inherited_environment()
    for key, value in values.items():
        os.environ[key] = value


def _registered_models() -> dict[str, set[str]]:
    from app.core.model_registry import AVAILABLE_MODELS

    return {provider: set(models) for provider, models in AVAILABLE_MODELS.items()}


def _is_registered(model_id: str, registry: Mapping[str, set[str]]) -> bool:
    return any(model_id in models for models in registry.values())


def _provider_for(model_id: str, registry: Mapping[str, set[str]]) -> str:
    for provider, models in registry.items():
        if model_id in models:
            return provider
    return ""


def _pricing_blocker(provider: str, model_id: str) -> str:
    from app.platform.models.runtime import resolve_model_binding

    resolved = resolve_model_binding(provider, model_id, runtime_availability={})
    return resolved.blocker(require_registered=True, require_runtime_verified=False, require_pricing=True)


def _check_registered_models(values: Mapping[str, str], ignored: Iterable[str], registry: Mapping[str, set[str]]) -> list[Finding]:
    ignored_keys = set(ignored)
    checked: list[str] = []
    failures: list[str] = []
    for key in sorted(values):
        if not key.endswith(("_MODEL", "_MODEL_EXACT")) or key in ignored_keys:
            continue
        model_id = values[key].strip()
        if not model_id:
            failures.append(f"{key}=<empty>")
            continue
        checked.append(key)
        if not _is_registered(model_id, registry):
            failures.append(f"{key}={model_id}(not_registered)")
            continue
        blocker = _pricing_blocker(_provider_for(model_id, registry), model_id)
        if blocker:
            failures.append(f"{key}={model_id}({blocker})")
    if failures:
        return [Finding("FAIL", "a.registered_models", "; ".join(failures))]
    return [Finding("PASS", "a.registered_models", f"keys={','.join(checked) or '-'}")]


def _effective_worker_model(values: Mapping[str, str]) -> tuple[str, str]:
    try:
        from app.core import gemini_models

        return str(gemini_models.DEFAULT_VIDEO_GEMINI_MODEL), "core.gemini_models.DEFAULT_VIDEO_GEMINI_MODEL"
    except ImportError:
        return str(values.get(WORKER_MODEL_KEY, "")).strip(), f"env:{WORKER_MODEL_KEY}(core.gemini_models unavailable)"


def _check_video_model_contract(values: Mapping[str, str], *, allow_worker_pin: bool) -> list[Finding]:
    from app.core.model_registry import TASK_MODEL_BINDING, current_task_model_binding, split_binding
    from app.platform.llm_local_evaluation import LOCAL_EVALUATION_MODEL

    worker_model, worker_source = _effective_worker_model(values)
    if not worker_model:
        return [Finding("FAIL", "b.video_model_contract", f"{WORKER_MODEL_KEY} unresolved ({worker_source})")]
    _, code_default = split_binding(TASK_MODEL_BINDING[VIDEO_TASK])
    _, effective_binding = split_binding(current_task_model_binding()[VIDEO_TASK])
    findings: list[Finding] = []
    if effective_binding != worker_model:
        findings.append(
            Finding(
                "FAIL",
                "b.video_model_contract",
                f"effective_binding[{VIDEO_TASK}]={effective_binding} != worker={worker_model} ({worker_source})",
            )
        )
    drift = []
    if code_default != worker_model:
        drift.append(f"TASK_MODEL_BINDING[{VIDEO_TASK}]={code_default}")
    if LOCAL_EVALUATION_MODEL != worker_model:
        drift.append(f"LOCAL_EVALUATION_MODEL={LOCAL_EVALUATION_MODEL}")
    if drift:
        findings.append(
            Finding(
                "WARN" if allow_worker_pin else "FAIL",
                "b.video_model_contract",
                f"worker={worker_model} drifts from code contract: {', '.join(drift)}"
                + (" (allowed by --allow-worker-pin)" if allow_worker_pin else ""),
            )
        )
    if not findings:
        findings.append(
            Finding(
                "PASS",
                "b.video_model_contract",
                f"worker == TASK_MODEL_BINDING[{VIDEO_TASK}] == LOCAL_EVALUATION_MODEL == {worker_model}",
            )
        )
    return findings


def _required_bindings() -> dict[str, list[str]]:
    """Return binding -> reasons (task names / provider default) that must pass the readiness gate."""

    from app.core.model_registry import current_task_model_binding
    from app.platform import llm_gateway

    required: dict[str, list[str]] = {}
    for task, binding in sorted(current_task_model_binding().items()):
        required.setdefault(str(binding), []).append(str(task))
    for provider, config in llm_gateway.PROVIDER_CONFIG.items():
        model_id = str((config or {}).get("model") or "").strip()
        if model_id:
            required.setdefault(f"{provider}/{model_id}", []).append(f"provider_default:{provider}")
    return required


def _signed_evidence_ready(binding: str) -> bool:
    from app.platform.models.readiness import exact_binding_readiness_from_environment

    try:
        item, _source = exact_binding_readiness_from_environment(binding)
    except Exception:  # noqa: BLE001 - malformed evidence must fail closed
        return False
    return item.get("production_ready") is True


def _check_readiness_ack(values: Mapping[str, str], registry: Mapping[str, set[str]]) -> list[Finding]:
    from app.platform import llm_gateway

    acked = llm_gateway._readiness_operator_ack_bindings()
    required = _required_bindings()
    unacked: list[str] = []
    unregistered: list[str] = []
    for binding, reasons in required.items():
        provider, _, model_id = binding.partition("/")
        if model_id not in registry.get(provider, set()):
            unregistered.append(f"{binding}({','.join(reasons)})")
            continue
        if binding in acked or _signed_evidence_ready(binding):
            continue
        unacked.append(f"{binding}({','.join(reasons)})")
    findings: list[Finding] = []
    if unregistered:
        findings.append(Finding("FAIL", "c.readiness_ack", "unregistered bindings: " + "; ".join(unregistered)))
    if unacked:
        findings.append(
            Finding(
                "FAIL",
                "c.readiness_ack",
                f"not in {ACK_KEY} and no signed evidence: " + "; ".join(unacked),
            )
        )
    if not findings:
        findings.append(
            Finding("PASS", "c.readiness_ack", f"{len(required)} bindings acked/evidenced: " + ", ".join(sorted(required)))
        )
    judge = str(values.get(JUDGE_MODEL_KEY, "")).strip()
    if judge:
        if judge not in registry.get("google", set()):
            findings.append(Finding("FAIL", "c.judge_model", f"{JUDGE_MODEL_KEY}={judge} not registered under google"))
        elif f"google/{judge}" not in acked:
            findings.append(Finding("WARN", "c.judge_model", f"google/{judge} ({JUDGE_MODEL_KEY}) not in {ACK_KEY}"))
    return findings


def _check_final_v1_chain(values: Mapping[str, str], registry: Mapping[str, set[str]]) -> list[Finding]:
    raw = str(values.get(FINAL_V1_CHAIN_KEY, "")).strip()
    if not raw:
        return [Finding("PASS", "d.final_v1_chain", f"{FINAL_V1_CHAIN_KEY} unset (single exact worker model)")]
    chain = [item.strip() for item in raw.split(",") if item.strip()]
    bad: list[str] = []
    for model_id in chain:
        if PREVIEW_MARKER in model_id.lower():
            bad.append(f"{model_id}(preview)")
        elif model_id not in registry.get("google", set()):
            bad.append(f"{model_id}(not_registered)")
    if bad:
        return [Finding("FAIL", "d.final_v1_chain", f"{FINAL_V1_CHAIN_KEY}: " + "; ".join(bad))]
    findings = [Finding("PASS", "d.final_v1_chain", f"{FINAL_V1_CHAIN_KEY}={','.join(chain)}")]
    worker_model, _ = _effective_worker_model(values)
    if worker_model and chain != [worker_model]:
        findings.append(
            Finding(
                "WARN",
                "d.final_v1_chain",
                f"{FINAL_V1_CHAIN_KEY} != [{worker_model}]: worker executes the exact {WORKER_MODEL_KEY} only",
            )
        )
    return findings


def _check_forbidden_ids(values: Mapping[str, str]) -> list[Finding]:
    hits: list[str] = []
    for key in sorted(values):
        lowered = values[key].lower()
        for marker in FORBIDDEN_RUNTIME_MODEL_MARKERS:
            if marker in lowered:
                hits.append(f"{key}~{marker}")
    if hits:
        return [Finding("FAIL", "e.forbidden_runtime_models", "; ".join(hits))]
    return [Finding("PASS", "e.forbidden_runtime_models", "no gemini-3.7 / *-latest runtime ids")]


def _check_budget(values: Mapping[str, str]) -> list[Finding]:
    raw = str(values.get(BUDGET_KEY, "")).strip()
    try:
        amount = float(raw) if raw else 0.0
    except ValueError:
        amount = 0.0
    if amount <= 0:
        return [Finding("WARN", "f.monthly_budget", f"{BUDGET_KEY} missing/<=0 in this file (default 0 blocks every paid call unless another EnvironmentFile sets it)")]
    return [Finding("PASS", "f.monthly_budget", f"{BUDGET_KEY} present and > 0")]


def run_preflight(
    values: Mapping[str, str],
    *,
    ignored_keys: Iterable[str] = (),
    allow_worker_pin: bool = False,
) -> list[Finding]:
    """One-shot per process: applies ``values`` to os.environ, then imports the registry/gateway."""

    apply_env(values)
    registry = _registered_models()
    findings: list[Finding] = []
    findings.extend(_check_registered_models(values, set(DEFAULT_IGNORED_MODEL_KEYS) | set(ignored_keys), registry))
    findings.extend(_check_video_model_contract(values, allow_worker_pin=allow_worker_pin))
    findings.extend(_check_readiness_ack(values, registry))
    findings.extend(_check_final_v1_chain(values, registry))
    findings.extend(_check_forbidden_ids(values))
    findings.extend(_check_budget(values))
    return findings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("env_file", type=Path, help="env 文件路径(.env / scp 下来的 prod 副本);值绝不打印")
    parser.add_argument(
        "--extra-env-file",
        action="append",
        default=[],
        type=Path,
        help="按顺序叠加的附加 env 文件(后者覆盖前者),如 runtime/local_operator_env.sh / lane-overrides.env",
    )
    parser.add_argument("--ignore-key", action="append", default=[], help="额外豁免的 *_MODEL 键(可重复)")
    parser.add_argument(
        "--allow-worker-pin",
        action="store_true",
        help="prod 用 APIFY_WORKER_GEMINI_MODEL 钉回旧模型的回滚场景:与代码契约的漂移降为 WARN",
    )
    parser.add_argument("--json", action="store_true", help="stdout 输出 JSON(键名/模型 id/判定,无值)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    path: Path = args.env_file
    if not path.is_file():
        sys.stderr.write(f"env file not found: {path}\n")
        return 2
    values = parse_env_file(path)
    for extra in args.extra_env_file:
        if not extra.is_file():
            sys.stderr.write(f"extra env file not found: {extra}\n")
            return 2
        values.update(parse_env_file(extra))
    findings = run_preflight(values, ignored_keys=args.ignore_key, allow_worker_pin=args.allow_worker_pin)
    failures = sum(1 for item in findings if item.status == "FAIL")
    warnings = sum(1 for item in findings if item.status == "WARN")
    verdict = "FAIL" if failures else "PASS"
    if args.json:
        payload: dict[str, Any] = {
            "env_file": str(path),
            "extra_env_files": [str(item) for item in args.extra_env_file],
            "keys_seen": sorted(values),
            "values_printed": False,
            "findings": [asdict(item) for item in findings],
            "failures": failures,
            "warnings": warnings,
            "verdict": verdict,
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        layered = "".join(f" +{item}" for item in args.extra_env_file)
        sys.stdout.write(f"model_upgrade_env_preflight: file={path}{layered} keys={len(values)} (values never printed)\n")
        for item in findings:
            sys.stdout.write(item.render() + "\n")
        sys.stdout.write(f"RESULT: {verdict} ({failures} failures, {warnings} warnings)\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
