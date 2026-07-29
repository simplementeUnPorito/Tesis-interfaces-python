"""Tab MASW (§3.5), etapa 1: la imagen de dispersión.

Porta la parte de `MaswPanel._build_dispersion_tab` que **calcula**: alimenta
``masw_dispersion.phase_shift_dispersion_image`` (phase-shift, Park et al.
1998) con lo que el waterfall manda —el mismo recorte que se está viendo, no la
matriz completa— y devuelve la imagen frecuencia/velocidad lista para dibujar.

El algoritmo no se reimplementa: es el mismo módulo que usa la app, así que la
web y PyQt dan la misma imagen sobre los mismos datos.

La imagen viaja como **PNG en base64**, no como una matriz de floats: una grilla
típica (200 frecuencias × 150 velocidades) son 30 000 números, y en JSON eso es
casi medio mega por recálculo. Cuantizada a 8 bits pierde 1/255 de contraste,
que es menos de lo que distingue el ojo en un mapa de color, y el navegador la
pinta de una con ``createImageBitmap``.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import math
import zlib
from pathlib import Path
from typing import Any

import numpy as np

from ._gs import frd  # noqa: F401  (asegura el bootstrap de sys.path)
from .state import (
    RevisionConflict,
    composite_revision,
    locked,
    require_composite_revision,
)
from .waterfall import matrix_for_masw

# Defaults de los spinboxes de la app (`_build_dispersion_tab`).
DEFAULTS = {
    "c_min": 50.0,
    "c_max": 800.0,
    "c_step": 2.0,
    "f_min": 1.0,
    "f_max": 100.0,
}

INV_ARRAY_KEYS = (
    "beta", "h", "beta_initial", "h_initial",
    "freqs", "c_obs", "c_t", "alpha", "wavelengths",
)


def state_path(raw_root: str | Path) -> Path:
    return frd.default_masw_state_path(raw_root)


def arrays_path(raw_root: str | Path) -> Path:
    return frd.default_masw_arrays_path(raw_root)


def _finite(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def backend_catalog() -> list[dict]:
    from geophone_scope import masw_backends

    return [
        {
            "key": key,
            "label": label,
            "kind": kind,
            "available": bool(masw_backends.backend_available(key)),
            "status": masw_backends.backend_status(key),
            "can_launch": bool(masw_backends.backend_can_launch(key)),
        }
        for key, label, kind in masw_backends.BACKENDS
    ]


def load_analysis_state(raw_root: str | Path) -> dict:
    """Carga el JSON/NPZ autoritativo sin reescribirlo al abrir la web."""
    raw_root = Path(raw_root)
    path = state_path(raw_root)
    state = frd.load_masw_state(path) or {}
    masw = state.get("masw") if isinstance(state.get("masw"), dict) else {}
    arrays = frd.load_masw_arrays(arrays_path(raw_root))
    result_arrays = {
        key: [
            _finite(value)
            for value in np.asarray(arrays[f"inv_{key}"]).reshape(-1)
        ]
        for key in INV_ARRAY_KEYS
        if f"inv_{key}" in arrays
    }
    return {
        "revision": composite_revision((path, arrays_path(raw_root))),
        "path": str(path),
        "arrays_path": str(arrays_path(raw_root)),
        "masw": masw,
        "result_arrays": result_arrays,
        "array_keys": sorted(arrays),
        "backends": backend_catalog(),
    }


def save_analysis_state(
    raw_root: str | Path, patch: dict, *, base_revision: str = ""
) -> dict:
    """Fusiona sólo las claves MASW recibidas y conserva estado desconocido."""
    raw_root = Path(raw_root)
    path = state_path(raw_root)
    allowed = {
        "image_params", "pick_range", "inversion_params", "active_mode",
        "picks_by_mode", "regions_by_mode", "raw_groups", "group_weights",
        "weight_slider_max", "display_options", "active_data_group",
        "geophone_spacing_m", "array_length_m", "inner_tab", "backend",
        "edited_profile",
    }
    clean = {key: value for key, value in patch.items() if key in allowed}
    if "edited_profile" in clean:
        profile = clean["edited_profile"]
        beta = [float(v) for v in profile.get("beta", [])]
        h = [float(v) for v in profile.get("h", [])]
        if not beta or any(v <= 0 or not math.isfinite(v) for v in beta):
            raise ValueError("edited_profile.beta debe contener Vs positivas")
        if len(h) not in {len(beta), len(beta) - 1}:
            raise ValueError("edited_profile.h no coincide con las capas")
        if any(v <= 0 or not math.isfinite(v) for v in h):
            raise ValueError("edited_profile.h debe contener espesores positivos")
        clean["edited_profile"] = {"beta": beta, "h": h}
    with locked(path):
        require_composite_revision((path, arrays_path(raw_root)), base_revision)
        state = frd.load_masw_state(path) or {}
        masw = state.get("masw") if isinstance(state.get("masw"), dict) else {}
        masw = dict(masw)
        masw.update(clean)
        state["masw"] = masw
        frd.save_masw_state(path, state)
    return load_analysis_state(raw_root)


def _normalizar(A: np.ndarray) -> np.ndarray:
    """Normaliza **por frecuencia**, igual que ``_normalize_dispersion_image``.

    Fila por fila y no con el máximo global: la energía cae fuerte con la
    frecuencia, y con un máximo global las frecuencias altas quedan negras y no
    se ve la rama que hay que picar ahí.
    """
    A = np.asarray(A, dtype=np.float64)
    if A.size == 0:
        return A
    with np.errstate(invalid="ignore", divide="ignore"):
        picos = np.nanmax(np.abs(A), axis=1, keepdims=True)
    picos = np.where(np.isfinite(picos) & (picos > 0), picos, 1.0)
    salida = np.abs(A) / picos
    return np.nan_to_num(salida, nan=0.0, posinf=1.0, neginf=0.0)


def _png_gris(img: np.ndarray) -> str:
    """PNG de 8 bits en escala de grises, en base64. Sin dependencias: el
    formato es corto de escribir y evita arrastrar Pillow al servidor."""
    alto, ancho = img.shape
    datos = img.astype(np.uint8)
    # Cada fila lleva adelante su byte de filtro (0 = ninguno).
    crudo = b"".join(b"\x00" + fila.tobytes() for fila in datos)

    def trozo(tipo: bytes, cuerpo: bytes) -> bytes:
        return (len(cuerpo).to_bytes(4, "big") + tipo + cuerpo
                + (zlib.crc32(tipo + cuerpo) & 0xFFFFFFFF).to_bytes(4, "big"))

    cabecera = (ancho.to_bytes(4, "big") + alto.to_bytes(4, "big")
                + bytes([8, 0, 0, 0, 0]))          # 8 bits, greyscale
    buf = io.BytesIO()
    buf.write(b"\x89PNG\r\n\x1a\n")
    buf.write(trozo(b"IHDR", cabecera))
    buf.write(trozo(b"IDAT", zlib.compress(crudo, 6)))
    buf.write(trozo(b"IEND", b""))
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _dispersion_group(
    raw_root: Path,
    group_id: int,
    *,
    c_min: float,
    c_max: float,
    c_step: float,
    f_min: float,
    f_max: float,
) -> dict:
    from geophone_scope.masw_dispersion import phase_shift_dispersion_image

    tiempo, distancias, matriz = matrix_for_masw(raw_root, group_id=group_id)

    if len(distancias) < 3:
        raise ValueError(
            f"Hacen falta al menos 3 receptores para una imagen de dispersión "
            f"y hay {len(distancias)}. Revisá el recorte de la pestaña Waterfall.")

    # (n_time, n_channels): el phase-shift espera los canales en columnas.
    u = np.asarray(matriz, dtype=np.float64).T
    # Los NaN de las colas (capturas más cortas que la ventana común) no pueden
    # entrar al slant-stack: valen 0, que es "no aporta", no "hay señal nula".
    u = np.nan_to_num(u, nan=0.0, posinf=0.0, neginf=0.0)
    fs = 1.0 / float(np.median(np.diff(tiempo)))

    f, c, A = phase_shift_dispersion_image(
        u, np.asarray(distancias, dtype=np.float64), fs,
        c_min=float(c_min), c_max=float(c_max), c_step=float(c_step),
        f_max=float(f_max), f_min=float(f_min),
    )

    return {
        "group_id": int(group_id),
        "time": np.asarray(tiempo, dtype=np.float64),
        "distances": np.asarray(distancias, dtype=np.float64),
        "matrix": np.asarray(matriz, dtype=np.float64),
        "fs": fs,
        "f": np.asarray(f, dtype=np.float64),
        "c": np.asarray(c, dtype=np.float64),
        "A_norm": _normalizar(A),
    }


def _array_geometry(distances: np.ndarray) -> tuple[float | None, float | None]:
    unique = np.unique(np.asarray(distances, dtype=np.float64))
    if unique.size < 2:
        return None, None
    diffs = np.diff(unique)
    positive = diffs[diffs > 0]
    spacing = float(np.median(positive)) if positive.size else None
    length = float(unique[-1] - unique[0])
    return spacing, length if length > 0 else None


def build_dispersion(raw_root: str | Path, *, group_id: int = 1,
                     c_min: float = DEFAULTS["c_min"],
                     c_max: float = DEFAULTS["c_max"],
                     c_step: float = DEFAULTS["c_step"],
                     f_min: float = DEFAULTS["f_min"],
                     f_max: float = DEFAULTS["f_max"],
                     group_weights: dict[int, float] | None = None,
                     intensity_log: bool = False,
                     intensity_per_freq: bool = True,
                     persist_groups: bool = False,
                     base_revision: str = "") -> dict:
    """Imagen ponderada de uno o varios grupos, compatible con PyQt."""
    raw_root = Path(raw_root)
    weights = group_weights or {int(group_id): 1.0}
    weights = {
        max(1, int(gid)): max(0.0, float(weight))
        for gid, weight in weights.items()
        if float(weight) > 0
    }
    if not weights:
        raise ValueError("hace falta al menos un grupo con peso positivo")
    results = [
        _dispersion_group(
            raw_root,
            gid,
            c_min=c_min,
            c_max=c_max,
            c_step=c_step,
            f_min=f_min,
            f_max=f_max,
        )
        for gid in sorted(weights)
    ]
    first = results[0]
    f, c = first["f"], first["c"]
    combined = np.zeros((f.size, c.size), dtype=np.float64)
    for result in results:
        if result["A_norm"].shape != combined.shape:
            raise ValueError("los grupos produjeron grillas de dispersión incompatibles")
        combined += weights[result["group_id"]] * result["A_norm"]
    display = np.abs(combined)
    if intensity_per_freq:
        display = _normalizar(display)
    else:
        peak = float(np.nanmax(display)) if display.size else 1.0
        display = display / (peak if peak > 0 else 1.0)
    if intensity_log:
        display = np.log1p(99.0 * display) / np.log(100.0)

    norm = np.nan_to_num(display, nan=0.0, posinf=1.0, neginf=0.0)
    # La imagen se manda con la frecuencia en el eje horizontal y la velocidad
    # en el vertical, y con la velocidad creciendo hacia arriba (fila 0 = c_max),
    # que es como se lee un gráfico y como lo muestra la app.
    img = np.flipud((norm.T * 255.0).clip(0, 255))

    geometries = [_array_geometry(result["distances"]) for result in results]
    spacings = [spacing for spacing, _length in geometries if spacing]
    lengths = [length for _spacing, length in geometries if length]
    # Igual que MaswPanel._update_combined_geometry: para una combinación se
    # muestran límites conservadores, el peor dx (mayor aliasing espacial) y
    # la menor apertura L. La unión de offsets puede inventar un dx menor que
    # el de cualquier tendido y habilitar picks físicamente inválidos.
    spacing = max(spacings) if spacings else None
    length = min(lengths) if lengths else None

    if persist_groups:
        _persist_raw_groups(
            raw_root,
            results,
            weights,
            spacing,
            length,
            base_revision=base_revision,
        )

    return {
        "group_id": int(group_id),
        "groups": [
            {
                "group_id": r["group_id"],
                "weight": weights[r["group_id"]],
                "n_channels": int(r["distances"].size),
                "distances": [round(float(d), 6) for d in r["distances"]],
                "fs": round(float(r["fs"]), 6),
            }
            for r in results
        ],
        "group_weights": {str(k): v for k, v in weights.items()},
        "n_channels": int(first["distances"].size),
        "distances": [round(float(d), 6) for d in first["distances"]],
        "fs": round(float(first["fs"]), 6),
        "f_min": round(float(f[0]), 6) if f.size else 0.0,
        "f_max": round(float(f[-1]), 6) if f.size else 0.0,
        "c_min": round(float(c[0]), 6) if c.size else 0.0,
        "c_max": round(float(c[-1]), 6) if c.size else 0.0,
        "width": int(img.shape[1]),
        "height": int(img.shape[0]),
        "image_png": _png_gris(img),
        "geophone_spacing_m": _finite(spacing),
        "array_length_m": _finite(length),
        "alias_boundary": (
            [[round(float(freq), 6), round(float(2.0 * spacing * freq), 6)]
             for freq in f]
            if spacing
            else []
        ),
        "lambda_boundary": (
            [[round(float(freq), 6), round(float(length * freq), 6)]
             for freq in f]
            if length
            else []
        ),
        "params": {"c_min": float(c_min), "c_max": float(c_max), "c_step": float(c_step),
                   "f_min": float(f_min), "f_max": float(f_max)},
    }


def _persist_raw_groups(
    raw_root: Path,
    results: list[dict],
    weights: dict[int, float],
    spacing: float | None,
    length: float | None,
    *,
    base_revision: str,
) -> None:
    """Guarda las matrices con las mismas claves NPZ que restaura PyQt."""
    path = state_path(raw_root)
    apath = arrays_path(raw_root)
    with locked(path):
        require_composite_revision((path, apath), base_revision)
        state = frd.load_masw_state(path) or {}
        masw = state.get("masw") if isinstance(state.get("masw"), dict) else {}
        masw = dict(masw)
        raw_groups = dict(masw.get("raw_groups") or {})
        for result in results:
            gid = int(result["group_id"])
            group_spacing, group_length = _array_geometry(result["distances"])
            raw_groups[str(gid)] = {
                "name": f"Grupo {gid}",
                "spacing": _finite(group_spacing),
                "length": _finite(group_length),
            }
        masw.update({
            "raw_groups": raw_groups,
            "group_weights": {str(k): float(v) for k, v in weights.items()},
            "active_data_group": int(results[0]["group_id"]),
            "geophone_spacing_m": _finite(spacing),
            "array_length_m": _finite(length),
            "has_data": True,
        })
        state["masw"] = masw
        arrays = frd.load_masw_arrays(apath)
        ids = set(int(v) for v in np.asarray(arrays.get("masw_group_ids", [])))
        for result in results:
            gid = int(result["group_id"])
            ids.add(gid)
            prefix = f"masw_g{gid}"
            arrays[f"{prefix}_time"] = result["time"]
            arrays[f"{prefix}_distances"] = result["distances"]
            arrays[f"{prefix}_matrix"] = result["matrix"]
        arrays["masw_group_ids"] = np.asarray(sorted(ids), dtype=np.int32)
        first = results[0]
        arrays["masw_time"] = first["time"]
        arrays["masw_distances"] = first["distances"]
        arrays["masw_matrix"] = first["matrix"]
        frd.save_masw_arrays(apath, arrays)
        frd.save_masw_state(path, state)


def _point_in_polygon(x: float, y: float, polygon: list[list[float]]) -> bool:
    inside = False
    if len(polygon) < 3:
        return False
    j = len(polygon) - 1
    for i, point in enumerate(polygon):
        xi, yi = float(point[0]), float(point[1])
        xj, yj = float(polygon[j][0]), float(polygon[j][1])
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        ):
            inside = not inside
        j = i
    return inside


def auto_pick_dispersion(
    raw_root: str | Path,
    *,
    group_weights: dict[int, float],
    regions_by_mode: dict[str, list],
    pick_fmin: float,
    pick_fmax: float,
    c_min: float,
    c_max: float,
    c_step: float,
    f_min: float,
    f_max: float,
) -> dict[str, list[list[float]]]:
    """Argmax por frecuencia dentro de regiones y límites físicos."""
    raw_root = Path(raw_root)
    results = [
        _dispersion_group(
            raw_root,
            gid,
            c_min=c_min,
            c_max=c_max,
            c_step=c_step,
            f_min=f_min,
            f_max=f_max,
        )
        for gid in sorted(group_weights)
        if group_weights[gid] > 0
    ]
    if not results:
        raise ValueError("sin grupos activos")
    f, c = results[0]["f"], results[0]["c"]
    intensity = sum(
        float(group_weights[result["group_id"]]) * result["A_norm"]
        for result in results
    )
    geometries = [_array_geometry(result["distances"]) for result in results]
    spacings = [value for value, _length in geometries if value]
    lengths = [value for _spacing, value in geometries if value]
    spacing = max(spacings) if spacings else 0.0
    length = min(lengths) if lengths else 0.0
    picked: dict[str, list[list[float]]] = {}
    for mode_text, polygons in regions_by_mode.items():
        mode_picks: list[list[float]] = []
        valid_polys = [p for p in polygons if isinstance(p, list) and len(p) >= 3]
        for fi, freq in enumerate(f):
            if freq < pick_fmin or freq > pick_fmax:
                continue
            valid = np.ones(c.size, dtype=bool)
            if spacing > 0:
                valid &= c >= (2.0 * spacing * freq)
            if length > 0:
                valid &= c <= (length * freq)
            if valid_polys:
                valid &= np.asarray(
                    [
                        any(_point_in_polygon(float(freq), float(vel), p)
                            for p in valid_polys)
                        for vel in c
                    ],
                    dtype=bool,
                )
            if not np.any(valid):
                continue
            indices = np.flatnonzero(valid)
            ci = indices[int(np.argmax(intensity[fi, indices]))]
            mode_picks.append([round(float(freq), 6), round(float(c[ci]), 6)])
        if mode_picks:
            picked[str(int(mode_text))] = mode_picks
    return picked


def _curves_from_payload(payload: dict) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    curves: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for mode, points in (payload.get("curves_by_mode") or {}).items():
        values = np.asarray(points, dtype=np.float64)
        if values.ndim != 2 or values.shape[1] != 2 or values.shape[0] < 3:
            continue
        order = np.argsort(values[:, 0])
        curves[int(mode)] = (values[order, 0], values[order, 1])
    if not curves:
        raise ValueError("se necesitan al menos 3 picks en algún modo")
    return curves


def _profile_arrays(
    result: dict, curves: dict[int, tuple[np.ndarray, np.ndarray]], nu: float
) -> dict[str, np.ndarray]:
    beta = np.asarray(result.get("beta", []), dtype=np.float64)
    h = np.asarray(result.get("h", []), dtype=np.float64)
    mode = sorted(curves)[0]
    freqs, c_obs = curves[mode]
    theoretical = result.get("theoretical") or {}
    tf, tc = theoretical.get(mode, theoretical.get(str(mode), ([], [])))
    tf, tc = np.asarray(tf, dtype=np.float64), np.asarray(tc, dtype=np.float64)
    c_t = (
        np.interp(freqs, tf, tc, left=np.nan, right=np.nan)
        if tf.size >= 2 and tc.size == tf.size
        else np.full(freqs.shape, np.nan)
    )
    vp_factor = math.sqrt(max(1e-9, 2.0 * (1.0 - nu) / (1.0 - 2.0 * nu)))
    return {
        "beta": beta,
        "h": h,
        "beta_initial": np.asarray(result.get("beta_initial", beta), dtype=np.float64),
        "h_initial": np.asarray(result.get("h_initial", h), dtype=np.float64),
        "freqs": freqs,
        "c_obs": c_obs,
        "c_t": c_t,
        "alpha": np.asarray(result.get("alpha", beta * vp_factor), dtype=np.float64),
        "wavelengths": np.divide(
            c_obs, freqs, out=np.full(c_obs.shape, np.nan), where=freqs != 0
        ),
    }


def _store_inversion_result(
    raw_root: Path,
    result: dict,
    curves: dict[int, tuple[np.ndarray, np.ndarray]],
    *,
    backend: str,
    params: dict,
    base_revision: str,
) -> dict[str, np.ndarray]:
    path, apath = state_path(raw_root), arrays_path(raw_root)
    nu = float(params.get("nu", 0.35) or 0.35)
    inv_arrays = _profile_arrays(result, curves, nu)
    with locked(path):
        require_composite_revision((path, apath), base_revision)
        state = frd.load_masw_state(path) or {}
        masw = state.get("masw") if isinstance(state.get("masw"), dict) else {}
        masw = dict(masw)
        masw.update({
            "backend": backend,
            "has_inv_result": True,
            "inv_scalars": {
                "misfit": _finite(result.get("misfit")),
                "nu": nu,
                "engine": str(result.get("engine") or backend),
            },
            "inversion_provenance": {
                "backend": backend,
                "params": params,
                "modes": sorted(curves),
            },
            "edited_profile": {
                "beta": [float(v) for v in inv_arrays["beta"]],
                "h": [float(v) for v in inv_arrays["h"]],
            },
        })
        state["masw"] = masw
        arrays = frd.load_masw_arrays(apath)
        arrays.update({f"inv_{k}": v for k, v in inv_arrays.items()})
        frd.save_masw_arrays(apath, arrays)
        frd.save_masw_state(path, state)
    return inv_arrays


def run_inversion_payload(payload: dict, artifact_dir: Path) -> dict:
    """Entrada del proceso hijo de inversión y exportación Geopsy."""
    from geophone_scope import masw_backends

    raw_root = Path(payload["raw_root"]).resolve()
    backend = str(payload.get("backend") or "maswavespy")
    curves = _curves_from_payload(payload)
    params = dict(payload.get("params") or {})
    artifact_dir.mkdir(parents=True, exist_ok=True)

    if masw_backends.backend_kind(backend) == "export":
        files = masw_backends.export_curves(backend, curves, artifact_dir)
        unique_files = []
        for path in files:
            unique = artifact_dir / f"{artifact_dir.name}_{path.name}"
            path.replace(unique)
            unique_files.append(unique)
        launched = False
        launch_message = ""
        if bool(payload.get("launch")):
            import os

            if os.name != "nt":
                launch_message = (
                    "El lanzamiento automático sólo está habilitado en Windows; "
                    "los archivos quedaron exportados."
                )
            elif not masw_backends.backend_can_launch(backend):
                launch_message = (
                    "Geopsy no está instalado; los archivos quedaron exportados."
                )
            else:
                launched, launch_message = masw_backends.launch_tool(
                    backend, unique_files
                )
        return {
            "backend": backend,
            "export_only": True,
            "launched": launched,
            "launch_message": launch_message,
            "artifacts": [
                {"id": path.name, "name": path.name, "media_type": "text/plain"}
                for path in unique_files
            ],
        }
    if not masw_backends.backend_available(backend):
        raise RuntimeError(masw_backends.backend_status(backend))
    result = masw_backends.run_inversion(backend, curves, **params)
    nu = float(params.get("nu", 0.35) or 0.35)
    inv_arrays = _profile_arrays(result, curves, nu)

    profile_path = artifact_dir / f"profile_{artifact_dir.name}.csv"
    with profile_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["layer", "vs_m_s", "thickness_m"])
        beta, h = inv_arrays["beta"], inv_arrays["h"]
        for index, vs in enumerate(beta):
            writer.writerow([
                index + 1,
                f"{float(vs):.8g}",
                f"{float(h[index]):.8g}" if index < h.size else "",
            ])

    curves_path = artifact_dir / f"curves_{artifact_dir.name}.csv"
    with curves_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["mode", "frequency_hz", "observed_m_s", "theoretical_m_s"])
        theoretical = result.get("theoretical") or {}
        for mode, (freq, observed) in sorted(curves.items()):
            tf, tc = theoretical.get(mode, theoretical.get(str(mode), ([], [])))
            tf, tc = np.asarray(tf), np.asarray(tc)
            calculated = (
                np.interp(freq, tf, tc, left=np.nan, right=np.nan)
                if tf.size >= 2
                else np.full(freq.shape, np.nan)
            )
            for f_value, obs, theory in zip(freq, observed, calculated):
                writer.writerow([
                    mode,
                    f"{float(f_value):.8g}",
                    f"{float(obs):.8g}",
                    "" if not np.isfinite(theory) else f"{float(theory):.8g}",
                ])

    persisted = True
    persistence_error = ""
    try:
        _store_inversion_result(
            raw_root,
            result,
            curves,
            backend=backend,
            params=params,
            base_revision=str(payload.get("base_revision", "")),
        )
    except RevisionConflict as exc:
        # El cálculo puede durar minutos. Si PyQt o la web editaron el estado
        # mientras corría, no se mezcla silenciosamente sobre esa revisión,
        # pero tampoco se tira el resultado: los artefactos ya escritos quedan
        # disponibles para inspección/importación manual.
        persisted = False
        persistence_error = str(exc)

    config_path = artifact_dir / f"inversion_{artifact_dir.name}.json"
    config_path.write_text(
        json.dumps({
            "backend": backend,
            "engine": result.get("engine"),
            "misfit": _finite(result.get("misfit")),
            "modes": sorted(curves),
            "params": params,
            "base_revision": str(payload.get("base_revision", "")),
            "persisted": persisted,
            "persistence_error": persistence_error,
            "profile": {
                "beta": [float(v) for v in inv_arrays["beta"]],
                "h": [float(v) for v in inv_arrays["h"]],
            },
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "backend": backend,
        "engine": str(result.get("engine") or backend),
        "misfit": _finite(result.get("misfit")),
        "modes": sorted(curves),
        "persisted": persisted,
        "persistence_error": persistence_error,
        "profile": {
            "beta": [float(v) for v in inv_arrays["beta"]],
            "h": [float(v) for v in inv_arrays["h"]],
        },
        "artifacts": [
            {"id": path.name, "name": path.name, "media_type": media}
            for path, media in (
                (profile_path, "text/csv"),
                (curves_path, "text/csv"),
                (config_path, "application/json"),
            )
        ],
    }
