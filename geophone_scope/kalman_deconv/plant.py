"""Construccion de la planta: GEO x CONDITIONER -> espacio de estados continuo.

Ruta numerica, y el porque de cada paso:

1. zpk desde el catalogo. **Nunca se forman polinomios en runtime**: el AFE
   medido tiene coeficientes con 25 decadas de rango dinamico y ``tf2ss`` sobre
   la forma companion da una matriz inutilizable.
2. Composicion en zpk (concatenar ceros y polos, multiplicar ganancias).
3. Realizacion **modal real bloque-diagonal**: 1x1 por polo real, 2x2 por par
   complejo. ``cond(A)`` pasa de ~1e28 a ~|p_max|/|p_min|.
4. Escalado diagonal, para que la covarianza del KF no pierda simetria por
   diferencias de magnitud entre estados de 0,05 Hz y de 291 Hz.

La poda de cuasi-cancelaciones y la residualizacion viven en ``reduce.py``.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
from scipy import linalg

from .models import ConditionerSpec, GeophoneSpec, PlantSpec

Zpk = tuple[np.ndarray, np.ndarray, float]
StateSpace = tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]


def second_order_poles(zeta: float, w0: float) -> np.ndarray:
    """Raices de s^2 + 2*zeta*w0*s + w0^2, en forma cerrada.

    Se resuelve a mano y no con ``np.roots`` por dos motivos: es exacto, y deja
    el modulo libre de llamadas a ``np.roots`` en runtime, que es el criterio de
    higiene numerica de TAREAS_KALMAN.md (los polinomios de orden 11 y 13 del
    informe solo se factorizan una vez, en ``build_catalog``).

    Con zeta >> 1 (el caso del compensador, zeta1 = 937) las dos raices reales
    estan muy separadas: la resta directa ``-zeta*w0 + w0*sqrt(zeta^2-1)`` sufre
    cancelacion catastrofica en la raiz lenta, asi que esa se obtiene por el
    producto de las raices (w0^2), que es estable.
    """
    zeta = float(zeta)
    w0 = float(w0)
    if zeta < 1.0:
        real = -zeta * w0
        imag = w0 * np.sqrt(1.0 - zeta * zeta)
        return np.array([complex(real, imag), complex(real, -imag)])
    disc = np.sqrt(zeta * zeta - 1.0)
    fast = -w0 * (zeta + disc)  # raiz grande, sin cancelacion
    slow = (w0 * w0) / fast     # producto de raices = w0^2
    return np.array([complex(fast, 0.0), complex(slow, 0.0)])


def geophone_zpk(spec: GeophoneSpec) -> Zpk:
    """Modelo del transductor en zpk.

    Forma "acceleration" (la que usa este proyecto, y la de Ma et al. 2023):

        H_a(s) = -G * s / (s^2 + 2*zeta*w0*s + w0^2)      entrada = aceleracion

    Forma "velocity", que se deja disponible pero no se usa:

        H_v(s) = -G * s^2 / (s^2 + 2*zeta*w0*s + w0^2)    entrada = velocidad

    El signo negativo es del modelo publicado y se conserva; el pipeline de campo
    aplica ademas su propia convencion de polaridad (geo no invertido, hammer
    invertido) en ``field_review_data``, que es independiente de esto.
    """
    w0 = spec.w0
    poles = second_order_poles(spec.zeta, w0)
    n_zeros_at_origin = 1 if spec.form == "acceleration" else 2
    zeros = np.zeros(n_zeros_at_origin, dtype=complex)
    gain = spec.gain_override if spec.gain_override is not None else spec.g0_v_s_per_m
    return zeros, poles, -float(gain)


def conditioner_zpk(spec: ConditionerSpec) -> Zpk:
    """Modelo del acondicionamiento en zpk."""
    if spec.kind == "unity":
        return np.zeros(0, dtype=complex), np.zeros(0, dtype=complex), float(spec.gain)

    if spec.kind == "zpk":
        return (
            np.asarray(spec.zeros, dtype=complex),
            np.asarray(spec.poles, dtype=complex),
            float(spec.gain),
        )

    if spec.kind == "compensator":
        # H(s) = k_dc * (s^2 + 2*z0*w0*s + w0^2) / (s^2 + 2*z1*w0*s + w0^2)
        #        * 1/(1 + s*tau)
        #
        # El numerador reproduce el denominador del geofono: por eso el
        # compensador CANCELA los polos del sensor por diseno, y la cascada
        # colapsa de orden 13 a orden 3. Ver HANDOFF_KALMAN.md §5.2.
        w0 = float(spec.w0_rad_s)
        zeros = second_order_poles(spec.zeta0, w0)
        poles = second_order_poles(spec.zeta1, w0)
        gain = float(spec.k_dc)
        if spec.antialias_hz:
            tau = 1.0 / (2.0 * np.pi * float(spec.antialias_hz))
            poles = np.concatenate([poles, [-1.0 / tau]])
            # 1/(1+s*tau) = (1/tau)/(s + 1/tau): monico abajo, (1/tau) a la ganancia.
            gain *= 1.0 / tau
        return zeros, poles, gain

    raise ValueError(f"kind de acondicionador desconocido: {spec.kind!r}")


def _integrator_chain(n: int) -> Zpk:
    """n integradores puros: cambia la magnitud de entrada del modelo.

    Estimar velocidad en vez de aceleracion equivale a poner un integrador
    delante de la planta. Ojo: cada integrador agrega un polo exacto en s=0 (z=1
    al discretizar) y empeora la observabilidad en DC; por eso ``displacement``
    emite warning en la capa de arriba.
    """
    return np.zeros(0, dtype=complex), np.zeros(n, dtype=complex), 1.0


def compose_plant_zpk(spec: PlantSpec) -> Zpk:
    """Cascada completa: movimiento del suelo -> volts en la entrada del ADC."""
    zeros: list[np.ndarray] = []
    poles: list[np.ndarray] = []
    gain = 1.0

    if spec.include_conditioner and spec.conditioner.includes_geophone:
        # Entrada del catalogo que YA trae el geofono adentro (caso GEO_LP). Se
        # usa solo para el chequeo cruzado de S1; combinarla con un geofono
        # duplicaria el sensor.
        if spec.include_geophone:
            raise ValueError(
                f"{spec.conditioner.id!r} ya incluye el geofono: "
                "usa include_geophone=False o elegi otro acondicionador"
            )
        z, p, k = conditioner_zpk(spec.conditioner)
        zeros.append(z)
        poles.append(p)
        gain *= k
    else:
        if spec.include_geophone:
            z, p, k = geophone_zpk(spec.geophone)
            zeros.append(z)
            poles.append(p)
            gain *= k
        if spec.include_conditioner:
            z, p, k = conditioner_zpk(spec.conditioner)
            zeros.append(z)
            poles.append(p)
            gain *= k

    n_integrators = {"acceleration": 0, "velocity": 1, "displacement": 2}[spec.estimate]
    if n_integrators:
        z, p, k = _integrator_chain(n_integrators)
        zeros.append(z)
        poles.append(p)
        gain *= k

    all_zeros = np.concatenate(zeros) if zeros else np.zeros(0, dtype=complex)
    all_poles = np.concatenate(poles) if poles else np.zeros(0, dtype=complex)
    gain *= float(spec.adc_scale_v_per_count)
    return all_zeros.astype(complex), all_poles.astype(complex), float(gain)


def relative_degree(z: Sequence[complex], p: Sequence[complex]) -> int:
    return len(p) - len(z)


def zpk_freqresp(z: np.ndarray, p: np.ndarray, k: float, w: np.ndarray) -> np.ndarray:
    """Respuesta en frecuencia evaluada como producto de factores.

    Se evalua factor por factor y no expandiendo el polinomio, justamente para no
    perder precision con polos que abarcan cinco decadas.
    """
    s = 1j * np.asarray(w, dtype=float)
    out = np.full(s.shape, complex(k))
    for zi in z:
        out = out * (s - zi)
    for pi in p:
        out = out / (s - pi)
    return out


def _pair_complex_poles(p: np.ndarray, tol: float = 1e-9) -> list[np.ndarray]:
    """Agrupa los polos en bloques: [real] o [par conjugado].

    Se ordena por parte real y luego imaginaria para que la salida sea
    determinista: dos corridas con los mismos datos tienen que dar la misma
    realizacion, si no los tests numericos no son reproducibles.
    """
    remaining = list(np.asarray(p, dtype=complex))
    remaining.sort(key=lambda v: (round(v.real, 12), round(abs(v.imag), 12), v.imag))
    blocks: list[np.ndarray] = []
    while remaining:
        current = remaining.pop(0)
        if abs(current.imag) <= tol * max(1.0, abs(current)):
            blocks.append(np.array([current.real + 0j]))
            continue
        # Buscar el conjugado mas parecido.
        target = np.conj(current)
        idx = min(range(len(remaining)), key=lambda i: abs(remaining[i] - target))
        if not remaining or abs(remaining[idx] - target) > 1e-6 * max(1.0, abs(current)):
            raise ValueError(
                f"polo complejo sin conjugado: {current!r}. El modelo debe tener "
                "coeficientes reales"
            )
        mate = remaining.pop(idx)
        blocks.append(np.array([current, mate]))
    return blocks


def zpk_to_modal_ss(
    z: np.ndarray,
    p: np.ndarray,
    k: float,
    *,
    balance: bool = True,
    fit_band_rad_s: tuple[float, float] = (2.0 * np.pi * 0.05, 2.0 * np.pi * 500.0),
    n_fit: int = 600,
) -> StateSpace:
    """(A, B, C, D) real con A bloque-diagonal, sin pasar por polinomios.

    ``A`` se arma directo de los polos (1x1 real, 2x2 [[s, w], [-w, s]] por par
    conjugado). ``B`` se fija en unos y ``C`` se ajusta por minimos cuadrados
    contra la respuesta del zpk sobre una grilla logaritmica: es equivalente a
    una expansion en fracciones simples pero mucho mas robusto cuando hay polos
    cuasi-repetidos, donde ``scipy.signal.residue`` se degrada.

    ``D`` sale del limite en alta frecuencia: es 0 salvo que el sistema sea
    bipropio (mismo numero de ceros que de polos).
    """
    p = np.asarray(p, dtype=complex)
    z = np.asarray(z, dtype=complex)
    n = p.size
    if n == 0:
        return (
            np.zeros((0, 0)),
            np.zeros((0, 1)),
            np.zeros((1, 0)),
            np.array([[float(k)]]),
        )
    if z.size > n:
        raise ValueError(
            f"sistema impropio: {z.size} ceros y {n} polos. No tiene realizacion "
            "en espacio de estados"
        )

    blocks = _pair_complex_poles(p)
    a_blocks: list[np.ndarray] = []
    b_rows: list[np.ndarray] = []
    for block in blocks:
        if block.size == 1:
            a_blocks.append(np.array([[block[0].real]]))
            b_rows.append(np.array([1.0]))
        else:
            sigma, omega = block[0].real, abs(block[0].imag)
            a_blocks.append(np.array([[sigma, omega], [-omega, sigma]]))
            b_rows.append(np.array([1.0, 0.0]))
    A = linalg.block_diag(*a_blocks)
    B = np.concatenate(b_rows).reshape(-1, 1)

    # D: termino directo. Solo no nulo si el sistema es bipropio.
    D = np.array([[float(k) if z.size == n else 0.0]])

    # Ajuste de C. Se resuelve el sistema real [Re; Im] para que C salga real.
    w = np.logspace(np.log10(fit_band_rad_s[0]), np.log10(fit_band_rad_s[1]), n_fit)
    target = zpk_freqresp(z, p, k, w) - D[0, 0]
    s = 1j * w
    # Phi[m, i] = [ (s_m I - A)^-1 B ]_i
    phi = np.empty((w.size, n), dtype=complex)
    eye = np.eye(n)
    for m, sm in enumerate(s):
        phi[m, :] = linalg.solve(sm * eye - A, B).ravel()
    # Ponderar por 1/|target| para que el ajuste sea relativo y no lo dominen las
    # frecuencias de mayor ganancia.
    weight = 1.0 / np.maximum(np.abs(target), 1e-300)
    lhs = np.vstack([(phi * weight[:, None]).real, (phi * weight[:, None]).imag])
    rhs = np.concatenate([(target * weight).real, (target * weight).imag])
    C, *_ = np.linalg.lstsq(lhs, rhs, rcond=None)
    C = C.reshape(1, -1)

    if balance and n > 1:
        # Escalado diagonal sobre [[A, B], [C, 0]]: iguala las magnitudes de los
        # estados para que P no pierda simetria en el KF.
        aug = np.block([[A, B], [C, np.zeros((1, 1))]])
        _, t = linalg.matrix_balance(aug[:n, :n], permute=False)
        t_inv = np.diag(1.0 / np.diag(t))
        A = t_inv @ A @ t
        B = t_inv @ B
        C = C @ t

    return A, B, C, D


def ss_freqresp(A, B, C, D, w: np.ndarray) -> np.ndarray:
    """Respuesta en frecuencia de una realizacion, sin formar la TF."""
    w = np.asarray(w, dtype=float)
    n = A.shape[0]
    eye = np.eye(n)
    out = np.empty(w.size, dtype=complex)
    for m, wm in enumerate(w):
        x = linalg.solve(1j * wm * eye - A, B)
        out[m] = (C @ x + D)[0, 0]
    return out


def plant_state_space(spec: PlantSpec, **kwargs) -> tuple[StateSpace, Zpk]:
    """Atajo: zpk compuesto + su realizacion modal."""
    z, p, k = compose_plant_zpk(spec)
    return zpk_to_modal_ss(z, p, k, **kwargs), (z, p, k)


__all__ = [
    "compose_plant_zpk",
    "conditioner_zpk",
    "geophone_zpk",
    "plant_state_space",
    "relative_degree",
    "ss_freqresp",
    "zpk_freqresp",
    "zpk_to_modal_ss",
]
