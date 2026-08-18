"""Verificacion de extremo a extremo del primer Kalman de velocidad.

Tres bloques, en este orden:

1. **Sintetico con verdad conocida.** Se genera ``v_ground(t)`` conocida, se la
   propaga por la planta ``H_v = s*H_a`` hasta volts, se agrega ruido y se
   estima con ``kf_forward`` (sin RTS). Se compara contra la planta **defectuosa**
   anterior (``H_a/s``, polos en el origen) corriendo el mismo experimento, para
   que la mejora sea medida y no declarada.
2. **Sensibilidad de Q/R.** Barrido de ``NLL(log10 q)`` sobre el rango completo
   del optimizador, para mostrar que el optimo es interior y unimodal y no un
   valor pegado a un limite de busqueda.
3. **Corrida real de los 21 canales** de Canchita grupo 1, con la comparacion
   espectral de tres brazos por canal y agregada.

Comando::

    cd C:/Github/Tesis/src/interfaces/python
    python -m geophone_scope.kalman_deconv.report_velocity_fix
"""

from __future__ import annotations

import json
from pathlib import Path

from dataclasses import replace

import numpy as np
from scipy import optimize, signal

from .discretize import (
    augment_with_input_model,
    build_input_model,
    discretize_plant,
    prepare_plant,
)
from .kf import kf_forward, nis_consistency
from .library import load_conditioner, load_geophone
from .models import InputModel, PlantSpec, ReductionSpec
from .plant import compose_plant_zpk, zpk_to_modal_ss
from .reduce import prune_near_cancellations, residualize_fast_modes
from .synthetic import forward_simulate, ricker_wavelet, _aligned_metrics

RESULTS = Path(__file__).resolve().parent / "reports" / "velocity_model_fix_2026-08-17"
REAL = Path(__file__).resolve().parent / "reports" / "real_canchita_group1"
METRICS_PATH = RESULTS / "velocity_fix_metrics.json"

# Bandas de la comparacion espectral. La banda util empirica de las campanas es
# 10-50 Hz (HANDOFF_KALMAN.md §5.5); las otras tres estan para detectar que la
# correccion mueva peso hacia abajo y no hacia arriba.
BANDS_HZ = ((1.0, 10.0), (10.0, 50.0), (50.0, 80.0), (80.0, 200.0))
CENTROID_BAND_HZ = (1.0, 200.0)
Q_SEARCH_LOG10 = (-8.0, 5.0)


# --------------------------------------------------------------------------- #
# Plantas: la corregida y la defectuosa, para poder compararlas
# --------------------------------------------------------------------------- #


def _plant_spec(estimate: str) -> PlantSpec:
    return PlantSpec(
        geophone=load_geophone("sm24_nominal"),
        conditioner=load_conditioner("lp_pga_medido"),
        estimate=estimate,
    )


def _defective_velocity_continuous(fs: float):
    """Reproduce la planta ANTERIOR: ``H_a/s``, con un polo en el origen.

    No llama a ``compose_plant_zpk`` con ``estimate='velocity'`` —eso ahora hace
    lo correcto— sino que compone la planta de aceleracion y le agrega a mano el
    polo en s=0 que agregaba ``_integrator_chain``. Existe unicamente para medir
    contra que se compara la correccion.
    """
    z, p, k = compose_plant_zpk(_plant_spec("acceleration"))
    p = np.concatenate([p, np.zeros(1, dtype=complex)])
    reduction = ReductionSpec()
    z, p, k, _ = prune_near_cancellations(z, p, k, tol_ratio=reduction.cancel_tol_ratio)
    A, B, C, D = zpk_to_modal_ss(z, p, k)
    A, B, C, D, _ = residualize_fast_modes(A, B, C, D, cutoff_rad_s=0.9 * np.pi * fs)
    return A, B, C, D


