"""
backend/app/services/vkpi/pillars.py

P1.5: Content pillar classification service.

Classifies posts into 1 primary + 0-2 secondary pillars across:
  - Layer 1 (generic, fallback)
  - Layer 2 (photography/cinema)
  - Layer 3 (data-driven, team adds later)

Uses llm_gateway.invoke() (4 provider fallback).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi import llm_gateway


PROMPT_VERSION = "v1.0"


PROMPT_TEMPLATE = """You are categorizing content for Viltrox, a camera lens brand.

Post info:
  Platform: {platform}
  Post type: {post_type}
  Title: {title}
  Description: {description}
  Hashtags: {hashtags}
  Duration: {duration_seconds}

Available pillars:

Generic (Layer 1, fallback):
  lifestyle, education, entertainment, product_review,
  tutorial, news_commentary, other

Photography/Cinema (Layer 2, prefer when applicable):
  lens_review, cinema_bts, shooting_tutorial, vlog, gear_comparison,
  lighting, interview, event_coverage, test_footage, color_grading

Choose:
  - 1 primary pillar (required)
  - 0-2 secondary pillars (optional, only if clearly relevant)

Prefer Layer 2 (photography/cinema) when content matches.
Fall back to Layer 1 only when Layer 2 doesn't fit.

Respond in valid JSON only:
{{
  "primary_pillar": "lens_review",
  "primary_confidence": 0.92,
  "secondary_pillars": ["shooting_tutorial"],
  "secondary_confidences": [0.65],
  "reasoning": "Brief 1-sentence"
}}

