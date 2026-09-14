"""Mide cuanta senal de ALTERNA hay en la cadena, a distintas ganancias de PGA.

LA HIPOTESIS

El sumador no responde a IDAC2 a PGA x50, y IDAC2 es su propia referencia: eso
solo pasa si esta saturado por su entrada. Pero su entrada, BPo, tiene la
continua fijada por IDAC1 cerca de Vref, asi que por offset no deberia estarlo.

Queda una posibilidad que no se habia mirado: que lo sature la SENAL. El geofono
recoge ruido ambiente -pasos, vibracion del edificio, la red-, y a PGA x50 eso
se amplifica cincuenta veces antes del pasabanda. Si el pico a pico en BPo es de
volts, el sumador vive recortando y la medida de continua lee el recorte
promediado, que es exactamente lo que se observa.

Si la hipotesis es cierta, x50 no es calibrable con el geofono conectado en un
ambiente ruidoso, y eso no es un problema del algoritmo sino del presupuesto de
ganancia. Se mide comparando el pico a pico a x1 y a x50: si escala con la
ganancia, es senal; si no, es otra cosa.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cerrar_lazo import real_mv
from escala_banco import FACTOR
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

NOMBRES = {0: "SEo", 1: "BPo", 2: "OPA_SUMo", 3: "SUMo", 4: "LPo"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pgaout", type=int, default=1)
    ap.add_argument("--ganancias", default="1,8,50")
    ap.add_argument("--asentar", type=float, default=60.0)
    args = ap.parse_args()

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    try:
        lab.set_adc_config(1)
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))
        for etapa in range(4):
            lab.set_idac(etapa, 0)

        print(f"PGAout x{args.pgaout}, los cuatro IDAC en cero.\n", flush=True)
        for pga in [int(x) for x in args.ganancias.split(",")]:
            lab.set_gain("pga", GAIN_CODES.index(pga))
            time.sleep(args.asentar)
            print(f"PGA x{pga}:", flush=True)
            for canal in (0, 1, 2, 3, 4):
                pps, medias = [], []
                for _ in range(4):
                    p = lab.measure_dc(canal, settle_sel=2)
                    if p is not None and p.ok:
                        pps.append(p.pp_uv * FACTOR / 1000.0)
                        medias.append(real_mv(p.mean_uv))
                if not pps:
                    print(f"   ch{canal} ({NOMBRES[canal]:<8}) sin lectura",
                          flush=True)
                    continue
                print(f"   ch{canal} ({NOMBRES[canal]:<8}) media "
                      f"{statistics.fmean(medias):+9.1f} mV   "
                      f"pico a pico {statistics.fmean(pps):8.1f} mV",
                      flush=True)
            print(flush=True)
        return 0
    finally:
        try:
            lab.set_gain("pga", 0)
            lab.set_gain("pgaout", 0)
        except Exception:
            pass
        console.close()


if __name__ == "__main__":
    raise SystemExit(main())