#: Prior de entrada por defecto del primer Kalman.
#:
#: ``ou_band`` centrado en la banda util empirica 10-50 Hz, y **no** el
#: ``leaky_rw`` de 0,7 Hz que se venia usando. El cambio es de fondo, no de
#: tuning: ``leaky_rw`` es plano hasta DC, o sea no le dice al estimador que el
#: suelo no se mueve en continua, y por eso ``v_ground`` terminaba con el 38,7 %
#: de su energia por debajo de 1 Hz. Medido sobre 3 canales (10 / 30 / 50 m):
#:
#:   prior                frac<1 Hz              frac 10-50 Hz
#:   leaky_rw 0,7      0,177 / 0,376 / 0,431   0,880 / 0,531 / 0,327
#:   leaky_rw 10       0,023 / 0,054 / 0,089   0,937 / 0,933 / 0,847
#:   ou_band 10-50     0,004 / 0,008 / 0,013   0,952 / 0,982 / 0,970
#:
#: Con ``leaky_rw`` la energia util se degrada con el offset; con ``ou_band`` se
#: mantiene. El par aumentado sigue siendo observable en z=1 (rango 9/9, mismo
#: sigma_min), asi que no se paga con observabilidad.
DEFAULT_INPUT_MODEL = InputModel(kind="ou_band", band_hz=(10.0, 50.0))

#: El prior anterior, conservado para poder comparar contra el.
LEGACY_INPUT_MODEL = InputModel(kind="leaky_rw", leak_hz=0.7)


def _augmented(continuous, fs: float, q_scale: float, spec: InputModel = None):
    discrete = discretize_plant(*continuous, fs=fs)
    spec = spec or DEFAULT_INPUT_MODEL
    input_model = build_input_model(replace(spec, q_scale=q_scale), fs=fs)
    return augment_with_input_model(discrete, input_model)


def _input_estimate(system, result) -> np.ndarray:
    """Lee ``u_hat`` del estado aumentado: es directamente la magnitud pedida."""
    return (
        result.filtered_state[:, system.plant_order:] @ system.input_model.C.T
    ).ravel()


# --------------------------------------------------------------------------- #
# Q/R
# --------------------------------------------------------------------------- #


def _negative_log_likelihood(values, continuous, fs: float, r_var: float, q_scale: float, spec=None) -> float:
    """NLL de las innovaciones. Es el mismo criterio que usa el pipeline MASW."""
    system = _augmented(continuous, fs, q_scale, spec)
    result = kf_forward(values, system.A, system.C, system.Q, r_var)
    innovation = result.innovations[:, 0]
    covariance = result.innovation_cov[:, 0, 0]
    valid = np.isfinite(innovation) & np.isfinite(covariance) & (covariance > 0)
    if not np.any(valid):
        return float("inf")
    return float(
        0.5
        * np.sum(
            np.log(2.0 * np.pi * covariance[valid])
            + innovation[valid] ** 2 / covariance[valid]
        )
    )


def fit_q_scale(values, continuous, fs: float, r_var: float, *, bounds=Q_SEARCH_LOG10, spec=None):
    fit = optimize.minimize_scalar(
        lambda x: _negative_log_likelihood(values, continuous, fs, r_var, 10.0 ** float(x), spec),
        bounds=bounds,
        method="bounded",
        options={"maxiter": 24, "xatol": 0.02},
    )
    log10_q = float(fit.x)
    margin = min(log10_q - bounds[0], bounds[1] - log10_q)
    return 10.0**log10_q, {
        "q_scale": 10.0**log10_q,
        "log10_q_scale": log10_q,
        "search_bounds_log10": list(bounds),
        "distance_to_nearest_bound_decades": margin,
        "railed_at_bound": bool(margin < 0.05),
        "innovation_nll": float(fit.fun),
        "optimizer_success": bool(fit.success),
        "optimizer_evaluations": int(fit.nfev),
        "method": "minimize_scalar bounded sobre log10(q_scale), NLL de innovaciones",
    }


