"""Mide autoridad local de IDAC2 e IDAC3 en el punto de trabajo actual.

El ensayo mantiene PGAout en x1 y usa ciclos ABBA repetidos para separar el
efecto de cada IDAC de la deriva lenta. No escribe EEPROM. Al terminar, incluso
ante error, restaura [19, 140, 254, 83] y PGAout x1.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

from escala_banco import BANCO_MAX_VALIDO_MV, BANCO_MIN_VALIDO_MV, FACTOR
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab, TAP_NAMES
from testbench.core.session import Session


SAFE_CODES = (19, 140, 254, 83)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--neutral-dwell", type=float, default=30.0)
    ap.add_argument("--upstream-dwell", type=float, default=45.0)
    ap.add_argument("--phase2-dwell", type=float, default=20.0)
    ap.add_argument("--point-dwell", type=float, default=5.0)
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--actuators", default="idac3,idac2",
                    help="lista: idac1,idac2,idac3")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    if args.cycles < 2 or args.samples < 3:
        ap.error("se requieren al menos 2 ciclos y 3 muestras por punto")
    requested = {value.strip().lower()
                 for value in args.actuators.split(",") if value.strip()}
    if not requested or not requested <= {"idac1", "idac2", "idac3"}:
        ap.error("--actuators admite idac1,idac2,idac3")

    output = args.output or (Path(__file__).resolve().parents[3] / "lab" /
        "calibracion_nueva" /
        f"autoridad_local_actual_{datetime.now():%Y%m%d_%H%M%S}.csv")
    summary_path = Path(str(output) + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = ("timestamp", "elapsed_s", "actuator", "cycle", "position",
              "code", "channel", "tap", "bank_uv", "physical_error_mv",
              "pp_bank_uv", "valid")
    rows: list[dict] = []
    started = time.monotonic()

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))

    def checked(call, what: str) -> None:
        for _ in range(4):
            if call():
                return
            time.sleep(0.35)
        raise RuntimeError(f"sin ACK: {what}")

    def set_codes(codes) -> None:
        for stage, code in enumerate(codes):
            checked(lambda s=stage, c=code: lab.set_idac(s, c),
                    f"IDAC{stage}={code}")

    def sample(actuator: str, cycle: int, position: str, code: int) -> dict:
        medians = {}
        for channel in range(5):
            values = []
            for _ in range(args.samples):
                point = None
                for attempt in range(4):
                    point = lab.measure_dc(channel, 3)
                    if point is not None:
                        break
                    print(f"reintento lectura ch{channel} {attempt + 1}/4",
                          flush=True)
                    time.sleep(0.30)
                if point is None:
                    raise RuntimeError(
                        f"sin medida ch{channel} tras cuatro intentos")
                values.append(point.mean_uv)
                rows.append({
                    "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                    "elapsed_s": round(time.monotonic() - started, 3),
                    "actuator": actuator, "cycle": cycle,
                    "position": position, "code": code,
                    "channel": channel, "tap": TAP_NAMES[channel],
                    "bank_uv": point.mean_uv,
                    "physical_error_mv": round(
                        (point.mean_uv - 1_001_500) * FACTOR / 1000.0, 3),
                    "pp_bank_uv": point.pp_uv,
                    "valid": int(point.ok and
                        BANCO_MIN_VALIDO_MV * 1000 <= point.mean_uv <=
                        BANCO_MAX_VALIDO_MV * 1000),
                })
            medians[channel] = statistics.median(values)
        with output.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        print(f"{actuator} ciclo {cycle} {position} code={code}: " +
              " ".join(f"ch{ch}={medians[ch]/1000:.2f}" for ch in range(5)),
              flush=True)
        return medians

    results = {}
    try:
        checked(lambda: lab.set_adc_config(1), "ADC +/-2.5 V")
        checked(lambda: lab.set_gain("pga", GAIN_CODES.index(50)), "PGA x50")
        checked(lambda: lab.set_gain("pgaout", GAIN_CODES.index(1)),
                "PGAout x1")
        set_codes((0, 0, 0, 0))
        time.sleep(args.neutral_dwell)
        set_codes((SAFE_CODES[0], SAFE_CODES[1], 0, 0))
        time.sleep(args.upstream_dwell)
        set_codes(SAFE_CODES)
        time.sleep(args.phase2_dwell)

        tests = tuple(test for test in (
            ("IDAC1", 1, SAFE_CODES[1], SAFE_CODES[1] + 48),
            ("IDAC3", 3, SAFE_CODES[3] - 16, SAFE_CODES[3] + 16),
            ("IDAC2", 2, SAFE_CODES[2] - 16, SAFE_CODES[2]),
        ) if test[0].lower() in requested)
        for name, stage, low, high in tests:
            effects = {ch: [] for ch in range(5)}
            delta = high - low
            for cycle in range(1, args.cycles + 1):
                points = []
                for position, code in (("A1", low), ("B1", high),
                                       ("B2", high), ("A2", low)):
                    checked(lambda s=stage, c=code: lab.set_idac(s, c),
                            f"{name}={code}")
                    time.sleep(args.point_dwell)
                    points.append(sample(name, cycle, position, code))
                for channel in range(5):
                    effects[channel].extend((
                        (points[1][channel] - points[0][channel]) / delta,
                        (points[2][channel] - points[3][channel]) / delta,
                    ))
            results[name] = {
                "low_code": low, "high_code": high, "delta_codes": delta,
                "channels": {
                    str(ch): {
                        "tap": TAP_NAMES[ch],
                        "slopes_bank_uv_per_code": effects[ch],
                        "median_bank_uv_per_code": statistics.median(effects[ch]),
                        "mean_bank_uv_per_code": statistics.fmean(effects[ch]),
                        "stdev_bank_uv_per_code": statistics.pstdev(effects[ch]),
                        "median_physical_mv_per_code":
                            statistics.median(effects[ch]) * FACTOR / 1000.0,
                    } for ch in range(5)
                },
            }
            checked(lambda s=stage, c=SAFE_CODES[stage]: lab.set_idac(s, c),
                    f"restaurar {name}")
            time.sleep(args.point_dwell)

        payload = {
            "test": "local repeated ABBA at current safe point, PGAout x1",
            "safe_codes": SAFE_CODES, "cycles": args.cycles,
            "factor_physical_per_bank": FACTOR, "results": results,
            "restored": "PGAout x1 and [19,140,254,83]",
        }
        summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload, indent=2), flush=True)
        return 0
    finally:
        try:
            lab.set_gain("pgaout", GAIN_CODES.index(1))
            set_codes(SAFE_CODES)
        finally:
            console.close()


if __name__ == "__main__":
    raise SystemExit(main())
