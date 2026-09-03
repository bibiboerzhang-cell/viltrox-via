"""S-07:旧 kol_ops 列表/详情读端联系方式脱敏;不再按 contact_email 模糊搜索。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

SOURCE = (BACKEND_ROOT / "app" / "api" / "routers" / "kol_ops.py").read_text(encoding="utf-8")


def test_mask_helper_redacts_email_and_phone_only():
    from app.api.routers.kol_ops import _mask_kol_contacts

    item = _mask_kol_contacts(
        {"id": 1, "contact_email": "john.doe@example.com", "contact_phone": "+1 555 0100", "channel_name": "JD"}
    )
    assert item["contact_email"] == "j***@e***"
    assert "john" not in item["contact_email"] and "example" not in item["contact_email"]
    assert item["contact_phone"] == "+***0"
    assert item["channel_name"] == "JD" and item["id"] == 1
    # 空值原样(诚实空态不许变成 ***)
    assert _mask_kol_contacts({"contact_email": "", "contact_phone": None}) == {"contact_email": "", "contact_phone": None}


def test_list_and_detail_project_through_mask():
    list_fn = SOURCE[SOURCE.index("def list_kols("):SOURCE.index("async def _execute_platform_search(")]
    detail_fn = SOURCE[SOURCE.index("def get_kol("):SOURCE.index('@router.post("/kols/{kol_id}/scan-account")')]
    assert "item = _mask_kol_contacts(dict(row))" in list_fn
    assert '"kol": _mask_kol_contacts(dict(row))' in detail_fn
    assert "SELECT k.*" in list_fn or "k.*," in list_fn  # 仍是 k.* 投影,脱敏发生在 Python 读端


def test_list_search_no_longer_matches_contact_email():
    list_fn = SOURCE[SOURCE.index("def list_kols("):SOURCE.index("async def _execute_platform_search(")]
    assert "LOWER(k.contact_email) LIKE" not in list_fn
    like_count = len(re.findall(r"LIKE \?", list_fn.split("if q:")[1].split("if date_from")[0]))
    assert like_count == 7
    assert "params.extend([_like(q)] * 7)" in list_fn
