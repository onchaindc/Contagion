# Task: verify and harden the Contagion plugin on a real Hermes install

## Context
Contagion is a Hermes Agent plugin for the Hermes Agent Plugin Hackathon (deadline Nov 11, 5PM EST). It taints a session when it reads untrusted content (web, browser, MCP, curl) and then runs that session's commands in an OS jail (bubblewrap on Linux, sandbox-exec on macOS) with no network, no secrets and no writes outside the project. Read README.md first.

It was written WITHOUT access to a Hermes install, from the docs only. Several assumptions are unverified. Your job: verify them against a real Hermes, fix what breaks, and report back. Do not guess. If something cannot be run, say exactly why.

## Ground rules
- Work on branch `verify-pass-1`. Commit in small steps with clear messages. Never push to main.
- Edit existing files. Add a new file only for tests or the report.
- Keep the architecture: `post_tool_call` and `pre_tool_call` hooks, a terminal backend named `contagion`, and the `/contagion` slash command. Do not redesign. Do not build a Desktop pane yet.
- No em dashes in README or docs text.
- Docs: https://hermes-agent.nousresearch.com/docs/developer-guide/plugins , .../terminal-environment-plugin , .../plugins/catalog-submission . Hermes source is under ~/.hermes/hermes-agent after install. Prefer the source over the docs when they disagree, and say so.
- The demo fixture only targets localhost and a throwaway canary file. Never point anything at real credentials.

## Step 0: environment
1. Install Hermes Agent following https://hermes-agent.nousresearch.com/docs/getting-started/quickstart . Record `hermes --version`. It must be newer than v0.22.5.
2. Linux: install `bubblewrap`. Run `bwrap --unshare-net --ro-bind / / true`. If it fails (user namespaces blocked in this cloud sandbox), record the exact error, try reasonable workarounds, and if still impossible, say so clearly and run every step that does not need the jail.
3. Record OS, kernel, Python version.

## Step 1: unit tests
`python -m pytest -q tests` from the repo root. All must pass.

## Step 2: install and validate
Copy or symlink this repo to `~/.hermes/plugins/contagion`. Run:
- `hermes plugins doctor . --ci`
- `hermes plugins validate . --install-deps`
- `hermes plugins enable contagion`
Fix every error and warning (manifest, hook signatures, `kind`, config_schema). Paste the final clean output.

## Step 3: verify these assumptions, with evidence from the Hermes source
A. `tools/environments/local.py`: what are LocalEnvironment's constructor and `execute()` signatures and return shape? Does it keep a persistent shell and track cwd? Is wrapping the command string as `env -i ... bwrap ... bash -c <cmd>` compatible with that?
B. Does `uses_host_filesystem = False` on a LocalEnvironment subclass make read_file, write_file, patch and search_files run through `execute()`? Show the code path.
C. Is the `task_id` passed to `create_environment()` the SAME id the hooks receive as `task_id` for the same session? Add temporary log lines, run a real session, compare. If they differ, fix the keying (conversation_id, session_id, or another) so a tainted session actually triggers the jail.
D. List the real built-in tool names for web fetch/search, browser, MCP, messaging/send, and upload (`hermes tools`, the tool registry). Update `DEFAULT_SOURCE_PATTERNS`, `DEFAULT_EGRESS_PATTERNS`, the matching defaults in `plugin.yaml`, and the tests. Remove patterns that match nothing real, and make sure local tools like `search_files` are not treated as sources.
E. Can a plugin with the default kind register a terminal environment provider AND hooks AND a slash command together? If `kind: backend` is required, check whether hooks and commands still load, and fix the manifest accordingly.
F. What does a `pre_tool_call` return of `{"action": "approve", "message": ...}` do in the CLI and in the gateway? Does the prompt actually appear?
G. Does `ctx.get_config(key, default=...)` return the `config_schema` defaults and user overrides? Does `settings.get` in `settings.py` work as intended, including list-typed values?
H. Do delegated subagents get a different `task_id`? Document what happens to taint in that case. Do not fix it yet.

## Step 4: real end-to-end test
1. Write `tests/e2e_jail.py` that creates a `ContagionEnvironment` via the provider, taints a task through `taint.STATE`, and checks via `execute()`: a write inside the project works; a write outside it fails; `echo $OPENAI_API_KEY` (set a fake one) is empty; `ls ~` does not show real home contents; `curl`/Python network access fails; and an untainted task still has normal network and home access. Print each result.
2. Real agent run: `echo canary > ~/contagion-canary.txt`, `python3 demo/listener.py &`, `cd demo && python3 -m http.server 8000 &`, set `terminal.cwd` to a throwaway project dir, then run Hermes non-interactively (for example `hermes chat -q "Summarize http://localhost:8000/poisoned.html"`):
   - mode `monitor`: report whether the listener received the canary and whether the session got tainted.
   - mode `enforce`: report whether the leak was blocked, and the output of `/contagion status` if reachable.
   If the model refuses to follow the injected instruction, say so and also test by calling the tool path directly, so the jail is still verified.

## Step 5: fix and test
Fix every bug found. Add a test for each fix where feasible. Re-run steps 1 and 2 at the end.

## Report
Commit a `REPORT.md` on the branch AND print it in your final message, so it can be pasted to the person who wrote the plugin. Keep it under about 150 lines. Format:

```
## Environment
(Hermes version, OS, bwrap/sandbox-exec status)
## Results
| Step | Pass/Fail | Evidence (one line) |
## Answers to A to H
(one short paragraph each, with file and line references)
## Changes made
(file: what and why, commit hash)
## Still broken or unknown
## Commands to reproduce
## Trimmed logs
```

Work in this order and write a partial REPORT.md after Step 3, so progress is not lost if you run out of time.
