"""logger.py — Dual human/machine log files with session rotation."""

from __future__ import annotations

import glob
import os
import re
from datetime import datetime
from typing import Callable, Optional


class Logger:
    """Writes timestamped lines to human and machine log files simultaneously."""

    def __init__(
        self,
        log_dir: str,
        stamp: str,
        callback: Optional[Callable[[str], None]] = None,
        max_sessions: int = 10,
    ) -> None:
        """
        Open both log files.

        Args:
            log_dir: Directory for log files (created if missing).
            stamp:   Datetime stamp string, e.g. '20240531_143022'.
            callback: Optional callable invoked with each human-readable line
                      (for displaying in the GUI log tab).
            max_sessions: How many old sessions to keep on disk.
        """
        os.makedirs(log_dir, exist_ok=True)
        self._callback = callback

        human_path   = os.path.join(log_dir, f"geophone_human_{stamp}.log")
        machine_path = os.path.join(log_dir, f"geophone_machine_{stamp}.log")

        self._human_fid   = open(human_path,   "w", encoding="utf-8", buffering=1)
        self._machine_fid = open(machine_path, "w", encoding="utf-8", buffering=1)

        header = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._human_fid.write(f"=== geophone_scope human | {header} ===\n")
        self._machine_fid.write(f"=== geophone_scope machine | {header} ===\n")

        self._cleanup_old(log_dir, "geophone_human_*.log",   max_sessions)
        self._cleanup_old(log_dir, "geophone_machine_*.log", max_sessions)

    # ── Public API ────────────────────────────────────────────────────────────

    def log_human(self, msg: str) -> None:
        """Write timestamped line to human file and invoke GUI callback if set."""
        ts   = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}"
        try:
            self._human_fid.write(line + "\n")
        except Exception:
            pass
        if self._callback is not None:
            try:
                self._callback(line)
            except Exception:
                pass

    def log_machine(self, msg: str) -> None:
        """Write raw line to machine log file (no timestamp prefix)."""
        ts   = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        try:
            self._machine_fid.write(f"{ts} {msg}\n")
        except Exception:
            pass

    def set_callback(self, callback: Optional[Callable[[str], None]]) -> None:
        """Replace or remove the GUI callback."""
        self._callback = callback

    def close(self) -> None:
        """Flush and close both log files."""
        for fid in (self._human_fid, self._machine_fid):
            try:
                fid.flush()
                fid.close()
            except Exception:
                pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _cleanup_old(log_dir: str, pattern: str, max_keep: int) -> None:
        """Delete oldest log files, keeping only max_keep most-recent sessions."""
        files = sorted(
            glob.glob(os.path.join(log_dir, pattern)),
            key=Logger._log_sort_key,
            reverse=True,
        )
        for f in files[max_keep:]:
            try:
                os.remove(f)
            except OSError:
                pass

    @staticmethod
    def _log_sort_key(path: str) -> str:
        """Extract datetime stamp from filename for sorting."""
        m = re.search(r"_(\d{8}_\d{6})", os.path.basename(path))
        return m.group(1) if m else "00000000_000000"
