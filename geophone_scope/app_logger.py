"""
app_logger.py — Simple file logger shared across all modules.

Call log() from anywhere; the file is opened once at import time.
Log path: geophone_YYYYMMDD_HHMMSS.log in the script's directory.
"""

import datetime
import os
import sys

_log_dir = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "logs")
os.makedirs(_log_dir, exist_ok=True)
_log_path = os.path.join(_log_dir, datetime.datetime.now().strftime("geophone_%Y%m%d_%H%M%S.log"))

_fh = open(_log_path, "w", encoding="utf-8", buffering=1)  # line-buffered

def log(msg: str):
    ts  = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"{ts}  {msg}"
    _fh.write(line + "\n")

def log_path() -> str:
    return _log_path
