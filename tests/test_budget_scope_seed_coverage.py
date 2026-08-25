"""Budget-scope seed coverage guard (audit R8/R9, migration 303).

Why this file exists
--------------------
``llm_production.generate_json`` defaults to ``require_configured_budget=True``.
The gateway turns that into
``check_budget_scopes([monthly_total, single_call, provider:X, cost_scope],
require_configured=True)`` and ``budget_guard.check_budget_scopes`` fails closed
on any scope that has **no row** in ``vkpi_provider_budget_caps``.  So a caller
whose ``cost_tag``/``purpose`` never got a seed row is not "unbudgeted" -- it is
100 percent dead, permanently degraded to rule_v0, and it never spends a cent
that would make the loss visible on the cost panel.  ``vkpi_intelligent_ask`` sat
in exactly that state (both of its recorded calls: ``all_providers_failed`` /
``provider=rule_v0``).

The guard below derives the strict caller set straight from the source tree, so
a *new* strict caller added without a seed row fails here instead of silently
answering with rule_v0 in production.

Two deliberate design choices:

* Scope resolution is static and conservative.  A call site whose cost scope
  cannot be read off a string literal or a module-level string constant is a
  hard failure (``test_every_strict_scope_is_statically_resolvable``) rather than
  a silent skip -- an unreadable scope is exactly how a caller escapes the net.
* Coverage is checked against ``migrations/*.sql``, not against whatever rows a
  live database happens to hold.  Two strict scopes (``audience_stats``,
  ``vkpi_kol_content_fit``) existed only because an operator hand-created them in
  the cost panel; every fresh rebuild had neither, so those features started dead
  in any new environment.  Migration 303 gives them a migration provenance.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.db import connection


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "backend" / "app"
MIGRATIONS_DIR = ROOT / "migrations"
UP = MIGRATIONS_DIR / "303_vkpi_budget_scope_seeds.sql"
DOWN = MIGRATIONS_DIR / "303_vkpi_budget_scope_seeds_down.sql"

CAPS_TABLE = "vkpi_provider_budget_caps"

# Scopes the gateway always adds alongside the caller's own scope
# (llm_gateway._budget_scopes_for_provider).  They must be configured too or the
# strict plan fails closed for every caller at once.
GATEWAY_INFRA_SCOPES = (
    "monthly_total",
    "single_call",
    "provider:openai",
    "provider:claude",
    "provider:gemini",
)

# The 16 rows migration 303 seeds.
MIGRATION_303_SCOPES = (
    "cron:marketing_advisor",
    "cron:kol_outreach_pack",
    "cron:vkpi_mention_sentiment",
    "cron:vkpi_sentiment_annotate",
    "cron:deepsight_triad",
    "cron:vkpi_weekly_summary",
    "vkpi_intelligent_ask",
    "comment_reply_draft",
    "vkpi_sentiment",
    "vkpi_pillar",
    "vkpi_contract_polish",
    "kol_outreach_draft",
    "kol_content_scorer",
    "projects:contract_extract",
    "audience_stats",
    "vkpi_kol_content_fit",
)

# Pre-existing rows that 303 must leave completely alone.  Each one carries real
# ledger history under its own name; aligning a drifted scope means seeding the
# scope the caller actually uses, never renaming the historical row.
DRIFT_SIBLINGS_LEFT_INTACT = (
    "cron:vkpi_pillar",
    "cron:vkpi_weekly_report",
    "cron:vkpi_contract_extract",
)


# --------------------------------------------------------------------------
# Static discovery of strict generate_json call sites
# --------------------------------------------------------------------------


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "literal"`` bindings (how every caller names its scope)."""

    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            value, targets = node.value, node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            value, targets = node.value, [node.target]
        else:
            continue
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value.value
    return constants


