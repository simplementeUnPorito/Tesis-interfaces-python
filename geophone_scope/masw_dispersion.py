"""MASW dispersion imaging (phase-shift method, Park et al. 1998).

Puerto a NumPy puro del mismo algoritmo que usa
`third-party/maswavespy` (ver `cy_dispersion_imaging.pyx`), sin depender de
compilar sus extensiones Cython (que requieren numpy<2 + un compilador C,
no disponibles en este entorno). Generaliza el espaciado de canales a
offsets arbitrarios en vez de asumir un paso `dx` uniforme.

Referencia
----------
Park, C.B., Miller, R.D. and Xia J. (1998). Imaging dispersion curves of
surface waves on multi-channel record. SEG technical program expanded
abstracts 1998, New Orleans, LA, pp. 1377-1380. doi:10.1190/1.1820161.
"""

from __future__ import annotations

import numpy as np


def phase_shift_dispersion_image(
    u: np.ndarray,
    offsets_m: np.ndarray,
    fs: float,
    c_min: float,
    c_max: float,
    c_step: float,
    f_max: float | None = None,
    f_min: float = 0.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Transforma un registro multicanal al dominio frecuencia-velocidad de
    fase (metodo phase-shift).

    Parameters
    ----------
    u : ndarray (n_time, n_channels)
        Registro multicanal (columnas = canales, ordenados por offset
        creciente respecto a la fuente; filas = tiempo).
    offsets_m : ndarray (n_channels,)
        Offset de cada canal respecto a la fuente [m]. No necesita ser
        uniforme.
    fs : float
        Frecuencia de muestreo [Hz].
    c_min, c_max, c_step : float
        Rango y paso de la velocidad de fase de prueba [m/s].
    f_max : float, opcional
        Frecuencia maxima a evaluar [Hz]. Default: fs/2 (Nyquist).
    f_min : float, opcional
        Frecuencia minima a evaluar [Hz]. Util para recortar la imagen a
        la banda util del sensor (p. ej. geofono de 1-200 Hz). Default: 0.

    Returns
    -------
    f : ndarray (n_freq,)
        Vector de frecuencias [Hz], desde 0 hasta f_max.
    c : ndarray (n_c,)
        Vector de velocidades de fase de prueba [m/s].
    A : ndarray (n_freq, n_c)
        Amplitud normalizada del slant-stack para cada par (f, c).
    """
    u = np.asarray(u, dtype=np.float64)
    if u.ndim != 2:
        raise ValueError("u debe ser 2D (n_time, n_channels)")
    n_time, n_channels = u.shape
    offsets = np.asarray(offsets_m, dtype=np.float64)
    if offsets.size != n_channels:
        raise ValueError("offsets_m debe tener un valor por canal")
    if fs <= 0:
        raise ValueError("fs debe ser > 0")
    if c_step <= 0:
        raise ValueError("c_step debe ser > 0")

    nyquist = fs / 2.0
    f_max = float(f_max) if f_max is not None else nyquist
    f_max = min(f_max, nyquist)
    f_min = max(0.0, float(f_min))

    U = np.fft.fft(u, axis=0)
    freqs_full = np.fft.fftfreq(n_time, d=1.0 / fs)
    mask = (freqs_full >= f_min) & (freqs_full <= f_max)
    idx = np.where(mask)[0]
    if idx.size == 0:
        raise ValueError("No hay bines de frecuencia en el rango pedido")

    f = freqs_full[idx]
    omega = 2.0 * np.pi * f
    c = np.arange(c_min, c_max + c_step, c_step, dtype=np.float64)
    if c.size == 0:
        raise ValueError("Rango de velocidades vacio")

    # Fase (angulo) de cada canal, por bin de frecuencia: E[m, q] = exp(i*arg(U))
    E = np.exp(1j * np.angle(U[idx, :]))  # (n_freq, n_channels)

    A = np.empty((f.size, c.size), dtype=np.float64)
    n_inv = 1.0 / n_channels
    for m, om in enumerate(omega):
        delta = om / c  # (n_c,)
        # phase[k, q] = exp(i * delta[k] * offsets[q])
        phase = np.exp(1j * np.outer(delta, offsets))  # (n_c, n_channels)
        temp = phase @ E[m, :]  # (n_c,)
        A[m, :] = np.abs(temp) * n_inv

    return f, c, A


def _rolling_median(x: np.ndarray, window: int) -> np.ndarray:
    n = x.size
    half = window // 2
    out = np.empty(n)
    for i in range(n):
        a = max(0, i - half)
        b = min(n, i + half + 1)
        out[i] = np.median(x[a:b])
    return out


def auto_extract_dispersion_curve(
    f: np.ndarray,
    c: np.ndarray,
    A: np.ndarray,
    offsets_m: np.ndarray,
    max_points: int = 45,
) -> tuple[np.ndarray, np.ndarray]:
    """Extrae automaticamente la curva de dispersion (modo fundamental) de
    una imagen f-c, con filtros de calidad para no requerir picking manual.

    Criterios:
    - por frecuencia se toma la velocidad de maxima amplitud (la cresta);
    - se exige amplitud normalizada alta (umbral adaptativo 0.8 -> 0.5
      hasta juntar suficientes puntos);
    - limites fisicos del tendido: lambda <= 1.5 * apertura (resolucion en
      profundidad) y lambda >= 2 * espaciado (aliasing espacial);
    - rechazo de outliers contra una mediana movil (la cresta debe ser
      suave) y suavizado final leve.

    Devuelve (freqs, c_obs) listos para invertir. Lanza ValueError si no
    hay cresta suficientemente coherente.
    """
    f = np.asarray(f, dtype=np.float64)
    c = np.asarray(c, dtype=np.float64)
    A = np.asarray(A, dtype=np.float64)
    offsets = np.sort(np.asarray(offsets_m, dtype=np.float64))
    aperture = float(offsets[-1] - offsets[0]) if offsets.size > 1 else 1.0
    dx = float(np.median(np.diff(offsets))) if offsets.size > 1 else 1.0

    peak_idx = np.argmax(A, axis=1)
    peak_c = c[peak_idx]
    peak_a = A[np.arange(f.size), peak_idx]
    valid_f = f > 0
    lam = np.divide(peak_c, f, out=np.full_like(peak_c, np.inf), where=valid_f)
    base = valid_f & (lam <= 1.5 * aperture) & (lam >= 2.0 * dx)

    mask = base
    for threshold in (0.8, 0.7, 0.6, 0.5):
        mask = base & (peak_a >= threshold)
        if mask.sum() >= 10:
            break
    if mask.sum() < 5:
        raise ValueError(
            "No se encontro una cresta suficientemente coherente para el auto-pick "
            "(amplitud maxima por frecuencia demasiado baja dentro de los limites del tendido)"
        )

    ff = f[mask]
    cc = peak_c[mask]
    med = _rolling_median(cc, 5)
    keep = np.abs(cc - med) <= np.maximum(0.15 * med, 15.0)
    ff = ff[keep]
    cc = _rolling_median(cc[keep], 3)
    if ff.size < 5:
        raise ValueError("Quedaron muy pocos puntos coherentes despues del filtrado de outliers")
    if ff.size > max_points:
        idx = np.round(np.linspace(0, ff.size - 1, max_points)).astype(int)
        ff = ff[idx]
        cc = cc[idx]
    return ff, cc


def common_finite_window(
    common_time: np.ndarray,
    matrix: np.ndarray,
    t_min: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Recorta al tramo donde TODAS las trazas tienen datos reales (sin NaN)
    a partir de `t_min` (por defecto el instante del golpe, t=0).

    El waterfall se arma con NaN mas alla del final real de cada traza (ver
    `field_review_data.build_waterfall_matrix`) para no inventar una meseta
    en 0; el MASW en cambio necesita un registro multicanal sincronico sin
    huecos, asi que se usa el solapamiento comun entre todos los canales.
    """
    common_time = np.asarray(common_time, dtype=np.float64)
    matrix = np.asarray(matrix, dtype=np.float64)
    start_idx = int(np.searchsorted(common_time, t_min))
    finite = np.isfinite(matrix[:, start_idx:])
    if finite.size == 0 or not finite.any():
        return common_time[start_idx:start_idx], matrix[:, start_idx:start_idx]
    all_finite = finite.all(axis=0)
    if not all_finite.any():
        return common_time[start_idx:start_idx], matrix[:, start_idx:start_idx]
    # Primer indice (relativo a start_idx) donde deja de haber dato en todos
    # los canales; como el NaN solo aparece al final de cada traza, el
    # solapamiento comun es un tramo contiguo desde start_idx.
    first_gap = np.argmin(all_finite) if not all_finite.all() else all_finite.size
    end_idx = start_idx + int(first_gap)
    return common_time[start_idx:end_idx], matrix[:, start_idx:end_idx]
