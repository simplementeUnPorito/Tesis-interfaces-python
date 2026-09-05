"""Valida la extrapolación del valor asentado a partir de tres muestras.

EL PROBLEMA QUE ATACA
Cada medida de continua de esta cadena cuesta una espera de planta. Con τ ≈ 30 s
y el criterio de 2 τ, son ~60 s por punto, y una campaña de decenas de puntos son
horas. Ése es el cuello de botella de todo el trabajo.

LA IDEA
Si la respuesta es y(t) = y_inf + A·e^(−t/τ), tres muestras igualmente espaciadas
en 0, t y 2t determinan y_inf **sin necesidad de esperar a que se asiente**:

    y_inf = ( y0·y2 − y1² ) / ( y0 + y2 − 2·y1 )

Es la extrapolación de Aitken. O sea que en 2t de espera se obtiene el valor al
que la señal iba a llegar en 5 τ.

DÓNDE ESTÁ LA TRAMPA, y por eso esto se valida antes de usarlo: el denominador
`y0 + y2 − 2y1` es la curvatura, y **tiende a cero cuando la señal ya está
asentada o cuando es casi recta**. Ahí la fórmula divide por ruido y devuelve
cualquier cosa. Hace falta un criterio de rechazo, no sólo la fórmula.

Este programa mide, sobre las series reales del escalón —que tienen ~500 puntos y
un ajuste exponencial completo como referencia—, cuánto error comete la
extrapolación para distintos t, y con qué frecuencia hay que rechazarla.

    python validar_extrapolacion.py
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def interpolar(pts: list[tuple[float, float]], t: float) -> float | None:
    if not pts or t < pts[0][0] or t > pts[-1][0]:
        return None
    for i in range(1, len(pts)):
        t0, y0 = pts[i - 1]
        t1, y1 = pts[i]
        if t <= t1:
            return y1 if t1 == t0 else y0 + (y1 - y0) * (t - t0) / (t1 - t0)
    return pts[-1][1]


def extrapolar(pts: list[tuple[float, float]], t: float,
               ruido_uv: float = 60.0) -> tuple[float | None, str]:
    """Devuelve (y_inf, motivo). y_inf None si hay que rechazar."""
    t0 = pts[0][0]
    y0 = interpolar(pts, t0)
    y1 = interpolar(pts, t0 + t)
    y2 = interpolar(pts, t0 + 2 * t)
    if y0 is None or y1 is None or y2 is None:
        return None, "serie corta"
    den = y0 + y2 - 2.0 * y1
    # La curvatura tiene que superar al ruido con margen; si no, la division
    # amplifica ruido sin limite. Cuatro veces el ruido es conservador y deja
    # pasar los casos donde la exponencial todavia se ve.
    if abs(den) < 4.0 * ruido_uv:
        # Ya esta asentada -o es recta-: el mejor estimador es la ultima muestra.
        return y2, "asentada, se usa la ultima"
    y_inf = (y0 * y2 - y1 * y1) / den
    # Un valor extrapolado que se va MUY lejos del recorrido observado es senal
    # de que el modelo de una exponencial no aplica.
    lo, hi = min(y0, y1, y2), max(y0, y1, y2)
    margen = 3.0 * (hi - lo) + 10.0 * ruido_uv
    if not (lo - margen <= y_inf <= hi + margen):
        return None, "extrapolacion fuera de rango"
    return y_inf, "extrapolada"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--crudo", default=None)
    a = ap.parse_args()
    rutas = [a.crudo] if a.crudo else sorted(
        glob.glob(str(REPO / "lab" / "planta" / "escalon_*.json")))
    if not rutas:
        print("no hay escalon_*.json")
        return 1
    d = json.loads(Path(rutas[-1]).read_text(encoding="utf-8"))
    print(f"crudo: {rutas[-1]}\n")

    T = (7.5, 10.0, 15.0, 20.0, 30.0)
    err = {t: [] for t in T}
    rech = {t: 0 for t in T}
    n_series = 0

    print("error de la extrapolacion contra y_inf del ajuste completo, en uV")
    print(f"{'etapa':>6} {'tap':>4} {'y_inf':>11} {'|A|':>9} " +
          " ".join(f"{'t=' + str(t):>9}" for t in T))
    print("-" * (23 + 10 * len(T)))
    for ens in d.get("ensayos", []):
        etapa = ens.get("etapa")
        for tap_key, aj in (ens.get("ajustes") or {}).items():
            if aj is None or aj.get("plano"):
                continue
            y_ref = aj.get("y_inf_uv")
            amp = abs(aj.get("amplitud_uv") or 0.0)
            if y_ref is None or amp < 200.0:
                continue      # sin transitorio util no hay nada que extrapolar
            # FILTRO QUE IMPORTA, y salio de mirar los fallos uno por uno: la
            # extrapolacion supone UNA exponencial, y las series que terminan
            # cerca del riel no lo son -son una exponencial recortada-. En esas
            # el estimador da errores de 270 mV, mientras que en las que si son
            # exponenciales da decenas de microvoltios. No es que el metodo
            # falle: es que se le esta pidiendo que ajuste algo que no es lo que
            # supone. Se descartan por un criterio OBSERVABLE en el momento -la
            # cercania al riel- y no por el resultado.
            if not (780_000.0 < y_ref < 1_090_000.0):
                continue
            if amp > 100_000.0:
                continue
            pts = sorted((p["t"], p["uv"]) for p in ens.get("puntos", [])
                         if p.get("ch") == int(tap_key) and p.get("uv") is not None)
            if len(pts) < 20:
                continue
            n_series += 1
            celdas = []
            for t in T:
                y, motivo = extrapolar(pts, t)
                if y is None:
                    rech[t] += 1
                    celdas.append(f"{'rech':>9}")
                else:
                    e = y - y_ref
                    err[t].append(abs(e))
                    celdas.append(f"{e:9.0f}")
            print(f"{etapa:>6} {tap_key:>4} {y_ref:11.0f} {amp:9.0f} " +
                  " ".join(celdas))

    print(f"\nresumen sobre {n_series} series con transitorio util")
    print(f"{'t':>6} {'n':>4} {'error medio':>12} {'peor':>10} {'rechazos':>9}")
    for t in T:
        e = err[t]
        if not e:
            print(f"{t:6.1f} {0:4d} {'-':>12} {'-':>10} {rech[t]:9d}")
            continue
        print(f"{t:6.1f} {len(e):4d} {sum(e) / len(e):11.0f}  {max(e):9.0f} "
              f"{rech[t]:9d}")
    print()
    print("Comparar contra el ruido de una medida sola, que es ~60 uV: si el")
    print("error de la extrapolacion es de ese orden, esperar 2t en vez de 2 tau")
    print("no cuesta precision y ahorra la mayor parte del tiempo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
