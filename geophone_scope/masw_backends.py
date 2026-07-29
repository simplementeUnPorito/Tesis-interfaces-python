"""Backends de inversion MASW y exportadores para herramientas externas.

Permite elegir con que herramienta invertir las curvas de dispersion que se
pickearon en la app (una por modo). Motores que corren dentro del proceso:

 - ``evodcinv``   : evodcinv + disba, inversion conjunta multi-modo (algoritmo
   evolutivo CPSO). Usa TODAS las curvas. Ver ``masw_multimodal``.
 - ``disba_mc``   : disba como forward multi-modo + un Monte Carlo propio
   (busqueda aleatoria de perfiles). Usa TODAS las curvas, sin evodcinv.
 - ``maswavespy`` : el algoritmo de maswavespy via el port numpy
   (``masw_inversion.monte_carlo_inversion``). Es de UN modo (el fundamental /
   modo activo): el paquete real de third-party/maswavespy necesita compilar
   Cython (``cy_theoretical_dc``), que no esta disponible; el port reproduce su
   fast-delta-matrix sin compilar. ``combination.CombineDCs`` (numpy puro) si se
   usa para combinar/exportar curvas.

Herramientas externas (no corren dentro de PyQt): se exportan las curvas en un
formato que puedan leer y, si estan en el PATH, se intenta lanzarlas:

 - ``adsurf`` : https://github.com/liufeng2317/ADsurf (multimodo, Python).
 - ``geopsy`` : Geopsy/Dinver (dinver lee curvas 'target').

Todas las inversiones in-proc devuelven un dict NORMALIZADO, igual que
``masw_multimodal.multimodal_inversion``:
    {beta (m/s por capa), h (m, espesores sin semiespacio), misfit,
     modes (lista), theoretical {modo: (freq_Hz, c_m_s)}, engine (str)}
"""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

try:
    from . import masw_multimodal
except ImportError:  # pragma: no cover - ejecucion como script
    import masw_multimodal

try:
    from .masw_inversion import monte_carlo_inversion
except ImportError:  # pragma: no cover
    from masw_inversion import monte_carlo_inversion

try:
    from . import masw_adsurf
except ImportError:  # pragma: no cover - ejecucion como script
    import masw_adsurf


# --------------------------------------------------------------------------
# Registro de backends
# --------------------------------------------------------------------------

# (key, label, kind): kind 'inproc' corre la inversion; 'export' escribe archivos.
BACKENDS: list[tuple[str, str, str]] = [
    ("evodcinv", "evodcinv + disba (multimodo)", "inproc"),
    ("disba_mc", "disba + Monte Carlo propio (multimodo)", "inproc"),
    ("maswavespy", "maswavespy (port numpy, 1 modo)", "inproc"),
    ("adsurf", "ADsurf (AD/PyTorch, multimodo)", "inproc"),
    ("geopsy", "Geopsy · Dinver (exportar / lanzar)", "export"),
]

_LABELS = {k: lbl for k, lbl, _ in BACKENDS}
_KINDS = {k: kind for k, _, kind in BACKENDS}


def backend_kind(key: str) -> str:
    return _KINDS.get(key, "inproc")


def backend_label(key: str) -> str:
    return _LABELS.get(key, key)


def _disba_available() -> bool:
    try:
        masw_multimodal._patch_numpy()
        import disba  # noqa: F401
        return True
    except Exception:
        return False


def _maswavespy_combination_available() -> bool:
    """combination.CombineDCs (numpy puro) — no necesita Cython."""
    try:
        _ensure_maswavespy_on_path()
        from maswavespy import combination  # noqa: F401
        return True
    except Exception:
        return False


def backend_available(key: str) -> bool:
    if key == "evodcinv":
        return masw_multimodal.available()
    if key == "disba_mc":
        return _disba_available()
    if key == "maswavespy":
        return True  # el port numpy siempre esta
    if key == "adsurf":
        return masw_adsurf.available()
    if key == "geopsy":
        return True  # export siempre disponible
    return False


def backend_status(key: str) -> str:
    """Texto corto para la UI sobre disponibilidad."""
    if key == "evodcinv":
        return "listo" if masw_multimodal.available() else "falta: pip install evodcinv disba"
    if key == "disba_mc":
        return "listo" if _disba_available() else "falta: pip install disba"
    if key == "maswavespy":
        extra = " (+combination)" if _maswavespy_combination_available() else ""
        return "listo (port numpy" + extra + ")"
    if key == "adsurf":
        return "listo (AD/PyTorch)" if masw_adsurf.available() else "falta: submodulo third-party/ADsurf + pip install torch"
    if key == "geopsy":
        return "export" + (" + launch" if (_which("dinver") or _which("geopsy")) else "")
    return "?"


