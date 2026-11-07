# Contagion verification pass 1 (partial: Steps 0 to 3)

Status: partial report written after Step 3. Step 4 (end to end) and Step 5 (fixes) follow in the committed version.

## Environment

- Hermes Agent `vgit.9f23c43.dirty (2026.9.24)` from `github.com/NousResearch/hermes-agent` at `9f23c4311a4c`, newer than v0.22.5. Python 3.14.9.
- OS: Ubuntu 22.04.5 LTS, kernel 6.19.14, x86_64. Runs as root with `HOME=/home/daytona`.
- bubblewrap 0.6.1 installed. `bwrap --unshare-net --ro-bind / / true` exits 0, so the jail is available here.
- Install deviation: the sandbox root filesystem is 3.9G. `install.sh` reached `pm install` and died with ENOSPC (ffmpeg, node, npm, pnpm). To get a working CLI I removed non-runtime trees from the checkout (tests, apps, website, contributors, evals, optional-skills, docs), installed the base dependency set with `uv pip install -e .` into `.venv`, and pointed `hermes` at `.venv/bin/hermes`. The pm-managed launcher needs a committed dependency generation and refuses to boot without it. Consequence: `hermes` prints "install out of sync (ffmpeg, node, npm, pnpm)" on every run and retries a failing source-update completion, then backs off. Plugin loading, doctor and validate are unaffected.

## Results

| Step | Pass/Fail | Evidence (one line) |
|---|---|---|
| 0 environment | partial | Hermes 2026.9.24 installed and running; bwrap works; pm tool set incomplete for disk reasons |
| 1 unit tests | pass | `18 passed in 0.03s` after restoring `tests/` layout |
| 2 doctor | pass | `OK: runtime discovery, manifest parsing, import, and registration passed`, 2 hooks |
| 2 validate | pass | `Validation passed.` with 3 warnings (bytecode, hidden_div, private core import) |
| 2 enable | pass | `✓ Plugin contagion enabled.` |
| 3 source verification | pass | A to H answered below; one critical bug confirmed by live probe |
| 4 end to end | pending | see final report |
| 5 fixes | pending | see final report |

Trimmed Step 2 output:

```
Plugin Doctor: /home/daytona/codebase
  manifest: contagion 0.1.0 (standalone)
  OK: runtime discovery, manifest parsing, import, and registration passed
  registrations: 0 tool(s), 2 hook(s)

✓ manifest — plugin.yaml parses
✓ config schema — shape valid
✓ loadable — entry: __init__.py
✓ capability probe — register() ran in isolation
✓ declared hooks — matches registrations
✓ security scan — caution
⚠ security scan caution: bytecode_file (...), hidden_div (poisoned.html:7)
⚠ private core imports (...): environment.py:10 tools.environments.local.LocalEnvironment
Validation passed.
```

## Answers to A to H

A. `LocalEnvironment.__init__(cwd="", timeout=60, env=None, persistent=True)` (`tools/environments/local.py:1078`). There is no persistent shell: every `execute()` spawns a fresh `bash -c` and cwd survives only via a stdout marker (`local.py:1050-1054`, `tools/environments/base_session_env.py:163-205`, `local.py:1221`). `execute()` is `BaseEnvironment.execute(command, cwd="", *, timeout=None, stdin_data=None, ...)` returning `{"output": str, "returncode": int}` plus `cwd` (`tools/environments/base.py:669-679`). Wrapping the command as `env -i ... bwrap ... bash -c <cmd>` is compatible because the base wrapper `eval`s the whole string in the outer bash (`base_session_env.py:196`), so quoting survives, and the cwd marker is emitted outside the jail. Caveat: `cd`/`export` inside the jail cannot persist across calls, as the README already says.

B. `BaseEnvironment.uses_host_filesystem` defaults to False (`tools/environments/base.py:282`) and is read through `env_uses_host_filesystem()` (`base.py:251-255`). Every file tool is a `ShellFileOperations` and every operation goes through `self.env.execute(...)` (`tools/file_operations.py:207-239`, `1516+`); the flag only disables host-side shortcuts: local path normalization and the in-process filename walk (`tools/file_operations_search.py:64-74`, `535-545`), and host-absolute V4A header rewriting and `os.stat` probes (`tools/file_tools.py:136-144`, `file_operations.py:914`). So setting it False is the right contract, and it does put read_file, write_file, patch and search_files behind `execute()`.

C. They differ. Hooks receive the agent's effective task id (`model_tools.py:910` and the `_CallIds` wire contract at `model_tools.py:658-666`; generated per turn at `agent/turn_context.py:622`), and the terminal tool receives that same id (`model_tools.py:841-854`). But `terminal_tool` immediately maps it to a container key with `_resolve_container_task_id()` (`tools/terminal_tool.py:517-570`, `1207`), and file tools do the same (`tools/file_tools.py:326`, `tools/terminal_tool_lifecycle.py:216`). In the CLI with no session key that key is literally `default`. Live probe in this install: hook task id `40661fa1-...` was tainted by `web_extract`, the environment reported `_ct_task_id: default`, `STATE.is_tainted("default") == False`, and the next terminal command ran unjailed. This is the critical bug: the jail never triggers in the CLI. Fix is in Step 5.

