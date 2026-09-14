"""Dispara la caracterizacion que YA corre en el nodo y captura el resultado.

POR QUE EXISTE

Medir la matriz de acople desde la PC cuesta diez minutos, y la fase 1 completa
veintiuno, porque cada sondeo viaja por serie a la PC y por I2C al PSoC. En el
nodo una iteracion son 30 ms: cien veces mas barato por muestra.

Y no hay que escribir nada, porque ya esta hecho. El autotest del esclavo tiene
el bloque D2, que mide la matriz 4x5 de pendientes IDAC->tap y ademas MARCA las
entradas que salieron con la etapa saturada. Eso ultimo es justo la trampa que
costo la jornada del 2026-09-13: una pendiente medida contra el riel parece un
cero y no lo es.

Lo unico que faltaba era usarla desde la PC en vez de reimplementarla.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from testbench.core.console import Console


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--comando", default="d",
                    help="grupo del autotest: d es el analogico, que trae D2")
    ap.add_argument("--timeout", type=float, default=900.0)
    args = ap.parse_args()

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    try:
        print(f"Enviando '{args.comando}' al nodo...", flush=True)
        limite = time.time() + args.timeout
        for linea in console.raw(args.comando, idle=5.0, timeout=args.timeout):
            print(linea, flush=True)
            if time.time() > limite:
                break
        return 0
    finally:
        console.close()


if __name__ == "__main__":
    raise SystemExit(main())
