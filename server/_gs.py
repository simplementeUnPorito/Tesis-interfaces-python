"""Punto único de entrada a la capa de datos compartida con la app PyQt.

``geophone_scope`` es hermano de este paquete: se agrega al path en vez de
duplicar la capa de datos (PORT_PLAN §0.4). Si esto se muda, mudar la ruta.

Existe como módulo aparte para que ``catalog.py`` pueda usar ``frd`` sin
importar ``pipeline`` (que ya importa ``catalog``: sería circular).
"""

from __future__ import annotations

import sys
from pathlib import Path

_GS = Path(__file__).resolve().parent.parent / "geophone_scope"
if str(_GS) not in sys.path:
    sys.path.insert(0, str(_GS))

import field_review_data as frd   # noqa: E402  (después del sys.path)

__all__ = ["frd"]