def backend_can_launch(key: str) -> bool:
    """True sólo si el backend puede abrir su herramienta en esta máquina."""
    return bool(
        key == "geopsy"
        and os.name == "nt"
        and (_which("dinver") or _which("geopsy"))
    )


# --------------------------------------------------------------------------
# Inversiones in-proc
# --------------------------------------------------------------------------

def run_inversion(key: str, curves_by_mode: dict[int, tuple[np.ndarray, np.ndarray]], **params) -> dict:
    """Corre el backend in-proc `key` y devuelve el dict normalizado."""
    if key == "evodcinv":
        res = masw_multimodal.multimodal_inversion(curves_by_mode, **_filter_kwargs(
            params, ("n_layers", "maxiter", "popsize", "seed", "vs_min_ms", "vs_max_ms", "thickness_max_m")))
        res["engine"] = "evodcinv+disba"
        return res
    if key == "disba_mc":
        res = disba_monte_carlo(curves_by_mode, **_filter_kwargs(
            params, ("n_layers", "n_iter", "seed", "vs_min_ms", "vs_max_ms", "thickness_max_m")))
        res["engine"] = "disba+MonteCarlo"
        return res
    if key == "maswavespy":
        res = maswavespy_port_inversion(curves_by_mode, **_filter_kwargs(
            params, ("n_layers", "n_iter", "bs", "bh", "nu", "rho", "mode")))
        res["engine"] = "maswavespy(port)"
        return res
    if key == "adsurf":
        res = masw_adsurf.adsurf_inversion(curves_by_mode, **_filter_kwargs(
            params, ("n_layers", "maxiter", "lr", "seed", "vs_min_ms", "vs_max_ms", "nu", "rho")))
        res["engine"] = "ADsurf(AD)"
        return res
    raise ValueError(f"Backend in-proc desconocido: {key}")


def _filter_kwargs(params: dict, keys: tuple[str, ...]) -> dict:
    return {k: params[k] for k in keys if k in params and params[k] is not None}


def _modes_ge(curves_by_mode: dict, min_points: int) -> list[int]:
    return sorted(
        m for m, (f, _c) in curves_by_mode.items()
        if f is not None and np.asarray(f).size >= min_points
    )


def maswavespy_port_inversion(
    curves_by_mode: dict[int, tuple[np.ndarray, np.ndarray]],
    *,
    n_layers: int = 3,
    n_iter: int = 1000,
    bs: float = 8.0,
    bh: float = 12.0,
    nu: float = 0.35,
    rho: float = 1850.0,
    mode: int | None = None,
    min_points: int = 3,
) -> dict:
    """Inversion de UN modo con el port numpy del algoritmo de maswavespy.

    Usa el modo `mode` si se da y tiene curva; si no, el modo mas bajo con
    >= min_points picks (normalmente el fundamental)."""
    modes = _modes_ge(curves_by_mode, min_points)
    if not modes:
        raise ValueError(f"Se necesitan al menos {min_points} puntos en algun modo.")
    m = mode if (mode is not None and mode in modes) else modes[0]
    f = np.asarray(curves_by_mode[m][0], dtype=float)
    c = np.asarray(curves_by_mode[m][1], dtype=float)
    order = np.argsort(f)
    freqs, c_obs = f[order], c[order]
    result = monte_carlo_inversion(
        freqs, c_obs, n_layers=int(n_layers), n_iterations=int(n_iter),
        bs=float(bs), bh=float(bh), nu=float(nu), rho=float(rho),
    )
    beta = np.asarray(result["beta"], dtype=float)
    h = np.asarray(result["h"], dtype=float)
    c_t = np.asarray(result.get("c_t", []), dtype=float)
    theo = {int(m): (freqs, c_t)} if c_t.size == freqs.size else {}
    return {
        "beta": beta, "h": h, "misfit": float(result.get("misfit", float("nan"))),
        "modes": [int(m)], "theoretical": theo, "only_mode": int(m),
    }


