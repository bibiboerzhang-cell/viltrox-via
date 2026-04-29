from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

from starlette.datastructures import QueryParams


ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import security  # noqa: E402
from app.services.ingestion import webhooks  # noqa: E402


class SecretRotationTests(unittest.TestCase):
    def test_verify_token_accepts_previous_secret(self) -> None:
        previous = "previous-secret-123"
        current = "current-secret-456"
        token = security._pyjwt.encode(
            {
                "uid": 88,
                "role": "creator",
                "iss": security.JWT_ISSUER,
                "aud": security.JWT_AUDIENCE,
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
            },
            previous,
            algorithm="HS256",
        )
        original = list(security.JWT_VERIFY_SECRETS)
        try:
            security.JWT_VERIFY_SECRETS = [current, previous]
            payload = security.verify_token(token)
            self.assertIsNotNone(payload)
            assert payload is not None
            self.assertEqual(payload["uid"], 88)
        finally:
            security.JWT_VERIFY_SECRETS = original

    def test_meta_challenge_accepts_previous_verify_token(self) -> None:
        original_primary = webhooks.META_WEBHOOK_VERIFY_TOKEN
        original_previous = list(webhooks.META_WEBHOOK_VERIFY_TOKEN_PREVIOUS)
        try:
            webhooks.META_WEBHOOK_VERIFY_TOKEN = "meta-new-token"
            webhooks.META_WEBHOOK_VERIFY_TOKEN_PREVIOUS = ["meta-old-token"]
            query = QueryParams(
                "hub.mode=subscribe&hub.verify_token=meta-old-token&hub.challenge=abc123"
            )
            challenge = webhooks.verify_meta_challenge(query)
            self.assertEqual(challenge, "abc123")
        finally:
            webhooks.META_WEBHOOK_VERIFY_TOKEN = original_primary
            webhooks.META_WEBHOOK_VERIFY_TOKEN_PREVIOUS = original_previous


if __name__ == "__main__":
    unittest.main()
