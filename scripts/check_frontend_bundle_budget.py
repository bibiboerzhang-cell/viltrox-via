#!/usr/bin/env python3
"""Fail-closed budgets for the built frontend's initial and deferred assets.

The existing chunk graph guard protects the Rollup DAG and one raw-size ceiling.
This companion answers a different question: what does ``index.html`` make an
initial navigation fetch, and how much remains deferred?  It reports exact bytes
and deterministic synthetic gzip-9 bytes.  Synthetic gzip is useful for stable
regression checks; it is not a claim about a CDN's transfer encoding.
"""

from __future__ import annotations

import argparse
import gzip
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Iterable
from urllib.parse import unquote, urlsplit

from stdout_utils import out, out_json


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST = REPO_ROOT / "frontend" / "dist"
KIB = 1024

# Matches Rollup's static imports while excluding dynamic import("...").
STATIC_IMPORT_RE = re.compile(
    r"(?:^|[;{}\s])import\s*(?:[\w$*{},:\s]+?from\s*)?[\"']([^\"']+\.js)[\"']"
)


@dataclass(frozen=True)
class BudgetLimits:
    initial_js_raw_kib: float = 1120.0
    initial_js_gzip_kib: float = 345.0
    max_js_chunk_raw_kib: float = 325.0
    max_js_chunk_gzip_kib: float = 92.0
    initial_css_raw_kib: float = 155.0
    initial_css_gzip_kib: float = 34.0
    max_css_asset_raw_kib: float = 215.0
    max_css_asset_gzip_kib: float = 36.0


class IndexAssetsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.module_entries: list[str] = []
        self.module_preloads: list[str] = []
        self.stylesheets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "script" and values.get("type") == "module" and values.get("src"):
            self.module_entries.append(values["src"])
            return
        if tag != "link" or not values.get("href"):
            return
        rels = set(values.get("rel", "").lower().split())
        if "modulepreload" in rels:
            self.module_preloads.append(values["href"])
        if "stylesheet" in rels:
            self.stylesheets.append(values["href"])


def _gzip_size(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), compresslevel=9, mtime=0))


def _relative_asset(dist: Path, reference: str) -> str:
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc:
        raise ValueError(f"external asset reference is not auditable: {reference}")
    relative = unquote(parsed.path).lstrip("/")
    if not relative:
        raise ValueError(f"empty asset reference: {reference}")
    resolved = (dist / relative).resolve()
    try:
        return resolved.relative_to(dist.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"asset escapes dist directory: {reference}") from exc


def _asset_summary(dist: Path, names: Iterable[str]) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for name in sorted(set(names)):
        path = dist / name
        raw = path.stat().st_size
        rows.append({"file": name, "raw_bytes": raw, "gzip_9_bytes": _gzip_size(path)})
    return {
        "file_count": len(rows),
        "raw_bytes": sum(int(row["raw_bytes"]) for row in rows),
        "gzip_9_bytes": sum(int(row["gzip_9_bytes"]) for row in rows),
        "files": rows,
    }


def _largest(rows: Iterable[dict[str, object]]) -> dict[str, object] | None:
    values = list(rows)
    if not values:
        return None
    return dict(max(values, key=lambda row: (int(row["raw_bytes"]), str(row["file"]))))


