"""Mide la matriz de acople de los cuatro IDAC sobre los cinco taps.

POR QUE HACE FALTA

El 2026-09-13 apareció una evidencia que obliga a medirla: **IDAC3 mueve ch3**,
que está aguas ARRIBA de él. Con IDAC3 en -255, ch3 leía 1.086 mV por debajo de
lo que leía con IDAC3 en 0, al mismo código de IDAC2. Por la cadena de señal eso
es imposible, porque el pasabajos está después del sumador.

La vía es el nodo `Vref` compartido. Los cuatro pines `Vref_XX` cuelgan del mismo
nodo a través de sus resistencias -15 k en PGA y BP, 1,5 k en el sumador, 10 k en
el pasabajos-, así que inyectar corriente en cualquiera de ellos corre la
referencia de los otros tres. Los actuadores están acoplados por construcción, no
sólo por la señal.

Consecuencia: la matriz tiene términos fuera de la diagonal en las DOS
direcciones, y un adelanto que sólo modele el camino de la señal apunta mal.

QUE SE MIDE Y COMO

Por cada actuador, un ABBA de dos bloques -base, base+delta, base+delta, base-
leyendo los cinco taps en cada punto. El ABBA cancela la deriva lineal, que en
esta cadena es de decenas de milivolts por minuto y arruinaría cualquier barrido
monótono.

Y sólo se anota la entrada si el tap estuvo DENTRO de ventana en los cuatro
puntos del bloque. Un tap contra su riel no responde, y la entrada que saldría de
ahí sería un cero falso: es el error que se repitió toda la noche del 13 y el que
hizo que la autoridad de IDAC3 figurara once veces por debajo de la real.

QUE VALE Y QUE NO

El acople por el nodo Vref es resistivo, así que esa parte debería ser estable y
sirve para el adelanto. Lo que la matriz NO arregla es la deriva del punto de
trabajo, que es un fenómeno distinto y que hay que atacar con el lazo cerrado
permanente.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from centrar_lpo import leer_promedio
from cerrar_lazo import SALIDA
from perfil_placa import PerfilPlaca
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

TAPS = (0, 1, 2, 3, 4)
NOMBRES = {0: "SEo", 1: "BPo", 2: "OPA_SUMo", 3: "SUMo", 4: "LPo"}
#: Los que pasan por el pasabanda necesitan esperas largas; los otros no.
LENTOS = (0, 1)


def bloque_abba(lab: Lab, etapa: int, base: int, delta: int,
                espera: float) -> dict[int, float | None]:
    """Un ABBA y las pendientes que salen, en mV por codigo y por tap.

    Devuelve None en los taps que no estuvieron dentro de ventana en los cuatro
    puntos: de un tap clavado no se puede sacar una pendiente.
    """
    lecturas: dict[int, dict[int, list[float]]] = {c: {} for c in TAPS}
    validos: dict[int, bool] = {c: True for c in TAPS}
    for codigo in (base, base + delta, base + delta, base):
        lab.set_idac(etapa, codigo)
        time.sleep(espera)
        for canal in TAPS:
            valor, vivo = leer_promedio(lab, canal, n=2)
            lecturas[canal].setdefault(codigo, []).append(valor)
            validos[canal] = validos[canal] and vivo
    lab.set_idac(etapa, base)

    pendientes: dict[int, float | None] = {}
    for canal in TAPS:
        if not validos[canal]:
            pendientes[canal] = None
            continue
        bajos = lecturas[canal].get(base, [])
        altos = lecturas[canal].get(base + delta, [])
        if not bajos or not altos:
            pendientes[canal] = None
            continue
        pendientes[canal] = (statistics.fmean(altos)
                             - statistics.fmean(bajos)) / delta
    return pendientes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=1)
    ap.add_argument("--pgaout", type=int, default=1)
    ap.add_argument("--idac", default="11,-53,0,0",
                    help="punto de trabajo desde el que se mide")
    ap.add_argument("--delta", type=int, default=20,
                    help="escalon del ABBA, en codigos")
    ap.add_argument("--espera-rapida", type=float, default=6.0)
    ap.add_argument("--espera-lenta", type=float, default=60.0)
    ap.add_argument("--asentar", type=float, default=100.0)
    ap.add_argument("--placa", default=None,
                    help="si se da, guarda la matriz en el perfil")
    args = ap.parse_args()

    base = [int(x) for x in args.idac.split(",")]

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))
        for etapa, codigo in enumerate(base):
            lab.set_idac(etapa, codigo)
        print(f"PGA x{args.pga}, PGAout x{args.pgaout}, punto "
              + " ".join(f"{c:+d}" for c in base), flush=True)
        print(f"Asentando {args.asentar:.0f} s...", flush=True)
        time.sleep(args.asentar)

        print("\nEstado de partida:", flush=True)
        for canal in TAPS:
            valor, vivo = leer_promedio(lab, canal)
            print(f"   ch{canal} ({NOMBRES[canal]:<8}) = {valor:+9.1f} mV"
                  + ("" if vivo else "   FUERA DE VENTANA"), flush=True)

        matriz: dict[int, dict[int, float | None]] = {}
        for etapa in range(4):
            espera = (args.espera_lenta if etapa in LENTOS
                      else args.espera_rapida)
            print(f"\nIDAC{etapa}: ABBA de {args.delta} codigos, esperas de "
                  f"{espera:.0f} s", flush=True)
            matriz[etapa] = bloque_abba(lab, etapa, base[etapa], args.delta,
                                        espera)
            for canal in TAPS:
                p = matriz[etapa][canal]
                print(f"   -> ch{canal} ({NOMBRES[canal]:<8}): "
                      + ("sin dato, el tap no estuvo en ventana"
                         if p is None else f"{p:+9.3f} mV/codigo"),
                      flush=True)

        print("\n=== MATRIZ DE ACOPLE, mV por codigo ===", flush=True)
        cabecera = "        " + "".join(f"{'ch'+str(c):>12}" for c in TAPS)
        print(cabecera, flush=True)
        for etapa in range(4):
            fila = f"IDAC{etapa}  "
            for canal in TAPS:
                p = matriz[etapa][canal]
                fila += f"{'      -     ' if p is None else f'{p:+12.3f}'}"
            print(fila, flush=True)

        print("\nLa diagonal util es la de cada actuador sobre el tap que "
              "gobierna. Todo lo demas es acople, y lo que aparezca de IDAC3 "
              "hacia ch0, ch1, ch2 o ch3 sólo puede venir del nodo Vref "
              "compartido, porque por la señal el pasabajos está al final.",
              flush=True)

        if args.placa:
            perfil = PerfilPlaca.cargar(args.placa) or PerfilPlaca(
                id_placa=args.placa, fecha=date.today().isoformat())
            for etapa in range(4):
                for canal in TAPS:
                    p = matriz[etapa][canal]
                    if p is not None:
                        perfil.pendientes[
                            PerfilPlaca.clave(etapa, canal, args.pgaout)] = p
            perfil.fecha = date.today().isoformat()
            print(f"\nGuardado en {perfil.guardar()}", flush=True)
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