def q_sensitivity_scan(values, continuous, fs: float, r_var: float, *, n: int = 27, spec=None):
    """Barrido explicito de NLL(log10 q) sobre todo el rango de busqueda."""
    grid = np.linspace(Q_SEARCH_LOG10[0], Q_SEARCH_LOG10[1], n)
    nll = np.array(
        [_negative_log_likelihood(values, continuous, fs, r_var, 10.0**g, spec) for g in grid]
    )
    finite = np.isfinite(nll)
    best = int(np.argmin(np.where(finite, nll, np.inf)))
    # Unimodalidad: cuantos cambios de signo tiene la derivada discreta.
    diff = np.diff(nll[finite])
    sign_changes = int(np.count_nonzero(np.diff(np.sign(diff)) != 0))
    return {
        "log10_q_grid": grid.tolist(),
        "nll": np.where(finite, nll, np.nan).tolist(),
        "grid_argmin_log10_q": float(grid[best]),
        "interior_optimum": bool(0 < best < n - 1),
        "monotonic_sign_changes": sign_changes,
        "unimodal": bool(sign_changes <= 1),
    }


# --------------------------------------------------------------------------- #
# Metricas espectrales
# --------------------------------------------------------------------------- #


def band_metrics(x: np.ndarray, fs: float) -> dict:
    """Fracciones de PSD normalizada por banda y centroide espectral.

    ⚠ Las fracciones por banda estan normalizadas a 1-200 Hz, que es la
    convencion de comparacion del proyecto. Esa normalizacion **esconde** lo que
    haya por debajo de 1 Hz, y con la planta de velocidad eso no es despreciable.
    Por eso se agrega aparte ``frac_below_1_hz_of_total``, medida contra la
    energia total de 0 a Nyquist: es la que detecta la deriva.
    """
    x = np.asarray(x, dtype=float)
    x = x - np.mean(x)
    nperseg = min(x.size, 1024)
    f, pxx = signal.welch(x, fs=fs, nperseg=nperseg)
    total_band = (f >= CENTROID_BAND_HZ[0]) & (f <= CENTROID_BAND_HZ[1])
    total = float(np.trapezoid(pxx[total_band], f[total_band]))
    full = float(np.trapezoid(pxx, f))
    out = {
        "frac_below_1_hz_of_total": float(
            np.trapezoid(pxx[f <= 1.0], f[f <= 1.0]) / max(full, np.finfo(float).tiny)
        ),
        "frac_below_3_hz_of_total": float(
            np.trapezoid(pxx[f <= 3.0], f[f <= 3.0]) / max(full, np.finfo(float).tiny)
        ),
    }
    for lo, hi in BANDS_HZ:
        sel = (f >= lo) & (f <= hi)
        energy = float(np.trapezoid(pxx[sel], f[sel])) if np.any(sel) else 0.0
        out[f"frac_{lo:g}_{hi:g}_hz"] = energy / max(total, np.finfo(float).tiny)
    centroid = float(
        np.sum(f[total_band] * pxx[total_band]) / max(np.sum(pxx[total_band]), np.finfo(float).tiny)
    )
    out["centroid_hz"] = centroid
    return out


def band_snr_db(x: np.ndarray, time_s: np.ndarray, fs: float) -> dict:
    """SNR evento/prearribo por banda, en dB de potencia."""
    x = np.asarray(x, dtype=float)
    pre = time_s < -0.05
    event = (time_s >= 0.0) & (time_s <= 0.6)
    out = {}
    for lo, hi in BANDS_HZ:
        sos = signal.butter(
            4, [lo / (0.5 * fs), min(hi, 0.49 * fs) / (0.5 * fs)], btype="bandpass", output="sos"
        )
        filtered = signal.sosfiltfilt(sos, x)
        p_pre = float(np.mean(filtered[pre] ** 2))
        p_evt = float(np.mean(filtered[event] ** 2))
        out[f"snr_db_{lo:g}_{hi:g}_hz"] = 10.0 * np.log10(
            max(p_evt, 1e-300) / max(p_pre, 1e-300)
        )
    return out


def _robust(rows: list[dict]) -> dict:
    """Agregado robusto sobre canales: mediana y rango intercuartil."""
    keys = rows[0].keys()
    out = {}
    for key in keys:
        values = np.array([row[key] for row in rows], dtype=float)
        out[key] = {
            "median": float(np.median(values)),
            "iqr": float(np.percentile(values, 75) - np.percentile(values, 25)),
            "min": float(np.min(values)),
            "max": float(np.max(values)),
        }
    return out


