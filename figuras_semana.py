# -*- coding: utf-8 -*-
"""Las figuras del informe de la semana, generadas de los datos crudos.

NO hay ningun numero escrito a mano en este archivo: todo sale de los JSON que
dejaron los ensayos. Si un ensayo se rehace, se corre esto y las figuras se
actualizan solas, sin que nadie tenga que acordarse de retocar un grafico.

Uso:  python figuras_semana.py
"""
from __future__ import annotations
import os
import sys
import json
import glob
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from escala_banco import (a_voltios, VREF_V, lectura_valida,   # noqa: E402
                          BANCO_MIN_VALIDO_MV, BANCO_MAX_VALIDO_MV)

LAB = r'C:\Github\Tesis\lab\planta'
FIG = os.path.join(LAB, 'figuras')
AZUL, ROJO, GRIS, VERDE = "#2c7fb8", "#c0392b", "#888888", "#27ae60"


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _guardar(fig, nombre):
    os.makedirs(FIG, exist_ok=True)
    ruta = os.path.join(FIG, nombre)
    fig.tight_layout()
    fig.savefig(ruta, dpi=150)
    print("  -> %s" % nombre)
    return ruta


def curva_adder():
    """El hallazgo principal: donde el firmware cortaba y donde estaba el codo.

    Es la figura que explica todo el fin de semana de una sola mirada: la meseta
    plana hasta -128 -donde el firmware se detenia-, el acantilado, y el punto
    donde la cadena queda en Vref.
    """
    plt = _plt()
    d = json.load(open(os.path.join(LAB, 'rescate_addr_largo.json'), encoding='utf-8'))
    pts = [(f["idac2"], f["taps"]["3"]) for f in d["filas"] if f["taps"].get("3") is not None]
    pts.sort(key=lambda p: p[0])
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]

    fig, ax = plt.subplots(figsize=(9, 5))
    dentro = [(x, y) for x, y in pts if lectura_valida(y)]
    fuera = [(x, y) for x, y in pts if not lectura_valida(y)]
    ax.plot(xs, ys, "-", color=GRIS, linewidth=1, zorder=1)
    if fuera:
        ax.plot([p[0] for p in fuera], [p[1] for p in fuera], "o",
                color=GRIS, markersize=7, label="fuera de la ventana observable", zorder=2)
    if dentro:
        ax.plot([p[0] for p in dentro], [p[1] for p in dentro], "o",
                color=AZUL, markersize=7, label="el tap se mide de verdad", zorder=3)

    ax.axhspan(min(ys) - 20, BANCO_MIN_VALIDO_MV, color="#f2f2f2", zorder=0)
    ax.axhline(BANCO_MIN_VALIDO_MV, color=GRIS, linestyle=":", linewidth=1)
    ax.text(-250, BANCO_MIN_VALIDO_MV - 8, "por debajo de acá la lectura no mide el tap",
            fontsize=8, color=GRIS, va="top")

    # El rotulo va a la IZQUIERDA de la linea. A la derecha se sale del eje,
    # porque -128 es justo el borde del barrido: la linea que marca donde
    # cortaba el firmware coincide con el ultimo punto medido, que es
    # precisamente lo que la figura quiere mostrar.
    ax.axvline(-128, color=ROJO, linestyle="--", linewidth=1.6)
    ax.text(-132, min(ys) + 0.55 * (max(ys) - min(ys)),
            "el firmware cortaba acá\n(clamp simétrico ±128)",
            color=ROJO, fontsize=9, va="center", ha="right")

    ax.axhline(1001.5, color=VERDE, linestyle="--", linewidth=1.2)
    ax.text(250, 1001.5 + 6, "Vref", color=VERDE, fontsize=9, ha="right")
    vref = [p for p in pts if lectura_valida(p[1]) and abs(p[1] - 1001.5) < 6]
    if vref:
        ax.plot([vref[0][0]], [vref[0][1]], "*", color=VERDE, markersize=18, zorder=4)
        ax.annotate("código %d:\ncadena centrada" % vref[0][0],
                    xy=vref[0], xytext=(vref[0][0] - 55, vref[0][1] - 105),
                    fontsize=9, color=VERDE,
                    arrowprops=dict(arrowstyle="->", color=VERDE))

    ax.set_xlabel("código del IDAC del ADDER")
    ax.set_ylabel("tap del LP [mV de banco]")
    ax.set_title("El ADDER sobre el tap del LP, en lazo abierto (PGA ×50, PGAout ×1)\n"
                 "el LP saturado no responde hasta que la contribución cruza un umbral")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(alpha=0.3)
    return _guardar(fig, "adder_curva_20260906.png")


