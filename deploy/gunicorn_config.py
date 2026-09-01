"""
Gunicorn configuration for Viltrox 2.0 web roles.

Used by:
  - public-web
  - admin-web
"""

from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path

bind = os.environ.get("BIND", "127.0.0.1:8101")
workers = int(os.environ.get("WORKERS", str(max(2, multiprocessing.cpu_count() // 2))))
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = int(os.environ.get("WORKER_CONNECTIONS", "2000"))
backlog = int(os.environ.get("BACKLOG", "4096"))

max_requests = int(os.environ.get("MAX_REQUESTS", "10000"))
max_requests_jitter = int(os.environ.get("MAX_REQUESTS_JITTER", "1000"))
timeout = int(os.environ.get("TIMEOUT", "300"))
graceful_timeout = int(os.environ.get("GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.environ.get("KEEPALIVE", "15"))

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()
preload_app = False

runtime_root = Path(os.environ.get("RUNTIME_ROOT", Path(__file__).resolve().parents[1] / "runtime"))
runtime_root.mkdir(parents=True, exist_ok=True)
pidfile = os.environ.get("PIDFILE", str(runtime_root / f"{os.environ.get('APP_ROLE', 'web')}.pid"))

worker_tmp_dir = "/dev/shm" if os.path.isdir("/dev/shm") else None

access_log_format = (
    '{"time":"%(t)s","ip":"%(h)s","method":"%(m)s","path":"%(U)s",'
    '"status":%(s)s,"duration_ms":%(M)s,"bytes":%(b)s,"ua":"%(a)s"}'
)


def _noop_process_title(_title: str) -> None:
    """Keep Gunicorn's post-fork title hook inert on affected Darwin runtimes."""


def _protect_darwin_post_fork_process_title() -> bool:
    """Disable the optional C title setter before Gunicorn forks on Darwin.

    macOS 27 rejects the CoreFoundation work performed by setproctitle in the
    child side of a multi-threaded fork.  Gunicorn calls that optional helper
    before it logs "Booting worker", which otherwise produces an immediate
    SIGSEGV loop.  Linux production keeps the normal process titles.
    """

    if sys.platform != "darwin":
        return False
    from gunicorn import util

    util._setproctitle = _noop_process_title
    return True


def on_starting(server):
    if _protect_darwin_post_fork_process_title():
        server.log.info(
            "Darwin fork safety: optional Gunicorn process-title updates disabled"
        )
    server.log.info("=" * 60)
    server.log.info(
        "viltrox-2.0 gunicorn starting | role=%s | workers=%s | backlog=%s | worker_connections=%s",
        os.environ.get("APP_ROLE", "web"),
        workers,
        backlog,
        worker_connections,
    )
    server.log.info("=" * 60)


def when_ready(server):
    server.log.info("viltrox-2.0 gunicorn ready on %s", bind)
