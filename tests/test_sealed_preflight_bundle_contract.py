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
        assert f'Path("{relative}"): 0o400' in verify
        assert seal.count(relative) == 3
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

    cli = bundle / "scripts/ops/legacy_to_atomic_preflight.py"
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
    completed = subprocess.run(
        [str(hostile_venv / "bin/python"), "-I", "-S", "-B", str(cli), "--help"],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout.lower()
    assert not startup_marker.exists()
    assert not list(bundle.rglob("__pycache__"))
    assert '"${DEPLOY_PHYSICAL_PYTHON}" -I -S -B' in deploy
    assert "run_sealed_controller_python" in deploy
    assert "run_frozen_candidate_python" in deploy
    assert not re.search(r'LOCAL_SAFE_PYTHON\}"[^\n]*\s-c(?:\s|$)', deploy)
