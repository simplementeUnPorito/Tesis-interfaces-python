"""Banco sintetico reproducible para la deconvolucion KF + RTS."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

import numpy as np
from scipy import linalg, signal

from .discretize import (
    augment_with_input_model,
    build_input_model,
    discretize_plant,
    prepare_plant,
)
from .kf import KalmanResult, SmootherResult, kf_forward, nis_consistency, rts_backward
from .models import InputModel, PlantSpec, ReductionSpec
from .plant import compose_plant_zpk, zpk_to_modal_ss


@dataclass(frozen=True)
class RecoveryMetrics:
    rmse: float
    rmse_aligned: float
    relative_rmse: float
    lag_samples: int
    amplitude_ratio: float
    phase_error_deg: float


@dataclass(frozen=True)
class BenchmarkResult:
    fs: float
    snr_db: float
    q_scale: float
    time_s: np.ndarray
    truth: np.ndarray
    measurement_clean: np.ndarray
    measurement: np.ndarray
    filtered_input: np.ndarray
    smoothed_input: np.ndarray
    filtered: KalmanResult
    smoothed: SmootherResult
    kf_metrics: RecoveryMetrics
    rts_metrics: RecoveryMetrics
    nis_report: dict[str, float | bool | int]
    used_full_model_simulation: bool
    nan_fraction: float


def ricker_wavelet(time_s: np.ndarray, *, f0_hz: float = 25.0, center_s: float = 1.0) -> np.ndarray:
    tau = np.asarray(time_s, dtype=float) - float(center_s)
    a = (np.pi * float(f0_hz) * tau) ** 2
    return (1.0 - 2.0 * a) * np.exp(-a)


def forward_simulate(
    A: np.ndarray,
    B: np.ndarray,
    C: np.ndarray,
    D: np.ndarray,
    u: np.ndarray,
    *,
    x0: Optional[np.ndarray] = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Simula una planta discreta con convencion y[k]=Cx[k]+Du[k]."""

    u = np.asarray(u, dtype=float).reshape(-1)
    n = A.shape[0]
    x = np.zeros(n) if x0 is None else np.asarray(x0, dtype=float).reshape(n)
    states = np.empty((u.size, n))
    y = np.empty(u.size)
    for k, value in enumerate(u):
        states[k] = x
        y[k] = float((C @ x + D * value)[0, 0])
        x = A @ x + B[:, 0] * value
    return y, states


def _aligned_metrics(
    truth: np.ndarray,
    estimate: np.ndarray,
    *,
    fs: float,
    phase_band_hz: tuple[float, float] = (10.0, 50.0),
) -> RecoveryMetrics:
    truth = np.asarray(truth, dtype=float)
    estimate = np.asarray(estimate, dtype=float)
    corr = signal.correlate(estimate, truth, mode="full", method="fft")
    lags = signal.correlation_lags(estimate.size, truth.size, mode="full")
    lag = int(lags[int(np.argmax(np.abs(corr)))])
    if lag > 0:
        est_a, truth_a = estimate[lag:], truth[:-lag]
    elif lag < 0:
        est_a, truth_a = estimate[:lag], truth[-lag:]
    else:
        est_a, truth_a = estimate, truth
    rmse = float(np.sqrt(np.mean((estimate - truth) ** 2)))
    rmse_aligned = float(np.sqrt(np.mean((est_a - truth_a) ** 2)))
    truth_rms = float(np.sqrt(np.mean(truth_a**2)))
    est_rms = float(np.sqrt(np.mean(est_a**2)))

    window = np.hanning(truth_a.size)
    U = np.fft.rfft(truth_a * window)
    E = np.fft.rfft(est_a * window)
    f = np.fft.rfftfreq(truth_a.size, 1.0 / fs)
    band = (f >= phase_band_hz[0]) & (f <= phase_band_hz[1])
    cross = np.sum(E[band] * np.conj(U[band]))
    phase = float(np.angle(cross, deg=True)) if np.any(band) else float("nan")
    return RecoveryMetrics(
        rmse=rmse,
        rmse_aligned=rmse_aligned,
        relative_rmse=rmse_aligned / max(truth_rms, np.finfo(float).tiny),
        lag_samples=lag,
        amplitude_ratio=est_rms / max(truth_rms, np.finfo(float).tiny),
        phase_error_deg=phase,
    )


def _oracle_q_scale(truth: np.ndarray, model: InputModel, fs: float) -> float:
    """Intensidad conocida del banco; S3 reemplaza esto por ML/L-curve."""

    if model.kind in ("random_walk", "leaky_rw"):
        a = 1.0 if model.kind == "random_walk" else np.exp(-2.0 * np.pi * model.leak_hz / fs)
        innovations = truth[1:] - a * truth[:-1]
        # El factor 2 compensa que el pulso determinista concentra la varianza
        # en una ventana corta; sin el, el prior queda demasiado rigido justo en
        # los flancos del Ricker. Es un valor oraculo exclusivo del banco.
        return max(float(2.0 * np.var(innovations) * fs), 1e-16)
    return max(float(np.var(np.diff(truth, n=2)) * fs**3), 1e-16)


