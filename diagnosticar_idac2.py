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

from escala_banco import (BANCO_MAX_VALIDO_MV, BANCO_MIN_VALIDO_MV,
                          BANCO_VREF_MV, FACTOR)
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab, TAP_NAMES
from testbench.core.session import Session

ADC_ABSOLUTE_MV = (700.0, 1150.0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--codes", default="0,9,10,9,10,0")
    ap.add_argument("--max-abs-code", type=int, default=32,
                    help="límite explícito; la primera exploración usa 32")
    ap.add_argument("--pre-dwell", type=float, default=15.0)
    ap.add_argument("--baseline-dwell", type=float, default=120.0,
                    help="registra ch0..ch4 en neutro antes de mover IDAC2")
    ap.add_argument("--dwell", type=float, default=60.0)
    ap.add_argument("--period", type=float, default=2.0)
    ap.add_argument("--read-retries", type=int, default=3)
    ap.add_argument("--stop-band-real-mv", type=float, default=0.0,
                    help="detiene el barrido si ch2 entra en esta banda de Vref")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    codes = [int(value) for value in args.codes.split(",")]
    if not 1 <= args.max_abs_code <= 255:
        ap.error("--max-abs-code debe estar entre 1 y 255")
    if any(not -args.max_abs_code <= code <= args.max_abs_code
           for code in codes):
        ap.error(f"los códigos exceden +/-{args.max_abs_code}")
    if args.read_retries < 1:
        ap.error("--read-retries debe ser al menos 1")

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

    def read_dc(channel: int):
        """Tolera pérdidas transitorias además de los reintentos de Lab."""
        for attempt in range(1, args.read_retries + 1):
            point = lab.measure_dc(channel, 1)
            if point is not None:
                return point
            print(f"reintento lectura ch{channel} "
                  f"({attempt}/{args.read_retries})", flush=True)
            time.sleep(0.25)
        return None

    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))

    def checked_write(call, description: str) -> None:
        for attempt in range(1, 5):
            if call():
                return
            print(f"reintento {description} ({attempt}/4)", flush=True)
            time.sleep(0.35)
        raise RuntimeError(f"sin ACK: {description}")

    try:
        checked_write(lambda: lab.set_adc_config(1), "ADC +/-2.5 V")
        checked_write(lambda: lab.set_gain("pga", GAIN_CODES.index(50)),
                      "PGA x50")
        checked_write(lambda: lab.set_gain("pgaout", GAIN_CODES.index(1)),
                      "PGAout x1")
        for stage in range(4):
            checked_write(lambda stage=stage: lab.set_idac(stage, 0),
                          f"IDAC{stage}=0")
        time.sleep(args.pre_dwell)

        baseline_start = time.monotonic()
        while time.monotonic() - baseline_start < args.baseline_dwell:
            summary = []
            for channel in range(5):
                point = read_dc(channel)
                if point is None:
                    raise RuntimeError(
                        f"sin respuesta ch{channel} tras "
                        f"{args.read_retries} intentos del runner")
                bank_mv = point.mean_mv
                if not ADC_ABSOLUTE_MV[0] <= bank_mv <= ADC_ABSOLUTE_MV[1]:
                    raise RuntimeError(
                        f"ch{channel} fuera de guarda ADC: {bank_mv} mV")
                usable = (point.ok and
                          BANCO_MIN_VALIDO_MV <= bank_mv <= BANCO_MAX_VALIDO_MV)
                rows.append({
                    "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                    "elapsed_s": round(time.monotonic() - started, 3),
                    "phase": -1, "idac2": 0, "channel": channel,
                    "tap": TAP_NAMES[channel], "bank_mv": bank_mv,
                    "pp_bank_mv": point.pp_uv / 1000.0,
                    "adc_ok": int(point.ok), "usable": int(usable),
                })
                summary.append(f"ch{channel}={bank_mv:.3f}")
            save()
            print(f"{time.monotonic()-started:7.1f}s BASE " +
                  " ".join(summary), flush=True)
            remaining = (args.period -
                         ((time.monotonic() - baseline_start) % args.period))
            time.sleep(min(max(0.0, remaining), args.period))

        stop_requested = False
        for phase, code in enumerate(codes):
            checked_write(lambda code=code: lab.set_idac(2, code),
                          f"IDAC2={code}")
            phase_start = time.monotonic()
            while time.monotonic() - phase_start < args.dwell:
                summary = []
                for channel in (2, 3, 4):
                    point = read_dc(channel)
                    if point is None:
                        raise RuntimeError(
                            f"sin respuesta ch{channel} tras "
                            f"{args.read_retries} intentos del runner")
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
                    if code != 0 and channel in (2, 3) and not usable:
                        save()
                        raise RuntimeError(
                            f"IDAC2={code:+d}: ch{channel} cruzó la ventana "
                            f"válida ({bank_mv:.3f} mV banco)")
                    if (channel == 2 and args.stop_band_real_mv > 0.0 and
                            abs(bank_mv - BANCO_VREF_MV) * FACTOR <=
                            args.stop_band_real_mv):
                        stop_requested = True
                    summary.append(f"ch{channel}={bank_mv:.3f}")
                save()
                print(f"{time.monotonic()-started:7.1f}s IDAC2={code:+3d} " +
                      " ".join(summary), flush=True)
                remaining = args.period - ((time.monotonic() - phase_start) % args.period)
                time.sleep(min(max(0.0, remaining), args.period))
                if stop_requested:
                    print(f"IDAC2={code:+d}: ch2 entró en banda de "
                          f"±{args.stop_band_real_mv:g} mV reales; deteniendo",
                          flush=True)
                    break
            if stop_requested:
                break
    finally:
        try:
            for stage in range(4):
                try:
                    checked_write(lambda stage=stage: lab.set_idac(stage, 0),
                                  f"neutralizar IDAC{stage}")
                except Exception:
                    pass
            try:
                checked_write(lambda: lab.set_gain(
                    "pgaout", GAIN_CODES.index(1)), "restaurar PGAout x1")
            except Exception:
                pass
        finally:
            console.close()
            if rows:
                save()
                print(f"Evidencia: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
