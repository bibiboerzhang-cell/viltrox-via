"""保守版评论语言判定(language_detection.language_detect)边界:短文本 / emoji / 混合语 / 字系直判。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.comments import language_detection as ld  # noqa: E402


@pytest.mark.parametrize(
    "text",
    ["", "   ", "😂😂😂", "👏👏👏👏👏", "❤️", "lol", "nice", "ok ok", "@someone", "#viltrox", "https://x.com/abc",
     "hahaha lolll", "first!!", "100%", "Sony a7iv + viltrox 85mm", "wow 太棒了 amazing"],
)
def test_short_emoji_and_mixed_text_stay_unknown(text: str) -> None:
    assert ld.language_detect(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("这个镜头太棒了", "zh"),
        ("帅咧", "zh"),
        ("すごい", "ja"),
        ("この写真きれい", "ja"),  # 汉字 + 假名 -> ja
        ("좋아요", "ko"),
        ("Очень красиво", "ru"),
        ("جميل جدا", "ar"),
        ("สวยมาก", "th"),
        ("ကြိုက်", "my"),
    ],
)
def test_non_latin_scripts_are_decided_by_script(text: str, expected: str) -> None:
    assert ld.language_detect(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("This lens is amazing for portraits, love it", "en"),
        ("Thanks for sharing", "en"),
        ("Great video", "en"),
        ("I love it 😍😍😍 #viltrox @viltrox", "en"),
        ("Muy buen video gracias", "es"),
        ("Sehr schönes Video danke", "de"),
        ("Je veux acheter cet objectif pour mon appareil photo", "fr"),
        ("Ótimo vídeo, obrigado por compartilhar", "pt"),
        ("Terima kasih banyak bang", "id"),
    ],
)
def test_latin_text_with_enough_evidence(text: str, expected: str) -> None:
    assert ld.language_detect(text) == expected


def test_langdetect_noise_on_short_english_is_rejected(monkeypatch) -> None:
    # langdetect 对 "wow amazing" 给 pl:0.86(主流语种、过阈值),但停用词启发式不互证 -> 不认
    monkeypatch.setattr(ld, "_langdetect_guess", lambda text: ("pl", 0.86))
    assert ld.language_detect("wow amazing") is None
    # 长文本(>= 8 词)langdetect 过阈值即认,冷门语种也认
    monkeypatch.setattr(ld, "_langdetect_guess", lambda text: ("sw", 0.9))
    assert ld.language_detect("habari ya leo ninapenda sana lenzi hii ya viltrox") == "sw"
    # 短文本冷门语种即使 0.99 也不认
    assert ld.language_detect("habari ya leo") is None


def test_langdetect_unavailable_falls_back_to_stopwords(monkeypatch) -> None:
    monkeypatch.setattr(ld, "_langdetect_guess", lambda text: ("", 0.0))
    assert ld.language_detect("thank you for this great video") == "en"
    assert ld.language_detect("Beautiful shot") is None  # 单个停用词命中不够


def test_strip_noise_and_script_profile() -> None:
    assert ld.strip_noise("Check https://x.com/a @me #tag 👍 now!") == "Check now!"
    profile = ld.script_profile("hello 你好")
    assert profile["counts"] == {"latin": 5, "zh": 2}
    assert profile["dominant"] == "latin"
    assert ld.script_language("你好世界") == "zh"
    assert ld.script_language("你") is None  # 单字不判
    assert ld.script_language("hello") is None


def test_write_path_script_shortcut_keeps_latin_contract(monkeypatch) -> None:
    import langdetect

    calls: list[str] = []

    def fake_detect(text: str) -> str:
        calls.append(text)
        return "en"

    monkeypatch.setattr(langdetect, "detect", fake_detect)

    class _Logger:
        def debug(self, *a, **k):
            pass

        def warning(self, *a, **k):
            raise AssertionError("no warning expected")

    # 非拉丁字系不再送 langdetect(短路直判);拉丁文本仍走既有 langdetect 契约
    assert ld.detect_comment_language("这个镜头太棒了", logger=_Logger()) == "zh"
    assert calls == []
    assert ld.detect_comment_language("real words", logger=_Logger()) == "en"
    assert calls == ["real words"]
