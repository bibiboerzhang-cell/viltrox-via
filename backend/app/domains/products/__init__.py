"""Products domain facade."""

from app.domains.products import product_aliases, product_specs
from app.domains.products import product_fit_monitor

__all__ = ["product_aliases", "product_fit_monitor", "product_specs"]
