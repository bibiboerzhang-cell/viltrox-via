"""Characterization of the cognitive1 counter and its collector wiring."""
from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

from scripts import vkpi_engineering_health_cognitive as cognitive
from scripts import vkpi_engineering_health_collect as collector

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads(
    (ROOT / "docs/vkpi/engineering-health-score-contract-v1.json").read_text(encoding="utf-8")
)
OBSERVED_AT = "2026-08-30T12:00:00Z"


def _cognitive_of(source: str) -> dict[str, int]:
    module = ast.parse(source)
    values: dict[str, int] = {}
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            values[node.name] = cognitive.cognitive_complexity(node)
    return values


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)


def test_sequential_function_is_zero() -> None:
    values = _cognitive_of(
        """def seq(a):
    x = a + 1
    y = x * 2
    return y
"""
    )
    assert values == {"seq": 0}


def test_if_elif_else_chain_counts_flat_branches() -> None:
    values = _cognitive_of(
        """def chain(a):
    if a > 3:
        return 3
    elif a > 1:
        return 1
    else:
        return 0
"""
    )
    # if +1, elif +1 flat, else +1 flat.
    assert values == {"chain": 3}


def test_nested_loops_apply_depth_penalty() -> None:
    values = _cognitive_of(
        """def nested(rows):
    total = 0
    for row in rows:
        for cell in row:
            if cell:
                total += 1
    return total
"""
    )
    # for +1 (d0), for +2 (d1), if +3 (d2).
    assert values == {"nested": 6}


def test_boolean_runs_count_one_per_operator_alternation() -> None:
    values = _cognitive_of(
        """def bools(a, b, c, d):
    if a and b and c:
        return 1
    if a and b or c and d:
        return 2
    return 0
"""
    )
    # if +1 + BoolOp(and-run) +1; if +1 + runs and/or/and +3.
    assert values == {"bools": 6}


def test_ternary_gets_structural_penalty_inside_loop() -> None:
    values = _cognitive_of(
        """def tern(rows):
    out = []
    for row in rows:
        out.append(1 if row else 0)
    return out
"""
    )
    # for +1 (d0), IfExp +2 (d1).
    assert values == {"tern": 3}


def test_except_handlers_count_and_nest() -> None:
    values = _cognitive_of(
        """def guarded(rows):
    try:
        for row in rows:
            row.check()
    except ValueError:
        return None
    except KeyError:
        return None
    return rows


def handler_nested(rows):
    try:
        return rows[0]
    except IndexError:
        if rows is None:
            return None
        return -1
"""
    )
    # guarded: for +1 (try body keeps depth), except +1, except +1.
    # handler_nested: except +1 (d0), if +2 (d1 inside handler body).
    assert values == {"guarded": 3, "handler_nested": 3}


def test_direct_and_method_recursion_add_one_per_call_site() -> None:
    values = _cognitive_of(
        """def fact(n):
    if n <= 1:
        return 1
    return n * fact(n - 1)


class Walker:
    def walk(self, node):
        for child in node:
            self.walk(child)
"""
    )
    assert values == {"fact": 2, "walk": 2}


def test_with_counts_only_additional_items() -> None:
    values = _cognitive_of(
        """def single(a):
    with open(a) as fa:
        return fa.read()


def double(a, b):
    with open(a) as fa, open(b) as fb:
        return fa.read() + fb.read()
"""
    )
    assert values == {"single": 0, "double": 1}


def test_match_cases_are_flat_even_when_nested() -> None:
    values = _cognitive_of(
        """def dispatch(cmd):
    match cmd:
        case "a":
            return 1
        case "b" if cmd:
            return 2
        case _:
            return 0


def dispatch_loop(cmds):
    for cmd in cmds:
        match cmd:
            case "a":
                return 1
            case _:
                return 0
"""
    )
    # Every case is +1 flat; the guard adds nothing; nesting adds nothing.
    assert values == {"dispatch": 3, "dispatch_loop": 3}


