"""scripts/smoke_vkpi_pillars_offline.py

P1.5 Pillar offline smoke (FORCE_OFFLINE).
"""

import os
import sys
from pathlib import Path

from stdout_utils import out

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ["VKPI_LLM_GATEWAY_FORCE_OFFLINE"] = "1"


def main():
    failures = []
    
    out("[1] Module import...")
    try:
        from app.domains.content import pillars
        out("  ✓")
    except Exception as exc:
        failures.append(f"Cannot import pillars: {exc}")
        sys.exit(1)
    
    out("[2] PROMPT_VERSION exported...")
    if not pillars.PROMPT_VERSION:
        failures.append("PROMPT_VERSION not set")
    out(f"  ✓ {pillars.PROMPT_VERSION}")
    
    out("[3] _build_prompt with full post...")
    post = {
        "platform": "youtube",
        "title": "Sigma 28-70 vs Viltrox 24-70 - Detailed Comparison",
        "description": "Today we compare two popular standard zoom lenses...",
        "hashtags_json": '["#lens", "#review", "#sigma", "#viltrox"]',
        "duration_seconds": 600,
    }
    prompt = pillars._build_prompt(post)
    if "Sigma 28-70" not in prompt:
        failures.append("title missing in prompt")
    if "youtube" not in prompt:
        failures.append("platform missing in prompt")
    if "lens_review" not in prompt:
        failures.append("layer 2 pillars missing in prompt")
    if "lifestyle" not in prompt:
        failures.append("layer 1 pillars missing in prompt")
    out("  ✓ prompt rendered")
    
    out("[4] _build_prompt handles empty hashtags...")
    post_empty = {
        "platform": "instagram",
        "title": "",
        "description": "Cool",
        "hashtags_json": None,
        "duration_seconds": None,
    }
    p = pillars._build_prompt(post_empty)
    if "(none)" not in p:
        failures.append("Empty hashtags not rendered as (none)")
    out("  ✓")
    
    out("[5] _parse_response handles plain JSON...")
    text = '{"primary_pillar": "lens_review", "primary_confidence": 0.9}'
    parsed = pillars._parse_response(text)
    if parsed.get("primary_pillar") != "lens_review":
        failures.append(f"plain JSON parse: {parsed}")
    out("  ✓")
    
    out("[6] _parse_response handles markdown...")
    text = '```json\n{"primary_pillar": "vlog"}\n```'
    parsed = pillars._parse_response(text)
    if parsed.get("primary_pillar") != "vlog":
        failures.append("markdown not stripped")
    out("  ✓")
    
    out("[7] _validate_response with valid input...")
    valid_keys = {"lens_review", "vlog", "tutorial", "other"}
    parsed = {
        "primary_pillar": "lens_review",
        "primary_confidence": 0.92,
        "secondary_pillars": ["tutorial"],
        "secondary_confidences": [0.65],
    }
    result = pillars._validate_response(parsed, valid_keys)
    if result["primary_pillar"] != "lens_review":
        failures.append("valid primary changed")
    if result["secondary_pillars"] != ["tutorial"]:
        failures.append("valid secondary changed")
    out("  ✓")
    
    out("[8] _validate_response invalid primary fallback to 'other'...")
    parsed = {"primary_pillar": "imaginary_pillar", "primary_confidence": 0.9}
    result = pillars._validate_response(parsed, valid_keys)
    if result["primary_pillar"] != "other":
        failures.append(f"Invalid primary not fallen back: {result['primary_pillar']}")
    out("  ✓")
    
    out("[9] _validate_response cap secondary at 2...")
    parsed = {
        "primary_pillar": "lens_review",
        "secondary_pillars": ["tutorial", "vlog", "other", "lens_review"],
        "secondary_confidences": [0.5, 0.4, 0.3, 0.2],
    }
    result = pillars._validate_response(parsed, valid_keys)
    if len(result["secondary_pillars"]) > 2:
        failures.append(f"Secondary not capped at 2: {len(result['secondary_pillars'])}")
    # primary should not be in secondary
    if result["primary_pillar"] in result["secondary_pillars"]:
        failures.append("primary leaked into secondary")
    out("  ✓ capped + dedup")
    
    out("[10] _validate_response confidence clamping...")
    parsed = {
        "primary_pillar": "lens_review",
        "primary_confidence": 1.5,  # > 1
        "secondary_pillars": ["vlog"],
        "secondary_confidences": [-0.3],  # < 0
    }
    result = pillars._validate_response(parsed, valid_keys)
    if not (0.0 <= result["primary_confidence"] <= 1.0):
        failures.append(f"primary_confidence not clamped: {result['primary_confidence']}")
    if result["secondary_confidences"] and not (0.0 <= result["secondary_confidences"][0] <= 1.0):
        failures.append("secondary confidence not clamped")
    out("  ✓")
    
    out("[11] _validate_response missing fields...")
    result = pillars._validate_response({}, valid_keys)
    if result["primary_pillar"] != "other":
        failures.append("Empty input should default to 'other'")
    if result["primary_confidence"] != 0.5:
        failures.append("Empty input confidence should default to 0.5")
    out("  ✓")
    
    # Final
    out()
    if failures:
        out(f"FAIL: {len(failures)} issues:")
        for f in failures:
            out(f"  - {f}")
        sys.exit(1)
    else:
        out("VKPI_PILLARS_OFFLINE_SMOKE_OK")


if __name__ == "__main__":
    main()
