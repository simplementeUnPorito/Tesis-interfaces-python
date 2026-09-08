"""Caracteriza IDAC2/PGAout con límites duros y restauración garantizada.

Esta utilidad sólo se usa durante la migración del IDAC2 al rango de 255 uA.
No modifica TopDesign. Por omisión nunca supera código 32: a 1 uA/bit y con
1,5 kohm es la misma corriente máxima que código 255 en el rango anterior.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

from testbench.core import console as con
from testbench.core.lab import GAIN_CODES, Lab, TAP_NAMES
from testbench.core.session import Session


TARGET_UV = 1_000_000
PGA_X50_CODE = GAIN_CODES.index(50)
GAIN_TO_CODE = {gain: code for code, gain in enumerate(GAIN_CODES)}
# 32 se validó primero (misma corriente máxima que el rango viejo). Después de
# comprobar x8/x24/x50 sin acercar ningún tap a las guardas se habilitó el
# siguiente escalón, 48 uA. El límite sigue siendo de software y deliberadamente
# muy inferior a los 255 uA del componente.
SAFE_MAX_CODE = 48
# Guardas deliberadamente más estrictas que los presupuestos pedidos. Si una
# se viola, se restaura el estado neutro sin seguir con el siguiente punto.
GUARD_ERROR_UV = (550_000, 550_000, 1_100_000, 1_100_000, 1_100_000)


def set_checked(call, description: str) -> None:
    for _ in range(3):
        if call():
            return
        time.sleep(0.3)
    raise RuntimeError(f"sin ACK al configurar {description}")


def read_snapshot(lab: Lab) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {}
    for ch in range(5):
        point = None
        for _ in range(3):
            point = lab.measure_dc(ch, 3)
            if point is not None and point.ok:
                break
            time.sleep(0.3)
        if point is None or not point.ok:
            raise RuntimeError(f"ch{ch} {TAP_NAMES[ch]} sin lectura válida")
        error = point.mean_uv - TARGET_UV
        if abs(error) > GUARD_ERROR_UV[ch]:
            raise RuntimeError(
                f"guarda analógica ch{ch} {TAP_NAMES[ch]}: {error / 1000:+.1f} mV"
            )
        out[ch] = {"mean_uv": point.mean_uv, "error_uv": error,
                   "pp_uv": point.pp_uv}
    return out


def wait_stable(lab: Lab, *, min_s: float, max_s: float,
                period_s: float, stable_delta_uv: int,
                stable_rounds: int) -> tuple[list[dict], bool]:
    history: list[dict] = []
    t0 = time.monotonic()
    consecutive = 0
    previous: tuple[int, int] | None = None
    while True:
        elapsed = time.monotonic() - t0
        snap = read_snapshot(lab)
        history.append({"elapsed_s": round(elapsed, 3), "taps": snap})
        errors = "  ".join(
            f"ch{ch} {snap[ch]['error_uv'] / 1000:+8.2f}mV" for ch in range(5)
        )
        print(f"      t={elapsed:6.1f}s  {errors}", flush=True)

        current = (snap[3]["mean_uv"], snap[4]["mean_uv"])
        if previous is not None and elapsed >= min_s and all(
            abs(a - b) <= stable_delta_uv for a, b in zip(current, previous)
        ):
            consecutive += 1
        else:
            consecutive = 0
        previous = current
        if elapsed >= min_s and consecutive >= stable_rounds:
            return history, True
        if elapsed >= max_s:
            return history, False
        time.sleep(period_s)


def representative(history: list[dict], n: int = 3) -> dict[int, dict[str, int]]:
    tail = history[-min(n, len(history)):]
    return {
        ch: {
            field: int(round(statistics.median(row["taps"][ch][field] for row in tail)))
            for field in ("mean_uv", "error_uv", "pp_uv")
        }
        for ch in range(5)
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--gains", default="8")
    ap.add_argument("--codes", default="0,32")
    ap.add_argument("--idac3", type=int, default=0,
                    help="código LP fijo; negativo limitado a -32 en esta prueba")
    ap.add_argument("--min-wait", type=float, default=60.0)
    ap.add_argument("--max-wait", type=float, default=180.0)
    ap.add_argument("--period", type=float, default=12.0)
    ap.add_argument("--stable-delta-mv", type=float, default=5.0)
    ap.add_argument("--stable-rounds", type=int, default=2)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()

    gains = [int(x) for x in args.gains.split(",")]
    codes = [int(x) for x in args.codes.split(",")]
    if any(gain not in GAIN_TO_CODE for gain in gains):
        ap.error("ganancia no soportada")
    if any(code < 0 or code > SAFE_MAX_CODE for code in codes):
        ap.error(f"por seguridad IDAC2 debe estar entre 0 y {SAFE_MAX_CODE}")
    if not -32 <= args.idac3 <= 255:
        ap.error("por seguridad IDAC3 debe estar entre -32 y 255")

    output = args.output or (
        Path(__file__).resolve().parents[3] / "lab" / "calibracion_nueva" /
        f"caracterizacion_idac2_segura_{datetime.now():%Y%m%d_%H%M%S}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "safety": {"idac2_range_ua": 255, "resistor_ohm": 1500,
                   "max_code": SAFE_MAX_CODE,
                   "max_current_ua": SAFE_MAX_CODE,
                   "max_reference_shift_mv": SAFE_MAX_CODE * 1.5,
                   "idac3_code": args.idac3,
                   "idac3_range_ua": 31.875, "idac3_resistor_ohm": 10000},
        "points": [],
    }

    console = con.Console(args.port)
    print(f"Abriendo {args.port}...", flush=True)
    console.open(wait_ready=True, timeout=25.0)
    lab = Lab(Session(console))
    try:
        set_checked(lambda: lab.set_adc_config(1), "ADC ±2,5 V")
        set_checked(lambda: lab.set_gain("pga", PGA_X50_CODE), "PGAgain x50")
        set_checked(lambda: lab.set_idac(0, 0), "IDAC0=0")
        set_checked(lambda: lab.set_idac(1, -110), "IDAC1=-110")
        set_checked(lambda: lab.set_idac(3, args.idac3),
                    f"IDAC3={args.idac3}")
        for gain in gains:
            set_checked(lambda g=gain: lab.set_gain("pgaout", GAIN_TO_CODE[g]),
                        f"PGAout x{gain}")
            for code in codes:
                set_checked(lambda c=code: lab.set_idac(2, c), f"IDAC2={code}")
                print(f"  PGAout x{gain}, IDAC2={code}: esperando estabilidad", flush=True)
                history, stable = wait_stable(
                    lab, min_s=args.min_wait, max_s=args.max_wait,
                    period_s=args.period,
                    stable_delta_uv=int(args.stable_delta_mv * 1000),
                    stable_rounds=args.stable_rounds,
                )
                point = {"pgaout_x": gain, "idac2": code, "stable": stable,
                         "history": history, "representative": representative(history)}
                payload["points"].append(point)
                output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                if not stable:
                    raise RuntimeError(f"x{gain} IDAC2={code} no asentó antes del timeout")
    finally:
        # Estado conocido, de mínima autoridad, aun si falló una medida/guarda.
        try:
            lab.set_idac(2, 0)
            lab.set_idac(3, 0)
            lab.set_gain("pgaout", GAIN_TO_CODE[1])
        finally:
            payload["finished_at"] = datetime.now().isoformat(timespec="seconds")
            output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            Path(str(output) + ".log").write_text(console.transcript.text(),
                                                  encoding="utf-8")
            console.close()
    print(f"Evidencia: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
