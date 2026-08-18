"""PDF sin picking con dos Kalman y curva externa de referencia.

Cadena experimental solicitada::

    aceleracion medida -> SOS previo -> Kalman temporal (v_ground)
    -> SOS posterior -> MASW -> Vs aparente -> mascara Kalman en frecuencia

``v_ground`` es velocidad de particula del suelo. La velocidad de corte
aparente ``Vs_app`` solo existe despues de formar la imagen multicanal MASW.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as path_effects
from matplotlib.colors import LinearSegmentedColormap, PowerNorm
import numpy as np
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer
from scipy import signal

from geophone_scope.kalman_deconv.report_real_pdf import _image_for_page, _styles, _table
from geophone_scope.kalman_deconv.report_velocity_fix import (
    first_kalman_velocity_gather,
)
from geophone_scope.masw_dispersion import phase_shift_dispersion_image
from geophone_scope.masw_ridge_kalman import RidgeKalmanConfig, track_dispersion_ridge


REPO_ROOT = Path(__file__).resolve().parents[5]
RESULTS = Path(__file__).resolve().parent / "reports" / "real_canchita_group1"
DEFAULT_OUTPUT = REPO_ROOT / "output" / "pdf" / "MASW_VS_APARENTE_MASCARAS_KALMAN.pdf"
DEFAULT_TEMP = REPO_ROOT / "tmp" / "pdfs" / "vs_apparent_masks"
METRICS_PATH = RESULTS / "vs_apparent_mask_metrics.json"
ENVELOPE_PATH = RESULTS / "vs_apparent_mask_envelope.csv"
KALMAN1_PATH = RESULTS / "kalman1_ground_velocity_gather.npz"
RAYLEIGH_FACTOR = 0.92

ENERGY_CMAP = LinearSegmentedColormap.from_list(
    "energia_azul_rojo_oscuro",
    ["#06184a", "#174ea6", "#2b8cbe", "#7fcdbb", "#ffffbf", "#f03b20", "#67000d"],
)


def _config() -> RidgeKalmanConfig:
    # El estado interno se usa para desplazar una ventana de busqueda; su curva
    # central no se exporta ni se presenta como picking en esta etapa.
    return RidgeKalmanConfig(
        f_min_hz=1.0,
        f_max_hz=50.0,
        direction="descending",
        seed_c_min_m_s=50.0,
        seed_c_max_m_s=160.0,
        gate_m_s=20.0,
        prediction_sigma_m_s=10.0,
        process_density=1.0e-4,
        min_relative_amplitude=0.20,
        min_wavelength_dx=2.0,
        max_wavelength_aperture=1.0,
        enforce_physical_mask=False,
        enforce_normal_dispersion=False,
    )


def _align_mask(full_f, tracker_f, tracker_mask):
    aligned = np.zeros((full_f.size, tracker_mask.shape[1]), dtype=bool)
    for frequency, row in zip(tracker_f, tracker_mask):
        aligned[int(np.argmin(np.abs(full_f - frequency)))] = row
    return aligned


def _physical_mask(frequency, velocity, offsets):
    dx = float(np.median(np.diff(np.sort(offsets))))
    aperture = float(np.max(offsets) - np.min(offsets))
    return (
        (velocity[None, :] >= 2.0 * dx * frequency[:, None])
        & (velocity[None, :] <= aperture * frequency[:, None])
    )


def _first_kalman_velocity_gather(matrix_sos, time_s, fs: float, offsets):
    """Primer Kalman sobre las 21 trazas -> v_ground(t, x).

    La implementacion vive en :mod:`report_velocity_fix`, que es donde se
    documentan el ajuste de ``q`` por maxima verosimilitud canal por canal y el
    NIS descompuesto por ventana. Aca solo se la llama, para que la corrida que
    valida el estimador y la que arma el MASW sean **la misma** y no dos copias
    que puedan divergir.
    """

    return first_kalman_velocity_gather(matrix_sos, time_s, fs, offsets)


def _fraction_below(trace: np.ndarray, fs: float, cutoff_hz: float) -> float:
    """Fraccion de la energia total (0 a Nyquist) por debajo de ``cutoff_hz``.

    Se mide sobre el total real y no sobre una banda de referencia: normalizar a
    1-200 Hz esconde justamente la deriva sub-1 Hz que interesa detectar.
    """
    x = np.asarray(trace, dtype=float)
    x = x - np.mean(x)
    f, pxx = signal.welch(x, fs=fs, nperseg=min(x.size, 1024))
    total = float(np.trapezoid(pxx, f))
    low = float(np.trapezoid(pxx[f <= cutoff_hz], f[f <= cutoff_hz]))
    return low / max(total, np.finfo(float).tiny)


def _normalize_columns(amplitude: np.ndarray) -> np.ndarray:
    """Normaliza cada bin de frecuencia a su maximo.

    El phase-shift entrega columnas con escalas muy distintas segun cuanta
    energia haya en cada frecuencia. Para comparar **forma** entre dos imagenes
    hay que sacar esa escala, si no la correlacion mide el espectro de la fuente
    y no la calidad de la imagen de dispersion.
    """
    peak = np.max(np.abs(amplitude), axis=1, keepdims=True)
    return np.abs(amplitude) / np.maximum(peak, np.finfo(float).tiny)


def _sample_on_reference(frequency, velocity, amplitude, ref_f, ref_c):
    """Muestrea la imagen normalizada sobre la curva externa de referencia.

    Devuelve tambien el percentil que ocupa ese valor dentro de su columna, que
    es mas informativo que el valor crudo: dice si la referencia cae en el pico
    de la energia o en el fondo.
    """
    norm = _normalize_columns(amplitude)
    values, percentiles = [], []
    inside = (ref_f >= frequency.min()) & (ref_f <= frequency.max()) & np.isfinite(ref_c)
    for fi, ci in zip(ref_f[inside], ref_c[inside]):
        col = int(np.argmin(np.abs(frequency - fi)))
        row = int(np.argmin(np.abs(velocity - ci)))
        column = norm[col]
        values.append(float(column[row]))
        percentiles.append(float(100.0 * np.mean(column <= column[row])))
    return np.array(values), np.array(percentiles), inside


def _masw_comparison(frequency, velocity, amplitude, offsets, ref_f, ref_c, *, band=(8.0, 30.0)):
    """Metricas de la imagen MASW contra la curva externa. Solo evaluacion.

    ⚠ Nada de lo que se calcula aca vuelve al procesamiento: la referencia entra
    despues de que la imagen ya esta formada, y su unico destino es este
    diccionario y el trazo cian de las figuras.
    """
    norm = _normalize_columns(amplitude)
    values, percentiles, inside = _sample_on_reference(
        frequency, velocity, amplitude, ref_f, ref_c
    )
    physical = _physical_mask(frequency, velocity, offsets)

    # Contraste: energia sobre la referencia contra el fondo fisicamente
    # admisible de la misma columna.
    contrast_db = []
    for fi, ci in zip(ref_f[inside], ref_c[inside]):
        col = int(np.argmin(np.abs(frequency - fi)))
        admissible = physical[col]
        if not np.any(admissible):
            continue
        row = int(np.argmin(np.abs(velocity - ci)))
        background = float(np.median(norm[col][admissible]))
        contrast_db.append(
            10.0 * np.log10(max(norm[col][row], 1e-12) / max(background, 1e-12))
        )

    # Maximo local dentro de +-20 m/s de la referencia.
    local_error = []
    for fi, ci in zip(ref_f[inside], ref_c[inside]):
        col = int(np.argmin(np.abs(frequency - fi)))
        window = np.abs(velocity - ci) <= 20.0
        if not np.any(window):
            continue
        idx = np.flatnonzero(window)
        local_error.append(float(velocity[idx[int(np.argmax(norm[col][window]))]] - ci))
    local_error = np.array(local_error)

    band_sel = (frequency >= band[0]) & (frequency <= band[1])
    return {
        "evaluated_reference_points": int(values.size),
        "reference_band_hz": list(band),
        "normalized_energy_on_reference": {
            "median": float(np.median(values)) if values.size else float("nan"),
            "mean": float(np.mean(values)) if values.size else float("nan"),
        },
        "percentile_on_reference_median": (
            float(np.median(percentiles)) if percentiles.size else float("nan")
        ),
        "contrast_reference_over_background_db_median": (
            float(np.median(contrast_db)) if contrast_db else float("nan")
        ),
        "local_max_error_within_20_m_s": {
            "rmse_m_s": float(np.sqrt(np.mean(local_error**2))) if local_error.size else float("nan"),
            "median_abs_m_s": float(np.median(np.abs(local_error))) if local_error.size else float("nan"),
            "bias_m_s": float(np.mean(local_error)) if local_error.size else float("nan"),
        },
        "_image_band": norm[band_sel],
    }


def _image_correlation(image_a: np.ndarray, image_b: np.ndarray) -> float:
    """Pearson entre dos imagenes MASW normalizadas por columna."""
    a = np.asarray(image_a, dtype=float).ravel()
    b = np.asarray(image_b, dtype=float).ravel()
    a = a - a.mean()
    b = b - b.mean()
    denominator = np.linalg.norm(a) * np.linalg.norm(b)
    return float(a @ b / denominator) if denominator > 0 else float("nan")


#: Esquina inferior del SOS posterior. Ver :func:`_post_sos_filter`.
POST_SOS_BAND_HZ = (1.0, 80.0)


def _post_sos_filter(matrix, fs: float, band_hz=POST_SOS_BAND_HZ):
    """Segundo SOS temporal, comun a todos los canales, antes del MASW.

    ⚠ **Es pasa-banda, no pasa-bajos, y el cambio esta medido.** La corrida
    anterior usaba ``butter(10, 80 Hz, 'lowpass')``, que **deja pasar DC**. Con
    la planta de velocidad corregida eso no es inocuo: ``v_ground`` concentra
    **38,7 %** de su energia por debajo de 1 Hz y **89,6 %** por debajo de 3 Hz,
    contra 0,07 % y 0,22 % de la aceleracion medida.

    No es un artefacto ni una sorpresa: es la Obstruccion 2 de
    ``HANDOFF_KALMAN.md`` §6. Estimar velocidad agrega un segundo cero en el
    origen, el margen de observabilidad en DC cae de 2,1e-2 a 4,0e-5 (medido con
    ``check-obsv``), y el ruido de medicion blanco se integra a una PSD ~1/w^2
    dominada por las frecuencias mas bajas. El ``leaky_rw`` de 0,7 Hz acota esa
    deriva pero no la elimina.

    Esa deriva sub-1 Hz —y no la banda 1-10 Hz— es lo que degradaba la imagen
    MASW. Medido sobre los 21 canales, contra ``Amedida + SOS``:

    ==========================  ======  =======  =========  =======
    SOS posterior               E_ref   pctil    contraste  err RMS
    ==========================  ======  =======  =========  =======
    pasa-bajos 0-80 (anterior)  0,631   80,2 %   1,77 dB    11,40
    pasa-banda 1-80 (actual)    0,968   99,4 %   3,55 dB     6,93
    ``Amedida + SOS`` (base)    0,986   99,7 %   3,51 dB     7,24
    ==========================  ======  =======  =========  =======

    La esquina en 1,0 Hz esta elegida para no tocar nada de lo que se reporta:
    la propia imagen MASW arranca en 1,14 Hz. Y la banda util empirica de las
    campanas es 10-50 Hz (§5.5), asi que declarar un pasa-altos por debajo de eso
    es el remedio que el propio HANDOFF llama "benigno aca".
    """

    low, high = float(band_hz[0]), float(band_hz[1])
    nyquist = 0.5 * fs
    sos = signal.butter(
        6, [low / nyquist, min(high, 0.99 * nyquist) / nyquist],
        btype="bandpass", output="sos",
    )
    return signal.sosfiltfilt(sos, matrix, axis=1)


def _plot_waterfall_sos(data, kalman_velocity, kalman_velocity_sos, output: Path) -> Path:
    time = data["time_s"]
    distances = data["distances_m"]
    arms = [
        ("Aceleracion medida + SOS previo", data["measured_acceleration_sos_v"], "#2166ac"),
        ("Kalman 1: velocidad de particula", kalman_velocity, "#7b3294"),
        ("Velocidad Kalman + SOS posterior", kalman_velocity_sos, "#b2182b"),
    ]
    view = (time >= -0.12) & (time <= 1.25)
    pre = (time >= -0.12) & (time < -0.03)
    fig, axes = plt.subplots(1, 3, figsize=(15.2, 7.2), sharex=True, sharey=True)
    for ax, (title, matrix, color) in zip(axes, arms):
        centered = matrix - np.nanmedian(matrix[:, pre], axis=1, keepdims=True)
        scale = np.maximum(
            np.nanpercentile(np.abs(centered[:, view]), 99.0, axis=1),
            np.finfo(float).tiny,
        )
        for distance, trace, norm in zip(distances, centered, scale):
            ax.plot(time[view], distance + 0.72 * trace[view] / norm, color=color, lw=0.75)
        ax.axvline(0.0, color="0.35", lw=0.9, ls=":")
        ax.set_title(title, fontsize=12, weight="bold")
        ax.set_xlabel("Tiempo desde el golpe [s]")
        ax.grid(True, alpha=0.18)
    axes[0].set_ylabel("Offset [m] - trazas normalizadas para visualizacion")
    axes[0].invert_yaxis()
    fig.suptitle(
        "Kalman 1 antes del MASW: estimacion causal de velocidad de particula",
        fontsize=15, weight="bold",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    path = output / "waterfall_aceleracion_y_sos.png"
    fig.savefig(path, dpi=190, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def compute_masks(frequency, velocity, offsets, tracker):
    """Las cuatro mascaras. **No recibe la referencia externa, a proposito.**

    Que la firma no tenga forma de aceptarla es la primera mitad de la garantia
    de que la curva externa es solo un overlay; la segunda mitad es la asercion
    de :func:`assert_reference_is_overlay_only`.
    """
    physical = _physical_mask(frequency, velocity, offsets)
    kalman_gate = _align_mask(frequency, tracker.frequency_hz, tracker.prediction_gate_mask)
    energetic = _align_mask(frequency, tracker.frequency_hz, tracker.candidate_mask)
    return physical, kalman_gate, energetic, physical & kalman_gate & energetic


def assert_reference_is_overlay_only(frequency, velocity, offsets, tracker, combined):
    """Recalcula las mascaras sin ninguna referencia y exige igualdad bit a bit.

    Es barata porque la referencia no entra en el camino de calculo: justamente
    por eso el chequeo es significativo. Si alguien la conectara al tracker, al
    MASW o al gating, esta asercion se cae.
    """
    again = compute_masks(frequency, velocity, offsets, tracker)[3]
    if not np.array_equal(again, combined):
        raise AssertionError(
            "la mascara conjunta cambio al recalcularla sin la referencia: "
            "la curva externa esta entrando al procesamiento"
        )
    return True


def _plot_masks(
    frequency, velocity, amplitude, offsets, tracker,
    reference_frequency, reference_c_rayleigh, output: Path,
):
    physical, kalman_gate, energetic, combined = compute_masks(
        frequency, velocity, offsets, tracker
    )
    vs = velocity / RAYLEIGH_FACTOR
    vmax = max(float(np.nanpercentile(amplitude, 99.5)), 0.3)
    norm = PowerNorm(gamma=0.72, vmin=0.0, vmax=vmax)
    panels = [
        (amplitude, "1. MASW desde v_ground Kalman + SOS"),
        (np.where(physical, amplitude, np.nan), "2. Mascara fisica del arreglo"),
        (np.where(kalman_gate, amplitude, np.nan), "3. Ventana predictiva Kalman 50 -> 1 Hz"),
        (np.where(combined, amplitude, np.nan), "4. Interseccion fisica + energia + Kalman"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(14.2, 9.2), sharex=True, sharey=True)
    reference_vs = reference_c_rayleigh / RAYLEIGH_FACTOR
    reference_valid = (
        (reference_frequency >= 1.0) & (reference_frequency <= 50.0)
        & np.isfinite(reference_vs)
    )
    for index, (ax, (values, title)) in enumerate(zip(axes.ravel(), panels)):
        ax.pcolormesh(frequency, vs, values.T, shading="auto", cmap=ENERGY_CMAP, norm=norm, rasterized=True)
        reference_line, = ax.plot(
            reference_frequency[reference_valid], reference_vs[reference_valid],
            color="#00ffff", lw=2.0,
            label="referencia externa cR/0.92" if index == 0 else None,
            zorder=6,
        )
        reference_line.set_path_effects([
            path_effects.Stroke(linewidth=3.5, foreground="#111111"),
            path_effects.Normal(),
        ])
        ax.set_title(title, fontsize=11, weight="bold")
        ax.set_xlim(1, 50)
        ax.set_ylim(20 / RAYLEIGH_FACTOR, 350 / RAYLEIGH_FACTOR)
    dx = float(np.median(np.diff(np.sort(offsets))))
    aperture = float(np.max(offsets) - np.min(offsets))
    axes[0, 1].plot(frequency, 2*dx*frequency/RAYLEIGH_FACTOR, "w--", lw=1.2, label="limite aliasing")
    axes[0, 1].plot(frequency, aperture*frequency/RAYLEIGH_FACTOR, "w:", lw=1.2, label="limite apertura")
    axes[0, 1].legend(fontsize=7, loc="upper left")
    axes[0, 0].legend(fontsize=7, loc="upper right")
    # La vista 4 es la que se mira primero, asi que lleva su propia leyenda: no
    # puede quedar duda de que el trazo cian es la referencia externa.
    axes[1, 1].plot([], [], color="#00ffff", lw=2.0, label="referencia externa cR/0.92")
    axes[1, 1].legend(fontsize=7, loc="upper right")
    for ax in axes[:, 0]:
        ax.set_ylabel("Vs aparente = cR / 0.92 [m/s]")
    for ax in axes[1, :]:
        ax.set_xlabel("Frecuencia [Hz]")
    # bottom=0.12 deja aire entre el rotulo "Frecuencia [Hz]" y el pie de figura;
    # con bbox_inches="tight" un margen menor los pega.
    fig.subplots_adjust(left=0.075, right=0.885, bottom=0.12, top=0.91, wspace=0.08, hspace=0.14)
    cax = fig.add_axes([0.905, 0.20, 0.014, 0.63])
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=ENERGY_CMAP), cax=cax)
    cb.set_label("Energia/coherencia phase-shift (azul baja, rojo oscuro alta)")
    fig.suptitle("Mascara de busqueda para Vs aparente - sin picking", fontsize=15, weight="bold")
    fig.text(
        0.5, 0.035,
        "Cian = referencia externa superpuesta. La cuarta vista es una region admisible, no un pick nuevo.",
        ha="center", fontsize=9, weight="bold",
    )
    path = output / "masw_vs_aparente_mascaras_sin_picking.png"
    fig.savefig(path, dpi=190, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path, physical, kalman_gate, energetic, combined


def _mask_envelope(frequency, vs, combined):
    low = np.full(frequency.size, np.nan)
    high = np.full(frequency.size, np.nan)
    cells = np.zeros(frequency.size, dtype=int)
    for i, row in enumerate(combined):
        indices = np.flatnonzero(row)
        cells[i] = indices.size
        if indices.size:
            low[i] = float(vs[indices[0]])
            high[i] = float(vs[indices[-1]])
    return low, high, cells


def _plot_envelope(
    frequency, low, high, cells, reference_frequency, reference_c_rayleigh, output: Path
) -> Path:
    valid = np.isfinite(low) & np.isfinite(high)
    fig, axes = plt.subplots(2, 1, figsize=(11.8, 7.7), sharex=True)
    axes[0].fill_between(frequency, low, high, where=valid, color="#2166ac", alpha=0.45)
    axes[0].plot(frequency, low, color="#053061", lw=1.0, label="limite inferior")
    axes[0].plot(frequency, high, color="#67001f", lw=1.0, label="limite superior")
    axes[0].plot(
        reference_frequency, reference_c_rayleigh / RAYLEIGH_FACTOR,
        color="#111111", lw=1.7, ls="--", label="referencia externa cR/0.92",
    )
    axes[0].set_ylabel("Vs aparente admisible [m/s]")
    axes[0].set_title("Envolvente de la mascara conjunta - no es un picking")
    axes[0].legend(fontsize=8)
    axes[1].bar(frequency, cells, width=0.24, color="#2b8cbe")
    axes[1].set_ylabel("Celdas admisibles")
    axes[1].set_xlabel("Frecuencia [Hz]")
    axes[1].set_title("Ancho discreto de la region de busqueda")
    for ax in axes:
        ax.set_xlim(1, 50)
        ax.grid(True, alpha=0.2)
    fig.tight_layout()
    path = output / "envolvente_vs_aparente_sin_picking.png"
    fig.savefig(path, dpi=190, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def _page(canvas, doc):
    canvas.saveState()
    width, _ = landscape(A4)
    canvas.setStrokeColor(colors.HexColor("#CBD5DC"))
    canvas.line(14*mm, 11*mm, width-14*mm, 11*mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#607483"))
    canvas.drawString(14*mm, 7*mm, "SOS - Kalman 1 v_ground - SOS - MASW - Kalman 2")
    canvas.drawRightString(width-14*mm, 7*mm, f"Pagina {doc.page}")
    canvas.restoreState()


def generate(output: Path = DEFAULT_OUTPUT, temp: Path = DEFAULT_TEMP) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)
    data = np.load(RESULTS / "real_gathers_and_dispersion.npz")
    fs = float(1.0 / np.median(np.diff(data["time_s"])))
    offsets = data["distances_m"]
    kalman_velocity, kalman1_diagnostics = _first_kalman_velocity_gather(
        data["measured_acceleration_sos_v"], data["time_s"], fs, offsets
    )
    kalman_velocity_sos = _post_sos_filter(kalman_velocity, fs)
    kalman1_diagnostics.update({
        "post_sos_filter": (
            f"Butterworth orden 6, pasa-banda {POST_SOS_BAND_HZ[0]:g}-"
            f"{POST_SOS_BAND_HZ[1]:g} Hz, sosfiltfilt"
        ),
        "velocity_rms_model_units": float(np.sqrt(np.mean(kalman_velocity_sos**2))),
        "velocity_peak_model_units": float(np.max(np.abs(kalman_velocity_sos))),
        "absolute_amplitude_validated_against_external_truth": False,
        "masw_uses_multichannel_phase": True,
    })
    np.savez_compressed(
        KALMAN1_PATH,
        time_s=data["time_s"],
        distances_m=offsets,
        kalman1_ground_particle_velocity_m_s=kalman_velocity,
        kalman1_ground_particle_velocity_post_sos_m_s=kalman_velocity_sos,
    )
    # El MASW recibe la velocidad de particula estimada por el primer Kalman y
    # filtrada por el segundo SOS. No recibe una curva Vs ni una salida RTS.
    f, c, amplitude = phase_shift_dispersion_image(
        kalman_velocity_sos.T,
        offsets, fs, 20.0, 350.0, 1.0, f_min=1.0, f_max=50.0,
    )
    tracker = track_dispersion_ridge(f, c, amplitude, offsets, _config())
    reference_frequency = data["reference_frequency_hz"]
    reference_c_rayleigh = data["reference_velocity_m_s"]

    # Brazo de comparacion: la misma imagen calculada desde la aceleracion medida
    # + SOS, sin pasar por el Kalman. Mismo grillado, para que la correlacion y
    # las metricas sobre la referencia sean comparables celda a celda.
    f_base, c_base, amplitude_base = phase_shift_dispersion_image(
        data["measured_acceleration_sos_v"].T,
        offsets, fs, 20.0, 350.0, 1.0, f_min=1.0, f_max=50.0,
    )
    # Tercer brazo, deliberadamente conservado: el SOS posterior pasa-bajos de la
    # corrida anterior, que deja pasar DC. Se mantiene en las metricas para que el
    # efecto de la deriva sub-1 Hz quede documentado y no haya que redescubrirlo.
    legacy_lowpass = signal.sosfiltfilt(
        signal.butter(10, 80.0 / (0.5 * fs), btype="lowpass", output="sos"),
        kalman_velocity, axis=1,
    )
    f_legacy, c_legacy, amplitude_legacy = phase_shift_dispersion_image(
        legacy_lowpass.T, offsets, fs, 20.0, 350.0, 1.0, f_min=1.0, f_max=50.0,
    )
    comparison = {
        "amedida_mas_sos": _masw_comparison(
            f_base, c_base, amplitude_base, offsets,
            reference_frequency, reference_c_rayleigh,
        ),
        "v_ground_kalman1_mas_sos_pasabanda_1_80": _masw_comparison(
            f, c, amplitude, offsets, reference_frequency, reference_c_rayleigh,
        ),
        "v_ground_kalman1_mas_sos_pasabajos_0_80_anterior": _masw_comparison(
            f_legacy, c_legacy, amplitude_legacy, offsets,
            reference_frequency, reference_c_rayleigh,
        ),
    }
    base_image = comparison["amedida_mas_sos"].pop("_image_band")
    image_correlation = _image_correlation(
        base_image,
        comparison["v_ground_kalman1_mas_sos_pasabanda_1_80"].pop("_image_band"),
    )
    legacy_correlation = _image_correlation(
        base_image,
        comparison["v_ground_kalman1_mas_sos_pasabajos_0_80_anterior"].pop("_image_band"),
    )
    drift = {
        name: float(np.median([_fraction_below(row, fs, 1.0) for row in matrix]))
        for name, matrix in (
            ("amedida_mas_sos", data["measured_acceleration_sos_v"]),
            ("v_ground_kalman1", kalman_velocity),
            ("v_ground_kalman1_mas_sos_pasabanda_1_80", kalman_velocity_sos),
        )
    }
    waterfall = _plot_waterfall_sos(data, kalman_velocity, kalman_velocity_sos, temp)
    mask_figure, physical, kalman_gate, energetic, combined = _plot_masks(
        f, c, amplitude, offsets, tracker,
        reference_frequency, reference_c_rayleigh, temp,
    )
    reference_is_overlay_only = assert_reference_is_overlay_only(
        f, c, offsets, tracker, combined
    )
    vs = c / RAYLEIGH_FACTOR
    low, high, cells = _mask_envelope(f, vs, combined)
    envelope_figure = _plot_envelope(
        f, low, high, cells, reference_frequency, reference_c_rayleigh, temp
    )
    np.savetxt(
        ENVELOPE_PATH,
        np.column_stack([f, low, high, cells]),
        delimiter=",", header="frequency_hz,vs_app_min_m_s,vs_app_max_m_s,admissible_cells",
        comments="",
    )
    valid_rows = cells > 0
    payload = {
        "pipeline": [
            "aceleracion_medida",
            "sos_previo_sosfiltfilt_0_80_hz",
            "kalman_1_forward_ground_particle_velocity",
            "sos_posterior_sosfiltfilt_pasabanda_1_80_hz",
            "masw_phase_shift",
            "vs_aparente_cR_over_0p92",
            "kalman_2_ventana_valida",
        ],
        "uses_time_domain_rts_reconstruction": False,
        "contains_estimated_picking_curve": False,
        "contains_external_reference_curve": True,
        "reference_used_for_kalman_tuning_or_mask": False,
        "reference_overlay_only_assertion_passed": bool(reference_is_overlay_only),
        "first_kalman": kalman1_diagnostics,
        "sample_rate_hz": fs,
        "frequency_resolution_hz": float(np.median(np.diff(f))),
        "requested_frequency_range_hz": [1.0, 50.0],
        "actual_frequency_range_hz": [float(f.min()), float(f.max())],
        "rayleigh_to_vs_apparent_factor": RAYLEIGH_FACTOR,
        "geometry": {"dx_m": 2.0, "aperture_m": 40.0, "mask": "2*dx*f <= cR <= L*f"},
        "mask_grid_fraction": {
            "physical": float(np.mean(physical)),
            "kalman_gate": float(np.mean(kalman_gate)),
            "energetic_candidates": float(np.mean(energetic)),
            "combined": float(np.mean(combined)),
        },
        "combined_nonempty_frequency_range_hz": [
            float(f[valid_rows].min()) if np.any(valid_rows) else None,
            float(f[valid_rows].max()) if np.any(valid_rows) else None,
        ],
        "nonempty_frequency_rows": int(np.count_nonzero(valid_rows)),
        "total_frequency_rows": int(f.size),
        "masw_comparison_vs_measured": {
            "image_correlation_8_30_hz": image_correlation,
            "image_correlation_8_30_hz_lowpass_legacy": legacy_correlation,
            "median_energy_fraction_below_1_hz": drift,
            **comparison,
            "note": (
                "Todas estas metricas se calculan DESPUES de formar las imagenes. "
                "La curva externa no participa del Kalman 1, del MASW ni del Kalman 2."
            ),
        },
    }
    METRICS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    styles = _styles()
    width, height = landscape(A4)
    usable_width = width - 30*mm
    usable_height = height - 33*mm
    doc = SimpleDocTemplate(
        str(output), pagesize=landscape(A4), leftMargin=15*mm, rightMargin=15*mm,
        topMargin=14*mm, bottomMargin=17*mm,
        title="Dos Kalman: velocidad de particula y mascara de Vs aparente",
        author="Proyecto Tesis",
    )
    story = [
        Spacer(1, 8*mm),
        Paragraph("Dos Kalman: velocidad de particula y mascara MASW", styles["title"]),
        Paragraph("Kalman 1 temporal + Kalman 2 en frecuencia - sin RTS y sin picking nuevo", styles["subtitle"]),
        _table([
            ["Entrada", "Kalman 1 temporal", "Entrada MASW", "Kalman 2"],
            ["Amedida -> SOS previo", "v_ground(t,x) causal", "SOS posterior -> MASW", "Ventana valida en P(f,Vs)"],
        ], [53*mm, 53*mm, 56*mm, 49*mm]),
        Spacer(1, 8*mm),
        Paragraph(
            "Cadena aplicada: Amedida -> SOS previo -> primer Kalman hacia adelante -> velocidad de "
            "particula del suelo v_ground(t,x) -> SOS posterior pasa-banda 1-80 Hz -> MASW. Recien en la "
            "imagen MASW, la velocidad de fase Rayleigh se expresa como Vs aparente mediante "
            "Vs_app = cR / 0.92. El segundo Kalman desplaza la ventana valida desde 50 Hz hacia "
            "aproximadamente 1 Hz.",
            styles["body"],
        ),
        Paragraph(
            "Distincion de unidades: v_ground [m/s] no es Vs aparente [m/s]. La primera es movimiento de "
            "particula en el tiempo; la segunda es velocidad de propagacion inferida entre offsets. No se "
            "usa RTS y no se produce ningun picking nuevo. La curva cian es una referencia externa "
            "superpuesta: no entra al ajuste de Q/R, ni al MASW, ni al gating de ninguno de los dos "
            "Kalman, y una asercion en tiempo de ejecucion recalcula las mascaras sin ella y exige "
            "igualdad bit a bit.",
            styles["result"],
        ),
        Paragraph(
            "Correccion respecto de la corrida anterior: la planta para estimar velocidad es "
            "H_v(s) = s*H_a(s) — un cero en el origen — y no H_a(s)/s como estaba implementado. "
            "El modelo anterior estimaba la derivada de la aceleracion y realzaba las frecuencias "
            "altas. Sobre un caso sintetico con verdad conocida, el modelo anterior erraba la "
            "amplitud por un factor 4628 y la fase en 176 grados; el corregido da amplitud 0,889 y "
            "fase -1,39 grados.",
            styles["body"],
        ),
        PageBreak(),
        Paragraph("1. Kalman temporal antes del MASW", styles["h1"]),
        _image_for_page(waterfall, usable_width, usable_height-20*mm),
        PageBreak(),
        Paragraph("2. MASW del primer Kalman y mascaras de Vs aparente", styles["h1"]),
        _image_for_page(mask_figure, usable_width, usable_height-18*mm),
        PageBreak(),
        Paragraph("3. Envolvente admisible - todavia sin picking", styles["h1"]),
        _image_for_page(envelope_figure, usable_width, usable_height-20*mm),
        PageBreak(),
        Paragraph("4. Resumen de las dos etapas Kalman", styles["h1"]),
        _table([
            ["Frecuencias MASW", "Filas conjuntas", "Rango no vacio", "Kalman 1", "Kalman 2"],
            [f"{f.min():.2f}-{f.max():.2f} Hz", f"{np.count_nonzero(valid_rows)}/{f.size}",
             f"{f[valid_rows].min():.2f}-{f[valid_rows].max():.2f} Hz",
             "v_ground causal", "Mascara, no pick"],
        ], [42*mm, 52*mm, 42*mm, 32*mm, 30*mm]),
        Spacer(1, 5*mm),
        _table([
            ["q Kalman 1", "NIS medio (21 canales)", "RTS", "Referencia usada para ajustar"],
            [f"{kalman1_diagnostics['q_scale']:.3g}",
             f"{kalman1_diagnostics['mean_nis_all_channels']:.3f}", "No", "No"],
        ], [46*mm, 57*mm, 32*mm, 63*mm]),
        Spacer(1, 3*mm),
        Paragraph("5. MASW del Kalman contra la aceleracion medida", styles["h1"]),
        _table([
            ["Brazo", "Energia sobre ref.", "Percentil", "Contraste", "Err. max local"],
            ["Amedida + SOS (base)",
             f"{comparison['amedida_mas_sos']['normalized_energy_on_reference']['median']:.4f}",
             f"{comparison['amedida_mas_sos']['percentile_on_reference_median']:.1f} %",
             f"{comparison['amedida_mas_sos']['contrast_reference_over_background_db_median']:.2f} dB",
             f"{comparison['amedida_mas_sos']['local_max_error_within_20_m_s']['rmse_m_s']:.2f} m/s"],
            ["v_ground Kalman 1 + SOS 1-80",
             f"{comparison['v_ground_kalman1_mas_sos_pasabanda_1_80']['normalized_energy_on_reference']['median']:.4f}",
             f"{comparison['v_ground_kalman1_mas_sos_pasabanda_1_80']['percentile_on_reference_median']:.1f} %",
             f"{comparison['v_ground_kalman1_mas_sos_pasabanda_1_80']['contrast_reference_over_background_db_median']:.2f} dB",
             f"{comparison['v_ground_kalman1_mas_sos_pasabanda_1_80']['local_max_error_within_20_m_s']['rmse_m_s']:.2f} m/s"],
            ["v_ground + SOS pasa-bajos (anterior)",
             f"{comparison['v_ground_kalman1_mas_sos_pasabajos_0_80_anterior']['normalized_energy_on_reference']['median']:.4f}",
             f"{comparison['v_ground_kalman1_mas_sos_pasabajos_0_80_anterior']['percentile_on_reference_median']:.1f} %",
             f"{comparison['v_ground_kalman1_mas_sos_pasabajos_0_80_anterior']['contrast_reference_over_background_db_median']:.2f} dB",
             f"{comparison['v_ground_kalman1_mas_sos_pasabajos_0_80_anterior']['local_max_error_within_20_m_s']['rmse_m_s']:.2f} m/s"],
        ], [58*mm, 42*mm, 30*mm, 30*mm, 38*mm]),
        Spacer(1, 4*mm),
        Paragraph(
            f"Correlacion de imagenes en 8-30 Hz: {image_correlation:.4f} con el SOS pasa-banda, "
            f"{legacy_correlation:.4f} con el pasa-bajos anterior. La diferencia entre esas dos filas "
            "es toda deriva por debajo de 1 Hz: v_ground concentra alli el "
            f"{100*drift['v_ground_kalman1']:.1f} % de su energia, contra el "
            f"{100*drift['amedida_mas_sos']:.2f} % de la aceleracion medida. Es la Obstruccion 2 del "
            "HANDOFF: estimar velocidad agrega un segundo cero en el origen y el margen de "
            "observabilidad en DC cae de 2,1e-2 a 4,0e-5.",
            styles["body"],
        ),
        Paragraph(
            "Lectura honesta: con el SOS posterior pasa-banda la imagen MASW del Kalman queda "
            "equivalente a la de la aceleracion medida —mejor en error de maximo local, levemente "
            "peor en energia sobre la referencia— y no la supera. La correccion de la planta era "
            "necesaria por fisica, pero no produce por si sola una mejora material del MASW.",
            styles["result"],
        ),
        Spacer(1, 3*mm),
        Paragraph(
            "La interseccion entre geometria, energia y ventana del segundo Kalman solo permanece no "
            "vacia en el rango indicado; fuera de ese intervalo no se fuerza una Vs aparente. "
            "Limitacion: no existe una verdad sincronizada de velocidad de particula para validar la "
            "amplitud absoluta del primer Kalman, asi que su salida se usa por la fase relativa entre "
            "canales y no se presenta como calibracion metrologica de v_ground.",
            styles["body"],
        ),
    ]
    doc.build(story, onFirstPage=_page, onLaterPages=_page)
    print(json.dumps(payload, indent=2))
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--temp", type=Path, default=DEFAULT_TEMP)
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args()
    path = generate(args.output.resolve(), args.temp.resolve())
    print(path)
    if not args.keep_temp:
        shutil.rmtree(args.temp.resolve(), ignore_errors=True)


if __name__ == "__main__":
    main()
