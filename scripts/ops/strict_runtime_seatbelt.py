#!/usr/bin/env python3
"""macOS Seatbelt profile for untrusted candidate runtime subprocesses."""

from __future__ import annotations

import json
import hashlib
import os
import platform
import pwd
import socket
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence


class SeatbeltError(RuntimeError):
    pass


def trusted_user_home() -> Path:
    """Return a physical home whose parent cannot be renamed by this uid."""

    raw = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    try:
        resolved = raw.resolve(strict=True)
        info = raw.lstat()
        parent_info = resolved.parent.lstat()
    except OSError as exc:
        raise SeatbeltError("trusted user home is unavailable") from exc
    if (
        raw.is_symlink()
        or raw.absolute() != resolved
        or not resolved.is_dir()
        or info.st_uid != os.geteuid()
        or resolved.parent.is_symlink()
        or not stat.S_ISDIR(parent_info.st_mode)
        or os.access(resolved.parent, os.W_OK)
    ):
        raise SeatbeltError("trusted user home has no stable filesystem anchor")
    return resolved


def _stable_write_anchor(path: Path) -> Path:
    """Find the first ancestor whose parent is not writable by this uid."""

    current = path.resolve(strict=True)
    if not current.is_dir():
        current = current.parent
    while current.parent != current and os.access(current.parent, os.W_OK):
        current = current.parent
    if current.parent == current:
        raise SeatbeltError("protected path has no stable filesystem anchor")
    return current


def _literal(value: Path | str) -> str:
    # Seatbelt profile strings are UTF-8, not JSON documents.  Escaping a
    # non-ASCII path as ``\uXXXX`` leaves those six characters literal in the
    # sandbox rule, so the allow-list silently misses repositories whose path
    # contains Chinese text or punctuation.
    return json.dumps(str(value), ensure_ascii=False)


def require_sandbox_exec() -> Path:
    binary = Path("/usr/bin/sandbox-exec")
    if platform.system() != "Darwin" or not binary.is_file() or not os.access(binary, os.X_OK):
        raise SeatbeltError("strict runtime requires macOS /usr/bin/sandbox-exec")
    return binary


def candidate_profile(*, candidate: Path, clean_source: Path, venv: Path,
                      node_modules: Path, runtime_root: Path,
                      allowed_ports: Sequence[int],
                      writable_paths: Sequence[Path] = (),
                      protect_clean_source: bool = True,
                      allow_runtime_root_write: bool = True,
                      executable_paths: Sequence[Path] = (),
                      executable_dirs: Sequence[Path] = (),
                      readable_paths: Sequence[Path] = ()) -> str:
    paths = [candidate, clean_source, venv, node_modules, runtime_root]
    resolved = [path.resolve(strict=True) for path in paths]
    if any(path.is_symlink() for path in paths):
        raise SeatbeltError("Seatbelt roots must be passed as physical non-symlink paths")
    if len(set(allowed_ports)) != len(allowed_ports) or 8102 in allowed_ports:
        raise SeatbeltError("Seatbelt ports must be unique and exclude 8102")
    network = "\n".join(
        f'(allow network-outbound (remote ip "localhost:{port}"))\n'
        f'(allow network-bind (local ip "localhost:{port}"))'
        for port in allowed_ports
    )
    writes = "\n".join(
        f'(allow file-write* (subpath {_literal(path.resolve())}))'
        for path in writable_paths
    )
    runtime_write = (
        f'(allow file-write* (subpath {_literal(resolved[4])}))'
        if allow_runtime_root_write else ""
    )
    clean_source_deny = (
        f'(deny file-write* (subpath {_literal(resolved[1])}))'
        if protect_clean_source else ""
    )
    executables = "\n".join(
        f'(allow process-exec (literal {_literal(path.resolve(strict=True))}))\n'
        f'(allow file-read* (subpath {_literal(path.resolve(strict=True).parent)}))'
        for path in executable_paths
    )
    executable_subpaths = "\n".join(
        f'(allow process-exec (subpath {_literal(path.resolve(strict=True))}))'
        for path in executable_dirs
    )
    readable_roots = [path.resolve(strict=True) for path in readable_paths]
    reads = "\n".join(
        f'(allow file-read* (subpath {_literal(path)}))'
        for path in readable_roots
    )
    executable_roots = [
        path.resolve(strict=True)
        for path in (*executable_paths, *executable_dirs)
    ]
    ancestors = sorted(
        {
            parent
            for path in (*resolved, *executable_roots, *readable_roots)
            for parent in path.parents
        }
    )
    metadata = "\n".join(
        f'(allow file-read-metadata (literal {_literal(path)}))' for path in ancestors
    )
    return f'''(version 1)
(deny default)
(import "system.sb")
{metadata}
(allow process-fork)
{executables}
{executable_subpaths}
(allow process-exec
  (literal "/private/var/select/sh")
  (subpath "/bin")
  (subpath "/usr/bin")
  (subpath "/usr/libexec")
  (subpath "/opt/homebrew")
  (subpath {_literal(resolved[0])})
  (subpath {_literal(resolved[1])})
  (subpath {_literal(resolved[2])})
  (subpath {_literal(resolved[3])}))
(allow signal (target same-sandbox))
(allow file-read*
  (literal "/opt")
  (literal "/private/var/select/sh")
  (subpath "/bin")
  (subpath "/usr/bin")
  (subpath "/usr/libexec")
  (subpath "/opt/homebrew")
  (subpath {_literal(resolved[0])})
  (subpath {_literal(resolved[1])})
  (subpath {_literal(resolved[2])})
  (subpath {_literal(resolved[3])})
  (subpath {_literal(resolved[4])}))
(allow file-read-metadata (subpath "/opt/homebrew"))
(allow file-read-metadata (literal "/private"))
(allow file-read-metadata (literal "/private/var"))
(allow file-read-metadata (literal "/private/var/select"))
(allow file-read-metadata (literal "/private/var/select/sh"))
{reads}
{runtime_write}
{clean_source_deny}
{writes}
(deny network*)
{network}
'''


