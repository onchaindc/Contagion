"""The Contagion terminal backend: local execution that jails itself once the session is tainted."""

from __future__ import annotations

import logging
import os
import platform

from agent.terminal_env_provider import TerminalEnvironmentProvider
from tools.environments.local import LocalEnvironment

from . import sandbox, settings
from .taint import STATE

logger = logging.getLogger(__name__)


class ContagionEnvironment(LocalEnvironment):
    # File tools must run through execute() so the jail covers them too.
    uses_host_filesystem = False

    _ct_task_id = "default"
    _ct_project = ""

    def execute(self, command, *args, **kwargs):
        if settings.get("mode", "enforce") == "enforce" and STATE.is_tainted(self._ct_task_id):
            try:
                command = sandbox.wrap(
                    command,
                    system=platform.system(),
                    project_dir=self._ct_project or os.getcwd(),
                    home=os.path.expanduser("~"),
                    environ=os.environ,
                    extra_deny_read=tuple(settings.get("extra_secret_paths", []) or []),
                )
            except sandbox.SandboxError as exc:
                logger.warning("contagion: failing closed: %s", exc)
                return {"output": f"[contagion] {exc}", "exit_code": 126}
        return super().execute(command, *args, **kwargs)


class ContagionProvider(TerminalEnvironmentProvider):
    name = "contagion"
    display_name = "Contagion (taint-aware local)"
    is_remote = False
    is_container = False

    @property
    def description(self):
        return "Runs commands locally. After the session reads untrusted content, commands lose network, secrets and out-of-project writes."

    def is_available(self):
        return sandbox.detect(platform.system()) is not None

    def create_environment(self, *, cwd, timeout, task_id="default", image=None, container_config=None, **kwargs):
        env = ContagionEnvironment(cwd=cwd, timeout=timeout)
        env._ct_task_id = task_id or "default"
        env._ct_project = os.path.realpath(cwd) if cwd and os.path.isabs(cwd) and os.path.isdir(cwd) else os.getcwd()
        return env
