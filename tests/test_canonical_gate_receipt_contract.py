import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "scripts/verify.sh"


def test_canonical_gate_has_optional_machine_readable_receipt() -> None:
    source = VERIFY.read_text(encoding="utf-8")
    assert 'VKPI_VERIFY_JSON_OUT' in source
    assert 'vkpi_canonical_gate_receipt_v1' in source
    assert '"failed_steps"' in source
    assert '"status_sha256"' in source
    assert '"source_content_sha256"' in source
    assert '"source_file_count"' in source
    assert '"--no-renames"' in source


def test_requested_receipt_failure_is_fail_closed() -> None:
    source = VERIFY.read_text(encoding="utf-8")
    assert 'if ! write_verify_receipt; then' in source
    assert 'append_failed_step_once "canonical gate receipt"' in source


def test_production_dependency_audit_is_fail_closed_and_receipted() -> None:
    source = VERIFY.read_text(encoding="utf-8")
    step_name = "frontend production dependency security audit (moderate+)"
    command = "npm audit --omit=dev --audit-level=moderate"
    audit_block = source.split(
        "frontend_production_dependency_audit() {", 1
    )[1].split("\n}", 1)[0]

    assert f'( cd "$ROOT/frontend" && {command} )' in audit_block
    assert "|| true" not in audit_block
    assert "npm audit fix" not in audit_block
    assert "--force" not in audit_block
    assert f'run_static_step "{step_name}"' in source

    # Receipt coverage follows both canonical step registrars. Static steps
    # are reused only after a controller-bound Phase-A receipt is validated.
    registered_literal_steps = re.findall(
        r'^run_(?:static_)?step "([^"]+)"', source, flags=re.MULTILINE
    )
    assert step_name in registered_literal_steps
    assert 'index < ${#STEP_NAMES[@]}' in source
    assert 'step_args+=("${STEP_NAMES[$index]}"' in source
    assert '"steps": steps' in source
