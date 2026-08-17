"""Filtro de Kalman numericamente defensivo y suavizador RTS de intervalo fijo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy import linalg, stats


@dataclass(frozen=True)
class KalmanResult:
    filtered_state: np.ndarray
    filtered_cov: np.ndarray
    predicted_state: np.ndarray
    predicted_cov: np.ndarray
    innovations: np.ndarray
    innovation_cov: np.ndarray
    nis: np.ndarray
    observed: np.ndarray
    min_cov_eigenvalue: float
    p0_method: str


@dataclass(frozen=True)
class SmootherResult:
    smoothed_state: np.ndarray
    smoothed_cov: np.ndarray
    gains: np.ndarray
    min_cov_eigenvalue: float


def _symmetrize(matrix: np.ndarray) -> np.ndarray:
    return 0.5 * (matrix + matrix.T)


def _positive_semidefinite(matrix: np.ndarray, *, relative_floor: float = 1e-14) -> np.ndarray:
    """Recorta solo autovalores negativos de redondeo y conserva la escala."""

    matrix = _symmetrize(matrix)
    values, vectors = linalg.eigh(matrix, check_finite=False)
    scale = max(float(np.max(np.abs(values))), 1.0)
    floor = relative_floor * scale
    values = np.maximum(values, floor)
    return (vectors * values) @ vectors.T


def stationary_covariance(A: np.ndarray, Q: np.ndarray) -> tuple[np.ndarray, str]:
    """P0 estacionaria; el fallback se usa solo si existe un modo no estable."""

    radius = float(np.max(np.abs(linalg.eigvals(A)))) if A.size else 0.0
    if radius < 1.0 - 1e-12:
        P = linalg.solve_discrete_lyapunov(A, Q)
        return _positive_semidefinite(P), "stationary_lyapunov"
    scale = max(float(np.trace(Q)) / max(A.shape[0], 1), 1.0)
    return np.eye(A.shape[0]) * scale, "nonstationary_fallback"


def _measurement_layout(
    y: np.ndarray, C: np.ndarray, R: float | np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    y = np.asarray(y, dtype=float)
    if y.ndim == 1:
        y = y[:, None]
    if y.ndim != 2:
        raise ValueError("y debe tener forma (N,) o (N, L)")
    base_c = np.asarray(C, dtype=float)
    if base_c.ndim != 2 or base_c.shape[0] != 1:
        if base_c.shape[0] != y.shape[1]:
            raise ValueError("C debe tener una fila o tantas filas como columnas de y")
        C_full = base_c
    else:
        C_full = np.repeat(base_c, y.shape[1], axis=0)

    R_arr = np.asarray(R, dtype=float)
    if R_arr.ndim == 0:
        if float(R_arr) <= 0:
            raise ValueError("R debe ser positiva")
        R_full = np.eye(y.shape[1]) * float(R_arr)
    elif R_arr.shape == (y.shape[1],):
        R_full = np.diag(R_arr)
    elif R_arr.shape == (y.shape[1], y.shape[1]):
        R_full = R_arr
    else:
        raise ValueError("R incompatible con las columnas de y")
    return y, C_full, R_full


def kf_forward(
    y: np.ndarray,
    A: np.ndarray,
    C: np.ndarray,
    Q: np.ndarray,
    R: float | np.ndarray,
    *,
    x0: Optional[np.ndarray] = None,
    P0: Optional[np.ndarray] = None,
) -> KalmanResult:
    """KF con Joseph, simetrizacion y actualizacion parcial ante NaN.

    ``y`` puede ser un vector SISO o una matriz ``(N, L)`` de geofonos que
    comparten la misma entrada. Una muestra NaN omite solo esa medicion; si toda
    la fila falta, el paso queda en prediccion pura.
    """

    A = np.asarray(A, dtype=float)
    C = np.asarray(C, dtype=float)
    Q = _positive_semidefinite(np.asarray(Q, dtype=float), relative_floor=0.0)
    y, C_full, R_full = _measurement_layout(y, C, R)
    n_samples, n_outputs = y.shape
    n = A.shape[0]
    if A.shape != (n, n) or Q.shape != (n, n) or C_full.shape[1] != n:
        raise ValueError("dimensiones incompatibles en A, C o Q")

    x = np.zeros(n) if x0 is None else np.asarray(x0, dtype=float).reshape(n)
    if P0 is None:
        P, p0_method = stationary_covariance(A, Q)
    else:
        P = _positive_semidefinite(np.asarray(P0, dtype=float))
        p0_method = "provided"

    xf = np.empty((n_samples, n))
    Pf = np.empty((n_samples, n, n))
    xp = np.empty_like(xf)
    Pp = np.empty_like(Pf)
    innovations = np.full((n_samples, n_outputs), np.nan)
    innovation_cov = np.full((n_samples, n_outputs, n_outputs), np.nan)
    nis = np.full(n_samples, np.nan)
    observed = np.isfinite(y)
    identity = np.eye(n)
    min_eig = np.inf

    for k in range(n_samples):
        if k == 0:
            x_pred = x
            P_pred = P
        else:
            x_pred = A @ x
            P_pred = _positive_semidefinite(A @ P @ A.T + Q)
        xp[k] = x_pred
        Pp[k] = P_pred

        mask = observed[k]
        if not np.any(mask):
            x, P = x_pred, P_pred
        else:
            H = C_full[mask]
            measurement = y[k, mask]
            Rk = R_full[np.ix_(mask, mask)]
            innovation = measurement - H @ x_pred
            S = _symmetrize(H @ P_pred @ H.T + Rk)
            # solve(S, H P) evita invertir S explicitamente.
            K = linalg.solve(S, H @ P_pred, assume_a="pos").T
            x = x_pred + K @ innovation
            left = identity - K @ H
            P = left @ P_pred @ left.T + K @ Rk @ K.T  # Joseph
            P = _positive_semidefinite(P)
            innovations[k, mask] = innovation
            innovation_cov[k][np.ix_(mask, mask)] = S
            nis[k] = float(innovation @ linalg.solve(S, innovation, assume_a="pos"))

        xf[k] = x
        Pf[k] = P
        min_eig = min(min_eig, float(linalg.eigvalsh(P, subset_by_index=[0, 0])[0]))

    return KalmanResult(
        xf, Pf, xp, Pp, innovations, innovation_cov, nis, observed,
        float(min_eig), p0_method,
    )


def rts_backward(filtered: KalmanResult, A: np.ndarray) -> SmootherResult:
    """RTS de intervalo fijo usando las predicciones guardadas por el KF."""

    A = np.asarray(A, dtype=float)
    xs = filtered.filtered_state.copy()
    Ps = filtered.filtered_cov.copy()
    n_samples, n = xs.shape
    gains = np.zeros((max(n_samples - 1, 0), n, n))
    min_eig = float(linalg.eigvalsh(Ps[-1], subset_by_index=[0, 0])[0])

    for k in range(n_samples - 2, -1, -1):
        cross = filtered.filtered_cov[k] @ A.T
        predicted_next = filtered.predicted_cov[k + 1]
        gain = linalg.solve(predicted_next, cross.T, assume_a="pos").T
        gains[k] = gain
        xs[k] = filtered.filtered_state[k] + gain @ (
            xs[k + 1] - filtered.predicted_state[k + 1]
        )
        Ps[k] = filtered.filtered_cov[k] + gain @ (
            Ps[k + 1] - predicted_next
        ) @ gain.T
        Ps[k] = _positive_semidefinite(Ps[k])
        min_eig = min(
            min_eig,
            float(linalg.eigvalsh(Ps[k], subset_by_index=[0, 0])[0]),
        )
    return SmootherResult(xs, Ps, gains, float(min_eig))


def nis_consistency(nis: np.ndarray, *, alpha: float = 0.05) -> dict[str, float | bool | int]:
    """Consistencia agregada NIS contra el IC chi-cuadrado bilateral."""

    values = np.asarray(nis, dtype=float)
    values = values[np.isfinite(values)]
    dof = int(values.size)
    if dof == 0:
        return {"passed": False, "dof": 0, "sum": float("nan"), "low": float("nan"), "high": float("nan")}
    total = float(np.sum(values))
    low = float(stats.chi2.ppf(alpha / 2.0, dof))
    high = float(stats.chi2.ppf(1.0 - alpha / 2.0, dof))
    return {"passed": low <= total <= high, "dof": dof, "sum": total, "low": low, "high": high}


__all__ = [
    "KalmanResult",
    "SmootherResult",
    "kf_forward",
    "nis_consistency",
    "rts_backward",
    "stationary_covariance",
]
