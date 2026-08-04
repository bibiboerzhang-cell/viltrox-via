from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path(os.getenv("RUNTIME_ROOT") or str(ROOT / "runtime"))
RUNTIME_VENDOR = Path(os.getenv("RUNTIME_VENDOR") or str(RUNTIME_ROOT / "vendor"))
RUNTIME_DATA = Path(os.getenv("RUNTIME_DATA") or str(RUNTIME_ROOT / "data"))
RUNTIME_LOGS = Path(os.getenv("RUNTIME_LOGS") or str(RUNTIME_ROOT / "logs"))
LEGACY_TOOLS_BIN = Path(os.getenv("LEGACY_TOOLS_BIN") or str(ROOT.parent / "viltrox-test" / "_tools" / "bin"))
INSECURE_LOCAL_JWT_SECRET = "viltrox2-local-dev-secret-change-me"
INSECURE_LOCAL_ADMIN_PASSWORD = "AdminPass123!"
PRODUCTION_ENVIRONMENTS = frozenset({"prod", "production", "stage", "staging"})

POSTGRES_PORT = os.getenv("POSTGRES_PORT") or "54329"
POSTGRES_USER = os.getenv("POSTGRES_USER") or "postgres"
POSTGRES_DB = os.getenv("POSTGRES_DB") or "viltrox2"
POSTGRES_HOST = os.getenv("POSTGRES_HOST") or "127.0.0.1"

REDIS_PORT = os.getenv("REDIS_PORT") or "6380"
REDIS_HOST = os.getenv("REDIS_HOST") or "127.0.0.1"


def _local_database_url() -> str:
    return (
        os.environ.get("LOCAL_DATABASE_URL")
        or f"postgresql://{POSTGRES_USER}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
    )


def _local_redis_url() -> str:
    return os.environ.get("LOCAL_REDIS_URL") or f"redis://{REDIS_HOST}:{REDIS_PORT}/0"


def _set_default_if_blank(key: str, value: str) -> None:
    if not os.environ.get(key):
        os.environ[key] = value


def _clean_env_value(value: str) -> str:
    """Mirror runtime_env.sh's small dotenv quote normalizer."""
    cleaned = value.strip()
    if cleaned.endswith('"'):
        cleaned = cleaned[:-1]
    if cleaned.startswith('"'):
        cleaned = cleaned[1:]
    if cleaned.endswith("'"):
        cleaned = cleaned[:-1]
    if cleaned.startswith("'"):
        cleaned = cleaned[1:]
    return cleaned


def _load_env_file(env_path: Path, *, override: bool = False) -> None:
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
        value = _clean_env_value(value)
        environment = os.environ.get("ENVIRONMENT", "local").strip().lower()
        if override:
            os.environ[key] = value
        elif (
            key == "JWT_SECRET"
            and environment == "local"
            and os.environ.get("RUNTIME_ENV_KEEP_INHERITED_JWT", "0") != "1"
        ):
            os.environ[key] = value
        elif key == "JWT_SECRET" and not os.environ.get("JWT_SECRET") and value:
            os.environ[key] = value
        elif (
            key == "JWT_SECRET"
            and os.environ.get("JWT_SECRET") == INSECURE_LOCAL_JWT_SECRET
            and value != INSECURE_LOCAL_JWT_SECRET
        ):
            os.environ[key] = value
        elif key == "ADMIN_PASSWORD" and not os.environ.get("ADMIN_PASSWORD") and value:
            os.environ[key] = value
        elif (
            key == "ADMIN_PASSWORD"
            and os.environ.get("ADMIN_PASSWORD") == INSECURE_LOCAL_ADMIN_PASSWORD
            and value != INSECURE_LOCAL_ADMIN_PASSWORD
        ):
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


def _load_local_env() -> None:
    _load_env_file(Path(os.getenv("LOCAL_ENV_FILE") or str(ROOT / ".env")))
    environment = os.environ.get("ENVIRONMENT", "local").strip()
    environment_path = ROOT / f".env.{environment}"
    if environment_path.exists():
        _load_env_file(environment_path, override=True)
    explicit_path = os.environ.get("ENV_FILE", "").strip()
    if explicit_path:
        _load_env_file(Path(explicit_path), override=True)


