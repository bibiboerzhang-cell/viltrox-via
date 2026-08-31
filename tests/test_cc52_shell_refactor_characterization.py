"""test_cc52_shell_refactor_characterization(前半;共享层见 _support)。"""
from tests.test_cc52_shell_refactor_characterization_support import (  # noqa: F401
    ADVANCED_MODEL_MODE,
    Any,
    DETERMINISTIC_DESCRIPTIVE_MODE,
    NormalizedRequest,
    OPENAI_MODEL,
    Path,
    QueryScope,
    QueryWindow,
    REPORT_CHALLENGER_MODEL,
    REPORT_PRIMARY_MODEL,
    VideoCacheCancelled,
    _BudgetGuard,
    _FakeResponse,
    _LedgerConn,
    _NOW,
    _OneRow,
    _Resolved,
    _RouteConn,
    _SOW_PLACEHOLDER,
    _creators,
    _full_routes,
    _good_sources,
    _llm_payload,
    _model_readiness,
    _patch_gateway,
    _patch_policy_env,
    _patch_pool_env,
    _patch_video_env,
    _ready_payload,
    _request,
    cache,
    datetime,
    freshness_status,
    handlers,
    hashlib,
    json,
    ledger,
    llm_gateway,
    llm_production,
    model_policy,
    outreach,
    pytest,
    timezone,
)




def test_record_call_forced_without_scope_raises_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _LedgerConn()
    _patch_gateway(monkeypatch, conn)
    with pytest.raises(RuntimeError, match="forced_ai_cost_ledger_scope_missing"):
        ledger.record_call(provider="openai", force_cost_ledger=True, cost_tag="  ")
    assert conn.calls == []
    assert conn.commits == 0


