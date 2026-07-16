from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from app.services.media import r2
from app.services.media.access_logging import MediaProxyAccessLogFilter, redact_media_proxy_request_target


def test_media_proxy_access_filter_removes_signed_query_before_formatting() -> None:
    secret = "x-signature=do-not-log"
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(
            "127.0.0.1:1234",
            "GET",
            f"/api/admin/vkpi/media/image-proxy?url=https%3A%2F%2Fcdn.invalid%2Fa.jpg%3F{secret}",
            "1.1",
            200,
        ),
        exc_info=None,
    )

    assert MediaProxyAccessLogFilter().filter(record) is True
    rendered = record.getMessage()
    assert rendered == '127.0.0.1:1234 - "GET /api/admin/vkpi/media/image-proxy HTTP/1.1" 200'
    assert secret not in rendered


@pytest.mark.parametrize(
    "path",
    [
        "/api/admin/vkpi/media/image-proxy",
        "/api/admin/vkpi/media/video-proxy",
        "/api/admin/vkpi/media/video-redirect",
    ],
)
def test_only_media_proxy_query_strings_are_redacted(path: str) -> None:
    assert redact_media_proxy_request_target(f"{path}?url=https%3A%2F%2Fsigned.invalid%2Fasset") == path
    ordinary = "/api/admin/vkpi/tasks?status=processing"
    assert redact_media_proxy_request_target(ordinary) == ordinary


def test_classified_r2_failure_is_one_warning_plus_info_fallback_without_secret(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    from app.domains.media import cache_core

    local_file = tmp_path / "cached.jpg"
    local_file.write_bytes(b"local-cache-survives")
    monkeypatch.setattr(cache_core, "_media_cache_r2_enabled", lambda: True)

    def fail_upload(*_args: Any, **_kwargs: Any) -> None:
        # Match the public R2 boundary: it logs one classified WARNING, then
        # raises a sanitized exception without a raw botocore chain.
        r2.logger.warning(
            "r2.upload_failed category=tls_transport retryable=True status_code=0 proxy_mode=disabled total_max_attempts=3"
        )
        raise r2.R2StorageError("upload", "tls_transport", retryable=True)

    monkeypatch.setattr(r2, "upload_file", fail_upload)
    signed_source = "https://cdn.invalid/image.jpg?x-signature=do-not-log"

    with caplog.at_level(logging.INFO):
        result = cache_core._upload_to_r2_if_enabled(
            media_kind="image",
            digest="abc123",
            cache_path=local_file,
            content_type="image/jpeg",
            source_url=signed_source,
        )

    messages = [record.getMessage() for record in caplog.records]
    warning_messages = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
    assert result == {"r2_status": "failed", "r2_error": "R2StorageError"}
    assert local_file.read_bytes() == b"local-cache-survives"
    assert len(warning_messages) == 1
    assert warning_messages[0].startswith("r2.upload_failed")
    assert any(
        message
        == "media.cache.r2_upload_fallback error_type=R2StorageError category=tls_transport retryable=True status_code=0"
        for message in messages
    )
    assert all("do-not-log" not in message and "abc123" not in message for message in messages)


def test_unexpected_cache_failure_warning_never_echoes_raw_signed_url(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    from app.domains.media import cache_core

    local_file = tmp_path / "cached.jpg"
    local_file.write_bytes(b"local-cache-survives")
    monkeypatch.setattr(cache_core, "_media_cache_r2_enabled", lambda: True)

    def fail_upload(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("https://signed.invalid/object?x-signature=secret-value")

    monkeypatch.setattr(r2, "upload_file", fail_upload)
    with caplog.at_level(logging.WARNING, logger=cache_core.logger.name):
        result = cache_core._upload_to_r2_if_enabled(
            media_kind="image",
            digest="abc123",
            cache_path=local_file,
            content_type="image/jpeg",
            source_url="https://source.invalid/image.jpg?token=also-secret",
        )

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert result == {"r2_status": "failed", "r2_error": "RuntimeError"}
    assert "media.cache.r2_upload_fallback error_type=RuntimeError category=unexpected" in log_text
    assert "signed.invalid" not in log_text
    assert "secret-value" not in log_text
    assert "also-secret" not in log_text


def test_botocore_wire_debug_is_never_enabled_by_process_debug_level() -> None:
    # The historical warning window included botocore DEBUG request headers
    # with AWS Credential/Signature fields.  The R2 module enforces a floor.
    for name in ("boto3", "botocore", "s3transfer"):
        assert logging.getLogger(name).level >= logging.WARNING
