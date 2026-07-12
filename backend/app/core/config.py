"""
core/config.py — 环境变量 & 路径配置
修改这个文件控制活动时间和AI模型，不用改其他代码
"""
from __future__ import annotations

import os
import secrets as _secrets_mod
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from app.core.logging import get_logger


logger = get_logger(__name__)

_INITIAL_ENV_KEYS = set(os.environ.keys())


def _proxy_host_port(proxy_url: str) -> str:
    parsed = urlparse(str(proxy_url or ""))
    host = parsed.hostname or ""
    if not host:
        return "configured"
    return f"{host}:{parsed.port}" if parsed.port else host


# X2(2026-07-02 cwd 连错库事故):此前 _load_env 用 Path(env_file) 相对路径,完全依赖进程 cwd —
# 从 backend/ 或任意别的目录起服务时静默读不到仓库根 .env(根下 87 行真配置;backend/.env 只剩
# JWT_SECRET 残留),DATABASE_URL 等缺省 → 连错库。修复:相对路径先锚定仓库根(本文件上溯 3 级:
# core→app→backend→根)的绝对路径;根下不存在该文件时兜底原 cwd 相对行为(兼容不同部署布局)。
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _resolve_env_path(env_file: str) -> Path:
    p = Path(env_file)
    if p.is_absolute():
        return p
    anchored = _REPO_ROOT / env_file
    if anchored.exists():
        return anchored
    return p


def _load_env(env_file: str = ".env", *, override: bool = False):
    p = _resolve_env_path(env_file)
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            key = k.strip()
            val = v.strip()
            if override:
                if key not in _INITIAL_ENV_KEYS:
                    os.environ[key] = val
            else:
                os.environ.setdefault(key, val)

def _normalize_environment(value: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"prod", "production"}:
        return "production"
    if raw in {"dev", "development"}:
        return "development"
    if raw in {"stage", "staging"}:
        return "staging"
    if raw in {"test", "testing"}:
        return "test"
    return "local"


_load_env(".env")
_env_hint = _normalize_environment(os.environ.get("ENVIRONMENT", "local"))
if _env_hint != "local":
    _load_env(f".env.{_env_hint}", override=True)
_explicit_env_file = os.environ.get("ENV_FILE", "").strip()
if _explicit_env_file:
    _load_env(_explicit_env_file, override=True)


def _env_flag(name: str, default: str = "0") -> bool:
    value = os.environ.get(name, default).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _env_int(name: str, default: str) -> int:
    return int(os.environ.get(name, default).strip() or default)


def _env_float(name: str, default: str) -> float:
    return float(os.environ.get(name, default).strip() or default)


