from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY_PATH = ROOT / "scripts" / "verify.sh"


def test_verify_captures_browser_token_without_exporting_it_to_child_steps() -> None:
    source = VERIFY_PATH.read_text(encoding="utf-8")

    xtrace_off = source.index('case "$-" in *x*) set +x ;; esac')
    capture = source.index(
        '_VKPI_VERIFY_BROWSER_GATE_TOKEN="${VKPI_BROWSER_GATE_TOKEN:-}"'
    )
    scrub = source.index(
        "unset VKPI_BROWSER_GATE_TOKEN POST_DEPLOY_BROWSER_TOKEN",
        capture,
    )
    unexport = source.index("export -n _VKPI_VERIFY_BROWSER_GATE_TOKEN", scrub)
    first_subprocess = source.index('SCRIPT_DIR="$(cd', unexport)
    first_child_step = source.index(
        'run_static_step "release candidate worktree (required for deploy)"',
        first_subprocess,
    )
    browser_gate = source.index("browser_console_release_gate()", first_child_step)

    assert xtrace_off < capture < scrub < unexport < first_subprocess
    assert first_subprocess < first_child_step < browser_gate
    assert 'local token="${_VKPI_VERIFY_BROWSER_GATE_TOKEN:-}"' in source[browser_gate:]
    assert 'local token="${VKPI_BROWSER_GATE_TOKEN:-}"' not in source


def test_verify_preamble_scrubs_tokens_even_with_xtrace_and_allexport() -> None:
    source = VERIFY_PATH.read_text(encoding="utf-8")
    preamble = source.split(
        "# ---- 定位仓库根(本脚本在 <root>/scripts/ 下)----",
        maxsplit=1,
    )[0]
    sentinel = "header.payload.verify-token-sentinel"
    probe = preamble + """
test "${_VKPI_VERIFY_BROWSER_GATE_TOKEN}" = "${EXPECTED_SENTINEL}"
test -z "${VKPI_BROWSER_GATE_TOKEN+x}"
test -z "${POST_DEPLOY_BROWSER_TOKEN+x}"
unset EXPECTED_SENTINEL
/bin/bash -c 'test -z "${VKPI_BROWSER_GATE_TOKEN+x}" && test -z "${POST_DEPLOY_BROWSER_TOKEN+x}" && test -z "${_VKPI_VERIFY_BROWSER_GATE_TOKEN+x}"'
"""
    completed = subprocess.run(
        ["/bin/bash", "-a", "-x", "-c", probe],
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "EXPECTED_SENTINEL": sentinel,
            "VKPI_BROWSER_GATE_TOKEN": sentinel,
            "POST_DEPLOY_BROWSER_TOKEN": "post-deploy-token-sentinel",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    combined = completed.stdout + completed.stderr
    assert sentinel not in combined
    assert "post-deploy-token-sentinel" not in combined


def test_browser_gate_has_one_narrow_token_injection_and_immediate_clear() -> None:
    source = VERIFY_PATH.read_text(encoding="utf-8")
    start = source.index("browser_console_release_gate()")
    end = source.index(
        'run_step "$BROWSER_CONSOLE_STEP_NAME" browser_console_release_gate',
        start,
    )
    function = source[start:end]

    consume = function.index(
        'local token="${_VKPI_VERIFY_BROWSER_GATE_TOKEN:-}"'
    )
    destroy_global = function.index("unset _VKPI_VERIFY_BROWSER_GATE_TOKEN", consume)
    unexport_local = function.index("export -n token", destroy_global)
    inject = function.index('VKPI_BROWSER_GATE_TOKEN="$token" node', unexport_local)
    clear = function.index('token=""', inject)
    verifier = function.index("verify_browser_console_capture.py", clear)

    assert consume < destroy_global < unexport_local < inject < clear < verifier
    assert source.count('VKPI_BROWSER_GATE_TOKEN="$token" node') == 1
