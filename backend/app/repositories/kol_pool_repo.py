"""KOL Pool 仓库(L2 试点)—— vkpi_kol_pool 常用取数/写数集中。

红线:只封装取数/写数;不做评分/裁决,零触 viltrox_fit_score。
"""
from __future__ import annotations

import hashlib
from typing import Any

from app.repositories.base import BaseRepository


class KolPoolRepository(BaseRepository):
    table = "vkpi_kol_pool"

    def get_by_id(self, kol_pool_id: int) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM vkpi_kol_pool WHERE id = ?", (int(kol_pool_id),))

    def find_id_by_platform_handle(self, platform: str, handle: str) -> int | None:
        row = self.fetch_one(
            "SELECT id FROM vkpi_kol_pool WHERE platform = ? AND handle = ? LIMIT 1",
            (str(platform), str(handle)),
        )
        return int(row["id"]) if row else None

    @staticmethod
    def discovered_uid(platform: str, handle: str) -> str:
        return "disc_" + hashlib.sha1(f"{platform}:{handle}".encode("utf-8")).hexdigest()[:16]

    def insert_discovered(self, *, platform: str, handle: str, name: str = "",
                          profile_url: str = "", source_ref: str = "federation") -> int | None:
        """落一条 discovered 新档(数据薄诚实)。返回 id。调用方负责去重(find_id_by_platform_handle)。"""
        row = self.fetch_one(
            "INSERT INTO vkpi_kol_pool (pool_uid, platform, handle, display_name, profile_url, source_type, source_ref) "
            "VALUES (?,?,?,?,?,?,?) RETURNING id",
            (self.discovered_uid(platform, handle), str(platform), str(handle),
             str(name)[:200], str(profile_url)[:500], "discovered", str(source_ref)[:80]),
        )
        return int(row["id"]) if row else None
