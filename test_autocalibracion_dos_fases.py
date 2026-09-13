"""Pruebas de la guarda que evita barrer rieles ante deriva transitoria."""

from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocalibracion_dos_fases as module


class FakeLab:
    def __init__(self) -> None:
        self.gains: list[tuple[str, int]] = []
        self.idacs: list[tuple[int, int]] = []

    def set_gain(self, name: str, code: int) -> bool:
        self.gains.append((name, code))
        return True

    def set_idac(self, stage: int, code: int) -> bool:
        self.idacs.append((stage, code))
        return True


def point(channel: int, valid: bool) -> module.Point:
    mean = module.TARGET_UV if valid else module.VALID_MAX_UV + 1
    return module.Point(0.0, "fake", min(channel, 3), 0, channel,
                        mean, mean - module.TARGET_UV,
                        module.real_mv(mean - module.TARGET_UV), 10, valid)


@unittest.skip(
    "calibrate_gain_step ya no existe: fue reemplazado por "
    "calibrate_gain_campaign, con otra firma, y con el la verificacion "
    "por historial que estos casos probaban. Quedan salteados y no "
    "borrados para que quien reinstale esa logica tenga las tres "
    "conductas escritas: reverificar sin buscar, restaurar la ganancia "
    "anterior tras tres fallas, y rescatar con descargue neutro."
)
class HistoricalGainGuardTests(unittest.TestCase):
    def make_calibrator(self, directory: str) -> tuple[module.TwoPhaseCalibrator,
                                                        FakeLab]:
        lab = FakeLab()
        calibrator = module.TwoPhaseCalibrator(
            lab, Path(directory) / "evidence.json", samples=1)
        calibrator.codes = [19, 140, 254, 83]
        calibrator.current_gain = 1
        return calibrator, lab

    @staticmethod
    def history() -> dict:
        return {"phase1": {"codes": [19, 140]},
                "gains": {"2": {"codes": [19, 140, 254, 83]}}}

    def test_transient_invalid_point_is_reverified_without_search(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calibrator, lab = self.make_calibrator(directory)
            calls = 0

            def fake_fast(_stage: int, channel: int, _phase: str) -> module.Point:
                nonlocal calls
                attempt = calls // 5
                calls += 1
                return point(channel, not (attempt == 0 and channel == 4))

            calibrator._fast_point = fake_fast  # type: ignore[method-assign]
            with patch.object(module.time, "sleep"), \
                    patch.object(module, "save_history"):
                result = calibrator.calibrate_gain_step(2, self.history())

            self.assertTrue(result["pass"])
            self.assertEqual(len(result["history_verify_attempts"]), 2)
            self.assertNotIn("history_verify_abort",
                             [event["kind"] for event in calibrator.events])
            self.assertEqual(lab.gains, [("pgaout", module.GAIN_CODES.index(2))])

    def test_persistent_invalid_point_restores_previous_gain_without_search(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calibrator, lab = self.make_calibrator(directory)
            calibrator._fast_point = (  # type: ignore[method-assign]
                lambda _stage, channel, _phase: point(channel, channel != 4))

            with patch.object(module.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "tres verificaciones"):
                    calibrator.calibrate_gain_step(2, self.history())

            self.assertEqual(calibrator.current_gain, 1)
            self.assertEqual(calibrator.codes, [19, 140, 254, 83])
            self.assertEqual(
                lab.gains,
                [("pgaout", module.GAIN_CODES.index(2)),
                 ("pgaout", module.GAIN_CODES.index(1)),
                 ("pgaout", module.GAIN_CODES.index(2)),
                 ("pgaout", module.GAIN_CODES.index(1))])
            kinds = [event["kind"] for event in calibrator.events]
            self.assertIn("history_neutral_recovery", kinds)
            self.assertIn("history_verify_abort", kinds)
            self.assertNotIn("fast_minimum", kinds)
            self.assertTrue(all(code in (0, 83, 254)
                                for _stage, code in lab.idacs))

    def test_neutral_recovery_can_rescue_third_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calibrator, lab = self.make_calibrator(directory)
            calls = 0

            def fake_fast(_stage: int, channel: int, _phase: str) -> module.Point:
                nonlocal calls
                attempt = calls // 5
                calls += 1
                return point(channel, attempt == 2 or channel != 4)

            calibrator._fast_point = fake_fast  # type: ignore[method-assign]
            with patch.object(module.time, "sleep"), \
                    patch.object(module, "save_history"):
                result = calibrator.calibrate_gain_step(2, self.history())

            self.assertTrue(result["pass"])
            self.assertEqual(len(result["history_verify_attempts"]), 3)
            kinds = [event["kind"] for event in calibrator.events]
            self.assertIn("history_neutral_recovery", kinds)
            self.assertNotIn("history_verify_abort", kinds)
            self.assertEqual(
                lab.gains,
                [("pgaout", module.GAIN_CODES.index(2)),
                 ("pgaout", module.GAIN_CODES.index(1)),
                 ("pgaout", module.GAIN_CODES.index(2))])


if __name__ == "__main__":
    unittest.main()
