"""Cuarentena reversible con banderas y subgrupos (PORT_PLAN §4).

El tab conserva el nombre histórico «Borrado», pero ninguna ruta elimina
archivos. Las carpetas seleccionadas reciben la bandera compartida
``frd.GLOBAL_DISABLED_LABEL`` en ``alignment_disabled_folders.json``. Así
quedan fuera del pipeline científico y pueden restaurarse sin perder raw, ZIP,
anotaciones ni procedencia.

Reglas vigentes:

* **Nada se borra**, ni siquiera después de confirmar.
* **El ZIP original siempre se conserva.**
* Desactivar y restaurar requieren preview exacta y dejan historial.
* Una carpeta entra en «sin martillo» sólo si **ninguna** de sus capturas tiene
  martillo. Es la regla conservadora del barrido viejo y se mantiene.

La unidad de cuarentena es la **carpeta**. Las banderas diagnósticas se
calculan mirando todas sus capturas.
"""

from __future__ import annotations

import subprocess
import secrets
import threading
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from ._gs import frd
from . import campaigns
from .captures import build_all_campaigns
from .state import (RevisionConflict, atomic_write_json, locked, read_json,
                    require_revision, revision)

# Banderas conocidas. El orden es el que se muestra.
FLAGS = (
    "sin_martillo", "sin_geofono", "sin_picking",
    "no_validada", "validada", "rechazada", "duplicada", "sin_lfs",
    "desactivada", "ausente",
)

_lfs_cache: dict[str, tuple[float, set[str]]] = {}
_lfs_lock = threading.Lock()
_LFS_TTL = 30.0
_preview_lock = threading.Lock()
_preview_tokens: dict[str, dict] = {}
_PREVIEW_TTL = 10 * 60.0


def _sin_subir(raw_root: Path) -> set[str] | None:
    """Rutas bajo ``raw_root`` que todavía no están commiteadas en el repo de datos.

    «Sin subir a LFS» se resuelve preguntándole a git, no adivinando: un archivo
    que no está commiteado no viajó a ningún lado. Devuelve ``None`` si esto no
    es un repo git (entonces la bandera no se muestra, en vez de mentir).

    Se cachea 30 s: es un subproceso y la tabla se puede refrescar seguido.
    """
    clave = str(raw_root)
    ahora = time.monotonic()
    with _lfs_lock:
        cacheado = _lfs_cache.get(clave)
        if cacheado and (ahora - cacheado[0]) < _LFS_TTL:
            return cacheado[1]
    try:
        salida = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal", "."],
            cwd=str(raw_root), capture_output=True, text=True, timeout=30,
        )
        if salida.returncode != 0:
            return None
    except (OSError, subprocess.SubprocessError):
        return None

    pendientes: set[str] = set()
    for linea in salida.stdout.splitlines():
        ruta = linea[3:].strip().strip('"')
        if not ruta:
            continue
        # git informa relativo al cwd; sólo interesa el primer tramo (la carpeta).
        pendientes.add(ruta.replace("\\", "/").split("/")[0])
    with _lfs_lock:
        _lfs_cache[clave] = (ahora, pendientes)
    return pendientes


def invalidate_lfs_cache() -> None:
    with _lfs_lock:
        _lfs_cache.clear()


def _fecha(path: Path) -> str:
    try:
        return time.strftime("%Y-%m-%d", time.localtime(path.stat().st_mtime))
    except OSError:
        return ""


