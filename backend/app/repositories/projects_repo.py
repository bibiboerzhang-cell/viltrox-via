"""Projects 仓库(L2)—— vkpi_projects 常用取数。

红线:只封装取数/写数;零业务裁决,零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from app.repositories.base import BaseRepository


class ProjectsRepository(BaseRepository):
    table = "vkpi_projects"

    def get_by_id(self, project_id: int) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM vkpi_projects WHERE id = ?", (int(project_id),))

    def get_sample_fields(self, project_id: int) -> dict[str, Any] | None:
        """寄样常用字段(kol_id/product_sku/product_name)。"""
        return self.fetch_one(
            "SELECT kol_id, product_sku, product_name FROM vkpi_projects WHERE id = ?",
            (int(project_id),),
        )
