"""Taint state and tool classification. Pure Python, no Hermes imports."""

from __future__ import annotations

import fnmatch
import re
import threading
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

DEFAULT_SOURCE_PATTERNS = ["web_*", "browser_*", "mcp__*", "*fetch*", "*crawl*", "*scrape*"]
DEFAULT_SOURCE_EXCLUDE = ["search_files", "session_search", "skill_*", "memory_*"]
DEFAULT_EGRESS_PATTERNS = ["send_message*", "*email*", "*upload*", "*webhook*", "*post_*", "*tweet*"]
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
    """Return a Source if this finished tool call brought untrusted content into the session."""
    patterns = list(patterns) if patterns else DEFAULT_SOURCE_PATTERNS
    exclude = list(exclude) if exclude else DEFAULT_SOURCE_EXCLUDE
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
        with self._lock:
            return bool(self._records.get(task_id or "default"))

    def records(self, task_id: str) -> List[TaintRecord]:
        with self._lock:
            return list(self._records.get(task_id or "default", []))

    def summary(self, task_id: str) -> str:
        records = self.records(task_id)
        if not records:
            return ""
        first = records[0]
        extra = f" (+{len(records) - 1} more)" if len(records) > 1 else ""
        detail = f": {first.detail}" if first.detail else ""
        return f"{first.tool}{detail}{extra}"

    def clear(self, task_id: Optional[str] = None) -> int:
        """Clear one task, or every task when task_id is None. Returns how many were cleared."""
        with self._lock:
            if task_id is None:
                count = len(self._records)
                self._records.clear()
                return count
            return 1 if self._records.pop(task_id, None) else 0

    def snapshot(self) -> Dict[str, List[TaintRecord]]:
        with self._lock:
            return {k: list(v) for k, v in self._records.items()}


STATE = TaintState()
