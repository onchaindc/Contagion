"""End-to-end jail check for the Contagion backend.

Run with Hermes' own interpreter, because environment.py imports Hermes internals:

    HERMES_HOME=~/.hermes ~/.hermes/hermes-agent/.venv/bin/python tests/e2e_jail.py

It creates a ContagionEnvironment through the real provider, taints a task through the same
post_tool_call handler Hermes calls (with a per-turn uuid task id, so the env-key bridge is
exercised), and checks what a tainted command can still do. Requires bwrap on Linux.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
RESULTS: list[tuple[str, bool, str]] = []


def load_plugin():
    sys.path.insert(0, str(ROOT.parent))
    spec = importlib.util.spec_from_file_location(
        "contagion", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["contagion"] = module
    spec.loader.exec_module(module)
    return module


def check(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"ok\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def start_server():
    server = HTTPServer(("127.0.0.1", 8765), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> int:
    plugin = load_plugin()
    from contagion.environment import ContagionProvider

    if not ContagionProvider().is_available():
        print("SKIP: no usable sandbox on this host (bwrap / sandbox-exec missing or blocked)")
        return 77

    home = os.path.expanduser("~")
    os.environ["OPENAI_API_KEY"] = "sk-e2e-fake-do-not-use"
    home_marker = pathlib.Path(home) / "contagion-e2e-marker"
    home_marker.write_text("real home file\n", encoding="utf-8")
    outside = pathlib.Path("/opt/contagion-e2e-outside.txt")
    if outside.exists():
        outside.unlink()

    server = start_server()
    project = tempfile.mkdtemp(prefix="contagion-e2e-", dir="/home/daytona")
    provider = ContagionProvider()
    env = provider.create_environment(cwd=project, timeout=60, task_id="default")

    turn_id = str(uuid.uuid4())
    plugin._post_tool_call("web_extract", {"url": "http://127.0.0.1:8765/poisoned.html"}, "{}", turn_id)
    tainted_env_key = "default"
    check("taint reaches the environment key",
          plugin.STATE.is_tainted(tainted_env_key),
          f"hook task {turn_id[:8]} -> env key {tainted_env_key}")

    def run(command: str) -> tuple[int, str]:
        result = env.execute(command)
        return int(result.get("returncode", 0)), (result.get("output") or "")

    code, out = run(f"echo inside > {project}/inside.txt && cat {project}/inside.txt")
    check("write inside the project", code == 0 and "inside" in out, f"rc={code} out={out.strip()!r}")

    code, out = run(f"echo nope > {outside}")
    check("write outside the project blocked", code != 0 and not outside.exists(), f"rc={code} out={out.strip()[:80]!r}")

    code, out = run("echo \"key=[$OPENAI_API_KEY]\"")
    check("fake API key not in the jail environment", "sk-e2e-fake" not in out, f"rc={code} out={out.strip()!r}")

    code, out = run(f"test -f ~/{home_marker.name} && echo PRESENT || echo ABSENT")
    check("real home contents hidden", code == 0 and "ABSENT" in out, f"rc={code} out={out.strip()!r}")

    code, out = run("curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/")
    check("network blocked in the jail", code != 0, f"rc={code} out={out.strip()!r}")

    code, out = run("python3 -c \"import socket;s=socket.create_connection(('127.0.0.1',8765),3)\"")
    check("python network blocked in the jail", code != 0, f"rc={code} out={out.strip()[:80]!r}")

    # A different, untainted task keeps normal privileges.
    clean = provider.create_environment(cwd=project, timeout=60, task_id="untainted-task")
    result = clean.execute(f"test -f ~/{home_marker.name} && echo PRESENT || echo ABSENT")
    clean_out = result.get("output") or ""
    check("untainted task sees the real home", "PRESENT" in clean_out,
          f"rc={result.get('returncode')} out={clean_out.strip()!r}")

    result = clean.execute("curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/")
    check("untainted task has network", result.get("returncode") == 0 and "200" in (result.get("output") or ""),
          f"rc={result.get('returncode')} out={(result.get('output') or '').strip()!r}")

    result = clean.execute("echo unjailed")
    check("untainted task runs unjailed", result.get("returncode") == 0,
          f"rc={result.get('returncode')} out={(result.get('output') or '').strip()!r}")

    plugin.STATE.clear()
    home_marker.unlink(missing_ok=True)
    if outside.exists():
        outside.unlink()
    server.shutdown()

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
