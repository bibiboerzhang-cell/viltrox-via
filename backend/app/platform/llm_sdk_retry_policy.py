"""Per-request Google SDK retry policy; outer retries own release admission."""
from __future__ import annotations

from typing import Any


def google_single_attempt_config(config: Any) -> Any:
    """Copy supported SDK config, retaining HTTP options but disabling retries.

Google permits per-request retry settings to override the client. Validate the
installed SDK's schema and clone only changed nodes; never mutate caller-owned
headers, tools, timeout, API version, or retry configuration.
    """
    try:
        from google.genai import types

        if config is None:
            config = {}
        if isinstance(config, dict):
            if "http_options" in config and "httpOptions" in config:
                raise ValueError("ambiguous HTTP options")
            validated = types.GenerateContentConfig.model_validate(config)
        elif isinstance(config, types.GenerateContentConfig):
            validated = config
        else:
            raise TypeError("unsupported config type")
        http = validated.http_options or types.HttpOptions()
        retry = http.retry_options or types.HttpRetryOptions()
        bounded_http = http.model_copy(update={
            "retry_options": retry.model_copy(update={"attempts": 1}),
        })
        if isinstance(config, dict):
            copied = dict(config)
            copied.pop("httpOptions", None)
            copied["http_options"] = bounded_http
            return copied
        return validated.model_copy(update={"http_options": bounded_http})
    except Exception:
        raise ValueError("google_single_attempt_config_unavailable") from None
