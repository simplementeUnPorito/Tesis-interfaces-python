"""Busca la discontinuidad de IDAC1 sobre el sumador, de a dos codigos.

El 2026-09-12 un movimiento de 18 codigos de IDAC1 movio ch2 veinticinco veces
mas de lo que predecia una pendiente medida tres veces minutos antes. Saltando
no se puede ver donde ocurre: la lectura llega saturada.

Aca se recorre el mismo tramo de a dos codigos, leyendo ch2 en cada paso, y se
informa la diferencia entre pasos consecutivos. Si hay una discontinuidad real,
aparece como un paso enormemente mayor que sus vecinos MIENTRAS ch2 todavia esta
dentro de rango. Si no aparece, el fenomeno no es del actuador y hay que
buscarlo en otro lado.

Se detiene apenas ch2 sale de rango: a partir de ahi la lectura no significa
nada, que es la leccion que costo media sesion.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from escala_banco import BANCO_VREF_MV, FACTOR, lectura_valida
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

SALIDA = Path(__file__).resolve().parents[3] / "lab" / "calibracion_nueva"


def real_mv(banco_uv: float) -> float:
    return (banco_uv / 1000.0 - BANCO_VREF_MV) * FACTOR


def leer(lab: Lab, ch: int):
    medias = []
    for _ in range(3):
        p = lab.measure_dc(ch, settle_sel=2)
        if p is not None and p.ok:
            medias.append(p.mean_uv)
    return statistics.median(medias) if medias else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=50)
    ap.add_argument("--pgaout", type=int, default=4)
    ap.add_argument("--idac0", type=int, default=11)
    ap.add_argument("--idac2", type=int, default=-160)
    ap.add_argument("--desde", type=int, default=-10)
    ap.add_argument("--hasta", type=int, default=60)
    ap.add_argument("--paso", type=int, default=2)
    ap.add_argument("--espera", type=float, default=20.0,
                    help="por paso; corto a proposito, se busca el salto")
    args = ap.parse_args()

    SALIDA.mkdir(parents=True, exist_ok=True)
    destino = SALIDA / ("salto_idac1_" + time.strftime("%Y%m%d_%H%M%S")
                        + ".csv")
    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    salida = destino.open("w", newline="", encoding="utf-8")
    escritor = csv.writer(salida)
    escritor.writerow(["idac1", "ch2_real_mv", "delta_mv", "mv_por_codigo",
                       "valido"])
    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))
        lab.set_idac(0, args.idac0)
        lab.set_idac(3, 0)
        lab.set_idac(1, args.desde)
        lab.set_idac(2, args.idac2)
        print(f"Punto: PGA x{args.pga}, PGAout x{args.pgaout}, "
              f"IDAC0={args.idac0:+d}, IDAC2={args.idac2:+d}", flush=True)
        print("Asentando 150 s...", flush=True)
        time.sleep(150.0)

        previo = None
        peor = (0.0, None)
        for codigo in range(args.desde, args.hasta + 1, args.paso):
            lab.set_idac(1, codigo)
            time.sleep(args.espera)
            media = leer(lab, 2)
            if media is None:
                print(f"  IDAC1={codigo:+4d}  sin lectura", flush=True)
                continue
            mv = real_mv(media)
            valido = lectura_valida(media / 1000.0)
            delta = None if previo is None else mv - previo
            por_codigo = None if delta is None else delta / args.paso
            escritor.writerow([codigo, f"{mv:.1f}",
                               "" if delta is None else f"{delta:.1f}",
                               "" if por_codigo is None else f"{por_codigo:.2f}",
                               valido])
            salida.flush()
            marca = "" if valido else "   FUERA DE RANGO"
            print(f"  IDAC1={codigo:+4d}  ch2={mv:+9.1f} mV"
                  + ("" if delta is None
                     else f"   paso {delta:+8.1f} mV"
                          f"   {por_codigo:+7.2f} mV/código")
                  + marca, flush=True)
            if por_codigo is not None and abs(por_codigo) > abs(peor[0]):
                peor = (por_codigo, codigo)
            if not valido:
                print("\n  ch2 salió de rango: se corta, mas alla la lectura "
                      "no significa nada.", flush=True)
                break
            previo = mv

        print("\n=== RESULTADO ===", flush=True)
        if peor[1] is None:
            print("No hubo pasos suficientes para comparar.")
        else:
            print(f"Paso mas grande: {peor[0]:+.2f} mV/código al llegar a "
                  f"IDAC1={peor[1]:+d}")
            print("Si ese numero es del orden de -8, no hay discontinuidad y el "
                  "salto de 25x viene de otro lado.")
            print("Si es de cientos, la discontinuidad esta ahi y este es el "
                  "codigo donde mirar con osciloscopio.")
        print(f"\nArchivo: {destino}", flush=True)
        return 0
    finally:
        try:
            lab.set_gain("pgaout", 0)
            for etapa in range(4):
                lab.set_idac(etapa, 0)
        except Exception:
            pass
        salida.close()
        console.close()


if __name__ == "__main__":
    raise SystemExit(main())
