"""Tests de contrato numérico para la inversión MASW.

Se ejecutan con ``python -m unittest geophone_scope.test_masw_inversion_correctness``.
"""

from __future__ import annotations

import unittest
from unittest import mock

import numpy as np

from geophone_scope import masw_inversion
from geophone_scope.masw_inversion import dispersion_misfit


class DispersionMisfitValidityTests(unittest.TestCase):
    @staticmethod
    def _misfit(total: int, valid: int) -> float:
        observed = np.full(total, 200.0)
        theoretical = np.full(total, np.nan)
        theoretical[:valid] = 200.0
        return dispersion_misfit(observed, theoretical)

    def test_odd_sample_count_requires_ceiling_half(self) -> None:
        cases = (
            (3, 1, False),
            (3, 2, True),
            (5, 2, False),
            (5, 3, True),
        )
        for total, valid, accepted in cases:
            with self.subTest(total=total, valid=valid):
                result = self._misfit(total, valid)
                self.assertEqual(np.isfinite(result), accepted)

    def test_even_sample_count_accepts_exactly_half(self) -> None:
        cases = (
            (4, 1, False),
            (4, 2, True),
            (6, 2, False),
            (6, 3, True),
        )
        for total, valid, accepted in cases:
            with self.subTest(total=total, valid=valid):
                result = self._misfit(total, valid)
                self.assertEqual(np.isfinite(result), accepted)


class _DeterministicRng:
    def __init__(self, beta_delta: np.ndarray):
        self.beta_delta = np.asarray(beta_delta, dtype=np.float64)

    def uniform(self, low, high):
        low = np.asarray(low)
        if low.size == self.beta_delta.size:
            return self.beta_delta.copy()
        return np.zeros_like(low, dtype=np.float64)


class MonteCarloReversalTests(unittest.TestCase):
    def _run_forced_fallback(
        self,
        beta_initial: np.ndarray,
        proposal: np.ndarray,
        reversals: int,
    ) -> np.ndarray:
        beta_initial = np.asarray(beta_initial, dtype=np.float64)
        proposal = np.asarray(proposal, dtype=np.float64)
        c_obs = np.array([200.0, 200.0, 200.0])

        def fake_forward(_c_test, wavelengths, _alpha, _beta, _rho, _h, delta_c=5.0):
            return np.full_like(wavelengths, 200.0)

        fake_rng = _DeterministicRng(proposal - beta_initial)
        with (
            mock.patch.object(masw_inversion.np.random, "default_rng", return_value=fake_rng),
            mock.patch.object(masw_inversion, "theoretical_dispersion_curve", side_effect=fake_forward),
        ):
            result = masw_inversion.monte_carlo_inversion(
                freqs=np.array([10.0, 20.0, 30.0]),
                c_obs=c_obs,
                n_iterations=1,
                bs=100.0,
                bh=0.0,
                reversals=reversals,
                beta_initial=beta_initial,
                h_initial=np.ones(beta_initial.size - 1),
                seed=7,
            )
        return result["beta"]

    def test_fallback_preserves_requested_velocity_reversals(self) -> None:
        cases = (
            # Monotónico: ordenar todo es el contrato pedido.
            (
                0,
                np.array([200.0, 300.0, 400.0, 500.0]),
                np.array([400.0, 200.0, 500.0, 300.0]),
                np.array([200.0, 300.0, 400.0, 500.0]),
            ),
            # Una inversión permitida: sólo el sufijo desde índice 1 se ordena.
            (
                1,
                np.array([300.0, 200.0, 400.0, 500.0]),
                np.array([300.0, 400.0, 200.0, 500.0]),
                np.array([300.0, 200.0, 400.0, 500.0]),
            ),
            # Dos inversiones permitidas: el prefijo [500, 300] debe sobrevivir.
            (
                2,
                np.array([500.0, 300.0, 200.0, 400.0]),
                np.array([500.0, 300.0, 400.0, 200.0]),
                np.array([500.0, 300.0, 200.0, 400.0]),
            ),
        )
        for reversals, initial, proposal, expected in cases:
            with self.subTest(reversals=reversals):
                actual = self._run_forced_fallback(initial, proposal, reversals)
                np.testing.assert_allclose(actual, expected)


class MonteCarloCancellationTests(unittest.TestCase):
    @staticmethod
    def _run(callback, n_iterations: int = 3) -> tuple[dict, int]:
        forward_calls = 0

        def fake_forward(_c_test, wavelengths, _alpha, _beta, _rho, _h, delta_c=5.0):
            nonlocal forward_calls
            forward_calls += 1
            return np.full_like(wavelengths, 200.0)

        with mock.patch.object(
            masw_inversion,
            "theoretical_dispersion_curve",
            side_effect=fake_forward,
        ):
            result = masw_inversion.monte_carlo_inversion(
                freqs=np.array([10.0, 20.0, 30.0]),
                c_obs=np.array([200.0, 200.0, 200.0]),
                n_iterations=n_iterations,
                bs=0.0,
                bh=0.0,
                beta_initial=np.array([200.0, 250.0, 300.0, 350.0]),
                h_initial=np.ones(3),
                seed=11,
                progress_cb=callback,
            )
        return result, forward_calls

    def test_cancellation_before_first_iteration(self) -> None:
        calls = []

        def cancel(iteration, *_args):
            calls.append(iteration)
            return False

        result, forward_calls = self._run(cancel)
        self.assertEqual(calls, [0])
        self.assertEqual(forward_calls, 1)  # sólo modelo inicial
        self.assertEqual(result["history"].size, 0)

    def test_cancellation_during_execution(self) -> None:
        calls = []

        def cancel_after_first(iteration, *_args):
            calls.append(iteration)
            return iteration != 1

        result, forward_calls = self._run(cancel_after_first)
        self.assertEqual(calls, [0, 1])
        self.assertEqual(forward_calls, 2)  # inicial + una propuesta
        self.assertEqual(result["history"].size, 1)

    def test_callback_at_normal_completion(self) -> None:
        calls = []

        def observe(iteration, *_args):
            calls.append(iteration)
            return True

        result, forward_calls = self._run(observe, n_iterations=3)
        self.assertEqual(calls, [0, 1, 3])
        self.assertEqual(forward_calls, 4)
        self.assertEqual(result["history"].size, 3)

if __name__ == "__main__":
    unittest.main()
