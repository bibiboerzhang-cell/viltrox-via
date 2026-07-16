#!/usr/bin/env python3
"""Build and validate the KOL profile vector recall index.

This script writes only vector-recall metadata plus Qdrant points. It does not
read or write V6 Fit score fields and does not alter KOL Pool ordering.
"""

from __future__ import annotations

from stdout_utils import out

import argparse
import hashlib
import json
import os
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from openai import OpenAI
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
MIGRATION_NAME = "099_vkpi_kol_profile_index.sql"
COLLECTION_NAME = "vkpi_kol_profile_index_v1"
METHOD = "vector_recall"
EMBEDDING_MODEL = "text-embedding-3-small"
VECTOR_SIZE = 1536
QDRANT_LOCAL_PATH = PROJECT_ROOT / "runtime" / "vkpi_qdrant"
OPENAI_EMBEDDING_PRICE_PER_1M = Decimal("0.02")
BATCH_SIZE = 100

TARGET_KOL_IDS = [
    1519, 3647, 3652, 3659, 3662, 3669, 3672, 3677, 4235, 3681, 3686, 3690,
    3701, 3702, 3711, 3720, 3725, 1543, 3733, 1567, 3755, 1582, 1585, 1538,
    3771, 3785, 3796, 3806, 3814, 3818, 1515, 3894, 3335, 3897, 1509, 1503,
    3902, 1571, 3910, 1576, 1510, 1529, 3945, 1537, 3956, 1504, 3961, 1506,
    1511, 3965, 3970, 1516, 3972, 1507, 3976, 3979, 3983, 3989, 1562, 1522,
    3995, 4001, 1547, 1514, 4003, 4006, 4012, 4015, 1565, 1532, 1521, 4035,
    4037, 4038, 1549, 4040, 4043, 4044, 4046, 4051, 1548, 4055, 4056, 4063,
    1508, 4078, 1587, 4085, 4094, 1540, 4098, 1524, 1569, 4110, 4111, 4116,
    1551, 4122, 4123, 1523, 1530, 4132, 1531, 1589, 4143, 4147, 4154, 1577,
    4164, 4168, 4172, 4173, 4174, 4183, 4185, 4186, 4191, 1517, 1513, 4204,
    4205, 4212, 4213, 4227, 4230,
]

PRODUCT_QUERY_TEXTS = {
    "35mm_f12_lab": """Product query profile: Viltrox AF 35mm F1.2 LAB.
Creator use cases: environmental portrait, street photography, documentary storytelling, wedding and engagement photography, low-light portrait, editorial fashion, hybrid photo and video, premium full-frame storyteller.
Desired creator profile: high quality people and scene storytelling, strong portrait or street portfolio, visible lens or camera review credibility, Viltrox or mirrorless lens experience, cinematic natural-light style, premium full-frame audience.""",
    "35mm_f17_air": """Product query profile: Viltrox AF 35mm F1.7 AIR.
Creator use cases: lightweight everyday carry, travel photography, vlog and creator kit, casual portrait, street walkaround, budget APS-C creator, compact Sony Fuji Nikon setup.
Desired creator profile: mobile everyday creator, travel or street shooter, beginner-friendly gear reviewer, light kit advocate, practical budget lens audience, casual hybrid photo and video workflow.""",
}


@dataclass
class ProfileDoc:
    kol_pool_id: int
    platform: str
    handle: str
    display_name: str
    text: str
    text_hash: str
    sufficiency: str
    source_fields: dict[str, Any]


def load_dotenv() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip() or "postgresql://viltrox2@127.0.0.1:54329/viltrox2"


def clean_text(value: Any, limit: int = 7000) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalise_sku(value: str) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def catalog_match(product_id: str, catalog_skus: set[str]) -> str:
    norm = normalise_sku(product_id)
    if not norm:
        return ""
    for sku in catalog_skus:
        sku_norm = normalise_sku(sku)
        if norm == sku_norm or norm in sku_norm or sku_norm in norm:
            return sku
    return ""


