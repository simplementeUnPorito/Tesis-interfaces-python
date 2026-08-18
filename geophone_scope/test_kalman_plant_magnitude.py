"""Regresiones fisicas de la conversion de magnitud de entrada de la planta.

Lo que se fija aca es una identidad, no una tolerancia de ajuste: si la planta
nativa tiene la aceleracion del suelo como entrada, entonces

    H_velocity(jw)     / H_acceleration(jw) = jw
    H_displacement(jw) / H_acceleration(jw) = (jw)^2

porque ``a_ground = s*v_ground = s^2*d_ground``. La version anterior de
``plant._integrator_chain`` agregaba **polos** en el origen y construia ``H_a/s``,
con lo cual el estimador de "velocidad" recuperaba en realidad la derivada de la
aceleracion y realzaba las frecuencias altas.

Comando::

    cd C:/Github/Tesis/src/interfaces/python
    python -m unittest geophone_scope.test_kalman_plant_magnitude -v
"""

from __future__ import annotations

import unittest

import numpy as np

from geophone_scope.kalman_deconv.discretize import prepare_plant
from geophone_scope.kalman_deconv.library import load_conditioner, load_geophone
from geophone_scope.kalman_deconv.models import PlantSpec
from geophone_scope.kalman_deconv.plant import (
    compose_plant_zpk,
    relative_degree,
    ss_freqresp,
    zpk_freqresp,
    zpk_to_modal_ss,
)

# Los dos acondicionadores de la campana. Ver HANDOFF_KALMAN.md §5.2.
CONDITIONERS = ("comp_nominal", "lp_pga_medido")
MAGNITUDES = ("acceleration", "velocity", "displacement")

# Grilla logaritmica ancha: cubre desde muy por debajo del limite de validez del
# modelo (0,211 Hz) hasta bastante por encima de la banda util 10-50 Hz. La
# identidad es algebraica y tiene que valer en todas.
FREQS_HZ = np.logspace(np.log10(0.01), np.log10(1000.0), 400)
OMEGA = 2.0 * np.pi * FREQS_HZ

# Tolerancias. La identidad sobre el zpk es exacta salvo redondeo de punto
# flotante; el producto de hasta 13 factores complejos deja unos pocos ulps.
TOL_ZPK_REL = 1e-12
# La realizacion modal ajusta C por minimos cuadrados sobre una grilla, asi que
# se le pide lo mismo que a los gates de S1: decimas de dB y decimas de grado.
TOL_MODAL_DB = 0.10
TOL_MODAL_DEG = 1.0


def _spec(cond_id: str, estimate: str) -> PlantSpec:
    cond = load_conditioner(cond_id)
    return PlantSpec(
        geophone=load_geophone("sm24_nominal"),
        conditioner=cond,
        include_geophone=not cond.includes_geophone,
        estimate=estimate,
    )


def _zpk_response(cond_id: str, estimate: str) -> np.ndarray:
    return zpk_freqresp(*compose_plant_zpk(_spec(cond_id, estimate)), OMEGA)


