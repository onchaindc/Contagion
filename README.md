# Contagion

Taint-aware execution for Hermes Agent. The moment a session reads untrusted content (a web page, a scraped result, an MCP tool reply, a `curl`), its commands lose three things at the OS layer: network, secrets and writes outside the project. A prompt injection that hijacks the model still hits a wall, because the wall is not made of prompts.

## What it does

| Surface | Role |
|---|---|
| `post_tool_call` hook | Marks the session tainted when an untrusted-source tool finishes. |
| Terminal backend `contagion` | Local execution. While tainted, every `terminal`, `execute_code` and file-tool call runs in a jail (bubblewrap on Linux, `sandbox-exec` on macOS) with a scrubbed environment. |
| `pre_tool_call` hook | Escalates egress tools (`send_message`, email, upload and similar) to the human approval gate while tainted. If the backend is not selected, it escalates local write tools instead of failing silently. |
| `/contagion` command | `status` lists tainted sessions, `clear` restores privileges. |

The jail while tainted: no network, home directory hidden (Linux) or secret folders denied (macOS), writes only inside the project and temp, environment reduced to an allowlist (API keys never reach the command).

## Install

```bash
hermes plugins install <owner>/contagion --enable
hermes config set terminal.backend contagion
hermes config set terminal.cwd /path/to/a/project   # must not be your home directory
```

Linux needs `bwrap` (package `bubblewrap`). Ubuntu 24.04 may block unprivileged user namespaces; if `hermes doctor` reports the backend unavailable, enable them for bwrap. Requires a Hermes release newer than v0.22.5 (needed for `uses_host_filesystem`).

## Settings

`mode`: `enforce` (default), `monitor` (record only, useful for tuning), `off`. `untrusted_tool_patterns`, `egress_tool_patterns` and `extra_secret_paths` are editable in Settings > Plugins. Check your tool names with `hermes tools` and adjust the patterns to match.

## Honest limits

- Enforcement covers terminal, `execute_code` and file tools. Other channels (send_message, MCP writes, browser) are gated by approval prompts, which a distracted human can accept.
- Taint is per session and sticky until `/contagion clear`. Delegated subagents have their own task ids and do not inherit it yet.
- Inside the jail, `cd` and `export` do not persist between commands, and tools installed under your home directory may be missing (Linux mounts a short list read-only).
- macOS `sandbox-exec` is deprecated by Apple but still works. Windows is not supported.
- Source detection is pattern based. A tool that returns attacker text but matches no pattern will not taint the session.

## Demo (under 90 seconds)

```bash
cd demo && python3 listener.py &            # prints anything that arrives on :9999
python3 -m http.server 8000 &               # serves poisoned.html
echo "canary" > ~/contagion-canary.txt
```
Ask Hermes: "Summarize http://localhost:8000/poisoned.html". The page tries to make the agent post the canary file to the listener. With `mode: monitor` the listener receives it. With `mode: enforce` the command runs in the jail, the file is invisible, the request cannot leave, and `/contagion status` shows where the taint came from.

## Develop

```bash
python -m pytest -q tests
hermes plugins doctor . --ci
hermes plugins validate . --install-deps
```
