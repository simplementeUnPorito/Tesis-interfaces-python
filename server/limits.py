"""Límites defensivos de ingesta, configurables sin cambiar el protocolo.

Los valores predeterminados son los de producción. Las variables de entorno
permiten ejecutar pruebas pequeñas y adaptar una instalación con menos disco,
pero nunca se aceptan valores nulos, negativos o no numéricos.
"""

from __future__ import annotations

import math
import os


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} debe ser un entero positivo") from exc
    if value <= 0:
        raise RuntimeError(f"{name} debe ser un entero positivo")
    return value


def _positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} debe ser un número positivo") from exc
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(f"{name} debe ser un número positivo y finito")
    return value


MAX_UPLOAD_BYTES = _positive_int("TESIS_MAX_UPLOAD_BYTES", 256 * 1024 * 1024)
MAX_ZIP_FILES = _positive_int("TESIS_MAX_ZIP_FILES", 20_000)
MAX_UNCOMPRESSED_BYTES = _positive_int(
    "TESIS_MAX_UNCOMPRESSED_BYTES", 2 * 1024 * 1024 * 1024
)
MAX_COMPRESSION_RATIO = _positive_float("TESIS_MAX_COMPRESSION_RATIO", 250.0)
