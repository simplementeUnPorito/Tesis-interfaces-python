"""Filas de la tabla de Capturas, con las mismas columnas y estados que la app.

Equivale a ``_populate_table`` / ``_update_table_row`` / ``_estado_display`` de
``field_review_app.py`` (:902-939): Estado · Dist · Trigger s · Carpeta ·
Captura · Hash.

Diferencia deliberada con la app: la app lista **disparos**
(``discover_dataset``, que exige el par hammer+geo) y acá se listan **todas las
capturas** del catálogo, tengan par o no. Las que no son disparo no se
esconden: aparecen con su estado real y se pueden graficar igual. Nada de lo
que llegó desaparece de la vista (PORT_PLAN §0.3, §5.5).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from . import campaigns
from ._gs import frd
from .catalog import scan_catalog
from .datacache import get_dataset, tree_signature


def _estado(reviewed: bool, accepted: bool) -> str:
    """`_estado_display` de la app (:931). Toda marca nueva empieza sin validar."""
    if not reviewed:
        return "Sin validar"
    return "OK" if accepted else "Rechazada"


# ── Cache del escaneo estructural ────────────────────────────────────────────
# `scan_catalog` + `discover_dataset` tardan ~30 s sobre una campaña de 944
# capturas (discover_dataset hashea archivos para detectar duplicados), y la
# tabla se refresca cada pocos segundos: sin cache el servidor no da abasto y
# las peticiones se encolan.
#
# Se cachea SÓLO lo estructural, que cambia cuando entra una captura nueva.
# Las anotaciones se releen siempre: son baratas y son lo que más cambia (y lo
# que la app PyQt puede tocar por detrás mientras la web está abierta).
_SCAN_TTL_S = 60.0
_scan_lock = threading.Lock()
_scan_cache: dict[str, tuple[float, tuple, dict, dict, int]] = {}


def invalidate_scan(raw_root: str | Path | None = None) -> None:
    """Tirar el cache. La llama el Pipeline cuando termina de ingestar."""
    with _scan_lock:
        if raw_root is None:
            _scan_cache.clear()
        else:
            _scan_cache.pop(str(Path(raw_root)), None)


def _scan(raw_root: Path) -> tuple[dict, dict, int]:
    """``(catálogo, {(carpeta, captura): [disparos]}, carpetas duplicadas)``.

    Es una LISTA de disparos por captura: con N geófonos en el tendido, un
    mismo golpe da N disparos (uno por receptor, cada uno a su distancia).
    """
    key = str(raw_root)
    sig = tree_signature(raw_root)
    now = time.monotonic()
    with _scan_lock:
        hit = _scan_cache.get(key)
        if hit is not None and (now - hit[0]) < _SCAN_TTL_S and hit[1] == sig:
            return hit[2], hit[3], hit[4]

    cat = scan_catalog(raw_root)
    shots_por_captura: dict[tuple[str, str], list] = {}
    duplicate_folder_count = 0
    try:
        dataset = get_dataset(raw_root)
        duplicate_folder_count = int(dataset.duplicate_folder_count)
        for shot in dataset.shots:
            shots_por_captura.setdefault((shot.folder_name, shot.capture_name), []).append(shot)
    except FileNotFoundError:
        pass   # todavía no llegó nada

    with _scan_lock:
        _scan_cache[key] = (time.monotonic(), sig, cat, shots_por_captura, duplicate_folder_count)
    return cat, shots_por_captura, duplicate_folder_count


def build_capture_rows(raw_root: str | Path, *, campaign_id: str = "",
                       campaign_name: str = "", skip_folders: set[str] | None = None) -> dict:
    """Una fila por captura, con el pick encima cuando la captura es un disparo.

    ``raw_root`` acá es la raíz de **una campaña**: sus ``shot_id`` y sus
    anotaciones son relativos a ella. ``skip_folders`` saca las carpetas que en
    realidad son otra campaña anidada, para no contarlas dos veces.
    """
    raw_root = Path(raw_root)
    skip_folders = skip_folders or set()
    cat, shots_por_captura, duplicate_folder_count = _scan(raw_root)
    try:
        anns = frd.load_annotations(frd.default_annotations_path(raw_root))
    except FileNotFoundError:
        anns = {}

    rows: list[dict] = []
    for folder in cat["folders"]:
        if folder["folder"] in skip_folders:
            continue
        for capture in folder["captures"]:
            key = (folder["folder"], capture["capture"])
            shots = shots_por_captura.get(key) or []
            nodes = capture["nodes"]
            fs = capture["fs"] or (nodes[0]["fs"] if nodes else 0.0)
            seconds = max((n["seconds"] for n in nodes), default=0.0)

            def base_row(geo_key: str = "") -> dict:
                return {
                    # Única entre campañas Y entre geófonos: dos campañas pueden
                    # tener el mismo nombre de carpeta y captura, y una captura
                    # puede dar un disparo por cada receptor del tendido.
                    "key": f'{campaign_id}|{folder["folder"]}|{capture["capture"]}'
                           + (f'|{geo_key}' if geo_key else ''),
                    "campaign": campaign_id,
                    "campaign_name": campaign_name,
                    "folder": folder["folder"],
                    "capture": capture["capture"],
                    "order": capture["order"],
                    "fs": fs,
                    "seconds": seconds,
                    "has_hammer": capture["has_hammer"],
                    "has_geo": capture["has_geo"],
                    "nodes": nodes,
                    "shot_id": None,
                    "hash": "",
                    "geo_label": "",
                    "geo_count": len(shots),
                    "distance_m": None,
                    "trigger_s": None,
                    "reviewed": False,
                    "accepted": True,
                    "notes": "",
                    "geo_flip": False,
                    "source": None,
                    "duplicate_of": None,
                    "estado": "",
                    # Se puede dibujar todo lo que tenga al menos un canal con
                    # señal: el estado no decide si se grafica (pedido explícito).
                    "plottable": bool(capture["has_hammer"] or capture["has_geo"]),
                }

            if not shots:
                row = base_row()
                # No es disparo. Se dice por qué, no se esconde.
                if not capture["has_hammer"] and not capture["has_geo"]:
                    row["estado"] = "Sin señal"
                elif not capture["has_hammer"]:
                    row["estado"] = "Sin martillo"
                elif not capture["has_geo"]:
                    row["estado"] = "Sin geófono"
                else:
                    # Tiene los dos roles pero discover_dataset no la tomó: la
                    # descartó el dedup por firma de señal.
                    row["estado"] = "Duplicada"
                rows.append(row)
                continue

            # Trigger de la captura: los N geófonos comparten el golpe, así que
            # el que ya esté marcado en cualquiera de ellos vale para todos.
            marcas = [anns[s.shot_id] for s in shots if s.shot_id in anns]
            validadas = [a for a in marcas if a.reviewed]
            trigger_captura = (validadas or marcas or [None])[0]

            # Una fila por geófono del tendido: mismo golpe, distinta distancia,
            # y cada uno con su propia marca.
            for shot in shots:
                geo = shot.geo
                etiqueta = geo.pcb_id or geo.label or (
                    f"n{geo.node_index}" if geo.node_index is not None else "geo")
                # Con un solo receptor no hay nada que desambiguar: la clave
                # queda como estaba y la columna Geófono no se muestra.
                row = base_row("" if len(shots) == 1 else etiqueta)
                ann = anns.get(shot.shot_id)
                row.update({
                    # Si es disparo, los dos canales existen por definición: la
                    # autoridad es discover_dataset, no el conteo del catálogo.
                    "has_hammer": True,
                    "has_geo": True,
                    "plottable": True,
                    "shot_id": shot.shot_id,
                    "hash": shot.folder_hash[:8],
                    "geo_label": etiqueta,
                    "distance_m": float(ann.distance_m) if ann else float(shot.distance_m),
                    # Sin marca propia se muestra el trigger heredado de la
                    # captura, que es el que se va a dibujar.
                    "trigger_s": float(ann.trigger_s) if ann else (
                        float(trigger_captura.trigger_s) if trigger_captura else 0.0),
                    "trigger_heredado": ann is None and trigger_captura is not None,
                    "reviewed": bool(ann.reviewed) if ann else False,
                    "accepted": bool(ann.accepted) if ann else True,
                    "notes": (ann.notes or "") if ann else "",
                    "geo_flip": bool(ann.geo_flip) if ann else False,
                    "source": (ann.source or None) if ann else None,
                    "duplicate_of": shot.duplicate_of,
                    "estado": _estado(bool(ann.reviewed) if ann else False,
                                      bool(ann.accepted) if ann else True),
                })
                rows.append(row)

    return {
        "raw_root": str(raw_root),
        "rows": rows,
        "total": len(rows),
        "shot_count": sum(len(v) for v in shots_por_captura.values()),
        "reviewed_count": sum(1 for a in anns.values() if a.reviewed),
        "duplicate_folder_count": duplicate_folder_count,
    }


def build_all_campaigns(raw_root: str | Path, data_root: str | Path) -> dict:
    """Filas de **todas las campañas habilitadas**, unidas en una sola tabla.

    Cada campaña se escanea con su propia raíz, así que sus ``shot_id`` y sus
    anotaciones son las que ya existen: unir no migra ni reescribe nada.
    """
    raw_root = Path(raw_root)
    config = campaigns.load_config(data_root)
    todas = campaigns.discover_campaign_ids(raw_root)

    rows: list[dict] = []
    total_shots = 0
    total_reviewed = 0
    total_dups = 0
    usadas: list[dict] = []

    for cid in todas:
        enabled = campaigns.is_enabled(cid, config)
        name = campaigns.display_name(cid, config)
        if not enabled:
            usadas.append({"id": cid, "name": name, "enabled": False,
                           "rows": 0, "reviewed": 0})
            continue
        part = build_capture_rows(
            campaigns.campaign_path(raw_root, cid),
            campaign_id=cid,
            campaign_name=name,
        )
        rows.extend(part["rows"])
        total_shots += part["shot_count"]
        total_reviewed += part["reviewed_count"]
        total_dups += part["duplicate_folder_count"]
        usadas.append({"id": cid, "name": name, "enabled": True,
                       "rows": len(part["rows"]), "reviewed": part["reviewed_count"]})

    return {
        "raw_root": str(raw_root),
        "rows": rows,
        "total": len(rows),
        "shot_count": total_shots,
        "reviewed_count": total_reviewed,
        "duplicate_folder_count": total_dups,
        "campaigns": usadas,
    }


# ── Orden "Pico a pico" ──────────────────────────────────────────────────────
# La app ordena por max-min del geo, mayor primero: empieza por la señal donde
# el golpe se ve más fácil, para calibrar el resto contra esa
# (`_compute_row_order` :872, `_shot_peak_to_peak` :1072).
#
# Calcularlo exige leer el geo entero de cada captura, así que va en un endpoint
# aparte: la tabla pinta al instante y el orden llega cuando está. Se cachea por
# archivo (ruta + mtime + tamaño): invertir una señal no cambia su max-min, así
# que el cache no se invalida al usar geo_flip, igual que en la app.
_P2P_CACHE: dict[tuple[str, int, int], float] = {}


def _geo_channels(raw_root: Path, folder: str, capture: str) -> list:
    """Todos los geófonos de la captura, en el mismo orden que los disparos."""
    folder_path = raw_root / folder
    capture_dir = folder_path if capture == "(raíz)" else folder_path / "captures" / capture
    if not capture_dir.is_dir():
        return []
    geos = [ch for ch in frd.discover_capture_channels(folder_path, capture_dir)
            if ch.role == "geo"]
    geos.sort(key=lambda ch: (ch.node_index if ch.node_index is not None else 999, ch.pcb_id))
    return geos


def peak_to_peak_map(raw_root: str | Path, data_root: str | Path) -> dict[str, float]:
    """``{key: pico a pico del geo}`` de todas las campañas habilitadas."""
    root = Path(raw_root)
    config = campaigns.load_config(data_root)
    out: dict[str, float] = {}
    for cid in campaigns.discover_campaign_ids(root):
        if not campaigns.is_enabled(cid, config):
            continue
        out.update(_peak_to_peak_campaign(campaigns.campaign_path(root, cid), cid, set()))
    return out


def _peak_to_peak_campaign(raw_root: Path, campaign_id: str,
                           skip_folders: set[str]) -> dict[str, float]:
    cat, _shots, _dups = _scan(raw_root)
    out: dict[str, float] = {}
    for folder in cat["folders"]:
        if folder["folder"] in skip_folders:
            continue
        for capture in folder["captures"]:
            base = f'{campaign_id}|{folder["folder"]}|{capture["capture"]}'
            geos = _geo_channels(raw_root, folder["folder"], capture["capture"]) \
                if capture["has_geo"] else []
            if not geos:
                out[base] = 0.0
                continue
            # Una entrada por receptor, con la misma clave que arma la fila:
            # con un solo geófono es la clave sin sufijo.
            for channel in geos:
                etiqueta = channel.pcb_id or channel.label or (
                    f"n{channel.node_index}" if channel.node_index is not None else "geo")
                key = base if len(geos) == 1 else f"{base}|{etiqueta}"
                path = channel.signal_file(prefer_filtered=False)
                if path is None:
                    out[key] = 0.0
                    continue
                stat = Path(path).stat()
                cache_key = (str(path), int(stat.st_mtime), int(stat.st_size))
                value = _P2P_CACHE.get(cache_key)
                if value is None:
                    signal = frd.load_signal(channel, prefer_filtered=False, apply_invert=True)
                    value = float(frd.peak_to_peak(signal))
                    _P2P_CACHE[cache_key] = value
                out[key] = value
    return out
