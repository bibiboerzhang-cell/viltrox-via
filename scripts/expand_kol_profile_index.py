#!/usr/bin/env python3
"""Incrementally expand the KOL vector recall index.

This script reuses the existing profile text assembly and type-classification
rules. It writes only vector_recall index entries/Qdrant points and type
metadata on vkpi_kol_profile_index_entries. It does not read or write V6 Fit
fields and does not affect KOL Pool ordering.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb
from qdrant_client.http import models as qdrant_models


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


def load_script(name: str):
    path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load script module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build = load_script("build_kol_profile_index")
type_rules = load_script("classify_kol_profile_type")


PRODUCT_QUERIES = {
    "35mm F1.2 LAB": build.PRODUCT_QUERY_TEXTS["35mm_f12_lab"],
    "85mm F1.4 Pro": """Product query profile: Viltrox AF 85mm F1.4 Pro.
Creator use cases: premium portrait, fashion editorial, wedding portrait, headshots, shallow depth of field, full-frame low-light people photography.
Desired creator profile: portrait-first creator or reviewer with strong people photography, bokeh samples, autofocus evaluation credibility, mirrorless full-frame lens experience, professional portrait audience.""",
    "135mm long telephoto portrait": """Product query profile: Viltrox 135mm telephoto portrait lens.
Creator use cases: compressed portrait, outdoor fashion, wedding ceremony distance, stage/event portraits, sports-adjacent portrait, long-lens bokeh and subject isolation.
Desired creator profile: telephoto portrait shooter, fashion or wedding creator, long focal length samples, compression and background separation discussion, advanced full-frame photography audience.""",
    "vlog / product ecommerce": """Product query profile: creator for vlog or product ecommerce shooting.
Creator use cases: talking-head vlog, tabletop product demo, product photography, ecommerce catalog, creator desk setup, compact video workflow.
Desired creator profile: product or ecommerce shooter, vlogger, small studio operator, product-lighting workflow, direct-to-camera review or commercial product content.""",
}


@dataclass
class CandidatePlan:
    docs: list[Any]
    existing_ids: set[int]
    pollution: list[tuple[int, str, str, str, str, str]]
    thin: list[tuple[int, str, str, str, str]]
    kept_ids: list[int]
    bucket_by_id: dict[int, str]

    @property
    def new_docs(self) -> list[Any]:
        return [doc for doc in self.docs if int(doc.kol_pool_id) not in self.existing_ids]


def raw_semantic_signal(dimensions: dict[str, Any]) -> bool:
    block1 = build.as_dict(dimensions.get("block1_content"))
    block4 = build.as_dict(dimensions.get("block4_specialty"))
    clusters = [item for item in build.as_list(block4.get("industry_cluster")) if build.clean_text(item, 80)]
    specialties = [
        key
        for key, value in build.as_dict(block1.get("content_specialty")).items()
        if build.clean_text(key, 60) and build.to_float(value) > 0
    ]
    product_fit = [
        key
        for key, value in build.as_dict(block4.get("product_fit")).items()
        if build.clean_text(key, 80) and build.to_float(value) > 0
    ]
    return bool(clusters or specialties or product_fit)


def final_negative(results: list[dict[str, Any]]) -> tuple[bool, str]:
    if not results:
        return False, ""
    no_product_brand: list[bool] = []
    summaries: list[str] = []
    for result in results:
        layer1 = build.as_dict(result.get("layer1_visual_content"))
        summaries.append(build.clean_text(layer1.get("content_summary") or result.get("content_summary"), 240).lower())
        product_presence = layer1.get("product_presence")
        brand_exposure = layer1.get("brand_exposure")
        product_text = (
            product_presence.lower()
            if isinstance(product_presence, str)
            else json.dumps(product_presence, ensure_ascii=False).lower()
        )
        brand_text = (
            brand_exposure.lower()
            if isinstance(brand_exposure, str)
            else json.dumps(brand_exposure, ensure_ascii=False).lower()
        )
        product_none = any(term in product_text for term in ("none", "not visible", "no product", "absent", "not present"))
        brand_none = any(term in brand_text for term in ("none", "not visible", "no brand", "absent", "not present"))
        no_product_brand.append(product_none and brand_none)
    if no_product_brand and all(no_product_brand):
        return True, "all final_v1 product_presence none + brand_exposure none"
    joined = " ".join(summaries)
    if any(term in joined for term in ("rick astley", "never gonna give you up", "music video", "mv")):
        return True, "final_v1 non-photography/music-video summary"
    return False, ""


def fetch_raw_inputs() -> tuple[list[dict[str, Any]], set[int], dict[int, list[dict[str, Any]]], set[int]]:
    with build.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.handle, p.display_name, p.platform, d.dimensions_11_json
                FROM vkpi_kol_pool p
                LEFT JOIN vkpi_kol_profile_deep d ON d.kol_pool_id = p.id
                ORDER BY p.id
                """
            )
            rows = [dict(row) for row in cur.fetchall()]
            ids = [int(row["id"]) for row in rows]

            cur.execute(
                """
                SELECT DISTINCT kol_pool_id
                FROM vkpi_kol_video_evidence
                WHERE kol_pool_id = ANY(%s::bigint[])
                  AND is_active IS NOT FALSE
                  AND COALESCE(NULLIF(title, ''), NULLIF(video_title, ''), '') <> ''
                """,
                (ids,),
            )
            evidence_ids = {int(row["kol_pool_id"]) for row in cur.fetchall()}

            cur.execute(
                """
                SELECT e.kol_pool_id, c.result
                FROM vkpi_kol_video_evidence e
                JOIN vkpi_analysis_cache c
                  ON c.target_type = 'video'
                 AND c.target_id = e.id::text
                 AND c.derive_method = 'video_analysis_final_v1'
                 AND c.status = 'ready'
                WHERE e.kol_pool_id = ANY(%s::bigint[])
                """,
                (ids,),
            )
            final_by_kol: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for row in cur.fetchall():
                result = row.get("result")
                final_by_kol[int(row["kol_pool_id"])].append(result if isinstance(result, dict) else {})

            cur.execute(
                """
                SELECT kol_pool_id
                FROM vkpi_kol_profile_index_entries
                WHERE collection_name = %s
                  AND method = %s
                  AND status = 'ready'
                """,
                (build.COLLECTION_NAME, build.METHOD),
            )
            existing_ids = {int(row["kol_pool_id"]) for row in cur.fetchall()}
    return rows, evidence_ids, final_by_kol, existing_ids


