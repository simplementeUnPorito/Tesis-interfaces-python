"""Contesta una sola pregunta: IDAC3 mueve LPo, si o no.

Todas las mediciones de este actuador hasta hoy se hicieron con LPo contra un
riel, donde ningun actuador se ve. La corrida del 2026-09-13 barrio IDAC3 de
-255 a +255 con 10 kohm puestos y LPo no se movio ni un milivolt: quedo clavado
en +2.300,0. Con 10 kohm el paso predicho son 7,5 mV por codigo y la autoridad
+-1,9 V, o sea que la falta de respuesta no puede ser de resolucion.

Hay dos explicaciones posibles y este script las separa:

  a) LPo esta saturado por lo que le llega de aguas arriba, y ningun actuador
     local alcanza. Se descarta poniendo PGAout en x1, donde la cadena entera
     entra comoda, y verificando que LPo quede DENTRO de la ventana antes de
     tocar IDAC3.
  b) La corriente de IDAC3 no desarrolla tension porque el nodo Vref del
     pasabajos lo maneja algo de baja impedancia. En ese caso LPo no se mueve
     aunque este dentro de rango, y la resistencia que hay puesta no importa.

El barrido es ABBA para que la deriva lenta de la cadena no se confunda con la
respuesta al codigo.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from autocalibracion_dos_fases import TwoPhaseCalibrator
from cerrar_lazo import CANAL_LPO, CANAL_SUM, SALIDA, leer
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

#: Prediccion del modelo con la resistencia puesta hoy.
GANANCIA_LP_DESDE_REF = 6.0
FONDO_ESCALA_UA = 32.0


def paso_predicho_mv(resistencia_ohm: float) -> float:
    return (FONDO_ESCALA_UA / 255.0) * 1e-6 * resistencia_ohm * GANANCIA_LP_DESDE_REF * 1000.0


def foto(lab: Lab) -> None:
    for canal in (0, 1, 2, 3, 4):
        valor, valido = leer(lab, canal)
        estado = "" if valido else "   FUERA DE RANGO"
        print(f"    ch{canal} = "
              + ("sin lectura" if valor is None else f"{valor:+9.1f} mV")
              + estado, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=1)
    ap.add_argument("--pgaout", type=int, default=1)
    ap.add_argument("--resistencia", type=float, default=10000.0)
    ap.add_argument("--delta", type=int, default=100)
    ap.add_argument("--espera", type=float, default=6.0)
    ap.add_argument("--bloques", type=int, default=4)
    ap.add_argument("--asentar", type=float, default=120.0,
                    help="espera del transitorio del pasabanda")
    args = ap.parse_args()

    paso = paso_predicho_mv(args.resistencia)
    print(f"Resistencia {args.resistencia:.0f} ohm -> predicho "
          f"{paso:.2f} mV/codigo, {paso * args.delta:.0f} mV para "
          f"{args.delta} codigos.\n", flush=True)

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))

        print("1) Fase 1 a lazo abierto", flush=True)
        cal = TwoPhaseCalibrator(lab, SALIDA / "diagnostico_idac3.json")
        cal.fase1_margen()
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))
        print(f"   IDAC0={cal.codes[0]:+d} IDAC1={cal.codes[1]:+d}", flush=True)
        lab.set_idac(2, 0)
        lab.set_idac(3, 0)
        print(f"   esperando {args.asentar:.0f} s el transitorio "
              "del pasabanda", flush=True)
        time.sleep(args.asentar)

        print(f"\n2) Estado con PGAout x{args.pgaout}", flush=True)
        foto(lab)
        lpo, lpo_ok = leer(lab, CANAL_LPO)
        if not lpo_ok:
            print("\n   LPo esta fuera de ventana ya en x1. Se busca con IDAC2 "
                  "un punto donde entre, porque sin eso no se puede medir "
                  "nada.", flush=True)
            for codigo in range(-150, 151, 15):
                lab.set_idac(2, codigo)
                time.sleep(4.0)
                lpo, lpo_ok = leer(lab, CANAL_LPO)
                ch2, _ = leer(lab, CANAL_SUM)
                print(f"     IDAC2={codigo:+4d}  ch2={ch2:+8.1f}  LPo="
                      + ("sin lectura" if lpo is None else f"{lpo:+9.1f}")
                      + ("" if lpo_ok else "  fuera"), flush=True)
                if lpo_ok:
                    break
            if not lpo_ok:
                print("\n   Ningun codigo de IDAC2 mete a LPo en la ventana ni "
                      "en x1. El problema no es IDAC3.", flush=True)
                return 2

        print(f"\n3) ABBA sobre IDAC3, {args.bloques} bloques de "
              f"0 / {args.delta:+d}", flush=True)
        diferencias = []
        for bloque in range(args.bloques):
            lecturas = {}
            for codigo in (0, args.delta, args.delta, 0):
                lab.set_idac(3, codigo)
                time.sleep(args.espera)
                valor, valido = leer(lab, CANAL_LPO)
                lecturas.setdefault(codigo, []).append(valor if valido else None)
                print(f"     IDAC3={codigo:+4d} -> LPo "
                      + ("sin lectura" if valor is None else f"{valor:+9.1f} mV")
                      + ("" if valido else "  FUERA"), flush=True)
            bajos = [v for v in lecturas.get(0, []) if v is not None]
            altos = [v for v in lecturas.get(args.delta, []) if v is not None]
            if bajos and altos:
                d = statistics.fmean(altos) - statistics.fmean(bajos)
                diferencias.append(d)
                print(f"   bloque {bloque}: {d:+.1f} mV por {args.delta} "
                      f"codigos = {d / args.delta:+.3f} mV/codigo", flush=True)
        lab.set_idac(3, 0)

        print("\n=== VEREDICTO ===", flush=True)
        if not diferencias:
            print("Ningun bloque dio dos lecturas validas. Sin dato.")
            return 2
        medio = statistics.fmean(diferencias) / args.delta
        print(f"Paso medido:   {medio:+.3f} mV/codigo")
        print(f"Paso predicho: {paso:+.3f} mV/codigo")
        if abs(medio) < 0.3:
            print("\nIDAC3 NO MUEVE LPo con LPo dentro de la ventana. La "
                  "corriente no desarrolla tension: el nodo de referencia del "
                  "pasabajos lo maneja algo de baja impedancia, o la "
                  "resistencia no esta donde creemos. No sirve como actuador "
                  "y el lazo fino tiene que salir de otro lado.")
            return 1
        razon = medio / paso if paso else 0.0
        print(f"Razon medido/predicho: {razon:+.2f}")
        print("\nIDAC3 SI MUEVE LPo. Sirve como actuador fino y es "
              "independiente de la ganancia de PGAout.")
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