def disba_monte_carlo(
    curves_by_mode: dict[int, tuple[np.ndarray, np.ndarray]],
    *,
    n_layers: int = 3,
    n_iter: int = 1200,
    seed: int | None = 0,
    vs_min_ms: float | None = None,
    vs_max_ms: float | None = None,
    thickness_max_m: float | None = None,
    min_points: int = 3,
) -> dict:
    """Inversion conjunta multi-modo con disba (forward) y un Monte Carlo propio
    (busqueda aleatoria con perturbaciones que se aceptan si bajan el desajuste).
    Alternativa a evodcinv sin depender de el. `n_layers` = capas SOBRE el
    semiespacio (n_layers=3 -> beta de 4), como el flujo Monte Carlo clasico."""
    masw_multimodal._patch_numpy()
    from disba import PhaseDispersion

    n_layers = int(n_layers)
    if n_layers < 1:
        raise ValueError("Se necesita al menos 1 capa sobre el semiespacio.")

    modes = _modes_ge(curves_by_mode, min_points)
    if not modes:
        raise ValueError(f"Se necesitan al menos {min_points} puntos en algun modo.")

    all_c = np.concatenate([np.asarray(curves_by_mode[m][1], dtype=float) for m in modes])
    all_f = np.concatenate([np.asarray(curves_by_mode[m][0], dtype=float) for m in modes])
    all_f = all_f[all_f > 0]
    if vs_min_ms is None:
        vs_min_ms = 0.5 * float(all_c.min())
    if vs_max_ms is None:
        vs_max_ms = 1.3 * float(all_c.max())
    if thickness_max_m is None:
        thickness_max_m = max(0.5 * float(all_c.max()) / max(float(all_f.min()), 1e-6), 5.0)

    # Observado por modo: (periodos crecientes [s], c [km/s]).
    obs: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for m in modes:
        f = np.asarray(curves_by_mode[m][0], dtype=float)
        c = np.asarray(curves_by_mode[m][1], dtype=float)
        good = f > 0
        f, c = f[good], c[good]
        p = 1.0 / f
        o = np.argsort(p)
        obs[m] = (p[o], (c / 1000.0)[o])

    nu = 0.35
    vp_factor = float(np.sqrt((1.0 - nu) / (0.5 - nu)))
    tmax_km = thickness_max_m / 1000.0
    tmin_km = 0.5 / 1000.0
    vmin_k, vmax_k = vs_min_ms / 1000.0, vs_max_ms / 1000.0

    def make_model(thick_km: np.ndarray, vs_kms: np.ndarray) -> np.ndarray:
        vp = vs_kms * vp_factor
        rho = np.full_like(vs_kms, 1.9)
        thick_full = np.append(thick_km, 1.0)  # espesor del semiespacio (ignorado)
        return np.column_stack([thick_full, vp, vs_kms, rho])

    def misfit(model: np.ndarray) -> float:
        try:
            pd = PhaseDispersion(*model.T)
        except Exception:
            return np.inf
        total, n = 0.0, 0
        for m in modes:
            p, co = obs[m]
            try:
                cp = pd(p, mode=int(m), wave="rayleigh")
            except Exception:
                return np.inf
            if np.asarray(cp.period).size == 0:
                return np.inf
            co_i = np.interp(np.asarray(cp.period), p, co)
            ct = np.asarray(cp.velocity)
            total += float(np.sqrt(np.mean((ct - co_i) ** 2)))
            n += 1
        return total / max(n, 1)

    rng = np.random.default_rng(seed)
    # n_layers espesores finitos + n_layers+1 velocidades (la ultima es el
    # semiespacio, cuyo espesor lo agrega make_model como placeholder).
    best_thick = np.full(n_layers, tmax_km / n_layers)
    best_vs = np.linspace(vmin_k, vmax_k, n_layers + 1)
    best_model = make_model(best_thick, best_vs)
    best_mis = misfit(best_model)
    for _ in range(int(n_iter)):
        step = 0.18 if best_mis == np.inf else 0.12
        thick = np.clip(best_thick * (1.0 + step * rng.standard_normal(best_thick.size)), tmin_km, tmax_km)
        vs = np.sort(np.clip(best_vs * (1.0 + 0.10 * rng.standard_normal(best_vs.size)), vmin_k, vmax_k))
        mdl = make_model(thick, vs)
        mis = misfit(mdl)
        if mis < best_mis:
            best_mis, best_thick, best_vs, best_model = mis, thick, vs, mdl

    beta = best_vs * 1000.0
    h = best_thick * 1000.0
    pd = PhaseDispersion(*best_model.T)
    theoretical: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for m in modes:
        p, _co = obs[m]
        try:
            cp = pd(p, mode=int(m), wave="rayleigh")
            theoretical[int(m)] = (1.0 / np.asarray(cp.period), np.asarray(cp.velocity) * 1000.0)
        except Exception:
            theoretical[int(m)] = (np.array([]), np.array([]))

    return {
        "beta": beta, "h": h, "misfit": float(best_mis),
        "modes": modes, "theoretical": theoretical,
        "vs_min_ms": float(vs_min_ms), "vs_max_ms": float(vs_max_ms),
        "thickness_max_m": float(thickness_max_m), "n_layers": int(n_layers),
    }


