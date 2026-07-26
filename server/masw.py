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
import io
import zlib
from pathlib import Path

import numpy as np

from ._gs import frd  # noqa: F401  (asegura el bootstrap de sys.path)
from .waterfall import matrix_for_masw

# Defaults de los spinboxes de la app (`_build_dispersion_tab`).
DEFAULTS = {
    "c_min": 50.0,
    "c_max": 800.0,
    "c_step": 2.0,
    "f_min": 1.0,
    "f_max": 100.0,
}


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


def build_dispersion(raw_root: str | Path, *, group_id: int = 1,
                     c_min: float = DEFAULTS["c_min"],
                     c_max: float = DEFAULTS["c_max"],
                     c_step: float = DEFAULTS["c_step"],
                     f_min: float = DEFAULTS["f_min"],
                     f_max: float = DEFAULTS["f_max"]) -> dict:
    """Imagen de dispersión del grupo, con lo que el waterfall tenga a la vista."""
    from geophone_scope.masw_dispersion import phase_shift_dispersion_image

    raw_root = Path(raw_root)
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

    norm = _normalizar(A)                       # (n_freq, n_c) en [0, 1]
    # La imagen se manda con la frecuencia en el eje horizontal y la velocidad
    # en el vertical, y con la velocidad creciendo hacia arriba (fila 0 = c_max),
    # que es como se lee un gráfico y como lo muestra la app.
    img = np.flipud((norm.T * 255.0).clip(0, 255))

    return {
        "group_id": int(group_id),
        "n_channels": len(distancias),
        "distances": [round(float(d), 6) for d in distancias],
        "fs": round(float(fs), 6),
        "f_min": round(float(f[0]), 6) if f.size else 0.0,
        "f_max": round(float(f[-1]), 6) if f.size else 0.0,
        "c_min": round(float(c[0]), 6) if c.size else 0.0,
        "c_max": round(float(c[-1]), 6) if c.size else 0.0,
        "width": int(img.shape[1]),
        "height": int(img.shape[0]),
        "image_png": _png_gris(img),
        "params": {"c_min": float(c_min), "c_max": float(c_max), "c_step": float(c_step),
                   "f_min": float(f_min), "f_max": float(f_max)},
    }
