"""CC51 characterization — build_identity_reconciliation_plan 动刀前锁行为.

口径:固定 pool/alias/session 输入,产出 plan 与录制 golden **逐键递归相等断言**
(含 plan_sha256、去重组、安全/人工桥、alias 回填与拒绝原因、官方隔离行、头像口径);
golden 由改刀前原码录制;改刀前后本文件必须同绿。
"""
from __future__ import annotations

import copy
import json
from typing import Any

from app.domains.kol.identity_reconciliation_plan import (
    build_identity_reconciliation_plan,
    plan_summary,
)


def _pool_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "platform": "youtube",
            "handle": "@alphacreator",
            "display_name": "Alpha Creator",
            "dashboard_account_type": "kol",
            "duplicate_of_id": None,
            "raw_platform_data": "{}",
            "avatar_url": "https://yt3.ggpht.com/a/avatar1",
        },
        {
            "id": 2,
            "platform": "youtube",
            "handle": "UCalpha1234567",
            "display_name": "Alpha Creator Backup",
            "dashboard_account_type": "kol",
            "duplicate_of_id": None,
            "raw_platform_data": "{}",
            "avatar_url": None,
        },
        {
            "id": 3,
            "platform": "instagram",
            "handle": "viltrox_official",
            "display_name": "VILTROX Official",
            "dashboard_account_type": "kol",
            "duplicate_of_id": None,
            "raw_platform_data": "{}",
            "avatar_url": "https://example.com/a.png",
        },
        {
            "id": 4,
            "platform": "tiktok",
            "handle": "foldedcreator",
            "display_name": "Folded Creator",
            "dashboard_account_type": "kol",
            "duplicate_of_id": 1,
            "raw_platform_data": "{}",
            "avatar_url": None,
        },
        {
            "id": 5,
            "platform": "youtube",
            "handle": "@gamma",
            "display_name": "Gamma Films",
            "dashboard_account_type": "kol",
            "duplicate_of_id": None,
            "raw_platform_data": "{}",
            "avatar_url": "ftp://bad-host/avatar",
        },
        {
            "id": 6,
            "platform": "instagram",
            "handle": "viltroxglobal",
            "display_name": "Viltrox Global",
            "dashboard_account_type": "company",
            "duplicate_of_id": None,
            "raw_platform_data": "{}",
            "avatar_url": "",
        },
        {
            "id": 7,
            "platform": "youtube",
            "handle": "watch",
            "display_name": "Weird Row",
            "dashboard_account_type": "kol",
            "duplicate_of_id": None,
            "raw_platform_data": "{}",
            "avatar_url": "https://p16-sign-va.tiktokcdn.com/img.jpeg?x-expires=1",
        },
        {
            "id": 8,
            "platform": "tiktok",
            "handle": "deltamaker",
            "display_name": "Delta Maker",
            "dashboard_account_type": "kol",
            "duplicate_of_id": None,
            "raw_platform_data": "{}",
            "avatar_url": None,
        },
        {
            "id": 9,
            "platform": "tiktok",
            "handle": "epsilon",
            "display_name": "Epsilon Studio",
            "dashboard_account_type": "kol",
            "duplicate_of_id": None,
            "raw_platform_data": "{}",
            "avatar_url": None,
        },
    ]


def _alias_rows() -> list[dict[str, Any]]:
    return [
        {"id": 11, "kol_pool_id": 2, "platform": "youtube", "handle": "@alphacreator"},
        {"id": 12, "kol_pool_id": 8, "platform": "tiktok", "handle": "deltamaker"},
        {"id": 13, "kol_pool_id": 8, "platform": "tiktok", "handle": "epsilon"},
        {"id": 14, "kol_pool_id": 0, "platform": "tiktok", "handle": "orphanalias"},
        {"id": 15, "kol_pool_id": 4, "platform": "tiktok", "handle": "foldedcreator"},
    ]


