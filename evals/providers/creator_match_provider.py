"""promptfoo custom provider: V-KPI creator_match skill(hermetic,零 LLM / 零 DB)。

promptfoo Python provider 协议:模块暴露 call_api(prompt, options, context) -> dict,
返回 {"output": <str>} 供断言消费。本 provider 把 prompt(一段 JSON,如
{"product": "viltrox af 85mm", "market": "US"})解析成 creator_match 的 input,
注入 skill 自带的确定性桩 preview(_fixture_preview)后跑 run(record=False)——
不真烧 LLM、不连活 DB/Memory,可离线复现。

运行前置:promptfoo 由 Node 侧驱动,会以本仓 .venv 的 python 起子进程 import 本文件;
子进程需能 import 到 backend 包,故下方把 <repo>/backend 与 <repo> 注入 sys.path
(等价 PYTHONPATH=backend:.)。运行命令见 evals/README.md。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# evals/providers/creator_match_provider.py → parents[2] = 仓库根。
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _candidate in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)


def _parse_input(prompt: str) -> dict:
    """prompt 文本 → creator_match input dict;非 JSON 则整体当 product 查询。"""
    text = (prompt or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {"product": text}
    return data if isinstance(data, dict) else {"product": str(data)}


def call_api(prompt, options=None, context=None):  # noqa: ANN001 — promptfoo 协议签名
    """promptfoo 入口:跑 creator_match(fixture 桩,零 LLM/零 DB),返回序列化输出。"""
    from app.domains.marketing_brain.skills import creator_match

    inp = _parse_input(prompt)
    original = creator_match._build_preview
    creator_match._build_preview = creator_match._fixture_preview
    try:
        result = creator_match.run(inp, record=False)
    finally:
        creator_match._build_preview = original
    return {"output": json.dumps(result, ensure_ascii=False)}
