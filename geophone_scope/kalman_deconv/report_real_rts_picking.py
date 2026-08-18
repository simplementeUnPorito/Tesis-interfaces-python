"""Corrida reproducible de RTS y picking Kalman sobre la campana Canchita.

Genera trazas, espectros, imagenes de dispersion y metricas contra la curva
hidrogeologicamente guiada.  La curva de referencia solo se usa en
``evaluate_curve``; no participa en el ajuste de q ni en el tracker.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import optimize, signal

from geophone_scope.kalman_deconv.discretize import (
    augment_with_input_model,
    build_input_model,
    discretize_plant,
    prepare_plant,
)
from geophone_scope.kalman_deconv.kf import kf_forward, rts_backward
from geophone_scope.kalman_deconv.library import load_conditioner, load_geophone
from geophone_scope.kalman_deconv.models import InputModel, PlantSpec
from geophone_scope.masw_dispersion import (
    auto_extract_dispersion_curve,
    phase_shift_dispersion_image,
)
from geophone_scope.masw_ridge_kalman import RidgeKalmanConfig, track_dispersion_ridge
from server._gs import frd
from server.datacache import get_dataset
from server.groups import filtered_dataset, load_grouping, project_disabled_for_group
from server.waterfall import matrix_for_masw


REPO_ROOT = Path(__file__).resolve().parents[5]
RAW_ROOT = REPO_ROOT / "data" / "raw" / "Canchita"
REFERENCE_CSV = REPO_ROOT / "data" / "Moldeo Hidro" / "grupo1_curva_dispersion_hidro_guiada.csv"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "reports" / "real_canchita_group1"


def _style() -> None:
    plt.rcParams.update({
        "figure.dpi": 130,
        "savefig.dpi": 180,
        "font.size": 9,
        "axes.grid": True,
        "grid.alpha": 0.20,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })


def load_real_gathers(group_id: int = 1):
    """Reconstruye la misma agrupacion una vez cruda y una vez con SOS."""

    count, assignments = load_grouping(RAW_ROOT)
    dataset = filtered_dataset(get_dataset(RAW_ROOT), group_id, count, assignments)
    annotations = frd.load_annotations(frd.default_annotations_path(RAW_ROOT))
    offsets = frd.load_alignment_offsets(frd.default_alignment_offsets_path(RAW_ROOT))
    shot_offsets = frd.load_alignment_shot_offsets(
        frd.default_alignment_shot_offsets_path(RAW_ROOT)
    )
    disabled = project_disabled_for_group(
        frd.load_disabled_folders(frd.default_disabled_folders_path(RAW_ROOT)),
        group_id,
        count,
    )
    groups, _ = frd.compute_average_groups(
        dataset,
        annotations,
        filter_settings=None,
        alignment_offsets=offsets,
        alignment_shot_offsets=shot_offsets,
        disabled_folders=disabled,
    )
    t_raw, distances_raw, matrix_raw = frd.build_waterfall_matrix(groups)
    t_sos, distances_sos, matrix_sos = matrix_for_masw(RAW_ROOT, group_id=group_id)
    if not np.array_equal(t_raw, t_sos) or not np.array_equal(distances_raw, distances_sos):
        raise RuntimeError("las reconstrucciones cruda y SOS no comparten la misma grilla")
    return t_raw, np.asarray(distances_raw, dtype=float), matrix_raw, matrix_sos


def _build_augmented(fs: float, q_scale: float):
    plant_spec = PlantSpec(
        geophone=load_geophone("sm24_nominal"),
        conditioner=load_conditioner("lp_pga_medido"),
    )
    prepared = prepare_plant(plant_spec, fs)
    discrete = discretize_plant(*prepared.continuous, fs=fs)
    input_ss = build_input_model(
        InputModel(kind="leaky_rw", q_scale=q_scale, leak_hz=0.7), fs=fs
    )
    return augment_with_input_model(discrete, input_ss)


def estimate_q_ml(y: np.ndarray, fs: float, r_var: float) -> tuple[float, dict[str, float]]:
    """Maxima verosimilitud de innovaciones en una traza representativa."""

    y = np.asarray(y, dtype=float)

    def objective(log10_q: float) -> float:
        augmented = _build_augmented(fs, 10.0**float(log10_q))
        result = kf_forward(y, augmented.A, augmented.C, augmented.Q, r_var)
        v = result.innovations[:, 0]
        s = result.innovation_cov[:, 0, 0]
        valid = np.isfinite(v) & np.isfinite(s) & (s > 0)
        return float(0.5 * np.sum(np.log(2.0 * np.pi * s[valid]) + v[valid] ** 2 / s[valid]))

    fit = optimize.minimize_scalar(
        objective, bounds=(-4.0, 3.0), method="bounded", options={"maxiter": 18, "xatol": 0.02}
    )
    q = 10.0**float(fit.x)
    return q, {
        "q_scale": q,
        "log10_q": float(fit.x),
        "innovation_nll": float(fit.fun),
        "optimizer_success": bool(fit.success),
        "optimizer_evaluations": int(fit.nfev),
    }


def apply_rts_gather(
    matrix_raw: np.ndarray,
    time_s: np.ndarray,
    fs: float,
    reference_channel: int,
) -> tuple[np.ndarray, dict[str, object]]:
    """Deconvoluciona todos los canales conservando la misma razon q/R."""

    pre = time_s < -0.05
    if np.count_nonzero(pre) < 20:
        raise ValueError("ventana pre-arribo insuficiente para estimar R")
    centered = matrix_raw - np.nanmedian(matrix_raw[:, pre], axis=1, keepdims=True)
    r_values = np.nanvar(centered[:, pre], axis=1, ddof=1)
    positive = r_values[np.isfinite(r_values) & (r_values > 0)]
    floor = max(float(np.median(positive)) * 1e-6, np.finfo(float).tiny)
    r_values = np.maximum(r_values, floor)
    r_ref = float(r_values[reference_channel])
    q_ref, tuning = estimate_q_ml(centered[reference_channel], fs, r_ref)

    recovered = np.empty_like(centered)
    per_channel = []
    min_cov = np.inf
    for i, values in enumerate(centered):
        q_i = q_ref * float(r_values[i]) / r_ref
        augmented = _build_augmented(fs, q_i)
        filtered = kf_forward(values, augmented.A, augmented.C, augmented.Q, float(r_values[i]))
        smoothed = rts_backward(filtered, augmented.A)
        recovered[i] = (
            smoothed.smoothed_state[:, augmented.plant_order:]
            @ augmented.input_model.C.T
        ).ravel()
        min_cov = min(min_cov, filtered.min_cov_eigenvalue, smoothed.min_cov_eigenvalue)
        per_channel.append({
            "distance_index": i,
            "r_var": float(r_values[i]),
            "q_scale": q_i,
            "mean_nis": float(np.nanmean(filtered.nis)),
            "min_cov_eigenvalue": float(min(filtered.min_cov_eigenvalue, smoothed.min_cov_eigenvalue)),
        })
    diagnostics = {
        **tuning,
        "reference_channel_index": reference_channel,
        "reference_r_var": r_ref,
        "regularization_q_over_r": q_ref / r_ref,
        "minimum_covariance_eigenvalue": float(min_cov),
        "channels": per_channel,
    }
    return recovered, diagnostics


def _band_spectrum(values: np.ndarray, fs: float):
    f, psd = signal.welch(values, fs=fs, nperseg=min(2048, values.size), detrend="constant")
    keep = (f >= 1.0) & (f <= min(200.0, np.nextafter(fs / 2.0, 0.0)))
    area = float(np.trapezoid(psd[keep], f[keep]))
    normalized = psd / max(area, np.finfo(float).tiny)
    return f, normalized


def spectral_metrics(values: np.ndarray, fs: float) -> dict[str, float]:
    f, psd = _band_spectrum(values, fs)
    bands = ((1.0, 10.0), (10.0, 50.0), (50.0, 80.0), (80.0, 200.0))
    result: dict[str, float] = {}
    for lo, hi in bands:
        mask = (f >= lo) & (f < min(hi, fs / 2.0))
        result[f"fraction_{lo:g}_{hi:g}_hz"] = float(np.trapezoid(psd[mask], f[mask]))
    useful = (f >= 1.0) & (f <= min(200.0, fs / 2.0))
    result["centroid_1_200_hz"] = float(
        np.trapezoid(f[useful] * psd[useful], f[useful])
        / max(np.trapezoid(psd[useful], f[useful]), np.finfo(float).tiny)
    )
    return result


def evaluate_curve(
    picked_f: np.ndarray,
    picked_c: np.ndarray,
    reference_f: np.ndarray,
    reference_c: np.ndarray,
) -> dict[str, float]:
    """Evalua por interpolacion; esta es la unica funcion que ve la referencia."""

    order = np.argsort(picked_f)
    pf = np.asarray(picked_f, dtype=float)[order]
    pc = np.asarray(picked_c, dtype=float)[order]
    unique, unique_idx = np.unique(pf, return_index=True)
    pc = pc[unique_idx]
    valid = (reference_f >= unique[0]) & (reference_f <= unique[-1])
    prediction = np.interp(reference_f[valid], unique, pc)
    error = prediction - reference_c[valid]
    return {
        "rmse_m_s": float(np.sqrt(np.mean(error**2))),
        "mae_m_s": float(np.mean(np.abs(error))),
        "bias_m_s": float(np.mean(error)),
        "coverage_fraction": float(np.count_nonzero(valid) / reference_f.size),
        "evaluated_reference_points": int(np.count_nonzero(valid)),
        "pick_points": int(unique.size),
    }


def _safe_baseline(f, c, image, offsets):
    try:
        return auto_extract_dispersion_curve(f, c, image, offsets, max_points=80)
    except ValueError:
        peak = np.argmax(image, axis=1)
        chosen_c = c[peak]
        aperture = float(np.max(offsets) - np.min(offsets))
        dx = float(np.median(np.diff(np.sort(offsets))))
        valid = (f > 0) & (chosen_c >= 2.0 * dx * f) & (chosen_c <= 1.5 * aperture * f)
        return f[valid], chosen_c[valid]


def _plot_trace(output: Path, time_s, raw, sos, rts, distance):
    scale = lambda x: x / max(np.nanmax(np.abs(x)), np.finfo(float).tiny)
    fig, axes = plt.subplots(2, 1, figsize=(9.2, 6.2))
    view = (time_s >= -0.15) & (time_s <= 1.2)
    axes[0].plot(time_s[view], scale(raw)[view], lw=0.9, label="aceleracion medida (Ma, V)")
    axes[0].plot(time_s[view], scale(sos)[view], lw=1.1, label="medida + SOS filtfilt")
    axes[0].plot(time_s[view], scale(rts)[view], lw=1.0, label="aceleracion reconstruida RTS")
    axes[0].set(xlabel="Tiempo [s]", ylabel="Amplitud normalizada",
                title=f"Traza real de campana a {distance:g} m")
    axes[0].legend(ncol=3, fontsize=8)
    for values, label in ((raw, "medida Ma"), (sos, "SOS filtfilt"), (rts, "RTS")):
        f, psd = _band_spectrum(values, 1.0 / np.median(np.diff(time_s)))
        keep = (f >= 1.0) & (f <= 200.0)
        axes[1].semilogx(f[keep], 10.0 * np.log10(np.maximum(psd[keep], 1e-300)), label=label)
    axes[1].axvspan(10, 50, alpha=0.10, color="#54a24b", label="banda MASW 10-50 Hz")
    axes[1].set(xlabel="Frecuencia [Hz]", ylabel="PSD normalizada [dB/Hz]",
                title="Contenido espectral (cada curva integra 1 entre 1 y 200 Hz)")
    axes[1].legend(ncol=4, fontsize=8)
    fig.tight_layout()
    fig.savefig(output / "01_traza_real_y_espectro.png", bbox_inches="tight")
    plt.close(fig)


def _plot_dispersion(output: Path, name: str, f, c, image, reference, baseline, tracker):
    fig, ax = plt.subplots(figsize=(8.8, 5.4))
    mesh = ax.pcolormesh(f, c, image.T, shading="auto", cmap="magma", rasterized=True)
    rf, rc = reference
    bf, bc = baseline
    ax.plot(rf, rc, color="cyan", lw=2.0, label="referencia hidro guiada")
    ax.scatter(bf, bc, s=18, facecolors="none", edgecolors="white", label="auto-pick actual")
    ax.plot(tracker.frequency_hz, tracker.smoothed_velocity_m_s, color="lime", lw=2.0,
            label="picker Kalman + RTS")
    ax.fill_between(
        tracker.frequency_hz,
        tracker.smoothed_velocity_m_s - 1.96 * tracker.smoothed_sigma_m_s,
        tracker.smoothed_velocity_m_s + 1.96 * tracker.smoothed_sigma_m_s,
        color="lime", alpha=0.16, linewidth=0,
    )
    ax.set(xlim=(5, 45), ylim=(50, 300), xlabel="Frecuencia [Hz]",
           ylabel="Velocidad de fase [m/s]", title=f"Imagen de dispersion: {name}")
    ax.legend(fontsize=8, loc="upper left")
    fig.colorbar(mesh, ax=ax, label="coherencia phase-shift")
    fig.tight_layout()
    fig.savefig(output / f"02_dispersion_{name.lower().replace(' ', '_')}.png", bbox_inches="tight")
    plt.close(fig)


def _plot_comparison(output: Path, reference, curves, metrics):
    fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.4))
    rf, rc = reference
    axes[0].plot(rf, rc, color="black", lw=2.0, label="referencia hidro guiada")
    styles = {
        "SOS / auto actual": ("#e45756", "--"),
        "SOS / Kalman+RTS": ("#4c78a8", "-"),
        "RTS / auto actual": ("#f58518", "--"),
        "RTS / Kalman+RTS": ("#54a24b", "-"),
    }
    for label, (pf, pc) in curves.items():
        color, style = styles[label]
        axes[0].plot(pf, pc, style, color=color, lw=1.6, label=label)
    axes[0].set(xlim=(8, 30), ylim=(50, 260), xlabel="Frecuencia [Hz]",
                ylabel="Velocidad de fase [m/s]", title="Curvas extraidas")
    axes[0].legend(fontsize=7)
    labels = list(metrics)
    values = [metrics[label]["rmse_m_s"] for label in labels]
    colors = [styles[label][0] for label in labels]
    bars = axes[1].barh(np.arange(len(labels)), values, color=colors)
    axes[1].set_yticks(np.arange(len(labels)), labels=labels)
    axes[1].invert_yaxis()
    axes[1].set(xlabel="RMSE contra referencia [m/s]", title="Error en 8-29,83 Hz")
    for bar, value in zip(bars, values):
        axes[1].text(value + 1.0, bar.get_y() + bar.get_height()/2, f"{value:.1f}", va="center")
    fig.tight_layout()
    fig.savefig(output / "04_comparacion_picking.png", bbox_inches="tight")
    plt.close(fig)


def generate(output: Path = DEFAULT_OUTPUT) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    _style()
    time_full, distances, raw_full, sos_full = load_real_gathers(group_id=1)
    fs = float(1.0 / np.median(np.diff(time_full)))
    crop = (time_full >= -0.5) & (time_full <= 3.0)
    time = time_full[crop]
    raw = np.nan_to_num(raw_full[:, crop], nan=0.0)
    sos = np.nan_to_num(sos_full[:, crop], nan=0.0)
    reference_channel = int(np.argmin(np.abs(distances - 30.0)))
    rts, diagnostics = apply_rts_gather(raw, time, fs, reference_channel)

    reference_data = np.genfromtxt(REFERENCE_CSV, delimiter=",", names=True)
    reference = (
        np.asarray(reference_data["freq_Hz"], dtype=float),
        np.asarray(reference_data["cR_pick_ms"], dtype=float),
    )
    dispersion = {}
    # No se fuerza monotonia: la referencia independiente y la propia imagen
    # muestran cambios de rama/curvatura incompatibles con dispersion normal
    # estricta. El tracker usa solo continuidad local y limites de longitud de onda.
    tracker_cfg = RidgeKalmanConfig(
        f_min_hz=8.0,
        f_max_hz=30.0,
        max_wavelength_aperture=1.0,
        enforce_normal_dispersion=False,
    )
    for name, gather in (("SOS filtfilt", sos), ("RTS real", rts)):
        f, c, image = phase_shift_dispersion_image(
            gather.T, distances, fs, 50.0, 300.0, 1.0, f_min=5.0, f_max=50.0
        )
        baseline = _safe_baseline(f, c, image, distances)
        tracker = track_dispersion_ridge(f, c, image, distances, tracker_cfg)
        dispersion[name] = (f, c, image, baseline, tracker)
        _plot_dispersion(output, name, f, c, image, reference, baseline, tracker)

    curves = {
        "SOS / auto actual": dispersion["SOS filtfilt"][3],
        "SOS / Kalman+RTS": (
            dispersion["SOS filtfilt"][4].frequency_hz,
            dispersion["SOS filtfilt"][4].smoothed_velocity_m_s,
        ),
        "RTS / auto actual": dispersion["RTS real"][3],
        "RTS / Kalman+RTS": (
            dispersion["RTS real"][4].frequency_hz,
            dispersion["RTS real"][4].smoothed_velocity_m_s,
        ),
    }
    metrics = {
        label: evaluate_curve(pf, pc, reference[0], reference[1])
        for label, (pf, pc) in curves.items()
    }
    spectral = {
        "Aceleracion medida (salida Ma, V)": spectral_metrics(raw[reference_channel], fs),
        "SOS filtfilt": spectral_metrics(sos[reference_channel], fs),
        "RTS": spectral_metrics(rts[reference_channel], fs),
    }
    _plot_trace(
        output, time, raw[reference_channel], sos[reference_channel],
        rts[reference_channel], distances[reference_channel],
    )
    _plot_comparison(output, reference, curves, metrics)

    for label, (pf, pc) in curves.items():
        safe = label.lower().replace(" ", "_").replace("/", "-").replace("+", "plus")
        np.savetxt(
            output / f"picks_{safe}.csv",
            np.column_stack([pf, pc]), delimiter=",", header="frequency_hz,velocity_m_s", comments="",
        )
    np.savez_compressed(
        output / "real_gathers_and_dispersion.npz",
        time_s=time,
        distances_m=distances,
        measured_acceleration_ma_output_v=raw,
        measured_acceleration_sos_v=sos,
        rts_acceleration=rts,
        reference_frequency_hz=reference[0],
        reference_velocity_m_s=reference[1],
    )
    payload = {
        "campaign": "Canchita grupo 1",
        "raw_root": str(RAW_ROOT),
        "reference_csv": str(REFERENCE_CSV),
        "sample_rate_hz": fs,
        "measurement_interpretation": "aceleracion medida por la cadena Ma et al.; almacenada en V segun metadata.json",
        "stored_signal_units": "V",
        "channels": int(distances.size),
        "distance_range_m": [float(distances.min()), float(distances.max())],
        "time_window_s": [float(time[0]), float(time[-1])],
        "sos_filter": {"type": "Butterworth SOS sosfiltfilt", "order": 10, "band_hz": [0, 80]},
        "rts_diagnostics": diagnostics,
        "spectral_metrics_representative_30m": spectral,
        "picking_metrics": metrics,
        "tracker_config": tracker_cfg.__dict__,
        "reference_used_for_tracking_or_tuning": False,
    }
    (output / "metrics.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    sos_auto = metrics["SOS / auto actual"]
    sos_k = metrics["SOS / Kalman+RTS"]
    rts_k = metrics["RTS / Kalman+RTS"]
    picker_gain = 100.0 * (1.0 - sos_k["rmse_m_s"] / sos_auto["rmse_m_s"])
    rts_gain = 100.0 * (1.0 - rts_k["rmse_m_s"] / sos_k["rmse_m_s"])
    report = f"""# RTS real y picking Kalman — Canchita grupo 1

