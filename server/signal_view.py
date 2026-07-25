"""Lógica pura del visor de señal (§3.1 del PORT_PLAN): sin FastAPI, sin
``Request``. Acá vive el decimado min/max y el armado del payload que
consume ``static/js/tabs/capturas_signal.js``. ``routers/dataset.py`` es el
único que la llama, desde un handler síncrono (§4.6: nada de esto puede
correr en el event loop).
"""

from __future__ import annotations

import math
import warnings
from pathlib import Path

import numpy as np

from .pipeline import frd


class UnknownShot(Exception):
    """``shot_id`` que no aparece en ``discover_dataset(raw_root)``."""


class InvalidKind(Exception):
    """``kind`` distinto de ``raw``/``filt``."""


def decimate_minmax(x: np.ndarray, max_points: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Envolvente min/max por bucket. Devuelve ``(mins, maxs, stride)``.

    Por qué min/max y no un promedio ni un salteado: el primer arribo es un
    pico de pocas muestras. Promediar lo suaviza hasta hacerlo invisible y
    saltear se lo come cuando el pico cae entre dos muestras elegidas. Con
    min/max, cada píxel dibuja el rango real que hay abajo: la envolvente
    pasa exactamente por los extremos de la señal completa.
    """
    n = int(x.size)
    if n == 0:
        empty = np.array([], dtype=np.float32)
        return empty, empty, 1
    x = x.astype(np.float32, copy=False)
    stride = max(1, -(-n // max_points))        # ceil(n / max_points)
    nb = -(-n // stride)                        # ceil(n / stride)  <= max_points
    pad = nb * stride - n
    if pad:
        x = np.concatenate([x, np.full(pad, np.nan, dtype=np.float32)])
    m = x.reshape(nb, stride)
    with warnings.catch_warnings():             # buckets all-NaN avisan y devuelven NaN
        warnings.simplefilter("ignore", RuntimeWarning)
        mins, maxs = np.nanmin(m, axis=1), np.nanmax(m, axis=1)
    return mins, maxs, stride


def _round6(v: float) -> float | None:
    """6 cifras significativas; no-finito -> ``None`` (nunca ``NaN`` en el JSON)."""
    v = float(v)
    if not math.isfinite(v):
        return None
    return float(f"{v:.6g}")


def _find_shot(raw_root: Path, shot_id: str):
    dataset = frd.discover_dataset(raw_root)
    for shot in dataset.shots:
        if shot.shot_id == shot_id:
            return shot
    raise UnknownShot(shot_id)


def _channel_payload(
    channel,
    *,
    raw_root: Path,
    prefer_filtered: bool,
    invert_extra: bool,
    trigger_s: float,
    fs: float,
    max_points: int,
) -> dict:
    signal = frd.load_signal(channel, prefer_filtered=prefer_filtered, apply_invert=True)
    flip_applied = False
    if invert_extra:
        signal = -signal
        flip_applied = True

    n = int(signal.size)
    channel_fs = float(channel.fs or fs)
    if n and channel_fs > 0:
        trigger_idx = int(np.clip(round(trigger_s * channel_fs), 0, n - 1))
        zeroed = frd._zero_by_pretrigger(signal, trigger_idx, channel_fs)
    else:
        zeroed = signal.astype(np.float32, copy=False)

    mins, maxs, stride = decimate_minmax(zeroed, max_points)
    buckets = int(mins.size)

    finite = zeroed[np.isfinite(zeroed)]
    y_min = _round6(float(np.min(finite))) if finite.size else None
    y_max = _round6(float(np.max(finite))) if finite.size else None

    file_path = channel.signal_file(prefer_filtered=prefer_filtered)
    file_rel = str(Path(file_path).relative_to(raw_root)).replace("\\", "/") if file_path else ""
    used_filtered = bool(prefer_filtered and channel.filt_file is not None)

    duration_s = (n / channel_fs) if channel_fs > 0 else 0.0
    bucket_dt = (stride / channel_fs) if channel_fs > 0 else 0.0

    return {
        "role": channel.role,
        "pcb_id": channel.pcb_id,
        "label": channel.label,
        "file": file_rel,
        "used_filtered": used_filtered,
        "invert_applied": bool(channel.invert_signal),
        "flip_applied": flip_applied,
        "samples": n,
        "duration_s": _round6(duration_s),
        "stride": stride,
        "buckets": buckets,
        "bucket_dt": _round6(bucket_dt),
        "decimated": stride > 1,
        "y_min": y_min,
        "y_max": y_max,
        "min": [_round6(v) for v in mins],
        "max": [_round6(v) for v in maxs],
    }


def build_signal_payload(
    raw_root: str | Path,
    *,
    shot_id: str,
    kind: str,
    max_points: int,
    geo_flip_param: bool | None,
) -> dict:
    """Arma el payload de ``GET /api/signal``. Ver PORT_PLAN §3.1 / spec §4.

    Orden de operaciones (no se cambia, cambia el resultado):
    1) ``load_signal(apply_invert=True)`` (convención fija: geo no invertido,
       hammer invertido); 2) flip extra sólo al geo; 3) resta de línea de
       base con el mismo ``trigger_idx`` en los dos canales; 4) decimado
       min/max, cada canal con su propio largo; 5) redondeo + no-finitos a
       ``null``.
    """
    if kind not in ("raw", "filt"):
        raise InvalidKind(kind)

    raw_root = Path(raw_root).resolve()
    shot = _find_shot(raw_root, shot_id)
    prefer_filtered = kind == "filt"
    fs = float(shot.fs or shot.hammer.fs or shot.geo.fs)

    anns = frd.load_annotations(frd.default_annotations_path(raw_root))
    ann = anns.get(shot_id)

    if ann is not None:
        trigger_s = float(ann.trigger_s)
        trigger_source = "annotation"
        geo_flip_ann = bool(ann.geo_flip)
        reviewed = bool(ann.reviewed)
        accepted = bool(ann.accepted)
    else:
        pick = frd.auto_pick_shot(shot, prefer_filtered=prefer_filtered)
        trigger_s = float(pick.trigger_s)
        trigger_source = "auto"
        geo_flip_ann = False
        reviewed = False
        accepted = True

    if geo_flip_param is None:
        geo_flip_effective = geo_flip_ann
        geo_flip_source = "annotation" if ann is not None else "default"
    else:
        geo_flip_effective = bool(geo_flip_param)
        geo_flip_source = "override"

    hammer_payload = _channel_payload(
        shot.hammer, raw_root=raw_root, prefer_filtered=prefer_filtered,
        invert_extra=False, trigger_s=trigger_s, fs=fs, max_points=max_points,
    )
    geo_payload = _channel_payload(
        shot.geo, raw_root=raw_root, prefer_filtered=prefer_filtered,
        invert_extra=geo_flip_effective, trigger_s=trigger_s, fs=fs, max_points=max_points,
    )

    return {
        "shot_id": shot.shot_id,
        "folder": shot.folder_name,
        "capture": shot.capture_name,
        "distance_m": _round6(float(shot.distance_m)),
        "fs": fs,
        "kind": kind,
        "max_points": int(max_points),
        "trigger_s": _round6(trigger_s),
        "trigger_source": trigger_source,
        "reviewed": reviewed,
        "accepted": accepted,
        "geo_flip": geo_flip_effective,
        "geo_flip_source": geo_flip_source,
        "channels": {"hammer": hammer_payload, "geo": geo_payload},
    }
