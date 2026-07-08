"""Inversion conjunta multi-modo de curvas de dispersion (Rayleigh) con
evodcinv + disba.

Se eligio esta combinacion (ver comparacion con ADsurf y Geopsy/Dinver) por ser
Python puro, instalable con pip, y con soporte real de modos superiores:

 - ``disba``   : forward model de dispersion de ondas superficiales (Rayleigh y
   Love, fase y grupo) acelerado con Numba, port del CPS de Herrmann. Calcula la
   curva teorica de cualquier modo (0=fundamental, 1=primer modo superior, ...).
 - ``evodcinv``: inversion de las curvas por algoritmos evolutivos (CPSO por
   defecto) usando disba como forward. Acepta varias curvas (una por modo) y las
   ajusta en conjunto contra un unico perfil de capas.

Ambas son opcionales: si no estan instaladas, ``available()`` devuelve False y la
UI cae al flujo Monte Carlo de un solo modo (masw_inversion.py). Para habilitarlo:

    pip install disba evodcinv

Nota de compatibilidad: evodcinv 2.2.x usa ``np.Inf`` (removido en NumPy 2.0);
``_patch_numpy`` re-agrega los alias viejos antes de importarlo.

Unidades: disba/evodcinv trabajan en km, km/s, s. Aca se recibe todo en Hz y
m/s (como el resto de la app) y se convierte internamente.
"""

from __future__ import annotations

import numpy as np


def _patch_numpy() -> None:
    """Re-agrega alias de NumPy < 2.0 que evodcinv 2.2.x todavia usa."""
    for name, target in (
        ("Inf", "inf"), ("NaN", "nan"), ("Infinity", "inf"),
        ("NAN", "nan"), ("infty", "inf"), ("PINF", "inf"), ("NINF", "inf"),
    ):
        if not hasattr(np, name):
            try:
                setattr(np, name, getattr(np, target))
            except Exception:
                pass


def available() -> bool:
    """True si disba y evodcinv se pueden importar."""
    try:
        _patch_numpy()
        import disba  # noqa: F401
        import evodcinv  # noqa: F401
        return True
    except Exception:
        return False


def multimodal_inversion(
    curves_by_mode: dict[int, tuple[np.ndarray, np.ndarray]],
    *,
    n_layers: int = 3,
    maxiter: int = 100,
    popsize: int = 20,
    seed: int | None = None,
    vs_min_ms: float | None = None,
    vs_max_ms: float | None = None,
    thickness_max_m: float | None = None,
    min_points: int = 3,
) -> dict:
    """Invierte en conjunto las curvas de dispersion de varios modos.

    Parameters
    ----------
    curves_by_mode
        {modo: (frecuencias_Hz, velocidades_m_s)}. Modo 0 = fundamental.
    n_layers
        Cantidad de capas (incluye el semiespacio).
    maxiter, popsize, seed
        Parametros del optimizador CPSO de evodcinv.
    vs_min_ms, vs_max_ms, thickness_max_m
        Limites de busqueda; si son None se estiman de las curvas.

    Returns
    -------
    dict con:
      beta (m/s por capa), h (m, espesores sin el semiespacio), misfit,
      modes (lista de modos usados), theoretical {modo: (freq_Hz, c_m_s)},
      y los limites de busqueda efectivamente usados.
    """
    _patch_numpy()
    from evodcinv import Curve, EarthModel, Layer
    from disba import PhaseDispersion

    modes = sorted(
        m for m, (f, c) in curves_by_mode.items()
        if f is not None and np.asarray(f).size >= int(min_points)
    )
    if not modes:
        raise ValueError(
            f"Se necesitan al menos {min_points} puntos en algun modo para invertir."
        )

    all_c = np.concatenate([np.asarray(curves_by_mode[m][1], dtype=float) for m in modes])
    all_f = np.concatenate([np.asarray(curves_by_mode[m][0], dtype=float) for m in modes])
    all_f = all_f[all_f > 0]
    if all_c.size == 0 or all_f.size == 0:
        raise ValueError("Curvas vacias o con frecuencias no positivas.")

    if vs_min_ms is None:
        vs_min_ms = 0.5 * float(all_c.min())
    if vs_max_ms is None:
        vs_max_ms = 1.3 * float(all_c.max())
    if thickness_max_m is None:
        lam_max = float(all_c.max()) / max(float(all_f.min()), 1e-6)
        thickness_max_m = max(0.5 * lam_max, 5.0)

    model = EarthModel()
    # Espesor maximo por capa: reparte el alcance total con holgura.
    layer_tmax_km = max((thickness_max_m / 1000.0) / max(n_layers - 1, 1) * 2.0, 1.0 / 1000.0)
    for _ in range(int(n_layers)):
        thickness = np.array([0.5 / 1000.0, layer_tmax_km])
        velocity_s = np.array([vs_min_ms / 1000.0, vs_max_ms / 1000.0])
        model.add(Layer(thickness, velocity_s))
    model.configure(
        optimizer="cpso",
        misfit="rmse",
        optimizer_args={"maxiter": int(maxiter), "popsize": int(popsize), "seed": seed},
    )

    curves = []
    for m in modes:
        f = np.asarray(curves_by_mode[m][0], dtype=float)
        c = np.asarray(curves_by_mode[m][1], dtype=float)
        good = f > 0
        f, c = f[good], c[good]
        period = 1.0 / f
        order = np.argsort(period)  # disba requiere periodos crecientes
        curves.append(
            Curve(period[order], (c / 1000.0)[order], mode=int(m), wave="rayleigh", type="phase")
        )

    result = model.invert(curves)
    best = np.asarray(result.model, dtype=float)  # (n_layers, 4): thick, vp, vs, rho [km]
    thick_km = best[:, 0]
    vs_kms = best[:, 2]
    beta = vs_kms * 1000.0
    h = thick_km[:-1] * 1000.0  # el ultimo espesor es el semiespacio (placeholder)

    pd = PhaseDispersion(*best.T)
    theoretical: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for m in modes:
        f = np.asarray(curves_by_mode[m][0], dtype=float)
        f = np.sort(f[f > 0])
        period = np.sort(1.0 / f)
        try:
            cp = pd(period, mode=int(m), wave="rayleigh")
            theoretical[int(m)] = (1.0 / np.asarray(cp.period), np.asarray(cp.velocity) * 1000.0)
        except Exception:
            theoretical[int(m)] = (np.array([]), np.array([]))

    return {
        "beta": beta,
        "h": h,
        "misfit": float(result.misfit),
        "modes": modes,
        "theoretical": theoretical,
        "vs_min_ms": float(vs_min_ms),
        "vs_max_ms": float(vs_max_ms),
        "thickness_max_m": float(thickness_max_m),
        "n_layers": int(n_layers),
    }
