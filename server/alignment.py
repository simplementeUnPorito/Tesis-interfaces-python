"""Tab Enfase (§3.2): alinear entre sí las carpetas de un mismo label.

Porta ``AlignmentPanel`` de ``field_review_app.py`` (:2259). La idea física:
el trigger de cada señal ya está bien (eso se hizo en Capturas), pero entre
**tandas de días distintos** queda un desfase sistemático —cableado, latencia
del disparo— que hay que compensar antes de promediar.

Por eso se trabaja **por carpeta**, no por señal: el gráfico muestra el
promedio de cada carpeta y el offset mueve la carpeta entera, rígida. Las
carpetas se ordenan por pico a pico de su promedio, mayor primero: la primera
define el cero.

Persistencia, en los mismos archivos que la app:

* ``frd.load_alignment_offsets`` / ``save_alignment_offsets`` — ``{label:
  {carpeta: offset_s}}``.
* ``frd.load_disabled_folders`` / ``save_disabled_folders`` — carpetas
  rechazadas: sus señales pueden ser válidas pero el usuario decidió que esa
  tanda no entre a promedios/waterfall/MASW/export.

Lo que NO está portado todavía, y por qué, en DUDAS_LUNES.md #16:
el auto-enfase de dos etapas y la auto-polaridad (`auto_align_polarity`).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ._gs import frd
from .datacache import get_dataset
from .signal_view import _round6, decimate_minmax


def offsets_path(raw_root: str | Path) -> Path:
    return frd.default_alignment_offsets_path(raw_root)


def shot_offsets_path(raw_root: str | Path) -> Path:
    return frd.default_alignment_shot_offsets_path(raw_root)


def _limpiar_shot_offsets(raw_root: Path, pairs) -> int:
    """Borra los offsets por señal de esas capturas. Devuelve cuántos sacó.

    Los offsets por señal son de una versión vieja del flujo y tienen prioridad
    sobre el de carpeta: si quedan, pelean con el ajuste que se acaba de hacer.
    La app los limpia en «OK», al rechazar y al resetear el label (:2718, :2734,
    :2763); no los crea en ningún lado.
    """
    path = shot_offsets_path(raw_root)
    shot_offsets = frd.load_alignment_shot_offsets(path)
    sacados = 0
    for shot, _ann in pairs:
        if shot_offsets.pop(shot.shot_id, None) is not None:
            sacados += 1
    if sacados:
        frd.save_alignment_shot_offsets(path, shot_offsets)
    return sacados


def disabled_path(raw_root: str | Path) -> Path:
    return frd.default_disabled_folders_path(raw_root)


def _group_key(label: str, group_id: int, group_count: int) -> str:
    """Clave de rechazo. Con un solo grupo es el label pelado; con varios, la
    app le agrega el grupo (`_group_disabled_key`)."""
    if group_count <= 1:
        return label
    return f"{label}#g{group_id}"


def _folder_average(pairs, raw_root: Path) -> tuple[np.ndarray, np.ndarray] | None:
    """Promedio del geófono de una carpeta, alineado por el trigger de cada
    señal y SIN offsets (la carpeta se corre después, entera).

    Interpola a una grilla común, así tolera fs mezcladas dentro de la carpeta.
    Calcado de `_folder_average` (:2518).
    """
    traces: list[tuple[np.ndarray, np.ndarray]] = []
    for shot, ann in pairs:
        fs = float(shot.fs or shot.geo.fs or shot.hammer.fs or 0.0)
        if fs <= 0:
            continue
        geo = frd.load_signal(shot.geo, prefer_filtered=False, apply_invert=True)
        if geo.size == 0:
            continue
        idx = int(np.clip(round(float(ann.trigger_s) * fs), 0, geo.size - 1))
        geo = frd.zero_by_pretrigger(geo, idx, fs)
        if ann.geo_flip:
            geo = -geo
        t = np.arange(geo.size, dtype=np.float64) / fs - float(ann.trigger_s)
        traces.append((t, geo.astype(np.float64)))
    if not traces:
        return None
    t_min = max(min(float(t[0]) for t, _g in traces), -0.2)
    t_max = min(max(float(t[-1]) for t, _g in traces), 2.0)
    if t_max <= t_min:
        return None
    dt = min((float(t[1] - t[0]) for t, _g in traces if t.size > 1), default=1e-3)
    if dt <= 0:
        return None
    n = min(int((t_max - t_min) / dt) + 1, 20000)
    grid = np.linspace(t_min, t_max, max(n, 2))
    stack = [np.interp(grid, t, g, left=np.nan, right=np.nan) for t, g in traces]
    with np.errstate(invalid="ignore"):
        mean = np.nanmean(np.vstack(stack), axis=0)
    return grid, mean


def _pairs_by_label(raw_root: Path, group_id: int):
    """``{label: {carpeta: [(shot, ann)]}}`` con las señales **aceptadas**.

    Sólo entran las carpetas del grupo pedido: cada grupo de dispersión es un
    flujo aparte (Agrupamiento §3.3).
    """
    dataset = get_dataset(raw_root)
    anns = frd.load_annotations(frd.default_annotations_path(raw_root))
    _count, assignments = frd.load_dispersion_groups(
        frd.default_dispersion_groups_path(raw_root))

    out: dict[str, dict[str, list]] = {}
    for shot in dataset.shots:
        ann = anns.get(shot.shot_id)
        if ann is None or not ann.accepted:
            continue
        if int(assignments.get(shot.folder_name, 1) or 1) != group_id:
            continue
        label = frd.format_distance_label(float(ann.distance_m))
        out.setdefault(label, {}).setdefault(shot.folder_name, []).append((shot, ann))
    return out


def load_alignment(raw_root: str | Path, *, group_id: int = 1,
                   label: str | None = None, max_points: int = 2000) -> dict:
    """Labels del grupo y, para el label pedido, sus carpetas con promedio."""
    raw_root = Path(raw_root)
    offsets = frd.load_alignment_offsets(offsets_path(raw_root))
    disabled = frd.load_disabled_folders(disabled_path(raw_root))
    # Offsets por señal: son de una versión vieja del flujo. La app ya no los
    # crea —el ajuste fino ahora es el trigger en Capturas— pero los que hayan
    # quedado tienen PRIORIDAD sobre el de carpeta y pelean con él, así que se
    # cuentan para avisar y «OK alineado» los limpia (`_mark_ok` :2705).
    shot_offsets = frd.load_alignment_shot_offsets(shot_offsets_path(raw_root))
    group_count, _assign = frd.load_dispersion_groups(
        frd.default_dispersion_groups_path(raw_root))
    group_count = max(1, int(group_count or 1))

    por_label = _pairs_by_label(raw_root, group_id)
    labels = sorted(por_label, key=lambda s: frd._distance_from_name(s) or 0.0)
    if not labels:
        return {"group_id": group_id, "group_count": group_count, "labels": [],
                "label": None, "folders": [],
                "offsets_path": str(offsets_path(raw_root)),
                "disabled_path": str(disabled_path(raw_root))}

    activo = label if label in por_label else labels[0]
    claves_rechazo = {_group_key(activo, group_id, group_count)}
    if group_count > 1 and group_id == 1:
        claves_rechazo.add(activo)

    folders = []
    for carpeta, pairs in por_label[activo].items():
        avg = _folder_average(pairs, raw_root)
        if avg is None:
            continue
        grid, mean = avg
        finite = mean[np.isfinite(mean)]
        p2p = float(finite.max() - finite.min()) if finite.size else 0.0
        mins, maxs, rising, stride = decimate_minmax(mean.astype(np.float32), max_points)
        dt = float(grid[1] - grid[0]) if grid.size > 1 else 1e-3
        folders.append({
            "folder": carpeta,
            "shots": len(pairs),
            "p2p": _round6(p2p),
            "offset_ms": _round6(float(offsets.get(activo, {}).get(carpeta, 0.0)) * 1000.0),
            # "Acumulada" = ya tiene offset guardado, o sea que se confirmó.
            "confirmed": carpeta in offsets.get(activo, {}),
            "legacy_shot_offsets": sum(1 for shot, _a in pairs
                                       if shot.shot_id in shot_offsets),
            "rejected": any(carpeta in disabled.get(k, ()) for k in claves_rechazo),
            "trace": {
                "t0": _round6(float(grid[0])),
                "bucket_dt": _round6(dt * stride),
                "samples": int(mean.size),
                "y_min": _round6(float(finite.min())) if finite.size else None,
                "y_max": _round6(float(finite.max())) if finite.size else None,
                "min": [_round6(v) for v in mins],
                "max": [_round6(v) for v in maxs],
                "rising": [bool(v) for v in rising],
            },
        })

    # Mayor pico a pico primero: esa carpeta define el 0 (`_ordered_folders_for_label`).
    folders.sort(key=lambda f: -(f["p2p"] or 0.0))
    return {
        "group_id": group_id,
        "group_count": group_count,
        "labels": labels,
        "label": activo,
        "folders": folders,
        "offsets_path": str(offsets_path(raw_root)),
        "disabled_path": str(disabled_path(raw_root)),
    }


def set_offset(raw_root: str | Path, *, label: str, folder: str,
               offset_ms: float, group_id: int = 1) -> int:
    """Offset de una carpeta dentro de un label, en milisegundos.

    Limpia de paso los offsets por señal viejos de esa carpeta, como «OK
    alineado» de la app (`_mark_ok` :2705): tienen prioridad sobre el de
    carpeta y pelearían con este ajuste. Devuelve cuántos limpió.
    """
    raw_root = Path(raw_root)
    path = offsets_path(raw_root)
    offsets = frd.load_alignment_offsets(path)
    offsets.setdefault(label, {})[folder] = float(offset_ms) / 1000.0
    frd.save_alignment_offsets(path, offsets)
    pairs = _pairs_by_label(raw_root, group_id).get(label, {}).get(folder, [])
    return _limpiar_shot_offsets(raw_root, pairs)


def reset_folder(raw_root: str | Path, *, label: str, folder: str) -> None:
    raw_root = Path(raw_root)
    path = offsets_path(raw_root)
    offsets = frd.load_alignment_offsets(path)
    if label in offsets:
        offsets[label].pop(folder, None)
    frd.save_alignment_offsets(path, offsets)


def reset_label(raw_root: str | Path, *, label: str, group_id: int = 1) -> int:
    """Borra los offsets de todo el label, y los por señal de sus carpetas
    (`_reset_label` :2755). Devuelve cuántos offsets por señal limpió."""
    raw_root = Path(raw_root)
    path = offsets_path(raw_root)
    offsets = frd.load_alignment_offsets(path)
    offsets.pop(label, None)
    frd.save_alignment_offsets(path, offsets)
    sacados = 0
    for _carpeta, pairs in _pairs_by_label(raw_root, group_id).get(label, {}).items():
        sacados += _limpiar_shot_offsets(raw_root, pairs)
    return sacados


def set_rejected(raw_root: str | Path, *, label: str, folder: str,
                 rejected: bool, group_id: int = 1) -> None:
    """Rechaza o vuelve a aceptar una carpeta para los promedios de ese label.

    Al aceptar se limpia de TODAS las claves de grupo, como hace la app
    (`_remove_rejection`): si no, una carpeta rechazada en otro grupo
    reaparecería rechazada al volver.
    """
    raw_root = Path(raw_root)
    path = disabled_path(raw_root)
    disabled = frd.load_disabled_folders(path)
    group_count, _assign = frd.load_dispersion_groups(
        frd.default_dispersion_groups_path(raw_root))
    group_count = max(1, int(group_count or 1))

    if rejected:
        clave = _group_key(label, group_id, group_count)
        entrada = disabled.setdefault(clave, [])
        if folder not in entrada:
            entrada.append(folder)
        # Rechazar también saca el offset de carpeta y los por señal, igual que
        # `_toggle_reject` (:2734): la carpeta deja de participar, sus ajustes
        # de alineación no tienen a qué aplicarse.
        offs = frd.load_alignment_offsets(offsets_path(raw_root))
        if label in offs and offs[label].pop(folder, None) is not None:
            frd.save_alignment_offsets(offsets_path(raw_root), offs)
        _limpiar_shot_offsets(
            raw_root, _pairs_by_label(raw_root, group_id).get(label, {}).get(folder, []))
    else:
        claves = {label} | {_group_key(label, gid, group_count)
                            for gid in range(1, group_count + 1)}
        for clave in claves:
            entrada = disabled.get(clave)
            if entrada and folder in entrada:
                entrada.remove(folder)

    frd.save_disabled_folders(path, disabled)
