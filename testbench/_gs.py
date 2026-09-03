"""Punto único de entrada a lo que se reutiliza de ``geophone_scope``.

Mismo criterio que ``server/_gs.py``: ``geophone_scope`` es hermano de este
paquete y se agrega al path en vez de duplicar código. Si esto se muda, mudar
la ruta.

De la app de scope (derogada como aplicación) sobreviven tres cosas que este
banco sí necesita:

* ``config``        — constantes del protocolo binario del maestro y de la
                      adquisición (Fs nativa, muestras por lote, tabla de PGA).
* ``protocol``      — codificar/decodificar los paquetes de 6 bytes del maestro.
* ``serial_worker`` — hilo de puerto serie para ese mismo enlace binario.

Nada de eso sirve para hablar con el firmware ``slaveTest``, que es texto ASCII
a 115200: ese enlace lo implementa ``testbench/core/console.py``. La división
es a propósito, son dos protocolos distintos contra dos placas distintas.

Los import se hacen perezosos porque el modo terminal del banco tiene que
arrancar sin PyQt6 ni numpy instalados: ``serial_worker`` importa PyQt6.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

_GS = Path(__file__).resolve().parent.parent / "geophone_scope"


def _ensure_path() -> None:
    if str(_GS) not in sys.path:
        sys.path.insert(0, str(_GS))


def config() -> ModuleType:
    """Constantes de adquisición y del protocolo del maestro."""
    _ensure_path()
    import config as _config  # noqa: PLC0415  (perezoso a propósito)

    return _config


def protocol() -> ModuleType:
    """Codificador/decodificador de los paquetes de 6 bytes del maestro."""
    _ensure_path()
    import protocol as _protocol  # noqa: PLC0415

    return _protocol


def serial_worker() -> ModuleType:
    """Hilo Qt del enlace binario con el maestro. Requiere PyQt6."""
    _ensure_path()
    import serial_worker as _serial_worker  # noqa: PLC0415

    return _serial_worker


def available() -> bool:
    """True si el hermano ``geophone_scope`` está donde se lo espera."""
    return (_GS / "config.py").is_file()


__all__ = ["config", "protocol", "serial_worker", "available"]