def _session_items() -> list[dict[str, Any]]:
    return [
        {
            "id": 101,
            "session_id": 100,
            "item_type": "new_creator",
            "session_archived_at": None,
            "source_url": "https://youtube.com/@gamma",
            "payload": {
                "platform": "youtube",
                "handle": "@gamma",
                "channel_id": "UCgamma1234567",
                "kol_pool_id": 5,
                "avatar_url": "https://yt3.ggpht.com/gamma",
            },
        },
        {
            "id": 102,
            "session_id": 100,
            "item_type": "new_creator",
            "session_archived_at": None,
            "source_url": "https://youtube.com/@alphacreator",
            "payload": '{"platform": "youtube", "handle": "@alphacreator", "kol_pool_id": 1}',
        },
        {
            "id": 103,
            "session_id": 100,
            "item_type": "existing_kol",
            "session_archived_at": None,
            "source_url": None,
            "payload": {
                "platform": "youtube",
                "handle": "@alphacreator",
                "kol_pool_id": 1,
            },
        },
        {
            "id": 104,
            "session_id": 200,
            "item_type": "online_qualified_candidate",
            "session_archived_at": None,
            "source_url": None,
            "payload": {
                "platform": "youtube",
                "handle": "@alphacreator",
                "channel_id": "UCalpha1234567",
                "kol_pool_id": 1,
            },
        },
        {
            "id": 105,
            "session_id": 200,
            "item_type": "new_creator",
            "session_archived_at": None,
            "source_url": None,
            "payload": {
                "platform": "instagram",
                "handle": "viltroxlens",
                "kol_pool_id": None,
                "avatar_url": "https://scontent.cdninstagram.com/v/t51/img.jpg?oe=FFFFFFFF",
            },
        },
        {
            "id": 106,
            "session_id": 300,
            "item_type": "existing_kol",
            "session_archived_at": "2026-08-01T00:00:00Z",
            "source_url": None,
            "payload": {"platform": "instagram", "handle": "viltroxphoto"},
        },
        {
            "id": 107,
            "session_id": 300,
            "item_type": "search_note",
            "session_archived_at": None,
            "source_url": None,
            "payload": {"note": "not a creator card"},
        },
        {
            "id": 108,
            "session_id": 300,
            "item_type": "recall_candidate",
            "session_archived_at": None,
            "source_url": None,
            "payload": {
                "platform": "youtube",
                "handle": "@folded",
                "channel_id": "UCfolded9999999",
                "kol_pool_id": 4,
            },
        },
    ]


GENERATED_AT = "2026-08-30T12:00:00Z"

