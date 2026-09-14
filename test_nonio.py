"""Pruebas del nonio de dos actuadores. Aritmetica pura, sin placa."""
from __future__ import annotations

import unittest

from nonio import buscar_par, resolucion_efectiva

#: Los numeros medidos el 2026-09-13, referidos a LPo.
K1_CH2 = 8.3       # IDAC1 -> ch2, mV/codigo
K2_CH2 = 12.2      # IDAC2 -> ch2, mV/codigo
GANANCIA_LP = -17.95


def pendientes(pgaout: int) -> tuple[float, float]:
    """mV de LPo por codigo de IDAC1 y de IDAC2, a esa ganancia de PGAout."""
    return (K1_CH2 * pgaout * GANANCIA_LP, K2_CH2 * pgaout * GANANCIA_LP)


class PruebaResolucion(unittest.TestCase):

    def test_el_par_es_mucho_mas_fino_que_cualquiera_solo(self):
        """La razon de ser del nonio: la diferencia bate a los dos pasos."""
        for pgaout in (2, 4, 8, 16, 24):
            k1, k2 = pendientes(pgaout)
            fina = resolucion_efectiva(k1, k2)
            self.assertLess(fina, abs(k2) / 10.0,
                            f"a x{pgaout} el par no gana nada")

    def test_x16_entra_en_el_recorrido_del_vernier(self):
        """A x16 el grueso tiene que dejar el error bajo los 1703 mV de IDAC3.

        Es la condicion que decide si x16 es alcanzable, y hasta hoy no lo era:
        con un solo actuador grueso el error de cuantizacion llega a 1752 mV.
        """
        k1, k2 = pendientes(16)
        self.assertGreater(abs(k2) / 2.0, 1703.0,
                           "con IDAC2 solo, x16 deberia NO entrar")
        self.assertLess(resolucion_efectiva(k1, k2) / 2.0, 1703.0)

    def test_x24_tambien(self):
        k1, k2 = pendientes(24)
        self.assertLess(resolucion_efectiva(k1, k2) / 2.0, 1703.0)


class PruebaBusqueda(unittest.TestCase):

    def test_corrige_de_verdad(self):
        k1, k2 = pendientes(16)
        for error in (-5000.0, -1200.0, -300.0, 250.0, 900.0, 4000.0):
            par = buscar_par(error, k1, k2, 0, 0, objetivo_mv=200.0)
            self.assertIsNotNone(par, f"no encontro par para {error}")
            self.assertLess(abs(par.error_previsto), abs(error))

    def test_prefiere_no_mover_el_lento(self):
        """Entre dos soluciones parecidas gana la que no espera 44 s."""
        k1, k2 = pendientes(2)
        # Un error que es casi exactamente un codigo del rapido.
        par = buscar_par(-k2, k1, k2, 0, 0, objetivo_mv=200.0)
        self.assertIsNotNone(par)
        self.assertEqual(par.d_lento, 0)
        self.assertEqual(par.d_rapido, -1)

    def test_respeta_los_topes(self):
        k1, k2 = pendientes(4)
        par = buscar_par(-90000.0, k1, k2, 255, 255, objetivo_mv=200.0)
        if par is not None:
            self.assertLessEqual(255 + par.d_lento, 255)
            self.assertLessEqual(255 + par.d_rapido, 255)

    def test_no_se_mueve_si_ya_esta_bien(self):
        """Con el error muy por debajo del objetivo no vale gastar pasos."""
        k1, k2 = pendientes(8)
        self.assertIsNone(buscar_par(5.0, k1, k2, 0, 0, objetivo_mv=200.0))

    def test_sin_objetivo_busca_el_minimo_error(self):
        k1, k2 = pendientes(16)
        par = buscar_par(-2000.0, k1, k2, 0, 0)
        self.assertIsNotNone(par)
        self.assertLess(abs(par.error_previsto), 200.0)

    def test_pendiente_nula_no_rompe(self):
        self.assertIsNone(buscar_par(100.0, 0.0, 0.0, 0, 0))


class PruebaConvergencia(unittest.TestCase):
    """El lazo completo, simulado: nonio grueso y despues IDAC3 de vernier."""

    def simular(self, pgaout: int, error_inicial: float,
                tolerancia: float = 200.0) -> tuple[bool, int]:
        k1, k2 = pendientes(pgaout)
        c1 = c2 = 0
        error = error_inicial
        pasos = 0
        for _ in range(8):
            if abs(error) <= 1703.0:
                break
            par = buscar_par(error, k1, k2, c1, c2, objetivo_mv=1000.0)
            if par is None:
                break
            c1 += par.d_lento
            c2 += par.d_rapido
            error = par.error_previsto
            pasos += 1
        # IDAC3: 6,68 mV por codigo, 255 codigos de recorrido.
        c3 = max(-255, min(255, int(round(error / 6.68))))
        error -= c3 * 6.68
        return abs(error) <= tolerancia, pasos

    def test_converge_en_todas_las_ganancias(self):
        for pgaout in (2, 4, 8, 16, 24):
            for inicial in (-6000.0, -1500.0, 800.0, 3000.0, 9000.0):
                bien, pasos = self.simular(pgaout, inicial)
                self.assertTrue(
                    bien, f"x{pgaout} no converge desde {inicial:+.0f} mV")
                self.assertLessEqual(pasos, 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
