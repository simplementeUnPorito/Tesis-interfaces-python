"""Cache del escaneo del árbol de datos.

``discover_dataset`` y ``scan_catalog`` recorren el volumen entero y
``discover_dataset`` además hashea archivos para detectar carpetas duplicadas:
sobre una campaña de ~950 capturas tarda unos 30 segundos. Los tres endpoints
que dibujan (``/api/captures``, ``/api/signal``, ``/api/overlays``) lo pedían
cada uno por su cuenta, en cada request, con la tabla refrescando cada pocos
segundos. El resultado era un servidor encolando peticiones de medio minuto.

Se cachea sólo lo **estructural** — qué capturas hay y cuáles son disparos —
que cambia cuando llega una captura nueva. Las **anotaciones** no se cachean
nunca: son baratas de leer y son lo que más cambia; además la app PyQt puede
escribirlas por detrás mientras la web está abierta, y la web tiene que verlo.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from ._gs import frd

TTL_S = 60.0

_lock = threading.Lock()
_datasets: dict[str, tuple[float, tuple, object]] = {}


def tree_signature(raw_root: str | Path) -> tuple:
    """Huella barata del árbol: ``(nombre, mtime)`` de la raíz, de cada carpeta
    y de su directorio ``captures``.

    Sirve para no servir un escaneo viejo cuando alguien agrega datos **sin
    pasar por el servidor** — la app PyQt, una copia a mano, o los fixtures del
    gate. Sólo hace ``stat`` (unas pocas decenas), no lee ni un byte: el TTL
    solo no alcanza, porque durante su ventana el dato nuevo sería invisible.
    """
    root = Path(raw_root)
    if not root.is_dir():
        return ()
    out: list[tuple[str, int]] = [("", root.stat().st_mtime_ns)]
    try:
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            out.append((child.name, child.stat().st_mtime_ns))
            captures = child / "captures"
            if captures.is_dir():
                out.append((f"{child.name}/captures", captures.stat().st_mtime_ns))
    except OSError:
        pass
    return tuple(out)


def invalidate(raw_root: str | Path | None = None) -> None:
    """Tira el cache. La llama el Pipeline al terminar de ingestar un ZIP."""
    with _lock:
        if raw_root is None:
            _datasets.clear()
        else:
            _datasets.pop(str(Path(raw_root).resolve()), None)


def get_dataset(raw_root: str | Path):
    """``discover_dataset(raw_root)``, cacheado por TTL + firma del árbol."""
    key = str(Path(raw_root).resolve())
    sig = tree_signature(raw_root)
    now = time.monotonic()
    with _lock:
        hit = _datasets.get(key)
        if hit is not None and (now - hit[0]) < TTL_S and hit[1] == sig:
            return hit[2]

    dataset = frd.discover_dataset(raw_root)

    with _lock:
        _datasets[key] = (time.monotonic(), sig, dataset)
    return dataset