def adjacent_coherence(matrix: np.ndarray, fs: float, band=(10.0, 50.0)) -> float:
    """Coherencia media entre canales contiguos dentro de la banda util."""
    values = []
    nperseg = min(matrix.shape[1], 512)
    for a, b in zip(matrix[:-1], matrix[1:]):
        f, cxy = signal.coherence(a, b, fs=fs, nperseg=nperseg)
        sel = (f >= band[0]) & (f <= band[1])
        if np.any(sel):
            values.append(float(np.mean(cxy[sel])))
    return float(np.median(values)) if values else float("nan")


# --------------------------------------------------------------------------- #
# Bloque 1: sintetico
# --------------------------------------------------------------------------- #


def synthetic_validation(*, fs: float = 1020.0, snr_db: float = 20.0, seed: int = 2909) -> dict:
    """Verdad conocida de v_ground, estimacion forward-only, planta buena vs mala."""

    n = int(round(2.0 * fs))
    time_s = np.arange(n) / fs
    # v_ground de prueba: dos Ricker dentro de la banda util 10-50 Hz.
    truth = ricker_wavelet(time_s, f0_hz=25.0, center_s=0.8)
    truth += 0.35 * ricker_wavelet(time_s, f0_hz=16.0, center_s=1.25)

    # La medicion se genera SIEMPRE con la planta fisicamente correcta: la de
    # velocidad. Lo que cambia entre brazos es con que modelo se estima.
    truth_plant = prepare_plant(_plant_spec("velocity"), fs).continuous
    discrete_truth = discretize_plant(*truth_plant, fs=fs)
    y_clean, _ = forward_simulate(
        discrete_truth.A, discrete_truth.B, discrete_truth.C, discrete_truth.D, truth
    )
    rng = np.random.default_rng(seed)
    noise_std = float(np.sqrt(np.mean(y_clean**2))) / (10.0 ** (snr_db / 20.0))
    measurement = y_clean + rng.normal(scale=noise_std, size=n)
    r_var = noise_std**2

    arms = {
        "kalman_velocidad_corregido": truth_plant,
        "kalman_velocidad_defectuoso_H_a_sobre_s": _defective_velocity_continuous(fs),
    }
    results = {}
    for name, continuous in arms.items():
        q_scale, tuning = fit_q_scale(measurement, continuous, fs, r_var)
        system = _augmented(continuous, fs, q_scale)
        filtered = kf_forward(measurement, system.A, system.C, system.Q, r_var)
        estimate = _input_estimate(system, filtered)
        metrics = _aligned_metrics(truth, estimate, fs=fs)
        results[name] = {
            "q_tuning": tuning,
            "rmse_aligned_relative": metrics.relative_rmse,
            "amplitude_ratio": metrics.amplitude_ratio,
            "phase_error_deg_10_50_hz": metrics.phase_error_deg,
            "lag_samples": metrics.lag_samples,
            "min_cov_eigenvalue": filtered.min_cov_eigenvalue,
            "p0_method": filtered.p0_method,
            "nis": nis_consistency(filtered.nis),
            "finite_output": bool(np.all(np.isfinite(estimate))),
            "spectrum": band_metrics(estimate, fs),
        }

    results["verdad_v_ground"] = {"spectrum": band_metrics(truth, fs)}
    results["medicion_volts"] = {"spectrum": band_metrics(measurement, fs)}
    results["_setup"] = {
        "fs_hz": fs,
        "snr_db": snr_db,
        "seed": seed,
        "duration_s": n / fs,
        "r_var": r_var,
        "truth": "v_ground = Ricker 25 Hz + 0.35*Ricker 16 Hz",
        "uses_rts": False,
        "note": (
            "La medicion se sintetiza con la planta de velocidad correcta en los "
            "dos brazos; lo unico que cambia es el modelo con el que se estima."
        ),
    }
    results["_sensitivity"] = q_sensitivity_scan(measurement, truth_plant, fs, r_var)
    return results