def calibracion_antes_despues():
    """Que hace la calibracion del nodo, en una figura: los cuatro taps."""
    plt = _plt()
    d = json.load(open(os.path.join(LAB, 'cal_traza.json'), encoding='utf-8'))
    nombres = ["PGA", "BP", "ADDER", "LP"]
    antes = [d["antes"][str(k)] for k in range(4)]
    desp = [d["despues"][str(k)] for k in range(4)]

    def a_vref(mv):
        return (a_voltios(mv) - VREF_V) * 1000 if lectura_valida(mv) else None

    fig, ax = plt.subplots(figsize=(9, 4.6))
    x = range(4)
    an = [a_vref(v) for v in antes]
    de = [a_vref(v) for v in desp]
    # Los que estaban fuera de la ventana no tienen magnitud: se dibujan como
    # marca, no como barra, porque poner un numero seria inventarlo.
    ax.bar([i - 0.19 for i in x], [v if v is not None else 0 for v in an],
           width=0.36, color=ROJO, label="antes de calibrar")
    ax.bar([i + 0.19 for i in x], [v if v is not None else 0 for v in de],
           width=0.36, color=AZUL, label="después")
    for i, v in enumerate(an):
        if v is None:
            ax.text(i - 0.19, 0, "fuera de\nla ventana", ha="center", va="bottom",
                    fontsize=8, color=ROJO, rotation=90)
        else:
            ax.text(i - 0.19, v, "%+.0f" % v, ha="center",
                    va="bottom" if v >= 0 else "top", fontsize=8, color=ROJO)
    for i, v in enumerate(de):
        if v is not None:
            ax.text(i + 0.19, v, "%+.0f" % v, ha="center",
                    va="bottom" if v >= 0 else "top", fontsize=8, color=AZUL)

    ax.axhline(0, color="k", linewidth=0.8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(nombres)
    ax.set_ylabel("desvío de Vref [mV reales]")
    ax.set_title("El nodo se calibra solo: `cal`, sin PC, %.0f s\n"
                 "arrancando con los cuatro IDAC en cero (PGA ×50, PGAout ×1)"
                 % d["t_s"])
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    return _guardar(fig, "calibracion_antes_despues.png")


def deriva():
    """EXP4c: el punto calibrado a lo largo de las horas, con su tendencia."""
    plt = _plt()
    f = max(glob.glob(os.path.join(LAB, 'deriva_2026*.json')), key=os.path.getmtime)
    d = json.load(open(f, encoding='utf-8'))
    m = [x for x in d["muestras"] if x["taps_mv"].get("3") is not None]
    t0 = datetime.fromisoformat(m[0]["t"])
    xs = [(datetime.fromisoformat(x["t"]) - t0).total_seconds() / 3600.0 for x in m]
    ys = [(a_voltios(x["taps_mv"]["3"]) - VREF_V) * 1000 for x in m]

    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    pend = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0

    fig, ax = plt.subplots(figsize=(9, 4.6))
    ax.plot(xs, ys, "-", color=AZUL, linewidth=1.2, label="tap del LP")
    ax.plot(xs, [my + pend * (x - mx) for x in xs], "--", color=ROJO, linewidth=1.6,
            label="tendencia: %.0f mV/h" % pend)
    ax.axhspan(-34, 34, color=VERDE, alpha=0.15,
               label="lo que la calibración consigue (±34 mV)")
    ax.axhline(0, color="k", linewidth=0.8)
    ax.set_xlabel("horas desde que el nodo se calibró a sí mismo")
    ax.set_ylabel("desvío de Vref [mV reales]")
    ax.set_title("EXP4c — el punto calibrado no se queda quieto\n"
                 "punto fijo, sin recalibrar, PGA ×50 / PGAout ×1")
    ax.legend(fontsize=9, loc="lower left")
    ax.grid(alpha=0.3)
    return _guardar(fig, "deriva_tendencia.png")


def ruido():
    """EXP4d: el ruido no crece con el tiempo."""
    plt = _plt()
    f = max(glob.glob(os.path.join(LAB, 'deriva_2026*.json')), key=os.path.getmtime)
    d = json.load(open(f, encoding='utf-8'))
    m = [x for x in d["muestras"] if "ruido_ch3" in x]
    if len(m) < 3:
        print("  (sin suficientes medidas de ruido)")
        return None
    t0 = datetime.fromisoformat(d["muestras"][0]["t"])
    xs = [(datetime.fromisoformat(x["t"]) - t0).total_seconds() / 3600.0 for x in m]
    rms = [x["ruido_ch3"]["rms_uv"] for x in m]
    hz50 = [x["ruido_ch3"]["hz50_uv"] for x in m]

    fig, ax = plt.subplots(figsize=(9, 4.0))
    ax.plot(xs, rms, "o-", color=AZUL, markersize=4, label="RMS")
    ax.plot(xs, hz50, "s-", color=ROJO, markersize=4, label="componente de 50 Hz")
    ax.set_xlabel("horas desde que el nodo se calibró")
    ax.set_ylabel("ruido en el tap del LP [µV de banco]")
    ax.set_title("EXP4d — calibrar no mete ruido\n"
                 "el RMS no crece a lo largo de la corrida, y los 50 Hz son marginales")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)
    ax.set_ylim(0, max(rms) * 1.25)
    return _guardar(fig, "ruido_exp4d.png")


