"""Diagnóstico corto y seguro de autoridad IDAC2 -> SUMo, con CSV inmediato."""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path

from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab
from testbench.core.session import Session

TARGET_UV = 1_000_000
GUARD_UV = (500_000, 500_000, 1_000_000, 1_000_000, 1_000_000)


def checked(call, what: str) -> None:
    if not call():
        raise RuntimeError(f"sin ACK: {what}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--codes", default="0,16,24,32,40,48")
    ap.add_argument("--dwell", type=float, default=3.0)
    ap.add_argument("--samples-per-code", type=int, default=1)
    ap.add_argument("--period", type=float, default=5.0)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    codes = [int(value) for value in args.codes.split(",")]
    if any(not 0 <= code <= 48 for code in codes):
        ap.error("IDAC2 limitado a 0..48 por seguridad")
    output = args.output or (Path(__file__).resolve().parents[3] / "lab" /
        "calibracion_nueva" / f"idac2_umbral_x24_{datetime.now():%Y%m%d_%H%M%S}.csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    json_path = Path(str(output) + ".json")
    rows: list[dict] = []
    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    fields = ("timestamp", "elapsed_s", "pga", "pgaout", "idac0", "idac1",
              "idac2", "idac3", "channel", "mean_uv", "error_uv", "pp_uv")
    started = time.monotonic()
    try:
        checked(lambda: lab.set_adc_config(1), "ADC ±2,5 V")
        checked(lambda: lab.set_gain("pga", GAIN_CODES.index(50)), "PGA x50")
        checked(lambda: lab.set_gain("pgaout", GAIN_CODES.index(24)), "PGAout x24")
        for stage in range(4):
            checked(lambda s=stage: lab.set_idac(s, 0), f"IDAC{stage}=0")
        for code in codes:
            checked(lambda c=code: lab.set_idac(2, c), f"IDAC2={code}")
            time.sleep(args.dwell)
            for sample_index in range(args.samples_per_code):
                for channel in range(5):
                    point = lab.measure_dc(channel, 3)
                    if point is None or not point.ok:
                        raise RuntimeError(f"sin medida ch{channel}")
                    error = point.mean_uv - TARGET_UV
                    if abs(error) > GUARD_UV[channel]:
                        raise RuntimeError(f"guarda ch{channel}: {error} uV")
                    row = {"timestamp": datetime.now().isoformat(timespec="milliseconds"),
                           "elapsed_s": round(time.monotonic() - started, 3),
                           "pga": 50, "pgaout": 24, "idac0": 0, "idac1": 0,
                           "idac2": code, "idac3": 0, "channel": channel,
                           "mean_uv": point.mean_uv, "error_uv": error,
                           "pp_uv": point.pp_uv}
                    rows.append(row)
                    with output.open("w", newline="", encoding="utf-8-sig") as handle:
                        writer = csv.DictWriter(handle, fieldnames=fields)
                        writer.writeheader()
                        writer.writerows(rows)
                    json_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
                print(f"IDAC2={code} muestra {sample_index + 1}: " + " ".join(
                    f"ch{row['channel']}={row['error_uv']/1000:+.1f}mV"
                    for row in rows[-5:]), flush=True)
                if sample_index + 1 < args.samples_per_code:
                    time.sleep(args.period)
    finally:
        try:
            lab.set_idac(2, 0)
            lab.set_gain("pgaout", GAIN_CODES.index(1))
        finally:
            console.close()
    print(f"Evidencia: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