def _env_csv(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


ENVIRONMENT = _normalize_environment(os.environ.get("ENVIRONMENT", "local"))
IS_PRODUCTION = ENVIRONMENT in {"prod", "production", "staging"} or _env_flag("V2_PRODUCTION_MODE", "0")
ENABLE_RUNTIME_PREVIEW_DATA = _env_flag("ENABLE_RUNTIME_PREVIEW_DATA", "0")
_DEFAULT_STACK_NAME = f"viltrox-2.0-{ENVIRONMENT}"
APP_STACK_NAME = os.environ.get("APP_STACK_NAME", _DEFAULT_STACK_NAME).strip() or _DEFAULT_STACK_NAME

# ── JWT ──
JWT_SECRET = os.environ.get("JWT_SECRET", "")
if not JWT_SECRET:
    if IS_PRODUCTION:
        raise RuntimeError("2.0 production requires JWT_SECRET")
    JWT_SECRET = _secrets_mod.token_hex(32)
    logger.warning("JWT_SECRET missing in local mode; generated an ephemeral in-memory secret for this process")
else:
    logger.info("JWT_SECRET loaded from environment")
JWT_SECRET_PREVIOUS = _env_csv("JWT_SECRET_PREVIOUS")
JWT_EXPIRES_DAYS = max(1, _env_int("JWT_EXPIRES_DAYS", "7"))
USER_CACHE_TTL_SEC = max(5, _env_int("USER_CACHE_TTL_SEC", "30"))

# ── AI 模型配置（改 .env 就能换模型，不用动代码）──
CLAUDE_MODEL       = os.environ.get("CLAUDE_MODEL",       "claude-sonnet-4-6")
CLAUDE_HAIKU_MODEL = os.environ.get("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
GEMINI_MODEL       = os.environ.get("GEMINI_MODEL",       "gemini-flash-latest")
OPENAI_MODEL       = os.environ.get("OPENAI_MODEL",       "gpt-5.4-mini")
DEFAULT_VIA_DIALOGUE_MODEL = "gpt-5.4-mini"

# ── API Keys ──
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY",    "")
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY",    "")
RAPIDAPI_KEY      = os.environ.get("RAPIDAPI_KEY",      "")
APIFY_TOKEN       = os.environ.get("APIFY_TOKEN", "").strip()
APIFY_TOKEN_PREVIOUS = _env_csv("APIFY_TOKEN_PREVIOUS")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
RESEND_API_KEY_PREVIOUS = _env_csv("RESEND_API_KEY_PREVIOUS")

# ── 活动时间控制 ──────────────────────────────────────────
# 只需改这两个变量，不用动其他任何代码
CAMPAIGN_START = datetime(2026, 4, 18, 0, 0, 0)   # 正式活动开始（UTC）
VIDEO_EARLIEST = datetime(2026, 4,  1, 0, 0, 0)   # 视频最早发布时间（兜底）
VIDEO_MAX_AGE_DAYS = int(os.environ.get("VIDEO_MAX_AGE_DAYS", "7"))


def is_campaign_live() -> bool:
    """是否已进入正式活动期（4月18日之后）"""
    return datetime.utcnow() >= CAMPAIGN_START


def is_test_period() -> bool:
    """是否在测试期（4.1–4.18）"""
    now = datetime.utcnow()
    return VIDEO_EARLIEST <= now < CAMPAIGN_START


# ── SMTP ──
SMTP_HOST  = os.environ.get("SMTP_HOST",  "")
SMTP_PORT  = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER  = os.environ.get("SMTP_USER",  "")
SMTP_PASS  = os.environ.get("SMTP_PASS",  "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", SMTP_USER or "noreply@viltroxvia.com")
SITE_URL   = os.environ.get("SITE_URL",   "https://www.viltroxvia.com")

# ── Paths ──
PROJECT_ROOT = Path(__file__).resolve().parents[3]
UPLOAD_DIR  = Path("uploads");      UPLOAD_DIR.mkdir(exist_ok=True)
FRAMES_DIR  = Path("frames");       FRAMES_DIR.mkdir(exist_ok=True)
CREATOR_DIR = Path("creator_profiles"); CREATOR_DIR.mkdir(exist_ok=True)
DB_PATH     = (PROJECT_ROOT / "submissions.db").resolve()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DATABASE_POOL_URL = os.environ.get("DATABASE_POOL_URL", "").strip()
DB_USE_PGBOUNCER = _env_flag("DB_USE_PGBOUNCER", "1" if DATABASE_POOL_URL else "0")
DB_RUNTIME_URL = DATABASE_POOL_URL if DB_USE_PGBOUNCER and DATABASE_POOL_URL else DATABASE_URL
DB_RUNTIME_BACKEND = os.environ.get(
    "DB_RUNTIME_BACKEND",
    "sqlite",
).strip().lower() or "sqlite"
DB_TARGET_BACKEND = os.environ.get(
    "DB_TARGET_BACKEND",
    "postgres" if DATABASE_URL else DB_RUNTIME_BACKEND,
).strip().lower() or DB_RUNTIME_BACKEND
POSTGRES_SCHEMA_PATH = os.environ.get(
    "POSTGRES_SCHEMA_PATH",
    "migrations/003_postgres_baseline.sql",
).strip() or "migrations/003_postgres_baseline.sql"
PLATFORM_INGEST_SHARED_SECRET = os.environ.get("PLATFORM_INGEST_SHARED_SECRET", "").strip()
PLATFORM_INGEST_SHARED_SECRET_PREVIOUS = _env_csv("PLATFORM_INGEST_SHARED_SECRET_PREVIOUS")
META_WEBHOOK_VERIFY_TOKEN = os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "").strip()
META_WEBHOOK_VERIFY_TOKEN_PREVIOUS = _env_csv("META_WEBHOOK_VERIFY_TOKEN_PREVIOUS")
META_APP_SECRET = os.environ.get("META_APP_SECRET", "").strip()
META_APP_SECRET_PREVIOUS = _env_csv("META_APP_SECRET_PREVIOUS")
SHOPIFY_WEBHOOK_SECRET = os.environ.get("SHOPIFY_WEBHOOK_SECRET", "").strip()
SHOPIFY_WEBHOOK_SECRET_PREVIOUS = _env_csv("SHOPIFY_WEBHOOK_SECRET_PREVIOUS")
SHOPIFY_AFFILIATE_BASE_URL = os.environ.get("SHOPIFY_AFFILIATE_BASE_URL", "https://viltrox.com/").strip() or "https://viltrox.com/"
TIKTOK_WEBHOOK_SECRET = os.environ.get("TIKTOK_WEBHOOK_SECRET", "").strip()
TIKTOK_WEBHOOK_SECRET_PREVIOUS = _env_csv("TIKTOK_WEBHOOK_SECRET_PREVIOUS")
PLATFORM_INGEST_SOURCES = [
    item.strip().lower()
    for item in os.environ.get(
        "PLATFORM_INGEST_SOURCES",
        "facebook,tiktok,instagram,youtube,shopify,amazon,bh,reddit,web",
    ).split(",")
    if item.strip()
]

# ── Runtime roles / queue ──
APP_ROLE = os.environ.get("APP_ROLE", "all").strip().lower() or "all"
REDIS_URL = os.environ.get("REDIS_URL", "").strip()
VALID_APP_ROLES = {"all", "web", "public-web", "admin-web", "worker"}
REDIS_NAMESPACE = os.environ.get("REDIS_NAMESPACE", f"{APP_STACK_NAME}:runtime").strip() or f"{APP_STACK_NAME}:runtime"
REDIS_CACHE_PREFIX = os.environ.get("REDIS_CACHE_PREFIX", f"{REDIS_NAMESPACE}:cache").strip() or f"{REDIS_NAMESPACE}:cache"
REDIS_RATE_LIMIT_PREFIX = os.environ.get("REDIS_RATE_LIMIT_PREFIX", f"{REDIS_NAMESPACE}:ratelimit").strip() or f"{REDIS_NAMESPACE}:ratelimit"
REDIS_JOB_STREAM_KEY = os.environ.get("REDIS_JOB_STREAM_KEY", f"{REDIS_NAMESPACE}:jobs:stream").strip() or f"{REDIS_NAMESPACE}:jobs:stream"
REDIS_JOB_GROUP = os.environ.get("REDIS_JOB_GROUP", f"{APP_STACK_NAME}-workers").strip() or f"{APP_STACK_NAME}-workers"
REDIS_JOB_DEAD_STREAM_KEY = os.environ.get("REDIS_JOB_DEAD_STREAM_KEY", f"{REDIS_NAMESPACE}:jobs:dead").strip() or f"{REDIS_NAMESPACE}:jobs:dead"
REDIS_JOB_EVENT_PREFIX = os.environ.get("REDIS_JOB_EVENT_PREFIX", f"{REDIS_NAMESPACE}:jobs:events").strip() or f"{REDIS_NAMESPACE}:jobs:events"
REDIS_JOB_STATUS_PREFIX = os.environ.get("REDIS_JOB_STATUS_PREFIX", f"{REDIS_NAMESPACE}:jobs:status")
REDIS_JOB_TTL_SEC = int(os.environ.get("REDIS_JOB_TTL_SEC", "604800"))  # 7 days
REDIS_JOB_CLAIM_IDLE_MS = _env_int("REDIS_JOB_CLAIM_IDLE_MS", "60000")
REDIS_JOB_BLOCK_MS = _env_int("REDIS_JOB_BLOCK_MS", "5000")
JOB_QUEUE_BACKPRESSURE_SOFT_LIMIT = _env_int("JOB_QUEUE_BACKPRESSURE_SOFT_LIMIT", "1200")
JOB_QUEUE_BACKPRESSURE_HARD_LIMIT = _env_int("JOB_QUEUE_BACKPRESSURE_HARD_LIMIT", "3000")
JOB_QUEUE_BACKPRESSURE_RETRY_AFTER_SEC = _env_int("JOB_QUEUE_BACKPRESSURE_RETRY_AFTER_SEC", "120")
REDIS_CACHE_DEFAULT_TTL_SEC = _env_int("REDIS_CACHE_DEFAULT_TTL_SEC", "120")
POSTGRES_POOL_MIN_SIZE = _env_int("POSTGRES_POOL_MIN_SIZE", "8")
POSTGRES_POOL_MAX_SIZE = _env_int("POSTGRES_POOL_MAX_SIZE", "64")
POSTGRES_POOL_TIMEOUT_SEC = _env_int("POSTGRES_POOL_TIMEOUT_SEC", "20")
DB_BUSY_TIMEOUT_MS = int(os.environ.get("DB_BUSY_TIMEOUT_MS", "5000"))
DB_CACHE_SIZE_KB = int(os.environ.get("DB_CACHE_SIZE_KB", "32768"))
DB_MMAP_SIZE_MB = int(os.environ.get("DB_MMAP_SIZE_MB", "128"))
DB_WAL_AUTOCHECKPOINT = int(os.environ.get("DB_WAL_AUTOCHECKPOINT", "1000"))
RESPONSE_GZIP_MIN_SIZE = int(os.environ.get("RESPONSE_GZIP_MIN_SIZE", "2048"))
ADMIN_READ_CACHE_TTL_SEC = int(os.environ.get("ADMIN_READ_CACHE_TTL_SEC", "5"))
ADMIN_STATS_CACHE_TTL_SEC = int(os.environ.get("ADMIN_STATS_CACHE_TTL_SEC", "3"))
AUDIT_RATE_LIMIT_MAX = int(os.environ.get("AUDIT_RATE_LIMIT_MAX", "12"))
AUDIT_RATE_LIMIT_WINDOW_SEC = int(os.environ.get("AUDIT_RATE_LIMIT_WINDOW_SEC", "300"))
UPLOAD_RATE_LIMIT_MAX = int(os.environ.get("UPLOAD_RATE_LIMIT_MAX", "6"))
UPLOAD_RATE_LIMIT_WINDOW_SEC = int(os.environ.get("UPLOAD_RATE_LIMIT_WINDOW_SEC", "600"))
UPLOAD_FINGERPRINT_CONCURRENCY = _env_int("UPLOAD_FINGERPRINT_CONCURRENCY", "4")
UPLOAD_R2_CONCURRENCY = _env_int("UPLOAD_R2_CONCURRENCY", "8")
VERIFY_CODE_TTL_HOURS = int(os.environ.get("VERIFY_CODE_TTL_HOURS", "24"))
TRUST_SCORE_DEFAULT = float(os.environ.get("TRUST_SCORE_DEFAULT", "30"))
TRUST_SCORE_STALE_SEC = int(os.environ.get("TRUST_SCORE_STALE_SEC", "21600"))
TRUST_LIMIT_MIN_HOURLY = int(os.environ.get("TRUST_LIMIT_MIN_HOURLY", "2"))
TRUST_LIMIT_MAX_HOURLY = int(os.environ.get("TRUST_LIMIT_MAX_HOURLY", "15"))
TRUST_LIMIT_MIN_DAILY_POINTS = int(os.environ.get("TRUST_LIMIT_MIN_DAILY_POINTS", "100"))
TRUST_LIMIT_MAX_DAILY_POINTS = int(os.environ.get("TRUST_LIMIT_MAX_DAILY_POINTS", "1000"))
# 成本闸(2026-07-01):via_daily_learning 每天抓 B&H 付费 actor,默认改停;要开在 .env 设 1。
VIA_ENABLE_DAILY_LEARNING = os.environ.get("VIA_ENABLE_DAILY_LEARNING", "0").strip().lower() not in {"0", "false", "no"}
VIA_LEARNING_MAX_POSTS = int(os.environ.get("VIA_LEARNING_MAX_POSTS", "18"))
VIA_LEARNING_COMMENT_LIMIT = int(os.environ.get("VIA_LEARNING_COMMENT_LIMIT", "40"))
VIA_LEARNING_COMMENT_SAMPLE = int(os.environ.get("VIA_LEARNING_COMMENT_SAMPLE", "12"))
VIA_LEARNING_BH_MAX_ITEMS = int(os.environ.get("VIA_LEARNING_BH_MAX_ITEMS", "120"))
# ── B&H Apify 开关(烧钱动作默认关,手动开)──────────────────────────
# 用户令 2026-07-02:search actor(产品列表)抓的数据与库内 100% 重复零增量,默认停;
# 要恢复在 .env 设 BH_SNAPSHOT_ENABLED=1。函数与 TTL 闸都保留,可随时恢复。
BH_SNAPSHOT_ENABLED = os.environ.get("BH_SNAPSHOT_ENABLED", "0").strip().lower() not in {"0", "false", "no", ""}
# reviews actor(用户评论→竞品口碑)也是付费 actor,默认关;手动跑 scripts/run_bh_reviews_once.py 前设 1。
BH_REVIEWS_ENABLED = os.environ.get("BH_REVIEWS_ENABLED", "0").strip().lower() not in {"0", "false", "no", ""}
# 单次评论抓取的产品数硬上限(防烧钱,每个产品 = 1 次付费 actor call)。
BH_REVIEWS_MAX_PRODUCTS = max(1, min(int(os.environ.get("BH_REVIEWS_MAX_PRODUCTS", "20")), 20))
# ── 迸发⑤ 市场之声监听扩源闸(X / 摄影论坛)——骨架就绪,默认关 ─────────
# 三态语义(监听覆盖卡如实展示,禁装):env 键不存在=未接入·盲区;键在但 0=骨架就绪·待开闸;
# 1=开闸(执行体在开闸刀才接,骨架阶段即使误开也零网络零烧钱)。运行时判定读 os.environ
# (app/domains/comments/market_listening.py),此处仅作中央登记;改闸只动 .env 不动代码。
VKPI_X_COLLECT_ENABLED = _env_flag("VKPI_X_COLLECT_ENABLED", "0")
VKPI_FORUM_COLLECT_ENABLED = _env_flag("VKPI_FORUM_COLLECT_ENABLED", "0")
VIA_OFFICIAL_INSTAGRAM_HANDLE = os.environ.get("VIA_OFFICIAL_INSTAGRAM_HANDLE", "viltrox.official").strip()
VIA_OFFICIAL_TIKTOK_HANDLE = os.environ.get("VIA_OFFICIAL_TIKTOK_HANDLE", "viltrox.global").strip()
VIA_OFFICIAL_YOUTUBE_HANDLE = os.environ.get("VIA_OFFICIAL_YOUTUBE_HANDLE", "viltroxofficial").strip()
VIA_OFFICIAL_FACEBOOK_HANDLE = os.environ.get("VIA_OFFICIAL_FACEBOOK_HANDLE", "").strip()
VIA_BASE_MODEL = os.environ.get("VIA_BASE_MODEL", DEFAULT_VIA_DIALOGUE_MODEL).strip() or DEFAULT_VIA_DIALOGUE_MODEL
VIA_DIALOGUE_PROVIDER = os.environ.get("VIA_DIALOGUE_PROVIDER", "openai").strip().lower() or "openai"
VIA_DIALOGUE_MODEL = os.environ.get("VIA_DIALOGUE_MODEL", DEFAULT_VIA_DIALOGUE_MODEL).strip() or DEFAULT_VIA_DIALOGUE_MODEL
VIA_DIALOGUE_COLLAB_ENABLED = _env_flag("VIA_DIALOGUE_COLLAB_ENABLED", "1")
VIA_DIALOGUE_COLLAB_PROVIDERS = [
    item.strip().lower()
    for item in os.environ.get("VIA_DIALOGUE_COLLAB_PROVIDERS", "claude,openai,gemini").split(",")
    if item.strip()
]
VIA_DIALOGUE_COLLAB_MAX_PROVIDERS = max(1, int(os.environ.get("VIA_DIALOGUE_COLLAB_MAX_PROVIDERS", "3")))
VIA_SUMMARY_PROVIDER = os.environ.get("VIA_SUMMARY_PROVIDER", "openai").strip().lower() or "openai"
VIA_SUMMARY_MODEL = os.environ.get("VIA_SUMMARY_MODEL", OPENAI_MODEL).strip() or OPENAI_MODEL
VIA_VISION_PROVIDER = os.environ.get("VIA_VISION_PROVIDER", "gemini").strip().lower() or "gemini"
VIA_VISION_MODEL = os.environ.get("VIA_VISION_MODEL", GEMINI_MODEL).strip() or GEMINI_MODEL
VIA_EMBEDDING_BACKEND = os.environ.get("VIA_EMBEDDING_BACKEND", "openai").strip().lower() or "openai"
VIA_EMBEDDING_MODEL = os.environ.get("VIA_EMBEDDING_MODEL", "text-embedding-3-small").strip() or "text-embedding-3-small"
VIA_BGE_MODEL = os.environ.get("VIA_BGE_MODEL", "BAAI/bge-small-en-v1.5").strip() or "BAAI/bge-small-en-v1.5"
VIA_VECTOR_BACKEND = os.environ.get("VIA_VECTOR_BACKEND", "qdrant").strip().lower() or "qdrant"
VIA_VECTOR_TOP_K = int(os.environ.get("VIA_VECTOR_TOP_K", "6"))
QDRANT_URL = os.environ.get("QDRANT_URL", "").strip()
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "").strip()
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "via_memory").strip() or "via_memory"
QDRANT_LOCAL_PATH = os.environ.get("QDRANT_LOCAL_PATH", "data/via_qdrant").strip() or "data/via_qdrant"
WEAVIATE_URL = os.environ.get("WEAVIATE_URL", "").strip()
WEAVIATE_API_KEY = os.environ.get("WEAVIATE_API_KEY", "").strip()
WEAVIATE_CLASS = os.environ.get("WEAVIATE_CLASS", "ViaMemory").strip() or "ViaMemory"
VIA_SESSION_TTL_SEC = int(os.environ.get("VIA_SESSION_TTL_SEC", "604800"))
VIA_EVENT_STREAM_PREFIX = os.environ.get("VIA_EVENT_STREAM_PREFIX", "viltrox:via-events").strip() or "viltrox:via-events"
VIA_EVENT_STREAM_MAXLEN = int(os.environ.get("VIA_EVENT_STREAM_MAXLEN", "500"))
VIA_EVENT_STREAM_BLOCK_MS = int(os.environ.get("VIA_EVENT_STREAM_BLOCK_MS", "15000"))
AI_BREAKER_FAILURE_THRESHOLD = _env_int("AI_BREAKER_FAILURE_THRESHOLD", "5")
AI_BREAKER_RECOVERY_TIMEOUT_SEC = _env_int("AI_BREAKER_RECOVERY_TIMEOUT_SEC", "60")
AI_BREAKER_HALF_OPEN_MAX_CALLS = _env_int("AI_BREAKER_HALF_OPEN_MAX_CALLS", "3")
AI_GEMINI_MAX_CONCURRENCY = _env_int("AI_GEMINI_MAX_CONCURRENCY", "12")
AI_OPENAI_MAX_CONCURRENCY = _env_int("AI_OPENAI_MAX_CONCURRENCY", "16")
AI_CLAUDE_MAX_CONCURRENCY = _env_int("AI_CLAUDE_MAX_CONCURRENCY", "10")
WORKER_SERVICE_PROCESSES = _env_int("WORKER_SERVICE_PROCESSES", "2")
WORKER_ASYNC_CONSUMERS = _env_int("WORKER_ASYNC_CONSUMERS", "15")
WORKER_CLUSTER_TIER = os.environ.get("WORKER_CLUSTER_TIER", "60").strip() or "60"
WORKER_CONFIGURED_CONCURRENCY = max(1, WORKER_SERVICE_PROCESSES) * max(1, WORKER_ASYNC_CONSUMERS)
VIDEO_WORKERS = _env_int("VIDEO_WORKERS", "30")
UPLOAD_DUPLICATE_PHASH_DISTANCE = int(os.environ.get("UPLOAD_DUPLICATE_PHASH_DISTANCE", "6"))
UPLOAD_DUPLICATE_PHASH_MATCHES = int(os.environ.get("UPLOAD_DUPLICATE_PHASH_MATCHES", "2"))
ADMIN_ENFORCE_IP_ALLOWLIST = _env_flag("ADMIN_ENFORCE_IP_ALLOWLIST", "0")
ADMIN_REQUIRE_SAME_ORIGIN = _env_flag("ADMIN_REQUIRE_SAME_ORIGIN", "1")
ADMIN_ALLOWED_IPS = [
    item.strip()
    for item in os.environ.get("ADMIN_ALLOWED_IPS", "").split(",")
    if item.strip()
]
ADMIN_TRUSTED_ORIGINS = [
    item.rstrip("/")
    for item in os.environ.get(
        "ADMIN_TRUSTED_ORIGINS",
        ",".join(
            filter(
                None,
                [
                    SITE_URL.rstrip("/"),
                    "https://viltroxvia.com",
                    "https://www.viltroxvia.com",
                    "https://www.viltroxtest.com",
                    "http://127.0.0.1",
                    "http://127.0.0.1:8001",
                    "http://127.0.0.1:8002",
                    "http://localhost",
                    "http://localhost:8001",
                    "http://localhost:8002",
                ],
            )
        ),
    ).split(",")
    if item.strip()
]

