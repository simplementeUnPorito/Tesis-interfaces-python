"""Figuras del banco. Matplotlib, para que el modo terminal también deje gráficos.

Criterios de color, en este orden:

* **La forma primero.** El checklist no es un gráfico: es una tabla con estado,
  y se dibuja como tabla. Los números sí se grafican.
* **Color por el trabajo que hace.** La matriz D2 tiene signo y magnitud, así
  que va en divergente azul<->rojo con gris neutro en el medio, nunca arcoíris.
  Los veredictos van con la paleta de estado, y siempre acompañados del texto:
  el color no carga significado solo. Las comparaciones de dos corridas usan las
  dos primeras ranuras categóricas.
* **Nunca dos ejes Y.** Cuando dos magnitudes no comparten escala, van en dos
  paneles.

La paleta es la de referencia del skill de visualización, sin modificar. Se usan
como mucho dos ranuras categóricas, dentro del subconjunto que esa paleta
documenta como validado para todos los pares en ambos modos.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

import matplotlib

matplotlib.use("Agg")  # el modo terminal no tiene ventana

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, SymLogNorm
from matplotlib.figure import Figure

from .checklist import (
    D2_MIN_SLOPE_UV_FIRMWARE,
    D2_MIN_SLOPE_UV_PLACA,
    STAGE_NAMES,
    TAP_NAMES,
    Measurements,
)

# -- paleta ----------------------------------------------------------------
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"

SERIES_1 = "#2a78d6"  # azul
SERIES_2 = "#eb6834"  # naranja

STATUS = {
    "PASS": "#0ca30c",
    "FAIL": "#d03b3b",
    "WARN": "#fab219",
    "SKIP": "#898781",
    "INFO": "#52514e",
}

# Divergente azul<->rojo con gris neutro al medio, pasos de la propia paleta.
DIVERGING = LinearSegmentedColormap.from_list(
    "banco_div",
    ["#104281", "#2a78d6", "#9ec5f4", "#f0efec", "#eda1a1", "#d03b3b", "#8c1f1f"],
)


def _style(ax: plt.Axes, titulo: str = "", ylabel: str = "") -> None:
    """Cromo recesivo: la grilla y los ejes no compiten con los datos."""
    ax.set_facecolor(SURFACE)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        ax.spines[lado].set_color(BASELINE)
        ax.spines[lado].set_linewidth(1.0)
    ax.tick_params(colors=MUTED, labelsize=9, length=3, width=0.8)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    if titulo:
        ax.set_title(titulo, color=INK, fontsize=11, loc="left", pad=10)
    if ylabel:
        ax.set_ylabel(ylabel, color=INK_2, fontsize=9)


def _figure(w: float, h: float) -> Figure:
    fig = plt.figure(figsize=(w, h), facecolor=SURFACE, dpi=140)
    return fig


# --------------------------------------------------------------------------
# D2 — matriz de transferencia
# --------------------------------------------------------------------------
def fig_d2_matrix(meas: Measurements, titulo: str = "") -> Figure:
    """Matriz DC IDAC->tap, en µV por código.

    Divergente porque el signo importa tanto como el módulo: una etapa mueve su
    propio tap y los de aguas abajo, con signos que dependen de si la etapa
    inversora está en el camino. Escala simétrica logarítmica porque los valores
    van de 0,5 a 6800 µV/código y una lineal aplastaría todo menos la última
    columna.
    """
    m = np.array(
        [[np.nan if v is None else v for v in fila] for fila in meas.d2], dtype=float
    )
    fig = _figure(7.6, 5.4)
    ax = fig.add_subplot(111)
    ax.set_facecolor(SURFACE)

    finito = m[np.isfinite(m)]
    tope = float(np.nanmax(np.abs(finito))) if finito.size else 1.0
    norm = SymLogNorm(linthresh=10.0, vmin=-tope, vmax=tope, base=10)
    im = ax.imshow(m, cmap=DIVERGING, norm=norm)

    ax.set_xticks(range(4), [f"ch{i}\n{TAP_NAMES[i]}" for i in range(4)])
    ax.set_yticks(range(4), [f"etapa {i}\n{STAGE_NAMES[i]}" for i in range(4)])
    ax.tick_params(colors=INK_2, labelsize=9, length=0)
    for lado in ("top", "right", "left", "bottom"):
        ax.spines[lado].set_visible(False)

    # Separación de 2 px entre celdas: el hueco es del fondo, no una línea.
    ax.set_xticks(np.arange(-0.5, 4, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, 4, 1), minor=True)
    ax.grid(which="minor", color=SURFACE, linewidth=2.5)
    ax.tick_params(which="minor", length=0)

    for i in range(4):
        for j in range(4):
            v = m[i, j]
            if not np.isfinite(v):
                ax.text(j, i, "—", ha="center", va="center", color=MUTED, fontsize=10)
                continue
            # Tinta clara sólo sobre celdas oscuras. Se decide con el color que
            # la celda realmente tiene (la norma simétrica log satura mucho
            # antes que el valor crudo), no con el valor: si no, un 770 sobre
            # rojo fuerte se dibujaba en tinta oscura y quedaba ilegible.
            fuerte = abs(float(norm(v)) - 0.5) > 0.30
            ax.text(
                j,
                i,
                f"{v:,.1f}".replace(",", " "),
                ha="center",
                va="center",
                color="#ffffff" if fuerte else INK,
                fontsize=9,
                fontweight="bold" if i == j else "normal",
            )

    ax.set_title(
        titulo or "D2 · cuánto mueve cada referencia a cada punto de la cadena",
        color=INK,
        fontsize=11.5,
        loc="left",
        pad=30,
    )
    ax.text(
        0,
        1.075,
        "Muevo el IDAC de una etapa un código y mido los cuatro puntos: cada celda es "
        "cuántos µV se movió ese punto.",
        transform=ax.transAxes, ha="left", va="bottom", color=INK_2, fontsize=8.5,
    )
    ax.text(
        0,
        1.020,
        "Diagonal (negrita) = la etapa mueve SU punto. A la derecha = se propaga aguas "
        "abajo, es lo esperado. A la izquierda = fuga hacia atrás.",
        transform=ax.transAxes, ha="left", va="bottom", color=MUTED, fontsize=8,
    )
    # Sin flechas en los rótulos: el de Y se dibuja rotado y una flecha ahí
    # apunta para cualquier lado menos el que uno quiere.
    ax.set_ylabel("muevo esta referencia", color=INK_2, fontsize=9.5, labelpad=10)
    # El rótulo del eje X va corto porque se centra sobre el eje: uno largo se
    # sale por la izquierda de la figura. La aclaración va al pie.
    ax.set_xlabel("y mido en este punto", color=INK_2, fontsize=9.5, labelpad=10)
    cb = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.03)
    cb.outline.set_visible(False)
    cb.ax.tick_params(colors=MUTED, labelsize=8)
    cb.set_label("µV / código (escala simétrica log)", color=INK_2, fontsize=8.5)
    # Margen explícito: con tight_layout el rótulo largo del eje X se recortaba.
    fig.subplots_adjust(left=0.20, right=0.99, top=0.80, bottom=0.20)
    fig.text(
        0.02, 0.025,
        "Los cuatro puntos están en orden de la cadena: entrada a la izquierda, "
        "salida al ADC a la derecha.",
        color=MUTED, fontsize=8,
    )
    return fig


def fig_d2_diagonal(meas: Measurements) -> Figure:
    """Diagonal contra los dos umbrales: el del firmware y el de esta placa.

    Un solo conjunto de barras, así que no lleva caja de leyenda: el título dice
    qué son. Los dos umbrales van como líneas de referencia rotuladas.
    """
    diag = meas.d2_diagonal()
    vals = [0.0 if v is None else abs(v) for v in diag]
    pasa = [v is not None and abs(v) >= D2_MIN_SLOPE_UV_PLACA for v in diag]

    fig = _figure(7.2, 4.2)
    ax = fig.add_subplot(111)
    _style(ax, "Diagonal de D2: cada etapa contra su propio tap", "µV / código")

    x = np.arange(4)
    colores = [STATUS["PASS"] if ok else STATUS["FAIL"] for ok in pasa]
    ax.bar(x, vals, width=0.5, color=colores, zorder=3)

    for i, (v, ok) in enumerate(zip(vals, pasa)):
        ax.text(
            i,
            v * 1.08,
            f"{v:,.0f}".replace(",", " ") + ("" if ok else "  ✗"),
            ha="center",
            va="bottom",
            color=INK if ok else STATUS["FAIL"],
            fontsize=9,
        )

    for umbral, etiqueta, estilo in (
        (D2_MIN_SLOPE_UV_PLACA, f"umbral de esta placa ({D2_MIN_SLOPE_UV_PLACA:.0f})", "-"),
        (D2_MIN_SLOPE_UV_FIRMWARE, f"umbral del firmware ({D2_MIN_SLOPE_UV_FIRMWARE:.0f})", "--"),
    ):
        ax.axhline(umbral, color=MUTED, linewidth=1.2, linestyle=estilo, zorder=2)
        ax.text(3.45, umbral, etiqueta, color=MUTED, fontsize=8, va="center", ha="left")

    ax.set_yscale("log")
    ax.set_xticks(x, [f"{i}\n{STAGE_NAMES[i]}" for i in range(4)])
    ax.set_xlim(-0.6, 3.4)
    # Sitio para las etiquetas de dos renglones y para la nota al pie: sin esto
    # los nombres de etapa se montaban encima del texto.
    fig.subplots_adjust(left=0.10, right=0.72, top=0.88, bottom=0.30)
    fig.text(
        0.02,
        0.10,
        "El umbral del firmware asume 3,75 mV/código (R = 30 kΩ, portadora JitX).",
        color=MUTED,
        fontsize=7.5,
    )
    fig.text(
        0.02,
        0.045,
        "Esta placa tiene R = 15 kΩ: el LSB real es 1,875 mV y el umbral equivalente, la mitad.",
        color=MUTED,
        fontsize=7.5,
    )
    return fig


# --------------------------------------------------------------------------
# D6 — piso de ruido
# --------------------------------------------------------------------------
def fig_d6(
    meas: Measurements,
    comparacion: Optional[Measurements] = None,
    etiquetas: tuple[str, str] = ("esta corrida", "referencia"),
) -> Figure:
    """RMS de ruido por tap. Con dos corridas, barras agrupadas y leyenda.

    Sirve para el par con y sin geófono: es el único discriminador confiable de
    que el sensor está cargando la entrada.
    """
    fig = _figure(7.2, 4.2)
    ax = fig.add_subplot(111)
    _style(ax, "Piso de ruido por tap (D6)", "RMS [µV]")

    x = np.arange(4)
    a = [meas.d6.get(i, {}).get("rms_uv", 0.0) for i in range(4)]

    if comparacion is None:
        ax.bar(x, a, width=0.5, color=SERIES_1, zorder=3)
        for i, v in enumerate(a):
            ax.text(i, v * 1.06, f"{v:,.0f}".replace(",", " "), ha="center",
                    va="bottom", color=INK, fontsize=9)
    else:
        b = [comparacion.d6.get(i, {}).get("rms_uv", 0.0) for i in range(4)]
        ancho = 0.34
        ax.bar(x - ancho / 2 - 0.01, a, width=ancho, color=SERIES_1,
               label=etiquetas[0], zorder=3)
        ax.bar(x + ancho / 2 + 0.01, b, width=ancho, color=SERIES_2,
               label=etiquetas[1], zorder=3)
        for i, (va, vb) in enumerate(zip(a, b)):
            ax.text(i - ancho / 2, va * 1.06, f"{va:,.0f}".replace(",", " "),
                    ha="center", va="bottom", color=INK, fontsize=8)
            ax.text(i + ancho / 2, vb * 1.06, f"{vb:,.0f}".replace(",", " "),
                    ha="center", va="bottom", color=INK, fontsize=8)
        leg = ax.legend(frameon=False, fontsize=9, loc="upper left")
        for t in leg.get_texts():
            t.set_color(INK_2)

    ax.set_yscale("log")
    ax.set_xticks(x, [f"ch{i}\n{TAP_NAMES[i]}" for i in range(4)])
    # Márgenes explícitos: con un tap en cero el eje logarítmico se vuelve
    # degenerado y tight_layout avisa que no puede acomodar las decoraciones.
    fig.subplots_adjust(left=0.11, right=0.97, top=0.88, bottom=0.16)
    return fig


# --------------------------------------------------------------------------
# D7 — golpe al geófono
# --------------------------------------------------------------------------
def fig_d7(taps: Sequence[dict]) -> Figure:
    """Pico contra fondo, intento por intento.

    Dos series en la misma escala de counts, así que van en el mismo eje con
    leyenda. Un intento vale cuando el pico se despega del fondo; los que no,
    son capturas sin golpe adentro, no fallas del sensor.
    """
    fig = _figure(7.6, 4.4)
    ax = fig.add_subplot(111)
    _style(ax, "D7 · ¿el geófono responde al golpe?", "counts del ADC")
    ax.text(
        0, 1.02,
        "Un intento vale cuando la barra azul (lo más fuerte que se midió) se despega "
        "de la naranja (el ruido de esa captura).",
        transform=ax.transAxes, ha="left", va="bottom", color=MUTED, fontsize=8,
    )

    if not taps:
        ax.text(0.5, 0.5, "sin intentos de D7", transform=ax.transAxes,
                ha="center", va="center", color=MUTED, fontsize=11)
        ax.set_xticks([])
        return fig

    x = np.arange(len(taps))
    picos = [t.get("pico") or 0 for t in taps]
    fondos = [t.get("fondo_pp") or 0 for t in taps]
    ancho = 0.34

    ax.bar(x - ancho / 2 - 0.01, picos, width=ancho, color=SERIES_1,
           label="pico de la captura", zorder=3)
    ax.bar(x + ancho / 2 + 0.01, fondos, width=ancho, color=SERIES_2,
           label="fondo pico a pico", zorder=3)

    for i, t in enumerate(taps):
        if t.get("verdict") == "PASS":
            pol = t.get("polaridad") or ""
            ax.text(i - ancho / 2, (t.get("pico") or 1) * 1.08,
                    f"{t.get('pico')}\n{pol.lower()}", ha="center", va="bottom",
                    color=STATUS["PASS"], fontsize=8, linespacing=1.3)

    ax.set_yscale("log")
    ax.set_xticks(x, [str(t.get("intento", i + 1)) for i, t in enumerate(taps)])
    ax.set_xlabel("intento", color=INK_2, fontsize=9)
    leg = ax.legend(frameon=False, fontsize=9, loc="upper right")
    for t in leg.get_texts():
        t.set_color(INK_2)

    buenos = sum(1 for t in taps if t.get("verdict") == "PASS")
    fig.text(0.02, 0.02,
             f"{buenos} de {len(taps)} intentos con el golpe dentro de la ventana de 1,47 s. "
             "Los demás son capturas sin golpe, no fallas del sensor.",
             color=MUTED, fontsize=7.5)
    fig.subplots_adjust(bottom=0.18)
    return fig


# --------------------------------------------------------------------------
# Modo manual — monitor en vivo y barridos
# --------------------------------------------------------------------------
def fig_monitor(samples: Sequence, ch: int = 0, titulo: str = "") -> Figure:
    """Traza del monitor lento: un tap contra el tiempo.

    Cambio en el tiempo, así que va en línea, no en barras. Una sola serie:
    sin caja de leyenda, el título dice qué es.
    """
    fig = _figure(8.0, 4.0)
    ax = fig.add_subplot(111)
    nombre = TAP_NAMES[ch] if 0 <= ch < len(TAP_NAMES) else f"ch{ch}"
    _style(ax, titulo or f"Monitor del tap ch{ch} ({nombre})", "mV respecto de Vref")

    if not samples:
        ax.text(0.5, 0.5, "sin muestras", transform=ax.transAxes, ha="center",
                va="center", color=MUTED, fontsize=11)
        ax.set_xticks([])
        return fig

    t = np.array([s.t_ms for s in samples], dtype=float) / 1000.0
    y = np.array([s.mean_uv for s in samples], dtype=float) / 1000.0
    ax.plot(t, y, color=SERIES_1, linewidth=2.0, solid_capstyle="round")
    ax.set_xlabel("segundos desde el arranque del monitor", color=INK_2, fontsize=9)

    # Etiqueta directa sobre el último punto en vez de un número por muestra.
    ax.annotate(f"{y[-1]:,.1f} mV".replace(",", " "), xy=(t[-1], y[-1]),
                xytext=(6, 0), textcoords="offset points", color=INK,
                fontsize=9, va="center")
    rango = float(y.max() - y.min())
    fig.text(0.02, 0.02,
             f"{len(samples)} muestras · excursión {rango * 1000:,.0f} µV".replace(",", " "),
             color=MUTED, fontsize=8)
    fig.subplots_adjust(left=0.11, right=0.93, top=0.88, bottom=0.20)
    return fig


def fig_sweep(sweep, titulo: str = "") -> Figure:
    """Barrido de una etapa de IDAC contra los taps que se hayan medido.

    Un panel por tap y no todos juntos en un eje: los taps de aguas abajo tienen
    ganancias muy distintas y superponerlos aplastaría los de arriba. Nunca dos
    escalas en un mismo eje.
    """
    canales = sorted(sweep.points)
    n = max(1, len(canales))
    fig = _figure(8.0, 2.3 * n + 1.0)
    stage = STAGE_NAMES[sweep.stage] if 0 <= sweep.stage < len(STAGE_NAMES) else str(sweep.stage)

    if not canales:
        ax = fig.add_subplot(111)
        _style(ax, titulo or f"Barrido de {stage}", "")
        ax.text(0.5, 0.5, "sin puntos", transform=ax.transAxes, ha="center",
                va="center", color=MUTED, fontsize=11)
        return fig

    for i, ch in enumerate(canales):
        ax = fig.add_subplot(n, 1, i + 1)
        nombre = TAP_NAMES[ch] if 0 <= ch < len(TAP_NAMES) else f"ch{ch}"
        _style(ax, "" if i else (titulo or f"Barrido del IDAC de {stage}"),
               f"ch{ch} {nombre} [mV]")
        pts = sorted(sweep.points[ch])
        x = np.array([p[0] for p in pts], dtype=float)
        y = np.array([p[1] for p in pts], dtype=float) / 1000.0
        ax.plot(x, y, color=SERIES_1, linewidth=2.0, marker="o", markersize=4,
                markerfacecolor=SERIES_1, markeredgecolor=SURFACE, markeredgewidth=1.0)

        pend = sweep.slope_uv_per_code(ch)
        gan = sweep.gain_from_reference(ch)
        if pend is not None:
            # La recta ajustada encima de los puntos: si la etapa satura, se ve
            # que los puntos se despegan de ella en una punta.
            b = float(np.mean(y * 1000.0 - pend * x))
            ax.plot(x, (pend * x + b) / 1000.0, color=MUTED, linewidth=1.0,
                    linestyle="--", zorder=2)
            ax.text(0.99, 0.06, f"{pend:,.1f} µV/código  ·  ganancia {gan:,.2f}x"
                    .replace(",", " "),
                    transform=ax.transAxes, ha="right", va="bottom",
                    color=INK_2, fontsize=8.5)
        if i == len(canales) - 1:
            ax.set_xlabel("código del IDAC", color=INK_2, fontsize=9)

    # El margen inferior se reserva en pulgadas y no en fracción: con un solo
    # panel una fracción chica dejaba el pie encima del rótulo del eje X.
    alto = 2.3 * n + 1.0
    fig.subplots_adjust(left=0.13, right=0.97, top=0.93,
                        bottom=0.75 / alto, hspace=0.30)
    fig.text(0.02, 0.16 / alto,
             "La ganancia sale de dividir la pendiente por el escalón real de esta "
             "placa, 1875 µV por código.",
             color=MUTED, fontsize=7.5)
    return fig


# --------------------------------------------------------------------------
# Checklist — es una tabla, no un gráfico
# --------------------------------------------------------------------------
def fig_checklist(items: Sequence, verdict: dict, titulo: str = "") -> Figure:
    """El checklist como tabla con chip de estado.

    Deliberadamente no es un gráfico de barras de conteos: lo que se necesita
    leer es qué ítem falló y con qué detalle. El color del chip nunca va solo,
    siempre lleva el texto del veredicto al lado.
    """
    n = len(items)
    fig = _figure(11.0, max(2.2, 0.30 * n + 1.4))
    ax = fig.add_subplot(111)
    ax.axis("off")
    ax.set_facecolor(SURFACE)

    c = verdict["counts"]
    encabezado = (
        f"{c['PASS']} PASS · {c['FAIL']} FAIL · {c['WARN']} WARN · "
        f"{c['SKIP']} SKIP · {c['INFO']} INFO    —    {verdict['board_verdict']}"
    )
    ax.text(0, 1.0, titulo or "Checklist del autotest", transform=ax.transAxes,
            color=INK, fontsize=12, va="top")
    ax.text(0, 0.965, encabezado, transform=ax.transAxes,
            color=STATUS["FAIL"] if verdict["fails"] else STATUS["PASS"],
            fontsize=10, va="top")

    paso = 1.0 / (n + 4)
    y = 1.0 - 3.2 * paso
    for it in items:
        col = STATUS.get(it.verdict, MUTED)
        ax.add_patch(
            plt.Rectangle((0.0, y - paso * 0.18), 0.010, paso * 0.62,
                          transform=ax.transAxes, color=col, clip_on=False)
        )
        ax.text(0.020, y, it.code, transform=ax.transAxes, color=INK,
                fontsize=8.5, va="center", family="monospace")
        ax.text(0.075, y, it.name[:52], transform=ax.transAxes, color=INK_2,
                fontsize=8.5, va="center")
        ax.text(0.400, y, it.verdict, transform=ax.transAxes, color=col,
                fontsize=8.5, va="center", family="monospace")
        ax.text(0.462, y, it.detail[:88], transform=ax.transAxes, color=MUTED,
                fontsize=8, va="center")
        y -= paso

    return fig


# --------------------------------------------------------------------------
# Guardado
# --------------------------------------------------------------------------
def save_all(
    outdir: Path | str,
    items: Sequence,
    verdict: dict,
    meas: Measurements,
    taps: Optional[Sequence[dict]] = None,
    comparacion: Optional[Measurements] = None,
    prefijo: str = "",
) -> list[Path]:
    """Escribe todas las figuras que tengan datos. Devuelve las rutas."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    escritas: list[Path] = []

    def guardar(fig: Figure, nombre: str) -> None:
        p = outdir / f"{prefijo}{nombre}.png"
        fig.savefig(p, facecolor=SURFACE, bbox_inches="tight")
        plt.close(fig)
        escritas.append(p)

    if items:
        guardar(fig_checklist(items, verdict), "checklist")
    if any(v is not None for fila in meas.d2 for v in fila):
        guardar(fig_d2_matrix(meas), "d2_matriz")
        guardar(fig_d2_diagonal(meas), "d2_diagonal")
    if meas.d6:
        guardar(fig_d6(meas, comparacion), "d6_ruido")
    lista = list(taps) if taps else meas.d7
    if lista:
        guardar(fig_d7(lista), "d7_golpe")
    return escritas


__all__ = [
    "fig_monitor",
    "fig_sweep",
    "fig_d2_matrix",
    "fig_d2_diagonal",
    "fig_d6",
    "fig_d7",
    "fig_checklist",
    "save_all",
    "STATUS",
    "SERIES_1",
    "SERIES_2",
    "SURFACE",
    "INK",
    "INK_2",
    "MUTED",
    "GRID",
]
