"""Compatibility facade for V-KPI channel services.

The implementation is split by responsibility to keep modules under the project
line limit. Existing callers import this module, so it re-exports the public and
legacy helper functions used by routers, scripts, and companion services.
"""
from __future__ import annotations

from app.services.vkpi.channels_common import *
from app.services.vkpi.channels_crud import *
from app.services.vkpi.channels_official import *
from app.services.vkpi.channels_posts import *
from app.services.vkpi.channels_evidence import *
from app.services.vkpi import channels_posts as _channels_posts


def _media_urls(*values):
    _channels_posts.cached_image_url = cached_image_url
    return _channels_posts._media_urls(*values)

__all__ = [name for name in globals() if not name.startswith('__')]