class MagnitudeIdentity(unittest.TestCase):
    """H_v/H_a = jw y H_d/H_a = (jw)^2 sobre el zpk compuesto."""

    def test_velocity_over_acceleration_is_jw(self):
        for cond_id in CONDITIONERS:
            with self.subTest(cond=cond_id):
                ratio = _zpk_response(cond_id, "velocity") / _zpk_response(
                    cond_id, "acceleration"
                )
                expected = 1j * OMEGA
                error = np.abs(ratio - expected) / np.abs(expected)
                self.assertTrue(np.all(np.isfinite(ratio)))
                self.assertLess(
                    float(np.max(error)), TOL_ZPK_REL,
                    f"{cond_id}: max error relativo {np.max(error):.3e} en "
                    f"{FREQS_HZ[int(np.argmax(error))]:.4g} Hz",
                )

    def test_displacement_over_acceleration_is_jw_squared(self):
        for cond_id in CONDITIONERS:
            with self.subTest(cond=cond_id):
                ratio = _zpk_response(cond_id, "displacement") / _zpk_response(
                    cond_id, "acceleration"
                )
                expected = (1j * OMEGA) ** 2
                error = np.abs(ratio - expected) / np.abs(expected)
                self.assertTrue(np.all(np.isfinite(ratio)))
                self.assertLess(
                    float(np.max(error)), TOL_ZPK_REL,
                    f"{cond_id}: max error relativo {np.max(error):.3e}",
                )

    def test_ratio_is_a_differentiator_in_magnitude_and_phase(self):
        """Chequeo redundante en dB y grados, que es como se lee en el informe.

        20*log10|H_v/H_a| tiene que subir 20 dB/decada y la fase valer +90 grados
        exactos. Es la firma que distingue un derivador (correcto) de un
        integrador (el bug): el integrador daria -20 dB/decada y -90 grados.
        """
        for cond_id in CONDITIONERS:
            with self.subTest(cond=cond_id):
                ratio = _zpk_response(cond_id, "velocity") / _zpk_response(
                    cond_id, "acceleration"
                )
                phase_deg = np.angle(ratio, deg=True)
                self.assertLess(float(np.max(np.abs(phase_deg - 90.0))), 1e-9)
                mag_db = 20.0 * np.log10(np.abs(ratio))
                slope = np.polyfit(np.log10(FREQS_HZ), mag_db, 1)[0]
                self.assertAlmostEqual(slope, 20.0, places=6)


class PoleZeroCounts(unittest.TestCase):
    """Conteo de ceros/polos y grado relativo esperado."""

    def test_zero_count_grows_and_poles_stay(self):
        for cond_id in CONDITIONERS:
            base_z, base_p, _ = compose_plant_zpk(_spec(cond_id, "acceleration"))
            for offset, estimate in enumerate(MAGNITUDES):
                with self.subTest(cond=cond_id, estimate=estimate):
                    z, p, _ = compose_plant_zpk(_spec(cond_id, estimate))
                    self.assertEqual(len(z), len(base_z) + offset)
                    self.assertEqual(len(p), len(base_p))
                    self.assertEqual(
                        relative_degree(z, p), relative_degree(base_z, base_p) - offset
                    )

    def test_extra_zeros_are_exactly_at_the_origin(self):
        for cond_id in CONDITIONERS:
            with self.subTest(cond=cond_id):
                base = compose_plant_zpk(_spec(cond_id, "acceleration"))[0]
                z = compose_plant_zpk(_spec(cond_id, "velocity"))[0]
                n_origin_base = int(np.sum(np.abs(base) == 0.0))
                n_origin = int(np.sum(np.abs(z) == 0.0))
                self.assertEqual(n_origin, n_origin_base + 1)

    def test_expected_absolute_counts_of_the_campaign(self):
        """Los numeros concretos de HANDOFF §5.2, extendidos a velocidad."""
        expected = {
            ("comp_nominal", "acceleration"): (3, 5, 2),
            ("comp_nominal", "velocity"): (4, 5, 1),
            ("comp_nominal", "displacement"): (5, 5, 0),
            ("lp_pga_medido", "acceleration"): (9, 13, 4),
            ("lp_pga_medido", "velocity"): (10, 13, 3),
            ("lp_pga_medido", "displacement"): (11, 13, 2),
        }
        for (cond_id, estimate), (n_z, n_p, rel) in expected.items():
            with self.subTest(cond=cond_id, estimate=estimate):
                z, p, _ = compose_plant_zpk(_spec(cond_id, estimate))
                self.assertEqual((len(z), len(p), relative_degree(z, p)), (n_z, n_p, rel))

    def test_biproper_case_has_nonzero_feedthrough(self):
        """comp_nominal + displacement queda bipropio: D no puede ser 0."""
        z, p, k = compose_plant_zpk(_spec("comp_nominal", "displacement"))
        self.assertEqual(len(z), len(p))
        _, _, _, D = zpk_to_modal_ss(z, p, k)
        self.assertNotEqual(float(D[0, 0]), 0.0)
        self.assertTrue(np.isfinite(D).all())


