"""A4 · 顶栏进度中心 / 思考流 聚合事件流(SSE)最小连通冒烟。

SSE 端点是无限 server-push 流,过 TestClient 拉真连接易挂;这里改为直接驱动端点返回
的 EventSourceResponse.body_iterator(我们的原始异步生成器,产出 {event,data} 帧),
用 asyncio.wait_for 只拉首帧即 aclose —— 确定性、不挂、打真 Postgres(纯读路径)。

断言:
  - 两条聚合流路由已挂载(ADMIN_ROUTER_MODULES 内 append-only);
  - 端点返回 200 text/event-stream;
  - 首帧 event=snapshot,data 是与轮询端点同源同投影的 JSON
    (progress=status/counts/running;activity=items/count)。
红线:零写库,不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


_ADMIN_STAFF = {
    "id": 1,
    "staff_id": 1,
    "user_id": 1,
    "role": "admin",
    "is_owner": 1,
    "permissions": {"vkpi": "admin"},
    "email": "admin@a4.test",
}


class _FakeRequest:
    """端点只用到 request.is_disconnected();保持连接(False)以拉首帧。"""

    async def is_disconnected(self) -> bool:
        return False


def _drive_first_frame(endpoint_coro_factory) -> dict:
    """同一 loop 内建端点响应 + 拉首帧,超时兜底防挂,拉完即关。"""

    async def _run():
        response = await endpoint_coro_factory()
        assert response.status_code == 200
        assert "text/event-stream" in str(response.media_type)
        it = response.body_iterator
        try:
            return await asyncio.wait_for(it.__anext__(), timeout=15)
        finally:
            await it.aclose()

    return asyncio.run(_run())


def test_aggregate_stream_routes_mounted():
    """注册表制挂载生效:两条聚合流路由存在于 app.routes。"""
    from app.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/admin/vkpi/progress/center/stream" in paths
    assert "/api/admin/vkpi/activity/stream" in paths


def test_progress_center_stream_first_frame():
    from app.api.routers.vkpi_progress_center import stream_progress_center

    frame = _drive_first_frame(
        lambda: stream_progress_center(_FakeRequest(), limit=20, recent_minutes=120, staff=_ADMIN_STAFF)
    )
    assert frame.get("event") == "snapshot"
    payload = json.loads(frame["data"])
    assert payload.get("status") == "ready"
    assert isinstance(payload.get("counts"), dict)
    assert isinstance(payload.get("running"), list)


def test_activity_stream_first_frame():
    from app.api.routers.vkpi_activity import stream_activity

    frame = _drive_first_frame(
        lambda: stream_activity(_FakeRequest(), limit=30, staff=_ADMIN_STAFF)
    )
    assert frame.get("event") == "snapshot"
    payload = json.loads(frame["data"])
    assert isinstance(payload.get("items"), list)
    assert isinstance(payload.get("count"), int)
