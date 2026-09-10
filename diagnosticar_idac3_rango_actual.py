"""Barrido amplio y reversible de IDAC3 en el punto actual, siempre en x1."""

from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime
from pathlib import Path

from escala_banco import FACTOR
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab, TAP_NAMES
from testbench.core.session import Session

SAFE = (19, 140, 254, 83)
PATH = (83, 0, -128, -255, -128, 0, 83, 128, 255, 128, 83)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--cycles", type=int, default=2)
    ap.add_argument("--dwell", type=float, default=5.0)
    ap.add_argument("--pre-dwell", type=float, default=15.0)
    ap.add_argument("--path", default=",".join(str(code) for code in PATH))
    args = ap.parse_args()
    path = tuple(int(value) for value in args.path.split(","))
    if not path or any(code < -255 or code > 255 for code in path):
        ap.error("path contiene códigos fuera de -255..255")
    out = (Path(__file__).resolve().parents[3] / "lab" / "calibracion_nueva" /
           f"idac3_rango_actual_{datetime.now():%Y%m%d_%H%M%S}.csv")
    rows = []
    started = time.monotonic()
    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))

    def checked(call, what):
        for _ in range(4):
            if call():
                return
            time.sleep(0.3)
        raise RuntimeError(f"sin ACK: {what}")

    def set_safe():
        checked(lambda: lab.set_gain("pgaout", GAIN_CODES.index(1)), "x1")
        for stage, code in enumerate(SAFE):
            checked(lambda s=stage, c=code: lab.set_idac(s, c),
                    f"IDAC{stage}={code}")

    def read(ch):
        for _ in range(4):
            point = lab.measure_dc(ch, 3)
            if point is not None:
                return point
            time.sleep(0.3)
        raise RuntimeError(f"sin lectura ch{ch}")

    try:
        checked(lambda: lab.set_adc_config(1), "ADC +/-2.5 V")
        checked(lambda: lab.set_gain("pga", GAIN_CODES.index(50)), "PGA x50")
        set_safe()
        time.sleep(args.pre_dwell)
        for cycle in range(1, args.cycles + 1):
            codes = path if cycle % 2 else tuple(reversed(path))
            for index, code in enumerate(codes):
                checked(lambda c=code: lab.set_idac(3, c), f"IDAC3={code}")
                time.sleep(args.dwell)
                summary = []
                for ch in (2, 3, 4):
                    point = read(ch)
                    rows.append({
                        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                        "elapsed_s": round(time.monotonic() - started, 3),
                        "cycle": cycle, "index": index, "idac3": code,
                        "channel": ch, "tap": TAP_NAMES[ch],
                        "bank_uv": point.mean_uv,
                        "physical_v": round(
                            (FACTOR * point.mean_uv / 1000.0 - 17533.5) / 1000.0,
                            5),
                        "pp_bank_uv": point.pp_uv, "adc_ok": int(point.ok),
                    })
                    summary.append(f"ch{ch}={point.mean_uv/1000:.2f}")
                with out.open("w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows(rows)
                print(f"ciclo {cycle} IDAC3={code:+4d} " + " ".join(summary),
                      flush=True)
        print(f"Evidencia: {out}", flush=True)
        return 0
    finally:
        try:
            set_safe()
        finally:
            console.close()


if __name__ == "__main__":
    raise SystemExit(main())
