"""Borrado con banderas y subgrupos (PORT_PLAN §4).

Reemplaza los dos botones sueltos: una tabla de carpetas con banderas
combinables, subgrupos por sitio y por distancia, selección múltiple y una
confirmación que **lista** lo que va a borrar.

Reglas que no se negocian (§0.3 y §4 del plan):

* **Nada se borra solo.** Ni por estar incompleto, ni por antigüedad, ni por
  cuota: sólo por pedido explícito. Acá no hay ninguna acción automática.
* **El ZIP original se conserva** salvo pedido explícito.
* **El historial del trabajo queda marcado ``borrado``**, no se elimina: el
  registro de lo que llegó no se pierde.
* Una carpeta entra en «sin martillo» sólo si **ninguna** de sus capturas tiene
  martillo. Es la regla conservadora del barrido viejo y se mantiene.

La unidad de borrado es la **carpeta**, porque es lo que borra
``Pipeline.delete_folder``. Las banderas se calculan mirando todas sus capturas.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from ._gs import frd
from . import campaigns
from .captures import build_all_campaigns

# Banderas conocidas. El orden es el que se muestra.
FLAGS = (
    "sin_martillo", "sin_geofono", "sin_picking",
    "no_validada", "validada", "rechazada", "duplicada", "sin_lfs",
)

_lfs_cache: dict[str, tuple[float, set[str]]] = {}
_lfs_lock = threading.Lock()
_LFS_TTL = 30.0


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
    """Una fila por carpeta, con sus banderas, conteos y a qué subgrupos cae."""
    raw_root = Path(raw_root)
    datos = build_all_campaigns(raw_root, data_root)

    # Carpetas rechazadas en Enfase, por campaña: la clave puede venir con
    # sufijo ``::grupoN`` (ver groups.project_disabled_for_group).
    rechazadas: dict[str, set[str]] = {}
    for cid in campaigns.discover_campaign_ids(raw_root):
        ruta = campaigns.campaign_path(raw_root, cid)
        try:
            disabled = frd.load_disabled_folders(frd.default_disabled_folders_path(ruta))
        except Exception:
            disabled = {}
        acumulado: set[str] = set()
        for carpetas in disabled.values():
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

        distancias = sorted(f["distancias"])
        filas.append({
            "key": f"{cid}|{carpeta}",
            "campaign": cid,
            "campaign_name": f["campaign_name"],
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
        })

    conteos = {flag: sum(1 for r in filas if flag in r["flags"]) for flag in FLAGS}
    return {
        "rows": filas,
        "flags": list(FLAGS),
        "counts": conteos,
        "total": len(filas),
        # Si no se pudo preguntar a git, la web no muestra la bandera en vez de
        # decir que todo está subido.
        "lfs_available": pendientes_lfs is not None,
    }


def delete_folders(pipeline, keys: list[str], *, with_zip: bool = False) -> dict:
    """Borra las carpetas pedidas. Sólo por pedido explícito y una por una.

    ``keys`` son ``campaña|carpeta``, tal cual salen de ``build_rows``.
    """
    borradas, errores = [], []
    for key in keys:
        cid, _, carpeta = str(key).partition("|")
        if not carpeta:
            errores.append({"key": key, "error": "clave inválida"})
            continue
        try:
            res = pipeline.delete_folder(carpeta, with_zip=with_zip, campaign=cid)
        except Exception as exc:                        # pragma: no cover
            errores.append({"key": key, "error": str(exc)})
            continue
        if res.get("ok"):
            borradas.append(key)
        else:
            errores.append({"key": key, "error": res.get("error", "?")})
    invalidate_lfs_cache()
    return {"deleted": borradas, "errors": errores, "with_zip": bool(with_zip)}
