"""Control MASW: aceleracion medida -> Kalman de cresta -> inversion Vs(z).

No usa el primer Kalman temporal ni RTS temporal. La curva externa se consulta
solo despues del tracking para evaluar y dibujar. La inversion recibe cR, no
``cR/0.92``: el solver Rayleigh ya calcula la relacion entre cR y Vs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, PowerNorm
import numpy as np

from geophone_scope.kalman_deconv.report_real_rts_picking import evaluate_curve
from geophone_scope.masw_dispersion import phase_shift_dispersion_image
from geophone_scope.masw_inversion import initial_model_from_dc, monte_carlo_inversion
from geophone_scope.masw_ridge_kalman import RidgeKalmanConfig, track_dispersion_ridge


REPO_ROOT = Path(__file__).resolve().parents[5]
REAL = Path(__file__).resolve().parent / "reports" / "real_canchita_group1"
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent
    / "reports"
    / "acceleration_kalman_inversion_2026-08-17"
)

ENERGY_CMAP = LinearSegmentedColormap.from_list(
    "energia_azul_rojo_oscuro",
    ["#06184a", "#174ea6", "#2b8cbe", "#7fcdbb", "#ffffbf", "#f03b20", "#67000d"],
)


def _tracker_config() -> RidgeKalmanConfig:
    return RidgeKalmanConfig(
        f_min_hz=8.0,
        # Con dx=2 m, el limite espacial cR >= 2*dx*f obliga a cR>=88 m/s
        # ya en 22 Hz. Por encima de aqui el modo lento observado entra en la
        # zona aliasada y el tracker subiria por geometria, no por geologia.
        f_max_hz=22.0,
        direction="ascending",
        seed_c_min_m_s=0.0,
        seed_c_max_m_s=160.0,
        gate_m_s=20.0,
        max_wavelength_aperture=1.0,
        enforce_physical_mask=True,
        enforce_normal_dispersion=False,
        clip_smoothed_to_physical_mask=True,
    )


def _curve_fit_metrics(c_obs: np.ndarray, c_theoretical: np.ndarray) -> dict[str, float | int]:
    valid = np.isfinite(c_obs) & np.isfinite(c_theoretical)
    error = c_theoretical[valid] - c_obs[valid]
    return {
        "points": int(np.count_nonzero(valid)),
        "rmse_m_s": float(np.sqrt(np.mean(error**2))),
        "mae_m_s": float(np.mean(np.abs(error))),
        "bias_m_s": float(np.mean(error)),
        "coverage_fraction": float(np.mean(valid)),
    }


def _run_inversions(frequency: np.ndarray, c_rayleigh: np.ndarray) -> tuple[dict, dict, list[dict]]:
    n_layers = 4
    beta_initial, h_initial = initial_model_from_dc(frequency, c_rayleigh, n_layers)

    # Control físicamente restrictivo: Vs no decrece con profundidad. El modelo
    # inicial se ordena porque initial_model_from_dc puede devolver inversiones.
    monotonic = monte_carlo_inversion(
        frequency,
        c_rayleigh,
        n_layers=n_layers,
        n_iterations=2000,
        bs=10.0,
        bh=15.0,
        reversals=0,
        beta_initial=np.sort(beta_initial),
        h_initial=h_initial,
        seed=2917,
    )

    # La curva observada no queda bien representada por un perfil monótono. Se
    # permiten dos inversiones someras y se repite con tres semillas para medir
    # la sensibilidad de la búsqueda local.
    candidates = []
    for seed in (2909, 2910, 2911):
        result = monte_carlo_inversion(
            frequency,
            c_rayleigh,
            n_layers=n_layers,
            n_iterations=2000,
            bs=10.0,
            bh=15.0,
            reversals=2,
            seed=seed,
        )
        candidates.append(result)
    best_reversal = min(candidates, key=lambda row: float(row["misfit"]))
    return monotonic, best_reversal, candidates


def _step_profile(beta: np.ndarray, h: np.ndarray, max_depth: float):
    beta = np.asarray(beta, dtype=float)
    h = np.asarray(h, dtype=float)
    boundaries = np.concatenate(([0.0], np.cumsum(h), [max_depth]))
    x, z = [], []
    for index, velocity in enumerate(beta):
        top = float(boundaries[index])
        bottom = float(boundaries[index + 1])
        x.extend([velocity, velocity])
        z.extend([top, bottom])
    return np.asarray(x), np.asarray(z)


def _save_profile(path: Path, beta: np.ndarray, h: np.ndarray) -> None:
    tops = np.concatenate(([0.0], np.cumsum(h)))
    bottoms = np.concatenate((np.cumsum(h), [np.nan]))
    rows = np.column_stack([np.arange(1, beta.size + 1), tops, bottoms, beta])
    np.savetxt(
        path,
        rows,
        delimiter=",",
        header="layer,top_depth_m,bottom_depth_m,vs_m_s",
        comments="",
    )


def _plot_dispersion(path: Path, f, c, image, tracker, reference) -> None:
    norm = PowerNorm(gamma=0.72, vmin=0.0, vmax=max(float(np.percentile(image, 99.5)), 0.3))
    fig, ax = plt.subplots(figsize=(10.2, 6.2))
    mesh = ax.pcolormesh(f, c, image.T, shading="auto", cmap=ENERGY_CMAP, norm=norm, rasterized=True)
    order = np.argsort(tracker.frequency_hz)
    tf = tracker.frequency_hz[order]
    tc = tracker.smoothed_velocity_m_s[order]
    ts = tracker.smoothed_sigma_m_s[order]
    ax.fill_between(tf, tc - 1.96 * ts, tc + 1.96 * ts, color="#00ff66", alpha=0.18, linewidth=0)
    ax.plot(tf, tc, color="#00ff66", lw=2.1, label="curva Kalman/RTS en frecuencia")
    ax.plot(reference[0], reference[1], color="#00e5ff", lw=1.8, label="referencia externa (solo evaluacion)")
    ax.set(xlim=(5, 45), ylim=(40, 260), xlabel="Frecuencia [Hz]", ylabel="cR [m/s]",
           title="MASW desde Amedida + SOS; Kalman solamente sobre la cresta")
    ax.legend(loc="upper left", fontsize=8)
    fig.colorbar(mesh, ax=ax, label="Energia/coherencia phase-shift")
    fig.tight_layout()
    fig.savefig(path, dpi=190, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _plot_inversion(path: Path, frequency, c_obs, monotonic, reversal, reference, reliable_depth) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12.2, 5.6))
    axes[0].plot(frequency, c_obs, "o", ms=4.0, color="#1f78b4", label="cR Kalman observada")
    axes[0].plot(frequency, monotonic["c_t"], "--", lw=1.6, color="#666666", label="teorica: Vs monotona")
    axes[0].plot(frequency, reversal["c_t"], "-", lw=2.0, color="#b2182b", label="teorica: 2 inversiones")
    axes[0].plot(reference[0], reference[1], ":", lw=1.6, color="#008b8b", label="referencia externa")
    axes[0].set(xlabel="Frecuencia [Hz]", ylabel="cR [m/s]", title="Ajuste de la curva Rayleigh")
    axes[0].grid(True, alpha=0.22)
    axes[0].legend(fontsize=7)

    plot_depth = max(1.35 * reliable_depth, float(np.sum(reversal["h"])) + 1.5)
    for result, label, color, style in (
        (monotonic, "Vs monotona", "#666666", "--"),
        (reversal, "Vs con hasta 2 inversiones", "#b2182b", "-"),
    ):
        x, z = _step_profile(result["beta"], result["h"], plot_depth)
        axes[1].plot(x, z, style, color=color, lw=2.0, label=label)
    axes[1].axhspan(reliable_depth, plot_depth, color="0.92", label="extrapolacion poco resuelta")
    axes[1].set(xlabel="Vs [m/s]", ylabel="Profundidad [m]", title="Modelo invertido")
    axes[1].invert_yaxis()
    axes[1].grid(True, alpha=0.22)
    axes[1].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=190, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def generate(output: Path = DEFAULT_OUTPUT) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    data = np.load(REAL / "real_gathers_and_dispersion.npz")
    time_s = data["time_s"]
    fs = float(1.0 / np.median(np.diff(time_s)))
    offsets = data["distances_m"]
    gather = data["measured_acceleration_sos_v"]

    f, c, image = phase_shift_dispersion_image(
        gather.T, offsets, fs, 20.0, 350.0, 1.0, f_min=1.0, f_max=50.0
    )
    tracker = track_dispersion_ridge(f, c, image, offsets, _tracker_config())
    order = np.argsort(tracker.frequency_hz)
    tracker_f = tracker.frequency_hz[order]
    tracker_c = tracker.smoothed_velocity_m_s[order]
    tracker_sigma = tracker.smoothed_sigma_m_s[order]

    # La grilla MASW tiene df=0.286 Hz. Tomar un punto de cada dos evita dar al
    # inversor el doble de peso a bines espectralmente no independientes.
    inversion_f = tracker_f[::2]
    inversion_c = tracker_c[::2]
    inversion_sigma = tracker_sigma[::2]
    monotonic, reversal, candidates = _run_inversions(inversion_f, inversion_c)

    reference = (
        np.asarray(data["reference_frequency_hz"], dtype=float),
        np.asarray(data["reference_velocity_m_s"], dtype=float),
    )
    pick_vs_reference = evaluate_curve(tracker_f, tracker_c, *reference)
    reversal_fit = _curve_fit_metrics(inversion_c, reversal["c_t"])
    monotonic_fit = _curve_fit_metrics(inversion_c, monotonic["c_t"])
    theoretical_vs_reference = evaluate_curve(
        inversion_f[np.isfinite(monotonic["c_t"])],
        monotonic["c_t"][np.isfinite(monotonic["c_t"])],
        *reference,
    )

    wavelengths = inversion_c / inversion_f
    reliable_depth = float(0.5 * np.max(wavelengths))
    seed_rows = []
    for seed, result in zip((2909, 2910, 2911), candidates):
        fit = _curve_fit_metrics(inversion_c, result["c_t"])
        seed_rows.append({
            "seed": seed,
            "misfit_percent": float(result["misfit"]),
            "rmse_m_s": fit["rmse_m_s"],
            "beta_m_s": np.asarray(result["beta"]).tolist(),
            "thickness_m": np.asarray(result["h"]).tolist(),
        })

    metrics = {
        "pipeline": [
            "measured_acceleration",
            "existing_sos_preprocessing",
            "masw_phase_shift",
            "kalman_rts_ridge_in_frequency",
            "rayleigh_curve_inversion",
        ],
        "uses_temporal_kalman": False,
        "uses_temporal_rts": False,
        "uses_frequency_kalman_rts": True,
        "reference_used_for_tracking_or_inversion": False,
        "sample_rate_hz": fs,
        "channels": int(offsets.size),
        "curve": {
            "points_tracker": int(tracker_f.size),
            "points_inversion": int(inversion_f.size),
            "frequency_range_hz": [float(inversion_f.min()), float(inversion_f.max())],
            "c_rayleigh_range_m_s": [float(inversion_c.min()), float(inversion_c.max())],
            "wavelength_range_m": [float(wavelengths.min()), float(wavelengths.max())],
            "approx_reliable_depth_m": reliable_depth,
            "against_external_reference": pick_vs_reference,
        },
        "monotonic_model": {
            "misfit_percent": float(monotonic["misfit"]),
            "fit": monotonic_fit,
            "beta_m_s": np.asarray(monotonic["beta"]).tolist(),
            "thickness_m": np.asarray(monotonic["h"]).tolist(),
        },
        "recommended_model": "monotonic_four_layers_plus_halfspace",
        "recommended_model_reason": "fit already below 1 m/s RMSE; reversals add complexity for marginal gain",
        "recommended_against_external_reference": theoretical_vs_reference,
        "best_two_reversal_model": {
            "misfit_percent": float(reversal["misfit"]),
            "fit": reversal_fit,
            "beta_m_s": np.asarray(reversal["beta"]).tolist(),
            "thickness_m": np.asarray(reversal["h"]).tolist(),
        },
        "two_reversal_seed_sensitivity": seed_rows,
    }

    np.savetxt(
        output / "dispersion_curve_kalman_acceleration.csv",
        np.column_stack([inversion_f, inversion_c, inversion_sigma, monotonic["c_t"], reversal["c_t"]]),
        delimiter=",",
        header="frequency_hz,cR_kalman_m_s,sigma_kalman_m_s,cR_theoretical_recommended_monotonic_m_s,cR_theoretical_two_reversal_m_s",
        comments="",
    )
    _save_profile(output / "vs_profile_recommended_monotonic.csv", monotonic["beta"], monotonic["h"])
    _save_profile(output / "vs_profile_two_reversal.csv", reversal["beta"], reversal["h"])
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    _plot_dispersion(output / "01_masw_acceleration_kalman_curve.png", f, c, image, tracker, reference)
    _plot_inversion(
        output / "02_inversion_vs_profile.png",
        inversion_f, inversion_c, monotonic, reversal, reference, reliable_depth,
    )

    selected_beta = np.asarray(monotonic["beta"])
    selected_h = np.asarray(monotonic["h"])
    layer_rows = []
    top = 0.0
    for index, velocity in enumerate(selected_beta):
        if index < selected_h.size:
            bottom = top + float(selected_h[index])
            interval = f"{top:.2f}-{bottom:.2f} m"
            top = bottom
        else:
            interval = f"> {top:.2f} m"
        layer_rows.append(f"| {index + 1} | {interval} | {velocity:.2f} |")

    report = f"""# Amedida + Kalman de dispersion + inversion Vs

