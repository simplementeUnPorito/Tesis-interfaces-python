"""Lógica pura del visor de señal (§3.1 del PORT_PLAN): sin FastAPI, sin
``Request``. Acá vive el decimado min/max y el armado del payload que
consume ``static/js/tabs/capturas_signal.js``. ``routers/dataset.py`` es el
único que la llama, desde un handler síncrono (§4.6: nada de esto puede
correr en el event loop).
"""

from __future__ import annotations

import hashlib
import math
import warnings
from pathlib import Path

import numpy as np

from .pipeline import frd
from .datacache import get_dataset


class UnknownShot(Exception):
    """``shot_id`` que no aparece en ``discover_dataset(raw_root)``."""


class InvalidKind(Exception):
    """``kind`` distinto de ``raw``/``filt``."""


def decimate_minmax(
    x: np.ndarray, max_points: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Envolvente min/max por bucket. Devuelve ``(mins, maxs, rising, stride)``.

    Por qué min/max y no un promedio ni un salteado: el primer arribo es un
    pico de pocas muestras. Promediar lo suaviza hasta hacerlo invisible y
    saltear se lo come cuando el pico cae entre dos muestras elegidas. Con
    min/max, cada píxel dibuja el rango real que hay abajo: la envolvente
    pasa exactamente por los extremos de la señal completa.

    ``rising[i]`` dice si dentro del bucket ``i`` el mínimo ocurrió **antes**
    que el máximo. Sin ese dato el dibujante no sabe en qué orden unir los dos
    puntos de cada columna y termina emitiendo siempre máximo→mínimo: sobre
    cualquier traza suave eso da un diente de sierra que no está en la señal
    (la subida real se dibuja como bajada y el salto a la columna siguiente
    hace el diente). Con el orden temporal correcto la polilínea sigue la forma
    de onda de verdad, igual que la línea sin decimar de pyqtgraph.
    """
    n = int(x.size)
    if n == 0:
        empty = np.array([], dtype=np.float32)
        return empty, empty, np.array([], dtype=bool), 1
    x = x.astype(np.float32, copy=False)
    stride = max(1, -(-n // max_points))        # ceil(n / max_points)
    nb = -(-n // stride)                        # ceil(n / stride)  <= max_points
    pad = nb * stride - n
    if pad:
        x = np.concatenate([x, np.full(pad, np.nan, dtype=np.float32)])
    m = x.reshape(nb, stride)
    with warnings.catch_warnings():             # buckets all-NaN avisan y devuelven NaN
        warnings.simplefilter("ignore", RuntimeWarning)
        mins, maxs = np.nanmin(m, axis=1), np.nanmax(m, axis=1)
    if stride == 1:
        # Un bucket = una muestra: no hay dos puntos que ordenar.
        rising = np.ones(nb, dtype=bool)
    else:
        # argmin/argmax ignorando NaN sin que un bucket entero de NaN reviente:
        # se los manda al extremo que no puede ganar.
        nan = np.isnan(m)
        imin = np.where(nan, np.inf, m).argmin(axis=1)
        imax = np.where(nan, -np.inf, m).argmax(axis=1)
        rising = imin <= imax
    return mins, maxs, rising, stride


def _round6(v: float) -> float | None:
    """6 cifras significativas; no-finito -> ``None`` (nunca ``NaN`` en el JSON)."""
    v = float(v)
    if not math.isfinite(v):
        return None
    return float(f"{v:.6g}")


def _find_shot(raw_root: Path, shot_id: str):
    dataset = get_dataset(raw_root)
    for shot in dataset.shots:
        if shot.shot_id == shot_id:
            return shot
    raise UnknownShot(shot_id)


def capture_annotation(raw_root: Path, anns: dict, shot):
    """Anotación que manda para el **trigger** de este disparo.

    Los N geófonos de un tendido comparten el golpe: hay un solo martillo, un
    solo trigger y un solo tiempo cero. Lo que cambia entre ellos es la
    posición. Así que si este receptor todavía no tiene marca propia pero otro
    de la MISMA captura sí, se usa esa: mover el trigger es una decisión de la
    captura, no de cada geófono.

    Devuelve ``(anotación propia | None, anotación de la captura | None)``.
    """
    propia = anns.get(shot.shot_id)
    if propia is not None:
        return propia, propia
    try:
        dataset = get_dataset(raw_root)
    except FileNotFoundError:
        return None, None
    hermanos = [s for s in dataset.shots
                if s.folder_name == shot.folder_name
                and s.capture_name == shot.capture_name
                and s.shot_id != shot.shot_id]
    # Se prefiere una validada a mano por sobre una automática.
    candidatas = [anns[s.shot_id] for s in hermanos if s.shot_id in anns]
    if not candidatas:
        return None, None
    validadas = [a for a in candidatas if a.reviewed]
    return None, (validadas[0] if validadas else candidatas[0])


def _capture_dir(raw_root: Path, folder: str, capture: str) -> Path:
    """Directorio de una captura. ``(raíz)`` es el layout viejo: la carpeta ES
    la captura (mismo criterio que ``catalog._capture_dirs``)."""
    folder_path = raw_root / folder
    if capture in ("", "(raíz)"):
        return folder_path
    nested = folder_path / "captures" / capture
    return nested if nested.is_dir() else folder_path


def _shot_for_capture(raw_root: Path, folder: str, capture: str):
    """Disparo sintético de una captura que ``discover_dataset`` no devuelve.

    Pasa con las duplicadas (las descarta el dedup por firma) y con las
    incompletas (falta hammer o geo). Se arma igual, con los canales que haya,
    para poder graficarlas: el estado no decide si se dibuja.

    Devuelve ``(shot | None, hammer | None, geo | None)``. ``shot`` sólo existe
    cuando están los dos canales, porque ``auto_pick_shot`` necesita el par.
    """
    folder_path = raw_root / folder
    capture_dir = _capture_dir(raw_root, folder, capture)
    if not capture_dir.is_dir():
        raise UnknownShot(f"{folder}/{capture}")

    channels = frd.discover_capture_channels(folder_path, capture_dir)
    hammer = next((c for c in channels if c.role == "hammer"), None)
    geo = next((c for c in channels if c.role == "geo"), None)
    if hammer is None and geo is None:
        raise UnknownShot(f"{folder}/{capture}")
    if hammer is None or geo is None:
        return None, hammer, geo

    meta_path = capture_dir / "metadata.json"
    meta = frd._read_json(meta_path) if meta_path.is_file() else {}
    rel = str(capture_dir.relative_to(raw_root)).replace("\\", "/")
    shot = frd.FieldShot(
        shot_id=hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16],
        folder=folder_path,
        capture_dir=capture_dir,
        folder_name=folder,
        capture_name=capture_dir.name if capture_dir != folder_path else "(raíz)",
        order=int(meta.get("capture_index", meta.get("order", 0)) or 0),
        fs=float(meta.get("fs") or hammer.fs or geo.fs or 0.0),
        distance_m=float(geo.position_m if geo.position_m is not None else 0.0),
        hammer=hammer,
        geo=geo,
        folder_hash="",
    )
    return shot, hammer, geo


def _channel_payload(
    channel,
    *,
    raw_root: Path,
    prefer_filtered: bool,
    invert_extra: bool,
    trigger_s: float,
    fs: float,
    max_points: int,
) -> dict:
    signal = frd.load_signal(channel, prefer_filtered=prefer_filtered, apply_invert=True)
    flip_applied = False
    if invert_extra:
        signal = -signal
        flip_applied = True

    n = int(signal.size)
    channel_fs = float(channel.fs or fs)
    if n and channel_fs > 0:
        trigger_idx = int(np.clip(round(trigger_s * channel_fs), 0, n - 1))
        zeroed = frd._zero_by_pretrigger(signal, trigger_idx, channel_fs)
    else:
        zeroed = signal.astype(np.float32, copy=False)

    mins, maxs, rising, stride = decimate_minmax(zeroed, max_points)
    buckets = int(mins.size)

    finite = zeroed[np.isfinite(zeroed)]
    y_min = _round6(float(np.min(finite))) if finite.size else None
    y_max = _round6(float(np.max(finite))) if finite.size else None

    file_path = channel.signal_file(prefer_filtered=prefer_filtered)
    file_rel = str(Path(file_path).relative_to(raw_root)).replace("\\", "/") if file_path else ""
    used_filtered = bool(prefer_filtered and channel.filt_file is not None)

    duration_s = (n / channel_fs) if channel_fs > 0 else 0.0
    bucket_dt = (stride / channel_fs) if channel_fs > 0 else 0.0

    return {
        "role": channel.role,
        "pcb_id": channel.pcb_id,
        "label": channel.label,
        "file": file_rel,
        "used_filtered": used_filtered,
        "invert_applied": bool(channel.invert_signal),
        "flip_applied": flip_applied,
        "samples": n,
        "duration_s": _round6(duration_s),
        "stride": stride,
        "buckets": buckets,
        "bucket_dt": _round6(bucket_dt),
        "decimated": stride > 1,
        "y_min": y_min,
        "y_max": y_max,
        "min": [_round6(v) for v in mins],
        "max": [_round6(v) for v in maxs],
        "rising": [bool(v) for v in rising],
    }


def build_signal_payload(
    raw_root: str | Path,
    *,
    shot_id: str = "",
    folder: str = "",
    capture: str = "",
    kind: str,
    max_points: int,
    geo_flip_param: bool | None,
    trigger_override: float | None = None,
) -> dict:
    """Arma el payload de ``GET /api/signal``. Ver PORT_PLAN §3.1 / spec §4.

    Se puede pedir por ``shot_id`` (el disparo, como la app) o por
    ``folder``+``capture`` (cualquier captura, sea disparo o no). La segunda
    forma existe porque el estado de una captura no decide si se puede mirar:
    una captura sin martillo, o duplicada, se grafica igual con lo que tenga.

    Orden de operaciones (no se cambia, cambia el resultado):
    1) ``load_signal(apply_invert=True)`` (convención fija: geo no invertido,
       hammer invertido); 2) flip extra sólo al geo; 3) resta de línea de
       base con el mismo ``trigger_idx`` en los dos canales; 4) decimado
       min/max, cada canal con su propio largo; 5) redondeo + no-finitos a
       ``null``.
    """
    if kind not in ("raw", "filt"):
        raise InvalidKind(kind)

    raw_root = Path(raw_root).resolve()
    prefer_filtered = kind == "filt"

    if shot_id:
        shot = _find_shot(raw_root, shot_id)
        hammer_ch, geo_ch = shot.hammer, shot.geo
    elif folder:
        shot, hammer_ch, geo_ch = _shot_for_capture(raw_root, folder, capture)
        shot_id = shot.shot_id if shot is not None else ""
    else:
        raise UnknownShot("hace falta shot_id, o folder + capture")

    ref = hammer_ch or geo_ch
    fs = float((shot.fs if shot is not None else 0.0) or ref.fs or 0.0)

    anns = frd.load_annotations(frd.default_annotations_path(raw_root))
    ann = anns.get(shot_id) if shot_id else None
    # Trigger heredado: los N geófonos de un tendido comparten el golpe. Si este
    # receptor no tiene marca propia pero otro de la misma captura sí, se usa la
    # de la captura (misma línea naranja, mismo tiempo cero).
    ann_captura = None
    if ann is None and shot is not None:
        _propia, ann_captura = capture_annotation(raw_root, anns, shot)

    if ann is not None:
        trigger_s = float(ann.trigger_s)
        trigger_source = "annotation"
        geo_flip_ann = bool(ann.geo_flip)
        reviewed = bool(ann.reviewed)
        accepted = bool(ann.accepted)
    elif ann_captura is not None:
        # El trigger y el tiempo cero se heredan; geo_flip NO: la polaridad es
        # de cada geófono (uno pudo quedar conectado al revés y el otro no).
        trigger_s = float(ann_captura.trigger_s)
        trigger_source = "capture"
        geo_flip_ann = False
        reviewed = False
        accepted = True
    elif shot is not None:
        pick = frd.auto_pick_shot(shot, prefer_filtered=prefer_filtered)
        trigger_s = float(pick.trigger_s)
        trigger_source = "auto"
        geo_flip_ann = False
        reviewed = False
        accepted = True
    else:
        # Sin el par hammer+geo no hay primer arribo que picar (PORT_PLAN §5.4):
        # se dibuja igual, con el eje en 0 y diciendo que no hay trigger.
        trigger_s = 0.0
        trigger_source = "none"
        geo_flip_ann = False
        reviewed = False
        accepted = True

    # El trigger que se está arrastrando en la web: la señal se re-cerca con él
    # (la línea de base depende del trigger), sin escribir nada.
    if trigger_override is not None:
        trigger_s = float(trigger_override)
        trigger_source = "override"

    if geo_flip_param is None:
        geo_flip_effective = geo_flip_ann
        geo_flip_source = "annotation" if ann is not None else "default"
    else:
        geo_flip_effective = bool(geo_flip_param)
        geo_flip_source = "override"

    channels: dict[str, dict] = {}
    if hammer_ch is not None:
        channels["hammer"] = _channel_payload(
            hammer_ch, raw_root=raw_root, prefer_filtered=prefer_filtered,
            invert_extra=False, trigger_s=trigger_s, fs=fs, max_points=max_points,
        )
    if geo_ch is not None:
        channels["geo"] = _channel_payload(
            geo_ch, raw_root=raw_root, prefer_filtered=prefer_filtered,
            invert_extra=geo_flip_effective, trigger_s=trigger_s, fs=fs, max_points=max_points,
        )

    if shot is not None:
        folder_name, capture_name = shot.folder_name, shot.capture_name
        distance_m = _round6(float(shot.distance_m))
    else:
        capture_dir = _capture_dir(raw_root, folder, capture)
        folder_name = folder
        capture_name = capture_dir.name if capture_dir != raw_root / folder else "(raíz)"
        pos = geo_ch.position_m if geo_ch is not None else None
        distance_m = _round6(float(pos)) if pos is not None else None

    return {
        "shot_id": shot_id,
        "folder": folder_name,
        "capture": capture_name,
        "distance_m": distance_m,
        "fs": fs,
        "kind": kind,
        "max_points": int(max_points),
        "trigger_s": _round6(trigger_s),
        "trigger_source": trigger_source,
        "reviewed": reviewed,
        "accepted": accepted,
        "geo_flip": geo_flip_effective,
        "geo_flip_source": geo_flip_source,
        "channels": channels,
    }
