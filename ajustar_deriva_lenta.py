"""Ajusta la deriva lenta de cada tap y dice si es un polo o si no cierra.

Entrada: el CSV que deja `vigilar_punto.py`, que registra los cinco taps cada
pocos segundos con los cuatro actuadores congelados. Con todo quieto, cualquier
cosa que se mueva es de la planta.

Para cada canal ajusta v(t) = A + B*exp(-t/T) barriendo T y resolviendo A y B por
minimos cuadrados, que con T fijo es lineal. Informa el T que mejor cierra, el
residuo, la excursion total y **cuanto le falta todavia**: si al final del
registro el tap sigue lejos de su asintota, el registro es corto y el T que sale
no se puede creer.

Sin numpy: listas y aritmetica a mano.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

TAPS = {0: "PGAgain", 1: "BPo", 2: "OPA_SUMo", 3: "SUMo", 4: "LPo"}


def ajustar_con_t(ts: list[float], vs: list[float],
                  tau: float) -> tuple[float, float, float]:
    """Con T fijo el modelo es lineal en A y B. Devuelve A, B y el residuo RMS."""
    xs = [math.exp(-t / tau) for t in ts]
    n = len(ts)
    mx = sum(xs) / n
    mv = sum(vs) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0.0:
        return mv, 0.0, math.sqrt(sum((v - mv) ** 2 for v in vs) / n)
    sxy = sum((x - mx) * (v - mv) for x, v in zip(xs, vs))
    b = sxy / sxx
    a = mv - b * mx
    res = math.sqrt(sum((v - (a + b * x)) ** 2 for x, v in zip(xs, vs)) / n)
    return a, b, res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?",
                    help="CSV de vigilar_punto.py; por defecto el mas reciente")
    ap.add_argument("--bloque", default="vigilancia",
                    help="solo se ajusta este bloque del registro")
    args = ap.parse_args()

    if args.csv:
        destino = Path(args.csv)
    else:
        base = Path(__file__).resolve().parents[3] / "lab" / "calibracion_nueva"
        candidatos = sorted(base.glob("vigilar_punto_*.csv"),
                            key=lambda p: p.stat().st_mtime)
        if not candidatos:
            print("No hay ningun vigilar_punto_*.csv")
            return 1
        destino = candidatos[-1]
    print(f"Archivo: {destino.name}\n")

    filas = [r for r in csv.DictReader(destino.open(encoding="utf-8"))
             if r.get("bloque") == args.bloque and r.get("real_mv")]
    if not filas:
        print(f"El bloque '{args.bloque}' no tiene filas con lectura.")
        return 1

    t0 = float(filas[0]["t_s"])
    duracion = float(filas[-1]["t_s"]) - t0
    print(f"Bloque '{args.bloque}': {len(filas)} lecturas en "
          f"{duracion/60.0:.1f} minutos\n")

    print(f"{'canal':<12} {'excursion':>10} {'T ajustado':>11} "
          f"{'residuo':>9} {'falta':>10}  veredicto")
    print("-" * 74)
    for ch in TAPS:
        pts = [(float(r["t_s"]) - t0, float(r["real_mv"]))
               for r in filas if int(r["ch"]) == ch]
        if len(pts) < 10:
            print(f"ch{ch} {TAPS[ch]:<9} sin datos suficientes")
            continue
        ts = [p[0] for p in pts]
        vs = [p[1] for p in pts]
        excursion = max(vs) - min(vs)

        mejor = None
        # De diez segundos a dos horas. Mas alla de eso el registro no alcanza
        # para distinguir un polo de una recta.
        tau = 10.0
        while tau <= 7200.0:
            a, b, res = ajustar_con_t(ts, vs, tau)
            if mejor is None or res < mejor[3]:
                mejor = (tau, a, b, res)
            tau *= 1.15
        tau, a, b, res = mejor

        # Cuanto le falta para llegar: la distancia entre la ultima lectura y la
        # asintota, en unidades de la excursion ya recorrida.
        falta = abs(a - vs[-1])
        ruido = res
        if excursion < 5.0 * max(ruido, 0.5):
            veredicto = "quieto"
        elif duracion < 2.0 * tau:
            veredicto = f"registro CORTO (dura {duracion/tau:.1f} T)"
        elif falta > 0.2 * excursion:
            veredicto = "sigue viajando"
        else:
            veredicto = "polo identificado"
        print(f"ch{ch} {TAPS[ch]:<9} {excursion:9.1f}  {tau:10.0f} s "
              f"{res:8.1f} {falta:9.1f}  {veredicto}")

    print("\nLectura: un 'registro CORTO' significa que el T que sale es una")
    print("extrapolacion y no se puede usar para dimensionar esperas. En ese")
    print("caso hay que volver a medir por mas tiempo, no creerle al numero.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
