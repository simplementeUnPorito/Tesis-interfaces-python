"""Cuanto mueve IDAC2 al sumador DE VERDAD, una vez que la cadena se asento.

Todo el calibrador usa la ganancia INSTANTANEA de IDAC2 sobre ch2: se escribe un
codigo, se espera un segundo y medio y se mide. Pero el pasabanda tiene treinta
y cuatro segundos de cola, asi que ese numero es el arranque de un transitorio,
no el efecto final.

La sospecha, medida el 2026-09-11: el lazo dejo ch2 en -220 mV y, tras esperar,
ch2 habia vuelto a +1.884, casi exactamente de donde habia salido. Si eso se
confirma, la ganancia de regimen es mucho menor que la instantanea y ningun lazo
que use la instantanea puede sostener un punto.

Este script mide las dos sobre los mismos codigos: lee enseguida y vuelve a leer
despues de cinco tau. La diferencia entre las dos pendientes es la respuesta.
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
CANAL_SUM = 2
PP_CLAVADO_UV = 300.0


def real_mv(banco_uv: float) -> float:
    return (banco_uv / 1000.0 - BANCO_VREF_MV) * FACTOR


def leer(lab: Lab, ch: int, repeticiones: int = 3):
    medias, picos = [], []
    for _ in range(repeticiones):
        p = lab.measure_dc(ch, settle_sel=2)
        if p is None or not p.ok:
            continue
        medias.append(p.mean_uv)
        picos.append(p.pp_uv)
    if not medias:
        return None, None
    return statistics.median(medias), statistics.median(picos)


def ajuste_lineal(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Minimos cuadrados a mano: pendiente y ordenada."""
    n = len(xs)
    if n < 2:
        return 0.0, 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0.0:
        return 0.0, my
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    pendiente = sxy / sxx
    return pendiente, my - pendiente * mx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=50)
    ap.add_argument("--pgaout", type=int, default=1)
    ap.add_argument("--tau", type=float, default=34.0)
    ap.add_argument("--codigos", default="-120,-60,0,60,120",
                    help="codigos de IDAC2 a visitar")
    args = ap.parse_args()

    codigos = [int(c) for c in args.codigos.split(",")]
    espera_regimen = 5.0 * args.tau
    SALIDA.mkdir(parents=True, exist_ok=True)
    destino = SALIDA / ("ganancia_regimen_idac2_"
                        + time.strftime("%Y%m%d_%H%M%S") + ".csv")

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    salida = destino.open("w", newline="", encoding="utf-8")
    escritor = csv.writer(salida)
    escritor.writerow(["t_s", "idac2", "momento", "media_banco_uv",
                       "pp_banco_uv", "real_mv", "valido", "clavado"])
    t0 = time.time()
    inmediatas: list[tuple[float, float]] = []
    regimenes: list[tuple[float, float]] = []

    def registrar(codigo: int, momento: str):
        media, pico = leer(lab, CANAL_SUM)
        if media is None:
            registrar.vivo = False
            escritor.writerow([f"{time.time()-t0:.1f}", codigo, momento,
                               "", "", "", "", ""])
            salida.flush()
            return None
        mv = real_mv(media)
        registrar.vivo = (lectura_valida(media / 1000.0)
                          and pico >= PP_CLAVADO_UV)
        escritor.writerow([f"{time.time()-t0:.1f}", codigo, momento,
                           f"{media:.0f}", f"{pico:.0f}", f"{mv:.1f}",
                           lectura_valida(media / 1000.0),
                           pico < PP_CLAVADO_UV])
        salida.flush()
        print(f"    IDAC2={codigo:+5d}  {momento:<10} ch2={mv:+9.1f} mV"
              f"  pp={pico/1000.0:6.1f}"
              f"{'' if lectura_valida(media/1000.0) else '  FUERA DE RANGO'}",
              flush=True)
        return mv

    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        lab.set_gain("pgaout", GAIN_CODES.index(args.pgaout))
        for etapa in range(4):
            lab.set_idac(etapa, 0)
        print(f"PGA x{args.pga}, PGAout x{args.pgaout}. Asentando "
              f"{espera_regimen:.0f} s con todo en Vref...", flush=True)
        time.sleep(espera_regimen)

        for codigo in codigos:
            print(f"  IDAC2 -> {codigo:+d}", flush=True)
            lab.set_idac(2, codigo)
            time.sleep(1.5)
            inmediata = registrar(codigo, "inmediato")
            vivo_inmediato = getattr(registrar, "vivo", False)
            time.sleep(espera_regimen)
            regimen = registrar(codigo, "regimen")
            vivo_regimen = getattr(registrar, "vivo", False)
            # Solo entran al ajuste los puntos VIVOS. Un tap clavado devuelve
            # siempre el mismo numero, y meterlo en los minimos cuadrados
            # aplana la pendiente hacia cero: la primera corrida dio 4,8
            # mV/codigo por incluir tres puntos pegados al riel, cuando los dos
            # puntos validos daban 12,3.
            if inmediata is not None and vivo_inmediato:
                inmediatas.append((float(codigo), inmediata))
            if regimen is not None and vivo_regimen:
                regimenes.append((float(codigo), regimen))

        print("\n=== RESULTADO ===", flush=True)
        if len(inmediatas) >= 2:
            p_i, _ = ajuste_lineal([x for x, _ in inmediatas],
                                   [y for _, y in inmediatas])
            print(f"Pendiente INSTANTANEA: {p_i:+.3f} mV/codigo "
                  f"({len(inmediatas)} puntos)")
        else:
            p_i = None
            print("Pendiente INSTANTANEA: sin puntos suficientes")
        if len(regimenes) >= 2:
            p_r, _ = ajuste_lineal([x for x, _ in regimenes],
                                   [y for _, y in regimenes])
            print(f"Pendiente DE REGIMEN:  {p_r:+.3f} mV/codigo "
                  f"({len(regimenes)} puntos)")
        else:
            p_r = None
            print("Pendiente DE REGIMEN: sin puntos suficientes")
        if p_i and p_r:
            print(f"\nRazon regimen/instantanea: {p_r/p_i:.3f}")
            if abs(p_r) < 0.3 * abs(p_i):
                print("IDAC2 NO sostiene a ch2: el efecto se desvanece. "
                      "Ningun lazo que use la pendiente instantanea puede "
                      "mantener un punto.")
            else:
                print("IDAC2 sostiene a ch2. La pendiente de regimen es la que "
                      "hay que usar en el lazo.")
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
