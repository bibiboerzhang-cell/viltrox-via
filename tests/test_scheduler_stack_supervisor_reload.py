"""O1:本地栈监督器版本自检——自身文件 sha256 变化即 exec 重载(launchd KeepAlive 下同 PID)。

监督器负责拉起 scheduler_daemon / worker 车道;此前 dist 拷回或改白名单后常驻老进程
仍跑旧逻辑。把 ensure_* 与 sleep 打桩后真跑一份拷贝:改文件 → 日志一行"exec 自身重载"
→ 新 sha 的"上岗"行 → PID 不变。
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / "scripts" / "ops" / "local_stack_supervisor.sh"


def _stubbed_copy(target_root: Path) -> Path:
    source = SUPERVISOR.read_text(encoding="utf-8")
    for ensure in ("ensure_admin_web", "ensure_apify_pool", "ensure_scheduler", "ensure_worker_main"):
        assert f"\n  {ensure}\n" in source
        source = source.replace(f"\n  {ensure}\n", "\n  :\n", 1)
    assert "\n  sleep 60\n" in source
    source = source.replace("\n  sleep 60\n", "\n  sleep 0.2\n", 1)
    copy = target_root / "scripts" / "ops" / "local_stack_supervisor.sh"
    copy.parent.mkdir(parents=True)
    copy.write_text(source, encoding="utf-8")
    return copy


def _wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_supervisor_source_declares_self_sha_reload() -> None:
    source = SUPERVISOR.read_text(encoding="utf-8")
    assert 'SELF_SHA="$(self_sha256)"' in source
    assert "reload_if_self_changed" in source
    assert 'exec bash "$SELF_PATH" "$@"' in source
    # exec 前必须 bash -n,防半写文件把常驻监督器弄死。
    reload_body = source.split("reload_if_self_changed() {", 1)[1].split("\n}\n", 1)[0]
    assert 'bash -n "$SELF_PATH"' in reload_body
    loop = source.split("\nwhile true; do\n", 1)[1]
    assert loop.index('reload_if_self_changed "$@"') < loop.index("ensure_admin_web")
    assert subprocess.run(["bash", "-n", str(SUPERVISOR)], check=False).returncode == 0


def test_supervisor_execs_itself_when_its_file_changes(tmp_path: Path) -> None:
    copy = _stubbed_copy(tmp_path)
    log = tmp_path / "runtime" / "logs" / "supervisor.log"
    env = {**os.environ, "APIFY_WORKER_EXPECTED_INSTANCES": "16"}
    proc = subprocess.Popen(["bash", str(copy)], env=env, cwd=tmp_path)
    try:
        assert _wait_for(lambda: log.exists() and "supervisor 上岗 self=" in log.read_text(encoding="utf-8"))
        first = re.search(r"supervisor 上岗 self=([0-9a-f]{12})", log.read_text(encoding="utf-8"))
        assert first is not None

        with copy.open("a", encoding="utf-8") as handle:
            handle.write("\n# reload probe\n")

        assert _wait_for(lambda: "exec 自身重载" in log.read_text(encoding="utf-8"))
        assert _wait_for(
            lambda: len(re.findall(r"supervisor 上岗 self=", log.read_text(encoding="utf-8"))) >= 2
        )
        text = log.read_text(encoding="utf-8")
        reload_lines = [line for line in text.splitlines() if "exec 自身重载" in line]
        assert len(reload_lines) == 1
        assert f"{first.group(1)}→" in reload_lines[0]
        second = re.findall(r"supervisor 上岗 self=([0-9a-f]{12})", text)[-1]
        assert second != first.group(1)
        assert proc.poll() is None  # exec 保留 PID,launchd 视角进程从未退出
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
