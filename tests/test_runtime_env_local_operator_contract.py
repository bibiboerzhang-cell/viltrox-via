from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _fixture(tmp_path: Path, operator_text: str) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    scripts = root / "scripts"
    runtime = root / "runtime"
    scripts.mkdir(parents=True)
    runtime.mkdir()
    shutil.copy2(ROOT / "scripts/runtime_env.sh", scripts / "runtime_env.sh")
    shutil.copy2(ROOT / "scripts/runtime_env.py", scripts / "runtime_env.py")
    operator = runtime / "local_operator_env.sh"
    operator.write_text(operator_text, encoding="utf-8")
    return root, operator


def _source(root: Path, *, environment: str) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": str(root / "home"),
        "PATH": "/usr/bin:/bin",
        "ENVIRONMENT": environment,
        "LOCAL_ENV_FILE": str(root / "missing.env"),
        "RUNTIME_ENV_QUIET": "1",
    }
    if environment != "local":
        env.update(
            {
                "JWT_SECRET": "fixture-production-jwt-not-a-real-secret",
                "ADMIN_PASSWORD": "fixture-production-password",
            }
        )
    return subprocess.run(
        [
            "/bin/bash",
            "-c",
            'source "$1" && printf "%s|%s\\n" '
            '"${VKPI_LOCAL_OPERATOR_ENV_STATUS:-}" '
            '"${VKPI_LLM_READINESS_OPERATOR_ACK:-}"',
            "runtime-env-test",
            str(root / "scripts/runtime_env.sh"),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _apply_python(root: Path, *, environment: str) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": str(root / "home"),
        "PATH": "/usr/bin:/bin",
        "ENVIRONMENT": environment,
        "LOCAL_ENV_FILE": str(root / "missing.env"),
    }
    if environment != "local":
        env.update(
            {
                "JWT_SECRET": "fixture-production-jwt-not-a-real-secret",
                "ADMIN_PASSWORD": "fixture-production-password",
            }
        )
    return subprocess.run(
        [
            str(ROOT / ".venv/bin/python"),
            "-I",
            "-B",
            "-c",
            "import os,runpy,sys; "
            "ns=runpy.run_path(sys.argv[1]); ns['apply_runtime_env'](); "
            "print(os.environ.get('VKPI_LOCAL_OPERATOR_ENV_STATUS','')+'|'"
            "+os.environ.get('VKPI_LLM_READINESS_OPERATOR_ACK','')+'|'"
            "+os.environ.get('ENVIRONMENT',''))",
            str(root / "scripts/runtime_env.py"),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_local_launcher_loads_exact_operator_ack_as_data(tmp_path: Path) -> None:
    root, _operator = _fixture(
        tmp_path,
        'export VKPI_LLM_READINESS_OPERATOR_ACK="google/gemini-3.6-flash,'
        'openai/gpt-5.6-luna"\n',
    )
    result = _source(root, environment="local")
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "loaded|google/gemini-3.6-flash,openai/gpt-5.6-luna\n"
    )
    python_result = _apply_python(root, environment="local")
    assert python_result.returncode == 0, python_result.stderr
    assert python_result.stdout == (
        "loaded|google/gemini-3.6-flash,openai/gpt-5.6-luna|local\n"
    )


def test_operator_file_is_never_executed_as_shell(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    root, _operator = _fixture(
        tmp_path,
        f'printf exploited > "{marker}"\n'
        'export VKPI_LLM_READINESS_OPERATOR_ACK="google/gemini-3.6-flash"\n',
    )
    result = _source(root, environment="local")
    assert result.returncode != 0
    assert "non-assignment content" in result.stderr
    assert not marker.exists()


def test_production_ignores_runtime_operator_authorization(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    root, _operator = _fixture(
        tmp_path,
        'export VKPI_LLM_READINESS_OPERATOR_ACK="google/gemini-3.6-flash"\n'
        f'printf exploited > "{marker}"\n',
    )
    result = _source(root, environment="production")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ignored_nonlocal|\n"
    assert not marker.exists()


def test_local_operator_symlink_fails_closed(tmp_path: Path) -> None:
    root, operator = _fixture(
        tmp_path,
        'export VKPI_LLM_READINESS_OPERATOR_ACK="google/gemini-3.6-flash"\n',
    )
    target = tmp_path / "operator-target"
    operator.rename(target)
    operator.symlink_to(target)
    result = _source(root, environment="local")
    assert result.returncode != 0
    assert "file is unsafe" in result.stderr


def test_non_vkpi_key_fails_closed_without_export(tmp_path: Path) -> None:
    root, _operator = _fixture(
        tmp_path,
        'export PATH="/attacker"\n'
        'export VKPI_LLM_READINESS_OPERATOR_ACK="google/gemini-3.6-flash"\n',
    )
    result = _source(root, environment="local")
    assert result.returncode != 0
    assert "forbidden key" in result.stderr


def test_unreviewed_vkpi_key_also_fails_closed(tmp_path: Path) -> None:
    root, _operator = _fixture(
        tmp_path,
        'export VKPI_FREEZE_GIT_WRAPPER="/attacker"\n',
    )
    result = _source(root, environment="local")
    assert result.returncode != 0
    assert "forbidden key" in result.stderr


def test_duplicate_key_fails_closed_in_shell_and_python(tmp_path: Path) -> None:
    root, _operator = _fixture(
        tmp_path,
        'export VKPI_LLM_READINESS_OPERATOR_ACK="google/gemini-3.6-flash"\n'
        'export VKPI_LLM_READINESS_OPERATOR_ACK="openai/gpt-5.6-luna"\n',
    )
    shell_result = _source(root, environment="local")
    python_result = _apply_python(root, environment="local")
    for result in (shell_result, python_result):
        assert result.returncode != 0
        assert "duplicate key" in result.stderr


def test_shell_substitution_text_is_never_executed(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    root, _operator = _fixture(
        tmp_path,
        f'export VKPI_ADVISOR_EXTERNAL_AI_ENABLED="$(touch {marker})"\n',
    )
    result = _source(root, environment="local")
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_production_overlay_cannot_switch_operator_scope_back_to_local(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "must-not-exist"
    root, _operator = _fixture(
        tmp_path,
        f'printf exploited > "{marker}"\n'
        'export VKPI_LLM_READINESS_OPERATOR_ACK="google/gemini-3.6-flash"\n',
    )
    (root / ".env.production").write_text("ENVIRONMENT=local\n", encoding="utf-8")
    result = _source(root, environment="production")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ignored_nonlocal|\n"
    assert not marker.exists()
    python_result = _apply_python(root, environment="production")
    assert python_result.returncode == 0, python_result.stderr
    assert python_result.stdout == "ignored_nonlocal||production\n"
    assert not marker.exists()
