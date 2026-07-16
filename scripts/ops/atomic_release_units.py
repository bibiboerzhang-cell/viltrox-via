"""Systemd unit-state validation for the atomic release filesystem helper."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


UNIT_RE = re.compile(r"^[A-Za-z0-9@_.-]+\.service$")


class LayoutError(RuntimeError):
    pass


def unit_names(values: list[str]) -> list[str]:
    if not values or any(not UNIT_RE.fullmatch(value) for value in values):
        raise LayoutError("one or more reviewed systemd unit names are invalid")
    if len(values) != len(set(values)):
        raise LayoutError("reviewed systemd unit names must be unique")
    return values


def optional_unit_names(values: list[str]) -> list[str]:
    if any(not UNIT_RE.fullmatch(value) for value in values):
        raise LayoutError("one or more optional systemd unit names are invalid")
    if len(values) != len(set(values)):
        raise LayoutError("optional systemd unit names must be unique")
    return values


def unit_state_token(state: dict[str, bool]) -> str:
    return ":".join(
        (
            "present" if state["present"] else "absent",
            "active" if state["active"] else "inactive",
            "enabled" if state["enabled"] else "disabled",
            "masked" if state["masked"] else "unmasked",
        )
    )


def validate_unit_state(name: str, state: dict[str, bool]) -> dict[str, bool]:
    required = {"present", "active", "enabled", "masked"}
    if set(state) != required or any(type(state[key]) is not bool for key in required):
        raise LayoutError(f"optional systemd unit state is malformed: {name}")
    if not state["present"] and (state["active"] or state["enabled"] or state["masked"]):
        raise LayoutError(f"absent optional unit has impossible runtime state: {name}")
    if state["masked"] and (state["active"] or state["enabled"]):
        raise LayoutError(f"masked optional unit has impossible runtime state: {name}")
    return state


def parse_optional_unit_states(
    optional_units: list[str],
    values: list[str],
    unit_dir: Path,
) -> dict[str, dict[str, bool]]:
    parsed: dict[str, dict[str, bool]] = {}
    for raw in values:
        name, separator, token = str(raw or "").partition("=")
        if not separator or name not in optional_units or name in parsed:
            raise LayoutError("optional systemd unit state receipt is invalid")
        parts = token.split(":")
        if len(parts) != 4:
            raise LayoutError(f"optional systemd unit state token is invalid: {name}")
        presence, activity, enablement, masking = parts
        if (
            presence not in {"present", "absent"}
            or activity not in {"active", "inactive"}
            or enablement not in {"enabled", "disabled"}
            or masking not in {"masked", "unmasked"}
        ):
            raise LayoutError(f"optional systemd unit state token is invalid: {name}")
        state = validate_unit_state(
            name,
            {
                "present": presence == "present",
                "active": activity == "active",
                "enabled": enablement == "enabled",
                "masked": masking == "masked",
            },
        )
        installed = unit_dir / name
        path_masked = installed.is_symlink() and installed.resolve(strict=False) == Path("/dev/null")
        path_regular = installed.is_file() and not installed.is_symlink()
        path_absent = not installed.exists() and not installed.is_symlink()
        if state["masked"] and not path_masked:
            raise LayoutError(f"captured masked unit path does not point to /dev/null: {installed}")
        if state["present"] and not state["masked"] and not path_regular:
            raise LayoutError(f"captured present unit is not a regular file: {installed}")
        if not state["present"] and not path_absent:
            raise LayoutError(f"captured absent unit path exists: {installed}")
        parsed[name] = state
    if set(parsed) != set(optional_units):
        raise LayoutError("every optional systemd unit requires an exact state receipt")
    return parsed


def _systemctl_value(systemctl_bin: str, command: str, unit_name: str) -> str:
    try:
        result = subprocess.run(
            [systemctl_bin, command, unit_name],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LayoutError(f"systemd state probe failed for {unit_name}") from exc
    value = result.stdout.strip()
    if not value:
        value = result.stderr.strip()
    return value.splitlines()[0].strip() if value else ""


def inspect_unit_state(args: argparse.Namespace) -> None:
    """Read one optional unit's four-dimensional rollback state."""

    name = optional_unit_names([args.unit_name])[0]
    unit_dir = Path(args.unit_dir).resolve()
    installed = unit_dir / name
    path_masked = installed.is_symlink() and installed.resolve(strict=False) == Path("/dev/null")
    path_regular = installed.is_file() and not installed.is_symlink()
    path_absent = not installed.exists() and not installed.is_symlink()
    if not (path_masked or path_regular or path_absent):
        raise LayoutError(f"optional installed unit path is unsafe: {installed}")
    active_raw = _systemctl_value(args.systemctl_bin, "is-active", name)
    enabled_raw = _systemctl_value(args.systemctl_bin, "is-enabled", name)
    if active_raw not in {"active", "inactive"}:
        raise LayoutError(f"optional unit active state is not restorable: {name}={active_raw!r}")
    if enabled_raw in {"enabled", "enabled-runtime"}:
        enabled, systemd_masked = True, False
    elif enabled_raw == "disabled":
        enabled, systemd_masked = False, False
    elif enabled_raw in {"masked", "masked-runtime"}:
        enabled, systemd_masked = False, True
    elif enabled_raw == "not-found" and path_absent:
        enabled, systemd_masked = False, False
    else:
        raise LayoutError(f"optional unit enablement is not restorable: {name}={enabled_raw!r}")
    if path_masked != systemd_masked:
        raise LayoutError(f"optional unit mask state disagrees with its unit path: {name}")
    state = validate_unit_state(
        name,
        {
            "present": not path_absent,
            "active": active_raw == "active",
            "enabled": enabled,
            "masked": systemd_masked,
        },
    )
    sys.stdout.write(unit_state_token(state) + "\n")


__all__ = [
    "LayoutError",
    "inspect_unit_state",
    "optional_unit_names",
    "parse_optional_unit_states",
    "unit_names",
    "unit_state_token",
    "validate_unit_state",
]
