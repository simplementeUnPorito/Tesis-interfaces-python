"""Demo interactivo de la pestaña MASW con datos ya cargados.

Levanta SOLO el panel MASW (sin el resto de la app de revision) con un gather
sintetico que tiene DOS crestas (dos velocidades aparentes) para poder probar el
flujo multi-modo y todos los motores de inversion sin necesitar el dataset de
campo. Uso:

    python masw_demo.py            # gather sintetico (2 modos)
    python masw_demo.py --npz X    # carga un .npz con common_time, distances, matrix

El .npz debe tener arrays 'common_time' (n_t,), 'distances' (n_ch,) y
'matrix' (n_ch, n_t). Es el mismo formato que guarda la app en
field_review_masw_state.npz bajo las claves wf_common_time / wf_distances /
wf_matrix (o masw_time / masw_distances / masw_matrix).

Una vez abierto:
  1. 'Calcular imagen'.
  2. 'Iniciar región' -> clickear un poligono alrededor de una cresta -> 'Cerrar región'.
     '+ Agregar modo', otra region sobre la otra cresta, 'Cerrar región'.
  3. 'Auto-pick' -> arma una curva por modo.
  4. Pestaña '2. Inversion' -> elegi el 'Motor de inversión' y 'Correr inversión'.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np


def synthetic_gather() -> tuple[np.ndarray, list[float], np.ndarray]:
    """Gather (n_ch, n_t) con dos moveouts (v1 y v2) -> dos crestas en la
    imagen de dispersion, para probar el picking multi-modo."""
    fs = 1000.0
    t = np.arange(0.0, 0.6, 1.0 / fs)
    distances = [4.0 * (i + 1) for i in range(12)]  # 4..48 m, dx=4, L=44
    v1, v2 = 180.0, 340.0
    rng = np.random.default_rng(0)
    mat = np.zeros((len(distances), t.size))
    for i, d in enumerate(distances):
        s1 = np.exp(-((t - d / v1) / 0.022) ** 2) * np.sin(2 * np.pi * 20 * (t - d / v1))
        s2 = 0.6 * np.exp(-((t - d / v2) / 0.018) ** 2) * np.sin(2 * np.pi * 34 * (t - d / v2))
        mat[i] = s1 + s2 + 0.02 * rng.standard_normal(t.size)
    return t, distances, mat


def load_npz(path: str) -> tuple[np.ndarray, list[float], np.ndarray]:
    with np.load(path) as data:
        keys = set(data.files)

        def pick(*names):
            for n in names:
                if n in keys:
                    return data[n]
            raise KeyError(f"Falta alguno de {names} en {path} (tiene {sorted(keys)})")

        t = np.asarray(pick("common_time", "wf_common_time", "masw_time"), dtype=float)
        distances = [float(x) for x in pick("distances", "wf_distances", "masw_distances")]
        mat = np.asarray(pick("matrix", "wf_matrix", "masw_matrix"), dtype=float)
    return t, distances, mat


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--npz", default=None, help="Ruta a un .npz con common_time/distances/matrix")
    args = ap.parse_args()

    from PyQt6.QtWidgets import QApplication

    try:
        from .field_review_app import MaswPanel
    except ImportError:
        from field_review_app import MaswPanel

    t, distances, mat = load_npz(args.npz) if args.npz else synthetic_gather()

    app = QApplication(sys.argv)
    app.setApplicationName("MASW demo")
    panel = MaswPanel(dark_mode=False)
    panel.resize(1200, 820)
    panel.set_data(t, distances, mat)
    panel._calculate()
    panel.setWindowTitle("MASW demo — probar motores de inversion (datos cargados)")
    panel.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
