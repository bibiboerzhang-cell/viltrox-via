from __future__ import annotations

from pathlib import Path

from app.services.vkpi import x_comments_go_no_go


def test_x_comments_report_is_read_only_gate(monkeypatch) -> None:
    monkeypatch.setattr(x_comments_go_no_go, "_table_exists", lambda table: True)
    monkeypatch.setattr(x_comments_go_no_go, "_count", lambda table: 0)
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.delenv("APIFY_X_COMMENTS_ACTOR_ID", raising=False)

    report = x_comments_go_no_go.build_x_comments_go_no_go_report()

    assert report["passed"] is True
    assert report["decision"] == "hold_targets_required"
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["write_db"] is False
    assert report["sync_triggered"] is False
    assert report["external_http_calls"] is False
    assert report["run_ready_after_approval"] is False
    assert report["policy"]["no_daily_collection_before_go"] is True


def test_x_comments_target_file_requires_exactly_14_valid_targets(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(x_comments_go_no_go, "_table_exists", lambda table: True)
    monkeypatch.setattr(x_comments_go_no_go, "_count", lambda table: 0)
    target_file = tmp_path / "targets.csv"
    target_file.write_text(
        "target_id,source_url,brand_context,expected_signal\n"
        "x1,https://x.com/viltrox/status/1888888888888888888,Viltrox,launch_reaction\n",
        encoding="utf-8",
    )

    report = x_comments_go_no_go.build_x_comments_go_no_go_report(target_file)

    assert report["passed"] is True
    assert report["decision"] == "hold_targets_invalid"
    assert report["targets"]["valid"] is False
    assert "target_count:1_expected:14" in report["targets"]["errors"]


def test_x_comments_can_be_ready_after_targets_and_provider(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(x_comments_go_no_go, "_table_exists", lambda table: True)
    monkeypatch.setattr(x_comments_go_no_go, "_count", lambda table: 0)
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    monkeypatch.setenv("APIFY_TOKEN", "dummy")
    monkeypatch.setenv("APIFY_X_COMMENTS_ACTOR_ID", "actor/test")
    target_file = tmp_path / "targets.csv"
    rows = ["target_id,source_url,brand_context,expected_signal"]
    for idx in range(14):
        rows.append(
            f"x{idx + 1},https://x.com/example/status/{1888888888888888800 + idx},competitor,creator_conversation"
        )
    target_file.write_text("\n".join(rows) + "\n", encoding="utf-8")

    report = x_comments_go_no_go.build_x_comments_go_no_go_report(target_file)

    assert report["passed"] is True
    assert report["decision"] == "ready_for_manual_approval"
    assert report["provider_modes"]["preferred_path"] == "apify_comments_actor"
    assert report["run_ready_after_approval"] is True