def test_else_body_is_one_level_deeper() -> None:
    values = _cognitive_of(
        """def else_depth(a, b):
    if a:
        pass
    else:
        if b:
            pass
        x = 1
"""
    )
    # if +1, else +1 flat, inner if +2 (d1); the two-statement else is not an elif.
    assert values == {"else_depth": 4}


def test_nested_function_is_excluded_and_resets_depth() -> None:
    values = _cognitive_of(
        """def outer_fn(a):
    if a:
        def inner(b):
            if b and a:
                return 1
            return 0
        return inner
    return None
"""
    )
    # outer: only its own if (+1); inner restarts at depth 0: if +1, BoolOp +1.
    assert values == {"outer_fn": 1, "inner": 2}


def test_lambda_counts_without_recursion_detection() -> None:
    module = ast.parse("choose = lambda a, b: 1 if a and b else 0\n")
    lambda_node = next(node for node in ast.walk(module) if isinstance(node, ast.Lambda))
    # IfExp +1 (d0), BoolOp +1; lambdas never count recursion.
    assert cognitive.cognitive_complexity(lambda_node) == 2


def test_collector_rows_carry_cognitive_values(tmp_path: Path) -> None:
    _write(
        tmp_path / "backend/app/example.py",
        """def deep(rows):
    for row in rows:
        for cell in row:
            if cell:
                return cell
    return None
""",
    )
    trees, failures = collector.parse_python_sources(collector.inventory_sources(tmp_path))
    rows = collector.collect_complexity(trees)
    assert failures == []
    assert [(item.qualified_name, item.cc, item.cognitive) for item in rows] == [("deep", 4, 6)]


def test_evidence_reports_observed_ratio_and_is_byte_deterministic(tmp_path: Path) -> None:
    _write(tmp_path / "backend/app/__init__.py", "")
    _write(
        tmp_path / "backend/app/domain.py",
        """def simple(flag):
    return 1 if flag else 0


def heavy(rows):
    total = 0
    for row in rows:
        for cell in row:
            if cell and total < 99:
                if cell > 1:
                    total += cell
                elif cell < -1:
                    total -= cell
                else:
                    total += 1
            for extra in cell:
                if extra:
                    total += extra
    while total > 100:
        total -= 1
    return total
""",
    )
    _git_repo(tmp_path)

    first = collector.build_evidence(tmp_path, CONTRACT, observed_at=OBSERVED_AT)
    second = collector.build_evidence(tmp_path, CONTRACT, observed_at=OBSERVED_AT)

    assert collector._json_bytes(first) == collector._json_bytes(second)
    metric = first["metrics"]["code"]["cognitive_le_15_ratio"]
    assert metric["status"] == "observed"
    assert metric["sample_count"] == 2
    # simple=1; heavy: for1+for2+BoolOp1+if3+if4+elif1+else1+for3+if4+while1 = 21.
    assert metric["value"] == 0.5
    assert metric["details"]["distribution"] == {"le_15": 1, "16_to_30": 1, "31_to_60": 0, "gt_60": 0}
    assert metric["details"]["max_cognitive"] == 21
    observations = first["collector"]["observations"]["python_complexity"]
    assert observations["cognitive_le_15_ratio"] == 0.5
    # importtime-cycles1(合同 v1.1)追加在版本串尾部后 cognitive1 不再是终段;
    # 本断言的意图是「cognitive1 口径在版本串里」,改为包含判定。
    assert "-cognitive1" in first["collector"]["algorithm_version"]


def test_parse_failure_keeps_cognitive_unknown(tmp_path: Path) -> None:
    _write(tmp_path / "backend/app/__init__.py", "")
    _write(tmp_path / "backend/app/broken.py", "def broken(:\n")
    _git_repo(tmp_path)

    evidence = collector.build_evidence(tmp_path, CONTRACT, observed_at=OBSERVED_AT)

    metric = evidence["metrics"]["code"]["cognitive_le_15_ratio"]
    assert metric["status"] == "unknown"
    assert metric["value"] is None
