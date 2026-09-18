"""Resta la interferencia de las escrituras a SD del PSoC usando sus marcas.

Por qué existe: cuando una captura no entra en la RAM del PSoC (más de 512
lotes decimados), el PSoC la vuelca a la SD mientras muestrea. Cada escritura
de un sector (cada 150 muestras) y cada f_sync mete una espiga en la cadena
analógica. No se corta el ADC ni se pierden muestras para evitarlo: el PSoC
marca la primera muestra guardada después de empezar cada escritura
(bit 0 = escritura, bit 1 = f_sync).

Dos pasos, porque la plantilla NO se puede sacar de la captura de campo:

1. `medir`: una vez, en banco, con el geófono quieto y la SD activa. Con
   cientos de escrituras la mediana alineada en las marcas da la firma limpia.
2. `limpiar`: en cada captura se resta esa plantilla fija en cada marca.

Validación con 63 trazas reales de Canchiga (2026-09-17, ver
validar_ruido_sd_geofono.py): el ruido de la SD suma ~0,7 % del rms de la
señal en 10-50 Hz. Estimar la plantilla de la propia captura NO sirve: el ruido
de campo (~4 mV de banda ancha) es mayor que la firma (~4 mV pico a pico) y la
resta empeora el resultado (0,7 % -> 2,3 %). Con plantilla medida en banco baja
a ~0,2-0,4 %.

Uso:
    python quitar_ruido_sd.py medir <export_banco> plantilla_sd.npz
    python quitar_ruido_sd.py limpiar <export_campo> plantilla_sd.npz
`limpiar` escribe raw_limpio_f32le.bin al lado de cada raw_f32le.bin con marcas.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import median_filter

MARCA_ESCRITURA = 0x01
MARCA_SYNC = 0x02
TIPOS = {MARCA_ESCRITURA: 'escritura', MARCA_SYNC: 'sync'}
MIN_EVENTOS_PLANTILLA = 100   # una plantilla con menos arrastra demasiado ruido
PRE = 2                       # muestras antes de la marca
POST = 70                     # cubre la firma triple (~16 ms a 2604 Hz)
TENDENCIA = 401               # mediana lenta que se saca antes de promediar


def _residuo_alto(x: np.ndarray, ancho: int = 9) -> np.ndarray:
    """x menos su mediana corta: deja pasar espigas, no la banda 10-50 Hz."""
    return x - median_filter(x, size=ancho, mode='nearest')


def _centros(marcas, tipo: int, n: int, pre: int, post: int) -> list[int]:
    return [int(i) for i, f in marcas
            if (int(f) & tipo) and pre <= int(i) and int(i) + post <= n]


def medir_plantilla(capturas, pre: int = PRE, post: int = POST) -> dict:
    """capturas: iterable de (muestras, marcas) de banco. Devuelve {tipo: plantilla}."""
    segmentos = {tipo: [] for tipo in TIPOS}
    for x, marcas in capturas:
        x = np.asarray(x, dtype=float)
        xd = x - median_filter(x, size=min(TENDENCIA, len(x) | 1), mode='nearest')
        for tipo in TIPOS:
            segmentos[tipo] += [xd[c - pre:c + post] for c in _centros(marcas, tipo, len(x), pre, post)]
    plantillas = {}
    for tipo, segs in segmentos.items():
        if len(segs) >= MIN_EVENTOS_PLANTILLA:
            plantillas[tipo] = np.median(np.stack(segs), axis=0)
    return plantillas


def limpiar(x, marcas, plantillas: dict, pre: int = PRE):
    """Resta la plantilla fija de cada tipo en cada marca. Devuelve (limpia, cuántas)."""
    y = np.asarray(x, dtype=float).copy()
    n = len(y)
    restadas = 0
    for tipo, plantilla in plantillas.items():
        post = len(plantilla) - pre
        for c in _centros(marcas, tipo, n, pre, post):
            y[c - pre:c + post] -= plantilla
            restadas += 1
    return y, restadas


def _nodos_con_marcas(carpeta: Path):
    for meta_path in sorted(carpeta.rglob('metadata.json')):
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        for nodo in meta.get('nodes', []):
            raw_file, marcas = nodo.get('raw_file'), nodo.get('sd_marks') or []
            if raw_file and marcas and (carpeta / raw_file).exists():
                yield carpeta / raw_file, marcas


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] not in ('medir', 'limpiar'):
        print(__doc__)
        return 1
    carpeta, archivo = Path(argv[2]), Path(argv[3])
    if argv[1] == 'medir':
        capturas = [(np.fromfile(p, dtype='<f4'), m) for p, m in _nodos_con_marcas(carpeta)]
        plantillas = medir_plantilla(capturas)
        if not plantillas:
            print(f'menos de {MIN_EVENTOS_PLANTILLA} marcas por tipo: capturá más tiempo con SD')
            return 1
        np.savez(archivo, **{TIPOS[t]: p for t, p in plantillas.items()})
        print(f'plantillas {[TIPOS[t] for t in plantillas]} -> {archivo}')
        return 0
    guardadas = np.load(archivo)
    plantillas = {t: guardadas[nombre] for t, nombre in TIPOS.items() if nombre in guardadas}
    for raw_path, marcas in _nodos_con_marcas(carpeta):
        y, restadas = limpiar(np.fromfile(raw_path, dtype='<f4'), marcas, plantillas)
        destino = raw_path.with_name('raw_limpio_f32le.bin')
        y.astype('<f4').tofile(destino)
        print(f'{raw_path}: {restadas} eventos restados -> {destino.name}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
