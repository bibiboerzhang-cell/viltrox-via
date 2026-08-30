"""Contract v1.1 duplication-rate counter (``stdlib-tokenize-w-shingling-50-v1``).

Frozen methodology (docs/vkpi/engineering-health-score-contract-v1.json,
``code_evidence_methodology.duplication_rate``):

* corpus: production Python under backend/app taken from the collector source
  snapshot (tests, migrations, generated code excluded by construction);
* token stream: stdlib ``tokenize`` per file; every token kind is kept except
  COMMENT, NL, NEWLINE, INDENT, DEDENT, ENCODING, and ENDMARKER; token
  identity is the exact ``(type, string)`` pair — no identifier, literal, or
  whitespace normalization (type-1 exact clones only);
* shingle: sliding window of 50 consecutive kept tokens, stride 1, never
  crossing a file boundary; files with fewer than 50 kept tokens produce no
  shingles but their tokens stay in the denominator;
* duplicate: a shingle is duplicated when its exact token sequence occurs at
  two or more distinct (file, start-offset) positions corpus-wide;
  occurrences may overlap and same-file repeats count;
* rate: ``duplication_rate`` = kept tokens covered by at least one duplicated
  shingle / total kept tokens in the corpus;
* determinism: the 64-bit hash index is only an accelerator — every hash hit
  is verified by exact token-id sequence comparison before it counts, so
  identical snapshot bytes produce identical rates on every run and a hash
  collision can never fabricate a duplicate.

Any file the stdlib tokenizer rejects is recorded as an explicit failure and
fails the corpus closed (rate ``None``); nothing is estimated or skipped
silently.
"""
from __future__ import annotations

import io
import tokenize
from array import array
from dataclasses import asdict, dataclass
from hashlib import blake2b
from typing import Any, Callable, Sequence

METHODOLOGY_ID = "stdlib-tokenize-w-shingling-50-v1"
WINDOW_TOKENS = 50
STRIDE = 1
METRIC_SOURCE = "collector://vkpi-engineering-health/v1/python-tokenize-duplication"
EXCLUDED_TOKEN_TYPES = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)
EXCLUDED_TOKEN_TYPE_NAMES = tuple(sorted(tokenize.tok_name[kind] for kind in EXCLUDED_TOKEN_TYPES))
DEFINITION = (
    "stdlib tokenize per production backend/app Python file; kept tokens are every kind except "
    "COMMENT, NL, NEWLINE, INDENT, DEDENT, ENCODING, and ENDMARKER with exact (type, string) "
    "identity (type-1 clones only); 50-token sliding windows, stride 1, never crossing a file "
    "boundary; a window is duplicated when its exact token sequence occurs at two or more "
    "distinct (file, start-offset) positions corpus-wide (overlaps and same-file repeats count); "
    "duplication_rate = kept tokens covered by at least one duplicated window / total kept tokens"
)
_DIGEST_SIZE = 8
_ITEM_SIZE = array("i").itemsize
_SPAN_BYTES = WINDOW_TOKENS * _ITEM_SIZE
_COVERED = b"\x01" * WINDOW_TOKENS
_TOKENIZE_ERRORS = (SyntaxError, UnicodeDecodeError, tokenize.TokenError)


@dataclass(frozen=True)
class TokenizeFailure:
    path: str
    error_type: str
    line: int | None


@dataclass(frozen=True)
class FileDuplication:
    path: str
    kept_token_count: int
    shingle_count: int
    duplicated_token_count: int


@dataclass(frozen=True)
class DuplicationResult:
    file_count: int
    files_with_shingles: int
    total_kept_tokens: int
    shingle_count: int
    duplicated_shingle_count: int
    distinct_duplicated_shingle_count: int
    duplicated_token_count: int
    duplication_rate: float | None
    files: tuple[FileDuplication, ...]
    failures: tuple[TokenizeFailure, ...]

    @property
    def complete(self) -> bool:
        return not self.failures


