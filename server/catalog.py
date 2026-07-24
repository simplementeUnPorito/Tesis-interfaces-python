"""Catálogo de lo que llegó, sin exigir nada.

Motivo de existir: ``discover_dataset`` descarta las capturas que no tengan el par
hammer+geo, porque su unidad de trabajo es el *disparo* (necesita la fuente para
tener tiempo de viaje). Eso es correcto para MASW, pero como catálogo es
destructivo: una captura de un solo nodo desaparece de la vista y parece que nunca
llegó.

Este módulo lee la metadata cruda y lista TODO lo que hay — cualquier cantidad de
nodos, con martillo o sin él. El picking queda como una capa opcional encima,
disponible sólo cuando existe el par. Así el servidor nunca esconde un dato.

No toca ``field_review_data``: ese contrato lo comparten el servidor y la app
PyQt, y relajarlo cambiaría el significado de "disparo" para las dos.
"""

from __future__ import annotations

import json
from pathlib import Path

# f32 little-endian: 4 bytes por muestra. Es el formato que escribe export.js.
BYTES_PER_SAMPLE = 4


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _node_signal(folder: Path, capture_dir: Path, node: dict) -> tuple[Path | None, int]:
    """Ubica el .bin del nodo y estima cuántas muestras tiene.

    Se prueban las rutas relativas declaradas en la metadata y, si no hay, el
    nombre convencional dentro del directorio del nodo. Las rutas pueden ser
    relativas a la carpeta o a la captura, igual que en _resolve_node_file.
    """
    candidates: list[Path] = []
    for key in ("raw_file", "filt_file"):
        rel = node.get(key)
        if not rel:
            continue
        rel_path = Path(str(rel).replace("\\", "/"))
        candidates += [folder / rel_path, capture_dir / rel_path]
    data_dir = node.get("data_dir")
    if data_dir:
        d = Path(str(data_dir).replace("\\", "/"))
        candidates += [folder / d / "raw_f32le.bin", capture_dir / d / "raw_f32le.bin"]

    for cand in candidates:
        if cand.is_file():
            return cand, cand.stat().st_size // BYTES_PER_SAMPLE
    return None, 0


def _capture_dirs(folder: Path) -> list[Path]:
    root = folder / "captures"
    if root.is_dir():
        dirs = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name)
        if dirs:
            return dirs
    return [folder]   # layout viejo: la carpeta ES la captura


def folders_without_hammer(raw_root: Path) -> list[str]:
    """Carpetas donde ninguna captura tiene martillo.

    Base del barrido manual. Es conservador a propósito: si alguna captura de la
    carpeta sí tiene martillo, la carpeta no entra — no se tira dato bueno junto
    con el incompleto.
    """
    cat = scan_catalog(raw_root)
    out = []
    for folder in cat["folders"]:
        if folder["captures"] and not any(c["has_hammer"] for c in folder["captures"]):
            out.append(folder["folder"])
    return out


def scan_catalog(raw_root: Path) -> dict:
    """Todo lo que hay bajo raw_root, sin filtrar por rol ni por completitud."""
    raw_root = Path(raw_root)
    if not raw_root.is_dir():
        return {"raw_root": str(raw_root), "folders": [],
                "capture_count": 0, "node_count": 0}

    folders = []
    capture_count = 0
    node_count = 0
    for folder in sorted([p for p in raw_root.iterdir() if p.is_dir()],
                         key=lambda p: p.name):
        captures = []
        for capture_dir in _capture_dirs(folder):
            meta = _read_json(capture_dir / "metadata.json")
            if not meta:
                meta = _read_json(folder / "metadata.json")
            nodes = []
            for node in meta.get("nodes", []):
                # Sólo lo que realmente trajo señal: la metadata lista los 9 nodos
                # posibles y marcar 8 vacíos como "faltantes" sería ruido.
                path, samples = _node_signal(folder, capture_dir, node)
                if path is None or samples == 0:
                    continue
                fs = float(node.get("fs") or meta.get("fs") or 0.0)
                nodes.append({
                    "index": node.get("index", node.get("node_id")),
                    "pcb_id": str(node.get("pcb_id") or node.get("slave_id") or ""),
                    "role": str(node.get("role") or "").lower() or "unknown",
                    "hw_type": node.get("hw_type") or "",
                    "fs": fs,
                    "samples": samples,
                    "seconds": (samples / fs) if fs > 0 else 0.0,
                    "file": str(path.relative_to(raw_root)).replace("\\", "/"),
                })
            if not nodes:
                continue
            roles = {n["role"] for n in nodes}
            captures.append({
                "capture": capture_dir.name if capture_dir != folder else "(raíz)",
                "order": meta.get("capture_index", meta.get("order", 0)),
                "fs": float(meta.get("fs") or 0.0),
                "nodes": nodes,
                "has_hammer": "hammer" in roles,
                "has_geo": "geo" in roles,
                # Un disparo MASW necesita la fuente: sin martillo no hay primer
                # arribo que picar. Se dice explícitamente en vez de esconder la
                # captura, que era el problema original.
                "pickable": ("hammer" in roles and "geo" in roles),
            })
            capture_count += 1
            node_count += len(nodes)
        if captures:
            captures.sort(key=lambda c: (c["order"], c["capture"]))
            folders.append({"folder": folder.name, "captures": captures})

    return {
        "raw_root": str(raw_root),
        "folders": folders,
        "capture_count": capture_count,
        "node_count": node_count,
    }
