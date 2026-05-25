"""scripts/smoke_vkpi_sentiment_offline.py

P1.4 Sentiment offline smoke (FORCE_OFFLINE mode).

Tests:
  1. Module import
  2. _validate_response with valid LLM output
  3. _validate_response with invalid enum (falls back to neutral)
  4. _validate_response with missing fields
  5. _parse_llm_response handles markdown code blocks
  6. _parse_llm_response handles invalid JSON
  7. PROMPT_TEMPLATE renders with all fields
  8. Enum sets are consistent (sentiment/emotion/brand_attitude)

Run:
  VKPI_LLM_GATEWAY_FORCE_OFFLINE=1 \
    PYTHONPATH=backend .venv/bin/python scripts/smoke_vkpi_sentiment_offline.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

# Force offline so any accidental LLM call is blocked
os.environ["VKPI_LLM_GATEWAY_FORCE_OFFLINE"] = "1"


def main():
    failures = []
    
    print("[1] Module import...")
    try:
        import app.domains.comments.sentiment as sentiment
        print("  ✓")
    except Exception as exc:
        failures.append(f"Cannot import sentiment: {exc}")
        sys.exit(1)
    
    print("[2] Enum sets present...")
    if not (sentiment.SENTIMENT_VALUES and sentiment.EMOTION_VALUES and sentiment.BRAND_ATTITUDE_VALUES):
        failures.append("Enum sets missing")
    
    if "neutral" not in sentiment.SENTIMENT_VALUES:
        failures.append("'neutral' missing from SENTIMENT_VALUES")
    if "joy" not in sentiment.EMOTION_VALUES:
        failures.append("'joy' missing from EMOTION_VALUES")
    if "advocate" not in sentiment.BRAND_ATTITUDE_VALUES:
        failures.append("'advocate' missing from BRAND_ATTITUDE_VALUES")
    print("  ✓ enums consistent")
    
    print("[3] _validate_response with valid LLM output...")
    valid_response = {
        "sentiment": "positive",
        "sentiment_confidence": 0.92,
        "emotion": "joy",
        "emotion_confidence": 0.85,
        "brand_attitude": "advocate",
        "brand_attitude_confidence": 0.78,
        "language_detected": "en",
    }
    result = sentiment._validate_response(valid_response)
    if result["sentiment"] != "positive":
        failures.append(f"Valid sentiment changed: {result['sentiment']}")
    if result["emotion"] != "joy":
        failures.append("Valid emotion changed")
    if result["brand_attitude"] != "advocate":
        failures.append("Valid brand_attitude changed")
    if result["sentiment_confidence"] != 0.92:
        failures.append(f"Confidence changed: {result['sentiment_confidence']}")
    print("  ✓ valid kept")
    
    print("[4] _validate_response with invalid enum (fallback to neutral)...")
    bad_response = {
        "sentiment": "amazing",      # not in enum
        "emotion": "happy_dance",    # not in enum
        "brand_attitude": "love",    # not in enum
    }
    result = sentiment._validate_response(bad_response)
    if result["sentiment"] != "neutral":
        failures.append(f"Invalid not fallen back: {result['sentiment']}")
    if result["emotion"] != "neutral":
        failures.append("Invalid emotion not fallen back")
    if result["brand_attitude"] != "neutral":
        failures.append("Invalid brand_attitude not fallen back")
    print("  ✓ invalid → neutral")
    
    print("[5] _validate_response with missing fields...")
    empty = {}
    result = sentiment._validate_response(empty)
    if result["sentiment"] != "neutral":
        failures.append("Missing sentiment not defaulting to neutral")
    if result["sentiment_confidence"] != 0.5:
        failures.append("Missing confidence not defaulting to 0.5")
    print("  ✓ missing → defaults")
    
    print("[6] _validate_response confidence clamping...")
    extreme = {
        "sentiment": "positive",
        "sentiment_confidence": 1.5,  # > 1
        "emotion": "joy",
        "emotion_confidence": -0.3,    # < 0
        "brand_attitude": "advocate",
        "brand_attitude_confidence": "not_a_number",
    }
    result = sentiment._validate_response(extreme)
    if not (0.0 <= result["sentiment_confidence"] <= 1.0):
        failures.append(f"Confidence not clamped: {result['sentiment_confidence']}")
    if not (0.0 <= result["emotion_confidence"] <= 1.0):
        failures.append("Negative confidence not clamped")
    print("  ✓ clamped")
    
    print("[7] _parse_llm_response handles plain JSON...")
    text = '{"sentiment": "positive", "emotion": "joy"}'
    parsed = sentiment._parse_llm_response(text)
    if parsed.get("sentiment") != "positive":
        failures.append(f"Plain JSON parse failed: {parsed}")
    print("  ✓")
    
    print("[8] _parse_llm_response handles markdown wrapper...")
    text = '```json\n{"sentiment": "positive", "emotion": "joy"}\n```'
    parsed = sentiment._parse_llm_response(text)
    if parsed.get("sentiment") != "positive":
        failures.append(f"Markdown json wrapper not stripped: {parsed}")
    
    text = '```\n{"sentiment": "negative"}\n```'
    parsed = sentiment._parse_llm_response(text)
    if parsed.get("sentiment") != "negative":
        failures.append("Plain markdown wrapper not stripped")
    print("  ✓ markdown stripped")
    
    print("[9] _parse_llm_response handles invalid JSON...")
    text = "Sure! Here's the analysis: positive"
    parsed = sentiment._parse_llm_response(text)
    if parsed != {}:
        failures.append(f"Invalid JSON should return empty dict, got {parsed}")
    print("  ✓ empty dict on parse failure")
    
    print("[10] _build_prompt renders correctly...")
    p = sentiment._build_prompt(
        "Great lens!",
        platform="youtube",
        post_type="comment",
        kol_brief="Sigma 28-70 review",
        language_hint="en",
    )
    if "Great lens!" not in p:
        failures.append("Comment text missing in prompt")
    if "youtube" not in p:
        failures.append("Platform missing in prompt")
    if "Viltrox" not in p:
        failures.append("Brand context missing in prompt")
    print("  ✓ prompt renders")
    
    print("[11] PROMPT_VERSION exported...")
    if not sentiment.PROMPT_VERSION:
        failures.append("PROMPT_VERSION not set")
    print(f"  ✓ {sentiment.PROMPT_VERSION}")
    
    # Final
    print()
    if failures:
        print(f"FAIL: {len(failures)} issues:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("VKPI_SENTIMENT_OFFLINE_SMOKE_OK")


if __name__ == "__main__":
    main()