def benchmark_ricker25(
    plant_spec: PlantSpec,
    *,
    fs: float,
    snr_db: float = 20.0,
    duration_s: float = 2.0,
    input_model: Optional[InputModel] = None,
    reduction: Optional[ReductionSpec] = None,
    q_scale: Optional[float] = None,
    nan_fraction: float = 0.0,
    use_full_model: bool = False,
    seed: int = 2909,
) -> BenchmarkResult:
    """Ricker de 25 Hz, ruido blanco y estimacion con la planta reducida."""

    fs = float(fs)
    n_samples = int(round(duration_s * fs))
    time_s = np.arange(n_samples) / fs
    truth = ricker_wavelet(time_s, f0_hz=25.0, center_s=0.8)
    # Un pulso debil adicional evita que la metrica dependa de un unico instante.
    truth += 0.35 * ricker_wavelet(time_s, f0_hz=16.0, center_s=1.25)

    prepared = prepare_plant(plant_spec, fs, reduction=reduction, residualize=True)
    discrete = discretize_plant(*prepared.continuous, fs=fs, method="zoh")

    if use_full_model:
        full_ss = zpk_to_modal_ss(*compose_plant_zpk(plant_spec))
        Ad, Bd, Cd, Dd, _ = signal.cont2discrete(full_ss, 1.0 / fs, method="zoh")
        y_clean, _ = forward_simulate(Ad, Bd, Cd, Dd, truth)
    else:
        y_clean, _ = forward_simulate(discrete.A, discrete.B, discrete.C, discrete.D, truth)

    rng = np.random.default_rng(seed)
    signal_rms = float(np.sqrt(np.mean(y_clean**2)))
    noise_std = signal_rms / (10.0 ** (float(snr_db) / 20.0))
    measurement = y_clean + rng.normal(scale=noise_std, size=n_samples)

    base_input = input_model or InputModel(kind="leaky_rw")
    q_used = _oracle_q_scale(truth, base_input, fs) if q_scale is None else float(q_scale)
    input_ss = build_input_model(replace(base_input, q_scale=q_used), fs=fs)
    augmented = augment_with_input_model(discrete, input_ss)

    if nan_fraction:
        count = int(round(n_samples * float(nan_fraction)))
        indices = rng.choice(n_samples, size=count, replace=False)
        measurement[indices] = np.nan

    filtered = kf_forward(
        measurement,
        augmented.A,
        augmented.C,
        augmented.Q,
        noise_std**2,
    )
    smoothed = rts_backward(filtered, augmented.A)
    input_slice = slice(augmented.plant_order, None)
    filtered_input = (filtered.filtered_state[:, input_slice] @ input_ss.C.T).ravel()
    smoothed_input = (smoothed.smoothed_state[:, input_slice] @ input_ss.C.T).ravel()

    return BenchmarkResult(
        fs=fs,
        snr_db=float(snr_db),
        q_scale=q_used,
        time_s=time_s,
        truth=truth,
        measurement_clean=y_clean,
        measurement=measurement,
        filtered_input=filtered_input,
        smoothed_input=smoothed_input,
        filtered=filtered,
        smoothed=smoothed,
        kf_metrics=_aligned_metrics(truth, filtered_input, fs=fs),
        rts_metrics=_aligned_metrics(truth, smoothed_input, fs=fs),
        nis_report=nis_consistency(filtered.nis),
        used_full_model_simulation=bool(use_full_model),
        nan_fraction=float(nan_fraction),
    )


def nmp_inverse_growth(
    plant_spec: PlantSpec,
    *,
    fs: float,
    lengths: tuple[int, int] = (1024, 2048),
) -> dict[str, float | bool]:
    """Test negativo simple: inversion causal recursiva de ceros discretos NMP."""

    z, p, k = compose_plant_zpk(plant_spec)
    ss = zpk_to_modal_ss(z, p, k)
    Ad, Bd, Cd, Dd, _ = signal.cont2discrete(ss, 1.0 / float(fs), method="zoh")
    with np.errstate(all="ignore"):
        num, den = signal.ss2tf(Ad, Bd, Cd, Dd)
    b = np.trim_zeros(np.asarray(num[0], dtype=float), "f")
    a = np.trim_zeros(np.asarray(den, dtype=float), "f")
    if b.size == 0:
        raise ValueError("numerador discreto nulo")
    norms = []
    for n in lengths:
        impulse = np.zeros(n)
        impulse[0] = 1.0
        inverse = signal.lfilter(a, b, impulse)
        finite = inverse[np.isfinite(inverse)]
        norms.append(float(linalg.norm(finite)) if finite.size else float("inf"))
    ratio = norms[1] / max(norms[0], np.finfo(float).tiny)
    return {
        "norm_short": norms[0],
        "norm_long": norms[1],
        "growth_ratio": ratio,
        "diverges": bool(ratio > 10.0 or not np.isfinite(ratio)),
    }


__all__ = [
    "BenchmarkResult",
    "RecoveryMetrics",
    "benchmark_ricker25",
    "forward_simulate",
    "nmp_inverse_growth",
    "ricker_wavelet",
]
