"""Caracteriza IDAC2 -> OPA_SUM/SUM/LP sin conectar el capacitor del AMux.

El ensayo es deliberadamente conservador: usa PGAout x1, limita IDAC2 a
+/-32, registra cada lectura inmediatamente y siempre vuelve a codigo cero.
Los taps pueden estar en un riel al comenzar; eso se registra como ``usable=0``
pero no se confunde con una medida valida para ajustar un controlador.
"""

from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime
from pathlib import Path

from escala_banco import BANCO_MAX_VALIDO_MV, BANCO_MIN_VALIDO_MV
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab, TAP_NAMES
from testbench.core.session import Session

ADC_ABSOLUTE_MV = (700.0, 1150.0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--codes", default="0,9,10,9,10,0")
    ap.add_argument("--pre-dwell", type=float, default=15.0)
    ap.add_argument("--dwell", type=float, default=60.0)
    ap.add_argument("--period", type=float, default=2.0)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    codes = [int(value) for value in args.codes.split(",")]
    if any(not -32 <= code <= 32 for code in codes):
        ap.error("por seguridad este ensayo limita IDAC2 a +/-32")

    output = args.output or (Path(__file__).resolve().parents[3] / "lab" /
        "calibracion_nueva" /
        f"idac2_sin_carga_{datetime.now():%Y%m%d_%H%M%S}.csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = ("timestamp", "elapsed_s", "phase", "idac2", "channel", "tap",
              "bank_mv", "pp_bank_mv", "adc_ok", "usable")
    rows: list[dict] = []
    started = time.monotonic()

    def save() -> None:
        with output.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    try:
        if not lab.set_adc_config(1):
            raise RuntimeError("sin ACK al seleccionar ADC +/-2.5 V")
        if not lab.set_gain("pga", GAIN_CODES.index(50)):
            raise RuntimeError("sin ACK PGA x50")
        if not lab.set_gain("pgaout", GAIN_CODES.index(1)):
            raise RuntimeError("sin ACK PGAout x1")
        for stage in range(4):
            if not lab.set_idac(stage, 0):
                raise RuntimeError(f"sin ACK IDAC{stage}=0")
        time.sleep(args.pre_dwell)

        for phase, code in enumerate(codes):
            if not lab.set_idac(2, code):
                raise RuntimeError(f"sin ACK IDAC2={code}")
            phase_start = time.monotonic()
            while time.monotonic() - phase_start < args.dwell:
                summary = []
                for channel in (2, 3, 4):
                    point = lab.measure_dc(channel, 1)
                    if point is None:
                        raise RuntimeError(f"sin respuesta ch{channel}")
                    bank_mv = point.mean_mv
                    if not ADC_ABSOLUTE_MV[0] <= bank_mv <= ADC_ABSOLUTE_MV[1]:
                        raise RuntimeError(f"ch{channel} fuera de guarda ADC: {bank_mv} mV")
                    usable = (point.ok and
                              BANCO_MIN_VALIDO_MV <= bank_mv <= BANCO_MAX_VALIDO_MV)
                    rows.append({
                        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                        "elapsed_s": round(time.monotonic() - started, 3),
                        "phase": phase, "idac2": code, "channel": channel,
                        "tap": TAP_NAMES[channel], "bank_mv": bank_mv,
                        "pp_bank_mv": point.pp_uv / 1000.0,
                        "adc_ok": int(point.ok), "usable": int(usable),
                    })
                    summary.append(f"ch{channel}={bank_mv:.3f}")
                save()
                print(f"{time.monotonic()-started:7.1f}s IDAC2={code:+3d} " +
                      " ".join(summary), flush=True)
                remaining = args.period - ((time.monotonic() - phase_start) % args.period)
                time.sleep(min(max(0.0, remaining), args.period))
    finally:
        try:
            lab.set_idac(2, 0)
            lab.set_gain("pgaout", GAIN_CODES.index(1))
        finally:
            console.close()
            if rows:
                save()
                print(f"Evidencia: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
