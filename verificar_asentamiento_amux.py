"""Si el multiplexor no se asienta, todas las lecturas mienten.

La foto de una corrida dio el sumador en -4.337 mV, fuera de rango, y la salida
del pasabajos en -1.310, dentro. Con la ganancia de -9 que hay entre los dos, esos
numeros no pueden coexistir: uno de los dos esta mal medido.

La sospecha es el AMux. Cuando se salta de un canal a otro con niveles muy
distintos, la entrada del ADC puede no alcanzar a asentarse y la lectura arrastra
al canal anterior. Este script lo separa: mide cada canal dos veces seguidas
(que da la repetibilidad) y despues lo mide precedido por cada uno de los otros
(que da el arrastre). Si la diferencia entre esos dos casos supera la
repetibilidad, el multiplexor esta contaminando todo.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from escala_banco import BANCO_VREF_MV, FACTOR
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

TAPS = {0: "PGAgain", 1: "BPo", 2: "OPA_SUMo", 3: "SUMo", 4: "LPo"}


def real_mv(banco_uv: float) -> float:
    return (banco_uv / 1000.0 - BANCO_VREF_MV) * FACTOR


def una(lab: Lab, ch: int, settle: int = 2) -> float | None:
    p = lab.measure_dc(ch, settle_sel=settle)
    return None if p is None or not p.ok else real_mv(p.mean_uv)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=50)
    ap.add_argument("--pgaout", type=int, default=4)
    ap.add_argument("--repeticiones", type=int, default=6)
    args = ap.parse_args()

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))

        print("1) Repetibilidad: mismo canal dos veces seguidas\n", flush=True)
        repetibilidad = {}
        for ch in TAPS:
            faltas, seguidas = 0, []
            for _ in range(args.repeticiones):
                a, b = una(lab, ch), una(lab, ch)
                if a is None or b is None:
                    faltas += 1
                    continue
                seguidas.append(abs(b - a))
            if not seguidas:
                print(f"  ch{ch} {TAPS[ch]:<9} sin lecturas")
                continue
            repetibilidad[ch] = max(seguidas)
            print(f"  ch{ch} {TAPS[ch]:<9} diferencia mediana "
                  f"{statistics.median(seguidas):7.1f} mV   maxima "
                  f"{max(seguidas):7.1f} mV", flush=True)

        print("\n2) Arrastre: el mismo canal precedido por cada otro\n",
              flush=True)
        print(f"  {'canal':<14} {'previo':<10} {'lectura':>10} "
              f"{'vs solo':>10}", flush=True)
        peor = (0.0, None)
        for ch in TAPS:
            solo = [v for v in (una(lab, ch) for _ in range(3))
                    if v is not None]
            if not solo:
                continue
            base = statistics.median(solo)
            print(f"  ch{ch} {TAPS[ch]:<9} {'(solo)':<10} {base:10.1f}"
                  f" {0.0:10.1f}", flush=True)
            for previo in TAPS:
                if previo == ch:
                    continue
                muestras = []
                for _ in range(3):
                    una(lab, previo)
                    v = una(lab, ch)
                    if v is not None:
                        muestras.append(v)
                if not muestras:
                    continue
                v = statistics.median(muestras)
                delta = v - base
                if abs(delta) > abs(peor[0]):
                    peor = (delta, f"ch{ch} tras ch{previo}")
                print(f"  {'':<14} tras ch{previo:<4} {v:10.1f} "
                      f"{delta:10.1f}", flush=True)

        print("\n=== VEREDICTO ===", flush=True)
        tope = max(repetibilidad.values()) if repetibilidad else 0.0
        print(f"Peor repetibilidad de un canal consigo mismo: {tope:.1f} mV")
        print(f"Peor arrastre entre canales: {peor[0]:+.1f} mV ({peor[1]})")
        if abs(peor[0]) > 3.0 * max(tope, 1.0):
            print("\nEL MULTIPLEXOR CONTAMINA. El arrastre supera con holgura "
                  "la repetibilidad: leer un canal despues de otro devuelve un "
                  "numero distinto que leerlo solo. Hay que separar las "
                  "lecturas o subir el tiempo de asentamiento.")
        else:
            print("\nNo hay arrastre apreciable: el orden de lectura no "
                  "explica las inconsistencias.")
        return 0
    finally:
        try:
            lab.set_gain("pgaout", 0)
        except Exception:
            pass
        console.close()


if __name__ == "__main__":
    raise SystemExit(main())
