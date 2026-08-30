"""class-LOC ≤400 棘轮:超线类存量白名单只减不增(W4 防倒退三棘轮之一)。

口径 = collector 的 class span(scripts/vkpi_engineering_health_architecture.py
的 ``collect_class_spans``):每个 AST ClassDef 记物理跨度 lineno..end_lineno
(含端点;decorator 与其后注释不计;嵌套类独立计数且仍留在外层类跨度内)。
扫描范围 = collector 的 PYTHON_ROOTS(backend/app 生产码,tests 排除)。

规则:
- ``CLASS_LOC_WHITELIST``:快照时全部 LOC>400 的类(``path::qualified_name`` -> 当时 LOC);
- 白名单外不许出现 LOC>400 的类(新类零豁免,老类不得被撑过线);
- 白名单内类不得比快照值更长(只许变短);
- 拆到 ≤400 / 改名 / 删除后必须同步从白名单删条目——白名单只减不增,
  绝不许手工加新条目给新类放行。

重拍快照(--refresh-baseline)会原地重写 CLASS_LOC_WHITELIST。
**refresh 动作须主会话/用户批准后才可执行**,命令:
``.venv/bin/python tests/test_class_loc_ratchet.py --refresh-baseline``
"""
from __future__ import annotations

import ast
import re
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import vkpi_engineering_health_architecture as architecture_tools  # noqa: E402
from scripts import vkpi_engineering_health_collect as collector  # noqa: E402
from scripts import vkpi_engineering_health_snapshot as snapshot  # noqa: E402

CLASS_LOC_LIMIT = 400
FIX_HINT = (
    "修法:按职责把方法群提取成协作类/模块级函数,放同目录兄弟文件(≤700 行),"
    "原类保薄门面;行为用 characterization 测试先锁再拆。白名单在 "
    "tests/test_class_loc_ratchet.py 的 CLASS_LOC_WHITELIST;确需放行请经主会话/用户批准后 "
    "运行 .venv/bin/python tests/test_class_loc_ratchet.py --refresh-baseline 并在提交说明里解释。"
)

# --- snapshot begin (generated; do not edit by hand) ---
CLASS_LOC_WHITELIST: dict[str, int] = {

}
# --- snapshot end ---


def _parse_production_trees() -> dict[str, ast.Module]:
    """collector 同口径快照 + 解析(backend/app 生产 Python,tests 排除)。"""
    captured = snapshot.snapshot_sources(
        ROOT,
        collector.PYTHON_ROOTS,
        {".py"},
        skip_parts=collector.SKIP_PARTS,
        test_directory_names=collector.TEST_DIRECTORY_NAMES,
        test_filename_markers=collector.TEST_FILENAME_MARKERS,
    )
    assert captured.complete, (
        f"源快照不完整,棘轮口径失真:symlinks={list(captured.symlink_sources)} "
        f"errors={list(captured.read_errors)}"
    )
    trees, failures = collector.parse_python_sources(list(captured.files))
    assert not failures, f"生产 Python 解析失败,棘轮口径失真:{failures}"
    return trees


@lru_cache(maxsize=1)
def _current_over_limit() -> dict[str, int]:
    """当前所有 LOC>400 的类:"path::qualified_name" -> LOC(重名取最大)。"""
    rows = architecture_tools.collect_class_spans(_parse_production_trees())
    over: dict[str, int] = {}
    for row in rows:
        if row.loc <= CLASS_LOC_LIMIT:
            continue
        key = f"{row.path}::{row.qualified_name}"
        over[key] = max(over.get(key, 0), row.loc)
    return over


def test_no_new_classes_over_loc_limit() -> None:
    current = _current_over_limit()
    newcomers = {key: loc for key, loc in current.items() if key not in CLASS_LOC_WHITELIST}
    assert not newcomers, (
        f"class-LOC 棘轮:白名单外出现 LOC>{CLASS_LOC_LIMIT} 的类(谁越线见下,值=当前 LOC)。"
        f"{FIX_HINT} 越线类:{dict(sorted(newcomers.items()))}"
    )


def test_whitelisted_classes_do_not_grow() -> None:
    current = _current_over_limit()
    grown = {
        key: {"now": current[key], "snapshot": CLASS_LOC_WHITELIST[key]}
        for key in CLASS_LOC_WHITELIST
        if key in current and current[key] > CLASS_LOC_WHITELIST[key]
    }
    assert not grown, (
        f"class-LOC 棘轮:白名单内类比快照时更长了(只许变短)。{FIX_HINT} 变长的类:{grown}"
    )


def test_whitelist_has_no_stale_entries() -> None:
    """拆到 ≤400 / 改名 / 删除的类必须同步从白名单删掉——白名单只减不增。"""
    current = _current_over_limit()
    stale = sorted(key for key in CLASS_LOC_WHITELIST if key not in current)
    assert not stale, (
        "class-LOC 棘轮:白名单有过期条目(类已拆好/改名/删除),请从 "
        f"tests/test_class_loc_ratchet.py 的 CLASS_LOC_WHITELIST 删除(只删不加):{stale}"
    )


def test_whitelist_is_well_formed() -> None:
    # 2026-08-31:六个存量巨类全部拆到 ≤400,白名单达成终点态(空)。
    # 空名单合法且是目标——此时上面的「白名单外零新类超线」断言即全仓硬闸。
    if not CLASS_LOC_WHITELIST:
        return
    for key, loc in CLASS_LOC_WHITELIST.items():
        assert "::" in key and key.startswith("backend/app/"), key
        assert isinstance(loc, int) and loc > CLASS_LOC_LIMIT, (key, loc)


def _refresh_baseline() -> None:
    """原地重拍 CLASS_LOC_WHITELIST。**须主会话/用户批准后才可运行**(见模块 docstring)。"""
    me = Path(__file__)
    text = me.read_text(encoding="utf-8")
    _current_over_limit.cache_clear()
    body = "\n".join(f'    "{key}": {loc},' for key, loc in sorted(_current_over_limit().items()))
    block = "CLASS_LOC_WHITELIST: dict[str, int] = {\n" + body + "\n}"
    pattern = re.compile(r"CLASS_LOC_WHITELIST: dict\[str, int\] = \{.*?\n\}", re.S)
    assert pattern.search(text), "snapshot block missing"
    me.write_text(pattern.sub(lambda _m: block, text, count=1), encoding="utf-8")
    print(f"refreshed {len(_current_over_limit())} entries into {me.relative_to(ROOT)}")


if __name__ == "__main__":  # pragma: no cover - 维护入口
    if "--refresh-baseline" in sys.argv:
        _refresh_baseline()
    else:
        print("usage: python tests/test_class_loc_ratchet.py --refresh-baseline  # 须主会话/用户批准")
