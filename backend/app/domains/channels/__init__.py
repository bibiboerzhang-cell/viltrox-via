"""Compatibility facade for V-KPI channel services.

The implementation is split by responsibility to keep modules under the project
line limit. Existing callers import this module, so it re-exports the public and
legacy helper functions used by routers, scripts, and companion services.
"""
from __future__ import annotations

from app.domains.channels.common import *
from app.domains.channels.crud import *
from app.domains.channels.official import *
from app.domains.channels.posts import *
from app.domains.channels.post_metrics import *
from app.domains.channels.refill import *
from app.domains.channels.evidence import *
from app.domains.channels import posts as _channels_posts


def _media_urls(*values):
    _channels_posts.cached_image_url = cached_image_url
    return _channels_posts._media_urls(*values)

__all__ = [name for name in globals() if not name.startswith('__')]
