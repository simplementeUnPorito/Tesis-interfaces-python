"""Mide de forma segura y simétrica la autoridad IDAC3 -> LPo.

No calibra ni usa semillas. Mantiene PGAout en x1, aplica un patrón ABBA
(-probe, +probe, +probe, -probe), registra los cinco taps y restaura x1/cero
incluso si ocurre un error. El patrón permite separar una respuesta física de
una deriva aproximadamente lineal.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session


TARGET_UV = 1_000_000
GUARD_UV = (500_000, 500_000, 1_000_000, 1_000_000, 1_000_000)


def checked(call, what: str) -> None:
    for _ in range(3):
        if call():
            return
        time.sleep(0.3)
    raise RuntimeError(f"sin ACK: {what}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--probe", type=int, default=16)
    ap.add_argument("--pre-dwell", type=float, default=20.0)
    ap.add_argument("--dwell", type=float, default=2.0)
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--period", type=float, default=1.0)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    if not 4 <= args.probe <= 64:
        ap.error("probe debe estar entre 4 y 64 códigos")
    if args.samples < 3:
        ap.error("se requieren al menos tres muestras por punto")

    output = args.output or (
        Path(__file__).resolve().parents[3] / "lab" / "calibracion_nueva" /
        f"autoridad_idac3_abba_x1_{datetime.now():%Y%m%d_%H%M%S}.csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    summary_path = Path(str(output) + ".json")
    rows: list[dict] = []
    fields = ("timestamp", "elapsed_s", "sequence_index", "label", "idac3",
              "channel", "mean_uv", "error_uv", "pp_uv")
    sequence = (("low_forward", -args.probe),
                ("high_forward", args.probe),
                ("high_reverse", args.probe),
                ("low_reverse", -args.probe))

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    started = time.monotonic()
    medians: dict[str, dict[int, int]] = {}
    try:
        checked(lambda: lab.set_adc_config(1), "ADC ±2,5 V")
        checked(lambda: lab.set_gain("pga", GAIN_CODES.index(50)), "PGA x50")
        checked(lambda: lab.set_gain("pgaout", GAIN_CODES.index(1)), "PGAout x1")
        for stage in range(4):
            checked(lambda s=stage: lab.set_idac(s, 0), f"IDAC{stage}=0")
        time.sleep(args.pre_dwell)

        for sequence_index, (label, code) in enumerate(sequence):
            checked(lambda c=code: lab.set_idac(3, c), f"IDAC3={code}")
            time.sleep(args.dwell)
            point_errors: dict[int, list[int]] = {ch: [] for ch in range(5)}
            for sample_index in range(args.samples):
                for channel in range(5):
                    point = None
                    for _ in range(3):
                        point = lab.measure_dc(channel, 3)
                        if point is not None and point.ok:
                            break
                        time.sleep(0.2)
                    if point is None or not point.ok:
                        raise RuntimeError(f"sin medida ch{channel}")
                    error = int(point.mean_uv) - TARGET_UV
                    if abs(error) > GUARD_UV[channel]:
                        raise RuntimeError(
                            f"guarda ch{channel}: {error / 1000:+.1f} mV")
                    point_errors[channel].append(error)
                    rows.append({
                        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                        "elapsed_s": round(time.monotonic() - started, 3),
                        "sequence_index": sequence_index, "label": label,
                        "idac3": code, "channel": channel,
                        "mean_uv": int(point.mean_uv), "error_uv": error,
                        "pp_uv": int(point.pp_uv),
                    })
                if sample_index + 1 < args.samples:
                    time.sleep(args.period)
            medians[label] = {
                ch: int(round(statistics.median(values)))
                for ch, values in point_errors.items()
            }
            with output.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            print(f"{label} IDAC3={code}: " + " ".join(
                f"ch{ch}={medians[label][ch]/1000:+.1f}mV" for ch in range(5)),
                flush=True)

        forward = medians["high_forward"][4] - medians["low_forward"][4]
        reverse = medians["high_reverse"][4] - medians["low_reverse"][4]
        same_sign = (forward != 0 and reverse != 0 and
                     ((forward < 0) == (reverse < 0)))
        ratio = (min(abs(forward), abs(reverse)) /
                 max(abs(forward), abs(reverse))) if forward or reverse else 0.0
        reliable = same_sign and ratio >= 0.50 and min(abs(forward), abs(reverse)) >= 12_000
        summary = {
            "test": "IDAC3 -> LPo ABBA at PGAout x1",
            "probe_codes": args.probe,
            "forward_effect_uv": forward,
            "reverse_effect_uv": reverse,
            "slope_uv_per_code": 0.5 * (forward + reverse) / (2 * args.probe),
            "same_sign": same_sign,
            "magnitude_ratio": ratio,
            "reliable_authority": reliable,
            "medians_uv": medians,
            "restored": "PGAout x1, IDAC0..3=0",
        }
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2), flush=True)
        return 0 if reliable else 2
    finally:
        try:
            for stage in range(4):
                lab.set_idac(stage, 0)
            lab.set_gain("pgaout", GAIN_CODES.index(1))
        finally:
            console.close()


if __name__ == "__main__":
    raise SystemExit(main())