def kept_token_pairs(content: bytes) -> list[tuple[int, str]]:
    """Exact (type, string) kept-token stream of one file, in source order."""
    pairs: list[tuple[int, str]] = []
    for token in tokenize.tokenize(io.BytesIO(content).readline):
        if token.type not in EXCLUDED_TOKEN_TYPES:
            pairs.append((token.type, token.string))
    return pairs


def _failure_line(exc: BaseException) -> int | None:
    """Best-effort 1-based line of a tokenizer rejection (None when unstated)."""
    if isinstance(exc, SyntaxError) and exc.lineno is not None:
        return int(exc.lineno)
    args = getattr(exc, "args", ())
    if len(args) == 2 and isinstance(args[1], tuple) and args[1] and isinstance(args[1][0], int):
        return int(args[1][0])
    return None


def _intern(table: dict[tuple[int, str], int], pair: tuple[int, str]) -> int:
    identity = table.get(pair)
    if identity is None:
        identity = len(table)
        table[pair] = identity
    return identity


def _mark_duplicates(blobs: list[bytes], counts: list[int]) -> tuple[list[bytearray], int, int]:
    """Mark every kept token covered by a duplicated 50-token window.

    Returns per-file coverage bitmaps plus (duplicated window positions,
    distinct duplicated window sequences).  The blake2b-64 key only routes a
    window to its bucket; equality is decided by exact token-id sequence
    comparison, so the counters are hash-collision-proof.
    """
    coverage = [bytearray(count) for count in counts]
    index: dict[int, list[list[int]]] = {}
    duplicated_positions = 0
    distinct_duplicates = 0
    for file_index, blob in enumerate(blobs):
        for offset in range(counts[file_index] - WINDOW_TOKENS + 1):
            start = offset * _ITEM_SIZE
            window = blob[start : start + _SPAN_BYTES]
            key = int.from_bytes(blake2b(window, digest_size=_DIGEST_SIZE).digest(), "big")
            bucket = index.get(key)
            if bucket is None:
                index[key] = [[file_index, offset, 1]]
                continue
            entry = _matching_entry(bucket, blobs, window)
            if entry is None:
                bucket.append([file_index, offset, 1])
                continue
            if entry[2] == 1:  # First repeat: the original position becomes duplicated too.
                distinct_duplicates += 1
                duplicated_positions += 1
                coverage[entry[0]][entry[1] : entry[1] + WINDOW_TOKENS] = _COVERED
            entry[2] += 1
            duplicated_positions += 1
            coverage[file_index][offset : offset + WINDOW_TOKENS] = _COVERED
    return coverage, duplicated_positions, distinct_duplicates


def _matching_entry(bucket: list[list[int]], blobs: list[bytes], window: bytes) -> list[int] | None:
    for entry in bucket:
        start = entry[1] * _ITEM_SIZE
        if blobs[entry[0]][start : start + _SPAN_BYTES] == window:
            return entry
    return None


def measure_duplication(sources: Sequence[tuple[str, bytes]]) -> DuplicationResult:
    """Measure the contract duplication rate over (relative_path, bytes) pairs."""
    ordered = sorted(sources, key=lambda item: item[0])
    table: dict[tuple[int, str], int] = {}
    paths: list[str] = []
    counts: list[int] = []
    blobs: list[bytes] = []
    failures: list[TokenizeFailure] = []
    for path, content in ordered:
        try:
            pairs = kept_token_pairs(content)
        except _TOKENIZE_ERRORS as exc:
            failures.append(TokenizeFailure(path, type(exc).__name__, _failure_line(exc)))
            continue
        identifiers = array("i", (_intern(table, pair) for pair in pairs))
        paths.append(path)
        counts.append(len(identifiers))
        blobs.append(identifiers.tobytes())
    coverage, duplicated_positions, distinct_duplicates = _mark_duplicates(blobs, counts)
    return _assemble(len(ordered), paths, counts, coverage, duplicated_positions, distinct_duplicates, failures)


