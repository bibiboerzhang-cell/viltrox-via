from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from app.domains.comments import collector
from app.domains.comments.collection_command import CollectorCommentCollectionCommand
from app.domains.kol import audience_language


ROOT = Path(__file__).resolve().parents[1]


class _Rows:
    def __init__(self, rows: Any) -> None:
        self.rows = rows

    def fetchall(self) -> list[Any]:
        return list(self.rows or [])

    def fetchone(self) -> Any:
        if isinstance(self.rows, list):
            return self.rows[0] if self.rows else None
        return self.rows


def test_high_value_selection_keeps_sql_order_limit_status_and_exception_contract(monkeypatch) -> None:
    executions: list[tuple[str, tuple[Any, ...]]] = []

    class Connection:
        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Rows:
            executions.append((" ".join(sql.split()), tuple(params)))
            return _Rows([{"id": 30}, {"id": 20}, {"id": 10}, {"id": 5}])

    class Command:
        def __init__(self) -> None:
            self.calls: list[tuple[int, Any, str]] = []

        def enqueue(self, kol_pool_id: int, *, staff: Any, queue_lane: str) -> dict[str, Any]:
            self.calls.append((kol_pool_id, staff, queue_lane))
            if kol_pool_id == 30:
                return {"status": "queued"}
            if kol_pool_id == 10:
                return {"status": "recently_done"}
            raise RuntimeError("queue unavailable")

    command = Command()
    monkeypatch.setattr("app.db.connection.get_conn", lambda: Connection())
    monkeypatch.setattr(
        audience_language,
        "audience_language_for_kol",
        lambda kol_pool_id, **_kwargs: {"sample_size": 8 if kol_pool_id == 20 else 0},
    )
    staff = {"id": 7}

    result = audience_language.enqueue_audience_comments_for_high_value(
        min_fit=81.5,
        limit=4,
        staff=staff,
        comment_command=command,
    )

    assert result == {
        "status": "done",
        "candidates": 4,
        "enqueued": 1,
        "already_has_comments": 1,
        "skipped": 2,
    }
    assert command.calls == [
        (30, staff, "batch"),
        (10, staff, "batch"),
        (5, staff, "batch"),
    ]
    sql, params = executions[0]
    assert "ORDER BY p.viltrox_fit_score DESC NULLS LAST LIMIT 4" in sql
    assert params == (81.5,)


def test_default_command_forwards_exact_enqueue_contract(monkeypatch) -> None:
    calls: list[tuple] = []
    expected = {"status": "already_queued", "job_id": 91}

    monkeypatch.setattr(
        collector,
        "enqueue_kol_pool_comments_job",
        lambda kol_pool_id, **kwargs: calls.append((kol_pool_id, kwargs)) or expected,
    )

    staff = {"id": 3}
    result = CollectorCommentCollectionCommand().enqueue(
        88,
        staff=staff,
        queue_lane="batch",
    )
    assert result is expected
    assert calls == [(88, {"staff": {"id": 3}, "queue_lane": "batch"})]
    assert calls[0][1]["staff"] is staff


def test_real_enqueue_adapter_never_invokes_provider(monkeypatch) -> None:
    class Connection:
        commits = 0

        def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> _Rows:
            normalized = " ".join(sql.split())
            if "FROM vkpi_kol_pool" in normalized:
                return _Rows({"id": 88, "handle": "creator", "display_name": "Creator"})
            if "FROM vkpi_kol_video_evidence" in normalized:
                return _Rows([{"id": 7}, {"id": 3}])
            if "status IN ('queued','running')" in normalized:
                return _Rows(None)
            if "status='done'" in normalized:
                return _Rows(None)
            raise AssertionError(normalized)

        def commit(self) -> None:
            self.commits += 1

    connection = Connection()
    monkeypatch.setattr("app.db.connection.get_conn", lambda: connection)
    monkeypatch.setattr(
        collector,
        "get_crawler",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider called during enqueue")),
    )
    monkeypatch.setattr(
        collector,
        "enqueue_active_apify_job",
        lambda _conn, **kwargs: ({"id": 501, "payload": kwargs["payload"]}, True),
    )

    result = CollectorCommentCollectionCommand().enqueue(
        88,
        staff=None,
        queue_lane="batch",
    )

    assert result["status"] == "queued"
    assert result["job_id"] == 501
    assert connection.commits == 1


def test_audience_language_has_no_comments_domain_import() -> None:
    source = ROOT / "backend/app/domains/kol/audience_language.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(name.startswith("app.domains.comments") for name in imports)
    assert "app.shared.comment_collection_port" in imports