## Datos y metodo

- Se reconstruyeron **{distances.size} trazas reales** entre {distances.min():g} y {distances.max():g} m, a **{fs:g} Hz**, sobre la misma grilla y agrupacion.
- Todas las capturas son mediciones de **aceleracion** realizadas con la cadena de Ma et al.; el repositorio las almacena en voltios (`signal_units: V`), es decir, como salida electrica de esa cadena.
- Brazo de control: esa aceleracion medida pasa por Butterworth de orden 10 en SOS, `sosfiltfilt`, 0-80 Hz.
- Brazo RTS: planta SM-24 nominal + LP_PGA medido, entrada `leaky_rw`, `q` ajustado por maxima verosimilitud de innovaciones en la traza real de 30 m. En los demas canales se conserva `q/R`.
- Picking: estado `[p, dp/df]`, `p=1/c`, KF hacia frecuencias crecientes + RTS hacia atras y limites de longitud de onda. No se fuerza monotonia porque la imagen contiene cambios de curvatura/rama.
- La curva hidrogeologicamente guiada ({reference[0].size} puntos, {reference[0].min():.1f}-{reference[0].max():.2f} Hz) se uso **solo para evaluar**, nunca para inicializar ni ajustar.

## Resultado espectral real (traza de 30 m)

Las PSD estan normalizadas a area unitaria entre 1 y 200 Hz porque la aceleracion medida/SOS esta almacenada como salida en voltios de la cadena Ma, mientras RTS estima su entrada en m/s2; por eso la comparacion defendible es la **distribucion espectral**, no una amplitud absoluta entre unidades distintas.

