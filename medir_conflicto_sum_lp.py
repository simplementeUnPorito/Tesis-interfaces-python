"""Mide el unico numero que decide si una ganancia es calibrable o no.

LA PREGUNTA

El sumador y el pasabajos comparten actuador: IDAC2 es el unico con autoridad
real sobre ch2, y el pasabajos ve lo que el sumador le entrega, multiplicado por
PGAout. Si el codigo de IDAC2 que centra al sumador no es el mismo que centra a
LPo, hay un conflicto, y la pregunta es de cuanto:

  - cuantos codigos de IDAC2 separan un centro del otro, y
  - si IDAC3, que tiene +-1,70 V sobre LPo y no depende de la ganancia, alcanza
    para tapar esa diferencia.

Con esos dos numeros el techo de ganancia sale de una cuenta en vez de ocho
corridas de veinte minutos.

POR QUE SOLO IDAC2

Medido en la placa el 2026-09-13, con PGA x50 y PGAout x4:

  - IDAC0 de -21 a +10, treinta y un codigos: ch2 leyo +1.897, +1.891, +1.887,
    +1.885, +1.885, +1.884. No se movio.
  - IDAC1 de -253 a -54, doscientos codigos: ch2 leyo +1.882 en todos.

Ninguno de los dos tiene autoridad de continua sobre el sumador. IDAC0 porque el
capacitor de 680 uF del pasabanda le bloquea la continua; IDAC1 porque, aunque
fija la continua de BPo, lo que llega al sumador ya esta saturado a esta
ganancia. El unico que lo mueve es IDAC2, y eso si esta demostrado: en la
primera corrida de la noche, 111 codigos llevaron ch2 de +1.670 a +34, o sea los
12 mV por codigo nominales, monotono y limpio.

QUE HACE ESTE SCRIPT

Biseca IDAC2 contra ch2 hasta centrar el sumador, y despues barre IDAC2
alrededor de ese punto leyendo ch2 y LPo JUNTOS en cada paso. De ahi salen la
ganancia real de la etapa del pasabajos desde el sumador y la distancia entre
los dos centros. Todo con las dos etapas dentro de su ventana, que es la unica
condicion en la que estas mediciones significan algo.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from autocalibracion_dos_fases import TwoPhaseCalibrator
from centrar_lpo import (CANAL_LPO, CANAL_SUM, IDAC2_LIMITE, bisectar,
                         leer_promedio)
from cerrar_lazo import SALIDA, TAU_REFERENCIA_S
from perfil_placa import PerfilPlaca
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

#: Autoridad de IDAC3 sobre LPo, medida el 2026-09-13 con la etapa en ventana.
IDAC3_PASO_MV = 6.684
IDAC3_ALCANCE_MV = IDAC3_PASO_MV * 255.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=50)
    ap.add_argument("--pgaout", type=int, default=4)
    ap.add_argument("--perfil", default="geo-01")
    ap.add_argument("--espera", type=float, default=6.0)
    ap.add_argument("--paso", type=int, default=5,
                    help="separacion del barrido alrededor del centro")
    ap.add_argument("--puntos", type=int, default=9)
    args = ap.parse_args()

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))

        perfil = PerfilPlaca.cargar(args.perfil)
        punto = perfil.punto_de_entrada(args.pgaout) if perfil else None
        tau = perfil.tau_bp_s if perfil and perfil.tau_bp_s else TAU_REFERENCIA_S
        if punto:
            print(f"1) Punto del perfil: IDAC "
                  + " ".join(f"{c:+d}" for c in punto), flush=True)
            for etapa, codigo in zip(range(4), punto):
                lab.set_idac(etapa, codigo)
        else:
            print("1) Sin perfil: fase 1 a lazo abierto", flush=True)
            cal = TwoPhaseCalibrator(lab, SALIDA / "conflicto_fase1.json")
            cal.fase1_margen()
            lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))
            lab.set_idac(2, 0)
            lab.set_idac(3, 0)
        print(f"   esperando {3.0 * tau:.0f} s el transitorio", flush=True)
        time.sleep(3.0 * tau)

        print("\n2) IDAC2 centra el sumador", flush=True)
        centro, ch2, ch2_ok, motivo = bisectar(
            lab, 2, CANAL_SUM, -IDAC2_LIMITE, IDAC2_LIMITE, args.espera, print)
        if motivo:
            print(f"   {motivo}: IDAC2 no centra el sumador. A PGA x{args.pga} "
                  "no hay punto de trabajo y no tiene sentido seguir.",
                  flush=True)
            return 2
        print(f"   IDAC2={centro:+d} deja ch2 en {ch2:+.1f} mV", flush=True)

        print("\n3) Barrido alrededor del centro, leyendo las dos etapas",
              flush=True)
        mitad = args.puntos // 2
        filas = []
        for i in range(-mitad, mitad + 1):
            codigo = max(-IDAC2_LIMITE, min(IDAC2_LIMITE, centro + i * args.paso))
            lab.set_idac(2, codigo)
            time.sleep(args.espera)
            v2, ok2 = leer_promedio(lab, CANAL_SUM)
            v4, ok4 = leer_promedio(lab, CANAL_LPO)
            filas.append((codigo, v2, ok2, v4, ok4))
            print(f"   IDAC2={codigo:+4d}  ch2={v2:+9.1f}"
                  + ("" if ok2 else "*")
                  + f"  LPo={v4:+9.1f}" + ("" if ok4 else "*"), flush=True)
        lab.set_idac(2, centro)

        print("\n=== VEREDICTO ===", flush=True)
        buenos = [(c, v2, v4) for c, v2, ok2, v4, ok4 in filas if ok2 and ok4]
        if len(buenos) < 2:
            print("Menos de dos puntos con las DOS etapas en ventana. A esta "
                  "ganancia el sumador y el pasabajos no conviven, y eso ya es "
                  "el veredicto.")
            en_ventana2 = sum(1 for _c, _v2, ok2, _v4, ok4 in filas if ok2)
            en_ventana4 = sum(1 for _c, _v2, ok2, _v4, ok4 in filas if ok4)
            print(f"  puntos con ch2 en ventana: {en_ventana2} de {len(filas)}")
            print(f"  puntos con LPo en ventana: {en_ventana4} de {len(filas)}")
            return 1

        # Pendientes por minimos cuadrados sobre los puntos buenos.
        n = len(buenos)
        mx = sum(c for c, _, _ in buenos) / n
        m2 = sum(v for _, v, _ in buenos) / n
        m4 = sum(v for _, _, v in buenos) / n
        sxx = sum((c - mx) ** 2 for c, _, _ in buenos)
        d2 = sum((c - mx) * (v - m2) for c, v, _ in buenos) / sxx
        d4 = sum((c - mx) * (v - m4) for c, _, v in buenos) / sxx
        print(f"IDAC2 -> ch2: {d2:+.2f} mV/codigo")
        print(f"IDAC2 -> LPo: {d4:+.2f} mV/codigo")
        if abs(d2) > 1e-9:
            print(f"Ganancia de la etapa LP desde el sumador: {d4 / d2:+.2f}")

        # Donde estaria el centro de LPo, extrapolando desde los puntos buenos.
        if abs(d4) > 1e-9:
            codigo_lpo = mx - m4 / d4
            aparte = codigo_lpo - centro
            print(f"\nCentro del sumador:  IDAC2 = {centro:+d}")
            print(f"Centro de LPo:       IDAC2 = {codigo_lpo:+.1f} "
                  f"({aparte:+.1f} codigos de distancia)")
            deficit = abs(aparte * d4)
            print(f"Con el sumador centrado, LPo queda a {deficit:.0f} mV de "
                  "su centro.")
            print(f"IDAC3 alcanza {IDAC3_ALCANCE_MV:.0f} mV.")
            if deficit <= IDAC3_ALCANCE_MV:
                print(f"\nENTRA: x{args.pgaout} es calibrable dejando el "
                      "sumador centrado y tapando el resto con IDAC3.")
            else:
                falta = deficit / IDAC3_ALCANCE_MV
                print(f"\nNO ENTRA: haria falta {falta:.1f} veces la autoridad "
                      "de IDAC3. La resistencia de Vref_LP tendria que ser "
                      f"{10.0 * falta:.0f} kOhm en vez de 10.")
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