If post too vague, use "other" with low confidence."""


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


PILLAR_SEEDS = [
    ("lifestyle", "Lifestyle", 1, 100, "Daily life, travel, personal moments"),
    ("education", "Education", 1, 110, "Academic, instructional, informative"),
    ("entertainment", "Entertainment", 1, 120, "Pure entertainment without specific niche"),
    ("product_review", "Product Review", 1, 130, "General product review"),
    ("tutorial", "Tutorial", 1, 140, "How-to content"),
    ("news_commentary", "News/Commentary", 1, 150, "News, opinion, commentary"),
    ("other", "Other", 1, 199, "Does not fit any specific category"),
    ("lens_review", "Lens Review", 2, 200, "Specific lens unboxing, hands-on, review"),
    ("cinema_bts", "Cinema Behind-Scenes", 2, 210, "Film/cinema behind the scenes"),
    ("shooting_tutorial", "Shooting Tutorial", 2, 220, "How-to: shooting techniques"),
    ("vlog", "Vlog", 2, 230, "Personal vlog with camera content"),
    ("gear_comparison", "Gear Comparison", 2, 240, "Comparing multiple cameras/lenses"),
    ("lighting", "Lighting", 2, 250, "Lighting setup, technique, gear"),
    ("interview", "Interview", 2, 260, "Interview content"),
    ("event_coverage", "Event Coverage", 2, 270, "Photography/cinema event coverage"),
    ("test_footage", "Test Footage", 2, 280, "Camera/lens test footage, sample shots"),
    ("color_grading", "Color Grading", 2, 290, "Color grading tutorials, looks, LUTs"),
]


def ensure_vkpi_pillar_schema() -> None:
    """Create P1.5 pillar tables and seed defaults when migrations are absent."""
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_pillars (
          id SERIAL PRIMARY KEY,
          pillar_key VARCHAR(50) UNIQUE NOT NULL,
          display_name VARCHAR(100) NOT NULL,
          description TEXT,
          layer SMALLINT NOT NULL,
          is_active BOOLEAN DEFAULT TRUE,
          display_order INT DEFAULT 0,
          created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_post_pillars (
          id BIGSERIAL PRIMARY KEY,
          post_id BIGINT NOT NULL,
          post_table VARCHAR(50) NOT NULL,
          pillar_id INT NOT NULL REFERENCES vkpi_pillars(id),
          is_primary BOOLEAN DEFAULT FALSE,
          confidence NUMERIC(3,2),
          llm_provider VARCHAR(50),
          llm_model VARCHAR(100),
          prompt_version VARCHAR(20),
          classified_at TIMESTAMPTZ DEFAULT NOW(),
          CONSTRAINT vkpi_post_pillar_uniq UNIQUE (post_id, post_table, pillar_id, prompt_version)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_post_pillars_post ON vkpi_post_pillars(post_id, post_table)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_post_pillars_pillar ON vkpi_post_pillars(pillar_id, classified_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_post_pillars_primary ON vkpi_post_pillars(post_id, post_table) WHERE is_primary")
    for key, name, layer, order, description in PILLAR_SEEDS:
        conn.execute(
            """
            INSERT INTO vkpi_pillars (pillar_key, display_name, layer, display_order, description)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (pillar_key) DO NOTHING
            """,
            (key, name, layer, order, description),
        )
    conn.commit()


def _load_active_pillar_keys() -> set[str]:
    """Load all active pillar_keys from DB."""
    ensure_vkpi_pillar_schema()
    conn = get_conn()
    rows = conn.execute(
        "SELECT pillar_key FROM vkpi_pillars WHERE is_active = TRUE"
    ).fetchall()
    return {r["pillar_key"] for r in rows}


def _pillar_id_by_key(key: str) -> int | None:
    """Get pillar_id by pillar_key."""
    ensure_vkpi_pillar_schema()
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM vkpi_pillars WHERE pillar_key = ?", (key,)
    ).fetchone()
    return row["id"] if row else None


def _resolve_post(post_id: int, post_table: str) -> dict | None:
    """Fetch post for classification with current V-KPI schema compatibility."""
    conn = get_conn()

    if post_table == "industry_posts":
        row = conn.execute(
            """
            SELECT
              id,
              platform,
              COALESCE(title, '') AS title,
              COALESCE(caption, '') AS description,
              hashtags_json,
              NULL::INT AS duration_seconds,
              raw_platform_data AS raw_data_json
            FROM vkpi_industry_posts WHERE id = ?
            """,
            (post_id,),
        ).fetchone()
    elif post_table == "kol_posts":
        try:
            row = conn.execute(
                """
                SELECT
                  id, platform,
                  COALESCE(title, '') AS title,
                  COALESCE(description, caption, '') AS description,
                  COALESCE(hashtags_json, '[]') AS hashtags_json,
                  duration_seconds,
                  COALESCE(raw_data_json, raw_platform_data, '{}') AS raw_data_json
                FROM vkpi_kol_posts WHERE id = ?
                """,
                (post_id,),
            ).fetchone()
        except Exception:
            row = None
    else:
        return None

    return dict(row) if row else None

def _build_prompt(post: dict) -> str:
    """Build classification prompt."""
    hashtags = post.get("hashtags_json") or "[]"
    if isinstance(hashtags, str):
        try:
            hashtags = json.loads(hashtags)
        except (json.JSONDecodeError, TypeError):
            hashtags = []
    
    return PROMPT_TEMPLATE.format(
        platform=post.get("platform") or "unknown",
        post_type="post",
        title=(post.get("title") or "")[:200],
        description=(post.get("description") or "")[:1500],
        hashtags=", ".join(str(t) for t in hashtags[:20]) if hashtags else "(none)",
        duration_seconds=post.get("duration_seconds") or "n/a",
    )


def _parse_response(text: str) -> dict:
    """Parse LLM response, handling markdown."""
    if not text or not isinstance(text, str):
        return {}
    
    s = text.strip()
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl > 0:
            s = s[first_nl + 1:]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return {}


def _validate_response(parsed: dict, valid_keys: set[str]) -> dict:
    """Validate LLM output against valid pillar keys."""
    primary = (parsed.get("primary_pillar") or "").lower().strip()
    if primary not in valid_keys:
        primary = "other"
    
    try:
        primary_conf = float(parsed.get("primary_confidence", 0.5))
        primary_conf = max(0.0, min(1.0, primary_conf))
    except (TypeError, ValueError):
        primary_conf = 0.5
    
    # Secondary pillars
    secondary_raw = parsed.get("secondary_pillars") or []
    secondary_conf_raw = parsed.get("secondary_confidences") or []
    
    secondary = []
    secondary_confs = []
    
    if isinstance(secondary_raw, list):
        for i, key in enumerate(secondary_raw[:2]):  # cap at 2
            key_norm = (str(key) or "").lower().strip()
            if key_norm in valid_keys and key_norm != primary:
                secondary.append(key_norm)
                # Match confidence
                if i < len(secondary_conf_raw):
                    try:
                        c = float(secondary_conf_raw[i])
                        secondary_confs.append(max(0.0, min(1.0, c)))
                    except (TypeError, ValueError):
                        secondary_confs.append(0.5)
                else:
                    secondary_confs.append(0.5)
    
    return {
        "primary_pillar": primary,
        "primary_confidence": primary_conf,
        "secondary_pillars": secondary,
        "secondary_confidences": secondary_confs,
    }


def classify_post(
    post_id: int,
    post_table: str = "industry_posts",
    *,
    force_reclassify: bool = False,
    staff: dict | None = None,
) -> dict:
    """Classify a single post into 1 primary + 0-2 secondary pillars."""
    ensure_vkpi_pillar_schema()
    conn = get_conn()
    
    # Skip if already classified (unless forced)
    if not force_reclassify:
        existing = conn.execute(
            """
            SELECT COUNT(*) as n 
            FROM vkpi_post_pillars 
            WHERE post_id = ? AND post_table = ? AND prompt_version = ?
            """,
            (post_id, post_table, PROMPT_VERSION),
        ).fetchone()
        if existing and existing["n"] > 0:
            return {
                "post_id": post_id,
                "post_table": post_table,
                "status": "duplicate",
            }
    
    post = _resolve_post(post_id, post_table)
    if not post:
        return {
            "post_id": post_id,
            "status": "fail",
            "error": "post not found",
        }
    
    valid_keys = _load_active_pillar_keys()
    if not valid_keys:
        return {
            "post_id": post_id,
            "status": "fail",
            "error": "no active pillars in DB (run seed migration)",
        }
    
    # Build prompt
    prompt = _build_prompt(post)
    
    # LLM call
    response = llm_gateway.invoke(
        prompt,
        purpose="vkpi_pillar",
        max_output_tokens=200,
        staff=staff,
    )
    
    response_status = str(response.get("status") or "")
    if response_status == "success":
        parsed = _parse_response(response.get("text", ""))
        validated = _validate_response(parsed, valid_keys)
    elif response_status == "fallback_to_rule":
        validated = _validate_response({}, valid_keys)
    else:
        return {
            "post_id": post_id,
            "status": "fail",
            "error": response.get("error") or response.get("reason") or "llm gateway failed",
            "provider": response.get("provider"),
        }
    
    # Persist
    return _persist_classification(
        post_id=post_id,
        post_table=post_table,
        result=validated,
        llm_provider=response.get("provider", "unknown"),
        llm_model=response.get("model", "unknown"),
    )


def _persist_classification(
    *,
    post_id: int,
    post_table: str,
    result: dict,
    llm_provider: str,
    llm_model: str,
) -> dict:
    """Write classification to vkpi_post_pillars."""
    ensure_vkpi_pillar_schema()
    conn = get_conn()
    
    # Primary pillar
    primary_id = _pillar_id_by_key(result["primary_pillar"])
    if primary_id is None:
        return {
            "post_id": post_id,
            "status": "fail",
            "error": f"primary pillar '{result['primary_pillar']}' not in DB",
        }
    
    conn.execute(
        """
        INSERT INTO vkpi_post_pillars (
          post_id, post_table, pillar_id, is_primary, confidence,
          llm_provider, llm_model, prompt_version, classified_at
        ) VALUES (?, ?, ?, TRUE, ?, ?, ?, ?, ?)
        ON CONFLICT (post_id, post_table, pillar_id, prompt_version) DO NOTHING
        """,
        (
            post_id, post_table, primary_id,
            result["primary_confidence"],
            llm_provider, llm_model, PROMPT_VERSION, _now_iso(),
        ),
    )
    
    # Secondary pillars
    for sec_key, sec_conf in zip(
        result["secondary_pillars"], result["secondary_confidences"]
    ):
        sec_id = _pillar_id_by_key(sec_key)
        if sec_id is None:
            continue
        conn.execute(
            """
            INSERT INTO vkpi_post_pillars (
              post_id, post_table, pillar_id, is_primary, confidence,
              llm_provider, llm_model, prompt_version, classified_at
            ) VALUES (?, ?, ?, FALSE, ?, ?, ?, ?, ?)
            ON CONFLICT (post_id, post_table, pillar_id, prompt_version) DO NOTHING
            """,
            (
                post_id, post_table, sec_id, sec_conf,
                llm_provider, llm_model, PROMPT_VERSION, _now_iso(),
            ),
        )
    
    conn.commit()

    return {
        "post_id": post_id,
        "post_table": post_table,
        "status": "ok",
        "primary_pillar": result["primary_pillar"],
        "primary_confidence": result["primary_confidence"],
        "secondary_pillars": result["secondary_pillars"],
        "llm_provider": llm_provider,
    }


def classify_batch(
    *,
    post_table: str = "industry_posts",
    days: int = 7,
    limit: int = 100,
    staff: dict | None = None,
) -> dict:
    """Batch classify recent unclassified posts."""
    ensure_vkpi_pillar_schema()
    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    if post_table == "industry_posts":
        table = "vkpi_industry_posts"
    elif post_table == "kol_posts":
        table = "vkpi_kol_posts"
    else:
        return {"status": "fail", "error": "invalid post_table"}
    
    posts = conn.execute(
        f"""
        SELECT p.id
        FROM {table} p
        LEFT JOIN vkpi_post_pillars pp
          ON pp.post_id = p.id
          AND pp.post_table = ?
          AND pp.prompt_version = ?
        WHERE p.created_at >= ? AND pp.id IS NULL
        ORDER BY p.created_at DESC
        LIMIT ?
        """,
        (post_table, PROMPT_VERSION, cutoff, limit),
    ).fetchall()
    
    summary = {"total": len(posts), "by_status": {}, "errors": []}
    for row in posts:
        result = classify_post(
            post_id=row["id"],
            post_table=post_table,
            staff=staff,
        )
        status = result.get("status", "unknown")
        summary["by_status"][status] = summary["by_status"].get(status, 0) + 1
        if status == "fail":
            summary["errors"].append(result)
    
    return summary


def backfill_historical(
    *,
    post_table: str = "industry_posts",
    days: int = 90,
    limit: int = 5000,
    staff: dict | None = None,
) -> dict:
    """Historical backfill (large batch)."""
    return classify_batch(
        post_table=post_table, days=days, limit=limit, staff=staff
    )


def stats(*, days: int = 30) -> dict:
    """Pillar distribution statistics."""
    ensure_vkpi_pillar_schema()
    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    by_pillar = conn.execute(
        """
        SELECT 
          p.pillar_key,
          p.display_name,
          p.layer,
          COUNT(*) as count,
          SUM(CASE WHEN pp.is_primary THEN 1 ELSE 0 END) as primary_count
        FROM vkpi_post_pillars pp
        JOIN vkpi_pillars p ON p.id = pp.pillar_id
        WHERE pp.classified_at >= ?
        GROUP BY p.pillar_key, p.display_name, p.layer
        ORDER BY count DESC
        """,
        (cutoff,),
    ).fetchall()
    
    return {
        "days": days,
        "by_pillar": [dict(r) for r in by_pillar],
    }


def list_pillars() -> dict:
    """Return all configured pillars (for UI dropdown / API)."""
    ensure_vkpi_pillar_schema()
    ensure_vkpi_pillar_schema()
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, pillar_key, display_name, description, layer,
               is_active, display_order
        FROM vkpi_pillars
        ORDER BY layer ASC, display_order ASC
        """
    ).fetchall()
    
    return {
        "pillars": [dict(r) for r in rows],
        "by_layer": {
            "1_generic": [dict(r) for r in rows if r["layer"] == 1],
            "2_photography": [dict(r) for r in rows if r["layer"] == 2],
            "3_data_driven": [dict(r) for r in rows if r["layer"] == 3],
        },
    }
