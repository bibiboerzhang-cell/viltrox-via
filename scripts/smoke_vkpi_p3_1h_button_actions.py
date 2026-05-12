#!/usr/bin/env python3
"""Static guard for P3.1H data-analysis button behavior.

This smoke is intentionally static: it prevents obvious "clickable but fake"
regressions before browser QA. Runtime API/browser coverage remains separate.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_ANALYSIS = ROOT / "frontend/src/components/vkpi/pages/data-analysis"


def read(relative: str) -> str:
    return (DATA_ANALYSIS / relative).read_text(encoding="utf-8")


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def button_blocks(text: str) -> list[tuple[str, str, int]]:
    blocks: list[tuple[str, str, int]] = []
    for match in re.finditer(r"<button\b(?P<attrs>[^>]*)>(?P<body>.*?)</button>", text, re.S):
        line = text[: match.start()].count("\n") + 1
        blocks.append((match.group("attrs"), match.group("body"), line))
    return blocks


def normalized_label(body: str) -> str:
    body = re.sub(r"<[^>]+>", "", body)
    body = re.sub(r"\s+", " ", body)
    return body.strip()


def test_no_unwired_buttons() -> None:
    offenders: list[str] = []
    for path in DATA_ANALYSIS.rglob("*.tsx"):
        text = path.read_text(encoding="utf-8")
        for attrs, body, line in button_blocks(text):
            has_action = "onClick=" in attrs or 'type="submit"' in attrs or "type='submit'" in attrs
            if not has_action:
                offenders.append(f"{path.relative_to(ROOT)}:{line}:{normalized_label(body)}")
    assert_true(not offenders, "Buttons without onClick/submit:\n" + "\n".join(offenders))


def test_no_known_fake_actions() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in DATA_ANALYSIS.rglob("*.tsx"))
    forbidden = [
        "window.print()",
        "Tag post",
        "TODO",
        "coming soon",
        "敬请期待",
        "占位",
    ]
    found = [token for token in forbidden if token in combined]
    assert_true(not found, f"Found fake/placeholder action markers: {found}")


def test_export_and_filters_are_real_actions() -> None:
    panel = read("CrossPlatformPanel.tsx")
    drawer = read("drawers/FilterDrawer.tsx")
    assert_true("exportDashboardCsv" in panel, "Data-analysis download must use exportDashboardCsv.")
    assert_true("downloadTextFile" in panel, "Data-analysis export must create a real downloadable file.")
    assert_true("Export CSV" in panel, "Hero action should state Export CSV, not generic fake Download.")
    assert_true("应用筛选" in drawer, "Filter drawer footer must use an explicit apply action.")


def test_top_all_controls_exist() -> None:
    home = read("tabs/HomeTab.tsx")
    posts = read("tabs/PostsTab.tsx")
    drawer_tabs = read("drawers/tabs/index.tsx")
    assert_true("显示全部" in home and "只看 Top 3" in home, "Home top-posts needs Top/All toggle.")
    assert_true("显示全部" in posts and "只看前 30" in posts, "Posts tab needs Top/All toggle.")
    assert_true("显示全部" in drawer_tabs and "只看前 50" in drawer_tabs, "Profile posts table needs Top/All toggle.")


def main() -> None:
    test_no_unwired_buttons()
    test_no_known_fake_actions()
    test_export_and_filters_are_real_actions()
    test_top_all_controls_exist()
    print("VKPI_P3_1H_BUTTON_ACTIONS_SMOKE_OK")


if __name__ == "__main__":
    main()
