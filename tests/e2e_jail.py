#!/usr/bin/env python3
"""End-to-end check for the Contagion jail.

Run from the repo root:  python3 tests/e2e_jail.py

With Hermes importable this drives the real backend (ContagionProvider ->
ContagionEnvironment.execute). Without Hermes it says so and checks the jail
layer (sandbox.wrap) directly, so the OS behaviour is still verified while the
execute() wiring is reported as untested.

Exit codes: 0 all checks passed, 1 a check failed, 2 the backend is unavailable.
"""

from __future__ import annotations

import http.server
import importlib
import importlib.util
import os
import platform
import socketserver
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import sandbox  # noqa: E402
import taint  # noqa: E402

TASK = "e2e-tainted-task"
FAKE_KEY = "CT_E2E_FAKE_KEY"
FAKE_VALUE = "sk-fake-canary-value"
HOME_MARKER = ".contagion-e2e-home-marker"

results = []


def report(name, ok, detail):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name:<36} {detail}")


def load_environment_module():
    """Import the plugin as a package, so its relative imports resolve."""
    name = "contagion_e2e"
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "__init__.py"), submodule_search_locations=[ROOT]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return importlib.import_module(f"{name}.environment")


def normalize(result):
    """Accept whatever shape execute() returns: dict, object or plain string."""
    if isinstance(result, dict):
        out = result.get("output") or result.get("stdout") or ""
        code = result.get("exit_code", result.get("returncode", 0))
    elif hasattr(result, "output"):
        out = getattr(result, "output")
        code = getattr(result, "exit_code", 0)
    else:
        out, code = result, 0
    return str(out or ""), int(code or 0)


def shell(command):
    proc = subprocess.run(command, shell=True, capture_output=True, text=True)
    return (proc.stdout or "") + (proc.stderr or ""), proc.returncode


def start_local_server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"NET_OK")

        def log_message(self, *args):
            pass

    class Server(socketserver.TCPServer):
        allow_reuse_address = True

    server = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def fetch_command(port):
    return (
        "python3 -c \"import urllib.request;"
        f"print(urllib.request.urlopen('http://127.0.0.1:{port}/x', timeout=3).read().decode())\""
    )


def readonly_target():
    """A path that exists, is outside the project, and must stay read-only in the jail."""
    for base in ("/usr/local/share", "/etc"):
        if os.path.isdir(base):
            return os.path.join(base, "contagion-e2e-leak.txt")
    return os.path.join(os.path.sep, "contagion-e2e-leak.txt")


def main():
    home = os.path.expanduser("~")
    project = tempfile.mkdtemp(prefix="contagion-e2e-proj-")
    leak = readonly_target()
    marker = os.path.join(home, HOME_MARKER)
    server = start_local_server()
    port = server.server_address[1]

    previous = os.environ.get(FAKE_KEY)
    os.environ[FAKE_KEY] = FAKE_VALUE
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write("real home contents\n")

    print(f"platform : {platform.system()} {platform.release()}  python {sys.version.split()[0]}")
    print(f"project  : {project}")
    print(f"readonly : {leak}")
    print(f"listener : http://127.0.0.1:{port}/x\n")

    try:
        environment_module = load_environment_module()
    except ImportError as exc:
        environment_module = None
        print(f"note: Hermes is not importable here ({exc}).")
        print("note: checking the jail layer only; execute() wiring is UNTESTED.\n")

    if environment_module is not None:
        provider = environment_module.ContagionProvider()
        if not provider.is_available():
            print("SKIP: backend reports unavailable (bwrap missing or user namespaces blocked).")
            return 2
        env = provider.create_environment(cwd=project, timeout=60, task_id=TASK)

        def run_tainted(command):
            return normalize(env.execute(command))

        def run_clean(command):
            return normalize(env.execute(command))

        mode = "backend"
        taint.STATE.mark(TASK, taint.Source("web_extract", "untrusted page", "http://localhost:8000/poisoned.html"))
    else:
        base = dict(os.environ)

        def run_tainted(command):
            return shell(sandbox.wrap(
                command, system=platform.system(), project_dir=project,
                home=home, environ=base,
            ))

        def run_clean(command):
            return shell(command)

        mode = "jail layer"

    print(f"running with: {mode}\n")

    out, code = run_tainted("echo inside-ok > note.txt && cat note.txt")
    report("write inside project", code == 0 and "inside-ok" in out, f"exit={code} out={out.strip()[:60]!r}")

    out, code = run_tainted(f"echo pwn > {leak}")
    report("write outside project blocked", not os.path.exists(leak) and code != 0, f"exit={code} leaked={os.path.exists(leak)}")

    out, code = run_tainted(f'echo "[${FAKE_KEY}]"')
    report("secret env scrubbed", FAKE_VALUE not in out, f"out={out.strip()[:60]!r}")

    out, code = run_tainted(f"ls -A ~ 2>/dev/null | grep -c {HOME_MARKER} || true")
    report("real home hidden", out.strip() in ("", "0"), f"marker hits={out.strip()[:20]!r}")

    out, code = run_tainted(fetch_command(port))
    report("network blocked", "NET_OK" not in out and code != 0, f"exit={code} out={out.strip()[-60:]!r}")

    taint.STATE.clear(TASK)
    out, code = run_clean(fetch_command(port))
    report("untainted network works", "NET_OK" in out, f"exit={code} out={out.strip()[-60:]!r}")

    out, code = run_clean("ls -A ~ 2>/dev/null")
    report("untainted home visible", HOME_MARKER in out, f"marker listed={HOME_MARKER in out}")

    server.shutdown()
    if previous is None:
        os.environ.pop(FAKE_KEY, None)
    else:
        os.environ[FAKE_KEY] = previous
    for path in (marker, leak):
        try:
            os.remove(path)
        except OSError:
            pass

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} checks passed" + (f", {mode} only" if mode == "jail layer" else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
