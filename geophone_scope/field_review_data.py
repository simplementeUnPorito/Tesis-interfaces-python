"""Field-review helpers for hammer/geophone captures.

This module is intentionally independent from Qt so the same code can power
the interactive reviewer and non-interactive exports.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from scipy.io import savemat
from scipy.signal import butter, correlate, resample_poly, sosfiltfilt


SCHEMA = "geophone_field_review_v1"


def _discover_data_root() -> Path:
    """Resuelve el almacén de datos sin acoplar este repo al superproyecto."""
    configured = os.environ.get("TESIS_DATA_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()

    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "data"
        if (candidate / "raw").is_dir() or (candidate / "processed").is_dir():
            return candidate

    return here.parents[1] / "data"


_DATA_ROOT = _discover_data_root()
DEFAULT_RAW_ROOT = _DATA_ROOT / "raw" / "Canchita"
DEFAULT_ANNOTATIONS_NAME = "field_review_annotations.json"
DEFAULT_AVERAGE_ARRIVALS_NAME = "average_arrivals.json"
DEFAULT_FILTER_SETTINGS_NAME = "filter_settings.json"
DEFAULT_ALIGNMENT_OFFSETS_NAME = "alignment_offsets.json"
DEFAULT_ALIGNMENT_SHOT_OFFSETS_NAME = "alignment_shot_offsets.json"
DEFAULT_DISABLED_FOLDERS_NAME = "alignment_disabled_folders.json"
# Clave reservada dentro del estado compartido de carpetas desactivadas.
# A diferencia de un rechazo de Enfase (que aplica a una distancia/grupo), esta
# bandera excluye la carpeta completa del pipeline sin tocar ningún archivo raw.
GLOBAL_DISABLED_LABEL = "__all__"
DEFAULT_DISPERSION_GROUPS_NAME = "dispersion_groups.json"
DEFAULT_SESSION_NAME = "field_review_session.json"
DEFAULT_MASW_STATE_NAME = "field_review_masw_state.json"
DEFAULT_MASW_ARRAYS_NAME = "field_review_masw_state.npz"

# Todo lo que genera la app (anotaciones, sesion, estado MASW, export
# Los resultados van a data/processed y nunca se mezclan con data/raw.
_configured_processed_root = os.environ.get("TESIS_PROCESSED_ROOT")
_PROCESADOS_ROOT = (
    Path(_configured_processed_root).expanduser().resolve()
    if _configured_processed_root
    else _DATA_ROOT / "processed"
)


def _procesados_dir_for(raw_root: str | Path) -> Path:
    """Carpeta en data/processed que espeja el dataset (mismo nombre que raw_root),
    donde van las anotaciones/sesion/estado que antes se guardaban en raw_root."""
    raw_root = Path(raw_root).resolve()
    d = _PROCESADOS_ROOT / raw_root.name
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass(frozen=True)
class ChannelRef:
    role: str
    node_index: int | None
    pcb_id: str
    raw_file: Path | None
    filt_file: Path | None
    fs: float
    position_m: float | None
    invert_signal: bool = False
    label: str = ""

    def signal_file(self, prefer_filtered: bool = False) -> Path | None:
        if prefer_filtered and self.filt_file is not None:
            return self.filt_file
        if self.raw_file is not None:
            return self.raw_file
        return self.filt_file


@dataclass(frozen=True)
class FieldShot:
    shot_id: str
    folder: Path
    capture_dir: Path
    folder_name: str
    capture_name: str
    order: int
    fs: float
    distance_m: float
    hammer: ChannelRef
    geo: ChannelRef
    folder_hash: str
    duplicate_of: str | None = None


@dataclass
class PickAnnotation:
    shot_id: str
    trigger_s: float
    arrival_s: float
    distance_m: float
    accepted: bool = True
    notes: str = ""
    source: str = "auto"
    reviewed: bool = False
    # Inversion extra del geofono por muestra: el geofono funciona conectado
    # en cualquier sentido, pero conectado al reves la señal capturada queda
    # invertida (y eso destruye el promedio/waterfall), asi que segun el dia
    # pudo quedar grabada en contrafase. Se aplica DESPUES de la convencion
    # fija (geo no invertido).
    geo_flip: bool = False

    @property
    def travel_time_s(self) -> float:
        return 0.0


@dataclass
class DuplicateGroup:
    folder_hash: str
    keep_folder: str
    duplicate_folders: list[str]
    capture_count: int
    signal_file_count: int


@dataclass
class AverageArrivalAnnotation:
    label: str
    distance_m: float
    arrival_s: float = 0.0
    reviewed: bool = False
    notes: str = ""


@dataclass
class FilterSettings:
    """Filtro pasa-banda Butterworth (SOS + sosfiltfilt, fase cero) + politica
    de resampleo para combinar capturas con fs distinta antes de promediar.

    - low_hz / high_hz en 0 desactivan ese extremo (0, high -> pasa-bajos;
      low, 0 -> pasa-altos; low, high -> pasa-banda; 0, 0 -> sin filtrar).
    - target_fs en 0/None significa "automatico": cada grupo (por distancia,
      o el hammer global) se resamplea a la fs minima presente en ese grupo,
      asi nunca hace falta upsamplear (evita inventar contenido espectral).
    """

    enabled: bool = False
    low_hz: float = 0.0
    high_hz: float = 0.0
    order: int = 4
    target_fs: float = 0.0
    dc_enabled: bool = False
    line_suppress_enabled: bool = False
    line_f0_hz: float = 50.0
    line_harmonics: int = 3
    line_search_hz: float = 2.0
    notes: str = ""


@dataclass
class FieldDataset:
    raw_root: Path
    shots: list[FieldShot]
    duplicate_groups: list[DuplicateGroup]
    skipped_folders: list[str] = field(default_factory=list)

    @property
    def duplicate_folder_count(self) -> int:
        return sum(len(group.duplicate_folders) for group in self.duplicate_groups)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write_text(path: Path, text: str) -> None:
    """Escribe junto al destino y publica con un replace atómico."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(
        path.suffix + f".{os.getpid()}.{datetime.now().timestamp():.6f}.tmp"
    )
    try:
        with tmp.open("wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def discover_dataset(raw_root: str | Path, include_duplicates: bool = False) -> FieldDataset:
    raw_root = Path(raw_root).resolve()
    if not raw_root.exists():
        raise FileNotFoundError(raw_root)

    folders = sorted([p for p in raw_root.iterdir() if p.is_dir()], key=lambda p: p.name)
    signatures: dict[str, list[tuple[Path, int, int]]] = {}
    skipped: list[str] = []
    for folder in folders:
        try:
            folder_hash, signal_count, capture_count = folder_signal_signature(folder)
        except Exception:
            skipped.append(folder.name)
            continue
        signatures.setdefault(folder_hash, []).append((folder, signal_count, capture_count))

    duplicate_groups: list[DuplicateGroup] = []
    duplicate_to_keep: dict[Path, Path] = {}
    for folder_hash, entries in signatures.items():
        if len(entries) <= 1:
            continue
        keep = entries[0][0]
        duplicates = [entry[0] for entry in entries[1:]]
        duplicate_groups.append(
            DuplicateGroup(
                folder_hash=folder_hash,
                keep_folder=keep.name,
                duplicate_folders=[p.name for p in duplicates],
                capture_count=entries[0][2],
                signal_file_count=entries[0][1],
            )
        )
        for dup in duplicates:
            duplicate_to_keep[dup] = keep

    shots: list[FieldShot] = []
    for folder_hash, entries in signatures.items():
        for folder, _signal_count, _capture_count in entries:
            if folder in duplicate_to_keep and not include_duplicates:
                continue
            duplicate_of = duplicate_to_keep.get(folder)
            shots.extend(
                _discover_folder_shots(
                    folder=folder,
                    folder_hash=folder_hash,
                    duplicate_of=duplicate_of.name if duplicate_of else None,
                    raw_root=raw_root,
                )
            )

    shots.sort(key=lambda shot: (shot.folder_name, shot.order, shot.capture_name))
    shots, capture_duplicate_groups = _dedupe_shots_by_signal(shots, include_duplicates=include_duplicates)
    duplicate_groups.extend(capture_duplicate_groups)
    return FieldDataset(
        raw_root=raw_root,
        shots=shots,
        duplicate_groups=duplicate_groups,
        skipped_folders=skipped,
    )


def _shot_signal_signature(shot: FieldShot) -> str | None:
    """Firma del contenido de la captura: bytes crudos (float32) de hammer y
    geo. Dos capturas con exactamente los mismos puntos dan la misma firma
    sin importar carpeta, nombre ni metadata."""
    digest = hashlib.sha256()
    digest.update(b"field-capture-signature-v1\0")
    found = False
    for channel in (shot.hammer, shot.geo):
        path = channel.signal_file(prefer_filtered=False)
        digest.update(channel.role.lower().encode("utf-8") + b"\0")
        if path is None or not path.exists():
            continue
        digest.update(path.stat().st_size.to_bytes(8, "little", signed=False))
        digest.update(_file_sha256(path))
        found = True
    return digest.hexdigest() if found else None


def _dedupe_shots_by_signal(
    shots: list[FieldShot],
    include_duplicates: bool = False,
) -> tuple[list[FieldShot], list[DuplicateGroup]]:
    """Deduplica a nivel captura comparando el contenido de las señales
    punto a punto (hash de los bytes), no los nombres. Complementa la
    deduplicacion por carpeta: atrapa capturas identicas repartidas en
    carpetas distintas que no son copias completas una de la otra."""
    seen: dict[str, FieldShot] = {}
    unique: list[FieldShot] = []
    dup_map: dict[str, list[FieldShot]] = {}
    for shot in shots:
        signature = _shot_signal_signature(shot)
        if signature is None:
            unique.append(shot)
            continue
        keeper = seen.get(signature)
        if keeper is None:
            seen[signature] = shot
            unique.append(shot)
            continue
        dup_map.setdefault(signature, []).append(shot)
        if include_duplicates:
            unique.append(replace(shot, duplicate_of=f"{keeper.folder_name}/{keeper.capture_name}"))
    groups = [
        DuplicateGroup(
            folder_hash=signature,
            keep_folder=f"{seen[signature].folder_name}/{seen[signature].capture_name}",
            duplicate_folders=[f"{d.folder_name}/{d.capture_name}" for d in dups],
            capture_count=1,
            signal_file_count=2,
        )
        for signature, dups in dup_map.items()
    ]
    return unique, groups


def folder_signal_signature(folder: Path) -> tuple[str, int, int]:
    """Hash only signal points, not folder names or labels."""
    digest = hashlib.sha256()
    signal_file_count = 0
    capture_dirs = _capture_dirs(folder)
    digest.update(b"field-folder-signature-v1\0")
    for capture_dir in capture_dirs:
        channels = _discover_channels(folder, capture_dir)
        digest.update(b"capture\0")
        for channel in sorted(channels, key=_channel_sort_key):
            role = channel.role.lower().encode("utf-8")
            pcb_id = channel.pcb_id.lower().encode("utf-8")
            digest.update(role + b"\0" + pcb_id + b"\0")
            path = channel.signal_file(prefer_filtered=False)
            if path is None or not path.exists():
                continue
            file_hash = _file_sha256(path)
            digest.update(b"raw-signal\0")
            digest.update(path.stat().st_size.to_bytes(8, "little", signed=False))
            digest.update(file_hash)
            signal_file_count += 1
    return digest.hexdigest(), signal_file_count, len(capture_dirs)


def load_signal(channel: ChannelRef, prefer_filtered: bool = False, apply_invert: bool = True) -> np.ndarray:
    path = channel.signal_file(prefer_filtered=prefer_filtered)
    if path is None:
        return np.array([], dtype=np.float32)
    data = np.fromfile(path, dtype="<f4").astype(np.float32, copy=False)
    if apply_invert and channel.invert_signal:
        data = -data
    return data


def peak_to_peak(samples: np.ndarray) -> float:
    """max-min de una senal, ignorando NaN. 0.0 para vacio o todo-NaN.

    Usado para ordenar capturas por "que tan facil es de ver el golpe",
    tanto en Capturas (orden de la tabla) como en Enfase (orden de
    navegacion senal-por-senal)."""
    if samples.size == 0:
        return 0.0
    finite = samples[np.isfinite(samples)]
    if finite.size == 0:
        return 0.0
    return float(np.max(finite) - np.min(finite))


def auto_pick_shot(
    shot: FieldShot,
    prefer_filtered: bool = False,
    search_window_s: tuple[float, float] | None = None,
) -> PickAnnotation:
    fs = shot.fs or shot.hammer.fs or shot.geo.fs
    hammer = load_signal(shot.hammer, prefer_filtered=prefer_filtered, apply_invert=True)
    trigger_idx = detect_hammer_trigger(hammer, fs, search_window_s=search_window_s)
    trigger_s = float(trigger_idx / fs) if fs > 0 else 0.0
    return PickAnnotation(
        shot_id=shot.shot_id,
        trigger_s=trigger_s,
        arrival_s=trigger_s,
        distance_m=float(shot.distance_m),
        accepted=True,
        source="auto",
    )


def detect_hammer_trigger(
    signal: np.ndarray,
    fs: float,
    search_window_s: tuple[float, float] | None = None,
) -> int:
    if signal.size == 0 or fs <= 0:
        return 0
    x = _finite_signal(signal)
    dx = np.diff(x, prepend=x[0])
    score = _moving_mean(np.abs(dx), max(1, int(round(fs * 0.003))))
    base_end = max(8, min(int(round(fs * 0.25)), score.size // 4))
    center, scale = _robust_center_scale(score[:base_end])
    threshold = center + 7.0 * scale
    search_start = 0
    search_end = score.size
    if search_window_s is not None:
        a, b = sorted((float(search_window_s[0]), float(search_window_s[1])))
        search_start = int(np.clip(math.floor(a * fs), 0, max(0, score.size - 1)))
        search_end = int(np.clip(math.ceil(b * fs) + 1, search_start + 1, score.size))
    search_score = score[search_start:search_end]
    idx = _first_sustained(search_score > threshold, max(1, int(round(fs * 0.0015))))
    if idx is None:
        idx = int(np.argmax(search_score)) if search_score.size else 0
    idx += search_start
    return int(np.clip(idx, 0, signal.size - 1))


def load_annotations(path: str | Path) -> dict[str, PickAnnotation]:
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    annotations: dict[str, PickAnnotation] = {}
    for item in data.get("annotations", []):
        try:
            ann = PickAnnotation(
                shot_id=str(item["shot_id"]),
                trigger_s=float(item.get("trigger_s", 0.0) or 0.0),
                arrival_s=float(item.get("trigger_s", 0.0) or 0.0),
                distance_m=float(item.get("distance_m", 0.0) or 0.0),
                accepted=bool(item.get("accepted", True)),
                notes=str(item.get("notes", "") or ""),
                source=str(item.get("source", "manual") or "manual"),
                reviewed=bool(item.get("reviewed", str(item.get("source", "")).startswith("manual"))),
                geo_flip=bool(item.get("geo_flip", False)),
            )
        except (KeyError, TypeError, ValueError):
            continue
        annotations[ann.shot_id] = ann
    return annotations


def save_annotations(
    path: str | Path,
    dataset: FieldDataset,
    annotations: dict[str, PickAnnotation],
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": SCHEMA,
        "updated_at": utc_now_iso(),
        "raw_root": str(dataset.raw_root),
        "shot_count": len(dataset.shots),
        "duplicate_folder_count": dataset.duplicate_folder_count,
        "duplicates": [asdict(group) for group in dataset.duplicate_groups],
        "annotations": [
            _annotation_to_dict(annotations[shot.shot_id])
            for shot in dataset.shots
            if shot.shot_id in annotations
        ],
    }
    _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
    return path


def load_average_arrivals(path: str | Path) -> dict[str, AverageArrivalAnnotation]:
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    arrivals: dict[str, AverageArrivalAnnotation] = {}
    for item in data.get("arrivals", []):
        try:
            ann = AverageArrivalAnnotation(
                label=str(item["label"]),
                distance_m=float(item.get("distance_m", 0.0) or 0.0),
                arrival_s=float(item.get("arrival_s", 0.0) or 0.0),
                reviewed=bool(item.get("reviewed", False)),
                notes=str(item.get("notes", "") or ""),
            )
        except (KeyError, TypeError, ValueError):
            continue
        arrivals[ann.label] = ann
    return arrivals


def save_average_arrivals(
    path: str | Path,
    arrivals: dict[str, AverageArrivalAnnotation],
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": SCHEMA,
        "updated_at": utc_now_iso(),
        "arrivals": [
            asdict(arrivals[label])
            for label in sorted(arrivals, key=lambda value: arrivals[value].distance_m)
        ],
    }
    _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
    return path


def default_filter_settings_path(raw_root: str | Path) -> Path:
    return _procesados_dir_for(raw_root) / DEFAULT_FILTER_SETTINGS_NAME


def load_filter_settings(path: str | Path) -> FilterSettings:
    path = Path(path)
    if not path.exists():
        return FilterSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        item = data.get("filter", data)
        return FilterSettings(
            enabled=bool(item.get("enabled", False)),
            low_hz=float(item.get("low_hz", 0.0) or 0.0),
            high_hz=float(item.get("high_hz", 0.0) or 0.0),
            order=int(item.get("order", 4) or 4),
            target_fs=float(item.get("target_fs", 0.0) or 0.0),
            dc_enabled=bool(item.get("dc_enabled", False)),
            line_suppress_enabled=bool(item.get("line_suppress_enabled", False)),
            line_f0_hz=float(item.get("line_f0_hz", 50.0) or 50.0),
            line_harmonics=max(
                1, min(12, int(item.get("line_harmonics", 3) or 3))
            ),
            line_search_hz=max(
                0.0, float(item.get("line_search_hz", 2.0) or 0.0)
            ),
            notes=str(item.get("notes", "") or ""),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return FilterSettings()


def save_filter_settings(path: str | Path, settings: FilterSettings) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": SCHEMA,
        "updated_at": utc_now_iso(),
        "filter": asdict(settings),
    }
    _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
    return path


def default_alignment_offsets_path(raw_root: str | Path) -> Path:
    return _procesados_dir_for(raw_root) / DEFAULT_ALIGNMENT_OFFSETS_NAME


def load_alignment_offsets(path: str | Path) -> dict[str, dict[str, float]]:
    """Offsets de enfase por (label de distancia, carpeta), en segundos.
    Corrigen el error chico de posicionamiento entre tandas medidas en dias
    o carpetas distintas con el mismo label: offset positivo corre esa tanda
    hacia la izquierda (como si llegara antes) antes de promediar."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    offsets: dict[str, dict[str, float]] = {}
    for item in data.get("offsets", []):
        try:
            label = str(item["label"])
            folder = str(item["folder"])
            offset_s = float(item.get("offset_s", 0.0) or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        offsets.setdefault(label, {})[folder] = offset_s
    return offsets


def save_alignment_offsets(path: str | Path, offsets: dict[str, dict[str, float]]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    items = [
        {"label": label, "folder": folder, "offset_s": float(offset_s)}
        for label in sorted(offsets)
        for folder, offset_s in sorted(offsets[label].items())
        if abs(float(offset_s)) > 1e-12
    ]
    data = {"schema": SCHEMA, "updated_at": utc_now_iso(), "offsets": items}
    _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
    return path


def alignment_offsets_signature(offsets: dict[str, dict[str, float]] | None) -> tuple:
    if not offsets:
        return ()
    return tuple(
        sorted(
            (label, folder, round(float(offset_s), 9))
            for label, folders in offsets.items()
            for folder, offset_s in folders.items()
            if abs(float(offset_s)) > 1e-12
        )
    )


def default_alignment_shot_offsets_path(raw_root: str | Path) -> Path:
    return _procesados_dir_for(raw_root) / DEFAULT_ALIGNMENT_SHOT_OFFSETS_NAME


def load_alignment_shot_offsets(path: str | Path) -> dict[str, float]:
    """Offsets de enfase por shot_id individual, en segundos. Tiene
    prioridad sobre el offset de carpeta (`load_alignment_offsets`) cuando
    el usuario ajusto esa captura puntual a mano en Enfase; las capturas
    nunca tocadas siguen usando el offset de su carpeta como default."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    shot_offsets: dict[str, float] = {}
    for item in data.get("shot_offsets", []):
        try:
            shot_id = str(item["shot_id"])
            offset_s = float(item.get("offset_s", 0.0) or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        shot_offsets[shot_id] = offset_s
    return shot_offsets


def save_alignment_shot_offsets(path: str | Path, shot_offsets: dict[str, float]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    items = [
        {"shot_id": shot_id, "offset_s": float(offset_s)}
        for shot_id, offset_s in sorted(shot_offsets.items())
        if abs(float(offset_s)) > 1e-12
    ]
    data = {"schema": SCHEMA, "updated_at": utc_now_iso(), "shot_offsets": items}
    _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
    return path


def alignment_shot_offsets_signature(shot_offsets: dict[str, float] | None) -> tuple:
    if not shot_offsets:
        return ()
    return tuple(
        sorted(
            (shot_id, round(float(offset_s), 9))
            for shot_id, offset_s in shot_offsets.items()
            if abs(float(offset_s)) > 1e-12
        )
    )


def default_disabled_folders_path(raw_root: str | Path) -> Path:
    return _procesados_dir_for(raw_root) / DEFAULT_DISABLED_FOLDERS_NAME


_disabled_lock_guard = threading.Lock()
_disabled_thread_locks: dict[str, threading.RLock] = {}
_disabled_lock_local = threading.local()


@contextmanager
def disabled_folders_lock(path: str | Path, timeout_s: float = 30.0):
    """Lock reentrante y entre procesos para el estado de carpetas.

    Web y PyQt pueden estar abiertos sobre la misma campaña. El lock de
    ``server.state`` sólo coordina hilos del servidor; este lockfile evita que
    una escritura ciega de PyQt pise la bandera global aplicada desde la web.
    """
    path = Path(path)
    key = str(path.resolve()).casefold()
    with _disabled_lock_guard:
        thread_lock = _disabled_thread_locks.setdefault(key, threading.RLock())
    with thread_lock:
        depths = getattr(_disabled_lock_local, "depths", None)
        if depths is None:
            depths = _disabled_lock_local.depths = {}
        if depths.get(key, 0):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(path.suffix + ".lock")
        deadline = time.monotonic() + max(0.1, float(timeout_s))
        acquired = False
        owner = (
            f"pid={os.getpid()} token={os.urandom(16).hex()} "
            f"at={utc_now_iso()}\n"
        ).encode("utf-8")
        while not acquired:
            try:
                fd = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY
                    | getattr(os, "O_BINARY", 0),
                )
                try:
                    os.write(fd, owner)
                finally:
                    os.close(fd)
                acquired = True
            except FileExistsError:
                try:
                    stale_stat = lock_path.stat()
                    stale_owner = lock_path.read_bytes()
                    stale = time.time() - stale_stat.st_mtime > 120.0
                except OSError:
                    stale = False
                if stale:
                    try:
                        current_stat = lock_path.stat()
                        current_owner = lock_path.read_bytes()
                        unchanged = (
                            current_stat.st_mtime_ns == stale_stat.st_mtime_ns
                            and current_stat.st_size == stale_stat.st_size
                            and current_owner == stale_owner
                        )
                        if unchanged:
                            lock_path.unlink()
                    except OSError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"no se pudo bloquear {path.name}; otra app lo está escribiendo"
                    )
                time.sleep(0.05)

        depths[key] = 1
        try:
            yield
        finally:
            depths.pop(key, None)
            try:
                # Un proceso que rompió un lock realmente obsoleto puede haber
                # creado otro entre tanto. Nunca se borra un lock cuyo token no
                # sea exactamente el de esta adquisición.
                if lock_path.read_bytes() == owner:
                    lock_path.unlink()
            except OSError:
                pass


def load_disabled_folders(path: str | Path) -> dict[str, list[str]]:
    """Carpetas desactivadas por (label de distancia, carpeta): sus señales
    pueden ser validas (trigger y forma coherentes, marcadas OK en Capturas)
    pero el usuario decidio en Enfase que esa tanda NO entre a promedios,
    waterfall, MASW ni al promedio del export (las muestras individuales se
    exportan igual).

    La clave reservada ``GLOBAL_DISABLED_LABEL`` representa una desactivación
    global y reversible de la carpeta. La usa el tab Borrado web como
    cuarentena no destructiva y también la entiende PyQt en los cálculos
    compartidos.
    """
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    disabled: dict[str, list[str]] = {}
    for item in data.get("disabled", []):
        try:
            label = str(item["label"])
            folder = str(item["folder"])
        except (KeyError, TypeError):
            continue
        if folder not in disabled.setdefault(label, []):
            disabled[label].append(folder)
    return disabled


def save_disabled_folders(
    path: str | Path,
    disabled: dict[str, list[str]],
    *,
    replace_global: bool = False,
) -> Path:
    path = Path(path)
    with disabled_folders_lock(path):
        merged = {
            str(label): list(folders)
            for label, folders in (disabled or {}).items()
        }
        if not replace_global:
            # PyQt conserva un snapshot en memoria. Releer esta única clave
            # justo bajo el lock evita que un guardado de Enfase borre una
            # cuarentena que la web aplicó mientras la ventana estaba abierta.
            disk_global = load_disabled_folders(path).get(
                GLOBAL_DISABLED_LABEL, []
            )
            if disk_global:
                merged[GLOBAL_DISABLED_LABEL] = list(disk_global)
            else:
                merged.pop(GLOBAL_DISABLED_LABEL, None)
        items = [
            {"label": label, "folder": folder}
            for label in sorted(merged)
            for folder in sorted(set(merged[label]))
        ]
        data = {"schema": SCHEMA, "updated_at": utc_now_iso(), "disabled": items}
        _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
    return path


def disabled_folders_signature(disabled: dict[str, list[str]] | None) -> tuple:
    if not disabled:
        return ()
    return tuple(
        sorted((label, folder) for label, folders in disabled.items() for folder in set(folders))
    )


def default_dispersion_groups_path(raw_root: str | Path) -> Path:
    return _procesados_dir_for(raw_root) / DEFAULT_DISPERSION_GROUPS_NAME


def load_dispersion_groups(path: str | Path) -> tuple[int, dict[str, int]]:
    """Carga los grupos usados para calcular dispersion por separado.

    La asignacion es por carpeta/tanda completa: folder_name -> grupo (1..N).
    Carpetas ausentes se interpretan como grupo 1 para mantener compatibilidad
    con sesiones previas.
    """
    path = Path(path)
    if not path.exists():
        return 1, {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 1, {}
    try:
        group_count = max(1, int(data.get("group_count", 1) or 1))
    except (TypeError, ValueError):
        group_count = 1
    assignments: dict[str, int] = {}
    raw = data.get("assignments", {})
    if isinstance(raw, dict):
        items = raw.items()
    else:
        items = ((item.get("folder"), item.get("group")) for item in raw if isinstance(item, dict))
    for folder, group in items:
        try:
            folder_name = str(folder)
            group_id = int(group)
        except (TypeError, ValueError):
            continue
        if not folder_name:
            continue
        assignments[folder_name] = int(np.clip(group_id, 1, group_count))
    return group_count, assignments


def save_dispersion_groups(
    path: str | Path,
    group_count: int,
    assignments: dict[str, int],
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    group_count = max(1, int(group_count or 1))
    clean = {
        str(folder): int(np.clip(int(group), 1, group_count))
        for folder, group in sorted(assignments.items())
        if str(folder)
    }
    data = {
        "schema": SCHEMA,
        "updated_at": utc_now_iso(),
        "group_count": group_count,
        "assignments": clean,
    }
    _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
    return path


def dispersion_groups_signature(group_count: int, assignments: dict[str, int] | None) -> tuple:
    group_count = max(1, int(group_count or 1))
    if not assignments:
        return (group_count,)
    return (
        group_count,
        tuple(sorted((str(folder), int(np.clip(int(group), 1, group_count))) for folder, group in assignments.items())),
    )


def is_folder_disabled(
    disabled: dict[str, list[str]] | None, distance_m: float, folder_name: str
) -> bool:
    if not disabled:
        return False
    return (
        folder_name in disabled.get(GLOBAL_DISABLED_LABEL, ())
        or folder_name in disabled.get(format_distance_label(distance_m), ())
    )


def default_session_path(raw_root: str | Path) -> Path:
    return _procesados_dir_for(raw_root) / DEFAULT_SESSION_NAME


def load_session(path: str | Path) -> dict[str, Any]:
    """Estado de UI de la ultima sesion (que muestra estaba seleccionada,
    orden, filtro, modo oscuro) para poder continuar donde se dejo al reabrir
    la app. Devuelve {} si no existe o esta corrupto."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_session(path: str | Path, session: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"schema": SCHEMA, "updated_at": utc_now_iso(), **session}
    _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
    return path


def default_masw_state_path(raw_root: str | Path) -> Path:
    return _procesados_dir_for(raw_root) / DEFAULT_MASW_STATE_NAME


def default_masw_arrays_path(raw_root: str | Path) -> Path:
    return _procesados_dir_for(raw_root) / DEFAULT_MASW_ARRAYS_NAME


def load_masw_state(path: str | Path) -> dict[str, Any]:
    """Estado guardado de las pestañas Waterfall y MASW (picks de la curva de
    dispersion, poligono M0, parametros e ultimo resultado de inversion, y
    ajustes de vista del waterfall) para restaurar el ultimo analisis al
    reabrir la app sobre el mismo dataset. Devuelve {} si no existe o esta
    corrupto; los arrays numericos pesados van aparte en el .npz (ver
    `load_masw_arrays`)."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_masw_state(path: str | Path, state: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"schema": SCHEMA, "updated_at": utc_now_iso(), **state}
    _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
    return path


def load_masw_arrays(path: str | Path) -> dict[str, np.ndarray]:
    """Carga el .npz con los arrays pesados del ultimo analisis MASW/waterfall
    (matriz del waterfall, datos crudos que se mandaron a MASW, arrays del
    resultado de inversion). Devuelve {} si no existe o esta corrupto."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        with np.load(path, allow_pickle=False) as data:
            return {key: data[key].copy() for key in data.files}
    except (OSError, ValueError, EOFError):
        return {}


def save_masw_arrays(path: str | Path, arrays: dict[str, np.ndarray] | None) -> Path | None:
    """Guarda los arrays pesados del analisis MASW/waterfall en un .npz
    comprimido. Si no hay nada que guardar, borra el archivo viejo para no
    dejar estado incoherente y devuelve None."""
    path = Path(path)
    if not arrays:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(
        path.suffix + f".{os.getpid()}.{datetime.now().timestamp():.6f}.tmp.npz"
    )
    try:
        with tmp.open("wb") as handle:
            np.savez_compressed(handle, **{k: np.asarray(v) for k, v in arrays.items()})
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def get_alignment_offset(
    offsets: dict[str, dict[str, float]] | None,
    distance_m: float,
    folder_name: str,
    shot_id: str | None = None,
    shot_offsets: dict[str, float] | None = None,
) -> float:
    """Offset de enfase en segundos para una captura: el offset por-shot
    (si existe) gana sobre el offset por-carpeta (default cuando esa
    captura puntual todavia no fue alineada a mano)."""
    if shot_id is not None and shot_offsets and shot_id in shot_offsets:
        return float(shot_offsets[shot_id])
    if not offsets:
        return 0.0
    return float(offsets.get(format_distance_label(distance_m), {}).get(folder_name, 0.0))


def filter_settings_signature(settings: FilterSettings | None) -> tuple:
    if settings is None:
        return (False, 0.0, 0.0, 0, 0.0, False, False, 50.0, 3, 2.0)
    return (
        bool(settings.enabled),
        round(float(settings.low_hz), 6),
        round(float(settings.high_hz), 6),
        int(settings.order),
        round(float(settings.target_fs), 6),
        bool(settings.dc_enabled),
        bool(settings.line_suppress_enabled),
        round(float(settings.line_f0_hz), 6),
        int(settings.line_harmonics),
        round(float(settings.line_search_hz), 6),
    )


def design_bandpass_filter(
    fs: float, low_hz: float, high_hz: float, order: int
) -> np.ndarray | None:
    """Devuelve las second-order-sections (SOS) de un Butterworth
    pasa-banda/pasa-altos/pasa-bajos segun que cortes esten activos, o None
    si no hay nada que filtrar.

    Se usa la forma SOS (no (b, a)) a proposito: con un corte bajo cerca de
    1 Hz y fs de varios cientos/miles de Hz, la banda relativa es muy
    angosta y la forma (b, a) de Butterworth se vuelve numericamente
    inestable a partir de orden ~6 (coeficientes que crecen ordenes de
    magnitud, filtfilt devolviendo inf/NaN en silencio, sin excepcion ni
    aviso). SOS es la forma que scipy recomienda para evitar justo este
    problema y se mantiene estable en los ordenes que soporta la UI (1-10)."""
    if fs <= 0:
        return None
    nyq = fs / 2.0
    low_hz = max(0.0, float(low_hz or 0.0))
    high_hz = max(0.0, float(high_hz or 0.0))
    if high_hz > 0 and high_hz >= nyq:
        high_hz = 0.0
    if low_hz > 0 and low_hz >= nyq:
        low_hz = 0.0
    order = max(1, int(order or 1))
    if low_hz > 0 and high_hz > 0 and low_hz < high_hz:
        return butter(order, [low_hz / nyq, high_hz / nyq], btype="bandpass", output="sos")
    if low_hz > 0:
        return butter(order, low_hz / nyq, btype="highpass", output="sos")
    if high_hz > 0:
        return butter(order, high_hz / nyq, btype="lowpass", output="sos")
    return None


def apply_bandpass_filter(
    x: np.ndarray, fs: float, low_hz: float, high_hz: float, order: int
) -> np.ndarray:
    """Butterworth (SOS) + sosfiltfilt (fase cero). Si la señal es demasiado
    corta para el padding que exige sosfiltfilt, la devuelve sin tocar en
    vez de tirar una excepcion."""
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return x.astype(np.float32, copy=False)
    sos = design_bandpass_filter(fs, low_hz, high_hz, order)
    if sos is None:
        return x.astype(np.float32, copy=False)
    padlen = 3 * (2 * sos.shape[0] + 1)
    if x.size <= padlen:
        return x.astype(np.float32, copy=False)
    y = sosfiltfilt(sos, x)
    return y.astype(np.float32, copy=False)


def apply_filter_chain(
    x: np.ndarray, fs: float, settings: FilterSettings | None
) -> np.ndarray:
    """Cadena científica común a PyQt, web, promedios, Waterfall y MASW.

    El supresor armónico no es un notch IIR. Estima la frecuencia de línea
    alrededor del valor nominal y resta, sobre la captura completa, el modelo
    de senos/cosenos ajustado por mínimos cuadrados que usa el master ESP.
    """
    y = np.asarray(x, dtype=np.float64)
    if y.size == 0 or settings is None or not settings.enabled:
        return y.astype(np.float32, copy=False)
    finite = np.isfinite(y)
    if not np.any(finite):
        return y.astype(np.float32, copy=False)
    work = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)
    if settings.dc_enabled:
        work = work - float(np.mean(work[finite]))
    work = np.asarray(
        apply_bandpass_filter(
            work, fs, settings.low_hz, settings.high_hz, settings.order
        ),
        dtype=np.float64,
    )
    if settings.line_suppress_enabled:
        try:
            from .signal_proc import harmonic_notch
        except ImportError:  # pragma: no cover - ejecución como script
            from signal_proc import harmonic_notch
        work = harmonic_notch(
            work,
            float(fs),
            float(settings.line_f0_hz),
            max(1, min(12, int(settings.line_harmonics))),
            max(0.0, float(settings.line_search_hz)),
        )
    work[~finite] = np.nan
    return work.astype(np.float32, copy=False)


def resample_signal(x: np.ndarray, fs: float, target_fs: float) -> np.ndarray:
    """Resamplea x de fs a target_fs con resample_poly (factor racional
    exacto via Fraction), para poder combinar capturas con distinta fs en un
    mismo promedio. Si fs y target_fs coinciden no hace nada."""
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0 or fs <= 0 or target_fs <= 0 or abs(fs - target_fs) < 1e-9:
        return x.astype(np.float32, copy=False)
    frac = Fraction(target_fs).limit_denominator(1000) / Fraction(fs).limit_denominator(1000)
    up, down = frac.numerator, frac.denominator
    if up <= 0 or down <= 0:
        return x.astype(np.float32, copy=False)
    y = resample_poly(x, up, down)
    return y.astype(np.float32, copy=False)


def fk_directional_filter(
    matrix: np.ndarray,
    distances: Iterable[float],
    common_time: np.ndarray,
    keep_forward: bool = True,
) -> np.ndarray:
    """Filtro direccional en el dominio frecuencia-numero de onda (f-k).

    `matrix` es el gather x-t del waterfall: una fila por distancia (posicion
    de geofono), una columna por tiempo. Las ondas que viajan de la fuente
    hacia los geofonos (moveout positivo: llegan mas tarde a mayor distancia)
    y las que rebotan y vuelven (moveout negativo) caen en mitades opuestas
    del plano f-k. Poniendo a cero una mitad se separan por direccion y se
    eliminan los rebotes/reflexiones.

    Convencion (verificada con onda sintetica ida+vuelta): con np.fft.fft2 una
    onda forward s(t - x/c), c>0, concentra energia en k = -f/c, o sea k y f
    de signo OPUESTO (k*f < 0). `keep_forward=True` conserva esa mitad
    (k*f <= 0, incluyendo los ejes k=0/f=0 que son no-direccionales) y anula
    la de los rebotes.

    Maneja distancias no uniformes remuestreando a una grilla espacial
    uniforme (dx = mediana de las separaciones), filtrando, y volviendo a las
    distancias originales. Los NaN (colas de trazas mas cortas) se rellenan
    con 0 para la FFT y se restauran en la salida. Devuelve la matriz sin
    tocar si es demasiado chica para filtrar."""
    m = np.asarray(matrix, dtype=np.float64)
    dist = np.asarray(list(distances), dtype=np.float64)
    t = np.asarray(common_time, dtype=np.float64)
    if m.ndim != 2 or m.shape[0] < 3 or m.shape[1] < 4 or dist.size != m.shape[0] or t.size != m.shape[1]:
        return matrix
    dt = float(np.median(np.diff(t))) if t.size > 1 else 0.0
    if dt <= 0:
        return matrix
    order = np.argsort(dist)
    inv_order = np.argsort(order)
    dist_sorted = dist[order]
    m_sorted = m[order]
    nan_mask = ~np.isfinite(m_sorted)
    diffs = np.diff(dist_sorted)
    diffs = diffs[diffs > 1e-9]
    if diffs.size == 0:
        return matrix
    dx = float(np.median(diffs))
    if dx <= 0:
        return matrix
    x0, x1 = float(dist_sorted[0]), float(dist_sorted[-1])
    nxu = max(int(round((x1 - x0) / dx)) + 1, m_sorted.shape[0])
    grid = np.linspace(x0, x1, nxu)
    filled = np.where(nan_mask, 0.0, m_sorted)
    # Remuestreo a grilla espacial uniforme (interp por columna de tiempo).
    up = np.empty((nxu, m_sorted.shape[1]), dtype=np.float64)
    for j in range(m_sorted.shape[1]):
        up[:, j] = np.interp(grid, dist_sorted, filled[:, j])
    spectrum = np.fft.fft2(up)
    kk = np.fft.fftfreq(nxu, d=dx)[:, None]
    ff = np.fft.fftfreq(up.shape[1], d=dt)[None, :]
    product = kk * ff
    keep = product <= 0.0 if keep_forward else product >= 0.0
    filtered_uniform = np.real(np.fft.ifft2(spectrum * keep))
    # Vuelta a las distancias originales.
    out_sorted = np.empty_like(m_sorted)
    for j in range(m_sorted.shape[1]):
        out_sorted[:, j] = np.interp(dist_sorted, grid, filtered_uniform[:, j])
    out_sorted[nan_mask] = np.nan
    return out_sorted[inv_order].astype(m.dtype, copy=False)


def _prepare_shot_for_grouping(
    shot: FieldShot,
    ann: PickAnnotation,
    prefer_filtered: bool,
    filter_settings: FilterSettings | None,
    offset_s: float = 0.0,
) -> dict[str, Any] | None:
    """Carga hammer/geo de una muestra, la centra en el trigger (mas el
    offset de enfase de su tanda, si tiene) y (si esta activo) le aplica el
    filtro pasa-banda. Comun a compute_average_groups y export_processed para
    que promedios en pantalla y promedios exportados usen exactamente la
    misma señal."""
    fs = float(shot.fs or shot.geo.fs or shot.hammer.fs)
    if fs <= 0:
        return None
    hammer = load_signal(shot.hammer, prefer_filtered=prefer_filtered, apply_invert=True)
    geo = load_signal(shot.geo, prefer_filtered=prefer_filtered, apply_invert=True)
    if ann.geo_flip:
        geo = -geo
    n = int(min(hammer.size, geo.size))
    if n <= 1:
        return None
    hammer = hammer[:n]
    geo = geo[:n]
    trigger_eff_s = float(ann.trigger_s) + float(offset_s or 0.0)
    trigger_idx = int(np.clip(round(trigger_eff_s * fs), 0, n - 1))
    hammer_zero = _zero_by_pretrigger(hammer, trigger_idx, fs)
    geo_zero = _zero_by_pretrigger(geo, trigger_idx, fs)
    if filter_settings is not None and filter_settings.enabled:
        hammer_zero = apply_filter_chain(hammer_zero, fs, filter_settings)
        geo_zero = apply_filter_chain(geo_zero, fs, filter_settings)
    return {
        "shot": shot,
        "annotation": ann,
        "fs": fs,
        "trigger_s": trigger_eff_s,
        "trigger_idx": trigger_idx,
        "hammer": hammer,
        "geo": geo,
        "hammer_zero": hammer_zero,
        "geo_zero": geo_zero,
    }


def _resample_items_to_common_fs(
    items: list[dict[str, Any]],
    filter_settings: FilterSettings | None,
    signal_keys: tuple[str, ...],
) -> float:
    """Resamplea in-place cada item a una fs comun (target_fs del filtro, o
    la fs minima del grupo si es automatico/0) para poder promediar
    capturas con distinta fs original. Devuelve la fs usada."""
    if not items:
        return 0.0
    fs_values = [float(item["fs"]) for item in items]
    target_fs = float(filter_settings.target_fs) if (filter_settings and filter_settings.target_fs) else 0.0
    if target_fs <= 0:
        target_fs = min(fs_values)
    for item in items:
        fs = float(item["fs"])
        if abs(fs - target_fs) > 1e-6:
            for key in signal_keys:
                item[key] = resample_signal(item[key], fs, target_fs)
            trigger_s = float(item["trigger_s"])
            new_n = int(item[signal_keys[0]].size)
            item["trigger_idx"] = int(np.clip(round(trigger_s * target_fs), 0, max(0, new_n - 1)))
            item["fs"] = target_fs
    return target_fs


def annotations_signature(
    dataset: FieldDataset,
    annotations: dict[str, PickAnnotation],
) -> tuple:
    """Huella liviana (sin leer archivos) del estado que afecta a los
    promedios: aceptacion, revision, distancia y trigger de cada muestra.
    Sirve para saltear un recalculo si nada relevante cambio desde la
    ultima vez. `reviewed` esta incluido porque compute_average_groups solo
    promedia muestras revisadas (marcadas OK): sin esto, marcar "Guardar y
    siguiente" no dispararia un recalculo del promedio."""
    return tuple(
        (
            shot.shot_id,
            bool(ann.accepted),
            bool(ann.reviewed),
            round(float(ann.distance_m), 6),
            round(float(ann.trigger_s), 6),
            bool(ann.geo_flip),
        )
        for shot in dataset.shots
        for ann in (annotations.get(shot.shot_id),)
        if ann is not None
    )


def compute_average_groups(
    dataset: FieldDataset,
    annotations: dict[str, PickAnnotation],
    prefer_filtered: bool = False,
    filter_settings: FilterSettings | None = None,
    alignment_offsets: dict[str, dict[str, float]] | None = None,
    alignment_shot_offsets: dict[str, float] | None = None,
    disabled_folders: dict[str, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Version liviana de los promedios, solo en memoria (no escribe nada a
    disco). Pensada para refrescar la pestaña de revision de promedios sin
    pagar el costo de reexportar las muestras individuales cada vez que se
    cambia de pestaña; el export completo a disco sigue siendo
    `export_processed`.

    Solo entran al promedio las capturas marcadas `accepted` Y `reviewed`
    (validadas a mano, ej. via "Guardar y siguiente"): el default de una
    marca nueva es "sin validar" (reviewed=False), asi que nada promedia
    hasta que alguien la revise explicitamente.

    Capturas con fs distinta dentro de un mismo grupo se resamplean a una fs
    comun (la minima del grupo, o filter_settings.target_fs). Las mas cortas
    quedan en NaN mas alla de su final: el promedio usa todas las señales en
    la ventana comun y solo las largas en la cola (asi las capturas viejas de
    3 s siguen mejorando el tramo inicial de los promedios de 10.59 s)."""
    grouped: dict[float, list[dict[str, Any]]] = {}
    all_hammer_items: list[dict[str, Any]] = []
    for shot in dataset.shots:
        ann = annotations.get(shot.shot_id)
        if ann is None or not ann.accepted or not ann.reviewed:
            continue
        if is_folder_disabled(disabled_folders, ann.distance_m, shot.folder_name):
            # Carpeta desactivada en Enfase: señales validas pero la tanda
            # entera no entra al promedio/waterfall por decision del usuario.
            continue
        offset_s = get_alignment_offset(
            alignment_offsets, ann.distance_m, shot.folder_name,
            shot_id=shot.shot_id, shot_offsets=alignment_shot_offsets,
        )
        item = _prepare_shot_for_grouping(shot, ann, prefer_filtered, filter_settings, offset_s=offset_s)
        if item is None:
            continue
        distance = float(ann.distance_m)
        grouped.setdefault(distance, []).append(item)
        # El hammer ya se carga siempre en polaridad invertida (convencion
        # fija), asi que va directo al promedio global.
        all_hammer_items.append(
            {
                "fs": item["fs"],
                "trigger_s": item["trigger_s"],
                "trigger_idx": item["trigger_idx"],
                "hammer_zero": item["hammer_zero"],
            }
        )

    groups: list[dict[str, Any]] = []
    for distance in sorted(grouped):
        items = grouped[distance]
        fs = _resample_items_to_common_fs(items, filter_settings, ("hammer_zero", "geo_zero"))
        if fs <= 0:
            continue
        rel_start = max(-int(item["trigger_idx"]) for item in items)
        rel_end = max(int(item["geo_zero"].size) - int(item["trigger_idx"]) for item in items)
        if rel_end <= rel_start + 1:
            continue
        geo_stack, hammer_stack, trigger_s = [], [], []
        for item in items:
            start = int(item["trigger_idx"]) + rel_start
            end = int(item["trigger_idx"]) + rel_end
            geo_stack.append(segment_nan_padded(item["geo_zero"], start, end))
            hammer_stack.append(segment_nan_padded(item["hammer_zero"], start, end))
            trigger_s.append(float(item["trigger_s"]))
        geo_arr = np.vstack(geo_stack)
        hammer_arr = np.vstack(hammer_stack)
        time_s = np.arange(rel_start, rel_end, dtype=np.float64) / fs
        with np.errstate(invalid="ignore"):
            geo_mean = np.nanmean(geo_arr, axis=0)
            geo_std = np.nanstd(geo_arr, axis=0)
            geo_count = np.sum(np.isfinite(geo_arr), axis=0)
            hammer_mean = np.nanmean(hammer_arr, axis=0)
        groups.append(
            {
                "distance_m": distance,
                "label": format_distance_label(distance),
                "n": len(items),
                "fs": fs,
                "time_s": time_s,
                "geo_mean_v": geo_mean,
                "geo_std_v": geo_std,
                "geo_count": geo_count,
                "hammer_mean_v": hammer_mean,
                "trigger_mean_s": float(np.mean(trigger_s)) if trigger_s else None,
            }
        )

    hammer_global: dict[str, Any] | None = None
    if all_hammer_items:
        items = all_hammer_items
        fs0 = _resample_items_to_common_fs(items, filter_settings, ("hammer_zero",))
        rel_start = max((-int(it["trigger_idx"]) for it in items), default=0)
        rel_end = max((int(it["hammer_zero"].size) - int(it["trigger_idx"]) for it in items), default=0)
        if fs0 > 0 and items and rel_end > rel_start + 1:
            stack = np.vstack(
                [
                    segment_nan_padded(
                        it["hammer_zero"], int(it["trigger_idx"]) + rel_start, int(it["trigger_idx"]) + rel_end
                    )
                    for it in items
                ]
            )
            time_s = np.arange(rel_start, rel_end, dtype=np.float64) / fs0
            with np.errstate(invalid="ignore"):
                hammer_mean = np.nanmean(stack, axis=0)
            hammer_global = {"n": len(items), "fs": fs0, "time_s": time_s, "hammer_mean_v": hammer_mean, "inverted": True}

    return groups, hammer_global


def flip_distance_group(
    dataset: FieldDataset,
    annotations: dict[str, PickAnnotation],
    distance_m: float,
    source: str = "manual",
) -> int:
    """Togglea geo_flip en TODAS las capturas de una distancia (validadas o
    no): invierte la polaridad del punto completo sin romper la coherencia
    interna que ya se logro dentro del punto. Devuelve cuantas cambio.
    El caller persiste (save_annotations)."""
    key = round(float(distance_m), 6)
    changed = 0
    for shot in dataset.shots:
        ann = annotations.get(shot.shot_id)
        distance = float(ann.distance_m) if ann is not None else float(shot.distance_m)
        if round(distance, 6) != key:
            continue
        if ann is None:
            ann = auto_pick_shot(shot)
            annotations[shot.shot_id] = ann
        ann.geo_flip = not bool(ann.geo_flip)
        ann.source = source
        changed += 1
    return changed


def _nanmean_rows(stack: np.ndarray) -> np.ndarray:
    """nanmean por columna sin el RuntimeWarning 'Mean of empty slice' en las
    colas todo-NaN (quedan NaN, como con nanmean)."""
    counts = np.sum(np.isfinite(stack), axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.nansum(stack, axis=0) / counts


def _polarity_corr_ready(signal: np.ndarray) -> np.ndarray | None:
    """Señal lista para correlacionar: media afuera y NaN->0 (los NaN de las
    colas desparejas no deben aportar)."""
    x = np.asarray(signal, dtype=np.float64)
    finite = np.isfinite(x)
    if not np.any(finite):
        return None
    out = np.where(finite, x - float(np.nanmean(x)), 0.0)
    return out if np.any(out) else None


def auto_align_polarity(
    dataset: FieldDataset,
    annotations: dict[str, PickAnnotation],
    prefer_filtered: bool = False,
    filter_settings: FilterSettings | None = None,
    alignment_offsets: dict[str, dict[str, float]] | None = None,
    alignment_shot_offsets: dict[str, float] | None = None,
    disabled_folders: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Correccion automatica de polaridad del geofono en dos etapas.

    Etapa A (intra-punto): dentro de cada distancia, SOLO las capturas sin
    validar (accepted y no reviewed) se comparan contra el consenso de las
    validadas de ese punto (que el usuario ya dejo en fase a mano); si el
    producto punto da negativo se les togglea geo_flip. Las validadas no se
    tocan nunca: el flip queda como propuesta que el usuario acepta (o no)
    al revisarlas en Capturas. Si el punto no tiene validadas, el consenso
    es la mayoria actual de las no validadas (cambios minimos).

    Etapa B (inter-punto): los promedios de cada distancia (solo validadas,
    igual que el waterfall) se encadenan desde la distancia menor; si la
    correlacion cruzada (con busqueda de lag, que absorbe el moveout) contra
    el vecino ya alineado da pico negativo, se invierte el punto COMPLETO
    via flip_distance_group (asi no se rompe la fase interna).

    No guarda nada a disco; el caller persiste las anotaciones. Idempotente:
    una segunda corrida no deberia cambiar nada.
    """
    grouped: dict[float, list[dict[str, Any]]] = {}
    for shot in dataset.shots:
        ann = annotations.get(shot.shot_id)
        if ann is None or not ann.accepted:
            continue
        if is_folder_disabled(disabled_folders, ann.distance_m, shot.folder_name):
            # Carpeta desactivada: no participa del consenso ni de la cadena
            # entre puntos (no va al waterfall).
            continue
        offset_s = get_alignment_offset(
            alignment_offsets, ann.distance_m, shot.folder_name,
            shot_id=shot.shot_id, shot_offsets=alignment_shot_offsets,
        )
        item = _prepare_shot_for_grouping(shot, ann, prefer_filtered, filter_settings, offset_s=offset_s)
        if item is None:
            continue
        grouped.setdefault(round(float(ann.distance_m), 6), []).append(item)

    stage_a_flipped: list[str] = []
    stage_b_flipped: list[float] = []
    stage_b_skipped: list[float] = []
    group_means: list[tuple[float, float, np.ndarray]] = []  # (distancia, fs, promedio validadas)

    for distance in sorted(grouped):
        items = grouped[distance]
        fs = _resample_items_to_common_fs(items, filter_settings, ("geo_zero",))
        if fs <= 0:
            continue
        rel_start = max(-int(item["trigger_idx"]) for item in items)
        rel_end = max(int(item["geo_zero"].size) - int(item["trigger_idx"]) for item in items)
        if rel_end <= rel_start + 1:
            continue
        for item in items:
            start = int(item["trigger_idx"]) + rel_start
            end = int(item["trigger_idx"]) + rel_end
            item["geo_seg"] = segment_nan_padded(item["geo_zero"], start, end)
        validated = [item for item in items if item["annotation"].reviewed]
        candidates = [item for item in items if not item["annotation"].reviewed]

        # ---- Etapa A: enfasar las no validadas contra el consenso del punto.
        if candidates:
            reference_items = validated if validated else candidates
            reference = _nanmean_rows(np.vstack([item["geo_seg"] for item in reference_items]))
            for item in candidates:
                product = item["geo_seg"] * reference
                if not np.any(np.isfinite(product)):
                    continue
                score = float(np.nansum(product))
                if score < 0:
                    ann = item["annotation"]
                    ann.geo_flip = not bool(ann.geo_flip)
                    ann.source = "auto_polaridad"
                    item["geo_seg"] = -item["geo_seg"]
                    stage_a_flipped.append(f"{item['shot'].folder_name}/{item['shot'].capture_name}")

        # El promedio inter-punto usa solo validadas (igual que el waterfall).
        if validated:
            mean = _nanmean_rows(np.vstack([item["geo_seg"] for item in validated]))
            group_means.append((distance, fs, mean))
        else:
            stage_b_skipped.append(distance)

    # ---- Etapa B: encadenar promedios desde la distancia menor.
    if len(group_means) >= 2:
        fs_common = min(fs for _, fs, _ in group_means)
        prepared: list[tuple[float, np.ndarray | None]] = []
        for distance, fs, mean in group_means:
            # NaN->0 ANTES de resamplear: resample_poly propaga NaN.
            ready = _polarity_corr_ready(mean)
            if ready is not None and abs(fs - fs_common) > 1e-6:
                ready = resample_signal(ready, fs, fs_common)
            prepared.append((distance, ready))
        previous = prepared[0][1]
        for distance, current in prepared[1:]:
            if current is None:
                stage_b_skipped.append(distance)
                continue
            if previous is None:
                previous = current
                continue
            corr = correlate(current, previous, mode="full", method="auto")
            peak = int(np.argmax(np.abs(corr)))
            if corr[peak] < 0:
                flip_distance_group(dataset, annotations, distance, source="auto_polaridad")
                current = -current
                stage_b_flipped.append(distance)
            previous = current

    return {
        "groups": len(grouped),
        "stage_a_flipped": stage_a_flipped,
        "stage_b_flipped_distances": stage_b_flipped,
        "stage_b_skipped_distances": stage_b_skipped,
    }


@dataclass
class ExportResult:
    output_dir: Path
    sample_count: int
    average_count: int
    skipped_count: int
    waterfall_png: Path | None
    waterfall_pdf: Path | None
    manifest_json: Path


def export_processed(
    dataset: FieldDataset,
    annotations: dict[str, PickAnnotation],
    output_dir: str | Path,
    prefer_filtered: bool = False,
    average_arrivals: dict[str, AverageArrivalAnnotation] | None = None,
    filter_settings: FilterSettings | None = None,
    alignment_offsets: dict[str, dict[str, float]] | None = None,
    alignment_shot_offsets: dict[str, float] | None = None,
    disabled_folders: dict[str, list[str]] | None = None,
) -> ExportResult:
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    samples_dir = output_dir / "muestras"
    averages_dir = output_dir / "promedios"
    samples_dir.mkdir(parents=True, exist_ok=True)
    averages_dir.mkdir(parents=True, exist_ok=True)

    grouped: dict[float, list[dict[str, Any]]] = {}
    all_hammer_items: list[dict[str, Any]] = []
    manifest_samples: list[dict[str, Any]] = []
    skipped_samples: list[dict[str, str]] = []
    skipped_count = 0

    for shot in dataset.shots:
        ann = annotations.get(shot.shot_id)
        if ann is None:
            ann = auto_pick_shot(shot, prefer_filtered=prefer_filtered)
        if not ann.accepted:
            skipped_count += 1
            skipped_samples.append(_skip_record(shot, "not accepted"))
            continue

        offset_s = get_alignment_offset(
            alignment_offsets, ann.distance_m, shot.folder_name,
            shot_id=shot.shot_id, shot_offsets=alignment_shot_offsets,
        )
        item = _prepare_shot_for_grouping(shot, ann, prefer_filtered, filter_settings, offset_s=offset_s)
        if item is None:
            skipped_count += 1
            skipped_samples.append(_skip_record(shot, "missing sample rate or empty signal"))
            continue
        fs = float(item["fs"])
        hammer = item["hammer"]
        geo = item["geo"]
        hammer_zero = item["hammer_zero"]
        geo_zero = item["geo_zero"]
        trigger_idx = int(item["trigger_idx"])
        arrival_idx = trigger_idx
        n = int(hammer.size)
        time_s = (np.arange(n, dtype=np.float64) - trigger_idx) / fs

        distance = float(ann.distance_m)
        label = format_distance_label(distance)
        sample_name = safe_filename(f"{label}_{shot.folder_name}_{shot.capture_name}")
        sample_dir = samples_dir / label
        sample_dir.mkdir(parents=True, exist_ok=True)
        npz_path = sample_dir / f"{sample_name}.npz"
        csv_path = sample_dir / f"{sample_name}.csv"
        meta_path = sample_dir / f"{sample_name}.json"

        np.savez_compressed(
            npz_path,
            time_s=time_s,
            hammer_v=hammer,
            hammer_zero_v=hammer_zero,
            geo_v=geo,
            geo_zero_v=geo_zero,
            fs=fs,
            trigger_s=float(ann.trigger_s),
            arrival_s=float(ann.trigger_s),
            travel_time_s=0.0,
            trigger_index=trigger_idx,
            arrival_index=arrival_idx,
            distance_m=distance,
            shot_id=shot.shot_id,
        )
        _write_signal_csv(csv_path, time_s, hammer, hammer_zero, geo, geo_zero)
        sample_meta = {
            "shot_id": shot.shot_id,
            "folder": shot.folder_name,
            "capture": shot.capture_name,
            "distance_m": distance,
            "fs": fs,
            "trigger_s": float(ann.trigger_s),
            "arrival_s": float(ann.trigger_s),
            "travel_time_s": 0.0,
            "geo_flip": bool(ann.geo_flip),
            "npz": str(npz_path),
            "csv": str(csv_path),
        }
        meta_path.write_text(json.dumps(sample_meta, indent=2), encoding="utf-8")
        manifest_samples.append(sample_meta)
        if not ann.reviewed or is_folder_disabled(disabled_folders, ann.distance_m, shot.folder_name):
            # La muestra individual se exporta igual (queda en disco para
            # quien quiera revisarla), pero solo entra al promedio/waterfall
            # una vez marcada OK a mano (ver compute_average_groups) y si su
            # carpeta no fue desactivada en Enfase.
            continue
        grouped.setdefault(distance, []).append(item)
        # El hammer ya se carga siempre en polaridad invertida (convencion
        # fija), asi que va directo al promedio global.
        all_hammer_items.append(
            {
                "fs": fs,
                "trigger_s": float(item["trigger_s"]),
                "trigger_idx": trigger_idx,
                "hammer_zero": hammer_zero,
            }
        )

    average_arrivals = average_arrivals or {}
    averages = _export_averages(grouped, averages_dir, average_arrivals, filter_settings)
    hammer_global = _export_global_hammer(all_hammer_items, averages_dir, filter_settings)
    waterfall_png, waterfall_pdf = _export_waterfall(averages, output_dir, average_arrivals, hammer_global=hammer_global)
    _write_duplicate_report(dataset, output_dir)

    manifest = {
        "schema": SCHEMA,
        "created_at": utc_now_iso(),
        "raw_root": str(dataset.raw_root),
        "prefer_filtered": bool(prefer_filtered),
        "sample_count": len(manifest_samples),
        "average_count": len(averages),
        "skipped_count": skipped_count,
        "skipped_samples": skipped_samples,
        "samples": manifest_samples,
        "averages": averages,
        "hammer_global": hammer_global,
        "average_arrivals": [asdict(average_arrivals[label]) for label in sorted(average_arrivals)],
        "filter_settings": asdict(filter_settings) if filter_settings else None,
        "alignment_offsets": [
            {"label": label, "folder": folder, "offset_s": float(offset)}
            for label in sorted(alignment_offsets or {})
            for folder, offset in sorted((alignment_offsets or {})[label].items())
            if abs(float(offset)) > 1e-12
        ],
        "alignment_shot_offsets": [
            {"shot_id": shot_id, "offset_s": float(offset)}
            for shot_id, offset in sorted((alignment_shot_offsets or {}).items())
            if abs(float(offset)) > 1e-12
        ],
        "waterfall_png": str(waterfall_png) if waterfall_png else None,
        "waterfall_pdf": str(waterfall_pdf) if waterfall_pdf else None,
        "duplicate_groups": [asdict(group) for group in dataset.duplicate_groups],
    }
    manifest_json = output_dir / "manifest.json"
    manifest_json.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    return ExportResult(
        output_dir=output_dir,
        sample_count=len(manifest_samples),
        average_count=len(averages),
        skipped_count=skipped_count,
        waterfall_png=waterfall_png,
        waterfall_pdf=waterfall_pdf,
        manifest_json=manifest_json,
    )


def format_distance_label(distance_m: float) -> str:
    if abs(distance_m - round(distance_m)) < 1e-6:
        return f"{int(round(distance_m)):03d}m"
    return f"{distance_m:07.2f}m".replace(".", "p").replace("-", "neg")


def safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    value = re.sub(r"_+", "_", value)
    return value.strip("._") or "sample"


def default_annotations_path(raw_root: str | Path) -> Path:
    return _procesados_dir_for(raw_root) / DEFAULT_ANNOTATIONS_NAME


def default_output_dir(raw_root: str | Path) -> Path:
    raw_root = Path(raw_root).resolve()
    return _PROCESADOS_ROOT / f"{raw_root.name}_procesado"


def default_average_arrivals_path(output_dir: str | Path) -> Path:
    return Path(output_dir).resolve() / DEFAULT_AVERAGE_ARRIVALS_NAME


def _discover_folder_shots(
    folder: Path,
    folder_hash: str,
    duplicate_of: str | None,
    raw_root: Path,
) -> list[FieldShot]:
    shots: list[FieldShot] = []
    for capture_dir in _capture_dirs(folder):
        meta = _read_json(capture_dir / "metadata.json")
        order = _capture_order(capture_dir, meta)
        channels = _discover_channels(folder, capture_dir)
        hammer = _first_role(channels, "hammer")
        # Un tendido puede tener N geófonos: cada uno es un disparo distinto
        # (mismo golpe, otra distancia), que es exactamente lo que necesita
        # MASW. Antes se tomaba sólo el primero y los demás no existían para
        # ninguna de las dos interfaces.
        geos = sorted([ch for ch in channels if ch.role == "geo"], key=_channel_sort_key)
        if hammer is None or not geos:
            continue
        rel_capture = _relative_text(capture_dir, raw_root)
        base_id = hashlib.sha1(rel_capture.encode("utf-8")).hexdigest()[:16]
        for index, geo in enumerate(geos):
            fs = float(meta.get("fs") or hammer.fs or geo.fs or 0.0)
            distance = _distance_from_channel(geo)
            if distance is None:
                distance = _distance_from_name(folder.name)
            if distance is None:
                distance = 0.0
            # El PRIMER geófono conserva el id histórico (hash de la ruta de la
            # captura, sin sufijo): con un solo geófono —que es todo lo que hay
            # grabado hasta hoy— los ids no cambian y las marcas ya hechas
            # siguen valiendo. Los demás se distinguen por su nodo.
            if index == 0:
                shot_id = base_id
            else:
                suffix = geo.pcb_id or (str(geo.node_index) if geo.node_index is not None else str(index))
                shot_id = hashlib.sha1(f"{rel_capture}#{suffix}".encode("utf-8")).hexdigest()[:16]
            shots.append(
                FieldShot(
                    shot_id=shot_id,
                    folder=folder,
                    capture_dir=capture_dir,
                    folder_name=folder.name,
                    capture_name=capture_dir.name,
                    order=order,
                    fs=fs,
                    distance_m=float(distance),
                    hammer=hammer,
                    geo=geo,
                    folder_hash=folder_hash,
                    duplicate_of=duplicate_of,
                )
            )
    return shots


def _capture_dirs(folder: Path) -> list[Path]:
    captures_root = folder / "captures"
    if captures_root.is_dir():
        captures = [p for p in captures_root.iterdir() if p.is_dir()]
        if captures:
            return sorted(captures, key=lambda p: (_capture_order(p, _read_json(p / "metadata.json")), p.name))
    return [folder]


def _discover_channels(folder: Path, capture_dir: Path) -> list[ChannelRef]:
    meta = _read_json(capture_dir / "metadata.json")
    nodes = meta.get("nodes", [])
    channels: list[ChannelRef] = []
    for node in nodes:
        role = _node_role(node)
        if role not in {"hammer", "geo"}:
            continue
        raw_file = _resolve_node_file(folder, capture_dir, node, "raw_file", "raw_f32le.bin")
        filt_file = _resolve_node_file(folder, capture_dir, node, "filt_file", "filt_f32le.bin")
        if raw_file is None and filt_file is None:
            continue
        channels.append(
            ChannelRef(
                role=role,
                node_index=_int_or_none(node.get("index", node.get("node_id"))),
                pcb_id=str(node.get("pcb_id") or node.get("slave_id") or ""),
                raw_file=raw_file,
                filt_file=filt_file,
                fs=float(node.get("fs") or meta.get("fs") or 0.0),
                position_m=_float_or_none(
                    node.get("position_m", node.get("offset_m_from_hammer", node.get("hammer_offset_m")))
                ),
                # Convencion de polaridad fija: geo siempre NO invertido,
                # hammer siempre invertido. invert_signal aca es la negacion
                # a aplicar al cargar para llegar a esa convencion: si el geo
                # viene marcado invertido se desinvierte, y si el hammer viene
                # sin invertir se invierte.
                invert_signal=(
                    bool(node.get("invert_signal", False))
                    if role == "geo"
                    else not bool(node.get("invert_signal", False))
                ),
                label=str(node.get("type") or node.get("name") or ""),
            )
        )
    if channels:
        return channels
    return _discover_channels_by_dirs(folder, capture_dir)


def _discover_channels_by_dirs(folder: Path, capture_dir: Path) -> list[ChannelRef]:
    channels: list[ChannelRef] = []
    for child in sorted([p for p in capture_dir.iterdir() if p.is_dir()], key=lambda p: p.name):
        raw_file = child / "raw_f32le.bin"
        filt_file = child / "filt_f32le.bin"
        if not raw_file.exists() and not filt_file.exists():
            continue
        name_l = child.name.lower()
        if "hammer" in name_l:
            role = "hammer"
        elif "geo" in name_l:
            role = "geo"
        elif "s1" in name_l:
            role = "hammer"
        else:
            role = "geo"
        channels.append(
            ChannelRef(
                role=role,
                node_index=None,
                pcb_id=child.name,
                raw_file=raw_file if raw_file.exists() else None,
                filt_file=filt_file if filt_file.exists() else None,
                fs=float(_read_json(capture_dir / "metadata.json").get("fs") or 0.0),
                position_m=_distance_from_name(folder.name),
                # Misma convencion fija que en _discover_channels; sin
                # metadata se asume que el archivo viene sin invertir.
                invert_signal=(role == "hammer"),
                label=child.name,
            )
        )
    return _dedupe_by_slave(channels)


def _slave_token(name: str) -> str | None:
    """El ``s<N>`` final del nombre de un directorio de nodo, si lo tiene.

    ``esclavo_2_s2`` y ``geo1_s2`` son el MISMO nodo (S2) exportado con dos
    nombres, no dos receptores.
    """
    match = re.search(r"_s(\d+)$", name.lower())
    return match.group(1) if match else None


def _dedupe_by_slave(channels: list[ChannelRef]) -> list[ChannelRef]:
    """Un canal por (rol, nodo físico).

    Sin esto, una captura con los dos exportes de cada nodo aparecería como un
    tendido de dos geófonos y se inventaría un disparo que no existe. Se
    conserva el primero por ``_channel_sort_key``, que es exactamente el que
    elegía ``_first_role`` antes de que hubiera soporte de N geófonos.
    """
    vistos: dict[tuple[str, str], ChannelRef] = {}
    sueltos: list[ChannelRef] = []
    for channel in sorted(channels, key=_channel_sort_key):
        token = _slave_token(channel.pcb_id or "")
        if token is None:
            sueltos.append(channel)      # sin id de nodo no se puede deducir: no se toca
            continue
        vistos.setdefault((channel.role, token), channel)
    return list(vistos.values()) + sueltos


def _resolve_node_file(
    folder: Path,
    capture_dir: Path,
    node: dict[str, Any],
    key: str,
    default_name: str,
) -> Path | None:
    rel = node.get(key)
    candidates: list[Path] = []
    if rel:
        rel_path = Path(str(rel).replace("\\", "/"))
        if rel_path.is_absolute():
            candidates.append(rel_path)
        else:
            candidates.append(folder / rel_path)
            candidates.append(capture_dir / rel_path)
    data_dir = node.get("data_dir")
    if data_dir:
        data_path = Path(str(data_dir).replace("\\", "/"))
        if data_path.is_absolute():
            candidates.append(data_path / default_name)
        else:
            candidates.append(folder / data_path / default_name)
            candidates.append(capture_dir / data_path / default_name)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def _capture_order(capture_dir: Path, meta: dict[str, Any]) -> int:
    order = _int_or_none(meta.get("order"))
    if order is not None:
        return order
    match = re.search(r"(\d+)", capture_dir.name)
    return int(match.group(1)) if match else 0


def _node_role(node: dict[str, Any]) -> str:
    parts = " ".join(
        str(node.get(key) or "")
        for key in ("role", "type", "hw_type", "name", "data_dir", "raw_file", "filt_file")
    ).lower()
    if "hammer" in parts:
        return "hammer"
    if "geo" in parts:
        return "geo"
    return "unknown"


# ── API pública que consume el servidor web ──────────────────────────────────
# Las tres de abajo son la versión pública de helpers que ya usaba discover_dataset.
# Existen para que la web llame a ESTA implementación en vez de tener la suya:
# tener dos deducciones de rol ya hizo que el catálogo del servidor y
# discover_dataset no coincidieran (194 vs 186 capturas).

def node_role(node: dict[str, Any]) -> str:
    """Rol de un nodo de ``metadata.json``: ``hammer`` / ``geo`` / ``unknown``.

    Mira ``role`` y además ``type``/``hw_type``/``name``/``data_dir``/``*_file``,
    porque no toda captura trae ``role`` escrito.
    """
    return _node_role(node)


def resolve_node_file(
    folder: str | Path,
    capture_dir: str | Path,
    node: dict[str, Any],
    key: str = "raw_file",
    default_name: str = "raw_f32le.bin",
) -> Path | None:
    """Ubica el ``.bin`` de un nodo. Contempla rutas absolutas y relativas a la
    carpeta o a la captura. Es la resolución que usa ``discover_dataset``: el
    catálogo del servidor tiene que usar ésta y no una propia, o marca "sin
    señal" nodos que el disparo sí encuentra."""
    return _resolve_node_file(Path(folder), Path(capture_dir), node, key, default_name)


def discover_capture_channels(folder: str | Path, capture_dir: str | Path) -> list[ChannelRef]:
    """Canales de UNA captura, sin exigir el par hammer+geo.

    ``discover_dataset`` descarta la captura incompleta porque su unidad es el
    disparo; esto devuelve lo que haya, para poder graficar igual.
    """
    return _discover_channels(Path(folder), Path(capture_dir))


def zero_by_pretrigger(signal: np.ndarray, trigger_idx: int, fs: float) -> np.ndarray:
    """Resta la línea de base medida ANTES del golpe (mediana de
    ``[trigger-0.25 s, trigger-0.005 s]``). Es lo que se grafica en la app."""
    return _zero_by_pretrigger(signal, trigger_idx, fs)


def _first_role(channels: Iterable[ChannelRef], role: str) -> ChannelRef | None:
    matches = [ch for ch in channels if ch.role == role]
    if not matches:
        return None
    return sorted(matches, key=_channel_sort_key)[0]


def _channel_sort_key(channel: ChannelRef) -> tuple[str, int, str]:
    return (channel.role, channel.node_index if channel.node_index is not None else 999, channel.pcb_id)


def _distance_from_channel(channel: ChannelRef) -> float | None:
    if channel.position_m is None:
        return None
    if not math.isfinite(channel.position_m):
        return None
    return float(channel.position_m)


def _distance_from_name(name: str) -> float | None:
    lowered = name.lower().replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)\s*metros?", lowered)
    if match:
        return float(match.group(1))
    match = re.search(r"^muestras?(\d+(?:\.\d+)?)(?:_|$)", lowered)
    if match:
        return float(match.group(1))
    return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.digest()


def _finite_signal(signal: np.ndarray) -> np.ndarray:
    x = np.asarray(signal, dtype=np.float64)
    if np.all(np.isfinite(x)):
        return x
    return np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)


def _moving_mean(values: np.ndarray, window: int) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    window = max(1, int(window))
    if window <= 1 or values.size == 0:
        return values
    kernel = np.ones(window, dtype=np.float64) / float(window)
    return np.convolve(values, kernel, mode="same")


def _robust_center_scale(values: np.ndarray) -> tuple[float, float]:
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return 0.0, 1.0
    center = float(np.median(values))
    mad = float(np.median(np.abs(values - center)) * 1.4826)
    std_floor = float(np.std(values) * 0.05)
    return center, max(mad, std_floor, 1e-12)


def _first_sustained(mask: np.ndarray, hold: int) -> int | None:
    count = 0
    hold = max(1, int(hold))
    for idx, value in enumerate(mask):
        if bool(value):
            count += 1
            if count >= hold:
                return idx - hold + 1
        else:
            count = 0
    return None


def _zero_by_pretrigger(signal: np.ndarray, trigger_idx: int, fs: float) -> np.ndarray:
    start = max(0, trigger_idx - int(round(fs * 0.25)))
    end = max(start + 1, trigger_idx - int(round(fs * 0.005)))
    if end > signal.size:
        end = min(signal.size, max(start + 1, trigger_idx))
    baseline = float(np.median(signal[start:end])) if end > start else float(np.median(signal))
    return signal.astype(np.float32, copy=False) - np.float32(baseline)


def segment_nan_padded(signal: np.ndarray, start: int, end: int) -> np.ndarray:
    """Slice [start:end); donde no hay dato real se deja NaN (no se promedia
    ni se dibuja ahi), en vez de forzar la curva a 0 cuando la muestra termina
    antes que las demas."""
    out = np.full(max(0, end - start), np.nan, dtype=np.float32)
    clip_start = max(start, 0)
    stop = min(end, int(signal.size))
    if stop > clip_start:
        out[clip_start - start : stop - start] = np.asarray(signal[clip_start:stop], dtype=np.float32)
    return out


def _write_signal_csv(
    path: Path,
    time_s: np.ndarray,
    hammer: np.ndarray,
    hammer_zero: np.ndarray,
    geo: np.ndarray,
    geo_zero: np.ndarray,
) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time_s", "hammer_v", "hammer_zero_v", "geo_v", "geo_zero_v"])
        for row in zip(time_s, hammer, hammer_zero, geo, geo_zero):
            writer.writerow([f"{float(v):.9g}" for v in row])


def _export_averages(
    grouped: dict[float, list[dict[str, Any]]],
    averages_dir: Path,
    average_arrivals: dict[str, AverageArrivalAnnotation] | None = None,
    filter_settings: FilterSettings | None = None,
) -> list[dict[str, Any]]:
    average_arrivals = average_arrivals or {}
    results: list[dict[str, Any]] = []
    for distance in sorted(grouped):
        items = grouped[distance]
        if not items:
            continue
        # Capturas con fs distinta se llevan a una fs comun antes de apilar.
        fs = _resample_items_to_common_fs(items, filter_settings, ("hammer_zero", "geo_zero"))
        if fs <= 0:
            continue
        rel_start = max(-int(item["trigger_idx"]) for item in items)
        # Largo maximo: las muestras mas cortas quedan en NaN mas alla de su
        # propio final, para no promediar ni dibujar un tramo inventado.
        rel_end = max(int(item["geo_zero"].size) - int(item["trigger_idx"]) for item in items)
        if rel_end <= rel_start + 1:
            continue
        geo_stack = []
        hammer_stack = []
        trigger_s = []
        for item in items:
            start = int(item["trigger_idx"]) + rel_start
            end = int(item["trigger_idx"]) + rel_end
            geo_stack.append(segment_nan_padded(item["geo_zero"], start, end))
            hammer_stack.append(segment_nan_padded(item["hammer_zero"], start, end))
            trigger_s.append(float(item["trigger_s"]))
        geo_arr = np.vstack(geo_stack)
        hammer_arr = np.vstack(hammer_stack)
        time_s = np.arange(rel_start, rel_end, dtype=np.float64) / fs
        with np.errstate(invalid="ignore"):
            geo_mean = np.nanmean(geo_arr, axis=0)
            geo_std = np.nanstd(geo_arr, axis=0)
            hammer_mean = np.nanmean(hammer_arr, axis=0)
        label = format_distance_label(distance)
        arrival = average_arrivals.get(label)
        arrival_s = float(arrival.arrival_s) if arrival is not None else np.nan
        arrival_reviewed = bool(arrival.reviewed) if arrival is not None else False
        npz_path = averages_dir / f"{label}_promedio.npz"
        csv_path = averages_dir / f"{label}_promedio.csv"
        mat_path = averages_dir / f"{label}_promedio.mat"
        np.savez_compressed(
            npz_path,
            time_s=time_s,
            geo_mean_v=geo_mean,
            geo_std_v=geo_std,
            hammer_mean_v=hammer_mean,
            fs=fs,
            distance_m=distance,
            n=len(items),
            trigger_s=np.asarray(trigger_s, dtype=np.float64),
            arrival_s=arrival_s,
            arrival_reviewed=arrival_reviewed,
        )
        savemat(
            mat_path,
            {
                "time_s": time_s,
                "geo_mean_v": geo_mean,
                "geo_std_v": geo_std,
                "hammer_mean_v": hammer_mean,
                "fs": fs,
                "distance_m": distance,
                "n": len(items),
                "trigger_s": np.asarray(trigger_s, dtype=np.float64),
                "arrival_s": arrival_s,
                "arrival_reviewed": arrival_reviewed,
            },
            do_compression=True,
            oned_as="column",
        )
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["time_s", "geo_mean_v", "geo_std_v", "hammer_mean_v"])
            for row in zip(time_s, geo_mean, geo_std, hammer_mean):
                writer.writerow([f"{float(v):.9g}" for v in row])
        results.append(
            {
                "distance_m": distance,
                "label": label,
                "n": len(items),
                "fs": fs,
                "npz": str(npz_path),
                "csv": str(csv_path),
                "mat": str(mat_path),
                "time_start_s": float(time_s[0]),
                "time_end_s": float(time_s[-1]),
                "trigger_mean_s": float(np.mean(trigger_s)) if trigger_s else None,
                "arrival_s": None if np.isnan(arrival_s) else arrival_s,
                "arrival_reviewed": arrival_reviewed,
            }
        )
    return results


def _export_global_hammer(
    items: list[dict[str, Any]],
    averages_dir: Path,
    filter_settings: FilterSettings | None = None,
) -> dict[str, Any] | None:
    """Promedio de TODOS los hammers aceptados, alineados al trigger.

    Las señales ya vienen en polaridad invertida; las que terminan antes que
    la mas larga quedan en NaN mas alla de su propio final (no se promedia
    ni se dibuja un tramo inventado en 0). Capturas con fs distinta se
    resamplean a la fs comun del conjunto.
    """
    if not items:
        return None
    fs = _resample_items_to_common_fs(items, filter_settings, ("hammer_zero",))
    if fs <= 0:
        return None
    rel_start = max(-int(item["trigger_idx"]) for item in items)
    rel_end = max(int(item["hammer_zero"].size) - int(item["trigger_idx"]) for item in items)
    if rel_end <= rel_start + 1:
        return None
    stack = np.vstack(
        [
            segment_nan_padded(
                item["hammer_zero"],
                int(item["trigger_idx"]) + rel_start,
                int(item["trigger_idx"]) + rel_end,
            )
            for item in items
        ]
    )
    time_s = np.arange(rel_start, rel_end, dtype=np.float64) / fs
    with np.errstate(invalid="ignore"):
        hammer_mean = np.nanmean(stack, axis=0)
        hammer_std = np.nanstd(stack, axis=0)
    npz_path = averages_dir / "hammer_global_promedio.npz"
    csv_path = averages_dir / "hammer_global_promedio.csv"
    np.savez_compressed(
        npz_path,
        time_s=time_s,
        hammer_mean_v=hammer_mean,
        hammer_std_v=hammer_std,
        fs=fs,
        n=len(items),
        inverted=True,
    )
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time_s", "hammer_mean_v", "hammer_std_v"])
        for row in zip(time_s, hammer_mean, hammer_std):
            writer.writerow([f"{float(v):.9g}" for v in row])
    return {
        "n": len(items),
        "fs": fs,
        "inverted": True,
        "npz": str(npz_path),
        "csv": str(csv_path),
        "time_start_s": float(time_s[0]),
        "time_end_s": float(time_s[-1]),
    }


def _avg_time_signal(avg: dict[str, Any]) -> tuple[float, np.ndarray, np.ndarray]:
    """Arrays (distance_m, time_s, geo_mean_v) de un promedio, ya sea que
    venga con arrays en memoria (revision rapida) o con ruta a .npz (export
    a disco)."""
    if "time_s" in avg and "geo_mean_v" in avg:
        return float(avg["distance_m"]), np.asarray(avg["time_s"]), np.asarray(avg["geo_mean_v"])
    with np.load(avg["npz"]) as data:
        return float(avg["distance_m"]), data["time_s"].copy(), data["geo_mean_v"].copy()


def hammer_global_time_signal(hammer_global: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Arrays (time_s, hammer_mean_v) del hammer global, en memoria o desde
    .npz, igual que `_avg_time_signal`."""
    if "time_s" in hammer_global and "hammer_mean_v" in hammer_global:
        return np.asarray(hammer_global["time_s"]), np.asarray(hammer_global["hammer_mean_v"])
    with np.load(hammer_global["npz"]) as data:
        return data["time_s"].copy(), data["hammer_mean_v"].copy()


def build_waterfall_matrix(
    averages: list[dict[str, Any]],
) -> tuple[np.ndarray, list[float], np.ndarray] | None:
    """Interpola los promedios a una base de tiempo comun.

    Devuelve (common_time, distances, matrix). La base de tiempo cubre desde
    el inicio mas temprano hasta el final mas tardio entre todos los
    promedios (la curva de mayor duracion, tipicamente la mas lejana, define
    el largo de la imagen). Donde una curva mas corta no tiene datos reales
    queda en NaN: no se dibuja nada ahi en vez de aplanarse a 0, para no dar
    la impresion de que la señal "desaparece".
    """
    if not averages:
        return None
    loaded: list[tuple[float, np.ndarray, np.ndarray]] = [_avg_time_signal(avg) for avg in averages]
    loaded.sort(key=lambda item: item[0])
    start = min(float(time[0]) for _dist, time, _sig in loaded)
    end = max(float(time[-1]) for _dist, time, _sig in loaded)
    if end <= start:
        return None
    fs = 1.0 / float(np.median(np.diff(loaded[0][1])))
    n = int(math.floor((end - start) * fs)) + 1
    common_time = start + np.arange(n, dtype=np.float64) / fs
    distances = [distance for distance, _time, _sig in loaded]
    matrix_arr = np.vstack(
        [_interp_nan_outside(common_time, time, signal) for _dist, time, signal in loaded]
    )
    return common_time, distances, matrix_arr


def _interp_nan_outside(common_time: np.ndarray, time: np.ndarray, signal: np.ndarray) -> np.ndarray:
    """np.interp pero devuelve NaN fuera de [time[0], time[-1]] en vez de
    extender con el valor del borde (evita inventar una meseta plana)."""
    finite = np.isfinite(signal)
    if not np.any(finite):
        return np.full(common_time.shape, np.nan, dtype=np.float64)
    time = time[finite]
    signal = signal[finite]
    out = np.interp(common_time, time, signal, left=np.nan, right=np.nan)
    return out


def _render_waterfall_figure(
    common_time: np.ndarray,
    distances: list[float],
    matrix_arr: np.ndarray,
    average_arrivals: dict[str, AverageArrivalAnnotation],
    hammer_global: dict[str, Any] | None,
    figsize: tuple[float, float],
):
    import matplotlib.pyplot as plt

    start = float(common_time[0])
    end = float(common_time[-1])
    fig, ax = plt.subplots(figsize=figsize, dpi=150)
    distances_arr = np.asarray(distances, dtype=np.float64)
    spacing = float(np.median(np.diff(distances_arr))) if len(distances_arr) > 1 else 1.0
    spacing = max(spacing, 1.0)
    for distance, signal in zip(distances, matrix_arr):
        with np.errstate(invalid="ignore"):
            peak = float(np.nanmax(np.abs(signal))) if np.any(np.isfinite(signal)) else 1.0
        peak = peak or 1.0
        trace = signal / peak * spacing * 0.4 + distance
        ax.plot(common_time, trace, linewidth=1.0, color="black")
        ax.text(common_time[0], distance, format_distance_label(distance), ha="right", va="center", fontsize=8)
        arrival = average_arrivals.get(format_distance_label(distance))
        if arrival is not None and arrival.reviewed and start <= arrival.arrival_s <= end:
            ax.plot(
                [arrival.arrival_s, arrival.arrival_s],
                [distance - spacing * 0.35, distance + spacing * 0.35],
                color="#d62728",
                linewidth=1.2,
            )
    if hammer_global:
        try:
            hammer_time, hammer_mean = hammer_global_time_signal(hammer_global)
        except Exception:
            hammer_time = None
        if hammer_time is not None:
            interp = _interp_nan_outside(common_time, hammer_time, hammer_mean)
            with np.errstate(invalid="ignore"):
                peak = float(np.nanmax(np.abs(interp))) if np.any(np.isfinite(interp)) else 1.0
            peak = peak or 1.0
            base = float(min(distances)) - spacing
            ax.plot(common_time, interp / peak * spacing * 0.4 + base, linewidth=1.2, color="#0066cc")
            ax.text(
                common_time[0],
                base,
                f"hammer prom. (n={hammer_global.get('n', '?')})",
                ha="right",
                va="center",
                fontsize=8,
                color="#0066cc",
            )
    ax.set_title("Waterfall de senales promediadas por distancia")
    ax.set_xlabel("Tiempo relativo al hammer [s]")
    ax.set_ylabel("Distancia [m] + amplitud normalizada")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    return fig


def _export_waterfall(
    averages: list[dict[str, Any]],
    output_dir: Path,
    average_arrivals: dict[str, AverageArrivalAnnotation] | None = None,
    hammer_global: dict[str, Any] | None = None,
) -> tuple[Path | None, Path | None]:
    average_arrivals = average_arrivals or {}
    built = build_waterfall_matrix(averages)
    if built is None:
        return None, None
    common_time, distances, matrix_arr = built
    matrix_csv = output_dir / "waterfall_matrix.csv"
    with matrix_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time_s", *[format_distance_label(d) for d in distances]])
        for idx, t in enumerate(common_time):
            writer.writerow([f"{float(t):.9g}", *[f"{float(row[idx]):.9g}" for row in matrix_arr]])
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None, None

    png_path = output_dir / "waterfall_promedios.png"
    fig = _render_waterfall_figure(common_time, distances, matrix_arr, average_arrivals, hammer_global, (12, 7))
    fig.savefig(png_path)
    plt.close(fig)

    # PDF mas alto (una fila por traza) para poder revisar cada distancia con
    # mas detalle sin que se amontonen las curvas como en el PNG.
    n_traces = len(distances) + (1 if hammer_global else 0)
    tall_height = max(7.0, 0.9 * n_traces + 2.0)
    pdf_path = output_dir / "waterfall_promedios.pdf"
    fig_pdf = _render_waterfall_figure(
        common_time, distances, matrix_arr, average_arrivals, hammer_global, (14, tall_height)
    )
    fig_pdf.savefig(pdf_path)
    plt.close(fig_pdf)

    return png_path, pdf_path


def _write_duplicate_report(dataset: FieldDataset, output_dir: Path) -> None:
    report = output_dir / "duplicados_descartados.json"
    report.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "created_at": utc_now_iso(),
                "raw_root": str(dataset.raw_root),
                "duplicate_folder_count": dataset.duplicate_folder_count,
                "groups": [asdict(group) for group in dataset.duplicate_groups],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _annotation_to_dict(annotation: PickAnnotation) -> dict[str, Any]:
    data = asdict(annotation)
    data["arrival_s"] = data["trigger_s"]
    return data


def _skip_record(shot: FieldShot, reason: str) -> dict[str, str]:
    return {
        "shot_id": shot.shot_id,
        "folder": shot.folder_name,
        "capture": shot.capture_name,
        "reason": reason,
    }


def _relative_text(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def copy_annotations_template(raw_root: str | Path, destination: str | Path) -> Path:
    """Copy an existing annotation file if one is present.

    Useful before experiments that should not overwrite the working picks.
    """
    src = default_annotations_path(raw_root)
    dst = Path(destination)
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return dst
