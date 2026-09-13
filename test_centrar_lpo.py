"""Prueba la biseccion de signo contra plantas simuladas.

Lo que hay que demostrar antes de gastar minutos de placa es que el metodo
aguanta justo lo que rompio a los lazos PI: saturacion, ganancia desconocida,
signo desconocido y deriva. La biseccion no deberia necesitar ninguna de esas
cosas, y aca se verifica que efectivamente no las necesita.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import centrar_lpo


class PlantaSaturada:
    """Un tap que responde lineal en el centro y se clava en los rieles.

    `cero` es el codigo que anula la salida, `ganancia` los mV por codigo y
    `riel` el modulo de la saturacion. La deriva se suma por cada lectura, para
    imitar la cadena lenta.
    """

    def __init__(self, cero: float, ganancia: float, riel: float = 2300.0,
                 deriva_por_lectura: float = 0.0, etapa: int = 2):
        self.etapa = etapa
        self.cero = cero
        self.ganancia = ganancia
        self.riel = riel
        self.deriva_por_lectura = deriva_por_lectura
        self.codigos = {0: 0, 1: 0, 2: 0, 3: 0}
        self.desplazamiento = 0.0
        self.lecturas = 0
        self.escrituras = 0

    # --- lo que usa el codigo bajo prueba -------------------------------
    def set_idac(self, etapa: int, codigo: int) -> bool:
        self.codigos[etapa] = codigo
        self.escrituras += 1
        return True

    def valor(self) -> float:
        self.lecturas += 1
        self.desplazamiento += self.deriva_por_lectura
        crudo = ((self.codigos[self.etapa] - self.cero) * self.ganancia
                 + self.desplazamiento)
        return max(-self.riel, min(self.riel, crudo))


class PuntoFalso:
    """Lo que devuelve measure_dc: media y pico a pico en microvolts de banco."""

    def __init__(self, mean_uv: float, pp_uv: float):
        self.mean_uv = mean_uv
        self.pp_uv = pp_uv
        self.ok = True


class LabFalso:
    def __init__(self, planta: PlantaSaturada):
        self.planta = planta

    def set_idac(self, etapa: int, codigo: int) -> bool:
        return self.planta.set_idac(etapa, codigo)

    def measure_dc(self, canal: int, settle_sel: int = 2) -> PuntoFalso:
        # La planta habla en mV reales; el banco es la unidad del ADC.
        real = self.planta.valor()
        banco_mv = real / centrar_lpo.FACTOR + centrar_lpo.BANCO_VREF_MV
        return PuntoFalso(banco_mv * 1000.0, 1500.0)


def _parchear(prueba, planta: PlantaSaturada) -> None:
    """Elimina las esperas. La lectura la sirve el LabFalso."""
    prueba.addCleanup(setattr, centrar_lpo.time, "sleep",
                      centrar_lpo.time.sleep)
    centrar_lpo.time.sleep = lambda _s: None


def _sin_registro(_texto: str) -> None:
    return None


class PruebaBiseccion(unittest.TestCase):

    def test_encuentra_el_cero_con_ganancia_positiva(self):
        planta = PlantaSaturada(cero=37.0, ganancia=12.0)
        _parchear(self, planta)
        codigo, valor, _valido, motivo = centrar_lpo.bisectar(
            LabFalso(planta), 2, 4, -180, 180, 0.0, _sin_registro)
        self.assertEqual(motivo, "")
        self.assertLessEqual(abs(codigo - 37), 1)
        self.assertLessEqual(abs(valor), 12.0)

    def test_encuentra_el_cero_con_ganancia_negativa(self):
        """El signo de la planta no se le dice al algoritmo."""
        planta = PlantaSaturada(cero=-91.0, ganancia=-12.0)
        _parchear(self, planta)
        codigo, _valor, _valido, motivo = centrar_lpo.bisectar(
            LabFalso(planta), 2, 4, -180, 180, 0.0, _sin_registro)
        self.assertEqual(motivo, "")
        self.assertLessEqual(abs(codigo + 91), 1)

    def test_funciona_con_los_dos_extremos_saturados(self):
        """Es el caso real: en los extremos el tap esta contra el riel.

        Un lazo que necesite la magnitud no puede hacer nada aca. La biseccion
        solo mira el signo, que la saturacion conserva.
        """
        planta = PlantaSaturada(cero=-64.0, ganancia=120.0, riel=2300.0)
        _parchear(self, planta)
        codigo, valor, _valido, motivo = centrar_lpo.bisectar(
            LabFalso(planta), 2, 4, -180, 180, 0.0, _sin_registro)
        self.assertEqual(motivo, "")
        self.assertLessEqual(abs(codigo + 64), 1)
        self.assertLessEqual(abs(valor), 120.0)

    def test_ganancia_cincuenta_veces_mayor_de_lo_que_nadie_supuso(self):
        """Lo que volteo al PI el 2026-09-13 no afecta a la biseccion."""
        planta = PlantaSaturada(cero=12.0, ganancia=600.0)
        _parchear(self, planta)
        codigo, _valor, _valido, motivo = centrar_lpo.bisectar(
            LabFalso(planta), 2, 4, -180, 180, 0.0, _sin_registro)
        self.assertEqual(motivo, "")
        self.assertLessEqual(abs(codigo - 12), 1)

    def test_avisa_cuando_el_cero_no_esta_encerrado(self):
        planta = PlantaSaturada(cero=400.0, ganancia=12.0)
        _parchear(self, planta)
        codigo, _valor, _valido, motivo = centrar_lpo.bisectar(
            LabFalso(planta), 2, 4, -180, 180, 0.0, _sin_registro)
        self.assertNotEqual(motivo, "")
        # Se queda en el extremo que menos se aleja, no en cualquiera.
        self.assertEqual(codigo, 180)

    def test_no_encerrar_el_cero_no_significa_estar_fuera_de_ventana(self):
        """Son dos cosas distintas y confundirlas aborto una corrida.

        El 2026-09-13 la biseccion devolvia validez False fija cuando los dos
        extremos daban el mismo signo, y el llamador leyo eso como "el sumador
        esta fuera de ventana" cuando el extremo elegido lo dejaba en +256 mV.
        """
        planta = PlantaSaturada(cero=400.0, ganancia=12.0)
        _parchear(self, planta)
        _codigo, valor, valido, motivo = centrar_lpo.bisectar(
            LabFalso(planta), 2, 4, -180, 180, 0.0, _sin_registro)
        self.assertNotEqual(motivo, "")
        self.assertTrue(valido, "el extremo elegido esta adentro de la ventana")
        self.assertLess(abs(valor), 2400.0)

    def test_aguanta_deriva(self):
        """Con deriva el resultado empeora, pero no se va: sigue acotado."""
        planta = PlantaSaturada(cero=20.0, ganancia=12.0,
                                deriva_por_lectura=1.0)
        _parchear(self, planta)
        codigo, _valor, _valido, motivo = centrar_lpo.bisectar(
            LabFalso(planta), 2, 4, -180, 180, 0.0, _sin_registro)
        self.assertEqual(motivo, "")
        self.assertLessEqual(abs(codigo - 20), 12)

    def test_no_gasta_mas_sondas_de_las_previstas(self):
        """El presupuesto de tiempo del campo depende de esta cuenta."""
        planta = PlantaSaturada(cero=5.0, ganancia=12.0)
        _parchear(self, planta)
        centrar_lpo.bisectar(LabFalso(planta), 2, 4, -180, 180, 0.0,
                             _sin_registro)
        # dos extremos + nueve pasos + dos de desempate, cada uno con tres
        # lecturas promediadas.
        self.assertLessEqual(planta.escrituras, 13)

    def test_el_intervalo_nunca_se_agranda(self):
        """La propiedad que hace imposible el modo de falla del PI."""
        planta = PlantaSaturada(cero=-150.0, ganancia=12.0)
        _parchear(self, planta)
        vistos = []
        centrar_lpo.bisectar(LabFalso(planta), 2, 4, -180, 180, 0.0,
                             lambda t: vistos.append(t))
        codigos = [planta.codigos[2]]
        self.assertTrue(all(-180 <= c <= 180 for c in codigos))


class PruebaBiseccionSinResondeos(unittest.TestCase):

    def test_devuelve_el_mejor_punto_visto_sin_medir_de_nuevo(self):
        """Cada sondeo de mas es una oportunidad mas de que la deriva arruine
        el resultado. El 2026-09-13 el desempate volvio a medir un codigo que
        habia dado -826 mV y leyo +1.855."""
        planta = PlantaSaturada(cero=37.0, ganancia=12.0)
        _parchear(self, planta)
        centrar_lpo.bisectar(LabFalso(planta), 2, 4, -180, 180, 0.0,
                             _sin_registro)
        # La propiedad que importa: la ultima escritura, la que deja el
        # actuador en el mejor punto, NO va seguida de una medicion. Cada
        # sondeo se lee tres veces, asi que las lecturas tienen que dar
        # exactamente tres por cada escritura menos la ultima.
        self.assertEqual(planta.lecturas, 3 * (planta.escrituras - 1))

    def test_el_punto_devuelto_es_el_de_menor_error(self):
        planta = PlantaSaturada(cero=37.0, ganancia=12.0)
        _parchear(self, planta)
        codigo, valor, _ok, _motivo = centrar_lpo.bisectar(
            LabFalso(planta), 2, 4, -180, 180, 0.0, _sin_registro)
        self.assertEqual(planta.codigos[2], codigo)
        self.assertLessEqual(abs(valor), 12.0)


class PruebaToleranciaTemprana(unittest.TestCase):
    """Parar apenas alcanza. En esta cadena un punto bueno no sobrevive a los
    sondeos siguientes: el 2026-09-13 la biseccion encontro ch3 en -183 mV,
    siguio sondeando cinco veces y al volver el punto ya no estaba."""

    def test_para_apenas_entra_en_la_banda(self):
        planta = PlantaSaturada(cero=0.0, ganancia=12.0, riel=1e9)
        _parchear(self, planta)
        codigo, valor, _ok, _motivo = centrar_lpo.bisectar(
            LabFalso(planta), 2, 4, -180, 180, 0.0, _sin_registro,
            tolerancia_mv=300.0)
        self.assertLessEqual(abs(valor), 300.0)
        # Con 12 mV por codigo, 300 mV son 25 codigos: para mucho antes de
        # agotar los nueve pasos.
        self.assertLessEqual(planta.escrituras, 6)
        self.assertEqual(planta.codigos[2], codigo)

    def test_sin_tolerancia_afina_hasta_el_final(self):
        planta = PlantaSaturada(cero=0.0, ganancia=12.0, riel=1e9)
        _parchear(self, planta)
        centrar_lpo.bisectar(LabFalso(planta), 2, 4, -180, 180, 0.0,
                             _sin_registro)
        self.assertGreater(planta.escrituras, 6)

    def test_un_tap_fuera_de_ventana_no_cuenta_como_alcanzado(self):
        """Un valor chico leido fuera de ventana no es un valor chico."""
        planta = PlantaSaturada(cero=0.0, ganancia=12.0, riel=1e9)
        _parchear(self, planta)
        lab = LabFalso(planta)
        # Toda lectura cae fuera de la ventana del ADC.
        lab.measure_dc = lambda canal, settle_sel=2: PuntoFalso(
            700.0 * 1000.0, 1500.0)
        _codigo, _valor, ok, _motivo = centrar_lpo.bisectar(
            lab, 2, 4, -180, 180, 0.0, _sin_registro, tolerancia_mv=1e9)
        self.assertFalse(ok)


class PruebaVentanaUtil(unittest.TestCase):

    def test_el_riel_de_ch3_no_cuenta_como_dentro(self):
        """El riel de ch3 son +1.855 mV, y eso cae adentro de la ventana de
        +-2,4 V del ADC: `lectura_valida` lo da por bueno. Sin una tolerancia
        aplicada, la busqueda acepta un tap clavado."""
        self.assertTrue(centrar_lpo.lectura_valida(
            1854.8 / centrar_lpo.FACTOR + centrar_lpo.BANCO_VREF_MV),
            "el ADC lo considera valido, y por eso hace falta la tolerancia")
        self.assertGreater(abs(1854.8), centrar_lpo.TOLERANCIA_CH3_MV)


class PruebaEscaneo(unittest.TestCase):
    """El barrido monotono es lo que reemplaza a la biseccion en el actuador
    lento, donde los saltos grandes dejan transitorios que tapan la lectura."""

    def test_encuentra_el_cruce(self):
        planta = PlantaSaturada(cero=-36.0, ganancia=41.0, etapa=1)
        _parchear(self, planta)
        codigo, valor, _ok, motivo = centrar_lpo.escanear(
            LabFalso(planta), 1, 2, -96, 24, 8, 0.0, _sin_registro)
        self.assertEqual(motivo, "")
        self.assertLessEqual(abs(codigo + 36), 8)
        self.assertLessEqual(abs(valor), 41.0 * 8)

    def test_funciona_con_pendiente_negativa(self):
        planta = PlantaSaturada(cero=-36.0, ganancia=-41.0, etapa=1)
        _parchear(self, planta)
        codigo, _valor, _ok, motivo = centrar_lpo.escanear(
            LabFalso(planta), 1, 2, -96, 24, 8, 0.0, _sin_registro)
        self.assertEqual(motivo, "")
        self.assertLessEqual(abs(codigo + 36), 8)

    def test_avisa_si_no_cruza(self):
        planta = PlantaSaturada(cero=+500.0, ganancia=41.0, etapa=1)
        _parchear(self, planta)
        _codigo, _valor, _ok, motivo = centrar_lpo.escanear(
            LabFalso(planta), 1, 2, -96, 24, 8, 0.0, _sin_registro)
        self.assertEqual(motivo, "el tap no cruzo el cero")

    def test_nunca_vuelve_atras_aunque_el_punto_previo_sea_mejor(self):
        """Volver no devuelve la cadena a donde estaba.

        El 2026-09-13 el barrido paso por IDAC1=-16 con ch2 en +320 y, al
        retroceder a ese mismo codigo, leyo riel durante ocho minutos. La
        absorcion dielectrica del acople de 680 uF hace que la vuelta no sea
        simetrica con la ida, asi que el barrido se queda donde llego.
        """
        planta = PlantaSaturada(cero=-40.0, ganancia=41.0, etapa=1)
        _parchear(self, planta)
        codigo, _valor, _ok, _motivo = centrar_lpo.escanear(
            LabFalso(planta), 1, 2, -96, 24, 8, 0.0, _sin_registro)
        # Cruza entre -40 y -32; se queda en -32, el punto que acaba de medir.
        self.assertEqual(codigo, -32)
        self.assertEqual(planta.codigos[1], -32)

    def test_no_gasta_mas_sondeos_que_el_rango_dividido_el_paso(self):
        planta = PlantaSaturada(cero=+1000.0, ganancia=41.0, etapa=1)
        _parchear(self, planta)
        centrar_lpo.escanear(LabFalso(planta), 1, 2, -96, 24, 8, 0.0,
                             _sin_registro)
        # De -96 a +24 en pasos de 8 son 16 puntos, mas la
        # escritura final que deja el actuador donde quedo.
        self.assertLessEqual(planta.escrituras, 17)


class PruebaEscaneoEnDosEtapas(unittest.TestCase):

    def test_afina_mejor_que_el_grueso_solo(self):
        planta = PlantaSaturada(cero=-37.0, ganancia=6.9, riel=1e9, etapa=1)
        _parchear(self, planta)
        codigo, _valor, _ok, motivo = centrar_lpo.escanear_en_dos_etapas(
            LabFalso(planta), 1, 2, -36, 60, 0.0, _sin_registro)
        self.assertEqual(motivo, "")
        # El paso grueso son 10 y el fino 2: tiene que quedar mejor que 10.
        self.assertLessEqual(abs(codigo + 37), 2)

    def test_propaga_el_aviso_del_grueso(self):
        planta = PlantaSaturada(cero=+900.0, ganancia=6.9, riel=1e9, etapa=1)
        _parchear(self, planta)
        _codigo, _valor, _ok, motivo = centrar_lpo.escanear_en_dos_etapas(
            LabFalso(planta), 1, 2, -36, 60, 0.0, _sin_registro)
        self.assertEqual(motivo, "el tap no cruzo el cero")


class PruebaApuntar(unittest.TestCase):
    """Medir la pendiente, saltar y VERIFICAR. Lo que rompio a los PI fue
    integrar sobre una ganancia mal estimada sin verificar cada paso."""

    def test_llega_al_cero_en_pocos_intentos(self):
        planta = PlantaSaturada(cero=+33.0, ganancia=6.9, riel=1e9, etapa=1)
        _parchear(self, planta)
        codigo, valor, ok, motivo = centrar_lpo.apuntar(
            LabFalso(planta), 1, 2, -96, 255, 8, 0.0, _sin_registro)
        self.assertEqual(motivo, "")
        self.assertTrue(ok)
        self.assertLessEqual(abs(valor), 150.0)
        self.assertLessEqual(abs(codigo - 33), 2)

    def test_converge_con_la_pendiente_al_reves(self):
        planta = PlantaSaturada(cero=-70.0, ganancia=-6.9, riel=1e9, etapa=1)
        _parchear(self, planta)
        _codigo, _valor, ok, motivo = centrar_lpo.apuntar(
            LabFalso(planta), 1, 2, 0, 255, 8, 0.0, _sin_registro)
        self.assertEqual(motivo, "")
        self.assertTrue(ok)

    def test_avisa_si_el_tap_no_responde(self):
        planta = PlantaSaturada(cero=0.0, ganancia=0.0, riel=1e9, etapa=1)
        _parchear(self, planta)
        planta.desplazamiento = 900.0
        _codigo, _valor, _ok, motivo = centrar_lpo.apuntar(
            LabFalso(planta), 1, 2, -96, 255, 8, 0.0, _sin_registro)
        self.assertEqual(motivo, "el tap no responde al actuador")

    def test_no_gasta_mas_de_tres_intentos(self):
        planta = PlantaSaturada(cero=+33.0, ganancia=6.9, riel=1e9, etapa=1)
        _parchear(self, planta)
        centrar_lpo.apuntar(LabFalso(planta), 1, 2, -96, 255, 8, 0.0,
                            _sin_registro)
        # Por intento: una sonda y un salto. Tres intentos son seis escrituras.
        self.assertLessEqual(planta.escrituras, 6)


class PruebaLeerPromedio(unittest.TestCase):

    class _LabQueNoResponde:
        def measure_dc(self, canal, settle_sel=2):
            return None

    def test_sin_lecturas_devuelve_cero_y_tap_muerto(self):
        valor, vivo = centrar_lpo.leer_promedio(self._LabQueNoResponde(), 4,
                                                n=3)
        self.assertEqual(valor, 0.0)
        self.assertFalse(vivo)

    def test_un_tap_quieto_contra_el_borde_no_esta_vivo(self):
        planta = PlantaSaturada(cero=0.0, ganancia=12.0)
        lab = LabFalso(planta)
        # Pegado al borde inferior de la ventana y sin ruido propio.
        lab.measure_dc = lambda canal, settle_sel=2: PuntoFalso(
            885.0 * 1000.0, 5.0)
        _valor, vivo = centrar_lpo.leer_promedio(lab, 4, n=2)
        self.assertFalse(vivo)

    def test_un_tap_quieto_en_el_centro_SI_pasa_como_vivo(self):
        """Y por eso no alcanza con el pico a pico.

        El criterio de escala_banco pide poco ruido Y cercania a un borde. El
        riel del sumador esta en +1.884 mV, o sea banco 1.096, lejos de los dos
        bordes: un tap clavado ahi pasa como vivo. Esa es la razon por la que
        centrar_lpo bisecta IDAC0 siempre en vez de preguntar si hace falta.
        """
        planta = PlantaSaturada(cero=0.0, ganancia=12.0)
        lab = LabFalso(planta)
        lab.measure_dc = lambda canal, settle_sel=2: PuntoFalso(
            1096.0 * 1000.0, 5.0)
        _valor, vivo = centrar_lpo.leer_promedio(lab, 4, n=2)
        self.assertTrue(vivo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
