from __future__ import annotations

from scripts.ops.load_test_runner import *


async def _execute_legacy_v2(_args: argparse.Namespace) -> dict[str, Any]:
    """Retired evidence path retained only as an explicit fail-closed hook."""
    raise RuntimeError(
        "legacy v2 load execution is retired because it cannot satisfy the v4 "
        "identity, telemetry-attestation, and pressure-completion evidence contract"
    )


__all__ = [name for name in globals() if not name.startswith("__")]
