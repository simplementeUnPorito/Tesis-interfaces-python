"""Valida quitar_ruido_sd.py con señal REAL de geófono.

Referencia limpia: trazas de campo de Canchiga (geo1, 2929 Hz, con golpe de
martillo), grabadas sin captura a SD. Sobre cada una se inyecta la firma de
escritura a SD medida en placa el 2026-09-17
(datos/ruido_sd/captura_sd_on_2026-09-17.csv), con sus marcas, como las genera
el PSoC: una escritura cada 150 muestras, la firma empezando 0..2 muestras
después de la marca, amplitud variable (±15 %) y un f_sync cada 32 bloques.

Se comparan, contra la traza original en la banda útil 10-50 Hz:
  - sucia,
  - limpiada con una plantilla sacada de la MISMA captura,
  - limpiada con una plantilla de BANCO: 60 s simulados con el geófono quieto,
    la misma variabilidad de la firma y ruido de banco, medidos con medir_plantilla().

    python validar_ruido_sd_geofono.py [carpeta_campaña] [salida.png]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfiltfilt

import quitar_ruido_sd as q

AQUI = Path(__file__).resolve().parent
CAMPANA = Path(sys.argv[1]) if len(sys.argv) > 1 else AQUI.parents[2] / 'data' / 'raw' / 'Canchiga'
SALIDA = Path(sys.argv[2]) if len(sys.argv) > 2 else AQUI / 'datos' / 'ruido_sd' / 'validacion_geofono.png'
FS_FIRMA = 2604.0
CUENTAS_POR_VOLT = 52429.0
PERIODO = 150
RUIDO_BANCO_V = 0.5e-3


def firmas_medidas():
    """Firma de escritura y la de otro evento (usada como f_sync), en volts."""
    d = np.loadtxt(AQUI / 'datos' / 'ruido_sd' / 'captura_sd_on_2026-09-17.csv',
                   delimiter=',', skiprows=1)
    x = d[d[:, 0] == 2, 1]
    r = q._residuo_alto(x)
    return r[152 - 2:152 + 70] / CUENTAS_POR_VOLT, r[400 - 2:400 + 70] / CUENTAS_POR_VOLT


def a_fs(firma, fs):
    """Remuestrea la firma (medida a 2604 Hz) a la fs de la traza: dura lo mismo en tiempo."""
    t0 = np.arange(len(firma)) / FS_FIRMA
    n = int(round(len(firma) * fs / FS_FIRMA))
    return np.interp(np.arange(n) / fs, t0, firma)


def ensuciar(x, fs, rng, escritura, otra):
    n = len(x)
    fw, fo = a_fs(escritura, fs), a_fs(otra, fs)
    ruido = np.zeros(n)
    marcas = []
    for k, c in enumerate(range(int(rng.integers(0, PERIODO)) + 5, n - len(fo) - 5, PERIODO)):
        sync = (k % 32 == 31)
        if sync:
            marcas.append((c - 1, q.MARCA_SYNC))
        marcas.append((c, q.MARCA_ESCRITURA))
        seg = (1 + rng.normal(0, 0.15)) * (fo if sync else fw)
        ini = c + int(rng.integers(0, 3)) - 2
        ruido[ini:ini + len(seg)] += seg
    return x + ruido, marcas


def trazas(campana):
    for meta_path in sorted((campana / 'captures').glob('*/metadata.json')):
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        for nodo in meta.get('nodes', []):
            if str(nodo.get('type', '')).startswith('Geo') and nodo.get('raw_file') \
                    and int(nodo.get('fs') or 0) == 2929:
                x = np.fromfile(campana / nodo['raw_file'], dtype='<f4').astype(float)
                if len(x) > 3000:
                    yield meta_path.parent.name, float(nodo['fs']), x


def main():
    rng = np.random.default_rng(17)
    escritura, otra = firmas_medidas()
    fs = 2929.0
    banco = rng.normal(0.0, RUIDO_BANCO_V, int(60 * fs))
    banco_sucio, marcas_banco = ensuciar(banco, fs, rng, escritura, otra)
    plantilla_banco = q.medir_plantilla([(banco_sucio, marcas_banco)])

    resultados, ejemplo = [], None
    for nombre, fs_t, x in trazas(CAMPANA):
        banda = butter(4, [10, 50], btype='band', fs=fs_t, output='sos')
        sucia, marcas = ensuciar(x, fs_t, rng, escritura, otra)
        q.MIN_EVENTOS_PLANTILLA, previo = 8, q.MIN_EVENTOS_PLANTILLA
        propia = q.medir_plantilla([(sucia, marcas)])
        q.MIN_EVENTOS_PLANTILLA = previo
        limpia_propia, _ = q.limpiar(sucia, marcas, propia)
        limpia, _ = q.limpiar(sucia, marcas, plantilla_banco)
        ref = sosfiltfilt(banda, x)
        error = lambda y: np.std(sosfiltfilt(banda, y) - ref) / np.std(ref)
        resultados.append((error(sucia), error(limpia_propia), error(limpia)))
        golpe = int(np.argmax(np.abs(ref)))
        if ejemplo is None and 0.3 * fs_t < golpe < len(x) - 0.4 * fs_t:
            ejemplo = (nombre, fs_t, x, sucia, limpia, golpe)

    r = np.array(resultados) * 100
    print(f'{len(r)} trazas reales de geófono ({CAMPANA.name}); error en 10-50 Hz, % del rms de la señal')
    for col, nom in enumerate(('sucia', 'plantilla de la misma captura', 'plantilla de banco')):
        print(f'  {nom:>30}: mediana {np.median(r[:, col]):4.2f}  p90 {np.percentile(r[:, col], 90):4.2f}')
    graficar(ejemplo, r, escritura, plantilla_banco[q.MARCA_ESCRITURA], fs)


def graficar(ejemplo, r, firma, plantilla, fs_banco):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    nombre, fs, x, sucia, limpia, golpe = ejemplo
    AZUL, NARANJA, AQUA, AMARILLO = '#2a78d6', '#eb6834', '#1baf7a', '#eda100'
    TINTA, TINTA2, GRILLA = '#0b0b0b', '#52514e', '#e4e3df'
    plt.rcParams.update({'font.size': 9, 'axes.edgecolor': GRILLA, 'axes.labelcolor': TINTA2,
                         'xtick.color': TINTA2, 'ytick.color': TINTA2, 'axes.titlecolor': TINTA,
                         'figure.facecolor': '#fcfcfb', 'axes.facecolor': '#fcfcfb'})
    banda = butter(4, [10, 50], btype='band', fs=fs, output='sos')
    fig, ax = plt.subplots(2, 2, figsize=(11, 6.8), constrained_layout=True)

    i0, i1 = golpe - int(0.1 * fs), golpe + int(0.3 * fs)
    t = (np.arange(i0, i1) - golpe) / fs * 1000
    a = ax[0, 0]
    a.plot(t, sosfiltfilt(banda, sucia)[i0:i1] * 1000, color=NARANJA, lw=1.2, label='con ruido de SD')
    a.plot(t, sosfiltfilt(banda, x)[i0:i1] * 1000, color=AZUL, lw=1.8, label='original')
    a.plot(t, sosfiltfilt(banda, limpia)[i0:i1] * 1000, color=AQUA, lw=1.2, ls='--', label='limpiada (banco)')
    a.set_title(f'Golpe real ({nombre}) en 10–50 Hz: las tres se superponen')
    a.set_xlabel('ms desde el pico'); a.set_ylabel('mV')
    a.legend(frameon=False, loc='lower right')

    a = ax[0, 1]
    j0, j1 = int(0.05 * fs), int(0.05 * fs) + int(0.3 * fs)
    t2 = np.arange(j0, j1) / fs * 1000
    a.plot(t2, (sucia[j0:j1] - x[j0:j1]) * 1000, color=NARANJA, lw=1.2, label='sucia − original')
    a.plot(t2, (limpia[j0:j1] - x[j0:j1]) * 1000, color=AQUA, lw=1.2, label='limpiada − original')
    a.set_title('Ruido de SD agregado y lo que queda (cruda, banda completa)')
    a.set_xlabel('ms'); a.set_ylabel('mV')
    a.legend(frameon=False, loc='upper right')

    a = ax[1, 0]
    tf = np.arange(len(plantilla)) / fs_banco * 1000
    a.plot(tf, a_fs(firma, fs_banco)[:len(plantilla)] * 1000, color=AZUL, lw=1.8, label='firma medida')
    a.plot(tf, plantilla * 1000, color=AMARILLO, lw=1.2, ls='--', label='plantilla de banco (60 s)')
    a.set_title('Plantilla medida en banco vs firma real')
    a.set_xlabel('ms desde la marca'); a.set_ylabel('mV')
    a.legend(frameon=False, loc='upper right')

    a = ax[1, 1]
    orden = np.argsort(r[:, 0])
    k = np.arange(len(r))
    a.plot(k, r[orden, 1], 'o', ms=3.5, color=AMARILLO, label='plantilla de la misma captura')
    a.plot(k, r[orden, 0], 'o', ms=4, color=NARANJA, label='sucia')
    a.plot(k, r[orden, 2], 'o', ms=4, color=AQUA, label='plantilla de banco')
    a.set_title(f'{len(r)} trazas reales: error en 10–50 Hz')
    a.set_xlabel('trazas, ordenadas por error sucio'); a.set_ylabel('% del rms de la señal')
    a.legend(frameon=False, loc='upper left')

    for a in ax.flat:
        a.grid(True, color=GRILLA, lw=0.6)
        for s in ('top', 'right'):
            a.spines[s].set_visible(False)
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(SALIDA, dpi=150)
    print(f'figura: {SALIDA}')


if __name__ == '__main__':
    main()
