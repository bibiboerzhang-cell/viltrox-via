from __future__ import annotations

import gzip
import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "check_frontend_bundle_budget",
    SCRIPTS / "check_frontend_bundle_budget.py",
)
assert SPEC and SPEC.loader
budget = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = budget
SPEC.loader.exec_module(budget)


def _write_bundle(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        """<!doctype html>
        <script type="module" src="/assets/app.js"></script>
        <link rel="modulepreload" href="/assets/shared.js">
        <link rel="stylesheet" href="/assets/initial.css">
        """,
        encoding="utf-8",
    )
    (assets / "app.js").write_text(
        'import "./shared.js"; import("./lazy.js"); console.log("entry");',
        encoding="utf-8",
    )
    (assets / "shared.js").write_text(
        'import { value } from "./leaf.js"; export { value };',
        encoding="utf-8",
    )
    (assets / "leaf.js").write_text('export const value = "leaf";', encoding="utf-8")
    (assets / "lazy.js").write_text('export const lazy = "lazy";', encoding="utf-8")
    (assets / "assist.worker.js").write_text('self.onmessage = () => {};', encoding="utf-8")
    (assets / "initial.css").write_text("body{color:#fff}", encoding="utf-8")
    (assets / "lazy.css").write_text(".lazy{display:block}", encoding="utf-8")
    return dist


def _wide_limits() -> object:
    return budget.BudgetLimits(
        initial_js_raw_kib=100,
        initial_js_gzip_kib=100,
        max_js_chunk_raw_kib=100,
        max_js_chunk_gzip_kib=100,
        initial_css_raw_kib=100,
        initial_css_gzip_kib=100,
        max_css_asset_raw_kib=100,
        max_css_asset_gzip_kib=100,
    )


def test_classifies_static_closure_separately_from_lazy_and_worker(tmp_path: Path) -> None:
    dist = _write_bundle(tmp_path)
    report = budget.analyze_bundle(dist, _wide_limits())

    assert report["decision"]["passed"] is True
    initial = {row["file"] for row in report["metrics"]["js"]["initial"]["files"]}
    deferred = {row["file"] for row in report["metrics"]["js"]["deferred"]["files"]}
    assert initial == {"assets/app.js", "assets/shared.js", "assets/leaf.js"}
    assert deferred == {"assets/lazy.js", "assets/assist.worker.js"}
    assert {row["file"] for row in report["metrics"]["css"]["initial"]["files"]} == {
        "assets/initial.css"
    }


def test_gzip_metric_is_deterministic_level_9_with_zero_mtime(tmp_path: Path) -> None:
    dist = _write_bundle(tmp_path)
    report = budget.analyze_bundle(dist, _wide_limits())
    app = next(
        row for row in report["metrics"]["js"]["initial"]["files"] if row["file"] == "assets/app.js"
    )
    expected = len(gzip.compress((dist / "assets/app.js").read_bytes(), compresslevel=9, mtime=0))
    assert app["gzip_9_bytes"] == expected


def test_budget_failure_reports_observed_limit_and_headroom(tmp_path: Path) -> None:
    dist = _write_bundle(tmp_path)
    limits = _wide_limits()
    limits = budget.BudgetLimits(**{**budget.asdict(limits), "initial_js_raw_kib": 0.001})
    report = budget.analyze_bundle(dist, limits)

    assert report["decision"]["passed"] is False
    assert "initial_js.raw" in report["decision"]["failures"]
    check = next(item for item in report["checks"] if item["id"] == "initial_js.raw")
    assert check["passed"] is False
    assert check["observed_bytes"] > check["limit_bytes"]
    assert check["headroom_bytes"] < 0


def test_missing_entry_fails_closed(tmp_path: Path) -> None:
    dist = _write_bundle(tmp_path)
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    report = budget.analyze_bundle(dist, _wide_limits())
    assert report["decision"]["passed"] is False
    assert "index.html has no module entry script" in report["decision"]["failures"]


def test_external_or_escaping_asset_reference_fails_closed(tmp_path: Path) -> None:
    dist = _write_bundle(tmp_path)
    (dist / "index.html").write_text(
        '<script type="module" src="https://example.invalid/app.js"></script>',
        encoding="utf-8",
    )
    report = budget.analyze_bundle(dist, _wide_limits())
    assert report["decision"]["passed"] is False
    assert any("external asset reference" in item for item in report["decision"]["failures"])


def test_missing_initial_stylesheet_fails_closed_without_crashing(tmp_path: Path) -> None:
    dist = _write_bundle(tmp_path)
    (dist / "assets/initial.css").unlink()
    report = budget.analyze_bundle(dist, _wide_limits())
    assert report["decision"]["passed"] is False
    assert any("initial CSS assets are missing" in item for item in report["decision"]["failures"])