def test_record_call_insert_row_byte_level(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _LedgerConn(select_row={"call_uid": "seen", "provider": "openai"})
    budget = _BudgetGuard(result={"recorded": True, "ledger_id": 7, "cost_micro_usd": 30000})
    _patch_gateway(monkeypatch, conn, budget=budget)
    result = ledger.record_call(
        provider="openai",
        model="gpt-x",
        purpose="test_purpose",
        prompt="hello",
        input_tokens=10,
        output_tokens=5,
        cost_cents=3,
        status="success",
        fallback_used=False,
        cost_tag="scope_a",
        staff={"staff_id": 42},
        metadata={"latency_ms": 123, "k": "v"},
    )
    insert_sql, insert_params = conn.calls[0]
    assert insert_sql.startswith("INSERT INTO vkpi_llm_calls")
    assert insert_sql.count("?") == 15
    uid = insert_params[0]
    assert uid.startswith("llm-") and len(uid) == 4 + 16
    assert insert_params[1:] == (
        "openai",
        "gpt-x",
        "test_purpose",
        hashlib.sha256(b"hello").hexdigest(),
        10,
        5,
        3,
        30000,
        123,
        "success",
        False,
        42,
        "2026-08-30T00:00:00Z",
        json.dumps({"latency_ms": 123, "k": "v"}, ensure_ascii=False, default=str),
    )
    assert conn.commits == 1
    select_sql, select_params = conn.calls[1]
    assert select_sql == "SELECT * FROM vkpi_llm_calls WHERE call_uid=?"
    assert select_params == (uid,)
    assert result["call"] == {"call_uid": "seen", "provider": "openai"}
    assert result["cost_ledger"] == {"recorded": True, "ledger_id": 7, "cost_micro_usd": 30000}
    assert "cost_ledger_error" not in result

    # 成本台账调用逐字段。
    assert len(budget.calls) == 1
    kwargs = budget.calls[0]
    assert kwargs == {
        "scope": "scope_a",
        "cron_task": "test_purpose",
        "ai_provider": "openai",
        "model_name": "gpt-x",
        "cost_usd": 0.03,
        "tokens_in": 10,
        "tokens_out": 5,
        "staff_id": 42,
        "metadata": {
            "latency_ms": 123,
            "k": "v",
            "llm_call_uid": uid,
            "purpose": "test_purpose",
            "status": "success",
            "fallback_used": False,
        },
        "triggered_by": {"staff_id": 42},
        "extra_scopes": ["monthly_total", "prov:openai"],
        "optional_scopes": ["scope_a", "monthly_total", "prov:openai"],
        "update_budget_scopes": True,
    }


def test_record_call_micro_usd_wins_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _LedgerConn(select_row=None)
    _patch_gateway(monkeypatch, conn)
    result = ledger.record_call(provider="", cost_micro_usd=123456)
    _sql, params = conn.calls[0]
    assert params[1] == "unknown"  # provider 空 → unknown
    assert params[2] == ""
    assert params[4] == ""  # prompt 空 → 空 hash
    assert params[7] == 12  # final_cents = round(123456/10000)
    assert params[8] == 123456
    assert params[9] is None  # 无 latency_ms → NULL
    assert params[10] == "not_configured"
    assert params[11] is True  # fallback 默认
    assert params[12] is None  # 无 staff → NULL
    assert params[14] == "{}"  # metadata None → "{}"
    assert result["call"] == {"call_uid": params[0]}
    assert result["cost_ledger"] is None


def test_record_call_alias_resolution_notes_original_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _LedgerConn()
    _patch_gateway(monkeypatch, conn)
    monkeypatch.setattr(ledger, "resolve_model_alias", lambda p, m: "exact-model")
    ledger.record_call(provider="openai", model="alias-latest", metadata={"a": 1})
    _sql, params = conn.calls[0]
    assert params[2] == "exact-model"
    assert json.loads(params[14]) == {"a": 1, "model_alias": "alias-latest"}


def test_record_call_skips_cost_ledger_without_success_or_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _LedgerConn()
    budget = _BudgetGuard(result={"recorded": True})
    _patch_gateway(monkeypatch, conn, budget=budget)
    result = ledger.record_call(provider="openai", status="failed", cost_tag="scope_a")
    assert budget.calls == []
    assert result["cost_ledger"] is None
    assert "cost_ledger_error" not in result


def test_record_call_nonforced_ledger_error_is_transparent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _LedgerConn(select_row={"call_uid": "x"})
    budget = _BudgetGuard(exc=ValueError("boom"))
    _patch_gateway(monkeypatch, conn, budget=budget)
    result = ledger.record_call(
        provider="openai", status="success", cost_tag="scope_a", metadata={"m": 1}
    )
    assert result["cost_ledger"] is None
    assert result["cost_ledger_error"] == "ValueError: boom"
    update_sql, update_params = conn.calls[1]
    assert update_sql == "UPDATE vkpi_llm_calls SET metadata_json=? WHERE call_uid=?"
    assert json.loads(update_params[0]) == {"m": 1, "cost_ledger_error": "ValueError: boom"}
    assert conn.commits == 2  # INSERT + error-note UPDATE


def test_record_call_forced_amount_mismatch_raises_with_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _LedgerConn()
    budget = _BudgetGuard(result={"recorded": True, "ledger_id": 3, "cost_micro_usd": 999})
    _patch_gateway(monkeypatch, conn, budget=budget)
    with pytest.raises(
        RuntimeError,
        match="forced_ai_cost_ledger_write_failed: RuntimeError: forced_ai_cost_ledger_amount_mismatch",
    ):
        ledger.record_call(
            provider="openai",
            status="success",
            cost_cents=3,
            cost_tag="scope_a",
            force_cost_ledger=True,
        )
    update_sql, update_params = conn.calls[1]
    assert update_sql.startswith("UPDATE vkpi_llm_calls SET metadata_json=?")
    assert json.loads(update_params[0])["cost_ledger_error"] == (
        "RuntimeError: forced_ai_cost_ledger_amount_mismatch"
    )
    # 强制模式 optional_scopes 必须为空(真写入,不允许 optional 兜底)。
    assert budget.calls[0]["optional_scopes"] == ()


@pytest.mark.parametrize(
    "ledger_result, needle",
    [
        (None, "forced_ai_cost_ledger_write_unconfirmed"),
        ({"recorded": False}, "forced_ai_cost_ledger_write_unconfirmed"),
        ({"recorded": True, "ledger_id": 0}, "forced_ai_cost_ledger_id_missing"),
    ],
)
def test_record_call_forced_validations(
    monkeypatch: pytest.MonkeyPatch, ledger_result: Any, needle: str
) -> None:
    conn = _LedgerConn()
    budget = _BudgetGuard(result=ledger_result)
    _patch_gateway(monkeypatch, conn, budget=budget)
    with pytest.raises(RuntimeError, match=needle):
        ledger.record_call(
            provider="openai",
            status="success",
            cost_cents=1,
            cost_tag="scope_a",
            force_cost_ledger=True,
        )


def test_record_call_triggered_by_int_used_as_staff_and_passed_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _LedgerConn()
    budget = _BudgetGuard(result={"recorded": True, "ledger_id": 1, "cost_micro_usd": 10000})
    _patch_gateway(monkeypatch, conn, budget=budget)
    ledger.record_call(
        provider="google",
        status="success",
        cost_cents=1,
        cost_tag="scope_b",
        triggered_by="77",
    )
    _sql, params = conn.calls[0]
    assert params[12] == 77
    assert budget.calls[0]["staff_id"] == 77
    assert budget.calls[0]["triggered_by"] == "77"


def test_generate_outreach_no_resolvable_creators(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outreach, "_load_creators", lambda ids: ([], [9, 8]))
    result = outreach.generate_outreach([9, 8])
    assert result == {
        "ok": False,
        "reason": "no_resolvable_creators",
        "llm_used": False,
        "messages": [],
        "sow_draft": {},
        "missing_kol_pool_ids": [9, 8],
        "truncated": False,
    }


def test_generate_outreach_llm_success_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(outreach, "_load_creators", lambda ids: (_creators(), [77]))

    def fake_generate_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
        seen["prompt"] = prompt
        seen["kwargs"] = kwargs
        return {
            "status": "success",
            "provider": "openai",
            "model": OPENAI_MODEL,
            "json": _llm_payload(),
        }

    monkeypatch.setattr(llm_production, "generate_json", fake_generate_json)
    result = outreach.generate_outreach(
        [1, 2],
        brief={"product_positioning": "AF 85mm", "target_persona": "filmmakers", "search_session_id": 5},
        staff={"staff_id": 42},
        preferred_provider="openai",
    )
    assert result["ok"] is True
    assert result["llm_used"] is True
    assert result["provider"] == "openai"
    assert result["model"] == OPENAI_MODEL
    assert result["reason"] == ""
    assert result["creator_count"] == 2
    assert result["missing_kol_pool_ids"] == [77]
    assert result["truncated"] is False
    assert result["max_creators"] == 8
    assert [m["subject"] for m in result["messages"]] == ["S1", "S2"]
    assert [m["personalized"] for m in result["messages"]] == [True, True]
    assert result["messages"][0] == {
        "kol_pool_id": 1,
        "handle": "alpha",
        "display_name": "Alpha",
        "platform": "youtube",
        "subject": "S1",
        "body": "B1",
        "personalized": True,
    }
    # SOW:LLM 字段保留,报酬一律 placeholder(红线)。
    assert result["sow_draft"] == {
        "scope": "Scope-X",
        "deliverables": ["d1", "d2"],
        "timeline": "2 weeks",
        "usage_rights": "3 months",
        "compensation": _SOW_PLACEHOLDER,
        "is_draft": True,
    }
    # LLM 调用参数锁死。
    kwargs = seen["kwargs"]
    assert kwargs["provider"] == "openai"
    assert kwargs["model"] == OPENAI_MODEL
    assert kwargs["purpose"] == "kol_outreach_draft"
    assert kwargs["cost_tag"] == "kol_outreach_draft"
    assert kwargs["max_output_tokens"] == 600 + 240 * 2
    assert kwargs["triggered_by"] == {"staff_id": 42}
    assert kwargs["staff"] == {"staff_id": 42}
    assert kwargs["required_keys"] == ("messages", "sow_draft")
    assert kwargs["deadline_seconds"] == 75.0
    assert kwargs["metadata"] == {
        "task_binding": "kol_outreach_pack",
        "creator_count": 2,
        "positioning": "AF 85mm",
        "search_session_id": 5,
        "phase": "project_outreach",
        "subphase": "draft_messages_and_sow",
        "attempt_index": 1,
        "total": 1,
        "target_label": "creators:2",
    }
    assert kwargs["validator"](_llm_payload()) is True
    assert "id=1 | Alpha | platform=youtube | topic=lenses | followers=1000" in seen["prompt"]
    assert "id=2 | beta | platform=instagram | topic=n/a | followers=n/a" in seen["prompt"]


def test_generate_outreach_partial_llm_filled_with_templates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _llm_payload()
    payload["messages"] = [payload["messages"][0]]
    monkeypatch.setattr(outreach, "_load_creators", lambda ids: (_creators(), []))
    monkeypatch.setattr(
        llm_production,
        "generate_json",
        lambda prompt, **kwargs: {
            "status": "success",
            "provider": "openai",
            "model": OPENAI_MODEL,
            "json": payload,
        },
    )
    result = outreach.generate_outreach([1, 2], preferred_provider="openai")
    assert result["llm_used"] is True
    assert [m["kol_pool_id"] for m in result["messages"]] == [1, 2]
    assert [m["personalized"] for m in result["messages"]] == [True, False]
    template = result["messages"][1]
    assert template["subject"] == "Viltrox × beta — collaboration invite"
    assert template["body"].startswith("Hi beta,")


def test_generate_outreach_provider_exception_falls_back_to_templates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(outreach, "_load_creators", lambda ids: (_creators(), []))

    def boom(prompt: str, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm_production, "generate_json", boom)
    result = outreach.generate_outreach([1, 2], preferred_provider="openai")
    assert result["ok"] is True
    assert result["llm_used"] is False
    assert result["provider"] == "rule_v0"
    assert result["model"] == ""
    assert result["reason"] == "llm_unavailable_used_template"
    assert [m["personalized"] for m in result["messages"]] == [False, False]
    assert result["sow_draft"]["compensation"] == _SOW_PLACEHOLDER
    assert result["sow_draft"]["is_draft"] is True
    assert result["sow_draft"]["creator_count"] == 2


def test_generate_outreach_wrong_provider_response_not_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(outreach, "_load_creators", lambda ids: (_creators(), []))
    monkeypatch.setattr(
        llm_production,
        "generate_json",
        lambda prompt, **kwargs: {
            "status": "success",
            "provider": "someone_else",
            "model": "other",
            "json": _llm_payload(),
            "reason": "budget_exceeded",
        },
    )
    result = outreach.generate_outreach([1, 2], preferred_provider="openai")
    assert result["llm_used"] is False
    assert result["provider"] == "someone_else"
    assert result["reason"] == "budget_exceeded"
    assert all(m["personalized"] is False for m in result["messages"])


def test_generate_outreach_dedupes_and_truncates_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_ids: list[list[int]] = []

    def fake_load(ids: list[int]) -> tuple[list[dict[str, Any]], list[int]]:
        seen_ids.append(list(ids))
        return _creators(), []

    monkeypatch.setattr(outreach, "_load_creators", fake_load)
    monkeypatch.setattr(
        llm_production,
        "generate_json",
        lambda prompt, **kwargs: {"status": "unavailable", "provider": "rule_v0", "model": ""},
    )
    raw_ids = [1, 1, "2", None, "x", 3, 4, 5, 6, 7, 8, 9, 10]
    result = outreach.generate_outreach(raw_ids, preferred_provider="openai")
    assert seen_ids == [[1, 2, 3, 4, 5, 6, 7, 8]]  # 去重 + 截到 8
    assert result["truncated"] is True
    assert result["max_creators"] == 8


def test_kol_pool_overview_source_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pool_env(monkeypatch, pool_columns=set(), tables_present=set())
    response = handlers.kol_pool_overview(_RouteConn({}), _request(), None, now=_NOW)
    assert response["status"] == "error"
    assert response["answer"] == "KOL Pool 数据源不可用。"
    assert response["degraded_reason"] == "kol_pool_source_unavailable"
    assert response["coverage"]["status"] == "unknown"
    assert response["coverage"]["notes"] == ["vkpi_kol_pool 数据源不可用。"]
    assert response["missing_fields"] == [
        {"field": "vkpi_kol_pool", "reason": "数据源表不可用", "impact": "无法核验 KOL 数量"}
    ]


def test_kol_pool_overview_requested_filter_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable = {"field": "filters.platform", "reason": "不可用", "impact": "无"}
    _patch_pool_env(
        monkeypatch,
        pool_columns={"id"},
        tables_present=set(),
        predicates=([], [], [unavailable, {"field": "other", "reason": "x", "impact": "y"}]),
    )
    response = handlers.kol_pool_overview(_RouteConn({}), _request(), None, now=_NOW)
    assert response["status"] == "error"
    assert response["degraded_reason"] == "requested_filter_unavailable"
    assert response["missing_fields"] == [unavailable]
    assert response["coverage"]["notes"] == ["筛选条件未被静默忽略。"]


def test_kol_pool_overview_full_zh_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pool_env(
        monkeypatch,
        pool_columns={"id", "duplicate_of_id", "updated_at"},
        tables_present={"vkpi_kol_video_evidence", "vkpi_kol_llm_deep_analysis_results"},
        predicates=(["p.owner=?"], [42], []),
        evidence_columns={"is_active", "scraped_at", "updated_at", "created_at"},
    )
    conn = _RouteConn(_full_routes())
    response = handlers.kol_pool_overview(conn, _request(), None, now=_NOW)

    assert response["status"] == "ready"
    assert response["answer"] == (
        "当前有效去重 KOL 共 120 个；45 个已有有效视频证据；12 个已有完成态深度分析。"
    )
    assert [f["key"] for f in response["facts"]] == [
        "kol.total",
        "kol.with_video_evidence",
        "video.evidence_rows",
        "kol.deep_analyzed",
        "kol.merged_duplicates",
    ]
    assert [f["value"] for f in response["facts"]] == [120, 45, 300, 12, 7]
    assert response["facts"][0]["confidence"] == "high"
    assert response["coverage"] == {
        "status": "complete",
        "matched_entities": 120,
        "evidence_count": 300,
        "total_scope": 120,
        "analyzed_count": 12,
        "ratio": round(45 / 120, 4),
        "notes": ["KOL 总数排除 duplicate_of_id 已归并从行。"],
    }
    assert response["missing_fields"] == []
    assert [e["id"] for e in response["evidence"]] == [
        "kol-pool-aggregate",
        "kol-video-aggregate",
    ]
    assert response["evidence"][0]["observed_at"] == "2026-08-28T00:00:00Z"
    assert response["evidence"][1]["observed_at"] == "2026-08-29T00:00:00Z"
    assert response["freshness"]["data_updated_at"] == "2026-08-29T00:00:00Z"
    assert response["freshness"]["status"] == freshness_status(
        "2026-08-29T00:00:00Z", now=_NOW
    )
    assert response["actions"] == [
        {
            "type": "navigate",
            "label": "打开 KOL Pool",
            "route": "kol-pool",
            "params": {"scope": "auto"},
            "requires_approval": False,
        }
    ]
    # SQL 形状:池聚合按谓词过滤,视频聚合叠加 is_active,深析追加 status 参数。
    pool_sql, pool_params = conn.calls[0]
    assert "COUNT(*) AS total_kols" in pool_sql
    assert "(SELECT COUNT(*) FROM vkpi_kol_pool WHERE duplicate_of_id IS NOT NULL)" in pool_sql
    assert "MAX(p.updated_at) AS data_updated_at" in pool_sql
    assert pool_params == (42,)
    video_sql, video_params = conn.calls[1]
    assert "e.is_active IS NOT FALSE" in video_sql
    assert "MAX(COALESCE(e.scraped_at, e.updated_at, e.created_at))" in video_sql
    assert video_params == (42,)
    deep_sql, deep_params = conn.calls[2]
    assert "d.status = ?" in deep_sql
    assert deep_params == (42, "ready")


def test_kol_pool_overview_full_en_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pool_env(
        monkeypatch,
        pool_columns={"id", "duplicate_of_id", "updated_at"},
        tables_present={"vkpi_kol_video_evidence", "vkpi_kol_llm_deep_analysis_results"},
        evidence_columns={"updated_at"},
    )
    conn = _RouteConn(_full_routes())
    response = handlers.kol_pool_overview(conn, _request(locale="en-US"), None, now=_NOW)
    assert response["answer"] == (
        "There are 120 canonical KOLs; 45 have active video evidence; "
        "12 have ready deep analysis."
    )
    assert "MAX(e.updated_at)" in conn.calls[1][0]
    assert response["evidence"][0]["title"] == "KOL Pool canonical aggregate"


def test_kol_pool_overview_missing_side_tables_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pool_env(
        monkeypatch,
        pool_columns={"id"},
        tables_present=set(),
    )
    conn = _RouteConn(
        {"FROM vkpi_kol_pool p": {"total_kols": 3, "duplicate_rows": 0, "data_updated_at": None}}
    )
    response = handlers.kol_pool_overview(conn, _request(), None, now=_NOW)
    assert response["status"] == "partial"
    assert response["answer"] == "当前原始 KOL 记录共 3 条；主从去重口径不可用。"
    assert [f["key"] for f in response["facts"]] == ["kol.raw_records"]
    assert response["facts"][0]["confidence"] == "medium"
    assert [m["field"] for m in response["missing_fields"]] == [
        "video_evidence",
        "deep_analysis",
    ]
    assert response["coverage"]["status"] == "partial"
    assert response["coverage"]["evidence_count"] == 0
    assert response["coverage"]["ratio"] is None
    assert response["coverage"]["notes"] == [
        "当前仅能提供原始记录数；主从去重口径不可用。"
    ]
    assert len(response["evidence"]) == 1
    assert response["evidence"][0]["confidence"] == "medium"
    assert response["evidence"][0]["observed_at"] is None
    # 无 duplicate 列 → 池 SQL 用原始 COUNT 且不带 MAX(updated_at)。
    pool_sql, _ = conn.calls[0]
    assert "0 AS duplicate_rows" in pool_sql
    assert "NULL AS data_updated_at" in pool_sql


def test_kol_pool_overview_zero_rows_is_empty_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pool_env(
        monkeypatch,
        pool_columns={"id", "duplicate_of_id"},
        tables_present={"vkpi_kol_video_evidence"},
        evidence_columns=set(),
    )
    conn = _RouteConn(
        {
            "FROM vkpi_kol_pool p": {"total_kols": 0, "duplicate_rows": 0, "data_updated_at": None},
            "FROM vkpi_kol_video_evidence e": {
                "video_kols": 0,
                "video_rows": 0,
                "data_updated_at": None,
            },
        }
    )
    response = handlers.kol_pool_overview(conn, _request(), None, now=_NOW)
    # deep 表缺席 → missing → partial 优先于 empty。
    assert response["status"] == "partial"
    assert response["coverage"]["ratio"] is None

    _patch_pool_env(
        monkeypatch,
        pool_columns={"id", "duplicate_of_id"},
        tables_present={"vkpi_kol_video_evidence", "vkpi_kol_llm_deep_analysis_results"},
        evidence_columns=set(),
    )
    conn2 = _RouteConn(
        {
            "FROM vkpi_kol_pool p": {"total_kols": 0, "duplicate_rows": 0, "data_updated_at": None},
            "FROM vkpi_kol_video_evidence e": {
                "video_kols": 0,
                "video_rows": 0,
                "data_updated_at": None,
            },
            "FROM vkpi_kol_llm_deep_analysis_results d": {"deep_kols": 0},
        }
    )
    response2 = handlers.kol_pool_overview(conn2, _request(), None, now=_NOW)
    assert response2["status"] == "empty"
    # merged_duplicates fact 在无 platform/country 过滤且 auto scope 下出现。
    assert "kol.merged_duplicates" in [f["key"] for f in response2["facts"]]


def test_kol_pool_overview_platform_filter_suppresses_duplicate_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pool_env(
        monkeypatch,
        pool_columns={"id", "duplicate_of_id"},
        tables_present={"vkpi_kol_video_evidence", "vkpi_kol_llm_deep_analysis_results"},
        evidence_columns=set(),
    )
    conn = _RouteConn(_full_routes())
    response = handlers.kol_pool_overview(
        conn, _request(filters={"platform": "youtube"}), None, now=_NOW
    )
    assert "kol.merged_duplicates" not in [f["key"] for f in response["facts"]]


def test_policy_all_gates_pass_selects_models(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_policy_env(monkeypatch)
    decision = model_policy.evaluate_report_model_policy(_ready_payload(), _good_sources())
    assert decision.mode == ADVANCED_MODEL_MODE
    assert decision.provider_calls_allowed is True
    assert decision.claim_level == "validated_analysis"
    assert decision.primary_model == REPORT_PRIMARY_MODEL
    assert decision.challenger_model == REPORT_CHALLENGER_MODEL
    assert decision.judge_candidates == (REPORT_CHALLENGER_MODEL,)
    assert decision.selected_models == (REPORT_PRIMARY_MODEL, REPORT_CHALLENGER_MODEL)
    assert decision.blockers == ()
    checks = decision.checks
    assert checks["evaluation_order"] == [
        "data_readiness",
        "source_provenance",
        "sample_thresholds",
        "model_registry",
        "model_runtime",
        "model_evaluation",
    ]
    assert checks["data_readiness"] == {
        "passed": True,
        "status": "ready",
        "ready": True,
        "claimable": True,
        "claim_level": "validated",
        "blockers": [],
    }
    assert checks["sources"]["passed"] is True
    assert checks["sources"]["required_count"] == 2
    assert [item["key"] for item in checks["sources"]["items"]] == ["weekly_metrics", "kol_rows"]
    assert checks["sources"]["items"][0] == {
        "key": "weekly_metrics",
        "label": "weekly_metrics",
        "status": "ready",
        "data_status": "real",
        "source_count": 3,
        "source_ready": True,
        "observed": 12,
        "minimum": 10,
        "sample_ready": True,
    }
    assert checks["model_registry"] == {
        "passed": True,
        "items": {REPORT_PRIMARY_MODEL: True, REPORT_CHALLENGER_MODEL: True},
    }
    runtime = checks["model_runtime"]
    assert runtime["passed"] is True
    assert runtime["probe_authorized"] is False
    assert runtime["probe_ready"] is True
    assert runtime["evidence_source"] == {"source": "environment", "parsed": True}
    item = runtime["items"][REPORT_PRIMARY_MODEL]
    assert item["binding"] == REPORT_PRIMARY_MODEL
    assert "runtime_availability" not in item
    assert "runtime_evidence_source" not in item
    assert item["legacy_runtime_execution_gate"] == "operator_allowlisted"
    assert item["legacy_runtime_availability_is_production_evidence"] is False
    assert item["gate_reason"] == "ready"
    assert item["passed"] is True
    assert item["runtime_probe_ready"] is True
    assert item["production_ready"] is True

    payload = decision.to_dict()
    assert payload["version"] == "report_model_policy_v3"
    assert payload["deterministic_only"] is False
    assert payload["high_order_models_allowed"] is True
    assert [c["role"] for c in payload["candidates"]] == [
        "primary",
        "challenger_and_judge_candidate",
    ]


def test_policy_readiness_not_ready_blocks_with_specific_blockers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_policy_env(monkeypatch)
    payload = {
        "status": "collecting",
        "ready": False,
        "claimable": False,
        "blockers": ["rows_missing", ""],
    }
    decision = model_policy.evaluate_report_model_policy(payload, _good_sources())
    assert decision.mode == DETERMINISTIC_DESCRIPTIVE_MODE
    assert decision.provider_calls_allowed is False
    assert decision.claim_level == "descriptive_only"
    assert decision.primary_model is None
    assert decision.challenger_model is None
    assert decision.judge_candidates == ()
    assert decision.selected_models == ()
    assert decision.blockers == ("data_readiness:rows_missing",)
    assert decision.checks["data_readiness"]["blockers"] == ["rows_missing"]
