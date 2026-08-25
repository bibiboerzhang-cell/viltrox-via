#!/usr/bin/env python3
"""Statically collect the GET endpoints the frontend bundle can call.

Why this exists
---------------
``backend/app/core/release_validation.py`` keeps a reviewed read-only
allowlist.  While a release is being proven the fence answers 503 to every
request outside that list.  Twice in 2026 a board shipped a poll against an
endpoint nobody had registered (``/my-kol/sku-play-overview`` and
``/api/admin/system/models``), the browser console gate collected the 503s and
the deploy was rejected — after the release had already been built.

This module turns "which GET paths does the frontend actually call" into a
statically decidable question so ``tests/test_release_read_whitelist_coverage``
can red the diff long before a deploy starts.

Scope and honesty rules
-----------------------
* Only **statically determined literals** are reported as covered call sites.
  Anything assembled at runtime (``${id}`` inside the path, a path handed in as
  a function parameter, a spread ``init``) is *skipped and counted*, never
  guessed at, and the counts are part of the test's report so the blind spot
  stays visible instead of looking like coverage.
* Only GET-shaped calls are collected.  The allowlist is a read-only contract;
  POST/PUT/DELETE are fenced by design and are out of scope.
* The module never imports frontend tooling and never runs node.  It is a
  character-level scanner over the TypeScript sources: a JS-aware masker blanks
  comments, string bodies and regex literals so call sites, argument
  boundaries and object keys are located in *code* context only.

Nothing here deletes, writes or deploys anything.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# Helpers whose first argument is a request path.  ``buildApiUrl`` is not a
# fetcher on its own — it is unwrapped when it appears as a path expression —
# but ``useCachedGet`` and ``cachedApiFetch`` are GET-only by construction.
FETCH_HELPERS: dict[str, str] = {
    "apiFetch": "init",  # method comes from the init object, default GET
    "cachedApiFetch": "init",
    "fetch": "init",
    "useCachedGet": "always-get",
}
_PATH_WRAPPERS = ("buildApiUrl",)

_MASK = "\x01"
_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_METHOD_LITERAL_RE = re.compile(r"(?<![A-Za-z0-9_$])method\s*:\s*[\"'`]([A-Za-z]+)[\"'`]")
_METHOD_KEY_RE = re.compile(r"(?<![A-Za-z0-9_$])method\s*:")
_SPREAD_RE = re.compile(r"\.\.\.")
_DECL_RE = re.compile(
    r"(?<![A-Za-z0-9_$])(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*"
    r"(?::[^=;\n]*)?=\s*"
)
_REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%~^<>") | {"\n"}
_REGEX_KEYWORDS = frozenset(
    {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "case", "do", "else", "yield", "await"}
)
_SOURCE_SUFFIXES = (".ts", ".tsx")
_SKIP_NAME_MARKERS = (".test.", ".spec.", ".stories.", ".d.ts")
_SKIP_DIR_PARTS = frozenset({"__tests__", "__mocks__", "node_modules", "dist", "coverage"})


@dataclass(frozen=True)
class EndpointCall:
    """One statically resolved GET call site."""

    path: str
    helper: str
    file: str
    line: int
    raw: str


@dataclass(frozen=True)
class SkippedCall:
    """A call site we refuse to guess at, kept so the gap stays countable."""

    helper: str
    file: str
    line: int
    reason: str
    raw: str


@dataclass(frozen=True)
class ScanResult:
    get_calls: tuple[EndpointCall, ...] = ()
    skipped: tuple[SkippedCall, ...] = ()
    non_get_calls: int = 0
    files_scanned: int = 0
    _paths: frozenset[str] = field(default=frozenset(), repr=False)

    @property
    def paths(self) -> frozenset[str]:
        return frozenset(call.path for call in self.get_calls)

    @property
    def total_call_sites(self) -> int:
        return len(self.get_calls) + len(self.skipped) + self.non_get_calls

    def static_coverage(self) -> float:
        """Share of in-scope (GET-or-unknown) call sites we could decide."""

        decidable = len(self.get_calls) + len(self.skipped)
        return (len(self.get_calls) / decidable) if decidable else 1.0

    def callers_for(self, path: str) -> tuple[EndpointCall, ...]:
        return tuple(call for call in self.get_calls if call.path == path)

    def skipped_by_reason(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.skipped:
            counts[item.reason] = counts.get(item.reason, 0) + 1
        return dict(sorted(counts.items()))


# --------------------------------------------------------------------------
# JS-aware masking
# --------------------------------------------------------------------------


def mask_source(source: str) -> str:
    """Return ``source`` with comments/string bodies/regex bodies blanked.

    Line structure and every code-context character are preserved so offsets
    map 1:1 back onto the original text.  Template substitutions (``${...}``)
    stay visible as code because they may contain nested calls.
    """

    out = list(source)
    _mask_code(source, out, 0, stop_at_brace=False)
    return "".join(out)


def _blank(out: list[str], start: int, end: int, source: str) -> None:
    for index in range(start, end):
        if source[index] != "\n":
            out[index] = _MASK


def _prev_code_char(source: str, index: int) -> str:
    scan = index - 1
    while scan >= 0 and source[scan] in " \t":
        scan -= 1
    return source[scan] if scan >= 0 else "\n"


def _looks_like_regex(source: str, index: int) -> bool:
    previous = _prev_code_char(source, index)
    if previous in _REGEX_PRECEDERS:
        return True
    if previous.isalnum() or previous in "_$)]":
        # ``x / y`` or ``foo() / 2`` — division, unless the word is a keyword.
        end = index
        while end > 0 and source[end - 1] in " \t":
            end -= 1
        start = end
        while start > 0 and (source[start - 1].isalnum() or source[start - 1] in "_$"):
            start -= 1
        return source[start:end] in _REGEX_KEYWORDS
    return False


def _mask_regex(source: str, out: list[str], index: int) -> int:
    scan = index + 1
    in_class = False
    while scan < len(source):
        char = source[scan]
        if char == "\\":
            scan += 2
            continue
        if char == "\n":
            return index + 1  # not a regex after all; treat as a bare slash
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            _blank(out, index + 1, scan, source)
            scan += 1
            while scan < len(source) and source[scan].isalpha():
                scan += 1
            return scan
        scan += 1
    return index + 1


def _mask_string(source: str, out: list[str], index: int) -> int:
    quote = source[index]
    scan = index + 1
    while scan < len(source):
        char = source[scan]
        if char == "\\":
            scan += 2
            continue
        if char == quote or char == "\n":
            break
        scan += 1
    _blank(out, index + 1, min(scan, len(source)), source)
    return min(scan + 1, len(source))


def _mask_template(source: str, out: list[str], index: int) -> int:
    scan = index + 1
    body_start = scan
    while scan < len(source):
        char = source[scan]
        if char == "\\":
            _blank(out, scan, min(scan + 2, len(source)), source)
            scan += 2
            continue
        if char == "`":
            _blank(out, body_start, scan, source)
            return scan + 1
        if char == "$" and scan + 1 < len(source) and source[scan + 1] == "{":
            _blank(out, body_start, scan, source)
            scan = _mask_code(source, out, scan + 2, stop_at_brace=True)
            if scan < len(source) and source[scan] == "}":
                scan += 1
            body_start = scan
            continue
        scan += 1
    _blank(out, body_start, len(source), source)
    return len(source)


def _mask_code(source: str, out: list[str], index: int, *, stop_at_brace: bool) -> int:
    depth = 0
    length = len(source)
    while index < length:
        char = source[index]
        if stop_at_brace:
            if char == "{":
                depth += 1
            elif char == "}":
                if depth == 0:
                    return index
                depth -= 1
        if char == "/" and index + 1 < length:
            following = source[index + 1]
            if following == "/":
                end = source.find("\n", index)
                end = length if end < 0 else end
                _blank(out, index, end, source)
                index = end
                continue
            if following == "*":
                end = source.find("*/", index + 2)
                end = length if end < 0 else end + 2
                _blank(out, index, end, source)
                index = end
                continue
            if _looks_like_regex(source, index):
                index = _mask_regex(source, out, index)
                continue
        if char in "\"'":
            index = _mask_string(source, out, index)
            continue
        if char == "`":
            index = _mask_template(source, out, index)
            continue
        index += 1
    return index


# --------------------------------------------------------------------------
# Expression resolution
# --------------------------------------------------------------------------


def _strip_wrappers(expr: str) -> str:
    text = expr.strip()
    changed = True
    while changed:
        changed = False
        for suffix in (" as const", " as string", " satisfies string"):
            if text.endswith(suffix):
                text = text[: -len(suffix)].strip()
                changed = True
        if text.startswith("(") and text.endswith(")") and _balanced(text[1:-1]):
            text = text[1:-1].strip()
            changed = True
    return text


def _balanced(text: str) -> bool:
    depth = 0
    for char in text:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _static_template_path(body: str) -> str | None:
    """Return the static path of a template literal, or ``None``.

    ``/api/x?limit=${n}`` is static: everything before the first ``?`` is
    fixed, and only the query varies.  ``/api/x/${id}`` is not.
    """

    first_sub = body.find("${")
    if first_sub < 0:
        return body
    question = body.find("?")
    if 0 <= question < first_sub:
        return body[:question]
    return None


def _literal_value(expr: str) -> tuple[str | None, bool]:
    """Return ``(value, was_literal)`` for a quoted/backtick expression."""

    if len(expr) >= 2 and expr[0] == expr[-1] and expr[0] in "\"'":
        body = expr[1:-1]
        if expr[0] in body.replace("\\" + expr[0], ""):
            return None, False
        return body.replace("\\" + expr[0], expr[0]), True
    if len(expr) >= 2 and expr[0] == "`" and expr[-1] == "`":
        return _static_template_path(expr[1:-1]), True
    return None, False


def resolve_path_expression(
    expr: str,
    constants: Mapping[str, str],
    *,
    _seen: frozenset[str] = frozenset(),
) -> tuple[str | None, str]:
    """Resolve a path expression to a literal path, or explain why not.

    Returns ``(path, reason)``.  ``path`` is ``None`` whenever the value is not
    statically decidable; ``reason`` then names the blind spot.
    """

    text = _strip_wrappers(expr)
    if not text:
        return None, "empty_expression"
    for wrapper in _PATH_WRAPPERS:
        if text.startswith(wrapper) and text.endswith(")"):
            inner = text[len(wrapper) :].lstrip()
            if inner.startswith("("):
                return resolve_path_expression(inner[1:-1], constants, _seen=_seen)
    value, was_literal = _literal_value(text)
    if was_literal:
        if value is None:
            return None, "interpolated_path"
        return value, "literal"
    if _IDENTIFIER_RE.fullmatch(text):
        if text in _seen:
            return None, "cyclic_constant"
        if text in constants:
            return resolve_path_expression(
                constants[text], constants, _seen=_seen | {text}
            )
        return None, "runtime_identifier"
    return None, "computed_expression"


def collect_constants(source: str, masked: str) -> dict[str, str]:
    """Map file-local ``const NAME = <expr>`` bindings to their raw expression."""

    constants: dict[str, str] = {}
    for match in _DECL_RE.finditer(masked):
        name = match.group(1)
        start = match.end()
        end = _expression_end(masked, start)
        expression = source[start:end].strip()
        if expression and name not in constants:
            constants[name] = expression
    return constants


def _expression_end(masked: str, start: int) -> int:
    depth = 0
    index = start
    while index < len(masked):
        char = masked[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                return index
            depth -= 1
        elif depth == 0 and char in ";\n":
            return index
        index += 1
    return len(masked)


# --------------------------------------------------------------------------
# Call-site extraction
# --------------------------------------------------------------------------


def _call_open_paren(masked: str, index: int) -> int | None:
    """Skip an optional generic argument list and return the ``(`` offset."""

    scan = index
    length = len(masked)
    while scan < length and masked[scan] in " \t\n":
        scan += 1
    if scan < length and masked[scan] == "<":
        depth = 0
        while scan < length:
            char = masked[scan]
            if char == "<":
                depth += 1
            elif char == ">":
                depth -= 1
                if depth == 0:
                    scan += 1
                    break
            elif char in ";()":
                return None
            scan += 1
        while scan < length and masked[scan] in " \t\n":
            scan += 1
    return scan if scan < length and masked[scan] == "(" else None


def split_arguments(masked: str, open_paren: int) -> tuple[list[tuple[int, int]], int]:
    """Return ``([(start, end), ...], close_paren)`` for one call's arguments."""

    depth = 0
    index = open_paren
    spans: list[tuple[int, int]] = []
    current = open_paren + 1
    while index < len(masked):
        char = masked[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                spans.append((current, index))
                return spans, index
        elif char == "," and depth == 1:
            spans.append((current, index))
            current = index + 1
        index += 1
    return spans, len(masked) - 1


def _method_of(source: str, spans: Sequence[tuple[int, int]], kind: str) -> str:
    if kind == "always-get":
        return "GET"
    tail = " ".join(source[start:end] for start, end in spans[1:])
    literal = _METHOD_LITERAL_RE.search(tail)
    if literal:
        return literal.group(1).strip().upper()
    if _METHOD_KEY_RE.search(tail):
        return "UNKNOWN"
    if _SPREAD_RE.search(tail):
        return "UNKNOWN"
    return "GET"


def _is_declaration_site(masked: str, start: int) -> bool:
    """True when the identifier is being *defined*, not called.

    ``export async function apiFetch<T>(path: string, ...)`` otherwise reads
    as a call site whose first argument is ``path: string``.
    """

    head = masked[max(0, start - 64) : start]
    return bool(re.search(r"(?:function|class)\s*\*?\s*$", head))


def _helper_at(masked: str, match: re.Match[str]) -> str | None:
    name = match.group(1)
    start = match.start(1)
    if _is_declaration_site(masked, start):
        return None
    if start > 0 and masked[start - 1] == ".":
        owner_end = start - 1
        owner_start = owner_end
        while owner_start > 0 and (masked[owner_start - 1].isalnum() or masked[owner_start - 1] in "_$"):
            owner_start -= 1
        if masked[owner_start:owner_end] not in {"window", "globalThis", "self", "global"}:
            return None
    elif start > 0 and (masked[start - 1].isalnum() or masked[start - 1] in "_$"):
        return None
    return name


_HELPER_RE = re.compile(r"(" + "|".join(sorted(FETCH_HELPERS, key=len, reverse=True)) + r")")


def scan_source(source: str, *, relative_path: str) -> tuple[list[EndpointCall], list[SkippedCall], int]:
    """Scan one TypeScript source for GET call sites."""

    masked = mask_source(source)
    constants = collect_constants(source, masked)
    found: list[EndpointCall] = []
    skipped: list[SkippedCall] = []
    non_get = 0
    for match in _HELPER_RE.finditer(masked):
        helper = _helper_at(masked, match)
        if helper is None:
            continue
        open_paren = _call_open_paren(masked, match.end(1))
        if open_paren is None:
            continue
        spans, _close = split_arguments(masked, open_paren)
        if not spans:
            continue
        line = source.count("\n", 0, match.start(1)) + 1
        method = _method_of(source, spans, FETCH_HELPERS[helper])
        raw = " ".join(source[spans[0][0] : spans[0][1]].split())[:120]
        if method not in {"GET", "HEAD", "UNKNOWN"}:
            non_get += 1
            continue
        path, reason = resolve_path_expression(source[spans[0][0] : spans[0][1]], constants)
        if path is None or not path.startswith("/"):
            skipped.append(
                SkippedCall(
                    helper=helper,
                    file=relative_path,
                    line=line,
                    reason="external_url" if path is not None else reason,
                    raw=raw,
                )
            )
            continue
        if method == "UNKNOWN":
            # Fail safe: an undecidable method is reported as a gap, never as a
            # covered GET and never silently dropped.
            skipped.append(
                SkippedCall(
                    helper=helper,
                    file=relative_path,
                    line=line,
                    reason="runtime_method",
                    raw=raw,
                )
            )
            continue
        found.append(
            EndpointCall(
                path=normalize_path(path),
                helper=helper,
                file=relative_path,
                line=line,
                raw=raw,
            )
        )
    return found, skipped, non_get


def normalize_path(path: str) -> str:
    trimmed = path.split("#", 1)[0].split("?", 1)[0].strip()
    if not trimmed.startswith("/"):
        trimmed = "/" + trimmed
    if len(trimmed) > 1 and trimmed.endswith("/"):
        trimmed = trimmed.rstrip("/") or "/"
    return trimmed


def iter_source_files(root: Path) -> Iterator[Path]:
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file() or candidate.suffix not in _SOURCE_SUFFIXES:
            continue
        if any(part in _SKIP_DIR_PARTS for part in candidate.parts):
            continue
        if any(marker in candidate.name for marker in _SKIP_NAME_MARKERS):
            continue
        yield candidate


def scan_frontend(root: Path, *, repo_root: Path | None = None) -> ScanResult:
    """Scan every non-test TypeScript source under ``root``."""

    base = repo_root or root
    calls: list[EndpointCall] = []
    skipped: list[SkippedCall] = []
    non_get = 0
    files = 0
    for source_file in iter_source_files(root):
        files += 1
        try:
            text = source_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:  # pragma: no cover - fail loud
            raise RuntimeError(f"unreadable frontend source: {source_file}") from exc
        try:
            relative = str(source_file.relative_to(base))
        except ValueError:
            relative = str(source_file)
        file_calls, file_skipped, file_non_get = scan_source(text, relative_path=relative)
        calls.extend(file_calls)
        skipped.extend(file_skipped)
        non_get += file_non_get
    return ScanResult(
        get_calls=tuple(calls),
        skipped=tuple(skipped),
        non_get_calls=non_get,
        files_scanned=files,
    )


def format_gap(paths: Iterable[str], result: ScanResult) -> str:
    """Render an actionable failure message naming path and calling file."""

    lines: list[str] = []
    for path in sorted(paths):
        callers = result.callers_for(path)
        where = ", ".join(f"{call.file}:{call.line}" for call in callers[:4])
        if len(callers) > 4:
            where += f" (+{len(callers) - 4} more)"
        lines.append(f"  {path}\n      called by {where or 'unknown'}")
    return "\n".join(lines)


__all__ = [
    "EndpointCall",
    "FETCH_HELPERS",
    "ScanResult",
    "SkippedCall",
    "collect_constants",
    "format_gap",
    "iter_source_files",
    "mask_source",
    "normalize_path",
    "resolve_path_expression",
    "scan_frontend",
    "scan_source",
    "split_arguments",
]
