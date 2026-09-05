"""Calibración conjunta corrida desde la PC contra la placa real.

POR QUÉ EXISTE
El algoritmo que se quiere poner en el PSoC —minimizar el conjunto ponderado en
vez de llevar cada tap a cero de a uno— hay que probarlo antes de escribirlo en
C. Y se puede: el firmware ``slaveTest`` expone ``idac``, ``dc`` y ``gain``, así
que la PC puede cerrar el lazo entero. Esto es exactamente lo que va a hacer el
firmware, con las mismas cuentas, pero observable paso a paso.

Sirve para tres cosas:

1. **Contestar la pregunta de Elías** —"sin importar la ganancia de los dos PGA
   la cadena siempre se tiene que poder calibrar"— midiendo, no simulando. La
   campaña de reposo no alcanza: dice qué combinaciones arrancan contra el riel,
   pero no si se pueden recuperar. Esto sí.
2. **Validar el optimizador contra el hardware** antes de portarlo.
3. **Medir el tiempo real**, que es lo que Elías pidió minimizar dentro del techo
   de 2 τ.

EL PUNTO QUE HACE QUE ESTO FUNCIONE CON LOS TAPS EN EL RIEL
Si un tap está recortando, su lectura es una COTA, no un valor: el offset real es
al menos ese. Igual sirve, porque la dirección de la corrección es correcta —hay
que alejarse del riel— aunque la magnitud esté subestimada. Iterando, cada vuelta
saca un poco a la cadena del riel hasta que las lecturas se vuelven válidas y las
últimas iteraciones ya trabajan con datos buenos. Por eso el procedimiento es
iterativo y no de un disparo.

USO
    python calibrar_pc.py --port COM8 --pga 8 --pgaout 8
    python calibrar_pc.py --port COM8 --todas          # barre las 81
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
sys.path.insert(0, str(REPO / "src" / "calculos_modelados" / "python" / "calibracion_pi"))

from testbench.core import console as con           # noqa: E402
from testbench.core.lab import GAIN_CODES, Lab      # noqa: E402
from testbench.core.session import Session          # noqa: E402

import modelo_cadena as M                            # noqa: E402
from modelo_cadena import (                          # noqa: E402
    ADC_NIVELES, ADC_SPAN_UV, OBJETIVO_MV, RIEL_ALTO_MV, RIEL_BAJO_MV,
    optimo_conjunto, pesos_por_ganancia_acumulada,
)

SALIDA = REPO / "lab" / "planta"
CANALES = (0, 1, 2, 3)
SETTLE_DC = 3           # 500 ms, el mismo selector que usa D2

#: τ medido en banco. La espera de planta se expresa como múltiplo de esto.
TAU_S = 29.5
MARGEN_RIEL_MV = 8.0


def en_riel(tap: int, mv: float) -> bool:
    return (mv <= RIEL_BAJO_MV[tap] + MARGEN_RIEL_MV or
            mv >= RIEL_ALTO_MV[tap] - MARGEN_RIEL_MV)


def leer_taps(lab: Lab) -> dict[int, float]:
    """Los cuatro taps en mV. Devuelve sólo los que contestaron."""
    out = {}
    for ch in CANALES:
        p = lab.measure_dc(ch, SETTLE_DC)
        if p is not None:
            out[ch] = p.mean_uv / 1000.0
    return out


def resolver(mv: dict[int, float], pga: int, pgaout: int,
             dac_actual: dict[int, int]) -> dict[int, int]:
    """El óptimo conjunto ABSOLUTO a partir de una medida cualquiera.

    Se descuenta el aporte de los IDAC que ya están puestos, así cada iteración
    resuelve el problema completo y no arrastra el error de la anterior.
    """
    guardado = M.G_DC_MEDIDA
    try:
        M.G_DC_MEDIDA = {
            k: {j: guardado[k][j] * (pga if k == 0 else 1) for j in range(4)}
            for k in range(4)
        }
        a_cuentas = ADC_NIVELES * 1000.0 / ADC_SPAN_UV
        off = {}
        for j in CANALES:
            medido = (mv[j] - OBJETIVO_MV) * a_cuentas
            aporte = sum(dac_actual[k] * M.G_DC_MEDIDA[k][j] * ADC_NIVELES / ADC_SPAN_UV
                         for k in CANALES)
            off[j] = medido - aporte
        pesos = pesos_por_ganancia_acumulada(pga, pgaout)
        return optimo_conjunto(off, pesos)
    finally:
        M.G_DC_MEDIDA = guardado


def calibrar(lab: Lab, pga_code: int, out_code: int, iteraciones: int,
             esperas_tau: list[float], verbose: bool = True) -> dict:
    pga = GAIN_CODES[pga_code]
    pgaout = GAIN_CODES[out_code]
    t0 = time.time()

    lab.set_gain("pga", pga_code)
    lab.set_gain("pgaout", out_code)
    dac = {k: 0 for k in CANALES}
    for k in CANALES:
        lab.set_idac(k, 0)

    traza = []
    for it in range(iteraciones):
        espera = esperas_tau[min(it, len(esperas_tau) - 1)] * TAU_S
        if verbose:
            print(f"    iter {it}: esperando {espera:.0f} s a la planta...")
        time.sleep(espera)
        mv = leer_taps(lab)
        if len(mv) < 4:
            return {"error": "no contestaron los cuatro taps", "iter": it,
                    "taps": mv, "pga_x": pga, "pgaout_x": pgaout}
        railados = [j for j in CANALES if en_riel(j, mv[j])]
        if verbose:
            desc = "  ".join(f"ch{j} {mv[j]:8.3f}" + ("*" if j in railados else " ")
                             for j in CANALES)
            print(f"      {desc}    dac={[dac[k] for k in CANALES]}")
        traza.append({"iter": it, "t_s": time.time() - t0,
                      "taps_mv": dict(mv), "dac": dict(dac),
                      "en_riel": railados})
        dac = resolver(mv, pga, pgaout, dac)
        for k in CANALES:
            lab.set_idac(k, dac[k])

    espera = esperas_tau[-1] * TAU_S
    if verbose:
        print(f"    final: esperando {espera:.0f} s...")
    time.sleep(espera)
    mv = leer_taps(lab)
    railados = [j for j in CANALES if j in mv and en_riel(j, mv[j])]
    err = {j: mv[j] - OBJETIVO_MV for j in mv}
    res = {
        "pga_code": pga_code, "pgaout_code": out_code,
        "pga_x": pga, "pgaout_x": pgaout,
        "iteraciones": iteraciones, "esperas_tau": esperas_tau,
        "tau_s": TAU_S,
        "dac_final": dict(dac),
        "taps_final_mv": dict(mv),
        "error_mv": err,
        "error_lp_mv": err.get(3),
        "error_peor_mv": max((abs(v) for v in err.values()), default=None),
        "en_riel_final": railados,
        "t_total_s": time.time() - t0,
        "traza": traza,
        "cumple_20mv": (abs(err.get(3, 1e9)) <= 20.0) and not railados,
    }
    if verbose:
        print(f"    -> LP {err.get(3, float('nan')):+8.2f} mV   "
              f"peor {res['error_peor_mv']:6.2f} mV   "
              f"{res['t_total_s']:.0f} s   "
              f"{'CUMPLE' if res['cumple_20mv'] else 'NO CUMPLE'}")
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=None)
    ap.add_argument("--pga", type=int, default=0, help="codigo 0-8")
    ap.add_argument("--pgaout", type=int, default=0, help="codigo 0-8")
    ap.add_argument("--todas", action="store_true",
                    help="barre las 81 combinaciones, reanudable")
    ap.add_argument("--iter", type=int, default=3)
    ap.add_argument("--esperas", default="2.0,0.5,0.5,0.5",
                    help="multiplos de tau por iteracion, separados por comas")
    a = ap.parse_args()

    esperas = [float(x) for x in a.esperas.split(",")]
    SALIDA.mkdir(parents=True, exist_ok=True)

    c = con.Console(a.port) if a.port else con.Console()
    print(f"Abriendo {c.port} (abrir resetea el ESP)...")
    c.open()
    time.sleep(1.0)
    sess = Session(c)
    lab = Lab(sess)
    try:
        if not a.todas:
            r = calibrar(lab, a.pga, a.pgaout, a.iter, esperas)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            ruta = SALIDA / f"calpc_pga{a.pga}_out{a.pgaout}_{stamp}.json"
            ruta.write_text(json.dumps(r, indent=2), encoding="utf-8")
            print(f"crudo -> {ruta}")
            return 0

        # Barrido completo, reanudable: se saltea lo que ya está.
        from medir_planta import combos_por_dificultad
        combos = combos_por_dificultad()
        for i, (p, o) in enumerate(combos, 1):
            ruta = SALIDA / f"calpc_pga{p}_out{o}.json"
            if ruta.is_file():
                print(f"  [{i}/{len(combos)}] PGA=x{GAIN_CODES[p]} "
                      f"PGAout=x{GAIN_CODES[o]}  ya estaba, salteo")
                continue
            print(f"  [{i}/{len(combos)}] PGA=x{GAIN_CODES[p]} PGAout=x{GAIN_CODES[o]}")
            r = calibrar(lab, p, o, a.iter, esperas)
            ruta.write_text(json.dumps(r, indent=2), encoding="utf-8")
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