GOLDEN_PLAN = r"""{
  "schema_version": "discovery_identity_reconciliation_plan_v1",
  "generated_at": "2026-08-30T12:00:00Z",
  "source": "local_production_snapshot",
  "mode": "dry_run",
  "claim_status": "descriptive_only",
  "writes_performed": 0,
  "pool": {
    "physical_rows": 9,
    "currently_visible_master_rows": 8,
    "already_soft_folded_rows": 1,
    "rows_with_canonical_alias": 8,
    "canonical_duplicate_group_count": 2,
    "canonical_extra_visible_rows": 2,
    "canonical_projection_unique_rows": 6,
    "duplicate_groups": [
      {
        "pool_rows": [
          {
            "id": 1,
            "platform": "youtube",
            "handle": "@alphacreator",
            "display_name": "Alpha Creator",
            "dashboard_account_type": "kol"
          },
          {
            "id": 2,
            "platform": "youtube",
            "handle": "UCalpha1234567",
            "display_name": "Alpha Creator Backup",
            "dashboard_account_type": "kol"
          }
        ],
        "shared_aliases": [
          "youtube:handle:alphacreator"
        ],
        "action": "manual_review_only"
      },
      {
        "pool_rows": [
          {
            "id": 8,
            "platform": "tiktok",
            "handle": "deltamaker",
            "display_name": "Delta Maker",
            "dashboard_account_type": "kol"
          },
          {
            "id": 9,
            "platform": "tiktok",
            "handle": "epsilon",
            "display_name": "Epsilon Studio",
            "dashboard_account_type": "kol"
          }
        ],
        "shared_aliases": [
          "tiktok:handle:epsilon"
        ],
        "action": "manual_review_only"
      }
    ]
  },
  "session_read_projection": {
    "session_count": 3,
    "creator_item_count": 7,
    "sessions_with_canonical_folds": 1,
    "creator_cards_folded": 1,
    "folded_sessions": [
      {
        "session_id": 100,
        "raw_creator_cards": 3,
        "canonical_creator_cards": 2,
        "folded_cards": 1
      }
    ]
  },
  "avatar_integrity": {
    "pool_visible_rows": {
      "durable": 2,
      "ephemeral": 0,
      "expired": 1,
      "invalid": 1,
      "missing": 4
    },
    "session_creator_items": {
      "durable": 1,
      "ephemeral": 1,
      "expired": 0,
      "invalid": 0,
      "missing": 5
    },
    "network_probe_performed": false
  },
  "identity_alias_backfill": {
    "existing_alias_rows": 5,
    "safe_bridge_group_count": 1,
    "manual_bridge_group_count": 1,
    "safe_bridge_groups": [
      {
        "kol_pool_id": 5,
        "youtube_id_aliases": [
          "youtube:id:ucgamma1234567"
        ],
        "youtube_handle_aliases": [
          "youtube:handle:gamma"
        ],
        "evidence_item_ids": [
          101
        ]
      }
    ],
    "manual_bridge_groups": [
      {
        "kol_pool_id": 1,
        "youtube_id_aliases": [
          "youtube:id:ucalpha1234567"
        ],
        "youtube_handle_aliases": [
          "youtube:handle:alphacreator"
        ],
        "evidence_item_ids": [
          104
        ],
        "review_reasons": [
          "alias_conflicts_with_pool_row"
        ]
      }
    ],
    "safe_alias_backfill_count": 4,
    "safe_alias_backfills": [
      {
        "kol_pool_id": 3,
        "platform": "instagram",
        "handle": "viltrox_official",
        "alias_kind": "handle",
        "confidence": 1.0,
        "source": "pool_top_level",
        "evidence_item_ids": []
      },
      {
        "kol_pool_id": 5,
        "platform": "youtube",
        "handle": "gamma",
        "alias_kind": "handle",
        "confidence": 1.0,
        "source": "pool_top_level",
        "evidence_item_ids": []
      },
      {
        "kol_pool_id": 2,
        "platform": "youtube",
        "handle": "ucalpha1234567",
        "alias_kind": "id",
        "confidence": 1.0,
        "source": "pool_top_level",
        "evidence_item_ids": []
      },
      {
        "kol_pool_id": 5,
        "platform": "youtube",
        "handle": "ucgamma1234567",
        "alias_kind": "id",
        "confidence": 0.95,
        "source": "historical_session_bridge",
        "evidence_item_ids": [
          101
        ]
      }
    ],
    "review_alias_count": 3,
    "review_aliases": [
      {
        "kol_pool_id": 1,
        "canonical_alias": "youtube:handle:alphacreator",
        "review_reasons": [
          "alias_observed_on_other_pool_row",
          "existing_alias_owned_by_other_pool"
        ]
      },
      {
        "kol_pool_id": 7,
        "canonical_alias": "youtube:handle:watch",
        "review_reasons": [
          "unsafe_or_reserved_locator"
        ]
      },
      {
        "kol_pool_id": 9,
        "canonical_alias": "tiktok:handle:epsilon",
        "review_reasons": [
          "alias_observed_on_other_pool_row",
          "existing_alias_owned_by_other_pool"
        ]
      }
    ],
    "write_contract": {
      "physical_delete_allowed": false,
      "duplicate_pointer_write_allowed": false,
      "master_selection_allowed": false,
      "score_field_write_allowed": false,
      "apply_supported_by_this_planner": false
    }
  },
  "official_isolation": {
    "pool_confirmed_count": 2,
    "pool_plan": [
      {
        "id": 3,
        "platform": "instagram",
        "handle": "viltrox_official",
        "display_name": "VILTROX Official",
        "dashboard_account_type": "kol",
        "verdict": "own_brand",
        "plan_action": "propose_company_segment_and_discovery_quarantine",
        "proposed_dashboard_account_type": "company",
        "metadata_marker": {
          "kind": "discovery_official_isolation_v1",
          "verdict": "own_brand",
          "source": "conservative_discovery_account_gate"
        }
      },
      {
        "id": 6,
        "platform": "instagram",
        "handle": "viltroxglobal",
        "display_name": "Viltrox Global",
        "dashboard_account_type": "company",
        "verdict": "own_brand",
        "plan_action": "keep_existing_non_kol_segment",
        "proposed_dashboard_account_type": "company",
        "metadata_marker": {
          "kind": "discovery_official_isolation_v1",
          "verdict": "own_brand",
          "source": "conservative_discovery_account_gate"
        }
      }
    ],
    "session_confirmed_count": 2,
    "session_unarchived_confirmed_count": 1,
    "session_read_plan": [
      {
        "item_id": 105,
        "session_id": 200,
        "kol_pool_id": null,
        "platform": "instagram",
        "handle": "viltroxlens",
        "verdict": "own_brand",
        "session_archived": false,
        "planned_read_action": "hide_from_discovery_projection_keep_evidence_row"
      },
      {
        "item_id": 106,
        "session_id": 300,
        "kol_pool_id": null,
        "platform": "instagram",
        "handle": "viltroxphoto",
        "verdict": "own_brand",
        "session_archived": true,
        "planned_read_action": "hide_from_discovery_projection_keep_evidence_row"
      }
    ],
    "physical_history_delete_allowed": false
  },
  "estimated_impact": {
    "before_release_snapshot": {
      "pool_visible_master_rows": 8,
      "alias_table_rows": 5,
      "unarchived_confirmed_official_session_cards": 1
    },
    "after_read_guard_release": {
      "confirmed_official_session_cards_hidden": 1,
      "history_rows_deleted": 0,
      "pool_rows_deleted": 0,
      "duplicate_pointer_rows_written": 0
    },
    "after_separately_reviewed_alias_backfill": {
      "status": "estimate_only_not_executed",
      "expected_alias_table_rows_without_drift": 9,
      "safe_uc_handle_bridge_groups": 1,
      "manual_bridge_groups_unchanged": 1,
      "pool_physical_rows_unchanged": 9,
      "score_fields_unchanged": true
    }
  },
  "plan_sha256": "0a49263be6f3558d5da1b40f91bb93e6e5f0823d971b5a06c6704009a4c4c37d"
}"""

