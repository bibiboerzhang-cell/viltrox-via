from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError, ProxyConnectionError, SSLError

from app.services.media import r2


_R2_ENV_KEYS = (
    "R2_ENDPOINT",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_NAME",
    "R2_PROXY_URL",
    "R2_CONNECT_TIMEOUT_SECONDS",
    "R2_READ_TIMEOUT_SECONDS",
    "R2_MAX_ATTEMPTS",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "YTDLP_PROXY",
    "AWS_MAX_ATTEMPTS",
    "AWS_RETRY_MODE",
)


class FakeS3Client:
    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _call(self, operation: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((operation, args, kwargs))
        if self.failure is not None:
            raise self.failure

    def upload_file(self, *args: Any, **kwargs: Any) -> None:
        self._call("upload", *args, **kwargs)

    def download_file(self, *args: Any, **kwargs: Any) -> None:
        self._call("download", *args, **kwargs)

    def delete_object(self, *args: Any, **kwargs: Any) -> None:
        self._call("delete", *args, **kwargs)

    def head_object(self, *args: Any, **kwargs: Any) -> None:
        self._call("head", *args, **kwargs)

    def generate_presigned_url(self, *args: Any, **kwargs: Any) -> str:
        self._call("presign", *args, **kwargs)
        return "https://signed.invalid/object"


class ClosableFakeS3Client(FakeS3Client):
    def __init__(self, *, close_error: BaseException | None = None) -> None:
        super().__init__()
        self.close_error = close_error
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


@pytest.fixture(autouse=True)
def configured_r2(monkeypatch: pytest.MonkeyPatch):
    for key in _R2_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("R2_ENDPOINT", "https://account.r2.cloudflarestorage.com")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "access-id")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret-key")
    monkeypatch.setenv("R2_BUCKET_NAME", "private-bucket")
    r2._reset_client_for_tests()
    yield
    r2._reset_client_for_tests()


def _capture_client(monkeypatch: pytest.MonkeyPatch, fake: FakeS3Client):
    created: list[dict[str, Any]] = []

    def factory(service: str, **kwargs: Any) -> FakeS3Client:
        assert service == "s3"
        created.append(kwargs)
        return fake

    monkeypatch.setattr(r2.boto3, "client", factory)
    return created