def build_rows(raw_root: str | Path, data_root: str | Path) -> dict:
    """Una fila por carpeta, incluidas las que están en cuarentena."""
    raw_root = Path(raw_root)
    datos = build_all_campaigns(
        raw_root,
        data_root,
        include_pipeline_disabled=True,
    )

    # Carpetas rechazadas en Enfase, por campaña: la clave puede venir con
    # sufijo ``::grupoN`` (ver groups.project_disabled_for_group).
    rechazadas: dict[str, set[str]] = {}
    desactivadas: dict[str, set[str]] = {}
    for cid in campaigns.discover_campaign_ids(raw_root):
        ruta = campaigns.campaign_path(raw_root, cid)
        try:
            disabled = frd.load_disabled_folders(frd.default_disabled_folders_path(ruta))
        except Exception:
            disabled = {}
        desactivadas[cid] = set(disabled.get(frd.GLOBAL_DISABLED_LABEL, ()))
        acumulado: set[str] = set()
        for label, carpetas in disabled.items():
            if label == frd.GLOBAL_DISABLED_LABEL:
                continue
            acumulado.update(carpetas)
        rechazadas[cid] = acumulado

    por_carpeta: dict[tuple[str, str], dict] = {}
    for row in datos.get("rows", []):
        clave = (row["campaign"], row["folder"])
        f = por_carpeta.get(clave)
        if f is None:
            f = por_carpeta[clave] = {
                "campaign": row["campaign"],
                "campaign_name": row["campaign_name"],
                "folder": row["folder"],
                "capturas": 0,
                "con_martillo": 0,
                "con_geo": 0,
                "con_shot": 0,
                "validadas": 0,
                "duplicadas": 0,
                "distancias": set(),
            }
        f["capturas"] += 1
        f["con_martillo"] += 1 if row.get("has_hammer") else 0
        f["con_geo"] += 1 if row.get("has_geo") else 0
        f["con_shot"] += 1 if row.get("shot_id") else 0
        f["validadas"] += 1 if row.get("reviewed") else 0
        f["duplicadas"] += 1 if row.get("estado") == "Duplicada" else 0
        if row.get("distance_m") is not None:
            f["distancias"].add(round(float(row["distance_m"]), 1))

    pendientes_lfs = _sin_subir(raw_root)

    filas = []
    for (cid, carpeta), f in sorted(por_carpeta.items()):
        raiz_campaña = campaigns.campaign_path(raw_root, cid)
        ruta = raiz_campaña / carpeta
        # Para el chequeo de LFS lo que importa es el primer tramo relativo al
        # repo de datos: en la campaña raíz es la carpeta, si no la campaña.
        tramo = carpeta if cid == campaigns.ROOT_ID else cid
        banderas = []
        # Regla conservadora: sólo si NINGUNA captura tiene martillo.
        if f["con_martillo"] == 0:
            banderas.append("sin_martillo")
        if f["con_geo"] == 0:
            banderas.append("sin_geofono")
        if f["con_shot"] == 0:
            banderas.append("sin_picking")
        if f["validadas"]:
            banderas.append("validada")
        if f["validadas"] < f["capturas"]:
            banderas.append("no_validada")
        if carpeta in rechazadas.get(cid, ()):
            banderas.append("rechazada")
        if f["duplicadas"]:
            banderas.append("duplicada")
        if pendientes_lfs is not None and tramo in pendientes_lfs:
            banderas.append("sin_lfs")
        if carpeta in desactivadas.get(cid, ()):
            banderas.append("desactivada")

        distancias = sorted(f["distancias"])
        filas.append({
            "key": f"{cid}|{carpeta}",
            "campaign": cid,
            "campaign_name": f["campaign_name"],
            "site": f["campaign_name"],
            "folder": carpeta,
            "captures": f["capturas"],
            "validated": f["validadas"],
            "duplicates": f["duplicadas"],
            "date": _fecha(ruta),
            "distances": distancias,
            # Subgrupo por distancia: la menor de la carpeta, que es con la que
            # se la identifica en el tendido.
            "distance_group": (frd.format_distance_label(distancias[0])
                               if distancias else "sin distancia"),
            "flags": banderas,
            "disabled": "desactivada" in banderas,
            "missing": False,
        })

    # Si alguien movió/renombró una carpeta por fuera del servidor, la bandera
    # no debe quedar huérfana e imposible de restaurar desde la UI.
    known_keys = {row["key"] for row in filas}
    config = campaigns.load_config(data_root)
    for cid, folders in desactivadas.items():
        for folder in sorted(folders):
            key = f"{cid}|{folder}"
            if key in known_keys:
                continue
            filas.append({
                "key": key,
                "campaign": cid,
                "campaign_name": campaigns.display_name(cid, config),
                "site": campaigns.display_name(cid, config),
                "folder": folder,
                "captures": 0,
                "validated": 0,
                "duplicates": 0,
                "date": "",
                "distances": [],
                "distance_group": "sin distancia",
                "flags": ["desactivada", "ausente"],
                "disabled": True,
                "missing": True,
            })
    filas.sort(key=lambda row: (
        str(row["campaign_name"]).casefold(),
        str(row["distance_group"]),
        str(row["folder"]).casefold(),
    ))

    conteos = {flag: sum(1 for r in filas if flag in r["flags"]) for flag in FLAGS}
    return {
        "rows": filas,
        "flags": list(FLAGS),
        "counts": conteos,
        "total": len(filas),
        # Si no se pudo preguntar a git, la web no muestra la bandera en vez de
        # decir que todo está subido.
        "lfs_available": pendientes_lfs is not None,
        "revision": {
            cid: revision(
                frd.default_disabled_folders_path(
                    campaigns.campaign_path(raw_root, cid)
                )
            )
            for cid in campaigns.discover_campaign_ids(raw_root)
        },
    }


