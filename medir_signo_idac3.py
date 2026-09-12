"""Mide el signo y el modulo de IDAC3 sobre LPo con excitacion grande.

Las siete identificaciones ABBA guardadas dieron entre -4,0 y +13,9 mV/codigo
con el signo cambiando, porque la excitacion de 8 codigos queda por debajo del
ruido propio de LPo. Aca se excita con 200 codigos, que son mas de un voltio, y
se repite en las dos direcciones para que una deriva lenta no decida el signo.

Corre en x1, que es donde LPo tiene mas margen antes de irse al riel.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from escala_banco import BANCO_VREF_MV, FACTOR, lectura_valida
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

CANAL_LPO = 4


def real_mv(banco_uv: float) -> float:
    """Milivoltios fisicos respecto de Vref a partir de la lectura del banco."""
    return (banco_uv / 1000.0 - BANCO_VREF_MV) * FACTOR


def leer(lab: Lab, repeticiones: int = 5) -> tuple[float | None, float, int]:
    """Mediana de varias ventanas. Devuelve media, pico a pico y cuantas valieron."""
    medias, picos, validas = [], [], 0
    for _ in range(repeticiones):
        p = lab.measure_dc(CANAL_LPO, settle_sel=2)
        if p is None or not p.ok:
            continue
        medias.append(p.mean_uv)
        picos.append(p.pp_uv)
        if lectura_valida(p.mean_uv / 1000.0):
            validas += 1
    if not medias:
        return None, 0.0, 0
    return statistics.median(medias), statistics.median(picos), validas


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--codigo", type=int, default=200,
                    help="amplitud de la excitacion, en codigos de IDAC3")
    ap.add_argument("--pga", type=int, default=1)
    # PGAout x4 a proposito: con x1 el offset de entrada de PGAout deja LPo
    # contra el riel y la pendiente aparente sale comprimida. Con x4 y PGA en
    # x1, LPo descansa en +209 mV, que es donde la medicion vale.
    ap.add_argument("--pgaout", type=int, default=4)
    ap.add_argument("--espera", type=float, default=3.0)
    args = ap.parse_args()

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))
        for etapa in range(4):
            lab.set_idac(etapa, 0)
        print("asentando 20 s con todo en Vref...", flush=True)
        time.sleep(20.0)

        lecturas: dict[int, list[float]] = {}
        # A-B-B-A sobre los dos extremos: la vuelta al origen cancela la deriva.
        secuencia = [0, -args.codigo, +args.codigo, +args.codigo,
                     -args.codigo, 0]
        for paso, codigo in enumerate(secuencia):
            lab.set_idac(3, codigo)
            time.sleep(args.espera)
            media, pico, validas = leer(lab)
            if media is None:
                print(f"  paso {paso}: IDAC3={codigo:+4d}  sin lectura",
                      flush=True)
                continue
            mv = real_mv(media)
            lecturas.setdefault(codigo, []).append(mv)
            print(f"  paso {paso}: IDAC3={codigo:+4d}  LPo={mv:+9.1f} mV  "
                  f"pp={pico/1000.0:7.1f} mV banco  validas={validas}/5",
                  flush=True)

        neg = lecturas.get(-args.codigo)
        pos = lecturas.get(+args.codigo)
        if not neg or not pos:
            print("\nNo hay lecturas en los dos extremos: nada que concluir.")
            return 1
        delta = statistics.mean(pos) - statistics.mean(neg)
        pendiente = delta / (2.0 * args.codigo)
        print(f"\nLPo en {-args.codigo:+d}: {statistics.mean(neg):+9.1f} mV")
        print(f"LPo en {+args.codigo:+d}: {statistics.mean(pos):+9.1f} mV")
        print(f"Excursion total: {delta:+.1f} mV sobre "
              f"{2*args.codigo} codigos")
        print(f"PENDIENTE: {pendiente:+.3f} mV/codigo")
        print(f"Autoridad a fondo de escala: +-{abs(pendiente)*255:.0f} mV")
        if abs(delta) < 100.0:
            print("OJO: la excursion es chica; el tap puede estar contra un "
                  "riel y no responder.")
        return 0
    finally:
        try:
            lab.set_gain("pgaout", 0)
        except Exception:
            pass
        for etapa in range(4):
            try:
                lab.set_idac(etapa, 0)
            except Exception:
                pass
        console.close()


if __name__ == "__main__":
    raise SystemExit(main())
