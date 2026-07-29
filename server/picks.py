"""Escritura de anotaciones (§3.1 del PORT_PLAN).

Escribe con ``frd.save_annotations`` en
``data/processed/<campaña>/field_review_annotations.json``, el **mismo archivo y
formato** que usa la app PyQt: las dos interfaces tienen que poder convivir
(PORT_PLAN §0.2).

Dos reglas que no son obvias y que vienen de cómo es el equipo:

* **El trigger es de la captura, no del geófono.** Un tendido de N receptores
  comparte un solo martillo: un solo golpe, un solo tiempo cero. Mover el
  trigger lo mueve para los N.
* **La polaridad es de cada geófono.** El circuito no tiene polaridad, así que
  uno pudo quedar conectado al revés y el otro no. ``geo_flip`` se escribe sólo
  en el disparo que se está mirando.
"""

from __future__ import annotations

from pathlib import Path

from ._gs import frd
from .datacache import get_dataset
from .state import locked, require_revision, revision


class UnknownShot(Exception):
    pass


def _blank(shot) -> "frd.PickAnnotation":
    return frd.PickAnnotation(
        shot_id=shot.shot_id,
        trigger_s=0.0,
        arrival_s=0.0,
        distance_m=float(shot.distance_m),
        accepted=True,
        notes="",
        source="manual",
        reviewed=False,
        geo_flip=False,
    )


def save_pick(
    raw_root: str | Path,
    *,
    shot_id: str,
    trigger_s: float | None = None,
    accepted: bool | None = None,
    reviewed: bool | None = None,
    geo_flip: bool | None = None,
    distance_m: float | None = None,
    notes: str | None = None,
    apply_distance_to_folder: bool = False,
    flip_folder: bool = False,
    base_revision: str = "",
) -> dict:
    """Guarda la marca de un disparo. Devuelve un resumen de lo que cambió."""
    raw_root = Path(raw_root)
    dataset = get_dataset(raw_root)
    shot = next((s for s in dataset.shots if s.shot_id == shot_id), None)
    if shot is None:
        raise UnknownShot(shot_id)

    path = frd.default_annotations_path(raw_root)
    tocados: list[str] = []

    with locked(path):
        require_revision(path, base_revision)
        anns = frd.load_annotations(path)

        def marca(target) -> "frd.PickAnnotation":
            existente = anns.get(target.shot_id)
            return existente if existente is not None else _blank(target)

        actual = marca(shot)

        if trigger_s is not None:
            # Un golpe, un tiempo cero: el trigger va a TODOS los geófonos de
            # esta captura, no sólo al que se está mirando.
            hermanos = [s for s in dataset.shots
                        if s.folder_name == shot.folder_name
                        and s.capture_name == shot.capture_name]
            for s in hermanos:
                a = marca(s)
                a.trigger_s = float(trigger_s)
                a.arrival_s = float(trigger_s)
                a.source = "manual"
                anns[s.shot_id] = a
                tocados.append(s.shot_id)
            actual = anns[shot.shot_id]

        if accepted is not None:
            actual.accepted = bool(accepted)
        if reviewed is not None:
            actual.reviewed = bool(reviewed)
        if notes is not None:
            actual.notes = str(notes)
        if geo_flip is not None:
            # Sólo este receptor: la polaridad es de cada geófono.
            actual.geo_flip = bool(geo_flip)
        if distance_m is not None:
            actual.distance_m = float(distance_m)
        if accepted is not None or reviewed is not None or notes is not None \
                or geo_flip is not None or distance_m is not None:
            actual.source = "manual"
        anns[shot.shot_id] = actual
        tocados.append(shot.shot_id)

        # "Aplicar dist. a carpeta" e "Invertir geo de carpeta" de la app
        # (:1702, :1722): toda la carpeta de una, que es como se trabaja en el
        # campo (un punto de medición = una carpeta).
        if apply_distance_to_folder or flip_folder:
            for s in dataset.shots:
                if s.folder_name != shot.folder_name or s.shot_id == shot.shot_id:
                    continue
                a = marca(s)
                if apply_distance_to_folder:
                    a.distance_m = float(actual.distance_m)
                if flip_folder:
                    a.geo_flip = bool(actual.geo_flip)
                a.source = "manual"
                anns[s.shot_id] = a
                tocados.append(s.shot_id)

        frd.save_annotations(path, dataset, anns)

    guardada = anns[shot.shot_id]
    return {
        "ok": True,
        "path": str(path),
        "shot_id": shot.shot_id,
        "touched": len(set(tocados)),
        "revision": revision(path),
        "annotation": {
            "trigger_s": guardada.trigger_s,
            "arrival_s": guardada.arrival_s,
            "distance_m": guardada.distance_m,
            "accepted": guardada.accepted,
            "reviewed": guardada.reviewed,
            "geo_flip": guardada.geo_flip,
            "notes": guardada.notes,
            "source": guardada.source,
        },
    }