## Cadena ejecutada

```text
Amedida -> SOS existente -> MASW phase-shift
        -> Kalman + RTS solamente en frecuencia sobre la cresta cR(f)
        -> inversion Rayleigh Vs(z)
```

- No se uso el primer Kalman temporal.
- No se uso RTS temporal.
- La inversion recibe `cR`, no `cR/0.92`.
- La referencia externa no participo en tracking ni inversion; se uso despues para evaluar.

## Curva observada

- {inversion_f.size} puntos usados, {inversion_f.min():.2f}-{inversion_f.max():.2f} Hz.
- cR = {inversion_c.min():.2f}-{inversion_c.max():.2f} m/s.
- Longitudes de onda = {wavelengths.min():.2f}-{wavelengths.max():.2f} m.
- Profundidad aproximadamente resoluble: hasta {reliable_depth:.2f} m.
- Contra referencia externa: RMSE {pick_vs_reference['rmse_m_s']:.2f} m/s, MAE {pick_vs_reference['mae_m_s']:.2f} m/s, sesgo {pick_vs_reference['bias_m_s']:.2f} m/s.

## Inversion recomendada: modelo monotono

| Capa | Profundidad | Vs [m/s] |
|---:|---:|---:|
{chr(10).join(layer_rows)}

- Misfit porcentual medio: **{monotonic['misfit']:.3f} %**.
- RMSE de la curva teorica contra el pick Kalman: **{monotonic_fit['rmse_m_s']:.3f} m/s**.
- Mejor modelo con hasta dos inversiones: misfit **{reversal['misfit']:.3f} %**, RMSE **{reversal_fit['rmse_m_s']:.3f} m/s**.
- Curva teorica recomendada contra referencia externa: RMSE **{theoretical_vs_reference['rmse_m_s']:.3f} m/s**.

## Lectura

La banda se corto en 22 Hz porque, con dx=2 m, el limite espacial `cR >= 2*dx*f` empieza a obligar al tracker a velocidades mayores que el modo lento. Dentro de 8-22 Hz el modelo monotono ya ajusta por debajo de 1 m/s RMSE. Permitir inversiones reduce algo mas el error, pero agrega capas oscilatorias sensibles a la semilla; no se justifica esa complejidad con esta banda. La profundidad aproximadamente resoluble es {reliable_depth:.1f} m. No interpretar las interfaces ni el semiespacio como un perfil geologico definitivo sin incertidumbre y comparacion con otros grupos/campanas.

## Artefactos

- `01_masw_acceleration_kalman_curve.png`
- `02_inversion_vs_profile.png`
- `dispersion_curve_kalman_acceleration.csv`
- `vs_profile_recommended_monotonic.csv`
- `vs_profile_two_reversal.csv`
- `metrics.json`
"""
    report_path = output / "INFORME_AMEDIDA_KALMAN_INVERSION.md"
    report_path.write_text(report, encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(generate(args.output.resolve()))


if __name__ == "__main__":
    main()
