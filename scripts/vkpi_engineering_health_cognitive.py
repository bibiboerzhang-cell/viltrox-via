#!/usr/bin/env python3
"""Deterministic cognitive-complexity counter for the engineering-health collector.

Methodology ``cognitive1`` (fixed subset in the spirit of Sonar Cognitive
Complexity; this docstring is the canonical contract-v1.1 wording):

Base value is 0 per function. Nested ``def``/``class``/``lambda`` bodies are
excluded from the enclosing function and analysed independently with nesting
depth reset to 0 (nested functions and lambdas receive their own rows).

Structural increments, worth ``+1 + current nesting depth`` at the point of
occurrence:

- ``if`` (the head of a chain only),
- ``for`` / ``async for``,
- ``while``,
- each ``except`` / ``except*`` handler,
- the ternary ``IfExp``.

Flat ``+1`` increments that never receive the nesting penalty:

- each ``elif`` branch and each ``else`` branch of an ``if`` chain (an
  ``else`` whose body is exactly one ``if`` statement is indistinguishable
  from ``elif`` in the Python AST and is treated as ``elif``),
- each boolean-operator run: every ``ast.BoolOp`` node counts 1, which equals
  one increment per operator alternation (``a and b and c`` = 1,
  ``a and b or c`` = 2, ``a and b or c and d`` = 3),
- each additional context manager beyond the first in one
  ``with`` / ``async with`` statement,
- each ``case`` clause of a ``match`` statement (wildcard case included,
  guards are not counted separately, case bodies add no nesting),
- each direct recursive call site: the callee is a bare name equal to the
  enclosing function's name, or ``self.<name>`` / ``cls.<name>`` with that
  name; named functions only — lambdas never count recursion.

Nesting depth: the bodies of ``if``/``elif``/``else``, ``for``/``async for``,
``while``, and ``except`` handlers are one level deeper than their parent;
an ``elif`` continues its chain at the chain's own level. ``with``, ``match``
cases, ``try`` bodies, ``else`` of ``try``, ``finally``, and loop ``else``
clauses do not change depth. ``def``/``class`` reset depth. ``IfExp`` earns
the structural increment but does not itself increase depth.

Not counted at all: sequential statements, ``return``, ``assert``, ``raise``,
``break``/``continue`` (Python has no labeled jump targets), ``try`` itself,
``finally``, ``else`` of ``try``, loop ``else`` clauses, a single-item
``with``, and comprehension generator/filter clauses — although boolean
operator runs, ternaries, and recursive calls inside any of those expressions
still count at the current depth.

Determinism: the value is a pure function of the parsed AST; identical source
bytes always produce identical counts.
"""
from __future__ import annotations

import ast
from typing import Iterable

THRESHOLD = 15

_SCOPE_RESET_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


class CognitiveComplexityCounter:
    """Walk one function body and accumulate the documented cognitive score."""

    def __init__(self, function_name: str | None) -> None:
        self.function_name = function_name
        self.total = 0

    # -- statements ------------------------------------------------------

    def walk_body(self, statements: Iterable[ast.stmt], depth: int) -> None:
        for statement in statements:
            self._walk_statement(statement, depth)

    def _walk_statement(self, node: ast.stmt, depth: int) -> None:
        if isinstance(node, _SCOPE_RESET_NODES):
            return  # Nested scopes are excluded and reset depth in their own row.
        if isinstance(node, ast.If):
            self._walk_if(node, depth, chained=False)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self.total += 1 + depth
            self.walk_expression(node.target, depth)
            self.walk_expression(node.iter, depth)
            self.walk_body(node.body, depth + 1)
            self.walk_body(node.orelse, depth)
        elif isinstance(node, ast.While):
            self.total += 1 + depth
            self.walk_expression(node.test, depth)
            self.walk_body(node.body, depth + 1)
            self.walk_body(node.orelse, depth)
        elif isinstance(node, (ast.Try, ast.TryStar)):
            self.walk_body(node.body, depth)
            for handler in node.handlers:
                self.total += 1 + depth
                if handler.type is not None:
                    self.walk_expression(handler.type, depth)
                self.walk_body(handler.body, depth + 1)
            self.walk_body(node.orelse, depth)
            self.walk_body(node.finalbody, depth)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            self.total += max(0, len(node.items) - 1)
            for item in node.items:
                self.walk_expression(item.context_expr, depth)
                if item.optional_vars is not None:
                    self.walk_expression(item.optional_vars, depth)
            self.walk_body(node.body, depth)
        elif isinstance(node, ast.Match):
            self.walk_expression(node.subject, depth)
            for case in node.cases:
                self.total += 1
                if case.guard is not None:
                    self.walk_expression(case.guard, depth)
                self.walk_body(case.body, depth)
        else:
            self.walk_expression(node, depth)

    def _walk_if(self, node: ast.If, depth: int, *, chained: bool) -> None:
        self.total += 1 if chained else 1 + depth
        self.walk_expression(node.test, depth)
        self.walk_body(node.body, depth + 1)
        orelse = node.orelse
        if len(orelse) == 1 and isinstance(orelse[0], ast.If):
            self._walk_if(orelse[0], depth, chained=True)
        elif orelse:
            self.total += 1
            self.walk_body(orelse, depth + 1)

    # -- expressions -----------------------------------------------------

    def walk_expression(self, node: ast.AST, depth: int) -> None:
        if isinstance(node, _SCOPE_RESET_NODES):
            return
        if isinstance(node, ast.BoolOp):
            self.total += 1
        elif isinstance(node, ast.IfExp):
            self.total += 1 + depth
        elif isinstance(node, ast.Call) and self._is_recursive_call(node):
            self.total += 1
        for child in ast.iter_child_nodes(node):
            self.walk_expression(child, depth)

    def _is_recursive_call(self, node: ast.Call) -> bool:
        name = self.function_name
        if name is None:
            return False
        callee = node.func
        if isinstance(callee, ast.Name):
            return callee.id == name
        return (
            isinstance(callee, ast.Attribute)
            and callee.attr == name
            and isinstance(callee.value, ast.Name)
            and callee.value.id in {"self", "cls"}
        )


def cognitive_complexity(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> int:
    """Return the documented cognitive complexity of one function-like node."""
    if isinstance(node, ast.Lambda):
        counter = CognitiveComplexityCounter(None)
        counter.walk_expression(node.body, 0)
        return counter.total
    counter = CognitiveComplexityCounter(node.name)
    counter.walk_body(node.body, 0)
    return counter.total


def distribution(values: Iterable[int]) -> dict[str, int]:
    """Bucket cognitive values for the evidence payload (deterministic order-free)."""
    materialized = list(values)
    return {
        "le_15": sum(value <= THRESHOLD for value in materialized),
        "16_to_30": sum(16 <= value <= 30 for value in materialized),
        "31_to_60": sum(31 <= value <= 60 for value in materialized),
        "gt_60": sum(value > 60 for value in materialized),
    }
