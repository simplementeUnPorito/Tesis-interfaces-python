"""Tab Promedios / arrivals (§3.3): el promedio por distancia y su arribo.

Porta ``AverageReviewPanel`` de ``field_review_app.py`` (:2842). El cálculo NO
se reimplementa: se llama a ``frd.compute_average_groups``, que es exactamente
lo que usan la app, el waterfall y el export. Eso importa porque ahí adentro ya
están aplicadas, en orden, todas las decisiones de las pestañas anteriores:

* sólo entran las capturas ``accepted`` **y** ``reviewed`` (Capturas);
* se descartan las carpetas rechazadas en Enfase (``disabled_folders``);
* se corre cada carpeta por su offset de enfase (``alignment_offsets``);
* se resamplea a la fs común y se filtra según ``filter_settings`` (Filtros).

Lo que se anota acá es el **primer arribo del promedio**, con
``frd.load_average_arrivals`` / ``save_average_arrivals``: es el número que
después alimenta la curva tiempo-distancia.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ._gs import frd
from .datacache import get_dataset
from .signal_view import _round6, decimate_minmax


def arrivals_path(raw_root: str | Path) -> Path:
    return frd.default_average_arrivals_path(frd.default_output_dir(raw_root))


def _trace(time_s: np.ndarray, values: np.ndarray, max_points: int) -> dict | None:
    if values.size == 0 or time_s.size == 0:
        return None
    mins, maxs, stride = decimate_minmax(values.astype(np.float32), max_points)
    finite = values[np.isfinite(values)]
    dt = float(time_s[1] - time_s[0]) if time_s.size > 1 else 1e-3
    return {
        "t0": _round6(float(time_s[0])),
        "bucket_dt": _round6(dt * stride),
        "samples": int(values.size),
        "y_min": _round6(float(finite.min())) if finite.size else None,
        "y_max": _round6(float(finite.max())) if finite.size else None,
        "min": [_round6(v) for v in mins],
        "max": [_round6(v) for v in maxs],
    }


def build_averages(raw_root: str | Path, *, max_points: int = 2000,
                   prefer_filtered: bool = False) -> dict:
    """Un promedio por distancia, con su arribo anotado si ya existe."""
    raw_root = Path(raw_root)
    dataset = get_dataset(raw_root)
    anns = frd.load_annotations(frd.default_annotations_path(raw_root))
    settings = frd.load_filter_settings(frd.default_filter_settings_path(raw_root))
    offsets = frd.load_alignment_offsets(frd.default_alignment_offsets_path(raw_root))
    shot_offsets = frd.load_alignment_shot_offsets(
        frd.default_alignment_shot_offsets_path(raw_root))
    disabled = frd.load_disabled_folders(frd.default_disabled_folders_path(raw_root))
    arrivals = frd.load_average_arrivals(arrivals_path(raw_root))

    groups, hammer_global = frd.compute_average_groups(
        dataset, anns,
        prefer_filtered=prefer_filtered,
        # El filtro sólo se aplica si el usuario lo dejó activo en Filtros: si
        # no, el promedio es de la señal cruda, como en la app.
        filter_settings=settings if settings.enabled else None,
        alignment_offsets=offsets,
        alignment_shot_offsets=shot_offsets,
        disabled_folders=disabled,
    )

    out = []
    for g in groups:
        marca = arrivals.get(g["label"])
        out.append({
            "label": g["label"],
            "distance_m": _round6(float(g["distance_m"])),
            "n": int(g["n"]),
            "fs": _round6(float(g["fs"])),
            "arrival_s": _round6(float(marca.arrival_s)) if marca else None,
            "reviewed": bool(marca.reviewed) if marca else False,
            "notes": (marca.notes or "") if marca else "",
            "geo": _trace(g["time_s"], g["geo_mean_v"], max_points),
            "hammer": _trace(g["time_s"], g["hammer_mean_v"], max_points),
        })

    hammer = None
    if hammer_global:
        hammer = _trace(hammer_global["time_s"], hammer_global["hammer_mean_v"], max_points)
        if hammer is not None:
            hammer["n"] = int(hammer_global["n"])

    return {
        "groups": out,
        "hammer_global": hammer,
        "filter_enabled": bool(settings.enabled),
        "path": str(arrivals_path(raw_root)),
    }


def save_arrival(raw_root: str | Path, *, label: str, distance_m: float,
                 arrival_s: float | None = None, reviewed: bool | None = None,
                 notes: str | None = None) -> dict:
    """Anota el primer arribo del promedio de un label. Sólo pisa lo que venga."""
    raw_root = Path(raw_root)
    path = arrivals_path(raw_root)
    arrivals = frd.load_average_arrivals(path)
    marca = arrivals.get(label) or frd.AverageArrivalAnnotation(
        label=label, distance_m=float(distance_m))
    if arrival_s is not None:
        marca.arrival_s = float(arrival_s)
    if reviewed is not None:
        marca.reviewed = bool(reviewed)
    if notes is not None:
        marca.notes = str(notes)
    marca.distance_m = float(distance_m)
    arrivals[label] = marca
    frd.save_average_arrivals(path, arrivals)
    return {"label": label, "arrival_s": marca.arrival_s,
            "reviewed": marca.reviewed, "notes": marca.notes, "path": str(path)}
