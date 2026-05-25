"""Backwards-compatible X comments go/no-go shim."""
from __future__ import annotations

import sys

from app.domains.market import x_comments_go_no_go as _x_comments_go_no_go

sys.modules[__name__] = _x_comments_go_no_go