def fetch_doc_for_id(kol_pool_id: int) -> Any:
    original = list(build.TARGET_KOL_IDS)
    try:
        build.TARGET_KOL_IDS = [int(kol_pool_id)]
        return build.fetch_profile_docs()[0]
    finally:
        build.TARGET_KOL_IDS = original


def build_plan() -> CandidatePlan:
    rows, evidence_ids, final_by_kol, existing_ids = fetch_raw_inputs()
    kept_ids: list[int] = []
    bucket_by_id: dict[int, str] = {}
    pollution: list[tuple[int, str, str, str, str, str]] = []
    row_by_id: dict[int, dict[str, Any]] = {}

    for row in rows:
        kol_id = int(row["id"])
        row_by_id[kol_id] = row
        dimensions = build.as_dict(row.get("dimensions_11_json"))
        if raw_semantic_signal(dimensions):
            bucket = "raw_semantic"
        elif kol_id in evidence_ids or kol_id in final_by_kol:
            bucket = "context_only"
        else:
            continue

        reason = ""
        if kol_id == 1525:
            reason = "hard_filter_rickroll_kol_pool_id_1525"
        else:
            negative, why = final_negative(final_by_kol.get(kol_id, []))
            if negative:
                reason = why
        if reason:
            pollution.append(
                (
                    kol_id,
                    str(row.get("handle") or ""),
                    str(row.get("display_name") or ""),
                    str(row.get("platform") or ""),
                    bucket,
                    reason,
                )
            )
            continue
        kept_ids.append(kol_id)
        bucket_by_id[kol_id] = bucket

    docs: list[Any] = []
    thin: list[tuple[int, str, str, str, str]] = []
    for kol_id in kept_ids:
        row = row_by_id[kol_id]
        try:
            docs.append(fetch_doc_for_id(kol_id))
        except Exception as exc:
            thin.append(
                (
                    kol_id,
                    str(row.get("handle") or ""),
                    str(row.get("platform") or ""),
                    bucket_by_id.get(kol_id, ""),
                    str(exc)[:180],
                )
            )
    docs.sort(key=lambda doc: int(doc.kol_pool_id))
    return CandidatePlan(
        docs=docs,
        existing_ids=existing_ids,
        pollution=pollution,
        thin=thin,
        kept_ids=kept_ids,
        bucket_by_id=bucket_by_id,
    )


