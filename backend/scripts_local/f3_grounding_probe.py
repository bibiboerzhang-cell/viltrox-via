"""F3「测」字探针(2026-06-12 裁定):Gemini grounding 单次成本实测。

只读探针:一次 google_search grounding 调用,量 token/grounding 计费口径,
结果追写 docs/F3-discovery-design-v2.md 附录。模型取 config.GEMINI_MODEL(零硬编码)。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import GEMINI_MODEL  # noqa: E402


def main() -> None:
    from google import genai
    from google.genai import types

    client = genai.Client()
    tool = types.Tool(google_search=types.GoogleSearch())
    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents="Find 3 YouTube creators who recently reviewed Viltrox AF 35mm lenses. Return names and channel URLs only.",
        config=types.GenerateContentConfig(tools=[tool]),
    )
    usage = resp.usage_metadata
    grounding = getattr(resp.candidates[0], "grounding_metadata", None)
    queries = list(getattr(grounding, "web_search_queries", None) or []) if grounding else []
    chunks = len(getattr(grounding, "grounding_chunks", None) or []) if grounding else 0
    print(f"model={GEMINI_MODEL}")
    print(f"prompt_tokens={usage.prompt_token_count} out_tokens={usage.candidates_token_count} total={usage.total_token_count}")
    print(f"grounding_queries={len(queries)} {queries[:3]} chunks={chunks}")
    print(f"text_head={(resp.text or '')[:200]}")


if __name__ == "__main__":
    main()