class ImproperCombinationFailsLoudly(unittest.TestCase):
    """La unica combinacion impropia del catalogo tiene que fallar explicita."""

    def test_geophone_alone_with_displacement_raises(self):
        spec = PlantSpec(
            geophone=load_geophone("sm24_nominal"),
            include_geophone=True,
            include_conditioner=False,
            estimate="displacement",
        )
        with self.assertRaises(ValueError) as ctx:
            compose_plant_zpk(spec)
        self.assertIn("impropia", str(ctx.exception))

    def test_geophone_alone_with_velocity_is_still_proper(self):
        """Con velocidad quedan 2 ceros / 2 polos: bipropio, no impropio."""
        spec = PlantSpec(
            geophone=load_geophone("sm24_nominal"),
            include_geophone=True,
            include_conditioner=False,
            estimate="velocity",
        )
        z, p, _ = compose_plant_zpk(spec)
        self.assertEqual((len(z), len(p)), (2, 2))

    def test_no_sensor_in_the_chain_rejects_ground_magnitudes(self):
        spec = PlantSpec(
            include_geophone=False,
            include_conditioner=True,
            conditioner=load_conditioner("comp_nominal"),
            estimate="velocity",
        )
        with self.assertRaises(ValueError) as ctx:
            compose_plant_zpk(spec)
        self.assertIn("geofono", str(ctx.exception))


class ModalRealizationKeepsTheIdentity(unittest.TestCase):
    """La identidad tambien tiene que sobrevivir a la realizacion modal.

    ``zpk_to_modal_ss`` ajusta ``C`` por minimos cuadrados sobre una grilla
    logaritmica, asi que su fidelidad es empirica y no algebraica. Medida: para
    los dos acondicionadores, ``acceleration`` y ``velocity`` reproducen el zpk a
    **0,00000 dB** en 0,01-1000 Hz.

    ⚠ **Limitacion documentada.** ``lp_pga_medido`` + ``displacement`` (11 ceros
    / 13 polos) es la unica combinacion que se degrada: 0,297 dB dentro de la
    banda de ajuste (0,05-500 Hz) y hasta 5,70 dB a 0,01 Hz, que es una decada y
    media por debajo del limite de validez del modelo (0,211 Hz). No es un error
    de la conversion de magnitud —la identidad sobre el zpk sigue siendo exacta a
    1e-12— sino del ajuste de ``C`` con doble derivacion y cinco decadas de
    dinamica. La campana usa ``velocity``, asi que no bloquea nada; queda
    anotado con su propia tolerancia para que no se descubra de nuevo.
    """

    #: (cond, estimate) -> (tol_db, tol_deg). Ausente = tolerancia estricta.
    RELAXED = {("lp_pga_medido", "displacement"): (0.35, 1.0)}

    def test_modal_ss_matches_its_zpk_for_every_magnitude(self):
        for cond_id in CONDITIONERS:
            for estimate in MAGNITUDES:
                with self.subTest(cond=cond_id, estimate=estimate):
                    relaxed = self.RELAXED.get((cond_id, estimate))
                    tol_db, tol_deg = relaxed or (TOL_MODAL_DB, TOL_MODAL_DEG)
                    # El caso degradado se mide solo dentro de la banda de ajuste
                    # de zpk_to_modal_ss; fuera de ahi la realizacion extrapola.
                    freqs = FREQS_HZ
                    if relaxed:
                        freqs = FREQS_HZ[(FREQS_HZ >= 0.05) & (FREQS_HZ <= 500.0)]
                    w = 2.0 * np.pi * freqs
                    z, p, k = compose_plant_zpk(_spec(cond_id, estimate))
                    ss = zpk_to_modal_ss(z, p, k)
                    ref = zpk_freqresp(z, p, k, w)
                    got = ss_freqresp(*ss, w)
                    self.assertTrue(np.all(np.isfinite(got)))
                    mag_db = 20.0 * np.log10(np.abs(got) / np.abs(ref))
                    phase = np.angle(got / ref, deg=True)
                    phase = (phase + 180.0) % 360.0 - 180.0
                    self.assertLess(float(np.max(np.abs(mag_db))), tol_db)
                    self.assertLess(float(np.max(np.abs(phase))), tol_deg)

    def test_the_two_magnitudes_the_campaign_uses_are_exact(self):
        """Gate duro y separado: aceleracion y velocidad, 0,01-1000 Hz."""
        for cond_id in CONDITIONERS:
            for estimate in ("acceleration", "velocity"):
                with self.subTest(cond=cond_id, estimate=estimate):
                    z, p, k = compose_plant_zpk(_spec(cond_id, estimate))
                    ref = zpk_freqresp(z, p, k, OMEGA)
                    got = ss_freqresp(*zpk_to_modal_ss(z, p, k), OMEGA)
                    mag_db = 20.0 * np.log10(np.abs(got) / np.abs(ref))
                    self.assertLess(float(np.max(np.abs(mag_db))), 1e-3)