def _literal_str(node: ast.AST | None, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _is_generate_json(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr == "generate_json"
    if isinstance(node, ast.Name):
        return node.id == "generate_json"
    return False


def _generate_json_call(node: ast.Call) -> ast.Call | None:
    """Return the call whose keywords are generate_json's, direct or wrapped.

    ``asyncio.to_thread(llm_production.generate_json, prompt, cost_tag=...)`` and
    ``functools.partial(...)`` forward the keywords verbatim, so they are the same
    strict boundary as a direct call.
    """

    if _is_generate_json(node.func):
        return node
    func = node.func
    name = ""
    if isinstance(func, ast.Attribute):
        name = func.attr
    elif isinstance(func, ast.Name):
        name = func.id
    if name in {"to_thread", "partial"} and node.args and _is_generate_json(node.args[0]):
        return node
    return None


def _cost_scope(node: ast.Call, constants: dict[str, str]) -> str | None:
    """Mirror llm_gateway._cost_scope_for_purpose for one static call site."""

    keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
    cost_tag = keywords.get("cost_tag")
    explicit = None
    if cost_tag is not None and not (
        isinstance(cost_tag, ast.Constant) and cost_tag.value is None
    ):
        explicit = _literal_str(cost_tag, constants)
        if explicit is None:
            return None
        if explicit.strip():
            return explicit.strip().lower().replace(" ", "_")
    purpose_node = keywords.get("purpose")
    if purpose_node is None:
        return ""
    purpose = _literal_str(purpose_node, constants)
    if purpose is None:
        return None
    purpose_key = purpose.strip().lower().replace(" ", "_")
    return f"cron:{purpose_key}" if purpose_key else ""


def _is_strict(node: ast.Call) -> bool:
    for keyword in node.keywords:
        if keyword.arg == "require_configured_budget":
            if isinstance(keyword.value, ast.Constant):
                return bool(keyword.value.value)
            # Non-literal: assume the strict default rather than let it slip out.
            return True
    return True


def _scan_generate_json_call_sites() -> tuple[dict[str, list[str]], list[str]]:
    """Return ({strict scope: [locations]}, [unresolvable locations])."""

    strict: dict[str, list[str]] = {}
    unresolved: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = _module_string_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call = _generate_json_call(node)
            if call is None:
                continue
            location = f"{path.relative_to(ROOT)}:{call.lineno}"
            scope = _cost_scope(call, constants)
            if scope is None:
                unresolved.append(location)
                continue
            if not _is_strict(call) or not scope:
                continue
            strict.setdefault(scope, []).append(location)
    return strict, unresolved


STRICT_SCOPES, UNRESOLVED_CALL_SITES = _scan_generate_json_call_sites()


# --------------------------------------------------------------------------
# Static discovery of seeded scopes in migrations/
# --------------------------------------------------------------------------

_INSERT_RE = re.compile(
    rf"INSERT\s+INTO\s+{CAPS_TABLE}\b(.*?);", re.IGNORECASE | re.DOTALL
)
# A seed tuple always opens with the quoted scope followed by a numeric cap.
_SCOPE_RE = re.compile(r"\(\s*'([^']+)'\s*,\s*[0-9]")
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


def _strip_sql_comments(text: str) -> str:
    """Drop ``--`` comments before statement splitting.

    Prose legitimately contains semicolons; leaving comments in truncates the
    INSERT body at the first one and silently under-reports seeded scopes.
    """

    return _LINE_COMMENT_RE.sub("", text)


def _migration_seeded_scopes() -> dict[str, list[str]]:
    seeded: dict[str, list[str]] = {}
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name.endswith("_down.sql"):
            continue
        text = path.read_text(encoding="utf-8")
        if CAPS_TABLE not in text:
            continue
        for statement in _INSERT_RE.findall(_strip_sql_comments(text)):
            for scope in _SCOPE_RE.findall(statement):
                seeded.setdefault(scope, []).append(path.name)
    return seeded


MIGRATION_SEEDED_SCOPES = _migration_seeded_scopes()


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_the_scanner_actually_found_the_known_strict_callers() -> None:
    """Guard the guard: a broken scanner must not pass as full coverage."""

    assert len(STRICT_SCOPES) >= 19, STRICT_SCOPES
    for scope in ("vkpi_intelligent_ask", "cron:deepsight_triad", "vkpi_pillar"):
        assert scope in STRICT_SCOPES, sorted(STRICT_SCOPES)


def test_every_strict_scope_is_statically_resolvable() -> None:
    """A cost scope this file cannot read is a caller that can escape the net."""

    assert not UNRESOLVED_CALL_SITES, (
        "generate_json call sites whose cost scope is neither a string literal "
        "nor a module-level string constant: "
        f"{UNRESOLVED_CALL_SITES}"
    )


def test_every_strict_generate_json_scope_has_a_migration_seed() -> None:
    missing = {
        scope: locations
        for scope, locations in STRICT_SCOPES.items()
        if scope not in MIGRATION_SEEDED_SCOPES
    }
    assert not missing, (
        "require_configured_budget=True callers with no vkpi_provider_budget_caps "
        "seed in migrations/. Each one is a permanent rule_v0 degradation on any "
        f"freshly built database: {missing}"
    )


def test_gateway_infrastructure_scopes_have_migration_seeds() -> None:
    """monthly_total / single_call / provider:* gate every strict caller at once."""

    missing = [s for s in GATEWAY_INFRA_SCOPES if s not in MIGRATION_SEEDED_SCOPES]
    assert not missing, missing


def test_migration_303_seeds_exactly_the_documented_scope_set() -> None:
    seeded_by_303 = {
        scope
        for scope, files in MIGRATION_SEEDED_SCOPES.items()
        if UP.name in files
    }
    assert seeded_by_303 == set(MIGRATION_303_SCOPES)


def test_migration_303_is_discovered_and_runner_owned() -> None:
    sequence = connection._discover_postgres_migrations()
    assert UP.name in sequence
    assert sequence.index(UP.name) > sequence.index("292_vkpi_agent_llm_budget_scopes.sql")
    up = UP.read_text(encoding="utf-8")
    assert connection._FORWARD_TRANSACTION_CONTROL_RE.search(up) is None
    assert "ON CONFLICT (scope) DO NOTHING" in up


def test_migration_303_avoids_the_compat_placeholder_trap() -> None:
    """An ASCII question mark anywhere (SQL or comment) explodes on apply."""

    for path in (UP, DOWN):
        text = path.read_text(encoding="utf-8")
        assert "?" not in text, f"{path.name} contains an ASCII question mark"
        assert "%" not in text, f"{path.name} contains an ASCII percent sign"


def test_migration_303_never_writes_over_an_existing_row() -> None:
    """Seed-only: an operator-tuned cap or a live spend total must survive re-apply."""

    statements = _strip_sql_comments(UP.read_text(encoding="utf-8")).upper()
    assert "DO UPDATE" not in statements
    assert "UPDATE " not in statements
    assert "DELETE" not in statements
    assert "ALTER " not in statements


def test_migration_303_down_removes_exactly_what_it_seeded() -> None:
    down = DOWN.read_text(encoding="utf-8")
    for scope in MIGRATION_303_SCOPES:
        assert f"'{scope}'" in down, scope
    # Operator-tuned or earlier-migration rows of the same name survive rollback.
    assert "strpos(metadata_json, 'migration_303') > 0" in down
    assert f"DELETE FROM schema_migrations" in down
    for scope in DRIFT_SIBLINGS_LEFT_INTACT:
        assert f"'{scope}'" not in down, scope


def test_drift_is_fixed_by_seeding_the_caller_scope_not_by_renaming_history() -> None:
    """R9: align on the scope the caller really sends; never touch the old row."""

    up = UP.read_text(encoding="utf-8")
    for actual, legacy in (
        ("vkpi_pillar", "cron:vkpi_pillar"),
        ("cron:vkpi_weekly_summary", "cron:vkpi_weekly_report"),
        ("projects:contract_extract", "cron:vkpi_contract_extract"),
    ):
        assert actual in STRICT_SCOPES or actual in MIGRATION_303_SCOPES, actual
        assert UP.name in MIGRATION_SEEDED_SCOPES.get(actual, []), actual
        # The legacy sibling keeps its own row and its own ledger history.
        assert UP.name not in MIGRATION_SEEDED_SCOPES.get(legacy, []), legacy
        assert legacy in up, f"{legacy} should be explained in the migration notes"


@pytest.mark.pg
def test_live_postgres_has_a_caps_row_for_every_strict_scope(pg_conn) -> None:
    wanted = sorted(set(STRICT_SCOPES) | set(GATEWAY_INFRA_SCOPES) | set(MIGRATION_303_SCOPES))
    with pg_conn.cursor() as cur:
        cur.execute(
            f"SELECT scope FROM {CAPS_TABLE} WHERE scope = ANY(%s)",
            (wanted,),
        )
        present = {row[0] for row in cur.fetchall()}
    missing = [scope for scope in wanted if scope not in present]
    assert not missing, f"caps rows missing after migration 303: {missing}"


@pytest.mark.pg
def test_live_postgres_seed_rows_carry_sane_caps(pg_conn) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            f"SELECT scope, cap_usd, warning_at, hard_stop_at FROM {CAPS_TABLE} "
            "WHERE strpos(metadata_json, 'migration_303') > 0 ORDER BY scope"
        )
        rows = cur.fetchall()
    assert rows, "migration 303 has not been applied to this database"
    for scope, cap_usd, warning_at, hard_stop_at in rows:
        assert float(cap_usd) > 0, scope
        assert 0 < float(warning_at) <= float(hard_stop_at) <= 1.0, scope
        # cron:* scopes roll daily (budget_windows), everything else rolls monthly.
        expected = 2.00 if scope.startswith("cron:") else 10.00
        assert float(cap_usd) == pytest.approx(expected), (scope, float(cap_usd))
