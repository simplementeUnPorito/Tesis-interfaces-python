"""Valida el estimador de tau de DOS PUNTOS contra el ajuste exponencial completo.

POR QUE HACE FALTA UN ESTIMADOR BARATO
Elias decidió que el nodo NO corrija por temperatura sino que MIDA su propio tau
y dimensione la espera de planta con lo medido. Eso significa que el estimador
tiene que correr adentro del PSoC, en enteros, sin scipy y sin guardar cientos de
muestras.

EL ESTIMADOR
Si la respuesta es y(t) = y_inf + A·e^(−t/τ), alcanzan TRES muestras: en t=0, en
t₁ y en 2·t₁. Con

    r = ( y(2t₁) − y(0) ) / ( y(t₁) − y(0) )

sale, haciendo la cuenta,  r = 1 + e^(−t₁/τ),  y por lo tanto

    τ = t₁ / ( − ln(r − 1) )

Lo que lo hace atractivo es que **no hace falta conocer y_inf**: el valor final se
cancela. O sea que no hay que esperar a que la planta termine de asentarse para
estimar cuánto tarda en asentarse, que es justo el problema del huevo y la
gallina. Y en el PSoC el logaritmo sale de una tabla de 16 entradas.

QUE VALIDA ESTE PROGRAMA
Toma las series del escalón ya medido —que tienen ~500 puntos cada una y un
ajuste exponencial completo por mínimos cuadrados— y les aplica el estimador de
tres muestras. Si los dos coinciden, el estimador barato sirve; y de paso dice
qué t₁ conviene elegir, que es el único parámetro libre.

    python validar_estimador_tau.py [--crudo lab/planta/escalon_*.json]
"""
from __future__ import annotations

import argparse
import glob
import json
import math
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def interpolar(puntos: list[tuple[float, float]], t: float) -> float | None:
    """Valor de la serie en t, interpolando linealmente entre muestras."""
    if not puntos or t < puntos[0][0] or t > puntos[-1][0]:
        return None
    for i in range(1, len(puntos)):
        t0, y0 = puntos[i - 1]
        t1, y1 = puntos[i]
        if t <= t1:
            if t1 == t0:
                return y1
            return y0 + (y1 - y0) * (t - t0) / (t1 - t0)
    return puntos[-1][1]


def tau_tres_muestras(puntos: list[tuple[float, float]], t1: float) -> float | None:
    """El estimador, tal como lo va a hacer el firmware."""
    y0 = interpolar(puntos, puntos[0][0])
    ya = interpolar(puntos, puntos[0][0] + t1)
    yb = interpolar(puntos, puntos[0][0] + 2.0 * t1)
    if y0 is None or ya is None or yb is None:
        return None
    da = ya - y0
    db = yb - y0
    if abs(da) < 1e-9:
        return None
    r = db / da
    x = r - 1.0
    # x = e^(−t1/τ) tiene que estar en (0,1). Fuera de ahí la serie no es una
    # exponencial simple, o el ruido se comió la diferencia: hay que rechazar en
    # vez de devolver un número inventado.
    if not (0.02 < x < 0.98):
        return None
    return t1 / (-math.log(x))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--crudo", default=None)
    a = ap.parse_args()

    if a.crudo:
        rutas = [a.crudo]
    else:
        rutas = sorted(glob.glob(str(REPO / "lab" / "planta" / "escalon_*.json")))
    if not rutas:
        print("no encuentro ningun escalon_*.json")
        return 1

    print(f"crudo: {rutas[-1]}")
    d = json.loads(Path(rutas[-1]).read_text(encoding="utf-8"))

    # t1 candidatos, como fraccion del tau que ya conocemos de banco (29,5 s).
    T1_CANDIDATOS = (7.5, 15.0, 20.0, 30.0, 45.0)

    print()
    print("tau por ajuste completo (~500 puntos) contra el estimador de 3 muestras")
    print()
    encabezado = f"{'etapa':>6} {'tap':>4} {'ajuste':>9} " + " ".join(
        f"{'t1=' + str(t) + 's':>10}" for t in T1_CANDIDATOS)
    print(encabezado)
    print("-" * len(encabezado))

    errores = {t: [] for t in T1_CANDIDATOS}
    for ens in d.get("ensayos", []):
        etapa = ens.get("etapa")
        for tap_key, aj in (ens.get("ajustes") or {}).items():
            if aj is None or aj.get("plano"):
                continue
            tau_ref = aj.get("tau_s")
            if not tau_ref or not (5.0 < tau_ref < 200.0):
                continue
            pts = [(p["t"], p["uv"]) for p in ens.get("puntos", [])
                   if p.get("ch") == int(tap_key) and p.get("uv") is not None]
            pts.sort()
            if len(pts) < 20:
                continue
            celdas = []
            for t1 in T1_CANDIDATOS:
                est = tau_tres_muestras(pts, t1)
                if est is None:
                    celdas.append(f"{'-':>10}")
                else:
                    celdas.append(f"{est:10.1f}")
                    errores[t1].append(abs(est - tau_ref) / tau_ref)
            print(f"{etapa:>6} {tap_key:>4} {tau_ref:9.1f} " + " ".join(celdas))

    print()
    print("error relativo del estimador contra el ajuste completo")
    print(f"{'t1':>8} {'n':>4} {'medio':>8} {'peor':>8}")
    for t1 in T1_CANDIDATOS:
        e = errores[t1]
        if not e:
            print(f"{t1:8.1f} {0:4d} {'-':>8} {'-':>8}")
            continue
        print(f"{t1:8.1f} {len(e):4d} {100 * sum(e) / len(e):7.1f}% "
              f"{100 * max(e):7.1f}%")
    print()
    print("El t1 con menor error peor-caso es el que conviene poner en el firmware.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
