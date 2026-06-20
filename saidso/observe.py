"""Terminal observability for the firewall — zero dependencies.

saidso emits one structured log event per decision on the ``saidso`` logger
(``saidso_event`` = ``pass`` | ``block``, with ``saidso_action`` / ``saidso_args``).
This module turns that stream into something you can actually read at a glance:

    from saidso.observe import enable_pretty_logging, summary, EventRecorder

    enable_pretty_logging()        # colored ✓/✗ live stream on stderr
    rec = EventRecorder().attach() # also remember every event for a final summary
    ...
    print(summary(audit, rec))     # end-of-run table: passed / blocked counts + rows

No ``rich`` / ``colorama`` — colors are raw ANSI, auto-disabled when the output
isn't a TTY (or when ``NO_COLOR`` is set), and Windows virtual-terminal mode is
enabled on demand so it looks right in PowerShell / cmd too.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import List, Optional

_LOGGER_NAME = "saidso"

_ANSI = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "cyan": "\033[36m", "grey": "\033[90m",
}

# event -> (icon, color, label)
_STYLE = {
    "pass": ("✓", "green", "grounded"),
    "block": ("✗", "red", "blocked"),
    "error": ("⚠", "yellow", "error"),
}


def _supports_color(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


def _enable_windows_ansi() -> None:
    """Turn on ANSI escape processing for legacy Windows consoles (no-op elsewhere)."""
    if sys.platform != "win32":
        return
    try:  # ENABLE_VIRTUAL_TERMINAL_PROCESSING on the stdout/stderr handles
        import ctypes

        kernel32 = ctypes.windll.kernel32
        for handle_id in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
            handle = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


class PrettyFormatter(logging.Formatter):
    """Render a saidso log record as ``HH:MM:SS ✓ grounded  action  arg1, arg2``."""

    def __init__(self, color: bool = True) -> None:
        super().__init__()
        self.color = color

    def _c(self, text: str, *names: str) -> str:
        if not self.color:
            return text
        return "".join(_ANSI[n] for n in names) + text + _ANSI["reset"]

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "saidso_event", None)
        if event is None and record.levelno >= logging.WARNING:
            event = "error"
        ts = self._c(time.strftime("%H:%M:%S"), "grey")
        msg = record.getMessage()

        if event in _STYLE:
            icon, color, label = _STYLE[event]
            action = getattr(record, "saidso_action", "")
            args = getattr(record, "saidso_args", None)
            detail = ", ".join(args) if args else msg
            line = (
                f"{self._c(icon, color, 'bold')} "
                f"{self._c(label.ljust(8), color)} "
                f"{self._c(action, 'cyan')}  {self._c(detail, 'dim')}"
            )
            return f"{ts} {line}"
        # Non-saidso / unstructured record: plain, dimmed.
        return f"{ts} {self._c(msg, 'dim')}"


def enable_pretty_logging(
    level: int = logging.INFO,
    *,
    stream=None,
    color: Optional[bool] = None,
) -> logging.Handler:
    """Attach a pretty colored handler to the ``saidso`` logger and return it.

    Idempotent: removes any handler this function added before. ``color`` auto-detects
    a TTY when left as ``None``. Call once near startup.
    """
    stream = stream or sys.stderr
    if color is None:
        color = _supports_color(stream)
    if color:
        _enable_windows_ansi()

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False  # don't double-print through the root logger
    for h in list(logger.handlers):
        if getattr(h, "_saidso_pretty", False):
            logger.removeHandler(h)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(PrettyFormatter(color=color))
    handler._saidso_pretty = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    return handler


class EventRecorder(logging.Handler):
    """A logging handler that remembers saidso decision events for a final summary."""

    def __init__(self) -> None:
        super().__init__()
        self.events: List[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        event = getattr(record, "saidso_event", None)
        if event is None:
            return
        self.events.append({
            "event": event,
            "action": getattr(record, "saidso_action", ""),
            "args": list(getattr(record, "saidso_args", []) or []),
            "ts": record.created,
        })

    def attach(self, level: int = logging.INFO) -> "EventRecorder":
        logger = logging.getLogger(_LOGGER_NAME)
        if logger.level == logging.NOTSET or logger.level > level:
            logger.setLevel(level)
        logger.addHandler(self)
        return self

    @property
    def passed(self) -> List[dict]:
        return [e for e in self.events if e["event"] == "pass"]

    @property
    def blocked(self) -> List[dict]:
        return [e for e in self.events if e["event"] == "block"]


def summary(audit=None, recorder: Optional[EventRecorder] = None) -> str:
    """Build a plain-text summary box: counts + one row per decision.

    Pass an :class:`~saidso.AttestationLog` (the actions that ran) and/or an
    :class:`EventRecorder` (the full pass/block stream). With only an audit log you
    see passes; add a recorder to also see what was blocked.
    """
    rows: List[str] = []
    n_pass = n_block = 0

    if recorder is not None:
        for e in recorder.events:
            icon = "✓" if e["event"] == "pass" else "✗"
            n_pass += e["event"] == "pass"
            n_block += e["event"] == "block"
            rows.append(f"  {icon} {e['action']:<22} {', '.join(e['args'])}")
    elif audit is not None:
        for a in audit.records:
            n_pass += 1
            rows.append(f"  ✓ {a.action:<22} {', '.join(f.name for f in a.args)}")

    head = f"saidso — {n_pass} grounded, {n_block} blocked"
    bar = "─" * max(len(head), *(len(r) for r in rows)) if rows else "─" * len(head)
    body = "\n".join(rows) if rows else "  (no decisions recorded)"
    return f"┌─ {head}\n{body}\n└{bar}"