def _disabled_state_path(raw_root: str | Path, campaign_id: str) -> Path:
    return frd.default_disabled_folders_path(
        campaigns.campaign_path(raw_root, campaign_id)
    )


def preview_deactivation(
    raw_root: str | Path,
    data_root: str | Path,
    keys: list[str],
    *,
    action: str = "disable",
) -> dict:
    action = str(action or "disable").strip().lower()
    if action not in {"disable", "restore"}:
        raise ValueError("action debe ser 'disable' o 'restore'")
    catalog = build_rows(raw_root, data_root)
    by_key = {row["key"]: row for row in catalog["rows"]}
    unique = list(dict.fromkeys(str(key) for key in keys))
    unknown = [key for key in unique if key not in by_key]
    if unknown:
        raise ValueError(f"carpetas desconocidas: {', '.join(unknown[:5])}")
    selected = [by_key[key] for key in unique]
    revisions = {
        row["campaign"]: revision(
            _disabled_state_path(raw_root, row["campaign"])
        )
        for row in selected
    }
    token = secrets.token_urlsafe(24)
    now = time.time()
    with _preview_lock:
        for old, entry in list(_preview_tokens.items()):
            if now - entry["created"] > _PREVIEW_TTL:
                _preview_tokens.pop(old, None)
        _preview_tokens[token] = {
            "created": now,
            "keys": unique,
            "action": action,
            "revisions": revisions,
        }
    return {
        "token": token,
        "expires_in_s": int(_PREVIEW_TTL),
        "action": action,
        "zip_preserved": True,
        "physical_deletion": False,
        "count": len(selected),
        "captures": sum(int(row["captures"]) for row in selected),
        "rows": selected,
        "base_revisions": revisions,
    }


def consume_preview(token: str, keys: list[str], action: str) -> dict[str, str]:
    now = time.time()
    with _preview_lock:
        entry = _preview_tokens.pop(str(token), None)
    if entry is None or now - entry["created"] > _PREVIEW_TTL:
        raise ValueError("confirmación vencida o desconocida; generá una nueva")
    if entry["keys"] != list(dict.fromkeys(str(key) for key in keys)):
        raise ValueError("la selección cambió después de confirmar")
    if entry["action"] != str(action):
        raise ValueError("la acción cambió después de confirmar")
    return dict(entry["revisions"])


def record_deactivation(data_root: str | Path, outcome: dict, keys: list[str]) -> None:
    """Historial append-only; conserva el nombre antiguo para continuidad."""
    path = Path(data_root) / "deletion_history.json"
    with locked(path):
        raw, _ = read_json(path, {"events": []})
        if not isinstance(raw, dict):
            raw = {"events": []}
        events = list(raw.get("events") or [])
        events.append({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "keys": list(keys),
            **outcome,
        })
        raw["events"] = events[-2000:]
        atomic_write_json(path, raw)


