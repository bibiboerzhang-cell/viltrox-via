from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
GEMINI_FAMILY = (
    ROOT / "backend/app/workers/apify_jobs_worker_gemini.py",
    ROOT / "backend/app/workers/apify_jobs_worker_gemini_contract.py",
    ROOT / "backend/app/workers/apify_jobs_worker_gemini_runtime.py",
    ROOT / "backend/app/workers/apify_jobs_worker_gemini_persistence_runtime.py",
)


def test_gemini_worker_blocks_wrong_target_before_loading_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers import apify_jobs_worker  # noqa: F401
    from app.workers import apify_jobs_worker_gemini as worker

    events: list[tuple[Any, ...]] = []
    monkeypatch.setattr(worker, "_target", lambda _payload: ("kol", "77"))
    monkeypatch.setattr(
        worker,
        "_load_video_evidence",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("unsupported targets must stop before evidence I/O")
        ),
    )
    monkeypatch.setattr(
        worker,
        "_block_job",
        lambda *args: events.append(args),
    )

    conn = object()
    worker._process_gemini_video(conn, {"id": 901}, {}, 0.25)

    assert events == [
        (
            conn,
            901,
            "unsupported_gemini_target_type",
            {"target_type": "kol"},
        )
    ]


@pytest.mark.parametrize(
    ("derive_method", "processor_name"),
    (
        ("video_analysis_final_v1_keyframe_qa", "_process_gemini_video_final_v1_keyframe_qa"),
        ("gemini_video_v2_flash_pro_judge", "_process_gemini_video_flash_pro_judge"),
        ("gemini_video_v2_flash_gpt55_judge", "_process_gemini_video_flash_gpt55_judge"),
        ("gemini_video_v2_flash_claude_judge", "_process_gemini_video_flash_claude_judge"),
    ),
)
def test_gemini_special_routes_dispatch_after_evidence_before_main_analyzer(
    monkeypatch: pytest.MonkeyPatch,
    derive_method: str,
    processor_name: str,
) -> None:
    from app.workers import apify_jobs_worker  # noqa: F401
    from app.workers import apify_jobs_worker_gemini as worker

    evidence = {
        "id": 701,
        "content_url": "https://www.youtube.com/watch?v=abcdefghijk",
    }
    events: list[tuple[Any, ...]] = []
    monkeypatch.setattr(worker, "_target", lambda _payload: ("video", "701"))
    monkeypatch.setattr(worker, "_derive_method", lambda _payload: derive_method)
    monkeypatch.setattr(
        worker,
        "_load_video_evidence",
        lambda _conn, target_id: events.append(("load", target_id)) or evidence,
    )
    monkeypatch.setattr(
        worker,
        processor_name,
        lambda *args: events.append(("dispatch", *args)),
    )
    monkeypatch.setattr(
        worker,
        "_run_gemini_analyzer_with_timeout",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("special routes must not enter the main analyzer")
        ),
    )

    conn = object()
    job = {"id": 902}
    payload = {"derive_method": derive_method}
    worker._process_gemini_video(conn, job, payload, 0.125)

    assert events == [
        ("load", "701"),
        ("dispatch", conn, job, payload, evidence, 0.125),
    ]


def test_gemini_worker_orchestration_stays_bounded_and_acyclic_by_direction() -> None:
    trees = {
        str(path): ast.parse(path.read_text(encoding="utf-8"))
        for path in GEMINI_FAMILY
    }
    rows = collect_complexity(trees)
    entry = next(
        row
        for row in rows
        if row.path.endswith("apify_jobs_worker_gemini.py")
        and row.qualified_name == "_process_gemini_video"
    )

    assert entry.cc <= 10
    assert max(row.cc for row in rows) < 50
    assert all(len(path.read_text(encoding="utf-8").splitlines()) < 800 for path in GEMINI_FAMILY)

    contract_source = GEMINI_FAMILY[1].read_text(encoding="utf-8")
    persistence_source = GEMINI_FAMILY[3].read_text(encoding="utf-8")
    runtime_source = GEMINI_FAMILY[2].read_text(encoding="utf-8")
    assert "from app." not in contract_source
    assert "apify_jobs_worker_gemini_runtime" not in persistence_source
    assert "from app.workers.apify_jobs_worker_gemini import" not in runtime_source
