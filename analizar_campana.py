"""Analiza la campaña de las 81 combinaciones PGA x PGAout.

QUÉ PREGUNTA CONTESTA
La de Elías: "sin importar la ganancia de los DOS PGA la cadena siempre se tiene
que poder calibrar". Esto dice, para cada una de las 81 combinaciones:

  1. si la cadena arranca DENTRO DE RANGO o ya contra el riel;
  2. si arranca dentro de rango, cuánto error queda DESPUÉS de la mejor
     corrección posible, resolviendo el óptimo conjunto sobre el offset medido;
  3. y por lo tanto cuáles combinaciones son usables en campo y cuáles no.

POR QUÉ NO ALCANZA CON MIRAR EL REPOSO
Que un tap esté a 100 mV del objetivo no significa que la combinación sea mala:
significa que hay que corregirla. Lo que decide es si la corrección EXISTE
dentro del rango de los IDAC. Eso es exactamente lo que resuelve el optimizador,
así que se lo aplica al offset medido de cada combinación.

LO QUE ESTE ANÁLISIS NO PUEDE DECIR, y hay que tenerlo presente: si el reposo ya
está contra el riel, el offset verdadero es DESCONOCIDO -sólo se sabe que es
mayor que el riel-, así que para esas combinaciones no se puede calcular el
óptimo. Quedan marcadas como "riel" y hay que medirlas de nuevo aplicando primero
una corrección gruesa que las traiga al rango.

    python analizar_campana.py [--dir lab/planta] [--figs lab/planta/analisis]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src" / "calculos_modelados" / "python" / "calibracion_pi"))

from modelo_cadena import (  # noqa: E402
    ADC_NIVELES, ADC_SPAN_UV, GAIN_CODES, RIEL_ALTO_MV, RIEL_BAJO_MV,
    OBJETIVO_MV, optimo_conjunto, pesos_por_ganancia_acumulada,
)
import modelo_cadena as M  # noqa: E402

#: Margen contra el riel, en mV, por debajo del cual se considera que el tap ya
#: está recortando. No es cero porque cerca del riel la etapa ya no es lineal
#: aunque todavía se mueva: la curva de la etapa 2 mostró pendientes locales que
#: caen a 0,4 µV/código antes de aplanarse del todo.
MARGEN_RIEL_MV = 8.0

NOMBRE_TAP = {0: "ch0 PGA", 1: "ch1 BP", 2: "ch2 ADDER", 3: "ch3 LP"}


def en_riel(tap: int, mv: float) -> bool:
    return (mv <= RIEL_BAJO_MV[tap] + MARGEN_RIEL_MV or
            mv >= RIEL_ALTO_MV[tap] - MARGEN_RIEL_MV)


def cargar(directorio: Path) -> list[dict]:
    filas = []
    for ruta in sorted(directorio.glob("campana_pga*_out*.json")):
        d = json.loads(ruta.read_text(encoding="utf-8"))
        # medir_planta guarda {"filas": [fila]} por combinacion; se acepta
        # tambien la fila suelta por si el formato cambia.
        for fila in d.get("filas") or [d]:
            if fila:
                filas.append(fila)
    return filas


def indice_de_ganancia(g: int) -> int:
    return GAIN_CODES.index(g) if g in GAIN_CODES else -1


def analizar_una(d: dict) -> dict:
    """Devuelve el veredicto de una combinación."""
    pga = d.get("pga_x") or GAIN_CODES[d.get("pga_code", 0)]
    out = d.get("pgaout_x") or GAIN_CODES[d.get("pgaout_code", 0)]
    reposo = d.get("reposo_uv") or {}
    mv = {}
    for k, v in reposo.items():
        if v is None:
            continue
        mv[int(k)] = v / 1000.0

    fila = {"pga": pga, "pgaout": out, "reposo_mv": mv}

    if len(mv) < 4:
        fila["veredicto"] = "incompleta"
        return fila

    railados = [j for j in range(4) if en_riel(j, mv[j])]
    fila["taps_en_riel"] = [NOMBRE_TAP[j] for j in railados]
    fila["peor_reposo_mv"] = max(abs(mv[j] - OBJETIVO_MV) for j in range(4))

    if railados:
        # No se puede calcular el óptimo: el offset real es desconocido, sólo se
        # sabe que la etapa ya no responde. Se informa y se marca para remedir.
        fila["veredicto"] = "riel en reposo"
        fila["residual_mv"] = None
        return fila

    # Óptimo conjunto sobre el offset MEDIDO. La fila 0 de la matriz escala con
    # la ganancia del PGA, medido el 2026-09-05 (EXP1).
    G0 = {k: {j: M.G_DC_MEDIDA[k][j] for j in range(4)} for k in range(4)}
    guardado = M.G_DC_MEDIDA
    try:
        M.G_DC_MEDIDA = {
            k: {j: G0[k][j] * (pga if k == 0 else 1) for j in range(4)}
            for k in range(4)
        }
        off_cuentas = {j: (mv[j] - OBJETIVO_MV) * ADC_NIVELES * 1000.0 / ADC_SPAN_UV
                       for j in range(4)}
        pesos = pesos_por_ganancia_acumulada(pga, out)
        dac = optimo_conjunto(off_cuentas, pesos)
        residual = {}
        for j in range(4):
            r = off_cuentas[j] + sum(dac[k] * M.G_DC_MEDIDA[k][j] * ADC_NIVELES / ADC_SPAN_UV
                                     for k in range(4))
            residual[j] = r * ADC_SPAN_UV / (ADC_NIVELES * 1000.0)   # a mV
    finally:
        M.G_DC_MEDIDA = guardado

    fila["dac"] = dac
    fila["residual_mv"] = residual
    fila["residual_lp_mv"] = abs(residual[3])
    fila["residual_peor_mv"] = max(abs(v) for v in residual.values())
    fila["esfuerzo_max"] = max(abs(v) for v in dac.values())
    fila["veredicto"] = "OK" if abs(residual[3]) <= 20.0 else "no cumple 20 mV"
    return fila


def tabla(filas: list[dict]) -> None:
    print()
    print("REPOSO Y CORRECCION OPTIMA POR COMBINACION")
    print("(residual = lo que queda en el LP despues del optimo conjunto)")
    print()
    print(f"{'PGA':>5} {'out':>5} | {'peor reposo':>12} | {'residual LP':>12} "
          f"| {'esfuerzo':>8} | veredicto")
    print("-" * 78)
    for f in sorted(filas, key=lambda x: (x["pga"], x["pgaout"])):
        peor = f.get("peor_reposo_mv")
        res = f.get("residual_lp_mv")
        esf = f.get("esfuerzo_max")
        print(f"{f['pga']:>5} {f['pgaout']:>5} | "
              f"{(f'{peor:10.1f} mV' if peor is not None else '         -'):>12} | "
              f"{(f'{res:10.2f} mV' if res is not None else '         -'):>12} | "
              f"{(f'{esf:6d}' if esf is not None else '     -'):>8} | "
              f"{f['veredicto']}")


def mapa(filas: list[dict], destino: Path) -> None:
    """Mapa de 9x9: verde lo usable, rojo lo que arranca en riel."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("(sin matplotlib: no se generan figuras)")
        return

    n = len(GAIN_CODES)
    z = np.full((n, n), np.nan)
    texto = [["" for _ in range(n)] for _ in range(n)]
    for f in filas:
        i = indice_de_ganancia(f["pga"])
        j = indice_de_ganancia(f["pgaout"])
        if i < 0 or j < 0:
            continue
        if f.get("residual_lp_mv") is None:
            z[i, j] = np.inf
            texto[i][j] = "riel"
        else:
            z[i, j] = f["residual_lp_mv"]
            texto[i][j] = f"{f['residual_lp_mv']:.1f}"

    fig, ax = plt.subplots(figsize=(9, 7.5))
    vis = np.where(np.isinf(z), np.nan, z)
    im = ax.imshow(np.log10(np.maximum(vis, 0.01)), cmap="RdYlGn_r",
                   origin="lower", aspect="auto")
    # Las de riel se pintan aparte, en gris, para que no se confundan con "malo
    # pero medido": son "no medible", que es distinto.
    for i in range(n):
        for j in range(n):
            if np.isinf(z[i, j]):
                ax.add_patch(plt.Rectangle((j - .5, i - .5), 1, 1,
                                           facecolor="0.45", edgecolor="none"))
            ax.text(j, i, texto[i][j], ha="center", va="center", fontsize=7.5,
                    color="black" if not np.isinf(z[i, j]) else "white")
    ax.set_xticks(range(n)); ax.set_xticklabels([f"x{g}" for g in GAIN_CODES])
    ax.set_yticks(range(n)); ax.set_yticklabels([f"x{g}" for g in GAIN_CODES])
    ax.set_xlabel("PGAout"); ax.set_ylabel("PGA de entrada")
    ax.set_title("Error residual del LP tras la corrección óptima, en mV\n"
                 "gris = arranca contra el riel, no se puede calcular")
    cb = fig.colorbar(im, ax=ax); cb.set_label("log10 del error del LP en mV")
    fig.tight_layout()
    destino.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destino, dpi=140)
    print(f"figura -> {destino}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(REPO / "lab" / "planta"))
    ap.add_argument("--figs", default=str(REPO / "lab" / "planta" / "analisis"))
    a = ap.parse_args()

    crudos = cargar(Path(a.dir))
    if not crudos:
        print("no hay archivos campana_*.json todavia")
        return 1
    filas = [analizar_una(d) for d in crudos]
    tabla(filas)

    ok = [f for f in filas if f["veredicto"] == "OK"]
    riel = [f for f in filas if f["veredicto"] == "riel en reposo"]
    mal = [f for f in filas if f["veredicto"] == "no cumple 20 mV"]
    print()
    print(f"RESUMEN sobre {len(filas)} combinaciones medidas:")
    print(f"  usables (LP <= 20 mV tras corregir) : {len(ok)}")
    print(f"  no cumplen 20 mV                    : {len(mal)}")
    print(f"  arrancan contra el riel             : {len(riel)}")
    if riel:
        print("  -> esas hay que remedirlas con una correccion gruesa aplicada antes")

    mapa(filas, Path(a.figs) / "campana_residual_lp.png")
    salida = Path(a.figs) / "campana_analisis.json"
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(filas, indent=2, default=str), encoding="utf-8")
    print(f"crudo del analisis -> {salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