def _apply_auth_contract() -> None:
    environment = os.environ.get("ENVIRONMENT", "local").strip().lower()
    if environment in PRODUCTION_ENVIRONMENTS:
        jwt_secret = os.environ.get("JWT_SECRET", "")
        admin_password = os.environ.get("ADMIN_PASSWORD", "")
        if jwt_secret == INSECURE_LOCAL_JWT_SECRET:
            os.environ.pop("JWT_SECRET", None)
            jwt_secret = ""
        if admin_password == INSECURE_LOCAL_ADMIN_PASSWORD:
            os.environ.pop("ADMIN_PASSWORD", None)
            admin_password = ""
        if not jwt_secret:
            raise RuntimeError("production JWT_SECRET is missing or unsafe")
        if not admin_password:
            raise RuntimeError("production ADMIN_PASSWORD is missing or unsafe")
        return
    _set_default_if_blank("JWT_SECRET", INSECURE_LOCAL_JWT_SECRET)
    _set_default_if_blank("ADMIN_PASSWORD", INSECURE_LOCAL_ADMIN_PASSWORD)


def apply_runtime_env() -> None:
    # Freeze the caller-selected mode before reading dotenv.  This mirrors
    # ``runtime_env.sh`` (`${ENVIRONMENT:-local}`): a stale ENVIRONMENT entry
    # inside .env must not silently turn a local smoke/load tool into a
    # production-mode client.  Production launchers pass the mode explicitly.
    if not os.environ.get("ENVIRONMENT", "").strip():
        os.environ["ENVIRONMENT"] = "local"
    # runtime_env.sh resolves these defaults before loading the base .env, so
    # a stale LOCAL_* URL in that file cannot redirect a local tool.  Reviewed
    # .env.<environment>/ENV_FILE overlays still use override semantics.
    os.environ["LOCAL_DATABASE_URL"] = _local_database_url()
    os.environ["LOCAL_REDIS_URL"] = _local_redis_url()
    os.environ["LOCAL_ENV_FILE"] = os.environ.get("LOCAL_ENV_FILE") or str(ROOT / ".env")
    os.environ.setdefault("ENV_FILE", "")
    _load_local_env()
    # Fail before filesystem setup or application imports can observe a public
    # development credential in a production-like runtime.
    _apply_auth_contract()
    if LEGACY_TOOLS_BIN.exists():
        current_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{LEGACY_TOOLS_BIN}{os.pathsep}{current_path}" if current_path else str(LEGACY_TOOLS_BIN)
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    RUNTIME_DATA.mkdir(parents=True, exist_ok=True)
    RUNTIME_LOGS.mkdir(parents=True, exist_ok=True)

    # Match ``runtime_env.sh``: command-line smoke/load/acceptance tools are
    # local by default, while production launchers always pass an explicit
    # ``ENVIRONMENT=production``.  A stale URL in the shared .env must not make
    # Python tools inspect a different local database/Redis than Web/workers.
    _set_default_if_blank("V2_PRODUCTION_MODE", "1")
    _set_default_if_blank("APP_ROLE", "worker")
    _set_default_if_blank("DB_RUNTIME_BACKEND", "postgres")
    local_database_url = os.environ["LOCAL_DATABASE_URL"]
    local_redis_url = os.environ["LOCAL_REDIS_URL"]
    if (
        os.environ["ENVIRONMENT"].strip().lower() == "local"
        and os.environ.get("RUNTIME_ENV_KEEP_DB_URL", "0") != "1"
    ):
        os.environ["DATABASE_URL"] = local_database_url
        os.environ["REDIS_URL"] = local_redis_url
    else:
        _set_default_if_blank("DATABASE_URL", local_database_url)
        _set_default_if_blank("REDIS_URL", local_redis_url)
    _set_default_if_blank("APP_STACK_NAME", "viltrox-2.0")
    _set_default_if_blank("REDIS_NAMESPACE", "viltrox-2.0:runtime")
