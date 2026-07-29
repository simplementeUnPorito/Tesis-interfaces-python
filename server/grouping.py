"""Tab Agrupamiento (§3.3): a qué grupo de dispersión pertenece cada carpeta.

Porta ``GroupingPanel`` de ``field_review_app.py`` (:2018). Cada grupo se
procesa como un flujo completo —Enfase → Promedios → Waterfall → imagen de
dispersión— y en MASW se combinan las imágenes normalizadas con pesos.

Se persiste con ``frd.load_dispersion_groups`` / ``save_dispersion_groups``, el
mismo archivo que la app. Una carpeta que no aparece en la asignación pertenece
al grupo 1 (misma regla que la app).
"""

from __future__ import annotations

from pathlib import Path

from ._gs import frd
from .datacache import get_dataset
from .state import locked, require_revision, revision

MAX_GROUPS = 20


def groups_path(raw_root: str | Path) -> Path:
    return frd.default_dispersion_groups_path(raw_root)


def load_groups(raw_root: str | Path) -> dict:
    """Grupos + una fila por carpeta, con lo que la tabla de la app muestra:
    capturas, distancias, cuántas están validadas y la fecha de la carpeta."""
    raw_root = Path(raw_root)
    path = groups_path(raw_root)
    group_count, assignments = frd.load_dispersion_groups(path)
    group_count = max(1, min(MAX_GROUPS, int(group_count or 1)))

    try:
        dataset = get_dataset(raw_root)
        anns = frd.load_annotations(frd.default_annotations_path(raw_root))
    except FileNotFoundError:
        return {
            "group_count": group_count,
            "folders": [],
            "path": str(path),
            "revision": revision(path),
        }

    por_carpeta: dict[str, dict] = {}
    for shot in dataset.shots:
        info = por_carpeta.setdefault(shot.folder_name, {
            "folder": shot.folder_name,
            "captures": 0,
            "distances": set(),
            "ok": 0,
            "reviewed": 0,
            "mtime": 0.0,
        })
        info["captures"] += 1
        ann = anns.get(shot.shot_id)
        dist = float(ann.distance_m) if ann else float(shot.distance_m)
        info["distances"].add(round(dist, 3))
        if ann is not None and ann.reviewed:
            info["reviewed"] += 1
            if ann.accepted:
                info["ok"] += 1
        try:
            info["mtime"] = max(info["mtime"], shot.folder.stat().st_mtime)
        except OSError:
            pass

    folders = []
    for nombre in sorted(por_carpeta):
        info = por_carpeta[nombre]
        # Sin asignación explícita, grupo 1 (regla de la app). Y se clampea:
        # bajar la cantidad de grupos no puede dejar carpetas en un grupo que
        # ya no existe.
        grupo = int(assignments.get(nombre, 1) or 1)
        folders.append({
            "folder": nombre,
            "group": max(1, min(group_count, grupo)),
            "captures": info["captures"],
            "distances": sorted(info["distances"]),
            "ok": info["ok"],
            "reviewed": info["reviewed"],
            "mtime": info["mtime"],
        })

    return {
        "group_count": group_count,
        "folders": folders,
        "path": str(path),
        "revision": revision(path),
    }


def save_groups(raw_root: str | Path, *, group_count: int | None = None,
                assign: dict[str, int] | None = None,
                base_revision: str = "") -> dict:
    """Guarda cantidad de grupos y/o asignaciones. Sólo pisa lo que venga."""
    raw_root = Path(raw_root)
    path = groups_path(raw_root)
    with locked(path):
        require_revision(path, base_revision)
        actual_count, assignments = frd.load_dispersion_groups(path)
        nuevo_count = max(1, min(MAX_GROUPS, int(
            group_count if group_count is not None else (actual_count or 1)
        )))
        if assign:
            for carpeta, grupo in assign.items():
                assignments[str(carpeta)] = max(1, min(nuevo_count, int(grupo)))
        # Clampeo global: si bajó la cantidad, nadie queda apuntando a un grupo
        # inexistente (la app hace lo mismo en `_clamp_assignments`).
        for carpeta, grupo in list(assignments.items()):
            assignments[carpeta] = max(1, min(nuevo_count, int(grupo)))

        frd.save_dispersion_groups(path, nuevo_count, assignments)
    return load_groups(raw_root)
