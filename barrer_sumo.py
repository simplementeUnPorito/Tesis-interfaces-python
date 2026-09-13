"""Barre IDAC2 y lee ch2 y ch3 juntos, para ver la relacion entre los dos.

ch3 (SUMo, despues de PGAout) solo tomo dos valores en todas las corridas del
2026-09-13: +1.855 o -5.049, saltando entre uno y otro con un solo codigo y sin
pasar nunca por el medio. O la etapa no tiene zona lineal alcanzable, o la
relacion con ch2 no es la que supongo. Un barrido con las dos lecturas en cada
punto lo contesta.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from centrar_lpo import leer_promedio
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=50)
    ap.add_argument("--pgaout", type=int, default=4)
    ap.add_argument("--idac0", type=int, default=11)
    ap.add_argument("--idac1", type=int, default=-53)
    ap.add_argument("--espera", type=float, default=5.0)
    ap.add_argument("--etapa", type=int, default=2,
                    help="que IDAC barrer")
    ap.add_argument("--desde", type=int, default=-180)
    ap.add_argument("--hasta", type=int, default=180)
    ap.add_argument("--paso", type=int, default=20)
    ap.add_argument("--idac2", type=int, default=0)
    ap.add_argument("--idac3", type=int, default=0,
                    help="el trim del pasabajos, que la busqueda de consigna "
                         "de cerrar_lazo deja siempre en cero")
    ap.add_argument("--asentar", type=float, default=90.0)
    args = ap.parse_args()

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))
        lab.set_idac(0, args.idac0)
        lab.set_idac(1, args.idac1)
        lab.set_idac(2, args.idac2)
        lab.set_idac(3, args.idac3)
        print(f"IDAC0={args.idac0:+d} IDAC1={args.idac1:+d}, esperando "
              f"{args.asentar:.0f} s", flush=True)
        time.sleep(args.asentar)

        print(f"\n IDAC{args.etapa}      ch0        ch1        ch2"
              "        ch3        ch4", flush=True)
        for codigo in range(args.desde, args.hasta + 1, args.paso):
            lab.set_idac(args.etapa, codigo)
            time.sleep(args.espera)
            fila = [f"{codigo:+5d} "]
            for canal in (0, 1, 2, 3, 4):
                valor, ok = leer_promedio(lab, canal, n=2)
                fila.append(f"{valor:+9.1f}" + ("" if ok else "*"))
            print("  ".join(fila), flush=True)
        print("\n(* = fuera de ventana; el numero no es una tension)",
              flush=True)
        return 0
    finally:
        try:
            lab.set_gain("pgaout", 0)
            for etapa in range(4):
                lab.set_idac(etapa, 0)
        except Exception:
            pass
        console.close()


if __name__ == "__main__":
    raise SystemExit(main())
