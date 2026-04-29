from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request


ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.routers import media  # noqa: E402


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


class MediaAccessTests(unittest.TestCase):
    def test_confirmed_submission_is_public(self) -> None:
        self.assertTrue(media._submission_is_public({"detection_status": "confirmed"}))
        self.assertFalse(media._submission_is_public({"detection_status": "pending"}))

    def test_owner_can_access_submission_media(self) -> None:
        row = {"id": 11, "user_id": 7, "detection_status": "pending"}
        with patch.object(media, "_load_submission_media_row", return_value=row), patch.object(
            media, "get_current_user", return_value={"id": 7, "role": "creator"}
        ):
            resolved = media._require_submission_media_access(_request(), 11)
        self.assertEqual(resolved, row)

    def test_admin_can_access_private_submission_media(self) -> None:
        row = {"id": 12, "user_id": 9, "detection_status": "pending"}
        with patch.object(media, "_load_submission_media_row", return_value=row), patch.object(
            media, "get_current_user", return_value={"id": 1, "role": "admin"}
        ):
            resolved = media._require_submission_media_access(_request(), 12)
        self.assertEqual(resolved, row)

    def test_public_confirmed_submission_allows_anonymous_access(self) -> None:
        row = {"id": 13, "user_id": 99, "detection_status": "confirmed"}
        with patch.object(media, "_load_submission_media_row", return_value=row), patch.object(
            media, "get_current_user", return_value=None
        ):
            resolved = media._require_submission_media_access(_request(), 13)
        self.assertEqual(resolved, row)

    def test_anonymous_private_submission_is_hidden(self) -> None:
        row = {"id": 14, "user_id": 99, "detection_status": "pending"}
        with patch.object(media, "_load_submission_media_row", return_value=row), patch.object(
            media, "get_current_user", return_value=None
        ):
            with self.assertRaises(HTTPException) as ctx:
                media._require_submission_media_access(_request(), 14)
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
