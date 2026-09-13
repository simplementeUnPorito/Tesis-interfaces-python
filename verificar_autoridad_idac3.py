"""Mide la autoridad REAL de IDAC3, con LPo dentro de rango.

Todas las mediciones anteriores de este actuador se hicieron con LPo contra un
riel, donde ningun actuador se ve, y de ahi salieron dos conclusiones falsas
seguidas: primero que no servia, despues que su paso era de 800 a 2.000 mV por
codigo. La segunda es imposible: con 6,68 kohm, 32 uA de fondo de escala y la
ganancia de 6 del pasabajos desde su pin positivo, el paso son 5 mV y la
autoridad total 1,28 V. Lo que se midio fue la cadena derivando sola durante un
barrido de varios minutos, atribuido al codigo que se estaba cambiando.

La unica medicion honesta necesita LPo DENTRO de rango, y eso es justo lo que
consigue el lazo cerrado. Asi que primero se cierra el lazo y despues se mide.

Prediccion a contrastar, con la resistencia de 385 ohm puesta hoy:

    paso     = (32/255) uA * 385 ohm * 6 = 0,29 mV por codigo
    autoridad= 255 codigos * 0,29        = 74 mV en total

Si el barrido completo mueve LPo unas decenas de milivolts, el modelo es
correcto y la resistencia que corresponde es de 15 kohm. Si mueve volts, el
modelo esta mal y hay que entender por que antes de tocar nada.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from autocalibracion_dos_fases import TwoPhaseCalibrator
from cerrar_lazo import (CANAL_LPO, CANAL_SUM, ETAPA_FINA, ETAPA_GRUESA,
                         SALIDA, TAU_REFERENCIA_S, leer, llevar_a_zona_lineal,
                         medir_signo, regular)
from regulador_pi import ReguladorCascada
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

#: Del datasheet y del codigo generado: los cuatro IDAC estan en RANGE_32uA.
FONDO_ESCALA_UA = 32.0
CODIGOS = 255
#: Ganancia del pasabajos desde su pin positivo, (150/30 + 1) segun Elias.
GANANCIA_LP_DESDE_REF = 6.0


def paso_predicho_mv(resistencia_ohm: float) -> float:
    """mV en LPo por codigo de IDAC3, segun el modelo."""
    corriente_por_codigo_a = (FONDO_ESCALA_UA / CODIGOS) * 1e-6
    return corriente_por_codigo_a * resistencia_ohm * GANANCIA_LP_DESDE_REF * 1000.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=50)
    ap.add_argument("--pgaout", type=int, default=4)
    ap.add_argument("--resistencia", type=float, default=385.0,
                    help="la que este puesta, sólo para contrastar la predicción")
    ap.add_argument("--espera", type=float, default=3.0 * TAU_REFERENCIA_S)
    args = ap.parse_args()

    paso = paso_predicho_mv(args.resistencia)
    print(f"Resistencia declarada: {args.resistencia:.0f} ohm", flush=True)
    print(f"Predicción: {paso:.2f} mV por código, "
          f"{paso * CODIGOS / 1000.0:.2f} V de autoridad total.\n", flush=True)

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))

        print("Adelanto a lazo abierto (fase 1)...", flush=True)
        cal = TwoPhaseCalibrator(lab, SALIDA / "verificacion_idac3.json")
        cal.fase1_margen()
        idac0 = cal.codes[0]
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))
        print(f"  IDAC0={idac0:+d} IDAC1={cal.codes[1]:+d}", flush=True)
        time.sleep(3.0 * TAU_REFERENCIA_S)

        # El sumador tiene que RESPONDER, no sólo estar en rango.
        k2, motivo = medir_signo(lab, ETAPA_FINA, CANAL_SUM, 0, 8, 3.0)
        if k2 is None:
            print(f"  ch2 no responde: {motivo}. Se lo lleva a su zona lineal.",
                  flush=True)
            idac0, ok = llevar_a_zona_lineal(
                lab, CANAL_SUM, ETAPA_FINA, 0, idac0, -1.0,
                1.5 * TAU_REFERENCIA_S)
            if not ok:
                print("  No se pudo. Sin un sumador que responda no hay nada "
                      "que medir.", flush=True)
                return 1
            k2, motivo = medir_signo(lab, ETAPA_FINA, CANAL_SUM, 0, 8, 3.0)
            if k2 is None:
                print(f"  Sigue sin responder: {motivo}", flush=True)
                return 1
        k0, _ = medir_signo(lab, 0, CANAL_SUM, idac0, 3,
                            1.5 * TAU_REFERENCIA_S)
        if k0 is None:
            k0 = -230.0
        print(f"  IDAC2->ch2 {k2:+.2f}, IDAC0->ch2 {k0:+.2f} mV/código\n",
              flush=True)

        # Se lleva ch2 a una consigna razonable y despues se BARRE IDAC3.
        #
        # Antes se buscaba una consigna de ch2 que dejara LPo en rango, y con la
        # resistencia de 385 ohm no habia ninguna: LPo quedaba topado 300 mV
        # afuera y el sumador no lo movia. Con una resistencia que le de
        # autoridad, es IDAC3 el que tiene que ir a buscarlo, que es justamente
        # para lo que existe.
        reg = ReguladorCascada(ganancia_fina=k2, ganancia_gruesa=k0,
                               tau_s=TAU_REFERENCIA_S, periodo_s=5.0,
                               referencia_real_mv=-1600.0,
                               tolerancia_real_mv=40.0,
                               codigo_fino=0, codigo_grueso=idac0)
        print("  llevando ch2 a -1600 mV con el lazo", flush=True)
        regular(lab, CANAL_SUM, reg, _NoEscribe(), 0.0, "prep", 3.0, 40.0, 0)
        ch2, _ = leer(lab, CANAL_SUM)
        print(f"    ch2 quedo en {ch2:+.0f} mV", flush=True)

        print(f"\nBarriendo IDAC3 con esperas de {args.espera:.0f} s.\n",
              flush=True)
        lecturas = []
        for codigo in (-200, -100, 0, 100, 200):
            lab.set_idac(3, codigo)
            time.sleep(args.espera)
            valor, valido = leer(lab, CANAL_LPO)
            estado = "" if valido else "   FUERA DE RANGO"
            print(f"  IDAC3={codigo:+5d} -> LPo "
                  + ("sin lectura" if valor is None else f"{valor:+9.1f} mV")
                  + estado, flush=True)
            if valor is not None and valido:
                lecturas.append((codigo, valor))
        lab.set_idac(3, 0)

        print("\n=== VEREDICTO ===", flush=True)
        if len(lecturas) < 2:
            print("Menos de dos lecturas válidas: LPo se fue de rango durante "
                  "el barrido y no se puede concluir nada.")
            return 1
        excursion = max(v for _, v in lecturas) - min(v for _, v in lecturas)
        span = lecturas[-1][0] - lecturas[0][0]
        medido = excursion / abs(span) if span else 0.0
        print(f"Excursión medida: {excursion:.0f} mV sobre {abs(span)} códigos")
        print(f"Paso medido:    {medido:.3f} mV/código")
        print(f"Paso predicho:  {paso:.3f} mV/código")
        razon = medido / paso if paso else 0.0
        print(f"Razón medido/predicho: {razon:.2f}")
        if 0.5 <= razon <= 2.0:
            recomendada = args.resistencia * (11.0 / medido) if medido else 0.0
            print("\nEL MODELO SE CONFIRMA. La autoridad es la que dice la "
                  "física, y la resistencia actual es demasiado chica.")
            print(f"Para 11 mV/código haría falta {recomendada:.0f} ohm, o sea "
                  "los 15 k recomendados.")
        else:
            print("\nEL MODELO NO SE CONFIRMA. Antes de cambiar ninguna "
                  "resistencia hay que entender por qué, porque ya hubo dos "
                  "conclusiones falsas sobre este actuador.")
        return 0
    finally:
        try:
            lab.set_gain("pgaout", 0)
            for etapa in range(4):
                lab.set_idac(etapa, 0)
        except Exception:
            pass
        console.close()


class _NoEscribe:
    """El lazo quiere un escritor de CSV; acá no interesa guardar nada."""

    def writerow(self, fila) -> None:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
