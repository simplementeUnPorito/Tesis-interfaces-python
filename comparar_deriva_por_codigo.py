"""Mide si la deriva lenta de la cadena depende del codigo de IDAC2.

Cada bloque fija un codigo, congela todo lo demas y mira varios minutos. Entre
bloques se espera para no arrastrar la cola del anterior, y conviene repetir los
codigos para ver si el efecto es reversible.

REGLA DURA: solo entran al ajuste las lecturas VALIDAS. Un tap fuera de rango
devuelve un numero comprimido contra el borde y una pendiente sacada de ahi no
mide nada. El 2026-09-12 una corrida entera "demostro" que la deriva dependia
del signo del codigo, con treinta lecturas por bloque, y las treinta estaban
fuera de rango.

De ahi sale como elegir los codigos: tienen que dejar ch2 dentro de rango en
TODOS los bloques. Ojo que eso limita lo que se puede preguntar. Comparar +210
contra -210 en el mismo punto de trabajo es IMPOSIBLE, porque ese recorrido
abarca 5,2 V y la ventana de medicion son 4,8: no existe un punto donde los dos
signos queden adentro.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from escala_banco import BANCO_VREF_MV, FACTOR, lectura_valida
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

SALIDA = Path(__file__).resolve().parents[3] / "lab" / "calibracion_nueva"
TAPS = {0: "PGAgain", 1: "BPo", 2: "OPA_SUMo", 3: "SUMo", 4: "LPo"}


def real_mv(banco_uv: float) -> float:
    return (banco_uv / 1000.0 - BANCO_VREF_MV) * FACTOR


def leer(lab: Lab, ch: int):
    medias, picos = [], []
    for _ in range(3):
        p = lab.measure_dc(ch, settle_sel=2)
        if p is None or not p.ok:
            continue
        medias.append(p.mean_uv)
        picos.append(p.pp_uv)
    if not medias:
        return None, None
    return statistics.median(medias), statistics.median(picos)


def pendiente(pts: list[tuple[float, float]]) -> float | None:
    """mV por minuto por minimos cuadrados. None si no hay con que.

    Devuelve None y no cero: cero significa "medi y esta quieto", y confundirlo
    con "no pude medir" es como se cuelan las conclusiones falsas.
    """
    n = len(pts)
    if n < 3:
        return None
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    if sxx == 0.0:
        return 0.0
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    return (sxy / sxx) * 60.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=50)
    ap.add_argument("--pgaout", type=int, default=4)
    ap.add_argument("--codigos", default="0,-247,0,-247",
                    help="bloques de IDAC2; se repite para ver reversibilidad")
    ap.add_argument("--minutos", type=float, default=10.0,
                    help="duracion de cada bloque")
    ap.add_argument("--periodo", type=float, default=10.0)
    args = ap.parse_args()

    codigos = [int(c) for c in args.codigos.split(",")]
    SALIDA.mkdir(parents=True, exist_ok=True)
    destino = SALIDA / ("deriva_por_codigo_"
                        + time.strftime("%Y%m%d_%H%M%S") + ".csv")

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    salida = destino.open("w", newline="", encoding="utf-8")
    escritor = csv.writer(salida)
    escritor.writerow(["t_s", "bloque", "idac2", "ch", "tap",
                       "media_banco_uv", "pp_banco_uv", "real_mv", "valido"])
    t0 = time.time()
    resumen: list[tuple[int, int, dict]] = []

    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))
        for etapa in range(4):
            lab.set_idac(etapa, 0)
        print("Asentando 180 s con todo en Vref...", flush=True)
        time.sleep(180.0)

        for bloque, codigo in enumerate(codigos):
            lab.set_idac(2, codigo)
            print(f"\nBloque {bloque}: IDAC2={codigo:+d}, "
                  f"{args.minutos:.0f} min", flush=True)
            # Se descartan los primeros noventa segundos: ahi manda la cola
            # rapida del pasabanda y lo que se busca es lo lento.
            time.sleep(90.0)
            series: dict[int, list[tuple[float, float]]] = {}
            invalidas: dict[int, int] = {}
            fin = time.time() + args.minutos * 60.0
            while time.time() < fin:
                for ch in TAPS:
                    media, pico = leer(lab, ch)
                    t = time.time() - t0
                    if media is None:
                        escritor.writerow([f"{t:.1f}", bloque, codigo, ch,
                                           TAPS[ch], "", "", "", ""])
                        continue
                    mv = real_mv(media)
                    escritor.writerow([f"{t:.1f}", bloque, codigo, ch, TAPS[ch],
                                       f"{media:.0f}", f"{pico:.0f}",
                                       f"{mv:.1f}",
                                       lectura_valida(media / 1000.0)])
                    # SOLO entran las lecturas VALIDAS. Un tap fuera de rango
                    # devuelve un numero comprimido contra el borde, y una
                    # pendiente sacada de ahi no mide nada. El 2026-09-12 una
                    # corrida entera concluyo que la deriva dependia del signo
                    # del codigo, con treinta lecturas por bloque, y las treinta
                    # estaban fuera de rango.
                    if lectura_valida(media / 1000.0):
                        series.setdefault(ch, []).append((t, mv))
                    else:
                        invalidas[ch] = invalidas.get(ch, 0) + 1
                salida.flush()
                time.sleep(args.periodo)
            pendientes = {ch: pendiente(series.get(ch, [])) for ch in TAPS}
            resumen.append((bloque, codigo, pendientes))
            for ch in TAPS:
                p = pendientes[ch]
                fuera = invalidas.get(ch, 0)
                if p is None:
                    print(f"    ch{ch} {TAPS[ch]:<9} SIN DATO VALIDO "
                          f"({fuera} lecturas fuera de rango)", flush=True)
                else:
                    extra = (f"   ({fuera} descartadas por fuera de rango)"
                             if fuera else "")
                    print(f"    ch{ch} {TAPS[ch]:<9} "
                          f"{p:+8.2f} mV/min{extra}", flush=True)

        print("\n=== VEREDICTO ===", flush=True)
        print(f"{'bloque':<8} {'IDAC2':>7} " +
              " ".join(f"{TAPS[c][:8]:>9}" for c in TAPS))
        for bloque, codigo, p in resumen:
            print(f"{bloque:<8} {codigo:>7} " +
                  " ".join("      s/d" if p.get(c) is None
                           else f"{p[c]:9.2f}" for c in TAPS))

        # Se compara el codigo de MENOR modulo contra el de MAYOR, no contra
        # cero: los bloques utiles son los que dejan ch2 dentro de rango, y con
        # IDAC2 en cero ch2 esta saturado y no se puede medir nada.
        modulos = sorted({abs(c) for _, c, _ in resumen})
        bajo, alto = modulos[0], modulos[-1]
        en_cero = [abs(p[2]) for _, c, p in resumen
                   if abs(c) == bajo and p.get(2) is not None]
        en_tope = [abs(p[2]) for _, c, p in resumen
                   if abs(c) == alto and p.get(2) is not None]
        if not en_cero or not en_tope:
            print("\nNO SE PUEDE COMPARAR: falta ch2 valido en alguno de los "
                  "dos extremos. Hay que elegir codigos que dejen ch2 dentro "
                  "de rango en TODOS los bloques, o el resultado no significa "
                  "nada.")
        if en_cero and en_tope and bajo != alto:
            print(f"\nComparando |IDAC2|={bajo} contra |IDAC2|={alto}")
            m0 = statistics.mean(en_cero)
            m1 = statistics.mean(en_tope)
            print(f"Deriva de OPA_SUMo con |IDAC2|={bajo}: {m0:.2f} mV/min")
            print(f"Deriva de OPA_SUMo con |IDAC2|={alto}: {m1:.2f} mV/min")
            if m1 > 3.0 * max(m0, 0.05):
                print("\nDEPENDE DEL CODIGO. El remedio practico es no "
                      "trabajar ahi, y eso alcanza para el algoritmo.")
                print("El MECANISMO no lo dice esta medicion. Un salto abrupto "
                      "entre dos codigos vecinos no es autocalentamiento, que "
                      "seria gradual y proporcional al cuadrado de la "
                      "corriente. Para saberlo hay que mirar el nodo del IDAC "
                      "con osciloscopio. No inventar la causa.")
            else:
                print("\nNO depende del codigo. Lo que se ve viene de otro "
                      "lado y hay que buscarlo en otra parte.")
        print(f"\nArchivo: {destino}", flush=True)
        return 0
    finally:
        try:
            lab.set_gain("pgaout", 0)
            for etapa in range(4):
                lab.set_idac(etapa, 0)
        except Exception:
            pass
        salida.close()
        console.close()


if __name__ == "__main__":
    raise SystemExit(main())
