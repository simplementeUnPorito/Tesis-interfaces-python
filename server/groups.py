"""Grupos de dispersión: recortar el dataset a un solo tendido.

Porta los tres ayudantes de ``field_review_app.py`` que deciden qué entra en un
grupo: ``_folder_group_id`` (:209), ``_project_disabled_for_group`` (:227) y
``_filtered_dataset_for_group`` (:284).

Existe porque un grupo **es** un tendido distinto: sus distancias no se
promedian con las de otro grupo aunque coincidan en metros. Promedios,
waterfall y MASW tienen que ver todos el mismo recorte, así que la cuenta vive
en un solo lugar.
"""

from __future__ import annotations

from pathlib import Path

from ._gs import frd


def folder_group_id(folder_name: str, group_count: int,
                    assignments: dict[str, int] | None) -> int:
    group_count = max(1, int(group_count or 1))
    assignments = assignments or {}
    try:
        group_id = int(assignments.get(str(folder_name), 1))
    except (TypeError, ValueError):
        group_id = 1
    return max(1, min(group_id, group_count))


def group_disabled_key(label: str, group_id: int) -> str:
    return f"{label}::grupo{int(group_id)}"


def project_disabled_for_group(disabled: dict[str, list[str]] | None,
                               group_id: int, group_count: int) -> dict[str, list[str]] | None:
    """Rechazos de Enfase que aplican a este grupo, con la clave sin sufijo.

    Las claves viejas (sin ``::grupoN``) son del flujo histórico, de cuando no
    había grupos: pertenecen al Grupo 1.
    """
    if not disabled:
        return disabled
    group_id = max(1, int(group_id or 1))
    group_count = max(1, int(group_count or 1))
    proyectado: dict[str, list[str]] = {}
    for key, folders in disabled.items():
        if "::grupo" in key:
            label, _, suffix = key.partition("::grupo")
            try:
                gid = int(suffix)
            except ValueError:
                continue
            if gid == group_id:
                proyectado.setdefault(label, []).extend(folders)
        elif group_count <= 1 or group_id == 1:
            proyectado.setdefault(key, []).extend(folders)
    return {label: sorted(set(f)) for label, f in proyectado.items() if f}


def filtered_dataset(dataset, group_id: int, group_count: int,
                     assignments: dict[str, int] | None):
    """El mismo dataset con sólo los disparos de las carpetas de este grupo."""
    group_id = max(1, min(int(group_id or 1), max(1, int(group_count or 1))))
    shots = [s for s in dataset.shots
             if folder_group_id(s.folder_name, group_count, assignments) == group_id]
    return frd.FieldDataset(
        raw_root=dataset.raw_root,
        shots=shots,
        duplicate_groups=dataset.duplicate_groups,
        skipped_folders=dataset.skipped_folders,
    )


def load_grouping(raw_root: str | Path) -> tuple[int, dict[str, int]]:
    return frd.load_dispersion_groups(frd.default_dispersion_groups_path(Path(raw_root)))