D. Real registry names (78 tools from the live registry): sources are `web_search`, `web_extract`, `browser_cdp`, `browser_dialog`, `browser_exec`, `browser_vault`, `x_search` and MCP tools named `mcp__<server>__<tool>` (`tools/mcp_tool_schema.py:172-183`). There is no tool containing `fetch`, `crawl` or `scrape`, so `*fetch*`, `*crawl*`, `*scrape*` match nothing. `mcp__*` is correct. Egress: `send_message` is deliberately not registered as a model tool (`tools/send_message_tool.py:28-30`), and no tool matches `*email*`, `*upload*`, `*webhook*`, `*post_*` or `*tweet*`. Real messaging tools are `discord`, `discord_admin`, `feishu_drive_*`, `yb_send_dm`, `yb_send_sticker`, `react_to_message`. Local tools (`search_files`, `read_file`, `write_file`, `patch`, `session_search`, `memory`, `skills_list`) match no source pattern, and `search_files`/`session_search`/`skill_*`/`memory_*` stay excluded. Patterns updated in Step 5.

E. Yes. `kind` defaults to `standalone` (`hermes_cli/plugins_manifest.py:377`, `532-547`) and only changes discovery for special kinds: `exclusive` and `model-provider` skip the general manager, bundled `backend` auto-loads, bundled `platform` defers (`hermes_cli/plugins_discovery.py:302-340`). A `standalone` plugin that is enabled goes through normal load and its `PluginContext` exposes hooks, commands and provider registration alike (`hermes_cli/plugins_context.py:741`, `456`, `858`). Proven in this install: plugin enabled, `plugin_backend_names()` returned `['contagion']`, `_plugin_commands` contained `contagion`, and two hooks fired. No `kind: backend` is required, and hooks and commands would still load if it were set, since kind only gates discovery.

F. `{"action": "approve", "message": ...}` is resolved by `_resolve_block_from_details()` (`hermes_cli/plugins.py:1167-1196`), which calls `request_tool_approval()` (`tools/approval.py:1211-1240`) and routes into the same human gate as dangerous shell commands. In an interactive CLI the prompt appears; in the gateway it becomes a pending approval in the chat; in any non-interactive context without an approval bridge it fails closed with `BLOCKED: Tool '<name>' requires approval ... but no interactive user or gateway is present`. If the gate errors, the call is blocked. So the prompt is real, and the default posture is safe.

G. `ctx.get_config(key, default)` reads only `plugins.entries.<id>.settings.<key>`, falling back to the legacy `config` subtree and then to the passed default (`hermes_cli/plugins_context.py:73-83`). It does not merge `config_schema` defaults. Probe: with no user settings, `settings.get("mode", "FALLBACK")` returned `FALLBACK` even though the schema default is `enforce`. User overrides do work, and list values come back as lists. Because callers pass their own fallbacks, the plugin behaves, but schema defaults are decorative and a user who sets an empty list cannot disable pattern matching (`if patterns else DEFAULT` in `taint.classify_source`). Step 5 tightens this.

H. Delegated subagents get a new id: `subagent-<index>-<hex>` (`tools/delegate_tool_child_run.py:820`) passed as the child turn's `task_id` (`delegate_tool_child_run.py:937`), and the child registers a container alias to the parent (`delegate_tool_child_run.py:833`, `tools/terminal_tool.py:373-390`). Taint is stored per hook task id, so a parent taint does not taint the child and a child taint does not taint the parent. Because the jail keys on the container key instead, parent and child collapse onto the same env in the CLI, so after the Step 5 fix a tainted parent would jail its children's commands. Documented, not fixed, per instructions.

## Changes made

- `tests/`, `demo/`: restored the directory layout (the git upload had flattened it) so `pytest tests` and `demo/listener.py` exist. Commit `50c5ffa`.
- `REPORT.md`: this file. Commit follows.

## Still broken or unknown

- The jail never triggers in the CLI because of the task id mismatch (C). Being fixed in Step 5.
- `execute()` returns `{"exit_code": 126}` on a fail-closed error, but Hermes reads `returncode` (`file_operations.py:238`, `base.py:679`). Being fixed in Step 5.
- No model provider credentials exist in this sandbox, so a real `hermes chat -q` turn may be impossible. Step 4 will use the direct tool path and say exactly what could not run.
- `install.sh` cannot complete on a 3.9G disk, so `hermes doctor` will keep reporting missing ffmpeg/node/npm/pnpm.

## Commands to reproduce

```bash
git switch verify-pass-1
python -m pytest -q tests
ln -sfn "$PWD" ~/.hermes/plugins/contagion
hermes plugins doctor . --ci
hermes plugins validate . --install-deps
hermes plugins enable contagion
bwrap --unshare-net --ro-bind / / true
```

## Trimmed logs

```
$ bwrap --unshare-net --ro-bind / / true ; echo $?
0

$ python -m pytest -q tests
..................                                                       [100%]
18 passed in 0.03s

$ hermes --version
Hermes Agent vgit.9f23c43.dirty (2026.9.24) · stable · local 9f23c4311a4c

$ hermes plugins doctor . --ci
  manifest: contagion 0.1.0 (standalone)
  OK: runtime discovery, manifest parsing, import, and registration passed
  registrations: 0 tool(s), 2 hook(s)

$ hermes plugins enable contagion
✓ Plugin contagion enabled.
```

Probe transcript for C:

```
PLUGIN_BACKENDS: ['contagion']
TASK_ID: 87afad0b-c029-4894-9e26-bf4e2e3f7fb2
OUT: {"output": "hello-from-probe", "exit_code": 0, "error": null}
ENVKEY: default ct_task_id: default class: ContagionEnvironment
contagion: session 40661fa1-190e-48c9-86ef-575ac2204c87 tainted by web_extract
hook tid tainted: True env key default tainted: False
OUT2: {"output": "second", "exit_code": 0, "error": null}
```
