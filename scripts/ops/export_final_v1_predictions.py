#!/usr/bin/env python3
"""final_v1 预测导出 + 无 gold 的「契约有效率 / 与基线一致率」对比(只读,零 LLM 成本)。

子命令 ``export``:从 ``vkpi_analysis_cache``(target_type=video,
derive_method=video_analysis_final_v1,status=ready)按 evidence id 列表导出 predictions JSON,
结构与 ``backend/app/domains/kol/final_v1_quality_eval.validate_predictions`` 契约一致
(schema_version=gemini_final_v1_quality_predictions_v1,dataset_id,
predictions[{case_id, media_sha256, model, prompt_version, output}]),可直接喂
``scripts/eval_gemini_final_v1_quality.py --predictions``(有真 gold 时)。

* 行的 ``model`` 列 / ``result.llm_execution.model`` 必须等于 --model,否则该 evidence 记为
  ``model_mismatch``(典型原因:cache 行没标 stale、worker 还在吐旧模型结果)。
* ``media_sha256`` 优先取 ``vkpi_media_cache_assets.checksum``(文件 sha256,通过
  result.raw_gemini_video.media_resolution.cache_asset_id 关联);拿不到时退化为
  源 URL 的 sha256(只做稳定 case 键,``meta.media_sha256_source`` 诚实标注来源)。
* ``prompt_version`` 默认 = ``video_analysis_final_v1@<静态 prompt sha256 前 12 位>``
  (prompt 取自当前工作树的 gemini_video_prompts;--prompt-version 可覆盖)。
* 每条 ``meta`` 附带 cost/latency/usage/方法/llm_dimensions_11 是否落库,供 compare 汇总。
* SQL 走 compat 适配器(``?`` 占位符,无字面 % / LIKE);只 SELECT。

子命令 ``compare``:基线 predictions vs 若干候选 predictions,产出描述性汇总
(``claim_status=descriptive_only``,不是准确率声明):契约有效率(REQUIRED_OUTPUT_SHAPES
全覆盖比例)、brand_status 与基线一致率、产品/竞品集合 Jaccard、unknown/absent 支撑、
畸形证据计数、成本与时延 p50/p95、llm_dimensions_11 落库率。没有真 gold 不造 gold。

用法:
  .venv/bin/python scripts/ops/export_final_v1_predictions.py export \\
      --database-url postgresql://postgres@127.0.0.1:54333/vkpi_eval_gemini_3_6_flash \\
      --model gemini-3.6-flash --dataset-id model-upgrade-2026-08 \\
      --evidence-ids /path/eval_evidence_ids.txt --output out/pred_gemini_3_6_flash.json
  .venv/bin/python scripts/ops/export_final_v1_predictions.py compare \\
      --baseline out/pred_gemini_2_5_flash.json --candidate out/pred_gemini_3_6_flash.json \\
      --output out/agreement_summary.json

退出码:0 全部导出;4 有 evidence 缺失/模型不符(文件仍写出);2 参数/输入错误;3 导出结果未过契约校验。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"
FINAL_V1_SCHEMA_VERSION = "video_analysis_final_v1"
DEEP_ANALYSIS_KIND = "video_final_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# --------------------------------------------------------------------------- helpers
def read_evidence_ids(path: Path) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            value = int(line)
        except ValueError as exc:
            raise ValueError(f"evidence id not an integer: {line!r}") from exc
        if value not in seen:
            seen.add(value)
            ids.append(value)
    if not ids:
        raise ValueError("evidence id list is empty")
    return ids


def redact_database_url(url: str) -> str:
    return re.sub(r"^([A-Za-z][A-Za-z0-9+.-]*://)([^/@]+@)", r"\1***@", str(url or ""))


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def default_prompt_version() -> str:
    """Schema version + fingerprint of the static final_v1 prompt in this working tree."""

    # 直接按文件路径加载 leaf 模块,绕开 analyzers 包 __init__(会拉起 provider client)。
    path = BACKEND / "app" / "services" / "ai" / "analyzers" / "gemini_video_prompts.py"
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("vkpi_export_gemini_video_prompts", path)
        if spec is None or spec.loader is None:
            return FINAL_V1_SCHEMA_VERSION
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        digest = hashlib.sha256(module._video_final_v1_static_prompt().encode("utf-8")).hexdigest()[:12]  # noqa: SLF001
        return f"{FINAL_V1_SCHEMA_VERSION}@{digest}"
    except Exception:  # noqa: BLE001 - prompt module optional for the exporter
        return FINAL_V1_SCHEMA_VERSION


def open_compat_connection(database_url: str) -> Any:
    """Wrap one dedicated psycopg connection in the compat adapter (``?`` placeholders)."""

    import psycopg

    from app.db.connection import PostgresCompatConnection

    raw = psycopg.connect(database_url, autocommit=True)
    raw.read_only = True
    return PostgresCompatConnection(raw)


# --------------------------------------------------------------------------- export
def fetch_cache_rows(conn: Any, evidence_ids: Sequence[int], *, derive_method: str) -> dict[str, dict[str, Any]]:
    placeholders = ", ".join("?" for _ in evidence_ids)
    rows = conn.execute(
        f"""
        SELECT c.id AS cache_id, c.target_id, c.model, c.status, c.cost, c.updated_at, c.result,
               EXISTS (
                 SELECT 1 FROM vkpi_kol_llm_deep_analysis_results d
                 WHERE d.source_cache_id = c.id
                   AND d.analysis_kind = ?
                   AND d.status = 'ready'
                   AND d.created_at >= c.updated_at
               ) AS dimensions_11_present
        FROM vkpi_analysis_cache c
        WHERE c.target_type = 'video'
          AND c.derive_method = ?
          AND c.target_id IN ({placeholders})
        """,
        (DEEP_ANALYSIS_KIND, derive_method, *[str(item) for item in evidence_ids]),
    ).fetchall()
    return {str(dict(row)["target_id"]): dict(row) for row in rows}


def fetch_evidence_urls(conn: Any, evidence_ids: Sequence[int]) -> dict[int, str]:
    placeholders = ", ".join("?" for _ in evidence_ids)
    rows = conn.execute(
        f"SELECT id, content_url FROM vkpi_kol_video_evidence WHERE id IN ({placeholders})",
        tuple(int(item) for item in evidence_ids),
    ).fetchall()
    return {int(dict(row)["id"]): str(dict(row).get("content_url") or "") for row in rows}


def fetch_media_checksums(conn: Any, asset_ids: Iterable[int]) -> dict[int, str]:
    ids = sorted({int(item) for item in asset_ids if item})
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT id, checksum FROM vkpi_media_cache_assets WHERE id IN ({placeholders})",
        tuple(ids),
    ).fetchall()
    return {int(dict(row)["id"]): str(dict(row).get("checksum") or "").lower() for row in rows}


def _cache_asset_id(result: dict[str, Any]) -> int | None:
    raw = _as_dict(result.get("raw_gemini_video"))
    resolution = _as_dict(raw.get("media_resolution"))
    try:
        value = int(resolution.get("cache_asset_id") or 0)
    except (TypeError, ValueError):
        return None
    return value or None


def _media_sha(result: dict[str, Any], checksums: dict[int, str], source_url: str) -> tuple[str, str]:
    asset_id = _cache_asset_id(result)
    checksum = checksums.get(asset_id or -1, "")
    if _SHA256_RE.fullmatch(checksum):
        return checksum, "media_cache_checksum"
    url = str(_as_dict(result.get("source")).get("url") or source_url or "").strip()
    return hashlib.sha256(url.encode("utf-8")).hexdigest(), "source_url_sha256"


def _reported_model(row: dict[str, Any], result: dict[str, Any]) -> tuple[str, str]:
    execution = _as_dict(result.get("llm_execution"))
    return str(row.get("model") or "").strip(), str(execution.get("model") or execution.get("reported_model") or "").strip()


def _meta(row: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    cost = _as_dict(result.get("cost"))
    raw = _as_dict(result.get("raw_gemini_video"))
    usage = _as_dict(cost.get("usage_metadata"))
    return {
        "cache_id": row.get("cache_id"),
        "job_id": result.get("job_id"),
        "updated_at": str(row.get("updated_at") or ""),
        "method": str(raw.get("method") or ""),
        "cost_usd": _number(cost.get("recorded_cost_usd")) if cost.get("recorded_cost_usd") is not None else _number(row.get("cost")),
        "latency_ms": _number(cost.get("latency_ms")),
        "stage_timings_ms": _as_dict(cost.get("stage_timings_ms")),
        "prompt_tokens": _number(usage.get("prompt_token_count")),
        "output_tokens": _number(usage.get("candidates_token_count")),
        "cached_tokens": _number(usage.get("cached_content_token_count")),
        "llm_dimensions_11_present": bool(row.get("dimensions_11_present")),
    }


def build_predictions(
    *,
    rows: dict[str, dict[str, Any]],
    evidence_ids: Sequence[int],
    evidence_urls: dict[int, str],
    checksums: dict[int, str],
    model: str,
    dataset_id: str,
    prompt_version: str,
    database_label: str,
    include_raw: bool = False,
) -> dict[str, Any]:
    predictions: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for evidence_id in evidence_ids:
        row = rows.get(str(evidence_id))
        if row is None:
            missing.append({"evidence_id": evidence_id, "reason": "no_cache_row"})
            continue
        status = str(row.get("status") or "")
        if status != "ready":
            missing.append({"evidence_id": evidence_id, "reason": f"status_{status or 'unknown'}"})
            continue
        result = _as_dict(row.get("result"))
        column_model, execution_model = _reported_model(row, result)
        if column_model != model or (execution_model and execution_model != model):
            missing.append(
                {
                    "evidence_id": evidence_id,
                    "reason": "model_mismatch",
                    "cache_model": column_model,
                    "execution_model": execution_model,
                }
            )
            continue
        media_sha, sha_source = _media_sha(result, checksums, evidence_urls.get(evidence_id, ""))
        output = dict(result)
        if not include_raw:
            output.pop("raw_gemini_video", None)
        meta = _meta(row, result)
        meta["media_sha256_source"] = sha_source
        predictions.append(
            {
                "case_id": f"evidence-{evidence_id}",
                "media_sha256": media_sha,
                "model": model,
                "prompt_version": prompt_version,
                "output": output,
                "meta": meta,
            }
        )
    return {
        "schema_version": "gemini_final_v1_quality_predictions_v1",
        "dataset_id": dataset_id,
        "model": model,
        "derive_method": FINAL_V1_DERIVE_METHOD,
        "prompt_version": prompt_version,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "claim_status": "descriptive_only",
        "execution": {
            "mode": "vkpi_analysis_cache_export",
            "model_invoked": True,
            "provider_calls": False,
            "database_writes": False,
        },
        "source": {
            "database": database_label,
            "requested": len(evidence_ids),
            "exported": len(predictions),
            "missing": missing,
        },
        "predictions": predictions,
    }


def run_export(args: argparse.Namespace) -> int:
    from app.domains.kol.final_v1_quality_eval import FinalV1QualityInputError, validate_predictions

    try:
        evidence_ids = read_evidence_ids(args.evidence_ids)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"evidence ids unreadable: {exc}\n")
        return 2
    prompt_version = str(args.prompt_version or "").strip() or default_prompt_version()
    conn = open_compat_connection(args.database_url)
    try:
        rows = fetch_cache_rows(conn, evidence_ids, derive_method=args.derive_method)
        evidence_urls = fetch_evidence_urls(conn, evidence_ids)
        asset_ids = [
            _cache_asset_id(_as_dict(row.get("result"))) or 0
            for row in rows.values()
        ]
        checksums = fetch_media_checksums(conn, asset_ids)
    finally:
        conn.close()
    manifest = build_predictions(
        rows=rows,
        evidence_ids=evidence_ids,
        evidence_urls=evidence_urls,
        checksums=checksums,
        model=args.model,
        dataset_id=args.dataset_id,
        prompt_version=prompt_version,
        database_label=redact_database_url(args.database_url),
        include_raw=bool(args.include_raw),
    )
    try:
        validate_predictions(manifest)
    except FinalV1QualityInputError as exc:
        sys.stderr.write(f"exported predictions failed contract validation: {exc}\n")
        return 3
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    source = manifest["source"]
    sys.stderr.write(
        f"export_final_v1_predictions: model={args.model} exported={source['exported']}/{source['requested']}"
        f" missing={len(source['missing'])} prompt_version={prompt_version}\n"
    )
    return 0 if not source["missing"] else 4


# --------------------------------------------------------------------------- compare
def percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def _stats(values: Sequence[float | None]) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "p50": None, "p95": None, "sum": None}
    return {
        "n": len(vals),
        "mean": round(sum(vals) / len(vals), 6),
        "p50": round(percentile(vals, 0.5) or 0.0, 6),
        "p95": round(percentile(vals, 0.95) or 0.0, 6),
        "sum": round(sum(vals), 6),
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    return round(len(left & right) / len(left | right), 6)


def extract_case(record: dict[str, Any]) -> dict[str, Any]:
    """Contract coverage + structured brand/product/competitor view of one prediction (pure)."""

    from app.domains.kol import final_v1_quality_eval as fq

    payload = fq._unwrap_output(record)  # noqa: SLF001 - single source of truth for the contract
    present, missing = fq._schema_profile(payload)  # noqa: SLF001
    extracted = fq._extract_case_prediction(payload, product_aliases={}, competitor_aliases={})  # noqa: SLF001
    meta = _as_dict(record.get("meta"))
    return {
        "case_id": str(record.get("case_id") or ""),
        "schema_fields_present": present,
        "schema_fields_required": len(fq.REQUIRED_OUTPUT_SHAPES),
        "schema_missing": list(missing),
        "contract_valid": not missing,
        "brand_status": str(extracted["status"]),
        "inspection_complete": bool(extracted["inspection_complete"]),
        "checked_modalities": sorted(extracted["checked_modalities"]),
        "products": set(extracted["products"]),
        "competitors": set(extracted["competitors"]),
        "evidence_count": len(extracted["evidence"]),
        "malformed_evidence": int(extracted["malformed_evidence_count"]),
        "absent_supported": (
            str(extracted["status"]) != "absent"
            or (bool(extracted["inspection_complete"]) and {"visual", "audio"} <= set(extracted["checked_modalities"]))
        ),
        "cost_usd": _number(meta.get("cost_usd")),
        "latency_ms": _number(meta.get("latency_ms")),
        "output_tokens": _number(meta.get("output_tokens")),
        "llm_dimensions_11_present": bool(meta.get("llm_dimensions_11_present")),
    }


def summarize_model(manifest: dict[str, Any], baseline_cases: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    records = manifest.get("predictions") if isinstance(manifest.get("predictions"), list) else []
    cases = [extract_case(record) for record in records if isinstance(record, dict)]
    n = len(cases)
    contract_valid = sum(1 for case in cases if case["contract_valid"])
    statuses: dict[str, int] = {}
    for case in cases:
        statuses[case["brand_status"]] = statuses.get(case["brand_status"], 0) + 1
    summary: dict[str, Any] = {
        "model": manifest.get("model"),
        "prompt_version": manifest.get("prompt_version"),
        "dataset_id": manifest.get("dataset_id"),
        "cases": n,
        "requested": _as_dict(manifest.get("source")).get("requested"),
        "missing": len(_as_dict(manifest.get("source")).get("missing") or []),
        "contract": {
            "valid_cases": contract_valid,
            "validity_rate": round(contract_valid / n, 6) if n else None,
            "missing_paths": sorted({path for case in cases for path in case["schema_missing"]}),
        },
        "brand_status_distribution": statuses,
        "unsupported_absent_count": sum(1 for case in cases if not case["absent_supported"]),
        "inspection_complete_rate": round(sum(1 for case in cases if case["inspection_complete"]) / n, 6) if n else None,
        "malformed_evidence_total": sum(case["malformed_evidence"] for case in cases),
        "evidence_count": _stats([float(case["evidence_count"]) for case in cases]),
        "llm_dimensions_11_rate": round(sum(1 for case in cases if case["llm_dimensions_11_present"]) / n, 6) if n else None,
        "cost_usd": _stats([case["cost_usd"] for case in cases]),
        "latency_ms": _stats([case["latency_ms"] for case in cases]),
        "output_tokens": _stats([case["output_tokens"] for case in cases]),
    }
    if baseline_cases is not None:
        paired = [(case, baseline_cases[case["case_id"]]) for case in cases if case["case_id"] in baseline_cases]
        agree = sum(1 for case, base in paired if case["brand_status"] == base["brand_status"])
        summary["agreement_vs_baseline"] = {
            "paired_cases": len(paired),
            "brand_status_agreement_rate": round(agree / len(paired), 6) if paired else None,
            "brand_status_disagreements": [
                {"case_id": case["case_id"], "baseline": base["brand_status"], "candidate": case["brand_status"]}
                for case, base in paired
                if case["brand_status"] != base["brand_status"]
            ],
            "products_jaccard_mean": round(sum(_jaccard(case["products"], base["products"]) for case, base in paired) / len(paired), 6) if paired else None,
            "competitors_jaccard_mean": round(sum(_jaccard(case["competitors"], base["competitors"]) for case, base in paired) / len(paired), 6) if paired else None,
            "unpaired_case_ids": sorted(case["case_id"] for case in cases if case["case_id"] not in baseline_cases),
        }
    return summary


def run_compare(args: argparse.Namespace) -> int:
    from app.domains.kol.final_v1_quality_eval import FinalV1QualityInputError, validate_predictions

    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        validate_predictions(baseline)
        candidates = []
        for path in args.candidate:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            validate_predictions(manifest)
            candidates.append(manifest)
    except (OSError, json.JSONDecodeError, FinalV1QualityInputError) as exc:
        sys.stderr.write(f"compare input invalid: {exc}\n")
        return 2
    baseline_cases = {case["case_id"]: case for case in (extract_case(r) for r in baseline["predictions"])}
    report = {
        "schema_version": "vkpi_final_v1_model_agreement_summary_v1",
        "claim_status": "descriptive_only",
        "gold": "none_agreement_vs_baseline_mode",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "baseline": summarize_model(baseline, None),
        "candidates": [summarize_model(manifest, baseline_cases) for manifest in candidates],
        "notes": [
            "contract validity = all REQUIRED_OUTPUT_SHAPES paths present with the right types (final_v1 contract unchanged)",
            "agreement metrics compare the candidate against the baseline model output, not against human truth",
            "cost_usd/latency_ms come from result.cost recorded by the worker (ledger basis), per video",
        ],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


# --------------------------------------------------------------------------- cli
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="export ready final_v1 cache rows as predictions JSON")
    export.add_argument("--database-url", required=True)
    export.add_argument("--model", required=True, help="exact model id the rows must carry")
    export.add_argument("--dataset-id", required=True)
    export.add_argument("--evidence-ids", required=True, type=Path, help="one evidence id per line (# comments ok)")
    export.add_argument("--output", type=Path, default=None, help="write JSON here (default stdout)")
    export.add_argument("--prompt-version", default="", help="override prompt_version string")
    export.add_argument("--derive-method", default=FINAL_V1_DERIVE_METHOD)
    export.add_argument("--include-raw", action="store_true", help="keep result.raw_gemini_video in output")
    export.set_defaults(func=run_export)
    compare = sub.add_parser("compare", help="contract validity + agreement vs baseline summary")
    compare.add_argument("--baseline", required=True, type=Path)
    compare.add_argument("--candidate", action="append", required=True, type=Path)
    compare.add_argument("--output", type=Path, default=None)
    compare.set_defaults(func=run_compare)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
