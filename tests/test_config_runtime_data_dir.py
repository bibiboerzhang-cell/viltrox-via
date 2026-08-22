"""VKPI_RUNTIME_DATA_DIR:设了就把 uploads/frames/creator_profiles 放到绝对数据目录,不写 cwd(不可变发布树)。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
_PROBE = (
    "from app.core import config as c;"
    "print(c.UPLOAD_DIR.resolve()); print(c.FRAMES_DIR.resolve()); print(c.CREATOR_DIR.resolve())"
)


def _run(env_extra: dict[str, str], cwd: Path) -> list[str]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(BACKEND), "APP_ROLE": "admin-web", "ENABLE_SCHEDULER": "0", **env_extra}
    out = subprocess.run([sys.executable, "-B", "-c", _PROBE], cwd=cwd, env=env, capture_output=True, text=True, check=True)
    return [line.strip() for line in out.stdout.strip().splitlines()[-3:]]


def test_runtime_data_dir_redirects_all_three_dirs_and_leaves_cwd_clean(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    cwd = tmp_path / "release"
    cwd.mkdir()
    paths = _run({"VKPI_RUNTIME_DATA_DIR": str(data_root)}, cwd)
    assert paths == [str(data_root / "uploads"), str(data_root / "frames"), str(data_root / "creator_profiles")]
    for name in ("uploads", "frames", "creator_profiles"):
        assert (data_root / name).is_dir()
        assert not (cwd / name).exists()


def test_without_env_keeps_legacy_cwd_relative_behaviour(tmp_path: Path) -> None:
    cwd = tmp_path / "dev"
    cwd.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "VKPI_RUNTIME_DATA_DIR"}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(BACKEND), "APP_ROLE": "admin-web", "ENABLE_SCHEDULER": "0"})
    subprocess.run([sys.executable, "-B", "-c", _PROBE], cwd=cwd, env=env, capture_output=True, text=True, check=True)
    assert (cwd / "uploads").is_dir() and (cwd / "creator_profiles").is_dir()