def estimate_docs(docs: list[Any]) -> tuple[int, Decimal]:
    tokens = build.estimate_tokens([doc.text for doc in docs])
    return tokens, build.cost_for_tokens(tokens)


def print_plan(plan: CandidatePlan) -> None:
    new_docs = plan.new_docs
    tokens, cost = estimate_docs(new_docs)
    print(f"kept_after_pollution={len(plan.kept_ids)}")
    print(f"buildable={len(plan.docs)} existing_ready={len(plan.existing_ids)} new_buildable={len(new_docs)} thin={len(plan.thin)} pollution={len(plan.pollution)}")
    print("buildable_bucket=" + json.dumps(Counter(plan.bucket_by_id[int(doc.kol_pool_id)] for doc in plan.docs), ensure_ascii=False, sort_keys=True))
    print("new_bucket=" + json.dumps(Counter(plan.bucket_by_id[int(doc.kol_pool_id)] for doc in new_docs), ensure_ascii=False, sort_keys=True))
    print("new_sufficiency=" + json.dumps(Counter(doc.sufficiency for doc in new_docs), ensure_ascii=False, sort_keys=True))
    print(f"estimated_tokens={tokens} estimated_cost_usd={cost}")
    print("new_ids=" + ",".join(str(int(doc.kol_pool_id)) for doc in new_docs))
    if plan.pollution:
        print("pollution:")
        for item in plan.pollution:
            print("\t".join(str(part) for part in item))
    if plan.thin:
        print("thin_sample:")
        for item in plan.thin[:20]:
            print("\t".join(str(part) for part in item))


def write_incremental_index(docs: list[Any]) -> tuple[int, int, Decimal, int]:
    if not docs:
        return 0, 0, Decimal("0"), 0
    openai_key = build.os.environ.get("OPENAI_API_KEY", "").strip()
    if not openai_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    original = list(build.TARGET_KOL_IDS)
    build.TARGET_KOL_IDS = [int(doc.kol_pool_id) for doc in docs]
    try:
        qclient = build.qdrant_client()
        build.ensure_collection(qclient)
        oclient = build.OpenAI(api_key=openai_key)
        with build.connect() as conn:
            run_id = build.create_run(conn, docs)
            conn.commit()
            try:
                vectors, tokens, cost = build.embed_texts(oclient, [doc.text for doc in docs])
                if len(vectors) != len(docs):
                    raise RuntimeError(f"embedding count mismatch: vectors={len(vectors)} docs={len(docs)}")
                points = []
                for doc, vector in zip(docs, vectors, strict=True):
                    if len(vector) != build.VECTOR_SIZE:
                        raise RuntimeError(f"vector size mismatch for {doc.kol_pool_id}: {len(vector)}")
                    points.append(
                        qdrant_models.PointStruct(
                            id=build.stable_point_id(doc.kol_pool_id, doc.text_hash),
                            vector=vector,
                            payload={
                                "method": build.METHOD,
                                "kol_pool_id": doc.kol_pool_id,
                                "platform": doc.platform,
                                "handle": doc.handle,
                                "display_name": doc.display_name,
                                "source_fields": doc.source_fields,
                                "profile_text_hash": doc.text_hash,
                                "sufficiency": doc.sufficiency,
                            },
                        )
                    )
                qclient.upsert(collection_name=build.COLLECTION_NAME, points=points, wait=True)
                build.upsert_metadata(conn, run_id, docs, tokens, cost)
                conn.commit()
            except Exception as exc:
                build.mark_run_failed(conn, run_id, str(exc))
                conn.commit()
                raise
        qdrant_count = qclient.count(collection_name=build.COLLECTION_NAME, exact=True).count
        return len(docs), int(tokens), cost, int(qdrant_count)
    finally:
        build.TARGET_KOL_IDS = original


