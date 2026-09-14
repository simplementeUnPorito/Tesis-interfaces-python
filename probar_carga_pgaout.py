"""Contesta si PGAout carga al sumador, o sea si no es transparente hacia atras.

LA PREGUNTA, Y POR QUE IMPORTA

ch2 es OPA_SUMo, que esta ANTES de PGAout. Cambiar la ganancia de salida no
deberia moverlo. Pero el 2026-09-13, con IDAC1=+164 e IDAC2=0 -los mismos
codigos-, ch2 leyo +1.184 mV con PGAout en x1 y -4.973 con PGAout en x2.

Si eso se confirma, PGAout carga al sumador y hay dos consecuencias:

  - El punto de trabajo hay que buscarlo POR CADA ganancia de salida, y el
    supuesto de que IDAC1 e IDAC0 no dependen de PGAout -que uso para propagar
    el punto entre ganancias- es falso.
  - Y es un dato sobre si la etapa nueva se justifica: una etapa de salida que
    perturba lo que tiene aguas arriba cuesta mas de lo que aporta.

El experimento es el mas simple posible: congelar los cuatro IDAC y mover una
sola cosa.
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

NOMBRES = {0: "SEo", 1: "BPo", 2: "OPA_SUMo", 3: "SUMo", 4: "LPo"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=50)
    ap.add_argument("--idac", default="0,164,0,0")
    ap.add_argument("--salidas", default="1,2,4")
    ap.add_argument("--asentar", type=float, default=90.0)
    ap.add_argument("--entre", type=float, default=25.0)
    args = ap.parse_args()

    base = [int(x) for x in args.idac.split(",")]
    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        for etapa, codigo in enumerate(base):
            lab.set_idac(etapa, codigo)
        lab.set_gain("pgaout", GAIN_CODES.index(1))
        print(f"PGA x{args.pga}, IDAC " + " ".join(f"{c:+d}" for c in base)
              + f", asentando {args.asentar:.0f} s", flush=True)
        time.sleep(args.asentar)

        print("\n PGAout      ch0        ch1        ch2        ch3        ch4",
              flush=True)
        for g in [int(x) for x in args.salidas.split(",")]:
            lab.set_gain("pgaout", GAIN_CODES.index(g))
            time.sleep(args.entre)
            fila = [f"   x{g:<4d}"]
            for canal in (0, 1, 2, 3, 4):
                v, ok = leer_promedio(lab, canal, n=3)
                fila.append(f"{v:+9.1f}" + ("" if ok else "*"))
            print("  ".join(fila), flush=True)
        print("\nSi ch2 se mueve al cambiar PGAout, la etapa de salida carga al "
              "sumador y NO es transparente hacia atras.", flush=True)
        return 0
    finally:
        try:
            lab.set_gain("pgaout", GAIN_CODES.index(1))
            for etapa in range(4):
                lab.set_idac(etapa, 0)
        except Exception:
            pass
        console.close()


if __name__ == "__main__":
    raise SystemExit(main())
