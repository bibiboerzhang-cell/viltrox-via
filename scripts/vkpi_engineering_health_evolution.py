#!/usr/bin/env python3
"""Build read-only, HEAD-bound Git-history evidence for evolution health.

The receipt deliberately fails closed until the repository has the contract's
full 180-day history window.  Working-tree content is never an input.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import itertools
import json
import math
import subprocess
import tempfile
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

try:
    from scripts.vkpi_engineering_health_evolution_people import (
        BUS_FACTOR_SHARE,
        CORE_DOMAINS,
        MIN_QUALIFIED_INDEPENDENT_REVIEWS,
        MIN_QUALIFIED_MERGED_PRS,
        MIN_QUALIFIED_OPERATIONAL_EVIDENCE,
        QUALIFICATION_SCHEMA_VERSION,
        QUALIFIED_CONTRIBUTION_SHARE,
        Change,
        Commit,
        _bus_factor,
        _canonical_sha256,
        _domains,
        _eligible_source,
        _identity,
        _identity_ambiguities,
        _is_bot,
        _is_shared_account,
        _qualification_evidence as _people_qualification_evidence,
        _ranked_bus_factor,
        _reference_list,
    )
    from scripts.stdout_utils import out as stdout_out
    from scripts.vkpi_engineering_health_collect import _DecisionCounter
except ModuleNotFoundError:  # Direct execution: scripts/ is sys.path[0].
    from vkpi_engineering_health_evolution_people import (
        BUS_FACTOR_SHARE,
        CORE_DOMAINS,
        MIN_QUALIFIED_INDEPENDENT_REVIEWS,
        MIN_QUALIFIED_MERGED_PRS,
        MIN_QUALIFIED_OPERATIONAL_EVIDENCE,
        QUALIFICATION_SCHEMA_VERSION,
        QUALIFIED_CONTRIBUTION_SHARE,
        Change,
        Commit,
        _bus_factor,
        _canonical_sha256,
        _domains,
        _eligible_source,
        _identity,
        _identity_ambiguities,
        _is_bot,
        _is_shared_account,
        _qualification_evidence as _people_qualification_evidence,
        _ranked_bus_factor,
        _reference_list,
    )
    from stdout_utils import out as stdout_out
    from vkpi_engineering_health_collect import _DecisionCounter


ALGORITHM_VERSION = "vkpi-evolution-git-v5"
WINDOW_DAYS = 180
TOP_PAIR_LIMIT = 20
MIN_PAIR_UNION_COMMITS = 10
MIN_PAIR_COCHANGE_COMMITS = 3
HOTSPOT_MIN_WINDOW_COMMITS = 10
HOTSPOT_UNHEALTHY_MEAN_CC = 12.0
HOTSPOT_PRODUCTION_ROOTS = ("backend/app/", "frontend/src/")


class EvolutionEvidenceError(ValueError):
    """Raised when Git history cannot produce a trustworthy receipt."""


def _git(root: Path, *args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd or root, capture_output=True, text=True, check=False
    )
    if completed.returncode:
        raise EvolutionEvidenceError(
            f"git command failed ({' '.join(args)}): {completed.stderr.strip()[:240]}"
        )
    return completed.stdout.strip()


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvolutionEvidenceError(f"invalid Git timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise EvolutionEvidenceError("Git timestamp lacks timezone")
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_log(raw: str) -> list[Commit]:
    commits: list[Commit] = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        lines = record.splitlines()
        header = lines[0].split("\x1f")
        if len(header) not in {4, 6}:
            raise EvolutionEvidenceError("unexpected git log header")
        if len(header) == 6:
            (
                oid,
                author_name,
                author_email,
                raw_author_name,
                raw_author_email,
                authored_at,
            ) = header
        else:
            oid, author_name, author_email, authored_at = header
            raw_author_name = author_name
            raw_author_email = author_email
        changes: list[Change] = []
        for line in lines[1:]:
            if not line.strip():
                continue
            columns = line.split("\t", 2)
            if len(columns) != 3:
                raise EvolutionEvidenceError("unexpected git numstat row")
            added, deleted, path = columns
            if added == "-" or deleted == "-":
                continue
            changes.append(Change(path=path, added=int(added), deleted=int(deleted)))
        commits.append(
            Commit(
                oid=oid,
                author_name=author_name,
                author_email=author_email,
                authored_at=_timestamp(authored_at),
                changes=tuple(changes),
                raw_author_name=raw_author_name,
                raw_author_email=raw_author_email,
            )
        )
    return commits


def _history(root: Path) -> tuple[list[Commit], tuple[str, ...]]:
    # ``git log --use-mailmap`` normally reads ``.mailmap`` from the working
    # tree, which would make this supposedly HEAD-bound receipt depend on
    # mutable, uncommitted bytes.  Disable that implicit file and ask Git to
    # resolve aliases only from the blob reachable at HEAD.  A repository
    # without a committed .mailmap is valid and simply has no aliases yet.
    recorded_command = (
        "git",
        "--git-dir=<resolved-git-dir>",
        "--work-tree=<isolated-empty-worktree>",
        "-c",
        "mailmap.file=/dev/null",
        "-c",
        "mailmap.blob=HEAD:.mailmap",
        "log",
        "--no-merges",
        "--use-mailmap",
        "--no-renames",
        # Keep both mailmapped and original identities in one immutable log.
        # The original fields prevent a committed mailmap from laundering a
        # bot or shared account into a human maintainer identity.
        "--format=%x1e%H%x1f%aN%x1f%aE%x1f%an%x1f%ae%x1f%aI",
        "--numstat",
        "HEAD",
        "--",
    )
    git_dir = _git(root, "rev-parse", "--absolute-git-dir")
    with tempfile.TemporaryDirectory(prefix="vkpi-evolution-empty-worktree-") as empty_worktree:
        actual_args = (
            f"--git-dir={git_dir}",
            f"--work-tree={empty_worktree}",
            *recorded_command[3:],
        )
        raw = _git(root, *actual_args, cwd=Path(empty_worktree))
    return _parse_log(raw), recorded_command


def _head_mailmap(root: Path) -> dict[str, Any]:
    completed = subprocess.run(
        ["git", "show", "HEAD:.mailmap"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        return {
            "source": "HEAD:.mailmap",
            "committed": False,
            "sha256": None,
            "entry_count": 0,
        }
    content = completed.stdout
    entries = [
        line
        for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return {
        "source": "HEAD:.mailmap",
        "committed": True,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "entry_count": len(entries),
    }


def _qualification_evidence(
    payload: dict[str, Any] | None,
    *,
    head: str,
    source: str,
) -> tuple[dict[str, set[str]] | None, dict[str, Any]]:
    try:
        return _people_qualification_evidence(
            payload, head=head, source=source, timestamp_parser=_timestamp
        )
    except ValueError as exc:
        raise EvolutionEvidenceError(str(exc)) from exc


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _temporal_counts(
    commits: Iterable[Commit],
) -> tuple[dict[str, set[str]], Counter[tuple[str, str]], int, int]:
    file_commits: dict[str, set[str]] = defaultdict(set)
    pair_counts: Counter[tuple[str, str]] = Counter()
    eligible_commit_count = 0
    multi_file_commit_count = 0
    for commit in commits:
        files = sorted(
            {
                change.path
                for change in commit.changes
                if _eligible_source(change.path) and _domains(change.path)
            }
        )
        if files:
            eligible_commit_count += 1
        if len(files) >= 2:
            multi_file_commit_count += 1
        for path in files:
            file_commits[path].add(commit.oid)
        pair_counts.update(itertools.combinations(files, 2))
    return file_commits, pair_counts, eligible_commit_count, multi_file_commit_count


def _temporal_rows(
    file_commits: dict[str, set[str]], pair_counts: Counter[tuple[str, str]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (left, right), numerator in pair_counts.items():
        denominator = len(file_commits[left] | file_commits[right])
        coupling = numerator / denominator if denominator else 0.0
        rows.append(
            {
                "left": left,
                "right": right,
                "cochange_commits": numerator,
                "union_change_commits": denominator,
                "coupling": coupling,
                "domains": sorted(set(_domains(left)) | set(_domains(right))),
            }
        )
    rows.sort(key=lambda row: (-row["coupling"], -row["cochange_commits"], row["left"], row["right"]))
    return rows


def _temporal_coupling(commits: Iterable[Commit]) -> dict[str, Any]:
    file_commits, pair_counts, eligible_count, multi_file_count = _temporal_counts(commits)
    rows = _temporal_rows(file_commits, pair_counts)
    qualified = [
        row
        for row in rows
        if row["union_change_commits"] >= MIN_PAIR_UNION_COMMITS
        and row["cochange_commits"] >= MIN_PAIR_COCHANGE_COMMITS
    ]
    return {
        "formula": "same_non_merge_commits / commits_changing_either_file (Jaccard)",
        "qualification": {
            "minimum_union_change_commits": MIN_PAIR_UNION_COMMITS,
            "minimum_cochange_commits": MIN_PAIR_COCHANGE_COMMITS,
            "reason": "exclude one-off and low-support 100% pairs from the formal percentile",
        },
        "eligible_commit_count": eligible_count,
        "multi_file_commit_count": multi_file_count,
        "raw_pair_count": len(rows),
        "qualified_pair_count": len(qualified),
        "excluded_low_support_count": len(rows) - len(qualified),
        "p95_denominator_count": len(qualified),
        "p95": _nearest_rank([row["coupling"] for row in qualified], 0.95),
        "top_pairs": qualified[:TOP_PAIR_LIMIT],
        "raw_top_pairs": rows[:TOP_PAIR_LIMIT],
    }


def _hotspot_production_file(path: str) -> bool:
    return path.startswith(HOTSPOT_PRODUCTION_ROOTS) and _eligible_source(path)


def _hotspot_churn(commits: Iterable[Commit]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for commit in commits:
        touched = {
            change.path
            for change in commit.changes
            if _hotspot_production_file(change.path)
        }
        for path in touched:
            counts[path] += 1
    return counts


def _head_production_paths(root: Path) -> frozenset[str]:
    raw = _git(
        root, "ls-tree", "-r", "--name-only", "-z", "HEAD", "--",
        *(prefix.rstrip("/") for prefix in HOTSPOT_PRODUCTION_ROOTS),
    )
    return frozenset(part for part in raw.split("\x00") if part)


def _head_function_ccs(root: Path, path: str) -> tuple[list[int] | None, str | None]:
    """Return per-function CC values from the HEAD blob, or a parse failure."""
    completed = subprocess.run(
        ["git", "show", f"HEAD:{path}"], cwd=root, capture_output=True, check=False
    )
    if completed.returncode:
        raise EvolutionEvidenceError(
            f"cannot read HEAD blob for hotspot file {path}: "
            f"{completed.stderr.decode('utf-8', 'replace').strip()[:240]}"
        )
    try:
        tree = ast.parse(completed.stdout.decode("utf-8"), filename=path)
    except (UnicodeDecodeError, SyntaxError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:160]}"
    ccs: list[int] = []
    for node in ast.walk(tree):
        counter = _DecisionCounter()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for statement in node.body:
                counter.visit(statement)
        elif isinstance(node, ast.Lambda):
            counter.visit(node.body)
        else:
            continue
        ccs.append(1 + counter.decisions)
    return ccs, None


def _hotspot_rows(
    root: Path, hot: list[tuple[str, int]]
) -> tuple[list[dict[str, Any]], list[int], list[dict[str, str]]]:
    rows: list[dict[str, Any]] = []
    cc_pool: list[int] = []
    failures: list[dict[str, str]] = []
    for path, window_commits in hot:
        row: dict[str, Any] = {
            "path": path, "window_commits": window_commits,
            "function_count": None, "cc_mean": None, "cc_max": None,
            "unhealthy": False, "cc_status": "non_python_no_cc",
        }
        if path.endswith(".py"):
            ccs, failure = _head_function_ccs(root, path)
            if failure is not None:
                row["cc_status"] = "parse_failed"
                failures.append({"path": path, "error": failure})
            elif not ccs:
                row.update({"function_count": 0, "cc_status": "no_functions"})
            else:
                mean = sum(ccs) / len(ccs)
                row.update({
                    "function_count": len(ccs), "cc_mean": round(mean, 2),
                    "cc_max": max(ccs),
                    "unhealthy": mean > HOTSPOT_UNHEALTHY_MEAN_CC,
                    "cc_status": "computed",
                })
                cc_pool.extend(ccs)
        rows.append(row)
    return rows, cc_pool, failures


def _hotspot_analysis(root: Path, commits: Iterable[Commit]) -> dict[str, Any]:
    """Hotspot 口径甲(已拍板,逐字进合同 v1.1):

    - 热度:180 天窗内(与既有 bus/temporal 同一窗口机制)非 merge 提交次数 ≥10 的
      生产文件(backend/app/ 与 frontend/src/ 下源文件;排除 scripts/ 与 tests/);
    - unhealthy_hotspot_count = 热度命中 ∩ 文件内函数 CC 均值 >12 的文件数(CC 用
      collector 同款 _DecisionCounter 口径——从 vkpi_engineering_health_collect 导入
      复用,不自己再实现一份);
    - hotspot_cc_mean = 全部热点文件(热度命中,不管健康与否)内所有函数 CC 的均值,
      保留 2 位。

    实现补注(不改变口径):
    - 提交总体 = 窗口内全部非 merge 提交(与 bus/temporal 共用 ``_history`` 的
      HEAD-bound 窗口切片);源文件判定复用 ``_eligible_source``(排除测试文件与
      tests/dist/generated 等目录),scripts/ 由生产根前缀天然排除;
    - 热点文件必须仍存在于 HEAD(窗口内被删除的热文件单列于
      ``hot_paths_missing_from_head``,不计入两项指标);
    - CC 仅对 .py 文件可算(AST 口径);非 Python 热点文件计入热度命中与
      hot_file_count,但没有函数样本,既不进 CC 池也不可能进 unhealthy 交集;
    - 函数 CC 读 HEAD blob(git show HEAD:path),工作树永不作为输入;
    - 合同 v1.1 对这两项不设 minimum_samples,窗口不足 180 天不阻塞 observed,
      sample_count 记实际覆盖天数。
    """
    churn = _hotspot_churn(commits)
    head_paths = _head_production_paths(root)
    hot = sorted(
        (
            (path, count)
            for path, count in churn.items()
            if count >= HOTSPOT_MIN_WINDOW_COMMITS and path in head_paths
        ),
        key=lambda item: (-item[1], item[0]),
    )
    deleted_hot = sorted(
        path for path, count in churn.items()
        if count >= HOTSPOT_MIN_WINDOW_COMMITS and path not in head_paths
    )
    rows, cc_pool, failures = _hotspot_rows(root, hot)
    unhealthy_count = sum(1 for row in rows if row["unhealthy"])
    cc_mean = round(sum(cc_pool) / len(cc_pool), 2) if cc_pool else None
    return {
        "unhealthy_hotspot_count": unhealthy_count,
        "hotspot_cc_mean": cc_mean,
        "details": {
            "formula": (
                "hot = production files (backend/app/, frontend/src/; _eligible_source; "
                f"present at HEAD) touched by >= {HOTSPOT_MIN_WINDOW_COMMITS} non-merge "
                "commits in the window; unhealthy_hotspot_count = hot files whose mean "
                f"function CC > {HOTSPOT_UNHEALTHY_MEAN_CC}; hotspot_cc_mean = mean CC of "
                "all functions across all hot files, rounded to 2 decimals"
            ),
            "thresholds": {
                "minimum_window_commits": HOTSPOT_MIN_WINDOW_COMMITS,
                "unhealthy_mean_cc_exclusive": HOTSPOT_UNHEALTHY_MEAN_CC,
            },
            "production_roots": list(HOTSPOT_PRODUCTION_ROOTS),
            "commit_population": (
                "all non-merge commits in the 180-day window (same _history slice as "
                "bus factor and temporal coupling)"
            ),
            "cc_definition": (
                "1 + vkpi_engineering_health_collect._DecisionCounter decisions per "
                "function/lambda, parsed from the HEAD blob (worktree is not an input)"
            ),
            "hot_file_count": len(rows),
            "unhealthy_hotspot_count": unhealthy_count,
            "hotspot_cc_mean": cc_mean,
            "cc_function_count": len(cc_pool),
            "python_parse_failures": failures,
            "hot_paths_missing_from_head": deleted_hot,
            "files": rows,
        },
    }


def _base_context(root: Path) -> dict[str, Any]:
    root = root.resolve()
    head = _git(root, "rev-parse", "HEAD")
    head_time = _timestamp(_git(root, "show", "-s", "--format=%cI", "HEAD"))
    history, command = _history(root)
    if not history:
        raise EvolutionEvidenceError("repository has no non-merge history")
    window_start = head_time - timedelta(days=WINDOW_DAYS)
    reachable = [commit for commit in history if window_start <= commit.authored_at <= head_time]
    earliest = min(commit.authored_at for commit in history)
    coverage_start = max(window_start, earliest)
    coverage_days = max(0.0, (head_time - coverage_start).total_seconds() / 86400)
    complete_window = earliest <= window_start
    non_bot_commits = [commit for commit in reachable if not _is_bot(commit)]
    bot_count = len(reachable) - len(non_bot_commits)
    shared_commits = [commit for commit in non_bot_commits if _is_shared_account(commit)]
    human_commits = [commit for commit in non_bot_commits if not _is_shared_account(commit)]
    identity_ambiguities = _identity_ambiguities(human_commits)
    mailmap = _head_mailmap(root)
    return {
        "root": root, "head": head, "head_time": head_time,
        "object_format": _git(root, "rev-parse", "--show-object-format"),
        "status": _git(root, "status", "--porcelain=v1", "--untracked-files=all"),
        "command": command, "window_start": window_start, "reachable": reachable,
        "earliest": earliest, "coverage_days": coverage_days,
        "complete_window": complete_window, "human_commits": human_commits,
        "bot_count": bot_count, "shared_commits": shared_commits,
        "identity_ambiguities": identity_ambiguities, "mailmap": mailmap,
        "mailmap_ready": bool(mailmap["committed"] and mailmap["entry_count"]),
    }


def _people_unavailable_reason(context: dict[str, Any]) -> str:
    if context["identity_ambiguities"]:
        return "author_identity_ambiguity_requires_mailmap"
    if not context["mailmap_ready"]:
        return "identity_mailmap_not_committed"
    if context["qualified_by_domain"] is None:
        return "maintainer_qualification_evidence_missing"
    if not context["all_domains_qualification_ready"]:
        return "maintainer_qualification_incomplete"
    return "metric_not_computable"


def _analyze_context(
    context: dict[str, Any], qualification_receipt: dict[str, Any] | None,
    qualification_source: str,
) -> None:
    qualified_by_domain, qualification = _qualification_evidence(
        qualification_receipt, head=context["head"], source=qualification_source
    )
    bus_domains, people_bus_factor = _bus_factor(
        context["human_commits"],
        qualified_by_domain=qualified_by_domain,
        shared_commits=context["shared_commits"],
    )
    ready_domains = [
        domain for domain, details in bus_domains.items() if details["qualification_ready"]
    ]
    all_ready = len(ready_domains) == len(CORE_DOMAINS)
    factors = [
        int(details["qualified_bus_factor"])
        for details in bus_domains.values()
        if details["qualification_ready"] and details["qualified_bus_factor"] is not None
    ]
    qualified_bus_factor = min(factors) if all_ready and len(factors) == len(CORE_DOMAINS) else None
    context.update({
        "qualified_by_domain": qualified_by_domain, "qualification": qualification,
        "bus_domains": bus_domains, "people_bus_factor": people_bus_factor,
        "qualification_ready_domains": ready_domains,
        "all_domains_qualification_ready": all_ready,
        "qualified_bus_factor": qualified_bus_factor,
        "qualified_domain_ratio": len(ready_domains) / len(CORE_DOMAINS),
        "coupling": _temporal_coupling(context["human_commits"]),
        "hotspot": _hotspot_analysis(context["root"], context["reachable"]),
        "observed_at": _iso(context["head_time"]),
        "source": (
            f"git-history://{context['head']}?window_days={WINDOW_DAYS}"
            f"&algorithm={ALGORITHM_VERSION}"
        ),
    })
    context["people_unavailable_reason"] = _people_unavailable_reason(context)


def _metric(
    context: dict[str, Any], value: Any, *, computable: bool,
    unavailable_reason: str = "metric_not_computable",
    sample_count: int | float = WINDOW_DAYS, sample_unit: str = "days",
) -> dict[str, Any]:
    if context["complete_window"] and computable:
        return {
            "status": "observed", "value": value, "source": context["source"],
            "observed_at": context["observed_at"], "sample_count": sample_count,
            "sample_unit": sample_unit,
        }
    return {
        "status": "unknown", "value": None, "source": context["source"],
        "observed_at": context["observed_at"],
        "sample_count": (
            math.floor(context["coverage_days"]) if sample_unit == "days" else sample_count
        ),
        "sample_unit": sample_unit,
        "reason": (
            "history_window_incomplete"
            if not context["complete_window"] else unavailable_reason
        ),
    }


def _history_payload(context: dict[str, Any]) -> dict[str, Any]:
    mailmap = context["mailmap"]
    ambiguities = context["identity_ambiguities"]
    return {
        "command": list(context["command"]),
        "non_merge_commits_in_window": len(context["reachable"]),
        "human_commits_in_window": len(context["human_commits"]),
        "excluded_bot_commits": context["bot_count"],
        "excluded_shared_account_commits": len(context["shared_commits"]),
        "merge_commits": "excluded_by_git_log_no_merges",
        "identity_normalization": (
            "git --use-mailmap author name/email from committed HEAD:.mailmap only; "
            "lower-case trimmed email primary"
        ),
        "identity_mailmap_source": "HEAD:.mailmap",
        "identity_mailmap_committed": mailmap["committed"],
        "identity_mailmap_sha256": mailmap["sha256"],
        "identity_mailmap_entry_count": mailmap["entry_count"],
        "working_tree_mailmap_ignored": True,
        "identity_limitations": (
            "aliases without a committed HEAD:.mailmap entry remain separate; "
            "qualification requires an independent HEAD-bound receipt"
        ),
        "identity_ambiguities": ambiguities,
        "identity_aliases_unambiguous": not ambiguities,
        "identity_quality_complete": context["mailmap_ready"] and not ambiguities,
        "shared_account_detection_uses_original_and_mailmapped_identity": True,
        "binary_numstat": "excluded from changed-line totals",
    }


def _hotspot_metric(
    context: dict[str, Any], value: Any, *, computable: bool,
    unavailable_reason: str = "metric_not_computable",
) -> dict[str, Any]:
    # Contract v1.1 registers the hotspot metrics without minimum_samples, so
    # an incomplete history window shrinks sample_count instead of blocking
    # the observed status (unlike the 180-day-gated metrics above).
    sample_count = (
        WINDOW_DAYS if context["complete_window"]
        else math.floor(context["coverage_days"])
    )
    entry = {
        "source": context["source"], "observed_at": context["observed_at"],
        "sample_count": sample_count, "sample_unit": "days",
    }
    if computable:
        return {"status": "observed", "value": value, **entry}
    return {"status": "unknown", "value": None, **entry, "reason": unavailable_reason}


def _metrics_payload(context: dict[str, Any]) -> dict[str, Any]:
    identity_ready = context["mailmap_ready"] and not context["identity_ambiguities"]
    hotspot = context["hotspot"]
    return {
        "core_domain_bus_factor_min": _metric(
            context, context["qualified_bus_factor"],
            computable=(
                context["qualified_bus_factor"] is not None and identity_ready
                and context["all_domains_qualification_ready"]
            ),
            unavailable_reason=context["people_unavailable_reason"],
        ),
        "temporal_coupling_p95": _metric(
            context, context["coupling"]["p95"],
            computable=context["coupling"]["p95"] is not None,
            unavailable_reason="insufficient_qualified_pairs",
        ),
        "qualified_maintainer_domain_ratio": _metric(
            context, context["qualified_domain_ratio"],
            computable=context["qualified_by_domain"] is not None and identity_ready,
            unavailable_reason=context["people_unavailable_reason"],
            sample_count=len(CORE_DOMAINS), sample_unit="core_domains",
        ),
        "unhealthy_hotspot_count": _hotspot_metric(
            context, hotspot["unhealthy_hotspot_count"], computable=True
        ),
        "hotspot_cc_mean": _hotspot_metric(
            context, hotspot["hotspot_cc_mean"],
            computable=hotspot["hotspot_cc_mean"] is not None,
            unavailable_reason="no_hotspot_functions",
        ),
    }


def _details_payload(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "bus_factor": {
            "formula": (
                "smallest qualified people set reaching >=50% of added+deleted "
                "production-source lines per core domain; metric is the domain minimum"
            ),
            "people_normalized_formula": (
                "smallest mailmap-normalized human set reaching >=50% before "
                "qualification filtering"
            ),
            "people_normalized_domain_minimum": context["people_bus_factor"],
            "qualified_domain_minimum": context["qualified_bus_factor"],
            "qualification_ready_domain_count": len(context["qualification_ready_domains"]),
            "qualified_maintainer_domain_ratio": context["qualified_domain_ratio"],
            "minimum_qualified_changed_line_share_per_domain": QUALIFIED_CONTRIBUTION_SHARE,
            "bots_and_shared_accounts_count_as_maintainers": False,
            "domains": context["bus_domains"],
        },
        "temporal_coupling": context["coupling"],
        "hotspot": context["hotspot"]["details"],
    }


def build_receipt(
    root: Path, *, qualification_receipt: dict[str, Any] | None = None,
    qualification_source: str = "",
) -> dict[str, Any]:
    context = _base_context(root)
    _analyze_context(context, qualification_receipt, qualification_source)
    return {
        "schema_version": "vkpi_engineering_health_evolution_receipt_v1",
        "algorithm_version": ALGORITHM_VERSION,
        "status": "observed" if context["complete_window"] else "partial",
        "candidate": {
            "repo": str(context["root"]), "head": context["head"],
            "git_object_format": context["object_format"],
            "worktree_dirty": bool(context["status"]), "worktree_is_input": False,
        },
        "window": {
            "required_days": WINDOW_DAYS, "start": _iso(context["window_start"]),
            "end": _iso(context["head_time"]),
            "earliest_reachable_non_merge_author_date": _iso(context["earliest"]),
            "covered_days": context["coverage_days"],
            "complete": context["complete_window"],
            "anchor": "HEAD committer date; commit inclusion uses author date",
        },
        "history": _history_payload(context),
        "maintainer_qualification": context["qualification"],
        "core_domain_mapping": {name: list(paths) for name, paths in CORE_DOMAINS.items()},
        "metrics": _metrics_payload(context), "details": _details_payload(context),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument(
        "--qualification-receipt",
        default="",
        help="Independent HEAD-bound maintainer qualification receipt JSON",
    )
    parser.add_argument("--json-out", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    qualification_payload: dict[str, Any] | None = None
    qualification_source = ""
    if args.qualification_receipt:
        qualification_path = Path(args.qualification_receipt).resolve()
        try:
            loaded = json.loads(qualification_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvolutionEvidenceError(
                f"cannot read maintainer qualification receipt: {qualification_path}"
            ) from exc
        if not isinstance(loaded, dict):
            raise EvolutionEvidenceError("maintainer qualification receipt must be a JSON object")
        qualification_payload = loaded
        qualification_source = f"receipt://{qualification_path}"
    output = json.dumps(
        build_receipt(
            Path(args.root),
            qualification_receipt=qualification_payload,
            qualification_source=qualification_source,
        ),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")
    stdout_out(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
