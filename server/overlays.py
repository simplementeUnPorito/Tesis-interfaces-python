"""Superposiciones del gráfico del geófono (paridad con la app PyQt).

Porta tres cosas de ``field_review_app.py``:

* ``_plot_overlays`` (:1186) — hasta N señales **con el mismo label** (misma
  distancia), sólo las aceptadas, alineadas por su propio trigger.
* ``_ok_average_for_distance`` (:1215) — promedio en vivo de las señales ya
  **validadas** (accepted + reviewed) de esa misma distancia.
* ``_folder_average_for_shot`` (:1271) — promedio de las señales ya validadas
  de la **misma carpeta**, excluyendo la actual. Es la referencia para dejar
  bien el trigger de cada señal.

Igual que en la app: no se resamplea entre fs distintas (dentro de una carpeta
la fs es una sola); si aparece otra fs en el grupo, esa señal se saltea en el
preview. El promedio "de verdad" para exportar es ``compute_average_groups``.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import numpy as np

from ._gs import frd
from .datacache import get_dataset
from .signal_view import _round6, decimate_minmax

# Señal ya centrada por pre-trigger, cacheada por (shot_id, kind, trigger). Sin
# esto, dibujar 12 overlays relee 12 archivos enteros en cada request. La app
# hace lo mismo con `_load_pair`/`_p2p_cache`.
_ZEROED_CACHE: OrderedDict[tuple[str, str, float], np.ndarray] = OrderedDict()
_CACHE_MAX = 192


def _zeroed_geo(shot, ann_trigger_s: float, prefer_filtered: bool) -> np.ndarray:
    fs = float(shot.fs or shot.geo.fs or shot.hammer.fs or 0.0)
    key = (shot.shot_id, "filt" if prefer_filtered else "raw", round(float(ann_trigger_s), 6))
    hit = _ZEROED_CACHE.get(key)
    if hit is not None:
        _ZEROED_CACHE.move_to_end(key)
        return hit
    geo = frd.load_signal(shot.geo, prefer_filtered=prefer_filtered, apply_invert=True)
    if geo.size and fs > 0:
        trigger_idx = int(np.clip(round(ann_trigger_s * fs), 0, geo.size - 1))
        geo = frd.zero_by_pretrigger(geo, trigger_idx, fs)
    _ZEROED_CACHE[key] = geo
    if len(_ZEROED_CACHE) > _CACHE_MAX:
        _ZEROED_CACHE.popitem(last=False)
    return geo


def _trace(signal: np.ndarray, *, t0: float, fs: float, max_points: int) -> dict | None:
    """Traza decimada lista para dibujar, con su origen de tiempo propio."""
    if signal.size == 0 or fs <= 0:
        return None
    mins, maxs, stride = decimate_minmax(signal, max_points)
    finite = signal[np.isfinite(signal)]
    return {
        "t0": _round6(t0),
        "bucket_dt": _round6(stride / fs),
        "samples": int(signal.size),
        "y_min": _round6(float(np.min(finite))) if finite.size else None,
        "y_max": _round6(float(np.max(finite))) if finite.size else None,
        "min": [_round6(v) for v in mins],
        "max": [_round6(v) for v in maxs],
    }


def _mean_of(segments: list[tuple[np.ndarray, int]], fs: float) -> tuple[float, np.ndarray] | None:
    """Promedio alineado por trigger. Calcado de `_ok_average_for_distance`."""
    if not segments or fs <= 0:
        return None
    rel_start = max(-idx for _sig, idx in segments)
    rel_end = max(sig.size - idx for sig, idx in segments)
    if rel_end <= rel_start + 1:
        return None
    stack = [frd.segment_nan_padded(sig, idx + rel_start, idx + rel_end) for sig, idx in segments]
    with np.errstate(invalid="ignore"):
        mean = np.nanmean(np.vstack(stack), axis=0)
    return rel_start / fs, mean


def build_overlays(
    raw_root: str | Path,
    *,
    shot_id: str,
    kind: str = "raw",
    max_points: int = 2000,
    same_label: bool = True,
    max_count: int = 12,
    folder_average: bool = True,
    ok_average: bool = True,
) -> dict:
    """Overlays del geófono para el disparo ``shot_id``. Todo alineado al
    trigger de cada señal, o sea con el mismo eje que el gráfico del geo."""
    raw_root = Path(raw_root).resolve()
    prefer_filtered = kind == "filt"

    dataset = get_dataset(raw_root)
    anns = frd.load_annotations(frd.default_annotations_path(raw_root))

    current = next((s for s in dataset.shots if s.shot_id == shot_id), None)
    if current is None:
        return {"shot_id": shot_id, "same_label": [], "ok_average": None, "folder_average": None}

    current_ann = anns.get(shot_id)
    target = round(float(current_ann.distance_m) if current_ann else float(current.distance_m), 3)

    traces: list[dict] = []
    ok_segments: list[tuple[np.ndarray, int]] = []
    folder_segments: list[tuple[np.ndarray, int]] = []
    ok_fs: float | None = None
    folder_fs: float | None = None

    for shot in dataset.shots:
        ann = anns.get(shot.shot_id)
        if ann is None:
            continue
        fs = float(shot.fs or shot.geo.fs or shot.hammer.fs or 0.0)
        if fs <= 0:
            continue
        is_current = shot.shot_id == shot_id
        same_dist = round(float(ann.distance_m), 3) == target

        need = (
            (same_label and not is_current and same_dist and ann.accepted and len(traces) < max_count)
            or (ok_average and same_dist and ann.accepted and ann.reviewed)
            or (folder_average and not is_current and shot.folder_name == current.folder_name
                and ann.accepted and ann.reviewed)
        )
        if not need:
            continue

        geo = _zeroed_geo(shot, float(ann.trigger_s), prefer_filtered)
        if geo.size == 0:
            continue
        trigger_idx = int(round(float(ann.trigger_s) * fs))

        if same_label and not is_current and same_dist and ann.accepted and len(traces) < max_count:
            trace = _trace(geo, t0=-float(ann.trigger_s), fs=fs, max_points=max_points)
            if trace is not None:
                trace["folder"] = shot.folder_name
                trace["capture"] = shot.capture_name
                traces.append(trace)

        if ok_average and same_dist and ann.accepted and ann.reviewed:
            if ok_fs is None:
                ok_fs = fs
            if abs(fs - ok_fs) <= 1e-6:
                ok_segments.append((geo, trigger_idx))

        if (folder_average and not is_current and shot.folder_name == current.folder_name
                and ann.accepted and ann.reviewed):
            if folder_fs is None:
                folder_fs = fs
            if abs(fs - folder_fs) <= 1e-6:
                folder_segments.append((geo, trigger_idx))

    ok_payload = None
    if ok_segments and ok_fs:
        got = _mean_of(ok_segments, ok_fs)
        if got is not None:
            t0, mean = got
            ok_payload = _trace(mean, t0=t0, fs=ok_fs, max_points=max_points)
            if ok_payload is not None:
                ok_payload["n"] = len(ok_segments)

    folder_payload = None
    if folder_segments and folder_fs:
        got = _mean_of(folder_segments, folder_fs)
        if got is not None:
            t0, mean = got
            folder_payload = _trace(mean, t0=t0, fs=folder_fs, max_points=max_points)
            if folder_payload is not None:
                folder_payload["n"] = len(folder_segments)

    return {
        "shot_id": shot_id,
        "distance_m": _round6(float(target)),
        "same_label": traces,
        "ok_average": ok_payload,
        "folder_average": folder_payload,
    }
