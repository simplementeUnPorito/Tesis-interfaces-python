"""Mapea de forma segura el umbral IDAC1 -> SUMo en PGAout x24.

Es un ensayo diagnóstico, no usa ni genera semillas de calibración. Recorre el
mismo intervalo en ambos sentidos para medir pendiente local e histéresis y
restaura x1/IDAC=0 aunque el ensayo falle.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path

from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab, TAP_NAMES
from testbench.core.session import Session


TARGET_UV = 1_000_000
GUARD_UV = (500_000, 500_000, 1_000_000, 1_000_000, 1_000_000)


def checked(call, what: str) -> None:
    for _ in range(3):
        if call():
            return
        time.sleep(0.3)
    raise RuntimeError(f"sin ACK: {what}")


def read_all(lab: Lab) -> list[dict[str, int]]:
    result = []
    for channel in range(5):
        point = None
        for _ in range(3):
            point = lab.measure_dc(channel, 3)
            if point is not None and point.ok:
                break
            time.sleep(0.2)
        if point is None or not point.ok:
            raise RuntimeError(f"sin lectura válida en ch{channel}")
        error = int(point.mean_uv) - TARGET_UV
        if abs(error) > GUARD_UV[channel]:
            raise RuntimeError(
                f"guarda ch{channel} {TAP_NAMES[channel]}: {error/1000:+.1f}mV")
        result.append({"channel": channel, "mean_uv": int(point.mean_uv),
                       "error_uv": error, "pp_uv": int(point.pp_uv)})
    return result


def ramp(lab: Lab, stage: int, start: int, target: int) -> int:
    current = start
    while current != target:
        direction = 1 if target > current else -1
        nxt = current + direction * min(16, abs(target - current))
        checked(lambda n=nxt: lab.set_idac(stage, n), f"IDAC{stage}={nxt}")
        try:
            read_all(lab)
        except Exception:
            lab.set_idac(stage, current)
            raise
        current = nxt
    return current


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--lo", type=int, default=-86)
    ap.add_argument("--hi", type=int, default=-70)
    ap.add_argument("--dwell", type=float, default=15.0)
    ap.add_argument("--hold-code", type=int,
                    help="en vez del ida/vuelta, aproxima desde un código mayor y lo mantiene")
    ap.add_argument("--hold-duration", type=float, default=70.0)
    ap.add_argument("--hold-period", type=float, default=10.0)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    if not (-120 <= args.lo < args.hi <= 0):
        ap.error("se exige -120 <= lo < hi <= 0")

    output = args.output or (Path(__file__).resolve().parents[3] / "lab" /
        "calibracion_nueva" /
        f"umbral_idac1_x24_{datetime.now():%Y%m%d_%H%M%S}.csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    json_path = Path(str(output) + ".json")
    log_path = Path(str(output) + ".log")
    fields = ("timestamp", "elapsed_s", "direction", "idac1", "channel",
              "tap", "mean_uv", "error_uv", "pp_uv")
    rows: list[dict] = []
    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    started = time.monotonic()
    current = 0

    def persist() -> None:
        with output.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        json_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    try:
        checked(lambda: lab.set_adc_config(1), "ADC ±2,5 V")
        checked(lambda: lab.set_gain("pga", GAIN_CODES.index(50)), "PGA x50")
        checked(lambda: lab.set_gain("pgaout", GAIN_CODES.index(1)), "PGAout x1")
        for stage in range(4):
            checked(lambda s=stage: lab.set_idac(s, 0), f"IDAC{stage}=0")
        time.sleep(30.0)
        checked(lambda: lab.set_gain("pgaout", GAIN_CODES.index(24)),
                "PGAout x24")
        checked(lambda: lab.set_idac(0, 3), "IDAC0=3")

        if args.hold_code is None:
            sequences = (("descendente", range(args.hi, args.lo - 1, -1)),
                         ("ascendente", range(args.lo, args.hi + 1)))
        else:
            if not args.lo <= args.hold_code <= args.hi:
                ap.error("hold-code debe quedar entre lo y hi")
            approach = min(args.hi, args.hold_code + 1)
            sequences = (("aproximacion", range(approach,
                                                args.hold_code - 1, -1)),)
        for direction, codes in sequences:
            for code in codes:
                current = ramp(lab, 1, current, code)
                time.sleep(args.dwell)
                points = read_all(lab)
                stamp = datetime.now().isoformat(timespec="milliseconds")
                elapsed = round(time.monotonic() - started, 3)
                for point in points:
                    rows.append({"timestamp": stamp, "elapsed_s": elapsed,
                                 "direction": direction, "idac1": code,
                                 "channel": point["channel"],
                                 "tap": TAP_NAMES[point["channel"]],
                                 "mean_uv": point["mean_uv"],
                                 "error_uv": point["error_uv"],
                                 "pp_uv": point["pp_uv"]})
                persist()
                print(f"{direction:11s} IDAC1={code:4d}: " + " ".join(
                    f"ch{p['channel']}={p['error_uv']/1000:+.1f}mV"
                    for p in points), flush=True)
        if args.hold_code is not None:
            hold_started = time.monotonic()
            while time.monotonic() - hold_started < args.hold_duration:
                time.sleep(min(args.hold_period,
                               args.hold_duration - (time.monotonic() - hold_started)))
                points = read_all(lab)
                stamp = datetime.now().isoformat(timespec="milliseconds")
                elapsed = round(time.monotonic() - started, 3)
                for point in points:
                    rows.append({"timestamp": stamp, "elapsed_s": elapsed,
                                 "direction": "hold", "idac1": args.hold_code,
                                 "channel": point["channel"],
                                 "tap": TAP_NAMES[point["channel"]],
                                 "mean_uv": point["mean_uv"],
                                 "error_uv": point["error_uv"],
                                 "pp_uv": point["pp_uv"]})
                persist()
                print(f"hold        IDAC1={args.hold_code:4d}: " + " ".join(
                    f"ch{p['channel']}={p['error_uv']/1000:+.1f}mV"
                    for p in points), flush=True)
    finally:
        try:
            lab.set_idac(1, 0)
            lab.set_idac(0, 0)
            lab.set_idac(2, 0)
            lab.set_idac(3, 0)
            lab.set_gain("pgaout", GAIN_CODES.index(1))
        finally:
            persist()
            log_path.write_text(console.transcript.text(), encoding="utf-8")
            console.close()
    print(f"Evidencia: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