# --------------------------------------------------------------------------- #
# Bloque 2: corrida real de 21 canales
# --------------------------------------------------------------------------- #


def _post_sos_filter(matrix, fs: float):
    """Se importa del reporte MASW para que los dos usen exactamente el mismo."""
    from .report_vs_apparent_masks import _post_sos_filter as post

    return post(matrix, fs)


def first_kalman_velocity_gather(matrix_sos, time_s, fs: float, offsets):
    """Primer Kalman causal sobre las 21 trazas. Devuelve v_ground(t,x).

    ``R`` sale de la varianza de la ventana pre-arribo de cada canal y ``q`` se
    ajusta por **maxima verosimilitud canal por canal**, no escalando el ``q`` de
    un canal de referencia por el cociente de varianzas. Ese atajo era el de la
    corrida anterior y dejaba el NIS medio entre 0,09 y 6,05 a lo largo del
    arreglo; el ajuste independiente lo acerca a 1 y ademas hace que cada canal
    sea reproducible por si solo.

    El NIS se reporta **descompuesto por ventana** (pre-arribo / evento / cola),
    porque el agregado esconde el hallazgo importante: un prior estacionario no
    puede describir a la vez una ventana silenciosa y un arribo impulsivo.
    """
    pre = time_s < -0.05
    event = (time_s >= 0.0) & (time_s <= 0.6)
    tail = time_s > 0.6
    if np.count_nonzero(pre) < 20:
        raise ValueError("ventana pre-arribo insuficiente para estimar R")
    centered = matrix_sos - np.nanmedian(matrix_sos[:, pre], axis=1, keepdims=True)
    r_values = np.nanvar(centered[:, pre], axis=1, ddof=1)
    positive = r_values[np.isfinite(r_values) & (r_values > 0)]
    floor = max(float(np.median(positive)) * 1e-6, np.finfo(float).tiny)
    r_values = np.maximum(r_values, floor)

    continuous = prepare_plant(_plant_spec("velocity"), fs).continuous
    reference_channel = int(np.argmin(np.abs(offsets - 30.0)))

    velocity = np.empty_like(centered)
    per_channel = []
    minimum_covariance = np.inf
    reference_tuning = None
    for index, values in enumerate(centered):
        r_channel = float(r_values[index])
        q_channel, tuning = fit_q_scale(values, continuous, fs, r_channel)
        if index == reference_channel:
            reference_tuning = tuning
        system = _augmented(continuous, fs, q_channel)
        filtered = kf_forward(values, system.A, system.C, system.Q, r_channel)
        velocity[index] = _input_estimate(system, filtered)
        report = nis_consistency(filtered.nis)
        per_channel.append(
            {
                "offset_m": float(offsets[index]),
                "r_var": r_channel,
                "q_scale": float(q_channel),
                "log10_q_scale": tuning["log10_q_scale"],
                "railed_at_bound": tuning["railed_at_bound"],
                "distance_to_nearest_bound_decades": tuning[
                    "distance_to_nearest_bound_decades"
                ],
                "mean_nis": float(np.nanmean(filtered.nis)),
                "mean_nis_pre_arrival": float(np.nanmean(filtered.nis[pre])),
                "mean_nis_event": float(np.nanmean(filtered.nis[event])),
                "mean_nis_tail": float(np.nanmean(filtered.nis[tail])),
                "nis_sum": report["sum"],
                "nis_ci_low": report["low"],
                "nis_ci_high": report["high"],
                "nis_within_ci": bool(report["passed"]),
                "min_cov_eigenvalue": float(filtered.min_cov_eigenvalue),
                "p0_method": filtered.p0_method,
                "finite": bool(np.all(np.isfinite(velocity[index]))),
            }
        )
        minimum_covariance = min(minimum_covariance, filtered.min_cov_eigenvalue)

    log10_q = np.array([r["log10_q_scale"] for r in per_channel])
    diagnostics = {
        **(reference_tuning or {}),
        "q_estimation": "maxima verosimilitud por canal sobre log10(q_scale)",
        "input_model_kind": DEFAULT_INPUT_MODEL.kind,
        "input_model_band_hz": list(DEFAULT_INPUT_MODEL.band_hz or ()),
        "input_model_leak_hz": DEFAULT_INPUT_MODEL.leak_hz,
        "output": "ground_particle_velocity_m_s",
        "uses_forward_kalman": True,
        "uses_rts": False,
        "reference_channel_distance_m": float(offsets[reference_channel]),
        "log10_q_median": float(np.median(log10_q)),
        "log10_q_spread_decades": float(np.max(log10_q) - np.min(log10_q)),
        "channels_railed_at_bound": int(sum(r["railed_at_bound"] for r in per_channel)),
        "mean_nis_all_channels": float(np.mean([r["mean_nis"] for r in per_channel])),
        "mean_nis_pre_arrival": float(np.mean([r["mean_nis_pre_arrival"] for r in per_channel])),
        "mean_nis_event": float(np.mean([r["mean_nis_event"] for r in per_channel])),
        "mean_nis_tail": float(np.mean([r["mean_nis_tail"] for r in per_channel])),
        "channels_nis_within_ci": int(sum(r["nis_within_ci"] for r in per_channel)),
        "minimum_covariance_eigenvalue": float(minimum_covariance),
        "channels_finite": int(sum(r["finite"] for r in per_channel)),
        "channels_total": len(per_channel),
        "per_channel": per_channel,
    }
    return velocity, diagnostics