def set_folders_disabled(
    pipeline,
    keys: list[str],
    *,
    disabled: bool,
    expected_revisions: dict[str, str],
) -> dict:
    """Activa o restaura la bandera global con locks y revisión optimista."""
    raw_root = Path(pipeline.raw_root)
    known_campaigns = set(campaigns.discover_campaign_ids(raw_root))
    by_campaign: dict[str, list[str]] = {}
    errors: list[dict] = []
    for key in list(dict.fromkeys(str(item) for item in keys)):
        cid, sep, folder = key.partition("|")
        if not sep or cid not in known_campaigns or not folder:
            errors.append({"key": key, "error": "clave inválida o campaña desconocida"})
            continue
        campaign_root = campaigns.campaign_path(raw_root, cid).resolve()
        target = (campaign_root / Path(folder).name).resolve()
        unsafe = (
            Path(folder).name != folder
            or target.parent != campaign_root
            or not target.is_relative_to(raw_root.resolve())
        )
        unavailable = disabled and not target.is_dir()
        wrong_type = target.exists() and not target.is_dir()
        if unsafe or unavailable or wrong_type:
            errors.append({"key": key, "error": "carpeta inválida o fuera de raw_root"})
            continue
        by_campaign.setdefault(cid, []).append(folder)

    paths = {
        cid: _disabled_state_path(raw_root, cid)
        for cid in by_campaign
    }
    changed: list[str] = []
    unchanged: list[str] = []
    with ExitStack() as stack:
        for path in sorted(paths.values(), key=lambda item: str(item.resolve()).casefold()):
            stack.enter_context(locked(path))
            stack.enter_context(frd.disabled_folders_lock(path))
        for cid, path in paths.items():
            require_revision(path, expected_revisions.get(cid, ""))

        states = {
            cid: frd.load_disabled_folders(path)
            for cid, path in paths.items()
        }
        for cid, folders in by_campaign.items():
            state = states[cid]
            current = state.setdefault(frd.GLOBAL_DISABLED_LABEL, [])
            for folder in folders:
                key = f"{cid}|{folder}"
                if disabled:
                    if folder in current:
                        unchanged.append(key)
                    else:
                        current.append(folder)
                        changed.append(key)
                elif folder in current:
                    current.remove(folder)
                    changed.append(key)
                else:
                    unchanged.append(key)
            if not current:
                state.pop(frd.GLOBAL_DISABLED_LABEL, None)

        for cid, path in paths.items():
            frd.save_disabled_folders(
                path,
                states[cid],
                replace_global=True,
            )

    # El cache estructural conserva los datos, pero la vista de Capturas debe
    # reflejar la bandera inmediatamente.
    from .captures import invalidate_scan
    from .datacache import invalidate

    for cid in paths:
        campaign_root = campaigns.campaign_path(raw_root, cid)
        invalidate_scan(campaign_root)
        invalidate(campaign_root)
    invalidate_lfs_cache()
    action = "disable" if disabled else "restore"
    return {
        "action": action,
        "disabled": changed if disabled else [],
        "restored": changed if not disabled else [],
        "unchanged": unchanged,
        "errors": errors,
        "deleted": [],
        "zip_preserved": True,
        "physical_deletion": False,
    }


# Compatibilidad interna defensiva: aunque código viejo invoque este símbolo,
# no existe ningún camino que elimine archivos.
def delete_folders(pipeline, keys: list[str], *, with_zip: bool = False) -> dict:
    return {
        "deleted": [],
        "errors": [{
            "key": str(key),
            "error": "el borrado físico está deshabilitado; use la cuarentena",
        } for key in keys],
        "with_zip": False,
        "zip_preserved": True,
        "physical_deletion": False,
    }


# Alias de lectura para integraciones que todavía nombran la operación vieja.
def preview_deletion(
    raw_root: str | Path,
    data_root: str | Path,
    keys: list[str],
    *,
    with_zip: bool = False,
) -> dict:
    return preview_deactivation(raw_root, data_root, keys, action="disable")


record_deletion = record_deactivation
