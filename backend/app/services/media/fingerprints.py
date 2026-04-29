"""
services/media/fingerprints.py — lightweight upload fingerprinting
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

try:
    import cv2
except Exception:
    cv2 = None


def compute_file_sha256(path: str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _frame_phash(frame: np.ndarray, hash_size: int = 8, highfreq_factor: int = 4) -> str:
    if cv2 is None:
        return ""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    size = hash_size * highfreq_factor
    resized = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(np.float32(resized))
    low_freq = dct[:hash_size, :hash_size]
    median = float(np.median(low_freq[1:, :])) if low_freq.size > 1 else float(low_freq[0, 0])
    bits = (low_freq > median).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bool(bit))
    return f"{value:016x}"


def _sample_frame_positions(total_frames: int, sample_count: int = 3) -> list[int]:
    if total_frames <= 1:
        return [0]
    anchors = [0.15, 0.5, 0.85]
    positions = {
        max(0, min(total_frames - 1, int(round((total_frames - 1) * ratio))))
        for ratio in anchors[: max(1, sample_count)]
    }
    return sorted(positions)


def probe_video_fingerprints(path: str, sample_count: int = 3) -> dict:
    file_path = Path(path)
    result = {
        "available": False,
        "file_sha256": compute_file_sha256(str(file_path)),
        "duration_ms": 0,
        "width": 0,
        "height": 0,
        "frame_hashes": [],
    }
    if cv2 is None or not file_path.exists():
        return result

    cap = cv2.VideoCapture(str(file_path))
    if not cap.isOpened():
        return result

    try:
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration_ms = int((total_frames / fps) * 1000) if total_frames > 0 and fps > 0 else 0
        frame_hashes: list[dict] = []
        slot_names = ["early", "mid", "late", "tail"]

        for slot_index, frame_index in enumerate(_sample_frame_positions(total_frames, sample_count=sample_count)):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            phash = _frame_phash(frame)
            if not phash:
                continue
            frame_hashes.append(
                {
                    "fingerprint_type": "phash",
                    "frame_slot": slot_names[min(slot_index, len(slot_names) - 1)],
                    "frame_index": int(frame_index),
                    "fingerprint_value": phash,
                }
            )

        result.update(
            {
                "available": bool(frame_hashes),
                "duration_ms": duration_ms,
                "width": width,
                "height": height,
                "frame_hashes": frame_hashes,
            }
        )
        return result
    finally:
        cap.release()