def real_run() -> dict:
    data = np.load(REAL / "real_gathers_and_dispersion.npz")
    time_s = data["time_s"]
    fs = float(1.0 / np.median(np.diff(time_s)))
    offsets = data["distances_m"]
    measured = data["measured_acceleration_sos_v"]

    velocity, diagnostics = first_kalman_velocity_gather(measured, time_s, fs, offsets)
    velocity_sos = _post_sos_filter(velocity, fs)

    arms = {
        "amedida_mas_sos_previo": measured,
        "v_ground_kalman1": velocity,
        "v_ground_kalman1_mas_sos_posterior": velocity_sos,
    }
    spectral = {}
    for name, matrix in arms.items():
        rows = []
        for index in range(matrix.shape[0]):
            row = band_metrics(matrix[index], fs)
            row.update(band_snr_db(matrix[index], time_s, fs))
            row["offset_m"] = float(offsets[index])
            rows.append(row)
        spectral[name] = {
            "per_channel": rows,
            "aggregate": _robust([{k: v for k, v in r.items() if k != "offset_m"} for r in rows]),
            "adjacent_coherence_10_50_hz": adjacent_coherence(matrix, fs),
        }

    np.savez_compressed(
        RESULTS / "kalman1_velocity_gather_corregido.npz",
        time_s=time_s,
        distances_m=offsets,
        kalman1_ground_particle_velocity_m_s=velocity,
        kalman1_ground_particle_velocity_post_sos_m_s=velocity_sos,
    )
    return {"fs_hz": fs, "first_kalman": diagnostics, "spectral": spectral}


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    payload = {
        "synthetic": synthetic_validation(),
        "real": real_run(),
    }
    METRICS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    syn = payload["synthetic"]
    print("=" * 78)
    print("SINTETICO - verdad conocida de v_ground, solo kf_forward")
    print("=" * 78)
    print(f"{'brazo':<44s} {'RMSE rel':>9s} {'amplitud':>9s} {'fase':>8s} {'lag':>5s}")
    for name in ("kalman_velocidad_corregido", "kalman_velocidad_defectuoso_H_a_sobre_s"):
        r = syn[name]
        print(
            f"{name:<44s} {r['rmse_aligned_relative']:9.4f} {r['amplitude_ratio']:9.4f} "
            f"{r['phase_error_deg_10_50_hz']:8.3f} {r['lag_samples']:5d}"
        )
    print(f"\n{'brazo':<44s} " + " ".join(f"{f'{lo:g}-{hi:g}':>10s}" for lo, hi in BANDS_HZ) + f" {'centroide':>10s}")
    for name in (
        "verdad_v_ground",
        "kalman_velocidad_corregido",
        "kalman_velocidad_defectuoso_H_a_sobre_s",
        "medicion_volts",
    ):
        s = syn[name]["spectrum"]
        cells = " ".join(f"{s[f'frac_{lo:g}_{hi:g}_hz']:10.6f}" for lo, hi in BANDS_HZ)
        print(f"{name:<44s} {cells} {s['centroid_hz']:10.3f}")
    sens = syn["_sensitivity"]
    print(
        f"\nQ/R: optimo interior={sens['interior_optimum']} unimodal={sens['unimodal']} "
        f"argmin grilla log10 q={sens['grid_argmin_log10_q']:.3f}"
    )
    for name in ("kalman_velocidad_corregido", "kalman_velocidad_defectuoso_H_a_sobre_s"):
        t = syn[name]["q_tuning"]
        print(
            f"  {name:<42s} log10 q={t['log10_q_scale']:+7.3f} "
            f"margen al borde={t['distance_to_nearest_bound_decades']:.3f} dec  "
            f"pegado={t['railed_at_bound']}"
        )

    real = payload["real"]
    fk = real["first_kalman"]
    print("\n" + "=" * 78)
    print(f"REAL - {fk['channels_total']} canales, fs={real['fs_hz']:.1f} Hz, sin RTS")
    print("=" * 78)
    print(
        f"q por canal (ML): mediana log10 q={fk['log10_q_median']:.3f}, "
        f"dispersion={fk['log10_q_spread_decades']:.3f} decadas, "
        f"pegados al borde={fk['channels_railed_at_bound']}/{fk['channels_total']}"
    )
    print(
        f"NIS medio={fk['mean_nis_all_channels']:.4f}  "
        f"(prearribo={fk['mean_nis_pre_arrival']:.4f}  evento={fk['mean_nis_event']:.4f}  "
        f"cola={fk['mean_nis_tail']:.4f})  dentro del IC95={fk['channels_nis_within_ci']}/{fk['channels_total']}"
    )
    print(
        f"min eig(P)={fk['minimum_covariance_eigenvalue']:.4e}  "
        f"canales finitos={fk['channels_finite']}/{fk['channels_total']}"
    )
    header = " ".join(f"{f'{lo:g}-{hi:g}':>10s}" for lo, hi in BANDS_HZ)
    print(f"\n{'brazo (mediana 21 canales)':<40s} {header} {'centroide':>10s} {'coher':>7s}")
    for name, block in real["spectral"].items():
        agg = block["aggregate"]
        cells = " ".join(f"{agg[f'frac_{lo:g}_{hi:g}_hz']['median']:10.6f}" for lo, hi in BANDS_HZ)
        print(
            f"{name:<40s} {cells} {agg['centroid_hz']['median']:10.3f} "
            f"{block['adjacent_coherence_10_50_hz']:7.4f}"
        )
    print(
        f"\n{'brazo':<40s} {'frac <1 Hz':>12s} {'frac <3 Hz':>12s}   "
        "(sobre la energia total 0-Nyquist, NO sobre 1-200 Hz)"
    )
    for name, block in real["spectral"].items():
        agg = block["aggregate"]
        print(
            f"{name:<40s} {agg['frac_below_1_hz_of_total']['median']:12.6f} "
            f"{agg['frac_below_3_hz_of_total']['median']:12.6f}"
        )
    print(f"\n{'brazo':<40s} " + " ".join(f"{f'SNR {lo:g}-{hi:g}':>12s}" for lo, hi in BANDS_HZ))
    for name, block in real["spectral"].items():
        agg = block["aggregate"]
        cells = " ".join(f"{agg[f'snr_db_{lo:g}_{hi:g}_hz']['median']:12.3f}" for lo, hi in BANDS_HZ)
        print(f"{name:<40s} {cells}")
    print(f"\nmetricas -> {METRICS_PATH}")


if __name__ == "__main__":
    main()
