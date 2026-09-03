"""Bitácora de sesión: qué se hizo en el banco y qué contestó la placa.

Para qué existe: cuando el operador experimenta a mano y algo sale raro, lo que
hace falta reconstruir después no es el gráfico sino la SECUENCIA — qué IDAC se
movió, a qué código, qué contestó, y qué se midió justo antes y justo después.
Eso no cabe en una captura de pantalla y se pierde apenas se cierra la ventana.

Formato JSON Lines, un evento por renglón, porque es lo que se puede leer con
una herramienta sin parsear nada: cada renglón es independiente, el archivo
sirve aunque la sesión se haya cortado a la mitad, y se puede filtrar con grep.

Cada evento lleva:

``t``
    Segundos desde que arrancó la sesión, con tres decimales. Relativo y no
    absoluto porque lo que importa es el orden y la distancia entre cosas.
``tipo``
    ``accion`` (algo que pidió el operador), ``medida`` (lo que contestó la
    placa), ``nota`` (contexto: conectar, parar, cambiar de canal) o ``error``.
``que``
    Qué fue, en una palabra: ``idac``, ``dc``, ``pga``, ``monitor``…
El resto de las claves dependen del evento y se guardan tal cual.

No se escribe la línea cruda del serie: para eso ya está el transcript de
``console``. Acá va lo interpretado, que es lo que se puede resumir.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

#: Dónde viven las bitácoras. Fuera del repo del código y en un lugar fijo, para
#: que se puedan pedir por nombre sin tener que averiguar dónde quedaron.
DIRECTORIO = Path(
    os.environ.get("BANCO_BITACORA_DIR",
                   Path.home() / "AppData" / "Local" / "banco_placas" / "bitacora")
)


class Bitacora:
    """Escribe eventos a un archivo JSONL, uno por sesión.

    Escribe y vacía en cada evento a propósito: si la ventana se cierra mal o se
    cuelga, lo que interesa es justamente lo último que pasó antes, y con buffer
    eso es lo primero que se pierde.
    """

    def __init__(self, etiqueta: str = "sesion", directorio: Optional[Path] = None) -> None:
        self.dir = Path(directorio) if directorio else DIRECTORIO
        self.dir.mkdir(parents=True, exist_ok=True)
        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.ruta = self.dir / f"{etiqueta}_{marca}.jsonl"
        self._t0 = time.monotonic()
        self._fh = self.ruta.open("w", encoding="utf-8")
        self.nota("inicio", archivo=str(self.ruta))

    # -- escritura --------------------------------------------------------
    def evento(self, tipo: str, que: str, **campos: Any) -> None:
        if self._fh is None:
            return
        fila = {"t": round(time.monotonic() - self._t0, 3), "tipo": tipo, "que": que}
        fila.update(campos)
        try:
            self._fh.write(json.dumps(fila, ensure_ascii=False) + "\n")
            self._fh.flush()
        except Exception:
            # Una bitácora rota no puede tirar abajo una sesión de laboratorio.
            self._fh = None

    def accion(self, que: str, **campos: Any) -> None:
        self.evento("accion", que, **campos)

    def medida(self, que: str, **campos: Any) -> None:
        self.evento("medida", que, **campos)

    def nota(self, que: str, **campos: Any) -> None:
        self.evento("nota", que, **campos)

    def error(self, que: str, **campos: Any) -> None:
        self.evento("error", que, **campos)

    def cerrar(self) -> None:
        if self._fh is not None:
            self.nota("fin")
            self._fh.close()
            self._fh = None


# --------------------------------------------------------------------------
# Lectura y resumen
# --------------------------------------------------------------------------
def ultimas(n: int = 1, directorio: Optional[Path] = None) -> list[Path]:
    """Las ``n`` bitácoras más recientes, la más nueva primero."""
    d = Path(directorio) if directorio else DIRECTORIO
    if not d.exists():
        return []
    return sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:n]


def leer(ruta: Path) -> list[dict]:
    filas = []
    for linea in Path(ruta).read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            filas.append(json.loads(linea))
        except json.JSONDecodeError:
            # Un renglón cortado —la sesión murió a mitad de escritura— no
            # invalida el resto: es la ventaja de un evento por renglón.
            continue
    return filas


def resumir(filas: list[dict]) -> str:
    """Resumen en texto de una sesión, pensado para leer de un vistazo.

    Da tres cosas: qué se tocó y cuántas veces, la secuencia completa de
    acciones con su resultado, y los errores. Con eso alcanza para reconstruir
    un experimento sin haber estado mirando.
    """
    if not filas:
        return "bitacora vacia"

    dur = max(f.get("t", 0) for f in filas)
    acciones = [f for f in filas if f["tipo"] == "accion"]
    medidas = [f for f in filas if f["tipo"] == "medida"]
    errores = [f for f in filas if f["tipo"] == "error"]

    conteo: dict[str, int] = {}
    for f in acciones:
        conteo[f["que"]] = conteo.get(f["que"], 0) + 1

    out = [
        f"Sesion de {dur:.0f} s: {len(acciones)} acciones, "
        f"{len(medidas)} medidas, {len(errores)} errores.",
        "",
        "Lo que se toco: " + (", ".join(f"{k} x{v}" for k, v in sorted(conteo.items()))
                              or "nada"),
        "",
        "Secuencia:",
    ]
    for f in filas:
        if f["tipo"] == "medida":
            continue
        campos = " ".join(f"{k}={v}" for k, v in f.items()
                          if k not in ("t", "tipo", "que"))
        marca = {"accion": "  ", "nota": "· ", "error": "! "}.get(f["tipo"], "  ")
        out.append(f"  {f['t']:8.1f}s {marca}{f['que']:<12} {campos}")

    if errores:
        out += ["", "Errores:"]
        for f in errores:
            campos = " ".join(f"{k}={v}" for k, v in f.items()
                              if k not in ("t", "tipo", "que"))
            out.append(f"  {f['t']:8.1f}s {f['que']}: {campos}")
    return "\n".join(out)