def short_json_signal(value: Any, limit: int = 360) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return clean_text(value, limit)
    return clean_text(json.dumps(value, ensure_ascii=False, sort_keys=True), limit)


def estimate_tokens(texts: list[str]) -> int:
    return int(sum(max(1, len(text)) for text in texts) / 3.0 * 1.15)


def cost_for_tokens(tokens: int) -> Decimal:
    return (Decimal(tokens) * OPENAI_EMBEDDING_PRICE_PER_1M / Decimal(1_000_000)).quantize(Decimal("0.00000001"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_point_id(kol_pool_id: int, text_hash: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{COLLECTION_NAME}:{kol_pool_id}:{text_hash}"))


def connect() -> psycopg.Connection:
    return psycopg.connect(database_url(), row_factory=dict_row)


def apply_migration() -> None:
    migration_path = PROJECT_ROOT / "migrations" / MIGRATION_NAME
    sql = migration_path.read_text(encoding="utf-8")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version_key TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('viltrox_schema_migrations'))")
            cur.execute("SELECT 1 FROM schema_migrations WHERE version_key = %s", (MIGRATION_NAME,))
            if cur.fetchone():
                out(f"migration already applied: {MIGRATION_NAME}")
                return
            cur.execute(sql)
            cur.execute("INSERT INTO schema_migrations(version_key) VALUES (%s)", (MIGRATION_NAME,))
        conn.commit()
    out(f"migration applied: {MIGRATION_NAME}")


def fetch_profile_docs() -> list[ProfileDoc]:
    ids = TARGET_KOL_IDS
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT sku FROM vkpi_products WHERE COALESCE(sku, '') <> ''")
            catalog_skus = {str(row["sku"]) for row in cur.fetchall()}

            cur.execute(
                """
                SELECT p.id, p.platform, p.handle, p.display_name, d.dimensions_11_json
                FROM vkpi_kol_pool p
                LEFT JOIN vkpi_kol_profile_deep d ON d.kol_pool_id = p.id
                WHERE p.id = ANY(%s::bigint[])
                ORDER BY array_position(%s::bigint[], p.id)
                """,
                (ids, ids),
            )
            kol_rows = cur.fetchall()

            cur.execute(
                """
                SELECT e.kol_pool_id,
                       COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, ''), NULLIF(e.content_url, '')) AS title,
                       NULLIF(e.channel_name, '') AS channel_name
                FROM vkpi_kol_video_evidence e
                WHERE e.kol_pool_id = ANY(%s::bigint[])
                  AND e.is_active IS NOT FALSE
                  AND COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, ''), '') <> ''
                ORDER BY e.kol_pool_id, e.posted_at DESC NULLS LAST, e.id DESC
                """,
                (ids,),
            )
            evidence_by_kol: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for row in cur.fetchall():
                evidence_by_kol[int(row["kol_pool_id"])].append(dict(row))

            cur.execute(
                """
                SELECT e.kol_pool_id,
                       COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, ''), NULLIF(e.content_url, '')) AS title,
                       c.result
                FROM vkpi_kol_video_evidence e
                JOIN vkpi_analysis_cache c
                  ON c.target_type = 'video'
                 AND c.target_id = e.id::text
                 AND c.derive_method = 'video_analysis_final_v1'
                 AND c.status = 'ready'
                WHERE e.kol_pool_id = ANY(%s::bigint[])
                ORDER BY e.kol_pool_id, c.updated_at DESC, c.id DESC
                """,
                (ids,),
            )
            final_by_kol: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for row in cur.fetchall():
                final_by_kol[int(row["kol_pool_id"])].append(dict(row))

    found_ids = {int(row["id"]) for row in kol_rows}
    missing = [kol_id for kol_id in ids if kol_id not in found_ids]
    if missing:
        raise RuntimeError(f"missing KOL ids in vkpi_kol_pool: {missing}")

    docs: list[ProfileDoc] = []
    thin_skipped: list[int] = []
    for row in kol_rows:
        kol_id = int(row["id"])
        if kol_id == 1525:
            raise RuntimeError("hard pollution id 1525 must not be indexed")
        platform = clean_text(row.get("platform"), 80)
        handle = clean_text(row.get("handle"), 120)
        display = clean_text(row.get("display_name"), 160)
        dimensions = as_dict(row.get("dimensions_11_json"))
        block1 = as_dict(dimensions.get("block1_content"))
        block4 = as_dict(dimensions.get("block4_specialty"))
        confidence = as_dict(dimensions.get("confidence"))
        block1_conf = as_dict(block1.get("confidence"))
        block4_conf = as_dict(block4.get("confidence"))

        source_fields: dict[str, Any] = {
            "method": METHOD,
            "cohort": "clean_positive_adequate_strong_v1",
            "profile_deep": {},
            "final_v1": {},
            "evidence": {},
            "excluded_fields": ["viltrox_fit_score", "viltrox_fit_reason", "raw_platform_data", "followers", "engagement_rate"],
        }
        lines = [
            "KOL profile text for vector recall only.",
            f"Platform: {platform or 'unknown'}",
            f"Handle: {handle or 'unknown'}",
            f"Display/channel: {display or handle or 'unknown'}",
        ]

        clusters = [clean_text(item, 80) for item in as_list(block4.get("industry_cluster")) if clean_text(item, 80)]
        if clusters and to_float(block4_conf.get("industry_cluster")) >= 0.65:
            lines.append("Content clusters: " + ", ".join(clusters[:8]))
            source_fields["profile_deep"]["industry_cluster"] = clusters[:8]

        specialties = as_dict(block1.get("content_specialty"))
        specialty_items = []
        if specialties and to_float(block1_conf.get("content_specialty")) >= 0.65:
            for key, value in sorted(specialties.items(), key=lambda item: to_float(item[1]), reverse=True):
                label = clean_text(key, 60)
                if label and to_float(value) > 0:
                    specialty_items.append(f"{label}({to_float(value):.0f})")
        if specialty_items:
            lines.append("Content specialties: " + ", ".join(specialty_items[:8]))
            source_fields["profile_deep"]["content_specialty"] = specialty_items[:8]

        product_fit = as_dict(block4.get("product_fit"))
        product_conf = as_dict(block4.get("product_fit_confidence"))
        product_items = []
        catalog_alias_gaps = []
        for product_id, score in sorted(product_fit.items(), key=lambda item: to_float(item[1]), reverse=True):
            label = clean_text(product_id, 80)
            if not label:
                continue
            conf = to_float(product_conf.get(product_id), to_float(block4_conf.get("product_fit")))
            if conf < 0.75:
                continue
            matched_sku = catalog_match(label, catalog_skus)
            if not matched_sku:
                catalog_alias_gaps.append(label)
                continue
            product_items.append(f"{label}({to_float(score):.0f})")
        if product_items:
            lines.append("High-confidence Viltrox/product fit signals: " + ", ".join(product_items[:10]))
            source_fields["profile_deep"]["product_fit"] = product_items[:10]
        if catalog_alias_gaps:
            source_fields["profile_deep"]["catalog_alias_gaps"] = catalog_alias_gaps[:10]

        final_rows = final_by_kol.get(kol_id, [])
        final_summaries = []
        final_product_signals = []
        for final_row in final_rows[:4]:
            result = as_dict(final_row.get("result"))
            layer1 = as_dict(result.get("layer1_visual_content"))
            summary = clean_text(layer1.get("content_summary") or result.get("content_summary"), 900)
            if summary and summary not in final_summaries:
                final_summaries.append(summary)
            product_presence = short_json_signal(layer1.get("product_presence"), 300)
            brand_exposure = short_json_signal(layer1.get("brand_exposure"), 300)
            if product_presence:
                final_product_signals.append("product_presence: " + product_presence)
            if brand_exposure:
                final_product_signals.append("brand_exposure: " + brand_exposure)
        for idx, summary in enumerate(final_summaries[:3], start=1):
            lines.append(f"Final video analysis summary {idx}: {summary}")
        if final_product_signals:
            lines.append("Final video analysis product/brand signals: " + " | ".join(final_product_signals[:4]))
        source_fields["final_v1"] = {
            "summary_count": len(final_summaries),
            "analysis_row_count": len(final_rows),
        }

        evidence_rows = evidence_by_kol.get(kol_id, [])
        titles = []
        channel_names = []
        for ev in evidence_rows:
            title = clean_text(ev.get("title"), 220)
            channel = clean_text(ev.get("channel_name"), 120)
            if title and title not in titles:
                titles.append(title)
            if channel and channel not in channel_names:
                channel_names.append(channel)
        if channel_names and not display:
            lines.append("Observed channel names: " + ", ".join(channel_names[:3]))
        if titles:
            lines.append("Evidence titles: " + " | ".join(titles[:6]))
        source_fields["evidence"] = {
            "title_count": len(titles),
            "channel_name_count": len(channel_names),
        }

        text = clean_text("\n".join(line for line in lines if line), 7000)
        if not text or len(text) < 120:
            # 2026-07-02:太薄的画像逐条跳过而非整体中止 —— 一条烂数据不该拖死全量索引;
            # 薄的等 enrichment 补厚后下次重建自然纳入。
            thin_skipped.append(int(kol_id))
            continue
        semantic_signal_count = int(bool(clusters)) + int(bool(specialty_items)) + int(bool(product_items))
        sufficiency = "strong" if len(final_summaries) >= 2 and semantic_signal_count >= 1 else "adequate"
        source_fields["selection"] = {
            "sufficiency": sufficiency,
            "semantic_signal_count": semantic_signal_count,
            "profile_text_chars": len(text),
        }
        docs.append(
            ProfileDoc(
                kol_pool_id=kol_id,
                platform=platform,
                handle=handle,
                display_name=display,
                text=text,
                text_hash=sha256_text(text),
                sufficiency=sufficiency,
                source_fields=source_fields,
            )
        )

    if len(docs) + len(thin_skipped) != len(TARGET_KOL_IDS):
        raise RuntimeError(f"expected {len(TARGET_KOL_IDS)} docs, got {len(docs)} (+{len(thin_skipped)} thin-skipped)")
    if thin_skipped:
        out(f"skipped {len(thin_skipped)} thin profiles: {thin_skipped[:20]}...")
    return docs


def qdrant_client() -> QdrantClient:
    url = os.environ.get("QDRANT_URL", "").strip()
    api_key = os.environ.get("QDRANT_API_KEY", "").strip() or None
    if url:
        return QdrantClient(url=url, api_key=api_key)
    QDRANT_LOCAL_PATH.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(QDRANT_LOCAL_PATH))


def vector_size_from_collection(info: Any) -> int:
    vectors = getattr(getattr(getattr(info, "config", None), "params", None), "vectors", None)
    if isinstance(vectors, dict):
        first = next(iter(vectors.values()), None)
        return int(getattr(first, "size", 0) or 0)
    return int(getattr(vectors, "size", 0) or 0)


def ensure_collection(client: QdrantClient) -> None:
    exists = client.collection_exists(COLLECTION_NAME)
    if not exists:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=qdrant_models.VectorParams(size=VECTOR_SIZE, distance=qdrant_models.Distance.COSINE),
        )
        out(f"qdrant collection created: {COLLECTION_NAME} size={VECTOR_SIZE} distance=cosine")
        return
    info = client.get_collection(COLLECTION_NAME)
    size = vector_size_from_collection(info)
    if size and size != VECTOR_SIZE:
        raise RuntimeError(f"qdrant collection {COLLECTION_NAME} has vector size {size}, expected {VECTOR_SIZE}")
    out(f"qdrant collection exists: {COLLECTION_NAME} size={size or VECTOR_SIZE} distance=cosine")


