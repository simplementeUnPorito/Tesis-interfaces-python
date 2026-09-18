"""Pruebas de la resta de la firma de SD. Señales sintéticas, sin placa."""
from __future__ import annotations

import unittest

import numpy as np
from scipy.signal import butter, sosfiltfilt

from quitar_ruido_sd import MARCA_ESCRITURA, MARCA_SYNC, limpiar, medir_plantilla

FS = 2604.0
PERIODO = 150    # muestras entre escrituras a SD (5 lotes x 30)
BANDA = butter(4, [10, 50], btype='band', fs=FS, output='sos')


def firma_sd() -> np.ndarray:
    """Firma triple como la medida en placa: golpes a +0, +22 y +41 muestras."""
    f = np.zeros(72)
    for inicio, amp in ((2, 70.0), (24, 115.0), (43, -70.0)):
        k = np.arange(12)
        f[inicio:inicio + 12] += amp * np.exp(-k / 3.0) * np.cos(k * 0.9)
    return f


def escenario(segundos: float, amp_senal: float, ruido: float = 11.0, semilla: int = 3):
    rng = np.random.default_rng(semilla)
    n = int(segundos * FS)
    t = np.arange(n) / FS
    senal = sum(amp_senal / 4 * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.3))
                for f in (11.0, 23.0, 37.0, 47.0))
    fondo = rng.normal(0.0, ruido, n)
    interferencia = np.zeros(n)
    marcas = []
    for k, c in enumerate(range(40, n - 80, PERIODO)):
        if k % 32 == 31:
            marcas.append((c - 1, MARCA_SYNC))
        marcas.append((c, MARCA_ESCRITURA))
        d = int(rng.integers(0, 3))
        interferencia[c + d - 2:c + d + 70] += (1 + rng.normal(0, 0.15)) * firma_sd()
    return senal + fondo + interferencia, senal + fondo, senal, marcas


def error_en_banda(y, referencia, senal) -> float:
    return float(np.std(sosfiltfilt(BANDA, y - referencia)) /
                 np.std(sosfiltfilt(BANDA, senal)))


class PruebaLimpieza(unittest.TestCase):

    def setUp(self):
        banco, _, _, marcas = escenario(60.0, 0.0, semilla=9)
        self.plantillas = medir_plantilla([(banco, marcas)])

    def test_banco_da_plantilla_de_escritura(self):
        self.assertIn(MARCA_ESCRITURA, self.plantillas)
        # El jitter de 0..2 muestras suaviza la oscilación (período ~7 muestras):
        # la plantilla queda en ~45 % del pico a pico de la firma, no en el 100 %.
        self.assertGreater(np.ptp(self.plantillas[MARCA_ESCRITURA]), 0.35 * np.ptp(firma_sd()))

    def test_baja_el_error_en_la_banda_util(self):
        x, ref, senal, marcas = escenario(10.0, 100.0)
        y, restadas = limpiar(x, marcas, self.plantillas)
        antes, despues = error_en_banda(x, ref, senal), error_en_banda(y, ref, senal)
        self.assertGreater(restadas, 100)
        self.assertLess(despues, antes / 2.0, (antes, despues))

    def test_sin_marcas_no_toca_nada(self):
        x, _, _, _ = escenario(2.0, 100.0)
        y, restadas = limpiar(x, [], self.plantillas)
        self.assertEqual(restadas, 0)
        np.testing.assert_array_equal(x, y)

    def test_pocas_escrituras_no_dan_plantilla(self):
        """Con menos de MIN_EVENTOS_PLANTILLA la mediana arrastra el ruido de campo."""
        x, _, _, marcas = escenario(2.0, 100.0)
        self.assertEqual(medir_plantilla([(x, marcas[:20])]), {})


if __name__ == '__main__':
    unittest.main()
