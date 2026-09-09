"""Autocalibración GEO dividida en arranque lento y cambio rápido de ganancia.

La rutina de arranque trabaja sólo con IDAC0/IDAC1 y los taps ch0/ch1. La
rutina de ganancia conservará esos códigos y ajustará IDAC2/IDAC3 después del
cambio físico de R(IDAC2) a 5,1 kOhm. Este archivo no usa semillas.

Todos los valores del ADC están en unidades del banco. La conversión a magnitud
física se centraliza en ``escala_banco.py``. Ninguna lectura de control conecta
el capacitor auxiliar del AMux: el firmware cargado implementa ``with_cap=0``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from escala_banco import (BANCO_MAX_VALIDO_MV, BANCO_MIN_VALIDO_MV,
                          BANCO_VREF_MV, FACTOR)
from testbench.core.console import Console
from testbench.core.lab import GAIN_CODES, Lab, TAP_NAMES
from testbench.core.session import Session

TARGET_UV = int(round(BANCO_VREF_MV * 1000.0))
VALID_MIN_UV = int(round(BANCO_MIN_VALIDO_MV * 1000.0))
VALID_MAX_UV = int(round(BANCO_MAX_VALIDO_MV * 1000.0))
TOLERANCE_UV = int(round(20_000.0 / FACTOR))
BP_FINAL_TOLERANCE_UV = int(round(50_000.0 / FACTOR))
UPSTREAM_GUARD_UV = int(round(500_000.0 / FACTOR))
PGA_X50_CODE = GAIN_CODES.index(50)
PGAOUT_X1_CODE = GAIN_CODES.index(1)


def real_mv(bank_delta_uv: float) -> float:
    return bank_delta_uv * FACTOR / 1000.0


def valid_bank(value_uv: float) -> bool:
    return VALID_MIN_UV <= value_uv <= VALID_MAX_UV


@dataclass
class Point:
    elapsed_s: float
    phase: str
    stage: int
    code: int
    channel: int
    mean_bank_uv: int
    error_bank_uv: int
    error_real_mv: float
    pp_bank_uv: int
    valid: bool


def fit_first_order(points: list[tuple[float, float]]) -> tuple[float | None, dict]:
    """Ajusta y_inf + A exp(-t/tau); rechaza respuestas débiles/no exponenciales."""
    if len(points) < 8:
        return None, {"reason": "pocas muestras"}
    t0 = points[0][0]
    ts = [t - t0 for t, _ in points]
    ys = [y for _, y in points]
    noise = statistics.pstdev(ys[-min(5, len(ys)):]) if len(ys) > 1 else 0.0
    best = None
    for tau_tenths in range(20, 1201):
        tau = tau_tenths / 10.0
        xs = [math.exp(-t / tau) for t in ts]
        mx = statistics.fmean(xs)
        my = statistics.fmean(ys)
        den = sum((x - mx) ** 2 for x in xs)
        if den <= 0.0:
            continue
        amplitude = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
        final = my - amplitude * mx
        residuals = [y - (final + amplitude * x) for x, y in zip(xs, ys)]
        sse = sum(r * r for r in residuals)
        if best is None or sse < best[0]:
            best = (sse, tau, amplitude, final, residuals)
    if best is None:
        return None, {"reason": "ajuste imposible"}
    sse, tau, amplitude, final, residuals = best
    total = sum((y - statistics.fmean(ys)) ** 2 for y in ys)
    r2 = 1.0 - sse / total if total > 0.0 else 0.0
    residual_rms = math.sqrt(sse / len(ys))
    metrics = {"tau_s": tau, "amplitude_bank_uv": amplitude,
               "amplitude_real_mv": real_mv(amplitude), "final_bank_uv": final,
               "r2": r2, "residual_rms_bank_uv": residual_rms,
               "noise_bank_uv": noise, "samples": len(points)}
    min_effect = max(8.0 * max(noise, 1.0), 2_000.0 / FACTOR)
    if abs(amplitude) < min_effect:
        metrics["reason"] = "excursion insuficiente"
        return None, metrics
    if r2 < 0.70 or residual_rms > max(4.0 * noise, 2_000.0 / FACTOR):
        metrics["reason"] = "respuesta no compatible con primer orden"
        return None, metrics
    return tau, metrics


class TwoPhaseCalibrator:
    def __init__(self, lab: Lab, output: Path, *, samples: int = 3) -> None:
        self.lab = lab
        self.output = output
        self.samples = samples
        self.started = time.monotonic()
        self.codes = [0, 0, 0, 0]
        self.rows: list[Point] = []
        self.events: list[dict] = []

    def event(self, kind: str, message: str, **data) -> None:
        item = {"at": datetime.now().isoformat(timespec="milliseconds"),
                "kind": kind, "message": message, **data}
        self.events.append(item)
        print(message, flush=True)
        self.save()

    def save(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        csv_path = Path(str(self.output) + ".csv")
        fields = tuple(Point.__dataclass_fields__)
        with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(asdict(row) for row in self.rows)
        payload = {"algorithm": "two-phase closed loop; no seeds",
                   "started_at": datetime.now().isoformat(timespec="seconds"),
                   "codes": self.codes, "events": self.events,
                   "measurements": [asdict(row) for row in self.rows]}
        self.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def set_code(self, stage: int, code: int) -> None:
        if not -255 <= code <= 255:
            raise RuntimeError(f"IDAC{stage} fuera de rango: {code}")
        for attempt in range(3):
            if self.lab.set_idac(stage, code):
                self.codes[stage] = code
                return
            self.event("write_retry", f"reintento IDAC{stage}={code}",
                       stage=stage, code=code, attempt=attempt + 1)
            time.sleep(0.25)
        raise RuntimeError(f"sin ACK IDAC{stage}={code} tras tres reintentos")

    def read(self, channel: int, phase: str, stage: int, code: int,
             *, require_valid: bool = True) -> Point:
        values = []
        for _ in range(self.samples):
            point = None
            for _attempt in range(4):
                point = self.lab.measure_dc(channel, 2)
                if point is not None and point.ok:
                    break
                time.sleep(0.2)
            if point is None or not point.ok:
                raise RuntimeError(f"sin lectura válida ch{channel}")
            values.append(point)
        mean_uv = int(statistics.median(point.mean_uv for point in values))
        pp_uv = int(statistics.median(point.pp_uv for point in values))
        is_valid = valid_bank(mean_uv)
        result = Point(time.monotonic() - self.started, phase, stage, code,
                       channel, mean_uv, mean_uv - TARGET_UV,
                       real_mv(mean_uv - TARGET_UV), pp_uv, is_valid)
        self.rows.append(result)
        self.save()
        if require_valid and not is_valid:
            raise RuntimeError(
                f"ch{channel} {TAP_NAMES[channel]} fuera de ventana: "
                f"{mean_uv/1000:.3f} mV banco")
        return result

    def configure_neutral(self) -> None:
        if not self.lab.set_adc_config(1):
            raise RuntimeError("sin ACK ADC +/-2,5 V")
        if not self.lab.set_gain("pga", PGA_X50_CODE):
            raise RuntimeError("sin ACK PGA x50")
        if not self.lab.set_gain("pgaout", PGAOUT_X1_CODE):
            raise RuntimeError("sin ACK PGAout x1")
        for stage in range(4):
            self.set_code(stage, 0)

    def probe(self, stage: int, channel: int, delta: int,
              *, settle_s: float = 1.0) -> dict:
        """Prueba +/-delta, siempre vuelve al origen y devuelve la pendiente local."""
        origin = self.codes[stage]
        base = self.read(channel, f"IDAC{stage} base", stage, origin)
        measured: dict[int, Point] = {origin: base}
        try:
            for candidate in (origin + delta, origin - delta):
                self.set_code(stage, candidate)
                time.sleep(settle_s)
                measured[candidate] = self.read(
                    channel, f"IDAC{stage} probe", stage, candidate,
                    require_valid=False)
                self.set_code(stage, origin)
                time.sleep(settle_s)
                self.read(channel, f"IDAC{stage} restore", stage, origin,
                          require_valid=False)
        finally:
            self.set_code(stage, origin)
        lo, hi = origin - delta, origin + delta
        if not measured[lo].valid or not measured[hi].valid:
            slope = None
        else:
            slope = ((measured[hi].mean_bank_uv - measured[lo].mean_bank_uv) /
                     float(hi - lo))
        data = {"stage": stage, "channel": channel, "origin": origin,
                "delta": delta, "slope_bank_uv_per_code": slope,
                "slope_real_mv_per_code": None if slope is None else real_mv(slope),
                "points": {str(code): asdict(point)
                           for code, point in measured.items()}}
        self.event("probe", f"IDAC{stage}->ch{channel}: pendiente " +
                   ("no observable" if slope is None else
                    f"{real_mv(slope):+.2f} mV/código"), **data)
        return data

    def probe_upstream(self) -> dict:
        self.configure_neutral()
        time.sleep(5.0)
        pga = self.probe(0, 0, 1, settle_s=1.0)
        bp = self.probe(1, 1, 2, settle_s=2.0)
        return {"pga": pga, "bp": bp}

    def wait_channel_stable(self, channel: int, *, stage: int, code: int,
                            timeout_s: float = 150.0,
                            threshold_real_mv: float = 10.0) -> Point:
        """Espera estabilidad de la media, no del pico-pico de 50 Hz."""
        threshold = threshold_real_mv * 1000.0 / FACTOR
        previous = self.read(channel, "espera estabilidad", stage, code)
        consecutive = 0
        started = time.monotonic()
        while time.monotonic() - started < timeout_s:
            time.sleep(3.0)
            current = self.read(channel, "espera estabilidad", stage, code)
            if abs(current.mean_bank_uv - previous.mean_bank_uv) <= threshold:
                consecutive += 1
                if consecutive >= 4:
                    self.event("stable", f"ch{channel} estable",
                               channel=channel, elapsed_s=time.monotonic()-started,
                               threshold_real_mv=threshold_real_mv)
                    return current
            else:
                consecutive = 0
            previous = current
        raise RuntimeError(f"ch{channel} no se estabilizó en {timeout_s:.0f} s")

    def ramp_to(self, stage: int, channel: int, target: int,
                *, max_step: int) -> None:
        """Lleva el IDAC al Newton en escalones eléctricos, midiendo cada uno."""
        while self.codes[stage] != target:
            delta = target - self.codes[stage]
            candidate = self.codes[stage] + max(-max_step, min(max_step, delta))
            previous = self.codes[stage]
            self.set_code(stage, candidate)
            time.sleep(0.35)
            trial = self.read(channel, f"IDAC{stage} rampa Newton", stage,
                              candidate, require_valid=False)
            if (not trial.valid or
                    abs(trial.error_bank_uv) > 2.0 * UPSTREAM_GUARD_UV):
                self.set_code(stage, previous)
                raise RuntimeError(f"IDAC{stage}: rampa Newton cruzó guarda")

    def newton_pi(self, stage: int, channel: int, slope: float,
                  *, max_abs_code: int, window_s: float,
                  sample_period_s: float, ramp_step: int,
                  max_pi_step: int = 4,
                  final_tolerance_uv: int = TOLERANCE_UV,
                  control_delay_s: float = 0.0,
                  kp: float = 0.30, ki: float = 0.04) -> Point:
        """Salto Newton acotado, PI pequeño y verificación dentro de una ventana."""
        if abs(slope) < 1.0:
            raise RuntimeError(f"IDAC{stage} sin pendiente útil")
        current = self.read(channel, f"IDAC{stage} inicio", stage,
                            self.codes[stage])
        raw_target = self.codes[stage] - current.error_bank_uv / slope
        target = max(-max_abs_code, min(max_abs_code, int(round(raw_target))))
        self.event("newton", f"IDAC{stage} Newton {self.codes[stage]}->{target}",
                   stage=stage, channel=channel, slope_bank_uv_per_code=slope,
                   raw_target=raw_target, target=target)
        self.ramp_to(stage, channel, target, max_step=ramp_step)

        started = time.monotonic()
        control_start = started + control_delay_s
        control_until = started + 0.90 * window_s
        integral_codes = 0.0
        stable = 0
        current = self.read(channel, f"IDAC{stage} post Newton", stage,
                            self.codes[stage], require_valid=False)
        while time.monotonic() - started < window_s:
            time.sleep(min(sample_period_s,
                           max(0.0, window_s - (time.monotonic() - started))))
            current = self.read(channel, f"IDAC{stage} PI", stage,
                                self.codes[stage], require_valid=False)
            if not current.valid or abs(current.error_bank_uv) > UPSTREAM_GUARD_UV:
                raise RuntimeError(f"IDAC{stage}: PI cruzó guarda upstream")
            if abs(current.error_bank_uv) <= final_tolerance_uv:
                stable += 1
            else:
                stable = 0
            if time.monotonic() < control_start or time.monotonic() >= control_until:
                continue
            raw_correction = -current.error_bank_uv / slope
            integral_codes = max(-24.0, min(24.0,
                integral_codes + raw_correction * sample_period_s / max(window_s, 1.0)))
            effort = kp * raw_correction + ki * integral_codes
            correction = int(round(max(-max_pi_step, min(max_pi_step, effort))))
            # Evitar que la ganancia pequeña seguida del redondeo mate el lazo
            # cuando todavía falta más de media LSB. Sigue siendo PI: el signo
            # y la necesidad del paso salen del error realimentado y la
            # pendiente medida, no de una semilla.
            if correction == 0 and abs(raw_correction) >= 0.50:
                correction = 1 if raw_correction > 0.0 else -1
            if correction:
                candidate = max(-max_abs_code,
                                min(max_abs_code, self.codes[stage] + correction))
                if candidate != self.codes[stage]:
                    before = self.codes[stage]
                    self.set_code(stage, candidate)
                    self.event("pi", f"IDAC{stage} PI {before}->{candidate}",
                               stage=stage, channel=channel,
                               error_real_mv=current.error_real_mv,
                               correction=correction)
        final = self.read(channel, f"IDAC{stage} verificación", stage,
                          self.codes[stage])
        self.event("closed_loop", f"IDAC{stage} final "
                   f"{final.error_real_mv:+.1f} mV", stage=stage,
                   channel=channel, stable_samples=stable,
                   final=asdict(final))
        if abs(final.error_bank_uv) > final_tolerance_uv or stable < 2:
            raise RuntimeError(f"IDAC{stage}: Newton+PI no convergió")
        return final

    def estimate_tau_bp(self, pga_slope: float, *, duration_s: float = 90.0) -> tuple[float, dict]:
        """Excita IDAC0 un código y estima el polo lento leyendo BPo/ch1."""
        origin = self.codes[0]
        # Elegir el signo que mantenga ch0 cerca de Vref según la pendiente.
        ch0 = self.read(0, "tau guarda ch0", 0, origin)
        direction = -1 if ch0.error_bank_uv * pga_slope > 0 else 1
        candidate = origin + direction
        series: list[tuple[float, float]] = []
        try:
            self.set_code(0, candidate)
            t0 = time.monotonic()
            while time.monotonic() - t0 <= duration_s:
                bp = self.read(1, "tau BP", 0, candidate)
                series.append((time.monotonic() - t0, float(bp.mean_bank_uv)))
                time.sleep(max(0.0, 3.0 - (time.monotonic() - t0) % 3.0))
        finally:
            self.set_code(0, origin)
        tau, metrics = fit_first_order(series)
        if tau is None:
            self.event("tau_rejected", "Tau BP rechazada", metrics=metrics)
            raise RuntimeError(f"tau BP no fiable: {metrics.get('reason')}")
        self.event("tau", f"Tau BP = {tau:.1f} s", metrics=metrics,
                   excitation={"stage": 0, "delta": direction, "channel": 1})
        return tau, metrics

    def calibrate_upstream(self, *, keep_on_pass: bool = False) -> dict:
        """Implementación de la rutina A hasta IDAC1; no toca IDAC2/IDAC3."""
        self.configure_neutral()
        time.sleep(5.0)
        pga_probe = self.probe(0, 0, 1, settle_s=1.0)
        pga_slope = pga_probe["slope_bank_uv_per_code"]
        if pga_slope is None:
            raise RuntimeError("no se pudo identificar IDAC0->PGAgain")
        pga_final = self.newton_pi(0, 0, pga_slope, max_abs_code=32,
                                   window_s=30.0, sample_period_s=1.5,
                                   ramp_step=4, max_pi_step=2)
        self.wait_channel_stable(1, stage=0, code=self.codes[0])
        tau, _ = self.estimate_tau_bp(pga_slope)
        # La estabilidad observada reemplaza otra espera fija y certifica que el
        # escalón de tau ya no contamina la identificación de IDAC1.
        self.wait_channel_stable(1, stage=0, code=self.codes[0],
                                 timeout_s=max(120.0, 4.0 * tau))
        bp_probe = self.probe(1, 1, 32, settle_s=2.0 * tau)
        bp_slope = bp_probe["slope_bank_uv_per_code"]
        if bp_slope is None:
            raise RuntimeError("no se pudo identificar IDAC1->BPo")
        bp_final = self.newton_pi(1, 1, bp_slope, max_abs_code=255,
                                  window_s=3.0 * tau,
                                  sample_period_s=max(3.0, 0.20 * tau),
                                  ramp_step=16, max_pi_step=1,
                                  final_tolerance_uv=BP_FINAL_TOLERANCE_UV,
                                  control_delay_s=1.25 * tau,
                                  kp=0.12, ki=0.02)
        # Verificación común: la propia ventana Newton+PI ya consumió 2tau.
        pga_check = self.read(0, "verificación upstream", 0, self.codes[0])
        bp_check = self.read(1, "verificación upstream", 1, self.codes[1])
        passed = (abs(pga_check.error_bank_uv) <= TOLERANCE_UV and
                  abs(bp_check.error_bank_uv) <= BP_FINAL_TOLERANCE_UV)
        result = {"pass": passed, "tau_s": tau, "codes": self.codes[:2],
                  "pga": asdict(pga_final), "bp": asdict(bp_final),
                  "verified": {"pga": asdict(pga_check), "bp": asdict(bp_check)}}
        self.event("upstream_pass" if passed else "upstream_fail",
                   f"Upstream {'PASS' if passed else 'FAIL'}: "
                   f"IDAC0={self.codes[0]} IDAC1={self.codes[1]}", result=result)
        if not passed:
            raise RuntimeError("verificación upstream fuera de tolerancia")
        if not keep_on_pass:
            self.set_code(0, 0)
            self.set_code(1, 0)
        return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=("probe-upstream", "calibrate-upstream"))
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--keep-on-pass", action="store_true")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    output = args.output or (Path(__file__).resolve().parents[3] / "lab" /
        "calibracion_nueva" /
        f"dos_fases_{args.mode}_{datetime.now():%Y%m%d_%H%M%S}.json")
    console = Console(args.port)
    console.open(wait_ready=True, timeout=25.0)
    calibrator = TwoPhaseCalibrator(Lab(Session(console)), output)
    exit_code = 1
    try:
        if args.mode == "probe-upstream":
            result = calibrator.probe_upstream()
        else:
            result = calibrator.calibrate_upstream(keep_on_pass=args.keep_on_pass)
        calibrator.event("result", "Ensayo finalizado", result=result)
        exit_code = 0
    except Exception as exc:
        calibrator.event("error", f"ABORT: {type(exc).__name__}: {exc}")
    finally:
        if exit_code != 0 or not args.keep_on_pass:
            for stage in (0, 1):
                try:
                    calibrator.set_code(stage, 0)
                except Exception:
                    pass
        calibrator.save()
        console.close()
        print(f"Evidencia: {output}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
