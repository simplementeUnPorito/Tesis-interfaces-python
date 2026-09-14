"""Sube PGAout con los cuatro IDAC CONGELADOS y mira los cinco taps.

QUE PREGUNTA CONTESTA

La corrida de las 17:22 del 2026-09-13 dejo un dato que no cierra con el modelo
de la cadena. Partiendo del punto calibrado a PGA x50 / PGAout x1, con los cinco
taps en ventana y LPo en +69 mV, al subir PGAout a x2 el tap ch0 -SEo, la salida
del PGA, que esta aguas ARRIBA de PGAout- se fue de -1.150 a -4.245 mV y quedo
fuera de ventana.

Por el camino de la senal eso es imposible. Asi que o el mapa de canales que uso
esta mal, o subir PGAout perturba la cadena por otra via -el nodo Vref
compartido, la carga que PGAout presenta al sumador, o la conmutacion misma-.

La diferencia importa mucho para el plan: si al subir la ganancia se desplaza
TODA la cadena, entonces recentrar solo con IDAC2 e IDAC3 -que fue la estrategia
de subir_ganancia.py, elegida para no excitar la cola de 680 uF del pasabanda-
es insuficiente por construccion, y hay que volver a tocar IDAC1.

COMO

Con los cuatro codigos quietos en el punto calibrado, se recorre PGAout de x1
para arriba leyendo los cinco taps en cada escalon. Nada se corrige. Lo unico
que se mide es cuanto desplaza cada escalon a cada tap.

Y se mide DOS veces por escalon, separadas por una espera: la cadena deriva
sola, asi que sin repetir no hay forma de distinguir el efecto del escalon de lo
que se habria movido igual.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from centrar_lpo import leer_promedio, poner_idac
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

TAPS = (0, 1, 2, 3, 4)
NOMBRES = {0: "SEo", 1: "BPo", 2: "OPA_SUMo", 3: "SUMo", 4: "LPo"}


def leer_todo(lab: Lab) -> list[tuple[float, bool]]:
    return [leer_promedio(lab, canal, n=3) for canal in TAPS]


def fila(etiqueta: str, lecturas: list[tuple[float, bool]]) -> str:
    partes = [f"{etiqueta:>12}"]
    for valor, ok in lecturas:
        partes.append(f"{valor:+9.1f}" + ("  " if ok else " *"))
    return " ".join(partes)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=50)
    ap.add_argument("--punto", required=True, help="idac0,idac1,idac2,idac3")
    ap.add_argument("--ganancias", default="1,2,4,8,16")
    ap.add_argument("--asentar", type=float, default=90.0,
                    help="espera inicial, para partir de una cadena quieta")
    ap.add_argument("--espera", type=float, default=45.0,
                    help="entre las dos lecturas de cada escalon")
    args = ap.parse_args()

    base = [int(x) for x in args.punto.split(",")]
    ganancias = [int(x) for x in args.ganancias.split(",")]

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(ganancias[0]))
        for etapa, codigo in enumerate(base):
            poner_idac(lab, etapa, codigo)
        print(f"PGA x{args.pga} fijo, IDAC congelados en "
              + " ".join(f"{c:+d}" for c in base), flush=True)
        print(f"Asentando {args.asentar:.0f} s antes de empezar...", flush=True)
        time.sleep(args.asentar)

        print("\n" + " " * 12 + " ".join(f"{'ch'+str(c):>9}  " for c in TAPS),
              flush=True)
        print(" " * 12 + " ".join(f"{NOMBRES[c]:>9}  " for c in TAPS),
              flush=True)

        previo: list[tuple[float, bool]] | None = None
        for ganancia in ganancias:
            lab.set_gain("pgaout", GAIN_CODES.index(ganancia))
            time.sleep(3.0)
            inmediato = leer_todo(lab)
            print(fila(f"x{ganancia} ya", inmediato), flush=True)
            if previo is not None:
                salto = [(a[0] - b[0]) for a, b in zip(inmediato, previo)]
                print(fila("  salto", [(s, True) for s in salto]), flush=True)
            time.sleep(args.espera)
            asentado = leer_todo(lab)
            print(fila(f"x{ganancia} +{args.espera:.0f}s", asentado),
                  flush=True)
            deriva = [(a[0] - b[0]) for a, b in zip(asentado, inmediato)]
            print(fila("  deriva", [(d, True) for d in deriva]), flush=True)
            previo = asentado

        print("\n(* = fuera de ventana; ese numero no es una tension)")
        print("\nEl salto es lo que hizo el escalon de ganancia. La deriva es "
              "lo que la cadena se habria movido igual. Si el salto en ch0 o "
              "ch1 es del orden del de ch3, entonces subir PGAout desplaza "
              "toda la cadena y recentrar con IDAC2 e IDAC3 nunca va a "
              "alcanzar.")
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
