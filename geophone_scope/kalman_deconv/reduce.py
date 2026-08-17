"""Poda, residualizacion y verificacion de la respuesta en frecuencia.

Tres operaciones, y ninguna se hace en silencio: las tres devuelven el log de lo
que tocaron, porque un modelo reducido sin registro de que se le saco no es
auditable.

- ``prune_near_cancellations``: el AFE medido de orden 11 tiene dos pares
  polo-cero que cuasi-cancelan (258 Hz y 0,26 Hz). Son artefactos del ajuste de
  orden, no fisica; el orden efectivo es ~7.
- ``reflect_rhp_zeros``: el cero en +0,0605 rad/s (0,0096 Hz) esta tres decadas
  bajo la banda util y veinte veces bajo el limite de validez de la
  identificacion. Se **refleja** al semiplano izquierdo (no se borra: borrarlo
  equivale a quitar un derivador y desvia 74 dB). Esta **apagado por defecto**,
  porque el cero RHP es el objeto del estudio de fase no minima.
- ``residualize_fast_modes``: perturbacion singular sobre los modos por encima
  de Nyquist. Preserva la ganancia DC de forma exacta, a diferencia del
  truncamiento plano. **No se usa truncamiento balanceado**: los gramianos sobre
  cinco decadas son ellos mismos mal condicionados.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
from scipy import linalg

from .plant import ss_freqresp, zpk_freqresp


@dataclass
class FrfCheck:
    """Resultado de comparar dos modelos en frecuencia."""

    band_hz: tuple[float, float]
    freqs_hz: np.ndarray
    max_abs_db: float
    rms_db: float
    max_abs_deg: float
    rms_deg: float
    tol_db: float
    tol_deg: float
    passed: bool
    label: str = ""
    # Offset constante de ganancia que se quito antes de medir, si se pidio.
    # No es un error de forma: es una diferencia de normalizacion.
    gain_offset_db: float = 0.0

    def summary(self) -> str:
        mark = "PASS" if self.passed else "FALLA"
        offset = (
            f"  [offset {self.gain_offset_db:+.4f} dB]"
            if abs(self.gain_offset_db) > 1e-9
            else ""
        )
        return (
            f"{mark:5s} {self.label:38s} "
            f"{self.band_hz[0]:8.3f}-{self.band_hz[1]:<8.1f} Hz  "
            f"mag max {self.max_abs_db:7.4f} dB (tol {self.tol_db:.2f})  "
            f"rms {self.rms_db:7.4f}  "
            f"fase max {self.max_abs_deg:7.3f} deg (tol {self.tol_deg:.2f})  "
            f"rms {self.rms_deg:7.3f}{offset}"
        )


@dataclass
class ReductionLog:
    """Que se le saco al modelo y por que."""

    pruned_pairs: list[dict[str, Any]] = field(default_factory=list)
    pruned_rhp: list[dict[str, Any]] = field(default_factory=list)
    residualized: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        lines = []
        for item in self.pruned_pairs:
            lines.append(
                f"  cuasi-cancelacion podada: polo {item['pole']:.6g} <-> "
                f"cero {item['zero']:.6g}  ({item['f_hz']:.4g} Hz, "
                f"delta relativo {item['rel']:.2e})"
            )
        for item in self.pruned_rhp:
            lines.append(
                f"  cero RHP reflejado: {item['zero']:.6g} -> "
                f"{item['reflected_to']:.6g}  ({item['f_hz']:.4g} Hz)"
            )
        for item in self.residualized:
            lines.append(
                f"  modo residualizado: |lambda| = {item['abs']:.6g} rad/s "
                f"({item['f_hz']:.4g} Hz)"
            )
        for msg in self.warnings:
            lines.append(f"  AVISO: {msg}")
        return "\n".join(lines) if lines else "  (no se poda ni residualiza nada)"


def _match_gain_at(
    z_old, p_old, k_old, z_new, p_new, *, w_ref: float
) -> float:
    """Ganancia nueva que preserva |H| en una frecuencia de referencia.

    Podar un par polo-cero cambia la ganancia; se reajusta en el centro de la
    banda util para que la poda sea transparente donde importa.
    """
    h_old = zpk_freqresp(z_old, p_old, k_old, np.array([w_ref]))[0]
    h_unit = zpk_freqresp(z_new, p_new, 1.0, np.array([w_ref]))[0]
    if abs(h_unit) < 1e-300:
        return float(k_old)
    return float(np.real(h_old / h_unit))


def prune_near_cancellations(
    z: np.ndarray,
    p: np.ndarray,
    k: float,
    *,
    tol_ratio: float = 0.05,
    ref_hz: float = 25.0,
) -> tuple[np.ndarray, np.ndarray, float, ReductionLog]:
    """Elimina pares (cero, polo) cuya distancia relativa es menor que ``tol_ratio``.

    Los conjugados se podan juntos: si se podara solo uno el modelo dejaria de
    tener coeficientes reales.
    """
    z = list(np.asarray(z, dtype=complex))
    p = list(np.asarray(p, dtype=complex))
    z_orig, p_orig, k_orig = np.array(z), np.array(p), float(k)
    log = ReductionLog()

    changed = True
    while changed:
        changed = False
        for pi_idx, pole in enumerate(p):
            if not z:
                break
            zi_idx = min(range(len(z)), key=lambda i: abs(z[i] - pole))
            zero = z[zi_idx]
            scale = max(abs(pole), 1e-300)
            rel = abs(zero - pole) / scale
            if rel >= tol_ratio:
                continue
            log.pruned_pairs.append(
                {
                    "pole": pole,
                    "zero": zero,
                    "rel": float(rel),
                    "f_hz": float(abs(pole) / (2.0 * np.pi)),
                }
            )
            p.pop(pi_idx)
            z.pop(zi_idx)
            # Si el polo era complejo, su conjugado tambien cuasi-cancela: se
            # poda en la vuelta siguiente del while.
            changed = True
            break

    z_new = np.array(z, dtype=complex)
    p_new = np.array(p, dtype=complex)
    k_new = _match_gain_at(
        z_orig, p_orig, k_orig, z_new, p_new, w_ref=2.0 * np.pi * ref_hz
    )
    return z_new, p_new, k_new, log


def reflect_rhp_zeros(
    z: np.ndarray,
    p: np.ndarray,
    k: float,
    *,
    below_hz: Optional[float] = 0.211,
) -> tuple[np.ndarray, np.ndarray, float, ReductionLog]:
    """Refleja al semiplano izquierdo los ceros RHP por debajo de ``below_hz``.

    **Refleja, no borra**, y la diferencia importa. Borrar el cero de
    ``+0,0605 rad/s`` (0,0096 Hz) equivale a quitar un derivador: en la banda
    util ``|s - a| ~ |s|``, asi que el modelo pierde 20 dB/decada de pendiente y
    la respuesta se va 74 dB en 10-50 Hz. Ninguna constante de ganancia arregla
    eso, porque no es un cambio de escala sino de forma. (Verificado: la primera
    version de esta funcion borraba, y fallaba exactamente asi.)

    Reflejar ``a -> -a`` mantiene ``|H|`` **exacto en toda frecuencia** — el
    factor ``(s-a)/(s+a)`` es pasa-todo — y solo cambia la fase. Con a = 0,0605
    rad/s ese corrimiento vale ``-2a/w``, o sea 0,11 grados a 10 Hz: despreciable
    donde importa. Y a cambio el modelo queda de **fase minima**, con lo cual el
    inverso causal pasa a ser estable.

    Un cero RHP **dentro** de la banda de validez es fisica y no se toca: es lo
    que obliga a usar un suavizador en vez de un filtro causal, y es objeto de
    estudio. Solo se reflejan los que caen fuera de esa banda, donde la propia
    identificacion declara que la dinamica no es observable.
    """
    log = ReductionLog()
    z = np.asarray(z, dtype=complex)
    p_arr = np.asarray(p, dtype=complex)
    if below_hz is None:
        return z, p_arr, float(k), log

    limit = 2.0 * np.pi * float(below_hz)
    z_new = z.copy()
    for i, zero in enumerate(z):
        if zero.real > 0 and abs(zero) < limit:
            z_new[i] = -zero.real + 1j * zero.imag
            log.pruned_rhp.append(
                {
                    "zero": zero,
                    "f_hz": float(abs(zero) / (2.0 * np.pi)),
                    "reflected_to": z_new[i],
                }
            )
    if not log.pruned_rhp:
        return z, p_arr, float(k), log

    # El signo de k se ajusta para que la fase no salte 180 grados: reflejar un
    # cero real negativiza su contribucion de DC.
    n_reflected = len(log.pruned_rhp)
    k_new = float(k) * ((-1.0) ** n_reflected)
    return z_new, p_arr, k_new, log


# Nombre viejo, conservado para no romper imports existentes.
prune_rhp_zeros = reflect_rhp_zeros


def residualize_fast_modes(
    A: np.ndarray,
    B: np.ndarray,
    C: np.ndarray,
    D: np.ndarray,
    *,
    cutoff_rad_s: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, ReductionLog]:
    """Perturbacion singular sobre los modos rapidos.

    Los modos con ``|lambda| > cutoff`` se toman como instantaneos: se impone
    ``xdot_f = 0`` y se resuelve ``x_f`` algebraicamente. Preserva la ganancia DC
    de forma **exacta**, que es lo que un truncamiento plano no hace.

    Motivo concreto: el AFE medido tiene un par de polos en 2728 Hz, por encima
    de Nyquist en todas las fs disponibles (1302 Hz maximo a fs = 2604). Si se
    discretiza sin sacarlo, ZOH lo aliasa a una resonancia falsa dentro de banda.
    """
    log = ReductionLog()
    A = np.asarray(A, dtype=float)
    n = A.shape[0]
    if n == 0:
        return A, B, C, D, log

    eig = linalg.eigvals(A)
    # La particion se hace por bloques diagonales: la realizacion modal ya tiene
    # cada modo en su bloque, asi que se marca el estado por el autovalor de su
    # bloque. Se usa la diagonal en bloques via la propia estructura de A.
    fast = np.zeros(n, dtype=bool)
    idx = 0
    while idx < n:
        is_pair = idx + 1 < n and abs(A[idx, idx + 1]) > 0
        block = A[idx : idx + 2, idx : idx + 2] if is_pair else A[idx : idx + 1, idx : idx + 1]
        magnitude = float(np.max(np.abs(linalg.eigvals(block))))
        size = 2 if is_pair else 1
        if magnitude > cutoff_rad_s:
            fast[idx : idx + size] = True
            log.residualized.append(
                {"abs": magnitude, "f_hz": magnitude / (2.0 * np.pi)}
            )
        idx += size

    if not fast.any():
        return A, B, C, D, log

    slow = ~fast
    if not slow.any():
        log.warnings.append(
            "todos los modos quedaron del lado rapido: no se residualiza nada"
        )
        return A, B, C, D, log

    A_ss = A[np.ix_(slow, slow)]
    A_sf = A[np.ix_(slow, fast)]
    A_fs = A[np.ix_(fast, slow)]
    A_ff = A[np.ix_(fast, fast)]
    B_s, B_f = B[slow], B[fast]
    C_s, C_f = C[:, slow], C[:, fast]

    try:
        A_ff_inv_A_fs = linalg.solve(A_ff, A_fs)
        A_ff_inv_B_f = linalg.solve(A_ff, B_f)
    except linalg.LinAlgError:
        log.warnings.append("A_ff singular: no se pudo residualizar, se deja completo")
        return A, B, C, D, log

    A_new = A_ss - A_sf @ A_ff_inv_A_fs
    B_new = B_s - A_sf @ A_ff_inv_B_f
    C_new = C_s - C_f @ A_ff_inv_A_fs
    D_new = D - C_f @ A_ff_inv_B_f
    return A_new, B_new, C_new, D_new, log


def verify_frf(
    reference,
    test,
    *,
    band_hz: tuple[float, float],
    n_points: int = 512,
    tol_db: float = 0.3,
    tol_deg: float = 2.0,
    label: str = "",
    align_phase: bool = True,
    align_gain: bool = False,
) -> FrfCheck:
    """Compara dos modelos sobre una grilla logaritmica.

    ``reference`` y ``test`` pueden ser ``(z, p, k)`` o ``(A, B, C, D)``: se
    distinguen por la cantidad de elementos.

    ``align_phase`` quita un offset **constante** de fase antes de medir (no un
    retardo). Sirve para no reprobar una realizacion por un signo global, que es
    irrelevante para el estimador.
    """
    f = np.logspace(np.log10(band_hz[0]), np.log10(band_hz[1]), n_points)
    w = 2.0 * np.pi * f

    def _resp(model):
        if len(model) == 3:
            return zpk_freqresp(model[0], model[1], model[2], w)
        return ss_freqresp(model[0], model[1], model[2], model[3], w)

    h_ref = _resp(reference)
    h_test = _resp(test)

    mag_db = 20.0 * np.log10(np.abs(h_test) / np.maximum(np.abs(h_ref), 1e-300))
    gain_offset_db = 0.0
    if align_gain:
        # Quita un factor de escala CONSTANTE antes de medir. Sirve cuando los
        # dos modelos usan normalizaciones distintas (p. ej. GEO_LP viene con la
        # constante zeta*w0 del informe y el catalogo usa G0 en V*s/m): lo que se
        # quiere comparar ahi es la FORMA, no la escala absoluta.
        gain_offset_db = float(np.median(mag_db))
        mag_db = mag_db - gain_offset_db
    phase = np.angle(h_test / h_ref, deg=True)
    phase = (phase + 180.0) % 360.0 - 180.0
    if align_phase:
        # Mediana en vez de media: robusta si unos pocos puntos saltan de rama.
        phase = phase - np.median(phase)
        phase = (phase + 180.0) % 360.0 - 180.0

    max_db = float(np.max(np.abs(mag_db)))
    rms_db = float(np.sqrt(np.mean(mag_db**2)))
    max_deg = float(np.max(np.abs(phase)))
    rms_deg = float(np.sqrt(np.mean(phase**2)))
    return FrfCheck(
        band_hz=band_hz,
        freqs_hz=f,
        max_abs_db=max_db,
        rms_db=rms_db,
        max_abs_deg=max_deg,
        rms_deg=rms_deg,
        tol_db=tol_db,
        tol_deg=tol_deg,
        passed=bool(max_db <= tol_db and max_deg <= tol_deg),
        label=label,
        gain_offset_db=gain_offset_db,
    )


__all__ = [
    "FrfCheck",
    "ReductionLog",
    "prune_near_cancellations",
    "prune_rhp_zeros",
    "reflect_rhp_zeros",
    "residualize_fast_modes",
    "verify_frf",
]
