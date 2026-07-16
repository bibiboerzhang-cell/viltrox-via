from __future__ import annotations

from app.domains.comments import collector


def _capture_language_logs(monkeypatch):
    debug_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    warning_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        collector.logger,
        "debug",
        lambda *args, **kwargs: debug_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        collector.logger,
        "warning",
        lambda *args, **kwargs: warning_calls.append((args, kwargs)),
    )
    return debug_calls, warning_calls


def _force_fallback_language(monkeypatch, language: str = "en") -> None:
    from app.domains.kol import audience_language

    monkeypatch.setattr(audience_language, "detect_lang", lambda _text: language)


def test_successful_langdetect_does_not_log_or_call_fallback(monkeypatch) -> None:
    import langdetect
    from app.domains.kol import audience_language

    def fail_fallback(_text: str) -> str:
        raise AssertionError("fallback must not run")

    monkeypatch.setattr(langdetect, "detect", lambda _text: "EN")
    monkeypatch.setattr(audience_language, "detect_lang", fail_fallback)
    debug_calls, warning_calls = _capture_language_logs(monkeypatch)

    result = collector._detect_comment_language("real words")

    assert result == "en"
    assert debug_calls == []
    assert warning_calls == []


def test_missing_optional_langdetect_uses_fallback_without_warning(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def import_without_langdetect(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "langdetect":
            raise ModuleNotFoundError("No module named 'langdetect'", name="langdetect")
        return real_import(name, globals, locals, fromlist, level)

    _force_fallback_language(monkeypatch)
    debug_calls, warning_calls = _capture_language_logs(monkeypatch)
    monkeypatch.setattr(builtins, "__import__", import_without_langdetect)

    result = collector._detect_comment_language("real words")

    assert result == "en"
    assert debug_calls == []
    assert warning_calls == []


def test_incompatible_langdetect_interface_keeps_warning_traceback(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def import_without_exception_api(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "langdetect.lang_detect_exception":
            raise ImportError("missing exception API")
        return real_import(name, globals, locals, fromlist, level)

    _force_fallback_language(monkeypatch)
    debug_calls, warning_calls = _capture_language_logs(monkeypatch)
    monkeypatch.setattr(builtins, "__import__", import_without_exception_api)

    result = collector._detect_comment_language("real words")

    assert result == "en"
    assert debug_calls == []
    assert len(warning_calls) == 1
    assert "接口导入异常" in str(warning_calls[0][0][0])
    assert warning_calls[0][1].get("exc_info") is True


def test_expected_langdetect_failure_uses_fallback_without_traceback(monkeypatch) -> None:
    import langdetect
    from langdetect.lang_detect_exception import ErrorCode, LangDetectException

    def no_features(_text: str) -> str:
        raise LangDetectException(ErrorCode.CantDetectError, "No features in text")

    monkeypatch.setattr(langdetect, "detect", no_features)
    _force_fallback_language(monkeypatch)
    debug_calls, warning_calls = _capture_language_logs(monkeypatch)

    result = collector._detect_comment_language("... ...")

    assert result == "en"
    assert len(debug_calls) == 1
    assert "无足够特征" in str(debug_calls[0][0][0])
    assert debug_calls[0][1].get("exc_info") is not True
    assert warning_calls == []


def test_unexpected_langdetect_state_keeps_warning_traceback(monkeypatch) -> None:
    import langdetect
    from langdetect.lang_detect_exception import ErrorCode, LangDetectException

    def missing_profiles(_text: str) -> str:
        raise LangDetectException(ErrorCode.NeedLoadProfileError, "Need to load profiles")

    monkeypatch.setattr(langdetect, "detect", missing_profiles)
    _force_fallback_language(monkeypatch)
    debug_calls, warning_calls = _capture_language_logs(monkeypatch)

    result = collector._detect_comment_language("real words")

    assert result == "en"
    assert debug_calls == []
    assert len(warning_calls) == 1
    assert "内部状态异常" in str(warning_calls[0][0][0])
    assert warning_calls[0][0][1] == ErrorCode.NeedLoadProfileError
    assert warning_calls[0][1].get("exc_info") is True


def test_unexpected_runtime_failure_keeps_warning_traceback(monkeypatch) -> None:
    import langdetect

    def broken_detector(_text: str) -> str:
        raise RuntimeError("detector defect")

    monkeypatch.setattr(langdetect, "detect", broken_detector)
    _force_fallback_language(monkeypatch)
    debug_calls, warning_calls = _capture_language_logs(monkeypatch)

    result = collector._detect_comment_language("real words")

    assert result == "en"
    assert debug_calls == []
    assert len(warning_calls) == 1
    assert "非预期异常" in str(warning_calls[0][0][0])
    assert warning_calls[0][1].get("exc_info") is True


def test_fallback_failure_returns_unknown_and_keeps_warning_traceback(monkeypatch) -> None:
    import langdetect
    from app.domains.kol import audience_language
    from langdetect.lang_detect_exception import ErrorCode, LangDetectException

    def no_features(_text: str) -> str:
        raise LangDetectException(ErrorCode.CantDetectError, "No features in text")

    def broken_fallback(_text: str) -> str:
        raise RuntimeError("fallback defect")

    monkeypatch.setattr(langdetect, "detect", no_features)
    monkeypatch.setattr(audience_language, "detect_lang", broken_fallback)
    debug_calls, warning_calls = _capture_language_logs(monkeypatch)

    result = collector._detect_comment_language("... ...")

    assert result is None
    assert len(debug_calls) == 1
    assert debug_calls[0][1].get("exc_info") is not True
    assert len(warning_calls) == 1
    assert "启发式回退异常" in str(warning_calls[0][0][0])
    assert warning_calls[0][1].get("exc_info") is True
