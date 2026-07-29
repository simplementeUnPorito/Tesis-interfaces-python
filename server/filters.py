"""Tab Filtros (§3.2 del PORT_PLAN): pasa-banda de fase cero + vista previa.

Porta ``FilterPanel`` de ``field_review_app.py`` (:1811). Nada de la cadena se
reimplementa: se llaman las funciones que ya existen en ``field_review_data``,
que son las mismas que usan promedios, waterfall, MASW y export.

* ``frd.load_filter_settings`` / ``save_filter_settings`` — mismo archivo que la
  app (``data/processed/<campaña>/filter_settings.json``).
* ``frd.resample_signal`` — a la fs común, si está fijada.
* ``frd.apply_bandpass_filter`` — Butterworth SOS + ``sosfiltfilt``: **fase
  cero**, o sea que no corre los tiempos de arribo. Es lo único que no se puede
  romper acá: un filtro de fase mínima desplazaría el primer arribo y arruinaría
  todo el picking.

Orden de la cadena (el mismo que en la app, y no es indistinto): primero
**resamplear** a la fs común, después **filtrar**.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np

from ._gs import frd
from .datacache import get_dataset
from .signal_view import _round6, decimate_minmax
from .state import locked, require_revision, revision


class UnknownShot(Exception):
    pass


def settings_path(raw_root: str | Path) -> Path:
    return frd.default_filter_settings_path(raw_root)


def load_settings(raw_root: str | Path) -> dict:
    s = frd.load_filter_settings(settings_path(raw_root))
    return {
        "enabled": bool(s.enabled),
        "low_hz": float(s.low_hz),
        "high_hz": float(s.high_hz),
        "order": int(s.order),
        "target_fs": float(s.target_fs),
        "dc_enabled": bool(s.dc_enabled),
        "line_suppress_enabled": bool(s.line_suppress_enabled),
        "line_f0_hz": float(s.line_f0_hz),
        "line_harmonics": int(s.line_harmonics),
        "line_search_hz": float(s.line_search_hz),
        "notes": str(s.notes or ""),
        "path": str(settings_path(raw_root)),
        "revision": revision(settings_path(raw_root)),
    }


def save_settings(
    raw_root: str | Path, patch: dict, base_revision: str = ""
) -> dict:
    """Guarda sólo lo que venga en ``patch``; el resto queda como estaba."""
    path = settings_path(raw_root)
    with locked(path):
        require_revision(path, base_revision)
        actual = frd.load_filter_settings(path)
        nuevo = frd.FilterSettings(
            enabled=bool(patch.get("enabled", actual.enabled)),
            low_hz=float(patch.get("low_hz", actual.low_hz) or 0.0),
            high_hz=float(patch.get("high_hz", actual.high_hz) or 0.0),
            order=max(1, min(10, int(patch.get("order", actual.order) or 4))),
            target_fs=float(patch.get("target_fs", actual.target_fs) or 0.0),
            dc_enabled=bool(patch.get("dc_enabled", actual.dc_enabled)),
            line_suppress_enabled=bool(
                patch.get("line_suppress_enabled", actual.line_suppress_enabled)
            ),
            line_f0_hz=float(
                patch.get("line_f0_hz", actual.line_f0_hz) or 50.0
            ),
            line_harmonics=max(
                1,
                min(
                    12,
                    int(
                        patch.get("line_harmonics", actual.line_harmonics) or 3
                    ),
                ),
            ),
            line_search_hz=max(
                0.0,
                float(
                    patch.get("line_search_hz", actual.line_search_hz) or 0.0
                ),
            ),
            notes=str(patch.get("notes", actual.notes) or ""),
        )
        frd.save_filter_settings(path, nuevo)
    return load_settings(raw_root)


def _spectrum(signal: np.ndarray, fs: float, max_points: int) -> dict | None:
    """``|FFT|`` sin la continua, agrupado en bins **espaciados en log**.

    Se dibuja en log-log (como el ``setLogMode`` de la app), y la FFT tiene sus
    muestras espaciadas de forma **uniforme** en frecuencia. Agruparlas de a
    tantas-por-bin uniforme deja las décadas bajas con un puñado de puntos muy
    separados y las altas saturadas: en el dibujo se ve como una sierra en la
    izquierda. Agrupando en bins de ancho constante *en log* cada década recibe
    la misma cantidad de puntos, que es lo que el ojo espera de un eje log.
    """
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
    # `right=False` + resta de 1: índice del bin al que cae cada frecuencia.
    idx = np.clip(np.searchsorted(edges, freqs, side="right") - 1, 0, n_bins - 1)

    f_out: list[float | None] = []
    mn_out: list[float | None] = []
    mx_out: list[float | None] = []
    for b in range(n_bins):
        sel = spec[idx == b]
        if sel.size == 0:
            continue                       # bin vacío: se saltea, no se inventa
        f_out.append(_round6(float(np.sqrt(edges[b] * edges[b + 1]))))   # centro geométrico
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


def build_preview(
    raw_root: str | Path,
    *,
    shot_id: str,
    max_points: int = 2000,
    low_hz: float | None = None,
    high_hz: float | None = None,
    order: int | None = None,
    target_fs: float | None = None,
    dc_enabled: bool | None = None,
    line_suppress_enabled: bool | None = None,
    line_f0_hz: float | None = None,
    line_harmonics: int | None = None,
    line_search_hz: float | None = None,
    include_envelope: bool = False,
) -> dict:
    """Original vs filtrada del geófono, en tiempo y en espectro.

    Los parámetros se pueden pasar sueltos para previsualizar sin guardar; lo
    que no venga sale de los ajustes persistidos.
    """
    raw_root = Path(raw_root)
    guardado = frd.load_filter_settings(settings_path(raw_root))
    low = guardado.low_hz if low_hz is None else float(low_hz)
    high = guardado.high_hz if high_hz is None else float(high_hz)
    orden = guardado.order if order is None else int(order)
    fs_comun = guardado.target_fs if target_fs is None else float(target_fs)
    preview_settings = replace(
        guardado,
        enabled=True,
        low_hz=low,
        high_hz=high,
        order=orden,
        target_fs=fs_comun,
        dc_enabled=(
            guardado.dc_enabled if dc_enabled is None else bool(dc_enabled)
        ),
        line_suppress_enabled=(
            guardado.line_suppress_enabled
            if line_suppress_enabled is None
            else bool(line_suppress_enabled)
        ),
        line_f0_hz=(
            guardado.line_f0_hz if line_f0_hz is None else float(line_f0_hz)
        ),
        line_harmonics=max(
            1,
            min(
                12,
                guardado.line_harmonics
                if line_harmonics is None
                else int(line_harmonics),
            ),
        ),
        line_search_hz=max(
            0.0,
            guardado.line_search_hz
            if line_search_hz is None
            else float(line_search_hz),
        ),
    )

    dataset = get_dataset(raw_root)
    shot = next((s for s in dataset.shots if s.shot_id == shot_id), None)
    if shot is None:
        raise UnknownShot(shot_id)

    anns = frd.load_annotations(frd.default_annotations_path(raw_root))
    ann = anns.get(shot_id)
    if ann is not None:
        trigger_s = float(ann.trigger_s)
    else:
        trigger_s = float(frd.auto_pick_shot(shot).trigger_s)

    fs = float(shot.fs or shot.geo.fs or shot.hammer.fs or 0.0)
    geo = frd.load_signal(shot.geo, prefer_filtered=False, apply_invert=True)
    if geo.size and fs > 0:
        idx = int(np.clip(round(trigger_s * fs), 0, geo.size - 1))
        geo = frd.zero_by_pretrigger(geo, idx, fs)
    geo = np.asarray(geo, dtype=np.float64)

    # Resamplear PRIMERO y filtrar después: es el orden de la app y el que usan
    # los promedios. Al revés, el filtro se diseñaría para otra fs.
    work, work_fs = geo, fs
    if fs_comun > 0 and abs(fs_comun - fs) > 1e-6:
        work = np.asarray(frd.resample_signal(geo, fs, fs_comun), dtype=np.float64)
        work_fs = fs_comun
    filtrada = np.asarray(
        frd.apply_filter_chain(work, work_fs, preview_settings), dtype=np.float64
    )
    envelope = None
    if include_envelope and filtrada.size:
        from scipy.signal import hilbert

        envelope = np.abs(hilbert(np.nan_to_num(filtrada, nan=0.0)))

    return {
        "shot_id": shot_id,
        "folder": shot.folder_name,
        "capture": shot.capture_name,
        "geo_label": shot.geo.pcb_id or shot.geo.label or "",
        "distance_m": _round6(float(ann.distance_m if ann else shot.distance_m)),
        "trigger_s": _round6(trigger_s),
        "fs": _round6(fs),
        "work_fs": _round6(work_fs),
        "resampled": abs(work_fs - fs) > 1e-6,
        "applied": {
            "low_hz": low,
            "high_hz": high,
            "order": orden,
            "target_fs": fs_comun,
            "dc_enabled": preview_settings.dc_enabled,
            "line_suppress_enabled": preview_settings.line_suppress_enabled,
            "line_f0_hz": preview_settings.line_f0_hz,
            "line_harmonics": preview_settings.line_harmonics,
            "line_search_hz": preview_settings.line_search_hz,
        },
        "enabled": bool(guardado.enabled),
        "time": {
            "original": _trace(geo, t0=-trigger_s, fs=fs, max_points=max_points),
            "filtered": _trace(filtrada, t0=-trigger_s, fs=work_fs, max_points=max_points),
            "envelope": (
                _trace(envelope, t0=-trigger_s, fs=work_fs, max_points=max_points)
                if envelope is not None
                else None
            ),
        },
        "spectrum": {
            "original": _spectrum(geo, fs, max_points),
            "filtered": _spectrum(filtrada, work_fs, max_points),
        },
    }