# --------------------------------------------------------------------------
# Exportadores para herramientas externas
# --------------------------------------------------------------------------

def _curve_rows(f: np.ndarray, c: np.ndarray):
    f = np.asarray(f, dtype=float)
    c = np.asarray(c, dtype=float)
    good = (f > 0) & np.isfinite(c)
    f, c = f[good], c[good]
    order = np.argsort(f)
    return f[order], c[order]


def export_curves(tool: str, curves_by_mode: dict[int, tuple[np.ndarray, np.ndarray]], out_dir: str | Path) -> list[Path]:
    """Escribe las curvas por modo en el formato de la herramienta y devuelve
    los archivos generados (incluye un README con instrucciones)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    modes = sorted(curves_by_mode)

    if tool == "adsurf":
        # ADsurf lee curvas como texto: columnas (frecuencia_Hz  velocidad_m_s),
        # un archivo por modo (mode 0 = fundamental).
        for m in modes:
            f, c = _curve_rows(*curves_by_mode[m])
            if f.size == 0:
                continue
            p = out / f"adsurf_mode{m}.txt"
            with p.open("w", encoding="utf-8") as fh:
                fh.write("# freq_Hz  phase_velocity_m_s   (mode %d)\n" % m)
                for fi, ci in zip(f, c):
                    fh.write(f"{fi:.6f} {ci:.6f}\n")
            written.append(p)
        readme = out / "README_ADsurf.txt"
        readme.write_text(
            "Curvas de dispersion por modo para ADsurf (github.com/liufeng2317/ADsurf).\n"
            "Cada adsurf_modeN.txt tiene columnas: freq_Hz  phase_velocity_m_s.\n"
            "mode0 = fundamental. Cargalas como observed dispersion en el flujo de ADsurf.\n",
            encoding="utf-8",
        )
        written.append(readme)

    elif tool == "geopsy":
        # Preferido: escribir el .target real de Dinver (XML gzip) con swprepost;
        # si no esta, cae a un texto simple (freq  slowness  stddev).
        used_swprepost = False
        try:
            import swprepost  # type: ignore

            for m in modes:
                f, c = _curve_rows(*curves_by_mode[m])
                if f.size == 0:
                    continue
                velstd = 0.05 * c  # 5% como incertidumbre por defecto (ajustable en Dinver)
                tg = swprepost.Target(f, c, velstd, description=(("rayleigh", int(m)),))
                prefix = out / f"geopsy_mode{m}"
                tg.to_target(str(prefix))  # escribe geopsy_modeN.target (gzip XML)
                written.append(out / f"geopsy_mode{m}.target")
            used_swprepost = True
        except Exception:
            for m in modes:
                f, c = _curve_rows(*curves_by_mode[m])
                if f.size == 0:
                    continue
                p = out / f"geopsy_mode{m}.txt"
                with p.open("w", encoding="utf-8") as fh:
                    fh.write("# Dinver dispersion target - Rayleigh mode %d\n" % m)
                    fh.write("# frequency_Hz  slowness_s_per_m  stddev_s_per_m\n")
                    for fi, ci in zip(f, c):
                        s = 1.0 / ci if ci > 0 else 0.0
                        fh.write(f"{fi:.6f} {s:.8e} {0.02 * s:.8e}\n")
                written.append(p)
        readme = out / "README_Geopsy_Dinver.txt"
        fmt = (
            "geopsy_modeN.target: formato .target real de Dinver (XML gzip, via swprepost), "
            "listo para 'Load target' en Dinver."
            if used_swprepost
            else "geopsy_modeN.txt: frequency_Hz  slowness_s_per_m  stddev (swprepost no instalado; "
            "para .target real: pip install swprepost)."
        )
        readme.write_text(
            "Curvas de dispersion por modo para Dinver (Geopsy). mode0 = fundamental.\n" + fmt + "\n",
            encoding="utf-8",
        )
        written.append(readme)

    else:
        # Formato generico (sirve para evodcinv/disba/maswavespy o revision manual).
        for m in modes:
            f, c = _curve_rows(*curves_by_mode[m])
            if f.size == 0:
                continue
            p = out / f"curve_mode{m}.csv"
            with p.open("w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["freq_hz", "phase_velocity_m_s", "period_s", "wavelength_m"])
                for fi, ci in zip(f, c):
                    w.writerow([f"{fi:.6f}", f"{ci:.4f}", f"{1.0/fi:.6f}", f"{ci/fi:.4f}"])
            written.append(p)
    return written


def _which(name: str) -> str | None:
    vendored = _vendored_geopsy_bin()
    if vendored is not None:
        for cand in (vendored / f"{name}.exe", vendored / name):
            if cand.exists():
                return str(cand)
    return shutil.which(name)


def launch_tool(tool: str, files: list[Path]) -> tuple[bool, str]:
    """Intenta abrir la herramienta externa con los archivos exportados. Devuelve
    (lanzado, mensaje). No falla si la herramienta no esta: solo informa."""
    exe = None
    note = ""
    if tool == "geopsy":
        exe = _which("dinver") or _which("geopsy")
        if not exe:
            ok, msg = ensure_geopsy()
            note = msg + " "
            if ok:
                exe = _which("dinver") or _which("geopsy")
    elif tool == "adsurf":
        exe = _which("adsurf")
    if not exe:
        return False, f"{note}No encontre el ejecutable de {tool} en el PATH; usa los archivos exportados."
    try:
        subprocess.Popen([exe], cwd=str(files[0].parent) if files else None)
        return True, f"{note}{tool} lanzado ({exe}). Cargá los archivos exportados en su carpeta."
    except Exception as exc:  # pragma: no cover
        return False, f"{note}No pude lanzar {tool}: {exc}"


# --------------------------------------------------------------------------
# Geopsy/Dinver vendorizado (paquete portable win64 de geopsy.org, sin
# instalador: se descarga un zip y se extrae en third-party/geopsy). No es
# pip-instalable (es una app de escritorio compilada en C++/Qt), por eso se
# vendoriza como binario en vez de como submodulo git de codigo fuente.
# --------------------------------------------------------------------------

_GEOPSY_ZIP_URL = "https://www.geopsy.org/download/archives/geopsypack-win64-3.5.2.zip"


def _repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "third-party").is_dir():
            return parent
    return None


def _vendored_geopsy_bin() -> Path | None:
    root = _repo_root()
    if root is None:
        return None
    base = root / "third-party" / "geopsy"
    if not base.is_dir():
        return None
    for cand in sorted(base.glob("geopsypack-*/bin"), reverse=True):
        if (cand / "dinver.exe").exists():
            return cand
    return None


def ensure_geopsy() -> tuple[bool, str]:
    """Descarga y extrae el paquete portable win64 de Geopsy/Dinver si no
    esta ya vendorizado en third-party/geopsy. Devuelve (ok, mensaje)."""
    existing = _vendored_geopsy_bin()
    if existing is not None:
        return True, f"Geopsy/Dinver ya esta en {existing}."
    if sys.platform != "win32":
        return False, "Solo hay paquete portable automatico para Windows (win64); instala Geopsy manualmente para tu plataforma."
    root = _repo_root()
    if root is None:
        return False, "No encontre la raiz del repo (carpeta third-party)."
    import urllib.request
    import zipfile

    dest_dir = root / "third-party" / "geopsy"
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "geopsypack-win64.zip"
    try:
        urllib.request.urlretrieve(_GEOPSY_ZIP_URL, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(dest_dir)
    except Exception as exc:
        return False, f"No pude descargar/extraer Geopsy: {exc}"
    finally:
        zip_path.unlink(missing_ok=True)
    bin_dir = _vendored_geopsy_bin()
    if bin_dir is None:
        return False, "Se descargo el paquete pero no encontre bin/dinver.exe adentro."
    return True, f"Geopsy/Dinver descargado e instalado en {bin_dir}."


# --------------------------------------------------------------------------
# maswavespy vendored en el PATH (para combination.CombineDCs)
# --------------------------------------------------------------------------

def _repo_maswavespy_src() -> Path | None:
    # .../src/python/geophone_scope/masw_backends.py -> repo/third-party/maswavespy/src
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "third-party" / "maswavespy" / "src"
        if cand.is_dir():
            return cand
    return None


def _ensure_maswavespy_on_path() -> None:
    src = _repo_maswavespy_src()
    if src is not None and str(src) not in sys.path:
        sys.path.insert(0, str(src))
