"""Mascaras MASW y picking Kalman descendente desde 50 Hz hasta ~1 Hz."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, PowerNorm
import numpy as np
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from geophone_scope.kalman_deconv.report_real_pdf import _image_for_page, _styles, _table
from geophone_scope.kalman_deconv.report_real_rts_picking import evaluate_curve
from geophone_scope.masw_dispersion import phase_shift_dispersion_image
from geophone_scope.masw_ridge_kalman import RidgeKalmanConfig, track_dispersion_ridge


REPO_ROOT = Path(__file__).resolve().parents[5]
RESULTS = Path(__file__).resolve().parent / "reports" / "real_canchita_group1"
DEFAULT_OUTPUT = REPO_ROOT / "output" / "pdf" / "MASW_MASCARAS_KALMAN_50_A_1HZ.pdf"
DEFAULT_TEMP = REPO_ROOT / "tmp" / "pdfs" / "masw_masks_50_to_1hz"
DEFAULT_METRICS = RESULTS / "masks_50_to_1hz_metrics.json"

ENERGY_CMAP = LinearSegmentedColormap.from_list(
    "energia_azul_rojo_oscuro",
    ["#06184a", "#174ea6", "#2b8cbe", "#7fcdbb", "#ffffbf", "#f03b20", "#67000d"],
)


def _tracker_config() -> RidgeKalmanConfig:
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
        clip_smoothed_to_physical_mask=False,
    )


def _physical_grid(frequency: np.ndarray, velocity: np.ndarray, offsets: np.ndarray) -> np.ndarray:
    dx = float(np.median(np.diff(np.sort(offsets))))
    aperture = float(np.max(offsets) - np.min(offsets))
    lower = 2.0 * dx * frequency[:, None]
    upper = aperture * frequency[:, None]
    return (velocity[None, :] >= lower) & (velocity[None, :] <= upper)


def _align_tracker_mask(
    full_frequency: np.ndarray,
    tracker_frequency: np.ndarray,
    tracker_mask: np.ndarray,
) -> np.ndarray:
    aligned = np.zeros((full_frequency.size, tracker_mask.shape[1]), dtype=bool)
    for frequency, row in zip(tracker_frequency, tracker_mask):
        index = int(np.argmin(np.abs(full_frequency - frequency)))
        aligned[index] = row
    return aligned


def _validity(tracker, offsets: np.ndarray) -> np.ndarray:
    dx = float(np.median(np.diff(np.sort(offsets))))
    aperture = float(np.max(offsets) - np.min(offsets))
    f = tracker.frequency_hz
    c = tracker.smoothed_velocity_m_s
    return (c >= 2.0 * dx * f) & (c <= aperture * f)


def _plot_masks(
    output: Path,
    arm_name: str,
    frequency: np.ndarray,
    velocity: np.ndarray,
    amplitude: np.ndarray,
    offsets: np.ndarray,
    tracker,
    reference_frequency: np.ndarray,
    reference_velocity: np.ndarray,
) -> Path:
    physical = _physical_grid(frequency, velocity, offsets)
    gate = _align_tracker_mask(frequency, tracker.frequency_hz, tracker.prediction_gate_mask)
    candidates = _align_tracker_mask(frequency, tracker.frequency_hz, tracker.candidate_mask)
    vmax = max(float(np.nanpercentile(amplitude, 99.5)), 0.3)
    norm = PowerNorm(gamma=0.72, vmin=0.0, vmax=vmax)
    fig, axes = plt.subplots(2, 2, figsize=(14.2, 9.3), sharex=True, sharey=True)
    panels = [
        (axes[0, 0], amplitude, "1. Energia MASW sin mascara"),
        (axes[0, 1], np.where(physical, amplitude, np.nan), "2. Mascara fisica del arreglo"),
        (axes[1, 0], np.where(gate, amplitude, np.nan), "3. Compuerta predictiva Kalman"),
        (axes[1, 1], amplitude, "4. Pick final 50 -> 1 Hz"),
    ]
    meshes = []
    for ax, values, title in panels:
        mesh = ax.pcolormesh(
            frequency, velocity, values.T, shading="auto", cmap=ENERGY_CMAP, norm=norm,
            rasterized=True,
        )
        meshes.append(mesh)
        ax.set_title(title, fontsize=11, weight="bold")
        ax.set_xlim(1.0, 50.0)
        ax.set_ylim(20.0, 350.0)
        ax.grid(False)

    dx = float(np.median(np.diff(np.sort(offsets))))
    aperture = float(np.max(offsets) - np.min(offsets))
    for ax in (axes[0, 1], axes[1, 1]):
        ax.plot(frequency, 2.0 * dx * frequency, color="white", lw=1.2, ls="--", label="c = 2 dx f")
        ax.plot(frequency, aperture * frequency, color="white", lw=1.2, ls=":", label="c = L f")

    candidate_rows, candidate_cols = np.where(candidates)
    if candidate_rows.size:
        step = max(1, candidate_rows.size // 900)
        axes[1, 0].scatter(
            frequency[candidate_rows[::step]], velocity[candidate_cols[::step]],
            s=3, color="white", alpha=0.45, linewidths=0,
            label="candidatos por amplitud",
        )

    order = np.argsort(tracker.frequency_hz)
    tf = tracker.frequency_hz[order]
    measured = tracker.measured_velocity_m_s[order]
    smoothed = tracker.smoothed_velocity_m_s[order]
    sigma = np.clip(tracker.smoothed_sigma_m_s[order], 0.0, 45.0)
    valid = _validity(tracker, offsets)[order]
    final_ax = axes[1, 1]
    final_ax.plot(tf, smoothed, color="#f7f7f7", lw=2.2, ls="--", label="Kalman+RTS fuera de mascara")
    final_ax.plot(tf, np.ma.masked_where(~valid, smoothed), color="#00ff66", lw=2.8,
                  label="pick fisicamente reportable")
    final_ax.scatter(tf[::4], measured[::4], s=9, facecolors="none", edgecolors="#ffe680",
                     linewidths=0.7, label="medicion de cresta")
    final_ax.fill_between(tf, smoothed - 1.96 * sigma, smoothed + 1.96 * sigma,
                          color="#f7f7f7", alpha=0.14, linewidth=0)
    ref = (reference_frequency >= 1.0) & (reference_frequency <= 50.0)
    final_ax.plot(reference_frequency[ref], reference_velocity[ref], color="#00e5ff", lw=2.0,
                  label="referencia hidro (solo evaluacion)")
    final_ax.annotate("inicio 50 Hz", xy=(tf[-1], smoothed[-1]), xytext=(42, 145),
                      color="white", arrowprops={"arrowstyle": "->", "color": "white"})
    final_ax.annotate(f"fin {tf[0]:.2f} Hz", xy=(tf[0], smoothed[0]), xytext=(4, 55),
                      color="white", arrowprops={"arrowstyle": "->", "color": "white"})

    for ax in axes[:, 0]:
        ax.set_ylabel("Velocidad de fase [m/s]")
    for ax in axes[1, :]:
        ax.set_xlabel("Frecuencia [Hz]")
    axes[0, 1].legend(fontsize=7, loc="upper left")
    axes[1, 0].legend(fontsize=7, loc="upper right")
    final_ax.legend(fontsize=6.4, loc="upper left", ncol=1)
    fig.subplots_adjust(left=0.065, right=0.885, bottom=0.075, top=0.91, wspace=0.08, hspace=0.14)
    color_axis = fig.add_axes([0.905, 0.16, 0.014, 0.67])
    colorbar = fig.colorbar(meshes[0], cax=color_axis)
    colorbar.set_label("Energia/coherencia phase-shift (azul: baja, rojo oscuro: alta)")
    fig.suptitle(
        f"MASW {arm_name}: mascaras y tracking Kalman descendente, 50 a {tf[0]:.2f} Hz",
        fontsize=15, weight="bold",
    )
    fig.text(
        0.5, 0.018,
        "La compuerta Kalman sigue la cresta en toda la banda. Verde = tramo dentro de la mascara fisica; blanco discontinuo = extrapolacion no reportable.",
        ha="center", fontsize=9,
    )
    safe = arm_name.lower().replace(" ", "_")
    path = output / f"masw_mascaras_{safe}_50_a_1hz.png"
    fig.savefig(path, dpi=190, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def _plot_comparison(output: Path, rows: dict, reference_f, reference_c, offsets) -> Path:
    fig, axes = plt.subplots(2, 1, figsize=(12.2, 8.3), sharex=True)
    for arm, payload, color in (
        ("SOS filtfilt", rows["SOS"], "#2166ac"),
        ("RTS", rows["RTS"], "#b2182b"),
    ):
        tracker = payload["tracker"]
        order = np.argsort(tracker.frequency_hz)
        f = tracker.frequency_hz[order]
        c = tracker.smoothed_velocity_m_s[order]
        valid = _validity(tracker, offsets)[order]
        axes[0].plot(f, c, color=color, lw=1.4, ls="--", alpha=0.75)
        axes[0].plot(f, np.ma.masked_where(~valid, c), color=color, lw=3.0,
                     label=f"{arm}: dentro de mascara")
        axes[1].plot(f, payload["peak"], color=color, lw=1.5, label=arm)
    axes[0].plot(reference_f, reference_c, color="black", lw=2.0, label="referencia hidro 8-29.83 Hz")
    axes[0].set_ylabel("Velocidad de fase [m/s]")
    axes[0].set_ylim(20, 350)
    axes[0].legend(ncol=3, fontsize=8)
    axes[0].set_title("Pick descendente: linea gruesa reportable, linea discontinua fuera de mascara")
    axes[1].set_ylabel("Energia del pico seleccionado")
    axes[1].set_xlabel("Frecuencia [Hz]")
    axes[1].set_ylim(0, 1)
    axes[1].legend(fontsize=8)
    axes[1].set_title("Calidad energetica del pick")
    for ax in axes:
        ax.set_xlim(1, 50)
        ax.grid(True, alpha=0.2)
    fig.tight_layout()
    path = output / "comparacion_picking_descendente_50_a_1hz.png"
    fig.savefig(path, dpi=190, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def _page(canvas, doc):
    canvas.saveState()
    width, _ = landscape(A4)
    canvas.setStrokeColor(colors.HexColor("#CBD5DC"))
    canvas.line(14 * mm, 11 * mm, width - 14 * mm, 11 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#607483"))
    canvas.drawString(14 * mm, 7 * mm, "MASW - mascaras Kalman - tracking 50 a 1 Hz")
    canvas.drawRightString(width - 14 * mm, 7 * mm, f"Pagina {doc.page}")
    canvas.restoreState()


def generate(output: Path = DEFAULT_OUTPUT, temp: Path = DEFAULT_TEMP) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)
    data = np.load(RESULTS / "real_gathers_and_dispersion.npz")
    fs = float(1.0 / np.median(np.diff(data["time_s"])))
    offsets = data["distances_m"]
    reference_f = data["reference_frequency_hz"]
    reference_c = data["reference_velocity_m_s"]
    cfg = _tracker_config()
    payloads = {}
    figures = {}
    for arm, key in (("SOS", "measured_acceleration_sos_v"), ("RTS", "rts_acceleration")):
        f, c, amplitude = phase_shift_dispersion_image(
            data[key].T, offsets, fs, 20.0, 350.0, 1.0, f_min=1.0, f_max=50.0
        )
        tracker = track_dispersion_ridge(f, c, amplitude, offsets, cfg)
        valid = _validity(tracker, offsets)
        metrics = evaluate_curve(
            tracker.frequency_hz, tracker.smoothed_velocity_m_s, reference_f, reference_c
        )
        metrics.update({
            "physically_valid_pick_fraction": float(np.mean(valid)),
            "actual_frequency_min_hz": float(np.min(tracker.frequency_hz)),
            "actual_frequency_max_hz": float(np.max(tracker.frequency_hz)),
            "valid_frequency_min_hz": float(np.min(tracker.frequency_hz[valid])) if np.any(valid) else None,
            "valid_frequency_max_hz": float(np.max(tracker.frequency_hz[valid])) if np.any(valid) else None,
        })
        payloads[arm] = {
            "tracker": tracker,
            "metrics": metrics,
            "peak": tracker.peak_amplitude[np.argsort(tracker.frequency_hz)],
        }
        figures[arm] = _plot_masks(
            temp, arm, f, c, amplitude, offsets, tracker, reference_f, reference_c
        )
        order = np.argsort(tracker.frequency_hz)
        np.savetxt(
            RESULTS / f"picks_{arm.lower()}_kalman_descendente_50_a_1hz.csv",
            np.column_stack([
                tracker.frequency_hz[order],
                tracker.smoothed_velocity_m_s[order],
                valid[order].astype(int),
                tracker.peak_amplitude[order],
            ]),
            delimiter=",", header="frequency_hz,velocity_m_s,inside_physical_mask,peak_energy",
            comments="",
        )
    comparison = _plot_comparison(temp, payloads, reference_f, reference_c, offsets)

    serializable = {
        "direction": "50 Hz hacia frecuencia decreciente",
        "tracker_config": cfg.__dict__,
        "sample_rate_hz": fs,
        "frequency_resolution_hz": float(1.0 / (data["time_s"][-1] - data["time_s"][0] + 1.0/fs)),
        "geometry": {
            "dx_m": float(np.median(np.diff(np.sort(offsets)))),
            "aperture_m": float(np.max(offsets) - np.min(offsets)),
            "physical_mask": "2*dx*f <= c <= aperture*f",
        },
        "SOS": payloads["SOS"]["metrics"],
        "RTS": payloads["RTS"]["metrics"],
        "reference_used_for_tracking": False,
    }
    DEFAULT_METRICS.write_text(json.dumps(serializable, indent=2), encoding="utf-8")

    styles = _styles()
    width, height = landscape(A4)
    usable_width = width - 30 * mm
    usable_height = height - 33 * mm
    doc = SimpleDocTemplate(
        str(output), pagesize=landscape(A4), leftMargin=15*mm, rightMargin=15*mm,
        topMargin=14*mm, bottomMargin=17*mm,
        title="MASW mascaras Kalman 50 a 1 Hz", author="Proyecto Tesis",
    )
    story = [
        Spacer(1, 8*mm),
        Paragraph("MASW con mascaras Kalman", styles["title"]),
        Paragraph(
            f"Tracking descendente desde 50 Hz hasta {serializable['SOS']['actual_frequency_min_hz']:.2f} Hz - Canchita grupo 1",
            styles["subtitle"],
        ),
        _table([
            ["Color de energia", "Direccion", "Mascara fisica", "Salida"],
            ["Azul baja - rojo oscuro alta", "50 Hz hacia abajo", "2 dx f <= c <= L f", "pick + sigma + validez"],
        ], [55*mm, 45*mm, 58*mm, 45*mm]),
        Spacer(1, 8*mm),
        Paragraph(
            "La imagen se separa en cuatro paneles: energia original, region resoluble por la geometria, "
            "compuerta predictiva de Kalman y pick final. El tracker recorre toda la banda solicitada, pero "
            "los tramos que violan la mascara del arreglo se dibujan discontinuos y no se consideran reportables.",
            styles["body"],
        ),
        Paragraph(
            "Esto es importante: con dx=2 m, las velocidades menores que 4 f sufren aliasing espacial; "
            "con apertura L=40 m, las velocidades mayores que 40 f exceden la longitud de onda resoluble. "
            "Por eso una curva continua de 1 a 50 Hz no puede declararse fisicamente valida en toda la banda.",
            styles["result"],
        ),
        PageBreak(),
    ]
    for index, arm in enumerate(("SOS", "RTS"), start=1):
        story.extend([
            Paragraph(f"{index}. Imagen {arm}: energia y mascaras", styles["h1"]),
            _image_for_page(figures[arm], usable_width, usable_height - 18*mm),
            PageBreak(),
        ])
    story.extend([
        Paragraph("3. Comparacion del tracking descendente", styles["h1"]),
        _image_for_page(comparison, usable_width, usable_height - 20*mm),
        PageBreak(),
        Paragraph("4. Metricas y limite fisico", styles["h1"]),
        _table([
            ["Brazo", "RMSE ref. [m/s]", "MAE [m/s]", "Pick dentro de mascara", "Rango fisico"],
            ["SOS", f"{payloads['SOS']['metrics']['rmse_m_s']:.2f}", f"{payloads['SOS']['metrics']['mae_m_s']:.2f}",
             f"{100*payloads['SOS']['metrics']['physically_valid_pick_fraction']:.1f}%",
             f"{payloads['SOS']['metrics']['valid_frequency_min_hz']:.2f}-{payloads['SOS']['metrics']['valid_frequency_max_hz']:.2f} Hz"],
            ["RTS", f"{payloads['RTS']['metrics']['rmse_m_s']:.2f}", f"{payloads['RTS']['metrics']['mae_m_s']:.2f}",
             f"{100*payloads['RTS']['metrics']['physically_valid_pick_fraction']:.1f}%",
             f"{payloads['RTS']['metrics']['valid_frequency_min_hz']:.2f}-{payloads['RTS']['metrics']['valid_frequency_max_hz']:.2f} Hz"],
        ], [42*mm, 39*mm, 33*mm, 50*mm, 44*mm]),
        Spacer(1, 8*mm),
        Paragraph(
            "Lectura correcta: el camino blanco muestra que Kalman puede mantener continuidad desde 50 Hz "
            "hasta aproximadamente 1 Hz. Los segmentos verdes son la parte compatible con la resolucion "
            "espacial del tendido. La referencia hidro solo existe entre 8.0 y 29.83 Hz y se usa despues "
            "del tracking para cuantificar error; no interviene en la seleccion de la cresta.",
            styles["body"],
        ),
        Paragraph(
            "La direccion descendente es una opcion explicita del estimador. No reemplaza la mascara fisica: "
            "iniciar en 50 Hz puede fijar una rama continua, pero no vuelve observables longitudes de onda "
            "que el arreglo de 2 m de paso y 40 m de apertura no puede resolver.",
            styles["result"],
        ),
    ])
    doc.build(story, onFirstPage=_page, onLaterPages=_page)
    print(json.dumps(serializable, indent=2))
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
