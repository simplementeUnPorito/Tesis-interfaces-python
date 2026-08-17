"""Discretizacion, modelos de entrada y diagnosticos estructurales de S2.

La planta que usa el estimador se residualiza *antes* de ``c2d``.  La planta
completa solo se discretiza en :func:`sampling_zeros` para estudiar los ceros
creados por el muestreo; esa ruta diagnostica el problema y no se usa para
filtrar datos.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import warnings

import numpy as np
from scipy import linalg, optimize, signal

from .models import InputModel, PlantSpec, ReductionSpec
from .plant import (
    StateSpace,
    Zpk,
    compose_plant_zpk,
    relative_degree,
    ss_freqresp,
    zpk_to_modal_ss,
)
from .reduce import (
    ReductionLog,
    prune_near_cancellations,
    reflect_rhp_zeros,
    residualize_fast_modes,
)


@dataclass(frozen=True)
class PreparedPlant:
    """Planta continua lista para discretizar, con trazabilidad de la reduccion."""

    full_zpk: Zpk
    reduced_zpk: Zpk
    continuous: StateSpace
    prune_log: ReductionLog
    rhp_log: ReductionLog
    residualize_log: ReductionLog


@dataclass(frozen=True)
class DiscretePlant:
    A: np.ndarray
    B: np.ndarray
    C: np.ndarray
    D: np.ndarray
    fs: float
    method: str
    continuous: StateSpace


@dataclass(frozen=True)
class MarkovReport:
    relative_degree: int
    delay_samples: int
    values: np.ndarray
    normalized: np.ndarray
    threshold: float


@dataclass(frozen=True)
class InputStateSpace:
    """Modelo discreto autonomo cuya salida escalar es ``u_k``."""

    A: np.ndarray
    C: np.ndarray
    Q: np.ndarray
    fs: float
    kind: str
    q_scale: float


@dataclass(frozen=True)
class AugmentedSystem:
    A: np.ndarray
    C: np.ndarray
    Q: np.ndarray
    plant_order: int
    input_model: InputStateSpace


@dataclass(frozen=True)
class ObservabilityReport:
    observable_at_one: bool
    pbh_rank: int
    order: int
    pbh_singular_values: np.ndarray
    gramian_singular_values: np.ndarray
    weak_direction: np.ndarray
    tolerance: float


@dataclass(frozen=True)
class SamplingZeroRow:
    value: complex
    source: str
    stability: str
    matched_continuous_zero: Optional[complex] = None
    match_error: Optional[float] = None


@dataclass(frozen=True)
class SamplingZeroReport:
    fs: float
    relative_degree: int
    rows: tuple[SamplingZeroRow, ...]

    @property
    def unstable_sampling_zeros(self) -> tuple[SamplingZeroRow, ...]:
        return tuple(
            row for row in self.rows
            if row.source == "sampling" and row.stability == "unstable"
        )


def prepare_plant(
    spec: PlantSpec,
    fs: float,
    *,
    reduction: Optional[ReductionSpec] = None,
    residualize: bool = True,
    reflect_rhp: bool = False,
) -> PreparedPlant:
    """Compone, poda y residualiza la planta para una ``fs`` concreta."""

    reduction = reduction or ReductionSpec()
    full_zpk = compose_plant_zpk(spec)
    z, p, k = full_zpk
    empty = ReductionLog()

    if reduction.enabled:
        z, p, k, prune_log = prune_near_cancellations(
            z, p, k, tol_ratio=reduction.cancel_tol_ratio
        )
    else:
        prune_log = empty

    rhp_log = ReductionLog()
    if reflect_rhp:
        z, p, k, rhp_log = reflect_rhp_zeros(
            z, p, k, below_hz=reduction.prune_rhp_below_hz
        )

    reduced_zpk = (z, p, k)
    continuous = zpk_to_modal_ss(z, p, k)
    residualize_log = ReductionLog()
    if residualize and reduction.enabled:
        cutoff = reduction.residualize_above_rad_s
        if cutoff is None:
            cutoff = 0.9 * np.pi * float(fs)
        A, B, C, D, residualize_log = residualize_fast_modes(
            *continuous, cutoff_rad_s=float(cutoff)
        )
        continuous = (A, B, C, D)

    return PreparedPlant(
        full_zpk=full_zpk,
        reduced_zpk=reduced_zpk,
        continuous=continuous,
        prune_log=prune_log,
        rhp_log=rhp_log,
        residualize_log=residualize_log,
    )


def discretize_plant(
    A: np.ndarray,
    B: np.ndarray,
    C: np.ndarray,
    D: np.ndarray,
    *,
    fs: float,
    method: str = "zoh",
    anti_alias_guard: bool = True,
) -> DiscretePlant:
    """Discretiza una planta continua y rechaza modos demasiado rapidos.

    El limite de 0,8 Nyquist deja margen respecto del corte de residualizacion
    (0,9 Nyquist).  Una planta no residualizada falla ruidosamente en vez de
    producir una resonancia discreta falsa.
    """

    fs = float(fs)
    if not np.isfinite(fs) or fs <= 0:
        raise ValueError(f"fs debe ser positiva y finita, no {fs!r}")
    A = np.asarray(A, dtype=float)
    B = np.asarray(B, dtype=float)
    C = np.asarray(C, dtype=float)
    D = np.asarray(D, dtype=float)
    if anti_alias_guard and A.size:
        eig = linalg.eigvals(A)
        worst = float(np.max(np.abs(eig)))
        limit = 0.8 * np.pi * fs
        if worst > limit:
            raise ValueError(
                "planta no residualizada: "
                f"|lambda|max={worst:.6g} rad/s supera 0,8*pi*fs={limit:.6g}; "
                "residualiza los modos rapidos antes de discretizar"
            )
    Ad, Bd, Cd, Dd, _ = signal.cont2discrete(
        (A, B, C, D), 1.0 / fs, method=method
    )
    return DiscretePlant(Ad, Bd, Cd, Dd, fs, method, (A, B, C, D))


def discrete_freqresp(plant: DiscretePlant, freqs_hz: np.ndarray) -> np.ndarray:
    """Respuesta ``H(z)`` sobre el circulo unidad, sin formar polinomios."""

    f = np.asarray(freqs_hz, dtype=float)
    eye = np.eye(plant.A.shape[0])
    out = np.empty(f.size, dtype=complex)
    for i, fi in enumerate(f):
        z = np.exp(2j * np.pi * fi / plant.fs)
        state = linalg.solve(z * eye - plant.A, plant.B)
        out[i] = (plant.C @ state + plant.D)[0, 0]
    return out


def discretization_error(
    plant: DiscretePlant,
    band_hz: tuple[float, float],
    *,
    n_points: int = 800,
) -> dict[str, float | np.ndarray]:
    """Error directo entre la FRF continua y la discreta.

    Se reporta sin ocultar el retardo del ZOH.  Tambien se incluye una version
    con el ``sinc`` y el medio periodo del retenedor removidos, util para separar
    el retenedor de la distorsion/aliasing de la propia planta.
    """

    lo, hi = map(float, band_hz)
    hi = min(hi, np.nextafter(plant.fs / 2.0, 0.0))
    f = np.logspace(np.log10(lo), np.log10(hi), n_points)
    w = 2.0 * np.pi * f
    hc = ss_freqresp(*plant.continuous, w)
    hd = discrete_freqresp(plant, f)
    ratio = hd / hc
    mag_db = 20.0 * np.log10(np.maximum(np.abs(ratio), 1e-300))
    phase_deg = np.unwrap(np.angle(ratio)) * 180.0 / np.pi

    x = w / (2.0 * plant.fs)
    hold = np.sinc(x / np.pi) * np.exp(-1j * x)
    deembedded = ratio / hold
    deemb_mag_db = 20.0 * np.log10(np.maximum(np.abs(deembedded), 1e-300))
    deemb_phase_deg = np.unwrap(np.angle(deembedded)) * 180.0 / np.pi
    return {
        "freqs_hz": f,
        "mag_db": mag_db,
        "phase_deg": phase_deg,
        "deembedded_mag_db": deemb_mag_db,
        "deembedded_phase_deg": deemb_phase_deg,
        "max_abs_db": float(np.max(np.abs(mag_db))),
        "max_abs_deg": float(np.max(np.abs(phase_deg))),
        "max_abs_deembedded_db": float(np.max(np.abs(deemb_mag_db))),
        "max_abs_deembedded_deg": float(np.max(np.abs(deemb_phase_deg))),
    }


def markov_parameters(
    A: np.ndarray,
    B: np.ndarray,
    C: np.ndarray,
    D: np.ndarray,
    *,
    max_order: int = 7,
    threshold: float = 1e-8,
) -> MarkovReport:
    """TEST 1 sobre matrices continuas, con umbral relativo a la escala modal."""

    A = np.asarray(A, dtype=float)
    B = np.asarray(B, dtype=float)
    C = np.asarray(C, dtype=float)
    D = np.asarray(D, dtype=float)
    values = np.empty(max_order + 1)
    normalized = np.empty_like(values)
    norm_a = max(float(linalg.norm(A, 2)), np.finfo(float).tiny)
    norm_b = max(float(linalg.norm(B, 2)), np.finfo(float).tiny)
    norm_c = max(float(linalg.norm(C, 2)), np.finfo(float).tiny)
    power = np.eye(A.shape[0])
    for i in range(max_order + 1):
        values[i] = float((C @ power @ B)[0, 0])
        normalized[i] = abs(values[i]) / (norm_c * (norm_a**i) * norm_b)
        power = power @ A

    d_scale = max(float(linalg.norm(D, 2)), 0.0)
    if d_scale > threshold:
        degree = 0
    else:
        indices = np.flatnonzero(normalized > threshold)
        degree = int(indices[0] + 1) if indices.size else max_order + 2
    return MarkovReport(degree, degree, values, normalized, threshold)


def _van_loan_q(
    A: np.ndarray, L: np.ndarray, Qc: np.ndarray, dt: float
) -> tuple[np.ndarray, np.ndarray]:
    """Discretizacion exacta de ``dx=A x dt + L dw`` por Van Loan."""

    A = np.asarray(A, dtype=float)
    L = np.asarray(L, dtype=float)
    Qc = np.asarray(Qc, dtype=float)
    n = A.shape[0]
    diffusion = L @ Qc @ L.T
    block = np.block(
        [[A, diffusion], [np.zeros_like(A), -A.T]]
    ) * float(dt)
    exp_block = linalg.expm(block)
    Ad = exp_block[:n, :n]
    Qd = exp_block[:n, n:] @ Ad.T
    Qd = 0.5 * (Qd + Qd.T)
    return Ad, Qd


def build_input_model(spec: InputModel, *, fs: float) -> InputStateSpace:
    """Construye los cuatro priors de entrada con densidad continua invariante."""

    fs = float(fs)
    dt = 1.0 / fs
    q = float(spec.q_scale)
    if q < 0 or not np.isfinite(q):
        raise ValueError("q_scale debe ser finito y no negativo")

    if spec.kind == "random_walk":
        Ac = np.array([[0.0]])
        C = np.array([[1.0]])
        L = np.array([[1.0]])
        Qc = np.array([[q]])
    elif spec.kind == "leaky_rw":
        rate = 2.0 * np.pi * float(spec.leak_hz)
        if rate <= 0:
            raise ValueError("leak_hz debe ser positivo")
        Ac = np.array([[-rate]])
        C = np.array([[1.0]])
        L = np.array([[1.0]])
        Qc = np.array([[q]])
    elif spec.kind == "wiener2":
        Ac = np.array([[0.0, 1.0], [0.0, 0.0]])
        C = np.array([[1.0, 0.0]])
        L = np.array([[0.0], [1.0]])
        Qc = np.array([[q]])
    elif spec.kind == "ou_band":
        if not spec.band_hz or spec.band_hz[0] <= 0 or spec.band_hz[1] <= spec.band_hz[0]:
            raise ValueError("ou_band requiere band_hz=(f_low, f_high) valida")
        low, high = map(float, spec.band_hz)
        center = np.sqrt(low * high)
        width = high - low
        decay = np.pi * width
        omega = 2.0 * np.pi * center
        Ac = np.array([[-decay, -omega], [omega, -decay]])
        C = np.array([[1.0, 0.0]])
        L = np.eye(2)
        Qc = q * np.eye(2)
    else:
        raise ValueError(f"modelo de entrada desconocido: {spec.kind!r}")

    Ad, Qd = _van_loan_q(Ac, L, Qc, dt)
    return InputStateSpace(Ad, C, Qd, fs, spec.kind, q)


def augment_with_input_model(
    plant: DiscretePlant,
    input_model: InputStateSpace,
    *,
    q_plant_scale: float = 0.0,
) -> AugmentedSystem:
    """Aumenta ``[x_planta, x_entrada]`` sin asumir feedthrough nulo."""

    if abs(plant.fs - input_model.fs) > 1e-12 * plant.fs:
        raise ValueError("planta y modelo de entrada tienen fs distintas")
    n = plant.A.shape[0]
    m = input_model.A.shape[0]
    A = np.block(
        [
            [plant.A, plant.B @ input_model.C],
            [np.zeros((m, n)), input_model.A],
        ]
    )
    C = np.hstack([plant.C, plant.D @ input_model.C])
    q_plant = float(q_plant_scale) * np.eye(n) / plant.fs
    Q = linalg.block_diag(q_plant, input_model.Q)
    return AugmentedSystem(A, C, Q, n, input_model)


def check_observability(
    A: np.ndarray,
    C: np.ndarray,
    *,
    z: complex = 1.0,
    tolerance: float = 1e-9,
    horizon: Optional[int] = None,
) -> ObservabilityReport:
    """TEST 2: PBH equilibrado en z=1 y espectro del gramiano finito."""

    A = np.asarray(A, dtype=float)
    C = np.asarray(C, dtype=float)
    n = A.shape[0]
    pbh = np.vstack([A - z * np.eye(n), C])
    col_scale = np.maximum(linalg.norm(pbh, axis=0), np.finfo(float).tiny)
    pbh_eq = pbh / col_scale
    _, s_pbh, vh = linalg.svd(pbh_eq, full_matrices=False)
    rank = int(np.sum(s_pbh > tolerance * s_pbh[0]))

    horizon = horizon or max(100, 10 * n)
    rows = []
    power = np.eye(n)
    for _ in range(horizon):
        rows.append(C @ power)
        power = power @ A
    obs = np.vstack(rows)
    obs_scale = np.maximum(linalg.norm(obs, axis=0), np.finfo(float).tiny)
    obs_eq = obs / obs_scale
    s_obs = linalg.svdvals(obs_eq)
    gramian_s = s_obs**2
    weak = vh[-1] / col_scale
    weak /= max(linalg.norm(weak), np.finfo(float).tiny)
    return ObservabilityReport(
        observable_at_one=rank == n,
        pbh_rank=rank,
        order=n,
        pbh_singular_values=s_pbh,
        gramian_singular_values=gramian_s,
        weak_direction=weak,
        tolerance=tolerance,
    )


def _invariant_zeros(A, B, C, D) -> np.ndarray:
    """Ceros SISO via pencil de Rosenbrock, sin coeficientes polinomiales."""

    n = A.shape[0]
    M = np.block([[A, B], [C, D]])
    N = np.block(
        [
            [np.eye(n), np.zeros((n, 1))],
            [np.zeros((1, n + 1))],
        ]
    )
    values = linalg.eigvals(M, N)
    return values[np.isfinite(values)]


def _stability(value: complex, tol: float = 1e-6) -> str:
    radius = abs(value)
    if radius > 1.0 + tol:
        return "unstable"
    if radius < 1.0 - tol:
        return "stable"
    return "marginal"


def sampling_zeros(spec: PlantSpec, *, fs: float) -> SamplingZeroReport:
    """TEST 3: separa ceros intrinsecos aproximados y ceros de muestreo.

    Los ``n_z`` ceros discretos mas cercanos (asignacion global) a
    ``exp(z_c T)`` se etiquetan como intrinsecos.  Los ``r-1`` restantes son los
    ceros de muestreo. La planta completa se usa deliberadamente en este
    diagnostico para no borrar el fenomeno mediante residualizacion.
    """

    zc, pc, kc = compose_plant_zpk(spec)
    A, B, C, D = zpk_to_modal_ss(zc, pc, kc)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        Ad, Bd, Cd, Dd, _ = signal.cont2discrete(
            (A, B, C, D), 1.0 / float(fs), method="zoh"
        )
    zd = _invariant_zeros(Ad, Bd, Cd, Dd)
    targets = np.exp(np.asarray(zc) / float(fs))
    cost = np.abs(targets[:, None] - zd[None, :])
    row_idx, col_idx = optimize.linear_sum_assignment(cost)
    matched = {int(j): (complex(zc[i]), float(cost[i, j])) for i, j in zip(row_idx, col_idx)}

    rows = []
    for j, value in enumerate(zd):
        if j in matched:
            continuous_zero, error = matched[j]
            rows.append(
                SamplingZeroRow(
                    complex(value), "intrinsic", _stability(value),
                    continuous_zero, error,
                )
            )
        else:
            rows.append(SamplingZeroRow(complex(value), "sampling", _stability(value)))
    rows.sort(key=lambda row: (row.source, abs(row.value), row.value.real))
    return SamplingZeroReport(float(fs), relative_degree(zc, pc), tuple(rows))


__all__ = [
    "AugmentedSystem",
    "DiscretePlant",
    "InputStateSpace",
    "MarkovReport",
    "ObservabilityReport",
    "PreparedPlant",
    "SamplingZeroReport",
    "SamplingZeroRow",
    "augment_with_input_model",
    "build_input_model",
    "check_observability",
    "discrete_freqresp",
    "discretization_error",
    "discretize_plant",
    "markov_parameters",
    "prepare_plant",
    "sampling_zeros",
]