if APP_ROLE not in VALID_APP_ROLES:
    raise RuntimeError(f"Unsupported APP_ROLE={APP_ROLE!r}. Expected one of {sorted(VALID_APP_ROLES)}")

if IS_PRODUCTION:
    if APP_ROLE not in {"public-web", "admin-web", "worker"}:
        raise RuntimeError("2.0 production requires APP_ROLE to be one of public-web/admin-web/worker")
    if DB_RUNTIME_BACKEND != "postgres":
        raise RuntimeError("2.0 production requires DB_RUNTIME_BACKEND=postgres")
    if not DATABASE_URL:
        raise RuntimeError("2.0 production requires DATABASE_URL")
    if not REDIS_URL:
        raise RuntimeError("2.0 production requires REDIS_URL")

if DB_RUNTIME_BACKEND == "postgres" and not DATABASE_URL:
    if IS_PRODUCTION:
        raise RuntimeError("DATABASE_URL is required when DB_RUNTIME_BACKEND=postgres")
    logger.warning("DB_RUNTIME_BACKEND=postgres ignored because DATABASE_URL is empty")
    DB_RUNTIME_BACKEND = "sqlite"

if DB_USE_PGBOUNCER and not DATABASE_POOL_URL:
    if IS_PRODUCTION:
        raise RuntimeError("DB_USE_PGBOUNCER=1 requires DATABASE_POOL_URL")
    logger.warning("DB_USE_PGBOUNCER enabled but DATABASE_POOL_URL is empty; falling back to direct DATABASE_URL")

