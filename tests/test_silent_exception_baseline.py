from __future__ import annotations

from scripts.check_silent_exception_baseline import _find_silent_exceptions


def test_find_silent_exceptions_detects_pass_and_empty_returns(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text(
        """
def one():
    try:
        risky()
    except Exception:
        pass


def two():
    try:
        risky()
    except BaseException:
        return {}


def logged():
    try:
        risky()
    except Exception as exc:
        logger.warning("failed: %s", exc)
        return {}
""",
        encoding="utf-8",
    )

    findings = _find_silent_exceptions(source)

    assert [finding.kind for finding in findings] == ["pass", "return_empty_dict"]
