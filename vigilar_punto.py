"""Arma el punto de trabajo y despues NO toca nada durante varios minutos.

El calibrador llega a un punto donde los cinco taps estan vivos y lo pierde en
segundos. Hay dos explicaciones posibles y este script las separa: o el punto no
es un equilibrio y la planta se va sola, o algo que hace el propio calibrador lo
voltea. Aca se arma el punto con el mismo procedimiento y despues se congelan
los cuatro IDAC y se mira, que es la unica forma de distinguirlas.

Escribe una fila por lectura para poder dibujar la curva despues.
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


def buscar_idac2(lab: Lab, objetivo_ch: int, pasos: int = 9) -> int:
    """Barrido grueso de IDAC2 buscando el codigo que deja el tap mas centrado.

    Se hace por enumeracion y no por Newton a proposito: cuando el tap arranca
    clavado no hay pendiente que identificar, y el barrido siempre devuelve algo.
    """
    mejor_codigo, mejor_error = 0, None
    for codigo in range(-240, 241, 480 // (pasos - 1)):
        lab.set_idac(2, codigo)
        time.sleep(1.5)
        media, pico = leer(lab, objetivo_ch, 2)
        if media is None:
            continue
        error = abs(real_mv(media))
        vivo = pico is not None and pico >= PP_CLAVADO_UV
        marca = "" if vivo else "  (clavado)"
        print(f"    IDAC2={codigo:+4d}  ch{objetivo_ch}={real_mv(media):+9.1f} mV"
              f"  pp={0.0 if pico is None else pico/1000.0:6.1f}{marca}",
              flush=True)
        if not vivo:
            continue
        if mejor_error is None or error < mejor_error:
            mejor_codigo, mejor_error = codigo, error
    return mejor_codigo


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--pga", type=int, default=50,
                    help="ganancia del PGA de entrada; el calibrador usa x50")
    ap.add_argument("--pgaout", type=int, default=4,
                    help="ganancia de PGAout a armar")
    ap.add_argument("--idac0", type=int, default=0,
                    help="codigo de IDAC0 a imponer; el que dejo la fase 1")
    ap.add_argument("--idac1", type=int, default=0,
                    help="codigo de IDAC1 a imponer; el que dejo la fase 1")
    ap.add_argument("--idac2", type=int, default=None,
                    help="codigo fijo de IDAC2; sin esto se busca por barrido")
    ap.add_argument("--sin-barrido", action="store_true",
                    help="no busca IDAC2: solo mira como deriva la cadena sola")
    ap.add_argument("--ver", type=int, default=4,
                    help="canal que se imprime durante la vigilancia")
    ap.add_argument("--minutos", type=float, default=6.0)
    ap.add_argument("--periodo", type=float, default=3.0)
    args = ap.parse_args()

    SALIDA.mkdir(parents=True, exist_ok=True)
    sello = time.strftime("%Y%m%d_%H%M%S")
    destino = SALIDA / f"vigilar_punto_pga{args.pga}_out{args.pgaout}_{sello}.csv"

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    salida = destino.open("w", newline="", encoding="utf-8")
    escritor = csv.writer(salida)
    escritor.writerow(["t_s", "bloque", "pgaout", "idac0", "idac1", "idac2",
                       "idac3", "ch", "tap", "media_banco_uv", "pp_banco_uv",
                       "real_mv", "valido", "clavado"])
    codigos = [0, 0, 0, 0]
    t0 = time.time()

    def registrar(bloque: str) -> dict:
        fila = {}
        for ch in TAPS:
            media, pico = leer(lab, ch)
            if media is None:
                escritor.writerow([f"{time.time()-t0:.2f}", bloque, args.pgaout,
                                   *codigos, ch, TAPS[ch], "", "", "", "", ""])
                continue
            mv = real_mv(media)
            clav = pico < PP_CLAVADO_UV
            escritor.writerow([f"{time.time()-t0:.2f}", bloque, args.pgaout,
                               *codigos, ch, TAPS[ch], f"{media:.0f}",
                               f"{pico:.0f}", f"{mv:.1f}",
                               lectura_valida(media / 1000.0), clav])
            fila[ch] = (mv, pico, clav)
        salida.flush()
        return fila

    try:
        lab.set_adc_config(1)
        lab.set_gain("pga", GAIN_CODES.index(args.pga))
        indice = GAIN_CODES.index(args.pgaout)
        lab.set_gain("pgaout", indice)
        for etapa in range(4):
            lab.set_idac(etapa, 0)
        # Los codigos de fase 1 se imponen ANTES de asentar, porque IDAC0 e
        # IDAC1 alimentan el pasabanda y su cola es de treinta segundos.
        lab.set_idac(0, args.idac0)
        lab.set_idac(1, args.idac1)
        codigos[0], codigos[1] = args.idac0, args.idac1
        print(f"IDAC0={args.idac0:+d}  IDAC1={args.idac1:+d}", flush=True)
        print(f"PGA x{args.pga}, PGAout x{args.pgaout}. Asentando 100 s (3 tau del pasabanda)...",
              flush=True)
        time.sleep(100.0)
        registrar("reposo")

        if args.idac2 is not None:
            print(f"IDAC2 fijo en {args.idac2:+d}.", flush=True)
            codigo2 = args.idac2
        elif args.sin_barrido:
            print("Sin barrido: se mira la cadena tal como quedo.", flush=True)
            codigo2 = 0
        else:
            print("Barriendo IDAC2 para centrar LPo:", flush=True)
            codigo2 = buscar_idac2(lab, 4)
        lab.set_idac(2, codigo2)
        codigos[2] = codigo2
        time.sleep(2.0)
        fila = registrar("punto_armado")
        if 4 in fila:
            print(f"  punto armado con IDAC2={codigo2:+d}: "
                  f"LPo={fila[4][0]:+.1f} mV", flush=True)

        print(f"\nCONGELADO. Nada se toca durante {args.minutos:.0f} min.",
              flush=True)
        fin = time.time() + args.minutos * 60.0
        primera = None
        while time.time() < fin:
            fila = registrar("vigilancia")
            if args.ver in fila:
                mv, pico, clav = fila[args.ver]
                if primera is None:
                    primera = mv
                print(f"  t={time.time()-t0:6.0f}s  {TAPS[args.ver]}={mv:+9.1f} mV  "
                      f"(deriva {mv-primera:+8.1f})  pp={pico/1000.0:6.1f}"
                      f"{'  CLAVADO' if clav else ''}", flush=True)
            time.sleep(max(0.0, args.periodo))
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
