"""Events 仓库(L2)—— vkpi_events 常用取数/写数。event id 为 TEXT。

红线:只封装取数/写数;零业务裁决,零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from app.repositories.base import BaseRepository


class EventsRepository(BaseRepository):
    table = "vkpi_events"

    def get_by_id(self, event_id: str) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM vkpi_events WHERE id = ?", (str(event_id),))

    def get_location_fields(self, event_id: str) -> dict[str, Any] | None:
        return self.fetch_one(
            "SELECT location_name, location_city, location_country FROM vkpi_events WHERE id = ?",
            (str(event_id),),
        )

    def update_location(self, event_id: str, lat: float, lng: float) -> None:
        self.execute(
            "UPDATE vkpi_events SET location_lat = ?, location_lng = ?, updated_at = NOW() WHERE id = ?",
            (lat, lng, str(event_id)),
        )
