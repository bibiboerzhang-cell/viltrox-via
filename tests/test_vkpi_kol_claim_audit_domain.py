from app.domains.kol import claim_audit


def test_kol_claim_audit_skips_missing_actor(monkeypatch):
    calls = []
    monkeypatch.setattr(claim_audit.audit, "log_business_event", lambda **kwargs: calls.append(kwargs))

    claim_audit.log_kol_audit(actor_staff_id=0, action_type="x", kol_id=1)

    assert calls == []


def test_kol_claim_audit_logs_business_event(monkeypatch):
    calls = []
    monkeypatch.setattr(claim_audit.audit, "log_business_event", lambda **kwargs: calls.append(kwargs))

    claim_audit.log_kol_audit(
        actor_staff_id=7,
        action_type="kol_claim_create",
        kol_id=9,
        detail="claim_id=2",
        metadata={"claim_id": 2},
    )

    assert calls == [
        {
            "staff_id": 7,
            "action_type": "kol_claim_create",
            "target_type": "kol",
            "target_id": 9,
            "detail": "claim_id=2",
            "metadata": {"claim_id": 2},
        }
    ]


def test_kol_claim_audit_swallow_failures(monkeypatch):
    def fail(**_kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr(claim_audit.audit, "log_business_event", fail)

    claim_audit.log_kol_audit(actor_staff_id=7, action_type="x", kol_id=1)
