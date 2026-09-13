"""Pruebas del perfil de placa: vigencia, escalado y persistencia."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from perfil_placa import PerfilPlaca, resumen


def _perfil() -> PerfilPlaca:
    return PerfilPlaca(
        id_placa="geo-01",
        fecha="2026-09-13",
        pendientes={
            "IDAC3->ch4@x1": 6.684,
            "IDAC2->ch2@x4": 12.06,
            "IDAC2->ch4@x1": 48.0,
        },
        tau_bp_s=28.5,
        vdda_mv=4826.0,
        puntos={"x4": [16, -41, -118, 12]},
        notas="Vref_LP con 10 kOhm",
    )


class PruebaVigencia(unittest.TestCase):

    def test_recien_medido_esta_vigente(self):
        self.assertTrue(_perfil().vigente(hoy=date(2026, 9, 13)))

    def test_vence_pasados_los_noventa_dias(self):
        self.assertFalse(_perfil().vigente(hoy=date(2026, 12, 20)))

    def test_una_fecha_futura_no_cuenta_como_vigente(self):
        """Un reloj mal puesto no debe hacer pasar un perfil por bueno."""
        self.assertFalse(_perfil().vigente(hoy=date(2026, 9, 1)))

    def test_los_dias_se_cuentan_bien(self):
        self.assertEqual(_perfil().dias(hoy=date(2026, 9, 23)), 10)


class PruebaEscalado(unittest.TestCase):

    def test_la_pendiente_exacta_gana(self):
        self.assertAlmostEqual(_perfil().pendiente(2, 2, 4), 12.06)

    def test_idac3_sobre_lpo_no_escala_con_la_ganancia(self):
        """Entra despues de PGAout: son los mismos mV por codigo en x1 y x50."""
        perfil = _perfil()
        self.assertAlmostEqual(perfil.pendiente(3, 4, 1), 6.684)
        self.assertAlmostEqual(perfil.pendiente(3, 4, 50), 6.684)

    def test_idac2_sobre_lpo_si_escala(self):
        """Entra antes de PGAout, asi que su efecto se multiplica."""
        perfil = _perfil()
        self.assertAlmostEqual(perfil.pendiente(2, 4, 4), 192.0)

    def test_lo_que_no_esta_devuelve_nada(self):
        self.assertIsNone(_perfil().pendiente(0, 3, 4))


class PruebaPuntoDeEntrada(unittest.TestCase):

    def test_usa_el_punto_exacto_si_existe(self):
        self.assertEqual(_perfil().punto_de_entrada(4), [16, -41, -118, 12])

    def test_de_otra_ganancia_conserva_solo_los_de_antes_de_pgaout(self):
        """IDAC0 e IDAC1 actuan antes de PGAout: su punto no depende de la
        ganancia de salida. IDAC2 e IDAC3 si, y arrancan de cero."""
        self.assertEqual(_perfil().punto_de_entrada(24), [16, -41, 0, 0])

    def test_sin_ningun_punto_no_hay_de_donde_partir(self):
        perfil = _perfil()
        perfil.puntos = {}
        self.assertIsNone(perfil.punto_de_entrada(4))


class PruebaProbado(unittest.TestCase):

    def test_un_perfil_recien_sembrado_no_esta_probado(self):
        """Sembrar a mano un punto no lo valida: nadie lo corrio."""
        self.assertFalse(_perfil().probado(4))

    def test_un_punto_validado_sirve_para_cualquier_ganancia(self):
        """IDAC0 e IDAC1 actuan antes de PGAout, asi que su optimo no depende
        de la ganancia de salida."""
        perfil = _perfil()
        perfil.validados = ["x4"]
        self.assertTrue(perfil.probado(24))


class PruebaPersistencia(unittest.TestCase):

    def test_ida_y_vuelta(self):
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = Path(tmp)
            original = _perfil()
            original.guardar(carpeta)
            leido = PerfilPlaca.cargar("geo-01", carpeta)
            self.assertEqual(leido, original)

    def test_una_placa_desconocida_no_revienta(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(PerfilPlaca.cargar("no-existe", Path(tmp)))

    def test_el_resumen_avisa_del_vencimiento(self):
        texto = resumen(_perfil(), hoy=date(2027, 1, 1))
        self.assertIn("VENCIDO", texto)
        self.assertIn("geo-01", texto)


if __name__ == "__main__":
    unittest.main(verbosity=2)