def _static_js_graph(dist: Path, js_names: set[str]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    dist_root = dist.resolve()
    for name in sorted(js_names):
        path = dist / name
        imports: set[str] = set()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in STATIC_IMPORT_RE.finditer(text):
            candidate = (path.parent / match.group(1)).resolve()
            try:
                relative = candidate.relative_to(dist_root).as_posix()
            except ValueError as exc:
                raise ValueError(f"static import escapes dist: {name} -> {match.group(1)}") from exc
            if relative not in js_names:
                raise ValueError(f"static import target is missing: {name} -> {relative}")
            imports.add(relative)
        graph[name] = imports
    return graph


def _closure(graph: dict[str, set[str]], roots: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    pending = list(roots)
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        if name not in graph:
            raise ValueError(f"initial JS asset is missing from graph: {name}")
        seen.add(name)
        pending.extend(sorted(graph[name] - seen))
    return seen


def _check(check_id: str, observed_bytes: int, limit_kib: float) -> dict[str, object]:
    limit_bytes = int(limit_kib * KIB)
    return {
        "id": check_id,
        "passed": observed_bytes <= limit_bytes,
        "observed_bytes": observed_bytes,
        "limit_bytes": limit_bytes,
        "headroom_bytes": limit_bytes - observed_bytes,
    }


def analyze_bundle(dist_dir: Path, limits: BudgetLimits | None = None) -> dict[str, object]:
    dist = dist_dir.resolve()
    selected_limits = limits or BudgetLimits()
    errors: list[str] = []
    index = dist / "index.html"
    if not dist.is_dir():
        errors.append(f"dist directory does not exist: {dist}")
    elif not index.is_file():
        errors.append(f"index.html does not exist: {index}")
    if errors:
        return {
            "schema_version": "vkpi_frontend_bundle_budget_v1",
            "decision": {"passed": False, "failures": errors},
            "dist_dir": str(dist),
            "limits_kib": asdict(selected_limits),
        }

    parser = IndexAssetsParser()
    parser.feed(index.read_text(encoding="utf-8"))
    try:
        entries = {_relative_asset(dist, value) for value in parser.module_entries}
        preloads = {_relative_asset(dist, value) for value in parser.module_preloads}
        initial_css = {_relative_asset(dist, value) for value in parser.stylesheets}
    except ValueError as exc:
        errors.append(str(exc))
        entries, preloads, initial_css = set(), set(), set()

    all_js = {path.relative_to(dist).as_posix() for path in dist.rglob("*.js")}
    all_css = {path.relative_to(dist).as_posix() for path in dist.rglob("*.css")}
    if not entries:
        errors.append("index.html has no module entry script")
    if not all_js:
        errors.append("dist has no JS assets")
    missing_css = sorted(initial_css - all_css)
    if missing_css:
        errors.append(f"initial CSS assets are missing: {', '.join(missing_css)}")
    auditable_initial_css = initial_css & all_css

    initial_js: set[str] = set()
    if not errors:
        try:
            graph = _static_js_graph(dist, all_js)
            initial_js = _closure(graph, entries | preloads)
        except ValueError as exc:
            errors.append(str(exc))

    deferred_js = all_js - initial_js
    deferred_css = all_css - auditable_initial_css
    js_initial_summary = _asset_summary(dist, initial_js)
    js_deferred_summary = _asset_summary(dist, deferred_js)
    js_all_summary = _asset_summary(dist, all_js)
    css_initial_summary = _asset_summary(dist, auditable_initial_css)
    css_deferred_summary = _asset_summary(dist, deferred_css)
    css_all_summary = _asset_summary(dist, all_css)
    largest_js = _largest(js_all_summary["files"])
    largest_css = _largest(css_all_summary["files"])

    checks: list[dict[str, object]] = []
    if not errors and largest_js and largest_css:
        checks = [
            _check("initial_js.raw", int(js_initial_summary["raw_bytes"]), selected_limits.initial_js_raw_kib),
            _check("initial_js.gzip_9", int(js_initial_summary["gzip_9_bytes"]), selected_limits.initial_js_gzip_kib),
            _check("max_js_chunk.raw", int(largest_js["raw_bytes"]), selected_limits.max_js_chunk_raw_kib),
            _check("max_js_chunk.gzip_9", int(largest_js["gzip_9_bytes"]), selected_limits.max_js_chunk_gzip_kib),
            _check("initial_css.raw", int(css_initial_summary["raw_bytes"]), selected_limits.initial_css_raw_kib),
            _check("initial_css.gzip_9", int(css_initial_summary["gzip_9_bytes"]), selected_limits.initial_css_gzip_kib),
            _check("max_css_asset.raw", int(largest_css["raw_bytes"]), selected_limits.max_css_asset_raw_kib),
            _check("max_css_asset.gzip_9", int(largest_css["gzip_9_bytes"]), selected_limits.max_css_asset_gzip_kib),
        ]
    elif not largest_css:
        errors.append("dist has no CSS assets")

    failures = list(errors)
    failures.extend(str(check["id"]) for check in checks if not bool(check["passed"]))
    return {
        "schema_version": "vkpi_frontend_bundle_budget_v1",
        "dist_dir": str(dist),
        "measurement": {
            "raw": "filesystem bytes",
            "gzip_9": "python gzip level 9 with mtime=0; deterministic estimate, not CDN transfer proof",
            "initial": "module entry + modulepreload + transitive static JS imports; index stylesheet links",
            "deferred": "assets outside the initial closure; includes lazy routes and workers",
        },
        "limits_kib": asdict(selected_limits),
        "entry": {
            "module_scripts": sorted(entries),
            "module_preloads": sorted(preloads),
            "stylesheets": sorted(initial_css),
        },
        "metrics": {
            "js": {
                "initial": js_initial_summary,
                "deferred": js_deferred_summary,
                "all": js_all_summary,
                "largest": largest_js,
            },
            "css": {
                "initial": css_initial_summary,
                "deferred": css_deferred_summary,
                "all": css_all_summary,
                "largest": largest_css,
            },
        },
        "checks": checks,
        "decision": {"passed": not failures, "failures": failures},
    }


def _parser() -> argparse.ArgumentParser:
    defaults = BudgetLimits()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist_dir", nargs="?", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--json-output", type=Path)
    for field, value in asdict(defaults).items():
        parser.add_argument(f"--{field.replace('_', '-')}", type=float, default=value)
    return parser


def main() -> int:
    args = _parser().parse_args()
    limits = BudgetLimits(**{field: getattr(args, field) for field in asdict(BudgetLimits())})
    report = analyze_bundle(args.dist_dir, limits)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    metrics = report.get("metrics", {})
    if isinstance(metrics, dict) and metrics:
        js = metrics["js"]
        css = metrics["css"]
        out(
            "[frontend-budget] "
            f"initial-js={js['initial']['raw_bytes']}B/{js['initial']['gzip_9_bytes']}B-gzip9 "
            f"largest-js={js['largest']['raw_bytes']}B/{js['largest']['gzip_9_bytes']}B-gzip9 "
            f"initial-css={css['initial']['raw_bytes']}B/{css['initial']['gzip_9_bytes']}B-gzip9"
        )
    decision = report["decision"]
    if decision["passed"]:
        out("[frontend-budget] PASS")
        return 0
    out_json({"frontend_bundle_budget_failures": decision["failures"]})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