def phase_a_protected_source_roots(
    *, source: Path, venv: Path, node_modules: Path,
) -> tuple[Path, ...]:
    clean_source = source.resolve(strict=True)
    physical_venv = venv.resolve(strict=True)
    physical_modules = node_modules.resolve(strict=True)
    roots = {clean_source}
    inferred_venv_root = (
        physical_venv.parent if physical_venv.name in {".venv", "venv"} else None
    )
    inferred_modules_root = (
        physical_modules.parent.parent
        if physical_modules.name == "node_modules"
        and physical_modules.parent.name == "frontend"
        else None
    )
    if inferred_venv_root is not None and inferred_venv_root == inferred_modules_root:
        roots.add(inferred_venv_root)
    return tuple(sorted(roots, key=str))


def phase_a_static_profile(
    *,
    source: Path,
    venv: Path,
    node_modules: Path,
    tool_paths: Sequence[Path],
    protected_write_paths: Sequence[Path] = (),
    user_home: Path | None = None,
) -> str:
    """Return a narrow deny-list profile for the complete Phase A test gate.

    The static suite deliberately creates nested sandboxes and loopback
    listeners, so this profile leaves the platform default allowed.  It only
    removes authority that the candidate never needs: writes to either the
    clean mirror or the physical source/dependency roots, reads of likely
    source/user credentials, and writes to controller-selected tools.
    """

    clean_source = source.resolve(strict=True)
    physical_venv = venv.resolve(strict=True)
    physical_modules = node_modules.resolve(strict=True)
    if not clean_source.is_dir() or not physical_venv.is_dir() or not physical_modules.is_dir():
        raise SeatbeltError("Phase A Seatbelt roots must be physical directories")

    home = user_home.resolve(strict=True) if user_home else trusted_user_home()
    source_roots = set(phase_a_protected_source_roots(
        source=source, venv=venv, node_modules=node_modules,
    ))

    protected_nodes = {
        home,
        *source_roots,
        Path(os.path.abspath(venv)),
        physical_venv,
        Path(os.path.abspath(node_modules)),
        physical_modules,
        *(path.resolve(strict=True) for path in protected_write_paths),
    }
    write_subpaths = {
        *protected_nodes,
        *(_stable_write_anchor(path) for path in (*protected_nodes, *tool_paths)),
    }
    write_literals = {
        alias
        for tool in tool_paths
        for alias in (Path(os.path.abspath(tool)), tool.resolve(strict=True))
    }
    secret_directories = (
        "runtime", "uploads", "frames", "backups", "creator_profiles",
        ".claude", ".codex-backups", ".secrets", "secrets",
        ".credentials", "credentials",
    )
    secret_names = {
        ".env", ".git-credentials", ".netrc", ".npmrc",
        "id_ed25519", "id_rsa",
    }
    secret_suffixes = (".dump", ".key", ".p12", ".pem", ".pfx")
    read_subpaths = {
        root / name for root in source_roots for name in secret_directories
    }
    read_literals: set[Path] = set()
    skipped_directories = {
        ".git", ".venv", "venv", "node_modules", *secret_directories,
    }
    for root in source_roots:
        for directory, names, files in os.walk(root, topdown=True, followlinks=False):
            names[:] = [name for name in names if name not in skipped_directories]
            for name in files:
                lower = name.lower()
                if (
                    lower in secret_names
                    or lower.startswith(".env.")
                    or lower.endswith(secret_suffixes)
                ):
                    path = Path(directory) / name
                    read_literals.add(Path(os.path.abspath(path)))
                    read_literals.add(path.resolve(strict=False))

    read_subpaths.update(
        {
            home / "Library/Keychains",
            home / ".ssh",
            home / ".aws",
            home / ".docker",
            home / ".kube",
            home / ".config/gcloud",
            home / ".config/gh",
        }
    )
    read_literals.update({home / ".git-credentials", home / ".netrc", home / ".npmrc"})

    rules = ["(version 1)", "(allow default)"]
    rules.extend(
        f"(deny file-write* (subpath {_literal(path)}))"
        for path in sorted(write_subpaths, key=str)
    )
    rules.extend(
        f"(deny file-write* (literal {_literal(path)}))"
        for path in sorted(write_literals, key=str)
    )
    rules.extend(
        f"(deny file-read* (subpath {_literal(path)}))"
        for path in sorted(read_subpaths, key=str)
    )
    rules.extend(
        f"(deny file-read* (literal {_literal(path)}))"
        for path in sorted(read_literals, key=str)
    )
    return "\n".join(rules) + "\n"


