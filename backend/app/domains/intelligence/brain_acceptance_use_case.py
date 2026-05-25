"""P6.79 read-only brain layer acceptance v0.

This aggregates P6.71-P6.78 artifacts to decide whether the rule-based brain
layer can support human new-launch and KOL planning decisions. It is not a
business sign-off, recommendation engine, model trainer, or sync runner.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.domains.intelligence.brain_acceptance import (
    ACCEPTANCE_VERSION,
    REQUIRED_REPORTS,
    build_brain_layer_acceptance_report,
    build_brain_layer_module_row,
)


DEFAULT_OPS_DIR = "runtime/ops"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _latest_artifact(root: Path, pattern: str) -> Path | None:
    if not root.exists() or not root.is_dir():
        return None
    candidates = [path for path in root.glob(pattern) if path.is_file()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: (path.stat().st_mtime, path.name), reverse=True)[0]


def _module_row(root: Path, spec: dict[str, str]) -> dict[str, Any]:
    path = _latest_artifact(root, spec["pattern"])
    report = _load_json(path) if path else {}
    return build_brain_layer_module_row(
        spec,
        report,
        artifact_path=str(path) if path else "",
        artifact_name=path.name if path else "",
    )


def build_brain_layer_acceptance_v0(*, ops_dir: str = DEFAULT_OPS_DIR) -> dict[str, Any]:
    root = Path(ops_dir)
    modules = [_module_row(root, spec) for spec in REQUIRED_REPORTS]
    return build_brain_layer_acceptance_report(modules, ops_dir=ops_dir)


__all__ = [
    "ACCEPTANCE_VERSION",
    "DEFAULT_OPS_DIR",
    "REQUIRED_REPORTS",
    "build_brain_layer_acceptance_v0",
]
