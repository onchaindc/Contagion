"""Command wrapping for the Contagion backend. Pure Python, no Hermes imports."""

from __future__ import annotations

import functools
import os
import shlex
import shutil
import subprocess
from typing import Callable, Dict, Iterable, List, Mapping, Optional


class SandboxError(RuntimeError):
    """The jail cannot be built safely. The caller fails closed."""


ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "USER", "LOGNAME", "SHELL", "TZ")
LINUX_RO_CANDIDATES = (".local/bin", ".cargo", ".rustup", ".nvm", ".pyenv", ".bun", ".deno", ".volta", "go")
MAC_SECRET_DIRS = (
    ".ssh", ".aws", ".gnupg", ".kube", ".docker", ".config/gcloud", ".config/gh",
    ".config/solana", ".ethereum", ".hermes", "Library/Keychains",
)
MAC_SECRET_FILES = (".netrc", ".npmrc", ".pypirc")


def scrub_env(environ: Mapping[str, str], extra_keep: Iterable[str] = ()) -> Dict[str, str]:
    keep = set(ENV_ALLOWLIST) | set(extra_keep)
    return {k: v for k, v in environ.items() if k in keep}


def check_project_dir(project_dir: str, home: str) -> str:
    """Refuse project roots that would make the jail meaningless."""
    project = os.path.realpath(project_dir)
    home_real = os.path.realpath(home)
    if project == "/" or project == home_real or home_real.startswith(project.rstrip("/") + "/"):
        raise SandboxError(
            "refusing to jail a session whose working directory is your home directory or a parent of it. "
            "Set terminal.cwd to a project folder."
        )
    return project


def linux_argv(command: str, *, project_dir: str, home: str, ro_paths: Iterable[str] = ()) -> List[str]:
    argv = [
        "bwrap", "--unshare-net", "--unshare-pid", "--die-with-parent", "--new-session",
        "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
    ]
    if home and home != "/":
        argv += ["--tmpfs", home]
    for path in ro_paths:
        argv += ["--ro-bind", path, path]
    argv += ["--bind", project_dir, project_dir, "--chdir", project_dir, "--", "bash", "-c", command]
    return argv


def _sbpl(path: str) -> str:
    if '"' in path or "\\" in path:
        raise SandboxError("unsupported character in a sandbox path")
    return '"' + path + '"'


def macos_profile(*, project_dir: str, home: str, tmp_dirs: Iterable[str], extra_deny_read: Iterable[str] = ()) -> str:
    write_ok = " ".join(f"(subpath {_sbpl(p)})" for p in [project_dir, *tmp_dirs])
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        "(deny file-write*)",
        f'(allow file-write* {write_ok} (literal "/dev/null") (literal "/dev/tty") (regex #"^/dev/ttys[0-9]+$"))',
    ]
    for rel in MAC_SECRET_DIRS:
        lines.append(f"(deny file-read* (subpath {_sbpl(os.path.join(home, rel))}))")
    for rel in MAC_SECRET_FILES:
        lines.append(f"(deny file-read* (literal {_sbpl(os.path.join(home, rel))}))")
    for extra in extra_deny_read:
        lines.append(f"(deny file-read* (subpath {_sbpl(os.path.expanduser(extra))}))")
    return "\n".join(lines)


def macos_argv(command: str, *, profile: str) -> List[str]:
    return ["sandbox-exec", "-p", profile, "bash", "-c", command]


@functools.lru_cache(maxsize=1)
def _bwrap_works() -> bool:
    try:
        result = subprocess.run(
            ["bwrap", "--unshare-net", "--ro-bind", "/", "/", "true"],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def detect(system: str, which: Callable[[str], Optional[str]] = shutil.which) -> Optional[str]:
    """Name the sandbox tool usable on this machine, or None."""
    if system == "Linux" and which("bwrap") and _bwrap_works():
        return "bwrap"
    if system == "Darwin" and which("sandbox-exec"):
        return "sandbox-exec"
    return None


def wrap(
    command: str,
    *,
    system: str,
    project_dir: str,
    home: str,
    environ: Mapping[str, str],
    exists: Callable[[str], bool] = os.path.isdir,
    extra_deny_read: Iterable[str] = (),
    extra_env_keep: Iterable[str] = (),
) -> str:
    """Return a shell string that runs `command` inside the jail with a scrubbed environment."""
    project = check_project_dir(project_dir, home)
    env = scrub_env(environ, extra_env_keep)

    if system == "Linux":
        ro = [p for p in (os.path.join(home, rel) for rel in LINUX_RO_CANDIDATES) if exists(p)]
        inner = linux_argv(command, project_dir=project, home=home, ro_paths=ro)
    elif system == "Darwin":
        tmp_dirs = ["/private/tmp", "/private/var/folders"]
        tmpdir = environ.get("TMPDIR")
        if tmpdir:
            tmp_dirs.append(os.path.realpath(tmpdir))
        profile = macos_profile(
            project_dir=project, home=home, tmp_dirs=tmp_dirs, extra_deny_read=extra_deny_read
        )
        inner = macos_argv(command, profile=profile)
    else:
        raise SandboxError(f"no sandbox available on {system or 'this platform'}")

    prefix = ["env", "-i"] + [f"{k}={v}" for k, v in sorted(env.items())]
    return shlex.join(prefix + inner)
