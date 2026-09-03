"""S-01 生产依赖安全地板(pip-audit 整改的回归锁)。

背景:2026-09 公测体检 pip-audit 报 13/123 生产依赖带已知 CVE。整改把
PyJWT / starlette(随 fastapi)/ python-multipart / aiohttp / Pillow 等抬到修复版。
本测试锁住三件事,防止后续「随手降版」把洞再开回来:

1. requirements.txt 的 pin 不得低于每个包的安全地板;
2. redis pin 不得回到 5.3.0(它声明 PyJWT~=2.9.0,与 PyJWT 2.13 互斥,
   会让 requirements.txt 整体 ResolutionImpossible);
3. requirements-ci.txt 只做「继承生产 pin + 额外精确 pin」,不许在 CI 里
   偷偷换掉生产版本。

安装态的校验(当前解释器里的版本)只在包已安装时执行,缺包 skip,不假红。
"""

from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"
REQUIREMENTS_CI = ROOT / "requirements-ci.txt"

# 包名(规范化小写)-> 最低安全版本。来源:pip-audit 修复版 + 本轮实际落地的 pin。
SECURITY_FLOOR: dict[str, str] = {
    "pyjwt": "2.13.0",  # CVE 修复线 ≥2.13.0
    "starlette": "1.3.1",  # ≥1.3.1(实际 pin 1.6.0,随 fastapi 0.136)
    "fastapi": "0.136.0",  # 与 starlette 1.x 兼容的 fastapi
    "python-multipart": "0.0.31",  # ≥0.0.31
    "aiohttp": "3.14.3",  # ≥3.14.3
    "pillow": "12.3.0",  # 最新安全版
    "redis": "5.3.1",  # 5.3.0 与 PyJWT 2.13 互斥
    "cryptography": "50.0.1",
    "urllib3": "2.7.0",
    "idna": "3.19",
    "sse-starlette": "3.4.8",
    "yt-dlp": "2026.7.4",  # PYSEC-2026-3622 修复线(实际 pin 2026.8.19)
    "weasyprint": "69.0",  # 68.1 带 CVE-2026-49452,69.0 已不在告警名单
}

_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]+\])?==([0-9][^\s;#]*)")


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _iter_requirement_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def _parse_pins(path: Path) -> dict[str, Version]:
    pins: dict[str, Version] = {}
    for line in _iter_requirement_lines(path):
        match = _PIN_RE.match(line)
        if match:
            pins[_canonical(match.group(1))] = Version(match.group(2))
    return pins


@pytest.fixture(scope="module")
def production_pins() -> dict[str, Version]:
    pins = _parse_pins(REQUIREMENTS)
    assert pins, "requirements.txt 解析不到任何 == pin"
    return pins


@pytest.mark.parametrize("package,floor", sorted(SECURITY_FLOOR.items()))
def test_production_pin_meets_security_floor(
    production_pins: dict[str, Version], package: str, floor: str
) -> None:
    assert package in production_pins, f"requirements.txt 缺少安全地板包 {package}"
    pinned = production_pins[package]
    assert pinned >= Version(floor), (
        f"{package}=={pinned} 低于安全地板 {floor}(pip-audit 已知 CVE)"
    )


def test_redis_pin_is_not_the_pyjwt_incompatible_release(
    production_pins: dict[str, Version],
) -> None:
    # redis 5.3.0 的元数据是 PyJWT~=2.9.0;与 PyJWT==2.13.0 一起会让 pip 解析失败。
    assert production_pins["redis"] != Version("5.3.0")
    assert production_pins["pyjwt"] >= Version("2.13.0")


def test_every_production_requirement_is_exactly_pinned() -> None:
    unpinned = [
        line for line in _iter_requirement_lines(REQUIREMENTS) if not _PIN_RE.match(line)
    ]
    assert unpinned == [], f"requirements.txt 存在非 == 精确 pin 的行:{unpinned}"


def test_ci_requirements_inherit_production_pins_without_override(
    production_pins: dict[str, Version],
) -> None:
    lines = _iter_requirement_lines(REQUIREMENTS_CI)
    assert lines and lines[0] == "-r requirements.txt", (
        "requirements-ci.txt 第一条有效行必须是 -r requirements.txt"
    )
    extras = _parse_pins(REQUIREMENTS_CI)
    overlapping = sorted(set(extras) & set(production_pins))
    assert overlapping == [], f"requirements-ci.txt 不得覆盖生产 pin:{overlapping}"
    non_pinned = [line for line in lines[1:] if not _PIN_RE.match(line)]
    assert non_pinned == [], f"requirements-ci.txt 额外依赖必须精确 pin:{non_pinned}"


def _installed_version(package: str) -> Version | None:
    try:
        return Version(metadata.version(package))
    except metadata.PackageNotFoundError:
        return None


@pytest.mark.parametrize("package,floor", sorted(SECURITY_FLOOR.items()))
def test_installed_version_meets_security_floor(package: str, floor: str) -> None:
    installed = _installed_version(package)
    if installed is None:
        pytest.skip(f"{package} 未安装在当前解释器")
    assert installed >= Version(floor), f"已安装 {package}=={installed} 低于地板 {floor}"


def test_installed_redis_accepts_installed_pyjwt() -> None:
    redis_version = _installed_version("redis")
    jwt_version = _installed_version("pyjwt")
    if redis_version is None or jwt_version is None:
        pytest.skip("redis / PyJWT 未同时安装")
    jwt_specs = [
        Requirement(spec)
        for spec in (metadata.requires("redis") or [])
        if _canonical(Requirement(spec).name) == "pyjwt"
    ]
    assert jwt_specs, "redis 元数据里找不到 PyJWT 依赖声明(版本假设已失效)"
    for requirement in jwt_specs:
        assert requirement.specifier.contains(jwt_version, prereleases=False), (
            f"redis=={redis_version} 要求 {requirement.specifier},"
            f"但已安装 PyJWT=={jwt_version}(pip check 会报错)"
        )