def sandboxed(arguments: Sequence[str], profile: str) -> list[str]:
    return [str(require_sandbox_exec()), "-p", profile, *arguments]


def run_preflight(source: Path) -> dict[str, object]:
    """Execute positive and negative Seatbelt probes without app services."""
    with tempfile.TemporaryDirectory(prefix="vkpi-seatbelt-preflight.", dir="/tmp") as raw:
        root = Path(raw); candidate = root / "candidate"; clean = root / "clean"
        runtime = root / "runtime"; node = root / "node_modules"; venv = source / ".venv"
        for path in (candidate, clean, runtime, node): path.mkdir(mode=0o700)
        readable = candidate / "readable"; readable.write_text("ok\n", encoding="utf-8")
        source_secret = source / ".env"
        if not source_secret.exists():
            source_secret = root.parent / f"{root.name}.source-secret"
            source_secret.write_text("secret\n", encoding="utf-8")
        write_decoy = root.parent / f"{root.name}.write-decoy"
        write_decoy.write_text("unchanged\n", encoding="utf-8")
        secret_before = hashlib.sha256(source_secret.read_bytes()).hexdigest()
        decoy_before = hashlib.sha256(write_decoy.read_bytes()).hexdigest()
        listener = socket.socket(); listener.bind(("127.0.0.1", 0)); listener.listen(1)
        allowed = listener.getsockname()[1]
        other_ports = []
        for _ in range(2):
            probe = socket.socket(); probe.bind(("127.0.0.1", 0))
            other_ports.append(probe.getsockname()[1]); probe.close()
        sleeper = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
        script = candidate / "probe.py"
        script.write_text('''import json, os, socket, sys
from pathlib import Path
candidate, runtime, secret, write_decoy, keychain, allowed, victim = sys.argv[1:]
result = {}
result["candidate_read"] = Path(candidate).read_text() == "ok\\n"
Path(runtime).write_text("ok\\n"); result["runtime_write"] = True
for name, action in {
  "source_env_denied": lambda: Path(secret).read_bytes(),
  "keychain_denied": lambda: Path(keychain).read_bytes(),
  "source_write_denied": lambda: Path(write_decoy).write_text("changed"),
  "signal_denied": lambda: os.kill(int(victim), 15),
  "port_8102_denied": lambda: socket.create_connection(("127.0.0.1", 8102), .2),
  "other_loopback_denied": lambda: socket.create_connection(("127.0.0.1", int(allowed)+17), .2),
  "external_denied": lambda: socket.create_connection(("1.1.1.1", 443), .2),
}.items():
  try: action(); result[name] = False
  except Exception: result[name] = True
with socket.create_connection(("127.0.0.1", int(allowed)), .5): pass
result["allowed_port"] = True
print(json.dumps(result, sort_keys=True))
''', encoding="utf-8")
        profile = candidate_profile(
            candidate=candidate, clean_source=clean, venv=venv, node_modules=node,
            runtime_root=runtime, allowed_ports=(allowed, *other_ports),
        )
        try:
            done = subprocess.run(
                sandboxed([str((venv / "bin/python").resolve(strict=True)), "-I", "-B", str(script),
                           str(readable), str(runtime / "write"), str(source_secret),
                           str(write_decoy),
                           str(Path.home() / "Library/Keychains/login.keychain-db"),
                           str(allowed), str(sleeper.pid)], profile),
                capture_output=True, text=True, timeout=15,
                env={"HOME": str(runtime), "LANG": "C", "LC_ALL": "C", "PATH": os.defpath},
            )
            try:
                payload = json.loads(done.stdout)
            except json.JSONDecodeError as exc:
                raise SeatbeltError(
                    f"Seatbelt probe produced no receipt rc={done.returncode}: {done.stderr[-500:]}"
                ) from exc
            if done.returncode != 0 or not payload or not all(payload.values()):
                raise SeatbeltError(f"Seatbelt preflight failed: {done.stderr[-300:]}")
            if hashlib.sha256(source_secret.read_bytes()).hexdigest() != secret_before:
                raise SeatbeltError("Seatbelt preflight changed the source secret")
            if hashlib.sha256(write_decoy.read_bytes()).hexdigest() != decoy_before:
                raise SeatbeltError("Seatbelt preflight changed the controlled write decoy")
            return {"pass": True, "checks": payload, "allowed_ports": [allowed, *other_ports]}
        finally:
            listener.close(); sleeper.terminate(); sleeper.wait(timeout=5)
            if source_secret.parent == root.parent and source_secret.name.endswith(".source-secret"):
                source_secret.unlink(missing_ok=True)
            write_decoy.unlink(missing_ok=True)