GOLDEN_SUMMARY = r"""{
  "schema_version": "discovery_identity_reconciliation_plan_v1",
  "mode": "dry_run",
  "writes_performed": 0,
  "plan_sha256": "0a49263be6f3558d5da1b40f91bb93e6e5f0823d971b5a06c6704009a4c4c37d",
  "pool_rows": 9,
  "pool_visible_rows": 8,
  "canonical_duplicate_groups": 2,
  "canonical_extra_visible_rows": 2,
  "session_cards_folded": 1,
  "safe_bridge_groups": 1,
  "manual_bridge_groups": 1,
  "safe_alias_backfills": 4,
  "pool_confirmed_official": 2,
  "unarchived_session_confirmed_official": 1
}"""


def _build() -> dict[str, Any]:
    return build_identity_reconciliation_plan(
        pool_rows=_pool_rows(),
        alias_rows=_alias_rows(),
        session_items=_session_items(),
        generated_at=GENERATED_AT,
    )


def _assert_same(path: str, expected: Any, actual: Any) -> None:
    """plan 行结构逐键递归相等断言 —— 差异按最短路径报错。"""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected dict, got {type(actual).__name__}"
        assert sorted(actual) == sorted(expected), (
            f"{path}: key set drift expected={sorted(expected)} actual={sorted(actual)}"
        )
        for key in expected:
            _assert_same(f"{path}.{key}", expected[key], actual[key])
        return
    if isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: expected list, got {type(actual).__name__}"
        assert len(actual) == len(expected), (
            f"{path}: length drift expected={len(expected)} actual={len(actual)}"
        )
        for index, (left, right) in enumerate(zip(expected, actual)):
            _assert_same(f"{path}[{index}]", left, right)
        return
    assert actual == expected, f"{path}: expected {expected!r}, got {actual!r}"


def test_plan_deep_equals_recorded_golden_key_by_key() -> None:
    plan = _build()
    golden = json.loads(GOLDEN_PLAN)
    _assert_same("plan", golden, plan)
    assert plan == golden
    # 类型级锁定:canonical 序列化必须逐字节一致(同时锁 plan_sha256 的输入形态)。
    canonical = lambda value: json.dumps(  # noqa: E731
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    assert canonical(plan) == canonical(golden)


def test_plan_sha256_is_deterministic_and_locked() -> None:
    golden = json.loads(GOLDEN_PLAN)
    first = _build()
    second = _build()
    assert first["plan_sha256"] == second["plan_sha256"] == golden["plan_sha256"]


def test_plan_summary_deep_equals_recorded_golden() -> None:
    summary = plan_summary(_build())
    assert summary == json.loads(GOLDEN_SUMMARY)


def test_write_contract_and_read_only_claims_stay_locked() -> None:
    plan = _build()
    assert plan["mode"] == "dry_run"
    assert plan["writes_performed"] == 0
    assert plan["claim_status"] == "descriptive_only"
    assert plan["identity_alias_backfill"]["write_contract"] == {
        "physical_delete_allowed": False,
        "duplicate_pointer_write_allowed": False,
        "master_selection_allowed": False,
        "score_field_write_allowed": False,
        "apply_supported_by_this_planner": False,
    }
    assert plan["official_isolation"]["physical_history_delete_allowed"] is False
    assert plan["avatar_integrity"]["network_probe_performed"] is False


def test_planner_never_mutates_inputs() -> None:
    pool = _pool_rows()
    aliases = _alias_rows()
    sessions = _session_items()
    pool_before = copy.deepcopy(pool)
    aliases_before = copy.deepcopy(aliases)
    sessions_before = copy.deepcopy(sessions)
    build_identity_reconciliation_plan(
        pool_rows=pool,
        alias_rows=aliases,
        session_items=sessions,
        generated_at=GENERATED_AT,
    )
    assert pool == pool_before
    assert aliases == aliases_before
    assert sessions == sessions_before


def test_source_label_and_generated_at_pass_through() -> None:
    plan = build_identity_reconciliation_plan(
        pool_rows=[],
        alias_rows=[],
        session_items=[],
        generated_at="2026-08-30T13:00:00Z",
        source_label="unit_probe",
    )
    assert plan["generated_at"] == "2026-08-30T13:00:00Z"
    assert plan["source"] == "unit_probe"
    assert plan["pool"]["physical_rows"] == 0
    assert plan["identity_alias_backfill"]["safe_alias_backfills"] == []
    assert plan["estimated_impact"]["after_read_guard_release"]["history_rows_deleted"] == 0
