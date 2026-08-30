#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from stdout_utils import out, out_json


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_env import apply_runtime_env  # noqa: E402

apply_runtime_env()

from smoke_auth_social_student import BASE_URL, assert_ok, create_student_session, http_json  # noqa: E402
from app.db.connection import close_db_runtime, get_conn  # noqa: E402
from app.db.startup import init_db_runtime  # noqa: E402


def _sample_video_path() -> Path:
    override = os.getenv("SMOKE_VIDEO_PATH", "").strip()
    if override:
        path = Path(override).expanduser().resolve()
        if path.exists():
            return path
    candidates = [
        ROOT.parent / "viltrox-test" / "uploads" / "vid_17709e1adf.mp4",
        ROOT.parent / "viltrox-test" / "uploads" / "vid_bf873dfc36.mov",
        ROOT.parent / "viltrox-test" / "uploads" / "vid_f7bce8c01f.mov",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No smoke video sample found; set SMOKE_VIDEO_PATH to a local mp4/mov file")


def _materialize_smoke_variant(source: Path) -> Path:
    tmp_dir = ROOT / "runtime" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix or ".mp4"
    variant = tmp_dir / f"smoke-{uuid.uuid4().hex}{suffix}"
    data = source.read_bytes()
    variant.write_bytes(data + f"\nSMOKE_VARIANT:{uuid.uuid4().hex}\n".encode("utf-8"))
    return variant


def _uploaded_video_from_duplicate(sample_video: Path, payload: dict) -> dict:
    duplicate = dict(payload.get("duplicate") or {})
    storage_key = str(duplicate.get("matched_storage_key") or "").strip()
    if not storage_key:
        raise RuntimeError(f"duplicate payload missing storage key: {payload}")
    existing_path = ROOT / storage_key
    if not existing_path.exists():
        raise RuntimeError(f"duplicate payload points to missing file: {existing_path}")
    return {
        "status": "success",
        "video_id": existing_path.stem,
        "filename": sample_video.name,
        "mime_type": mimetypes.guess_type(sample_video.name)[0] or "video/mp4",
        "size_mb": round(existing_path.stat().st_size / (1024 * 1024), 2),
        "r2_key": "",
        "asset_id": int(duplicate.get("matched_asset_id") or 0),
        "checksum": "",
        "frame_hash_count": 0,
        "title": "Smoke upload sample",
        "notes": "Reused duplicate-safe asset for 2.0 upload/video-factory smoke run",
    }


def _latest_uploaded_video_payload(*, fallback_sample: Path, title: str, notes: str) -> dict:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT id, storage_key, mime_type, size_bytes
        FROM submission_assets
        WHERE asset_role IN ('uploaded_video', 'uploaded_video_pending')
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        raise RuntimeError("upload rate limited and no existing uploaded asset found for fallback")
    storage_key = str(row["storage_key"] or "").strip()
    existing_path = Path(storage_key)
    if not existing_path.is_absolute():
        existing_path = ROOT / storage_key
    if not existing_path.exists():
        raise RuntimeError(f"fallback uploaded asset is missing on disk: {existing_path}")
    return {
        "status": "success",
        "video_id": existing_path.stem,
        "filename": fallback_sample.name,
        "mime_type": str(row["mime_type"] or mimetypes.guess_type(fallback_sample.name)[0] or "video/mp4"),
        "size_mb": round(int(row["size_bytes"] or existing_path.stat().st_size) / (1024 * 1024), 2),
        "r2_key": "",
        "asset_id": int(row["id"] or 0),
        "checksum": "",
        "frame_hash_count": 0,
        "title": title,
        "notes": notes,
    }


def http_multipart(path: str, *, token: str, fields: dict[str, str], file_field: str, file_path: Path) -> dict:
    boundary = f"----Viltrox2Smoke{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    file_bytes = file_path.read_bytes()
    parts.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode("utf-8"),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    body = b"".join(parts)
    request = Request(
        f"{BASE_URL}{path}",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", "ignore")
        try:
            data = json.loads(raw)
        except Exception:
            data = {"status": "error", "detail": raw or exc.reason}
        data["_http_status"] = exc.code
        return data


def poll_submission_status(submission_id: int, *, timeout_sec: int = 150) -> dict:
    deadline = time.time() + timeout_sec
    last_payload: dict = {}
    while time.time() < deadline:
        payload = http_json("GET", f"/api/submissions/{submission_id}/status")
        last_payload = payload
        if str(payload.get("job_status") or "") in {"done", "failed"}:
            return payload
        time.sleep(2)
    raise TimeoutError(f"submission {submission_id} did not reach terminal state: {last_payload}")


def main() -> int:
    asyncio.run(init_db_runtime())
    try:
        stamp = int(time.time())
        session = create_student_session(stamp=stamp)
        token = session["token"]
        sample_video = _materialize_smoke_variant(_sample_video_path())
        smoke_title = f"Smoke upload sample {stamp}"
        smoke_notes = f"2.0 upload/video-factory smoke run {stamp}"

        out(f"1) upload sample video: {sample_video.name}")
        upload_payload = http_multipart(
            "/api/upload/video",
            token=token,
            fields={
                "title": smoke_title,
                "notes": smoke_notes,
            },
            file_field="file",
            file_path=sample_video,
        )
        if int(upload_payload.get("_http_status") or 0) == 429:
            upload_payload = _latest_uploaded_video_payload(
                fallback_sample=sample_video,
                title=smoke_title,
                notes=smoke_notes,
            )
        if str(upload_payload.get("status") or "") == "rejected" and (upload_payload.get("duplicate") or {}).get("duplicate"):
            upload_payload = _uploaded_video_from_duplicate(sample_video, upload_payload)
        assert_ok("upload video", upload_payload)

        out("2) queue async audit job against uploaded video")
        audit_payload = http_json(
            "POST",
            "/api/audit/v2",
            token=token,
            payload={
                "title": smoke_title,
                "caption": f"Testing the 2.0 upload -> audit -> worker path {stamp}",
                "raw_text": f"Smoke upload sample for queue-native audit {stamp}",
                "linked_handles": {"instagram": "@smoke_student"},
                "uploaded_video": {
                    "video_id": upload_payload["video_id"],
                    "asset_id": upload_payload.get("asset_id", 0),
                    "filename": upload_payload["filename"],
                    "mime_type": upload_payload["mime_type"],
                    "size_mb": upload_payload["size_mb"],
                    "r2_key": upload_payload.get("r2_key", ""),
                },
            },
        )
        if str(audit_payload.get("status") or "") != "queued":
            raise RuntimeError(f"audit enqueue failed: {audit_payload}")
        submission_id = int(audit_payload["submission_id"])

        out("3) wait for worker-driven audit result")
        submission_status = poll_submission_status(submission_id)
        if str(submission_status.get("job_status") or "") != "done":
            raise RuntimeError(f"audit job did not finish cleanly: {submission_status}")

        summary = {
            "upload_status": upload_payload["status"],
            "job_id": audit_payload["job_id"],
            "submission_id": submission_id,
            "job_status": submission_status.get("job_status"),
            "detection_status": submission_status.get("detection_status"),
            "final_score": submission_status.get("final_score"),
            "overall_score": submission_status.get("overall_score"),
        }
        out_json(summary, ensure_ascii=False, indent=2)
        return 0
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
