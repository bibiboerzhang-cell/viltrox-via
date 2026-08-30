"""分层 lint(AST 扫描,白名单快照只许减少)。

规则(2026-08-23 优化波 B·A 车道 C 架构债):
- ``backend/app/domains/**`` 不得 import ``app.workers.*`` / ``app.api.*``;
- ``backend/app/services/**`` 不得 import ``app.api.*``。

函数体内的 lazy import 同样算违例(那只是把环藏进运行期)。当前违例做成
``LAYERING_WHITELIST`` 快照:清单内文件只能减少其违例模块,清单外文件零容忍。
修掉一处后请从快照删掉对应条目(或运行
``.venv/bin/python tests/test_layering_lint.py --refresh`` 重新生成,并在 PR 里解释
为什么快照只减不增)。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "backend" / "app"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.vkpi_engineering_health_architecture import (  # noqa: E402
    LAYER_RULES,
    banned_import,
)

# path(相对 backend/app) -> 被禁 import 的模块名(排序)。只许减少。
LAYERING_WHITELIST: dict[str, tuple[str, ...]] = {}


def _banned(module: str, banned_prefixes: tuple[str, ...]) -> bool:
    return banned_import(module, banned_prefixes)


def _scan() -> dict[str, tuple[str, ...]]:
    violations: dict[str, tuple[str, ...]] = {}
    for layer, banned_prefixes in LAYER_RULES.items():
        for path in sorted((APP / layer).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            hits: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if _banned(alias.name, banned_prefixes):
                            hits.add(alias.name)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    module = node.module or ""
                    if _banned(module, banned_prefixes):
                        hits.add(module)
            if hits:
                violations[str(path.relative_to(APP))] = tuple(sorted(hits))
    return violations


def test_layering_violations_can_only_decrease() -> None:
    actual = _scan()
    new_files = {path: mods for path, mods in actual.items() if path not in LAYERING_WHITELIST}
    grown = {
        path: sorted(set(mods) - set(LAYERING_WHITELIST[path]))
        for path, mods in actual.items()
        if path in LAYERING_WHITELIST and not set(mods) <= set(LAYERING_WHITELIST[path])
    }
    assert not new_files, (
        "分层违例(新文件):domains/* 不得 import app.workers/app.api;services/* 不得 import app.api。"
        f" 请改成事件/接口依赖或把共享件下沉到 domains/platform:{new_files}"
    )
    assert not grown, f"分层违例增加(白名单内文件新增被禁 import):{grown}"


def test_layering_whitelist_has_no_stale_entries() -> None:
    """修掉的违例要同步从快照删掉,避免白名单变成长期豁免。"""
    actual = _scan()
    stale = {
        path: sorted(set(mods) - set(actual.get(path, ())))
        for path, mods in LAYERING_WHITELIST.items()
        if not set(mods) <= set(actual.get(path, ()))
    }
    assert not stale, f"分层白名单有已修复的过期条目,请从 LAYERING_WHITELIST 删除:{stale}"


if __name__ == "__main__":  # pragma: no cover - 维护入口
    if "--refresh" in sys.argv:
        import pprint

        pprint.pprint(_scan(), width=100)
    else:
        print("usage: python tests/test_layering_lint.py --refresh  # 打印当前违例快照")