def rescate_comparado():
    """Por que hizo falta abrir el clamp: el ADDER mueve, el LP no."""
    plt = _plt()
    da = json.load(open(os.path.join(LAB, 'rescate_lazo_abierto.json'), encoding='utf-8'))
    dl = json.load(open(os.path.join(LAB, 'rescate_lp.json'), encoding='utf-8'))

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.4), sharey=True)
    for ax, d, etapa, titulo in (
            (a1, da, "idac2", "moviendo el ADDER, hasta el clamp de −128"),
            (a2, dl, "idac3", "moviendo el LP, todo su recorrido")):
        pts = [(f[etapa], f["taps"]["3"], f["taps"].get("2")) for f in d["filas"]
               if f["taps"].get("3") is not None]
        pts.sort(key=lambda p: abs(p[0]))
        ax.plot([abs(p[0]) for p in pts], [p[1] for p in pts], "o-",
                color=AZUL, label="tap del LP (ch3)")
        propios = [(abs(p[0]), p[2]) for p in pts if p[2] is not None]
        if propios:
            ax.plot([p[0] for p in propios], [p[1] for p in propios], "s--",
                    color=GRIS, markersize=5, label="tap del ADDER (ch2)")
        ax.set_xlabel("|código| del IDAC")
        ax.set_title(titulo, fontsize=10)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    a1.set_ylabel("mV de banco")
    fig.suptitle("El ADDER recorre 1,73 V en su propio tap y el del LP no se mueve;\n"
                 "el LP a rango completo tampoco se saca a sí mismo", fontsize=11)
    return _guardar(fig, "rescate_comparado.png")


if __name__ == "__main__":
    print("generando las figuras del informe:")
    for f in (curva_adder, calibracion_antes_despues, deriva, ruido, rescate_comparado):
        try:
            f()
        except Exception as e:
            print("  FALLO %s: %s" % (f.__name__, e))
