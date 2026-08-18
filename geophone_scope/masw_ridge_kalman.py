"""Seguimiento Kalman/RTS de una cresta en una imagen de dispersion MASW.

El estado es ``[p, dp/df]``, donde ``p = 1/c`` es la lentitud.  Trabajar en
lentitud hace lineal la extrapolacion con la frecuencia y permite, cuando la
geologia lo justifica, imponer la hipotesis de dispersion normal
(``dp/df >= 0``). La referencia hidrogeologica nunca entra al estimador: solo
se usa posteriormente para evaluar los picks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from scipy import linalg


@dataclass(frozen=True)
class RidgeKalmanConfig:
    """Parametros fisicos y estadisticos del seguidor de cresta."""

    f_min_hz: float = 8.0
    f_max_hz: float = 30.0
    direction: Literal["ascending", "descending"] = "ascending"
    seed_c_min_m_s: float = 0.0
    seed_c_max_m_s: float = 160.0
    gate_m_s: float = 20.0
    process_density: float = 2.0e-10
    prediction_sigma_m_s: float = 10.0
    min_peak_sigma_m_s: float = 1.0
    max_peak_sigma_m_s: float = 20.0
    min_relative_amplitude: float = 0.20
    min_wavelength_dx: float = 2.0
    max_wavelength_aperture: float = 1.0
    enforce_physical_mask: bool = True
    enforce_normal_dispersion: bool = False
    max_upward_step_m_s: float = 0.0
    clip_smoothed_to_physical_mask: bool = False


@dataclass(frozen=True)
class RidgeKalmanResult:
    frequency_hz: np.ndarray
    measured_velocity_m_s: np.ndarray
    filtered_velocity_m_s: np.ndarray
    smoothed_velocity_m_s: np.ndarray
    smoothed_sigma_m_s: np.ndarray
    peak_amplitude: np.ndarray
    filtered_state: np.ndarray
    filtered_cov: np.ndarray
    predicted_state: np.ndarray
    predicted_cov: np.ndarray
    smoothed_state: np.ndarray
    smoothed_cov: np.ndarray
    candidate_count: np.ndarray
    physical_mask: np.ndarray
    prediction_gate_mask: np.ndarray
    candidate_mask: np.ndarray
    selected_velocity_index: np.ndarray


def _physical_mask(
    frequency_hz: float,
    velocities_m_s: np.ndarray,
    offsets_m: np.ndarray,
    config: RidgeKalmanConfig,
) -> np.ndarray:
    offsets = np.sort(np.asarray(offsets_m, dtype=float))
    aperture = float(offsets[-1] - offsets[0]) if offsets.size > 1 else 1.0
    dx = float(np.median(np.diff(offsets))) if offsets.size > 1 else 1.0
    lower = config.min_wavelength_dx * dx * frequency_hz
    upper = config.max_wavelength_aperture * aperture * frequency_hz
    return (velocities_m_s >= lower) & (velocities_m_s <= upper)


def _peak_sigma(
    row: np.ndarray,
    velocities: np.ndarray,
    index: int,
    config: RidgeKalmanConfig,
) -> float:
    """Convierte el ancho a media altura de un pico en sigma de velocidad."""

    peak = float(row[index])
    half = 0.5 * peak
    left = index
    while left > 0 and row[left] >= half:
        left -= 1
    right = index
    while right < row.size - 1 and row[right] >= half:
        right += 1
    fwhm = max(float(velocities[right] - velocities[left]), 0.0)
    sigma = fwhm / 2.355
    # Un pico debil es menos informativo aun si es angosto por discretizacion.
    relative = peak / max(float(np.nanmax(row)), np.finfo(float).tiny)
    sigma /= np.sqrt(max(relative, 0.05))
    return float(np.clip(
        sigma, config.min_peak_sigma_m_s, config.max_peak_sigma_m_s
    ))


def track_dispersion_ridge(
    frequency_hz: np.ndarray,
    velocities_m_s: np.ndarray,
    amplitude: np.ndarray,
    offsets_m: np.ndarray,
    config: RidgeKalmanConfig | None = None,
) -> RidgeKalmanResult:
    """Sigue una cresta MASW con KF hacia adelante y RTS en frecuencia.

    La seleccion de la medicion se hace dentro de una compuerta alrededor de la
    prediccion.  El KF pondera esa medicion por el ancho del pico, y el RTS usa
    toda la banda para suavizar la trayectoria.  No se consulta ninguna curva
    de referencia durante el proceso.
    """

    cfg = config or RidgeKalmanConfig()
    f_all = np.asarray(frequency_hz, dtype=float)
    c = np.asarray(velocities_m_s, dtype=float)
    image = np.asarray(amplitude, dtype=float)
    offsets = np.asarray(offsets_m, dtype=float)
    if image.shape != (f_all.size, c.size):
        raise ValueError("amplitude debe tener forma (n_frecuencias, n_velocidades)")
    if f_all.size < 2 or c.size < 2:
        raise ValueError("la imagen de dispersion es demasiado pequena")

    band = (
        np.isfinite(f_all)
        & (f_all >= cfg.f_min_hz)
        & (f_all <= cfg.f_max_hz)
    )
    indices = np.flatnonzero(band)
    if indices.size < 3:
        raise ValueError("hay menos de tres bines en la banda del tracker")
    if cfg.direction == "descending":
        indices = indices[::-1]
    elif cfg.direction != "ascending":
        raise ValueError(f"direccion desconocida: {cfg.direction!r}")
    f = f_all[indices]
    rows = image[indices]

    n = f.size
    measured = np.empty(n)
    filtered_x = np.empty((n, 2))
    filtered_p = np.empty((n, 2, 2))
    predicted_x = np.empty_like(filtered_x)
    predicted_p = np.empty_like(filtered_p)
    peak_amp = np.empty(n)
    candidate_count = np.empty(n, dtype=int)
    physical_masks = np.zeros((n, c.size), dtype=bool)
    prediction_masks = np.zeros_like(physical_masks)
    candidate_masks = np.zeros_like(physical_masks)
    selected_indices = np.empty(n, dtype=int)
    transitions: list[np.ndarray] = []

    first_physical = _physical_mask(f[0], c, offsets, cfg)
    first_search = first_physical if cfg.enforce_physical_mask else np.ones_like(first_physical)
    first_valid = (
        first_search
        & (c >= cfg.seed_c_min_m_s)
        & (c <= cfg.seed_c_max_m_s)
    )
    if not np.any(first_valid):
        raise ValueError("no hay candidato fisico para inicializar el tracker")
    first_candidates = np.flatnonzero(first_valid)
    first_index = int(first_candidates[np.argmax(rows[0, first_candidates])])
    c0 = float(c[first_index])
    sigma_c0 = _peak_sigma(rows[0], c, first_index, cfg)
    p0 = 1.0 / c0
    sigma_p0 = sigma_c0 / (c0 * c0)
    x = np.array([p0, 0.0])
    P = np.diag([sigma_p0**2, (sigma_p0 / max(f[-1] - f[0], 1.0))**2])

    for k in range(n):
        if k == 0:
            x_pred = x.copy()
            P_pred = P.copy()
            selected = first_index
        else:
            df = float(f[k] - f[k - 1])
            F = np.array([[1.0, df], [0.0, 1.0]])
            q = cfg.process_density
            step = abs(df)
            direction = 1.0 if df >= 0.0 else -1.0
            Q = q * np.array([
                [step**3 / 3.0, direction * step**2 / 2.0],
                [direction * step**2 / 2.0, step],
            ])
            x_pred = F @ x
            P_pred = F @ P @ F.T + Q
            P_pred = 0.5 * (P_pred + P_pred.T)
            transitions.append(F)

            c_pred = 1.0 / max(float(x_pred[0]), np.finfo(float).tiny)
            # Una extrapolacion momentaneamente cercana a p=0 no debe producir
            # penalizaciones infinitas ni sacar la busqueda de la grilla.
            c_pred = float(np.clip(c_pred, c[0] - cfg.gate_m_s, c[-1] + cfg.gate_m_s))
            physical = _physical_mask(f[k], c, offsets, cfg)
            valid = physical.copy() if cfg.enforce_physical_mask else np.ones_like(physical)
            valid &= np.abs(c - c_pred) <= cfg.gate_m_s
            if cfg.enforce_normal_dispersion:
                if df >= 0.0:
                    valid &= c <= measured[k - 1] + cfg.max_upward_step_m_s
                else:
                    valid &= c >= measured[k - 1] - cfg.max_upward_step_m_s
            prediction_masks[k] = valid
            row_max = max(float(np.nanmax(rows[k])), np.finfo(float).tiny)
            valid &= rows[k] >= cfg.min_relative_amplitude * row_max
            candidates = np.flatnonzero(valid)
            if candidates.size == 0:
                # Mantiene continuidad aun cuando el pico cae bajo el umbral.
                valid = physical.copy() if cfg.enforce_physical_mask else np.ones_like(physical)
                valid &= np.abs(c - c_pred) <= cfg.gate_m_s
                if cfg.enforce_normal_dispersion:
                    if df >= 0.0:
                        valid &= c <= measured[k - 1] + cfg.max_upward_step_m_s
                    else:
                        valid &= c >= measured[k - 1] - cfg.max_upward_step_m_s
                candidates = np.flatnonzero(valid)
            if candidates.size == 0:
                candidates = np.flatnonzero(
                    physical if cfg.enforce_physical_mask else np.ones_like(physical)
                )
            if candidates.size == 0:
                raise ValueError(f"sin candidato fisico en f={f[k]:.3f} Hz")
            log_amp = np.log(np.maximum(rows[k, candidates], np.finfo(float).tiny))
            penalty = 0.5 * ((c[candidates] - c_pred) / cfg.prediction_sigma_m_s) ** 2
            selected = int(candidates[np.argmax(log_amp - penalty)])

        physical_masks[k] = _physical_mask(f[k], c, offsets, cfg)
        if k == 0:
            prediction_masks[k] = first_valid
        candidate_masks[k, candidates if k else first_candidates] = True
        candidate_count[k] = int(np.count_nonzero(candidate_masks[k]))
        selected_indices[k] = selected
        c_meas = float(c[selected])
        measured[k] = c_meas
        peak_amp[k] = float(rows[k, selected])
        sigma_c = _peak_sigma(rows[k], c, selected, cfg)
        sigma_p = sigma_c / (c_meas * c_meas)
        H = np.array([[1.0, 0.0]])
        R = np.array([[sigma_p**2]])
        innovation = np.array([1.0 / c_meas]) - H @ x_pred
        S = H @ P_pred @ H.T + R
        K = linalg.solve(S, H @ P_pred, assume_a="pos").T
        x = x_pred + K @ innovation
        I_KH = np.eye(2) - K @ H
        P = I_KH @ P_pred @ I_KH.T + K @ R @ K.T
        P = 0.5 * (P + P.T)
        if cfg.enforce_normal_dispersion and x[1] < 0.0:
            x[1] = 0.0
        predicted_x[k] = x_pred
        predicted_p[k] = P_pred
        filtered_x[k] = x
        filtered_p[k] = P

    smoothed_x = filtered_x.copy()
    smoothed_p = filtered_p.copy()
    for k in range(n - 2, -1, -1):
        F = transitions[k]
        gain = linalg.solve(predicted_p[k + 1], (filtered_p[k] @ F.T).T, assume_a="pos").T
        smoothed_x[k] = filtered_x[k] + gain @ (smoothed_x[k + 1] - predicted_x[k + 1])
        smoothed_p[k] = filtered_p[k] + gain @ (smoothed_p[k + 1] - predicted_p[k + 1]) @ gain.T
        smoothed_p[k] = 0.5 * (smoothed_p[k] + smoothed_p[k].T)

    if cfg.enforce_normal_dispersion:
        # Proyeccion convexa simple: p no puede decrecer con f. Conserva la
        # interpretacion fisica aun cuando el paso RTS no restringido lo intente.
        if f[0] <= f[-1]:
            smoothed_x[:, 0] = np.maximum.accumulate(smoothed_x[:, 0])
        else:
            smoothed_x[:, 0] = np.minimum.accumulate(smoothed_x[:, 0])
        smoothed_x[:, 1] = np.maximum(smoothed_x[:, 1], 0.0)
    filtered_c = 1.0 / np.maximum(filtered_x[:, 0], np.finfo(float).tiny)
    smoothed_c = 1.0 / np.maximum(smoothed_x[:, 0], np.finfo(float).tiny)
    filtered_c = np.clip(filtered_c, c[0], c[-1])
    smoothed_c = np.clip(smoothed_c, c[0], c[-1])
    if cfg.clip_smoothed_to_physical_mask:
        sorted_offsets = np.sort(offsets)
        dx = float(np.median(np.diff(sorted_offsets))) if sorted_offsets.size > 1 else 1.0
        aperture = float(sorted_offsets[-1] - sorted_offsets[0]) if sorted_offsets.size > 1 else 1.0
        lower = cfg.min_wavelength_dx * dx * f
        upper = cfg.max_wavelength_aperture * aperture * f
        smoothed_c = np.clip(smoothed_c, lower, upper)
        smoothed_x[:, 0] = 1.0 / np.maximum(smoothed_c, np.finfo(float).tiny)
    sigma_p = np.sqrt(np.maximum(smoothed_p[:, 0, 0], 0.0))
    sigma_c = sigma_p / np.maximum(smoothed_x[:, 0] ** 2, np.finfo(float).tiny)
    return RidgeKalmanResult(
        frequency_hz=f,
        measured_velocity_m_s=measured,
        filtered_velocity_m_s=filtered_c,
        smoothed_velocity_m_s=smoothed_c,
        smoothed_sigma_m_s=sigma_c,
        peak_amplitude=peak_amp,
        filtered_state=filtered_x,
        filtered_cov=filtered_p,
        predicted_state=predicted_x,
        predicted_cov=predicted_p,
        smoothed_state=smoothed_x,
        smoothed_cov=smoothed_p,
        candidate_count=candidate_count,
        physical_mask=physical_masks,
        prediction_gate_mask=prediction_masks,
        candidate_mask=candidate_masks,
        selected_velocity_index=selected_indices,
    )


__all__ = ["RidgeKalmanConfig", "RidgeKalmanResult", "track_dispersion_ridge"]
