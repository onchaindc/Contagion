"""Fail-soft access to this plugin's settings."""

from __future__ import annotations

_ctx = None


def bind(ctx) -> None:
    global _ctx
    _ctx = ctx


def get(key: str, default=None):
    if _ctx is None:
        return default
    try:
        value = _ctx.get_config(key, default=default)
        return default if value is None else value
    except Exception:
        return default
