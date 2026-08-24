"""Regression test for the shared field-review filter-chain order."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np

import field_review_data
from field_review_data import FilterSettings, apply_filter_chain


def test_harmonic_subtraction_runs_before_zero_phase_filter() -> None:
    calls: list[str] = []

    def fake_harmonic_notch(x, fs, f0, n_harmonics, search_hz=2.0):
        calls.append("harmonic")
        return np.asarray(x, dtype=float) + 1.0

    def fake_bandpass(x, fs, low_hz, high_hz, order):
        calls.append("filtfilt")
        return np.asarray(x, dtype=float) * 2.0

    settings = FilterSettings(
        enabled=True,
        high_hz=100.0,
        order=4,
        line_suppress_enabled=True,
        line_f0_hz=50.0,
        line_harmonics=3,
        line_search_hz=2.0,
    )

    with patch("signal_proc.harmonic_notch", fake_harmonic_notch), patch.object(
        field_review_data, "apply_bandpass_filter", fake_bandpass
    ):
        result = apply_filter_chain(np.array([1.0, 2.0, 3.0]), 1020.0, settings)

    assert calls == ["harmonic", "filtfilt"]
    np.testing.assert_allclose(result, np.array([4.0, 6.0, 8.0], dtype=np.float32))
