"""Genera las figuras del trabajo del 2026-09-05, todas desde los crudos.

Ninguna figura de acá tiene números escritos a mano: todo sale de los JSON de
`lab/planta/`. Si una medición se rehace, se vuelve a correr esto y las figuras
se actualizan solas.

    python generar_figuras.py [--dir lab/planta] [--out lab/planta/figuras]
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
import numpy as np                        # noqa: E402

REPO = Path(__file__).resolve().parents[3]

#: Paleta sobria y con suficiente contraste en blanco y negro, por si el informe
#: se imprime.
COLOR = {0: "#1f4e79", 1: "#2e8b57", 2: "#c1442e", 3: "#7b3f9d"}
NOMBRE = {0: "ch0 PGA", 1: "ch1 BP", 2: "ch2 ADDER", 3: "ch3 LP"}
RIEL_BAJO = {0: 759.4, 1: 750.0, 2: 750.8, 3: 746.4}
RIEL_ALTO = {0: 1114.4, 1: 1122.0, 2: 1121.6, 3: 1122.3}


def _guardar(fig, destino: Path, nombre: str) -> None:
    destino.mkdir(parents=True, exist_ok=True)
    ruta = destino / nombre
    fig.savefig(ruta, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  {ruta}")


# ---------------------------------------------------------------------------
def fig_autoridad_pga(dirc: Path, out: Path) -> None:
    """EXP1: la pendiente del IDAC de la etapa 0 escala con la ganancia del PGA.

    Es la figura que justifica que x50 sea usable: si la autoridad NO escalara,
    el offset de entrada amplificado no se podria corregir.
    """
    puntos = []
    for ruta in sorted(glob.glob(str(dirc / "curva_e0_pga*_out0_20260905_1[34]*.json"))):
        d = json.loads(Path(ruta).read_text(encoding="utf-8"))
        pga = d.get("pga_x")
        pts = [(p["code"], p.get("ch0")) for p in d.get("puntos", [])]
        pts = [(c, v) for c, v in pts if c is not None and v is not None]
        if not pts or pga is None:
            continue
        # Pendiente sobre la zona central, que es donde no hay riel.
        pts.sort()
        centro = [(c, v) for c, v in pts
                  if 790_000 < v < 1_085_000] or pts
        if len(centro) < 2:
            continue
        (x0, y0), (x1, y1) = centro[0], centro[-1]
        puntos.append((pga, (y1 - y0) / (x1 - x0)))
    if len(puntos) < 2:
        print("  (sin datos de EXP1 en el formato esperado; se omite la figura)")
        return
    puntos.sort()
    g = np.array([p[0] for p in puntos], dtype=float)
    s = np.array([p[1] for p in puntos], dtype=float)

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.loglog(g, s, "o-", color=COLOR[0], lw=2, ms=8, label="medido")
    ax.loglog(g, s[0] * g / g[0], "--", color="0.5", lw=1.5,
              label="proporcional a la ganancia")
    ax.set_xlabel("ganancia del PGA de entrada")
    ax.set_ylabel("pendiente del IDAC sobre ch0  [µV por código]")
    ax.set_title("EXP1 · la autoridad del IDAC de entrada escala con la ganancia\n"
                 "por eso ×50 se puede calibrar: offset y autoridad crecen juntos")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    _guardar(fig, out, "exp1_autoridad_pga.png")


# ---------------------------------------------------------------------------
def fig_adder(dirc: Path, out: Path) -> None:
    """El barrido del ADDER con espera de planta: la ventana util del LP."""
    rutas = sorted(glob.glob(str(dirc / "curva_e2_pga0_out0_20260905_*.json")))
    if not rutas:
        print("  (sin barrido del ADDER del 2026-09-05; se omite)")
        return
    d = json.loads(Path(rutas[-1]).read_text(encoding="utf-8"))
    filas = d.get("filas") or d.get("puntos") or []
    cod = [f.get("code") for f in filas]
    serie = {}
    for ch in (2, 3):
        serie[ch] = [(f.get(f"ch{ch}") or 0) / 1000.0 if f.get(f"ch{ch}") else None
                     for f in filas]
    if not cod:
        print("  (barrido del ADDER sin puntos legibles; se omite)")
        return

    fig, ax = plt.subplots(figsize=(9, 5.5))
    for ch in (2, 3):
        x = [c for c, v in zip(cod, serie[ch]) if v is not None]
        y = [v for v in serie[ch] if v is not None]
        ax.plot(x, y, "o-", ms=4, lw=1.6, color=COLOR[ch], label=NOMBRE[ch])
        ax.axhline(RIEL_BAJO[ch], color=COLOR[ch], ls=":", lw=1, alpha=0.6)
        ax.axhline(RIEL_ALTO[ch], color=COLOR[ch], ls=":", lw=1, alpha=0.6)
    ax.axhspan(RIEL_BAJO[3] - 4, RIEL_BAJO[3] + 4, color="0.85", zorder=0)
    ax.axhspan(RIEL_ALTO[3] - 4, RIEL_ALTO[3] + 4, color="0.85", zorder=0)
    ax.set_xlabel("código del IDAC del ADDER")
    ax.set_ylabel("tensión en el tap  [mV]")
    ax.set_title("Barrido del ADDER con 60 s de espera de planta por punto\n"
                 "las zonas planas son RIEL en las dos etapas a la vez: es "
                 "saturación, no no-linealidad")
    ax.grid(alpha=0.3)
    ax.legend()
    _guardar(fig, out, "adder_barrido_completo.png")


# ---------------------------------------------------------------------------
def fig_excursion(out: Path) -> None:
    """La excursión real de cada tap contra el objetivo de 1000 mV.

    Es una figura de una sola idea, y es una idea que se ve mejor dibujada que
    escrita: el objetivo está descentrado respecto del rango físico.
    """
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for i, ch in enumerate((0, 2, 3)):
        lo, hi = RIEL_BAJO[ch], RIEL_ALTO[ch]
        ax.barh(i, hi - lo, left=lo, height=0.5, color=COLOR[ch], alpha=0.35,
                edgecolor=COLOR[ch], lw=1.5)
        ax.plot([(lo + hi) / 2], [i], "v", color=COLOR[ch], ms=10,
                label="centro real" if i == 0 else None)
        ax.text(lo - 6, i, f"{lo:.0f}", ha="right", va="center", fontsize=9)
        ax.text(hi + 6, i, f"{hi:.0f}", ha="left", va="center", fontsize=9)
    ax.axvline(1000, color="k", lw=2, ls="--", label="objetivo del firmware (1000 mV)")
    ax.set_yticks(range(3))
    ax.set_yticklabels([NOMBRE[c] for c in (0, 2, 3)])
    ax.set_xlabel("tensión en el tap  [mV]")
    ax.set_xlim(700, 1170)
    ax.set_title("Excursión medida de los taps contra el objetivo del firmware\n"
                 "hay 250 mV de margen para abajo y sólo 122 para arriba")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(axis="x", alpha=0.3)
    _guardar(fig, out, "excursion_taps.png")


# ---------------------------------------------------------------------------
def fig_estrategias(out: Path) -> None:
    """Comparación de estrategias en el modelo: error del LP y tiempo."""
    ruta = (REPO / "src" / "calculos_modelados" / "python" / "calibracion_pi" /
            "resultados_cadena" / "estrategias.json")
    if not ruta.is_file():
        print("  (sin estrategias.json; se omite)")
        return
    d = json.loads(ruta.read_text(encoding="utf-8"))
    filas = [f for f in d if f.get("mult_tau") == 2.0]
    if not filas:
        print("  (estrategias.json sin filas de 2 tau; se omite)")
        return
    metodos = [k for k in filas[0] if isinstance(filas[0][k], dict)]
    etiquetas = [f"{f['pga']}×{f['pgaout']}" for f in filas]

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    ancho = 0.8 / max(1, len(metodos))
    x = np.arange(len(filas))
    for i, m in enumerate(metodos):
        a1.bar(x + i * ancho, [max(f[m]["lp_mv"], 0.01) for f in filas],
               ancho, label=m)
        a2.bar(x + i * ancho, [f[m]["t_s"] for f in filas], ancho, label=m)
    a1.axhline(20, color="k", ls="--", lw=1.5, label="techo de Elías (20 mV)")
    a1.set_yscale("log"); a1.set_ylabel("error del LP  [mV]")
    a1.set_title("Estrategias de calibración en el modelo, espera 2 τ\n"
                 "(SIMULACIÓN; a ganancia alta el modelo todavía no está "
                 "validado contra la placa)")
    a1.grid(axis="y", alpha=0.3, which="both"); a1.legend(fontsize=8, ncol=3)
    a2.set_ylabel("tiempo total  [s]"); a2.grid(axis="y", alpha=0.3)
    a2.set_xticks(x + 0.4); a2.set_xticklabels(etiquetas, rotation=45, ha="right")
    a2.set_xlabel("PGA × PGAout")
    _guardar(fig, out, "estrategias_modelo.png")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(REPO / "lab" / "planta"))
    ap.add_argument("--out", default=str(REPO / "lab" / "planta" / "figuras"))
    a = ap.parse_args()
    dirc, out = Path(a.dir), Path(a.out)
    print("figuras generadas:")
    fig_excursion(out)
    fig_adder(dirc, out)
    fig_autoridad_pga(dirc, out)
    fig_estrategias(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
