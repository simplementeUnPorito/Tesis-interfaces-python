"""Tab Filtros / MASW: deconvolucion Kalman **opcional**.

Regla de este modulo, y es la que manda sobre todo lo demas: **nada de esto es
obligatorio**. La funcionalidad nace apagada, se prende por campana, y si el
paquete ``kalman_deconv`` no se puede importar el servidor sigue andando igual
con ``available=False``. Ningun endpoint de aca puede tumbar un tab existente.

Que expone:

* ``catalog()`` — geofonos, acondicionadores, priors de entrada y magnitudes,
  para poblar los combos de la interfaz sin hardcodear nada en el JS.
* ``load_settings`` / ``save_settings`` — ajustes por campana, con revision y
  escritura atomica via ``state.py`` (nunca escritura directa).
* ``build_preview`` — corre el Kalman hacia adelante sobre **un** disparo y
  devuelve traza, espectro y diagnosticos. Es una vista previa: no toca el
  pipeline de promedios/waterfall/MASW.
* ``build_masw_window`` — la ventana admisible del **segundo** Kalman, como
  envolvente ``(f, c)`` para superponer sobre la imagen de dispersion.

El contexto fisico esta en ``geophone_scope/HANDOFF_KALMAN.md`` y en el informe
``kalman_deconv/reports/velocity_model_fix_2026-08-17/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from ._gs import frd
from .datacache import get_dataset
from .signal_view import _round6, decimate_minmax
from .state import atomic_write_json, locked, read_json, require_revision

# --------------------------------------------------------------------------- #
# Import blando. Si falla, el resto del servidor no se entera.
# --------------------------------------------------------------------------- #

AVAILABLE = True
UNAVAILABLE_REASON = ""
try:  # pragma: no cover - depende del entorno
    from geophone_scope.kalman_deconv.discretize import (
        augment_with_input_model,
        build_input_model,
        discretize_plant,
        prepare_plant,
    )
    from geophone_scope.kalman_deconv.kf import (
        kf_forward,
        nis_consistency,
        rts_backward,
    )
    from geophone_scope.kalman_deconv.library import (
        list_conditioners,
        list_geophones,
        load_conditioner,
        load_geophone,
    )
    from geophone_scope.kalman_deconv.models import InputModel, PlantSpec
except Exception as exc:  # pragma: no cover
    AVAILABLE = False
    UNAVAILABLE_REASON = f"{type(exc).__name__}: {exc}"


class UnknownShot(Exception):
    pass


class KalmanUnavailable(Exception):
    """El paquete no esta: la funcionalidad simplemente no se ofrece."""


# --------------------------------------------------------------------------- #
# Catalogo para poblar la interfaz
# --------------------------------------------------------------------------- #

#: Priors de entrada, con la explicacion que se muestra al lado del combo.
INPUT_MODELS = [
    {
        "id": "ou_band",
        "name": "Oscilador de banda (recomendado)",
        "help": (
            "Codifica la banda util como fisica del prior. Es el unico que acota "
            "la deriva sub-1 Hz dentro del modelo en vez de recortarla despues."
        ),
        "needs_band": True,
    },
    {
        "id": "leaky_rw",
        "name": "Paseo aleatorio con fuga",
        "help": (
            "Plano hasta continua: no le dice al estimador que el suelo no se "
            "mueve en DC. Con magnitud velocidad deja mucha deriva."
        ),
        "needs_leak": True,
    },
    {
        "id": "random_walk",
        "name": "Paseo aleatorio puro (demostrativo)",
        "help": (
            "AVISO: inobservable en continua. Sirve para demostrar la deriva, "
            "no para producir resultados."
        ),
        "warn": True,
    },
    {
        "id": "wiener2",
        "name": "Wiener de segundo orden",
        "help": "Prior mas suave; util para comparar.",
    },
]

MAGNITUDES = [
    {"id": "acceleration", "name": "Aceleracion (nativa del modelo)"},
    {"id": "velocity", "name": "Velocidad de particula"},
    {"id": "displacement", "name": "Desplazamiento"},
]

DISCRETIZATIONS = [
    {"id": "zoh", "name": "Retenedor de orden cero"},
    {"id": "bilinear", "name": "Bilineal (Tustin)"},
    {"id": "foh", "name": "Retenedor de primer orden"},
]

R_SOURCES = [
    {"id": "pre_arrival", "name": "Varianza de la ventana pre-arribo"},
    {"id": "manual", "name": "Valor fijo"},
]

Q_SOURCES = [
    {
        "id": "ml_reference",
        "name": "Verosimilitud en un canal de referencia (rapido)",
        "help": (
            "Ajusta q una sola vez sobre el canal del medio y lo escala por la "
            "varianza de ruido de cada traza. Es el unico practico para la "
            "imagen MASW completa."
        ),
    },
    {
        "id": "ml",
        "name": "Verosimilitud canal por canal (lento, mas fiel)",
        "help": (
            "Ajusta q de forma independiente en cada traza. Da el mejor NIS pero "
            "multiplica el tiempo por la cantidad de canales."
        ),
    },
    {"id": "manual", "name": "Valor fijo"},
]

#: Valores por defecto. ``enabled`` en False no es un detalle: la funcionalidad
#: es opcional y tiene que nacer apagada para que nadie la reciba sin pedirla.
DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "geophone": "sm24_nominal",
    "conditioner": "lp_pga_medido",
    "estimate": "velocity",
    "input_model": "ou_band",
    "band_low_hz": 10.0,
    "band_high_hz": 50.0,
    "leak_hz": 0.7,
    "disc_method": "zoh",
    "q_source": "ml_reference",
    "q_scale": 1e-4,
    "r_source": "pre_arrival",
    "r_var": 0.0,
    "pre_arrival_s": 0.05,
    "post_band_enabled": True,
    "post_low_hz": 1.0,
    "post_high_hz": 80.0,
    # Suavizador RTS. Apagado por defecto: es no causal (usa toda la ventana) y
    # el usuario tiene que elegirlo a sabiendas. Cuando esta prendido, el brazo
    # que sale hacia MASW es el suavizado.
    "smoother_enabled": False,
    # Overlays de MASW, cada uno por su cuenta.
    "masw_window_enabled": False,
    "masw_physical_enabled": False,
    "masw_energetic_enabled": False,
    "masw_reference_enabled": False,
    "masw_from_kalman": False,
    "notes": "",
}


def catalog() -> dict:
    """Todo lo que la interfaz necesita para armar sus combos."""
    if not AVAILABLE:
        return {
            "available": False,
            "reason": UNAVAILABLE_REASON,
            "defaults": dict(DEFAULTS),
        }
    return {
        "available": True,
        "reason": "",
        "geophones": list_geophones(),
        "conditioners": list_conditioners(),
        "input_models": INPUT_MODELS,
        "magnitudes": MAGNITUDES,
        "discretizations": DISCRETIZATIONS,
        "q_sources": Q_SOURCES,
        "r_sources": R_SOURCES,
        "defaults": dict(DEFAULTS),
    }


# --------------------------------------------------------------------------- #
# Ajustes persistidos. Todo write pasa por state.py.
# --------------------------------------------------------------------------- #


def settings_path(raw_root: str | Path) -> Path:
    """Archivo propio, al lado del de filtros: no se pisa nada existente."""
    return Path(frd.default_filter_settings_path(raw_root)).with_name(
        "kalman_settings.json"
    )


def _coerce(patch: dict, base: dict) -> dict:
    """Mezcla ``patch`` sobre ``base`` respetando tipos y rangos."""
    out = {key: base.get(key, default) for key, default in DEFAULTS.items()}
    for key, default in DEFAULTS.items():
        if key not in patch:
            continue
        value = patch[key]
        if isinstance(default, bool):
            out[key] = bool(value)
        elif isinstance(default, float):
            out[key] = float(value)
        elif isinstance(default, str):
            out[key] = str(value)
        else:
            out[key] = value
    # Rangos: un prior con banda invertida o una fuga negativa no es un ajuste,
    # es un error que despues aparece como excepcion adentro del filtro.
    out["band_low_hz"] = max(0.01, float(out["band_low_hz"]))
    out["band_high_hz"] = max(out["band_low_hz"] + 0.01, float(out["band_high_hz"]))
    out["leak_hz"] = max(0.001, float(out["leak_hz"]))
    out["post_low_hz"] = max(0.0, float(out["post_low_hz"]))
    out["post_high_hz"] = max(out["post_low_hz"] + 0.01, float(out["post_high_hz"]))
    out["pre_arrival_s"] = max(0.0, float(out["pre_arrival_s"]))
    if out["estimate"] not in {m["id"] for m in MAGNITUDES}:
        raise ValueError(f"magnitud desconocida: {out['estimate']!r}")
    if out["input_model"] not in {m["id"] for m in INPUT_MODELS}:
        raise ValueError(f"modelo de entrada desconocido: {out['input_model']!r}")
    if out["disc_method"] not in {d["id"] for d in DISCRETIZATIONS}:
        raise ValueError(f"discretizacion desconocida: {out['disc_method']!r}")
    return out


def load_settings(raw_root: str | Path) -> dict:
    path = settings_path(raw_root)
    stored, rev = read_json(path, default=None)
    data = dict(DEFAULTS)
    if isinstance(stored, dict):
        for key in DEFAULTS:
            if key in stored:
                data[key] = stored[key]
    data["path"] = str(path)
    data["revision"] = rev
    data["available"] = AVAILABLE
    return data


def save_settings(raw_root: str | Path, patch: dict, base_revision: str = "") -> dict:
    path = settings_path(raw_root)
    with locked(path):
        # "missing" es una revision valida: la primera vez el archivo no existe.
        require_revision(path, base_revision)
        stored, _ = read_json(path, default=None)
        base = dict(DEFAULTS)
        if isinstance(stored, dict):
            for key in DEFAULTS:
                if key in stored:
                    base[key] = stored[key]
        atomic_write_json(path, _coerce(patch, base))
    return load_settings(raw_root)


# --------------------------------------------------------------------------- #
# El estimador
# --------------------------------------------------------------------------- #


def _plant_spec(settings: dict):
    conditioner = load_conditioner(str(settings["conditioner"]))
    return PlantSpec(
        geophone=load_geophone(str(settings["geophone"])),
        conditioner=conditioner,
        include_geophone=not conditioner.includes_geophone,
        estimate=str(settings["estimate"]),
    )


def _input_spec(settings: dict, q_scale: float):
    kind = str(settings["input_model"])
    band = None
    if kind == "ou_band":
        band = (float(settings["band_low_hz"]), float(settings["band_high_hz"]))
    return InputModel(
        kind=kind,
        q_scale=float(q_scale),
        leak_hz=float(settings["leak_hz"]),
        band_hz=band,
    )


def _augmented(continuous, fs: float, settings: dict, q_scale: float):
    discrete = discretize_plant(
        *continuous, fs=fs, method=str(settings["disc_method"])
    )
    return augment_with_input_model(
        discrete, build_input_model(_input_spec(settings, q_scale), fs=fs)
    )


#: Cuantos segundos de registro usa el ajuste de ``q``. El KF despues corre
#: sobre la traza entera; esto solo acota el costo de la **busqueda**, que
#: repite el filtro una vez por evaluacion del optimizador. Con 4 s alcanza y
#: sobra para estimar la densidad de la entrada, y en las capturas largas de
#: 11,4 s baja el costo casi 3x.
Q_FIT_SECONDS = 4.0


def _fit_q(values, continuous, fs: float, settings: dict, r_var: float):
    """Maxima verosimilitud de las innovaciones sobre ``log10(q)``.

    La busqueda corre sobre una ventana acotada (``Q_FIT_SECONDS``), no sobre el
    registro completo: cada evaluacion del optimizador es un filtro entero y sin
    esta cota una imagen MASW de 21 canales no termina nunca.
    """
    from scipy import optimize

    values = np.asarray(values, dtype=float)
    tope = int(round(Q_FIT_SECONDS * fs))
    if tope > 32 and values.size > tope:
        values = values[:tope]

    def objective(log10_q: float) -> float:
        system = _augmented(continuous, fs, settings, 10.0 ** float(log10_q))
        result = kf_forward(values, system.A, system.C, system.Q, r_var)
        innovation = result.innovations[:, 0]
        covariance = result.innovation_cov[:, 0, 0]
        ok = np.isfinite(innovation) & np.isfinite(covariance) & (covariance > 0)
        if not np.any(ok):
            return float("inf")
        return float(
            0.5
            * np.sum(
                np.log(2.0 * np.pi * covariance[ok])
                + innovation[ok] ** 2 / covariance[ok]
            )
        )

    bounds = (-8.0, 5.0)
    fit = optimize.minimize_scalar(
        objective, bounds=bounds, method="bounded",
        options={"maxiter": 16, "xatol": 0.05},
    )
    log10_q = float(fit.x)
    margin = min(log10_q - bounds[0], bounds[1] - log10_q)
    return 10.0**log10_q, {
        "log10_q_scale": _round6(log10_q),
        "search_bounds_log10": list(bounds),
        "railed_at_bound": bool(margin < 0.05),
        "optimizer_success": bool(fit.success),
        "optimizer_evaluations": int(fit.nfev),
        "fit_seconds": _round6(min(Q_FIT_SECONDS, values.size / max(fs, 1e-9))),
    }


def _fraction_below(x: np.ndarray, fs: float, cutoff_hz: float) -> float:
    """Energia bajo ``cutoff_hz`` sobre el total 0-Nyquist.

    Contra el total real y no contra 1-200 Hz: normalizar a una banda de
    referencia esconde justamente la deriva que interesa detectar.
    """
    from scipy import signal as sig

    x = np.asarray(x, dtype=float)
    x = x - np.mean(x)
    if x.size < 16 or fs <= 0:
        return float("nan")
    f, pxx = sig.welch(x, fs=fs, nperseg=min(x.size, 1024))
    total = float(np.trapezoid(pxx, f))
    low = float(np.trapezoid(pxx[f <= cutoff_hz], f[f <= cutoff_hz]))
    return low / max(total, np.finfo(float).tiny)


def _band_fraction(x: np.ndarray, fs: float, lo: float, hi: float) -> float:
    from scipy import signal as sig

    x = np.asarray(x, dtype=float)
    x = x - np.mean(x)
    if x.size < 16 or fs <= 0:
        return float("nan")
    f, pxx = sig.welch(x, fs=fs, nperseg=min(x.size, 1024))
    ref = (f >= 1.0) & (f <= 200.0)
    sel = (f >= lo) & (f <= hi)
    total = float(np.trapezoid(pxx[ref], f[ref]))
    return float(np.trapezoid(pxx[sel], f[sel])) / max(total, np.finfo(float).tiny)


def _post_band(x: np.ndarray, fs: float, settings: dict) -> np.ndarray:
    """SOS posterior. Es **opcional**, como todo lo demas de este modulo."""
    from scipy import signal as sig

    if not bool(settings.get("post_band_enabled", True)):
        return x
    nyquist = 0.5 * fs
    low = max(float(settings["post_low_hz"]), 0.0)
    high = min(float(settings["post_high_hz"]), 0.99 * nyquist)
    if high <= low:
        return x
    if low <= 0:
        sos = sig.butter(6, high / nyquist, btype="lowpass", output="sos")
    else:
        sos = sig.butter(
            6, [low / nyquist, high / nyquist], btype="bandpass", output="sos"
        )
    return np.asarray(sig.sosfiltfilt(sos, x), dtype=np.float64)


def _trace(signal: np.ndarray, *, t0: float, fs: float, max_points: int) -> dict | None:
    if signal.size == 0 or fs <= 0:
        return None
    mins, maxs, rising, stride = decimate_minmax(signal.astype(np.float32), max_points)
    finite = signal[np.isfinite(signal)]
    return {
        "t0": _round6(t0),
        "bucket_dt": _round6(stride / fs),
        "fs": _round6(fs),
        "samples": int(signal.size),
        "y_min": _round6(float(np.min(finite))) if finite.size else None,
        "y_max": _round6(float(np.max(finite))) if finite.size else None,
        "min": [_round6(v) for v in mins],
        "max": [_round6(v) for v in maxs],
        "rising": [bool(v) for v in rising],
    }


def _spectrum(signal: np.ndarray, fs: float, max_points: int) -> dict | None:
    """Mismo formato que ``filters._spectrum``: bins espaciados en log."""
    if signal.size < 8 or fs <= 0:
        return None
    spec = np.abs(np.fft.rfft(signal))
    freqs = np.fft.rfftfreq(signal.size, d=1.0 / fs)
    mask = freqs > 0
    freqs, spec = freqs[mask], np.maximum(spec[mask], 1e-12)
    if freqs.size == 0:
        return None
    n_bins = int(max(64, min(max_points, 2000)))
    edges = np.logspace(np.log10(freqs[0]), np.log10(freqs[-1]), n_bins + 1)
    idx = np.clip(np.searchsorted(edges, freqs, side="right") - 1, 0, n_bins - 1)
    f_out: list[float] = []
    mn_out: list[float] = []
    mx_out: list[float] = []
    for b in range(n_bins):
        sel = spec[idx == b]
        if sel.size == 0:
            continue
        f_out.append(_round6(float(np.sqrt(edges[b] * edges[b + 1]))))
        mn_out.append(_round6(float(sel.min())))
        mx_out.append(_round6(float(sel.max())))
    if not f_out:
        return None
    return {
        "f": f_out,
        "f_min": f_out[0],
        "f_max": f_out[-1],
        "y_min": _round6(float(np.min(spec))),
        "y_max": _round6(float(np.max(spec))),
        "min": mn_out,
        "max": mx_out,
    }


def build_preview(
    raw_root: str | Path,
    *,
    shot_id: str,
    max_points: int = 2000,
    overrides: dict | None = None,
) -> dict:
    """Corre el Kalman hacia adelante sobre un disparo y devuelve la vista previa.

    Es **solo vista previa**: no persiste nada y no cambia lo que consumen
    promedios, waterfall, MASW ni export.
    """
    if not AVAILABLE:
        raise KalmanUnavailable(UNAVAILABLE_REASON)

    raw_root = Path(raw_root)
    settings = load_settings(raw_root)
    if overrides:
        settings = _coerce(overrides, settings)

    dataset = get_dataset(raw_root)
    shot = next((s for s in dataset.shots if s.shot_id == shot_id), None)
    if shot is None:
        raise UnknownShot(shot_id)

    anns = frd.load_annotations(frd.default_annotations_path(raw_root))
    ann = anns.get(shot_id)
    trigger_s = (
        float(ann.trigger_s) if ann is not None
        else float(frd.auto_pick_shot(shot).trigger_s)
    )

    fs = float(shot.fs or shot.geo.fs or shot.hammer.fs or 0.0)
    if fs <= 0:
        raise ValueError("la captura no declara frecuencia de muestreo")
    geo = frd.load_signal(shot.geo, prefer_filtered=False, apply_invert=True)
    if geo.size:
        idx = int(np.clip(round(trigger_s * fs), 0, geo.size - 1))
        geo = frd.zero_by_pretrigger(geo, idx, fs)
    geo = np.nan_to_num(np.asarray(geo, dtype=np.float64), nan=0.0)
    if geo.size < 64:
        raise ValueError("la captura es demasiado corta para el estimador")

    # R de la ventana pre-arribo. El margen despega el borde del arribo: si se
    # mide pegado al trigger, R se contamina con el inicio del evento.
    n_pre = int(round((trigger_s - float(settings["pre_arrival_s"])) * fs))
    n_pre = int(np.clip(n_pre, 8, geo.size - 1))
    pre = geo[:n_pre]
    centered = geo - float(np.median(pre))
    if str(settings["r_source"]) == "manual" and float(settings["r_var"]) > 0:
        r_var = float(settings["r_var"])
    else:
        r_var = float(np.var(pre - np.mean(pre), ddof=1)) if pre.size > 2 else 0.0
    if not np.isfinite(r_var) or r_var <= 0:
        r_var = max(float(np.var(centered)) * 1e-6, np.finfo(float).tiny)

    prepared = prepare_plant(_plant_spec(settings), fs)
    continuous = prepared.continuous

    if str(settings["q_source"]) == "manual" and float(settings["q_scale"]) > 0:
        q_scale = float(settings["q_scale"])
        tuning = {
            "log10_q_scale": _round6(float(np.log10(q_scale))),
            "railed_at_bound": False,
            "optimizer_success": True,
            "optimizer_evaluations": 0,
            "search_bounds_log10": None,
        }
    else:
        q_scale, tuning = _fit_q(centered, continuous, fs, settings, r_var)

    system = _augmented(continuous, fs, settings, q_scale)
    filtered = kf_forward(centered, system.A, system.C, system.Q, r_var)
    estimate = (
        filtered.filtered_state[:, system.plant_order:] @ system.input_model.C.T
    ).ravel()
    # RTS opcional. Es un suavizador de intervalo: no causal, y por eso es una
    # eleccion explicita y no un default. Ver HANDOFF §6, Obstruccion 3.
    smoothed = None
    if bool(settings.get("smoother_enabled")):
        rts = rts_backward(filtered, system.A)
        smoothed = (
            rts.smoothed_state[:, system.plant_order:] @ system.input_model.C.T
        ).ravel()
    salida = smoothed if smoothed is not None else estimate
    post = _post_band(salida, fs, settings)

    nis = filtered.nis
    index = np.arange(nis.size)
    pre_mask = index < n_pre
    event_mask = (index >= n_pre) & (index < n_pre + int(0.6 * fs))
    report = nis_consistency(nis)

    unidades = {"acceleration": "m/s^2", "velocity": "m/s", "displacement": "m"}
    return {
        "shot_id": shot_id,
        "folder": shot.folder_name,
        "capture": shot.capture_name,
        "distance_m": _round6(float(ann.distance_m if ann else shot.distance_m)),
        "trigger_s": _round6(trigger_s),
        "fs": _round6(fs),
        "estimate": str(settings["estimate"]),
        "units": unidades.get(str(settings["estimate"]), ""),
        "applied": {k: settings[k] for k in DEFAULTS if k != "notes"},
        "plant": {
            "n_zeros": int(np.asarray(prepared.reduced_zpk[0]).size),
            "n_poles": int(np.asarray(prepared.reduced_zpk[1]).size),
            "n_states": int(continuous[0].shape[0]),
            "n_states_augmented": int(system.A.shape[0]),
            "pruned_pairs": len(prepared.prune_log.pruned_pairs),
            "residualized_modes": len(prepared.residualize_log.residualized),
        },
        "diagnostics": {
            **tuning,
            "q_scale": _round6(q_scale),
            "r_var": _round6(r_var),
            "min_cov_eigenvalue": _round6(float(filtered.min_cov_eigenvalue)),
            "p0_method": filtered.p0_method,
            "mean_nis": _round6(float(np.nanmean(nis))),
            "mean_nis_pre_arrival": _round6(
                float(np.nanmean(nis[pre_mask])) if pre_mask.any() else float("nan")
            ),
            "mean_nis_event": _round6(
                float(np.nanmean(nis[event_mask])) if event_mask.any() else float("nan")
            ),
            "nis_within_ci": bool(report["passed"]),
            "finite_output": bool(np.all(np.isfinite(post))),
            "frac_below_1_hz": _round6(_fraction_below(estimate, fs, 1.0)),
            "frac_below_1_hz_post": _round6(_fraction_below(post, fs, 1.0)),
            "frac_10_50_hz": _round6(_band_fraction(estimate, fs, 10.0, 50.0)),
            "frac_10_50_hz_post": _round6(_band_fraction(post, fs, 10.0, 50.0)),
            "frac_10_50_hz_measured": _round6(_band_fraction(centered, fs, 10.0, 50.0)),
            "smoother_enabled": bool(settings.get("smoother_enabled")),
        },
        "time": {
            "measured": _trace(centered, t0=-trigger_s, fs=fs, max_points=max_points),
            "kalman": _trace(estimate, t0=-trigger_s, fs=fs, max_points=max_points),
            "kalman_rts": (
                _trace(smoothed, t0=-trigger_s, fs=fs, max_points=max_points)
                if smoothed is not None
                else None
            ),
            "kalman_post": (
                _trace(post, t0=-trigger_s, fs=fs, max_points=max_points)
                if bool(settings["post_band_enabled"])
                else None
            ),
        },
        "spectrum": {
            "measured": _spectrum(centered, fs, max_points),
            "kalman": _spectrum(
                post if bool(settings["post_band_enabled"]) else salida,
                fs, max_points,
            ),
        },
    }


# --------------------------------------------------------------------------- #
# Ventana del segundo Kalman, para superponer sobre la imagen de dispersion
# --------------------------------------------------------------------------- #

#: Parametros del seguidor de cresta expuestos en la interfaz. Los demas quedan
#: en el default del modulo: no todo tiene que ser un control.
WINDOW_DEFAULTS: dict[str, Any] = {
    "f_min_hz": 1.0,
    "f_max_hz": 50.0,
    "seed_c_min_m_s": 50.0,
    "seed_c_max_m_s": 160.0,
    "gate_m_s": 20.0,
    "prediction_sigma_m_s": 10.0,
    "min_relative_amplitude": 0.20,
    "direction": "descending",
}


def _envelope(frequency: np.ndarray, velocity: np.ndarray, mask: np.ndarray):
    """Borde inferior y superior de la region admisible, por frecuencia.

    Es una **envolvente**, no un picking: dice donde puede estar la curva, no
    donde esta. Esa distincion es la razon de que este modulo no exporte una
    curva estimada.
    """
    lower: list[list[float]] = []
    upper: list[list[float]] = []
    cells: list[int] = []
    for i, row in enumerate(mask):
        idx = np.flatnonzero(row)
        cells.append(int(idx.size))
        if idx.size:
            lower.append([_round6(float(frequency[i])), _round6(float(velocity[idx[0]]))])
            upper.append([_round6(float(frequency[i])), _round6(float(velocity[idx[-1]]))])
    return lower, upper, cells


def build_masw_window(
    raw_root: str | Path,
    *,
    group_weights: dict[int, float] | None = None,
    group_id: int = 1,
    c_min: float,
    c_max: float,
    c_step: float,
    f_min: float,
    f_max: float,
    window: dict | None = None,
) -> dict:
    """Ventana admisible del segundo Kalman sobre la imagen de dispersion.

    Devuelve envolventes ``(f, c)`` listas para dibujar como una linea mas,
    junto al limite de aliasing y al de apertura. **No devuelve un picking.**
    """
    if not AVAILABLE:
        raise KalmanUnavailable(UNAVAILABLE_REASON)

    from geophone_scope.masw_ridge_kalman import (
        RidgeKalmanConfig,
        track_dispersion_ridge,
    )

    from .masw import _dispersion_group, _normalizar

    raw_root = Path(raw_root)
    weights = group_weights or {int(group_id): 1.0}
    weights = {
        max(1, int(gid)): max(0.0, float(w))
        for gid, w in weights.items()
        if float(w) > 0
    }
    if not weights:
        raise ValueError("hace falta al menos un grupo con peso positivo")

    results = [
        _dispersion_group(
            raw_root, gid,
            c_min=c_min, c_max=c_max, c_step=c_step, f_min=f_min, f_max=f_max,
        )
        for gid in sorted(weights)
    ]
    first = results[0]
    f, c = first["f"], first["c"]
    combined = np.zeros((f.size, c.size), dtype=np.float64)
    for result in results:
        if result["A_norm"].shape != combined.shape:
            raise ValueError("los grupos produjeron grillas incompatibles")
        combined += weights[result["group_id"]] * result["A_norm"]
    amplitude = _normalizar(np.abs(combined))

    offsets = np.concatenate([r["distances"] for r in results])

    opts = dict(WINDOW_DEFAULTS)
    for key, value in (window or {}).items():
        if key in opts:
            opts[key] = value
    config = RidgeKalmanConfig(
        f_min_hz=float(opts["f_min_hz"]),
        f_max_hz=float(opts["f_max_hz"]),
        direction=str(opts["direction"]),
        seed_c_min_m_s=float(opts["seed_c_min_m_s"]),
        seed_c_max_m_s=float(opts["seed_c_max_m_s"]),
        gate_m_s=float(opts["gate_m_s"]),
        prediction_sigma_m_s=float(opts["prediction_sigma_m_s"]),
        process_density=1.0e-4,
        min_relative_amplitude=float(opts["min_relative_amplitude"]),
        min_wavelength_dx=2.0,
        max_wavelength_aperture=1.0,
        enforce_physical_mask=False,
        enforce_normal_dispersion=False,
    )
    tracker = track_dispersion_ridge(f, c, amplitude, offsets, config)

    # Las mascaras del tracker viven en su propia grilla de frecuencias; se
    # alinean a la grilla completa por vecino mas cercano.
    def align(mask: np.ndarray) -> np.ndarray:
        out = np.zeros((f.size, mask.shape[1]), dtype=bool)
        for freq, row in zip(tracker.frequency_hz, mask):
            out[int(np.argmin(np.abs(f - freq)))] = row
        return out

    unique = np.unique(offsets)
    diffs = np.diff(unique)
    positive = diffs[diffs > 0]
    dx = float(np.median(positive)) if positive.size else 0.0
    aperture = float(unique[-1] - unique[0]) if unique.size > 1 else 0.0
    physical = (
        (c[None, :] >= 2.0 * dx * f[:, None]) & (c[None, :] <= aperture * f[:, None])
        if dx > 0 and aperture > 0
        else np.ones((f.size, c.size), dtype=bool)
    )
    gate = align(tracker.prediction_gate_mask)
    energetic = align(tracker.candidate_mask)
    combined_mask = physical & gate & energetic

    lower, upper, cells = _envelope(f, c, combined_mask)
    # Cada mascara por separado, para que la interfaz pueda prender y apagar
    # una por una en vez de recibir solo la interseccion.
    capas = {}
    for nombre, mask in (("physical", physical), ("kalman_gate", gate),
                         ("energetic", energetic), ("combined", combined_mask)):
        lo, hi, n = _envelope(f, c, mask)
        capas[nombre] = {"lower": lo, "upper": hi, "cells": n}
    nonempty = [f[i] for i, n in enumerate(cells) if n > 0]
    return {
        "available": True,
        "layers": capas,
        "lower": lower,
        "upper": upper,
        "cells": cells,
        "frequency": [_round6(float(v)) for v in f],
        "nonempty_frequency_range_hz": (
            [_round6(float(min(nonempty))), _round6(float(max(nonempty)))]
            if nonempty else None
        ),
        "nonempty_rows": int(len(nonempty)),
        "total_rows": int(f.size),
        "grid_fraction": {
            "physical": _round6(float(np.mean(physical))),
            "kalman_gate": _round6(float(np.mean(gate))),
            "energetic": _round6(float(np.mean(energetic))),
            "combined": _round6(float(np.mean(combined_mask))),
        },
        "geometry": {"dx_m": _round6(dx), "aperture_m": _round6(aperture)},
        "window": {k: opts[k] for k in WINDOW_DEFAULTS},
        "is_picking": False,
        "note": (
            "Region admisible, no un picking. El limite inferior y el superior "
            "acotan donde puede estar la curva de dispersion."
        ),
    }

# --------------------------------------------------------------------------- #
# Curva externa de referencia. Es SOLO overlay.
# --------------------------------------------------------------------------- #

#: Factor de conversion Rayleigh -> Vs aparente. Ver el informe de
#: ``velocity_model_fix_2026-08-17``.
RAYLEIGH_FACTOR = 0.92

#: Curva hidrogeologica guiada de ``data/Moldeo Hidro``. NO es una captura: es el
#: resultado de un trabajo previo, y se muestra encima de la imagen para comparar
#: a ojo. **Nunca entra al calculo** de la imagen, de las mascaras ni del ajuste
#: de Q/R.
REFERENCE_CSV_CANDIDATES = (
    "grupo1_curva_dispersion_hidro_guiada.csv",
)


def _data_root() -> Path:
    import os

    return Path(
        os.environ.get("TESIS_DATA_ROOT", Path(__file__).resolve().parents[4] / "data")
    )


def reference_curve() -> dict:
    """Curva de dispersion externa, si esta disponible.

    Devuelve ``available: false`` en vez de fallar cuando el archivo no esta:
    es un overlay opcional, no un requisito.
    """
    import csv

    base = _data_root() / "Moldeo Hidro"
    path = None
    for name in REFERENCE_CSV_CANDIDATES:
        candidate = base / name
        if candidate.is_file():
            path = candidate
            break
    if path is None:
        return {
            "available": False,
            "reason": f"no se encontro la curva externa en {base}",
        }
    freqs: list[float] = []
    c_pick: list[float] = []
    c_prior: list[float] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    f = float(row["freq_Hz"])
                    c = float(row["cR_pick_ms"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not (np.isfinite(f) and np.isfinite(c)):
                    continue
                freqs.append(f)
                c_pick.append(c)
                try:
                    c_prior.append(float(row.get("cR_hydro_prior_ms") or "nan"))
                except ValueError:
                    c_prior.append(float("nan"))
    except OSError as exc:
        return {"available": False, "reason": f"no se pudo leer {path}: {exc}"}
    if not freqs:
        return {"available": False, "reason": f"{path} no tiene filas utilizables"}
    return {
        "available": True,
        "path": str(path),
        "n_points": len(freqs),
        "frequency_range_hz": [_round6(min(freqs)), _round6(max(freqs))],
        # Rayleigh, que es el eje nativo de la imagen de dispersion.
        "c_rayleigh": [[_round6(f), _round6(c)] for f, c in zip(freqs, c_pick)],
        # Vs aparente, para cuando el eje se muestra convertido.
        "vs_apparent": [
            [_round6(f), _round6(c / RAYLEIGH_FACTOR)] for f, c in zip(freqs, c_pick)
        ],
        "c_hydro_prior": [
            [_round6(f), _round6(c)]
            for f, c in zip(freqs, c_prior)
            if np.isfinite(c)
        ],
        "rayleigh_to_vs_factor": RAYLEIGH_FACTOR,
        "is_external": True,
        "enters_computation": False,
        "note": (
            "Resultado de un trabajo previo guiado por hidrogeologia. Se muestra "
            "para comparar; no participa del calculo de la imagen ni de las mascaras."
        ),
    }


# --------------------------------------------------------------------------- #
# Imagen MASW calculada desde la salida del Kalman
# --------------------------------------------------------------------------- #


def build_dispersion_from_kalman(
    raw_root: str | Path,
    *,
    group_weights: dict[int, float] | None = None,
    group_id: int = 1,
    c_min: float,
    c_max: float,
    c_step: float,
    f_min: float,
    f_max: float,
    intensity_log: bool = False,
    intensity_per_freq: bool = True,
    overrides: dict | None = None,
) -> dict:
    """La misma imagen de dispersion, pero desde ``v_ground`` en vez del ADC.

    Devuelve **el mismo contrato** que ``masw.build_dispersion`` (incluido
    ``image_png``) para que la interfaz pueda intercambiar una por otra sin
    cambiar nada mas. Es una opcion: la ruta normal sigue intacta.

    Ojo con el costo: corre el estimador **por canal**, asi que tarda del orden
    de un segundo por traza. Por eso es un boton y no algo que pase solo.
    """
    if not AVAILABLE:
        raise KalmanUnavailable(UNAVAILABLE_REASON)

    from geophone_scope.masw_dispersion import phase_shift_dispersion_image

    from .masw import _array_geometry, _normalizar, _png_gris

    raw_root = Path(raw_root)
    settings = load_settings(raw_root)
    if overrides:
        settings = _coerce(overrides, settings)

    weights = group_weights or {int(group_id): 1.0}
    weights = {
        max(1, int(gid)): max(0.0, float(w))
        for gid, w in weights.items()
        if float(w) > 0
    }
    if not weights:
        raise ValueError("hace falta al menos un grupo con peso positivo")

    from .waterfall import matrix_for_masw

    resultados = []
    for gid in sorted(weights):
        tiempo, distancias, matriz = matrix_for_masw(raw_root, group_id=gid)
        if len(distancias) < 3:
            raise ValueError(
                "Hacen falta al menos 3 receptores para una imagen de dispersion "
                f"y hay {len(distancias)}."
            )
        fs = 1.0 / float(np.median(np.diff(tiempo)))
        matriz = np.nan_to_num(
            np.asarray(matriz, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0
        )
        # La ventana pre-arribo del waterfall es el tramo con t < 0.
        pre = tiempo < -0.05
        if np.count_nonzero(pre) < 20:
            pre = np.arange(tiempo.size) < max(20, tiempo.size // 20)
        continuous = prepare_plant(_plant_spec(settings), fs).continuous
        estimado = np.empty_like(matriz)
        diagnosticos = []

        # Centrado y R de todos los canales, antes del bucle: el modo
        # "ml_reference" necesita las varianzas para escalar.
        centradas = np.array([fila - float(np.median(fila[pre])) for fila in matriz])
        r_vars = []
        for base in centradas:
            r_var = float(np.var(base[pre], ddof=1)) if np.count_nonzero(pre) > 2 else 0.0
            if not np.isfinite(r_var) or r_var <= 0:
                r_var = max(float(np.var(base)) * 1e-6, np.finfo(float).tiny)
            r_vars.append(r_var)

        modo_q = str(settings["q_source"])
        q_ref = None
        tuning_ref = {}
        indice_ref = int(len(centradas) // 2)
        if modo_q == "ml_reference":
            # Un solo ajuste, sobre el canal del medio del tendido. Es lo que
            # hace practicable la imagen completa.
            q_ref, tuning_ref = _fit_q(
                centradas[indice_ref], continuous, fs, settings, r_vars[indice_ref]
            )

        for i, base in enumerate(centradas):
            r_var = r_vars[i]
            if modo_q == "manual" and float(settings["q_scale"]) > 0:
                q_scale = float(settings["q_scale"])
                tuning = {"log10_q_scale": _round6(float(np.log10(q_scale))),
                          "railed_at_bound": False}
            elif modo_q == "ml_reference" and q_ref is not None:
                q_scale = q_ref * r_var / max(r_vars[indice_ref], np.finfo(float).tiny)
                tuning = {
                    "log10_q_scale": _round6(float(np.log10(max(q_scale, 1e-300)))),
                    "railed_at_bound": bool(tuning_ref.get("railed_at_bound")),
                    "scaled_from_reference": True,
                }
            else:
                q_scale, tuning = _fit_q(base, continuous, fs, settings, r_var)
            system = _augmented(continuous, fs, settings, q_scale)
            filtered = kf_forward(base, system.A, system.C, system.Q, r_var)
            u = (
                filtered.filtered_state[:, system.plant_order:]
                @ system.input_model.C.T
            ).ravel()
            if bool(settings.get("smoother_enabled")):
                rts = rts_backward(filtered, system.A)
                u = (
                    rts.smoothed_state[:, system.plant_order:]
                    @ system.input_model.C.T
                ).ravel()
            estimado[i] = _post_band(u, fs, settings)
            diagnosticos.append({
                "distance_m": _round6(float(distancias[i])),
                "log10_q_scale": tuning.get("log10_q_scale"),
                "railed_at_bound": bool(tuning.get("railed_at_bound")),
                "mean_nis": _round6(float(np.nanmean(filtered.nis))),
                "min_cov_eigenvalue": _round6(float(filtered.min_cov_eigenvalue)),
                "finite": bool(np.all(np.isfinite(estimado[i]))),
            })
        f, c, A = phase_shift_dispersion_image(
            estimado.T, np.asarray(distancias, dtype=np.float64), fs,
            c_min=float(c_min), c_max=float(c_max), c_step=float(c_step),
            f_max=float(f_max), f_min=float(f_min),
        )
        resultados.append({
            "group_id": int(gid),
            "distances": np.asarray(distancias, dtype=np.float64),
            "fs": fs,
            "f": np.asarray(f, dtype=np.float64),
            "c": np.asarray(c, dtype=np.float64),
            "A_norm": _normalizar(A),
            "diagnostics": diagnosticos,
        })

    first = resultados[0]
    f, c = first["f"], first["c"]
    combinada = np.zeros((f.size, c.size), dtype=np.float64)
    for r in resultados:
        if r["A_norm"].shape != combinada.shape:
            raise ValueError("los grupos produjeron grillas incompatibles")
        combinada += weights[r["group_id"]] * r["A_norm"]

    display = np.abs(combinada)
    if intensity_per_freq:
        display = _normalizar(display)
    else:
        peak = float(np.nanmax(display)) if display.size else 1.0
        display = display / (peak if peak > 0 else 1.0)
    if intensity_log:
        display = np.log1p(99.0 * display) / np.log(100.0)
    norm = np.nan_to_num(display, nan=0.0, posinf=1.0, neginf=0.0)
    img = np.flipud((norm.T * 255.0).clip(0, 255))

    geometrias = [_array_geometry(r["distances"]) for r in resultados]
    spacings = [sp for sp, _l in geometrias if sp]
    lengths = [ln for _sp, ln in geometrias if ln]
    spacing = max(spacings) if spacings else None
    length = min(lengths) if lengths else None

    return {
        "source": "kalman",
        "group_id": int(group_id),
        "groups": [
            {
                "group_id": r["group_id"],
                "weight": weights[r["group_id"]],
                "n_channels": int(r["distances"].size),
                "distances": [_round6(float(d)) for d in r["distances"]],
                "fs": _round6(float(r["fs"])),
                "diagnostics": r["diagnostics"],
            }
            for r in resultados
        ],
        "group_weights": {str(k): v for k, v in weights.items()},
        "n_channels": int(first["distances"].size),
        "distances": [_round6(float(d)) for d in first["distances"]],
        "fs": _round6(float(first["fs"])),
        "f_min": _round6(float(f[0])) if f.size else 0.0,
        "f_max": _round6(float(f[-1])) if f.size else 0.0,
        "c_min": _round6(float(c[0])) if c.size else 0.0,
        "c_max": _round6(float(c[-1])) if c.size else 0.0,
        "width": int(img.shape[1]),
        "height": int(img.shape[0]),
        "image_png": _png_gris(img),
        "geophone_spacing_m": _finite_or_none(spacing),
        "array_length_m": _finite_or_none(length),
        "alias_boundary": (
            [[_round6(float(freq)), _round6(float(2.0 * spacing * freq))] for freq in f]
            if spacing else []
        ),
        "lambda_boundary": (
            [[_round6(float(freq)), _round6(float(length * freq))] for freq in f]
            if length else []
        ),
        "params": {"c_min": float(c_min), "c_max": float(c_max), "c_step": float(c_step),
                   "f_min": float(f_min), "f_max": float(f_max)},
        "applied": {k: settings[k] for k in DEFAULTS if k != "notes"},
    }


def _finite_or_none(value):
    if value is None:
        return None
    value = float(value)
    return _round6(value) if np.isfinite(value) else None
