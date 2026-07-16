"""Test-only Ed25519 helpers for model evidence fixtures.

Production modules intentionally expose verification only.  Tests keep private
keys and signing logic in this isolated helper so runtime code cannot mint its
own evidence or trust roots.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.platform.models.evaluation_artifact import canonical_sha256


_PROBE_SIGNING_DOMAIN = b"vkpi:model-probe-evidence:v1\n"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def public_key_b64(private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode("ascii")


def sign_evaluation_artifact(
    artifact: Mapping[str, Any],
    *,
    private_key: Ed25519PrivateKey,
    key_id: str,
) -> dict[str, Any]:
    payload = json.loads(_canonical_json(dict(artifact)))
    payload.pop("attestation", None)
    integrity = payload.get("integrity")
    unsigned = {key: value for key, value in payload.items() if key != "integrity"}
    if not isinstance(integrity, Mapping) or integrity.get("sha256") != canonical_sha256(
        unsigned
    ):
        raise ValueError("test artifact integrity must be valid before signing")
    signature = private_key.sign(_canonical_json(payload).encode("utf-8"))
    payload["attestation"] = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "role": "evaluation",
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    return payload


def sign_probe_evidence(
    probe: Mapping[str, Any],
    *,
    private_key: Ed25519PrivateKey,
    key_id: str,
) -> dict[str, Any]:
    payload = json.loads(_canonical_json(dict(probe)))
    payload.pop("attestation", None)
    signature = private_key.sign(
        _PROBE_SIGNING_DOMAIN + _canonical_json(payload).encode("utf-8")
    )
    payload["attestation"] = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "role": "exact_probe",
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    return payload


def install_test_trust_roots(
    monkeypatch: Any,
    *,
    evaluation_keys: Mapping[str, str | bytes],
    probe_keys: Mapping[str, str | bytes],
) -> None:
    from app.platform.models import evaluation_artifact, readiness

    monkeypatch.setattr(
        evaluation_artifact,
        "TRUSTED_EVALUATION_ED25519_PUBLIC_KEYS",
        dict(evaluation_keys),
    )
    monkeypatch.setattr(
        readiness,
        "TRUSTED_EXACT_PROBE_ED25519_PUBLIC_KEYS",
        dict(probe_keys),
    )