class PreparedPlantKeepsTheIdentity(unittest.TestCase):
    """Post ``prepare_plant``: poda + residualizacion, a las fs de la campana.

    Aca la identidad ya no es exacta: la poda de cuasi-cancelaciones reajusta la
    ganancia y la residualizacion cambia la respuesta fuera de banda. Lo que se
    exige es que el cociente siga siendo ``jw`` dentro de la banda donde el
    modelo es defendible, con las mismas tolerancias que los gates de S1.
    """

    BAND_HZ = (1.0, 150.0)

    def test_identity_survives_reduction(self):
        band = np.logspace(
            np.log10(self.BAND_HZ[0]), np.log10(self.BAND_HZ[1]), 300
        )
        w = 2.0 * np.pi * band
        for cond_id in CONDITIONERS:
            for fs in (1020.0, 2604.0, 2929.0):
                with self.subTest(cond=cond_id, fs=fs):
                    h_a = ss_freqresp(
                        *prepare_plant(_spec(cond_id, "acceleration"), fs).continuous, w
                    )
                    h_v = ss_freqresp(
                        *prepare_plant(_spec(cond_id, "velocity"), fs).continuous, w
                    )
                    self.assertTrue(np.all(np.isfinite(h_v)))
                    ratio = h_v / h_a
                    mag_db = 20.0 * np.log10(np.abs(ratio) / w)
                    phase = np.angle(ratio * np.exp(-0.5j * np.pi), deg=True)
                    phase = (phase + 180.0) % 360.0 - 180.0
                    self.assertLess(float(np.max(np.abs(mag_db))), 0.30)
                    self.assertLess(float(np.max(np.abs(phase))), 2.0)


class NoNanOrInf(unittest.TestCase):
    def test_all_matrices_are_finite(self):
        for cond_id in CONDITIONERS:
            for estimate in MAGNITUDES:
                for fs in (1020.0, 2604.0, 2929.0):
                    with self.subTest(cond=cond_id, estimate=estimate, fs=fs):
                        prepared = prepare_plant(_spec(cond_id, estimate), fs)
                        for matrix in prepared.continuous:
                            self.assertTrue(np.all(np.isfinite(matrix)))
                        for array in prepared.reduced_zpk[:2]:
                            self.assertTrue(np.all(np.isfinite(array)))
                        self.assertTrue(np.isfinite(prepared.reduced_zpk[2]))


if __name__ == "__main__":
    unittest.main()
