"""Taint state and tool classification. Pure Python, no Hermes imports."""

from __future__ import annotations

import fnmatch
import re
import threading
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

# Verified against the live Hermes tool registry (78 built-in tools).
# Sources: web_search/web_extract are the only web tools, browser_* covers cdp/dialog/exec/vault,
# mcp__<server>__<tool> is the real MCP naming (tools/mcp_tool_schema.py:172), x_search reads
# remote posts. There is no *fetch*, *crawl* or *scrape* tool, so those globs matched nothing.
DEFAULT_SOURCE_PATTERNS = ["web_search", "web_extract", "browser_*", "mcp__*", "x_search"]
DEFAULT_SOURCE_EXCLUDE = ["search_files", "session_search", "skill_*", "memory_*"]
# Egress: send_message is deliberately not an agent-callable tool (tools/send_message_tool.py:28),
# and no tool matches *email*, *upload*, *webhook*, *post_* or *tweet*. These are the real
# outbound tools plus MCP (which can carry writes).
DEFAULT_EGRESS_PATTERNS = ["discord*", "feishu_drive_*", "yb_send_*", "react_to_message", "mcp__*"]
LOCAL_EXEC_TOOLS = ("terminal", "execute_code", "write_file", "patch")

_FETCH_COMMAND_RE = re.compile(r"(^|[\s;&|(])(curl|wget|http|https|gh\s+api|git\s+clone)\b")
_CODE_FETCH_RE = re.compile(r"(requests\.(get|post)|urllib|httpx|aiohttp|fetch\()")

MAX_RECORDS_PER_TASK = 50


@dataclass(frozen=True)
class Source:
    tool: str
    reason: str
    detail: str


@dataclass(frozen=True)
class TaintRecord:
    tool: str
    reason: str
    detail: str
    at: float


def matches(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def _detail(args) -> str:
    if not isinstance(args, dict):
        return ""
    for key in ("url", "urls", "query", "command", "q", "path"):
        value = args.get(key)
        if value:
            return (value if isinstance(value, str) else str(value))[:120]
    return ""


def classify_source(tool_name, args, patterns=None, exclude=None) -> Optional[Source]:
    """Return a Source if this finished tool call brought untrusted content into the session.

    ``patterns=[]`` really means no pattern-based sources, so an operator can turn the glob
    tier off while keeping the terminal/execute_code fetch detection.
    """
    patterns = list(DEFAULT_SOURCE_PATTERNS if patterns is None else patterns)
    exclude = list(DEFAULT_SOURCE_EXCLUDE if exclude is None else exclude)
    args = args if isinstance(args, dict) else {}

    if tool_name == "terminal":
        command = str(args.get("command") or "")
        if _FETCH_COMMAND_RE.search(command):
            return Source(tool_name, "network fetch in terminal", command[:120])
        return None
    if tool_name == "execute_code":
        code = str(args.get("code") or "")
        if _CODE_FETCH_RE.search(code):
            return Source(tool_name, "network fetch in code", code[:120].replace("\n", " "))
        return None
    if matches(tool_name, exclude):
        return None
    if matches(tool_name, patterns):
        return Source(tool_name, "untrusted tool output", _detail(args))
    return None


class TaintState:
    """Thread-safe record of which tasks have read untrusted content."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Dict[str, List[TaintRecord]] = {}
        # Hook task ids (per turn, e.g. a uuid) and the environment key Hermes derives for
        # them (usually "default" in the CLI) are different namespaces. link() records the
        # mapping so the jailed environment sees the session's taint.
        self._aliases: Dict[str, str] = {}

    def link(self, task_id: str, env_key: Optional[str]) -> None:
        """Map an environment key to the task ids whose taint must apply to it."""
        if not task_id or not env_key or env_key == task_id:
            return
        with self._lock:
            self._aliases[task_id] = env_key

    def _keys_for(self, task_id: str) -> List[str]:
        key = task_id or "default"
        with self._lock:
            aliases = list(self._aliases.items())
        return [key, *(src for src, target in aliases if target == key)]

    def mark(self, task_id: str, source: Source) -> bool:
        """Record taint. Returns True when this call is the one that tainted the task."""
        task_id = task_id or "default"
        with self._lock:
            records = self._records.setdefault(task_id, [])
            first = not records
            records.append(TaintRecord(source.tool, source.reason, source.detail, time.time()))
            if len(records) > MAX_RECORDS_PER_TASK:
                del records[0]
            return first

    def is_tainted(self, task_id: str) -> bool:
        keys = self._keys_for(task_id)
        with self._lock:
            return any(self._records.get(key) for key in keys)

    def records(self, task_id: str) -> List[TaintRecord]:
        keys = self._keys_for(task_id)
        with self._lock:
            found = [record for key in keys for record in self._records.get(key, [])]
        return sorted(found, key=lambda record: record.at)

    def summary(self, task_id: str) -> str:
        records = self.records(task_id)
        if not records:
            return ""
        first = records[0]
        extra = f" (+{len(records) - 1} more)" if len(records) > 1 else ""
        detail = f": {first.detail}" if first.detail else ""
        return f"{first.tool}{detail}{extra}"

    def clear(self, task_id: Optional[str] = None) -> int:
        """Clear one task (and any env key aliased to it), or everything when task_id is None."""
        if task_id is None:
            with self._lock:
                count = len(self._records)
                self._records.clear()
                self._aliases.clear()
                return count
        keys = self._keys_for(task_id)
        with self._lock:
            cleared = sum(1 for key in keys if self._records.pop(key, None))
            for src, target in list(self._aliases.items()):
                if src == task_id or target == task_id:
                    self._aliases.pop(src, None)
            return cleared

    def snapshot(self) -> Dict[str, List[TaintRecord]]:
        with self._lock:
            return {k: list(v) for k, v in self._records.items()}


STATE = TaintState()
