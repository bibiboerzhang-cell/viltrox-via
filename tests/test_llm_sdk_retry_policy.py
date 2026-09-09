"""Single-attempt SDK configuration and installed-library transport regressions."""
from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.core import release_validation
from app.platform.llm_sdk_retry_policy import google_single_attempt_config

ROOT = Path(__file__).resolve().parents[1]
CONSTRUCTORS = {
    "backend/app/services/ai/clients/openai_client.py": 3,
    "backend/app/services/ai/clients/claude_client.py": 2,
    "backend/app/services/verification/comment_generator.py": 1,
    "backend/app/services/ai/analyzers/claude_contract_extract.py": 1,
    "backend/app/services/ai/analyzers/gemini_video_keyframes.py": 1,
    "backend/app/services/ai/analyzers/claude_vision_client.py": 1,
    "backend/app/domains/projects/contract_assist.py": 1,
    "backend/app/domains/kol/profile_recall_support.py": 3,
}


def _sdk_constructor(node):
    if not isinstance(node, ast.Call):
        return False
    name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
    return name in {"OpenAI", "Anthropic", "AsyncOpenAI", "AsyncAnthropic"}


@pytest.mark.parametrize("relative,expected", CONSTRUCTORS.items())
def test_all_constructor_branches_pass_zero_retries_to_fake_sdk(relative, expected):
    tree = ast.parse((ROOT / relative).read_text())
    nodes = [node for node in ast.walk(tree) if _sdk_constructor(node)]
    calls = []
    def construct(**kwargs):
        calls.append(kwargs)
        return object()
    namespace = {
        "__builtins__": {}, "OpenAI": construct, "anthropic": SimpleNamespace(Anthropic=construct),
        "httpx": SimpleNamespace(Client=lambda **kwargs: object()),
        "_httpx": SimpleNamespace(Client=lambda **kwargs: object()),
        "api_key": "fixture", "_api_key": "fixture", "key": "fixture",
        "_openai_key": "fixture", "_OPENAI_KEY": "fixture", "ANTHROPIC_API_KEY": "fixture",
        "_oai_proxy": "http://unused.invalid", "proxy": "http://unused.invalid", "timeout": 1,
    }
    for node in nodes:
        # Execute only the isolated constructor expression with fake names;
        # never import app clients or their dotenv/key-loading startup.
        eval(compile(ast.Expression(node), relative, "eval"), namespace)
    assert len(calls) == expected
    assert all(call["max_retries"] == 0 for call in calls)


def test_constructor_inventory_has_no_unreviewed_production_path():
    found = {}
    for source in (ROOT / "backend/app").rglob("*.py"):
        count = sum(_sdk_constructor(node) for node in ast.walk(ast.parse(source.read_text())))
        if count:
            found[str(source.relative_to(ROOT))] = count
    assert found == CONSTRUCTORS


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_installed_sdk_zero_retries_sends_only_once_when_first_error_activates_fence(monkeypatch, provider):
    requests = []
    def request(req):
        requests.append(req)
        monkeypatch.setattr(release_validation, "release_validation_status",
                            lambda: {"active": True, "valid": True})
        return httpx.Response(500, json={"error": {"type": "api_error", "message": "synthetic failure"}})
    with httpx.Client(transport=httpx.MockTransport(request), trust_env=False) as transport:
        if provider == "openai":
            from openai import OpenAI, APIStatusError
            with OpenAI(api_key="fixture-key", max_retries=0, http_client=transport) as client:
                with pytest.raises(APIStatusError):
                    client.responses.create(model="fixture", input="fixture")
        else:
            from anthropic import Anthropic, APIStatusError
            with Anthropic(api_key="fixture-key", max_retries=0, http_client=transport) as client:
                with pytest.raises(APIStatusError):
                    client.messages.create(model="fixture", max_tokens=16,
                                           messages=[{"role": "user", "content": "fixture"}])
    assert len(requests) == 1


@pytest.mark.parametrize("shape", ["dict", "model", "camelcase"])
def test_google_copy_forces_one_attempt_without_losing_other_http_options(shape):
    from google.genai import types
    http = {"timeout": 1234, "api_version": "v1beta", "headers": {"x-fixture": "keep"},
            "retry_options": {"attempts": 3, "initial_delay": 0.25}}
    config = {"temperature": 0.5, "http_options": http}
    if shape == "model":
        config = types.GenerateContentConfig(**config)
        before = config.model_dump()
    elif shape == "camelcase":
        config["httpOptions"] = config.pop("http_options")
        before = deepcopy(config)
    else:
        before = deepcopy(config)
    copied = google_single_attempt_config(config)
    observed = copied["http_options"] if isinstance(copied, dict) else copied.http_options
    assert observed.retry_options.attempts == 1
    assert observed.retry_options.initial_delay == 0.25
    assert observed.timeout == 1234
    assert observed.api_version == "v1beta"
    assert observed.headers == {"x-fixture": "keep"}
    assert (config.model_dump() if shape == "model" else config) == before
    assert copied is not config


@pytest.mark.parametrize("config", [
    object(), {"http_options": object()}, {"http_options": {}, "httpOptions": {}},
    {"http_options": {"retry_options": "invalid"}},
])
def test_google_unverifiable_config_is_fixed_failure_before_io(config):
    with pytest.raises(ValueError, match="^google_single_attempt_config_unavailable$"):
        google_single_attempt_config(config)


def test_google_copies_http_client_reference_without_mutating_caller():
    from google.genai import types
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("no request"))) as http:
        config = types.GenerateContentConfig(http_options=types.HttpOptions(
            httpx_client=http, retry_options=types.HttpRetryOptions(attempts=4)))
        copied = google_single_attempt_config(config)
        assert copied.http_options.httpx_client is http
        assert copied.http_options is not config.http_options
        assert copied.http_options.retry_options is not config.http_options.retry_options
        assert config.http_options.retry_options.attempts == 4


def test_google_installed_sdk_request_override_cannot_retry_after_first_error(monkeypatch):
    from google import genai
    from google.genai import types, errors
    requests = []
    def request(req):
        requests.append(req)
        monkeypatch.setattr(release_validation, "release_validation_status",
                            lambda: {"active": True, "valid": True})
        return httpx.Response(503, json={"error": {"code": 503, "message": "synthetic", "status": "UNAVAILABLE"}})
    with httpx.Client(transport=httpx.MockTransport(request), trust_env=False) as http:
        client = genai.Client(api_key="fixture-key", http_options=types.HttpOptions(httpx_client=http))
        source = types.GenerateContentConfig(http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=3, initial_delay=0.001)))
        try:
            with pytest.raises(errors.ServerError):
                client.models.generate_content(model="fixture", contents="fixture",
                                               config=google_single_attempt_config(source))
        finally:
            client.close()
    assert len(requests) == 1
    assert source.http_options.retry_options.attempts == 3

