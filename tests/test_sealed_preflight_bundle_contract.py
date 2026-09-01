from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _deploy() -> str:
    return (ROOT / "scripts/ops/deploy_local_to_cloud.sh").read_text(encoding="utf-8")


def test_sealed_preflight_bundle_starts_in_isolated_python(tmp_path: Path) -> None:
    deploy = _deploy()
    dependencies = (
        "scripts/ops/legacy_to_atomic_preflight_report.py",
        "scripts/ops/legacy_to_atomic_preflight_transport.py",
    )
    digest = deploy.split("compute_deploy_verifier_bundle_digest()", 1)[1].split(
        "verify_deploy_verifier_bundle()", 1
    )[0]
    verify = deploy.split("verify_deploy_verifier_bundle()", 1)[1].split(
        "seal_deploy_verifier_bundle()", 1
    )[0]
    seal = deploy.split("seal_deploy_verifier_bundle()", 1)[1].split(
        "cleanup_deploy_verifier_bundle()", 1
    )[0]
    cleanup = deploy.split("cleanup_deploy_verifier_bundle()", 1)[1].split(
        'LOCAL_CANDIDATE_WEB_PID=""', 1
    )[0]
    for relative in dependencies:
        assert f'Path("{relative}"): 0o400' in digest
        assert seal.count(relative) == 2
        assert f'"${{DEPLOY_VERIFIER_BUNDLE_DIR}}/{relative}"' in cleanup

    copy_list = seal.split("for relative in \\\n", 1)[1].split("; do", 1)[0]
    relatives = tuple(
        line.strip().removesuffix("\\").strip()
        for line in copy_list.splitlines()
        if line.strip()
    )
    assert set(dependencies).issubset(relatives)

    bundle = tmp_path / "sealed-verifier"
    for relative in relatives:
        source = ROOT / relative
        target = bundle / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        target.chmod(0o400 if relative in dependencies else 0o500)

    clis = (
        bundle / "scripts/ops/legacy_to_atomic_preflight.py",
        bundle / "scripts/verify_runtime_health.py",
    )
    hostile_venv = tmp_path / "hostile-venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(hostile_venv)],
        check=True,
    )
    hostile_site = next((hostile_venv / "lib").glob("python*/site-packages"))
    startup_marker = tmp_path / "hostile-pth-loaded"
    (hostile_site / "hostile.pth").write_text(
        f"import pathlib; pathlib.Path({str(startup_marker)!r}).write_text('loaded')\n",
        encoding="utf-8",
    )
    for cli in clis:
        completed = subprocess.run(
            [
                str(hostile_venv / "bin/python"),
                "-I",
                "-S",
                "-B",
                str(cli),
                "--help",
            ],
            cwd=tmp_path,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        assert completed.returncode == 0, f"{cli}: {completed.stderr}"
        assert "usage:" in completed.stdout.lower()
    assert not startup_marker.exists()
    assert not list(bundle.rglob("__pycache__"))
    assert '"${DEPLOY_PHYSICAL_PYTHON}" -I -S -B' in deploy
    assert "run_sealed_controller_python" in deploy
    assert "run_frozen_candidate_python" in deploy
    assert not re.search(r'LOCAL_SAFE_PYTHON\}"[^\n]*\s-c(?:\s|$)', deploy)


def test_sealed_verifier_digest_is_canonical_and_tamper_evident(tmp_path: Path) -> None:
    deploy = _deploy()
    compute_function = (
        "compute_deploy_verifier_bundle_digest()"
        + deploy.split("compute_deploy_verifier_bundle_digest()", 1)[1].split(
            "\nverify_deploy_verifier_bundle()", 1
        )[0]
    )
    verify_function = (
        "verify_deploy_verifier_bundle()"
        + deploy.split("verify_deploy_verifier_bundle()", 1)[1].split(
            "\nrun_sealed_controller_python()", 1
        )[0]
    )
    seal_function = (
        "seal_deploy_verifier_bundle()"
        + deploy.split("seal_deploy_verifier_bundle()", 1)[1].split(
            "\ncleanup_deploy_verifier_bundle()", 1
        )[0]
    )
    inventory = dict(
        re.findall(r'Path\("([^"]+)"\): (0o[0-7]+)', compute_function)
    )
    assert inventory

    source_root = tmp_path / "source"
    candidate_root = tmp_path / "candidate"
    for relative in inventory:
        payload = f"reviewed:{relative}\n".encode()
        for root in (source_root, candidate_root):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

    program = f"""\
set -euo pipefail
PROJECT_ROOT="$1"
DEPLOY_CANDIDATE_DIR="$2"
LOCAL_SAFE_PYTHON="$3"
export VKPI_SAFE_PYTHON_REAL="$4"
TMPDIR="$5"
DEPLOY_VERIFIER_BUNDLE_DIR=""
DEPLOY_VERIFIER_BUNDLE_SHA256=""
TRUSTED_CANDIDATE_VERIFIER=""
TRUSTED_RUNTIME_ADMISSION=""
DEPLOY_VERIFIER_BUNDLE_READY=0
{compute_function}
{verify_function}
{seal_function}
seal_deploy_verifier_bundle
[ "${{DEPLOY_VERIFIER_BUNDLE_READY}}" = "1" ]
first_digest="${{DEPLOY_VERIFIER_BUNDLE_SHA256}}"
verify_deploy_verifier_bundle
[ "${{DEPLOY_VERIFIER_BUNDLE_SHA256}}" = "${{first_digest}}" ]
tampered="${{DEPLOY_VERIFIER_BUNDLE_DIR}}/scripts/ops/freeze_worktree_candidate.py"
chmod u+w "${{tampered}}"
printf 'tampered\n' >> "${{tampered}}"
chmod 0500 "${{tampered}}"
if verify_deploy_verifier_bundle >/dev/null 2>&1; then
  echo "tampered verifier bundle was accepted" >&2
  exit 91
fi
install -m 0500 \
  "${{PROJECT_ROOT}}/scripts/ops/freeze_worktree_candidate.py" "${{tampered}}"
verify_deploy_verifier_bundle

extra_file="${{DEPLOY_VERIFIER_BUNDLE_DIR}}/scripts/ops/__init__.py"
printf 'unexpected\n' > "${{extra_file}}"
chmod 0400 "${{extra_file}}"
if verify_deploy_verifier_bundle >/dev/null 2>&1; then
  echo "extra verifier file was accepted" >&2
  exit 92
fi
rm -f "${{extra_file}}"
verify_deploy_verifier_bundle

extra_link="${{DEPLOY_VERIFIER_BUNDLE_DIR}}/scripts/ops/unexpected-link.py"
ln -s "${{tampered}}" "${{extra_link}}"
if verify_deploy_verifier_bundle >/dev/null 2>&1; then
  echo "extra verifier symlink was accepted" >&2
  exit 93
fi
rm -f "${{extra_link}}"
verify_deploy_verifier_bundle

extra_directory="${{DEPLOY_VERIFIER_BUNDLE_DIR}}/scripts/ops/unexpected"
mkdir "${{extra_directory}}"
if verify_deploy_verifier_bundle >/dev/null 2>&1; then
  echo "extra verifier directory was accepted" >&2
  exit 94
fi
rmdir "${{extra_directory}}"
verify_deploy_verifier_bundle

hardlink="${{TMPDIR}}/verifier-hardlink-${{RANDOM}}"
ln "${{tampered}}" "${{hardlink}}"
if verify_deploy_verifier_bundle >/dev/null 2>&1; then
  echo "hard-linked verifier file was accepted" >&2
  exit 95
fi
rm -f "${{hardlink}}"
verify_deploy_verifier_bundle
"""
    completed = subprocess.run(
        [
            "/bin/bash",
            "-c",
            program,
            "sealed-verifier-test",
            str(source_root),
            str(candidate_root),
            str(ROOT / "scripts/ops/safe_python.sh"),
            sys.executable,
            str(tmp_path),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