def embed_texts(client: OpenAI, texts: list[str]) -> tuple[list[list[float]], int, Decimal]:
    vectors: list[list[float]] = []
    total_tokens = 0
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        for item in resp.data:
            vectors.append([float(value) for value in item.embedding])
        usage = getattr(resp, "usage", None)
        total_tokens += int(getattr(usage, "prompt_tokens", 0) or getattr(usage, "total_tokens", 0) or 0)
    return vectors, total_tokens, cost_for_tokens(total_tokens)


def create_run(conn: psycopg.Connection, docs: list[ProfileDoc]) -> int:
    texts = [doc.text for doc in docs]
    estimated_tokens = estimate_tokens(texts)
    estimated_cost = cost_for_tokens(estimated_tokens)
    run_uid = f"kol-index-v1-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO vkpi_kol_profile_index_runs (
              run_uid, collection_name, embedding_model, vector_size, method, status,
              target_count, estimated_tokens, estimated_cost_usd, metadata_json
            )
            VALUES (%s, %s, %s, %s, %s, 'running', %s, %s, %s, %s)
            RETURNING id
            """,
            (
                run_uid,
                COLLECTION_NAME,
                EMBEDDING_MODEL,
                VECTOR_SIZE,
                METHOD,
                len(docs),
                estimated_tokens,
                estimated_cost,
                Jsonb({"target_kol_ids": TARGET_KOL_IDS, "qdrant_local_path": str(QDRANT_LOCAL_PATH)}),
            ),
        )
        return int(cur.fetchone()["id"])


def upsert_metadata(conn: psycopg.Connection, run_id: int, docs: list[ProfileDoc], tokens: int, cost: Decimal) -> None:
    with conn.cursor() as cur:
        for doc in docs:
            point_id = stable_point_id(doc.kol_pool_id, doc.text_hash)
            cur.execute(
                """
                INSERT INTO vkpi_kol_profile_index_entries (
                  run_id, kol_pool_id, collection_name, qdrant_point_id, embedding_model,
                  vector_size, method, profile_text_hash, profile_text, source_fields_json,
                  sufficiency, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ready')
                ON CONFLICT (collection_name, kol_pool_id, embedding_model, profile_text_hash)
                DO UPDATE SET
                  run_id = EXCLUDED.run_id,
                  qdrant_point_id = EXCLUDED.qdrant_point_id,
                  profile_text = EXCLUDED.profile_text,
                  source_fields_json = EXCLUDED.source_fields_json,
                  sufficiency = EXCLUDED.sufficiency,
                  status = 'ready',
                  updated_at = NOW()
                """,
                (
                    run_id,
                    doc.kol_pool_id,
                    COLLECTION_NAME,
                    point_id,
                    EMBEDDING_MODEL,
                    VECTOR_SIZE,
                    METHOD,
                    doc.text_hash,
                    doc.text,
                    Jsonb(doc.source_fields),
                    doc.sufficiency,
                ),
            )
        cur.execute(
            """
            UPDATE vkpi_kol_profile_index_runs
            SET status = 'ready',
                embedded_count = %s,
                actual_tokens = %s,
                actual_cost_usd = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (len(docs), tokens, cost, run_id),
        )


