"""Catálogo de lo que llegó, sin exigir nada.

Motivo de existir: ``discover_dataset`` descarta las capturas que no tengan el par
hammer+geo, porque su unidad de trabajo es el *disparo* (necesita la fuente para
tener tiempo de viaje). Eso es correcto para MASW, pero como catálogo es
destructivo: una captura de un solo nodo desaparece de la vista y parece que nunca
llegó.

Este módulo lee la metadata cruda y lista TODO lo que hay — cualquier cantidad de
nodos, con martillo o sin él. El picking queda como una capa opcional encima,
disponible sólo cuando existe el par. Así el servidor nunca esconde un dato.

No relaja el contrato de ``field_review_data``: no cambia qué es un "disparo".
Sí usa su ``node_role`` para deducir el rol de cada nodo, en vez de mirar sólo
``node["role"]`` como hacía antes. Con dos deducciones distintas el catálogo
marcaba "sin martillo" capturas que ``discover_dataset`` sí tomaba como disparo
(186 vs 194): la columna Estado le mentía al usuario en 8 capturas.
"""

from __future__ import annotations

import json
from pathlib import Path

from ._gs import frd

# f32 little-endian: 4 bytes por muestra. Es el formato que escribe export.js.
BYTES_PER_SAMPLE = 4


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _node_signal(folder: Path, capture_dir: Path, node: dict) -> tuple[Path | None, int]:
    """Ubica el .bin del nodo y estima cuántas muestras tiene.

    La resolución la hace ``frd.resolve_node_file``, la misma que usa
    ``discover_dataset``. Antes había una copia acá que no contemplaba rutas
    absolutas, y por eso el catálogo marcaba capturas como "sin señal" cuando
    el disparo sí las encontraba.
    """
    for key, default in (("raw_file", "raw_f32le.bin"), ("filt_file", "filt_f32le.bin")):
        path = frd.resolve_node_file(folder, capture_dir, node, key, default)
        if path is not None and path.is_file():
            return path, path.stat().st_size // BYTES_PER_SAMPLE
    return None, 0


def _nodes_from_channels(folder: Path, capture_dir: Path, raw_root: Path) -> list[dict]:
    """Nodos deducidos por directorio, con la misma función que usa el disparo."""
    nodes = []
    for ch in frd.discover_capture_channels(folder, capture_dir):
        path = ch.signal_file(prefer_filtered=False) or ch.signal_file(prefer_filtered=True)
        if path is None or not Path(path).is_file():
            continue
        samples = Path(path).stat().st_size // BYTES_PER_SAMPLE
        fs = float(ch.fs or 0.0)
        nodes.append({
            "index": ch.node_index,
            "pcb_id": ch.pcb_id,
            "role": ch.role,
            "hw_type": "",
            "fs": fs,
            "samples": samples,
            "seconds": (samples / fs) if fs > 0 else 0.0,
            "file": str(Path(path).relative_to(raw_root)).replace("\\", "/"),
        })
    return nodes


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
                    "role": frd.node_role(node),
                    "hw_type": node.get("hw_type") or "",
                    "fs": fs,
                    "samples": samples,
                    "seconds": (samples / fs) if fs > 0 else 0.0,
                    "file": str(path.relative_to(raw_root)).replace("\\", "/"),
                })
            if not nodes:
                # La metadata no sirvió (sin `nodes`, o sin rutas resolubles).
                # `discover_dataset` en ese caso descubre los canales por
                # directorio; el catálogo tiene que hacer lo mismo o la captura
                # es un disparo que no aparece en ninguna tabla.
                nodes = _nodes_from_channels(folder, capture_dir, raw_root)
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
