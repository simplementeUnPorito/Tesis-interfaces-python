"""Escalon local seguro de un IDAC y registro temporal de los cinco taps.

Se usa para identificar una columna del Jacobiano sin asumir pendientes ni
confundir el transitorio lento con autoridad continua. Siempre restaura el
codigo de origen en ``finally`` y aborta si cualquier tap entra en la zona no
calibrada del ADC.
"""

from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime
from pathlib import Path

from escala_banco import (BANCO_MAX_VALIDO_MV, BANCO_MIN_VALIDO_MV,
                          BANCO_VREF_MV, FACTOR, OFFSET_MV)
from testbench.core import console as con
from testbench.core.lab import Lab, TAP_NAMES
from testbench.core.session import Session


def acquire(lab: Lab) -> list[dict]:
    rows = []
    for channel in range(5):
        point = None
        for _ in range(3):
            point = lab.measure_dc(channel, 3)
            if point is not None and point.ok:
                break
            time.sleep(0.25)
        if point is None or not point.ok:
            raise RuntimeError(f"sin lectura valida en ch{channel}")
        bank_mv = point.mean_uv / 1000.0
        rows.append({
            "channel": channel,
            "tap": TAP_NAMES[channel],
            "bank_mv": bank_mv,
            "real_v": (FACTOR * bank_mv + OFFSET_MV) / 1000.0,
            "error_real_mv": FACTOR * (bank_mv - BANCO_VREF_MV),
            "pp_real_mv": FACTOR * point.pp_uv / 1000.0,
            "valid": BANCO_MIN_VALIDO_MV <= bank_mv <= BANCO_MAX_VALIDO_MV,
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=int, choices=range(4))
    parser.add_argument("delta", type=int)
    parser.add_argument("--origin", type=int, default=0)
    parser.add_argument("--duration", type=float, default=75.0)
    parser.add_argument("--period", type=float, default=5.0)
    parser.add_argument("--port", default="COM8")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    target = args.origin + args.delta
    if not -255 <= target <= 255:
        parser.error("codigo objetivo fuera de -255..255")
    output = args.output or (Path(__file__).resolve().parents[3] / "lab" /
                             "calibracion_nueva" /
                             f"step_idac{args.stage}_{args.delta:+d}_"
                             f"{datetime.now():%Y%m%d_%H%M%S}.csv")
    output.parent.mkdir(parents=True, exist_ok=True)

    console = con.Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    evidence: list[dict] = []
    started = time.monotonic()
    try:
        if not lab.set_idac(args.stage, args.origin):
            raise RuntimeError("sin ACK al fijar origen")
        for phase, code, duration in (("base", args.origin, 15.0),
                                      ("step", target, args.duration),
                                      ("restore", args.origin, args.duration)):
            if not lab.set_idac(args.stage, code):
                raise RuntimeError(f"sin ACK IDAC{args.stage}={code}")
            phase_started = time.monotonic()
            while time.monotonic() - phase_started <= duration:
                sample = acquire(lab)
                elapsed = time.monotonic() - started
                for item in sample:
                    evidence.append({"elapsed_s": round(elapsed, 3),
                                     "phase": phase, "stage": args.stage,
                                     "code": code, **item})
                summary = " ".join(
                    f"ch{x['channel']}={x['error_real_mv']:+.0f}mV"
                    for x in sample)
                print(f"{elapsed:7.1f}s {phase:7s} code={code:+d} {summary}",
                      flush=True)
                if not all(item["valid"] for item in sample):
                    raise RuntimeError("tap fuera del rango fisico del ADC")
                remaining = args.period - (time.monotonic() - phase_started) % args.period
                time.sleep(min(max(0.0, remaining), args.period))
    finally:
        lab.set_idac(args.stage, args.origin)
        console.close()
        if evidence:
            with output.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=evidence[0].keys())
                writer.writeheader()
                writer.writerows(evidence)
            print(f"Evidencia: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
