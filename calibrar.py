"""Calibración completa: adelanto a lazo abierto y después lazo cerrado sobre LPo.

Es la versión directa, escrita después de entender tres cosas que costaron caro.

**LPo se regula con IDAC3, no desde el sumador.** IDAC3 entra DESPUÉS del
amplificador de salida, así que su paso sobre LPo no depende de la ganancia de
PGAout: son los mismos milivolts por código en x4 que en x50. Todo el problema de
resolución que hacía imposible x16 para arriba venía de intentar centrar LPo
desde ch2, que está antes y por eso se multiplica por la ganancia.

**Con 10 kΩ, IDAC3 tiene ±1,92 V de autoridad** y 7,5 mV por código. El compliance
del IDAC8 es 1,0 V respecto de cada riel, y con la resistencia a VDDA/2 el nodo se
mueve ±0,32 V alrededor de 2,4: queda cómodamente adentro.

**Un tap saturado no responde a nada.** Antes de medir o regular cualquier cosa
hay que comprobar que responda a su propio actuador, y que responda con una
fracción razonable de lo nominal, no con cualquier cosa distinta de cero.

Uso:

    py -3 calibrar.py --port COM8 --pgaout 4
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from autocalibracion_dos_fases import TwoPhaseCalibrator
from cerrar_lazo import (CANAL_LPO, CANAL_SUM, ETAPA_FINA, SALIDA,
                         TAU_REFERENCIA_S, leer, llevar_a_zona_lineal,
                         medir_signo, regular)
from regulador_pi import ReguladorCascada
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

ETAPA_LP = 3
#: Con 10 kOhm: (32/255) uA * 10k * 6 = 7,5 mV por codigo sobre LPo.
PASO_IDAC3_NOMINAL_MV = 7.5


def rastrear_idac3(lab: Lab, espera_s: float,
                   paso: int = 20) -> tuple[int | None, float]:
    """Busca un código de IDAC3 que deje LPo dentro de rango.

    Se recorre el rango COMPLETO en los dos signos. Nada de bisección: una
    búsqueda que se supone ciega no puede suponer hacia dónde crece la señal, y
    ese error ya costó dos diagnósticos falsos.
    """
    mejor, mejor_dist = None, float("inf")
    for codigo in range(-255, 256, paso):
        lab.set_idac(ETAPA_LP, codigo)
        time.sleep(espera_s)
        valor, valido = leer(lab, CANAL_LPO)
        if valor is None:
            continue
        print(f"    IDAC3={codigo:+5d} -> LPo {valor:+9.1f} mV"
              + ("   DENTRO" if valido else ""), flush=True)
        if valido:
            return codigo, valor
        if abs(valor) < mejor_dist:
            mejor, mejor_dist = codigo, abs(valor)
    if mejor is not None:
        lab.set_idac(ETAPA_LP, mejor)
    return None, float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=50)
    ap.add_argument("--pgaout", type=int, default=4)
    ap.add_argument("--minutos", type=float, default=10.0)
    ap.add_argument("--periodo", type=float, default=5.0)
    ap.add_argument("--tolerancia", type=float, default=100.0)
    ap.add_argument("--consigna-ch2", type=float, default=0.0,
                    help="a dónde llevar el sumador antes de tocar LPo")
    args = ap.parse_args()

    SALIDA.mkdir(parents=True, exist_ok=True)
    destino = SALIDA / ("calibrar_x" + str(args.pgaout) + "_"
                        + time.strftime("%Y%m%d_%H%M%S") + ".csv")
    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    salida = destino.open("w", newline="", encoding="utf-8")
    escritor = csv.writer(salida)
    escritor.writerow(["t_s", "etapa", "medido_real_mv", "valido", "error_mv",
                       "idac1", "idac2", "paso_fino", "paso_grueso", "motivo"])
    t0 = time.time()

    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))

        print(f"=== PGA x{args.pga}, PGAout x{args.pgaout} ===\n", flush=True)
        print("1) Adelanto a lazo abierto (fase 1)", flush=True)
        cal = TwoPhaseCalibrator(lab, SALIDA / "adelanto.json")
        cal.fase1_margen()
        idac0 = cal.codes[0]
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))
        print(f"   IDAC0={idac0:+d} IDAC1={cal.codes[1]:+d}", flush=True)
        time.sleep(3.0 * TAU_REFERENCIA_S)

        print("\n2) El sumador tiene que RESPONDER, no sólo estar en rango",
              flush=True)
        k2, motivo = medir_signo(lab, ETAPA_FINA, CANAL_SUM, 0, 8, 3.0)
        if k2 is None:
            print(f"   no responde ({motivo}); se lo lleva a su zona lineal",
                  flush=True)
            idac0, ok = llevar_a_zona_lineal(
                lab, CANAL_SUM, ETAPA_FINA, 0, idac0, -1.0,
                1.5 * TAU_REFERENCIA_S)
            if not ok:
                print("   No responde en todo el recorrido de IDAC0. Sin un "
                      "tap que responda no hay calibración posible.",
                      flush=True)
                return 1
            k2, _ = medir_signo(lab, ETAPA_FINA, CANAL_SUM, 0, 8, 3.0)
            if k2 is None:
                return 1
        print(f"   IDAC2 -> ch2: {k2:+.2f} mV/código", flush=True)
        # El actuador de entrada se mide con UN codigo, no con tres.
        #
        # Con tres, la excursion son unos 600 mV sobre ch2 y si el tap esta
        # cerca de su borde un lado se recorta: el 2026-09-13 dio -31 mV/codigo
        # cuando la real ronda -200, y el lazo, con la ganancia seis veces
        # subestimada, se paso hasta -5.172 mV.
        k0, _ = medir_signo(lab, 0, CANAL_SUM, idac0, 1, 1.5 * TAU_REFERENCIA_S)
        if k0 is None or abs(k0) < 50.0:
            # Por debajo de 50 mV/codigo la medicion no es plausible para este
            # actuador: su paso nominal sobre ch2 son unos 200.
            print(f"   IDAC0 -> ch2: medicion no plausible "
                  f"({0.0 if k0 is None else k0:+.1f}); se usa -200 nominal",
                  flush=True)
            k0 = -200.0
        else:
            print(f"   IDAC0 -> ch2: {k0:+.2f} mV/código", flush=True)

        print(f"\n3) Lazo sobre el sumador hasta {args.consigna_ch2:+.0f} mV",
              flush=True)
        reg_ch2 = ReguladorCascada(ganancia_fina=k2, ganancia_gruesa=k0,
                                   tau_s=TAU_REFERENCIA_S,
                                   periodo_s=args.periodo,
                                   referencia_real_mv=args.consigna_ch2,
                                   tolerancia_real_mv=60.0,
                                   codigo_fino=0, codigo_grueso=idac0,
                                   # El fino tiene 15,86 mV/codigo por 160
                                   # codigos, o sea 2,5 V de autoridad sobre
                                   # ch2: alcanza solo. El grueso queda como
                                   # reserva y no entra hasta que el fino este
                                   # casi agotado, porque su paso es enorme y
                                   # su ganancia dificil de medir bien.
                                   zona_comoda=140)
        ok_ch2, err_ch2 = regular(lab, CANAL_SUM, reg_ch2, escritor, t0,
                                  "sumador", args.minutos / 3.0, 60.0, 0)
        ch2, ch2_ok = leer(lab, CANAL_SUM)
        print(f"   sumador en {ch2:+.0f} mV"
              + ("" if ch2_ok else "   FUERA DE RANGO"), flush=True)

        print("\n4) IDAC3 va a buscar la ventana de LPo", flush=True)
        codigo3, lpo = rastrear_idac3(lab, 8.0)
        if codigo3 is None:
            print("\n   Ningún código de IDAC3 deja LPo dentro de rango. Con "
                  "±1,9 V de autoridad, eso significa que LPo está más lejos "
                  "que eso y hace falta corregir desde el sumador primero.",
                  flush=True)
            return 1
        print(f"   IDAC3={codigo3:+d} deja LPo en {lpo:+.0f} mV", flush=True)

        print("\n5) Lazo cerrado sobre LPo con IDAC3", flush=True)
        k3, motivo = medir_signo(lab, ETAPA_LP, CANAL_LPO, codigo3, 10, 8.0)
        if k3 is None:
            print(f"   IDAC3 -> LPo: {motivo}", flush=True)
            k3 = PASO_IDAC3_NOMINAL_MV
        print(f"   IDAC3 -> LPo: {k3:+.2f} mV/código "
              f"(nominal {PASO_IDAC3_NOMINAL_MV})", flush=True)

        reg_lpo = ReguladorCascada(ganancia_fina=k3, ganancia_gruesa=k3 * 10.0,
                                   tau_s=TAU_REFERENCIA_S,
                                   periodo_s=args.periodo,
                                   tolerancia_real_mv=args.tolerancia,
                                   codigo_fino=codigo3, codigo_grueso=0,
                                   limite_fino=255, zona_comoda=200)
        ok_lpo, err_lpo = _regular_solo_fino(
            lab, reg_lpo, escritor, t0, args.minutos, args.tolerancia)

        print("\n=== RESULTADO ===", flush=True)
        print(f"PGAout x{args.pgaout}")
        print(f"LPo: {'CONVERGIO' if ok_lpo else 'no convergió'}"
              + (f", error final {err_lpo:+.0f} mV" if err_lpo is not None
                 else ""))
        print(f"IDAC3 quedó en {reg_lpo.codigo_fino:+d}")
        print(f"IDAC2 quedó en {reg_ch2.codigo_fino:+d}, "
              f"IDAC0 en {reg_ch2.codigo_grueso:+d}")
        print(f"\nArchivo: {destino}", flush=True)
        return 0 if ok_lpo else 1
    finally:
        try:
            lab.set_gain("pgaout", 0)
            for etapa in range(4):
                lab.set_idac(etapa, 0)
        except Exception:
            pass
        salida.close()
        console.close()


def _regular_solo_fino(lab: Lab, reg: ReguladorCascada, escritor, t0: float,
                       minutos: float,
                       tolerancia_mv: float) -> tuple[bool, float | None]:
    """Lazo sobre LPo moviendo SOLO IDAC3.

    No hay cascada acá: IDAC3 es local a la etapa y tiene autoridad de sobra, así
    que meter un segundo actuador sólo agregaría acoplamiento con lo de aguas
    arriba, que es lo que se quiere evitar.
    """
    fin = time.time() + minutos * 60.0
    ultimo, ciegos = None, 0
    while time.time() < fin:
        valor, valido = leer(lab, CANAL_LPO)
        if valor is None:
            time.sleep(reg.periodo_s)
            continue
        s = reg.paso(valor, valido)
        if s.paso_fino:
            lab.set_idac(ETAPA_LP, s.codigo_fino)
        t = time.time() - t0
        escritor.writerow([f"{t:.1f}", "LPo", f"{valor:.1f}", valido,
                           "" if s.error_real_mv != s.error_real_mv
                           else f"{s.error_real_mv:.1f}",
                           "", s.codigo_fino, s.paso_fino, 0, s.motivo])
        estado = ("FUERA DE RANGO" if not valido
                  else f"error {s.error_real_mv:+8.1f}")
        print(f"   t={t:6.0f}s  LPo={valor:+9.1f}  {estado}"
              f"  IDAC3={s.codigo_fino:+4d}"
              + (f"  {s.motivo}" if s.motivo else ""), flush=True)
        if valido:
            ultimo, ciegos = s.error_real_mv, 0
        else:
            ciegos += 1
            if ciegos >= 12:
                print("   demasiadas lecturas fuera de rango; se abandona",
                      flush=True)
                return False, ultimo
        if reg.convergido(tolerancia_mv):
            print(f"   CONVERGIDO a los {t:.0f} s", flush=True)
            return True, ultimo
        time.sleep(reg.periodo_s)
    return False, ultimo


if __name__ == "__main__":
    raise SystemExit(main())
