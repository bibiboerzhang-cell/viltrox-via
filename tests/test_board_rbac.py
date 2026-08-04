# -*- coding: utf-8 -*-
"""board.* 板块可见性闸(2026-07-18 权限双洞修)单测。

覆盖:缺键放行 / 显式 none 拦截 / OR 语义 / owner 豁免 / inactive 拒绝 /
旧 V2 占位死键不闸 / 路径映射(独占前缀、共享前缀、未命中)/ board-series 参数映射。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.permissions import check_board_permission  # noqa: E402
from app.main_request_guards import (  # noqa: E402
    board_requirement_for_board_series,
    board_requirement_for_request,
)


def _staff(perms: dict | None = None, *, owner: bool = False, active: bool = True) -> dict:
    return {
        "id": 42,
        "role": "employee",
        "is_owner": 1 if owner else 0,
        "active": 1 if active else 0,
        "email": "member@viltrox.com",
        "permissions_json": json.dumps(perms or {}),
    }


def test_missing_board_keys_allow():
    """向后兼容铁律:无 board.* 键的员工(20/21 现状)不能被误锁。"""
    assert check_board_permission(_staff({}), frozenset({"dealers"})) is True


def test_explicit_none_blocks():
    staff = _staff({"board.dealers": "none"})
    assert check_board_permission(staff, frozenset({"dealers"})) is False


def test_read_allows():
    staff = _staff({"board.dealers": "read"})
    assert check_board_permission(staff, frozenset({"dealers"})) is True


def test_or_semantics_any_visible_board_allows():
    """共享前缀 OR:藏了 A 板块但 B 板块可见 → 放行。"""
    staff = _staff({"board.kol-pool": "none"})
    assert check_board_permission(staff, frozenset({"kol-pool", "kolProfile"})) is True
    staff2 = _staff({"board.kol-pool": "none", "board.kolProfile": "none"})
    assert check_board_permission(staff2, frozenset({"kol-pool", "kolProfile"})) is False


def test_owner_exempt():
    staff = _staff({"board.dealers": "none"}, owner=True)
    assert check_board_permission(staff, frozenset({"dealers"})) is True


def test_inactive_denied():
    staff = _staff({}, active=False)
    assert check_board_permission(staff, frozenset({"dashboard"})) is False


def test_empty_nav_keys_allow():
    assert check_board_permission(_staff({}), frozenset()) is True


def test_route_mapping_exclusive_prefixes():
    assert board_requirement_for_request("/api/admin/vkpi/dealers/locations") == frozenset({"dealers"})
    assert board_requirement_for_request("/api/admin/vkpi/event-radar/opp_1") == frozenset({"events"})
    assert board_requirement_for_request("/api/admin/vkpi/dashboard") == frozenset({"dashboard"})
    assert board_requirement_for_request("/api/admin/vkpi/kol-search-sessions/12/approve") == frozenset({"kol-pool"})


def test_route_mapping_shared_prefixes_or():
    assert board_requirement_for_request("/api/admin/vkpi/kol-pool/123") == frozenset({"kol-pool", "kolProfile"})
    assert board_requirement_for_request("/api/admin/vkpi/projects/9/kols") == frozenset({"projects", "launchpad"})
    assert board_requirement_for_request("/api/admin/vkpi/actions/inbox") == frozenset({"autonomy", "gtmCommand"})


def test_route_mapping_unmatched_returns_none():
    # market/trends 属 ops 导航,不在 board 注册表——绝不闸。
    assert board_requirement_for_request("/api/admin/vkpi/market/trends") is None
    assert board_requirement_for_request("/api/marketing/projects") is None
    assert board_requirement_for_request("/api/admin/vkpi/media/image-proxy") is None


def test_board_series_param_mapping():
    assert board_requirement_for_board_series("dealers") == frozenset({"dealers"})
    # 旧 V2 占位键(board.analytics 等 8 个死键)不在注册表 → 不闸。
    assert board_requirement_for_board_series("analytics") is None
    assert board_requirement_for_board_series("") is None
    assert board_requirement_for_board_series(None) is None