def _client_error(status: int, message: str = "upstream failed") -> ClientError:
    return ClientError(
        {
            "Error": {"Code": str(status), "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "HeadObject",
    )


def test_generic_scraping_proxy_is_not_inherited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://scrape-user:scrape-pass@generic.proxy.invalid:8080")
    monkeypatch.setenv("YTDLP_PROXY", "http://video-user:video-pass@generic.proxy.invalid:8081")
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "99")
    fake = FakeS3Client()
    created = _capture_client(monkeypatch, fake)

    assert r2.upload_file("/tmp/offline.bin", "safe/key.bin", "application/octet-stream") == "safe/key.bin"

    config = created[0]["config"]
    assert config.proxies == {}
    assert config.retries == {"mode": "standard", "total_max_attempts": 3}
    assert config.connect_timeout == 5
    assert config.read_timeout == 60
    assert fake.calls == [
        (
            "upload",
            ("/tmp/offline.bin", "private-bucket", "safe/key.bin"),
            {"ExtraArgs": {"ContentType": "application/octet-stream"}},
        )
    ]


def test_dedicated_proxy_is_explicit_and_singleton_rotates_on_policy_change(monkeypatch: pytest.MonkeyPatch) -> None:
    clients: list[FakeS3Client] = []
    created: list[dict[str, Any]] = []

    def factory(service: str, **kwargs: Any) -> FakeS3Client:
        assert service == "s3"
        created.append(kwargs)
        client = FakeS3Client()
        clients.append(client)
        return client

    monkeypatch.setattr(r2.boto3, "client", factory)
    monkeypatch.setenv("R2_PROXY_URL", "http://r2-user:r2-pass@r2.proxy.invalid:8443")

    first = r2._get_client()
    assert r2._get_client() is first
    assert len(created) == 1
    assert created[0]["config"].proxies == {
        "http": "http://r2-user:r2-pass@r2.proxy.invalid:8443",
        "https": "http://r2-user:r2-pass@r2.proxy.invalid:8443",
    }

    monkeypatch.setenv("R2_MAX_ATTEMPTS", "2")
    second = r2._get_client()
    assert second is not first
    assert len(created) == 2
    assert created[1]["config"].retries["total_max_attempts"] == 2


def test_rotation_closes_retired_client_without_undoing_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients = [
        ClosableFakeS3Client(),
        ClosableFakeS3Client(close_error=RuntimeError("secret proxy credential")),
        ClosableFakeS3Client(),
    ]
    created = 0

    def factory(service: str, **_kwargs: Any) -> ClosableFakeS3Client:
        nonlocal created
        assert service == "s3"
        client = clients[created]
        created += 1
        return client

    monkeypatch.setattr(r2.boto3, "client", factory)
    cleanup_logs: list[str] = []
    monkeypatch.setattr(r2.logger, "debug", lambda message, *_args, **_kwargs: cleanup_logs.append(str(message)))

    first = r2._get_client()
    monkeypatch.setenv("R2_MAX_ATTEMPTS", "2")
    second = r2._get_client()
    assert first is clients[0]
    assert second is clients[1]
    assert clients[0].close_calls == 1

    monkeypatch.setenv("R2_MAX_ATTEMPTS", "4")
    third = r2._get_client()

    assert third is clients[2]
    assert r2._get_client() is third
    assert clients[1].close_calls == 1
    assert clients[2].close_calls == 0
    assert created == 3
    log_text = "\n".join(cleanup_logs)
    assert cleanup_logs == ["r2.client_cleanup_failed"]
    assert "secret proxy credential" not in log_text


@pytest.mark.parametrize(
    ("failure", "category", "status_code"),
    [
        (_client_error(522, "secret-key http://user:pass@proxy.invalid"), "edge_522", 522),
        (
            SSLError(
                endpoint_url="https://account.r2.cloudflarestorage.com",
                error="decryption failed or bad record mac via http://user:pass@proxy.invalid",
            ),
            "tls_transport",
            None,
        ),
        (
            ProxyConnectionError(proxy_url="http://user:pass@proxy.invalid"),
            "proxy_transport",
            None,
        ),
    ],
)
def test_final_transport_failure_is_classified_once_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: BaseException,
    category: str,
    status_code: int | None,
) -> None:
    fake = FakeS3Client(failure=failure)
    _capture_client(monkeypatch, fake)
    monkeypatch.setenv("R2_PROXY_URL", "http://dedicated-user:dedicated-pass@r2.proxy.invalid:8443")

    with caplog.at_level(logging.WARNING, logger=r2.__name__), pytest.raises(r2.R2StorageError) as raised:
        r2.upload_file("/tmp/offline.bin", "private/key.bin")

    error = raised.value
    assert error.category == category
    assert error.status_code == status_code
    assert error.retryable is True
    assert error.__suppress_context__ is True
    assert len(fake.calls) == 1  # botocore owns the bounded retry policy; wrapper never loops
    public_text = str(error)
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    for secret in ("secret-key", "user:pass", "dedicated-pass", "private/key.bin"):
        assert secret not in public_text
        assert secret not in log_text
    assert log_text.count("r2.upload_failed") == 1
    assert category in log_text
    assert "proxy_mode=dedicated" in log_text


def test_head_404_is_an_honest_miss_but_522_is_not(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    missing = FakeS3Client(failure=_client_error(404, "missing"))
    _capture_client(monkeypatch, missing)
    with caplog.at_level(logging.WARNING, logger=r2.__name__):
        assert r2.object_exists("missing.bin") is False
    assert "r2.head_failed" not in "\n".join(record.getMessage() for record in caplog.records)

    r2._reset_client_for_tests()
    unavailable = FakeS3Client(failure=_client_error(522, "edge unavailable"))
    _capture_client(monkeypatch, unavailable)
    with pytest.raises(r2.R2StorageError, match="edge_522"):
        r2.object_exists("unknown.bin")


def test_invalid_or_missing_transport_fails_before_client_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _capture_client(monkeypatch, FakeS3Client())
    monkeypatch.setenv("R2_PROXY_URL", "socks5://not-supported.invalid")
    with pytest.raises(r2.R2ConfigurationError, match="invalid_r2_proxy_url"):
        r2.upload_file("/tmp/offline.bin", "key.bin")
    assert created == []

    monkeypatch.delenv("R2_PROXY_URL")
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY")
    assert r2.object_exists("key.bin") is False
    with pytest.raises(r2.R2ConfigurationError, match="not_configured"):
        r2.upload_file("/tmp/offline.bin", "key.bin")
    assert created == []


def test_media_cache_keeps_local_file_and_reports_honest_r2_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.domains.media import cache_core

    local_file = tmp_path / "cached.jpg"
    local_file.write_bytes(b"offline-cache")
    monkeypatch.setattr(cache_core, "_media_cache_r2_enabled", lambda: True)

    def fail_upload(*_args: Any, **_kwargs: Any) -> None:
        raise r2.R2StorageError("upload", "tls_transport", retryable=True)

    monkeypatch.setattr(r2, "upload_file", fail_upload)

    result = cache_core._upload_to_r2_if_enabled(
        media_kind="image",
        digest="abc123",
        cache_path=local_file,
        content_type="image/jpeg",
        source_url="https://source.invalid/image.jpg",
    )

    assert result == {"r2_status": "failed", "r2_error": "R2StorageError"}
    assert local_file.read_bytes() == b"offline-cache"
