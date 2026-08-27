"""KOL 语言推断(简介 + 视频标题)的判定边界与落库安全契约。

守四条:
1. 判定复用评论域保守检测器,阈值不动 —— 短文本 / 纯 emoji / 纯 URL / 混合语一律未知;
2. 推断值只进 language_inferred,**绝不覆写自报的 language 列**;
3. 干跑(无 --apply)一行不写;
4. 来源标记(bio / video_titles / bio+video_titles)与置信档如实反映证据。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from app.domains.comments import language_detection as ld  # noqa: E402
from app.domains.kol import language_inference as li  # noqa: E402


# ── 单条文本判定:复用检测器,不放宽 ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("bio", "titles", "expected"),
    [
        ("Photography tutorials, camera gear reviews and travel films", [], "en"),
        ("", ["How I shoot portraits with a vintage lens on a rainy day"], "en"),
        ("专注分享摄影技巧与器材评测", [], "zh"),
        ("写真の撮り方と機材レビューを発信しています", [], "ja"),
        ("사진 촬영 기법과 장비 리뷰를 공유합니다", [], "ko"),
        ("Tutoriels photo et tests de matériel pour les débutants", [], "fr"),
    ],
)
def test_single_source_languages(bio: str, titles: list[str], expected: str) -> None:
    assert li.infer_language_from_content(bio=bio, titles=titles)["language"] == expected


@pytest.mark.parametrize(
    ("bio", "titles", "reason"),
    [
        ("", [], li.REASON_NO_TEXT),
        ("   ", [""], li.REASON_NO_TEXT),
        ("😂😂😂🔥", [], li.REASON_NO_VERDICT),          # 纯 emoji
        ("https://linktr.ee/someone", [], li.REASON_NO_VERDICT),  # 纯 URL
        ("@viltrox #viltrox", [], li.REASON_NO_VERDICT),          # 纯提及/话题
        ("photo", [], li.REASON_NO_VERDICT),                      # 极短:单词不判
        ("ok ok", [], li.REASON_NO_VERDICT),                      # 极短:字母不足
    ],
)
def test_unknown_stays_unknown_with_reason(bio: str, titles: list[str], reason: str) -> None:
    verdict = li.infer_language_from_content(bio=bio, titles=titles)
    assert verdict["language"] is None
    assert verdict["unknown_reason"] == reason
    assert verdict["confidence"] is None
    assert verdict["source"] == ""


def test_mixed_language_creator_is_unknown_not_guessed() -> None:
    """一半法语一半英语、无多数 -> 诚实未知(不许拿多数决以外的手段硬猜)。"""
    verdict = li.infer_language_from_content(
        bio="Tutoriels photo et tests de matériel pour les débutants",
        titles=["How I shoot portraits with a vintage lens on a rainy day"],
    )
    assert verdict["language"] is None
    assert verdict["unknown_reason"] == li.REASON_MIXED
    assert verdict["decided_n"] == 2


def test_mixed_script_single_text_is_unknown() -> None:
    # 单条文本里中英混排,检测器本身就判未知;推断层不得越过它。
    assert ld.language_detect("wow 太棒了 amazing") is None
    assert li.infer_language_from_content(bio="wow 太棒了 amazing")["language"] is None


def test_majority_wins_when_share_is_enough() -> None:
    verdict = li.infer_language_from_content(
        bio="Tutoriales de fotografia y analisis de objetivos para principiantes",
        titles=[
            "Como fotografiar retratos con un objetivo antiguo en dias de lluvia",
            "Analisis completo del nuevo objetivo para camaras sin espejo",
            "How I shoot portraits with a vintage lens on a rainy day",
        ],
    )
    assert verdict["language"] == "es"
    assert verdict["votes"]["es"] == 3
    assert verdict["votes"]["en"] == 1


# ── 来源标记与置信档 ────────────────────────────────────────────────────────


def test_source_label_reflects_the_winning_evidence() -> None:
    only_bio = li.infer_language_from_content(
        bio="Photography tutorials, camera gear reviews and travel films"
    )
    assert only_bio["source"] == li.SOURCE_BIO
    assert only_bio["confidence"] == li.CONFIDENCE_LOW

    only_titles = li.infer_language_from_content(
        titles=[
            "How I shoot portraits with a vintage lens on a rainy day",
            "Testing the newest mirrorless camera lens for street photography",
        ]
    )
    assert only_titles["source"] == li.SOURCE_TITLES
    assert only_titles["confidence"] == li.CONFIDENCE_MEDIUM

    both = li.infer_language_from_content(
        bio="Photography tutorials, camera gear reviews and travel films",
        titles=[
            "How I shoot portraits with a vintage lens on a rainy day",
            "Testing the newest mirrorless camera lens for street photography",
        ],
    )
    assert both["source"] == "bio+video_titles"
    assert both["confidence"] == li.CONFIDENCE_HIGH
    assert both["sample_n"] == 3


def test_samples_are_capped_and_deduplicated() -> None:
    titles = ["How I shoot portraits with a vintage lens on a rainy day"] * 50
    items = li.collect_language_texts(bio="", titles=titles)
    assert len(items) == 1  # 完全相同的标题去重
    items = li.collect_language_texts(bio="", titles=[f"Lens review number {i} for mirrorless cameras" for i in range(80)])
    assert len(items) == li.MAX_TEXT_SAMPLES


def test_method_version_is_carried_on_every_verdict() -> None:
    for verdict in (
        li.infer_language_from_content(bio="Photography tutorials and camera gear reviews"),
        li.infer_language_from_content(bio=""),
    ):
        assert verdict["method"] == li.KOL_LANGUAGE_INFERENCE_VERSION


def test_detector_thresholds_are_untouched() -> None:
    """推断层不得为了提高覆盖率去改评论域检测器的阈值(红线 2)。"""
    assert ld.MIN_LETTERS == 6
    assert ld.MIN_WORDS == 2
    assert ld.LONG_TEXT_WORDS == 8
    assert ld.MIN_SCRIPT_CHARS == 2
    assert ld.SCRIPT_DOMINANCE == 0.6
    assert ld.LANGDETECT_MIN_PROB == 0.85
    assert ld.LANGDETECT_SURE_PROB == 0.99
    assert ld.MIXED_SCRIPT_SHARE == 0.2


# ── 回填脚本:干跑不写 / 只补空 / 不碰自报值 ───────────────────────────────

import backfill_kol_pool_language_inference as bf  # noqa: E402


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]], rowcount: int = 0):
        self._rows = rows
        self.rowcount = rowcount

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _Conn:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self.selects: list[str] = []
        self.updates: list[tuple[str, tuple]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple = ()) -> _Cursor:
        head = sql.strip().upper()
        if head.startswith("SELECT"):
            self.selects.append(sql)
            if "VKPI_KOL_VIDEO_EVIDENCE" in head:
                return _Cursor([])
            return _Cursor(self.rows)
        self.updates.append((sql, params))
        return _Cursor([], rowcount=1)

    def commit(self) -> None:
        self.commits += 1


def _pool_row(rid: int = 1) -> dict[str, Any]:
    return {
        "id": rid,
        "platform": "youtube",
        "handle": "somechannel",
        "language": "",
        "bio": "Photography tutorials, camera gear reviews and travel films",
        "language_inferred": None,
        "raw_platform_data": json.dumps(
            {"videos": [{"snippet": {"title": "How I shoot portraits with a vintage lens on a rainy day"}}]}
        ),
    }


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> _Conn:
    conn = _Conn([_pool_row()])
    monkeypatch.setattr(bf, "get_conn", lambda: conn)
    monkeypatch.setattr(bf, "_clear_kol_pool_read_cache", lambda: None)
    monkeypatch.setattr(sys, "argv", ["backfill_kol_pool_language_inference.py", *argv])
    assert bf.main() == 0
    return conn


def test_dry_run_writes_nothing(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    conn = _run(monkeypatch, [])
    assert conn.updates == []
    assert conn.commits == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["apply"] is False
    assert payload["written"] == 0
    assert payload["inferred"] == 1


def test_apply_writes_only_the_inferred_columns(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    conn = _run(monkeypatch, ["--apply"])
    assert len(conn.updates) == 1
    sql, params = conn.updates[0]
    lowered = " ".join(sql.split()).lower()
    # 红线 1:自报值一个字都不动。
    assert "set language =" not in lowered
    assert "language = ?" not in lowered
    assert "language_inferred = ?" in lowered
    # 只补空:UPDATE 自己再守一遍。
    assert "language_inferred is null or trim(language_inferred) = ''" in lowered
    assert params[0] == "en"
    assert params[-2] == li.KOL_LANGUAGE_INFERENCE_VERSION
    assert conn.commits == 1
    assert json.loads(capsys.readouterr().out)["written"] == 1


def test_selected_rows_never_include_self_reported_language(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    conn = _run(monkeypatch, [])
    capsys.readouterr()
    pool_select = " ".join(conn.selects[0].split()).lower()
    # 只扫「自报语言为空」的人;有自报值的人根本不进推断。
    assert "language is null or trim(language) = ''" in pool_select
    assert "duplicate_of_id is null" in pool_select


def test_backfill_sql_stays_compat_safe() -> None:
    source = (ROOT / "scripts" / "backfill_kol_pool_language_inference.py").read_text(encoding="utf-8")
    assert " LIKE " not in source.upper()
    for line in source.splitlines():
        if line.lstrip().startswith("#"):
            continue
        assert "%" not in line, f"SQL 兼容层禁字面量百分号:{line}"


def test_raw_title_and_self_description_extraction() -> None:
    raw = {
        "profile": {"items": [{"snippet": {"description": "Camera gear reviews and photography tutorials"}}]},
        "videos": [
            {"snippet": {"title": "Vintage lens review"}},
            {"text": "TikTok caption here"},
            {"caption": "Instagram caption here"},
        ],
    }
    assert bf.raw_self_description(raw) == "Camera gear reviews and photography tutorials"
    assert bf.raw_video_titles(raw) == ["Vintage lens review", "TikTok caption here", "Instagram caption here"]
