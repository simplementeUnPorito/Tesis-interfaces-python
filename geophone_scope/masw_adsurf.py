"""Inversion multimodo con ADsurf (automatic differentiation, PyTorch).

Vendorizado en ``third-party/ADsurf`` (git submodule de
github.com/liufeng2317/ADsurf, igual patron que ``third-party/maswavespy``).
No es un ejecutable de linea de comandos: es una libreria Python que se usa
in-proc, por eso este modulo corre la inversion directamente (a diferencia de
``masw_backends.export_curves('adsurf', ...)``, que sigue disponible para
exportar las curvas en el formato de ADsurf si alguien quiere correrlas a
mano en un notebook).

Unidades internas de ADsurf: km, km/s, s (igual que disba/evodcinv). Ac{'a}
se recibe todo en Hz y m/s como el resto de la app.

Notas de compatibilidad / bugs de la libreria vendorizada:
 - ``ADsurf._model.iter_inversion._run`` compara ``self.device=="cpu"``, que
   es False si se pasa un ``torch.device`` (solo compara igual a un string).
   Por eso ac{'a} se pasa ``device="cpu"`` como string.
 - ``inversion_method="vs-and-thick"`` dispara el early-stopping casi
   inmediatamente (bug en el conteo de `trigger_times` de la libreria
   original); se usa ``inversion_method="vs"`` (invierte Vs con el espesor
   inicial de la parametrizacion LN fijo), que converge normalmente.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


def _repo_adsurf_src() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "third-party" / "ADsurf"
        if cand.is_dir():
            return cand
    return None


def _ensure_on_path() -> None:
    src = _repo_adsurf_src()
    if src is not None and str(src) not in sys.path:
        sys.path.insert(0, str(src))


def available() -> bool:
    """True si torch y el ADsurf vendorizado se pueden importar."""
    try:
        _ensure_on_path()
        import torch  # noqa: F401
        from ADsurf._ADsurf import Model_param  # noqa: F401
        return True
    except Exception:
        return False


def adsurf_inversion(
    curves_by_mode: dict[int, tuple[np.ndarray, np.ndarray]],
    *,
    n_layers: int = 3,
    maxiter: int = 200,
    lr: float = 0.03,
    seed: int | None = None,
    vs_min_ms: float | None = None,
    vs_max_ms: float | None = None,
    nu: float = 0.35,
    rho: float = 1900.0,
    min_points: int = 3,
) -> dict:
    """Invierte en conjunto las curvas de dispersion con ADsurf (AD/PyTorch).

    Parameters
    ----------
    curves_by_mode
        {modo: (frecuencias_Hz, velocidades_m_s)}. Modo 0 = fundamental; los
        modos deben ser una secuencia 0..N (ADsurf combina las curvas
        multimodo por indice acumulado, no soporta huecos).
    n_layers
        Capas SOBRE el semiespacio (misma convencion que evodcinv/disba_mc).
    maxiter, lr, seed
        Parametros del optimizador Adam (gradiente via autodiferenciacion).
    vs_min_ms, vs_max_ms
        Limites de busqueda de Vs; si son None se estiman de las curvas.
    nu, rho
        Poisson y densidad para el Vp/Vs constante y la densidad inicial.

    Returns
    -------
    dict normalizado como los demas backends: beta, h, misfit, modes,
    theoretical, engine.
    """
    _ensure_on_path()
    import torch
    from ADsurf._ADsurf import Init_model, Model_param
    from ADsurf._ADsurf import cal_dispersion_curve
    from ADsurf._ADsurf import inv_param as _InvParam
    from ADsurf._ADsurf import inversion as _inversion

    if seed is not None:
        torch.manual_seed(int(seed))

    modes = sorted(
        m for m, (f, c) in curves_by_mode.items()
        if f is not None and np.asarray(f).size >= int(min_points)
    )
    if not modes:
        raise ValueError(f"Se necesitan al menos {min_points} puntos en algun modo.")
    if modes != list(range(len(modes))):
        raise ValueError(
            "ADsurf necesita modos consecutivos empezando en 0 (fundamental); "
            f"faltan modos intermedios en {modes}."
        )

    rows = []
    for m in modes:
        f = np.asarray(curves_by_mode[m][0], dtype=float)
        c = np.asarray(curves_by_mode[m][1], dtype=float)
        good = f > 0
        f, c = f[good], c[good]
        period = 1.0 / f
        c_kms = c / 1000.0
        rows.extend((p, cv, int(m)) for p, cv in zip(period, c_kms))
    pvs_obs = np.array(sorted(rows, key=lambda r: (r[2], r[0])), dtype=float)
    if pvs_obs.shape[0] == 0:
        raise ValueError("Curvas vacias o con frecuencias no positivas.")

    all_t = pvs_obs[:, 0]
    all_c = pvs_obs[:, 1]

    if vs_min_ms is None:
        vs_min_ms = 0.5 * float(all_c.min()) * 1000.0
    if vs_max_ms is None:
        vs_max_ms = 1.3 * float(all_c.max()) * 1000.0

    n_layers = int(n_layers)
    if n_layers < 1:
        raise ValueError("Se necesita al menos 1 capa sobre el semiespacio.")

    vp_vs_ratio = float(np.sqrt((2.0 - 2.0 * nu) / (1.0 - 2.0 * nu)))
    rho_kcm = float(rho) / 1000.0 if rho > 100 else float(rho)  # kg/m3 -> g/cm3 si hace falta

    model_param = Model_param(
        dc=0.005,
        vmin=max(vs_min_ms / 1000.0 * 0.8, 1e-3),
        vmax=vs_max_ms / 1000.0 * 1.2,
        Nt=100,
        tmin=float(all_t.min()) * 0.9,
        tmax=float(all_t.max()) * 1.1,
        layering_method="LN",
        initialize_method="Constant",
        depth_factor=2,
        layer_number=n_layers + 1,
        vp_vs_ratio=vp_vs_ratio,
        rho=rho_kcm,
    )
    init_model = Init_model(model_param, pvs_obs=pvs_obs)

    inv_p = _InvParam(
        inversion_method="vs",
        mode=0,
        iteration=int(maxiter),
        lr=float(lr),
        step_size=max(int(maxiter) // 3, 10),
        gamma=0.7,
        optimizer="Adam",
    )

    result = _inversion(
        model_param, inv_p, init_model, pvs_obs,
        vsrange_sign="mul", vsrange=[0.3, 3.0], device="cpu",
    )

    vs = np.asarray(result.inv_model["vs"], dtype=float)
    thick = np.asarray(result.inv_model["thick"], dtype=float)
    beta = vs * 1000.0
    h = thick[:-1] * 1000.0  # el ultimo espesor es el semiespacio (placeholder LN)

    vel_model = {
        "vs": vs,
        "vp": vs * vp_vs_ratio,
        "rho": np.full_like(vs, model_param.rho),
        "thick": thick,
    }
    theoretical: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    try:
        disp_order = list(range(max(modes) + 1))
        pvs = cal_dispersion_curve(vel_model, model_param, dispOrder=disp_order)
        for m in modes:
            sel = pvs[:, 2] == m
            period_m = pvs[sel, 0]
            vel_m = pvs[sel, 1]
            order = np.argsort(period_m)
            theoretical[int(m)] = (1.0 / period_m[order], vel_m[order] * 1000.0)
    except Exception:
        for m in modes:
            theoretical[int(m)] = (np.array([]), np.array([]))

    loss_hist = result.inv_process.get("loss", [])
    if hasattr(loss_hist, "tolist"):
        loss_hist = loss_hist.tolist()
    misfit = float(loss_hist[-1]) if len(loss_hist) else float("nan")

    return {
        "beta": beta,
        "h": h,
        "misfit": misfit,
        "modes": modes,
        "theoretical": theoretical,
        "vs_min_ms": float(vs_min_ms),
        "vs_max_ms": float(vs_max_ms),
        "n_layers": n_layers,
    }
