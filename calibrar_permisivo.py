"""Calibración PERMISIVA: gasta desvío en las etapas de arriba para ganar ganancia.

DE DÓNDE SALE
Idea de Elías, 2026-09-05:

    "podrías descalibrar parcialmente BP y/o PGAinput para buscar permitir usar
     más ganancia en PGAout y así LP no driftee. Si lográs que funcione aunque
     sea el ×8 ya se justifica el PGAout muchísimo."

    "si permitís que cada etapa salga de hasta 100 mV pero a cambio tengamos más
     ganancia me parece un win-win"

    "hacé por mínimos, o sea si conseguís menos, mejor; pero si conseguís con
     100 mV desperdiciados en etapas intermedias es aceptable, son intercambios
     justos. En LP sí debemos tratar de reducir al mínimo."

EL PROBLEMA QUE RESUELVE, EN UNA LÍNEA
Lo que PGAout amplifica es el desbalance **δ** que le llega a su entrada. Si δ se
anula ANTES de PGAout, PGAout deja de ser un problema. Los actuadores capaces de
tocar δ son los de aguas arriba, y el método conservador los tenía prohibidos.

EL OBJETIVO, EN ORDEN LEXICOGRÁFICO
    1. que el error del LP sea mínimo, y en todo caso ≤ 20 mV;
    2. entre las soluciones que lo consiguen, la que menos desvíe las etapas
       intermedias, con 100 mV como TECHO y no como objetivo.

Por eso la búsqueda prueba en orden de **costo creciente** y se queda con la
primera que funciona, en vez de ir directo a la más poderosa:

    a) sólo el ADDER            — el más barato: ch2 no se captura
    b) ADDER + ajuste fino del LP
    c) ADDER + BP               — sólo si (a) y (b) no alcanzan

POR QUÉ (c) PUEDE FUNCIONAR DONDE (a) FALLA
Los dos modos de fallo medidos el 2026-09-05 son distintos y el BP ataca los dos:

  - *"los dos extremos en el mismo riel"* = falta AUTORIDAD. El BP suma su
    recorrido al del ADDER: ±99 mV más de δ.
  - *"la ventana es más angosta que un código"* = falta RESOLUCIÓN. El BP mueve
    ch3 unos 1002 µV/código contra los 3823 del ADDER: es **3,8 veces más fino**,
    y su recorrido cubre 67 escalones del ADDER, así que los dos encadenan sin
    dejar huecos.

Y cuesta poco: mover el BP en todo su rango desvía su propio tap sólo ±23 mV,
sobre 186 mV de margen. Muy por debajo del techo de 100 mV que fijó Elías.

    python calibrar_permisivo.py --port COM8 --pga 8 --outs 0,1,2,3
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
    CANALES, RIEL_ALTO, RIEL_BAJO, TAU_S, en_riel, leer,
)

SALIDA = REPO / "lab" / "planta"

#: Objetivo nominal del firmware. Se sigue informando el error contra esto
#: porque es el criterio con el que Elías viene midiendo.
OBJETIVO_MV = 1000.0

# --------------------------------------------------------------------------
# "NINGUNA ETAPA JAMAS DEBE SATURAR" — Elías, 2026-09-05
#
# Ese requisito obliga a medir el presupuesto de 100 mV **desde el CENTRO de la
# excursión y no desde el objetivo**, y la diferencia no es cosmética.
#
# Los rieles medidos son 750 y 1122 mV, o sea que el centro real es 936 mV y el
# objetivo nominal de 1000 ya está 64 mV corrido hacia arriba. Con un techo de
# ±100 mV medido desde 1000, un tap podría quedar en 1100 mV: a **22 mV del
# riel**, que recorta con cualquier señal encima. El presupuesto se estaría
# gastando entero hacia el lado que ya está más cerca.
#
# Medido desde 936, ±100 mV deja los taps entre 836 y 1036 y garantiza **86 mV
# de margen contra cada riel**, que es lo que "no saturar" significa cuando hay
# señal.
#
# Corolario: el objetivo del LP debería ser el CENTRO y no los 1000 mV. Ahí el
# margen simétrico pasa de +122/−250 mV a ±186 mV, un 52 % más del lado que ata.
# Se sigue apuntando a 1000 para no cambiar el criterio con el que Elías viene
# midiendo, pero se informan las dos cosas.
# --------------------------------------------------------------------------
CENTRO_MV = {c: (RIEL_BAJO[c] + RIEL_ALTO[c]) / 2.0 for c in CANALES}

#: Techo de desvío de las etapas intermedias, DESDE EL CENTRO. Es un techo, no
#: un objetivo: la búsqueda minimiza y esto sólo dice hasta dónde puede llegar.
TECHO_INTERMEDIAS_MV = 100.0
#: Margen mínimo contra cada riel exigido a TODOS los taps. Es la forma
#: operativa de "ninguna etapa jamás debe saturar".
MARGEN_ANTISATURACION_MV = 80.0
#: Lo que se acepta en el LP. Elías: "en LP sí debemos tratar de reducir al
#: mínimo".
TOLERANCIA_LP_MV = 20.0


def margen_a_riel(ch: int, mv):
    """Cuánto le falta al tap para llegar al riel más cercano, en mV."""
    if mv is None:
        return None
    return min(mv - RIEL_BAJO[ch], RIEL_ALTO[ch] - mv)


def desvio_intermedias(v):
    """El peor desvío de las etapas que NO son el LP, medido DESDE EL CENTRO.

    Desde el centro y no desde el objetivo: ver el bloque de arriba. Medirlo
    desde el objetivo dejaria gastar los 100 mV enteros hacia el riel que ya
    esta mas cerca, que es exactamente lo que "no saturar" prohibe.
    """
    vals = [abs(v[c] - CENTRO_MV[c]) for c in (0, 1, 2) if v.get(c) is not None]
    return max(vals) if vals else None


def peor_margen(v):
    """El margen al riel del tap que este mas comprometido."""
    m = [margen_a_riel(c, v.get(c)) for c in CANALES]
    m = [x for x in m if x is not None]
    return min(m) if m else None


def newton(lab: Lab, etapa: int, tap: int, base: int, objetivo: float,
           espera: float, delta: int, log, pasos: int = 4,
           lo: int = -255, hi: int = 255) -> int:
    """Lleva `tap` a `objetivo` moviendo `etapa`, midiendo la pendiente en el lugar.

    Newton con derivada numérica y no bisección: cuando la ventana útil es de
    unos pocos códigos —que es el caso que hace fallar todo con PGAout alto— la
    bisección puede pasarle por al lado, mientras que el Newton salta directo
    apenas tiene una pendiente válida.
    """
    cod = base
    for paso in range(pasos):
        lab.set_idac(etapa, cod)
        time.sleep(espera)
        v0 = leer(lab, (tap,))[tap]
        if v0 is None:
            return cod
        err = objetivo - v0
        if abs(err) < 3.0:
            log(f"      newton etapa {etapa}: cod {cod} -> tap {tap} en {v0:.1f} mV")
            return cod
        prueba = max(lo, min(hi, cod + delta))
        if prueba == cod:
            prueba = max(lo, min(hi, cod - delta))
        if prueba == cod:
            return cod
        lab.set_idac(etapa, prueba)
        time.sleep(espera)
        v1 = leer(lab, (tap,))[tap]
        if v1 is None or v1 == v0:
            return cod
        pend = (v1 - v0) / (prueba - cod)        # mV por codigo
        if abs(pend) < 1e-6:
            return cod
        nuevo = int(round(cod + err / pend))
        nuevo = max(lo, min(hi, nuevo))
        log(f"      newton etapa {etapa}: {v0:.1f} mV, pend {1000 * pend:.0f} uV/cod"
            f" -> cod {nuevo}")
        if nuevo == cod:
            return cod
        cod = nuevo
    return cod


def una(lab: Lab, pga_code: int, out_code: int, eb: float, em: float) -> dict:
    t0 = time.time()
    lineas: list[str] = []

    def log(t):
        print(t, flush=True)
        lineas.append(t)

    lab.set_gain("pga", pga_code)
    lab.set_gain("pgaout", out_code)
    dac = {k: 0 for k in CANALES}
    for k in CANALES:
        lab.set_idac(k, 0)
    time.sleep(eb)

    intentos = []

    # --- (a) sólo el ADDER, que es el actuador más barato ------------------
    log("    (a) solo el ADDER")
    dac[2] = newton(lab, 2, 3, 0, OBJETIVO_MV, em, 4, log)
    time.sleep(em)
    v = leer(lab)
    intentos.append(("solo ADDER", dict(dac), dict(v)))

    def evaluar(v):
        if v.get(3) is None or en_riel(3, v[3]):
            return None, None
        return abs(v[3] - OBJETIVO_MV), desvio_intermedias(v)

    err_lp, desv = evaluar(v)
    log(f"        LP {v.get(3)}  desvio intermedias {desv}")

    # --- (b) + ajuste fino del LP -----------------------------------------
    if err_lp is None or err_lp > 2.0:
        log("    (b) ADDER + ajuste fino del LP")
        dac[3] = newton(lab, 3, 3, dac[3], OBJETIVO_MV, em, 8, log)
        time.sleep(em)
        v = leer(lab)
        intentos.append(("ADDER+LP", dict(dac), dict(v)))
        err_lp, desv = evaluar(v)
        log(f"        LP {v.get(3)}  desvio intermedias {desv}")

    # --- (c) meter el BP, sólo si hace falta -------------------------------
    if err_lp is None or err_lp > TOLERANCIA_LP_MV:
        log("    (c) hace falta el BP: es mas fino que el ADDER (1002 contra "
            "3823 uV/cod) y suma recorrido")
        # El BP se usa para acercar ch3 y despues el LP recorta. Se limita su
        # excursion a lo que mantenga su propio tap dentro del techo de 100 mV:
        # su paso sobre ch1 son ~90 uV/codigo, asi que 100 mV son ~1100 codigos,
        # o sea que el rango entero del IDAC cabe holgado. No hace falta acotarlo.
        dac[1] = newton(lab, 1, 3, 0, OBJETIVO_MV, em, 4, log)
        dac[3] = newton(lab, 3, 3, dac[3], OBJETIVO_MV, em, 8, log)
        time.sleep(em)
        v = leer(lab)
        intentos.append(("ADDER+BP+LP", dict(dac), dict(v)))
        err_lp, desv = evaluar(v)
        log(f"        LP {v.get(3)}  desvio intermedias {desv}")

    railados = [c for c in CANALES if en_riel(c, v.get(c))]
    margen = peor_margen(v)
    # No alcanza con que ningun tap este EN el riel: tiene que quedarle margen
    # para la senal encima. Es la diferencia entre "no satura ahora" y "no
    # satura nunca", que es lo que pidio Elias.
    sin_saturar = margen is not None and margen >= MARGEN_ANTISATURACION_MV
    cumple = (err_lp is not None and err_lp <= TOLERANCIA_LP_MV
              and desv is not None and desv <= TECHO_INTERMEDIAS_MV
              and not railados and sin_saturar)
    log(f"    -> LP {err_lp if err_lp is None else round(err_lp, 2)} mV   "
        f"intermedias {desv if desv is None else round(desv, 1)} mV   "
        f"margen {margen if margen is None else round(margen, 1)} mV   "
        f"{'CUMPLE' if cumple else 'NO CUMPLE'}   {time.time() - t0:.0f} s")
    if margen is not None and not sin_saturar:
        log(f"       el margen de {margen:.0f} mV no llega a los "
            f"{MARGEN_ANTISATURACION_MV:.0f} exigidos: con senal encima "
            f"alguna etapa recortaria")
    return {"pga_x": GAIN_CODES[pga_code], "pgaout_x": GAIN_CODES[out_code],
            "dac": dac, "taps_mv": v, "error_lp_mv": err_lp,
            "desvio_intermedias_mv": desv, "en_riel": railados,
            "peor_margen_al_riel_mv": margen, "sin_saturar": sin_saturar,
            "cumple": cumple, "t_s": time.time() - t0,
            "intentos": [{"cual": c, "dac": d, "taps": t} for c, d, t in intentos],
            "log": lineas}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=None)
    ap.add_argument("--pga", type=int, default=8)
    ap.add_argument("--outs", default="0,1,2,3")
    ap.add_argument("--tau-busqueda", type=float, default=1.0)
    ap.add_argument("--tau-medicion", type=float, default=1.5)
    a = ap.parse_args()

    eb, em = a.tau_busqueda * TAU_S, a.tau_medicion * TAU_S
    c = con.Console(a.port) if a.port else con.Console()
    print(f"Abriendo {c.port}...", flush=True)
    c.open(); time.sleep(1.0)
    lab = Lab(Session(c))
    filas = []
    SALIDA.mkdir(parents=True, exist_ok=True)
    ruta = SALIDA / f"permisivo_pga{a.pga}_{datetime.now():%Y%m%d_%H%M%S}.json"
    try:
        for oc in [int(x) for x in a.outs.split(",")]:
            print(f"\n=== PGA x{GAIN_CODES[a.pga]}  PGAout x{GAIN_CODES[oc]} "
                  f"(total x{GAIN_CODES[a.pga] * GAIN_CODES[oc]}) ===", flush=True)
            filas.append(una(lab, a.pga, oc, eb, em))
            ruta.write_text(json.dumps(filas, indent=1), encoding="utf-8")
    finally:
        c.close()

    print("\n" + "=" * 68)
    print(f"{'PGA':>6} {'PGAout':>7} {'total':>7} {'LP mV':>9} "
          f"{'intermedias':>12} {'margen':>8} {'t s':>6}  veredicto")
    maximo = None
    for f in filas:
        lp, de = f.get("error_lp_mv"), f.get("desvio_intermedias_mv")
        print(f"{'x' + str(f['pga_x']):>6} {'x' + str(f['pgaout_x']):>7} "
              f"{'x' + str(f['pga_x'] * f['pgaout_x']):>7} "
              f"{(f'{lp:9.2f}' if lp is not None else '        -')} "
              f"{(f'{de:12.1f}' if de is not None else '           -')} "
              f"{(f'{mg:8.1f}' if (mg := f.get('peor_margen_al_riel_mv')) is not None else '       -')} "
              f"{f['t_s']:6.0f}  {'CUMPLE' if f['cumple'] else 'no cumple'}")
        if f["cumple"]:
            maximo = f["pgaout_x"]
    print(f"\nMAXIMO PGAout con el metodo permisivo: "
          f"{('x' + str(maximo)) if maximo else 'ninguno'}")
    print(f"crudo -> {ruta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