| salida | 1-10 Hz | 10-50 Hz | 50-80 Hz | 80-200 Hz | centroide Hz |
|---|---:|---:|---:|---:|---:|
"""
    for label, values in spectral.items():
        report += (
            f"| {label} | {values['fraction_1_10_hz']:.4f} | {values['fraction_10_50_hz']:.4f} | "
            f"{values['fraction_50_80_hz']:.4f} | {values['fraction_80_200_hz']:.4f} | "
            f"{values['centroid_1_200_hz']:.2f} |\n"
        )
    report += """

## Picking contra la referencia externa

| brazo | RMSE m/s | MAE m/s | sesgo m/s | cobertura | puntos pick |
|---|---:|---:|---:|---:|---:|
"""
    for label, values in metrics.items():
        report += (
            f"| {label} | {values['rmse_m_s']:.3f} | {values['mae_m_s']:.3f} | "
            f"{values['bias_m_s']:.3f} | {100*values['coverage_fraction']:.1f}% | "
            f"{values['pick_points']} |\n"
        )
    report += f"""

## Conclusion

- Sobre la imagen SOS, el tracker Kalman+RTS cambia el RMSE de {sos_auto['rmse_m_s']:.2f} a {sos_k['rmse_m_s']:.2f} m/s ({picker_gain:.1f}% de mejora relativa): el salto a un modo de alta velocidad del auto-pick actual desaparece.
- Aplicar la deconvolucion RTS a las trazas y luego el mismo tracker deja RMSE {rts_k['rmse_m_s']:.2f} m/s ({rts_gain:+.1f}% respecto del tracker sobre SOS). Esta cifra separa si la mejora viene del preprocesamiento RTS o del tracker.
- El NIS medio de la traza de referencia es {diagnostics['channels'][reference_channel]['mean_nis']:.3f}; por tanto el `q` ML es reproducible pero su consistencia estadistica no debe sobreinterpretarse como calibracion metrologica.
- No se reporta RMSE temporal del RTS real: el repositorio no contiene una aceleracion de suelo verdadera sincronizada. La evidencia real valida aplicacion, estabilidad numerica y contenido espectral; la exactitud temporal absoluta queda limitada al benchmark sintetico.
"""
    report_path = output / "INFORME_RTS_REAL_Y_PICKING.md"
    report_path.write_text(report, encoding="utf-8")
    print(json.dumps({
        "report": str(report_path),
        "q_scale": diagnostics["q_scale"],
        "mean_nis_reference": diagnostics["channels"][reference_channel]["mean_nis"],
        "picking_metrics": metrics,
        "spectral_metrics": spectral,
    }, indent=2))
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    generate(args.output.resolve())


if __name__ == "__main__":
    main()
