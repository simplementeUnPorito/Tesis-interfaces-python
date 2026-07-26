"""Tab Waterfall (§3.4): el tendido entero en un solo gráfico.

Porta ``WaterfallPanel`` de ``field_review_app.py`` (:3341). Como allá, acá no
se calcula ningún promedio propio: se parte de ``frd.compute_average_groups``
(lo mismo que come Promedios) y se arma la base de tiempo común con
``frd.build_waterfall_matrix``. Así el waterfall muestra exactamente lo que se
va a exportar, no una segunda versión de los mismos números.

Qué es de vista y qué no — es la distinción que hace el docstring del panel y
conviene no perderla:

* **Sólo vista** (y lo que se manda a MASW): el recorte de tiempo, las trazas
  destildadas, la escala de amplitud y el filtro f-k. El export de Promedios
  sigue usando todas las distancias y el rango completo.
* **Persiste**: «Invertir traza» toggplea ``geo_flip`` en TODAS las capturas de
  esa distancia (``frd.flip_distance_group``), así que llega a promedios, MASW
  y export. Por eso vive en el backend y escribe las anotaciones.

El estado de vista se guarda en el mismo archivo que la app
(``masw_state.json`` + ``masw_arrays.npz``) con ``frd.load/save_masw_state``,
para que abrir la web y abrir la app muestren el mismo encuadre.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np

from ._gs import frd
from .averages import arrivals_path
from .datacache import get_dataset
from .groups import filtered_dataset, load_grouping, project_disabled_for_group
from .signal_view import _round6, decimate_minmax

_write_lock = threading.Lock()

KFILTER_MODES = ("off", "directo", "inverso")

# Valores por defecto de los controles, calcados de `_build_ui` (:3380): el
# recorte arranca apagado y de 0 a 1 s, la amplitud normalizada por traza y el
# filtro f-k en Off.
DEFAULT_VIEW: dict[str, Any] = {
    "trim_enabled": False,
    "trim_start": 0.0,
    "trim_end": 1.0,
    "raw_amplitude": False,
    "kfilter_mode": "off",
    "hidden_distances": [],
}


def state_path(raw_root: str | Path) -> Path:
    return frd.default_masw_state_path(raw_root)


def arrays_path(raw_root: str | Path) -> Path:
    return frd.default_masw_arrays_path(raw_root)


def _group_name(group_id: int) -> str:
    return f"Grupo {int(group_id)}"


def load_view(raw_root: str | Path, group_id: int) -> dict:
    """Ajustes de vista guardados. Son por grupo: cada tendido tiene su recorte.

    El archivo lo comparte con la app, que guarda ahí un estado más grande (el
    de MASW). Se lee lo que se entiende y se deja el resto intacto.
    """
    estado = frd.load_masw_state(state_path(raw_root)) or {}
    wf = estado.get("waterfall")
    if not isinstance(wf, dict):
        wf = {}
    # La app guarda un solo waterfall (el activo) más un dict `groups`; acá se
    # guarda uno por grupo bajo `by_group`, y si no está se cae al de la app.
    por_grupo = wf.get("by_group") if isinstance(wf.get("by_group"), dict) else {}
    crudo = por_grupo.get(str(int(group_id)))
    if not isinstance(crudo, dict):
        crudo = wf if wf.get("group_id") in (None, int(group_id)) else {}

    vista = dict(DEFAULT_VIEW)
    for k in ("trim_enabled", "raw_amplitude"):
        if k in crudo:
            vista[k] = bool(crudo[k])
    for k in ("trim_start", "trim_end"):
        if k in crudo:
            try:
                vista[k] = float(crudo[k])
            except (TypeError, ValueError):
                pass
    modo = str(crudo.get("kfilter_mode", "off")).strip().lower()
    vista["kfilter_mode"] = modo if modo in KFILTER_MODES else "off"
    ocultas = crudo.get("hidden_distances")
    if isinstance(ocultas, (list, tuple)):
        vista["hidden_distances"] = sorted(
            {round(float(v), 6) for v in ocultas if _es_numero(v)})
    return vista


def _es_numero(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def save_view(raw_root: str | Path, group_id: int, patch: dict) -> dict:
    """Guarda los ajustes de vista de un grupo. Parcial: sólo pisa lo que llega."""
    with _write_lock:
        path = state_path(raw_root)
        estado = frd.load_masw_state(path) or {}
        wf = estado.get("waterfall")
        if not isinstance(wf, dict):
            wf = {}
        por_grupo = wf.get("by_group")
        if not isinstance(por_grupo, dict):
            por_grupo = {}

        vista = load_view(raw_root, group_id)
        for k in ("trim_enabled", "raw_amplitude"):
            if k in patch:
                vista[k] = bool(patch[k])
        for k in ("trim_start", "trim_end"):
            if k in patch and _es_numero(patch[k]):
                vista[k] = float(patch[k])
        if "kfilter_mode" in patch:
            modo = str(patch["kfilter_mode"]).strip().lower()
            vista["kfilter_mode"] = modo if modo in KFILTER_MODES else "off"
        if "hidden_distances" in patch and isinstance(patch["hidden_distances"], (list, tuple)):
            vista["hidden_distances"] = sorted(
                {round(float(v), 6) for v in patch["hidden_distances"] if _es_numero(v)})

        por_grupo[str(int(group_id))] = vista
        wf["by_group"] = por_grupo
        # Se deja también en la raíz para que la app, que lee un solo
        # waterfall, encuentre el del grupo que se tocó último.
        wf.update({k: vista[k] for k in DEFAULT_VIEW})
        wf["group_id"] = int(group_id)
        estado["waterfall"] = wf
        frd.save_masw_state(path, estado)
        return vista


def _trim(common_time: np.ndarray, matrix: np.ndarray,
          vista: dict) -> tuple[np.ndarray, np.ndarray]:
    """Recorte de tiempo. Calcado de `_trimmed_time_and_matrix` (:3585)."""
    if not vista.get("trim_enabled") or common_time.size == 0:
        return common_time, matrix
    start_s = float(vista.get("trim_start", 0.0))
    end_s = float(vista.get("trim_end", 1.0))
    if end_s <= start_s:
        return common_time, matrix
    lo = int(np.searchsorted(common_time, start_s, side="left"))
    hi = int(np.searchsorted(common_time, end_s, side="right"))
    lo = max(0, min(lo, common_time.size - 1))
    hi = max(lo + 1, min(hi, common_time.size))
    return common_time[lo:hi], matrix[:, lo:hi]


def _kfilter(common_time: np.ndarray, matrix: np.ndarray,
             distances: list[float], modo: str) -> np.ndarray:
    """f-k direccional sobre TODAS las distancias, antes de descartar las
    ocultas: así la resolución en k usa el tendido completo (`_apply_kfilter`
    :3600). Cuál sentido es el "correcto" depende de cómo quedó el tendido, por
    eso lo elige el usuario y no se adivina."""
    if modo == "off" or matrix.size == 0:
        return matrix
    try:
        return np.asarray(
            frd.fk_directional_filter(matrix, distances, common_time,
                                      keep_forward=modo != "inverso"),
            dtype=np.float64,
        )
    except Exception:
        return matrix


def _visibles(distances: list[float], matrix: np.ndarray,
              ocultas: set[float]) -> tuple[list[float], np.ndarray]:
    if not ocultas:
        return distances, matrix
    keep = [i for i, d in enumerate(distances) if round(float(d), 6) not in ocultas]
    if not keep:
        return [], matrix[:0]
    return [distances[i] for i in keep], matrix[keep, :]


def _fila(common_time: np.ndarray, signal: np.ndarray, max_points: int) -> dict | None:
    """Una traza del waterfall, decimada. La curva ya viene escalada y corrida
    a su distancia: acá sólo se la reduce a algo dibujable."""
    if signal.size == 0 or common_time.size == 0:
        return None
    mins, maxs, rising, stride = decimate_minmax(signal.astype(np.float32), max_points)
    dt = float(common_time[1] - common_time[0]) if common_time.size > 1 else 1e-3
    return {
        "t0": _round6(float(common_time[0])),
        "bucket_dt": _round6(dt * stride),
        "samples": int(signal.size),
        "min": [_round6(v) for v in mins],
        "max": [_round6(v) for v in maxs],
        "rising": [bool(v) for v in rising],
    }


def _averages_for(raw_root: Path, group_id: int) -> tuple[list, dict | None, dict, bool, int]:
    """Los promedios del grupo. Mismo camino que Promedios, a propósito: el
    waterfall tiene que mostrar exactamente lo que se va a exportar."""
    group_count, assignments = load_grouping(raw_root)
    group_id = max(1, min(int(group_id or 1), max(1, group_count)))
    dataset = filtered_dataset(get_dataset(raw_root), group_id, group_count, assignments)
    anns = frd.load_annotations(frd.default_annotations_path(raw_root))
    settings = frd.load_filter_settings(frd.default_filter_settings_path(raw_root))
    offsets = frd.load_alignment_offsets(frd.default_alignment_offsets_path(raw_root))
    shot_offsets = frd.load_alignment_shot_offsets(
        frd.default_alignment_shot_offsets_path(raw_root))
    disabled = project_disabled_for_group(
        frd.load_disabled_folders(frd.default_disabled_folders_path(raw_root)),
        group_id, group_count)
    groups, hammer_global = frd.compute_average_groups(
        dataset, anns,
        filter_settings=settings if settings.enabled else None,
        alignment_offsets=offsets,
        alignment_shot_offsets=shot_offsets,
        disabled_folders=disabled,
    )
    arrivals = frd.load_average_arrivals(arrivals_path(raw_root))
    return groups, hammer_global, arrivals, bool(settings.enabled), max(1, group_count)


def matrix_for_masw(raw_root: str | Path, *, group_id: int = 1,
                    view: dict | None = None) -> tuple[np.ndarray, list[float], np.ndarray]:
    """Lo que el waterfall le manda a MASW. Calcado de `_emit_masw` (:3974).

    Es el mismo recorte que se está viendo —tiempo recortado, f-k aplicado y
    sin las trazas destildadas— y NO la matriz completa: si en el waterfall se
    sacó una traza mala, MASW tampoco la tiene que ver. Devuelve
    ``(common_time, distances, matrix)`` y levanta ``ValueError`` si el
    recorte dejó todo vacío.
    """
    raw_root = Path(raw_root)
    vista = dict(load_view(raw_root, group_id))
    if view:
        vista.update({k: v for k, v in view.items() if v is not None})

    groups, _hammer, _arrivals, _filtro, _gc = _averages_for(raw_root, group_id)
    if not groups:
        raise ValueError("No hay promedios: hace falta validar capturas en Capturas.")
    built = frd.build_waterfall_matrix(groups)
    if built is None:
        raise ValueError("No se pudo armar la base de tiempo común.")
    common_time, distances, matrix = built

    common_time, matrix = _trim(np.asarray(common_time, dtype=np.float64),
                                np.asarray(matrix, dtype=np.float64), vista)
    matrix = _kfilter(common_time, matrix, distances, vista["kfilter_mode"])
    ocultas = {round(float(v), 6) for v in vista.get("hidden_distances", [])}
    distances, matrix = _visibles(distances, matrix, ocultas)
    if common_time.size == 0 or not distances:
        raise ValueError("No hay trazas visibles con el recorte/filtro actual "
                         "de la pestaña Waterfall.")
    return common_time, distances, matrix


def build_waterfall(raw_root: str | Path, *, group_id: int = 1,
                    max_points: int = 1400, view: dict | None = None) -> dict:
    """Arma el waterfall de un grupo: trazas ya escaladas y listas para dibujar.

    Se devuelve la curva **ya corrida a su distancia** (``y = señal/pico *
    spacing*0.4 + distancia``, igual que `_redraw` :3845) y no la señal cruda:
    la separación entre trazas depende del espaciado mediano del tendido, que
    es un dato del conjunto, no de cada traza. Calcularla en el navegador sería
    reimplementar la misma cuenta en otro lado.
    """
    raw_root = Path(raw_root)
    vista = dict(load_view(raw_root, group_id))
    if view:
        vista.update({k: v for k, v in view.items() if v is not None})

    groups, hammer_global, arrivals, filtro_activo, group_count = _averages_for(
        raw_root, group_id)
    group_id = max(1, min(int(group_id or 1), group_count))
    base = {
        "group_id": int(group_id),
        "group_count": int(group_count),
        "group_name": _group_name(group_id),
        "n_averages": len(groups),
        "filter_enabled": filtro_activo,
        "view": vista,
        "traces": [],
        "hammer": None,
        "spacing": 1.0,
        "all_distances": [],
        "t_min": None,
        "t_max": None,
        "y_min": None,
        "y_max": None,
    }
    if not groups:
        base["message"] = ("No hay promedios: hace falta validar capturas en Capturas "
                           "(sólo entran las accepted Y reviewed).")
        return base

    built = frd.build_waterfall_matrix(groups)
    if built is None:
        base["message"] = "No se pudo armar la base de tiempo común."
        return base
    common_time, distances, matrix = built
    base["all_distances"] = [_round6(float(d)) for d in distances]

    common_time, matrix = _trim(np.asarray(common_time, dtype=np.float64),
                                np.asarray(matrix, dtype=np.float64), vista)
    matrix = _kfilter(common_time, matrix, distances, vista["kfilter_mode"])
    ocultas = {round(float(v), 6) for v in vista.get("hidden_distances", [])}
    vis_dist, matrix = _visibles(distances, matrix, ocultas)

    if common_time.size == 0 or not vis_dist:
        base["message"] = "Sin trazas visibles (recorte o filtro vacío)."
        return base

    # Separación entre trazas: la mediana del espaciado del tendido, con piso en
    # 1 m. Es lo que define cuánta amplitud entra sin que una traza pise a la de
    # al lado.
    arr = np.asarray(vis_dist, dtype=np.float64)
    spacing = float(np.median(np.diff(arr))) if arr.size > 1 else 1.0
    spacing = max(spacing, 1.0)

    # «Amplitud real»: todas las trazas comparten el pico global, así se ve caer
    # la amplitud con la distancia. Por defecto cada una se normaliza a la suya,
    # que es lo que sirve para comparar formas.
    pico_global = 1.0
    if vista.get("raw_amplitude"):
        with np.errstate(invalid="ignore"):
            picos = [float(np.nanmax(np.abs(s))) for s in matrix if np.any(np.isfinite(s))]
        pico_global = (max(picos) if picos else 1.0) or 1.0

    trazas = []
    for distancia, signal in zip(vis_dist, matrix):
        if vista.get("raw_amplitude"):
            pico = pico_global
        else:
            with np.errstate(invalid="ignore"):
                pico = float(np.nanmax(np.abs(signal))) if np.any(np.isfinite(signal)) else 1.0
            pico = pico or 1.0
        y = signal / pico * spacing * 0.4 + float(distancia)
        fila = _fila(common_time, y, max_points)
        if fila is None:
            continue
        label = frd.format_distance_label(distancia)
        marca = arrivals.get(label)
        fila.update({
            "label": label,
            "distance_m": _round6(float(distancia)),
            "peak": _round6(pico),
            # El arribo se dibuja como un tramo vertical centrado en la traza,
            # y sólo si está validado (`_redraw`: `arrival.reviewed`).
            "arrival_s": (_round6(float(marca.arrival_s))
                          if marca is not None and marca.reviewed else None),
        })
        trazas.append(fila)

    hammer = None
    if hammer_global:
        try:
            h_t, h_v = frd.hammer_global_time_signal(hammer_global)
            finite = np.isfinite(h_v)
            interp = (np.interp(common_time, h_t[finite], h_v[finite],
                                left=np.nan, right=np.nan)
                      if np.any(finite) else np.full(common_time.shape, np.nan))
            with np.errstate(invalid="ignore"):
                pico = float(np.nanmax(np.abs(interp))) if np.any(np.isfinite(interp)) else 1.0
            pico = pico or 1.0
            # El hammer va una separación por debajo de la traza más cercana.
            piso = float(min(vis_dist)) - spacing
            hammer = _fila(common_time, interp / pico * spacing * 0.4 + piso, max_points)
            if hammer is not None:
                hammer.update({"n": int(hammer_global.get("n", 0)), "base": _round6(piso)})
        except Exception:
            hammer = None

    lo = min(t["distance_m"] for t in trazas) - spacing * (1.6 if hammer else 0.6)
    hi = max(t["distance_m"] for t in trazas) + spacing * 0.6
    base.update({
        "traces": trazas,
        "hammer": hammer,
        "spacing": _round6(spacing),
        "t_min": _round6(float(common_time[0])),
        "t_max": _round6(float(common_time[-1])),
        "y_min": _round6(lo),
        "y_max": _round6(hi),
    })
    return base


def auto_polarity(raw_root: str | Path) -> dict:
    """«Auto polaridad»: las dos etapas de ``frd.auto_align_polarity``.

    No se reimplementa nada: se llama la misma función que el botón de la app
    (`_auto_polarity` :820), con los mismos argumentos y sobre el dataset
    **completo** —no el del grupo—, porque la etapa B encadena los promedios
    de todas las distancias desde la menor.

    Etapa A (intra-punto): las capturas SIN validar se enfasan contra el
    consenso de las validadas de su punto. Las validadas no se tocan: el flip
    queda como propuesta que se acepta al revisarlas en Capturas.
    Etapa B (inter-punto): si el promedio de un punto da en contrafase con el
    del vecino ya alineado, se invierte el punto COMPLETO.

    Persiste, así que llega a promedios, waterfall, MASW y export. Es
    idempotente: una segunda corrida no debería cambiar nada.
    """
    raw_root = Path(raw_root)
    with _write_lock:
        dataset = get_dataset(raw_root)
        path = frd.default_annotations_path(raw_root)
        anns = frd.load_annotations(path)
        settings = frd.load_filter_settings(frd.default_filter_settings_path(raw_root))
        reporte = frd.auto_align_polarity(
            dataset, anns,
            filter_settings=settings if settings.enabled else None,
            alignment_offsets=frd.load_alignment_offsets(
                frd.default_alignment_offsets_path(raw_root)),
            alignment_shot_offsets=frd.load_alignment_shot_offsets(
                frd.default_alignment_shot_offsets_path(raw_root)),
            disabled_folders=frd.load_disabled_folders(
                frd.default_disabled_folders_path(raw_root)),
        )
        etapa_a = list(reporte.get("stage_a_flipped", []))
        etapa_b = list(reporte.get("stage_b_flipped_distances", []))
        if etapa_a or etapa_b:
            frd.save_annotations(path, dataset, anns)

    return {
        "groups": int(reporte.get("groups", 0)),
        "stage_a_flipped": etapa_a,
        "stage_b_flipped": [frd.format_distance_label(d) for d in etapa_b],
        "stage_b_skipped": [frd.format_distance_label(d)
                            for d in sorted(set(reporte.get("stage_b_skipped_distances", [])))],
        "changed": bool(etapa_a or etapa_b),
        "path": str(path),
    }


def flip_distance(raw_root: str | Path, distance_m: float) -> dict:
    """«Invertir traza»: toggplea ``geo_flip`` en TODAS las capturas de esa
    distancia y lo guarda. NO es de vista — llega a promedios, MASW y export
    (es la excepción que documenta el panel)."""
    raw_root = Path(raw_root)
    with _write_lock:
        # Dataset COMPLETO, no el del grupo: el flip es de todas las capturas de
        # esa distancia, y `save_annotations` además guarda el conteo de
        # disparos del dataset entero en el encabezado del archivo.
        dataset = get_dataset(raw_root)
        path = frd.default_annotations_path(raw_root)
        anns = frd.load_annotations(path)
        cambiadas = frd.flip_distance_group(dataset, anns, float(distance_m),
                                            source="waterfall")
        if cambiadas:
            frd.save_annotations(path, dataset, anns)
    return {"distance_m": _round6(float(distance_m)), "changed": int(cambiadas),
            "path": str(path)}