def fetch_entry_rows(kol_pool_ids: list[int]) -> list[dict[str, Any]]:
    if not kol_pool_ids:
        return []
    with type_rules.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id AS entry_id,
                       e.kol_pool_id,
                       p.handle,
                       p.display_name,
                       p.platform,
                       e.profile_text,
                       e.source_fields_json
                FROM vkpi_kol_profile_index_entries e
                JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
                WHERE e.collection_name = %s
                  AND e.status = 'ready'
                  AND e.method = 'vector_recall'
                  AND e.kol_pool_id = ANY(%s::bigint[])
                ORDER BY e.kol_pool_id
                """,
                (build.COLLECTION_NAME, kol_pool_ids),
            )
            return [dict(row) for row in cur.fetchall()]


def classify_docs(docs: list[Any]) -> tuple[int, list[Any]]:
    rows = fetch_entry_rows([int(doc.kol_pool_id) for doc in docs])
    results = [type_rules.classify(row) for row in rows]
    updated = type_rules.write_results(results)
    return updated, results


def entry_briefs(kol_pool_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not kol_pool_ids:
        return {}
    with build.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.kol_pool_id,
                       p.handle,
                       p.display_name,
                       p.platform,
                       e.profile_type,
                       e.creator_type_score,
                       e.reviewer_type_score
                FROM vkpi_kol_profile_index_entries e
                JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
                WHERE e.collection_name = %s
                  AND e.method = %s
                  AND e.status = 'ready'
                  AND e.kol_pool_id = ANY(%s::bigint[])
                """,
                (build.COLLECTION_NAME, build.METHOD, kol_pool_ids),
            )
            return {int(row["kol_pool_id"]): dict(row) for row in cur.fetchall()}


def recall_validation() -> tuple[int, Decimal, dict[str, list[tuple[int, str, str, str, float]]]]:
    openai_key = build.os.environ.get("OPENAI_API_KEY", "").strip()
    if not openai_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    qclient = build.qdrant_client()
    build.ensure_collection(qclient)
    oclient = build.OpenAI(api_key=openai_key)
    vectors, tokens, cost = build.embed_texts(oclient, list(PRODUCT_QUERIES.values()))
    report: dict[str, list[tuple[int, str, str, str, float]]] = {}
    for label, vector in zip(PRODUCT_QUERIES.keys(), vectors, strict=True):
        hits = build.search_points(qclient, vector, 10)
        ids: list[int] = []
        raw_hits: list[tuple[int, float]] = []
        for point in hits:
            payload = dict(getattr(point, "payload", None) or {})
            kol_id = int(payload.get("kol_pool_id") or 0)
            if kol_id:
                ids.append(kol_id)
                raw_hits.append((kol_id, float(getattr(point, "score", 0.0) or 0.0)))
        briefs = entry_briefs(ids)
        rows = []
        for kol_id, score in raw_hits[:5]:
            brief = briefs.get(kol_id, {})
            rows.append(
                (
                    kol_id,
                    str(brief.get("handle") or ""),
                    str(brief.get("display_name") or ""),
                    str(brief.get("profile_type") or ""),
                    score,
                )
            )
        report[label] = rows
    return int(tokens), cost, report


def target_rank_report(query_text: str, handles: set[str]) -> list[str]:
    openai_key = build.os.environ.get("OPENAI_API_KEY", "").strip()
    if not openai_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    qclient = build.qdrant_client()
    oclient = build.OpenAI(api_key=openai_key)
    vectors, _, _ = build.embed_texts(oclient, [query_text])
    hits = build.search_points(qclient, vectors[0], 200)
    ids: list[int] = []
    scored: list[tuple[int, float]] = []
    for point in hits:
        payload = dict(getattr(point, "payload", None) or {})
        kol_id = int(payload.get("kol_pool_id") or 0)
        if kol_id:
            ids.append(kol_id)
            scored.append((kol_id, float(getattr(point, "score", 0.0) or 0.0)))
    briefs = entry_briefs(ids)
    output: list[str] = []
    for rank, (kol_id, score) in enumerate(scored, start=1):
        brief = briefs.get(kol_id, {})
        handle = str(brief.get("handle") or "").lower()
        if handle in handles:
            output.append(
                f"{handle}\trank={rank}\tkol_pool_id={kol_id}\tscore={score:.6f}\ttype={brief.get('profile_type')}"
            )
    return output


def sample_docs(docs: list[Any], type_results: list[Any], limit: int = 5) -> list[str]:
    type_by_id = {int(item.kol_pool_id): item for item in type_results}
    chosen = docs[:limit]
    output: list[str] = []
    for doc in chosen:
        result = type_by_id.get(int(doc.kol_pool_id))
        output.append(
            "\n".join(
                [
                    f"kol_pool_id={doc.kol_pool_id} handle={doc.handle} platform={doc.platform} sufficiency={doc.sufficiency}",
                    (
                        "type="
                        + (str(result.profile_type) if result else "unknown")
                        + (f" creator={result.creator_type_score} reviewer={result.reviewer_type_score}" if result else "")
                    ),
                    doc.text[:900],
                ]
            )
        )
    return output


