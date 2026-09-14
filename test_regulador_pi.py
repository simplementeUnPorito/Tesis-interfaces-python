"""Pruebas del regulador en cascada, contra una planta simulada.

Lo que hay que demostrar no es que converja con el modelo correcto, que seria
facil y no probaria nada. Hay que demostrar que converge **con el modelo mal**,
porque esa es la unica razon para preferirlo al lazo abierto que ya existe.

Corre con `py -3 test_regulador_pi.py`. Sin hardware y en segundos.
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from regulador_pi import (ETAPA_FINA, INVALIDAS_PARA_REPLEGAR,
                          LIMITE_FINO, ReguladorCascada,
                          alpha_desde_tau, paso_neutro,
                          paso_vernier, tolerancia_de_ganancia)


class PlantaSimulada:
    """Retardo de primer orden con dos actuadores, deriva y saturacion.

    Modela lo que se midio en placa: la salida sigue a los codigos con la tau
    del pasabanda, hay una deriva lenta propia, y el tap satura fuera de una
    ventana. Las ganancias REALES pueden diferir de las que el regulador cree.
    """

    def __init__(self, ganancia_fina: float, ganancia_gruesa: float,
                 tau_s: float, valor_inicial: float = 0.0,
                 deriva_mv_por_s: float = 0.0,
                 ventana_mv: float = 2400.0) -> None:
        self.kf, self.kg, self.tau = ganancia_fina, ganancia_gruesa, tau_s
        self.deriva = deriva_mv_por_s
        self.ventana = ventana_mv
        self.objetivo_interno = valor_inicial
        self.valor = valor_inicial
        self.offset = valor_inicial

    def escribir(self, codigo_fino: int, codigo_grueso: int) -> None:
        self.objetivo_interno = (self.offset + self.kf * codigo_fino
                                 + self.kg * codigo_grueso)

    def avanzar(self, dt_s: float) -> tuple[float, bool]:
        """Integra el retardo. Devuelve la lectura y si es VALIDA.

        La saturacion no se puede modelar solo recortando el valor: hay que
        decir tambien que la lectura dejo de significar algo, porque el
        regulador tiene que saberlo.
        """
        if self.tau > 0.0:
            fraccion = 1.0 - math.exp(-dt_s / self.tau)
        else:
            fraccion = 1.0
        self.valor += (self.objetivo_interno - self.valor) * fraccion
        self.offset += self.deriva * dt_s
        self.objetivo_interno += self.deriva * dt_s
        valido = abs(self.valor) <= self.ventana
        return max(-self.ventana, min(self.ventana, self.valor)), valido


def correr(regulador: ReguladorCascada, planta: PlantaSimulada,
           pasos: int = 200) -> list[float]:
    errores = []
    for _ in range(pasos):
        medido, valido = planta.avanzar(regulador.periodo_s)
        salida = regulador.paso(medido, valido)
        planta.escribir(salida.codigo_fino, salida.codigo_grueso)
        errores.append(salida.error_real_mv)
    return errores


class TestSintonia(unittest.TestCase):
    def test_alpha_sale_del_retardo_de_la_planta(self) -> None:
        # T/tau, que es donde el margen de fase es razonable.
        self.assertAlmostEqual(alpha_desde_tau(5.0, 34.0), 5.0 / 34.0,
                               places=4)

    def test_alpha_queda_acotada_en_los_extremos(self) -> None:
        # Con una planta rapidisima no se corrige de golpe: se perderia la
        # tolerancia al error de ganancia, que es toda la gracia.
        self.assertLessEqual(alpha_desde_tau(5.0, 1.0), 0.25)
        # Y con una lentisima no se paraliza el lazo.
        self.assertGreaterEqual(alpha_desde_tau(5.0, 10000.0), 0.05)

    def test_la_tolerancia_declarada_es_la_que_de_verdad_aguanta(self) -> None:
        # No alcanza con afirmar la formula: hay que ver que la planta real
        # converja justo por debajo del limite y no muy por encima.
        alpha = alpha_desde_tau(5.0, 34.0)
        limite = tolerancia_de_ganancia(alpha)
        self.assertGreater(limite, 5.0)

        adentro = ReguladorCascada(ganancia_fina=-12.0, ganancia_gruesa=-8.0,
                                   tau_s=34.0)
        planta = PlantaSimulada(-12.0 * (limite * 0.5), -8.0, 34.0, 900.0)
        errores = correr(adentro, planta, 300)
        self.assertLess(abs(errores[-1]), 20.0,
                        "con la ganancia errada por debajo del limite tenia "
                        "que converger")


class TestConvergencia(unittest.TestCase):
    def test_converge_al_piso_de_cuantizacion(self) -> None:
        # El error final no puede ser menor que un codigo del actuador fino:
        # doce milivolts. Pedir menos seria pedir una resolucion que el hardware
        # no tiene, y el lazo se pondria a oscilar buscandola.
        reg = ReguladorCascada(ganancia_fina=-12.0, ganancia_gruesa=-8.0,
                               tau_s=34.0)
        planta = PlantaSimulada(-12.0, -8.0, 34.0, valor_inicial=800.0)
        errores = correr(reg, planta)
        self.assertLessEqual(abs(errores[-1]), abs(reg.ganancia_fina))

    def test_converge_con_la_ganancia_cuatro_veces_mas_grande(self) -> None:
        # El caso que mata al lazo abierto. Las pendientes medidas variaron un
        # factor ocho entre corridas; el lazo tiene que sobrevivir a eso.
        reg = ReguladorCascada(ganancia_fina=-12.0, ganancia_gruesa=-8.0,
                               tau_s=34.0)
        planta = PlantaSimulada(-48.0, -8.0, 34.0, valor_inicial=800.0)
        errores = correr(reg, planta)
        self.assertLess(abs(errores[-1]), 20.0)

    def test_converge_con_la_ganancia_cuatro_veces_mas_chica(self) -> None:
        reg = ReguladorCascada(ganancia_fina=-12.0, ganancia_gruesa=-8.0,
                               tau_s=34.0)
        planta = PlantaSimulada(-3.0, -8.0, 34.0, valor_inicial=800.0)
        errores = correr(reg, planta, 400)
        self.assertLess(abs(errores[-1]), 40.0)

    def test_rechaza_la_deriva_lenta(self) -> None:
        # 7 mV/min es la deriva medida en la zona mala del actuador. Un
        # integrador la sigue sin despeinarse; el lazo abierto no puede.
        reg = ReguladorCascada(ganancia_fina=-12.0, ganancia_gruesa=-8.0,
                               tau_s=34.0)
        planta = PlantaSimulada(-12.0, -8.0, 34.0, valor_inicial=200.0,
                                deriva_mv_por_s=7.0 / 60.0)
        errores = correr(reg, planta, 400)
        self.assertLess(abs(errores[-1]), 30.0,
                        "el integrador tenia que seguir la deriva")

    def test_el_signo_equivocado_clava_el_fino_contra_su_limite(self) -> None:
        # Lo UNICO que el lazo no perdona. Y no se nota mirando el tap: con el
        # signo al reves el lazo interno corre hasta su tope, el externo se hace
        # cargo y el tap termina convergiendo igual. Lo que queda mal es DONDE
        # queda el actuador fino: clavado en su limite, o sea justo en la zona
        # donde deriva 7 mV/min.
        #
        # Por eso el signo se mide antes en vez de suponerse, y por eso la
        # aceptacion tiene que mirar el codigo del fino y no solo el error.
        reg = ReguladorCascada(ganancia_fina=+12.0, ganancia_gruesa=-8.0,
                               tau_s=34.0)
        planta = PlantaSimulada(-12.0, -8.0, 34.0, valor_inicial=200.0)
        correr(reg, planta, 100)
        # Desde el 2026-09-13 el descargue neutro tambien corre cuando el fino
        # satura, asi que ya no queda EXACTAMENTE en el limite: lo despega unos
        # pocos codigos. Lo que la prueba documenta sigue en pie -con el signo
        # al reves el fino se va contra su tope y se queda ahi- y por eso el
        # margen es de un puñado de codigos y no una tolerancia generosa.
        self.assertGreaterEqual(
            abs(reg.codigo_fino), LIMITE_FINO - 5,
            "con el signo al reves el fino tiene que terminar contra su "
            "limite, y esta prueba existe para documentarlo")


class TestCascada(unittest.TestCase):
    def test_el_grueso_devuelve_al_fino_hacia_el_centro(self) -> None:
        # Mid-ranging: lo que importa no es solo que converja el tap, sino que
        # el actuador fino termine lejos de su limite, que es donde se vuelve
        # inestable.
        reg = ReguladorCascada(ganancia_fina=-12.0, ganancia_gruesa=-8.0,
                               tau_s=34.0)
        planta = PlantaSimulada(-12.0, -8.0, 34.0, valor_inicial=1800.0)
        errores = correr(reg, planta, 600)
        self.assertLess(abs(errores[-1]), 30.0, "tenia que converger")
        self.assertLess(abs(reg.codigo_fino), reg.zona_comoda + 20,
                        f"el fino quedo en {reg.codigo_fino}, deberia haber "
                        "vuelto cerca del centro")
        self.assertNotEqual(reg.codigo_grueso, 0,
                            "el grueso tenia que haberse hecho cargo")

    def test_el_fino_nunca_pasa_su_limite(self) -> None:
        # Aunque la referencia sea inalcanzable. Pasarse no es "casi lograrlo":
        # es entrar en la zona donde el actuador deriva 7 mV/min.
        reg = ReguladorCascada(ganancia_fina=-12.0, ganancia_gruesa=-8.0,
                               tau_s=34.0)
        planta = PlantaSimulada(-12.0, -8.0, 34.0, valor_inicial=20000.0)
        correr(reg, planta, 300)
        for salida in reg.historial:
            self.assertLessEqual(abs(salida.codigo_fino), LIMITE_FINO)

    def test_no_hay_windup_al_volver_de_la_saturacion(self) -> None:
        # El integrador no puede acumular contra el limite: al liberarse
        # descargaria todo lo acumulado y sobrepasaria feo.
        reg = ReguladorCascada(ganancia_fina=-12.0, ganancia_gruesa=-8.0,
                               tau_s=34.0)
        planta = PlantaSimulada(-12.0, -8.0, 34.0, valor_inicial=20000.0)
        correr(reg, planta, 60)
        # Ahora la referencia se vuelve alcanzable de golpe.
        planta.offset = 300.0
        planta.escribir(reg.codigo_fino, reg.codigo_grueso)
        errores = correr(reg, planta, 300)
        self.assertLess(abs(errores[-1]), 40.0,
                        "tras salir de saturacion tenia que asentarse")
        self.assertLess(max(abs(e) for e in errores[-30:]), 60.0,
                        "quedo oscilando: huele a windup")


class TestVernier(unittest.TestCase):
    """A ganancias altas un solo actuador no alcanza. Esto es lo que lo salva."""

    def test_da_resolucion_muy_por_debajo_de_un_codigo(self) -> None:
        # Los dos pasos, 12,35 y 8,30 mV, son incomensurables: combinandolos en
        # oposicion se llega a decimas de milivolt.
        n_fino, n_grueso = paso_vernier(0.5, -12.35, -8.30)
        efecto = n_fino * -12.35 + n_grueso * -8.30
        self.assertLess(abs(efecto - 0.5), 2.0)
        self.assertLess(abs(efecto), 12.35,
                        "tenia que dar un paso MENOR que un codigo del fino")

    def test_no_devuelve_el_paso_nulo_cuando_hay_algo_que_hacer(self) -> None:
        self.assertNotEqual(paso_vernier(50.0, -12.35, -8.30), (0, 0))

    def test_x24_converge_dentro_de_la_ventana(self) -> None:
        # El caso que Elias pidio. En x24 un codigo del fino mueve LPo 2.830 mV
        # sobre una ventana de +-2.400: con el fino solo es IMPOSIBLE centrar,
        # porque el paso mas chico ya se pasa de largo.
        #
        # Las ganancias de abajo son las de LPo, o sea las de ch2 multiplicadas
        # por la ganancia de PGAout y por la de la etapa LP.
        k_lp = 24.0 * 9.07
        reg = ReguladorCascada(ganancia_fina=-12.35 * k_lp,
                               ganancia_gruesa=-8.30 * k_lp,
                               tau_s=34.0, tolerancia_real_mv=400.0)
        planta = PlantaSimulada(-12.35 * k_lp, -8.30 * k_lp, 34.0,
                                valor_inicial=1500.0, ventana_mv=2400.0)
        errores = [e for e in correr(reg, planta, 400) if e == e]
        self.assertLess(abs(errores[-1]), 400.0,
                        f"quedo en {errores[-1]:.0f} mV; con el vernier tenia "
                        "que entrar en la ventana")
        self.assertGreater(reg.pasos_vernier, 0,
                           "tenia que haber usado el vernier")

    def test_en_ganancias_bajas_no_gasta_el_vernier(self) -> None:
        # Mover el grueso arrastra la cola del pasabanda, asi que el vernier es
        # caro. Donde el fino solo alcanza, no hay que usarlo.
        reg = ReguladorCascada(ganancia_fina=-12.0, ganancia_gruesa=-8.0,
                               tau_s=34.0, tolerancia_real_mv=25.0)
        planta = PlantaSimulada(-12.0, -8.0, 34.0, valor_inicial=800.0)
        correr(reg, planta, 200)
        self.assertEqual(reg.pasos_vernier, 0)


class TestDescargueNeutro(unittest.TestCase):
    def test_el_residuo_es_mucho_menor_que_mover_uno_solo(self) -> None:
        # Es la razon de existir de la pieza. A x24 mover el grueso solo son
        # 1.807 mV de patada; el par neutro deja 43.
        k = 24.0 * 9.07
        kf, kg = -12.35 * k, -8.30 * k
        m_fino, m_grueso = paso_neutro(kf, kg)
        residuo = abs(m_fino * kf + m_grueso * kg)
        self.assertLess(residuo, abs(kg) / 10.0)
        self.assertLess(residuo, abs(kf) / 10.0)

    def test_devuelve_el_fino_positivo_para_que_el_llamador_ponga_el_signo(self) -> None:
        m_fino, _ = paso_neutro(-2688.0, -1807.0)
        self.assertGreater(m_fino, 0)

    def test_sirve_tambien_con_las_ganancias_de_ganancia_baja(self) -> None:
        m_fino, m_grueso = paso_neutro(-12.35, -8.30)
        residuo = abs(m_fino * -12.35 + m_grueso * -8.30)
        self.assertLess(residuo, 12.35 / 5.0)

    def test_todas_las_ganancias_convergen_en_simulacion(self) -> None:
        # El barrido completo. Con el fino solo, de x16 para arriba es
        # imposible; con vernier y descargue neutro entran todas.
        for ganancia in (1, 4, 8, 16, 24, 32, 50):
            with self.subTest(ganancia=ganancia):
                k = ganancia * 9.07
                reg = ReguladorCascada(ganancia_fina=-12.35 * k,
                                       ganancia_gruesa=-8.30 * k,
                                       tau_s=34.0, tolerancia_real_mv=400.0)
                planta = PlantaSimulada(-12.35 * k, -8.30 * k, 34.0,
                                        valor_inicial=1500.0,
                                        ventana_mv=2400.0)
                errores = [e for e in correr(reg, planta, 500) if e == e]
                self.assertLess(abs(errores[-1]), 400.0)
                self.assertLess(abs(reg.codigo_fino), LIMITE_FINO,
                                "el fino quedo en su zona mala")


class TestRepliegue(unittest.TestCase):
    """Un regulador que se pinta solo en un rincon no sirve desatendido."""

    def test_vuelve_al_ultimo_punto_bueno(self) -> None:
        reg = ReguladorCascada(ganancia_fina=-12.0, ganancia_gruesa=-8.0,
                               tau_s=34.0)
        # Unos pasos con lectura valida para que quede memoria. El punto bueno
        # son los codigos CON LOS QUE SE TOMO la ultima lectura valida, o sea
        # los de antes de corregir, no los de despues: son esos los que la
        # produjeron.
        for _ in range(2):
            reg.paso(100.0, True)
        bueno = (reg.codigo_fino, reg.codigo_grueso)
        reg.paso(100.0, True)
        # Y ahora el tap se va de rango y no vuelve.
        for _ in range(INVALIDAS_PARA_REPLEGAR):
            reg.paso(0.0, False)
        self.assertEqual((reg.codigo_fino, reg.codigo_grueso), bueno)
        self.assertEqual(reg.repliegues, 1)
        self.assertIn("repliega", reg.historial[-1].motivo)

    def test_no_se_repliega_por_una_lectura_suelta(self) -> None:
        # Una invalida aislada es un transitorio, no un rincon.
        reg = ReguladorCascada(ganancia_fina=-12.0, ganancia_gruesa=-8.0,
                               tau_s=34.0)
        reg.paso(100.0, True)
        antes = (reg.codigo_fino, reg.codigo_grueso)
        reg.paso(0.0, False)
        reg.paso(100.0, True)
        self.assertEqual(reg.repliegues, 0)
        self.assertNotEqual((reg.codigo_fino, reg.codigo_grueso), antes)

    def test_sin_punto_bueno_previo_no_inventa_uno(self) -> None:
        # Si nunca hubo lectura valida no hay a donde volver, y hay que decirlo
        # en vez de saltar a un lugar arbitrario.
        reg = ReguladorCascada(ganancia_fina=-12.0, ganancia_gruesa=-8.0,
                               tau_s=34.0, codigo_fino=25, codigo_grueso=7)
        for _ in range(INVALIDAS_PARA_REPLEGAR + 2):
            reg.paso(0.0, False)
        self.assertEqual(reg.repliegues, 0)
        self.assertEqual((reg.codigo_fino, reg.codigo_grueso), (25, 7))


class TestConvergido(unittest.TestCase):
    def test_pide_varias_muestras_seguidas(self) -> None:
        reg = ReguladorCascada(ganancia_fina=-12.0, ganancia_gruesa=-8.0,
                               tau_s=34.0)
        planta = PlantaSimulada(-12.0, -8.0, 34.0, valor_inicial=600.0)
        correr(reg, planta, 200)
        self.assertTrue(reg.convergido(30.0))
        # Una sola muestra buena en medio de un transitorio no alcanza: la
        # cadena tiene mas de un polo y el error cruza cero de paso.
        reg.historial[-1].error_real_mv = 0.0
        reg.historial[-2].error_real_mv = 500.0
        self.assertFalse(reg.convergido(30.0))


if __name__ == "__main__":
    unittest.main(verbosity=1)


class PruebaTustinYProporcional(unittest.TestCase):
    """Lo que se agrego el 2026-09-13 a pedido de Elias."""

    def _regulador(self, **extra):
        base = dict(ganancia_fina=12.0, ganancia_gruesa=200.0, tau_s=34.0,
                    periodo_s=5.0, referencia_real_mv=0.0)
        base.update(extra)
        return ReguladorCascada(**base)

    def test_el_primer_paso_es_igual_que_antes(self):
        """Sin muestra anterior el trapecio degenera en rectangulo y el
        proporcional no aporta: arranca igual que la version vieja."""
        reg = self._regulador()
        salida = reg.paso(-600.0)
        esperado = int(round(reg.alpha * 600.0 / 12.0))
        self.assertEqual(salida.codigo_fino, esperado)

    def test_el_proporcional_acelera_cuando_el_error_cambia(self):
        """Con el error creciendo, el paso tiene que ser mayor que el que daria
        la integral sola."""
        con_p = self._regulador(kp_relativo=1.0)
        sin_p = self._regulador(kp_relativo=0.0)
        for reg in (con_p, sin_p):
            reg.paso(-300.0)
        paso_con = con_p.paso(-600.0).codigo_fino - con_p.codigo_fino
        # El estado ya avanzo; se compara la magnitud del codigo acumulado.
        self.assertGreater(abs(con_p.codigo_fino), abs(sin_p.paso(-600.0).codigo_fino))

    def test_en_regimen_el_proporcional_no_aporta(self):
        """Con el error quieto, e[k]-e[k-1] es cero: solo queda la integral, y
        por eso el error de regimen sigue siendo nulo."""
        reg = self._regulador(kp_relativo=1.0)
        reg.paso(-100.0)
        antes = reg.codigo_fino
        reg.paso(-100.0)
        paso = reg.codigo_fino - antes
        integral_sola = int(round(reg.alpha * 100.0 / 12.0))
        self.assertLessEqual(abs(abs(paso) - abs(integral_sola)), 1)

    def test_olvida_el_error_previo_tras_una_lectura_invalida(self):
        """Dos muestras separadas por minutos no son vecinas: promediarlas en
        el trapecio o restarlas en el proporcional seria inventar una pendiente
        que no existe."""
        reg = self._regulador()
        reg.paso(-500.0)
        self.assertIsNotNone(reg.error_previo_mv)
        reg.paso(0.0, lectura_valida=False)
        self.assertIsNone(reg.error_previo_mv)

    def test_el_proporcional_acelera_de_verdad(self):
        """La afirmacion que justifica el cambio, medida.

        El barrido de todas las ganancias ya lo cubre
        `test_todas_las_ganancias_convergen_en_simulacion`, que corre con Tustin
        y proporcional puestos. Lo que falta comprobar es que el proporcional
        sirva para algo: que llegue ANTES.
        """
        def pasos_hasta(tolerancia: float, kp: float) -> int:
            k = 4 * 9.07
            reg = ReguladorCascada(ganancia_fina=-12.35 * k,
                                   ganancia_gruesa=-8.30 * k,
                                   tau_s=34.0, tolerancia_real_mv=400.0,
                                   kp_relativo=kp)
            planta = PlantaSimulada(-12.35 * k, -8.30 * k, 34.0,
                                    valor_inicial=1500.0, ventana_mv=2400.0)
            for n in range(500):
                medido, valido = planta.avanzar(reg.periodo_s)
                salida = reg.paso(medido, valido)
                planta.escribir(salida.codigo_fino, salida.codigo_grueso)
                if valido and abs(medido) < tolerancia:
                    return n
            return 500

        con_p = pasos_hasta(200.0, 1.0)
        sin_p = pasos_hasta(200.0, 0.0)
        self.assertLessEqual(con_p, sin_p,
                             f"con P tardo {con_p} pasos y sin P {sin_p}")
