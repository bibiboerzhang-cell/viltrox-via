from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.domains.settings import use_cases as settings_use_cases
from app.domains.staff import profile as staff_profile
from app.shared.staff_role_policy import has_manager_staff_role


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"


@pytest.mark.parametrize(
    ("staff", "expected"),
    [
        ({}, False),
        ({"role": "member"}, False),
        ({"role": " ADMIN "}, True),
        ({"role": "manager"}, True),
        ({"role": "lead"}, True),
        ({"role": "marketing_lead"}, True),
        ({"role": "marketing_manager"}, True),
        ({"role": "marketing-manager"}, True),
        ({"role": "member", "is_owner": 1}, True),
        ({"role": "member", "is_owner": "1"}, True),
    ],
)
def test_shared_policy_preserves_both_domain_verdicts(staff, expected):
    assert has_manager_staff_role(staff) is expected
    assert settings_use_cases.is_manager_staff(staff) is expected
    assert staff_profile.is_manager_staff(staff) is expected


def test_shared_policy_preserves_legacy_result_and_exception_contract():
    roles = (None, "", "member", " ADMIN ", "manager", "marketing-manager")
    owner_values = (None, 0, 1, "0", "1", False, True, "invalid")

    for role in roles:
        for is_owner in owner_values:
            staff = {"role": role, "is_owner": is_owner}

            def legacy_verdict():
                normalized_role = str(staff.get("role") or "").strip().lower()
                if int(staff.get("is_owner") or 0) == 1:
                    return True
                return normalized_role in {
                    "admin",
                    "manager",
                    "lead",
                    "marketing_lead",
                    "marketing_manager",
                    "marketing-manager",
                }

            try:
                expected = legacy_verdict()
            except Exception as expected_error:  # preserve legacy fail-fast behavior
                with pytest.raises(type(expected_error)) as actual_error:
                    has_manager_staff_role(staff)
                assert actual_error.value.args == expected_error.args
            else:
                assert has_manager_staff_role(staff) is expected


def test_settings_use_case_no_longer_depends_on_staff_domain():
    path = ROOT / "backend/app/domains/settings/use_cases.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported_modules.add(node.module or "")

    assert not any(
        module == "app.domains.staff" or module.startswith("app.domains.staff.")
        for module in imported_modules
    )


def test_settings_use_case_cold_import_does_not_load_staff_domain(tmp_path):
    code = """
import sys
import app.domains.settings.use_cases
loaded = sorted(
    name for name in sys.modules
    if name == 'app.domains.staff' or name.startswith('app.domains.staff.')
)
if loaded:
    raise AssertionError(loaded)
"""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.fspath(tmp_path),
        "TMPDIR": os.fspath(tmp_path),
        "PYTHONPATH": os.fspath(BACKEND_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "VKPI_SKIP_DOTENV": "1",
        "ENVIRONMENT": "test",
        "JWT_SECRET": "staff-role-policy-boundary",
        "DB_RUNTIME_BACKEND": "sqlite",
    }
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_shared_policy_is_dependency_free():
    path = ROOT / "backend/app/shared/staff_role_policy.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    internal_imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            internal_imports.update(alias.name for alias in node.names if alias.name.startswith("app."))
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if module.startswith("app."):
                internal_imports.add(module)

    assert internal_imports == set()
