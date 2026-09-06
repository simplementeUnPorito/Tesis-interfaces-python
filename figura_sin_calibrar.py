# -*- coding: utf-8 -*-
"""La figura del argumento: donde queda cada ganancia SIN calibrar.

Es el grafico que acompaña al apartado de la tesis. Muestra, para cada
combinacion de ganancia, cuanto se aparta de Vref el tap peor parado cuando los
cuatro IDAC estan en cero -o sea, el nodo sin sistema de calibracion-.

POR QUE ESTE GRAFICO Y NO OTRO
Lo que decide si el nodo captura o no es si ALGUNA etapa esta contra el riel: una
etapa saturada no transmite, y no importa cuan bien esten las otras tres. Por eso
la barra es el PEOR tap, no el promedio, y por eso las que tocan riel se dibujan
distinto: no son "peores", son cualitativamente otra cosa.

La linea de Vref esta en cero y los rieles marcados: la distancia de la barra a
la banda gris es lo que la calibracion tiene que recuperar.

Uso:  python figura_sin_calibrar.py [ruta.json]
"""
from __future__ import annotations
import sys, json, glob, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from escala_banco import a_voltios, VREF_V

LAB = r'C:\Github\Tesis\lab\planta'


def main():
    ruta = sys.argv[1] if len(sys.argv) > 1 else None
    if ruta is None:
        cands = sorted(glob.glob(os.path.join(LAB, 'sin_calibrar_*.json')))
        if not cands:
            raise SystemExit("no hay ningun sin_calibrar_*.json en %s" % LAB)
        ruta = cands[-1]
    d = json.load(open(ruta, encoding='utf-8'))
    filas = d["filas"]

    # Una combinacion puede estar repetida (los controles). Para la figura se
    # toma la PRIMERA medida de cada una y las repeticiones quedan para el
    # chequeo de reproducibilidad, que va en el texto y no en el grafico.
    vistas, unicas = set(), []
    for f in filas:
        clave = (f["pga_x"], f["pgaout_x"])
        if clave in vistas:
            continue
        vistas.add(clave)
        unicas.append(f)
    unicas.sort(key=lambda f: (f["pga_x"] * f["pgaout_x"], f["pga_x"]))

    print("%-22s %14s %12s %s" % ("combinacion", "peor tap", "asentada", "veredicto"))
    for f in unicas:
        print("%-22s %11.0f mV %12s %s" % (
            "PGA x%d  PGAout x%d" % (f["pga_x"], f["pgaout_x"]),
            f["peor_mv_reales"] or 0,
            "si" if f.get("asentada", True) else "NO",
            ("EN RIEL %s" % f["en_riel"]) if f["en_riel"] else "en rango"))
    n_riel = sum(1 for f in unicas if f["en_riel"])
    print()
    print("%d de %d combinaciones arrancan con al menos un tap contra el riel"
          % (n_riel, len(unicas)))

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("(sin matplotlib: no se genera la figura)")
        return

    etiquetas = ["%d x %d" % (f["pga_x"], f["pgaout_x"]) for f in unicas]
    valores = [abs(f["peor_mv_reales"] or 0) for f in unicas]
    railada = [bool(f["en_riel"]) for f in unicas]

    fig, ax = plt.subplots(figsize=(10, 5))
    colores = ["#c0392b" if r else "#2c7fb8" for r in railada]
    barras = ax.bar(range(len(unicas)), valores, color=colores)

    # El margen hasta el riel, en mV reales desde Vref: hacia abajo Vref-0 y
    # hacia arriba Vdda-Vref. La banda gris es donde la etapa todavia transmite.
    ax.axhspan(0, VREF_V * 1000, color="#dddddd", zorder=0)
    ax.axhline(VREF_V * 1000, color="#666666", linestyle="--", linewidth=1)
    ax.text(len(unicas) - 0.5, VREF_V * 1000 * 1.02, "riel", ha="right",
            va="bottom", color="#666666", fontsize=9)

    ax.set_xticks(range(len(unicas)))
    ax.set_xticklabels(etiquetas, rotation=45, ha="right")
    ax.set_xlabel("PGA entrada x  PGAout")
    ax.set_ylabel("desvio del PEOR tap respecto de Vref [mV reales]")
    ax.set_title("Sin calibrar: los cuatro IDAC en cero\n"
                 "rojo = al menos una etapa contra el riel, o sea que el nodo no captura")
    ax.grid(axis="y", alpha=0.3)
    for b, r in zip(barras, railada):
        if r:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height(), "riel",
                    ha="center", va="bottom", fontsize=8, color="#c0392b")

    salida = os.path.join(LAB, "figuras", os.path.basename(ruta).replace(".json", ".png"))
    os.makedirs(os.path.dirname(salida), exist_ok=True)
    fig.tight_layout()
    fig.savefig(salida, dpi=140)
    print("figura -> %s" % salida)


if __name__ == "__main__":
    main()
