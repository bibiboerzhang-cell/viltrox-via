"""受众头像判龄 Gemini 调用(从 audience_stats 抽出):默认模型、thinking minimal、无 temperature、env 覆盖。"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.domains.kol import audience_avatar_llm


class _ThinkingConfig:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _GenerateContentConfig:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _Part:
    def __init__(self, data: bytes, mime_type: str) -> None:
        self.data = data
        self.mime_type = mime_type

    @classmethod
    def from_bytes(cls, *, data: bytes, mime_type: str) -> "_Part":
        return cls(data, mime_type)


_FAKE_TYPES = SimpleNamespace(ThinkingConfig=_ThinkingConfig, GenerateContentConfig=_GenerateContentConfig, Part=_Part)


class _FakeClient:
    def __init__(self, text: str = "[]") -> None:
        self.calls: list[dict[str, Any]] = []
        self._text = text
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(text=self._text)


def test_default_model_is_gemini_36_flash(monkeypatch) -> None:
    monkeypatch.delenv("AUDIENCE_AVATAR_MODEL", raising=False)
    assert audience_avatar_llm.avatar_model() == "gemini-3.6-flash"
    assert audience_avatar_llm.AUDIENCE_AVATAR_DEFAULT_MODEL == "gemini-3.6-flash"


def test_env_override_is_honoured(monkeypatch) -> None:
    monkeypatch.setenv("AUDIENCE_AVATAR_MODEL", " gemini-2.5-flash ")
    assert audience_avatar_llm.avatar_model() == "gemini-2.5-flash"
    monkeypatch.setenv("AUDIENCE_AVATAR_MODEL", "   ")
    assert audience_avatar_llm.avatar_model() == "gemini-3.6-flash"


def test_generate_config_uses_minimal_thinking_and_no_sampling_params() -> None:
    config = audience_avatar_llm.avatar_generate_config(_FAKE_TYPES)
    assert config.kwargs["max_output_tokens"] == 4000
    thinking = config.kwargs["thinking_config"]
    assert isinstance(thinking, _ThinkingConfig)
    assert thinking.kwargs == {"thinking_level": "minimal"}
    assert "thinking_budget" not in thinking.kwargs
    for forbidden in ("temperature", "top_p", "top_k"):
        assert forbidden not in config.kwargs


def test_classify_batch_builds_numbered_contents_and_calls_default_model(monkeypatch) -> None:
    monkeypatch.delenv("AUDIENCE_AVATAR_MODEL", raising=False)
    client = _FakeClient('[{"i":1,"age":"19-29","gender":"female","conf":0.5}]')

    text = audience_avatar_llm.classify_avatar_batch(
        [(b"img-one", "image/png"), (b"img-two", "image/jpeg")],
        client=client,
        genai_types=_FAKE_TYPES,
    )

    assert text.startswith("[")
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "gemini-3.6-flash"
    assert "temperature" not in call
    contents = call["contents"]
    assert contents[0] == "Image 1:"
    assert isinstance(contents[1], _Part) and contents[1].mime_type == "image/png"
    assert contents[2] == "Image 2:"
    assert isinstance(contents[3], _Part) and contents[3].data == b"img-two"
    assert contents[-1] == audience_avatar_llm.AVATAR_BATCH_PROMPT
    assert call["config"].kwargs["thinking_config"].kwargs == {"thinking_level": "minimal"}


def test_classify_batch_explicit_model_wins_over_env(monkeypatch) -> None:
    monkeypatch.setenv("AUDIENCE_AVATAR_MODEL", "gemini-2.5-flash")
    client = _FakeClient()
    audience_avatar_llm.classify_avatar_batch([(b"x", "image/jpeg")], client=client, genai_types=_FAKE_TYPES, model="gemini-3.5-flash-lite")
    assert client.calls[0]["model"] == "gemini-3.5-flash-lite"
    client2 = _FakeClient()
    audience_avatar_llm.classify_avatar_batch([(b"x", "image/jpeg")], client=client2, genai_types=_FAKE_TYPES)
    assert client2.calls[0]["model"] == "gemini-2.5-flash"


def test_audience_stats_avatar_batch_uses_extracted_module(monkeypatch) -> None:
    from app.domains.kol import audience_stats

    client = _FakeClient('[{"i":1,"age":"30-39","gender":"male","conf":0.9},{"i":2,"age":"","gender":"","conf":0.0}]')
    monkeypatch.setattr(audience_stats, "load_avatar_gemini", lambda: (client, _FAKE_TYPES, ""))
    monkeypatch.setattr(audience_stats, "download_avatar", lambda url: (b"j" * 400, "image/jpeg"))
    monkeypatch.delenv("AUDIENCE_AVATAR_MODEL", raising=False)

    out, meta = audience_stats._age_avatar_batch(
        [
            {"author_key": "u1", "avatar_url": "https://cdn/a.jpg"},
            {"author_key": "u2", "avatar_url": "https://cdn/b.jpg"},
            {"author_key": "u3", "avatar_url": "https://cdn/c.jpg", "age_bucket": "19-29"},
        ]
    )

    assert meta["status"] == "ok"
    assert meta["calls"] == 1
    assert meta["people_in"] == 2
    assert out["u1"]["age_bucket"] == "30-39"
    assert out["u1"]["gender"] == "male"
    assert out["u1"]["conf"] == 0.6  # 视觉判龄封顶 .6
    assert "u2" not in out
    assert client.calls[0]["model"] == "gemini-3.6-flash"
    assert client.calls[0]["config"].kwargs["thinking_config"].kwargs == {"thinking_level": "minimal"}


def test_audience_stats_reports_unavailable_client(monkeypatch) -> None:
    from app.domains.kol import audience_stats

    monkeypatch.setattr(audience_stats, "load_avatar_gemini", lambda: (None, None, "gemini_unavailable"))
    out, meta = audience_stats._age_avatar_batch([{"author_key": "u1", "avatar_url": "https://cdn/a.jpg"}])
    assert out == {}
    assert meta["status"] == "gemini_unavailable"
    assert meta["calls"] == 0
