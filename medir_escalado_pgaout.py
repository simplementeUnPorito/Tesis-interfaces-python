"""Mide cómo escalan con PGAout la autoridad y la RESOLUCIÓN de cada IDAC.

LAS DOS PREGUNTAS DE ELÍAS QUE ESTO CONTESTA (2026-09-05)

  1. "¿cómo podríamos hacer cambios mínimos para que funcione con el PGAout a
     mayores ganancias? porque si el PGAout sólo puede ganar 1 lo quitamos"
  2. "¿si finetuneamos los valores de resistencia para cada etapa, sería posible
     controlar mejor los desbalances y así permitirnos mayores ganancias?"

POR QUÉ NO SE PUEDEN CONTESTAR SIN ESTA MEDICIÓN
Las dos dependen de un único hecho que todavía no está medido: **si el IDAC de
cada etapa inyecta antes o después de PGAout**. Cambia todo:

  - Si inyecta ANTES, su efecto sobre el tap se multiplica por la ganancia de
    PGAout. Entonces gana autoridad, pero PIERDE resolución en la misma
    proporción: un código pasa a valer Go veces más.
  - Si inyecta DESPUÉS, su escalón se mantiene, así que conserva la resolución
    pero no puede corregir nada de lo que ocurrió antes de PGAout.

Y de ahí sale directamente la respuesta a la pregunta de las resistencias, que
es la parte contraintuitiva: **la resistencia fija la ESCALA, no el RANGO
DINÁMICO.** Un IDAC de 8 bits da 255 pasos; cambiar R hace los 255 pasos más
grandes o más chicos, pero siguen siendo 255. Y lo que hace falta al subir
PGAout es autoridad ∝ Go *y a la vez* resolución ∝ 1/Go, o sea rango dinámico
∝ Go². Ninguna resistencia da eso.

Lo que sí puede funcionar es que DOS etapas se repartan el trabajo: una gruesa y
una fina, tipo DAC de dos rangos. Para que eso cubra todo el recorrido sin
huecos hace falta que **la autoridad de la fina supere un escalón de la gruesa**.
Ése es un número concreto, y es exactamente lo que esta medición produce.

QUÉ MIDE
Para cada ganancia de PGAout, con el PGA de entrada fijo:
  1. lleva ch3 a rango moviendo el ADDER (bisección, sin usar ninguna matriz);
  2. mide la pendiente del IDAC del ADDER sobre ch3;
  3. mide la pendiente del IDAC del LP sobre ch3;
  4. informa autoridad y escalón de cada uno, y si la fina cubre un paso de la
     gruesa.

    python medir_escalado_pgaout.py --port COM8 --pga 0 --outs 0,1,2,3,4
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from testbench.core import console as con                  # noqa: E402
from testbench.core.lab import GAIN_CODES, Lab             # noqa: E402
from testbench.core.session import Session                 # noqa: E402
from buscar_max_pgaout import (                            # noqa: E402
    CANALES, MARGEN, RIEL_ALTO, RIEL_BAJO, TAU_S, centrar_tap, en_riel, leer,
)

SALIDA = REPO / "lab" / "planta"
#: Escalón para la derivada. Chico para no salirse de la zona lineal, pero no
#: tanto que el ruido lo tape: con 4 códigos el ADDER mueve ch3 unos 15 mV a
#: PGAout x1, que son ~250 veces el ruido de una medida.
DELTA = 4


def pendiente(lab: Lab, etapa: int, base: int, tap: int, espera: float,
              log) -> tuple[float | None, float | None]:
    """(µV por código sobre `tap`, valor central) midiendo ±DELTA en el lugar."""
    vals = {}
    for signo in (-1, +1):
        cod = max(-255, min(255, base + signo * DELTA))
        lab.set_idac(etapa, cod)
        time.sleep(espera)
        vals[signo] = (cod, leer(lab, (tap,))[tap])
    lab.set_idac(etapa, base)
    (c_lo, v_lo), (c_hi, v_hi) = vals[-1], vals[+1]
    if v_lo is None or v_hi is None or c_hi == c_lo:
        log(f"      etapa {etapa} -> tap {tap}: sin lectura")
        return None, None
    if en_riel(tap, v_lo) or en_riel(tap, v_hi):
        log(f"      etapa {etapa} -> tap {tap}: uno de los dos puntos quedo en "
            f"el riel ({v_lo} / {v_hi}); la pendiente no seria real")
        return None, None
    p = 1000.0 * (v_hi - v_lo) / (c_hi - c_lo)
    return p, (v_lo + v_hi) / 2.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=None)
    ap.add_argument("--pga", type=int, default=0)
    ap.add_argument("--outs", default="0,1,2,3,4")
    ap.add_argument("--tau-busqueda", type=float, default=1.0)
    ap.add_argument("--tau-medicion", type=float, default=1.5)
    a = ap.parse_args()

    eb, em = a.tau_busqueda * TAU_S, a.tau_medicion * TAU_S
    outs = [int(x) for x in a.outs.split(",")]

    c = con.Console(a.port) if a.port else con.Console()
    print(f"Abriendo {c.port}...", flush=True)
    c.open(); time.sleep(1.0)
    lab = Lab(Session(c))
    filas = []

    def log(t):
        print(t, flush=True)

    try:
        lab.set_gain("pga", a.pga)
        for oc in outs:
            go = GAIN_CODES[oc]
            print(f"\n=== PGA x{GAIN_CODES[a.pga]}  PGAout x{go} ===", flush=True)
            lab.set_gain("pgaout", oc)
            for k in CANALES:
                lab.set_idac(k, 0)
            time.sleep(eb)

            v = leer(lab, (3,))[3]
            base2 = 0
            if v is None or en_riel(3, v):
                log(f"    ch3 en {v} mV: hay que traerlo a rango con el ADDER")
                cod = centrar_tap(lab, 3, 2, eb, log, objetivo=1000.0)
                if cod is None:
                    filas.append({"pgaout_x": go, "estado": "sin ventana"})
                    continue
                base2 = cod
            else:
                log(f"    ch3 ya en rango ({v:.1f} mV) con el ADDER en 0")

            # LAS CUATRO ETAPAS, no solo el ADDER y el LP.
            #
            # Lo pidio Elias y tiene razon: si se descalibra a proposito el BP o
            # el PGA de entrada se puede anular el desbalance ANTES de que
            # PGAout lo amplifique, y ahi PGAout deja de ser inutil. Para saber
            # si eso funciona hay que conocer el paso y el recorrido de LOS
            # CUATRO sobre ch3, no de dos.
            #
            # La sospecha, que esta medicion confirma o descarta: el IDAC del
            # PGA de entrada escala con la ganancia del PGA -medido en EXP1- asi
            # que a x50 su paso se vuelve grosero y deja de servir como ajuste
            # fino, mientras que el del BP NO escala y conserva su finura. Si es
            # asi, el par ADDER (grueso) + BP (fino) es el que hay que usar.
            pend = {}
            NOMBRE = {0: "PGA  ", 1: "BP   ", 2: "ADDER", 3: "LP   "}
            for etapa in CANALES:
                base = base2 if etapa == 2 else 0
                pend[etapa], _ = pendiente(lab, etapa, base, 3, em, log)
            fila = {"pgaout_x": go, "base_adder": base2, "estado": "medido",
                    "uv_por_codigo_sobre_ch3": dict(pend)}
            filas.append(fila)
            for etapa in CANALES:
                pe = pend[etapa]
                if pe is None:
                    log(f"    {NOMBRE[etapa]}: sin medida")
                else:
                    log(f"    {NOMBRE[etapa]}: {pe:10.1f} uV/cod   "
                        f"recorrido +-{abs(pe) * 255 / 1000:8.1f} mV")

            # El criterio que decide si un par (grueso, fino) sirve: el recorrido
            # del fino tiene que cubrir un escalon del grueso, o quedan huecos
            # que ningun codigo alcanza.
            grueso = max((e for e in CANALES if pend[e]), key=lambda e: abs(pend[e]),
                         default=None)
            finos = [e for e in CANALES if pend[e] and e != grueso]
            if grueso is not None and finos:
                fino = min(finos, key=lambda e: abs(pend[e]))
                cubre = abs(pend[fino]) * 255.0 >= abs(pend[grueso])
                resid_mv = abs(pend[fino]) / 2000.0
                log(f"    par sugerido: grueso={NOMBRE[grueso].strip()} "
                    f"fino={NOMBRE[fino].strip()}  ->  "
                    f"{'encadenan' if cubre else 'DEJAN HUECO'}; "
                    f"residual teorico +-{resid_mv:.2f} mV")
                fila["grueso"] = grueso
                fila["fino"] = fino
                fila["encadenan"] = cubre
                fila["residual_teorico_mv"] = resid_mv
    finally:
        c.close()

    print("\n" + "=" * 76)
    print("paso de cada IDAC sobre ch3, en uV por codigo")
    print(f"{'PGAout':>7} {'PGA':>11} {'BP':>11} {'ADDER':>11} {'LP':>11} "
          f"{'residual mV':>12} {'par':>14}")
    for f in filas:
        if f.get("estado") != "medido":
            print(f"{'x' + str(f['pgaout_x']):>7}   {f.get('estado', '')}")
            continue
        p = f["uv_por_codigo_sobre_ch3"]
        celdas = "".join(
            (f"{p[e]:11.1f}" if p.get(e) is not None else f"{'-':>11}")
            for e in (0, 1, 2, 3))
        r = f.get("residual_teorico_mv")
        par = ""
        if f.get("grueso") is not None:
            nom = {0: "PGA", 1: "BP", 2: "ADDER", 3: "LP"}
            par = f"{nom[f['grueso']]}+{nom[f['fino']]}"
            if not f.get("encadenan"):
                par += "!"
        print(f"{'x' + str(f['pgaout_x']):>7} {celdas} "
              f"{(f'{r:12.2f}' if r is not None else '           -')} {par:>14}")
    print("  ! = el fino NO cubre un escalon del grueso: quedan huecos")

    SALIDA.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta = SALIDA / f"escalado_pgaout_pga{a.pga}_{stamp}.json"
    ruta.write_text(json.dumps({"pga_x": GAIN_CODES[a.pga], "filas": filas},
                               indent=1), encoding="utf-8")
    print(f"\ncrudo -> {ruta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
