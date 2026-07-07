"""Cut 1 — pluggable keyframe strategy in services/media/frames.py.

The extractor now prefers PySceneDetect scene-cut keyframes (one frame per shot =
unique content, fewer vision tokens) and degrades to the proven ffmpeg blind
sampling when scenedetect is unavailable or detection yields nothing. These tests
inject every subprocess/scenedetect boundary — nothing touches ffmpeg or the wire.
"""
from __future__ import annotations

import unittest
from unittest import mock

from app.services.media import frames


class _FakeTimecode:
    def __init__(self, seconds: float) -> None:
        self._seconds = seconds

    def get_seconds(self) -> float:
        return self._seconds


class SceneTimestampDetectionTests(unittest.TestCase):
    def test_returns_none_when_pyscenedetect_unavailable(self):
        with mock.patch.object(frames, "PYSCENEDETECT_AVAILABLE", False):
            self.assertIsNone(frames._detect_scene_timestamps("v.mp4", 120.0))

    def test_returns_none_and_swallows_detector_error(self):
        def _boom(*_args, **_kwargs):
            raise RuntimeError("codec explode")

        with mock.patch.object(frames, "PYSCENEDETECT_AVAILABLE", True), \
                mock.patch.object(frames, "ContentDetector", lambda: object()), \
                mock.patch.object(frames, "scene_detect", _boom):
            self.assertIsNone(frames._detect_scene_timestamps("v.mp4", 120.0))

    def test_sorts_dedups_and_clamps_to_duration(self):
        scenes = [
            (_FakeTimecode(5.0), _FakeTimecode(8.0)),
            (_FakeTimecode(1.0), _FakeTimecode(4.0)),
            (_FakeTimecode(1.0), _FakeTimecode(2.0)),   # duplicate start
            (_FakeTimecode(500.0), _FakeTimecode(510.0)),  # beyond duration
        ]
        with mock.patch.object(frames, "PYSCENEDETECT_AVAILABLE", True), \
                mock.patch.object(frames, "ContentDetector", lambda: object()), \
                mock.patch.object(frames, "scene_detect", lambda *_a, **_k: scenes):
            result = frames._detect_scene_timestamps("v.mp4", 100.0)
        self.assertEqual(result, [1.0, 5.0, 100.0])

    def test_returns_none_when_no_scenes(self):
        with mock.patch.object(frames, "PYSCENEDETECT_AVAILABLE", True), \
                mock.patch.object(frames, "ContentDetector", lambda: object()), \
                mock.patch.object(frames, "scene_detect", lambda *_a, **_k: []):
            self.assertIsNone(frames._detect_scene_timestamps("v.mp4", 100.0))

    def test_caps_scene_count(self):
        scenes = [(_FakeTimecode(float(i)), _FakeTimecode(float(i) + 0.5)) for i in range(60)]
        with mock.patch.object(frames, "PYSCENEDETECT_AVAILABLE", True), \
                mock.patch.object(frames, "ContentDetector", lambda: object()), \
                mock.patch.object(frames, "scene_detect", lambda *_a, **_k: scenes):
            result = frames._detect_scene_timestamps("v.mp4", 1000.0)
        self.assertEqual(len(result), frames._MAX_SCENE_FRAMES)
        self.assertEqual(result, sorted(result))


class ExtractionStrategyRoutingTests(unittest.TestCase):
    def test_no_ffmpeg_returns_empty(self):
        with mock.patch.object(frames, "FFMPEG_AVAILABLE", False):
            self.assertEqual(frames.extract_video_frames_with_ts("v.mp4"), [])

    def test_scene_path_used_when_frames_extracted(self):
        scene_frames = [("b64a", 1.0), ("b64b", 5.0)]
        with mock.patch.object(frames, "FFMPEG_AVAILABLE", True), \
                mock.patch.object(frames, "_probe_duration", return_value=60.0), \
                mock.patch.object(frames, "_detect_scene_timestamps", return_value=[1.0, 5.0]), \
                mock.patch.object(frames, "_extract_frames_at_timestamps", return_value=scene_frames) as ext, \
                mock.patch.object(frames, "_extract_frames_blind") as blind:
            result = frames.extract_video_frames_with_ts("v.mp4")
        self.assertEqual(result, scene_frames)
        ext.assert_called_once()
        blind.assert_not_called()

    def test_falls_back_to_blind_when_no_scenes(self):
        blind_frames = [("blind0", 0), ("blind5", 5)]
        with mock.patch.object(frames, "FFMPEG_AVAILABLE", True), \
                mock.patch.object(frames, "_probe_duration", return_value=42.0), \
                mock.patch.object(frames, "_detect_scene_timestamps", return_value=None), \
                mock.patch.object(frames, "_extract_frames_blind", return_value=blind_frames) as blind:
            result = frames.extract_video_frames_with_ts("v.mp4")
        self.assertEqual(result, blind_frames)
        blind.assert_called_once_with("v.mp4", 42.0)

    def test_falls_back_to_blind_when_scene_extraction_empty(self):
        blind_frames = [("blind0", 0)]
        with mock.patch.object(frames, "FFMPEG_AVAILABLE", True), \
                mock.patch.object(frames, "_probe_duration", return_value=42.0), \
                mock.patch.object(frames, "_detect_scene_timestamps", return_value=[1.0]), \
                mock.patch.object(frames, "_extract_frames_at_timestamps", return_value=[]), \
                mock.patch.object(frames, "_extract_frames_blind", return_value=blind_frames) as blind:
            result = frames.extract_video_frames_with_ts("v.mp4")
        self.assertEqual(result, blind_frames)
        blind.assert_called_once()

    def test_extract_video_frames_strips_timestamps(self):
        with mock.patch.object(
            frames, "extract_video_frames_with_ts", return_value=[("a", 1), ("b", 2)]
        ):
            self.assertEqual(frames.extract_video_frames("v.mp4"), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
