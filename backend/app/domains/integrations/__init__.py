"""V-KPI Integrations domain — 第三方平台接入骨架(creds 加密存 + 薄 REST client)。

当前成员:
- goaffpro_connect:GOAFFPRO Affiliate 接入骨架(D1)。无 key 也能建,creds 加密落库
  vkpi_goaffpro_credentials / 回退 env;REST client 字段映射「待 key 校准」。

与 KOL 评分域物理隔离:无 viltrox_fit_score / rule_v0 触点。
"""
from app.domains.integrations import goaffpro_connect  # noqa: F401

__all__ = ["goaffpro_connect"]
