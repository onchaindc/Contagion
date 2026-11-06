"""Contagion: taint-aware execution for Hermes Agent."""

from __future__ import annotations

import json
import logging
import time

from . import settings, taint
from .taint import STATE

logger = logging.getLogger(__name__)


def _log_event(kind: str, **data) -> None:
    try:
        from plugins.plugin_storage import plugin_data_dir

        path = plugin_data_dir("contagion") / "events.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": time.time(), "kind": kind, **data}) + "\n")
    except Exception:
        pass


def _backend_is_contagion() -> bool:
    try:
        from hermes_cli.config import load_config_readonly

        cfg = load_config_readonly() or {}
        return (cfg.get("terminal") or {}).get("backend") == "contagion"
    except Exception:
        return True  # cannot tell, so do not nag


def _post_tool_call(tool_name, args, result, task_id, **kwargs):
    if settings.get("mode", "enforce") == "off":
        return
    source = taint.classify_source(
        tool_name, args, patterns=settings.get("untrusted_tool_patterns", None)
    )
    if source is None:
        return
    if STATE.mark(task_id, source):
        logger.warning("contagion: session %s tainted by %s", task_id, source.tool)
    _log_event("taint", task_id=task_id, tool=source.tool, reason=source.reason, detail=source.detail)


def _pre_tool_call(tool_name, args, task_id, **kwargs):
    mode = settings.get("mode", "enforce")
    if mode == "off" or not STATE.is_tainted(task_id):
        return None
    why = STATE.summary(task_id)
    egress = settings.get("egress_tool_patterns", None) or taint.DEFAULT_EGRESS_PATTERNS
    if taint.matches(tool_name, egress):
        _log_event("egress_gate", task_id=task_id, tool=tool_name, mode=mode)
        if mode == "enforce":
            return {
                "action": "approve",
                "message": f"Contagion: this session read untrusted content ({why}). Allow {tool_name}?",
            }
        return None
    if mode == "enforce" and tool_name in taint.LOCAL_EXEC_TOOLS and not _backend_is_contagion():
        return {
            "action": "approve",
            "message": (
                f"Contagion: session tainted by {why}, but terminal.backend is not 'contagion', "
                f"so commands cannot be jailed. Allow {tool_name}?"
            ),
        }
    return None


def _command(raw: str) -> str:
    parts = (raw or "").strip().split()
    action = parts[0].lower() if parts else "status"
    if action == "clear":
        count = STATE.clear()
        _log_event("clear", cleared=count)
        return f"Contagion: cleared taint on {count} task(s). Privileges restored."
    if action == "mode" and len(parts) == 2 and parts[1] in ("enforce", "monitor", "off"):
        return "Contagion: set the mode in Settings > Plugins > contagion (or plugins.entries.contagion.settings.mode)."
    snap = STATE.snapshot()
    if not snap:
        return "Contagion: no tainted sessions."
    lines = ["Contagion: tainted sessions"]
    for task_id, records in snap.items():
        first = records[0]
        lines.append(f"- {task_id}: {first.tool} ({first.detail or first.reason}), {len(records)} event(s)")
    lines.append("Run /contagion clear to restore privileges.")
    return "\n".join(lines)


def register(ctx):
    settings.bind(ctx)
    from .environment import ContagionProvider

    ctx.register_terminal_environment_provider(ContagionProvider())
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
    ctx.register_command(
        "contagion",
        _command,
        description="Show tainted sessions, or /contagion clear to restore privileges",
    )
