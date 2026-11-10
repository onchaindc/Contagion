"""Fail-soft access to this plugin's settings.

``ctx.get_config`` only returns values written under
``plugins.entries.contagion.settings`` (plus the legacy ``config`` subtree); it does not merge
the ``config_schema`` defaults from plugin.yaml. Verified against Hermes 2026.9.24
(hermes_cli/plugins_context.py:73), so the defaults live here as well and stay in sync with
plugin.yaml. List values default to a fresh copy so callers cannot mutate the module state.
"""

from __future__ import annotations

from . import taint

_ctx = None

DEFAULTS = {
    "mode": "enforce",
    "untrusted_tool_patterns": list(taint.DEFAULT_SOURCE_PATTERNS),
    "egress_tool_patterns": list(taint.DEFAULT_EGRESS_PATTERNS),
    "extra_secret_paths": [],
}


def bind(ctx) -> None:
    global _ctx
    _ctx = ctx


def get(key: str, default=None):
    value = None
    if _ctx is not None:
        try:
            value = _ctx.get_config(key, default=None)
        except Exception:
            value = None
    if key == "mode" and value is not None and value not in ("enforce", "monitor", "off"):
        # `hermes config set` accepts any value; an unknown mode must not silently
        # disable both the jail and the approval gates.
        return "enforce"
    if value is not None:
        return value
    if default is not None:
        return default
    fallback = DEFAULTS.get(key)
    if isinstance(fallback, list):
        return list(fallback)
    return fallback
