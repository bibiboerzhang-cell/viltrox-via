"""scripts/ops/backup_to_r2.py — R2 定时备份推送器契约。

假 pg_dump / 假 pg_restore 走真 subprocess(证明密钥不进 argv),假 S3 客户端
走真上传/回读代码路径。覆盖:成功路径、回读 sha256 不匹配保留临时件、幂等跳过、
缺 R2_BACKUP_* 回退并告警、密钥不出现在任何日志或异常文本、boto3 缺失明确报错。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import backup_to_r2 as backup  # noqa: E402


SERVICE_UNIT = ROOT / "scripts/ops/systemd/vkpi-backup-r2.service"
TIMER_UNIT = ROOT / "scripts/ops/systemd/vkpi-backup-r2.timer"
STAMP = "20260825T060000Z"
DUMP_KEY = f"vkpi-db/2026/08/25/prod-db-{STAMP}.dump.gz"
SIDECAR_KEY = f"{DUMP_KEY}.sha256"
DB_PASSWORD = "pg-secret-do-not-print-9f2b"
R2_SECRET = "r2-secret-do-not-print-4a71"
R2_BACKUP_SECRET = "r2-backup-secret-do-not-print-c3d9"
DATABASE_URL = (
    f"postgresql://vkpi_backup:{DB_PASSWORD}@127.0.0.1:5432/"
    "viltrox2_test_release_9d2f7ca7158477ec10b7?sslmode=prefer"
)
DUMP_PAYLOAD = b"PGDMP-fake-custom-archive-payload\n" * 64
SECRETS = (DB_PASSWORD, R2_SECRET, R2_BACKUP_SECRET, DATABASE_URL)


# --------------------------------------------------------------------------
# fixtures / fakes
# --------------------------------------------------------------------------


def _write_env(tmp_path: Path, *, dedicated_token: bool) -> Path:
    lines = [
        f'DATABASE_URL="{DATABASE_URL}"',
        "R2_ENDPOINT=https://abc123def456.r2.cloudflarestorage.com",
        "R2_BUCKET_NAME=viltrox-media",
        "R2_ACCESS_KEY_ID=shared-access-key-id",
        f"R2_SECRET_ACCESS_KEY={R2_SECRET}",
    ]
    if dedicated_token:
        lines += [
            "R2_BACKUP_BUCKET=viltrox-db-backup",
            "R2_BACKUP_ACCESS_KEY_ID=backup-access-key-id",
            f"R2_BACKUP_SECRET_ACCESS_KEY={R2_BACKUP_SECRET}",
        ]
    path = tmp_path / "dot-env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _write_fake_bin(tmp_path: Path) -> tuple[Path, Path]:
    """Real executables on PATH so the subprocess boundary is exercised."""

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    evidence = tmp_path / "pg_dump-evidence.json"
    payload_literal = repr(DUMP_PAYLOAD)
    (fake_bin / "pg_dump").write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "target = ''\n"
        "for item in sys.argv[1:]:\n"
        "    if item.startswith('--file='):\n"
        "        target = item.split('=', 1)[1]\n"
        f"evidence = {str(evidence)!r}\n"
        "with open(evidence, 'w', encoding='utf-8') as handle:\n"
        "    json.dump({'argv': sys.argv[1:], 'env': dict(os.environ)}, handle)\n"
        "if not target:\n"
        "    sys.exit(3)\n"
        "with open(target, 'wb') as handle:\n"
        f"    handle.write({payload_literal})\n",
        encoding="utf-8",
    )
    (fake_bin / "pg_dump").chmod(0o755)
    (fake_bin / "pg_restore").write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdout.write('; Archive created at 2026-08-25\\n')\n",
        encoding="utf-8",
    )
    (fake_bin / "pg_restore").chmod(0o755)
    return fake_bin, evidence


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.closed = False

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        self.closed = True


class FakeS3Client:
    """In-memory S3 double that records the calls the pusher makes."""

    def __init__(self, *, corrupt_sidecar: bool = False, size_delta: int = 0) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.corrupt_sidecar = corrupt_sidecar
        self.size_delta = size_delta
        self.closed = False

    # -- helpers -----------------------------------------------------------
    def seed(self, key: str, body: bytes) -> None:
        self.objects[key] = {"Body": body, "Metadata": {}}

    def _missing(self, key: str) -> Exception:
        error = Exception(f"missing {key}")
        error.response = {  # type: ignore[attr-defined]
            "Error": {"Code": "NoSuchKey"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        }
        return error

    # -- S3 surface --------------------------------------------------------
    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(f"put:{kwargs['Key']}")
        body = kwargs["Body"]
        data = body if isinstance(body, bytes) else body.read()
        if self.corrupt_sidecar and kwargs["Key"].endswith(".sha256"):
            data = b"0" * 64 + b"  tampered.dump.gz\n"
        self.objects[kwargs["Key"]] = {
            "Body": data,
            "Metadata": dict(kwargs.get("Metadata") or {}),
            "ContentType": kwargs.get("ContentType"),
        }
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append(f"head:{Key}")
        record = self.objects.get(Key)
        if record is None:
            raise self._missing(Key)
        return {
            "ContentLength": len(record["Body"]) + self.size_delta,
            "Metadata": record["Metadata"],
        }

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append(f"get:{Key}")
        record = self.objects.get(Key)
        if record is None:
            raise self._missing(Key)
        return {"Body": FakeBody(record["Body"])}

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("list")
        prefix = kwargs.get("Prefix") or ""
        contents = [
            {"Key": key, "Size": len(record["Body"])}
            for key, record in sorted(self.objects.items())
            if key.startswith(prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}


def _run_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    client: FakeS3Client,
    dedicated_token: bool = True,
    extra_args: list[str] | None = None,
) -> tuple[int, Path, Path]:
    env_file = _write_env(tmp_path, dedicated_token=dedicated_token)
    fake_bin, evidence = _write_fake_bin(tmp_path)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    work_dir = tmp_path / "volume" / "vkpi-backups" / "r2-staging"
    argv = [
        "--env-file",
        str(env_file),
        "--work-dir",
        str(work_dir),
        "--stamp",
        STAMP,
        "--min-free-bytes",
        "0",
        *(extra_args or []),
    ]
    code = backup.main(argv, client_factory=lambda credentials: client)
    return code, work_dir, evidence


def _events(captured: str) -> dict[str, dict[str, Any]]:
    parsed: dict[str, dict[str, Any]] = {}
    for line in captured.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        payload = json.loads(line)
        parsed[payload["event"]] = payload
    return parsed


def _assert_no_secret(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text, f"secret leaked into output: {secret[:6]}..."


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------


def test_success_uploads_verifies_and_cleans_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeS3Client()
    code, work_dir, evidence = _run_backup(tmp_path, monkeypatch, client=client)
    captured = capsys.readouterr()
    events = _events(captured.out)

    assert code == 0, captured.err
    # The idempotency HEAD runs before anything expensive; the sidecar HEAD is
    # short-circuited because the dump key is already absent.
    assert client.calls[0] == f"head:{DUMP_KEY}"
    assert f"put:{DUMP_KEY}" in client.calls
    assert f"put:{SIDECAR_KEY}" in client.calls
    assert f"get:{SIDECAR_KEY}" in client.calls
    assert "readback_verified" in events
    assert "backup_completed" in events

    # The uploaded object is the gzip of exactly what pg_dump produced, and the
    # sidecar carries its SHA-256 in sha256sum wire format.
    uploaded = client.objects[DUMP_KEY]["Body"]
    assert gzip.decompress(uploaded) == DUMP_PAYLOAD
    expected_sha = hashlib.sha256(uploaded).hexdigest()
    assert client.objects[SIDECAR_KEY]["Body"] == (
        f"{expected_sha}  prod-db-{STAMP}.dump.gz\n".encode("utf-8")
    )
    assert client.objects[DUMP_KEY]["Metadata"]["vkpi-sha256"] == expected_sha
    assert client.objects[DUMP_KEY]["ContentType"] == "application/gzip"

    completed = events["backup_completed"]
    assert completed["sha256"] == expected_sha
    assert completed["uploaded_bytes"] == len(uploaded) + len(
        client.objects[SIDECAR_KEY]["Body"]
    )
    assert completed["token_source"] == "dedicated_backup_token"
    assert completed["inventory"] == {
        "available": True,
        "objects": 2,
        "bytes": len(uploaded) + len(client.objects[SIDECAR_KEY]["Body"]),
        "pages_read": 1,
    }

    # try/finally cleanup: the staging directory is gone, the work root stays.
    assert work_dir.is_dir()
    assert list(work_dir.iterdir()) == []
    assert "temporary_files_removed" in events

    # The dump ran as a real subprocess and never saw the password in argv or
    # in its environment; libpq credentials arrived through 0600 files.
    proof = json.loads(evidence.read_text(encoding="utf-8"))
    _assert_no_secret(json.dumps(proof["argv"]))
    assert proof["env"]["PGSERVICE"] == "vkpi_r2_backup"
    assert "PGPASSWORD" not in proof["env"]
    assert "DATABASE_URL" not in proof["env"]
    for value in proof["env"].values():
        _assert_no_secret(str(value))


def test_sha256_mismatch_fails_and_retains_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeS3Client(corrupt_sidecar=True)
    code, work_dir, _ = _run_backup(tmp_path, monkeypatch, client=client)
    captured = capsys.readouterr()

    assert code == 1
    failure = _events(captured.err)["backup_failed"]
    assert failure["stage"] == "verify"
    assert failure["category"] == "readback_sha256_mismatch"

    events = _events(captured.out)
    # The bad object is not deleted, and that is stated rather than assumed.
    assert events["readback_mismatch_object_left_in_place"]["object_key"] == DUMP_KEY

    retained = events["temporary_files_retained"]
    staging = Path(retained["path"])
    assert staging.is_dir(), "temporary artifacts must survive a readback mismatch"
    assert (staging / f"prod-db-{STAMP}.dump.gz").is_file()
    # The libpq credential files are deleted even when the dump is retained.
    assert not (staging / ".pgpass").exists()
    assert not (staging / ".pgservice").exists()
    assert staging.parent == work_dir


def test_head_size_mismatch_fails_and_retains_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeS3Client(size_delta=7)
    code, _, _ = _run_backup(tmp_path, monkeypatch, client=client)
    captured = capsys.readouterr()

    assert code == 1
    failure = _events(captured.err)["backup_failed"]
    assert failure["category"] == "readback_size_mismatch"
    assert Path(_events(captured.out)["temporary_files_retained"]["path"]).is_dir()


def test_existing_stamp_is_skipped_without_dumping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeS3Client()
    client.seed(DUMP_KEY, b"already-uploaded")
    client.seed(SIDECAR_KEY, b"already-uploaded-sidecar")
    code, work_dir, evidence = _run_backup(tmp_path, monkeypatch, client=client)
    captured = capsys.readouterr()

    assert code == 0, captured.err
    skipped = _events(captured.out)["backup_skipped_existing_stamp"]
    assert skipped["object_key"] == DUMP_KEY
    assert skipped["uploaded_bytes"] == 0
    assert not any(call.startswith("put:") for call in client.calls)
    # No dump, and therefore no work directory and no temporary files at all.
    assert not evidence.exists()
    assert not work_dir.exists()


def test_partial_previous_upload_is_not_treated_as_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeS3Client()
    client.seed(DUMP_KEY, b"orphan-dump-without-sidecar")
    code, _, _ = _run_backup(tmp_path, monkeypatch, client=client)
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert f"put:{DUMP_KEY}" in client.calls
    assert f"put:{SIDECAR_KEY}" in client.calls


def test_missing_backup_token_falls_back_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeS3Client()
    code, _, _ = _run_backup(
        tmp_path, monkeypatch, client=client, dedicated_token=False
    )
    captured = capsys.readouterr()
    events = _events(captured.out)

    assert code == 0, captured.err
    warning = events["r2_token_fallback_shared_readwrite"]
    assert warning["severity"] == "warning"
    assert warning["message"] == "正在使用读写全权令牌,建议改用只写令牌"
    assert warning["missing_backup_keys"] == [
        "R2_BACKUP_BUCKET",
        "R2_BACKUP_ACCESS_KEY_ID",
        "R2_BACKUP_SECRET_ACCESS_KEY",
    ]
    assert events["backup_completed"]["token_source"] == (
        "shared_readwrite_token_fallback"
    )


def test_partial_backup_token_also_falls_back_and_names_the_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = dict(
        R2_ENDPOINT="https://abc.r2.cloudflarestorage.com",
        R2_BUCKET_NAME="viltrox-media",
        R2_ACCESS_KEY_ID="shared-access-key-id",
        R2_SECRET_ACCESS_KEY=R2_SECRET,
        R2_BACKUP_BUCKET="viltrox-db-backup",
    )
    credentials = backup.resolve_r2_credentials(env)
    assert credentials.is_fallback
    assert credentials.bucket == "viltrox-media"
    assert credentials.missing_backup_keys == (
        "R2_BACKUP_ACCESS_KEY_ID",
        "R2_BACKUP_SECRET_ACCESS_KEY",
    )


def test_no_secret_reaches_any_log_line_or_exception_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class LeakingClient(FakeS3Client):
        def put_object(self, **kwargs: Any) -> dict[str, Any]:
            # A hostile/verbose SDK error that embeds the live credentials.
            raise RuntimeError(
                f"signing failed url=https://r2/?key={R2_BACKUP_SECRET} "
                f"dsn={DATABASE_URL}"
            )

    client = LeakingClient()
    code, _, _ = _run_backup(tmp_path, monkeypatch, client=client)
    captured = capsys.readouterr()

    assert code == 1
    _assert_no_secret(captured.out)
    _assert_no_secret(captured.err)
    assert "backup_failed" in _events(captured.err)

    # The same guarantee holds for a raw exception message built from a secret.
    backup.register_secret(DB_PASSWORD)
    try:
        error = backup.BackupError("dump", "nonzero_exit", hint=f"pw={DB_PASSWORD}")
        assert DB_PASSWORD not in str(error)
        assert "***REDACTED***" in str(error)
    finally:
        backup.reset_secrets()


def test_log_redaction_is_reported_not_silent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    backup.register_secret(R2_SECRET)
    try:
        backup.emit("synthetic_event", detail=f"leak={R2_SECRET}")
    finally:
        backup.reset_secrets()
    captured = capsys.readouterr()
    assert R2_SECRET not in captured.out
    assert _events(captured.err)["log_redaction_applied"]["source_event"] == (
        "synthetic_event"
    )


def test_missing_boto3_is_a_named_failure_pointing_at_the_venv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "boto3", None)
    credentials = backup.resolve_r2_credentials(
        {
            "R2_ENDPOINT": "https://abc.r2.cloudflarestorage.com",
            "R2_BUCKET_NAME": "viltrox-media",
            "R2_ACCESS_KEY_ID": "shared-access-key-id",
            "R2_SECRET_ACCESS_KEY": R2_SECRET,
        }
    )
    with pytest.raises(backup.BackupError) as excinfo:
        backup.build_client(credentials)
    assert excinfo.value.stage == "client"
    assert excinfo.value.category == "boto3_missing"
    assert ".venv" in excinfo.value.hint


def test_pg_dump_failure_cleans_up_and_reports_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = _write_env(tmp_path, dedicated_token=True)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "pg_dump").write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stderr.write('FATAL: password authentication failed {DB_PASSWORD}\\n')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    (fake_bin / "pg_dump").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    work_dir = tmp_path / "volume" / "r2-staging"
    code = backup.main(
        [
            "--env-file",
            str(env_file),
            "--work-dir",
            str(work_dir),
            "--stamp",
            STAMP,
            "--min-free-bytes",
            "0",
        ],
        client_factory=lambda credentials: FakeS3Client(),
    )
    captured = capsys.readouterr()

    assert code == 1
    failure = _events(captured.err)["backup_failed"]
    assert failure["stage"] == "dump"
    assert failure["category"] == "nonzero_exit"
    # The subprocess stderr is surfaced (no silent swallow) but scrubbed.
    assert _events(captured.err)["subprocess_failed"]["returncode"] == 1
    _assert_no_secret(captured.err)
    _assert_no_secret(captured.out)
    # Non-readback failures still clean the work directory.
    assert list(work_dir.iterdir()) == []


def test_object_key_layout_and_stamp_validation() -> None:
    dump_key, sidecar_key, filename = backup.object_keys("vkpi-db", STAMP)
    assert dump_key == "vkpi-db/2026/08/25/prod-db-20260825T060000Z.dump.gz"
    assert sidecar_key == dump_key + ".sha256"
    assert filename == "prod-db-20260825T060000Z.dump.gz"
    for bad in ("2026-08-25", "20260825T0600Z", "", "../etc"):
        with pytest.raises(backup.BackupError):
            backup.object_keys("vkpi-db", bad)
    with pytest.raises(backup.BackupError):
        backup.object_keys("../escape", STAMP)


def test_database_url_never_reaches_argv_or_forbidden_parameters() -> None:
    credentials = backup.parse_database_url(DATABASE_URL)
    assert credentials.dbname == "viltrox2_test_release_9d2f7ca7158477ec10b7"
    assert credentials.host == "127.0.0.1"
    assert ("sslmode", "prefer") in credentials.params
    assert not any(DB_PASSWORD in value for _, value in credentials.params)
    for bad in (
        "postgresql://u:p@h/db?service=other",
        "postgresql://u:p@h/db?passfile=/tmp/x",
        "mysql://u:p@h/db",
        "postgresql://u:p@h/",
    ):
        with pytest.raises(backup.BackupError):
            backup.parse_database_url(bad)


def test_world_readable_env_file_is_rejected(tmp_path: Path) -> None:
    env_file = _write_env(tmp_path, dedicated_token=True)
    env_file.chmod(0o644)
    with pytest.raises(backup.BackupError) as excinfo:
        backup.load_env_file(env_file)
    assert excinfo.value.category == "env_file_not_private_regular"


def test_systemd_units_match_the_reviewed_contract() -> None:
    service = SERVICE_UNIT.read_text(encoding="utf-8")
    timer = TIMER_UNIT.read_text(encoding="utf-8")

    assert "Type=oneshot" in service
    assert "User=viltrox" in service
    assert "Group=viltrox" in service
    # The .venv interpreter under the atomic 'current' pointer, not python3.
    assert (
        "ExecStart=/opt/viltrox-2.0/.venv/bin/python -B "
        "/opt/viltrox-2.0/current/scripts/ops/backup_to_r2.py"
    ) in service
    assert "--work-dir /mnt/HC_Volume_106700445/vkpi-backups/r2-staging" in service
    # Secrets must not be handed to the unit's process environment.
    directives = [
        line.strip()
        for line in service.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any(line.startswith("EnvironmentFile") for line in directives)
    # Fail closed when the volume is not mounted, so the dump can never land on
    # the 89%-full system disk.
    assert "ExecStartPre=/usr/bin/mountpoint -q /mnt/HC_Volume_106700445" in service
    assert "ReadWritePaths=/mnt/HC_Volume_106700445/vkpi-backups" in service
    assert "InaccessiblePaths=/opt/viltrox-2.0/backups" in service
    assert not any(
        line.startswith("InaccessiblePaths=/opt/viltrox-2.0/releases")
        for line in directives
    ), "current/ is a symlink into releases/; blocking it breaks WorkingDirectory"
    # The timer owns this unit; an [Install] section would also dump at boot.
    assert "[Install]" not in directives

    assert "OnCalendar=*-*-* 00,06,12,18:00:00 UTC" in timer
    assert "Persistent=true" in timer
    assert "RandomizedDelaySec=900" in timer
    assert "Unit=vkpi-backup-r2.service" in timer
    assert "WantedBy=timers.target" in timer
