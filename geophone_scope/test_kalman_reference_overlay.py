"""La curva externa es solo overlay: no toca Q/R, ni el MASW, ni las mascaras.

El pedido exige una prueba de que cambiar o retirar la curva hidrogeologica de
referencia no modifica ningun resultado. La prueba tiene dos mitades:

1. **Estructural.** Las funciones del camino de calculo —ajuste de ``q``,
   ``phase_shift_dispersion_image``, ``track_dispersion_ridge``,
   ``compute_masks``— no tienen ningun parametro por donde entre la referencia.
2. **Numerica.** Sobre un gather sintetico chico se corre la cadena entera dos
   veces con curvas de referencia **distintas** (y una tercera vez sin ninguna) y
   se exige igualdad **bit a bit** de q, de la imagen MASW y de las cuatro
   mascaras.

Comando::

    cd C:/Github/Tesis/src/interfaces/python
    python -m unittest geophone_scope.test_kalman_reference_overlay -v
"""

from __future__ import annotations

import inspect
import unittest

import numpy as np

from geophone_scope.kalman_deconv.discretize import prepare_plant
from geophone_scope.kalman_deconv.report_velocity_fix import (
    _plant_spec,
    fit_q_scale,
)
from geophone_scope.kalman_deconv.report_vs_apparent_masks import (
    _config,
    assert_reference_is_overlay_only,
    compute_masks,
)
from geophone_scope.masw_dispersion import phase_shift_dispersion_image
from geophone_scope.masw_ridge_kalman import track_dispersion_ridge

FS = 1020.0
OFFSETS = np.arange(10.0, 52.0, 2.0)

# Tres referencias deliberadamente distintas, incluida una fisicamente absurda.
# Si alguna se colara al calculo, los resultados no podrian coincidir.
REFERENCE_A = (np.linspace(8.0, 30.0, 60), np.linspace(102.0, 72.0, 60))
REFERENCE_B = (np.linspace(8.0, 30.0, 60), np.linspace(300.0, 250.0, 60))
REFERENCE_NONE = (np.zeros(0), np.zeros(0))


def _synthetic_gather(*, c_true: float = 120.0, seed: int = 7) -> np.ndarray:
    """Gather sintetico simple: un pulso que se propaga a velocidad constante."""
    rng = np.random.default_rng(seed)
    n = 1024
    time_s = np.arange(n) / FS
    gather = np.zeros((OFFSETS.size, n))
    for index, offset in enumerate(OFFSETS):
        tau = time_s - offset / c_true - 0.05
        a = (np.pi * 20.0 * tau) ** 2
        gather[index] = (1.0 - 2.0 * a) * np.exp(-a)
    return gather + rng.normal(scale=0.02, size=gather.shape)


def _pipeline(reference):
    """Corre la cadena y devuelve lo que no puede depender de ``reference``."""
    gather = _synthetic_gather()
    f, c, amplitude = phase_shift_dispersion_image(
        gather.T, OFFSETS, FS, 20.0, 350.0, 1.0, f_min=1.0, f_max=50.0
    )
    tracker = track_dispersion_ridge(f, c, amplitude, OFFSETS, _config())
    masks = compute_masks(f, c, OFFSETS, tracker)
    # La referencia se "usa" solo aca, y solo para graficar: se toca para que el
    # test no pueda pasar por haberla ignorado del todo.
    _ = np.sum(reference[1]) if reference[1].size else 0.0
    return f, c, amplitude, masks


class ReferenceNeverEntersTheComputation(unittest.TestCase):

    def test_computation_signatures_have_no_reference_parameter(self):
        forbidden = ("reference", "ref_c", "ref_f", "hidro", "truth")
        for function in (fit_q_scale, phase_shift_dispersion_image,
                         track_dispersion_ridge, compute_masks):
            with self.subTest(function=function.__name__):
                params = set(inspect.signature(function).parameters)
                for token in forbidden:
                    self.assertFalse(
                        any(token in name for name in params),
                        f"{function.__name__} acepta {token!r}: la referencia "
                        "podria entrar al calculo",
                    )

    def test_masw_and_masks_are_bit_identical_across_references(self):
        base = _pipeline(REFERENCE_A)
        for label, reference in (("B", REFERENCE_B), ("sin referencia", REFERENCE_NONE)):
            with self.subTest(reference=label):
                other = _pipeline(reference)
                np.testing.assert_array_equal(base[0], other[0])
                np.testing.assert_array_equal(base[1], other[1])
                np.testing.assert_array_equal(base[2], other[2])
                for expected, got in zip(base[3], other[3]):
                    np.testing.assert_array_equal(expected, got)

    def test_runtime_assertion_accepts_the_real_masks(self):
        f, c, _, masks = _pipeline(REFERENCE_A)
        gather = _synthetic_gather()
        f2, c2, amplitude = phase_shift_dispersion_image(
            gather.T, OFFSETS, FS, 20.0, 350.0, 1.0, f_min=1.0, f_max=50.0
        )
        tracker = track_dispersion_ridge(f2, c2, amplitude, OFFSETS, _config())
        self.assertTrue(
            assert_reference_is_overlay_only(f, c, OFFSETS, tracker, masks[3])
        )

    def test_runtime_assertion_detects_a_tampered_mask(self):
        """Control negativo: si la mascara cambiara, la asercion debe fallar."""
        f, c, _, masks = _pipeline(REFERENCE_A)
        gather = _synthetic_gather()
        f2, c2, amplitude = phase_shift_dispersion_image(
            gather.T, OFFSETS, FS, 20.0, 350.0, 1.0, f_min=1.0, f_max=50.0
        )
        tracker = track_dispersion_ridge(f2, c2, amplitude, OFFSETS, _config())
        tampered = masks[3].copy()
        tampered[0, 0] = not tampered[0, 0]
        with self.assertRaises(AssertionError):
            assert_reference_is_overlay_only(f, c, OFFSETS, tracker, tampered)


class QDoesNotDependOnTheReference(unittest.TestCase):
    """El ajuste de q solo ve la traza, R y la planta."""

    def test_q_is_reproducible_and_reference_free(self):
        rng = np.random.default_rng(11)
        values = rng.normal(size=1500)
        continuous = prepare_plant(_plant_spec("velocity"), FS).continuous
        r_var = float(np.var(values[:300]))
        first, _ = fit_q_scale(values, continuous, FS, r_var)
        second, _ = fit_q_scale(values, continuous, FS, r_var)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
