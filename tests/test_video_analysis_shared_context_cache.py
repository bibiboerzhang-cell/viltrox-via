"""刀②:final_v1 静态提示 context cache 跨进程共享(persistent_cache)+ 失效驱逐重试。

全程 fake:不连库(共享存取函数被替换成内存 dict)、不调 Gemini。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.ai.analyzers import gemini_video


class _Caches:
    def __init__(self, *, fail: bool = False) -> None:
        self.created: list[str] = []
        self.fail = fail

    def create(self, *, model: str, config: Any) -> Any:
        if self.fail:
            raise AssertionError("caches.create must not be called when a shared entry exists")
        self.created.append(model)
        return SimpleNamespace(name=f"cachedContents/created-{len(self.created)}")


def _gemini_env(monkeypatch: pytest.MonkeyPatch, caches: _Caches) -> dict[str, Any]:
    store: dict[str, tuple[str, float]] = {}
    monkeypatch.setattr(gemini_video, "GEMINI_AVAILABLE", True)
    monkeypatch.setattr(gemini_video, "gemini_client", SimpleNamespace(caches=caches))
    monkeypatch.setattr(
        gemini_video,
        "genai_types",
        SimpleNamespace(
            CreateCachedContentConfig=lambda **kw: kw,
            GenerateContentConfig=lambda **kw: SimpleNamespace(**kw),
        ),
    )
    monkeypatch.setattr(gemini_video, "_video_final_v1_static_prompt", lambda: "STATIC PROMPT")
    monkeypatch.setattr(gemini_video, "_shared_context_cache_get", lambda key: store.get(key, ("", 0.0)))
    monkeypatch.setattr(
        gemini_video,
        "_shared_context_cache_put",
        lambda key, name, *, model_name: store.__setitem__(key, (name, 0.0)),
    )
    monkeypatch.setattr(gemini_video, "_shared_context_cache_evict", lambda key: store.pop(key, None))
    monkeypatch.setattr(gemini_video, "_FINAL_V1_CONTEXT_CACHES", {})
    return store


def test_shared_entry_is_reused_without_creating_a_new_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    caches = _Caches(fail=True)
    store = _gemini_env(monkeypatch, caches)
    key = gemini_video._final_v1_shared_cache_key("gemini-2.5-flash", "STATIC PROMPT")
    store[key] = ("cachedContents/shared-1", 120.0)
    config, info = gemini_video._final_v1_cache_config("gemini-2.5-flash")
    assert config.cachedContent == "cachedContents/shared-1"
    assert info["enabled"] is True and info["source"] == "shared"
    assert caches.created == []
    # 进程内 memo 已建立,第二次连共享存取都不用
    monkeypatch.setattr(gemini_video, "_shared_context_cache_get", lambda key: (_ for _ in ()).throw(AssertionError("memo first")))
    config2, info2 = gemini_video._final_v1_cache_config("gemini-2.5-flash")
    assert config2.cachedContent == "cachedContents/shared-1" and info2["source"] == "process"


def test_created_cache_is_published_to_shared_store(monkeypatch: pytest.MonkeyPatch) -> None:
    caches = _Caches()
    store = _gemini_env(monkeypatch, caches)
    config, info = gemini_video._final_v1_cache_config("gemini-2.5-flash")
    assert info["source"] == "created" and config.cachedContent == "cachedContents/created-1"
    key = gemini_video._final_v1_shared_cache_key("gemini-2.5-flash", "STATIC PROMPT")
    assert store[key][0] == "cachedContents/created-1"
    assert caches.created == ["gemini-2.5-flash"]


def test_stale_shared_entry_is_ignored_and_recreated(monkeypatch: pytest.MonkeyPatch) -> None:
    caches = _Caches()
    store = _gemini_env(monkeypatch, caches)
    key = gemini_video._final_v1_shared_cache_key("gemini-2.5-flash", "STATIC PROMPT")
    store[key] = ("cachedContents/old", gemini_video._FINAL_V1_CACHE_REUSE_SECONDS + 5)
    config, info = gemini_video._final_v1_cache_config("gemini-2.5-flash")
    assert info["source"] == "created" and config.cachedContent == "cachedContents/created-1"


def test_shared_store_outage_is_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    caches = _Caches()
    _gemini_env(monkeypatch, caches)
    monkeypatch.setattr(gemini_video, "_shared_context_cache_get", lambda key: ("", 0.0))
    monkeypatch.setattr(gemini_video, "_shared_context_cache_put", lambda *a, **k: None)
    config, info = gemini_video._final_v1_cache_config("gemini-2.5-flash")
    assert info["enabled"] is True and info["source"] == "created"


def test_real_store_functions_never_raise_without_database(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.db.connection as connection

    monkeypatch.setattr(connection, "db_connection_sync_scope", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no database")))
    monkeypatch.setattr(gemini_video, "_shared_cache_store_warned", False)
    assert gemini_video._shared_context_cache_get("k") == ("", 0.0)
    gemini_video._shared_context_cache_put("k", "cachedContents/x", model_name="m")
    gemini_video._shared_context_cache_evict("k")
    assert gemini_video._shared_cache_store_warned is True


def test_shared_cache_key_is_stable_across_processes() -> None:
    key_a = gemini_video._final_v1_shared_cache_key("gemini-2.5-flash", "STATIC")
    key_b = gemini_video._final_v1_shared_cache_key("gemini-2.5-flash", "STATIC")
    assert key_a == key_b and key_a.startswith("vkpi:gemini-ctx-cache:final_v1:gemini-2.5-flash:")
    assert gemini_video._final_v1_shared_cache_key("gemini-3-flash-preview", "STATIC") != key_a


def test_env_switch_disables_shared_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gemini_video, "_SHARED_CONTEXT_CACHE_ENABLED", False)
    import app.db.connection as connection

    monkeypatch.setattr(connection, "db_connection_sync_scope", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not touch db")))
    assert gemini_video._shared_context_cache_get("k") == ("", 0.0)
    gemini_video._shared_context_cache_put("k", "x", model_name="m")
    gemini_video._shared_context_cache_evict("k")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("403 PERMISSION_DENIED: CachedContent not found: cachedContents/abc", True),
        ("400 INVALID_ARGUMENT: cached content has expired", True),
        ("503 Service unavailable", False),
        ("Expecting ',' delimiter: line 140", False),
    ],
)
def test_context_cache_error_detection(error: str, expected: bool) -> None:
    assert gemini_video._is_context_cache_error(error) is expected


def test_retry_helper_evicts_once_per_model(monkeypatch: pytest.MonkeyPatch) -> None:
    evicted: list[str] = []
    monkeypatch.setattr(gemini_video, "_final_v1_cache_evict", lambda model, *, reason="": evicted.append(model))
    retried: set[str] = set()
    err = RuntimeError("CachedContent not found")
    assert gemini_video._retry_after_context_cache_error(err, {"enabled": True}, "m", retried) is True
    assert gemini_video._retry_after_context_cache_error(err, {"enabled": True}, "m", retried) is False
    assert gemini_video._retry_after_context_cache_error(err, {"enabled": False}, "n", retried) is False
    assert gemini_video._retry_after_context_cache_error(RuntimeError("503"), {"enabled": True}, "n", retried) is False
    assert evicted == ["m"]


SIX_LAYERS = {
    "layer1_visual_content": {
        "content_summary": "A creator compares autofocus and flare performance.",
        "scene_timeline": [{"timestamp": "00:04", "what": "Lens close-up."}],
        "evidence": {"timestamps": ["00:04 lens close-up"]},
    },
    "layer6_flags_and_scores": {"final_verdict": "Useful category evidence."},
}


def test_local_analyzer_evicts_poisoned_cache_and_retries_same_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 4096)
    generate_calls: list[str] = []
    evicted: list[str] = []

    class _Files:
        def upload(self, **_kwargs: Any) -> Any:
            return SimpleNamespace(name="files/abc", uri="https://files/abc", state=SimpleNamespace(name="ACTIVE"))

        def get(self, *, name: str) -> Any:
            return SimpleNamespace(name=name, uri="https://files/abc", state=SimpleNamespace(name="ACTIVE"))

        def delete(self, *, name: str) -> None:
            return None

    def _generate(**kwargs: Any) -> Any:
        generate_calls.append(kwargs["model_name"])
        if len(generate_calls) == 1:
            raise RuntimeError("403 PERMISSION_DENIED: CachedContent not found: cachedContents/poison")
        return SimpleNamespace(text=json.dumps(SIX_LAYERS), usage_metadata=None)

    monkeypatch.setattr(gemini_video, "GEMINI_AVAILABLE", True)
    monkeypatch.setattr(gemini_video, "gemini_client", SimpleNamespace(files=_Files()))
    monkeypatch.setattr(gemini_video, "genai_types", SimpleNamespace(Part=SimpleNamespace(from_uri=lambda **kw: kw)))
    monkeypatch.setattr(gemini_video, "get_creator_profile", lambda _handle: {})
    monkeypatch.setattr(gemini_video, "_strict_generate_content", _generate)
    monkeypatch.setattr(
        gemini_video,
        "_final_v1_cache_config",
        lambda _model: (SimpleNamespace(cachedContent="cachedContents/poison"), {"enabled": True, "cache_name": "cachedContents/poison"}),
    )
    monkeypatch.setattr(gemini_video, "_video_generate_config", lambda *_args, **_k: None)
    monkeypatch.setattr(gemini_video, "_final_v1_cache_evict", lambda model, *, reason="": evicted.append(model))
    result = asyncio.run(
        gemini_video.analyze_local_video_with_gemini(
            str(video), "demo", schema_version="final_v1", models=["gemini-2.5-flash"]
        )
    )
    assert result["analyzed"] is True
    assert generate_calls == ["gemini-2.5-flash", "gemini-2.5-flash"]
    assert evicted == ["gemini-2.5-flash"]


def test_local_analyzer_does_not_retry_non_cache_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 4096)
    generate_calls: list[str] = []

    class _Files:
        def upload(self, **_kwargs: Any) -> Any:
            return SimpleNamespace(name="files/abc", uri="https://files/abc", state=SimpleNamespace(name="ACTIVE"))

        def get(self, *, name: str) -> Any:
            return SimpleNamespace(name=name, uri="https://files/abc", state=SimpleNamespace(name="ACTIVE"))

        def delete(self, *, name: str) -> None:
            return None

    def _generate(**kwargs: Any) -> Any:
        generate_calls.append(kwargs["model_name"])
        raise RuntimeError("503 Service unavailable")

    monkeypatch.setattr(gemini_video, "GEMINI_AVAILABLE", True)
    monkeypatch.setattr(gemini_video, "gemini_client", SimpleNamespace(files=_Files()))
    monkeypatch.setattr(gemini_video, "genai_types", SimpleNamespace(Part=SimpleNamespace(from_uri=lambda **kw: kw)))
    monkeypatch.setattr(gemini_video, "get_creator_profile", lambda _handle: {})
    monkeypatch.setattr(gemini_video, "_strict_generate_content", _generate)
    monkeypatch.setattr(
        gemini_video,
        "_final_v1_cache_config",
        lambda _model: (SimpleNamespace(cachedContent="cachedContents/ok"), {"enabled": True, "cache_name": "cachedContents/ok"}),
    )
    monkeypatch.setattr(gemini_video, "_video_generate_config", lambda *_args, **_k: None)
    monkeypatch.setattr(gemini_video, "_final_v1_cache_evict", lambda model, *, reason="": (_ for _ in ()).throw(AssertionError("no evict")))
    result = asyncio.run(
        gemini_video.analyze_local_video_with_gemini(
            str(video), "demo", schema_version="final_v1", models=["gemini-2.5-flash"]
        )
    )
    assert result["analyzed"] is False
    assert generate_calls == ["gemini-2.5-flash"]
    assert "503" in result["error"]
