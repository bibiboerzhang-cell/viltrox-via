from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path(os.getenv("RUNTIME_ROOT", str(ROOT / "runtime")))
RUNTIME_VENDOR = Path(os.getenv("RUNTIME_VENDOR", str(RUNTIME_ROOT / "vendor")))
RUNTIME_DATA = Path(os.getenv("RUNTIME_DATA", str(RUNTIME_ROOT / "data")))
RUNTIME_LOGS = Path(os.getenv("RUNTIME_LOGS", str(RUNTIME_ROOT / "logs")))
LEGACY_TOOLS_BIN = Path(os.getenv("LEGACY_TOOLS_BIN", str(ROOT.parent / "viltrox-test" / "_tools" / "bin")))
INSECURE_LOCAL_JWT_SECRET = "viltrox2-local-dev-secret-change-me"

POSTGRES_PORT = os.getenv("POSTGRES_PORT", "54329")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_DB = os.getenv("POSTGRES_DB", "viltrox2")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")

REDIS_PORT = os.getenv("REDIS_PORT", "6380")
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")


def _load_local_env() -> None:
    env_path = Path(os.getenv("LOCAL_ENV_FILE", str(ROOT / ".env")))
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        environment = os.environ.get("ENVIRONMENT", "local").strip().lower()
        if (
            key == "JWT_SECRET"
            and environment == "local"
            and os.environ.get("RUNTIME_ENV_KEEP_INHERITED_JWT", "0") != "1"
        ):
            os.environ[key] = value
        elif (
            key == "JWT_SECRET"
            and os.environ.get("JWT_SECRET") == INSECURE_LOCAL_JWT_SECRET
            and value != INSECURE_LOCAL_JWT_SECRET
        ):
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


def apply_runtime_env() -> None:
    _load_local_env()
    if LEGACY_TOOLS_BIN.exists():
        current_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{LEGACY_TOOLS_BIN}{os.pathsep}{current_path}" if current_path else str(LEGACY_TOOLS_BIN)
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    RUNTIME_DATA.mkdir(parents=True, exist_ok=True)
    RUNTIME_LOGS.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("ENVIRONMENT", "production")
    os.environ.setdefault("V2_PRODUCTION_MODE", "1")
    os.environ.setdefault("APP_ROLE", "worker")
    os.environ.setdefault("DB_RUNTIME_BACKEND", "postgres")
    os.environ.setdefault("DATABASE_URL", f"postgresql://{POSTGRES_USER}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}")
    os.environ.setdefault("REDIS_URL", f"redis://{REDIS_HOST}:{REDIS_PORT}/0")
    os.environ.setdefault("APP_STACK_NAME", "viltrox-2.0")
    os.environ.setdefault("REDIS_NAMESPACE", "viltrox-2.0:runtime")
    os.environ.setdefault("JWT_SECRET", INSECURE_LOCAL_JWT_SECRET)
    os.environ.setdefault("ADMIN_PASSWORD", "AdminPass123!")
