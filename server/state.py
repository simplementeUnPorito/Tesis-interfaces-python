"""Primitivas de estado compartido: revisión, locks y publicación atómica."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class RevisionConflict(RuntimeError):
    def __init__(self, expected: str, current: str) -> None:
        super().__init__(
            "el archivo cambió desde que se abrió "
            f"(base_revision={expected!r}, revision={current!r})"
        )
        self.expected = expected
        self.current = current


_locks_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}


def path_lock(path: str | Path) -> threading.RLock:
    key = str(Path(path).resolve()).casefold()
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


@contextmanager
def locked(path: str | Path) -> Iterator[None]:
    with path_lock(path):
        yield


def revision(path: str | Path) -> str:
    path = Path(path)
    try:
        payload = path.read_bytes()
    except FileNotFoundError:
        return "missing"
    return hashlib.sha256(payload).hexdigest()


def require_revision(path: str | Path, base_revision: Any) -> str:
    current = revision(path)
    expected = str(base_revision or "")
    if not expected or expected != current:
        raise RevisionConflict(expected, current)
    return current


def composite_revision(paths: list[str | Path] | tuple[str | Path, ...]) -> str:
    """Una revisión estable para estados que abarcan varios archivos."""
    digest = hashlib.sha256()
    for item in sorted((Path(path).resolve() for path in paths), key=str):
        digest.update(str(item).casefold().encode("utf-8"))
        digest.update(b"\0")
        digest.update(revision(item).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def require_composite_revision(
    paths: list[str | Path] | tuple[str | Path, ...], base_revision: Any
) -> str:
    current = composite_revision(paths)
    expected = str(base_revision or "")
    if not expected or expected != current:
        raise RevisionConflict(expected, current)
    return current


def read_json(path: str | Path, default: Any = None) -> tuple[Any, str]:
    path = Path(path)
    with locked(path):
        rev = revision(path)
        if rev == "missing":
            return default, rev
        try:
            return json.loads(path.read_text(encoding="utf-8")), rev
        except (OSError, json.JSONDecodeError):
            return default, rev


def atomic_write_bytes(path: str | Path, payload: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(
        path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        with tmp.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_write_json(path: str | Path, data: Any) -> str:
    path = Path(path)
    with locked(path):
        atomic_write_bytes(
            path,
            json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False).encode(
                "utf-8"
            ),
        )
        return revision(path)