def print_recall_report(report: dict[str, list[tuple[int, str, str, str, float]]]) -> None:
    for label, rows in report.items():
        print(f"\n=== {label} Top 5 ===")
        for idx, (kol_id, handle, display, profile_type, score) in enumerate(rows, start=1):
            print(f"{idx:02d}. kol_pool_id={kol_id} handle={handle} display={display} type={profile_type} vector_score={score:.6f}")


def current_totals() -> tuple[int, int, dict[str, int]]:
    with build.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM vkpi_kol_profile_index_entries
                WHERE collection_name = %s
                  AND method = %s
                  AND status = 'ready'
                """,
                (build.COLLECTION_NAME, build.METHOD),
            )
            entries = int(cur.fetchone()["n"])
            cur.execute(
                """
                SELECT COALESCE(profile_type, 'untyped') AS profile_type, COUNT(*) AS n
                FROM vkpi_kol_profile_index_entries
                WHERE collection_name = %s
                  AND method = %s
                  AND status = 'ready'
                GROUP BY COALESCE(profile_type, 'untyped')
                """,
                (build.COLLECTION_NAME, build.METHOD),
            )
            dist = {str(row["profile_type"]): int(row["n"]) for row in cur.fetchall()}
    qclient = build.qdrant_client()
    qdrant_count = int(qclient.count(collection_name=build.COLLECTION_NAME, exact=True).count)
    return entries, qdrant_count, dist


def command_plan(_: argparse.Namespace) -> int:
    build.load_dotenv()
    plan = build_plan()
    print_plan(plan)
    return 0


def command_write_and_validate(_: argparse.Namespace) -> int:
    build.load_dotenv()
    plan = build_plan()
    print_plan(plan)
    docs = plan.new_docs
    wrote, tokens, cost, qdrant_count = write_incremental_index(docs)
    updated, type_results = classify_docs(docs)
    entries, current_qdrant_count, distribution = current_totals()
    query_tokens, query_cost, recall_report = recall_validation()
    ranks = target_rank_report(
        build.PRODUCT_QUERY_TEXTS["35mm_f12_lab"],
        {"eliinfante", "jaysoundo", "zwadephoto", "michaelziegann", "editorskeys", "thecamerastoretv"},
    )

    print("\n=== write summary ===")
    print(f"embedded_new={wrote} embedding_tokens={tokens} embedding_cost_usd={cost} qdrant_count_after_write={qdrant_count}")
    print(f"classified_new={updated}")
    print(f"entries_total={entries} qdrant_total={current_qdrant_count}")
    print("classification_total_distribution=" + json.dumps(distribution, ensure_ascii=False, sort_keys=True))
    print("new_classification_distribution=" + json.dumps(Counter(item.profile_type for item in type_results), ensure_ascii=False, sort_keys=True))
    print(f"validation_query_tokens={query_tokens} validation_query_cost_usd={query_cost}")
    print_recall_report(recall_report)

    print("\n=== 35mm tracked handles rank after expansion ===")
    for line in ranks:
        print(line)

    print("\n=== new profile samples ===")
    for sample in sample_docs(docs, type_results, limit=5):
        print("---")
        print(sample)
    return 0


def command_validate(_: argparse.Namespace) -> int:
    build.load_dotenv()
    entries, qdrant_count, distribution = current_totals()
    tokens, cost, recall_report = recall_validation()
    ranks = target_rank_report(
        build.PRODUCT_QUERY_TEXTS["35mm_f12_lab"],
        {"eliinfante", "jaysoundo", "zwadephoto", "michaelziegann", "editorskeys", "thecamerastoretv"},
    )
    print(f"entries_total={entries} qdrant_total={qdrant_count}")
    print("classification_total_distribution=" + json.dumps(distribution, ensure_ascii=False, sort_keys=True))
    print(f"validation_query_tokens={tokens} validation_query_cost_usd={cost}")
    print_recall_report(recall_report)
    print("\n=== 35mm tracked handles rank ===")
    for line in ranks:
        print(line)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Expand KOL vector recall index incrementally.")
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.set_defaults(func=command_plan)
    write_parser = sub.add_parser("write-and-validate")
    write_parser.set_defaults(func=command_write_and_validate)
    validate_parser = sub.add_parser("validate")
    validate_parser.set_defaults(func=command_validate)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
