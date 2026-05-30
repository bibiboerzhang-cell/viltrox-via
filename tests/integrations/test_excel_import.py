from __future__ import annotations

from app.integrations.excel_import.classifiers.is_junk_row import is_junk_row, is_junk_value
from app.integrations.excel_import.classifiers.owned_vs_kol import classify_owned_vs_kol
from app.integrations.excel_import.normalizers.handle import normalize_handle
from app.integrations.excel_import.parsers.cell_unpacker import unpack_cell
from app.integrations.excel_import.pipeline import DryRunResult, format_dry_run_report


def test_junk_row_filtered() -> None:
    assert is_junk_value("由于<|产品型号|>字段为空，无法按照要求准确输出。")
    assert is_junk_row({"a": "由于<|产品型号|>字段为空，无法按照要求准确输出。"})
    assert not is_junk_row({"项目": "AF 35mm", "备注": "由于<|产品型号|>字段为空"})


def test_owned_whitelist_strict() -> None:
    assert classify_owned_vs_kol("Viltrox.official", "INSTAGRAM") == "owned"
    assert classify_owned_vs_kol("viltrox_fan_official", "instagram") == "unknown"


def test_unpack_cell_splits_schedule_tokens() -> None:
    tokens = unpack_cell("AF 35-INSTAGRAM-Viltrox.official, AF 35-YOUTUBE-Viltrox Official\nAF 35-X-ViltroxOfficial")
    assert tokens == [
        "AF 35-INSTAGRAM-Viltrox.official",
        "AF 35-YOUTUBE-Viltrox Official",
        "AF 35-X-ViltroxOfficial",
    ]


def test_normalize_handle() -> None:
    assert normalize_handle("@Viltrox. official ") == "viltrox.official"
    assert normalize_handle("https://www.youtube.com/@CreatorOne/videos") == "creatorone"


def test_dry_run_report_no_commit_shape() -> None:
    result = DryRunResult(junk_filtered=2, unknown_classify=1)
    report = format_dry_run_report(result)
    assert "projects: 0 rows extracted" in report
    assert "junk filtered: 2 rows" in report
    assert "unknown classify: 1 rows" in report