ENABLE_LOCAL_ORCHESTRATOR = _env_flag(
    "ENABLE_LOCAL_ORCHESTRATOR",
    "1" if APP_ROLE == "all" and not REDIS_URL and not IS_PRODUCTION else "0",
)
ENABLE_BROWSER = _env_flag(
    "ENABLE_BROWSER",
    "1" if APP_ROLE in {"all", "worker"} else "0",
)
ENABLE_SCHEDULER = _env_flag(
    "ENABLE_SCHEDULER",
    "1" if APP_ROLE == "all" else "0",
)
ENABLE_UPLOAD_CLEANUP = _env_flag(
    "ENABLE_UPLOAD_CLEANUP",
    "1" if APP_ROLE == "all" else "0",
)
USE_REDIS_JOBS = bool(REDIS_URL)
VKPI_ASYNC_ENABLED = _env_flag("VKPI_ASYNC_ENABLED", "0")
VKPI_VIDEO_CACHE_MAX_FILE_MB = max(1.0, _env_float("VKPI_VIDEO_CACHE_MAX_FILE_MB", "50"))
VKPI_VIDEO_CACHE_MAX_TOTAL_GB = max(0.01, _env_float("VKPI_VIDEO_CACHE_MAX_TOTAL_GB", "20"))

# ── CORS Origins ──
CORS_ORIGINS = [
    "http://127.0.0.1", "http://localhost",
    "http://127.0.0.1:8001", "http://127.0.0.1:8002",
    "http://localhost:8001", "http://localhost:8002",
    "http://127.0.0.1:5173", "http://localhost:5173",
    "http://127.0.0.1:8101", "http://127.0.0.1:8102",
    "http://localhost:8101", "http://localhost:8102",
    "https://viltroxvia.com", "https://www.viltroxvia.com",
    "https://viltroxtest.com", "https://www.viltroxtest.com",
]
if not IS_PRODUCTION:
    CORS_ORIGINS.append("null")
for _origin in os.environ.get("CORS_ORIGINS", "").split(","):
    origin = _origin.strip()
    if origin and origin not in CORS_ORIGINS:
        CORS_ORIGINS.append(origin)

# ── YTDLP Proxy ──
YTDLP_PROXY = os.environ.get("YTDLP_PROXY", "")
if YTDLP_PROXY:
    logger.info("yt-dlp proxy enabled: %s", _proxy_host_port(YTDLP_PROXY))
else:
    logger.info("yt-dlp running without proxy (direct IP)")

if IS_PRODUCTION:
    logger.info(
        "%s production mode enabled | role=%s | db=%s | redis=%s",
        APP_STACK_NAME,
        APP_ROLE,
        DB_RUNTIME_BACKEND,
        "yes" if REDIS_URL else "no",
    )
