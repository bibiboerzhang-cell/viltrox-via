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


def _production_auth_file_contract_category(
    env_path: Path,
    *,
    expected_owner_uid: int,
    expected_group_gid: int,
) -> str:
    """Return one fixed category without disclosing credential material."""
    import stat

    try:
        initial = env_path.lstat()
    except FileNotFoundError:
        return "env_missing"
    except OSError:
        return "env_stat_unavailable"
    if not stat.S_ISREG(initial.st_mode):
        return "env_not_regular"
    if initial.st_nlink != 1:
        return "env_link_count_invalid"
    if initial.st_uid != expected_owner_uid:
        return "env_owner_invalid"
    if initial.st_gid != expected_group_gid:
        return "env_group_invalid"
    if stat.S_IMODE(initial.st_mode) != 0o640:
        return "env_mode_invalid"

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(env_path, flags)
    except OSError:
        return "env_open_failed"

    previous_environment = dict(os.environ)
    try:
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
            or (observed.st_dev, observed.st_ino) != (initial.st_dev, initial.st_ino)
            or observed.st_uid != expected_owner_uid
            or observed.st_gid != expected_group_gid
            or stat.S_IMODE(observed.st_mode) != 0o640
        ):
            return "env_identity_changed"

        # Parse the already-open inode through the candidate's own dotenv
        # implementation.  This keeps the check read-only and closes the path
        # replacement window between metadata verification and parsing.
        descriptor_path = next(
            (
                candidate
                for candidate in (
                    Path(f"/proc/self/fd/{descriptor}"),
                    Path(f"/dev/fd/{descriptor}"),
                )
                if candidate.exists()
            ),
            None,
        )
        if descriptor_path is None:
            return "env_descriptor_unavailable"
        os.environ.clear()
        os.environ["ENVIRONMENT"] = "production"
        try:
            _load_env_file(descriptor_path)
        except (OSError, UnicodeError):
            return "env_read_invalid"
        except BaseException:
            return "candidate_runtime_invalid"

        jwt_secret = os.environ.get("JWT_SECRET", "")
        admin_password = os.environ.get("ADMIN_PASSWORD", "")
        if not jwt_secret:
            return "jwt_secret_missing"
        if jwt_secret == INSECURE_LOCAL_JWT_SECRET:
            return "jwt_secret_public_default"
        if not admin_password:
            return "admin_password_missing"
        if admin_password == INSECURE_LOCAL_ADMIN_PASSWORD:
            return "admin_password_public_default"
        try:
            _apply_auth_contract()
        except BaseException:
            return "candidate_auth_rejected"
        return "verified"
    finally:
        os.environ.clear()
        os.environ.update(previous_environment)
        try:
            os.close(descriptor)
        except OSError:
            pass


def _production_auth_preflight_cli(arguments: list[str]) -> int:
    """Machine-only CLI used by the remote deployment preflight."""
    import grp
    import sys

    category = "invalid_invocation"
    try:
        if len(arguments) == 3 and arguments[0] == "--production-auth-preflight":
            try:
                expected_gid = grp.getgrnam(arguments[2]).gr_gid
            except (KeyError, OSError):
                category = "expected_group_unavailable"
            else:
                category = _production_auth_file_contract_category(
                    Path(arguments[1]),
                    expected_owner_uid=0,
                    expected_group_gid=expected_gid,
                )
    except BaseException:
        category = "candidate_runtime_invalid"
    sys.stdout.write(category + "\n")
    return 0 if category == "verified" else 2


if __name__ == "__main__":
    import sys

    raise SystemExit(_production_auth_preflight_cli(sys.argv[1:]))
