from app.domains.kol import claim_payloads


def test_kol_claim_payloads_serialize_json_shapes():
    assert claim_payloads.json_object({"a": 1}) == '{"a": 1}'
    assert claim_payloads.json_object(None) == "{}"
    assert claim_payloads.json_array([{"a": 1}]) == '[{"a": 1}]'
    assert claim_payloads.json_array({"not": "list"}) == "[]"


def test_kol_claim_payload_marks_active_state():
    assert claim_payloads.claim_payload(None) == {}
    assert claim_payloads.claim_payload({"id": 1, "status": "active"}) == {"id": 1, "status": "active", "is_active": True}
    assert claim_payloads.claim_payload({"id": 2, "status": "released"}) == {"id": 2, "status": "released", "is_active": False}