def _assemble(
    file_count: int,
    paths: list[str],
    counts: list[int],
    coverage: list[bytearray],
    duplicated_positions: int,
    distinct_duplicates: int,
    failures: list[TokenizeFailure],
) -> DuplicationResult:
    rows = tuple(
        FileDuplication(
            path=paths[position],
            kept_token_count=counts[position],
            shingle_count=max(0, counts[position] - WINDOW_TOKENS + 1),
            duplicated_token_count=coverage[position].count(1),
        )
        for position in range(len(paths))
    )
    total = sum(counts)
    covered = sum(row.duplicated_token_count for row in rows)
    complete = not failures
    return DuplicationResult(
        file_count=file_count,
        files_with_shingles=sum(1 for row in rows if row.shingle_count),
        total_kept_tokens=total,
        shingle_count=sum(row.shingle_count for row in rows),
        duplicated_shingle_count=duplicated_positions,
        distinct_duplicated_shingle_count=distinct_duplicates,
        duplicated_token_count=covered,
        duplication_rate=round(covered / total, 8) if total and complete else None,
        files=rows,
        failures=tuple(sorted(failures, key=lambda item: item.path)),
    )


def top_duplicated_files(result: DuplicationResult, limit: int) -> list[dict[str, Any]]:
    """Files carrying duplicated tokens, heaviest first (honest triplet trace)."""
    rows = sorted(
        (row for row in result.files if row.duplicated_token_count),
        key=lambda row: (-row.duplicated_token_count, row.path),
    )
    return [
        {
            "path": row.path,
            "kept_token_count": row.kept_token_count,
            "duplicated_token_count": row.duplicated_token_count,
            "duplicated_token_ratio": round(row.duplicated_token_count / row.kept_token_count, 8),
        }
        for row in rows[:limit]
    ]


def observation(python_files: Sequence[Any], *, snapshot_complete: bool, top_limit: int) -> dict[str, Any]:
    """Build the collector ``python_duplication`` observation block.

    ``python_files`` are snapshot SourceFile records (``relative_path`` +
    immutable ``content`` bytes).  The rate is withheld (``None``) unless the
    snapshot is complete, the corpus is non-empty, and every file tokenized.
    """
    result = measure_duplication([(item.relative_path, item.content) for item in python_files])
    complete = snapshot_complete and result.complete and result.total_kept_tokens > 0
    return {
        "status": "observed" if complete else "unknown",
        "methodology_id": METHODOLOGY_ID,
        "definition": DEFINITION,
        "window_tokens": WINDOW_TOKENS,
        "stride": STRIDE,
        "excluded_token_types": list(EXCLUDED_TOKEN_TYPE_NAMES),
        "python_file_count": result.file_count,
        "files_with_shingles": result.files_with_shingles,
        "total_kept_tokens": result.total_kept_tokens,
        "shingle_count": result.shingle_count,
        "duplicated_shingle_count": result.duplicated_shingle_count,
        "distinct_duplicated_shingle_count": result.distinct_duplicated_shingle_count,
        "duplicated_token_count": result.duplicated_token_count,
        "duplication_rate": result.duplication_rate if complete else None,
        "top_files": top_duplicated_files(result, top_limit),
        "tokenize_errors": [asdict(item) for item in result.failures],
    }


def score_metric(
    observation_block: dict[str, Any],
    *,
    observed_at: str,
    stable: bool,
    drift_reason: str,
    observed: Callable[..., dict[str, Any]],
    unknown: Callable[[str, str], dict[str, Any]],
) -> dict[str, Any]:
    """Wire the observation into the code.duplication_rate evidence metric."""
    if stable and observation_block["status"] == "observed":
        return observed(
            observation_block["duplication_rate"],
            observed_at,
            METRIC_SOURCE,
            sample_count=observation_block["total_kept_tokens"],
            details={
                "methodology_id": observation_block["methodology_id"],
                "window_tokens": observation_block["window_tokens"],
                "python_file_count": observation_block["python_file_count"],
                "duplicated_token_count": observation_block["duplicated_token_count"],
                "duplicated_shingle_count": observation_block["duplicated_shingle_count"],
                "distinct_duplicated_shingle_count": observation_block["distinct_duplicated_shingle_count"],
                "top_files": observation_block["top_files"],
            },
        )
    reason = (
        drift_reason
        if not stable
        else "duplication corpus is empty or incomplete (snapshot or tokenize failure)"
    )
    return unknown(observed_at, reason)
