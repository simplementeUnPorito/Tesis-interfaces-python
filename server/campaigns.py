"""Campañas: qué conjuntos de datos hay bajo ``raw_root`` y cuáles se usan.

Por qué existe: ``data/raw`` **no** es una raíz de datos, es un contenedor de
campañas, y ``discover_dataset`` sólo mira un nivel (``<root>/<carpeta>/captures/``).
Apuntándolo a ``data/raw`` se veían 194 de ~1400 capturas y ninguna de las 606
marcas validadas a mano, porque el ``shot_id`` es un hash de la ruta relativa a
la raíz y las marcas se habían hecho con la campaña como raíz.

Modelo:

* Una **campaña** es un directorio que contiene directamente una o más
  *carpetas* (directorios con un ``captures/`` adentro). Se busca en
  ``raw_root`` y en cada uno de sus hijos, así que entran tanto el layout plano
  (``raw/<carpeta>/captures/``) como el anidado (``raw/<campaña>/<carpeta>/captures/``).
* Cada campaña es su propia raíz de datos: sus ``shot_id`` y su
  ``data/processed/<campaña>/`` no se tocan. Por eso las anotaciones que ya
  existen siguen valiendo sin migrar nada.
* La web las **une** para trabajar: cada fila lleva a qué campaña pertenece y
  las claves son ``campaña|carpeta|captura``, que es único entre campañas.

Lo que el usuario configura (nombre visible y si se usa o no) se guarda en
``<data_root>/campaigns.json`` y sobrevive reinicios.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

STATE_FILE = "campaigns.json"

# id de la campaña que es la propia raw_root (layout plano).
ROOT_ID = "."

_lock = threading.Lock()


def _is_folder(path: Path) -> bool:
    """Una *carpeta* de datos: tiene un ``captures/`` con al menos un directorio."""
    captures = path / "captures"
    if not captures.is_dir():
        return False
    try:
        return any(p.is_dir() for p in captures.iterdir())
    except OSError:
        return False


def _has_folders(path: Path) -> bool:
    try:
        return any(_is_folder(p) for p in path.iterdir() if p.is_dir())
    except OSError:
        return False


def discover_campaign_ids(raw_root: str | Path) -> list[str]:
    """Ids de campaña bajo ``raw_root``, en orden estable.

    El id es la ruta relativa a ``raw_root`` (``"."`` para la raíz misma).
    """
    root = Path(raw_root)
    if not root.is_dir():
        return []
    ids: list[str] = []
    if _has_folders(root):
        ids.append(ROOT_ID)
    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        if _has_folders(child):
            ids.append(child.name)
    return ids


def campaign_path(raw_root: str | Path, campaign_id: str) -> Path:
    root = Path(raw_root)
    return root if campaign_id == ROOT_ID else root / campaign_id


def nested_ids(raw_root: str | Path) -> set[str]:
    """Campañas que además son hijas de ``raw_root``.

    Sus carpetas no deben contarse dos veces: ``data/raw/Canchita`` tiene un
    ``captures/`` propio (o sea que también califica como *carpeta* de la
    campaña raíz) y además cuelga ``muestra*/captures/``. Se cuenta como
    campaña y se excluye de la raíz.
    """
    return {cid for cid in discover_campaign_ids(raw_root) if cid != ROOT_ID}


# ── Configuración persistida ─────────────────────────────────────────────────
def _state_path(data_root: str | Path) -> Path:
    return Path(data_root) / STATE_FILE


def load_config(data_root: str | Path) -> dict:
    path = _state_path(data_root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data.get("campaigns", {}) if isinstance(data, dict) else {}


def save_config(data_root: str | Path, config: dict) -> Path:
    path = _state_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps({"campaigns": config}, indent=2, ensure_ascii=False)
    with _lock:
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(blob, encoding="utf-8")
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
    return path


def update_campaign(data_root: str | Path, campaign_id: str, *,
                    name: str | None = None, enabled: bool | None = None) -> dict:
    """Renombra y/o habilita una campaña. Devuelve su entrada ya actualizada."""
    config = load_config(data_root)
    entry = dict(config.get(campaign_id, {}))
    if name is not None:
        clean = " ".join(str(name).split())[:120]
        # Un nombre vacío vuelve al nombre del directorio, no deja la campaña
        # sin etiqueta.
        if clean:
            entry["name"] = clean
        else:
            entry.pop("name", None)
    if enabled is not None:
        entry["enabled"] = bool(enabled)
    config[campaign_id] = entry
    save_config(data_root, config)
    return entry


def display_name(campaign_id: str, config: dict) -> str:
    entry = config.get(campaign_id) or {}
    name = entry.get("name")
    if name:
        return str(name)
    return "raíz" if campaign_id == ROOT_ID else campaign_id


def is_enabled(campaign_id: str, config: dict) -> bool:
    """Por defecto todas se usan: la idea es que ningún dato quede afuera sin
    que alguien lo decida."""
    entry = config.get(campaign_id) or {}
    return bool(entry.get("enabled", True))


def enabled_ids(raw_root: str | Path, data_root: str | Path) -> list[str]:
    config = load_config(data_root)
    return [cid for cid in discover_campaign_ids(raw_root) if is_enabled(cid, config)]


# ── Metadata barata para el panel ────────────────────────────────────────────
def _folders_of(path: Path, skip: set[str]) -> list[Path]:
    try:
        return sorted(p for p in path.iterdir()
                      if p.is_dir() and p.name not in skip and _is_folder(p))
    except OSError:
        return []


def campaign_info(raw_root: str | Path, data_root: str | Path, campaign_id: str) -> dict:
    """Metadata sin abrir una sola señal: sólo recorrer directorios.

    El conteo de disparos y de validadas es caro (hay que escanear y leer las
    anotaciones), así que no va acá: lo agrega ``routers`` cuando la campaña
    está habilitada y su escaneo ya está cacheado.
    """
    root = Path(raw_root)
    path = campaign_path(root, campaign_id)
    # No se excluyen las campañas anidadas: un directorio puede tener a la vez
    # su propio `captures/` (que cuenta para la raíz) y carpetas adentro con el
    # suyo (que son esta campaña). Son capturas distintas, no se duplican.
    folders = _folders_of(path, set())

    captures = 0
    for folder in folders:
        try:
            captures += sum(1 for p in (folder / "captures").iterdir() if p.is_dir())
        except OSError:
            pass

    config = load_config(data_root)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0

    return {
        "id": campaign_id,
        "path": str(path),
        "name": display_name(campaign_id, config),
        "renamed": bool((config.get(campaign_id) or {}).get("name")),
        "enabled": is_enabled(campaign_id, config),
        "folder_count": len(folders),
        "capture_count": captures,
        "folders": [f.name for f in folders[:50]],
        "mtime": mtime,
        # Dónde vive lo que se anota de esta campaña. Es información, no una
        # opción: lo decide `_procesados_dir_for` por el nombre del directorio.
        "processed_dir_name": path.name,
    }


def list_campaigns(raw_root: str | Path, data_root: str | Path) -> list[dict]:
    return [campaign_info(raw_root, data_root, cid)
            for cid in discover_campaign_ids(raw_root)]