def mark_run_failed(conn: psycopg.Connection, run_id: int, error: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE vkpi_kol_profile_index_runs
            SET status = 'failed',
                metadata_json = metadata_json || %s::jsonb,
                updated_at = NOW()
            WHERE id = %s
            """,
            (json.dumps({"error": error[:500]}, ensure_ascii=False), run_id),
        )


def build_index() -> None:
    docs = fetch_profile_docs()
    texts = [doc.text for doc in docs]
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not openai_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    qclient = qdrant_client()
    ensure_collection(qclient)
    oclient = OpenAI(api_key=openai_key)

    with connect() as conn:
        run_id = create_run(conn, docs)
        conn.commit()
        try:
            vectors, tokens, cost = embed_texts(oclient, texts)
            if len(vectors) != len(docs):
                raise RuntimeError(f"embedding count mismatch: vectors={len(vectors)} docs={len(docs)}")
            points = []
            for doc, vector in zip(docs, vectors, strict=True):
                if len(vector) != VECTOR_SIZE:
                    raise RuntimeError(f"vector size mismatch for {doc.kol_pool_id}: {len(vector)}")
                points.append(
                    qdrant_models.PointStruct(
                        id=stable_point_id(doc.kol_pool_id, doc.text_hash),
                        vector=vector,
                        payload={
                            "method": METHOD,
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
            qclient.upsert(collection_name=COLLECTION_NAME, points=points, wait=True)
            upsert_metadata(conn, run_id, docs, tokens, cost)
            conn.commit()
        except Exception as exc:
            mark_run_failed(conn, run_id, str(exc))
            conn.commit()
            raise

    count = qclient.count(collection_name=COLLECTION_NAME, exact=True).count
    out(f"build complete: run_id={run_id} docs={len(docs)} qdrant_count={count} tokens={tokens} cost_usd={cost}")


def search_points(client: QdrantClient, query_vector: list[float], limit: int) -> list[Any]:
    query_filter = qdrant_models.Filter(
        must=[
            qdrant_models.FieldCondition(
                key="method",
                match=qdrant_models.MatchValue(value=METHOD),
            )
        ]
    )
    if hasattr(client, "query_points"):
        response = client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        return list(getattr(response, "points", []) or [])
    return client.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_vector,
        query_filter=query_filter,
        limit=limit,
        with_payload=True,
    )


def recall() -> None:
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not openai_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    qclient = qdrant_client()
    ensure_collection(qclient)
    oclient = OpenAI(api_key=openai_key)
    query_texts = [PRODUCT_QUERY_TEXTS["35mm_f12_lab"], PRODUCT_QUERY_TEXTS["35mm_f17_air"]]
    vectors, tokens, cost = embed_texts(oclient, query_texts)
    labels = ["35mm F1.2 LAB", "35mm F1.7 AIR"]
    out(f"recall query embeddings: queries={len(query_texts)} tokens={tokens} cost_usd={cost}")
    for label, vector in zip(labels, vectors, strict=True):
        out(f"\n=== {label} Top 10 ({METHOD}) ===")
        hits = search_points(qclient, vector, 10)
        for idx, hit in enumerate(hits, start=1):
            payload = dict(getattr(hit, "payload", None) or {})
            score = float(getattr(hit, "score", 0.0) or 0.0)
            out(
                f"{idx:02d}. kol_pool_id={payload.get('kol_pool_id')} "
                f"handle={payload.get('handle') or ''} "
                f"display={payload.get('display_name') or ''} "
                f"platform={payload.get('platform') or ''} "
                f"vector_score={score:.6f}"
            )


def show_stats() -> None:
    docs = fetch_profile_docs()
    texts = [doc.text for doc in docs]
    by_sufficiency = defaultdict(int)
    for doc in docs:
        by_sufficiency[doc.sufficiency] += 1
    out(f"target_docs={len(docs)}")
    out("sufficiency=" + json.dumps(dict(sorted(by_sufficiency.items())), ensure_ascii=False))
    out(f"chars={sum(len(text) for text in texts)} estimated_tokens={estimate_tokens(texts)} estimated_cost_usd={cost_for_tokens(estimate_tokens(texts))}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build KOL vector recall index.")
    parser.add_argument("command", choices=["apply-migration", "build", "recall", "stats"])
    args = parser.parse_args()
    load_dotenv()
    if args.command == "apply-migration":
        apply_migration()
    elif args.command == "build":
        build_index()
    elif args.command == "recall":
        recall()
    elif args.command == "stats":
        show_stats()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        out(f"ERROR: {exc}", file=sys.stderr)
        raise
