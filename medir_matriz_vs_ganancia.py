"""Mide la matriz de acople COMO FUNCIÓN de las dos ganancias.

POR QUÉ HACE FALTA, Y POR QUÉ ES EL ERROR DE MÉTODO QUE MÁS CARO SALIÓ
La matriz del 2026-09-04 se midió entera con **PGA ×1 y PGAout ×1**, y después se
usó para razonar sobre las 81 combinaciones. Eso no vale: el 2026-09-05 la
calibración conjunta corrida contra la placa saltó de un riel al otro porque un
cambio de códigos movió ch2 **371 mV cuando la matriz predecía 59**, un factor
6,3. La matriz no es una constante del circuito, es una constante **del punto de
operación**.

Este programa la mide en varios puntos de operación, con dos cuidados que las
mediciones anteriores no tuvieron:

* **Escalones CHICOS.** La medición del 04 usó +120 códigos, y con la pendiente
  real eso mete al LP contra el riel: la exponencial se ajustó sobre una
  respuesta recortada y el acople ADDER→LP salió subestimado 1,8×. Acá se usan
  ±8 códigos, que es lo más grande que se queda dentro de la zona lineal en el
  peor caso conocido.
* **Se arranca desde un punto DENTRO DE RANGO, no desde códigos cero.** Con
  PGAout ≥ ×2 la cadena arranca contra el riel, y contra el riel la pendiente es
  cero: medir ahí da un acople falso de cero. Antes de cada medición se busca un
  punto de operación válido.

    python medir_matriz_vs_ganancia.py --port COM8
    python medir_matriz_vs_ganancia.py --port COM8 --combos 0:0,3:0,0:3,3:3
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

from testbench.core import console as con             # noqa: E402
from testbench.core.lab import GAIN_CODES, Lab        # noqa: E402
from testbench.core.session import Session            # noqa: E402

SALIDA = REPO / "lab" / "planta"
CANALES = (0, 1, 2, 3)
SETTLE_DC = 3

#: Rieles medidos el 2026-09-05, en mV. Ver docs/MEDICIONES_2026-09-05.md §4.
RIEL_BAJO = {0: 759.4, 1: 750.0, 2: 750.8, 3: 746.4}
RIEL_ALTO = {0: 1114.4, 1: 1122.0, 2: 1121.6, 3: 1122.3}
MARGEN = 15.0

#: τ de banco. La espera de planta se expresa como múltiplo.
TAU_S = 29.5
#: Escalón de medición. Chico a propósito: ver el encabezado.
DELTA = 8


def en_riel(ch: int, mv: float) -> bool:
    return mv <= RIEL_BAJO[ch] + MARGEN or mv >= RIEL_ALTO[ch] - MARGEN


def leer(lab: Lab, chs=CANALES) -> dict[int, float | None]:
    out = {}
    for ch in chs:
        p = lab.measure_dc(ch, SETTLE_DC)
        out[ch] = (p.mean_uv / 1000.0) if (p and p.ok) else None
    return out


def buscar_punto_valido(lab: Lab, espera_s: float, verbose=True) -> dict[int, int] | None:
    """Deja la cadena con los cuatro taps dentro de rango, si se puede.

    ESTRATEGIA. Se busca de aguas arriba hacia aguas abajo, porque la cadena es
    triangular: lo que se arregla arriba no lo rompe lo de abajo, pero al revés
    sí. Para cada etapa se hace una **búsqueda binaria sobre su propio código**
    mirando su propio tap.

    POR QUÉ BINARIA Y NO CALCULADA: justamente porque la matriz que permitiría
    calcularlo es lo que este programa está tratando de medir. La búsqueda no
    necesita saber la ganancia, sólo su signo, y ni siquiera: prueba los dos
    extremos y se queda con el que acerca.
    """
    dac = {k: 0 for k in CANALES}
    for k in CANALES:
        lab.set_idac(k, 0)
    time.sleep(espera_s)

    for etapa in CANALES:
        tap = etapa
        v = leer(lab, (tap,))[tap]
        if v is None:
            return None
        if not en_riel(tap, v):
            continue    # esta etapa ya está bien
        objetivo = (RIEL_BAJO[tap] + RIEL_ALTO[tap]) / 2.0
        lo, hi = -255, 255
        # Primero hay que saber de qué lado empuja esta etapa: se prueban los dos
        # extremos y se ve cuál acerca al centro.
        lab.set_idac(etapa, lo); time.sleep(espera_s)
        v_lo = leer(lab, (tap,))[tap]
        lab.set_idac(etapa, hi); time.sleep(espera_s)
        v_hi = leer(lab, (tap,))[tap]
        if v_lo is None or v_hi is None:
            return None
        if verbose:
            print(f"      etapa {etapa}: extremos dan {v_lo:.1f} y {v_hi:.1f} mV")
        if en_riel(tap, v_lo) and en_riel(tap, v_hi):
            if verbose:
                print(f"      etapa {etapa}: NINGUN codigo saca al tap {tap} del riel")
            return None
        # Binaria: se conserva el intervalo cuyos extremos rodean al objetivo.
        creciente = v_hi > v_lo
        for _ in range(6):
            mid = (lo + hi) // 2
            lab.set_idac(etapa, mid); time.sleep(espera_s)
            v = leer(lab, (tap,))[tap]
            if v is None:
                return None
            if abs(v - objetivo) < 20.0:
                break
            if (v < objetivo) == creciente:
                lo = mid
            else:
                hi = mid
        dac[etapa] = mid
        if verbose:
            print(f"      etapa {etapa}: codigo {mid} deja el tap {tap} en {v:.1f} mV")

    time.sleep(espera_s)
    v = leer(lab)
    if any(x is None or en_riel(ch, x) for ch, x in v.items()):
        if verbose:
            print(f"      no se logro sacar todo del riel: {v}")
        return None
    return dac


def columna(lab: Lab, etapa: int, base: dict[int, int], espera_s: float) -> dict:
    """Un escalón de ±DELTA en `etapa`, midiendo los cuatro taps.

    Se hace simétrico (−Δ y +Δ) y no desde el punto base, porque así la pendiente
    sale centrada en el punto de operación y se cancela cualquier deriva lineal
    que haya ocurrido entre las dos medidas.
    """
    res = {}
    for signo in (-1, +1):
        code = max(-255, min(255, base[etapa] + signo * DELTA))
        lab.set_idac(etapa, code)
        time.sleep(espera_s)
        res[signo] = (code, leer(lab))
    lab.set_idac(etapa, base[etapa])

    (c_lo, v_lo), (c_hi, v_hi) = res[-1], res[+1]
    pend = {}
    for ch in CANALES:
        if v_lo[ch] is None or v_hi[ch] is None or c_hi == c_lo:
            pend[ch] = None
        elif en_riel(ch, v_lo[ch]) or en_riel(ch, v_hi[ch]):
            pend[ch] = None       # contra el riel la pendiente medida es falsa
        else:
            pend[ch] = 1000.0 * (v_hi[ch] - v_lo[ch]) / (c_hi - c_lo)   # uV/codigo
    return {"etapa": etapa, "base": base[etapa],
            "codigos": [c_lo, c_hi],
            "taps_lo_mv": v_lo, "taps_hi_mv": v_hi,
            "pendiente_uv_por_codigo": pend}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=None)
    ap.add_argument("--combos", default="0:0,3:0,0:1,0:3,3:3",
                    help="lista pga:pgaout en CODIGOS (0-8), separada por comas")
    ap.add_argument("--tau", type=float, default=2.0,
                    help="multiplos de tau para la MEDICION de las pendientes")
    ap.add_argument("--tau-busqueda", type=float, default=1.0,
                    help="multiplos de tau durante la busqueda del punto valido. "
                         "Puede ser menor que el de medicion: para decidir si un "
                         "tap salio del riel no hace falta el valor asentado, "
                         "alcanza con que ya no este pegado")
    a = ap.parse_args()

    espera = a.tau * TAU_S
    espera_busq = a.tau_busqueda * TAU_S
    combos = []
    for par in a.combos.split(","):
        p, o = par.split(":")
        combos.append((int(p), int(o)))

    c = con.Console(a.port) if a.port else con.Console()
    print(f"Abriendo {c.port}...")
    c.open(); time.sleep(1.0)
    lab = Lab(Session(c))
    salida = {"experimento": "matriz_vs_ganancia", "tau_s": TAU_S,
              "espera_s": espera, "delta_codigos": DELTA, "combos": []}
    try:
        for pc, oc in combos:
            print(f"\n=== PGA x{GAIN_CODES[pc]}  PGAout x{GAIN_CODES[oc]} "
                  f"(espera {espera:.0f} s por punto) ===")
            lab.set_gain("pga", pc)
            lab.set_gain("pgaout", oc)
            print("    buscando un punto de operacion dentro de rango...")
            base = buscar_punto_valido(lab, espera_busq)
            if base is None:
                print("    -> NO HAY punto valido: esta combinacion no se puede "
                      "medir, y probablemente tampoco usar")
                salida["combos"].append({"pga_code": pc, "pgaout_code": oc,
                                         "pga_x": GAIN_CODES[pc],
                                         "pgaout_x": GAIN_CODES[oc],
                                         "punto_valido": None})
                continue
            print(f"    punto de operacion: {[base[k] for k in CANALES]}")
            cols = []
            for etapa in CANALES:
                col = columna(lab, etapa, base, espera)
                cols.append(col)
                p = col["pendiente_uv_por_codigo"]
                txt = "  ".join(f"ch{ch}={p[ch]:8.1f}" if p[ch] is not None
                                else f"ch{ch}={'riel':>8}" for ch in CANALES)
                print(f"      etapa {etapa}: {txt}")
            salida["combos"].append({"pga_code": pc, "pgaout_code": oc,
                                     "pga_x": GAIN_CODES[pc],
                                     "pgaout_x": GAIN_CODES[oc],
                                     "punto_valido": base, "columnas": cols})
    finally:
        c.close()

    SALIDA.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta = SALIDA / f"matriz_vs_ganancia_{stamp}.json"
    ruta.write_text(json.dumps(salida, indent=1), encoding="utf-8")
    print(f"\ncrudo -> {ruta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
