"""Regresiones pequenas del tracker; no requieren los datos de campana."""

from __future__ import annotations

import unittest

import numpy as np

from geophone_scope.masw_ridge_kalman import RidgeKalmanConfig, track_dispersion_ridge


class TestMaswRidgeKalman(unittest.TestCase):
    def test_tracker_rejects_spurious_high_velocity_mode(self):
        rng = np.random.default_rng(2909)
        frequency = np.linspace(8.0, 30.0, 90)
        velocity = np.arange(50.0, 301.0)
        truth = 100.0 - 0.8 * (frequency - 8.0) + 4.0 * np.sin(0.45 * frequency)
        image = np.exp(-0.5 * ((velocity[None, :] - truth[:, None]) / 3.0) ** 2)
        # Un modo espurio mas energetico durante varios intervalos hace saltar
        # al argmax global, pero esta fuera de la compuerta predictiva.
        false_mode = 220.0 + 0.5 * frequency
        bursts = ((frequency > 12.0) & (frequency < 15.0)) | (frequency > 23.0)
        image += bursts[:, None] * 1.4 * np.exp(
            -0.5 * ((velocity[None, :] - false_mode[:, None]) / 4.0) ** 2
        )
        image += 0.03 * rng.random(image.shape)
        offsets = np.arange(10.0, 52.0, 2.0)

        argmax = velocity[np.argmax(image, axis=1)]
        tracked = track_dispersion_ridge(
            frequency,
            velocity,
            image,
            offsets,
            RidgeKalmanConfig(
                seed_c_max_m_s=160.0,
                gate_m_s=20.0,
                max_wavelength_aperture=1.0,
                enforce_normal_dispersion=False,
            ),
        )
        truth_at_pick = np.interp(tracked.frequency_hz, frequency, truth)
        argmax_at_pick = np.interp(tracked.frequency_hz, frequency, argmax)
        rmse_argmax = float(np.sqrt(np.mean((argmax_at_pick - truth_at_pick) ** 2)))
        rmse_tracker = float(np.sqrt(np.mean(
            (tracked.smoothed_velocity_m_s - truth_at_pick) ** 2
        )))
        self.assertLess(rmse_tracker, 0.25 * rmse_argmax)
        self.assertTrue(np.all(np.isfinite(tracked.smoothed_velocity_m_s)))

    def test_physical_gates_are_respected(self):
        frequency = np.linspace(8.0, 30.0, 60)
        velocity = np.arange(20.0, 401.0)
        offsets = np.arange(10.0, 52.0, 2.0)
        ridge = 90.0 - 0.2 * frequency
        image = np.exp(-0.5 * ((velocity[None, :] - ridge[:, None]) / 2.0) ** 2)
        result = track_dispersion_ridge(frequency, velocity, image, offsets)
        dx = float(np.median(np.diff(offsets)))
        aperture = float(offsets[-1] - offsets[0])
        self.assertTrue(np.all(result.measured_velocity_m_s >= 2.0 * dx * result.frequency_hz))
        self.assertTrue(np.all(result.measured_velocity_m_s <= aperture * result.frequency_hz))

    def test_descending_direction_starts_at_high_frequency(self):
        frequency = np.linspace(1.0, 50.0, 100)
        velocity = np.arange(20.0, 201.0)
        offsets = np.arange(10.0, 52.0, 2.0)
        ridge = 75.0 + 0.3 * frequency
        image = np.exp(-0.5 * ((velocity[None, :] - ridge[:, None]) / 2.5) ** 2)
        result = track_dispersion_ridge(
            frequency, velocity, image, offsets,
            RidgeKalmanConfig(
                f_min_hz=1.0, f_max_hz=50.0, direction="descending",
                seed_c_min_m_s=50.0, seed_c_max_m_s=160.0,
                enforce_physical_mask=False,
            ),
        )
        self.assertGreater(result.frequency_hz[0], result.frequency_hz[-1])
        self.assertAlmostEqual(result.frequency_hz[0], 50.0)
        self.assertAlmostEqual(result.frequency_hz[-1], 1.0)
        self.assertTrue(np.all(np.isfinite(result.smoothed_velocity_m_s)))


if __name__ == "__main__":
    unittest.main()
